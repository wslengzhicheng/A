"""离线自检（零网络）：指标断言 + 预测不变量 + 评分边界 + fixtures 端到端回放。
运行：stock.bat --test 或 python -X utf8 test_offline.py
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stocklib import analyzer, chanlun, indicators as ind, predictor, report

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


def test_indicators():
    print("[1] 指标手工验算断言")
    seq10 = [float(i) for i in range(1, 11)]
    m = ind.ma(seq10, 5)
    check("MA5([1..10])[4] == 3.0", approx(m[4], 3.0), f"got {m[4]}")
    check("MA5([1..10])[9] == 8.0", approx(m[9], 8.0), f"got {m[9]}")
    check("MA5 数据不足段为 None", m[3] is None)

    e = ind.ema([1.0, 2.0, 3.0], 3)  # alpha=0.5 → [1, 1.5, 2.25]
    check("EMA3([1,2,3]) == [1,1.5,2.25]",
          approx(e[0], 1.0) and approx(e[1], 1.5) and approx(e[2], 2.25), f"got {e}")

    const = [5.0] * 40
    dif, dea, hist = ind.macd(const)
    check("常数序列 MACD 全为 0",
          approx(dif[-1], 0) and approx(dea[-1], 0) and approx(hist[-1], 0))

    up = [float(i) for i in range(1, 31)]
    r = ind.rsi(up, 6)
    check("单调上升 RSI6 == 100", approx(r[-1], 100.0), f"got {r[-1]}")
    down = [float(31 - i) for i in range(1, 31)]
    r2 = ind.rsi(down, 6)
    check("单调下降 RSI6 == 0", approx(r2[-1], 0.0), f"got {r2[-1]}")

    k, d, j = ind.kdj(const, const, const)
    check("常数序列 KDJ 收敛于 50",
          approx(k[-1], 50.0) and approx(d[-1], 50.0) and approx(j[-1], 50.0),
          f"got K={k[-1]} D={d[-1]} J={j[-1]}")
    highs = [v + 0.5 for v in up]
    lows = [v - 0.5 for v in up]
    k2, _, _ = ind.kdj(highs, lows, up)
    check("单调上升 KDJ 的 K > 80", k2[-1] is not None and k2[-1] > 80, f"got {k2[-1]}")

    seq20 = [float(i) for i in range(1, 21)]
    mid, upper, lower = ind.boll(seq20)
    sd = math.sqrt(sum((x - 10.5) ** 2 for x in seq20) / 19)
    check("BOLL20([1..20]) 中轨 == 10.5", approx(mid[-1], 10.5), f"got {mid[-1]}")
    check("BOLL20([1..20]) 上轨 == 10.5+2σ(ddof=1)", approx(upper[-1], 10.5 + 2 * sd),
          f"got {upper[-1]} expect {10.5 + 2 * sd}")

    slope, r2v = ind.linreg([1.0, 3.0, 5.0, 7.0])  # y=2x+1
    check("线性回归 y=2x+1 → 斜率2, R²=1", approx(slope, 2.0) and approx(r2v, 1.0))


def _synth_klines(mode, n=120):
    out, price = [], 100.0
    for i in range(n):
        if mode == "up":
            price *= 1.01
        elif mode == "down":
            price *= 0.99
        vol = 10000 * (1 + 0.002 * i)
        out.append({"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                    "open": price * 0.999, "close": price,
                    "high": price * 1.005, "low": price * 0.995, "volume": vol})
    return out


_SNAP_NEUTRAL = {"code": "000000", "name": "测试股", "price": 100.0, "prev_close": 100.0,
                 "open": 100.0, "change_pct": 0.0, "high": 101.0, "low": 99.0,
                 "volume": 10000.0, "amount": 10000.0, "turnover": 1.0,
                 "pe_ttm": 30.0, "pb": 3.0, "mktcap_total": 100.0, "mktcap_float": 80.0,
                 "high_52w": None, "low_52w": None, "time": "", "is_trading": True}


def test_prediction_invariants():
    print("[2] 预测与评分不变量")
    for mode, expect in (("up", "上行"), ("down", "下行"), ("flat", "震荡")):
        klines = _synth_klines(mode)
        snap = dict(_SNAP_NEUTRAL, price=klines[-1]["close"])
        x = analyzer.compute_indicators(klines)
        signals, _ = analyzer.build_signals(x, snap, None)
        pred = predictor.outlook(signals, x["closes"])
        check(f"单调{mode}序列 → 展望『{expect}』", pred["direction"] == expect,
              f"got {pred['direction']} (共振{pred['resonance']})")
    up_k = _synth_klines("up")
    x = analyzer.compute_indicators(up_k)
    signals, _ = analyzer.build_signals(x, dict(_SNAP_NEUTRAL, price=up_k[-1]["close"]), None)
    total, grade, _ = analyzer.score(signals)
    down_k = _synth_klines("down")
    x2 = analyzer.compute_indicators(down_k)
    signals2, _ = analyzer.build_signals(x2, dict(_SNAP_NEUTRAL, price=down_k[-1]["close"]), None)
    total2, grade2, _ = analyzer.score(signals2)
    check(f"上升趋势评分({total}) > 下降趋势评分({total2})", total > total2)


def test_score_boundaries():
    print("[3] 评分公式边界")
    full_bull = [{"category": c, "name": "t", "score": 2, "text": ""}
                 for c in ("trend", "osc", "volume", "valuation") for _ in range(3)]
    total, grade, _ = analyzer.score(full_bull)
    check(f"全+2信号 → 评分 {total} ≥ 80 且结论强势看多", total >= 80 and grade == "强势看多")
    full_bear = [dict(s, score=-2) for s in full_bull]
    total2, grade2, _ = analyzer.score(full_bear)
    check(f"全-2信号 → 评分 {total2} ≤ 20 且结论弱势看空", total2 <= 20 and grade2 == "弱势看空")
    total3, grade3, _ = analyzer.score([])
    check("无信号 → 50分中性", total3 == 50.0 and grade3 == "中性震荡")
    # 空类别权重重分配：只有 trend 一个类别全+2 → 仍应为满分而非被稀释
    only_trend = [{"category": "trend", "name": "t", "score": 2, "text": ""}] * 3
    total4, _, _ = analyzer.score(only_trend)
    check("仅趋势类全+2 → 权重重分配后为100", approx(total4, 100.0), f"got {total4}")

    # v1.3：类内加权 — MACD 权重大于年度位置
    from stocklib.analyzer import _category_norm, SCORE_VERSION
    check(f"score_version 为 1.3（got {SCORE_VERSION}）", SCORE_VERSION == "1.3")
    weighted = [
        {"category": "trend", "name": "MACD", "score": 2, "text": ""},
        {"category": "trend", "name": "年度位置", "score": -2, "text": ""},
    ]
    equal = [
        {"category": "trend", "name": "t", "score": 2, "text": ""},
        {"category": "trend", "name": "t", "score": -2, "text": ""},
    ]
    check("类内加权使强信号不完全被弱信号等权稀释",
          _category_norm(weighted) > _category_norm(equal) + 0.1)

    # 趋势/缠论冲突时总分更靠近趋势（chan 降权）
    conflict = (
        [{"category": "trend", "name": "MACD", "score": 2, "text": ""}] * 3
        + [{"category": "chan", "name": "买卖点", "score": -2, "text": ""}] * 3
    )
    total_c, _, detail_c = analyzer.score(conflict)
    check("趋势/缠论冲突后总分仍偏多（chan 已降权）", total_c > 55, f"got {total_c}")
    check("冲突明细保留异号",
          detail_c.get("trend", 0) > 0.5 and detail_c.get("chan", 0) < -0.5)

    # 估值：成长画像放宽 PE；财务含营收/毛利率信号
    fin = {"roe": 18.0, "profit_yoy": 25.0, "revenue_yoy": 22.0, "gross_margin": 45.0,
           "report_date": "2025-12-31"}
    pe_g, text_g = analyzer._pe_signal(55.0, fin)
    pe_v, _ = analyzer._pe_signal(55.0, None)
    check("成长画像下 PE=55 不判偏高", pe_g == 0 and "成长" in text_g)
    check("非成长画像下 PE=55 判偏高", pe_v == -1)

    # v1.3：行业 PE 分位优先
    peers = list(range(10, 50))  # 40 个同业
    ctx_cheap = {"industry": "测试业", "peer_count": len(peers), "peer_pes": peers,
                 "pe_percentile": 0.15}
    pe_i, text_i = analyzer._pe_signal(12.0, None, ctx_cheap)
    check("行业分位偏低 → +1", pe_i == 1 and "分位" in text_i)
    ctx_rich = dict(ctx_cheap, pe_percentile=0.85)
    pe_r, text_r = analyzer._pe_signal(40.0, None, ctx_rich)
    check("行业分位偏高 → -1", pe_r == -1 and "偏高" in text_r)

    # 构造最短可用指标集测财务信号名（用上升序列保证 RSI 有值）
    klines = _synth_klines("up")
    x = analyzer.compute_indicators(klines)
    snap = dict(_SNAP_NEUTRAL, price=klines[-1]["close"], pe_ttm=22.0, pb=2.0)
    sigs, _ = analyzer.build_signals(x, snap, fin, ctx_cheap)
    names = {s["name"] for s in sigs if s["category"] == "valuation"}
    check("估值类含营收增速与毛利率", {"营收增速", "毛利率"}.issubset(names), f"got {names}")
    rsi_names = [s for s in sigs if s["name"] == "RSI"]
    check("RSI 文案含 RSI12", bool(rsi_names) and "RSI12" in rsi_names[0]["text"],
          f"got {rsi_names[0]['text'] if rsi_names else None}")
    rsi24 = [s for s in sigs if s["name"] == "RSI24"]
    check("产出独立 RSI24 信号", bool(rsi24) and "RSI24=" in rsi24[0]["text"])
    pe_sig = [s for s in sigs if s["name"] == "市盈率"]
    check("市盈率使用行业分位文案", bool(pe_sig) and "分位" in pe_sig[0]["text"] and "无行业分位" not in pe_sig[0]["text"])


def _k(h, l, i=0):
    """构造单根K（仅缠论关心 high/low，其余字段占位）。"""
    return {"date": f"2026-01-{(i % 28) + 1:02d}", "open": (h + l) / 2, "close": (h + l) / 2,
            "high": float(h), "low": float(l), "volume": 10000.0}


def _zig(points):
    """转折价序列 → 逐单位步进的锯齿K线（high=p+0.5, low=p-0.5，相邻K必不包含）。"""
    prices = [float(points[0])]
    for a, b in zip(points, points[1:]):
        step = 1.0 if b > a else -1.0
        p = a
        while abs(p - b) > 1e-9:
            p += step
            prices.append(p)
    return [_k(p + 0.5, p - 0.5, i) for i, p in enumerate(prices)]


def test_chanlun_morphology():
    print("[5] 缠论形态学（包含处理/分型/笔/中枢）")
    # 包含处理：K2 被 K1 包含（上升处理取高高），K4 包含 K3（上升处理）
    ks = [_k(10, 8), _k(11, 9), _k(10.5, 9.5), _k(12, 10), _k(13, 9)]
    m = chanlun.merge_klines(ks)
    check("包含处理后剩 3 根", len(m) == 3, f"got {len(m)}")
    check("上升包含合并高低点取大 (11, 9.5)", approx(m[1]["h"], 11) and approx(m[1]["l"], 9.5),
          f"got {m[1]['h']},{m[1]['l']}")
    check("上升包含(K4⊃K3)取大 (13, 10)", approx(m[2]["h"], 13) and approx(m[2]["l"], 10),
          f"got {m[2]['h']},{m[2]['l']}")
    check("合并K span 记录正确", m[1]["i0"] == 1 and m[1]["i1"] == 2 and m[2]["i1"] == 4)
    # 头部包含：K1 包含 K0 → 保留包含者，span 并集
    ks2 = [_k(10, 8), _k(11, 7), _k(12, 8)]
    m2 = chanlun.merge_klines(ks2)
    check("头部包含保留包含者 (11,7)", len(m2) == 2 and approx(m2[0]["h"], 11) and approx(m2[0]["l"], 7),
          f"got {m2}")
    check("头部包含 span 为并集 [0,1]", m2[0]["i0"] == 0 and m2[0]["i1"] == 1)
    # 不变量：相邻合并K互不包含（用随机性较强的构造序列全程验证）
    ks3 = _zig([100, 106, 101, 107, 96]) + [_k(97, 93), _k(98, 92), _k(99, 95)]
    m3 = chanlun.merge_klines(ks3)
    ok = all(not (a["h"] >= b["h"] and a["l"] <= b["l"]) and not (b["h"] >= a["h"] and b["l"] <= a["l"])
             for a, b in zip(m3, m3[1:]))
    check("合并序列不变量：相邻互不包含", ok)
    # 分型与笔：锯齿 100→106→101→107→96
    kz = _zig([100, 106, 101, 107, 96])
    ch = chanlun.analyze(kz, [0.0] * len(kz), [0.0] * len(kz))
    check("锯齿序列分型数 == 3 (T,B,T)", ch["fractal_count"] == 3, f"got {ch['fractal_count']}")
    sts = ch["strokes"]
    check("确认笔 2 根（下、上）", len(sts) == 2 and sts[0]["dir"] == -1 and sts[1]["dir"] == 1,
          f"got {len(sts)}")
    check("笔端点价正确（106.5→100.5→107.5）",
          approx(sts[0]["p0"], 106.5) and approx(sts[0]["p1"], 100.5) and approx(sts[1]["p1"], 107.5))
    pot = ch["potential"]
    check("潜在末笔向下且未确认", pot is not None and pot["dir"] == -1 and not pot["confirmed"])
    check("潜在末笔终点为末端极值 95.5", approx(pot["p1"], 95.5), f"got {pot and pot['p1']}")
    # C1 失败（分型间隔<4根合并K）不成笔：小锯齿 100→103→101→104（各段仅3步）
    kz2 = _zig([100, 103, 101, 104, 102])
    ch2 = chanlun.analyze(kz2, [0.0] * len(kz2), [0.0] * len(kz2))
    check("间距不足不成笔（确认笔=0）", len(ch2["strokes"]) == 0, f"got {len(ch2['strokes'])}")
    # 同型更极端分型延伸上一笔：100→106→101→107→96→108(更高顶不隔够) 场景改为验证顶延伸：
    kz3 = _zig([100, 110, 104, 112, 103])
    ch3 = chanlun.analyze(kz3, [0.0] * len(kz3), [0.0] * len(kz3))
    check("锯齿(110,104,112)成 2 笔", len(ch3["strokes"]) == 2)
    # 中枢：100→110→104→112→103→111（3笔重叠）
    kz4 = _zig([100, 110, 104, 112, 103, 111])
    ch4 = chanlun.analyze(kz4, [0.0] * len(kz4), [0.0] * len(kz4))
    pv = ch4["pivots"]
    check("三笔重叠成中枢", len(pv) == 1, f"got {len(pv)}")
    if pv:
        check("中枢 ZG/ZD == 110.5/103.5", approx(pv[0]["zg"], 110.5) and approx(pv[0]["zd"], 103.5),
              f"got {pv[0]['zg']},{pv[0]['zd']}")
        check("中枢未终结 open=True", pv[0]["open"])
    # 中枢延伸不改 ZG/ZD + 终结 + 三买：100→110→104→112→103→111→105→130→124→131
    kz5 = _zig([100, 110, 104, 112, 103, 111, 105, 130, 124, 131])
    ch5 = chanlun.analyze(kz5, [0.0] * len(kz5), [0.0] * len(kz5))
    pv5 = ch5["pivots"]
    check("延伸后仍 1 个中枢且 ZG/ZD 不变",
          len(pv5) == 1 and approx(pv5[0]["zg"], 110.5) and approx(pv5[0]["zd"], 103.5),
          f"got {pv5}")
    check("中枢延伸至 6 笔并终结", pv5[0]["n"] == 6 and not pv5[0]["open"],
          f"got n={pv5[0]['n']} open={pv5[0]['open']}")
    p3 = [p for p in ch5["points"] if p["t"] == "三买"]
    check("回抽不破 ZG → 三买 @123.5", len(p3) == 1 and approx(p3[0]["price"], 123.5),
          f"got {p3}")
    # 无重叠三笔无中枢：单边下行锯齿
    kz6 = _zig([100, 90, 95, 80, 85, 70])
    ch6 = chanlun.analyze(kz6, [0.0] * len(kz6), [0.0] * len(kz6))
    check("无重叠不成中枢", len(ch6["pivots"]) == 0, f"got {len(ch6['pivots'])}")
    # 确定性：同一输入两次结果一致
    ch5b = chanlun.analyze(kz5, [0.0] * len(kz5), [0.0] * len(kz5))
    check("确定性：两次计算逐字段一致", ch5 == ch5b)


def _trend_div_klines():
    """两个依次向下的中枢 + 末段下跌 → 趋势背驰场景。
    转折价：100,110,104,112,103,111,70,78,69,79,68,76,60,65,55,58"""
    return _zig([100, 110, 104, 112, 103, 111, 70, 78, 69, 79, 68, 76, 60, 65, 55, 58])


def _mk_macd(klines, strokes_hint):
    """按笔的原始K区间构造 dif/hist：默认 0，指定 {笔索引: 值} 段内恒为该值。"""
    n = len(klines)
    dif, hist = [0.0] * n, [0.0] * n
    for (a0, a1), v in strokes_hint:
        for i in range(a0, min(a1, n)):
            dif[i] = v
            hist[i] = v
    return dif, hist


def test_chanlun_dynamics():
    print("[6] 缠论动力学（背驰/买卖点/时效）")
    kz = _trend_div_klines()
    zeros = [0.0] * len(kz)
    base = chanlun.analyze(kz, zeros, zeros)
    sts = base["strokes"]
    check("趋势场景确认笔 == 13", len(sts) == 13, f"got {len(sts)}")
    pvs = base["pivots"]
    check("形成 2 个依次向下中枢", len(pvs) == 2 and pvs[1]["zg"] < pvs[0]["zd"],
          f"got {[(p['zd'], p['zg']) for p in pvs]}")
    # 构造 MACD：A=笔10(76→60) 强动能，B=笔12(65→55) 弱动能 → 趋势底背驰
    dif, hist = _mk_macd(kz, [((sts[10]["a0"], sts[10]["a1"]), -1.0),
                              ((sts[12]["a0"], sts[12]["a1"]), -0.1)])
    ch = chanlun.analyze(kz, dif, hist)
    divs = [d for d in ch["divergences"] if d["b"] == 12]
    check("笔12 触发底背驰", len(divs) == 1 and divs[0]["dir"] == 1, f"got {ch['divergences']}")
    check("背驰强度为趋势（两个同向中枢）", divs and divs[0]["strength"] == "趋势",
          f"got {divs and divs[0]['strength']}")
    check("背驰未被否定", divs and not divs[0]["negated"])
    p1 = [p for p in ch["points"] if p["t"] == "一买"]
    check("趋势背驰跌破中枢 → 一买 @54.5", len(p1) == 1 and approx(p1[0]["price"], 54.5),
          f"got {ch['points']}")
    # 动能反向（B 更强）→ 无背驰
    dif2, hist2 = _mk_macd(kz, [((sts[10]["a0"], sts[10]["a1"]), -0.1),
                                ((sts[12]["a0"], sts[12]["a1"]), -1.0)])
    ch2 = chanlun.analyze(kz, dif2, hist2)
    check("价格新低但动能更强 → 无背驰", not any(d["b"] == 12 for d in ch2["divergences"]))
    # 单中枢 → 类一买（截取到第一个中枢结束后的下跌）
    kz3 = _zig([100, 110, 104, 112, 103, 111, 70, 78, 69, 74])
    z3 = [0.0] * len(kz3)
    b3 = chanlun.analyze(kz3, z3, z3)
    s3 = b3["strokes"]
    dif3, hist3 = _mk_macd(kz3, [((s3[4]["a0"], s3[4]["a1"]), -1.0),
                                 ((s3[6]["a0"], s3[6]["a1"]), -0.1)])
    ch3 = chanlun.analyze(kz3, dif3, hist3)
    pk = [p for p in ch3["points"] if p["b"] == 6]
    check("单中枢盘整背驰 → 类一买（而非一买）", len(pk) == 1 and pk[0]["t"] == "类一买",
          f"got {ch3['points']}")
    # 二买：一买后 上-下 不创新低（主场景加两段）
    kz4 = _zig([100, 110, 104, 112, 103, 111, 70, 78, 69, 79, 68, 76, 60, 65, 55, 62, 57, 60])
    z4len = len(kz4)
    b4 = chanlun.analyze(kz4, [0.0] * z4len, [0.0] * z4len)
    s4 = b4["strokes"]
    dif4, hist4 = _mk_macd(kz4, [((s4[10]["a0"], s4[10]["a1"]), -1.0),
                                 ((s4[12]["a0"], s4[12]["a1"]), -0.1)])
    ch4 = chanlun.analyze(kz4, dif4, hist4)
    p2 = [p for p in ch4["points"] if p["t"] == "二买"]
    check("一买后回落不创新低 → 二买 @56.5", len(p2) == 1 and approx(p2[0]["price"], 56.5),
          f"got {ch4['points']}")
    # 背驰否定：直接构造笔列表（底背驰后又现更低终点的向下确认笔）
    mk = lambda d, p0, p1, a0, a1: {"dir": d, "p0": p0, "p1": p1, "m0": 0, "m1": 0,
                                    "k0": a0, "k1": a1 - 1, "a0": a0, "a1": a1, "confirmed": True}
    hand = [mk(-1, 100, 80, 0, 10), mk(1, 80, 90, 10, 20), mk(-1, 90, 75, 20, 30),
            mk(1, 75, 85, 30, 40), mk(-1, 85, 70, 40, 50)]
    difh = [0.0] * 50
    histh = [0.0] * 50
    for i in range(0, 10):
        difh[i] = histh[i] = -1.0
    for i in range(20, 30):
        difh[i] = histh[i] = -0.1
    for i in range(40, 50):
        difh[i] = histh[i] = -0.5
    divh = chanlun.divergences(hand, [], difh, histh)
    d2 = [d for d in divh if d["b"] == 2]
    check("手工笔列表：笔2 底背驰成立", len(d2) == 1, f"got {divh}")
    check("其后更低终点向下笔 → 背驰被否定", d2 and d2[0]["negated"])
    # 卖点镜像：主场景价格取负对称（上涨趋势顶背驰 → 一卖）
    kzm = _zig([200, 190, 196, 188, 197, 189, 230, 222, 231, 221, 232, 224, 240, 235, 245, 242])
    zm = [0.0] * len(kzm)
    bm = chanlun.analyze(kzm, zm, zm)
    sm = bm["strokes"]
    difm, histm = _mk_macd(kzm, [((sm[10]["a0"], sm[10]["a1"]), 1.0),
                                 ((sm[12]["a0"], sm[12]["a1"]), 0.1)])
    chm = chanlun.analyze(kzm, difm, histm)
    ps = [p for p in chm["points"] if p["t"] == "一卖"]
    check("镜像上涨趋势顶背驰 → 一卖", len(ps) == 1 and ps and ps[0]["dir"] == -1,
          f"got {chm['points']}")
    # 时效：一买 age > 10 → 信号不计分（analyzer 层）
    tail = [dict(kz4[-1]) for _ in range(12)]  # 末尾追加12根平盘K，使买点陈旧
    for i, t in enumerate(tail):
        t["date"] = f"2026-03-{i + 1:02d}"
    kz5 = kz4 + tail
    x5 = analyzer.compute_indicators(kz5)
    sig5, _ = analyzer.build_signals(x5, dict(_SNAP_NEUTRAL, price=kz5[-1]["close"]), None)
    bs5 = [s for s in sig5 if s["category"] == "chan" and s["name"] == "买卖点"]
    check("买卖点 age>10 不产出计分信号", len(bs5) == 0, f"got {bs5}")
    # 整体集成：构造场景下 chan 信号存在且带类别
    x4 = analyzer.compute_indicators(kz4)
    sig4, _ = analyzer.build_signals(x4, dict(_SNAP_NEUTRAL, price=kz4[-1]["close"]), None)
    cats4 = {s["category"] for s in sig4}
    check("结构类信号进入信号列表", "chan" in cats4, f"got {cats4}")
    # 动能递增的上行锯齿 → 无顶背驰、chan 类得分 ≥ 0
    kzu = _zig([100, 108, 103, 112, 106, 118, 111, 126])
    zu = len(kzu)
    bu = chanlun.analyze(kzu, [0.0] * zu, [0.0] * zu)
    su = bu["strokes"]
    ups = [i for i, s in enumerate(su) if s["dir"] == 1]
    hints = [((su[i]["a0"], su[i]["a1"]), 0.5 * (n + 1)) for n, i in enumerate(ups)]
    difu, histu = _mk_macd(kzu, hints)
    chu = chanlun.analyze(kzu, difu, histu)
    check("动能递增上行 → 无顶背驰", not any(d["dir"] == -1 for d in chu["divergences"]),
          f"got {chu['divergences']}")
    # 未确认末笔隔离性：潜在末笔不进入中枢/背驰/买卖点
    check("潜在末笔不计入确认笔/中枢",
          all(not (p["s1"] >= len(bu["strokes"])) for p in bu["pivots"]))


def test_fixture_replay():
    print("[7] fixtures 端到端回放（解析→分析→预测→报告）")
    path = os.path.join(FIXTURE_DIR, "600519.json")
    if not os.path.exists(path):
        check("fixtures/600519.json 存在", False, "先运行一次在线查询并录制 fixtures")
        return
    with open(path, "r", encoding="utf-8") as f:
        fx = json.load(f)
    import stock
    result = stock.build_result("sh", "600519", fx["klines"], fx["snapshot"],
                                fx.get("finance"), 250, "fixtures回放")
    text = report.render_text(result)
    for section in ("行情快照", "指标信号", "综合评分", "总结", "结构分析", "走势预测",
                    "支撑位", "做差价建议", "不构成投资建议"):
        check(f"报告含【{section}】", section in text)
    check("信号数 ≥ 7", len(result["signals"]) >= 7, f"got {len(result['signals'])}")
    check("含 spread 计划", isinstance(result.get("spread"), dict) and "timing" in result["spread"])
    parsed = json.loads(report.render_json(result))
    for key in ("score", "trend", "support", "resistance", "grade", "chan", "score_version", "spread"):
        check(f"JSON 含字段 {key}", key in parsed)


def test_calibration_offline():
    print("[8] 评分历史校准（离线）")
    from stocklib import calibrate
    report = calibrate.run_calibration(live=0, apply=False)
    check("校准产出采样点", report.get("n_points", 0) >= 20, f"got {report.get('n_points')}")
    check("校准含 base_metrics", isinstance(report.get("base_metrics"), dict))
    check("校准建议权重或可为空网格", "suggested_WEIGHTS" in report)


def test_buy_opp_backtest_offline():
    print("[9] 买入机会判定 + 回测状态机（离线）")
    from stocklib.stock_picker import evaluate_opportunity, EXIT_SCORE
    from stocklib import backtest

    ok = evaluate_opportunity(70, change_pct=0.02, main_net=2000, has_buy_point=True)
    check("有买点+资金流 → passed", ok["passed"])
    no_flow = evaluate_opportunity(70, change_pct=0.02, main_net=None, has_buy_point=True,
                                  require_main_net=True)
    check("缺资金流且要求净流入 → 不通过", not no_flow["passed"])
    bt_mode = evaluate_opportunity(70, change_pct=0.02, main_net=None, has_buy_point=True,
                                 require_main_net=False)
    check("回测模式不要求净流入 → passed", bt_mode["passed"])
    chase = evaluate_opportunity(70, change_pct=0.09, main_net=5000, has_buy_point=True,
                               require_main_net=False)
    check("涨幅过大 → 不通过", not chase["passed"])
    check("EXIT_SCORE 为 50", EXIT_SCORE == 50)

    # 止损按日触发：持仓后次日大跌，无需新信号
    klines = []
    price = 100.0
    for i in range(30):
        if i == 11:
            price = 94.0  # -6% from 100
        klines.append({
            "date": f"2025-01-{i + 1:02d}",
            "open": price, "close": price, "high": price, "low": price, "volume": 1e6,
        })
    signals = [{"type": "buy", "date": klines[10]["date"], "price": 100.0,
                "reason": "测试买", "k_index": 10}]
    res = backtest.backtest_signals(klines, signals, hold_days=20, stop_loss_pct=0.05)
    check("无卖信号时止损仍触发", len(res["trades"]) == 1, f"got {len(res['trades'])}")
    if res["trades"]:
        check("止损理由含止损", "止损" in res["trades"][0]["reason"])
        check("止损约 -6%", res["trades"][0]["pnl_pct"] < -5)

    check("STRATEGIES 含 buy_opp", "buy_opp" in backtest.STRATEGIES)
    strat = backtest.BuyOpportunityStrategy(step=10, min_bars=60)
    k_up = _synth_klines("up", n=120)
    sigs = strat.generate_signals(k_up, None)
    check("buy_opp 信号带 k_index", all("k_index" in s for s in sigs) if sigs else True)
    check("buy_opp 可生成列表", isinstance(sigs, list))


def test_spread_offline():
    print("[10] 单票做差价建议（离线）")
    from stocklib import spread, analyzer, predictor

    klines = _synth_klines("up", n=80)
    for k in klines:
        k["high"] = k["close"] * 1.025
        k["low"] = k["close"] * 0.975
    x = analyzer.compute_indicators(klines)
    levels = predictor.key_levels(klines, x, lookback_days=20)
    snap = dict(_SNAP_NEUTRAL, price=klines[-1]["close"], high=klines[-1]["high"],
                low=klines[-1]["low"], change_pct=0.01, amount=20000, turnover=1.2,
                code="600000", name="测试股", is_trading=True)
    sigs, _ = analyzer.build_signals(x, snap, None)
    score, _grade, _ = analyzer.score(sigs)
    pred = predictor.outlook(sigs, x["closes"])
    plan = spread.build_spread_plan(klines, snap, x, levels, score, pred, sigs)
    check("产出偏置", plan.get("bias") in ("偏多", "偏空", "震荡"))
    check("低吸带有序", plan["buy_zone"]["low"] <= plan["buy_zone"]["high"])
    check("高抛带有序", plan["sell_zone"]["low"] <= plan["sell_zone"]["high"])
    check("时机合法", plan["timing"]["action"] in ("观望", "低吸窗口", "高抛窗口", "停做"))

    snap_hi = dict(snap, price=plan["sell_zone"]["high"], change_pct=0.01)
    hi = spread.build_spread_plan(klines, snap_hi, x, levels, score, pred, sigs)
    check("价在高抛带 → 高抛窗口", hi["timing"]["action"] == "高抛窗口", hi["timing"]["action"])

    snap_lo = dict(snap, price=plan["buy_zone"]["low"], change_pct=-0.01)
    lo = spread.build_spread_plan(klines, snap_lo, x, levels, score, pred, sigs)
    check("价在低吸带 → 低吸窗口", lo["timing"]["action"] == "低吸窗口", lo["timing"]["action"])

    snap_lim = dict(snap, change_pct=0.098)
    lim = spread.build_spread_plan(klines, snap_lim, x, levels, score, pred, sigs)
    check("接近涨停 → 停做", lim["timing"]["action"] == "停做", lim["timing"]["action"])


def run():
    for fn in (test_indicators, test_prediction_invariants,
               test_score_boundaries, test_chanlun_morphology,
               test_chanlun_dynamics, test_fixture_replay,
               test_calibration_offline, test_buy_opp_backtest_offline,
               test_spread_offline):
        fn()
        print()
    if _failures:
        print(f"结果：{len(_failures)} 项失败：{_failures}")
        return 1
    print("结果：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(run())
