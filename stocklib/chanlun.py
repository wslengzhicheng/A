"""缠论结构分析（纯函数，无IO）。实现口径见 PLAN.md §9（缠论集成计划 v5）。

范围（显式声明的工程化子集）：日线单级别、老笔标准、笔中枢；不做线段/多级别/走势类型递归。
- "包含"含边界相等：K1 包含 K2 ⟺ K1.high≥K2.high 且 K1.low≤K2.low（或反向）；
- 笔：顶底分型三K组不共用合并K（分型合并K索引差 ≥4），同型分型取更极端者并延伸上一笔，
  异型不满足条件时丢弃（唯一失败路径，无回溯）；
- 中枢：连续3确认笔重叠，ZG=min(高点) ZD=max(低点)，延伸不改变 ZG/ZD，
  结束后从离开笔的下一笔重扫（保守取舍）；
- 背驰：相邻同向两笔比较，MACD 柱面积与 DIF 同向动能双衰减才成立；
  趋势背驰要求 ≥2 个依次同向且区间不重叠的中枢；
- 买卖点：一买严格由趋势背驰构成；盘整背驰产物命名"类一买"；二买允许以类一买为起点。
"""
import math


# ---------- 2.1 K线包含处理 ----------

def merge_klines(klines):
    """返回合并K列表：{h, l, i0, i1, hi, lo}。
    i0/i1 为覆盖的原始K索引区间（闭区间）；hi/lo 为贡献当前高/低点的原始K索引。
    不变量：相邻合并K互不包含（头部包含对按"保留包含者"处理，不变量全序列成立）。"""
    merged = []
    for idx, k in enumerate(klines):
        h, l = k["high"], k["low"]
        if not merged:
            merged.append({"h": h, "l": l, "i0": idx, "i1": idx, "hi": idx, "lo": idx})
            continue
        m = merged[-1]
        m_contains = m["h"] >= h and m["l"] <= l
        c_contains = h >= m["h"] and l <= m["l"]
        if not (m_contains or c_contains):
            merged.append({"h": h, "l": l, "i0": idx, "i1": idx, "hi": idx, "lo": idx})
            continue
        if len(merged) == 1:
            # 头部特例：保留区间更大的一根（互含/相等时保留先出现者），span 取并集
            if c_contains and not m_contains:
                m.update(h=h, l=l, hi=idx, lo=idx)
            m["i1"] = idx
            continue
        p = merged[-2]
        if p["h"] < m["h"]:  # 上升处理：高低点均取大
            if h > m["h"]:
                m["h"], m["hi"] = h, idx
            if l > m["l"]:
                m["l"], m["lo"] = l, idx
        else:  # 下降处理：高低点均取小
            if h < m["h"]:
                m["h"], m["hi"] = h, idx
            if l < m["l"]:
                m["l"], m["lo"] = l, idx
        m["i1"] = idx
    return merged


# ---------- 2.2 分型 ----------

def fractals(merged):
    """返回分型列表：{t: 'T'|'B', p: 极值价, m: 合并K索引, k: 原始K索引}。"""
    out = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        if b["h"] > a["h"] and b["h"] > c["h"]:
            out.append({"t": "T", "p": b["h"], "m": i, "k": b["hi"]})
        elif b["l"] < a["l"] and b["l"] < c["l"]:
            out.append({"t": "B", "p": b["l"], "m": i, "k": b["lo"]})
    return out


# ---------- 2.3 笔（老笔标准，确定性状态机） ----------

def strokes(fracs, merged):
    """返回 (确认笔列表, 潜在末笔或None)。
    笔：{dir: 1|-1, p0, p1, m0, m1, k0, k1, a0, a1, confirmed}
    a0/a1 为 MACD 面积计算用原始K区间 [a0, a1)（端点K归后一笔）。"""
    sts = []
    if not fracs:
        return sts, _potential(None, merged, sts)
    pending = fracs[0]
    for f in fracs[1:]:
        if f["t"] == pending["t"]:
            more = f["p"] > pending["p"] if f["t"] == "T" else f["p"] < pending["p"]
            if more:
                pending = f
                if sts:  # 延伸上一笔：改写终点
                    s = sts[-1]
                    s.update(p1=f["p"], m1=f["m"], k1=f["k"], a1=merged[f["m"]]["i0"])
            continue
        c1 = f["m"] - pending["m"] >= 4
        top, bot = (pending, f) if pending["t"] == "T" else (f, pending)
        c2 = top["p"] > bot["p"]
        if c1 and c2:
            sts.append({
                "dir": 1 if f["t"] == "T" else -1,
                "p0": pending["p"], "p1": f["p"],
                "m0": pending["m"], "m1": f["m"],
                "k0": pending["k"], "k1": f["k"],
                "a0": merged[pending["m"]]["i0"], "a1": merged[f["m"]]["i0"],
                "confirmed": True,
            })
            pending = f
        # else: 丢弃 f（唯一失败处理）
    return sts, _potential(pending, merged, sts)


