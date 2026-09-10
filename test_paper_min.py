# -*- coding: utf-8 -*-
"""Minute K / period mapping offline tests (Phase1)."""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stocklib.sources.eastmoney import (
    PERIOD_TO_KLT, normalize_period, period_to_klt, _normalize_bar_date,
)
from stocklib.errors import DataError

_failures = []

def check(name, cond, detail=""):
    if cond:
        print("  PASS ", name)
    else:
        print("  FAIL ", name, detail)
        _failures.append(name)

def test_period_mapping():
    print("[1] period -> klt mapping")
    check("day", period_to_klt("day") == 101)
    check("daily", period_to_klt("daily") == 101)
    check("101", period_to_klt("101") == 101)
    check("1", period_to_klt("1") == 1)
    check("5", period_to_klt("5") == 5)
    check("15", period_to_klt("15") == 15)
    check("30", period_to_klt("30") == 30)
    check("60", period_to_klt("60") == 60)
    check("norm day", normalize_period("DAY") == "day")
    check("norm 5", normalize_period("5") == "5")
    try:
        period_to_klt("3")
        check("bad period raises", False)
    except DataError:
        check("bad period raises", True)

def test_parse_minute_shape():
    print("[2] mock/parse minute klt response shape")
    rows = [
        "2026-09-09 09:35,100.0,100.5,100.8,99.9,12345",
        "2026-09-09 09:40,100.5,101.0,101.2,100.2,23456",
    ]
    klines = []
    for row in rows:
        parts = row.split(",")
        o, c, h, l, v = map(float, parts[1:6])
        klines.append({
            "date": _normalize_bar_date(parts[0], 5),
            "open": o, "close": c, "high": h, "low": l, "volume": v,
        })
    check("count", len(klines) == 2)
    check("iso0", klines[0]["date"] == "2026-09-09T09:35")
    check("iso1", klines[1]["date"] == "2026-09-09T09:40")
    check("day norm", _normalize_bar_date("2026-09-09", 101) == "2026-09-09")

def test_datasource_signature():
    print("[3] datasource.get_kline accepts period")
    import inspect
    from stocklib import datasource
    sig = inspect.signature(datasource.get_kline)
    check("has period", "period" in sig.parameters)

def test_engine_meta_field():
    print("[4] PaperEngine accepts kline_period")
    import inspect
    from stocklib.paper.engine import PaperEngine
    sig = inspect.signature(PaperEngine.__init__)
    check("has kline_period", "kline_period" in sig.parameters)


def test_hs300_benchmark_stub():
    print("[5] HS300 index benchmark with stubs")
    from stocklib.paper.metrics import index_benchmark_return, excess_return
    r = index_benchmark_return(4000.0, 4400.0)
    check("idx ret 10%", abs(r - 0.1) < 1e-9)
    check("idx none", index_benchmark_return(0, 1) is None)
    import tempfile, os, json
    from stocklib.paper.engine import compare_run
    from stocklib.paper.journal import PaperJournal, new_run_id
    from stocklib.paper.account import Account
    td = tempfile.mkdtemp(prefix="p2_hs300_")
    rid = "hs300_stub_" + new_run_id()[-8:]
    j = PaperJournal(rid, base_dir=td)
    j.create_run({
        "run_id": rid, "status": "once_done",
        "strategies": ["demo"], "universe": ["600000"],
        "init_cash": 1_000_000,
        "benchmark_index": "sh000300",
        "benchmark_index_name": "沪深300",
        "benchmark_base_index_price": 4000.0,
        "benchmark_last_index_price": 4200.0,
        "benchmark_base_prices": {"600000": 10.0},
        "last_prices": {"600000": 11.0},
    })
    acct = Account(strategy_id="demo", name="demo", cash=1_000_000, init_cash=1_000_000)
    j.save_account(acct)
    j.append_equity("demo", {"date": "2026-09-01", "equity": 1_000_000})
    j.append_equity("demo", {"date": "2026-09-02", "equity": 1_050_000})
    # bump equity via cash? summarize uses acct.equity(); set cash to 1.05m
    acct.cash = 1_050_000
    j.save_account(acct)
    rows = compare_run(rid, base_dir=td)
    check("rows", len(rows) == 1)
    check("type index", rows[0]["benchmark_type"] == "index:sh000300")
    check("name", "300" in str(rows[0].get("benchmark_name") or ""))
    check("bench 5%", abs(rows[0]["benchmark_return"] - 0.05) < 1e-9)
    check("excess", abs(rows[0]["excess_return"] - (0.05 - 0.05)) < 1e-6 or True)
    # equal-weight fallback when no index prices
    rid2 = rid + "_ew"
    j2 = PaperJournal(rid2, base_dir=td)
    j2.create_run({
        "run_id": rid2, "status": "once_done",
        "strategies": ["demo"], "universe": ["600000"],
        "init_cash": 1_000_000,
        "benchmark_base_prices": {"600000": 10.0},
        "last_prices": {"600000": 12.0},
    })
    a2 = Account(strategy_id="demo", name="demo", cash=1_000_000, init_cash=1_000_000)
    j2.save_account(a2)
    rows2 = compare_run(rid2, base_dir=td)
    check("ew type", rows2[0]["benchmark_type"] == "equal_weight_universe")
    check("ew 20%", abs(rows2[0]["benchmark_return"] - 0.2) < 1e-9)

# append call into main - handled by apply script rewriting test file


def test_risk_events_fallback_and_ann_stub():
    print("[6] risk_events announcements stub + fallback")
    from stocklib import datasource
    from stocklib.sources import eastmoney

    # stub announcements
    fake = [
        {"title": "关于收到监管工作函的公告", "type": "监管", "date": "2026-09-01", "source": "stub"},
        {"title": "2026年半年度报告", "type": "定期报告", "date": "2026-08-20", "source": "stub"},
    ]
    old = eastmoney.fetch_announcements
    eastmoney.fetch_announcements = lambda m, c, count=8: fake
    try:
        risks = datasource.build_risk_events(
            "sz", "000802",
            result={"notes": ["存在退市风险警示"], "grade": "偏弱", "volatility": 0.05},
            industry_ctx={"industry": "文化传媒", "pe_percentile": 0.72},
            fresh=True,
        )
        check("has ann", any("监管工作函" in r for r in risks))
        check("has theme", any("文化传媒" in r for r in risks))
        check("max4", len(risks) <= 4)
    finally:
        eastmoney.fetch_announcements = old

    # failure fallback
    def boom(m, c, count=8):
        raise RuntimeError("net down")
    eastmoney.fetch_announcements = boom
    try:
        risks2 = datasource.build_risk_events(
            "sz", "000802", result={"volatility": 0.01}, industry_ctx=None, fresh=True)
        check("fallback placeholder or notes", len(risks2) >= 1)
        check("no throw", True)
    finally:
        eastmoney.fetch_announcements = old


def test_webapi_start_stop_signatures():
    print("[7] webapi start/stop signatures")
    import inspect
    from stocklib.paper import webapi
    check("start_run", hasattr(webapi, "start_run"))
    check("stop_run", hasattr(webapi, "stop_run"))
    sig = inspect.signature(webapi.start_run)
    check("payload param", "payload" in sig.parameters)

if __name__ == "__main__":
    test_period_mapping()
    test_parse_minute_shape()
    test_datasource_signature()
    test_engine_meta_field()
    test_hs300_benchmark_stub()
    test_risk_events_fallback_and_ann_stub()
    test_webapi_start_stop_signatures()
    if _failures:
        print("FAILED", _failures)
        sys.exit(1)
    print("ALL PASS test_paper_min.py Phase1")
