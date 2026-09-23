"""Unit tests for stocklib/ocr_portfolio.py — position parsing logic."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stocklib.ocr_portfolio import parse_positions, _extract_name_near_code


def test_parse_six_digit_codes():
    text = "600519 贵州茅台 1000 1000 1800.50\n000001 平安银行 2000 500 15.20"
    positions = parse_positions(text)
    codes = {p["code"] for p in positions}
    assert "600519" in codes
    assert "000001" in codes
    assert len(positions) == 2


def test_parse_no_codes():
    positions = parse_positions("这段文字没有股票代码")
    assert len(positions) == 0


def test_parse_empty():
    assert parse_positions("") == []
    assert parse_positions(None) == []


def test_code_prefix_filter():
    text = "600519 OK\n999999 not A-share\n300750 OK"
    positions = parse_positions(text)
    codes = {p["code"] for p in positions}
    assert "600519" in codes
    assert "300750" in codes
    assert "999999" not in codes


def test_dedup_codes():
    text = "600519 贵州茅台 100\n600519 again"
    positions = parse_positions(text)
    assert len(positions) == 1


def test_extract_name():
    name = _extract_name_near_code("600519 贵州茅台 1000", "600519")
    assert name == "贵州茅台"


def test_extract_name_no_chinese():
    name = _extract_name_near_code("600519 1000 500", "600519")
    assert name is None


def test_numbers_extracted():
    text = "600519 贵州茅台 1000 500 1800.50"
    positions = parse_positions(text)
    assert len(positions) == 1
    p = positions[0]
    assert p["code"] == "600519"
    assert p.get("qty") == 1000
    assert p.get("available") == 500
    assert p.get("cost") == 1800.50


def test_mixed_format():
    text = """持仓明细
代码    名称    数量  可用  成本价
600036  招商银行 3000  3000  42.15
300750  宁德时代 500   0     210.30
"""
    positions = parse_positions(text)
    codes = {p["code"] for p in positions}
    assert "600036" in codes
    assert "300750" in codes


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
    print(f"\nOCR parse tests: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
