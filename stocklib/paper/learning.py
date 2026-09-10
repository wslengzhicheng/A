"""模拟盘归因学习字段：reason / score / score_version / 指标快照。

P2：在信号、订单、成交落盘时附带；提供廉价快照与 jsonl 回填辅助。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# 从 compute_indicators 输出里抽取的末值键（廉价，无二次重算）
_INDICATOR_KEYS = (
    "ma5", "ma10", "ma20", "ma60",
    "dif", "dea", "hist",
    "k", "d", "j",
    "rsi6", "rsi12", "rsi24",
    "boll_mid", "boll_up", "boll_low",
)


def score_version() -> str:
    try:
        from stocklib import analyzer
        return str(getattr(analyzer, "SCORE_VERSION", "") or "")
    except Exception:
        return ""


def _last_num(series) -> Optional[float]:
    if series is None:
        return None
    if isinstance(series, (int, float)):
        try:
            v = float(series)
        except (TypeError, ValueError):
            return None
        if v != v:  # NaN
            return None
        return round(v, 4)
    if isinstance(series, (list, tuple)):
        for item in reversed(series):
            if item is None:
                continue
            try:
                v = float(item)
            except (TypeError, ValueError):
                continue
            if v != v:
                continue
            return round(v, 4)
    return None


def snapshot_indicators(x: Optional[dict]) -> Dict[str, float]:
    """从已算好的 analyzer.compute_indicators 结果取末根关键指标。"""
    out: Dict[str, float] = {}
    if not isinstance(x, dict):
        return out
    for k in _INDICATOR_KEYS:
        v = _last_num(x.get(k))
        if v is not None:
            out[k] = v
    return out


def learning_fields(
    *,
    reason: str = "",
    score: Any = None,
    x: Optional[dict] = None,
    sig: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> Dict[str, Any]:
    """组装可并入 signal/order/fill 的学习字段。

    score 优先用显式参数，其次 sig["score"]；完整 analyzer.score 需类别信号，
    盘中路径默认不重算（保持廉价），仅写入 score_version + 指标快照。
    """
    sig = sig or {}
    if score is None and "score" in sig:
        score = sig.get("score")
    reason = str(reason or sig.get("reason") or "")
    payload: Dict[str, Any] = {
        "reason": reason,
        "score": score,
        "score_version": score_version(),
        "indicators": snapshot_indicators(x),
    }
    if extra:
        for k, v in extra.items():
            if k not in payload or payload[k] in (None, "", {}):
                payload[k] = v
            elif k == "indicators" and isinstance(v, dict):
                merged = dict(payload["indicators"] or {})
                merged.update(v)
                payload["indicators"] = merged
            else:
                payload[k] = v
    return payload


def attach_learning(row: dict, fields: dict) -> dict:
    """就地合并学习字段到 row（不覆盖已有非空 reason/score）。"""
    if not isinstance(row, dict):
        return row
    for k, v in (fields or {}).items():
        if k == "reason":
            if not row.get("reason") and v:
                row["reason"] = v
            elif "reason" not in row:
                row["reason"] = v or ""
            continue
        if k in ("score",) and row.get(k) is not None:
            continue
        if k == "indicators":
            if not row.get("indicators") and v:
                row["indicators"] = v
            elif "indicators" not in row:
                row["indicators"] = v or {}
            continue
        if k not in row or row.get(k) in (None, ""):
            row[k] = v
    return row


def backfill_jsonl_rows(rows: Iterable[dict], *, default_score_version: Optional[str] = None) -> List[dict]:
    """为内存中的 rows 补齐缺失学习字段（不写盘）。"""
    ver = default_score_version if default_score_version is not None else score_version()
    out: List[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        r.setdefault("reason", r.get("reason") or "")
        if "score" not in r:
            r["score"] = None
        if not r.get("score_version"):
            r["score_version"] = ver
        if "indicators" not in r or r.get("indicators") is None:
            r["indicators"] = {}
        out.append(r)
    return out


def backfill_run_learning(journal, *, write: bool = True) -> dict:
    """回填某 run 的 signals/orders/fills jsonl 缺失学习字段。

    成交若缺 reason，尝试从同 order_id 的订单拷贝。
    write=True 时重写三个 jsonl（覆盖）。
    返回统计。
    """
    ver = score_version()
    signals = list(journal.read_jsonl("signals.jsonl"))
    orders = list(journal.read_jsonl("orders.jsonl"))
    fills = list(journal.read_jsonl("fills.jsonl"))

    order_by_id = {o.get("id"): o for o in orders if isinstance(o, dict) and o.get("id")}

    def _bf(rows, *, copy_reason_from_order: bool = False):
        n = 0
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            r = dict(row)
            changed = False
            if copy_reason_from_order and not r.get("reason"):
                oid = r.get("order_id")
                src = order_by_id.get(oid) or {}
                if src.get("reason"):
                    r["reason"] = src["reason"]
                    changed = True
            if "reason" not in r:
                r["reason"] = ""
                changed = True
            if "score" not in r:
                r["score"] = None
                changed = True
            if not r.get("score_version"):
                r["score_version"] = ver
                changed = True
            if "indicators" not in r or r.get("indicators") is None:
                r["indicators"] = {}
                changed = True
            if changed:
                n += 1
            out.append(r)
        return out, n

    signals2, ns = _bf(signals)
    orders2, no = _bf(orders)
    fills2, nf = _bf(fills, copy_reason_from_order=True)
    stats = {
        "signals_patched": ns,
        "orders_patched": no,
        "fills_patched": nf,
        "score_version": ver,
        "written": False,
    }
    if write:
        journal.ensure_dirs()
        import json
        import os

        def _rewrite(name, rows):
            path = os.path.join(journal.root, name)
            with open(path, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

        _rewrite("signals.jsonl", signals2)
        _rewrite("orders.jsonl", orders2)
        _rewrite("fills.jsonl", fills2)
        stats["written"] = True
    return stats
