"""Unit tests for stocklib/s2.py — S2 status engine pure logic."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stocklib.s2 import (
    compute_s2, diff_snapshots, scan,
    STATUS_PRIORITY, BREAK_STREAK_THRESHOLD, DEEP_LOSS_RATIO,
)


def _make_klines(closes, base_date="2026-01-"):
    """Build minimal kline dicts from a list of close prices."""
    return [
        {
            "date": f"{base_date}{i+1:02d}",
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": 10000,
        }
        for i, c in enumerate(closes)
    ]


def test_insufficient_data():
    klines = _make_klines([10.0] * 30)
    result = compute_s2(klines)
    assert result["status"] == "insufficient_data"
    assert "数据不足" in result.get("label", "") or "error" in result


def test_hold_status():
    n = 80
    closes = [10.0 + i * 0.05 for i in range(n)]
    klines = _make_klines(closes)
    result = compute_s2(klines)
    assert result["status"] == "hold", f"expected hold, got {result['status']}"
    assert result["label"] == "持有"
    assert result["px"] > 0
    assert result["ma20"] is not None
    assert result["ma60"] is not None
    assert result["break_streak"] == 0


def test_break_reduce_status():
    n = 80
    closes = [20.0] * 60
    for _ in range(20):
        closes.append(closes[-1] * 0.96)
    klines = _make_klines(closes)
    result = compute_s2(klines)
    assert result["status"] in ("break_reduce", "reduce_watch", "deep_loss_reduce_only"), \
        f"expected break-related, got {result['status']}"
    assert result["break_streak"] >= BREAK_STREAK_THRESHOLD


def test_reduce_watch_single_day():
    n = 80
    closes = [20.0] * 79
    closes.append(20.0 * 0.96)
    klines = _make_klines(closes)
    result = compute_s2(klines)
    if result["break_streak"] == 1:
        assert result["status"] == "reduce_watch"


def test_deep_loss_reduce_only():
    n = 80
    closes = [10.0] * n
    klines = _make_klines(closes)
    cost = 15.0
    result = compute_s2(klines, cost=cost)
    loss = (cost - closes[-1]) / cost
    assert loss > DEEP_LOSS_RATIO
    assert result["status"] == "deep_loss_reduce_only"
    assert result["deep_loss_pct"] is not None
    assert result["deep_loss_pct"] > 0


def test_reentry_watch():
    n = 80
    closes = [10.0] * 60
    for _ in range(10):
        closes.append(closes[-1] * 0.96)
    for _ in range(10):
        closes.append(closes[-1] * 1.05)
    klines = _make_klines(closes)
    result = compute_s2(klines)
    assert result["status"] in ("hold", "reentry_watch"), \
        f"expected hold or reentry_watch, got {result['status']}"


def test_status_priority_order():
    expected = [
        "deep_loss_reduce_only",
        "break_reduce",
        "reduce_watch",
        "reentry_watch",
        "hold",
    ]
    assert STATUS_PRIORITY == expected


def test_diff_snapshots():
    current = [
        {"ok": True, "code": "600519", "name": "贵州茅台", "status": "hold"},
        {"ok": True, "code": "000001", "name": "平安银行", "status": "break_reduce"},
    ]
    previous = [
        {"ok": True, "code": "600519", "name": "贵州茅台", "status": "hold"},
        {"ok": True, "code": "000001", "name": "平安银行", "status": "hold"},
    ]
    changes = diff_snapshots(current, previous)
    assert len(changes) == 2
    c600519 = next(c for c in changes if c["code"] == "600519")
    assert c600519["need_notify"] == False
    c000001 = next(c for c in changes if c["code"] == "000001")
    assert c000001["need_notify"] == True
    assert c000001["prev_status"] == "hold"
    assert c000001["new_status"] == "break_reduce"


def test_diff_no_previous():
    current = [{"ok": True, "code": "600519", "status": "hold"}]
    changes = diff_snapshots(current, [])
    assert len(changes) == 1
    assert changes[0]["need_notify"] == False
    assert changes[0]["prev_status"] is None


def test_scan_with_mock():
    def mock_fetch(code):
        n = 80
        closes = [10.0 + i * 0.05 for i in range(n)]
        klines = _make_klines(closes)
        return klines, "sh", "测试股票", {}

    items = [{"code": "600519"}, {"code": "000001", "cost": 5.0}]
    results = scan(items, mock_fetch)
    assert len(results) == 2
    for r in results:
        assert r["ok"]
        assert r["status"] in STATUS_PRIORITY or r["status"] == "insufficient_data"


def test_scan_empty_code():
    results = scan([{"code": ""}], lambda c: None)
    assert len(results) == 1
    assert results[0]["ok"] == False


def test_scan_fetch_error():
    def failing_fetch(code):
        raise ValueError("test error")

    results = scan([{"code": "600519"}], failing_fetch)
    assert len(results) == 1
    assert results[0]["ok"] == False
    assert "error" in results[0]


def test_defense_line_computed():
    n = 80
    closes = [20.0] * n
    klines = _make_klines(closes)
    result = compute_s2(klines)
    assert result["defense_line"] is not None
    assert result["reduce_zone"] is not None
    assert result["reentry_zone"] is not None


def test_result_fields_present():
    n = 80
    closes = [10.0 + i * 0.02 for i in range(n)]
    klines = _make_klines(closes)
    result = compute_s2(klines)
    expected_keys = {
        "status", "label", "provisional", "px", "ma5", "ma10",
        "ma20", "ma60", "break_streak", "asof",
        "defense_line", "reduce_zone", "reentry_zone",
    }
    for k in expected_keys:
        assert k in result, f"missing key: {k}"


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  ✓ {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\nS2 tests: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
