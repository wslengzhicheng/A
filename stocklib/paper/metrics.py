"""模拟盘基础绩效：总收益、最大回撤、成交笔数、胜率。"""
from __future__ import annotations

from typing import Dict, List, Optional


def _safe_div(a, b, default=0.0):
    try:
        if b == 0:
            return default
        return a / b
    except Exception:
        return default


def total_return(init_cash: float, equity: float) -> float:
    init_cash = float(init_cash or 0)
    equity = float(equity or 0)
    return _safe_div(equity - init_cash, init_cash, 0.0)


def max_drawdown(equity_curve: List[dict]) -> float:
    """equity_curve: [{equity: float, ...}, ...]；返回最大回撤（正数比例，如 0.12=12%）。"""
    peak = None
    mdd = 0.0
    for row in equity_curve or []:
        eq = row.get("equity") if isinstance(row, dict) else row
        try:
            eq = float(eq)
        except (TypeError, ValueError):
            continue
        if peak is None or eq > peak:
            peak = eq
        if peak and peak > 0:
            dd = (peak - eq) / peak
            if dd > mdd:
                mdd = dd
    return mdd


def trade_stats(fills: List[dict]) -> dict:
    """从成交推断已平仓回合的胜率（FIFO 简化配对）。

    返回 trade_count（卖出笔数）、win_rate、buy_count、sell_count、fee_total。
    """
    fills = list(fills or [])
    buy_count = sum(1 for f in fills if str(f.get("side", "")).lower() == "buy")
    sell_count = sum(1 for f in fills if str(f.get("side", "")).lower() == "sell")
    fee_total = 0.0
    for f in fills:
        fee_total += float(f.get("fee") or 0) + float(f.get("tax") or 0)

    # 按 code FIFO 配对估算已实现盈亏
    books: Dict[str, list] = {}
    wins = 0
    closed = 0
    for f in fills:
        side = str(f.get("side", "")).lower()
        code = str(f.get("code", ""))
        qty = int(f.get("qty") or 0)
        price = float(f.get("price") or 0)
        fee = float(f.get("fee") or 0) + float(f.get("tax") or 0)
        if qty <= 0 or price <= 0:
            continue
        if side == "buy":
            books.setdefault(code, []).append({"qty": qty, "price": price, "fee": fee})
        elif side == "sell":
            remain = qty
            sell_fee = fee
            while remain > 0 and books.get(code):
                lot = books[code][0]
                take = min(remain, lot["qty"])
                # 按比例分摊买入费用
                buy_fee_part = lot["fee"] * (take / lot["qty"]) if lot["qty"] else 0.0
                sell_fee_part = sell_fee * (take / qty) if qty else 0.0
                pnl = (price - lot["price"]) * take - buy_fee_part - sell_fee_part
                closed += 1
                if pnl > 0:
                    wins += 1
                lot["qty"] -= take
                lot["fee"] -= buy_fee_part
                remain -= take
                if lot["qty"] <= 0:
                    books[code].pop(0)

    win_rate = (_safe_div(wins, closed, 0.0) if closed else None)
    return {
        "trade_count": sell_count,  # 以卖出笔数作为「交易次数」
        "closed_rounds": closed,
        "win_rate": win_rate,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "fill_count": len(fills),
        "fee_total": round(fee_total, 4),
    }


def summarize(
    *,
    init_cash: float,
    equity: float,
    equity_curve: Optional[List[dict]] = None,
    fills: Optional[List[dict]] = None,
) -> dict:
    """汇总一份策略报表。"""
    curve = list(equity_curve or [])
    if not curve:
        curve = [{"equity": float(equity)}]
    stats = trade_stats(fills or [])
    return {
        "init_cash": round(float(init_cash), 4),
        "equity": round(float(equity), 4),
        "total_return": round(total_return(init_cash, equity), 6),
        "max_drawdown": round(max_drawdown(curve), 6),
        **stats,
    }


def equal_weight_benchmark_return(
    base_prices: Optional[dict] = None,
    current_prices: Optional[dict] = None,
) -> float:
    """等权买入持有：对两边都有正价的标的，取算术平均简单收益。

    缺少重叠标的时返回 0.0。
    """
    base_prices = base_prices or {}
    current_prices = current_prices or {}
    rets = []
    for code, bp in base_prices.items():
        try:
            b = float(bp)
            c = float(current_prices.get(code) or 0)
        except (TypeError, ValueError):
            continue
        if b <= 0 or c <= 0:
            continue
        rets.append((c - b) / b)
    if not rets:
        return 0.0
    return round(sum(rets) / len(rets), 6)


def excess_return(strategy_return: float, benchmark_return: float) -> float:
    """策略收益相对基准的超额。"""
    try:
        return round(float(strategy_return) - float(benchmark_return), 6)
    except (TypeError, ValueError):
        return 0.0


def index_benchmark_return(base_price, last_price) -> float:
    """单指数简单收益 (last-base)/base；无效时返回 None。"""
    try:
        b = float(base_price)
        c = float(last_price)
    except (TypeError, ValueError):
        return None
    if b <= 0 or c <= 0:
        return None
    return round((c - b) / b, 6)
