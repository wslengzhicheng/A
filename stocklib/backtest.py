"""回测引擎模块：支持多种策略的回测验证。"""
import math
import json
from . import datasource, analyzer, chanlun, indicators as ind


def _safe_div(a, b, default=0.0):
    """安全的除法，避免除零错误"""
    if b == 0 or not math.isfinite(b):
        return default
    result = a / b
    return result if math.isfinite(result) else default

def _clean_for_json(obj):
    """递归清理对象，确保可以JSON序列化"""
    if isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_for_json(item) for item in obj]
    elif isinstance(obj, (int, float)):
        if math.isinf(obj) or math.isnan(obj):
            return None
        return obj
    else:
        return obj


class BacktestStrategy:
    """策略基类"""
    def __init__(self, name: str):
        self.name = name

    def generate_signals(self, klines, x):
        """
        生成交易信号
        参数:
            klines: K线数据
            x: 指标数据（由 analyzer.compute_indicators 返回）
        返回:
            List[Dict] - [{"type": "buy/sell", "date": str, "price": float, "reason": str}]
        """
        raise NotImplementedError


class ChanLunStrategy(BacktestStrategy):
    """缠论买卖点策略"""
    def __init__(self):
        super().__init__("缠论买卖点")

    def generate_signals(self, klines, x):
        closes = [k["close"] for k in klines]
        dif = x.get("dif", [])
        hist = x.get("hist", [])

        merged = chanlun.merge_klines(klines)
        fr = chanlun.fractals(merged)
        sts, _ = chanlun.strokes(fr, merged)
        pvs = chanlun.pivots(sts)
        divs = chanlun.divergences(sts, pvs, dif, hist)
        pts = chanlun.buysell_points(sts, pvs, divs)

        signals = []
        for p in pts:
            sig_type = "buy" if p["t"] in {"一买", "二买", "三买", "类一买"} else "sell"
            signals.append({
                "type": sig_type,
                "date": klines[p["k"]]["date"],
                "price": p["price"],
                "reason": p["t"],
                "age": p.get("age", 0),
                "k_index": p["k"]
            })
        return signals


class MACrossStrategy(BacktestStrategy):
    """均线金叉死叉策略"""
    def __init__(self, short=5, long=10):
        super().__init__(f"MA{short}/MA{long}")
        self.short = short
        self.long = long

    def generate_signals(self, klines, x):
        closes = [k["close"] for k in klines]
        ma_short = x.get(f"ma{self.short}", [])
        ma_long = x.get(f"ma{self.long}", [])

        signals = []
        prev_status = None  # None, bull, bear

        min_idx = max(self.short, self.long)
        for i in range(min_idx, len(klines)):
            if ma_short[i] is None or ma_long[i] is None:
                continue

            if ma_short[i] > ma_long[i]:
                status = "bull"
            elif ma_short[i] < ma_long[i]:
                status = "bear"
            else:
                continue

            if prev_status is not None and status != prev_status:
                sig_type = "buy" if status == "bull" else "sell"
                reason = f"MA{self.short}" + ("金叉" if status == "bull" else "死叉") + f"MA{self.long}"
                signals.append({
                    "type": sig_type,
                    "date": klines[i]["date"],
                    "price": klines[i]["close"],
                    "reason": reason,
                    "k_index": i
                })
            prev_status = status

        return signals


class MACDStrategy(BacktestStrategy):
    """MACD金叉死叉策略"""
    def __init__(self, fast=12, slow=26, signal=9):
        super().__init__("MACD金叉死叉")
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def generate_signals(self, klines, x):
        dif = x.get("dif", [])
        dea = x.get("dea", [])
        hist = x.get("hist", [])

        signals = []
        for i in range(1, len(klines)):
            if dif[i] is None or dea[i] is None or dif[i-1] is None or dea[i-1] is None:
                continue

            # 金叉
            if dif[i-1] <= dea[i-1] and dif[i] > dea[i]:
                signals.append({
                    "type": "buy",
                    "date": klines[i]["date"],
                    "price": klines[i]["close"],
                    "reason": "MACD金叉",
                    "k_index": i
                })
            # 死叉
            elif dif[i-1] >= dea[i-1] and dif[i] < dea[i]:
                signals.append({
                    "type": "sell",
                    "date": klines[i]["date"],
                    "price": klines[i]["close"],
                    "reason": "MACD死叉",
                    "k_index": i
                })

        return signals


