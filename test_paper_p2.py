"""P2 模拟盘离线单测：续跑 / 学习字段 / Web 只读路由。

运行：python -X utf8 test_paper_p2.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stocklib.backtest import STRATEGIES
from stocklib.paper import PaperJournal, learning_fields, snapshot_indicators
from stocklib.paper.engine import PaperEngine
from stocklib.paper.learning import backfill_run_learning, attach_learning
from stocklib.paper import webapi as paper_webapi
import web as webmod

_failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def _fake_klines(n=60, start_price=10.0):
    rows = []
    px = start_price
    for i in range(n):
        if i < n // 2:
            px *= 0.995
        else:
            px *= 1.006
        date = f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        rows.append({
            "date": date,
            "open": round(px * 0.99, 4),
            "high": round(px * 1.01, 4),
            "low": round(px * 0.98, 4),
            "close": round(px, 4),
            "volume": 10000 + i * 10,
        })
    return rows


def _stub_data(klines, price=None):
    price = float(price if price is not None else klines[-1]["close"])

    def resolve(q):
        q = str(q)
        market = "sh" if q.startswith("6") else "sz"
        return market, q, f"NAME{q}"

    def fetch_snapshot(market, code):
        return ({
            "code": code,
            "name": f"NAME{code}",
            "price": price,
            "prev_close": price * 0.99,
            "is_trading": True,
        }, "stub")

    def fetch_kline(market, code, count=180):
        return (klines[-count:], "stub")

    def compute_indicators(ks):
        n = len(ks)
        closes = [k["close"] for k in ks]
        # 末值有数，便于 snapshot_indicators
        ma5 = [None] * n
        ma5[-1] = closes[-1]
        dif = [None] * n
        dif[-1] = 0.12
        dea = [None] * n
        dea[-1] = 0.08
        return {
            "closes": closes,
            "ma5": ma5,
            "ma10": [None] * (n - 1) + [closes[-1] * 0.99],
            "ma20": [None] * n,
            "dif": dif,
            "dea": dea,
            "hist": [None] * (n - 1) + [0.04],
            "rsi6": [None] * (n - 1) + [55.0],
        }

    return resolve, fetch_snapshot, fetch_kline, compute_indicators


class FakeBuy:
    def __init__(self):
        self.name = "fake_buy_p2"

    def generate_signals(self, klines, x):
        last = len(klines) - 1
        return [{
            "type": "buy",
            "date": klines[last]["date"],
            "price": klines[last]["close"],
            "reason": "p2_stub_buy",
            "k_index": last,
            "score": 66.5,
        }]


def test_learning_helpers():
    print("[1] learning fields helpers")
    x = {
        "ma5": [1, 2, 3.14159],
        "dif": [None, 0.5],
        "dea": [],
        "rsi6": 70,
    }
    snap = snapshot_indicators(x)
    check("snapshot ma5", snap.get("ma5") == 3.1416, str(snap))
    check("snapshot dif", snap.get("dif") == 0.5, str(snap))
    check("snapshot rsi6", snap.get("rsi6") == 70.0, str(snap))
    fields = learning_fields(reason="abc", score=61, x=x)
    check("reason", fields["reason"] == "abc")
    check("score", fields["score"] == 61)
    check("score_version non-empty", bool(fields.get("score_version")), str(fields))
    check("indicators in fields", "ma5" in fields["indicators"])
    row = {"side": "buy"}
    attach_learning(row, fields)
    check("attach reason", row.get("reason") == "abc")
    check("attach score_version", bool(row.get("score_version")))


def test_resume_reload_state():
    print("[2] resume: create -> stop -> resume -> state intact")
    tmp = tempfile.mkdtemp(prefix="paper_p2_resume_")
    run_id = "p2_resume_stub"
    STRATEGIES["fake_buy_p2"] = FakeBuy
    try:
        klines = _fake_klines(50)
        resolve, snap, kline, compute = _stub_data(klines)
        engine = PaperEngine(
            strategies=["fake_buy_p2"],
            universe=["600519"],
            init_cash=1_000_000,
            poll_sec=60,
            run_id=run_id,
            base_dir=tmp,
            force_poll=True,
            resolve=resolve,
            fetch_snapshot=snap,
            fetch_kline=kline,
            compute_indicators=compute,
        )
        result = engine.run(once=True, force=True)
        check("first not skipped", not result.get("skipped"), str(result))
        check("first fills>=1", result.get("fills", 0) >= 1, str(result))
        acct1 = engine.accounts["fake_buy_p2"]
        cash1 = acct1.cash
        pos1 = acct1.get_position("600519")
        check("pos after first", pos1 is not None)
        seen1 = set(engine.seen)
        sigs1 = engine.journal.read_jsonl("signals.jsonl")
        fills1 = engine.journal.read_fills()
        meta1 = engine.journal.read_meta()
        eq1 = engine.journal.read_equity("fake_buy_p2")
        check("signals persisted", len(sigs1) >= 1)
        check("equity persisted", len(eq1) >= 1)

        # 模拟 stop
        engine.request_stop()
        meta1["status"] = "stopped"
        engine.journal.write_meta(meta1)

        # 新引擎实例续跑（等同 start/resume --run）
        engine2 = PaperEngine(
            strategies=["fake_buy_p2"],
            universe=["600519"],
            init_cash=1_000_000,  # 不应覆盖已有账户现金
            poll_sec=60,
            run_id=run_id,
            base_dir=tmp,
            force_poll=True,
            resolve=resolve,
            fetch_snapshot=snap,
            fetch_kline=kline,
            compute_indicators=compute,
        )
        meta_r = engine2.create_or_resume(resume=True)
        check("resumed_at set", bool(meta_r.get("resumed_at")), str(meta_r))
        check("resume block", isinstance(meta_r.get("resume"), dict), str(meta_r.get("resume")))
        check("seen restored", engine2.seen == seen1, f"{len(engine2.seen)} vs {len(seen1)}")
        acct2 = engine2.accounts["fake_buy_p2"]
        check("cash restored", abs(acct2.cash - cash1) < 1e-3, f"{acct2.cash} vs {cash1}")
        pos2 = acct2.get_position("600519")
        check("position restored", pos2 is not None and int(pos2.get("qty") or 0) == int(pos1.get("qty") or 0))
        check("last_asof restored", engine2._last_asof_date == meta1.get("last_asof_date"),
              f"{engine2._last_asof_date} vs {meta1.get('last_asof_date')}")

        # 再 poll：去重，不应再产生同信号
        r2 = engine2.poll_once()
        check("resume poll signals==0", r2.get("signals", 0) == 0, str(r2))
        fills2 = engine2.journal.read_fills()
        check("fills not duplicated", len(fills2) == len(fills1), f"{len(fills2)} vs {len(fills1)}")
        eq2 = engine2.journal.read_equity("fake_buy_p2")
        check("equity appended or same+", len(eq2) >= len(eq1), f"{len(eq2)} vs {len(eq1)}")
    finally:
        STRATEGIES.pop("fake_buy_p2", None)
        shutil.rmtree(tmp, ignore_errors=True)


def test_learning_fields_on_engine_records():
    print("[3] engine writes learning fields on signals/orders/fills")
    tmp = tempfile.mkdtemp(prefix="paper_p2_learn_")
    run_id = "p2_learn_stub"
    STRATEGIES["fake_buy_p2"] = FakeBuy
    try:
        klines = _fake_klines(50)
        resolve, snap, kline, compute = _stub_data(klines)
        engine = PaperEngine(
            strategies=["fake_buy_p2"],
            universe=["600519"],
            init_cash=1_000_000,
            run_id=run_id,
            base_dir=tmp,
            force_poll=True,
            resolve=resolve,
            fetch_snapshot=snap,
            fetch_kline=kline,
            compute_indicators=compute,
        )
        result = engine.run(once=True, force=True)
        check("ran fills", result.get("fills", 0) >= 1, str(result))
        sigs = engine.journal.read_jsonl("signals.jsonl")
        orders = engine.journal.read_orders()
        fills = engine.journal.read_fills()
        check("has signal", len(sigs) >= 1)
        s0 = sigs[0]
        check("sig reason", s0.get("reason") == "p2_stub_buy", str(s0))
        check("sig score", s0.get("score") == 66.5, str(s0.get("score")))
        check("sig score_version", bool(s0.get("score_version")), str(s0))
        check("sig indicators", isinstance(s0.get("indicators"), dict) and "ma5" in (s0.get("indicators") or {}), str(s0.get("indicators")))
        o0 = orders[0]
        check("order reason", "p2_stub_buy" in str(o0.get("reason") or ""), str(o0.get("reason")))
        check("order score_version", bool(o0.get("score_version")))
        f0 = fills[0]
        check("fill reason", bool(f0.get("reason")), str(f0))
        check("fill score_version", bool(f0.get("score_version")), str(f0))
        check("fill indicators", isinstance(f0.get("indicators"), dict), str(f0.get("indicators")))
        # backfill helper idempotent-ish
        stats = backfill_run_learning(engine.journal, write=True)
        check("backfill wrote", stats.get("written") is True, str(stats))
        meta = engine.journal.read_meta()
        check("meta score_version", bool(meta.get("score_version")), str(meta.get("score_version")))
        check("meta phase P2+", meta.get("phase") in ("P2", "P3"), str(meta.get("phase")))
    finally:
        STRATEGIES.pop("fake_buy_p2", None)
        shutil.rmtree(tmp, ignore_errors=True)


def test_webapi_direct():
    print("[4] paper webapi direct (empty + populated)")
    # empty
    tmp = tempfile.mkdtemp(prefix="paper_p2_web_empty_")
    try:
        data = paper_webapi.list_runs(base_dir=tmp)
        check("empty runs list", data.get("count") == 0 and data.get("runs") == [], str(data))
        missing = paper_webapi.get_run("nope", base_dir=tmp)
        check("missing run error", missing.get("error") == "run_not_found", str(missing))
        missing_p = paper_webapi.get_positions("nope", base_dir=tmp)
        check("missing positions error", missing_p.get("error") == "run_not_found")
        missing_t = paper_webapi.get_trades("nope", base_dir=tmp)
        check("missing trades error", missing_t.get("error") == "run_not_found")
        missing_c = paper_webapi.get_compare("nope", base_dir=tmp)
        check("missing compare error", missing_c.get("error") == "run_not_found")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp = tempfile.mkdtemp(prefix="paper_p2_web_")
    run_id = "p2_web_stub"
    STRATEGIES["fake_buy_p2"] = FakeBuy
    try:
        klines = _fake_klines(50)
        resolve, snap, kline, compute = _stub_data(klines)
        engine = PaperEngine(
            strategies=["fake_buy_p2"],
            universe=["600519"],
            init_cash=1_000_000,
            run_id=run_id,
            base_dir=tmp,
            force_poll=True,
            resolve=resolve,
            fetch_snapshot=snap,
            fetch_kline=kline,
            compute_indicators=compute,
        )
        engine.run(once=True, force=True)

        listed = paper_webapi.list_runs(base_dir=tmp)
        check("list has run", any(r["run_id"] == run_id for r in listed.get("runs") or []), str(listed))
        overview = paper_webapi.get_run(run_id, base_dir=tmp)
        check("overview no error", "error" not in overview, str(overview))
        check("overview accounts", len(overview.get("accounts") or []) == 1, str(overview.get("accounts")))
        positions = paper_webapi.get_positions(run_id, base_dir=tmp)
        check("positions count>=1", positions.get("count", 0) >= 1, str(positions))
        trades = paper_webapi.get_trades(run_id, base_dir=tmp)
        check("trades count>=1", trades.get("count", 0) >= 1, str(trades))
        check("trades have reason", bool((trades.get("trades") or [{}])[0].get("reason")))
        cmp_ = paper_webapi.get_compare(run_id, base_dir=tmp)
        check("compare rows>=1", cmp_.get("count", 0) >= 1, str(cmp_))
    finally:
        STRATEGIES.pop("fake_buy_p2", None)
        shutil.rmtree(tmp, ignore_errors=True)


def test_http_handlers_ephemeral():
    print("[5] HTTP handlers via ephemeral ThreadingHTTPServer")
    tmp = tempfile.mkdtemp(prefix="paper_p2_http_")
    run_id = "p2_http_stub"
    # seed a minimal run under project-relative cache by monkeypatching paper_root usage:
    # StockHandler uses default base_dir=None -> project cache/paper.
    # For isolation, write into real paper_root under tmp by patching paper_webapi functions? 
    # Simpler: create run under project temp via journal with base_dir=project and unique id,
    # then call handler methods with monkeypatched paper_webapi base — actually webapi uses default base.
    # We'll patch paper_webapi module functions' default by setting env-like: temporarily chdir? No.
    # Best: seed under real project cache/paper with unique run_id then delete.

    from stocklib.paper.journal import paper_root
    project = os.path.dirname(os.path.abspath(__file__))
    STRATEGIES["fake_buy_p2"] = FakeBuy
    server = None
    thread = None
    try:
        klines = _fake_klines(40)
        resolve, snap, kline, compute = _stub_data(klines)
        engine = PaperEngine(
            strategies=["fake_buy_p2"],
            universe=["600519"],
            init_cash=500_000,
            run_id=run_id,
            base_dir=project,
            force_poll=True,
            resolve=resolve,
            fetch_snapshot=snap,
            fetch_kline=kline,
            compute_indicators=compute,
        )
        engine.run(once=True, force=True)

        server = ThreadingHTTPServer(("127.0.0.1", 0), webmod.StockHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def get(path):
            with urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, json.loads(body)

        st, data = get("/paper/runs")
        check("GET /paper/runs 200", st == 200, str(st))
        check("GET /paper/runs has our id", any(r.get("run_id") == run_id for r in data.get("runs") or []), str(data)[:200])

        st, data = get(f"/paper/runs/{run_id}")
        check("GET run 200", st == 200)
        check("GET run accounts", len(data.get("accounts") or []) >= 1)

        st, data = get(f"/paper/runs/{run_id}/positions")
        check("GET positions 200", st == 200)
        check("GET positions count", data.get("count", 0) >= 1, str(data))

        st, data = get(f"/paper/runs/{run_id}/trades")
        check("GET trades 200", st == 200)
        check("GET trades count", data.get("count", 0) >= 1)

        st, data = get(f"/paper/runs/{run_id}/compare")
        check("GET compare 200", st == 200)
        check("GET compare rows", data.get("count", 0) >= 1)

        # 404
        try:
            get("/paper/runs/does_not_exist_p2")
            check("404 missing run", False, "expected HTTPError")
        except Exception as e:
            from urllib.error import HTTPError
            check("404 missing run", isinstance(e, HTTPError) and e.code == 404, str(e))
    finally:
        STRATEGIES.pop("fake_buy_p2", None)
        if server is not None:
            server.shutdown()
            server.server_close()
        # cleanup seeded run
        try:
            shutil.rmtree(os.path.join(paper_root(project), run_id), ignore_errors=True)
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_help_mentions_resume():
    print("[6] CLI help documents resume")
    import tools.paper_run as paper_run
    p = paper_run.build_parser()
    help_txt = p.format_help()
    check("help has resume", "resume" in help_txt.lower())
    # subparser help
    # parse resume --help via argparse is SystemExit; just ensure subparsers
    subs = [a.dest for a in p._subparsers._group_actions]
    # easier: parse known cmds
    for cmd in ("demo", "start", "resume", "stop", "status", "report", "compare"):
        try:
            # each subparser registered
            pass
        except Exception:
            pass
    # introspect
    sp_actions = [a for a in p._actions if getattr(a, "dest", None) == "cmd"]
    check("has cmd subparsers", bool(sp_actions))
    choices = set()
    for a in sp_actions:
        if getattr(a, "choices", None):
            choices |= set(a.choices.keys())
    check("resume in subcommands", "resume" in choices, str(sorted(choices)))
    check("start help mentions resume/续跑",
          "续跑" in (p._subparsers._group_actions[0].choices["start"].format_help()))


def run():
    print("=== test_paper_p2 ===")
    test_learning_helpers()
    test_resume_reload_state()
    test_learning_fields_on_engine_records()
    test_webapi_direct()
    test_http_handlers_ephemeral()
    test_cli_help_mentions_resume()
    if _failures:
        print(f"\nFAILED: {len(_failures)} -> {_failures}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
