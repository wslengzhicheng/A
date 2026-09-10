"""模拟盘账户：现金、持仓、T+1 可用量、市值盯市。

纯内存结构，JSON 友好（to_dict / from_dict）；无第三方依赖。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(x, default=0.0) -> float:
    try:
        v = float(x)
        if v != v:  # NaN
            return float(default)
        return v
    except (TypeError, ValueError):
        return float(default)


def _i(x, default=0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return int(default)


class Account:
    """纸面账户。

    positions[code] = {
        qty, available_qty, avg_cost, last_price,
        pending: [{qty, buy_date}, ...]  # T+1 待释放
    }
    """

    def __init__(
        self,
        strategy_id: str = "default",
        name: str = "",
        cash: float = 1_000_000.0,
        init_cash: Optional[float] = None,
        positions: Optional[Dict[str, dict]] = None,
        params: Optional[dict] = None,
    ):
        self.strategy_id = str(strategy_id)
        self.name = name or self.strategy_id
        self.cash = _f(cash)
        self.init_cash = _f(init_cash if init_cash is not None else cash)
        self.positions: Dict[str, dict] = {}
        if positions:
            for code, pos in positions.items():
                self.positions[str(code)] = self._norm_pos(code, pos)
        self.params = dict(params or {})
        self.params.setdefault("init_cash", self.init_cash)
        self.params.setdefault("lot", 100)

    @staticmethod
    def _norm_pos(code: str, pos: dict) -> dict:
        pending = pos.get("pending") or []
        clean_pending = []
        for p in pending:
            clean_pending.append({
                "qty": _i(p.get("qty", 0)),
                "buy_date": str(p.get("buy_date") or ""),
            })
        return {
            "code": str(pos.get("code") or code),
            "qty": _i(pos.get("qty", 0)),
            "available_qty": _i(pos.get("available_qty", 0)),
            "avg_cost": _f(pos.get("avg_cost", 0.0)),
            "last_price": _f(pos.get("last_price", pos.get("avg_cost", 0.0))),
            "pending": clean_pending,
        }

    def get_position(self, code: str) -> Optional[dict]:
        return self.positions.get(str(code))

    def market_value(self, prices: Optional[Dict[str, float]] = None) -> float:
        """持仓市值；prices 可覆盖 last_price。"""
        total = 0.0
        for code, pos in self.positions.items():
            px = None
            if prices and code in prices:
                px = _f(prices[code])
            else:
                px = _f(pos.get("last_price", 0.0))
            total += _i(pos.get("qty", 0)) * px
        return total

    def equity(self, prices: Optional[Dict[str, float]] = None) -> float:
        """权益 = 现金 + 持仓市值。"""
        return self.cash + self.market_value(prices)

    def mark_to_market(self, prices: Dict[str, float]) -> float:
        """按最新价刷新 last_price 并返回权益。"""
        for code, px in (prices or {}).items():
            pos = self.positions.get(str(code))
            if pos is not None:
                pos["last_price"] = _f(px)
        return self.equity()

    def apply_buy(self, code: str, qty: int, price: float, buy_date: str) -> None:
        """成交买入：扣现金在 broker；此处只改持仓。T+1：available 不增加，记 pending。"""
        code = str(code)
        qty = _i(qty)
        price = _f(price)
        if qty <= 0:
            return
        pos = self.positions.get(code)
        if pos is None:
            pos = {
                "code": code,
                "qty": 0,
                "available_qty": 0,
                "avg_cost": 0.0,
                "last_price": price,
                "pending": [],
            }
            self.positions[code] = pos
        old_qty = _i(pos["qty"])
        new_qty = old_qty + qty
        if new_qty > 0:
            pos["avg_cost"] = (old_qty * _f(pos["avg_cost"]) + qty * price) / new_qty
        pos["qty"] = new_qty
        pos["last_price"] = price
        pos.setdefault("pending", []).append({"qty": qty, "buy_date": str(buy_date)})

    def apply_sell(self, code: str, qty: int, price: float) -> None:
        """成交卖出：从 available_qty / qty 扣减；现金入账在 broker。"""
        code = str(code)
        qty = _i(qty)
        price = _f(price)
        pos = self.positions.get(code)
        if pos is None or qty <= 0:
            return
        pos["qty"] = _i(pos["qty"]) - qty
        pos["available_qty"] = _i(pos["available_qty"]) - qty
        pos["last_price"] = price
        if pos["qty"] <= 0:
            # 清仓
            del self.positions[code]
        else:
            if pos["available_qty"] < 0:
                pos["available_qty"] = 0

    def release_t1(self, asof_date: str) -> int:
        """释放 buy_date < asof_date 的待解冻股数到 available_qty。

        返回本次释放的总股数。日历日即可（交易日由上层传入）。
        """
        asof = str(asof_date)
        released = 0
        for pos in list(self.positions.values()):
            pending = pos.get("pending") or []
            keep = []
            for lot in pending:
                bd = str(lot.get("buy_date") or "")
                q = _i(lot.get("qty", 0))
                if bd and bd < asof and q > 0:
                    pos["available_qty"] = _i(pos["available_qty"]) + q
                    released += q
                elif q > 0:
                    keep.append({"qty": q, "buy_date": bd})
            pos["pending"] = keep
            # 可用量不得超过持仓
            if _i(pos["available_qty"]) > _i(pos["qty"]):
                pos["available_qty"] = _i(pos["qty"])
        return released

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "cash": round(self.cash, 4),
            "init_cash": round(self.init_cash, 4),
            "equity": round(self.equity(), 4),
            "market_value": round(self.market_value(), 4),
            "positions": {c: dict(p) for c, p in self.positions.items()},
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        d = d or {}
        return cls(
            strategy_id=d.get("strategy_id", "default"),
            name=d.get("name", ""),
            cash=d.get("cash", 1_000_000.0),
            init_cash=d.get("init_cash"),
            positions=d.get("positions") or {},
            params=d.get("params") or {},
        )


def target_qty_for_pct(equity: float, price: float, pct: float = 0.15, lot: int = 100) -> int:
    """按权益占比估算目标买入股数（向下取整到手数）。"""
    equity = _f(equity)
    price = _f(price)
    pct = _f(pct)
    lot = max(1, _i(lot, 100))
    if price <= 0 or equity <= 0 or pct <= 0:
        return 0
    budget = equity * pct
    shares = int(budget // price)
    return (shares // lot) * lot
