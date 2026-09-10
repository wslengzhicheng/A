"""模拟盘引擎：交易时段轮询、多策略并行、撮合与落盘。"""
from __future__ import annotations

import json
import os
import time
import traceback
from datetime import datetime, time as dtime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from stocklib.paper.account import Account, target_qty_for_pct
from stocklib.paper.broker import BrokerSim, DEFAULT_FEES
from stocklib.paper.journal import PaperJournal, new_run_id, paper_root
from stocklib.paper.metrics import summarize
from stocklib.paper.strategies_adapter import (
    SignalKey,
    collect_new_signals,
    list_strategy_ids,
    load_seen_from_signals,
    make_strategy,
)
from stocklib.paper.learning import attach_learning, learning_fields

CN_TZ = ZoneInfo("Asia/Shanghai")

# 默认：工作日 09:30-11:30, 13:00-15:00
DEFAULT_SESSIONS = (
    (dtime(9, 30), dtime(11, 30)),
    (dtime(13, 0), dtime(15, 0)),
)

ACTIVE_FILE = "_active.json"


def now_cn(dt: Optional[datetime] = None) -> datetime:
    if dt is None:
        return datetime.now(CN_TZ)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=CN_TZ)
    return dt.astimezone(CN_TZ)


def is_trading_session(
    dt: Optional[datetime] = None,
    *,
    force: bool = False,
    sessions: Optional[Tuple[Tuple[dtime, dtime], ...]] = None,
) -> bool:
    """Asia/Shanghai 交易时段检查。force=True 时恒为 True（盘后联调）。"""
    if force:
        return True
    dt = now_cn(dt)
    if dt.weekday() >= 5:
        return False
    sessions = sessions or DEFAULT_SESSIONS
    t = dt.time()
    for start, end in sessions:
        if start <= t <= end:
            return True
    return False


def _limit_pct_for_code(code: str) -> float:
    c = str(code)
    if c.startswith(("300", "301", "688", "689")):
        return 0.20
    if c.startswith(("8", "4")):  # 北交所粗估
        return 0.30
    return 0.10


def infer_limit_flags(snapshot: dict, code: str) -> Tuple[bool, bool]:
    """用昨收粗判涨跌停（无官方字段时）。"""
    snap = snapshot or {}
    price = float(snap.get("price") or 0)
    prev = float(snap.get("prev_close") or 0)
    if price <= 0 or prev <= 0:
        return False, False
    lim = _limit_pct_for_code(code)
    # 允许 0.1% 误差
    eps = 0.001
    limit_up = price >= prev * (1.0 + lim) * (1.0 - eps)
    limit_down = price <= prev * (1.0 - lim) * (1.0 + eps)
    return bool(limit_up), bool(limit_down)


def active_path(base_dir: Optional[str] = None) -> str:
    return os.path.join(paper_root(base_dir), ACTIVE_FILE)


