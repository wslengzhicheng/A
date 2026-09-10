"""走势预测（纯函数，PLAN.md §4.5）：回归趋势 + 支撑/阻力 + 信号共振展望。
置信度判定：共振度 c≥70% 且 R²≥0.6 → 高；c≥55% 或 R²≥0.35 → 中；否则低。
"""
from . import indicators as ind

CONF_HIGH_C, CONF_HIGH_R2 = 0.70, 0.60
CONF_MID_C, CONF_MID_R2 = 0.55, 0.35


def trend_regression(closes):
    """近20/60日线性回归 → 趋势方向与强度。"""
    out = {}
    for window in (20, 60):
        seq = closes[-window:]
        if len(seq) < max(10, window // 2):
            out[window] = None
            continue
        slope, r2 = ind.linreg(seq)
        mean = sum(seq) / len(seq)
        ann_pct = slope / mean * 250 * 100 if mean else 0.0  # 斜率年化（%）
        direction = "上行" if ann_pct > 5 else ("下行" if ann_pct < -5 else "震荡")
        out[window] = {"annualized_pct": round(ann_pct, 1), "r2": round(r2, 2),
                       "direction": direction}
    return out


def key_levels(klines, x, lookback_days=20):
    """支撑 = max(MA20, BOLL下轨, 近N日低点, 缠论中枢边界)；阻力对称取 min。"""
    price = klines[-1]["close"]
    window = max(5, min(int(lookback_days or 20), len(klines)))
    recent = klines[-window:]
    lo20 = min(k["low"] for k in recent)
    hi20 = max(k["high"] for k in recent)
    ma20 = x["ma20"][-1]
    b_up, b_low = x["boll_up"][-1], x["boll_low"][-1]
    support_cands = [v for v in (ma20, b_low, lo20) if v is not None and v < price]
    resist_cands = [v for v in (hi20, b_up) if v is not None and v > price]
    chan = x.get("chan") or {}
    if chan.get("pivots"):
        pv = chan["pivots"][-1]
        zg, zd = pv["zg"], pv["zd"]
        if zg < price:
            support_cands.append(zg)
        elif zd < price:
            support_cands.append(zd)
        if zd > price:
            resist_cands.append(zd)
        elif zg > price:
            resist_cands.append(zg)
    support = max(support_cands) if support_cands else lo20
    resistance = min(resist_cands) if resist_cands else hi20
    return {
        "window_days": window,
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "support_pct": round((support / price - 1) * 100, 1),
        "resistance_pct": round((resistance / price - 1) * 100, 1),
    }


def resonance(signals):
    """信号共振度：非零信号中同向占比 c 与主方向。"""
    nonzero = [s["score"] for s in signals if s["score"] != 0]
    if not nonzero:
        return 0.0, 0
    pos = sum(1 for v in nonzero if v > 0)
    neg = len(nonzero) - pos
    direction = 1 if pos >= neg else -1
    c = max(pos, neg) / len(nonzero)
    return c, direction


def outlook(signals, closes):
    """5~10个交易日展望：共振 + 回归合成 → 方向与置信度。"""
    reg = trend_regression(closes)
    c, sig_dir = resonance(signals)
    reg20 = reg.get(20)
    r2 = reg20["r2"] if reg20 else 0.0
    reg_dir = 0
    if reg20:
        reg_dir = 1 if reg20["direction"] == "上行" else (-1 if reg20["direction"] == "下行" else 0)
    combined = sig_dir + reg_dir
    if combined >= 2 or (combined == 1 and c >= CONF_MID_C):
        direction = "上行"
    elif combined <= -2 or (combined == -1 and c >= CONF_MID_C):
        direction = "下行"
    else:
        direction = "震荡"
    if sig_dir == reg_dir and c >= CONF_HIGH_C and r2 >= CONF_HIGH_R2:
        confidence = "高"
    elif c >= CONF_MID_C or r2 >= CONF_MID_R2:
        confidence = "中"
    else:
        confidence = "低"
    if sig_dir != 0 and reg_dir != 0 and sig_dir != reg_dir:
        confidence = "低"  # 信号与趋势打架，强制降级
    return {"direction": direction, "confidence": confidence,
            "resonance": round(c, 2), "regression": reg}
