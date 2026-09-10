"""信号判定 + 加权评分 + 中文总结（纯函数）。
评分公式见 PLAN.md §4.4：信号 s∈[-2,+2] → 类内加权归一 [-1,+1] → 类别加权 → [0,100]。
全部可校准参数集中在本文件顶部；可选加载 calibration_overrides.json。
"""
import json
import os

from . import chanlun
from . import indicators as ind

# ---- 可校准参数（PLAN.md §8；校准脚本可写 overrides） ----
SCORE_VERSION = "1.3"  # v1.3：行业PE分位、RSI24独立信号、历史回测校准挂钩
# 与 v1.2 及更早分值不可横向比较
WEIGHTS = {"trend": 0.35, "osc": 0.25, "chan": 0.15, "volume": 0.10, "valuation": 0.15}
GRADE_THRESHOLDS = [(75, "强势看多"), (60, "偏多"), (40, "中性震荡"), (25, "偏空"), (0, "弱势看空")]
CATEGORY_NAMES = {"trend": "趋势类", "osc": "摆动类", "chan": "结构类",
                  "volume": "量价", "valuation": "估值/基本面"}
MIN_LONG_KLINE = 60
CHAN_POINT_MAX_AGE = 10

SIGNAL_WEIGHTS = {
    "均线排列": 1.4,
    "均线交叉": 1.5,
    "MACD": 1.4,
    "价格位置": 1.0,
    "年度位置": 0.7,
    "KDJ": 1.0,
    "RSI": 1.1,
    "RSI24": 0.9,
    "BOLL": 0.8,
    "量价配合": 1.0,
    "笔结构": 0.6,
    "中枢位置": 0.8,
    "背驰": 1.5,
    "买卖点": 1.5,
    "市盈率": 1.2,
    "市净率": 0.9,
    "ROE": 1.1,
    "净利增速": 1.0,
    "营收增速": 0.9,
    "毛利率": 0.8,
}

TREND_CHAN_CONFLICT_DAMPEN = 0.5
CONFLICT_NORM_ABS = 0.15
PE_PCT_CHEAP = 0.30
PE_PCT_RICH = 0.70
MIN_INDUSTRY_PEERS = 8


def _apply_calibration_overrides():
    global WEIGHTS, GRADE_THRESHOLDS, SIGNAL_WEIGHTS
    global TREND_CHAN_CONFLICT_DAMPEN, PE_PCT_CHEAP, PE_PCT_RICH
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_overrides.json")
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            ov = json.load(f)
    except (OSError, ValueError):
        return
    if isinstance(ov.get("WEIGHTS"), dict):
        WEIGHTS = {**WEIGHTS, **{k: float(v) for k, v in ov["WEIGHTS"].items() if k in WEIGHTS}}
    if isinstance(ov.get("SIGNAL_WEIGHTS"), dict):
        SIGNAL_WEIGHTS = {**SIGNAL_WEIGHTS, **{k: float(v) for k, v in ov["SIGNAL_WEIGHTS"].items()}}
    if isinstance(ov.get("GRADE_THRESHOLDS"), list) and ov["GRADE_THRESHOLDS"]:
        GRADE_THRESHOLDS = [(int(a), str(b)) for a, b in ov["GRADE_THRESHOLDS"]]
    if "TREND_CHAN_CONFLICT_DAMPEN" in ov:
        TREND_CHAN_CONFLICT_DAMPEN = float(ov["TREND_CHAN_CONFLICT_DAMPEN"])
    if "PE_PCT_CHEAP" in ov:
        PE_PCT_CHEAP = float(ov["PE_PCT_CHEAP"])
    if "PE_PCT_RICH" in ov:
        PE_PCT_RICH = float(ov["PE_PCT_RICH"])


_apply_calibration_overrides()


def _sig(category, name, score, text):
    return {"category": category, "name": name, "score": max(-2, min(2, score)), "text": text}


def _last(seq):
    return seq[-1] if seq and seq[-1] is not None else None


def _signal_weight(name):
    return SIGNAL_WEIGHTS.get(name, 1.0)


