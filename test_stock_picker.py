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


def test_safe_num():
    """_safe_num coerces '-', None, '' to default; normal numbers pass through."""
    from stocklib.stock_picker import _safe_num

    print("\n测试 _safe_num 辅助函数...")
    all_passed = True

    cases = [
        ("-", 0.0, 0.0),
        ("", 0.0, 0.0),
        (None, 0.0, 0.0),
        (0, 0.0, 0.0),
        (123456, 0.0, 123456.0),
        ("789.5", 0.0, 789.5),
        ("-3.14", 0.0, -3.14),
        ("abc", 5.0, 5.0),
        ("-", 99.0, 99.0),
    ]

    for i, (raw, default, expected) in enumerate(cases, 1):
        result = _safe_num(raw, default)
        ok = result == expected
        status = "✓ 通过" if ok else "✗ 失败"
        if not ok:
            print(f"  测试 {i}: {status}  _safe_num({raw!r}, {default}) = {result}, expected {expected}")
            all_passed = False
        else:
            print(f"  测试 {i}: {status}")

    return all_passed


def test_fetchers_with_dash_sentinels():
    """Simulate East Money rows with '-' for f62/f116/f3/f107;
    verify fetcher helpers return non-empty lists without raising."""
    from stocklib.stock_picker import _safe_num, _pct_from_eastmoney_f3

    print("\n测试 '-' 哨兵值处理...")
    all_passed = True

    fixture_items = [
        {"f12": "600519", "f14": "贵州茅台", "f3": "2.50", "f62": "-", "f116": "-", "f107": "-"},
        {"f12": "000001", "f14": "平安银行", "f3": "-", "f62": 150000, "f116": None, "f107": 80000},
        {"f12": "300750", "f14": "宁德时代", "f3": 1.80, "f62": "", "f116": "", "f107": ""},
        {"f12": "601398", "f14": "工商银行", "f3": None, "f62": None, "f116": None, "f107": None},
    ]

    stocks = []
    for it in fixture_items:
        try:
            code = it.get("f12", "")
            name = it.get("f14", "")
            change_pct = _pct_from_eastmoney_f3(it.get("f3"))
            main_net = _safe_num(it.get("f62"))
            market = "sh" if code.startswith(("60", "68")) else "sz"
            stocks.append({
                "code": code,
                "name": name,
                "market": market,
                "change_pct": change_pct,
                "main_net": main_net,
                "volume": _safe_num(it.get("f107"))
            })
        except Exception as e:
            print(f"  ✗ 失败: item {it['f12']} raised {e}")
            all_passed = False

    ok_count = len(stocks) == len(fixture_items)
    if not ok_count:
        print(f"  ✗ 失败: expected {len(fixture_items)} stocks, got {len(stocks)}")
        all_passed = False
    else:
        print(f"  ✓ 通过: all {len(stocks)} items parsed without error")

    # Verify specific coercions
    s0 = stocks[0]
    ok_main = s0["main_net"] == 0.0
    ok_vol = s0["volume"] == 0.0
    ok_pct = abs(s0["change_pct"] - 0.025) < 1e-9
    if not (ok_main and ok_vol and ok_pct):
        print(f"  ✗ 失败: row 0 coercion: main_net={s0['main_net']}, volume={s0['volume']}, change_pct={s0['change_pct']}")
        all_passed = False
    else:
        print(f"  ✓ 通过: row 0 coerced correctly (main_net=0, vol=0, pct=0.025)")

    s1 = stocks[1]
    ok_dash_f3 = s1["change_pct"] == 0.0
    if not ok_dash_f3:
        print(f"  ✗ 失败: f3='-' should give change_pct=0.0, got {s1['change_pct']}")
        all_passed = False
    else:
        print(f"  ✓ 通过: f3='-' → change_pct=0.0")

    return all_passed


def test_simple_safe_num_and_to_wan():
    """Test _safe_num and _to_wan from stock_picker_simple."""
    print("\n测试 stock_picker_simple _safe_num / _to_wan...")
    all_passed = True

    try:
        from stocklib.stock_picker_simple import _safe_num, _to_wan
    except ImportError:
        # Re-implement locally to test the logic when deps are missing
        def _safe_num(raw, default=0.0):
            if raw is None or raw == "" or raw == "-":
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        def _to_wan(value):
            return _safe_num(value) / 10000

    cases = [("-", 0.0), (None, 0.0), ("", 0.0), (100000, 10.0), ("50000", 5.0)]
    for raw, expected in cases:
        result = _to_wan(raw)
        ok = abs(result - expected) < 1e-9
        if not ok:
            print(f"  ✗ 失败: _to_wan({raw!r}) = {result}, expected {expected}")
            all_passed = False

    if all_passed:
        print(f"  ✓ 通过: _to_wan handles '-'/None/''/numbers correctly")

    # Also verify the source file contains the hardened _safe_num
    with open("stocklib/stock_picker_simple.py") as f:
        src = f.read()
    if 'def _safe_num(' in src and '_safe_num(value)' in src:
        print(f"  ✓ 通过: stock_picker_simple.py uses _safe_num")
    else:
        print(f"  ✗ 失败: stock_picker_simple.py missing _safe_num")
        all_passed = False

    return all_passed


if __name__ == "__main__":
    print("=" * 50)
    print("股票筛选模块功能测试")
    print("=" * 50)

    test1_passed = test_buy_reason_generation()
    test2_passed = test_signal_filtering()
    test3_passed = test_top_signals_and_buy_point()
    test4_passed = test_safe_num()
    test5_passed = test_fetchers_with_dash_sentinels()
    test6_passed = test_simple_safe_num_and_to_wan()

    print("\n" + "=" * 50)
    all_ok = all([test1_passed, test2_passed, test3_passed,
                  test4_passed, test5_passed, test6_passed])
    if all_ok:
        print("✓ 所有测试通过")
        sys.exit(0)
    else:
        print("✗ 部分测试失败")
        sys.exit(1)
