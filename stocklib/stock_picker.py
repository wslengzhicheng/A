"""股票筛选模块：收盘前分析哪些股票有买入机会。

筛选策略：
1. 从涨幅榜、资金流入榜、跌幅超跌榜获取候选股票
2. 批量分析这些股票
3. 根据评分、技术信号、缠论买卖点等筛选买入机会
4. 给出每只股票的买入理由

涨跌幅约定：与 snapshot.change_pct 一致，使用小数（0.05 = 5%）。
东财 clist fltt=2 的 f3 为百分数，入库前需 /100。
"""
import time


# 筛选配置
MIN_SCORE = 60  # 最低评分阈值
MAX_SCORE = 80  # 最高评分阈值（避免追高）
MIN_MAIN_NET = 1000  # 主力净流入（万元）
MAX_DAILY_CHANGE = 0.08  # 最高涨幅（小数），避免追涨停
MIN_DAILY_CHANGE = -0.05  # 最低涨幅（小数），避免接飞刀

# 获取候选股票的数量
CANDIDATE_COUNT = 100
# 回测出场：评分跌破该阈值视为机会失效（卖出）
EXIT_SCORE = 50


def evaluate_opportunity(
    score,
    change_pct=0.0,
    main_net=None,
    has_buy_point=False,
    bullish_count=0,
    bearish_count=0,
    *,
    require_main_net=True,
    min_score=None,
    max_score=None,
    min_change=None,
    max_change=None,
    min_main_net=None,
    priority_score=65,
):
    """统一「是否买入机会」判定（线上筛选与回测共用）。

    参数:
        score: analyzer 综合分
        change_pct: 当日涨跌幅（小数）
        main_net: 主力净流入（万元）；回测可传 None 并设 require_main_net=False
        has_buy_point / bullish_count / bearish_count: 技术侧优先级
    返回:
        dict: passed, is_valid, is_priority, rank, reasons
    """
    min_score = MIN_SCORE if min_score is None else min_score
    max_score = MAX_SCORE if max_score is None else max_score
    min_change = MIN_DAILY_CHANGE if min_change is None else min_change
    max_change = MAX_DAILY_CHANGE if max_change is None else max_change
    min_main_net = MIN_MAIN_NET if min_main_net is None else min_main_net

    score = score or 0
    change_pct = change_pct or 0.0
    main_net_val = 0.0 if main_net is None else float(main_net)

    flow_ok = (not require_main_net) or (main_net is not None and main_net_val >= min_main_net)
    is_valid = (
        min_score <= score <= max_score
        and min_change <= change_pct <= max_change
        and flow_ok
    )
    is_priority = (
        has_buy_point
        or bullish_count >= bearish_count + 2
    )
    passed = bool(is_valid and (is_priority or score >= priority_score))

    reasons = []
    if passed:
        reasons.append(f"评分{score:.1f}")
        if has_buy_point:
            reasons.append("缠论买点")
        if bullish_count >= bearish_count + 2:
            reasons.append(f"多头信号占优({bullish_count}/{bearish_count})")
        if require_main_net and main_net is not None and main_net_val > 0:
            reasons.append(f"主力净流入{main_net_val:.0f}万")
        reasons.append(f"涨跌{change_pct * 100:+.1f}%")

    rank = score + (10 if has_buy_point else 0) + (main_net_val / 10000 if main_net is not None else 0)
    return {
        "passed": passed,
        "is_valid": is_valid,
        "is_priority": is_priority,
        "rank": rank,
        "reasons": reasons,
        "flow_ok": flow_ok,
    }


