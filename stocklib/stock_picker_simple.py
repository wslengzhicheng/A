"""简化版股票筛选模块 - 候选来源简化，评分复用 analyzer。

涨跌幅约定：与 snapshot.change_pct 一致，使用小数（0.05 = 5%）。
东财 clist fltt=2 的 f3 为百分数，入库前需 /100。
"""

import time
from . import datasource


def _safe_num(raw, default=0.0):
    """Coerce an East Money field to float; treat None, '', '-' and other
    non-numeric sentinels as *default*."""
    if raw is None or raw == "" or raw == "-":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _to_wan(value):
    return _safe_num(value) / 10000


def _pct_from_eastmoney_f3(raw):
    """东财 fltt=2 的 f3 为百分数 → 小数涨跌幅。"""
    return _safe_num(raw) / 100.0


def get_simple_candidates():
    """
    使用现有数据源获取候选股票
    返回:
        List[Dict]: 候选股票列表
    """
    candidates = []

    # 获取涨跌停股票
    try:
        limit_up, _ = datasource.get_limit_stocks("up", count=20)
        for stock in limit_up[:10]:  # 取前10只涨停股
            candidates.append({
                "code": stock["code"],
                "name": stock["name"],
                "market": "sh" if stock["code"].startswith(("60", "68")) else "sz",
                "change_pct": stock["change_pct"],
                "main_net": _to_wan(stock.get("main_net", 0)),
                "reason": "强势涨停"
            })
    except Exception:
        pass

    # 获取涨幅榜股票
    try:
        from .sources import http_get

        # 获取涨幅排行
        r = http_get("https://push2.eastmoney.com/api/qt/clist/get", params={
            "fid": "f3",
            "po": "1", "pz": "100", "pn": "1",
            "fltt": "2", "invt": "2",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12,f14,f2,f3,f62"
        })
        data = r.json()
        items = (data.get("data") or {}).get("diff") or []

        # 取涨幅在1-5%的股票（小数区间）
        for it in items:
            try:
                change_pct = _pct_from_eastmoney_f3(it.get("f3"))
                if 0.01 <= change_pct <= 0.05:
                    candidates.append({
                        "code": it.get("f12", ""),
                        "name": it.get("f14", ""),
                        "market": "sh" if it.get("f12", "").startswith(("60", "68")) else "sz",
                        "change_pct": change_pct,
                        "main_net": _to_wan(it.get("f62", 0)),
                        "reason": "温和上涨"
                    })
            except Exception:
                continue
    except Exception:
        pass

    return candidates[:30]  # 最多返回30只


def _heuristic_score(snapshot):
    """analyzer 不可用时的降级启发式（量纲与小数涨跌幅对齐）。"""
    price = snapshot.get("price", 0) or 0
    change_pct = snapshot.get("change_pct", 0) or 0
    pe_ttm = snapshot.get("pe_ttm")
    volume = snapshot.get("volume", 0) or 0

    score = 50
    if change_pct > 0:
        # 约每 1% 涨幅 +0.5 分，最多 +20
        score += min(change_pct * 500, 20)
    if pe_ttm and pe_ttm > 0 and pe_ttm < 30:
        score += 10
    # 成交量单位为「手」；100万手以上视为活跃
    if volume > 1_000_000:
        score += 5
    if 0 < price < 50:
        score += 5
    return min(score, 100)


def analyze_simple_candidate(market, code, name, days=60, main_net=0):
    """
    分析候选股票：优先复用 analyzer 评分，失败则启发式降级。
    """
    try:
        klines, k_src = datasource.get_kline(market, code, count=days, fresh=False)
        snapshot, s_src = datasource.get_snapshot(market, code, fresh=False)
        finance, f_src = datasource.get_finance(market, code, fresh=False)

        if not klines or not snapshot:
            return {"success": False, "error": "数据获取失败"}

        price = snapshot.get("price", 0)
        change_pct = snapshot.get("change_pct", 0)

        try:
            from . import analyzer
            klines = klines[-min(days, len(klines)):]
            x = analyzer.compute_indicators(klines)
            industry_ctx, _ = datasource.get_industry_context(
                market, code, pe=snapshot.get("pe_ttm"), fresh=False)
            signals, notes = analyzer.build_signals(x, snapshot, finance, industry_ctx)
            score, grade, _detail = analyzer.score(signals)
            return {
                "success": True,
                "code": code,
                "name": name,
                "market": market,
                "price": price,
                "change_pct": change_pct,
                "main_net": main_net,
                "score": round(score, 1),
                "grade": grade,
                "signals": sorted(signals, key=lambda s: -abs(s.get("score", 0)))[:10],
                "notes": notes,
                "reason": "analyzer 规则评分（简化候选池）",
                "score_source": "analyzer",
            }
        except Exception:
            score = _heuristic_score(snapshot)
            return {
                "success": True,
                "code": code,
                "name": name,
                "market": market,
                "price": price,
                "change_pct": change_pct,
                "main_net": main_net,
                "score": round(score, 1),
                "reason": "启发式评分（涨跌/估值/成交量，不可与 analyzer 分比较）",
                "score_source": "simple",
            }

    except Exception as e:
        return {
            "success": False,
            "code": code,
            "name": name,
            "market": market,
            "error": str(e)
        }


def find_buy_opportunities_simple(fresh=False):
    """
    寻找买入机会（简化版）
    """
    print("[筛选] 开始获取候选股票...")
    candidates = get_simple_candidates()
    print(f"[筛选] 获取到 {len(candidates)} 只候选股票")

    opportunities = []
    used_simple = False

    for candidate in candidates:
        print(f"[筛选] 分析: {candidate['name']}({candidate['code']})")
        candidate_main_net = candidate.get("main_net", 0)

        result = analyze_simple_candidate(
            candidate["market"],
            candidate["code"],
            candidate["name"],
            main_net=candidate_main_net
        )

        if result.get("success"):
            if result.get("score_source") == "simple":
                used_simple = True
            # 筛选条件：评分60-80分
            score = result.get("score", 0)
            if 60 <= score <= 80:
                result["buy_reason"] = (
                    f"综合评分{score}分，{result['reason']}，"
                    f"主力资金净流入{candidate_main_net:.0f}万元"
                )
                result["candidate_reason"] = candidate.get("reason", "")
                opportunities.append(result)

        time.sleep(0.3)  # 避免请求过快

    # 按评分排序
    opportunities.sort(key=lambda x: x.get("score", 0), reverse=True)
    score_source = "simple" if used_simple else "analyzer"

    return {
        "timestamp": int(time.time()),
        "candidates_count": len(candidates),
        "analyzed_count": len(candidates),
        "opportunities": opportunities[:10],  # 最多返回10只
        "score_source": score_source,
        "score_note": (
            "简化候选池；若含启发式分，不可与 analyzer 完整评分横向比较"
            if used_simple else
            "简化候选池，评分来自 analyzer"
        ),
        "summary": {
            "total_analyzed": len(candidates),
            "success_count": len(opportunities),
            "failed_count": 0,
            "opportunities_count": len(opportunities),
            "avg_score": round(
                sum(o.get("score", 0) for o in opportunities) / len(opportunities), 2
            ) if opportunities else 0
        }
    }
