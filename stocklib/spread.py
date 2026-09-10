"""单票做差价建议 + 盘中时机（S1：日线定带 + 快照现价）。

与买入机会分流：不要求 60–80 分/主力净流入硬门槛。
A 股 T+1：高抛默认按「已有底仓做 T」表述。
"""

MIN_AVG_AMPLITUDE_PCT = 1.2
MIN_BAND_PCT = 1.0
BUY_ZONE_FRAC = 0.35
SELL_ZONE_FRAC = 0.35
LIMIT_NEAR = 0.95
STOP_BUFFER = 0.008


def _last(seq):
    return seq[-1] if seq else None


def _as_fraction(chg):
    """涨跌幅统一为小数。|x|>1 视为百分数（兼容部分源）。"""
    if chg is None:
        return 0.0
    v = float(chg)
    if abs(v) > 1:
        return v / 100.0
    return v


def _round_px(v):
    if v is None:
        return None
    return round(float(v), 2)


def limit_pct(code, name=""):
    name = name or ""
    if "ST" in name.upper():
        return 0.05
    code = str(code or "")
    if code.startswith(("300", "301", "688", "8")):
        return 0.20
    return 0.10


def avg_amplitude_pct(klines, n=10):
    recent = klines[-n:] if klines else []
    amps = []
    for k in recent:
        lo = k.get("low") or 0
        hi = k.get("high")
        if lo > 0 and hi:
            amps.append((hi / lo - 1.0) * 100.0)
    return sum(amps) / len(amps) if amps else 0.0


def today_amplitude_pct(snapshot, klines):
    hi = snapshot.get("high")
    lo = snapshot.get("low")
    if hi and lo and lo > 0:
        return (hi / lo - 1.0) * 100.0
    if klines:
        k = klines[-1]
        if k.get("low"):
            return (k["high"] / k["low"] - 1.0) * 100.0
    return 0.0


def _bias_from_prediction(prediction):
    direction = (prediction or {}).get("direction") or "震荡"
    if "上" in direction:
        return "偏多", "低吸为主，冲高可减仓做 T", False
    if "下" in direction:
        return "偏空", "高抛为主，深跌才考虑回补", False
    return "震荡", "区间内双边做 T", True


def _expand_levels(price, support, resistance, avg_amp):
    """区间过窄时按均振幅扩出可交易带，避免支撑阻力贴在一起。"""
    if support is None or resistance is None or not price:
        return support, resistance
    width = resistance - support
    min_width = price * max(MIN_BAND_PCT, avg_amp * 0.45) / 100.0
    if width >= min_width and resistance > support:
        return support, resistance
    half = min_width / 2.0
    return price - half, price + half


def _zones(support, resistance):
    rng = max(resistance - support, 1e-9)
    buy_low, buy_high = support, support + rng * BUY_ZONE_FRAC
    sell_low, sell_high = resistance - rng * SELL_ZONE_FRAC, resistance
    if buy_high >= sell_low:
        mid = (support + resistance) / 2.0
        buy_high = mid
        sell_low = mid
    return {
        "buy_low": _round_px(buy_low),
        "buy_high": _round_px(buy_high),
        "sell_low": _round_px(sell_low),
        "sell_high": _round_px(sell_high),
    }


def classify_timing(price, zones, *, bias, both_sides, change_frac, limit,
                    rsi6=None, is_trading=True):
    """现价落在哪一带 → 观望 / 低吸窗口 / 高抛窗口 / 停做。"""
    if not is_trading:
        return "停做", "当前无成交（停牌或未开盘），不宜做差价"
    if limit and abs(change_frac) >= limit * LIMIT_NEAR:
        side = "涨停" if change_frac > 0 else "跌停"
        return "停做", f"接近{side}（涨跌 {change_frac * 100:+.1f}%），差价空间被封死"

    buy_high = zones["buy_high"]
    sell_low = zones["sell_low"]
    in_buy = price <= buy_high
    in_sell = price >= sell_low
    oversold = rsi6 is not None and rsi6 < 30
    overbought = rsi6 is not None and rsi6 > 70

    if in_buy and in_sell:
        return "观望", "低吸/高抛带重叠，区间过窄，等待拉开"

    if in_buy:
        extra = "；RSI6 超卖" if oversold else ""
        if bias == "偏空" and not oversold:
            extra += "；日线偏空，回补需更谨慎"
        return "低吸窗口", f"现价落入低吸带（≤{buy_high:.2f}）{extra}"

    if in_sell:
        extra = "；RSI6 超买" if overbought else ""
        extra += "；T+1 需已有底仓"
        if bias == "偏多":
            extra += "；偏多下以减仓做 T 为主"
        return "高抛窗口", f"现价落入高抛带（≥{sell_low:.2f}）{extra}"

    return "观望", "现价在区间中部，等待回落低吸带或冲高抛带"