def get_candidates():
    """
    获取候选股票列表
    返回:
        List[Dict]: 候选股票 [{code, name, market, reason}]
    """
    candidates = []

    try:
        # 1. 主力资金流入榜（前30）
        inflow_stocks = _fetch_top_stocks_by_main_net(30, direction="in")
        for stock in inflow_stocks:
            stock["reason"] = "主力资金流入"
            candidates.append(stock)

        # 2. 涨幅榜中的强势股（但不是涨停，5%-8%涨幅）
        rising_stocks = _fetch_stocks_by_change_range(30, min_change=0.05, max_change=0.08)
        for stock in rising_stocks:
            stock["reason"] = "强势上涨"
            candidates.append(stock)

        # 3. 跌幅榜中的超跌股（-2% 到 -5%，寻找反弹机会）
        oversold_stocks = _fetch_stocks_by_change_range(20, min_change=-0.05, max_change=-0.02)
        for stock in oversold_stocks:
            stock["reason"] = "超跌反弹"
            candidates.append(stock)

    except Exception as e:
        print(f"[警告] 获取候选股票列表时出错: {e}")

    # 去重
    seen = set()
    unique_candidates = []
    for stock in candidates:
        key = (stock["code"], stock["market"])
        if key not in seen:
            seen.add(key)
            unique_candidates.append(stock)

    return unique_candidates[:CANDIDATE_COUNT]


def _pct_from_eastmoney_f3(raw):
    """东财 fltt=2 的 f3 为百分数 → 小数涨跌幅。"""
    try:
        return float(raw or 0) / 100.0
    except (TypeError, ValueError):
        return 0.0


def _fetch_top_stocks_by_main_net(count, direction="in"):
    """
    获取主力资金流入/流出前N名
    参数:
        count: 返回数量
        direction: 'in' 流入或 'out' 流出
    返回:
        List[Dict]: 股票列表
    """
    try:
        from .sources import http_get
        # 直接复用东财接口获取资金流排行
        r = http_get("https://push2.eastmoney.com/api/qt/clist/get", params={
            "fid": "f62",  # 按主力净流入排序
            "po": "1" if direction == "in" else "0",
            "pz": str(count + 20),
            "pn": "1", "np": "1",
            "fltt": "2", "invt": "2",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12,f14,f3,f62,f116,f117,f107"
        })
        data = r.json()
        items = (data.get("data") or {}).get("diff") or []

        stocks = []
        for it in items[:count]:
            code = it.get("f12", "")
            name = it.get("f14", "")
            change_pct = _pct_from_eastmoney_f3(it.get("f3"))
            main_net = it.get("f62", 0)

            # 确定市场
            market = "sh" if code.startswith(("60", "68")) else "sz"

            stocks.append({
                "code": code,
                "name": name,
                "market": market,
                "change_pct": change_pct,
                "main_net": main_net,
                "mktcap_total": (float(it.get("f116", 0)) or 0) / 10000,  # 亿元
                "volume": it.get("f107", 0)
            })

        return stocks
    except Exception as e:
        print(f"[警告] 获取资金流榜单失败: {e}")
        return []


def _fetch_stocks_by_change_range(count, min_change, max_change):
    """
    获取涨跌幅在指定范围内的股票
    参数:
        count: 返回数量
        min_change: 最小涨幅（小数，如 0.05 = 5%）
        max_change: 最大涨幅（小数）
    返回:
        List[Dict]: 股票列表
    """
    try:
        from .sources import http_get

        # 获取全市场股票数据
        r = http_get("https://push2.eastmoney.com/api/qt/clist/get", params={
            "fid": "f3",
            "po": "1" if max_change > 0 else "0",
            "pz": "2000",  # 获取足够多的股票
            "pn": "1", "np": "1",
            "fltt": "2", "invt": "2",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12,f14,f2,f3,f62,f116,f117,f107"
        })
        data = r.json()
        items = (data.get("data") or {}).get("diff") or []

        stocks = []
        for it in items:
            code = it.get("f12", "")
            name = it.get("f14", "")
            change_pct = _pct_from_eastmoney_f3(it.get("f3"))

            # 筛选涨跌幅范围
            if min_change <= change_pct <= max_change:
                market = "sh" if code.startswith(("60", "68")) else "sz"
                stocks.append({
                    "code": code,
                    "name": name,
                    "market": market,
                    "change_pct": change_pct,
                    "main_net": it.get("f62", 0),
                    "mktcap_total": (it.get("f116") or 0) / 10000,
                    "volume": it.get("f107", 0)
                })

        return stocks[:count]
    except Exception as e:
        print(f"[警告] 获取涨跌幅榜失败: {e}")
        return []


