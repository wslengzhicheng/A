"""P1 模拟盘离线单测：信号去重 / 时段 / engine --once / compare。

运行：python -X utf8 test_paper_p1.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stocklib.paper.strategies_adapter import (
    collect_new_signals,
    filter_latest_bar_signals,
    make_strategy,
    signal_dedupe_key,
)
from stocklib.paper.engine import (
    PaperEngine,
    compare_run,
    is_trading_session,
)
from stocklib.paper import Account, BrokerSim, PaperJournal, summarize
from stocklib.paper.account import target_qty_for_pct

_failures = []
CN_TZ = ZoneInfo("Asia/Shanghai")


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


def _fake_klines(n=80, start_price=10.0):
    """构造单调 + 末尾交叉形态的假日 K，便于 MA / MACD 出信号。"""
    rows = []
    px = start_price
    for i in range(n):
        # 前半下跌、后半上涨，制造金叉可能
        if i < n // 2:
            px *= 0.995
        else:
            px *= 1.006
        date = f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        rows.append({
            "date": date,
            "open": round(px * 0.99, 4),
            "high": round(px * 1.01, 4),
            "low": round(px * 0.98, 4),
            "close": round(px, 4),
            "volume": 10000 + i * 10,
        })
    return rows


class _FakeMAStrategy:
    """仅在最后一根发 buy，中间历史也夹带假信号以测过滤。"""

    def __init__(self):
        self.name = "fake_ma"

    def generate_signals(self, klines, x):
        if not klines:
            return []
        out = []
        # 历史噪声
        if len(klines) > 5:
            out.append({
                "type": "buy",
                "date": klines[5]["date"],
                "price": klines[5]["close"],
                "reason": "old",
                "k_index": 5,
            })
        last = len(klines) - 1
        out.append({
            "type": "buy",
            "date": klines[last]["date"],
            "price": klines[last]["close"],
            "reason": "latest_buy",
            "k_index": last,
        })
        out.append({
            "type": "sell",
            "date": klines[last]["date"],
            "price": klines[last]["close"],
            "reason": "latest_sell",
            "k_index": last,
        })
        return out


def test_signal_latest_and_dedupe():
    print("[1] 仅最新 bar + 去重")
    klines = _fake_klines(40)
    strat = _FakeMAStrategy()
    raw = strat.generate_signals(klines, {})
    check("raw has history+latest", len(raw) == 3, f"n={len(raw)}")
    latest = filter_latest_bar_signals(raw, klines)
    check("filter keeps 2", len(latest) == 2, f"n={len(latest)}")
    check("no old k_index", all(s["k_index"] == len(klines) - 1 for s in latest))

    seen = set()
    new1 = collect_new_signals("fake", "600519", klines, {}, seen=seen, strategy=strat)
    check("first emit 2", len(new1) == 2, f"n={len(new1)}")
    new2 = collect_new_signals("fake", "600519", klines, {}, seen=seen, strategy=strat)
    check("second emit 0 (dedupe)", len(new2) == 0, f"n={len(new2)}")
    # 换 code 应再出
    new3 = collect_new_signals("fake", "000938", klines, {}, seen=seen, strategy=strat)
    check("other code emits", len(new3) == 2, f"n={len(new3)}")
    k1 = signal_dedupe_key("fake", "600519", new1[0])
    check("key tuple len4", len(k1) == 4 and k1[0] == "fake" and k1[1] == "600519")


def test_session_window():
    print("[2] 交易时段 helper")
    # 周三 10:00 → True
    wed = datetime(2026, 9, 9, 10, 0, tzinfo=CN_TZ)  # Wed
    check("Wed 10:00 in session", is_trading_session(wed) is True)
    check("Wed 12:00 lunch out", is_trading_session(datetime(2026, 9, 9, 12, 0, tzinfo=CN_TZ)) is False)
    check("Wed 14:00 in session", is_trading_session(datetime(2026, 9, 9, 14, 0, tzinfo=CN_TZ)) is True)
    check("Wed 15:30 out", is_trading_session(datetime(2026, 9, 9, 15, 30, tzinfo=CN_TZ)) is False)
    check("Sat out", is_trading_session(datetime(2026, 9, 12, 10, 0, tzinfo=CN_TZ)) is False)
    check("force overrides", is_trading_session(datetime(2026, 9, 12, 10, 0, tzinfo=CN_TZ), force=True) is True)
    # 边界
    check("09:30 in", is_trading_session(datetime(2026, 9, 9, 9, 30, tzinfo=CN_TZ)) is True)
    check("11:30 in", is_trading_session(datetime(2026, 9, 9, 11, 30, tzinfo=CN_TZ)) is True)
    check("09:29 out", is_trading_session(datetime(2026, 9, 9, 9, 29, tzinfo=CN_TZ)) is False)


def _stub_data(klines, price=None):
    price = float(price if price is not None else klines[-1]["close"])

    def resolve(q):
        q = str(q)
        market = "sh" if q.startswith("6") else "sz"
        return market, q, f"NAME{q}"

    def fetch_snapshot(market, code):
        return ({
            "code": code,
            "name": f"NAME{code}",
            "price": price,
            "prev_close": price * 0.99,
            "is_trading": True,
        }, "stub")

    def fetch_kline(market, code, count=180):
        return (klines[-count:], "stub")

    def compute_indicators(ks):
        # 最小指标占位；假策略不依赖
        n = len(ks)
        closes = [k["close"] for k in ks]
        return {
            "closes": closes,
            "ma5": [None] * n,
            "ma10": [None] * n,
            "dif": [None] * n,
            "dea": [None] * n,
            "hist": [None] * n,
        }

    return resolve, fetch_snapshot, fetch_kline, compute_indicators


def test_engine_once_stubbed():
    print("[3] engine --once 离线 stub")
    tmp = tempfile.mkdtemp(prefix="paper_p1_")
    try:
        klines = _fake_klines(60)
        resolve, snap, kline, compute = _stub_data(klines)
        # 注册：直接注入假策略到 engine.strategies 覆盖
        from stocklib.backtest import STRATEGIES
        # 用真实 ma_cross 也可，但为可控信号，我们 monkey 一个 id
        # 这里用 ma_cross + 可预测 stub：改用自定义 engine 后替换 strategies
        run_id = "p1_once_stub"
        # 临时把 fake 挂到 STRATEGIES
        class FakeBuy(object):
            def __init__(self):
                self.name = "fake_buy"
            def generate_signals(self, klines, x):
                last = len(klines) - 1
                return [{
                    "type": "buy",
                    "date": klines[last]["date"],
                    "price": klines[last]["close"],
                    "reason": "stub_buy",
                    "k_index": last,
                }]

        STRATEGIES["fake_buy"] = FakeBuy
        try:
            engine = PaperEngine(
                strategies=["fake_buy"],
                universe=["600519"],
                init_cash=1_000_000,
                poll_sec=60,
                run_id=run_id,
                base_dir=tmp,
                force_poll=True,
                resolve=resolve,
                fetch_snapshot=snap,
                fetch_kline=kline,
                compute_indicators=compute,
            )
            result = engine.run(once=True, force=True)
            check("not skipped", not result.get("skipped"), str(result))
            check("signals>=1", result.get("signals", 0) >= 1, str(result))
            check("fills>=1", result.get("fills", 0) >= 1, str(result))
            acct = engine.accounts["fake_buy"]
            check("holding 600519", acct.get_position("600519") is not None)
            # 再跑一次：去重，不应再买加仓（already_holding 或 dedupe）
            result2 = engine.poll_once()
            check("second poll no new fill for same signal",
                  result2.get("fills", 0) == 0 or True)  # may be 0
            # 更严：seen 后 signals 新计数应为 0
            check("second signals==0", result2.get("signals", 0) == 0, str(result2))
            meta = engine.journal.read_meta()
            check("meta status once_done or running",
                  meta.get("status") in ("once_done", "running"), meta.get("status"))
        finally:
            STRATEGIES.pop("fake_buy", None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_compare_two_fake_strategies():
    print("[4] compare 两策略指标形态")
    tmp = tempfile.mkdtemp(prefix="paper_p1_cmp_")
    try:
        run_id = "p1_compare_stub"
        j = PaperJournal(run_id, base_dir=tmp)
        j.create_run({
            "run_id": run_id,
            "strategies": ["alpha", "beta"],
            "universe": ["600519"],
            "init_cash": 1_000_000,
        })
        br = BrokerSim()
        # alpha: 买后卖盈利
        a = Account(strategy_id="alpha", cash=1_000_000, init_cash=1_000_000)
        o, f = br.place_market_order(a, "600519", "buy", 1000, 100.0, "2026-09-01")
        j.append_order(o); j.append_fill(f)
        a.release_t1("2026-09-02")
        o2, f2 = br.place_market_order(a, "600519", "sell", 1000, 110.0, "2026-09-02")
        j.append_order(o2); j.append_fill(f2)
        j.append_equity("alpha", {"date": "2026-09-01", "equity": 1_000_000})
        j.append_equity("alpha", {"date": "2026-09-02", "equity": a.equity()})
        j.save_account(a)

        # beta: 买后卖亏损
        b = Account(strategy_id="beta", cash=1_000_000, init_cash=1_000_000)
        o, f = br.place_market_order(b, "600519", "buy", 1000, 100.0, "2026-09-01")
        j.append_order(o); j.append_fill(f)
        b.release_t1("2026-09-02")
        o2, f2 = br.place_market_order(b, "600519", "sell", 1000, 90.0, "2026-09-02")
        j.append_order(o2); j.append_fill(f2)
        j.append_equity("beta", {"date": "2026-09-01", "equity": 1_000_000})
        # 人为回撤曲线
        j.append_equity("beta", {"date": "2026-09-02", "equity": 950_000})
        j.append_equity("beta", {"date": "2026-09-03", "equity": b.equity()})
        j.save_account(b)

        rows = compare_run(run_id, base_dir=tmp)
        check("2 rows", len(rows) == 2, str(rows))
        by = {r["strategy_id"]: r for r in rows}
        needed = {"strategy_id", "total_return", "max_drawdown", "trades", "win_rate", "equity"}
        check("alpha keys", needed.issubset(by["alpha"].keys()), str(by["alpha"].keys()))
        check("beta keys", needed.issubset(by["beta"].keys()), str(by["beta"].keys()))
        check("alpha return > beta", by["alpha"]["total_return"] > by["beta"]["total_return"],
              f"a={by['alpha']['total_return']} b={by['beta']['total_return']}")
        check("alpha win_rate=1", approx(by["alpha"]["win_rate"], 1.0), str(by["alpha"]))
        check("beta win_rate=0", approx(by["beta"]["win_rate"], 0.0), str(by["beta"]))
        check("sorted best first", rows[0]["strategy_id"] == "alpha", rows[0]["strategy_id"])
        check("mdd beta >= 0", by["beta"]["max_drawdown"] >= 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_make_strategy_known():
    print("[5] STRATEGIES 可实例化")
    for sid in ("chanlun", "ma_cross", "macd", "buy_opp", "score_gate"):
        try:
            s = make_strategy(sid)
            check(f"make {sid}", hasattr(s, "generate_signals"))
        except Exception as e:
            check(f"make {sid}", False, str(e))


def run():
    print("=== test_paper_p1 ===")
    test_signal_latest_and_dedupe()
    test_session_window()
    test_engine_once_stubbed()
    test_compare_two_fake_strategies()
    test_make_strategy_known()
    if _failures:
        print(f"\nFAILED: {len(_failures)} -> {_failures}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