def _potential(pending, merged, sts):
    """pending 之后尚未成笔的反向走势 → 潜在末笔（confirmed=False）。"""
    if pending is None or pending["m"] + 1 >= len(merged):
        return None
    tail = merged[pending["m"] + 1:]
    if pending["t"] == "T":
        ext = min(tail, key=lambda m: m["l"])
        return {"dir": -1, "p0": pending["p"], "p1": ext["l"], "m0": pending["m"],
                "m1": pending["m"] + 1 + tail.index(ext), "k0": pending["k"],
                "k1": ext["lo"], "a0": merged[pending["m"]]["i0"],
                "a1": ext["i1"] + 1, "confirmed": False}
    ext = max(tail, key=lambda m: m["h"])
    return {"dir": 1, "p0": pending["p"], "p1": ext["h"], "m0": pending["m"],
            "m1": pending["m"] + 1 + tail.index(ext), "k0": pending["k"],
            "k1": ext["hi"], "a0": merged[pending["m"]]["i0"],
            "a1": ext["i1"] + 1, "confirmed": False}


# ---------- 2.4 笔中枢 ----------

def _hi(s):
    return max(s["p0"], s["p1"])


def _lo(s):
    return min(s["p0"], s["p1"])


def pivots(sts):
    """返回中枢列表：{zg, zd, gg, dd, s0, s1, n, open}。s0/s1 为构成笔索引（闭区间）。"""
    out, i = [], 0
    while i + 2 < len(sts):
        three = sts[i:i + 3]
        zg = min(_hi(s) for s in three)
        zd = max(_lo(s) for s in three)
        if zg > zd:
            j = i + 3
            while j < len(sts) and _lo(sts[j]) <= zg and _hi(sts[j]) >= zd:
                j += 1  # 延伸：ZG/ZD 不变
            seg = sts[i:j]
            out.append({"zg": zg, "zd": zd,
                        "gg": max(_hi(s) for s in seg), "dd": min(_lo(s) for s in seg),
                        "s0": i, "s1": j - 1, "n": j - i, "open": j >= len(sts)})
            i = j + 1  # 离开笔为 j，新中枢从离开笔的下一笔重扫
        else:
            i += 1
    return out


# ---------- 2.5 背驰 ----------

def _momentum(s, dif, hist):
    """笔的 MACD 动能：(同号柱面积, DIF 同向动能，截断到0)。区间 [a0, a1)。"""
    idxs = range(s["a0"], min(s["a1"], len(hist)))
    if not idxs:
        return 0.0, 0.0
    if s["dir"] == -1:
        area = sum(-hist[i] for i in idxs if hist[i] < 0)
        dx = max(0.0, -min(dif[i] for i in idxs if math.isfinite(dif[i])))
    else:
        area = sum(hist[i] for i in idxs if hist[i] > 0)
        dx = max(0.0, max(dif[i] for i in idxs if math.isfinite(dif[i])))
    # 确保返回值不是Infinity
    if not math.isfinite(area):
        area = 0.0
    if not math.isfinite(dx):
        dx = 0.0
    return area, dx


def divergences(sts, pvs, dif, hist):
    """对每根确认笔逐一评估背驰，返回全部成立者（时序）。
    {dir: 1(底背驰,看多)|-1(顶背驰,看空), strength: '趋势'|'盘整', b: 笔索引,
     k: 原始K索引, negated: bool, area_a/area_b/difx_a/difx_b}"""
    out = []
    for b in range(2, len(sts)):
        B, A = sts[b], sts[b - 2]
        d = B["dir"]
        if A["dir"] != d:
            continue
        if d == -1 and not B["p1"] < A["p1"]:
            continue
        if d == 1 and not B["p1"] > A["p1"]:
            continue
        area_a, dx_a = _momentum(A, dif, hist)
        area_b, dx_b = _momentum(B, dif, hist)
        if not (area_b < area_a and dx_b < dx_a):
            continue
        strength = "盘整"
        before = [p for p in pvs if p["s1"] < b]
        if len(before) >= 2:
            p1, p2 = before[-2], before[-1]
            if d == -1 and p2["zg"] < p1["zd"] and B["p1"] < p2["zd"]:
                strength = "趋势"
            elif d == 1 and p2["zd"] > p1["zg"] and B["p1"] > p2["zg"]:
                strength = "趋势"
        negated = any(s["dir"] == d and
                      (s["p1"] < B["p1"] if d == -1 else s["p1"] > B["p1"])
                      for s in sts[b + 1:])
        out.append({"dir": -d, "strength": strength, "b": b, "k": B["k1"],
                    "negated": negated,
                    "area_a": area_a, "area_b": area_b,
                    "difx_a": dx_a, "difx_b": dx_b})
    return out


