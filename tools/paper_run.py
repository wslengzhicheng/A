"""模拟盘 CLI：P0–P3（demo / start / resume / stop / status / report / compare / universe）。

用法（项目根目录）：
  python -X utf8 tools/paper_run.py demo
  python -X utf8 tools/paper_run.py start --strategies chanlun,ma_cross,macd --universe 600519,000938 --cash 1000000 [--poll 60] [--force] [--once]
  python -X utf8 tools/paper_run.py start --run <existing_id> ...   # 若 run 已存在则续跑
  python -X utf8 tools/paper_run.py resume --run <existing_id> [--force] [--once] [--poll 60]
  python -X utf8 tools/paper_run.py stop [--run <id>]
  python -X utf8 tools/paper_run.py status [--run <id>]
  python -X utf8 tools/paper_run.py report --run <id>
  python -X utf8 tools/paper_run.py compare --run <id>
  python -X utf8 tools/paper_run.py universe --universe-file codes.txt
  python -X utf8 tools/paper_run.py universe --from-picker [--limit 30]
  python -X utf8 tools/paper_run.py start --strategies score_gate --universe-file cache/paper/universe.json --once --force
  python -X utf8 tools/paper_run.py start --strategies score_gate,chanlun,ma_cross,macd --mode market_race --precise-limit 400 --force --once

续跑说明：
  - `start --run <已有id>` 或 `resume --run <id>` 会从 cache/paper/<id>/ 恢复：
    各策略账户、signals.jsonl 去重游标、meta.last_asof_date、净值曲线（追加）。
  - resume 可省略 --strategies/--universe/--cash，默认读 meta.json。

亦可：stock.py --paper <subcommand> ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from stocklib.paper import (
    Account,
    BrokerSim,
    PaperJournal,
    new_run_id,
    summarize,
    target_qty_for_pct,
)
from stocklib.paper.engine import (
    PaperEngine,
    clear_active,
    compare_run,
    read_active,
)
from stocklib.paper.journal import paper_root
from stocklib.paper.universe import (
    default_universe_path,
    load_universe_file,
    save_universe,
    merge_universe,
    universe_from_picker,
    resolve_universe_spec,
    DEFAULT_PRECISE_LIMIT,
)


INIT_CASH = 1_000_000.0
TARGET_PCT = 0.15
DEMO_STRATEGY = "demo"
DEMO_CODE = "600000"


def _parse_csv(s: str):
    if not s:
        return []
    return [x.strip() for x in str(s).replace(";", ",").split(",") if x.strip()]


def _resolve_run_id(args_run) -> str:
    if args_run:
        return str(args_run)
    act = read_active(_ROOT)
    if act and act.get("run_id"):
        return str(act["run_id"])
    raise SystemExit("[错误] 需要 --run <id>（或先 start 产生 _active.json）")



def _resolve_universe_arg(args) -> list:
    """优先 --universe-file，其次 --universe CSV；二者皆空则试默认 cache/paper/universe.json。"""
    uf = getattr(args, "universe_file", None)
    if uf:
        codes = load_universe_file(uf)
        if not codes:
            raise SystemExit(f"[错误] universe-file 为空: {uf}")
        return codes
    uni = _parse_csv(getattr(args, "universe", None) or "")
    if uni:
        return uni
    default_path = default_universe_path(_ROOT)
    if os.path.isfile(default_path):
        codes = load_universe_file(default_path)
        if codes:
            print(f"[info] using default universe file: {default_path} ({len(codes)} codes)")
            return codes
    return []


def cmd_demo(args) -> int:
    run_id = args.run or new_run_id()
    journal = PaperJournal(run_id, base_dir=_ROOT)
    fees = {
        "commission_rate": 0.0003,
        "commission_min": 5.0,
        "stamp_tax_rate": 0.0001,
        "lot": 100,
    }
    meta = journal.create_run({
        "run_id": run_id,
        "status": "demo",
        "strategies": [DEMO_STRATEGY],
        "universe": [DEMO_CODE],
        "init_cash": INIT_CASH,
        "target_pct": TARGET_PCT,
        "poll_sec": 60,
        "kline": "daily_mvp",
        "fees": fees,
        "note": "P0 offline demo: 3 fills on fake code",
    })
    acct = Account(
        strategy_id=DEMO_STRATEGY,
        name="Demo",
        cash=INIT_CASH,
        init_cash=INIT_CASH,
        params={"init_cash": INIT_CASH, "lot": 100, "target_pct": TARGET_PCT},
    )
    broker = BrokerSim(fees)
    code = DEMO_CODE

    d1, px1 = "2026-09-01", 10.0
    qty1 = target_qty_for_pct(acct.equity(), px1, TARGET_PCT, lot=100)
    if qty1 < 100:
        qty1 = 100 * 150
    o1, f1 = broker.place_market_order(
        acct, code, "buy", qty1, px1, d1, reason="demo_buy1")
    journal.append_order(o1)
    if f1:
        journal.append_fill(f1)
    journal.append_signal({
        "ts": o1["ts"], "asof_date": d1, "strategy_id": DEMO_STRATEGY,
        "code": code, "side": "buy", "price": px1, "reason": "demo_buy1",
    })
    eq1 = acct.mark_to_market({code: px1})
    journal.append_equity(DEMO_STRATEGY, {"date": d1, "equity": round(eq1, 4), "cash": round(acct.cash, 4)})

    d2, px2 = "2026-09-02", 10.5
    released = acct.release_t1(d2)
    pos = acct.get_position(code) or {}
    avail = int(pos.get("available_qty") or 0)
    sell_qty = (avail // 2 // 100) * 100
    if sell_qty < 100:
        sell_qty = (avail // 100) * 100
    o2, f2 = broker.place_market_order(
        acct, code, "sell", sell_qty, px2, d2, reason="demo_sell1")
    journal.append_order(o2)
    if f2:
        journal.append_fill(f2)
    journal.append_signal({
        "ts": o2["ts"], "asof_date": d2, "strategy_id": DEMO_STRATEGY,
        "code": code, "side": "sell", "price": px2, "reason": "demo_sell1",
    })

    px2b = 10.2
    qty3 = target_qty_for_pct(acct.equity({code: px2b}), px2b, 0.08, lot=100)
    if qty3 < 100:
        qty3 = 100
    o3, f3 = broker.place_market_order(
        acct, code, "buy", qty3, px2b, d2, reason="demo_buy2")
    journal.append_order(o3)
    if f3:
        journal.append_fill(f3)
    journal.append_signal({
        "ts": o3["ts"], "asof_date": d2, "strategy_id": DEMO_STRATEGY,
        "code": code, "side": "buy", "price": px2b, "reason": "demo_buy2",
    })

    eq2 = acct.mark_to_market({code: px2b})
    journal.append_equity(DEMO_STRATEGY, {
        "date": d2, "equity": round(eq2, 4), "cash": round(acct.cash, 4),
        "released_t1": released,
    })

    journal.save_account(acct)
    meta["status"] = "demo_done"
    meta["fills"] = len(journal.read_fills())
    journal.write_meta(meta)

    fills = journal.read_fills()
    mv = acct.market_value()
    equity = acct.equity()
    balanced = abs(equity - (acct.cash + mv)) < 1e-6

    print("=== Paper Demo ===")
    print(f"run_id     : {run_id}")
    print(f"strategy   : {DEMO_STRATEGY}")
    print(f"fills      : {len(fills)} (expect 3)")
    for i, f in enumerate(fills, 1):
        print(f"  fill{i}: {f.get('side')} {f.get('code')} qty={f.get('qty')} "
              f"px={f.get('price')} fee={f.get('fee')} tax={f.get('tax')}")
    print(f"cash       : {acct.cash:.4f}")
    print(f"market_val : {mv:.4f}")
    print(f"equity     : {equity:.4f}")
    print(f"init_cash  : {INIT_CASH:.4f}")
    print(f"pnl        : {equity - INIT_CASH:.4f}")
    print(f"book_ok    : {balanced}")
    print(f"positions  : {json.dumps(acct.to_dict()['positions'], ensure_ascii=False)}")
    if len(fills) != 3 or not balanced:
        print("[FAIL] demo books not balanced or fill count != 3", file=sys.stderr)
        return 1
    print("[OK] demo 3 fills, books balanced")
    return 0


def cmd_start(args) -> int:
    strategies = _parse_csv(args.strategies)
    mode = str(getattr(args, "mode", None) or "").strip().lower()
    precise_limit = int(getattr(args, "precise_limit", None) or DEFAULT_PRECISE_LIMIT)
    universe_meta = None
    try:
        if mode in ("market_race", "all_a", "full_a", "race"):
            print(f"[info] resolving market_race universe precise_limit={precise_limit} ...")
            universe_meta = resolve_universe_spec("market_race", precise_limit=precise_limit)
            universe = list(universe_meta.get("codes") or [])
            if not getattr(args, "once", False):
                # race 默认 once，避免 60s 全市场循环
                args.once = True
                print("[info] market_race forces --once (no 60s full-market loop)")
        else:
            universe = _resolve_universe_arg(args)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 5
    except FileNotFoundError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 5
    except Exception as e:
        print(f"[错误] universe resolve failed: {e}", file=sys.stderr)
        return 5
    if not strategies:
        print("[错误] --strategies 必填，如 chanlun,ma_cross,macd,score_gate", file=sys.stderr)
        return 5
    if not universe:
        print("[错误] 需要 --universe / --universe-file / --mode market_race", file=sys.stderr)
        return 5
    cash = float(args.cash)
    poll = int(args.poll)
    run_id = args.run or new_run_id()
    engine = PaperEngine(
        strategies=strategies,
        universe=universe,
        init_cash=cash,
        poll_sec=poll,
        target_pct=float(getattr(args, "target_pct", TARGET_PCT) or TARGET_PCT),
        kline_period=str(getattr(args, "kline", "day") or "day"),
        run_id=run_id,
        base_dir=_ROOT,
        force_poll=bool(args.force),
    )
    resuming = os.path.isfile(os.path.join(paper_root(_ROOT), run_id, "meta.json"))
    print("=== Paper Start" + (" (resume)" if resuming else "") + " ===")
    print(f"run_id     : {run_id}")
    if resuming:
        print("note       : existing run — will reload accounts/signals cursor/equity")
    print(f"strategies : {strategies}")
    if len(universe) > 20:
        print(f"universe   : {universe[:10]} ... (+{len(universe)-10} more), count={len(universe)}")
    else:
        print(f"universe   : {universe}")
    if universe_meta:
        print(f"mode       : market_race precise_limit={precise_limit} clist_total={universe_meta.get('clist_total')} screened={universe_meta.get('screened_count')}")
    print(f"cash       : {cash}")
    print(f"poll_sec   : {poll}")
    print(f"force      : {bool(args.force)}")
    print(f"once       : {bool(args.once)}")
    print(f"kline      : {getattr(args, "kline", "day")}")
    try:
        result = engine.run(once=bool(args.once), force=bool(args.force))
    except KeyError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 5
    except Exception as e:
        print(f"[错误] engine failed: {e}", file=sys.stderr)
        return 4
    print("--- last poll ---")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("skipped"):
        print("[WARN] skipped outside session; use --force for after-hours smoke")
        return 0
    print(f"[OK] run_id={run_id} status done/loop-exit")
    return 0


def cmd_stop(args) -> int:
    try:
        run_id = _resolve_run_id(getattr(args, "run", None))
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 5
    journal = PaperJournal(run_id, base_dir=_ROOT)
    journal.ensure_dirs()
    stop_path = os.path.join(journal.root, "STOP")
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ts = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%dT%H:%M:%S")
    with open(stop_path, "w", encoding="utf-8") as f:
        f.write(ts)
    try:
        meta = journal.read_meta()
        meta["status"] = "stop_requested"
        meta["stop_requested_at"] = ts
        journal.write_meta(meta)
    except FileNotFoundError:
        print(f"[错误] run 不存在: {run_id}", file=sys.stderr)
        return 3
    print(f"[OK] stop requested for run_id={run_id}")
    return 0


def cmd_status(args) -> int:
    run_id = getattr(args, "run", None)
    act = read_active(_ROOT)
    if not run_id:
        if act and act.get("run_id"):
            run_id = act["run_id"]
        else:
            runs = PaperJournal.list_runs(_ROOT)
            print("=== Paper Status ===")
            print(f"active: {act}")
            print(f"runs ({len(runs)}): {runs[-10:]}")
            return 0
    journal = PaperJournal(run_id, base_dir=_ROOT)
    try:
        meta = journal.read_meta()
    except FileNotFoundError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 3
    print("=== Paper Status ===")
    print(f"active_file: {json.dumps(act, ensure_ascii=False)}")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    for sid in journal.list_accounts():
        acct = journal.load_account(sid)
        d = acct.to_dict()
        print(f"--- account {sid} ---")
        print(f"cash={d['cash']} equity={d['equity']} mv={d['market_value']}")
        print(f"positions={json.dumps(d['positions'], ensure_ascii=False)}")
        print(f"orders={len(journal.read_orders(sid))} fills={len(journal.read_fills(sid))}")
    return 0


def cmd_report(args) -> int:
    if not args.run:
        print("[错误] report 需要 --run <id>", file=sys.stderr)
        return 5
    journal = PaperJournal(args.run, base_dir=_ROOT)
    try:
        meta = journal.read_meta()
    except FileNotFoundError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 3
    print("=== Paper Report ===")
    print(f"run_id: {args.run}")
    for sid in journal.list_accounts():
        acct = journal.load_account(sid)
        fills = journal.read_fills(sid)
        curve = journal.read_equity(sid)
        rep = summarize(
            init_cash=acct.init_cash,
            equity=acct.equity(),
            equity_curve=curve,
            fills=fills,
        )
        print(f"--- {sid} ---")
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    if not journal.list_accounts():
        print("(no accounts)")
    return 0


def cmd_compare(args) -> int:
    if not args.run:
        print("[错误] compare 需要 --run <id>", file=sys.stderr)
        return 5
    try:
        rows = compare_run(args.run, base_dir=_ROOT)
    except FileNotFoundError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 3
    print("=== Paper Compare ===")
    print(f"run_id: {args.run}")
    if rows:
        bt = rows[0].get("benchmark_type") or ""
        bn = rows[0].get("benchmark_name") or ""
        print(f"benchmark : {bn} ({bt})" if bn or bt else "benchmark : (n/a)")
    # 表格
    headers = [
        "strategy_id", "total_return", "benchmark", "excess",
        "max_drawdown", "closed", "win_rate", "equity", "fills",
    ]
    print(" | ".join(f"{h:>14}" for h in headers))
    print("-" * (16 * len(headers)))
    for r in rows:
        vals = [
            str(r.get("strategy_id")),
            f"{r.get('total_return', 0):.4%}",
            f"{r.get('benchmark_return', 0):.4%}",
            f"{r.get('excess_return', 0):.4%}",
            f"{r.get('max_drawdown', 0):.4%}",
            str(r.get("closed_rounds", r.get("trades"))),
            ("无完整轮次" if r.get("win_rate") is None else f"{r.get('win_rate'):.2%}"),
            f"{r.get('equity', 0):.2f}",
            str(r.get("fill_count")),
        ]
        print(" | ".join(f"{v:>14}" for v in vals))
    print("--- json ---")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0



def cmd_resume(args) -> int:
    """续跑已有 run：从 meta 恢复 strategies/universe/cash，再 engine.run。"""
    run_id = getattr(args, "run", None)
    if not run_id:
        print("[错误] resume 需要 --run <existing_id>", file=sys.stderr)
        return 5
    journal = PaperJournal(run_id, base_dir=_ROOT)
    try:
        meta = journal.read_meta()
    except FileNotFoundError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 3

    strategies = _parse_csv(getattr(args, "strategies", None) or "")
    universe = _parse_csv(getattr(args, "universe", None) or "")
    uf = getattr(args, "universe_file", None)
    if uf:
        try:
            universe = load_universe_file(uf)
        except Exception as e:
            print(f"[错误] {e}", file=sys.stderr)
            return 5
    if not strategies:
        strategies = [str(s) for s in (meta.get("strategies") or [])]
    if not universe:
        universe = [str(c) for c in (meta.get("universe") or [])]
    if not strategies or not universe:
        print("[错误] meta 缺少 strategies/universe，且未通过 CLI 提供", file=sys.stderr)
        return 5

    cash = float(args.cash) if getattr(args, "cash", None) is not None else float(
        meta.get("init_cash") or INIT_CASH
    )
    poll = int(args.poll) if getattr(args, "poll", None) is not None else int(
        meta.get("poll_sec") or 60
    )
    target_pct = float(getattr(args, "target_pct", None) or meta.get("target_pct") or TARGET_PCT)

    engine = PaperEngine(
        strategies=strategies,
        universe=universe,
        init_cash=cash,
        poll_sec=poll,
        target_pct=target_pct,
        run_id=run_id,
        base_dir=_ROOT,
        force_poll=bool(args.force),
    )
    print("=== Paper Resume ===")
    print(f"run_id     : {run_id}")
    print(f"strategies : {strategies}")
    print(f"universe   : {universe}")
    print(f"cash       : {cash} (init; live cash from saved accounts)")
    print(f"poll_sec   : {poll}")
    print(f"force      : {bool(args.force)}")
    print(f"once       : {bool(args.once)}")
    print(f"meta.status: {meta.get('status')}")
    try:
        # run() 内 create_or_resume(resume=True) 因 meta 已存在
        result = engine.run(once=bool(args.once), force=bool(args.force))
    except KeyError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 5
    except Exception as e:
        print(f"[错误] engine failed: {e}", file=sys.stderr)
        return 4
    # 验证续跑状态
    meta2 = engine.journal.read_meta()
    print("--- resume meta ---")
    print(json.dumps({
        "resumed_at": meta2.get("resumed_at"),
        "resume": meta2.get("resume"),
        "last_asof_date": meta2.get("last_asof_date"),
        "status": meta2.get("status"),
        "seen_signals": len(engine.seen),
        "accounts": {sid: round(acct.cash, 2) for sid, acct in engine.accounts.items()},
    }, ensure_ascii=False, indent=2, default=str))
    print("--- last poll ---")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("skipped"):
        print("[WARN] skipped outside session; use --force for after-hours smoke")
        return 0
    print(f"[OK] resumed run_id={run_id}")
    return 0



def cmd_universe(args) -> int:
    """构建/更新 cache/paper/universe.json（或 --out）。"""
    out_path = getattr(args, "out", None) or default_universe_path(_ROOT)
    mode = getattr(args, "mode", "replace") or "replace"
    codes = []
    source = "file"
    extra = {}

    if getattr(args, "from_picker", False):
        limit = int(getattr(args, "limit", 50) or 50)
        try:
            pack = universe_from_picker(limit=limit, fresh=bool(getattr(args, "fresh", False)))
        except Exception as e:
            print(f"[错误] from-picker failed: {e}", file=sys.stderr)
            return 4
        codes = pack.get("codes") or []
        source = "picker"
        extra = {
            "opportunities_count": pack.get("opportunities_count"),
            "candidates_count": pack.get("candidates_count"),
            "note": pack.get("note"),
            "limit": pack.get("limit"),
        }
        print(f"[picker] {pack.get('note')} -> {len(codes)} codes (limit={limit})")
    elif getattr(args, "universe_file", None):
        try:
            codes = load_universe_file(args.universe_file)
        except Exception as e:
            print(f"[错误] {e}", file=sys.stderr)
            return 5
        source = f"file:{args.universe_file}"
    elif getattr(args, "codes", None):
        codes = _parse_csv(args.codes)
        source = "cli"
    else:
        print("[错误] 需要 --universe-file / --codes / --from-picker 之一", file=sys.stderr)
        return 5

    if not codes:
        print("[错误] 未得到任何代码", file=sys.stderr)
        return 5

    existing = []
    if mode in ("append", "union") and os.path.isfile(out_path):
        try:
            existing = load_universe_file(out_path)
        except Exception:
            existing = []
    merged = merge_universe(existing, codes, mode=mode)
    payload = save_universe(merged, path=out_path, base_dir=_ROOT, source=source, extra=extra)
    print("=== Paper Universe ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[OK] wrote {out_path} ({payload['count']} codes)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A股模拟盘 CLI (P0–P3：score_gate / 选股入池 / 基准超额)")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="离线演示：3 笔买卖并校验账本")
    d.add_argument("--run", default=None, help="指定 run_id（默认自动生成）")
    d.set_defaults(func=cmd_demo)

    st = sub.add_parser("start", help="启动模拟盘轮询；--run 已存在则续跑（恢复账户/信号去重/净值）")
    st.add_argument("--strategies", required=True, help="逗号分隔，如 chanlun,ma_cross,macd,score_gate")
    st.add_argument("--universe", required=False, default=None, help="逗号分隔代码，如 600519,000938")
    st.add_argument("--universe-file", dest="universe_file", default=None,
                    help="从文件加载标的池（json/txt）；也可用默认 cache/paper/universe.json")
    st.add_argument("--cash", type=float, default=INIT_CASH)
    st.add_argument("--poll", type=int, default=60)
    st.add_argument("--target-pct", dest="target_pct", type=float, default=TARGET_PCT)
    st.add_argument("--force", action="store_true", help="忽略交易时段（盘后联调）")
    st.add_argument("--once", action="store_true", help="只跑一轮轮询后退出")
    st.add_argument("--kline", default="day",
                    help="K线周期：day|1|5|15|30|60（默认 day）")
    st.add_argument("--mode", default=None,
                    help="market_race=全A粗筛后取成交额 top precise_limit，默认 once")
    st.add_argument("--precise-limit", dest="precise_limit", type=int, default=DEFAULT_PRECISE_LIMIT,
                    help="market_race 精筛上限（默认 400）")
    st.add_argument("--run", default=None, help="指定 run_id；若目录已存在则续跑，否则新建")
    st.set_defaults(func=cmd_start)

    rs = sub.add_parser(
        "resume",
        help="续跑已有 run：从 meta 恢复 strategies/universe/账户/信号去重游标/净值",
    )
    rs.add_argument("--run", required=True, help="已有 run_id")
    rs.add_argument("--strategies", default=None, help="可选覆盖；默认读 meta")
    rs.add_argument("--universe", default=None, help="可选覆盖；默认读 meta")
    rs.add_argument("--universe-file", dest="universe_file", default=None, help="可选：从文件覆盖 universe")
    rs.add_argument("--cash", type=float, default=None, help="可选；默认 meta.init_cash")
    rs.add_argument("--poll", type=int, default=None)
    rs.add_argument("--target-pct", dest="target_pct", type=float, default=None)
    rs.add_argument("--force", action="store_true", help="忽略交易时段")
    rs.add_argument("--once", action="store_true", help="只跑一轮后退出")
    rs.set_defaults(func=cmd_resume)

    sp = sub.add_parser("stop", help="请求停止正在运行的引擎")
    sp.add_argument("--run", default=None)
    sp.set_defaults(func=cmd_stop)

    s = sub.add_parser("status", help="查看 run 状态")
    s.add_argument("--run", required=False, default=None)
    s.set_defaults(func=cmd_status)

    r = sub.add_parser("report", help="绩效摘要")
    r.add_argument("--run", required=True)
    r.set_defaults(func=cmd_report)

    c = sub.add_parser("compare", help="多策略对照表（含等权基准与超额）")
    c.add_argument("--run", required=True)
    c.set_defaults(func=cmd_compare)

    u = sub.add_parser("universe", help="构建/更新选股入池 universe.json")
    u.add_argument("--universe-file", dest="universe_file", default=None, help="从文本/JSON 读代码列表")
    u.add_argument("--codes", default=None, help="逗号分隔代码")
    u.add_argument("--from-picker", dest="from_picker", action="store_true", help="调用选股器（需网络）")
    u.add_argument("--limit", type=int, default=50, help="from-picker 最多保留 N 只")
    u.add_argument("--fresh", action="store_true", help="选股器强制刷新")
    u.add_argument("--mode", choices=["replace", "append", "union"], default="replace")
    u.add_argument("--out", default=None, help="输出路径（默认 cache/paper/universe.json）")
    u.set_defaults(func=cmd_universe)

    return p


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