STRATEGIES = {
    "chanlun": ChanLunStrategy,
    "ma_cross": MACrossStrategy,
    "macd": MACDStrategy,
}


class BuyOpportunityStrategy(BacktestStrategy):
    """买入机会策略：滚动截窗复用 stock_picker.evaluate_opportunity。

    历史回测无法稳定还原主力资金流，默认 require_main_net=False；
    涨跌幅由相邻 K 线推算。每隔 step 根 K 评估一次以控制耗时。
    """

    def __init__(self, step=5, min_bars=60, exit_score=None, require_main_net=False):
        super().__init__("买入机会筛选")
        from .stock_picker import EXIT_SCORE
        self.step = max(1, int(step))
        self.min_bars = max(40, int(min_bars))
        self.exit_score = EXIT_SCORE if exit_score is None else exit_score
        self.require_main_net = bool(require_main_net)

    def generate_signals(self, klines, x=None):
        from . import analyzer
        from .stock_picker import evaluate_opportunity

        signals = []
        n = len(klines)
        if n < self.min_bars + 2:
            return signals

        # 从 min_bars 起，按 step 采样；最后一根也评估以便出场
        indices = list(range(self.min_bars, n, self.step))
        if (n - 1) not in indices:
            indices.append(n - 1)

        for i in indices:
            window = klines[: i + 1]
            closes = [k["close"] for k in window]
            change_pct = 0.0
            if len(closes) >= 2 and closes[-2]:
                change_pct = closes[-1] / closes[-2] - 1.0

            xi = analyzer.compute_indicators(window)
            snap = {
                "price": closes[-1],
                "pe_ttm": None,
                "pb": None,
                "change_pct": change_pct,
            }
            sigs, _notes = analyzer.build_signals(xi, snap, None, None)
            score, _grade, _detail = analyzer.score(sigs)
            bullish = sum(1 for s in sigs if s.get("score", 0) > 0)
            bearish = sum(1 for s in sigs if s.get("score", 0) < 0)
            has_buy = any(
                s.get("category") == "chan"
                and s.get("name") == "买卖点"
                and s.get("score", 0) > 0
                for s in sigs
            )

            verdict = evaluate_opportunity(
                score,
                change_pct=change_pct,
                main_net=None,
                has_buy_point=has_buy,
                bullish_count=bullish,
                bearish_count=bearish,
                require_main_net=self.require_main_net,
            )

            if verdict["passed"]:
                reason = "；".join(verdict["reasons"]) or f"买入机会(分{score:.0f})"
                signals.append({
                    "type": "buy",
                    "date": klines[i]["date"],
                    "price": klines[i]["close"],
                    "reason": reason,
                    "k_index": i,
                    "score": score,
                })
            elif score < self.exit_score:
                signals.append({
                    "type": "sell",
                    "date": klines[i]["date"],
                    "price": klines[i]["close"],
                    "reason": f"机会失效(分{score:.0f}<{self.exit_score})",
                    "k_index": i,
                    "score": score,
                })

        return signals



STRATEGIES["buy_opp"] = BuyOpportunityStrategy