def compute_indicators(klines):
    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    vols = [k["volume"] for k in klines]
    dif, dea, hist = ind.macd(closes)
    k, d, j = ind.kdj(highs, lows, closes)
    mid, upper, lower = ind.boll(closes)
    return {
        "klines": klines, "closes": closes, "vols": vols,
        "ma5": ind.ma(closes, 5), "ma10": ind.ma(closes, 10),
        "ma20": ind.ma(closes, 20), "ma60": ind.ma(closes, 60),
        "dif": dif, "dea": dea, "hist": hist,
        "k": k, "d": d, "j": j,
        "rsi6": ind.rsi(closes, 6), "rsi12": ind.rsi(closes, 12), "rsi24": ind.rsi(closes, 24),
        "boll_mid": mid, "boll_up": upper, "boll_low": lower,
        "vol5": ind.ma(vols, 5), "vol10": ind.ma(vols, 10),
        "volatility": ind.annualized_volatility(closes),
        "chan": chanlun.analyze(klines, dif, hist),
    }


def _is_growth_profile(finance):
    if not finance:
        return False
    pyoy = finance.get("profit_yoy")
    ryoy = finance.get("revenue_yoy")
    return (pyoy is not None and pyoy >= 20) or (ryoy is not None and ryoy >= 20)


def _rsi_bucket(r):
    if r is None:
        return None
    if r > 80:
        return -2
    if r > 70:
        return -1
    if r < 20:
        return 2
    if r < 30:
        return 1
    if r >= 50:
        return 1
    return -1


def _rsi_signal(r6, r12):
    s6, s12 = _rsi_bucket(r6), _rsi_bucket(r12)
    if s6 is None and s12 is None:
        return None
    if s6 is None:
        s, label = s12, f"RSI12={r12:.0f}"
    elif s12 is None:
        s, label = s6, f"RSI6={r6:.0f}"
    else:
        if s6 * s12 > 0:
            s = max(s6, s12, key=abs)
        elif s6 * s12 < 0:
            s = int(round((s6 + s12) / 2))
        else:
            s = s6 or s12
        label = f"RSI6={r6:.0f}/RSI12={r12:.0f}"
    tone = {-2: "严重超买", -1: "偏空/超买区", 0: "多空胶着",
            1: "偏多/超卖区", 2: "严重超卖"}.get(s, "")
    return s, f"{label} {tone}".strip()


def _rsi24_signal(r24):
    if r24 is None:
        return None
    if r24 > 75:
        s, tone = -2, "中期超买"
    elif r24 > 65:
        s, tone = -1, "中期偏强需防回撤"
    elif r24 < 25:
        s, tone = 2, "中期超卖"
    elif r24 < 35:
        s, tone = 1, "中期偏弱存修复空间"
    elif r24 >= 50:
        s, tone = 1, "中期多方占优"
    else:
        s, tone = -1, "中期空方占优"
    return s, f"RSI24={r24:.0f} {tone}"


def _pe_signal(pe, finance, industry_ctx=None):
    if pe is None:
        return None
    if pe <= 0:
        return -1, f"PE(TTM)={pe:.1f}，公司处于亏损状态"

    pct = None
    industry = None
    peer_n = 0
    if industry_ctx:
        pct = industry_ctx.get("pe_percentile")
        industry = industry_ctx.get("industry")
        peer_n = industry_ctx.get("peer_count") or 0
        if pct is None and industry_ctx.get("peer_pes"):
            from .sources.eastmoney import compute_pe_percentile
            pct = compute_pe_percentile(pe, industry_ctx["peer_pes"])
            peer_n = max(peer_n, len(industry_ctx.get("peer_pes") or []))

    if pct is not None and peer_n >= MIN_INDUSTRY_PEERS:
        ind_txt = industry or "同行"
        if pct <= PE_PCT_CHEAP:
            return 1, f"PE(TTM)={pe:.1f}，{ind_txt}分位 {pct * 100:.0f}%（偏低，n={peer_n}）"
        if pct >= PE_PCT_RICH:
            return -1, f"PE(TTM)={pe:.1f}，{ind_txt}分位 {pct * 100:.0f}%（偏高，n={peer_n}）"
        return 0, f"PE(TTM)={pe:.1f}，{ind_txt}分位 {pct * 100:.0f}%（中性，n={peer_n}）"

    growth = _is_growth_profile(finance)
    suffix = "（无行业分位，绝对阈值）"
    if growth:
        if pe < 40:
            return 1, f"PE(TTM)={pe:.1f}，成长画像下估值偏低/尚可{suffix}"
        if pe > 100:
            return -1, f"PE(TTM)={pe:.1f}，成长画像下估值偏高{suffix}"
        return 0, f"PE(TTM)={pe:.1f}，成长画像下估值中等{suffix}"
    if pe < 15:
        return 1, f"PE(TTM)={pe:.1f}，估值处于较低水平{suffix}"
    if pe > 50:
        return -1, f"PE(TTM)={pe:.1f}，估值偏高{suffix}"
    if pe < 25:
        return 0, f"PE(TTM)={pe:.1f}，估值中等偏低{suffix}"
    return 0, f"PE(TTM)={pe:.1f}，估值中等{suffix}"


