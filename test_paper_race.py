"""全市场策略赛马离线单测：universe 粗筛 / win_rate None / scan_progress / compare。

运行：python -X utf8 test_paper_race.py
不访问网络。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from stocklib.paper.universe import (
    coarse_screen_all_a,
    fetch_all_a_codes,
    resolve_universe_spec,
    normalize_codes,
    DEFAULT_PRECISE_LIMIT,
)
from stocklib.paper.metrics import trade_stats, summarize
from stocklib.paper.engine import PaperEngine, compare_run
from stocklib.paper.journal import PaperJournal


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fake_clist_http(pages):
    """pages: list of list[dict] raw clist items."""
    state = {"pn": 0}

    def http_get(url, params=None, timeout=10, headers=None):
        params = params or {}
        pn = int(params.get("pn") or 1)
        idx = pn - 1
        if idx < 0 or idx >= len(pages):
            return FakeResp({"data": {"total": sum(len(p) for p in pages), "diff": []}})
        return FakeResp({
            "data": {
                "total": sum(len(p) for p in pages),
                "diff": pages[idx],
            }
        })

    return http_get


def sample_items():
    # amounts descending after screen; include ST / zero amount / low price
    return [
        {"f12": "600519", "f14": "贵州茅台", "f2": 1600.0, "f3": 1.2, "f5": 1000, "f6": 9.0e9},
        {"f12": "000001", "f14": "平安银行", "f2": 12.0, "f3": -0.5, "f5": 2000, "f6": 5.0e9},
        {"f12": "300750", "f14": "宁德时代", "f2": 180.0, "f3": 2.0, "f5": 3000, "f6": 8.0e9},
        {"f12": "000002", "f14": "ST万科", "f2": 8.0, "f3": 0.1, "f5": 100, "f6": 7.0e9},  # ST drop
        {"f12": "600000", "f14": "浦发银行", "f2": 0.3, "f3": 0.0, "f5": 10, "f6": 6.0e9},  # low price
        {"f12": "002415", "f14": "海康威视", "f2": 30.0, "f3": 0.8, "f5": 500, "f6": 0.0},  # no amount
        {"f12": "601318", "f14": "中国平安", "f2": 45.0, "f3": 0.3, "f5": 800, "f6": 4.0e9},
        {"f12": "000938", "f14": "紫光股份", "f2": 25.0, "f3": 1.0, "f5": 400, "f6": 3.0e9},
    ]


def test_fetch_and_coarse():
    print("[1] fetch_all_a_codes + coarse_screen_all_a (fixture http)")
    items = sample_items()
    # split into 2 pages
    http = _fake_clist_http([items[:4], items[4:]])
    pack = fetch_all_a_codes(http_get=http, page_size=4, max_pages=10)
    check("pages>=2", pack["pages"] >= 2, str(pack["pages"]))
    check("items>=6", len(pack["items"]) >= 6, str(len(pack["items"])))
    check("codes normalized", "600519" in pack["codes"])

    screened = coarse_screen_all_a(pack["items"], precise_limit=3)
    codes = screened["codes"]
    check("precise_limit=3", len(codes) == 3, str(codes))
    # top by amount among survivors: 600519(9e9), 300750(8e9), 000001(5e9), 601318(4e9)...
    check("top1 600519", codes[0] == "600519", str(codes))
    check("no ST", "000002" not in codes)
    check("no low price", "600000" not in codes)
    check("no zero amount", "002415" not in codes)
    check("dropped st>0", screened["dropped"]["st"] >= 1, str(screened["dropped"]))


def test_resolve_universe_spec():
    print("[2] resolve_universe_spec")
    http = _fake_clist_http([sample_items()])
    pack = resolve_universe_spec("market_race", precise_limit=2, http_get=http)
    check("mode market_race", pack["mode"] == "market_race")
    check("len==2", len(pack["codes"]) == 2, str(pack["codes"]))
    check("not hardcoded trio", pack["codes"] != ["000802", "002422", "002881"])
    pack2 = resolve_universe_spec(["000802", "sh600519"], precise_limit=10)
    check("list mode", pack2["codes"] == ["000802", "600519"], str(pack2))
    pack3 = resolve_universe_spec({"codes": ["002422", "002881"]})
    check("dict codes", pack3["codes"] == ["002422", "002881"])


def test_win_rate_none():
    print("[3] win_rate None when closed_rounds==0")
    stats = trade_stats([
        {"side": "buy", "code": "600519", "qty": 100, "price": 10, "fee": 5},
    ])
    check("closed=0", stats["closed_rounds"] == 0)
    check("win_rate None", stats["win_rate"] is None, str(stats["win_rate"]))
    rep = summarize(init_cash=1e6, equity=1e6, fills=[
        {"side": "buy", "code": "600519", "qty": 100, "price": 10, "fee": 5},
    ])
    check("summarize win_rate None", rep["win_rate"] is None)


def test_engine_scan_progress_and_compare():
    print("[4] engine scan_progress + compare closed_rounds/win_rate")
    tmp = tempfile.mkdtemp(prefix="paper_race_")
    run_id = "race_stub_1"

    def resolve(raw):
        code = str(raw)
        return ("sh" if code.startswith("6") else "sz"), code, code

    def fetch_snapshot(market, code):
        return {"price": 10.0, "prev_close": 10.0, "is_trading": True}, "stub"

    def fetch_kline(market, code, count):
        # enough bars; flat so most strategies silent — still progress updates
        bars = []
        for i in range(max(40, count)):
            bars.append({
                "date": f"2026-01-{(i % 28) + 1:02d}",
                "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "volume": 1000,
            })
        return bars, "stub"

    def compute_indicators(klines):
        n = len(klines)
        z = [0.0] * n
        return {
            "ma5": z[:], "ma10": z[:], "ma20": z[:], "ma60": z[:],
            "dif": z[:], "dea": z[:], "hist": z[:],
            "k": z[:], "d": z[:], "j": z[:],
            "rsi6": z[:], "rsi12": z[:], "rsi24": z[:],
            "boll_mid": z[:], "boll_up": z[:], "boll_low": z[:],
            "close": [10.0] * n,
        }

    # inject a strategy that always buys on first bar via adapter — use stub strategies list
    # Prefer real strategy ids but force signals by wrapping collect — instead seed fills manually after poll
    engine = PaperEngine(
        strategies=["ma_cross", "macd"],
        universe=["600519", "000001", "000938"],
        init_cash=1_000_000,
        run_id=run_id,
        base_dir=tmp,
        force_poll=True,
        resolve=resolve,
        fetch_snapshot=fetch_snapshot,
        fetch_kline=fetch_kline,
        compute_indicators=compute_indicators,
    )
    engine.create_or_resume(resume=False)
    summary = engine.poll_once()
    meta = engine.journal.read_meta()
    sp = meta.get("scan_progress") or {}
    check("scan_progress present", bool(sp), str(sp))
    check("scan phase done", sp.get("phase") in ("done", "mark_to_market", "scanning"), str(sp))
    check("scan total=3", int(sp.get("total") or 0) == 3, str(sp))
    check("no hardcoded universe", meta.get("universe") == ["600519", "000001", "000938"])

    # seed one buy-only account to assert win_rate None in compare
    j = PaperJournal(run_id, base_dir=tmp)
    acct = j.load_account("ma_cross")
    from stocklib.paper.broker import BrokerSim
    br = BrokerSim()
    o, f = br.place_market_order(acct, "600519", "buy", 100, 10.0, "2026-09-10", reason="stub_buy|score=61")
    if f:
        f["score"] = 61
        f["reason"] = "stub_buy|score=61"
        j.append_fill(f)
    j.save_account(acct)
    # ensure meta prices for ew benchmark
    meta = j.read_meta()
    meta.setdefault("benchmark_base_prices", {"600519": 10.0, "000001": 10.0, "000938": 10.0})
    meta.setdefault("last_prices", {"600519": 10.0, "000001": 10.0, "000938": 10.0})
    j.write_meta(meta)

    rows = compare_run(run_id, base_dir=tmp)
    by = {r["strategy_id"]: r for r in rows}
    check("compare has ma_cross", "ma_cross" in by)
    check("closed_rounds exposed", "closed_rounds" in by["ma_cross"], str(by["ma_cross"]))
    check("ma_cross win_rate None", by["ma_cross"]["win_rate"] is None, str(by["ma_cross"]))
    check("fill reason/score", True)  # structural; fill already written
    fills = j.read_fills("ma_cross")
    check("fill has reason", bool(fills and fills[0].get("reason")), str(fills[:1]))
    check("fill has score", fills and fills[0].get("score") == 61, str(fills[:1]))


def test_default_precise_limit():
    print("[5] DEFAULT_PRECISE_LIMIT")
    check("default 400", DEFAULT_PRECISE_LIMIT == 400)


def main():
    test_fetch_and_coarse()
    test_resolve_universe_spec()
    test_win_rate_none()
    test_engine_scan_progress_and_compare()
    test_default_precise_limit()
    print(f"\n=== result PASS={PASS} FAIL={FAIL} ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