def score_gate_side(score, buy_threshold=60.0, sell_threshold=40.0):
    """纯函数：综合分相对阈值决定 buy / sell / None（区间内观望）。"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    buy_threshold = float(buy_threshold)
    sell_threshold = float(sell_threshold)
    if s >= buy_threshold:
        return "buy"
    if s <= sell_threshold:
        return "sell"
    return None


class ScoreGateStrategy(BacktestStrategy):
    """综合评分阈值策略：score >= buy_threshold 买，<= sell_threshold 卖。

    复用 analyzer.build_signals + analyzer.score；信号附带 score / score_version。
    默认阈值 60 / 40；step 控制历史回放采样密度（末根始终评估）。
    """

    def __init__(self, buy_threshold=60.0, sell_threshold=40.0, min_bars=40, step=5):
        super().__init__("综合评分阈值")
        self.buy_threshold = float(buy_threshold)
        self.sell_threshold = float(sell_threshold)
        self.min_bars = max(30, int(min_bars))
        self.step = max(1, int(step))

    def generate_signals(self, klines, x=None):
        from . import analyzer

        signals = []
        n = len(klines or [])
        if n < self.min_bars + 1:
            return signals

        indices = list(range(self.min_bars, n, self.step))
        if (n - 1) not in indices:
            indices.append(n - 1)

        ver = str(getattr(analyzer, "SCORE_VERSION", "") or "")

        for i in indices:
            window = klines[: i + 1]
            closes = [k["close"] for k in window]
            change_pct = 0.0
            if len(closes) >= 2 and closes[-2]:
                change_pct = closes[-1] / closes[-2] - 1.0

            if i == n - 1 and isinstance(x, dict) and x:
                xi = x
            else:
                xi = analyzer.compute_indicators(window)

            snap = {
                "price": closes[-1],
                "pe_ttm": None,
                "pb": None,
                "change_pct": change_pct,
            }
            sigs, _notes = analyzer.build_signals(xi, snap, None, None)
            score_val, grade, _detail = analyzer.score(sigs)
            side = score_gate_side(score_val, self.buy_threshold, self.sell_threshold)
            if not side:
                continue
            if side == "buy":
                reason = f"score_gate买入(分{score_val:.1f}>={self.buy_threshold}, {grade})"
            else:
                reason = f"score_gate卖出(分{score_val:.1f}<={self.sell_threshold}, {grade})"
            signals.append({
                "type": side,
                "date": klines[i]["date"],
                "price": klines[i]["close"],
                "reason": reason,
                "k_index": i,
                "score": float(score_val),
                "score_version": ver,
                "grade": grade,
            })
        return signals


STRATEGIES["score_gate"] = ScoreGateStrategy



def backtest_signals(klines, signals, hold_days=10, stop_loss_pct=0.05):
    """按交易日推进的持仓回测（止损/到期不依赖新信号才触发）。"""
    buy_at = {}
    sell_at = {}
    for sig in sorted(signals, key=lambda x: x["k_index"]):
        idx = sig["k_index"]
        if sig["type"] == "buy":
            buy_at[idx] = sig
        elif sig["type"] == "sell":
            sell_at[idx] = sig

    trades = []
    position = None

    for i, bar in enumerate(klines):
        price = bar["close"]
        date = bar["date"]

        if position is not None:
            days_held = i - position["entry_idx"]
            loss_pct = (price - position["entry_price"]) / position["entry_price"]
            exit_reason = None
            if i in sell_at:
                exit_reason = sell_at[i].get("reason") or "卖出信号"
            elif days_held >= hold_days:
                exit_reason = f"{position['reason']}（到期平仓）"
            elif loss_pct <= -stop_loss_pct:
                exit_reason = f"{position['reason']}（止损）"

            if exit_reason is not None:
                pnl_pct = (price - position["entry_price"]) / position["entry_price"] * 100
                trades.append({
                    "entry_date": position["entry_date"],
                    "entry_price": position["entry_price"],
                    "exit_date": date,
                    "exit_price": price,
                    "pnl_pct": round(pnl_pct, 2),
                    "hold_days": days_held,
                    "reason": exit_reason,
                })
                position = None

        if position is None and i in buy_at:
            sig = buy_at[i]
            position = {
                "entry_date": date,
                "entry_price": sig.get("price", price),
                "entry_idx": i,
                "reason": sig.get("reason") or "买入",
            }

    if not trades:
        return {"trades": [], "stats": {}}

    win_trades = [t for t in trades if t["pnl_pct"] > 0]
    lose_trades = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = _safe_div(len(win_trades), len(trades), 0.0)
    avg_win = _safe_div(sum(t["pnl_pct"] for t in win_trades), len(win_trades), 0.0) if win_trades else 0
    avg_lose = _safe_div(sum(t["pnl_pct"] for t in lose_trades), len(lose_trades), 0.0) if lose_trades else 0
    total_pnl = sum(t["pnl_pct"] for t in trades)

    cumulative = [0]
    for t in trades:
        cumulative.append(cumulative[-1] + t["pnl_pct"])
    max_dd = 0
    peak = cumulative[0]
    for val in cumulative[1:]:
        if val > peak:
            peak = val
        dd = _safe_div(peak - val, abs(peak), 0.0) if peak != 0 else 0
        max_dd = max(max_dd, dd)

    total_win = sum(t["pnl_pct"] for t in win_trades)
    total_loss = sum(abs(t["pnl_pct"]) for t in lose_trades)
    profit_factor = _safe_div(total_win, total_loss, 0.0)
    if len(trades) > 1:
        avg_return = _safe_div(total_pnl, len(trades), 0.0)
        variance = sum((t["pnl_pct"] - avg_return) ** 2 for t in trades) / len(trades)
        std_return = math.sqrt(variance) if variance >= 0 else 0
        sharpe = _safe_div(avg_return, std_return, 0.0)
    else:
        sharpe = 0

    return {
        "trades": trades,
        "stats": {
            "total_trades": len(trades),
            "win_trades": len(win_trades),
            "lose_trades": len(lose_trades),
            "win_rate": round(win_rate * 100, 2),
            "avg_win": round(avg_win, 2),
            "avg_lose": round(avg_lose, 2),
            "total_pnl": round(total_pnl, 2),
            "max_drawdown": round(max_dd * 100, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 2),
            "avg_hold_days": round(sum(t["hold_days"] for t in trades) / len(trades), 1) if trades else 0,
        }
    }


def run_backtest(query, strategy_name="chanlun", days=250, hold_days=10,
                 stop_loss_pct=0.05, strategy_params=None, fresh=False):
    """
    运行单只股票的回测
    参数:
        query: 股票代码或名称
        strategy_name: 策略名称
        days: 回测天数
        hold_days: 默认持有天数
        stop_loss_pct: 止损百分比
        strategy_params: 策略参数字典
        fresh: 是否强制刷新缓存
    返回:
        Dict: 回测结果
    """
    market, code, name = datasource.resolve(query)
    klines, _ = datasource.get_kline(market, code, count=days, fresh=fresh)
    klines = klines[-days:]

    # 计算指标
    x = analyzer.compute_indicators(klines)

    # 获取策略实例
    strategy_cls = STRATEGIES.get(strategy_name, ChanLunStrategy)
    strategy_params = strategy_params or {}

    if strategy_name == "ma_cross":
        strategy = strategy_cls(**strategy_params)
    else:
        strategy = strategy_cls(**strategy_params)

    # 生成信号
    signals = strategy.generate_signals(klines, x)

    # 执行回测
    result = backtest_signals(klines, signals, hold_days=hold_days, stop_loss_pct=stop_loss_pct)

    # 基准对比（随机入场）
    from random import randint
    random_returns = []
    for _ in range(50):
        entry_idx = randint(0, len(klines) - hold_days - 1)
        exit_idx = entry_idx + hold_days
        if exit_idx < len(klines):
            ret = (klines[exit_idx]["close"] / klines[entry_idx]["close"] - 1) * 100
            random_returns.append(ret)

    random_avg = sum(random_returns) / len(random_returns) if random_returns else 0
    random_win_rate = sum(1 for r in random_returns if r > 0) / len(random_returns) if random_returns else 0

    # 买入持有基准
    buy_hold_return = (klines[-1]["close"] / klines[0]["close"] - 1) * 100

    result = {
        "name": name or f"{market}{code}",
        "code": code,
        "market": market,
        "strategy": strategy.name,
        "klines_count": len(klines),
        "signals_count": len(signals),
        "trades_count": len(result["trades"]),
        "backtest_period": {
            "start_date": klines[0]["date"],
            "end_date": klines[-1]["date"],
            "trading_days": len(klines),
        },
        "trades": result["trades"],
        "performance": result["stats"],
        "benchmark": {
            "random_entry": {
                "avg_return": round(random_avg, 2),
                "win_rate": round(random_win_rate * 100, 2),
                "samples": len(random_returns)
            },
            "buy_hold": {
                "return": round(buy_hold_return, 2),
            }
        },
        "strategy_alpha": round(result["stats"].get("total_pnl", 0) - random_avg, 2),
    }

    # 清理JSON数据中的Infinity和NaN值
    return _clean_for_json(result)