def _pb_signal(pb, finance):
    roe = finance.get("roe") if finance else None
    if pb < 1:
        return 1, f"PB={pb:.2f}，破净状态"
    if roe is not None and roe >= 15:
        if pb > 15:
            return -1, f"PB={pb:.2f}，高 ROE 下溢价仍偏高"
        if pb > 8:
            return 0, f"PB={pb:.2f}，高 ROE 对应溢价中等"
        return 0, f"PB={pb:.2f}，高 ROE 溢价尚可"
    if pb > 10:
        return -1, f"PB={pb:.2f}，净资产溢价高"
    if pb > 5:
        return 0, f"PB={pb:.2f}"
    return 0, f"PB={pb:.2f}"


def build_signals(x, snapshot, finance, industry_ctx=None):
    """指标 + 快照 + 财务 + 可选行业上下文 → (signals, notes)。"""
    signals, notes = [], []
    closes = x["closes"]
    price = closes[-1]
    n = len(closes)
    is_young = n < MIN_LONG_KLINE
    if is_young:
        notes.append(f"K线仅 {n} 根（<{MIN_LONG_KLINE}），疑似次新股，MA60/52周位置等长周期指标与缠论结构已跳过")

    ma5, ma10, ma20 = _last(x["ma5"]), _last(x["ma10"]), _last(x["ma20"])
    ma60 = None if is_young else _last(x["ma60"])
    eps = price * 1e-4
    if ma5 and ma10 and ma20:
        chain = [ma5, ma10, ma20] + ([ma60] if ma60 else [])
        if max(chain) - min(chain) <= eps:
            signals.append(_sig("trend", "均线排列", 0, "均线粘合，方向待选择"))
        elif all(a > b for a, b in zip(chain, chain[1:])):
            signals.append(_sig("trend", "均线排列", 2, "均线多头排列，趋势向好"))
        elif all(a < b for a, b in zip(chain, chain[1:])):
            signals.append(_sig("trend", "均线排列", -2, "均线空头排列，趋势偏弱"))
        elif ma5 > ma10:
            signals.append(_sig("trend", "均线排列", 1, "短期均线上穿中期，趋势修复中"))
        else:
            signals.append(_sig("trend", "均线排列", -1, "短期均线走弱，趋势承压"))
        rel = (price / ma20 - 1) * 100
        if abs(rel) < 0.5:
            signals.append(_sig("trend", "价格位置", 0, "现价紧贴MA20运行"))
        else:
            pos = 2 if rel > 0 else -2
            signals.append(_sig("trend", "价格位置", pos if abs(rel) >= 1 else pos // 2,
                                f"现价位于MA20{'上方' if pos > 0 else '下方'} {abs(rel):.1f}%"))
        m5, m10 = x["ma5"], x["ma10"]
        for back in range(1, min(4, n)):
            i = n - back
            if i < 1 or None in (m5[i], m10[i], m5[i - 1], m10[i - 1]):
                break
            if m5[i - 1] <= m10[i - 1] and m5[i] > m10[i]:
                signals.append(_sig("trend", "均线交叉", 2, f"{back}日内MA5金叉MA10"))
                break
            if m5[i - 1] >= m10[i - 1] and m5[i] < m10[i]:
                signals.append(_sig("trend", "均线交叉", -2, f"{back}日内MA5死叉MA10"))
                break
    dif, dea, hist = _last(x["dif"]), _last(x["dea"]), x["hist"]
    if dif is not None and dea is not None:
        h_now = hist[-1]
        h_prev = hist[-2] if len(hist) > 1 else h_now
        if abs(dif - dea) <= eps and abs(dif) <= eps:
            signals.append(_sig("trend", "MACD", 0, "MACD零轴附近粘合，无明确方向"))
        elif dif > dea:
            s = 2 if h_now > h_prev else 1
            zone = "零轴上方" if dif > 0 else "零轴下方"
            signals.append(_sig("trend", "MACD", s,
                                f"DIF在DEA上方（{zone}），红柱{'放大' if h_now > h_prev else '收窄'}"))
        else:
            s = -2 if h_now < h_prev else -1
            zone = "零轴上方" if dif > 0 else "零轴下方"
            signals.append(_sig("trend", "MACD", s,
                                f"DIF在DEA下方（{zone}），绿柱{'放大' if h_now < h_prev else '收窄'}"))
    if not is_young:
        hi = max(k["high"] for k in _tail_klines(x, 250))
        lo = min(k["low"] for k in _tail_klines(x, 250))
        if hi > lo:
            pct = (price - lo) / (hi - lo) * 100
            s = 1 if pct >= 70 else (-1 if pct <= 30 else 0)
            signals.append(_sig("trend", "年度位置", s,
                                f"现价处于近一年区间 {pct:.0f}% 分位"))

    k_v, d_v, j_v = _last(x["k"]), _last(x["d"]), _last(x["j"])
    if k_v is not None and d_v is not None:
        if abs(k_v - d_v) < 1e-6:
            signals.append(_sig("osc", "KDJ", 0, f"KDJ粘合（K≈D≈{k_v:.0f}）"))
        elif k_v > 80 or (j_v is not None and j_v > 100):
            signals.append(_sig("osc", "KDJ", -2, f"KDJ超买（K={k_v:.0f}），短线回调风险"))
        elif k_v < 20 or (j_v is not None and j_v < 0):
            signals.append(_sig("osc", "KDJ", 2, f"KDJ超卖（K={k_v:.0f}），存在反弹动能"))
        elif k_v > d_v:
            signals.append(_sig("osc", "KDJ", 1, f"KDJ金叉状态（K={k_v:.0f} D={d_v:.0f}）"))
        else:
            signals.append(_sig("osc", "KDJ", -1, f"KDJ死叉状态（K={k_v:.0f} D={d_v:.0f}）"))
    rsi_pack = _rsi_signal(_last(x["rsi6"]), _last(x["rsi12"]))
    if rsi_pack is not None:
        signals.append(_sig("osc", "RSI", rsi_pack[0], rsi_pack[1]))
    rsi24_pack = _rsi24_signal(_last(x["rsi24"]))
    if rsi24_pack is not None:
        signals.append(_sig("osc", "RSI24", rsi24_pack[0], rsi24_pack[1]))
    b_mid, b_up, b_low = _last(x["boll_mid"]), _last(x["boll_up"]), _last(x["boll_low"])
    if b_mid and b_up and b_low:
        if b_up - b_low <= 2 * eps:
            signals.append(_sig("osc", "BOLL", 0, "布林带极度收口，等待方向选择"))
        elif price >= b_up:
            signals.append(_sig("osc", "BOLL", -1, "价格触及布林上轨，短线乖离偏大"))
        elif price <= b_low:
            signals.append(_sig("osc", "BOLL", 1, "价格触及布林下轨，超跌状态"))
        elif price > b_mid:
            signals.append(_sig("osc", "BOLL", 1, "价格站上布林中轨，偏强运行"))
        else:
            signals.append(_sig("osc", "BOLL", -1, "价格位于布林中轨下方，偏弱运行"))

    v5, v10 = _last(x["vol5"]), _last(x["vol10"])
    if v5 and v10 and len(closes) >= 2:
        vol_ratio = v5 / v10
        price_chg = closes[-1] - closes[-2]
        price_up = price_chg > 0
        if abs(price_chg) <= eps and 0.85 <= vol_ratio <= 1.15:
            signals.append(_sig("volume", "量价配合", 0, f"量价平稳（量比{vol_ratio:.2f}），无明确信号"))
        elif vol_ratio > 1.15 and price_up:
            signals.append(_sig("volume", "量价配合", 2, f"放量上涨（5日均量为10日的{vol_ratio:.2f}倍），量价健康"))
        elif vol_ratio > 1.15 and not price_up:
            signals.append(_sig("volume", "量价配合", -2, f"放量下跌（{vol_ratio:.2f}倍），抛压明显"))
        elif vol_ratio < 0.85 and price_up:
            signals.append(_sig("volume", "量价配合", -1, f"缩量上涨（{vol_ratio:.2f}倍），上攻动能存疑"))
        elif vol_ratio < 0.85 and not price_up:
            signals.append(_sig("volume", "量价配合", 0, f"缩量回调（{vol_ratio:.2f}倍），抛压有限"))
        else:
            signals.append(_sig("volume", "量价配合", 1 if price_up else -1,
                                f"量能平稳（{vol_ratio:.2f}倍），价格{'小幅走强' if price_up else '小幅走弱'}"))

    if not is_young:
        _chan_signals(x["chan"], price, n, signals, notes)

    pe, pb = snapshot.get("pe_ttm"), snapshot.get("pb")
    ctx = industry_ctx
    if ctx is None and snapshot.get("pe_percentile") is not None:
        ctx = {
            "industry": snapshot.get("industry"),
            "peer_count": snapshot.get("peer_pe_count") or snapshot.get("peer_count") or MIN_INDUSTRY_PEERS,
            "pe_percentile": snapshot.get("pe_percentile"),
            "peer_pes": snapshot.get("peer_pes"),
        }
    used_industry_pe = False
    if pe is not None:
        pack = _pe_signal(pe, finance, ctx)
        if pack:
            signals.append(_sig("valuation", "市盈率", pack[0], pack[1]))
            used_industry_pe = ctx is not None and "分位" in pack[1] and "无行业分位" not in pack[1]
    if pe is not None and not used_industry_pe:
        notes.append("行业 PE 分位暂不可用，市盈率按绝对阈值计分")
    if pb is not None and pb > 0:
        s, text = _pb_signal(pb, finance)
        signals.append(_sig("valuation", "市净率", s, text))
    if finance:
        roe, pyoy = finance.get("roe"), finance.get("profit_yoy")
        ryoy, gm = finance.get("revenue_yoy"), finance.get("gross_margin")
        report = finance.get("report_date", "")
        if roe is not None:
            s = 1 if roe >= 12 else (-1 if roe < 3 else 0)
            signals.append(_sig("valuation", "ROE", s, f"净资产收益率 {roe:.1f}%（{report}报告期）"))
        if pyoy is not None:
            s = 1 if pyoy >= 15 else (-1 if pyoy < 0 else 0)
            signals.append(_sig("valuation", "净利增速", s, f"净利润同比 {pyoy:+.1f}%"))
        if ryoy is not None:
            s = 1 if ryoy >= 15 else (-1 if ryoy < 0 else 0)
            signals.append(_sig("valuation", "营收增速", s, f"营收同比 {ryoy:+.1f}%"))
        if gm is not None:
            s = 1 if gm >= 40 else (-1 if gm < 10 else 0)
            signals.append(_sig("valuation", "毛利率", s, f"毛利率 {gm:.1f}%"))
    else:
        notes.append("财务摘要暂不可用，基本面评分降级为仅估值快照")
    return signals, notes


