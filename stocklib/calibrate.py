"""综合评分历史回测校准。

对每只股票滚动截窗计算 analyzer 分数，与未来 N 日收益做 IC / 五分位价差，
并网格搜索类别权重微调，写出 calibration_overrides.json（可选应用）。

用法：
  python -m stocklib.calibrate              # 离线：fixtures + 合成样本
  python -m stocklib.calibrate --live 10    # 在线：取 N 只缓存/热门股（需网络）
  python -m stocklib.calibrate --apply      # 将建议写入 calibration_overrides.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from copy import deepcopy

from . import analyzer


FORWARD_DAYS = (5, 10, 20)
MIN_HISTORY = 80
STEP = 5  # 每隔多少根 K 采样一次评分


def _pearson(xs, ys):
    n = len(xs)
    if n < 8:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx < 1e-12 or dy < 1e-12:
        return None
    return num / (dx * dy)


def _forward_return(closes, i, horizon):
    j = i + horizon
    if j >= len(closes) or closes[i] in (None, 0):
        return None
    return (closes[j] / closes[i] - 1.0) * 100.0


def score_path(klines, snapshot=None, finance=None, industry_ctx=None):
    """滚动评分路径：[(idx, score, forward_rets_dict), ...]"""
    snap = snapshot or {
        "pe_ttm": 25.0, "pb": 2.0, "price": klines[-1]["close"],
    }
    closes = [k["close"] for k in klines]
    rows = []
    start = max(MIN_HISTORY, 60)
    for i in range(start, len(klines) - max(FORWARD_DAYS), STEP):
        window = klines[: i + 1]
        x = analyzer.compute_indicators(window)
        local_snap = dict(snap, price=closes[i])
        signals, _ = analyzer.build_signals(x, local_snap, finance, industry_ctx)
        total, grade, _ = analyzer.score(signals)
        fwd = {}
        for h in FORWARD_DAYS:
            r = _forward_return(closes, i, h)
            if r is not None:
                fwd[h] = r
        if fwd:
            rows.append({"i": i, "score": total, "grade": grade, "fwd": fwd})
    return rows


def summarize_ic(all_rows):
    """跨样本合并后按持有期算 IC 与高低五分位收益差。"""
    out = {}
    for h in FORWARD_DAYS:
        pairs = [(r["score"], r["fwd"][h]) for r in all_rows if h in r["fwd"]]
        if len(pairs) < 20:
            out[h] = {"n": len(pairs), "ic": None, "spread": None}
            continue
        scores = [p[0] for p in pairs]
        rets = [p[1] for p in pairs]
        ic = _pearson(scores, rets)
        # 五分位：高分桶均值 - 低分桶均值
        ranked = sorted(pairs, key=lambda p: p[0])
        q = max(1, len(ranked) // 5)
        low = statistics.mean(p[1] for p in ranked[:q])
        high = statistics.mean(p[1] for p in ranked[-q:])
        out[h] = {"n": len(pairs), "ic": None if ic is None else round(ic, 4),
                  "spread": round(high - low, 3), "low_q": round(low, 3), "high_q": round(high, 3)}
    return out


def _synth_klines(mode="up", n=180):
    """离线合成 K 线（避免依赖 test_offline）。"""
    from datetime import date, timedelta
    rows = []
    price = 100.0
    d0 = date(2025, 1, 2)
    for i in range(n):
        if mode == "up":
            price += 0.4
        elif mode == "down":
            price -= 0.35
        else:
            price += 0.05 * (1 if i % 2 == 0 else -1)
        px = max(price, 5.0)
        rows.append({
            "date": (d0 + timedelta(days=i)).isoformat(),
            "open": px, "close": px, "high": px * 1.01, "low": px * 0.99,
            "volume": 10000.0 + i * 10,
        })
    return rows


def _load_fixture_universe():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fix = os.path.join(root, "fixtures", "600519.json")
    samples = []
    if os.path.isfile(fix):
        with open(fix, "r", encoding="utf-8") as f:
            obj = json.load(f)
        samples.append({
            "code": "600519",
            "klines": obj["klines"],
            "snapshot": obj.get("snapshot"),
            "finance": obj.get("finance"),
            "industry_ctx": {
                "industry": "白酒",
                "peer_count": 20,
                "pe_percentile": 0.35,
                "peer_pes": [12, 14, 16, 18, 20, 22, 24, 26, 28, 30,
                             15, 17, 19, 21, 23, 25, 27, 29, 31, 33],
            },
        })
    for mode, pe in (("up", 18.0), ("down", 45.0), ("flat", 28.0)):
        ks = _synth_klines(mode, n=180)
        samples.append({
            "code": f"SYN_{mode}",
            "klines": ks,
            "snapshot": {"pe_ttm": pe, "pb": 3.0, "price": ks[-1]["close"]},
            "finance": {"roe": 12.0, "profit_yoy": 10.0, "revenue_yoy": 8.0, "gross_margin": 30.0},
            "industry_ctx": None,
        })
    return samples


def _try_live_universe(limit=10):
    """从缓存目录收集有 K 线的代码；不足则跳过。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(root, "cache")
    if not os.path.isdir(cache_dir):
        return []
    codes = set()
    for name in os.listdir(cache_dir):
        if name.startswith("kline_") and name.endswith(".json"):
            # kline_600519_20260709.json
            parts = name.split("_")
            if len(parts) >= 2 and parts[1].isdigit():
                codes.add(parts[1])
    samples = []
    from . import datasource
    for code in sorted(codes)[:limit]:
        try:
            market = "sh" if code.startswith(("60", "68")) else "sz"
            klines, _ = datasource.get_kline(market, code, count=250, fresh=False)
            snap, _ = datasource.get_snapshot(market, code, fresh=False)
            fin, _ = datasource.get_finance(market, code, fresh=False)
            ctx, _ = datasource.get_industry_context(
                market, code, pe=snap.get("pe_ttm") if snap else None, fresh=False)
            samples.append({
                "code": code, "klines": klines, "snapshot": snap,
                "finance": fin, "industry_ctx": ctx,
            })
        except Exception:
            continue
    return samples


