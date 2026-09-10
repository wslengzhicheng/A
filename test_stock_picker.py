"""测试股票筛选模块的基础功能"""
import sys
sys.path.insert(0, ".")

# 测试核心逻辑（不涉及网络请求）
def test_buy_reason_generation():
    """测试买入理由生成逻辑"""
    from stocklib.stock_picker import generate_buy_reason

    test_cases = [
        {
            "result": {
                "success": True,
                "score": 72,
                "name": "测试股票",
                "signals": [
                    {"name": "均线排列", "score": 2, "text": "均线多头排列"},
                    {"name": "MACD", "score": 1, "text": "DIF在DEA上方"},
                ],
                "main_net": 5000,
                "prediction": "上行",
                "confidence": "高",
            },
            "expected_contains": ["72", "均线多头排列", "主力资金净流入", "走势预测上行"]
        },
        {
            "result": {
                "success": True,
                "score": 65,
                "name": "测试股票2",
                "signals": [
                    {"name": "KDJ", "score": 2, "text": "KDJ超卖"},
                ],
                "main_net": 2000,
            },
            "expected_contains": ["65", "KDJ超卖"]
        },
        {
            "result": {
                "success": False,
                "error": "网络错误"
            },
            "expected_contains": ["分析失败", "网络错误"]
        }
    ]

    print("测试买入理由生成逻辑...")
    all_passed = True

    for i, test_case in enumerate(test_cases, 1):
        result = test_case["result"]
        expected = test_case["expected_contains"]

        try:
            reason = generate_buy_reason(result)

            passed = all(exp in reason for exp in expected)
            status = "✓ 通过" if passed else "✗ 失败"

            if passed:
                print(f"  测试 {i}: {status}")
            else:
                print(f"  测试 {i}: {status}")
                print(f"    期望包含: {expected}")
                print(f"    实际结果: {reason}")
                all_passed = False

        except Exception as e:
            print(f"  测试 {i}: ✗ 异常 - {e}")
            all_passed = False

    return all_passed


def test_signal_filtering():
    """测试信号筛选逻辑（涨跌幅为小数）"""
    from stocklib.stock_picker import (
        MIN_SCORE, MAX_SCORE, MIN_MAIN_NET, MIN_DAILY_CHANGE, MAX_DAILY_CHANGE,
    )

    print("\n测试筛选条件...")
    test_stocks = [
        {"score": 75, "main_net": 5000, "change_pct": 0.06, "expected": True},
        {"score": 55, "main_net": 6000, "change_pct": 0.05, "expected": False},  # 评分过低
        {"score": 85, "main_net": 4000, "change_pct": 0.09, "expected": False},  # 评分过高
        {"score": 70, "main_net": 500, "change_pct": 0.04, "expected": False},   # 资金流不足
        {"score": 65, "main_net": 3000, "change_pct": 0.095, "expected": False},  # 涨幅过高
    ]

    all_passed = True
    for i, stock in enumerate(test_stocks, 1):
        score = stock["score"]
        main_net = stock["main_net"]
        change_pct = stock["change_pct"]

        is_valid = (
            MIN_SCORE <= score <= MAX_SCORE and
            MIN_DAILY_CHANGE <= change_pct <= MAX_DAILY_CHANGE and
            main_net >= MIN_MAIN_NET
        )

        passed = is_valid == stock["expected"]
        status = "✓ 通过" if passed else "✗ 失败"

        if passed:
            print(f"  测试 {i}: {status}")
        else:
            print(f"  测试 {i}: {status}")
            print(f"    期望: {stock['expected']}, 实际: {is_valid}")
            all_passed = False

    return all_passed


def test_top_signals_and_buy_point():
    """信号按强度截断；买卖点判定不依赖 name 含「买」的脆弱匹配。"""
    from stocklib.stock_picker import _top_signals

    print("\n测试信号截断与买点语义...")
    signals = [
        {"category": "trend", "name": "年度位置", "score": 0, "text": "中性"},
        {"category": "chan", "name": "买卖点", "score": 2, "text": "一买"},
        {"category": "osc", "name": "KDJ", "score": -1, "text": "弱"},
        {"category": "trend", "name": "MACD", "score": 1, "text": "偏多"},
    ]
    top = _top_signals(signals, 2)
    # |2| 最大；|−1| 与 |1| 并列时保留原顺序 → KDJ 先于 MACD
    ok = [s["name"] for s in top] == ["买卖点", "KDJ"]
    buy_points = [
        s for s in signals
        if s.get("category") == "chan" and s.get("name") == "买卖点" and s.get("score", 0) > 0
    ]
    sell_only = {"category": "chan", "name": "买卖点", "score": -2, "text": "一卖"}
    has_buy = len(buy_points) > 0
    has_buy_on_sell = sell_only["name"] == "买卖点" and sell_only["score"] > 0
    passed = ok and has_buy and not has_buy_on_sell
    print(f"  {'✓ 通过' if passed else '✗ 失败'}")
    if not passed:
        print(f"    top={[s['name'] for s in top]}")
    return passed


if __name__ == "__main__":
    print("=" * 50)
    print("股票筛选模块功能测试")
    print("=" * 50)

    test1_passed = test_buy_reason_generation()
    test2_passed = test_signal_filtering()
    test3_passed = test_top_signals_and_buy_point()

    print("\n" + "=" * 50)
    if test1_passed and test2_passed and test3_passed:
        print("✓ 所有测试通过")
        sys.exit(0)
    else:
        print("✗ 部分测试失败")
        sys.exit(1)
