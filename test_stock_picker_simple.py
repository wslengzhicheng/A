"""简单测试股票筛选功能，不依赖网络"""
import sys
sys.path.insert(0, ".")


def test_non_network_functions():
    print("测试不依赖网络的功能...")

    from stocklib.stock_picker import (
        generate_buy_reason, MIN_SCORE, MAX_SCORE, MIN_MAIN_NET,
        MIN_DAILY_CHANGE, MAX_DAILY_CHANGE,
    )

    test_result = {
        "success": True,
        "score": 65,
        "name": "测试股票",
        "signals": [
            {"name": "均线排列", "score": 2, "text": "均线多头排列"},
            {"name": "MACD", "score": 1, "text": "DIF在DEA上方"},
        ],
        "main_net": 5000,
        "prediction": "上行",
        "confidence": "中",
    }

    reason = generate_buy_reason(test_result)
    print(f"买入理由生成测试: {reason}")
    assert "走势预测上行" in reason

    print("\n测试评分筛选逻辑...")
    test_cases = [
        {"score": 75, "main_net": 5000, "change_pct": 0.06, "expected": True},
        {"score": 55, "main_net": 6000, "change_pct": 0.05, "expected": False},
        {"score": 85, "main_net": 4000, "change_pct": 0.09, "expected": False},
        {"score": 70, "main_net": 500, "change_pct": 0.04, "expected": False},
    ]

    for i, case in enumerate(test_cases, 1):
        is_valid = (
            MIN_SCORE <= case["score"] <= MAX_SCORE and
            MIN_DAILY_CHANGE <= case["change_pct"] <= MAX_DAILY_CHANGE and
            case["main_net"] >= MIN_MAIN_NET
        )
        passed = is_valid == case["expected"]
        print(f"测试 {i}: {'✓ 通过' if passed else '✗ 失败'}")
        assert passed

    print("\n所有基础功能测试完成！")


if __name__ == "__main__":
    test_non_network_functions()
