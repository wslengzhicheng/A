"""模拟盘只读 Web API 数据层（供 web.py 路由调用，可单测直调）。"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from stocklib.paper.engine import clear_active, compare_run, read_active


def _run_is_active(run_id: str, base_dir=None) -> bool:
    act = read_active(base_dir) or {}
    if act.get("run_id") != run_id:
        return False
    return act.get("status") not in {"stopped", "stop_requested"}

from stocklib.paper.journal import PaperJournal, paper_root
from stocklib.paper.metrics import summarize


_NAME_HINTS = {
    "600000": "浦发银行",
    "600519": "贵州茅台",
    "000802": "北京文化",
    "002422": "科伦药业",
    "002881": "美格智能",
    "000938": "紫光股份",
}


def classify_run_tag(meta: dict, run_id: str) -> dict:
    """给 run 打展示标签。全市场赛马优先于 smoke 启发式（force+once_done 不再误标烟测）。"""
    status = str(meta.get("status") or "")
    note = str(meta.get("note") or "")
    strategies = meta.get("strategies") or []
    mode = str(meta.get("mode") or "").strip().lower()
    existing = str(meta.get("tag") or "").strip().lower()
    low = (run_id + " " + status + " " + note + " " + mode).lower()
    if existing in ("race", "market_race") or mode in ("market_race", "race", "all_a", "full_a") or "赛马" in note or "全市场" in note:
        return {"tag": "race", "tag_label": "全市场对照", "is_demo": False}
    if status.startswith("demo") or "demo" in strategies or "demo" in low or "stub" in low:
        return {"tag": "demo", "tag_label": "演示", "is_demo": True}
    if "smoke" in low or "smoke" in run_id or run_id.endswith("_live"):
        return {"tag": "smoke", "tag_label": "实盘行情烟测", "is_demo": False}
    # 普通小池 once+force 仍可能是烟测；但若已有 formal/race 标签则尊重
    if existing == "formal":
        return {"tag": "formal", "tag_label": str(meta.get("tag_label") or "正式模拟"), "is_demo": False}
    if bool(meta.get("force_poll")) and status == "once_done" and mode in ("", "manual") and len(meta.get("universe") or []) <= 5:
        return {"tag": "smoke", "tag_label": "实盘行情烟测", "is_demo": False}
    return {"tag": "formal", "tag_label": "正式模拟", "is_demo": False}


def enrich_universe(codes):
    out = []
    for c in codes or []:
        code = str(c)
        out.append({"code": code, "name": _NAME_HINTS.get(code)})
    return out


def _base(base_dir: Optional[str] = None) -> Optional[str]:
    return base_dir


def list_runs(base_dir: Optional[str] = None) -> dict:
    """GET /paper/runs"""
    root = paper_root(base_dir)
    run_ids = PaperJournal.list_runs(base_dir)
    active = read_active(base_dir) or {}
    known = set(run_ids)
    ignored = None
    if active and active.get("run_id") not in known:
        ignored = {
            "run_id": active.get("run_id"),
            "status": "orphan_stub",
            "note": "active points to missing run (test residue); ignored",
            "ignored": True,
        }
        active = {}
    runs: List[dict] = []
    for rid in run_ids:
        j = PaperJournal(rid, base_dir=base_dir)
        try:
            meta = j.read_meta()
        except FileNotFoundError:
            meta = {"run_id": rid, "status": "unknown"}
        tag = classify_run_tag(meta, rid)
        uni = meta.get("universe") or []
        runs.append({
            "run_id": rid,
            "status": meta.get("status"),
            "mode": meta.get("mode"),
            "strategies": meta.get("strategies") or [],
            "universe": uni,
            "universe_named": enrich_universe(uni),
            "universe_count": meta.get("universe_count") or (len(uni) if uni else None),
            "scan_progress": meta.get("scan_progress"),
            "init_cash": meta.get("init_cash"),
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("last_poll") or meta.get("stopped_at") or meta.get("resumed_at"),
            "is_active": bool(active) and active.get("run_id") == rid,
            **tag,
        })
    runs.sort(key=lambda r: (1 if r.get("is_demo") else 0, r.get("created_at") or ""))
    return {
        "runs": runs,
        "count": len(runs),
        "active": active or None,
        "active_ignored": ignored,
        "root": root,
    }


def get_run(run_id: str, base_dir: Optional[str] = None) -> dict:
    """GET /paper/runs/<id> — 元数据 + 各策略账户总览。"""
    j = PaperJournal(run_id, base_dir=base_dir)
    try:
        meta = j.read_meta()
    except FileNotFoundError:
        return {"error": "run_not_found", "run_id": run_id}
    accounts = []
    for sid in j.list_accounts():
        acct = j.load_account(sid)
        d = acct.to_dict()
        fills = j.read_fills(sid)
        curve = j.read_equity(sid)
        rep = summarize(
            init_cash=acct.init_cash,
            equity=acct.equity(),
            equity_curve=curve,
            fills=fills,
        )
        accounts.append({
            "strategy_id": sid,
            "cash": d.get("cash"),
            "equity": d.get("equity"),
            "market_value": d.get("market_value"),
            "positions_count": len(d.get("positions") or {}),
            "fills": len(fills),
            "orders": len(j.read_orders(sid)),
            "metrics": {
                "total_return": rep.get("total_return"),
                "max_drawdown": rep.get("max_drawdown"),
                "win_rate": rep.get("win_rate"),
                "trade_count": rep.get("trade_count"),
                "closed_rounds": rep.get("closed_rounds"),
                "fill_count": rep.get("fill_count"),
            },
        })
    return {
        "run_id": run_id,
        "meta": meta,
        "accounts": accounts,
        "active": (read_active(base_dir) or {}).get("run_id") == run_id,
    }


def get_positions(run_id: str, base_dir: Optional[str] = None) -> dict:
    """GET /paper/runs/<id>/positions

    Light enrich: available/locked/cost/mark/pnl/pnl_pct/name/reason
    (reason = latest buy fill reason for strategy+code).
    """
    j = PaperJournal(run_id, base_dir=base_dir)
    try:
        j.read_meta()
    except FileNotFoundError:
        return {"error": "run_not_found", "run_id": run_id}

    reason_map = {}
    try:
        fills = j.read_fills()
    except Exception:
        fills = []
    for f in fills or []:
        if str(f.get("side") or "").lower() != "buy":
            continue
        sid = str(f.get("strategy_id") or "")
        code = str(f.get("code") or "")
        if not code:
            continue
        learning = f.get("learning") if isinstance(f.get("learning"), dict) else {}
        reason_map[(sid, code)] = f.get("reason") or learning.get("reason")

    by_strategy = {}
    flat: List[dict] = []
    for sid in j.list_accounts():
        acct = j.load_account(sid)
        d = acct.to_dict()
        positions = d.get("positions") or {}
        rows = []
        for code, pos in positions.items():
            if not isinstance(pos, dict):
                row = {"strategy_id": sid, "code": code, "raw": pos}
                rows.append(row)
                flat.append(row)
                continue
            qty = int(pos.get("qty") or 0)
            available = int(pos.get("available_qty") or 0)
            pending = pos.get("pending") or []
            pending_qty = 0
            for lot in pending:
                try:
                    pending_qty += int((lot or {}).get("qty") or 0)
                except (TypeError, ValueError):
                    pass
            locked = pending_qty if pending_qty else max(0, qty - available)
            cost = float(pos.get("avg_cost") or 0.0)
            mark = float(pos.get("last_price") or cost or 0.0)
            pnl = (mark - cost) * qty if qty else 0.0
            pnl_pct = ((mark - cost) / cost) if cost else None
            row = {
                "strategy_id": sid,
                "code": str(code),
                "name": _NAME_HINTS.get(str(code)),
                "qty": qty,
                "available": available,
                "available_qty": available,
                "locked": locked,
                "pending": pending,
                "cost": cost,
                "avg_cost": cost,
                "mark": mark,
                "last_price": mark,
                "price": mark,
                "pnl": round(pnl, 4),
                "pnl_pct": pnl_pct,
                "reason": reason_map.get((sid, str(code))) or "",
            }
            rows.append(row)
            flat.append(row)
        by_strategy[sid] = rows
    return {
        "run_id": run_id,
        "positions": flat,
        "by_strategy": by_strategy,
        "count": len(flat),
    }


def get_trades(run_id: str, base_dir: Optional[str] = None) -> dict:
    """GET /paper/runs/<id>/trades — 成交（含学习字段若有）。"""
    j = PaperJournal(run_id, base_dir=base_dir)
    try:
        j.read_meta()
    except FileNotFoundError:
        return {"error": "run_not_found", "run_id": run_id}
    fills = j.read_fills()
    signals = j.read_jsonl("signals.jsonl")
    return {
        "run_id": run_id,
        "trades": fills,
        "fills": fills,
        "signals": signals,
        "count": len(fills),
        "signal_count": len(signals),
    }


def get_compare(run_id: str, base_dir: Optional[str] = None) -> dict:
    """GET /paper/runs/<id>/compare"""
    j = PaperJournal(run_id, base_dir=base_dir)
    try:
        j.read_meta()
    except FileNotFoundError:
        return {"error": "run_not_found", "run_id": run_id}
    try:
        rows = compare_run(run_id, base_dir=base_dir)
    except FileNotFoundError:
        rows = []
    bench_name = (rows[0].get("benchmark_name") if rows else None)
    bench_type = (rows[0].get("benchmark_type") if rows else None)
    bench_ret = (rows[0].get("benchmark_return") if rows else None)
    return {
        "run_id": run_id,
        "compare": rows,
        "rows": rows,
        "count": len(rows),
        "benchmark_name": bench_name,
        "benchmark_type": bench_type,
        "benchmark_return": bench_ret,
    }


# ---- 启停（本机无鉴权；仅供单用户本地使用）----
_bg_threads = {}


def start_run(payload=None, base_dir=None):
    """POST /paper/runs 启动模拟盘。

    payload: {strategies, universe, cash, once, force, kline, poll, mode, precise_limit}
    mode=market_race: 全A粗筛 + top precise_limit，四策略独立账户，once 异步跑并写 scan_progress
      （默认不是 60s 全市场循环）。
    once=True 默认同步一轮；market_race 的 once 强制异步。
    本地、无鉴权。
    """
    import threading
    from pathlib import Path as _Path
    from stocklib.paper.engine import PaperEngine
    from stocklib.paper.journal import new_run_id
    from stocklib.paper.strategies_adapter import list_strategy_ids
    from stocklib.paper.universe import DEFAULT_PRECISE_LIMIT, resolve_universe_spec

    payload = dict(payload or {})
    mode = str(payload.get("mode") or "").strip().lower()
    precise_limit = int(payload.get("precise_limit") or payload.get("preciseLimit") or DEFAULT_PRECISE_LIMIT)
    strategies = payload.get("strategies") or ["score_gate", "chanlun", "ma_cross", "macd"]
    if isinstance(strategies, str):
        strategies = [x.strip() for x in strategies.replace(";", ",").split(",") if x.strip()]

    universe = payload.get("universe")
    universe_meta = None
    if mode in ("market_race", "all_a", "full_a", "race"):
        try:
            universe_meta = resolve_universe_spec(
                "market_race",
                precise_limit=precise_limit,
            )
            universe = list(universe_meta.get("codes") or [])
        except Exception as e:
            return {"error": "universe_resolve_failed", "detail": "%s: %s" % (type(e).__name__, e)}
        once = True
        force = bool(payload.get("force", True))
        async_once = True
    else:
        if universe is None or universe == "":
            universe = ["000802", "002422", "002881"]
        if isinstance(universe, str):
            low = universe.strip().lower()
            if low in ("all_a", "market_race", "full_a", "race"):
                try:
                    universe_meta = resolve_universe_spec(low, precise_limit=precise_limit)
                    universe = list(universe_meta.get("codes") or [])
                except Exception as e:
                    return {"error": "universe_resolve_failed", "detail": "%s: %s" % (type(e).__name__, e)}
            else:
                universe = [x.strip() for x in universe.replace(";", ",").split(",") if x.strip()]
        once = bool(payload.get("once", True))
        force = bool(payload.get("force", True))
        async_once = bool(payload.get("async") or payload.get("async_once"))

    cash = float(payload.get("cash") or 1_000_000)
    kline = str(payload.get("kline") or "day")
    poll = int(payload.get("poll") or 60)
    run_id = payload.get("run_id") or payload.get("run") or new_run_id()

    known = set(list_strategy_ids())
    bad = [s for s in strategies if s not in known]
    if bad:
        return {"error": "unknown_strategies", "unknown": bad, "known": sorted(known)}
    if not universe:
        return {"error": "universe_required"}

    if base_dir is None:
        base_dir = str(_Path(__file__).resolve().parents[2])

    engine = PaperEngine(
        strategies=strategies,
        universe=universe,
        init_cash=cash,
        poll_sec=poll,
        kline_period=kline,
        run_id=str(run_id),
        base_dir=base_dir,
        force_poll=force,
    )

    try:
        meta0 = engine.create_or_resume(resume=False)
        meta0["mode"] = mode or ("market_race" if universe_meta else "manual")
        meta0["precise_limit"] = precise_limit
        if str(meta0.get("mode") or "").lower() in ("market_race", "race", "all_a", "full_a"):
            meta0["tag"] = "race"
            meta0["tag_label"] = "全市场对照"
            meta0["note"] = meta0.get("note") or "全市场策略对照"
        if universe_meta:
            meta0["universe_source"] = universe_meta.get("source")
            meta0["universe_meta"] = {
                k: universe_meta.get(k)
                for k in ("clist_total", "clist_pages", "screened_count", "dropped", "precise_limit", "mode")
            }
        meta0["scan_progress"] = {
            "done": 0,
            "total": len(universe),
            "phase": "queued",
            "pct": 0.0,
        }
        meta0["universe_count"] = len(universe)
        engine.journal.write_meta(meta0)
    except Exception:
        pass

    if once and not async_once:
        result = engine.run(once=True, force=force)
        return {
            "ok": True,
            "run_id": engine.run_id,
            "mode": mode or "once",
            "universe_count": len(universe),
            "precise_limit": precise_limit,
            "result": result,
            "warning": "local_no_auth: POST /paper/runs is unauthenticated; bind to localhost only",
        }

    def _runner():
        try:
            engine.run(once=bool(once), force=force)
        except Exception as e:
            try:
                meta = engine.journal.read_meta()
                meta["status"] = "error"
                meta["error"] = "%s: %s" % (type(e).__name__, e)
                engine.journal.write_meta(meta)
            except Exception:
                pass

    th = threading.Thread(target=_runner, name="paper-%s" % engine.run_id, daemon=True)
    _bg_threads[engine.run_id] = th
    th.start()
    return {
        "ok": True,
        "run_id": engine.run_id,
        "mode": mode or ("async_once" if once else "continuous"),
        "status": "starting",
        "universe_count": len(universe),
        "precise_limit": precise_limit,
        "once": bool(once),
        "async": True,
        "warning": "local_no_auth: POST /paper/runs is unauthenticated; bind to localhost only",
    }


def stop_run(run_id, base_dir=None):
    """POST /paper/runs/<id>/stop — 写 STOP 文件。"""
    from pathlib import Path as _Path
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from stocklib.paper.engine import clear_active, read_active, write_active
    from stocklib.paper.journal import PaperJournal

    if base_dir is None:
        base_dir = str(_Path(__file__).resolve().parents[2])
    j = PaperJournal(run_id, base_dir=base_dir)
    try:
        j.read_meta()
    except FileNotFoundError:
        return {"error": "run_not_found", "run_id": run_id}
    j.ensure_dirs()
    stop_path = os.path.join(j.root, "STOP")
    ts = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%dT%H:%M:%S")
    with open(stop_path, "w", encoding="utf-8") as f:
        f.write(ts)
    try:
        meta = j.read_meta()
        meta["status"] = "stop_requested"
        meta["stop_requested_at"] = ts
        j.write_meta(meta)
    except Exception:
        pass
    act = read_active(base_dir) or {}
    meta_status = None
    try:
        meta_status = j.read_meta().get("status")
    except Exception:
        pass
    # 已结束的 once/demo：直接清 active；运行中：标 stop_requested，引擎退出后再 clear
    if act.get("run_id") == run_id:
        if meta_status in {"once_done", "demo_done", "stopped", "skipped_outside_session"} or act.get("once"):
            clear_active(base_dir)
            try:
                meta = j.read_meta()
                meta["status"] = "stopped"
                meta["stopped_at"] = ts
                j.write_meta(meta)
            except Exception:
                pass
        else:
            from stocklib.paper.engine import write_active as _wa
            _wa(run_id, base_dir, status="stop_requested")
    return {"ok": True, "run_id": run_id, "stop": stop_path, "ts": ts, "active": False}