def build_spread_plan(klines, snapshot, x, levels, score, prediction, signals=None):
    """根据已分析的单票数据生成做差价建议。"""
    snap = snapshot or {}
    price = snap.get("price") or (klines[-1]["close"] if klines else None)
    if not price or not klines:
        return {
            "suitable": False,
            "bias": "震荡",
            "timing": {"action": "观望", "reason": "数据不足"},
            "reasons": ["缺少价格或 K 线"],
            "disclaimer": "需已有底仓才能当日卖出；不构成投资建议。",
        }

    avg_amp = avg_amplitude_pct(klines, 10)
    day_amp = today_amplitude_pct(snap, klines)
    change_frac = _as_fraction(snap.get("change_pct"))
    lim = limit_pct(snap.get("code"), snap.get("name"))
    rsi6 = _last(x.get("rsi6") or [])
    kdj_k = _last(x.get("k") or [])
    turnover = snap.get("turnover")
    amount = snap.get("amount")  # 万元
    is_trading = snap.get("is_trading", True)

    support = levels.get("support") if levels else None
    resistance = levels.get("resistance") if levels else None
    support, resistance = _expand_levels(price, support, resistance, avg_amp)
    zones = _zones(support, resistance)
    band_pct = (zones["sell_high"] - zones["buy_low"]) / price * 100.0

    bias, template, both_sides = _bias_from_prediction(prediction)
    action, action_reason = classify_timing(
        price, zones,
        bias=bias, both_sides=both_sides,
        change_frac=change_frac, limit=lim,
        rsi6=rsi6, is_trading=is_trading,
    )

    reasons = [template, f"近10日均振幅 {avg_amp:.1f}%", f"今日已实现振幅 {day_amp:.1f}%"]
    if rsi6 is not None:
        reasons.append(f"RSI6={rsi6:.0f}")
    if kdj_k is not None:
        reasons.append(f"KDJ.K={kdj_k:.0f}")
    reasons.append(f"综合分 {score:.0f}（仅作偏置，非买入机会门槛）")
    if turnover is not None and turnover < 0.5:
        reasons.append(f"换手 {turnover:.2f}% 偏低，滑点风险")
    if amount is not None and amount < 5000:
        reasons.append(f"成交额 {amount:.0f} 万偏低，进出可能困难")

    halted = action == "停做"
    liquid_ok = (turnover is None or turnover >= 0.4) and (amount is None or amount >= 3000)
    suitable = (not halted) and avg_amp >= MIN_AVG_AMPLITUDE_PCT and band_pct >= MIN_BAND_PCT and liquid_ok

    pred_conf = (prediction or {}).get("confidence") or "低"
    if halted or not suitable:
        confidence = "低"
    elif pred_conf == "高" and avg_amp >= 2.0:
        confidence = "高"
    elif pred_conf == "低":
        confidence = "低"
    else:
        confidence = "中"

    stop = _round_px(zones["buy_low"] * (1 - STOP_BUFFER))
    pos = None
    span = zones["sell_high"] - zones["buy_low"]
    if span > 0:
        pos = round((price - zones["buy_low"]) / span, 2)

    return {
        "suitable": bool(suitable),
        "both_sides": bool(both_sides),
        "bias": bias,
        "template": template,
        "confidence": confidence,
        "score": round(float(score or 0), 1),
        "price": _round_px(price),
        "change_pct": round(change_frac * 100, 2),
        "avg_amplitude_pct": round(avg_amp, 2),
        "today_amplitude_pct": round(day_amp, 2),
        "band_pct": round(band_pct, 2),
        "range_position": pos,
        "limit_pct": round(lim * 100, 0),
        "rsi6": round(rsi6, 1) if rsi6 is not None else None,
        "buy_zone": {"low": zones["buy_low"], "high": zones["buy_high"]},
        "sell_zone": {"low": zones["sell_low"], "high": zones["sell_high"]},
        "stop": stop,
        "support": _round_px(support),
        "resistance": _round_px(resistance),
        "timing": {"action": action, "reason": action_reason},
        "reasons": reasons,
        "spread_plan": (
            f"{bias}｜{action}｜低吸 {zones['buy_low']:.2f}~{zones['buy_high']:.2f}"
            f"｜高抛 {zones['sell_low']:.2f}~{zones['sell_high']:.2f}"
        ),
        "disclaimer": "A 股 T+1：当日买入不能当日卖出；高抛默认按已有底仓做 T。不构成投资建议。",
    }
