"""S2 持仓状态引擎（纯函数，无 IO）。

状态枚举（优先级高→低）：
  deep_loss_reduce_only > break_reduce > reduce_watch > reentry_watch > hold

规则（可配置常量）：
  hold:            close > MA20  且  MA20 >= MA60 * 0.98
  break_reduce:    close < MA20 * 0.97 连续 ≥ BREAK_STREAK_THRESHOLD 日
  reduce_watch:    close < MA20 * 0.97 但未满连续天数
  reentry_watch:   close > MA20  且  MA5 > MA10（破位后/监控用）
  deep_loss_reduce_only: 有 cost 且 (cost - px) / cost > DEEP_LOSS_RATIO
  provisional:     True 当数据来自非确认（盘中）会话

研究工具，非投资建议。
"""

from . import indicators as ind

# --------------- configurable constants ---------------
MA_HOLD_TOLERANCE = 0.98       # MA20 >= MA60 * tolerance → hold
BREAK_THRESHOLD = 0.97         # close < MA20 * threshold → break zone
BREAK_STREAK_THRESHOLD = 2     # consecutive sessions in break zone
DEEP_LOSS_RATIO = 0.20         # (cost - px) / cost > ratio → deep loss
MIN_KLINES_FOR_S2 = 61         # need at least 61 bars for MA60


STATUS_PRIORITY = [
    "deep_loss_reduce_only",
    "break_reduce",
    "reduce_watch",
    "reentry_watch",
    "hold",
]

STATUS_LABELS = {
    "hold": "持有",
    "reduce_watch": "减仓观察",
    "break_reduce": "破位减仓",
    "reentry_watch": "回补观察",
    "deep_loss_reduce_only": "深套仅减",
}


def compute_s2(klines, cost=None):
    """Compute S2 status from day-kline bars.

    Args:
        klines: list of dicts with at least 'close', 'date' keys.
                Must be chronologically ordered (oldest first).
        cost:   optional float, position average cost.

    Returns dict:
        status, label, provisional, ma5, ma10, ma20, ma60,
        px (last close), break_streak, asof (last date),
        deep_loss_pct (if cost given), defense_line, reduce_zone, reentry_zone
    """
    if not klines or len(klines) < MIN_KLINES_FOR_S2:
        return {
            "status": "insufficient_data",
            "label": "数据不足",
            "error": f"需要至少 {MIN_KLINES_FOR_S2} 根日K线",
        }

    closes = [k["close"] for k in klines]

    ma5_arr = ind.ma(closes, 5)
    ma10_arr = ind.ma(closes, 10)
    ma20_arr = ind.ma(closes, 20)
    ma60_arr = ind.ma(closes, 60)

    px = closes[-1]
    ma5 = ma5_arr[-1]
    ma10 = ma10_arr[-1]
    ma20 = ma20_arr[-1]
    ma60 = ma60_arr[-1]
    asof = klines[-1].get("date", "")

    if ma20 is None or ma60 is None:
        return {
            "status": "insufficient_data",
            "label": "数据不足",
            "error": "MA20 或 MA60 无法计算",
        }

    break_streak = _count_break_streak(closes, ma20_arr)
    candidates = []

    if cost is not None:
        try:
            cost_f = float(cost)
            if cost_f > 0:
                loss_pct = (cost_f - px) / cost_f
                if loss_pct > DEEP_LOSS_RATIO:
                    candidates.append("deep_loss_reduce_only")
        except (TypeError, ValueError):
            pass

    if px < ma20 * BREAK_THRESHOLD:
        if break_streak >= BREAK_STREAK_THRESHOLD:
            candidates.append("break_reduce")
        else:
            candidates.append("reduce_watch")

    hold_ok = px > ma20 and ma20 >= ma60 * MA_HOLD_TOLERANCE
    if hold_ok:
        candidates.append("hold")
    elif px > ma20 and ma5 is not None and ma10 is not None and ma5 > ma10:
        candidates.append("reentry_watch")

    status = _pick_highest(candidates) if candidates else "hold"

    deep_loss_pct = None
    if cost is not None:
        try:
            cost_f = float(cost)
            if cost_f > 0:
                deep_loss_pct = round((cost_f - px) / cost_f, 4)
        except (TypeError, ValueError):
            pass

    defense_line = round(ma20 * BREAK_THRESHOLD, 3) if ma20 else None
    reduce_zone = round(ma20 * 0.95, 3) if ma20 else None
    reentry_zone = round(ma20 * 1.01, 3) if ma20 else None

    return {
        "status": status,
        "label": STATUS_LABELS.get(status, status),
        "provisional": False,
        "px": round(px, 3),
        "ma5": round(ma5, 3) if ma5 else None,
        "ma10": round(ma10, 3) if ma10 else None,
        "ma20": round(ma20, 3),
        "ma60": round(ma60, 3),
        "break_streak": break_streak,
        "asof": asof,
        "deep_loss_pct": deep_loss_pct,
        "defense_line": defense_line,
        "reduce_zone": reduce_zone,
        "reentry_zone": reentry_zone,
    }


def _count_break_streak(closes, ma20_arr):
    """Count consecutive trailing sessions where close < MA20 * BREAK_THRESHOLD."""
    streak = 0
    for i in range(len(closes) - 1, -1, -1):
        m = ma20_arr[i]
        if m is None:
            break
        if closes[i] < m * BREAK_THRESHOLD:
            streak += 1
        else:
            break
    return streak


def _pick_highest(candidates):
    """Return the highest-priority status among candidates."""
    for s in STATUS_PRIORITY:
        if s in candidates:
            return s
    return candidates[0] if candidates else "hold"


def scan(items, fetch_klines_fn):
    """Batch S2 scan over a list of positions.

    Args:
        items: list of dicts, each with 'code' and optionally 'cost', 'available'.
        fetch_klines_fn: callable(code) -> (klines, market, name, asof_info)
            Should raise on failure and return at least klines list.

    Returns list of result dicts (one per item), each with all compute_s2 fields
    plus 'code', 'name', 'market', 'ok', and optionally 'error'.
    """
    results = []
    for pos in items:
        code = pos.get("code", "").strip()
        if not code:
            results.append({"ok": False, "code": code, "error": "代码为空"})
            continue
        cost = pos.get("cost")
        try:
            klines, market, name, extra = fetch_klines_fn(code)
            s2 = compute_s2(klines, cost=cost)
            s2["ok"] = True
            s2["code"] = code
            s2["name"] = name
            s2["market"] = market
            if pos.get("available") is not None:
                s2["available"] = pos["available"]
            if cost is not None:
                s2["cost"] = cost
            results.append(s2)
        except Exception as e:
            results.append({
                "ok": False,
                "code": code,
                "error": f"{type(e).__name__}: {e}",
            })
    return results


def diff_snapshots(current, previous):
    """Compare two scan result lists to detect status changes.

    Returns list of dicts with code, prev_status, new_status, need_notify.
    """
    prev_map = {}
    for item in (previous or []):
        c = item.get("code")
        if c:
            prev_map[c] = item

    changes = []
    for item in (current or []):
        if not item.get("ok"):
            continue
        code = item["code"]
        prev = prev_map.get(code)
        prev_status = prev["status"] if prev and prev.get("ok") else None
        new_status = item["status"]
        need_notify = prev_status is not None and prev_status != new_status
        changes.append({
            "code": code,
            "name": item.get("name"),
            "prev_status": prev_status,
            "prev_label": STATUS_LABELS.get(prev_status) if prev_status else None,
            "new_status": new_status,
            "new_label": STATUS_LABELS.get(new_status, new_status),
            "need_notify": need_notify,
        })
    return changes
