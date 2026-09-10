"""P3 模拟盘离线单测：score_gate / universe-file / 基准超额。

运行：python -X utf8 test_paper_p3.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stocklib.backtest import STRATEGIES, ScoreGateStrategy, score_gate_side
from stocklib.paper.engine import PaperEngine, compare_run
from stocklib.paper.metrics import equal_weight_benchmark_return, excess_return
from stocklib.paper.universe import (
    load_universe_file,
    save_universe,
    normalize_codes,
    merge_universe,
    default_universe_path,
)
from stocklib.paper.strategies_adapter import list_strategy_ids, make_strategy

_failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def test_score_gate_side():
    print("[1] score_gate_side thresholds")
    check("buy at 60", score_gate_side(60, 60, 40) == "buy")
    check("buy above", score_gate_side(75.5, 60, 40) == "buy")
    check("sell at 40", score_gate_side(40, 60, 40) == "sell")
    check("sell below", score_gate_side(10, 60, 40) == "sell")
    check("hold mid", score_gate_side(50, 60, 40) is None)
    check("bad score", score_gate_side("x", 60, 40) is None)
    check("registered", "score_gate" in STRATEGIES)
    check("list_strategy_ids", "score_gate" in list_strategy_ids())
    strat = make_strategy("score_gate")
    check("make_strategy type", isinstance(strat, ScoreGateStrategy))
    check("defaults", strat.buy_threshold == 60.0 and strat.sell_threshold == 40.0)


def test_score_gate_signals_stubbed():
    print("[2] score_gate generate_signals with stubbed analyzer score")
    # Build minimal klines
    klines = []
    px = 10.0
    for i in range(50):
        px *= 1.001
        klines.append({
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": px, "high": px * 1.01, "low": px * 0.99,
            "close": px, "volume": 1000 + i,
        })

    # Monkeypatch analyzer path inside strategy
    import stocklib.analyzer as analyzer
    import stocklib.backtest as bt

    scores_cycle = [55.0, 62.0, 35.0]  # hold, buy, sell on successive evals
    state = {"i": 0}

    def fake_compute(window):
        return {"closes": [k["close"] for k in window], "ma5": [None] * len(window)}

    def fake_build_signals(x, snapshot, finance, industry_ctx=None):
        return ([{"category": "trend", "score": 1, "name": "stub"}], [])

    def fake_score(sigs):
        s = scores_cycle[state["i"] % len(scores_cycle)]
        state["i"] += 1
        grade = "强势看多" if s >= 60 else ("偏弱" if s <= 40 else "中性")
        return s, grade, {"trend": 0.5}

    old = {
        "compute_indicators": analyzer.compute_indicators,
        "build_signals": analyzer.build_signals,
        "score": analyzer.score,
        "SCORE_VERSION": getattr(analyzer, "SCORE_VERSION", "1.3"),
    }
    analyzer.compute_indicators = fake_compute
    analyzer.build_signals = fake_build_signals
    analyzer.score = fake_score
    try:
        # step=100 so only last bar (+ maybe first sample) — force only last by huge step
        strat = ScoreGateStrategy(buy_threshold=60, sell_threshold=40, min_bars=40, step=999)
        sigs = strat.generate_signals(klines, x=fake_compute(klines))
        # only last bar evaluated once with first score in cycle after any prior? 
        # With step=999 and min_bars=40: indices = [40, 41, ...] wait range(40,50,999)=[40], then append 49
        # so two evals: scores 55 (hold) then 62 (buy)
        sides = [s["type"] for s in sigs]
        check("has buy from stub", "buy" in sides, str(sigs))
        buy = [s for s in sigs if s["type"] == "buy"][0]
        check("score attached", buy.get("score") == 62.0, str(buy))
        check("score_version attached", bool(buy.get("score_version")), str(buy))
        check("reason mentions score_gate", "score_gate" in str(buy.get("reason")), str(buy))

        # Force sell on last bar only
        state["i"] = 0
        scores_cycle[:] = [30.0]
        strat2 = ScoreGateStrategy(buy_threshold=60, sell_threshold=40, min_bars=49, step=1)
        sigs2 = strat2.generate_signals(klines, x=None)
        check("sell signal", any(s["type"] == "sell" for s in sigs2), str(sigs2))
        sell = [s for s in sigs2 if s["type"] == "sell"][0]
        check("sell score", sell.get("score") == 30.0, str(sell))
    finally:
        analyzer.compute_indicators = old["compute_indicators"]
        analyzer.build_signals = old["build_signals"]
        analyzer.score = old["score"]


def test_universe_file():
    print("[3] universe file load/save")
    tmp = tempfile.mkdtemp(prefix="paper_p3_uni_")
    txt = os.path.join(tmp, "codes.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write("# comment\n600519\n000938\nsh600000\n600519\n")
    codes = load_universe_file(txt)
    check("txt load unique", codes == ["600519", "000938", "600000"], str(codes))

    js = os.path.join(tmp, "u.json")
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"codes": ["000001", "sz000002", "bad"]}, f)
    codes2 = load_universe_file(js)
    check("json load", codes2 == ["000001", "000002"], str(codes2))

    js_list = os.path.join(tmp, "list.json")
    with open(js_list, "w", encoding="utf-8") as f:
        json.dump(["600519", "300750"], f)
    check("json list", load_universe_file(js_list) == ["600519", "300750"])

    out = os.path.join(tmp, "universe.json")
    payload = save_universe(["600519", "000938"], path=out, source="test")
    check("save count", payload["count"] == 2)
    check("reload", load_universe_file(out) == ["600519", "000938"])
    check("normalize", normalize_codes(["600519.SH", {"code": "000938"}]) == ["600519", "000938"])
    check("merge union", merge_universe(["600519"], ["000938", "600519"], mode="union") == ["600519", "000938"])
    check("default path ends universe.json", default_universe_path(tmp).endswith("universe.json"))


def test_benchmark_and_compare():
    print("[4] benchmark excess + compare shape")
    base = {"600519": 100.0, "000938": 50.0}
    cur = {"600519": 110.0, "000938": 45.0}
    # equal weight: (+10% + -10%)/2 = 0
    br = equal_weight_benchmark_return(base, cur)
    check("bench ~0", abs(br) < 1e-9, str(br))
    check("excess", excess_return(0.05, br) == 0.05)

    base2 = {"A": 10.0, "B": 20.0}
    cur2 = {"A": 12.0, "B": 22.0}
    # A +20%, B +10% -> 15%
    br2 = equal_weight_benchmark_return(base2, cur2)
    check("bench 0.15", abs(br2 - 0.15) < 1e-6, str(br2))

    tmp = tempfile.mkdtemp(prefix="paper_p3_cmp_")
    run_id = "p3_bench_stub"

    class FakeHold:
        def __init__(self):
            self.name = "fake_hold"

        def generate_signals(self, klines, x):
            return []  # no trades

    STRATEGIES["fake_hold_p3"] = FakeHold
    try:
        klines = []
        px = 10.0
        for i in range(50):
            px *= 1.002
            klines.append({
                "date": f"2026-02-{(i % 28) + 1:02d}",
                "open": px, "high": px * 1.01, "low": px * 0.99,
                "close": round(px, 4), "volume": 1000,
            })
        price = float(klines[-1]["close"])

        def resolve(q):
            q = str(q)
            return ("sh" if q.startswith("6") else "sz"), q, f"N{q}"

        def fetch_snapshot(market, code):
            return ({
                "code": code, "name": f"N{code}", "price": price,
                "prev_close": price * 0.99, "is_trading": True,
            }, "stub")

        def fetch_kline(market, code, count=180):
            return (klines[-count:], "stub")

        def compute_indicators(ks):
            n = len(ks)
            return {"closes": [k["close"] for k in ks], "ma5": [None] * (n - 1) + [ks[-1]["close"]]}

        engine = PaperEngine(
            strategies=["fake_hold_p3"],
            universe=["600519", "000938"],
            init_cash=1_000_000,
            poll_sec=60,
            run_id=run_id,
            base_dir=tmp,
            force_poll=True,
            resolve=resolve,
            fetch_snapshot=fetch_snapshot,
            fetch_kline=fetch_kline,
            compute_indicators=compute_indicators,
        )
        result = engine.run(once=True, force=True)
        check("poll ok", not result.get("skipped"), str(result))
        meta = engine.journal.read_meta()
        check("base prices stored", bool(meta.get("benchmark_base_prices")), str(meta.get("benchmark_base_prices")))
        check("last prices stored", bool(meta.get("last_prices")), str(meta.get("last_prices")))
        check("phase P3", meta.get("phase") == "P3", str(meta.get("phase")))

        # Simulate price move for excess: rewrite last_prices up 10%
        base_p = dict(meta["benchmark_base_prices"])
        last_p = {c: round(v * 1.10, 4) for c, v in base_p.items()}
        meta["last_prices"] = last_p
        engine.journal.write_meta(meta)

        rows = compare_run(run_id, base_dir=tmp)
        check("compare rows", len(rows) == 1, str(rows))
        row = rows[0]
        check("has benchmark_return", "benchmark_return" in row, str(row))
        check("has excess_return", "excess_return" in row, str(row))
        check("benchmark_type", row.get("benchmark_type") == "equal_weight_universe")
        check("bench ~0.10", abs(row["benchmark_return"] - 0.10) < 1e-4, str(row["benchmark_return"]))
        # no trades -> strategy return ~0, excess ~ -0.10
        check("excess ~-0.10", abs(row["excess_return"] - (row["total_return"] - row["benchmark_return"])) < 1e-9, str(row))
    finally:
        STRATEGIES.pop("fake_hold_p3", None)


def test_score_gate_in_engine_stub():
    print("[5] score_gate through engine with stub strategy scores")
    tmp = tempfile.mkdtemp(prefix="paper_p3_sg_")
    run_id = "p3_sg_stub"

    class StubScoreGate:
        """替代真实 ScoreGate，避免重算 analyzer；信号带 score。"""
        def __init__(self, buy_threshold=60, sell_threshold=40):
            self.name = "stub_score_gate"
            self.buy_threshold = buy_threshold
            self.sell_threshold = sell_threshold

        def generate_signals(self, klines, x):
            last = len(klines) - 1
            score = 72.0
            return [{
                "type": "buy",
                "date": klines[last]["date"],
                "price": klines[last]["close"],
                "reason": f"score_gate买入(分{score}>={self.buy_threshold})",
                "k_index": last,
                "score": score,
                "score_version": "1.3",
            }]

    STRATEGIES["score_gate"] = StubScoreGate  # temp override for engine path
    try:
        klines = []
        px = 10.0
        for i in range(50):
            px *= 1.001
            klines.append({
                "date": f"2026-03-{(i % 28) + 1:02d}",
                "open": px, "high": px * 1.01, "low": px * 0.99,
                "close": round(px, 4), "volume": 1000,
            })
        price = float(klines[-1]["close"])

        def resolve(q):
            return ("sh", str(q), "N")

        def fetch_snapshot(market, code):
            return ({"price": price, "prev_close": price * 0.99, "is_trading": True}, "stub")

        def fetch_kline(market, code, count=180):
            return (klines[-count:], "stub")

        def compute_indicators(ks):
            n = len(ks)
            return {"ma5": [None] * (n - 1) + [ks[-1]["close"]], "dif": [None] * (n - 1) + [0.1]}

        engine = PaperEngine(
            strategies=["score_gate"],
            universe=["600519"],
            init_cash=1_000_000,
            run_id=run_id,
            base_dir=tmp,
            force_poll=True,
            resolve=resolve,
            fetch_snapshot=fetch_snapshot,
            fetch_kline=fetch_kline,
            compute_indicators=compute_indicators,
        )
        result = engine.run(once=True, force=True)
        check("sg signals", result.get("signals", 0) >= 1, str(result))
        check("sg fills", result.get("fills", 0) >= 1, str(result))
        sigs = engine.journal.read_jsonl("signals.jsonl")
        check("sig score", sigs and sigs[0].get("score") == 72.0, str(sigs[:1]))
        check("sig score_version", bool(sigs and sigs[0].get("score_version")), str(sigs[:1]))
    finally:
        # restore real ScoreGateStrategy
        from stocklib.backtest import ScoreGateStrategy as RealSG
        STRATEGIES["score_gate"] = RealSG


def main():
    print("=== test_paper_p3 ===")
    test_score_gate_side()
    test_score_gate_signals_stubbed()
    test_universe_file()
    test_benchmark_and_compare()
    test_score_gate_in_engine_stub()
    print("---")
    if _failures:
        print(f"FAILED {len(_failures)}: {_failures}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
