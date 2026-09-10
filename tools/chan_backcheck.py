"""缠论买卖点事后事件检验（一次性验证/校准工具，不进入主流程与自动测试）。
用法：python -X utf8 tools/chan_backcheck.py 000938 300738 002240

对每只个股：识别全部缠论买卖点，统计其后 5/10 个交易日收益，与随机入场基准
（全体交易日 5/10 日前瞻收益均值）对比。
方法学声明：收益从"该点可被算法确认的那根K"（确认K≈端点分型右侧合并K完成日）起算，
避免前视偏差；同时给出自点位K起算的对照值（偏乐观）。样本小，仅做合理性检验，
不构成统计显著性主张。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stocklib import chanlun, datasource, indicators as ind

BUY = {"一买", "二买", "三买", "类一买"}


def fwd(closes, i, n):
    if i is None or i + n >= len(closes):
        return None
    return (closes[i + n] / closes[i] - 1) * 100


def run_stock(query):
    market, code, name = datasource.resolve(query)
    name = name or f"{market}{code}"
    klines, src = datasource.get_kline(market, code, count=320)
    klines = klines[-250:]
    closes = [k["close"] for k in klines]
    dif, _, hist = ind.macd(closes)
    merged = chanlun.merge_klines(klines)
    fr = chanlun.fractals(merged)
    sts, _pot = chanlun.strokes(fr, merged)
    pvs = chanlun.pivots(sts)
    divs = chanlun.divergences(sts, pvs, dif, hist)
    pts = chanlun.buysell_points(sts, pvs, divs)

    base5 = [fwd(closes, i, 5) for i in range(len(closes) - 5)]
    base10 = [fwd(closes, i, 10) for i in range(len(closes) - 10)]
    b5 = sum(base5) / len(base5)
    b10 = sum(base10) / len(base10)

    print(f"\n=== {name}（{market}{code}）  K线{len(klines)}根 来源:{src} ===")
    print(f"  结构：合并K {len(merged)} / 分型 {len(fr)} / 确认笔 {len(sts)} / 中枢 {len(pvs)}"
          f" / 背驰 {len(divs)} / 买卖点 {len(pts)}")
    print(f"  随机入场基准：5日 {b5:+.2f}%   10日 {b10:+.2f}%")
    if not pts:
        print("  无买卖点，跳过")
        return None
    rows = []
    for p in pts:
        st = sts[p["b"]]
        m1 = st["m1"]
        conf = merged[m1 + 1]["i1"] if m1 + 1 < len(merged) else None
        sign = 1 if p["t"] in BUY else -1  # 卖点收益取反（做空视角/避险有效性）
        r5c, r10c = fwd(closes, conf, 5), fwd(closes, conf, 10)
        r5o = fwd(closes, p["k"], 5)
        rows.append((p, conf, sign, r5c, r10c, r5o))
        cf = klines[conf]["date"] if conf is not None else "未确认"
        print(f"  {p['t']:<4} @{p['price']:>8.2f}  点位日 {klines[p['k']]['date']}"
              f"  确认日 {cf}"
              f"  确认后5日 {'—' if r5c is None else f'{r5c:+.2f}%'}"
              f"  10日 {'—' if r10c is None else f'{r10c:+.2f}%'}"
              f"  （自点位日5日 {'—' if r5o is None else f'{r5o:+.2f}%'}）")
    eff5 = [sign * r for _, _, sign, r, _, _ in rows if r is not None]
    eff10 = [sign * r for _, _, sign, _, r, _ in rows if r is not None]
    if eff5:
        print(f"  方向修正后均值：5日 {sum(eff5) / len(eff5):+.2f}%（基准 {b5:+.2f}%，n={len(eff5)}）")
    if eff10:
        print(f"                  10日 {sum(eff10) / len(eff10):+.2f}%（基准 {b10:+.2f}%，n={len(eff10)}）")
    return {"name": name, "n_points": len(pts),
            "eff5": sum(eff5) / len(eff5) if eff5 else None,
            "eff10": sum(eff10) / len(eff10) if eff10 else None,
            "base5": b5, "base10": b10}


def main(argv):
    targets = argv or ["000938", "300738", "002240"]
    results = []
    for q in targets:
        try:
            r = run_stock(q)
            if r:
                results.append(r)
        except Exception as e:  # noqa: BLE001 —— 校验工具，逐股容错继续
            print(f"[跳过] {q}: {e}", file=sys.stderr)
    if results:
        print("\n=== 汇总（买卖点方向修正后 vs 随机基准） ===")
        for r in results:
            e5 = "—" if r["eff5"] is None else f"{r['eff5']:+.2f}%"
            e10 = "—" if r["eff10"] is None else f"{r['eff10']:+.2f}%"
            print(f"  {r['name']:<6} 点数{r['n_points']:>2}  5日 {e5} vs {r['base5']:+.2f}%"
                  f"   10日 {e10} vs {r['base10']:+.2f}%")
        print("  （样本小，仅合理性检验；确认日起算已避免前视偏差）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
