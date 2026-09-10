"""模拟盘撮合：市价单、整手、T+1、佣金/印花税、涨跌停拒单。

无真实券商连接；纯本地规则。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from .account import Account, _f, _i


# 默认费率（可被 fees 覆盖）
DEFAULT_FEES = {
    "commission_rate": 0.0003,   # 万三
    "commission_min": 5.0,       # 最低 5 元
    "stamp_tax_rate": 0.0001,    # 卖出印花税万一
    "lot": 100,
}


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def calc_commission(notional: float, fees: dict) -> float:
    rate = _f(fees.get("commission_rate", DEFAULT_FEES["commission_rate"]))
    amin = _f(fees.get("commission_min", DEFAULT_FEES["commission_min"]))
    return max(amin, abs(notional) * rate)


def calc_stamp_tax(notional: float, side: str, fees: dict) -> float:
    if str(side).lower() != "sell":
        return 0.0
    rate = _f(fees.get("stamp_tax_rate", DEFAULT_FEES["stamp_tax_rate"]))
    return abs(notional) * rate


def max_affordable_lots(cash: float, price: float, fees: dict) -> int:
    """在现金约束下最大可买整手数（考虑佣金）。"""
    lot = max(1, _i(fees.get("lot", DEFAULT_FEES["lot"]), 100))
    price = _f(price)
    cash = _f(cash)
    if price <= 0 or cash <= 0:
        return 0
    # 粗估：逐档缩量；从理论最大手往下试
    max_shares = int(cash // price)
    lots = max_shares // lot
    while lots > 0:
        qty = lots * lot
        notional = qty * price
        fee = calc_commission(notional, fees)
        if notional + fee <= cash + 1e-9:
            return lots
        lots -= 1
    return 0


class BrokerSim:
    """纸面经纪：校验 + 成交，改写 Account，返回 order/fill 字典。"""

    def __init__(self, fees: Optional[dict] = None):
        self.fees = dict(DEFAULT_FEES)
        if fees:
            self.fees.update(fees)

    def place_market_order(
        self,
        account: Account,
        code: str,
        side: str,
        qty: int,
        price: float,
        asof_date: str,
        *,
        limit_up: bool = False,
        limit_down: bool = False,
        reason: str = "",
        ts: Optional[str] = None,
    ) -> Tuple[dict, Optional[dict]]:
        """下市价单并尝试立即成交。

        返回 (order, fill|None)。拒单时 fill 为 None，order.status=rejected。
        """
        code = str(code)
        side = str(side).lower().strip()
        qty = _i(qty)
        price = _f(price)
        asof_date = str(asof_date)
        ts = ts or _now_iso()
        lot = max(1, _i(self.fees.get("lot", 100), 100))
        order_id = _new_id("ord")

        order: Dict[str, Any] = {
            "id": order_id,
            "ts": ts,
            "asof_date": asof_date,
            "strategy_id": account.strategy_id,
            "code": code,
            "side": side,
            "qty": qty,
            "typ": "market",
            "price": price,
            "status": "new",
            "reason": reason or "",
            "reject_reason": "",
        }

        if side not in ("buy", "sell"):
            order["status"] = "rejected"
            order["reject_reason"] = "invalid_side"
            return order, None
        if price <= 0:
            order["status"] = "rejected"
            order["reject_reason"] = "invalid_price"
            return order, None
        if qty <= 0:
            order["status"] = "rejected"
            order["reject_reason"] = "invalid_qty"
            return order, None
        if qty % lot != 0:
            order["status"] = "rejected"
            order["reject_reason"] = f"lot_size_{lot}"
            return order, None

        # 涨跌停拒单
        if side == "buy" and limit_up:
            order["status"] = "rejected"
            order["reject_reason"] = "limit_up_cannot_buy"
            return order, None
        if side == "sell" and limit_down:
            order["status"] = "rejected"
            order["reject_reason"] = "limit_down_cannot_sell"
            return order, None

        if side == "buy":
            return self._fill_buy(account, order, code, qty, price, asof_date, ts, lot)
        return self._fill_sell(account, order, code, qty, price, asof_date, ts)

    def _fill_buy(self, account, order, code, qty, price, asof_date, ts, lot):
        affordable_lots = max_affordable_lots(account.cash, price, self.fees)
        want_lots = qty // lot
        fill_lots = min(want_lots, affordable_lots)
        if fill_lots <= 0:
            order["status"] = "rejected"
            order["reject_reason"] = "insufficient_cash"
            return order, None
        fill_qty = fill_lots * lot
        if fill_qty < qty:
            order["qty"] = fill_qty  # 缩量成交
            order["reason"] = (order.get("reason") or "") + ("|shrunk" if order.get("reason") else "shrunk")

        notional = fill_qty * price
        fee = calc_commission(notional, self.fees)
        tax = 0.0
        cost = notional + fee
        if cost > account.cash + 1e-9:
            order["status"] = "rejected"
            order["reject_reason"] = "insufficient_cash"
            return order, None

        account.cash -= cost
        account.apply_buy(code, fill_qty, price, asof_date)
        account.positions[code]["last_price"] = price

        fill = {
            "id": _new_id("fill"),
            "order_id": order["id"],
            "ts": ts,
            "asof_date": asof_date,
            "strategy_id": account.strategy_id,
            "code": code,
            "side": "buy",
            "price": price,
            "qty": fill_qty,
            "fee": round(fee, 4),
            "tax": round(tax, 4),
            "notional": round(notional, 4),
            "cash_after": round(account.cash, 4),
            "reason": order.get("reason") or "",
        }
        order["status"] = "filled"
        order["filled_qty"] = fill_qty
        order["fee"] = fill["fee"]
        order["tax"] = fill["tax"]
        return order, fill

    def _fill_sell(self, account, order, code, qty, price, asof_date, ts):
        pos = account.get_position(code)
        if pos is None:
            order["status"] = "rejected"
            order["reject_reason"] = "no_position"
            return order, None
        avail = _i(pos.get("available_qty", 0))
        if avail <= 0:
            order["status"] = "rejected"
            order["reject_reason"] = "t1_not_available"
            return order, None
        fill_qty = min(qty, avail)
        # 卖出也按整手：若 avail 非整手，只卖整手部分
        lot = max(1, _i(self.fees.get("lot", 100), 100))
        fill_qty = (fill_qty // lot) * lot
        if fill_qty <= 0:
            order["status"] = "rejected"
            order["reject_reason"] = "t1_not_available"
            return order, None
        if fill_qty < qty:
            order["qty"] = fill_qty
            order["reason"] = (order.get("reason") or "") + ("|shrunk" if order.get("reason") else "shrunk")

        notional = fill_qty * price
        fee = calc_commission(notional, self.fees)
        tax = calc_stamp_tax(notional, "sell", self.fees)
        proceeds = notional - fee - tax
        if proceeds < 0:
            order["status"] = "rejected"
            order["reject_reason"] = "fee_exceeds_proceeds"
            return order, None

        account.apply_sell(code, fill_qty, price)
        account.cash += proceeds

        fill = {
            "id": _new_id("fill"),
            "order_id": order["id"],
            "ts": ts,
            "asof_date": asof_date,
            "strategy_id": account.strategy_id,
            "code": code,
            "side": "sell",
            "price": price,
            "qty": fill_qty,
            "fee": round(fee, 4),
            "tax": round(tax, 4),
            "notional": round(notional, 4),
            "cash_after": round(account.cash, 4),
            "reason": order.get("reason") or "",
        }
        order["status"] = "filled"
        order["filled_qty"] = fill_qty
        order["fee"] = fill["fee"]
        order["tax"] = fill["tax"]
        return order, fill