def _chan_signals(chan, price, n, signals, notes):
    sts = chan["strokes"]
    if len(sts) < 3:
        notes.append("缠论结构不足（确认笔<3），结构类信号已跳过")
        return
    last = chan["potential"] or sts[-1]
    ext = "（延伸中）" if not last["confirmed"] else ""
    signals.append(_sig("chan", "笔结构", last["dir"],
                        f"当前缠论笔向{'上' if last['dir'] == 1 else '下'}{ext}"))
    if chan["pivots"]:
        pv = chan["pivots"][-1]
        zone = f"{pv['zd']:.2f}~{pv['zg']:.2f}"
        if price > pv["zg"]:
            signals.append(_sig("chan", "中枢位置", 1, f"现价位于最近中枢（{zone}）上方，结构偏强"))
        elif price < pv["zd"]:
            signals.append(_sig("chan", "中枢位置", -1, f"现价位于最近中枢（{zone}）下方，结构偏弱"))
        else:
            signals.append(_sig("chan", "中枢位置", 0, f"现价处于最近中枢（{zone}）内部，属盘整区间"))
    dv = next((d for d in reversed(chan["divergences"])
               if not d["negated"] and n - 1 - d["k"] <= CHAN_POINT_MAX_AGE), None)
    if dv:
        s = (2 if dv["strength"] == "趋势" else 1) * dv["dir"]
        kind = "底背驰" if dv["dir"] == 1 else "顶背驰"
        verb = "下跌动能衰竭" if dv["dir"] == 1 else "上涨动能衰竭"
        signals.append(_sig("chan", "背驰", s,
                            f"MACD{dv['strength']}{kind}（面积 {dv['area_b']:.3f} < {dv['area_a']:.3f}），{verb}"))
    pt = next((p for p in reversed(chan["points"]) if n - 1 - p["k"] <= CHAN_POINT_MAX_AGE), None)
    if pt:
        s = (2 if _PRIO3(pt["t"]) else 1) * pt["dir"]
        label = "疑似二买结构" if pt["t"] == "二买" else ("疑似二卖结构" if pt["t"] == "二卖" else pt["t"])
        signals.append(_sig("chan", "买卖点",
                            s, f"出现缠论{label}（{pt['price']:.2f} 元，{n - 1 - pt['k']}个交易日前）"))


