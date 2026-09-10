"""P0 模拟盘离线单测：整手/费用/T+1/现金不足/涨跌停拒单。

运行：python -X utf8 test_paper_p0.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stocklib.paper import (
    Account,
    BrokerSim,
    PaperJournal,
    calc_commission,
    calc_stamp_tax,
    max_affordable_lots,
    summarize,
    target_qty_for_pct,
)

_failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


def test_fees_and_lots():
    print("[1] 费用与整手")
    fees = {"commission_rate": 0.0003, "commission_min": 5.0, "stamp_tax_rate": 0.0001, "lot": 100}
    # 小额名义：佣金触最低 5
    check("commission min 5", approx(calc_commission(1000, fees), 5.0), f"got {calc_commission(1000, fees)}")
    # 大额：万三
    check("commission 0.0003", approx(calc_commission(100_000, fees), 30.0), f"got {calc_commission(100_000, fees)}")
    check("stamp buy=0", approx(calc_stamp_tax(100_000, "buy", fees), 0.0))
    check("stamp sell 0.0001", approx(calc_stamp_tax(100_000, "sell", fees), 10.0))
    check("lot helper", max_affordable_lots(10_000, 10.0, fees) >= 9)  # 需留佣金
    # 刚好不够一手+佣金
    lots = max_affordable_lots(5.0, 10.0, fees)
    check("cannot afford any lot", lots == 0, f"got {lots}")
    q = target_qty_for_pct(1_000_000, 10.0, 0.15, 100)
    check("target 15% -> 15000", q == 15000, f"got {q}")


def test_t1_and_sell():
    print("[2] T+1 冻结与释放")
    acct = Account(strategy_id="t", cash=1_000_000)
    br = BrokerSim()
    o, f = br.place_market_order(acct, "600519", "buy", 1000, 100.0, "2026-09-01")
    check("buy filled", o["status"] == "filled" and f is not None, o.get("reject_reason"))
    pos = acct.get_position("600519")
    check("qty=1000", pos and pos["qty"] == 1000)
    check("available=0 same day", pos and pos["available_qty"] == 0)
    o2, f2 = br.place_market_order(acct, "600519", "sell", 100, 101.0, "2026-09-01")
    check("sell same day rejected", o2["status"] == "rejected" and o2["reject_reason"] == "t1_not_available")
    n = acct.release_t1("2026-09-02")
    check("release 1000", n == 1000 and acct.get_position("600519")["available_qty"] == 1000, f"released={n}")
    o3, f3 = br.place_market_order(acct, "600519", "sell", 500, 102.0, "2026-09-02")
    check("sell after T+1", o3["status"] == "filled" and f3 and f3["qty"] == 500)
    check("tax on sell", f3 and f3["tax"] > 0)
    check("fee on sell", f3 and f3["fee"] >= 5.0)


def test_insufficient_cash_shrink():
    print("[3] 现金不足缩量 / 拒单")
    acct = Account(strategy_id="c", cash=10_050)  # 约可买 1000 股@10 + 佣金
    br = BrokerSim()
    o, f = br.place_market_order(acct, "000001", "buy", 5000, 10.0, "2026-09-01")
    check("shrunk fill", o["status"] == "filled" and f is not None)
    check("filled <= affordable", f and f["qty"] <= 1000 and f["qty"] % 100 == 0, f"qty={f and f['qty']}")
    check("cash >= 0", acct.cash >= -1e-6, f"cash={acct.cash}")

    acct2 = Account(strategy_id="c2", cash=3.0)
    o2, f2 = br.place_market_order(acct2, "000001", "buy", 100, 10.0, "2026-09-01")
    check("reject insufficient", o2["status"] == "rejected" and o2["reject_reason"] == "insufficient_cash")
    check("no fill", f2 is None)


def test_limit_reject():
    print("[4] 涨停买 / 跌停卖拒单")
    acct = Account(strategy_id="l", cash=1_000_000)
    br = BrokerSim()
    o, f = br.place_market_order(
        acct, "600000", "buy", 100, 10.0, "2026-09-01", limit_up=True)
    check("limit_up reject buy", o["status"] == "rejected" and o["reject_reason"] == "limit_up_cannot_buy")
    # 先正常买再释放再跌停卖
    o1, f1 = br.place_market_order(acct, "600000", "buy", 100, 10.0, "2026-09-01")
    acct.release_t1("2026-09-02")
    o2, f2 = br.place_market_order(
        acct, "600000", "sell", 100, 9.0, "2026-09-02", limit_down=True)
    check("limit_down reject sell", o2["status"] == "rejected" and o2["reject_reason"] == "limit_down_cannot_sell")
    check("still holding", acct.get_position("600000") and acct.get_position("600000")["qty"] == 100)


def test_lot_reject_and_equity():
    print("[5] 非整手拒单与盯市")
    acct = Account(strategy_id="e", cash=1_000_000)
    br = BrokerSim()
    o, f = br.place_market_order(acct, "600000", "buy", 150, 10.0, "2026-09-01")
    check("non-lot rejected", o["status"] == "rejected" and "lot_size" in o["reject_reason"])
    o2, f2 = br.place_market_order(acct, "600000", "buy", 1000, 10.0, "2026-09-01")
    eq = acct.mark_to_market({"600000": 11.0})
    mv = 1000 * 11.0
    check("equity = cash + mv", approx(eq, acct.cash + mv), f"eq={eq} cash={acct.cash}")


def test_journal_and_metrics():
    print("[6] Journal 持久化与 metrics")
    tmp = tempfile.mkdtemp(prefix="paper_p0_")
    try:
        run_id = "test_run_001"
        j = PaperJournal(run_id, base_dir=tmp)
        j.create_run({"run_id": run_id, "init_cash": 1_000_000})
        acct = Account(strategy_id="demo", cash=1_000_000, init_cash=1_000_000)
        br = BrokerSim()
        o, f = br.place_market_order(acct, "600000", "buy", 1000, 10.0, "2026-09-01")
        j.append_order(o)
        j.append_fill(f)
        acct.release_t1("2026-09-02")
        o2, f2 = br.place_market_order(acct, "600000", "sell", 1000, 11.0, "2026-09-02")
        j.append_order(o2)
        j.append_fill(f2)
        j.append_equity("demo", {"date": "2026-09-01", "equity": 1_000_000})
        j.append_equity("demo", {"date": "2026-09-02", "equity": acct.equity()})
        j.save_account(acct)
        check("meta exists", os.path.isfile(j.meta_path))
        check("2 fills", len(j.read_fills()) == 2)
        loaded = j.load_account("demo")
        check("reload cash", approx(loaded.cash, acct.cash))
        rep = summarize(
            init_cash=1_000_000,
            equity=acct.equity(),
            equity_curve=j.read_equity("demo"),
            fills=j.read_fills(),
        )
        check("trade_count=1", rep["trade_count"] == 1, str(rep))
        check("win_rate=1", approx(rep["win_rate"], 1.0), str(rep))
        check("total_return>0", rep["total_return"] > 0, str(rep))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run():
    print("=== test_paper_p0 ===")
    test_fees_and_lots()
    test_t1_and_sell()
    test_insufficient_cash_shrink()
    test_limit_reject()
    test_lot_reject_and_equity()
    test_journal_and_metrics()
    if _failures:
        print(f"\nFAILED: {len(_failures)} -> {_failures}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
