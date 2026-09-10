"""A股个股分析程序（入口）。
用法：
  stock.bat 贵州茅台            按名称查询
  stock.bat 600519              按代码查询
  stock.bat 600519 --days 120   自定义分析窗口（30~320）
  stock.bat 600519 --json       JSON输出
  stock.bat 600519 --fresh      跳过当日缓存
  stock.bat --web               启动Web界面（默认端口8888）
  stock.bat --web --port 9000   指定Web端口
  stock.bat --test              运行离线自检
  stock.bat --paper demo        模拟盘演示（见 tools/paper_run.py）
  stock.bat --paper start --strategies chanlun,ma_cross,macd --universe 600519 --once --force
  stock.bat --paper status|report|compare|stop [--run <id>]
退出码：0 成功 ｜ 2 网络失败 ｜ 3 未找到 ｜ 4 数据错误 ｜ 5 参数错误
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stocklib import analyzer, chanlun, datasource, predictor, report, spread
from stocklib.errors import StockError, NetworkError

USAGE = __doc__

DAYS_MIN, DAYS_MAX, DAYS_DEFAULT = 30, 320, 250


def parse_args(argv):
    opts = {"query": None, "days": DAYS_DEFAULT, "json": False, "fresh": False, "test": False, "web": False, "port": 8888, "paper": False, "paper_argv": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(USAGE)
            sys.exit(0)
        elif a == "--web":
            opts["web"] = True
        elif a == "--port":
            i += 1
            if i >= len(argv) or not argv[i].isdigit():
                print("[错误] --port 需要一个数字参数", file=sys.stderr)
                sys.exit(5)
            opts["port"] = int(argv[i])
        elif a == "--test":
            opts["test"] = True
        elif a == "--json":
            opts["json"] = True
        elif a == "--fresh":
            opts["fresh"] = True
        elif a == "--days":
            i += 1
            if i >= len(argv) or not argv[i].lstrip("-").isdigit():
                print("[错误] --days 需要一个数字参数", file=sys.stderr)
                sys.exit(5)
            days = int(argv[i])
            clamped = max(DAYS_MIN, min(DAYS_MAX, days))
            if clamped != days:
                print(f"[提示] --days {days} 超出范围 {DAYS_MIN}~{DAYS_MAX}，已调整为 {clamped}")
            opts["days"] = clamped
        elif a == "--paper":
            opts["paper"] = True
            opts["paper_argv"] = argv[i + 1 :]
            return opts
        elif a.startswith("--"):
            print(f"[错误] 未知参数 {a}\n{USAGE}", file=sys.stderr)
            sys.exit(5)
        else:
            if opts["query"] is not None:
                print(f"[错误] 只支持一个查询目标（已有 '{opts['query']}'，又出现 '{a}'）", file=sys.stderr)
                sys.exit(5)
            opts["query"] = a
        i += 1
    return opts


def build_result(market, code, klines, snapshot, finance, days, sources, industry_ctx=None):
    """取数结果 → 分析/预测 → 汇总字典（供 report 渲染；也用于fixtures回放测试）。"""
    klines = klines[-days:]
    snap = dict(snapshot) if snapshot else {}
    if industry_ctx:
        snap["industry"] = industry_ctx.get("industry")
        snap["industry_bk"] = industry_ctx.get("bk")
        snap["pe_percentile"] = industry_ctx.get("pe_percentile")
        snap["peer_pe_count"] = industry_ctx.get("peer_count")
    x = analyzer.compute_indicators(klines)
    signals, notes = analyzer.build_signals(x, snap, finance, industry_ctx)
    total, grade, detail = analyzer.score(signals)
    summary = analyzer.summarize(signals, total, grade, detail, snap, notes)
    pred = predictor.outlook(signals, x["closes"])
    levels = predictor.key_levels(klines, x, lookback_days=days)
    spread_plan = spread.build_spread_plan(
        klines, snap, x, levels, total, pred, signals)
    last_date = klines[-1]["date"]
    in_session = _in_trading_session_now()
    # 收盘后统一把 is_trading 置 false，避免与「收盘数据」文案不一致
    if snap.get("is_trading") and not in_session:
        snap["is_trading"] = False
    if snap.get("is_trading") and _is_today(last_date) and in_session:
        session_state = "盘中数据"
    elif _is_today(last_date):
        session_state = "收盘数据（今日已收盘）"
    else:
        session_state = "收盘数据"
    if not snap.get("is_trading") and not _is_today(last_date):
        # 非今日且无成交，更可能是停牌/久未交易
        notes.append("该股当前无成交（可能停牌），分析基于最近有效交易日数据")
        session_state = "最近交易日（疑似停牌）"
    elif not snap.get("is_trading") and not in_session and _is_today(last_date):
        # 今日已收盘：保持收盘文案，不标停牌
        pass
    elif not snap.get("is_trading"):
        notes.append("该股当前无成交（可能停牌），分析基于最近有效交易日数据")
        session_state = "最近交易日（疑似停牌）"
    return {
        "market": market,
        "snapshot": snap,
        "finance": finance,
        "industry": industry_ctx,
        "klines_count": len(klines),
        "days": len(klines),
        "data_asof": last_date,
        "session_state": session_state,
        "sources": sources,
        "volatility": x["volatility"],
        "chan": chanlun.brief(x["chan"], klines),
        "signals": signals,
        "score": total,
        "grade": grade,
        "score_detail": detail,
        "summary": summary,
        "prediction": pred,
        "levels": levels,
        "spread": spread_plan,
        "notes": notes,
    }


def _is_today(date_str):
    from datetime import datetime
    from stocklib.cache import CN_TZ
    return date_str == datetime.now(CN_TZ).strftime("%Y-%m-%d")


def _in_trading_session_now():
    """A股常规连续竞价：工作日 09:30-11:30、13:00-15:00（上海时区）。"""
    from datetime import datetime
    from stocklib.cache import CN_TZ
    now = datetime.now(CN_TZ)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1500)


def main(argv):
    opts = parse_args(argv)
    if opts["web"]:
        import web
        web.main(opts["port"])
        return 0
    if opts.get("paper"):
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "paper_run.py")
        spec = importlib.util.spec_from_file_location("paper_run", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.main(opts.get("paper_argv") or [])
    if opts["test"]:
        import test_offline
        return test_offline.run()
    if not opts["query"]:
        print(USAGE)
        return 0
    try:
        market, code, _ = datasource.resolve(opts["query"])
        klines, k_src = datasource.get_kline(market, code, count=DAYS_MAX, fresh=opts["fresh"])
        snapshot, s_src = datasource.get_snapshot(market, code, fresh=opts["fresh"])
        finance, f_src = datasource.get_finance(market, code, fresh=opts["fresh"])
        industry_ctx, i_src = datasource.get_industry_context(
            market, code, pe=(snapshot or {}).get("pe_ttm"), fresh=opts["fresh"])
        if finance is None:
            print(f"[提示] 财务摘要获取失败（{f_src}），基本面降级为估值快照")
        if industry_ctx is None:
            print(f"[提示] 行业PE分位不可用（{i_src}），估值按绝对阈值")
        sources = f"K线:{k_src} 快照:{s_src}" + (f" 财务:{f_src}" if finance else "")
        if industry_ctx:
            sources += f" 行业:{i_src}"
        result = build_result(
            market, code, klines, snapshot, finance, opts["days"], sources,
            industry_ctx=industry_ctx)
        print(report.render_json(result) if opts["json"] else report.render_text(result))
        return 0
    except NetworkError as e:
        print(f"[网络错误] {e}", file=sys.stderr)
        for src, msg in e.attempts:
            print(f"  - {src}: {msg[:120]}", file=sys.stderr)
        print("  诊断建议：检查本机能否访问 qt.gtimg.cn / push2.eastmoney.com（详见 README 接口失效自查）",
              file=sys.stderr)
        return e.exit_code
    except StockError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