def _PRIO3(t):
    return t in ("一买", "一卖", "三买", "三卖")


def _tail_klines(x, n):
    return x["klines"][-n:]


def _category_norm(cat_signals):
    wsum = num = 0.0
    for s in cat_signals:
        w = _signal_weight(s.get("name", ""))
        num += w * s["score"]
        wsum += w
    if wsum <= 0:
        return 0.0
    return num / (2.0 * wsum)


def score(signals):
    cats = {}
    for s in signals:
        cats.setdefault(s["category"], []).append(s)
    detail = {}
    for cat in WEIGHTS:
        if cat in cats and cats[cat]:
            detail[cat] = _category_norm(cats[cat])
    if not detail:
        return 50.0, "中性震荡", {}

    eff_w = dict(WEIGHTS)
    t, c = detail.get("trend"), detail.get("chan")
    if (
        t is not None and c is not None
        and t * c < 0
        and abs(t) > CONFLICT_NORM_ABS
        and abs(c) > CONFLICT_NORM_ABS
    ):
        eff_w["chan"] = WEIGHTS["chan"] * TREND_CHAN_CONFLICT_DAMPEN

    usable_weight = sum(eff_w[cat] for cat in detail)
    if usable_weight == 0:
        return 50.0, "中性震荡", {}
    total = 0.0
    for cat, norm in detail.items():
        total += (eff_w[cat] / usable_weight) * (norm + 1) / 2 * 100
    total = round(total, 1)
    grade = next(g for th, g in GRADE_THRESHOLDS if total >= th)
    return total, grade, detail