def grid_search_weights(samples, base_metrics):
    """在趋势/摆动权重上做小网格，最大化 10 日 IC（若无 IC 则用 spread）。"""
    candidates = []
    for dt in (-0.05, 0.0, 0.05):
        for do in (-0.05, 0.0, 0.05):
            if abs(dt) + abs(do) > 0.08:
                continue
            w = deepcopy(analyzer.WEIGHTS)
            w["trend"] = max(0.2, min(0.45, w["trend"] + dt))
            w["osc"] = max(0.15, min(0.35, w["osc"] + do))
            # 归一
            s = sum(w.values())
            w = {k: v / s for k, v in w.items()}
            old = analyzer.WEIGHTS
            analyzer.WEIGHTS = w
            try:
                rows = []
                for smp in samples:
                    rows.extend(score_path(
                        smp["klines"], smp.get("snapshot"), smp.get("finance"),
                        smp.get("industry_ctx")))
                m = summarize_ic(rows)
            finally:
                analyzer.WEIGHTS = old
            score10 = m.get(10) or {}
            key = score10.get("ic")
            if key is None:
                key = (score10.get("spread") or 0) / 100.0
            candidates.append({"WEIGHTS": w, "metrics": m, "key": key})
    candidates.sort(key=lambda c: (c["key"] is not None, c["key"] or -9), reverse=True)
    best = candidates[0] if candidates else None
    return best, base_metrics


def run_calibration(live=0, apply=False):
    samples = _load_fixture_universe()
    if live:
        live_s = _try_live_universe(limit=live)
        samples.extend(live_s)

    all_rows = []
    per_code = {}
    for smp in samples:
        rows = score_path(
            smp["klines"], smp.get("snapshot"), smp.get("finance"),
            smp.get("industry_ctx"))
        per_code[smp["code"]] = len(rows)
        all_rows.extend(rows)

    base = summarize_ic(all_rows)
    best, _ = grid_search_weights(samples, base)

    report = {
        "score_version": analyzer.SCORE_VERSION,
        "samples": per_code,
        "n_points": len(all_rows),
        "base_metrics": base,
        "suggested_WEIGHTS": best["WEIGHTS"] if best else None,
        "suggested_metrics": best["metrics"] if best else None,
        "note": "IC>0 表示高分与未来收益正相关；建议人工复核后再 --apply",
    }

    out_dir = os.path.dirname(os.path.abspath(__file__))
    report_path = os.path.join(out_dir, "calibration_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if apply and best and best.get("WEIGHTS"):
        ov_path = os.path.join(out_dir, "calibration_overrides.json")
        ov = {
            "WEIGHTS": {k: round(v, 4) for k, v in best["WEIGHTS"].items()},
            "source": "stocklib.calibrate",
            "based_on": report["base_metrics"],
        }
        with open(ov_path, "w", encoding="utf-8") as f:
            json.dump(ov, f, ensure_ascii=False, indent=2)
        report["overrides_written"] = ov_path

    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="综合评分历史校准")
    p.add_argument("--live", type=int, default=0, help="额外使用缓存中最多 N 只股票")
    p.add_argument("--apply", action="store_true", help="写入 calibration_overrides.json")
    args = p.parse_args(argv)
    report = run_calibration(live=args.live, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
