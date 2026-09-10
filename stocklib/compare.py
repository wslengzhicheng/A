"""多股票对比分析模块。批量分析股票并提取对比指标。"""
import time

from . import datasource, analyzer, predictor


def batch_analyze(queries, days=250, fresh=False):
    """
    批量分析多只股票，返回对比指标
    参数:
        queries: List[str] - 股票代码或名称列表
        days: int - 分析天数（30~320）
        fresh: bool - 是否强制刷新缓存
    返回:
        List[Dict] - 每只股票的分析结果摘要
    """
    # 限制数量避免超时
    if len(queries) > 20:
        raise ValueError("最多支持同时对比20只股票")

    results = []
    for i, query in enumerate(queries):
        try:
            print(f"[{i+1}/{len(queries)}] 分析: {query}")

            market, code, name = datasource.resolve(query)
            klines, k_src = datasource.get_kline(market, code, count=days, fresh=fresh)
            klines = klines[-days:]
            snapshot, s_src = datasource.get_snapshot(market, code, fresh=fresh)
            finance, f_src = datasource.get_finance(market, code, fresh=fresh)
            industry_ctx, _ = datasource.get_industry_context(
                market, code, pe=(snapshot or {}).get("pe_ttm"), fresh=fresh)

            # 计算指标
            x = analyzer.compute_indicators(klines)
            signals, notes = analyzer.build_signals(x, snapshot, finance, industry_ctx)
            score, grade, detail = analyzer.score(signals)
            pred = predictor.outlook(signals, x["closes"])
            levels = predictor.key_levels(klines, x, lookback_days=days)

            # 统计信号方向
            bullish_count = sum(1 for s in signals if s["score"] > 0)
            bearish_count = sum(1 for s in signals if s["score"] < 0)

            results.append({
                "code": code,
                "name": snapshot.get("name", name or f"{market}{code}"),
                "market": market,
                "price": snapshot.get("price"),
                "prev_close": snapshot.get("prev_close"),
                "change_pct": snapshot.get("change_pct"),
                "open": snapshot.get("open"),
                "high": snapshot.get("high"),
                "low": snapshot.get("low"),
                "volume": snapshot.get("volume"),
                "amount": snapshot.get("amount"),
                "turnover": snapshot.get("turnover"),
                "score": round(score, 2) if score is not None else None,
                "grade": grade,
                "prediction": pred.get("direction"),
                "confidence": pred.get("confidence"),
                "pe_ttm": snapshot.get("pe_ttm"),
                "pb": snapshot.get("pb"),
                "mktcap_total": snapshot.get("mktcap_total"),
                "mktcap_float": snapshot.get("mktcap_float"),
                "high_52w": snapshot.get("high_52w"),
                "low_52w": snapshot.get("low_52w"),
                "volatility": x.get("volatility"),
                "support": levels.get("support"),
                "resistance": levels.get("resistance"),
                "signals_count": len(signals),
                "bullish_signals": bullish_count,
                "bearish_signals": bearish_count,
                "score_detail": detail,
                "success": True,
                "source": f"K线:{k_src} 快照:{s_src}",
            })

            # 请求间隔避免限流
            if i < len(queries) - 1:
                time.sleep(0.3)

        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append({
                "query": query,
                "error": str(e),
                "success": False
            })

    return results


def compute_summary(results):
    """
    计算对比结果汇总统计
    参数:
        results: batch_analyze 返回的结果列表
    返回:
        Dict - 汇总统计
    """
    valid_results = [r for r in results if r.get("success")]

    if not valid_results:
        return {
            "total": len(results),
            "success": 0,
            "failed": len(results),
            "avg_score": None,
            "avg_change": None,
            "strongest": None,
            "weakest": None,
        }

    scores = [r.get("score") for r in valid_results if r.get("score") is not None]
    changes = [r.get("change_pct") for r in valid_results if r.get("change_pct") is not None]

    # 找出最强/最弱股票
    strongest = max(valid_results, key=lambda x: x.get("score") or 0) if scores else None
    weakest = min(valid_results, key=lambda x: x.get("score") or 0) if scores else None

    # 计算平均涨跌判断整体方向
    total_change = sum(changes) if changes else 0
    if total_change > 0.5:
        consensus = "偏多"
    elif total_change < -0.5:
        consensus = "偏空"
    else:
        consensus = "震荡"

    return {
        "total": len(results),
        "success": len(valid_results),
        "failed": len(results) - len(valid_results),
        "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
        "avg_change": round(sum(changes) / len(changes), 2) if changes else None,
        "strongest": {
            "code": strongest.get("code"),
            "name": strongest.get("name"),
            "score": strongest.get("score")
        } if strongest else None,
        "weakest": {
            "code": weakest.get("code"),
            "name": weakest.get("name"),
            "score": weakest.get("score")
        } if weakest else None,
        "consensus": consensus,
    }


def extract_comparison_matrix(results):
    """
    提取对比矩阵数据（用于可视化）
    参数:
        results: batch_analyze 返回的结果列表
    返回:
        Dict - 对比矩阵数据
    """
    valid_results = [r for r in results if r.get("success")]

    return {
        "codes": [r.get("code") for r in valid_results],
        "names": [r.get("name") for r in valid_results],
        "scores": [r.get("score") or 0 for r in valid_results],
        "changes": [r.get("change_pct") or 0 for r in valid_results],
        "pe_ttm": [r.get("pe_ttm") or 0 for r in valid_results],
        "pb": [r.get("pb") or 0 for r in valid_results],
        "mktcap_total": [r.get("mktcap_total") or 0 for r in valid_results],
        "turnover": [r.get("turnover") or 0 for r in valid_results],
        "volatility": [r.get("volatility") or 0 for r in valid_results],
    }