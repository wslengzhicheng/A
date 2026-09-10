"""策略适配器：把近期日 K + analyzer 指标喂给 BacktestStrategy.generate_signals

含 score_gate（综合评分阈值，注册于 stocklib.backtest.STRATEGIES）。。

仅采纳指向最新一根 K 的信号，并按 (strategy_id, code, date/k_index, side) 去重。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from stocklib import analyzer
from stocklib.backtest import STRATEGIES


SignalKey = Tuple[str, str, str, str]


def list_strategy_ids() -> List[str]:
    return sorted(STRATEGIES.keys())


def make_strategy(strategy_id: str, **params):
    """按 STRATEGIES 注册表实例化策略。"""
    sid = str(strategy_id).strip()
    cls = STRATEGIES.get(sid)
    if cls is None:
        known = ", ".join(list_strategy_ids())
        raise KeyError(f"unknown strategy_id={sid!r}; known: {known}")
    try:
        return cls(**params) if params else cls()
    except TypeError:
        # 部分策略构造参数不兼容时退回无参
        return cls()


def signal_dedupe_key(strategy_id: str, code: str, sig: dict) -> SignalKey:
    """去重键：(strategy_id, code, bar_id, side)。

    bar_id 优先 date:k_index，缺 k_index 时仅用 date。
    """
    date = str(sig.get("date") or "")
    ki = sig.get("k_index")
    if ki is not None and str(ki) != "":
        try:
            bar_id = f"{date}:{int(ki)}"
        except (TypeError, ValueError):
            bar_id = f"{date}:{ki}"
    else:
        bar_id = date
    side = str(sig.get("type") or sig.get("side") or "").lower().strip()
    return (str(strategy_id), str(code), bar_id, side)


def filter_latest_bar_signals(signals: Iterable[dict], klines: List[dict]) -> List[dict]:
    """只保留指向最新一根 K 的信号（k_index == last 或 date == last.date）。"""
    if not klines:
        return []
    last_idx = len(klines) - 1
    last_date = str(klines[-1].get("date") or "")
    out: List[dict] = []
    for sig in signals or []:
        if not isinstance(sig, dict):
            continue
        matched = False
        ki = sig.get("k_index")
        if ki is not None and str(ki) != "":
            try:
                if int(ki) == last_idx:
                    matched = True
            except (TypeError, ValueError):
                pass
        if not matched and last_date and str(sig.get("date") or "") == last_date:
            matched = True
        if matched:
            out.append(sig)
    return out


def collect_new_signals(
    strategy_id: str,
    code: str,
    klines: List[dict],
    x: Optional[dict] = None,
    *,
    seen: Optional[Set[SignalKey]] = None,
    strategy=None,
) -> List[dict]:
    """跑策略 → 仅最新 bar → 去重；返回带 strategy_id/code/side 的新信号。

    seen 就地更新。
    """
    if seen is None:
        seen = set()
    if not klines:
        return []
    if strategy is None:
        strategy = make_strategy(strategy_id)
    if x is None:
        x = analyzer.compute_indicators(klines)
    raw = strategy.generate_signals(klines, x) or []
    latest = filter_latest_bar_signals(raw, klines)
    new_rows: List[dict] = []
    for sig in latest:
        key = signal_dedupe_key(strategy_id, code, sig)
        if key in seen:
            continue
        seen.add(key)
        row = dict(sig)
        row["strategy_id"] = str(strategy_id)
        row["code"] = str(code)
        row["side"] = str(sig.get("type") or sig.get("side") or "").lower()
        new_rows.append(row)
    return new_rows


def load_seen_from_signals(rows: Iterable[dict]) -> Set[SignalKey]:
    """从已落盘 signals.jsonl 恢复去重集合。"""
    seen: Set[SignalKey] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sid = row.get("strategy_id") or ""
        code = row.get("code") or ""
        # 兼容已存 type 或 side
        sig = {
            "date": row.get("date") or row.get("asof_date") or "",
            "k_index": row.get("k_index"),
            "type": row.get("side") or row.get("type") or "",
        }
        if sid and code:
            seen.add(signal_dedupe_key(str(sid), str(code), sig))
    return seen