def _top_signals(signals, n=10):
    """按 |score| 降序取前 n 条，强度相同时保持原顺序。"""
    indexed = list(enumerate(signals))
    indexed.sort(key=lambda pair: (-abs(pair[1].get("score", 0)), pair[0]))
    return [s for _, s in indexed[:n]]


def analyze_candidate(market, code, name, days=60):
    """
    分析单只候选股票
    参数:
        market: 市场代码 ('sh' 或 'sz')
        code: 股票代码
        name: 股票名称
        days: 分析天数
    返回:
        Dict: 分析结果或错误信息
    """
    from . import datasource, analyzer, predictor
    try:
        # 获取数据
        klines, k_src = datasource.get_kline(market, code, count=days, fresh=False)
        snapshot, s_src = datasource.get_snapshot(market, code, fresh=False)
        finance, f_src = datasource.get_finance(market, code, fresh=False)

        # 确保有足够的数据
        klines = klines[-min(days, len(klines)):]

        # 计算指标
        x = analyzer.compute_indicators(klines)
        industry_ctx, _ = datasource.get_industry_context(
            market, code, pe=(snapshot or {}).get("pe_ttm"), fresh=False)
        signals, notes = analyzer.build_signals(x, snapshot, finance, industry_ctx)
        score, grade, detail = analyzer.score(signals)
        pred = predictor.outlook(signals, x["closes"])
        levels = predictor.key_levels(klines, x, lookback_days=days)

        # 资金流向
        fund_flow = None
        try:
            flow, _ = datasource.get_fund_flow(market, code)
            if flow:
                fund_flow = {
                    "main_net": flow.get("main_net", 0),
                    "super_net": flow.get("super_net", 0),
                    "big_net": flow.get("big_net", 0),
                }
        except Exception:
            pass

        # 统计信号
        bullish_signals = [s for s in signals if s["score"] > 0]
        bearish_signals = [s for s in signals if s["score"] < 0]

        # 缠论买卖点：信号名为「买卖点」且得分为正
        buy_points = [
            s for s in signals
            if s.get("category") == "chan" and s.get("name") == "买卖点" and s.get("score", 0) > 0
        ]

        return {
            "success": True,
            "code": code,
            "name": name,
            "market": market,
            "price": snapshot.get("price"),
            "change_pct": snapshot.get("change_pct"),
            "main_net": fund_flow.get("main_net") if fund_flow else 0,
            "score": round(score, 2) if score is not None else None,
            "grade": grade,
            "prediction": pred.get("direction"),
            "confidence": pred.get("confidence"),
            "bullish_count": len(bullish_signals),
            "bearish_count": len(bearish_signals),
            "has_buy_point": len(buy_points) > 0,
            "signals": _top_signals(signals, 10),
            "levels": levels,
            "notes": notes,
            "score_source": "analyzer",
        }

    except Exception as e:
        return {
            "success": False,
            "code": code,
            "name": name,
            "market": market,
            "error": str(e)
        }


