"""终端格式化报告与 JSON 输出。"""
import json

from .analyzer import CATEGORY_NAMES, SCORE_VERSION

DISCLAIMER = "⚠️  本分析基于历史行情与公开数据的技术指标计算，仅供参考，不构成投资建议，股市有风险，入市需谨慎。"

_LINE = "─" * 62


def _fmt(v, suffix="", nd=2):
    if v is None:
        return "—"
    return f"{v:.{nd}f}{suffix}"


def render_text(result):
    """result 为 build_result() 产出的字典。"""
    snap = result["snapshot"]
    out = []
    out.append(_LINE)
    out.append(f"  {snap['name']}（{result['market']}{snap['code']}）个股分析报告")
    out.append(f"  数据截止：{result['data_asof']}（{result['session_state']}）"
               f"  来源：{result['sources']}")
    out.append(_LINE)
    chg = snap.get("change_pct")
    arrow = "" if chg is None else ("↑" if chg >= 0 else "↓")
    out.append("【行情快照】")
    out.append(f"  现价 {_fmt(snap['price'])} 元  {arrow}{_fmt(chg, '%')}   "
               f"今开 {_fmt(snap.get('open'))}  最高 {_fmt(snap.get('high'))}  最低 {_fmt(snap.get('low'))}")
    out.append(f"  成交量 {_fmt(snap.get('volume'), ' 手', 0)}   成交额 {_fmt(snap.get('amount'), ' 万元', 0)}   "
               f"换手率 {_fmt(snap.get('turnover'), '%')}")
    out.append(f"  PE(TTM) {_fmt(snap.get('pe_ttm'))}   PB {_fmt(snap.get('pb'))}   "
               f"总市值 {_fmt(snap.get('mktcap_total'), ' 亿', 0)}   流通 {_fmt(snap.get('mktcap_float'), ' 亿', 0)}")
    if snap.get("industry") or snap.get("pe_percentile") is not None:
        pct = snap.get("pe_percentile")
        pct_txt = f"{pct * 100:.0f}%" if pct is not None else "—"
        out.append(f"  行业 {snap.get('industry') or '—'}   PE分位 {pct_txt}   "
                   f"同业样本 {snap.get('peer_pe_count') or '—'}")
    out.append(f"  52周区间 {_fmt(snap.get('low_52w'))} ~ {_fmt(snap.get('high_52w'))} 元   "
               f"年化波动率 {_fmt(result.get('volatility'), '%', 1)}")
    fin = result.get("finance")
    if fin:
        out.append(f"  财务摘要（{fin.get('report_date', '—')}）：ROE {_fmt(fin.get('roe'), '%', 1)}   "
                   f"营收同比 {_fmt(fin.get('revenue_yoy'), '%', 1)}   "
                   f"净利同比 {_fmt(fin.get('profit_yoy'), '%', 1)}   "
                   f"毛利率 {_fmt(fin.get('gross_margin'), '%', 1)}")
    out.append("")
    out.append(f"【指标信号】（分析窗口：近 {result['days']} 个交易日）")
    for s in result["signals"]:
        mark = {2: "▲▲", 1: "▲ ", 0: "· ", -1: "▽ ", -2: "▽▽"}[s["score"]]
        out.append(f"  {mark} [{CATEGORY_NAMES[s['category']]}] {s['name']}：{s['text']}")
    out.append("")
    out.append(f"【综合评分】 {result['score']:.0f} / 100  →  {result['grade']}")
    detail = result["score_detail"]
    if detail:
        parts = [f"{CATEGORY_NAMES[c]} {v:+.2f}" for c, v in detail.items()]
        out.append(f"  类别得分（-1~+1）：{'  '.join(parts)}")
    out.append("")
    out.append("【总结】")
    out.append(f"  {result['summary']}")
    out.append("")
    out.append("【结构分析（缠论·日线单级别）】")
    ch = result.get("chan")
    if not ch or "stroke" not in ch:
        out.append("  结构不足（K线过少或笔数不够），缠论分析已跳过")
    else:
        st = ch["stroke"]
        out.append(f"  当前笔：{st['dir']}{'（延伸中）' if not st['confirmed'] else ''}   "
                   f"确认笔 {ch['stroke_count']} 根 / 中枢 {ch['pivot_count']} 个")
        pv = ch.get("pivot")
        if pv:
            out.append(f"  最近中枢：{pv['zd']:.2f} ~ {pv['zg']:.2f} 元"
                       f"（{pv['n']}笔构成{'，延伸中' if pv['open'] else ''}）")
        dv = ch.get("divergence")
        if dv:
            out.append(f"  背驰：{dv['strength']}{dv['kind']}（{dv['date']}，{dv['age']}个交易日前，"
                       f"MACD面积 {dv['area_b']} vs {dv['area_a']}）")
        else:
            out.append("  背驰：暂无有效背驰")
        pt = ch.get("point")
        if pt:
            stale = "" if pt["age"] <= 10 else "（已陈旧，不参与计分）"
            out.append(f"  最近买卖点：{pt['type']} @ {pt['price']:.2f} 元"
                       f"（{pt['date']}，{pt['age']}个交易日前）{stale}")
        else:
            out.append("  最近买卖点：暂无")
    out.append("")
    pred = result["prediction"]
    out.append("【走势预测】")
    reg = pred["regression"]
    for w in (20, 60):
        r = reg.get(w)
        if r:
            out.append(f"  近{w}日趋势：{r['direction']}（回归斜率年化 {r['annualized_pct']:+.1f}%，拟合度R² {r['r2']:.2f}）")
    lv = result["levels"]
    level_window = lv.get("window_days")
    level_label = f"近{level_window}日" if level_window else "当前窗口"
    out.append(f"  支撑位（{level_label}） {lv['support']} 元（{lv['support_pct']:+.1f}%）   "
               f"阻力位（{level_label}） {lv['resistance']} 元（{lv['resistance_pct']:+.1f}%）")
    out.append(f"  未来5~10个交易日展望：【{pred['direction']}】倾向，置信度：{pred['confidence']}"
               f"（信号共振度 {pred['resonance']:.0%}）")
    sp = result.get("spread")
    if sp:
        out.append("")
        out.append("【做差价建议】")
        timing = sp.get("timing") or {}
        out.append(f"  偏置 {sp.get('bias')}  时机 【{timing.get('action', '—')}】  置信度 {sp.get('confidence')}"
                   f"{'  适合做差价' if sp.get('suitable') else '  空间/流动性一般'}")
        bz, sz = sp.get("buy_zone") or {}, sp.get("sell_zone") or {}
        out.append(f"  低吸带 {bz.get('low')} ~ {bz.get('high')} 元   "
                   f"高抛带 {sz.get('low')} ~ {sz.get('high')} 元   "
                   f"止损参考 {sp.get('stop')} 元")
        out.append(f"  {timing.get('reason', '')}")
        if sp.get("spread_plan"):
            out.append(f"  {sp['spread_plan']}")
        out.append(f"  {sp.get('disclaimer', '')}")
    out.append("")
    out.append(_LINE)
    out.append(DISCLAIMER)
    out.append(_LINE)
    return "\n".join(out)


def render_json(result):
    payload = {
        "code": result["snapshot"]["code"],
        "name": result["snapshot"]["name"],
        "market": result["market"],
        "data_asof": result["data_asof"],
        "session_state": result["session_state"],
        "sources": result["sources"],
        "days": result["days"],
        "snapshot": result["snapshot"],
        "finance": result.get("finance"),
        "industry": result.get("industry"),
        "volatility": result.get("volatility"),
        "signals": result["signals"],
        "chan": result.get("chan"),
        "score_version": SCORE_VERSION,
        "score": result["score"],
        "grade": result["grade"],
        "score_detail": result["score_detail"],
        "summary": result["summary"],
        "trend": result["prediction"]["direction"],
        "confidence": result["prediction"]["confidence"],
        "resonance": result["prediction"]["resonance"],
        "regression": result["prediction"]["regression"],
        "support": result["levels"]["support"],
        "resistance": result["levels"]["resistance"],
        "window_days": result["levels"].get("window_days"),
        "spread": result.get("spread"),
        "notes": result["notes"],
        "disclaimer": DISCLAIMER,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