def write_active(run_id: str, base_dir: Optional[str] = None, **extra) -> None:
    root = paper_root(base_dir)
    os.makedirs(root, exist_ok=True)
    payload = {"run_id": run_id, "updated_at": now_cn().strftime("%Y-%m-%dT%H:%M:%S"), **extra}
    with open(active_path(base_dir), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def read_active(base_dir: Optional[str] = None) -> Optional[dict]:
    p = active_path(base_dir)
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def clear_active(base_dir: Optional[str] = None) -> None:
    p = active_path(base_dir)
    if os.path.isfile(p):
        try:
            os.remove(p)
        except OSError:
            pass


class PaperEngine:
    """多策略纸面引擎。

    fetch_* / resolve 可注入，便于离线单测（无网络）。
    """

    def __init__(
        self,
        *,
        strategies: List[str],
        universe: List[str],
        init_cash: float = 1_000_000.0,
        poll_sec: int = 60,
        target_pct: float = 0.15,
        kline_count: int = 180,
        kline_period: str = "day",
        benchmark_index: str = "sh000300",
        fees: Optional[dict] = None,
        run_id: Optional[str] = None,
        base_dir: Optional[str] = None,
        force_poll: bool = False,
        max_positions: int = 5,
        resolve: Optional[Callable] = None,
        fetch_snapshot: Optional[Callable] = None,
        fetch_kline: Optional[Callable] = None,
        compute_indicators: Optional[Callable] = None,
    ):
        self.strategy_ids = [str(s).strip() for s in strategies if str(s).strip()]
        if not self.strategy_ids:
            raise ValueError("strategies required")
        unknown = [s for s in self.strategy_ids if s not in set(list_strategy_ids())]
        if unknown:
            raise KeyError(f"unknown strategies: {unknown}; known={list_strategy_ids()}")
        self.universe = [str(c).strip() for c in universe if str(c).strip()]
        if not self.universe:
            raise ValueError("universe required")
        self.init_cash = float(init_cash)
        self.poll_sec = max(1, int(poll_sec))
        self.target_pct = float(target_pct)
        self.kline_count = max(40, int(kline_count))
        try:
            from stocklib.sources.eastmoney import normalize_period as _np
            self.kline_period = _np(kline_period)
        except Exception:
            self.kline_period = str(kline_period or "day").strip().lower() or "day"
        self.fees = dict(DEFAULT_FEES)
        if fees:
            self.fees.update(fees)
        self.base_dir = base_dir
        self.force_poll = bool(force_poll)
        self.benchmark_index = str(benchmark_index or "sh000300").strip() or "sh000300"
        self.max_positions = max(1, int(max_positions))
        self.run_id = run_id or new_run_id()
        self.journal = PaperJournal(self.run_id, base_dir=base_dir)
        self.broker = BrokerSim(self.fees)
        self.accounts: Dict[str, Account] = {}
        self.strategies = {sid: make_strategy(sid) for sid in self.strategy_ids}
        self.seen: Set[SignalKey] = set()
        self._last_asof_date: Optional[str] = None
        self._stop_requested = False
        self._running = False

        # 可注入数据层
        self._resolve = resolve
        self._fetch_snapshot = fetch_snapshot
        self._fetch_kline = fetch_kline
        self._compute_indicators = compute_indicators
        # offline/stubbed runs skip live index benchmark fetch
        self._data_injected = any(
            x is not None for x in (resolve, fetch_snapshot, fetch_kline)
        )

    # ---- data helpers ----
    def _ensure_data_fns(self) -> None:
        if self._resolve is None or self._fetch_snapshot is None or self._fetch_kline is None:
            from stocklib import datasource
            if self._resolve is None:
                self._resolve = datasource.resolve
            if self._fetch_snapshot is None:
                self._fetch_snapshot = (
                    lambda market, code: datasource.get_snapshot(market, code, fresh=True)
                )
            if self._fetch_kline is None:
                _period = self.kline_period
                self._fetch_kline = (
                    lambda market, code, count, _p=_period: datasource.get_kline(
                        market, code, count=count, fresh=True, period=_p
                    )
                )
        if self._compute_indicators is None:
            from stocklib import analyzer
            self._compute_indicators = analyzer.compute_indicators

    def _stop_flag_path(self) -> str:
        return os.path.join(self.journal.root, "STOP")

    def request_stop(self) -> None:
        self._stop_requested = True
        self.journal.ensure_dirs()
        with open(self._stop_flag_path(), "w", encoding="utf-8") as f:
            f.write(now_cn().strftime("%Y-%m-%dT%H:%M:%S"))

    def _check_stop(self) -> bool:
        if self._stop_requested:
            return True
        if os.path.isfile(self._stop_flag_path()):
            self._stop_requested = True
            return True
        return False

    def create_or_resume(self, resume: bool = False) -> dict:
        """创建 run 或从已有目录恢复账户与 seen。"""
        self.journal.ensure_dirs()
        meta_exists = os.path.isfile(self.journal.meta_path)
        if resume and meta_exists:
            meta = self.journal.read_meta()
            for sid in self.strategy_ids:
                try:
                    self.accounts[sid] = self.journal.load_account(sid)
                except FileNotFoundError:
                    self.accounts[sid] = Account(
                        strategy_id=sid,
                        name=sid,
                        cash=self.init_cash,
                        init_cash=self.init_cash,
                        params={
                            "init_cash": self.init_cash,
                            "lot": self.fees.get("lot", 100),
                            "target_pct": self.target_pct,
                        },
                    )
            self.seen = load_seen_from_signals(self.journal.read_jsonl("signals.jsonl"))
            self._last_asof_date = meta.get("last_asof_date")
            meta["status"] = "running"
            meta["resumed_at"] = now_cn().strftime("%Y-%m-%dT%H:%M:%S")
            meta["phase"] = meta.get("phase") or "P3"
            try:
                from stocklib.paper.learning import score_version as _sv
                meta.setdefault("score_version", _sv())
            except Exception:
                pass
            meta["resume"] = {
                "accounts": list(self.accounts.keys()),
                "seen_signals": len(self.seen),
                "last_asof_date": self._last_asof_date,
            }
            self.journal.write_meta(meta)
            return meta

        meta = self.journal.create_run({
            "run_id": self.run_id,
            "status": "running",
            "strategies": list(self.strategy_ids),
            "universe": list(self.universe),
            "init_cash": self.init_cash,
            "target_pct": self.target_pct,
            "poll_sec": self.poll_sec,
            "kline": self.kline_period,
            "kline_period": self.kline_period,
            "benchmark_index": self.benchmark_index,
            "kline_count": self.kline_count,
            "fees": self.fees,
            "force_poll": self.force_poll,
            "max_positions": self.max_positions,
            "phase": "P3",
            "score_version": __import__(
                "stocklib.paper.learning", fromlist=["score_version"]
            ).score_version(),
        })
        for sid in self.strategy_ids:
            acct = Account(
                strategy_id=sid,
                name=sid,
                cash=self.init_cash,
                init_cash=self.init_cash,
                params={
                    "init_cash": self.init_cash,
                    "lot": self.fees.get("lot", 100),
                    "target_pct": self.target_pct,
                },
            )
            self.accounts[sid] = acct
            self.journal.save_account(acct)
        return meta

    def _asof_date(self) -> str:
        return now_cn().strftime("%Y-%m-%d")

    def _maybe_release_t1(self, asof: str) -> None:
        if self._last_asof_date and self._last_asof_date == asof:
            return
        # 日期变化（含首次）：释放 buy_date < asof 的冻结
        for sid, acct in self.accounts.items():
            released = acct.release_t1(asof)
            if released:
                self.journal.append_equity(sid, {
                    "date": asof,
                    "event": "release_t1",
                    "released": released,
                    "equity": round(acct.equity(), 4),
                    "cash": round(acct.cash, 4),
                })
        self._last_asof_date = asof

    def _size_buy_qty(self, acct: Account, price: float) -> int:
        lot = int(self.fees.get("lot", 100) or 100)
        return target_qty_for_pct(acct.equity(), price, self.target_pct, lot=lot)

    def _should_skip_buy(self, acct: Account, code: str) -> str:
        if acct.get_position(code):
            return "already_holding"
        if len(acct.positions) >= self.max_positions:
            return "max_positions"
        return ""


    def _update_index_benchmark(self, meta: dict) -> dict:
        """Best-effort: store HS300 (or configured) base/last prices on meta."""
        if getattr(self, "_data_injected", False):
            return meta
        symbol = meta.get("benchmark_index") or getattr(self, "benchmark_index", None) or "sh000300"
        try:
            from stocklib import datasource
            market, code, name = datasource.resolve_index_symbol(symbol)
            snap_pack = datasource.get_index_snapshot(market, code, fresh=True)
            snap = snap_pack[0] if isinstance(snap_pack, tuple) else snap_pack
            px = float((snap or {}).get("price") or 0)
            if px <= 0:
                return meta
            meta["benchmark_index"] = ("sh" if market == "sh" else "sz") + str(code)
            meta["benchmark_index_name"] = name or meta.get("benchmark_index_name") or "沪深300"
            if not meta.get("benchmark_base_index_price"):
                meta["benchmark_base_index_price"] = px
            meta["benchmark_last_index_price"] = px
        except Exception as e:
            meta.setdefault("benchmark_index_error", str(e)[:120])
        return meta


    def _write_scan_progress(self, *, done: int, total: int, code: str = "", phase: str = "scanning", **extra) -> None:
        """写入 meta.scan_progress，供全市场 once 赛马进度条使用。"""
        try:
            meta = self.journal.read_meta()
        except FileNotFoundError:
            meta = {"run_id": self.run_id}
        prog = {
            "done": int(done),
            "total": int(total),
            "code": str(code or ""),
            "phase": str(phase or "scanning"),
            "pct": round((float(done) / float(total)) * 100.0, 2) if total else 0.0,
            "ts": now_cn().strftime("%Y-%m-%dT%H:%M:%S"),
        }
        prog.update(extra)
        meta["scan_progress"] = prog
        if phase in ("scanning", "pricing"):
            meta["status"] = "running"
        self.journal.write_meta(meta)
        try:
            write_active(self.run_id, self.base_dir, status=meta.get("status") or "running",
                         scan_progress=prog)
        except Exception:
            pass

    def poll_once(self) -> dict:
        """单次轮询：取数 → 信号 → 下单 → 盯市落盘。"""
        self._ensure_data_fns()
        asof = self._asof_date()
        self._maybe_release_t1(asof)
        summary: Dict[str, Any] = {
            "asof_date": asof,
            "ts": now_cn().strftime("%Y-%m-%dT%H:%M:%S"),
            "codes": {},
            "signals": 0,
            "orders": 0,
            "fills": 0,
            "errors": [],
        }
        prices: Dict[str, float] = {}
        total_codes = len(self.universe)
        self._write_scan_progress(done=0, total=total_codes, phase="scanning")

        for idx, raw_code in enumerate(self.universe):
            if self._check_stop():
                summary["stopped"] = True
                summary["stop_at"] = idx
                self._write_scan_progress(
                    done=idx, total=total_codes, code=str(raw_code), phase="stopped")
                break
            if idx == 0 or (idx + 1) % 5 == 0 or idx + 1 == total_codes:
                self._write_scan_progress(
                    done=idx, total=total_codes, code=str(raw_code), phase="scanning")
            try:
                market, code, name = self._resolve(raw_code)
                code = str(code)
                snap_pack = self._fetch_snapshot(market, code)
                if isinstance(snap_pack, tuple):
                    snapshot, _src = snap_pack
                else:
                    snapshot = snap_pack
                snapshot = snapshot or {}
                price = float(snapshot.get("price") or 0)
                if price <= 0:
                    summary["errors"].append(f"{code}: no price")
                    continue
                if snapshot.get("is_trading") is False and not self.force_poll:
                    # 停牌：仍更新价格但不开新仓；本轮跳过交易
                    prices[code] = price
                    summary["codes"][code] = {"skipped": "not_trading", "price": price}
                    continue

                k_pack = self._fetch_kline(market, code, self.kline_count)
                if isinstance(k_pack, tuple):
                    klines, _ksrc = k_pack
                else:
                    klines = k_pack
                klines = list(klines or [])
                if len(klines) < 30:
                    summary["errors"].append(f"{code}: klines too short ({len(klines)})")
                    continue

                # 盘中：用快照价覆盖最后一根 close，便于信号价贴近现价
                klines = [dict(k) for k in klines]
                klines[-1] = dict(klines[-1])
                klines[-1]["close"] = price

                x = self._compute_indicators(klines)
                limit_up, limit_down = infer_limit_flags(snapshot, code)
                prices[code] = price
                code_info = {
                    "name": name or code,
                    "price": price,
                    "limit_up": limit_up,
                    "limit_down": limit_down,
                    "actions": [],
                }

                for sid in self.strategy_ids:
                    acct = self.accounts[sid]
                    new_sigs = collect_new_signals(
                        sid, code, klines, x,
                        seen=self.seen,
                        strategy=self.strategies[sid],
                    )
                    for sig in new_sigs:
                        summary["signals"] += 1
                        side = sig.get("side") or ""
                        reason = str(sig.get("reason") or "")
                        score_val = sig.get("score")
                        if score_val is None and isinstance(sig.get("extra"), dict):
                            score_val = sig.get("extra", {}).get("score")
                        if score_val is not None and "score=" not in reason.lower():
                            reason = (reason + ("|" if reason else "") + f"score={score_val}")
                        learn = learning_fields(reason=reason, score=score_val, x=x, sig=sig)
                        sig_row = {
                            "ts": summary["ts"],
                            "asof_date": asof,
                            "strategy_id": sid,
                            "code": code,
                            "side": side,
                            "price": float(sig.get("price") or price),
                            "k_index": sig.get("k_index"),
                            "date": sig.get("date"),
                        }
                        attach_learning(sig_row, learn)
                        self.journal.append_signal(sig_row)

                        if side == "buy":
                            skip = self._should_skip_buy(acct, code)
                            if skip:
                                code_info["actions"].append({
                                    "strategy": sid, "side": "buy", "status": "skipped",
                                    "reason": skip,
                                })
                                continue
                            qty = self._size_buy_qty(acct, price)
                            if qty < int(self.fees.get("lot", 100) or 100):
                                code_info["actions"].append({
                                    "strategy": sid, "side": "buy", "status": "skipped",
                                    "reason": "qty_too_small",
                                })
                                continue
                            order, fill = self.broker.place_market_order(
                                acct, code, "buy", qty, price, asof,
                                limit_up=limit_up, reason=reason,
                            )
                        elif side == "sell":
                            pos = acct.get_position(code)
                            if not pos:
                                code_info["actions"].append({
                                    "strategy": sid, "side": "sell", "status": "skipped",
                                    "reason": "no_position",
                                })
                                continue
                            avail = int(pos.get("available_qty") or 0)
                            lot = int(self.fees.get("lot", 100) or 100)
                            qty = (avail // lot) * lot
                            if qty <= 0:
                                code_info["actions"].append({
                                    "strategy": sid, "side": "sell", "status": "skipped",
                                    "reason": "t1_or_zero",
                                })
                                continue
                            order, fill = self.broker.place_market_order(
                                acct, code, "sell", qty, price, asof,
                                limit_down=limit_down, reason=reason,
                            )
                        else:
                            continue

                        summary["orders"] += 1
                        learn_extra = {
                            **learn,
                            "limit_up": bool(limit_up),
                            "limit_down": bool(limit_down),
                        }
                        attach_learning(order, learn_extra)
                        self.journal.append_order(order)
                        if fill:
                            summary["fills"] += 1
                            attach_learning(fill, learn_extra)
                            fill.setdefault("reject_reason", order.get("reject_reason") or "")
                            self.journal.append_fill(fill)
                        code_info["actions"].append({
                            "strategy": sid,
                            "side": side,
                            "status": order.get("status"),
                            "reject_reason": order.get("reject_reason") or "",
                            "qty": order.get("qty"),
                            "fill_qty": (fill or {}).get("qty"),
                            "reason": reason,
                        })

                summary["codes"][code] = code_info
            except Exception as e:
                summary["errors"].append(f"{raw_code}: {e}")
                summary.setdefault("tracebacks", []).append(traceback.format_exc()[-500:])

        if not summary.get("stopped"):
            self._write_scan_progress(done=total_codes, total=total_codes, phase="mark_to_market")

        # 盯市 + 落盘
        for sid, acct in self.accounts.items():
            eq = acct.mark_to_market(prices)
            self.journal.append_equity(sid, {
                "date": asof,
                "ts": summary["ts"],
                "equity": round(eq, 4),
                "cash": round(acct.cash, 4),
                "market_value": round(acct.market_value(prices), 4),
            })
            self.journal.save_account(acct)

        try:
            meta = self.journal.read_meta()
        except FileNotFoundError:
            meta = {"run_id": self.run_id}
        meta["status"] = "running"
        meta["last_poll"] = summary["ts"]
        meta["last_asof_date"] = asof
        meta["last_summary"] = {
            "signals": summary["signals"],
            "orders": summary["orders"],
            "fills": summary["fills"],
            "errors": summary["errors"][:5],
        }
        # 基准：首次成功拿到的价为等权买入持有基期；每轮刷新 last_prices
        if prices:
            rounded = {str(c): round(float(p), 4) for c, p in prices.items() if float(p) > 0}
            if rounded:
                meta["last_prices"] = rounded
                meta = self._update_index_benchmark(meta)
                if not meta.get("benchmark_base_prices"):
                    meta["benchmark_base_prices"] = dict(rounded)
                    meta["benchmark_base_ts"] = summary["ts"]
                # 与 compare 对齐：有指数基期价则记 index:xxx，否则等权
                if meta.get("benchmark_base_index_price"):
                    idx = meta.get("benchmark_index") or "sh000300"
                    meta["benchmark_type"] = "index:%s" % idx
                    meta["benchmark_name"] = meta.get("benchmark_index_name") or "沪深300"
                else:
                    meta["benchmark_type"] = "equal_weight_universe"
                    meta.setdefault("benchmark_name", "等权持有")
        phase_done = "stopped" if summary.get("stopped") else "done"
        meta["scan_progress"] = {
            "done": int(summary.get("stop_at") or total_codes),
            "total": total_codes,
            "phase": phase_done,
            "pct": round((float(summary.get("stop_at") or total_codes) / float(total_codes)) * 100.0, 2) if total_codes else 100.0,
            "ts": summary["ts"],
            "signals": summary["signals"],
            "fills": summary["fills"],
        }
        self.journal.write_meta(meta)
        write_active(self.run_id, self.base_dir, status="running", last_poll=summary["ts"],
                     scan_progress=meta.get("scan_progress"))
        return summary

    def write_day_end(self, asof: Optional[str] = None) -> dict:
        """可选日终摘要。"""
        asof = asof or self._asof_date()
        report = {"date": asof, "strategies": {}}
        for sid, acct in self.accounts.items():
            fills = self.journal.read_fills(sid)
            curve = self.journal.read_equity(sid)
            report["strategies"][sid] = summarize(
                init_cash=acct.init_cash,
                equity=acct.equity(),
                equity_curve=curve,
                fills=fills,
            )
        self.journal.ensure_dirs()
        path = os.path.join(self.journal.root, "daily", f"{asof}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return report

    def run(self, *, once: bool = False, force: Optional[bool] = None) -> dict:
        """启动循环。once=True 时只跑一轮（忽略时段，除非 force 显式 False 且非交易时段则仍受检）。

        规则：
        - once + force：强制跑一轮
        - once 且非交易时段且未 force：跳过交易逻辑但可写 status（返回 skipped）
        - 循环模式：非交易时段 sleep，交易时段 poll
        """
        if force is not None:
            self.force_poll = bool(force)
        self.create_or_resume(resume=os.path.isfile(self.journal.meta_path))
        # 清掉旧 STOP
        if os.path.isfile(self._stop_flag_path()):
            try:
                os.remove(self._stop_flag_path())
            except OSError:
                pass
        self._stop_requested = False
        self._running = True
        write_active(self.run_id, self.base_dir, status="running", once=once)

        last: Dict[str, Any] = {}
        try:
            if once:
                if not is_trading_session(force=self.force_poll):
                    last = {
                        "skipped": True,
                        "reason": "outside_session",
                        "hint": "pass --force to poll after hours",
                    }
                    meta = self.journal.read_meta()
                    meta["status"] = "skipped_outside_session"
                    meta["last_poll"] = now_cn().strftime("%Y-%m-%dT%H:%M:%S")
                    self.journal.write_meta(meta)
                    return last
                last = self.poll_once()
                try:
                    self.write_day_end()
                except Exception:
                    pass
                meta = self.journal.read_meta()
                meta["status"] = "once_done"
                self.journal.write_meta(meta)
                return last

            # 持续模式
            while not self._check_stop():
                if is_trading_session(force=self.force_poll):
                    last = self.poll_once()
                else:
                    # 盘后：尝试写日终一次
                    try:
                        meta = self.journal.read_meta()
                        today = self._asof_date()
                        if meta.get("day_end_written") != today:
                            self.write_day_end(today)
                            meta["day_end_written"] = today
                            meta["status"] = "idle_outside_session"
                            self.journal.write_meta(meta)
                    except Exception:
                        pass
                # 分段 sleep，便于响应 STOP
                slept = 0
                while slept < self.poll_sec and not self._check_stop():
                    time.sleep(min(1, self.poll_sec - slept))
                    slept += 1
        finally:
            self._running = False
            try:
                meta = self.journal.read_meta()
                if self._check_stop():
                    meta["status"] = "stopped"
                elif not once:
                    meta["status"] = meta.get("status") or "stopped"
                meta["stopped_at"] = now_cn().strftime("%Y-%m-%dT%H:%M:%S")
                self.journal.write_meta(meta)
            except Exception:
                pass
            act = read_active(self.base_dir) or {}
            if act.get("run_id") == self.run_id:
                # 结束后清掉 active，避免 detail.active 仍为 true
                clear_active(self.base_dir)
        return last


def compare_run(run_id: str, base_dir: Optional[str] = None) -> List[dict]:
    """多策略对照表（优先沪深300指数基准，失败则等权持有池）。"""
    from stocklib.paper.metrics import (
        equal_weight_benchmark_return,
        excess_return,
        index_benchmark_return,
    )

    journal = PaperJournal(run_id, base_dir=base_dir)
    meta = journal.read_meta()
    base_prices = meta.get("benchmark_base_prices") or {}
    last_prices = dict(meta.get("last_prices") or {})
    bench_index = meta.get("benchmark_index") or "sh000300"
    bench_name = meta.get("benchmark_index_name") or "沪深300"

    # Prefer index return when base/last index prices present
    idx_ret = index_benchmark_return(
        meta.get("benchmark_base_index_price"),
        meta.get("benchmark_last_index_price"),
    )
    if idx_ret is not None:
        bench_ret = idx_ret
        bench_type = "index:%s" % bench_index
        bench_label = bench_name
    else:
        # try live fetch once (best-effort; offline stubs skip)
        try:
            from stocklib import datasource
            market, code, name = datasource.resolve_index_symbol(bench_index)
            snap, _src = datasource.get_index_snapshot(market, code, fresh=False)
            if snap and meta.get("benchmark_base_index_price"):
                last_px = float(snap.get("price") or 0)
                idx_ret = index_benchmark_return(
                    meta.get("benchmark_base_index_price"), last_px)
                if idx_ret is not None:
                    bench_ret = idx_ret
                    bench_type = "index:%s" % bench_index
                    bench_label = name or bench_name
                else:
                    raise ValueError("no index ret")
            else:
                raise ValueError("no index base")
        except Exception:
            ew = equal_weight_benchmark_return(base_prices, last_prices)
            bench_ret = ew
            bench_type = "equal_weight_universe"
            bench_label = "等权持有"

    rows = []
    for sid in journal.list_accounts():
        acct = journal.load_account(sid)
        fills = journal.read_fills(sid)
        curve = journal.read_equity(sid)
        if not last_prices:
            for code, pos in (acct.positions or {}).items():
                lp = float(pos.get("last_price") or 0)
                if lp > 0:
                    last_prices[str(code)] = lp
            if bench_type == "equal_weight_universe" and last_prices and base_prices:
                bench_ret = equal_weight_benchmark_return(base_prices, last_prices)
        rep = summarize(
            init_cash=acct.init_cash,
            equity=acct.equity(),
            equity_curve=curve,
            fills=fills,
        )
        tr = rep["total_return"]
        closed_rounds = int(rep.get("closed_rounds") or 0)
        wr = rep.get("win_rate")
        if closed_rounds == 0:
            wr = None
        rows.append({
            "strategy_id": sid,
            "equity": rep["equity"],
            "total_return": tr,
            "benchmark_return": bench_ret,
            "excess_return": excess_return(tr, bench_ret),
            "benchmark_type": bench_type,
            "benchmark_name": bench_label,
            "max_drawdown": rep["max_drawdown"],
            "trades": closed_rounds if closed_rounds else rep.get("trade_count", 0),
            "closed_rounds": closed_rounds,
            "win_rate": wr,
            "fill_count": rep.get("fill_count", 0),
            "buy_count": rep.get("buy_count", 0),
            "sell_count": rep.get("sell_count", 0),
            "fee_total": rep.get("fee_total", 0.0),
            "cash": round(acct.cash, 4),
            "positions": len(acct.positions),
        })
    rows.sort(key=lambda r: r["total_return"], reverse=True)
    for r in rows:
        r["run_id"] = run_id
        r["universe"] = meta.get("universe")
    return rows