# ---------- 2.6 三类买卖点 ----------

_PRIO = {"一买": 3, "一卖": 3, "三买": 3, "三卖": 3,
         "二买": 2, "二卖": 2, "类一买": 1, "类一卖": 1}


def buysell_points(sts, pvs, divs):
    """返回买卖点列表（按时间序）：{t, dir: 1|-1, price, k, b}。
    同一K上多类点重合时只保留强度最高者。"""
    pts = []
    for dv in divs:
        b = dv["b"]
        B = sts[b]
        before = [p for p in pvs if p["s1"] < b]
        if not before:
            continue
        pv = before[-1]
        if B["dir"] == -1 and B["p1"] < pv["zd"]:
            t = "一买" if dv["strength"] == "趋势" else "类一买"
            pts.append({"t": t, "dir": 1, "price": B["p1"], "k": B["k1"], "b": b})
            if b + 2 < len(sts) and sts[b + 2]["p1"] > B["p1"]:
                P = sts[b + 2]
                pts.append({"t": "二买", "dir": 1, "price": P["p1"], "k": P["k1"], "b": b + 2})
        elif B["dir"] == 1 and B["p1"] > pv["zg"]:
            t = "一卖" if dv["strength"] == "趋势" else "类一卖"
            pts.append({"t": t, "dir": -1, "price": B["p1"], "k": B["k1"], "b": b})
            if b + 2 < len(sts) and sts[b + 2]["p1"] < B["p1"]:
                P = sts[b + 2]
                pts.append({"t": "二卖", "dir": -1, "price": P["p1"], "k": P["k1"], "b": b + 2})
    for pv in pvs:
        # 向上离开时，离开笔因穿越中枢必与其重叠而成为延伸笔（pv.s1），
        # 首个与中枢无交集的终结笔即回抽笔 P：整体高于 ZG 的向下笔 → 三买（对称得三卖）。
        j = pv["s1"] + 1
        if j >= len(sts):
            continue
        P = sts[j]
        if P["dir"] == -1 and _lo(P) > pv["zg"]:
            pts.append({"t": "三买", "dir": 1, "price": P["p1"], "k": P["k1"], "b": j})
        elif P["dir"] == 1 and _hi(P) < pv["zd"]:
            pts.append({"t": "三卖", "dir": -1, "price": P["p1"], "k": P["k1"], "b": j})
    best = {}
    for p in pts:
        cur = best.get(p["k"])
        if cur is None or _PRIO[p["t"]] > _PRIO[cur["t"]]:
            best[p["k"]] = p
    return sorted(best.values(), key=lambda p: p["k"])


# ---------- 顶层入口 ----------

def analyze(klines, dif, hist):
    """K线 + MACD 序列 → 缠论结构字典（全部可 JSON 序列化）。"""
    merged = merge_klines(klines)
    fr = fractals(merged)
    sts, potential = strokes(fr, merged)
    pvs = pivots(sts)
    divs = divergences(sts, pvs, dif, hist)
    pts = buysell_points(sts, pvs, divs)
    return {"merged_count": len(merged), "fractal_count": len(fr),
            "strokes": sts, "potential": potential,
            "pivots": pvs, "divergences": divs, "points": pts}


def brief(chan, klines):
    """analyze() 输出 → 报告友好的摘要字典（含日期与 age）。"""
    n = len(klines)
    b = {"stroke_count": len(chan["strokes"]), "pivot_count": len(chan["pivots"])}
    last = chan["potential"] or (chan["strokes"][-1] if chan["strokes"] else None)
    if last:
        b["stroke"] = {"dir": "向上" if last["dir"] == 1 else "向下",
                       "confirmed": last["confirmed"]}
    if chan["pivots"]:
        pv = chan["pivots"][-1]
        b["pivot"] = {"zd": round(pv["zd"], 2), "zg": round(pv["zg"], 2),
                      "n": pv["n"], "open": pv["open"]}
    dv = next((d for d in reversed(chan["divergences"]) if not d["negated"]), None)
    if dv:
        b["divergence"] = {"kind": "底背驰" if dv["dir"] == 1 else "顶背驰",
                           "strength": dv["strength"], "age": n - 1 - dv["k"],
                           "date": klines[dv["k"]]["date"],
                           "area_b": round(dv["area_b"], 3), "area_a": round(dv["area_a"], 3)}
    if chan["points"]:
        pt = chan["points"][-1]
        b["point"] = {"type": pt["t"], "price": round(pt["price"], 2),
                      "age": n - 1 - pt["k"], "date": klines[pt["k"]]["date"]}
    return b