def generate_buy_reason(result):
    """
    根据分析结果生成买入理由
    参数:
        result: analyze_candidate 的返回结果
    返回:
        str: 买入理由
    """
    if not result.get("success"):
        return f"分析失败: {result.get('error')}"

    reasons = []

    # 1. 评分理由
    score = result.get("score", 0)
    if score >= 70:
        reasons.append(f"综合评分{score:.1f}分，技术面强势")
    elif score >= 60:
        reasons.append(f"综合评分{score:.1f}分，技术面偏好")

    # 2. 技术信号理由
    bullish_signals = [s for s in result.get("signals", []) if s.get("score", 0) > 0]
    strong_signals = [s for s in bullish_signals if s.get("score", 0) == 2]

    for sig in strong_signals[:3]:  # 最多取3个最强信号
        reasons.append(f"{sig.get('name')}：{sig.get('text')}")

    # 3. 缠论买卖点
    chan_signals = [
        s for s in result.get("signals", [])
        if s.get("category") == "chan" and s.get("score", 0) > 0
    ]
    if chan_signals:
        reasons.append(f"缠论结构：{chan_signals[0].get('text')}")

    # 4. 资金流向
    main_net = result.get("main_net", 0)
    if main_net > 0:
        reasons.append(f"主力资金净流入{main_net:.0f}万元，资金面良好")

    # 5. 价格位置
    levels = result.get("levels", {})
    support = levels.get("support")
    resistance = levels.get("resistance")
    price = result.get("price", 0)
    if support and resistance and resistance != support:
        position = (price - support) / (resistance - support) * 100
        if position < 30:
            reasons.append(f"现价接近支撑位{support:.2f}，处于相对低位")

    # 6. 预测方向（与 predictor.outlook 返回值对齐：上行/下行/震荡 + 高/中/低）
    pred_dir = result.get("prediction")
    conf = result.get("confidence")
    if pred_dir == "上行" and conf in ("高", "中"):
        reasons.append(f"走势预测上行，置信度{conf}")

    if not reasons:
        return "暂无明显买入信号，建议观望"

    return "；".join(reasons)


def find_buy_opportunities(fresh=False):
    """
    寻找收盘前的买入机会
    参数:
        fresh: 是否强制刷新缓存
    返回:
        Dict: {
            "timestamp": 分析时间戳,
            "candidates": 候选股票数,
            "analyzed": 已分析股票数,
            "opportunities": List[Dict] - 买入机会列表,
            "summary": 汇总统计
        }
    """
    print("[筛选] 开始获取候选股票...")
    candidates = get_candidates()
    print(f"[筛选] 获取到 {len(candidates)} 只候选股票")

    results = []
    opportunities = []
    failed = 0

    # 批量分析
    for i, candidate in enumerate(candidates):
        print(f"[筛选] 分析 {i+1}/{len(candidates)}: {candidate['name']}({candidate['code']})")

        result = analyze_candidate(
            candidate["market"],
            candidate["code"],
            candidate["name"],
            days=60
        )

        results.append(result)

        if not result.get("success"):
            failed += 1
            continue

        # 筛选条件（与回测共用 evaluate_opportunity）
        score = result.get("score", 0)
        change_pct = result.get("change_pct", 0) or 0
        main_net = result.get("main_net", 0) or 0
        has_buy_point = result.get("has_buy_point", False)

        verdict = evaluate_opportunity(
            score,
            change_pct=change_pct,
            main_net=main_net,
            has_buy_point=has_buy_point,
            bullish_count=result.get("bullish_count", 0),
            bearish_count=result.get("bearish_count", 0),
            require_main_net=True,
        )

        if verdict["passed"]:
            result["buy_reason"] = generate_buy_reason(result)
            result["candidate_reason"] = candidate.get("reason", "")
            result["rank"] = verdict["rank"]
            opportunities.append(result)

        # 避免请求过于频繁
        if i < len(candidates) - 1:
            time.sleep(0.3)

    # 按优先级排序
    opportunities.sort(key=lambda x: x.get("rank", 0), reverse=True)
    opportunities = opportunities[:20]  # 最多返回20只

    # 汇总统计
    summary = {
        "total_analyzed": len(candidates),
        "success_count": len([r for r in results if r.get("success")]),
        "failed_count": failed,
        "opportunities_count": len(opportunities),
        "avg_score": 0
    }

    if opportunities:
        avg_score = sum(o.get("score", 0) for o in opportunities) / len(opportunities)
        summary["avg_score"] = round(avg_score, 2)

    return {
        "timestamp": int(time.time()),
        "candidates_count": len(candidates),
        "analyzed_count": len([r for r in results if r.get("success")]),
        "opportunities": opportunities,
        "summary": summary,
        "score_source": "analyzer",
    }