def summarize(signals, total, grade, detail, snapshot, notes):
    parts = [f"综合评分 {total:.0f}/100，结论：{grade}。"]
    cat_txt = []
    for cat in ("trend", "osc", "chan", "volume", "valuation"):
        if cat in detail:
            v = detail[cat]
            tone = "偏多" if v > 0.15 else ("偏空" if v < -0.15 else "中性")
            cat_txt.append(f"{CATEGORY_NAMES[cat]}{tone}")
    parts.append("、".join(cat_txt) + "。")
    t, o = detail.get("trend"), detail.get("osc")
    if t is not None and o is not None:
        if t > 0.2 and o < -0.2:
            parts.append("主要矛盾：中期趋势向好但短线指标超买/走弱，追高需谨慎，回踩短期均线是更稳妥的观察点。")
        elif t < -0.2 and o > 0.2:
            parts.append("主要矛盾：短线出现超卖反弹信号，但中期趋势尚未扭转，反弹宜视为修复而非反转。")
    ch = detail.get("chan")
    if t is not None and ch is not None and t * ch < 0 and abs(t) > CONFLICT_NORM_ABS and abs(ch) > CONFLICT_NORM_ABS:
        parts.append("结构提示：趋势类与缠论结构方向冲突，结构类权重已降权，宜等待共振确认。")
    if snapshot.get("industry") and snapshot.get("pe_percentile") is not None:
        parts.append(
            f"估值参照：{snapshot['industry']}行业 PE 分位 "
            f"{snapshot['pe_percentile'] * 100:.0f}%。"
        )
    strongest = max(signals, key=lambda s: abs(s["score"]), default=None)
    if strongest and abs(strongest["score"]) == 2:
        parts.append(f"当前最强信号：{strongest['name']}——{strongest['text']}。")
    for note in notes:
        parts.append(f"（{note}）")
    return "".join(parts)
