"""股票筛选演示版本 - 使用示例数据"""

def get_demo_candidates():
    """获取候选股票的示例数据"""
    return [
        {
            "code": "600519",
            "name": "贵州茅台",
            "market": "sh",
            "change_pct": 0.03,
            "main_net": 5000,
            "reason": "温和上涨"
        },
        {
            "code": "000001",
            "name": "平安银行",
            "market": "sz",
            "change_pct": 0.025,
            "main_net": 3000,
            "reason": "温和上涨"
        },
        {
            "code": "300750",
            "name": "宁德时代",
            "market": "sz",
            "change_pct": -0.02,
            "main_net": 2000,
            "reason": "超跌反弹"
        },
        {
            "code": "002714",
            "name": "牧原股份",
            "market": "sz",
            "change_pct": 0.04,
            "main_net": 8000,
            "reason": "温和上涨"
        },
        {
            "code": "600036",
            "name": "招商银行",
            "market": "sh",
            "change_pct": 0.015,
            "main_net": 4000,
            "reason": "温和上涨"
        }
    ]

def analyze_demo_candidate(stock):
    """分析候选股票（演示版本）"""
    # 模拟评分计算
    score = 50 + abs(stock["change_pct"]) * 1000 + stock["main_net"] / 1000

    # 添加一些随机因素
    if stock["code"] == "600519":
        score += 20  # 茅台加分
    elif stock["code"] == "000001":
        score += 10  # 平安银行加分

    score = min(score, 100)  # 最高100分

    return {
        "success": True,
        "code": stock["code"],
        "name": stock["name"],
        "market": stock["market"],
        "price": 100 + stock["change_pct"] * 1000,  # 模拟价格
        "change_pct": stock["change_pct"],
        "score": round(score, 1),
        "reason": "基于涨跌、资金流入的综合评分",
        "has_buy_point": score >= 65,
        "signals": [
            {"name": "均线排列", "score": 1, "text": "短期均线向上"},
            {"name": "MACD", "score": 1, "text": "MACD金叉"}
        ]
    }

def find_buy_opportunities_demo():
    """寻找买入机会（演示版本）"""
    import time
    print("[筛选] 开始获取候选股票...")
    candidates = get_demo_candidates()
    print(f"[筛选] 获取到 {len(candidates)} 只候选股票")

    opportunities = []
    signals = []

    for candidate in candidates:
        print(f"[筛选] 分析: {candidate['name']}({candidate['code']})")

        result = analyze_demo_candidate(candidate)

        # 筛选条件：评分60-80分
        score = result.get("score", 0)
        if 60 <= score <= 80:
            result["buy_reason"] = f"综合评分{score}分，{result['reason']}，主力资金净流入{candidate['main_net']:.0f}万元"
            result["candidate_reason"] = candidate.get("reason", "")

            # 统计信号
            bullish_signals = [s for s in result.get("signals", []) if s.get("score", 0) > 0]
            result["bullish_count"] = len(bullish_signals)
            result["bearish_count"] = 0

            opportunities.append(result)

    # 按评分排序
    opportunities.sort(key=lambda x: x.get("score", 0), reverse=True)

    return {
        "timestamp": int(time.time()),
        "candidates_count": len(candidates),
        "analyzed_count": len([r for r in opportunities if r.get("success")]),
        "opportunities": opportunities,
        "summary": {
            "total_analyzed": len(candidates),
            "success_count": len([r for r in opportunities if r.get("success")]),
            "failed_count": 0,
            "opportunities_count": len(opportunities),
            "avg_score": round(sum(o.get("score", 0) for o in opportunities) / len(opportunities), 2) if opportunities else 0
        }
    }

if __name__ == "__main__":
    result = find_buy_opportunities_demo()
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))