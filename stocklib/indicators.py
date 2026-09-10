"""纯函数指标库（无IO）。输入为 float 列表，输出等长列表（数据不足处为 None）。
公式与国内行情软件通行口径一致（PLAN.md §7 初值约定）：
- KDJ 起算 K=D=50；RSI/KDJ 平滑用 SMA(X,N,1) 递推；EMA 预热取全量；BOLL 标准差 ddof=1。
"""
import math


def ma(values, n):
    out = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(values, n):
    if not values:
        return []
    out = [values[0]]
    alpha = 2.0 / (n + 1)
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _sma_cn(values, n, m=1, init=None):
    """国内通行 SMA(X,N,M) 递推：Y = (M*X + (N-M)*Y') / N。init 为 None 时首值 = X0。"""
    out = []
    prev = None
    for v in values:
        if v is None:
            out.append(prev)
            continue
        if prev is None:
            prev = init if init is not None else v
        prev = (m * v + (n - m) * prev) / n
        out.append(prev)
    return out


def macd(closes, fast=12, slow=26, signal=9):
    """返回 (dif, dea, hist)；hist 为国内口径 2*(DIF-DEA)。"""
    e_fast, e_slow = ema(closes, fast), ema(closes, slow)
    dif = [f - s for f, s in zip(e_fast, e_slow)]
    dea = ema(dif, signal)
    hist = [2 * (a - b) for a, b in zip(dif, dea)]
    return dif, dea, hist


def kdj(highs, lows, closes, n=9, k_n=3, d_n=3):
    """返回 (K, D, J)，K/D 起算值 50。"""
    length = len(closes)
    rsv = [None] * length
    for i in range(n - 1, length):
        hh = max(highs[i - n + 1: i + 1])
        ll = min(lows[i - n + 1: i + 1])
        rsv[i] = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100
    k = _sma_cn(rsv, k_n, 1, init=50.0)
    d = _sma_cn(k, d_n, 1, init=50.0)
    j = [None if (a is None or b is None) else 3 * a - 2 * b for a, b in zip(k, d)]
    # 数据不足段还原为 None
    for i in range(min(n - 1, length)):
        k[i] = d[i] = j[i] = None
    return k, d, j


def rsi(closes, n):
    length = len(closes)
    if length < 2:
        return [None] * length
    ups = [None] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, length)]
    downs = [None] + [abs(closes[i] - closes[i - 1]) for i in range(1, length)]
    up_s = _sma_cn(ups[1:], n, 1)
    dn_s = _sma_cn(downs[1:], n, 1)
    out = [None]
    for u, d in zip(up_s, dn_s):
        out.append(None if (u is None or d is None or d == 0) else u / d * 100)
    for i in range(min(n, length)):
        out[i] = None  # 平滑未充分预热段不输出
    return out


def boll(closes, n=20, k=2.0):
    mid = ma(closes, n)
    upper, lower = [None] * len(closes), [None] * len(closes)
    for i in range(n - 1, len(closes)):
        window = closes[i - n + 1: i + 1]
        mean = mid[i]
        var = sum((x - mean) ** 2 for x in window) / (n - 1)  # ddof=1
        sd = math.sqrt(var)
        upper[i] = mean + k * sd
        lower[i] = mean - k * sd
    return mid, upper, lower


def linreg(values):
    """最小二乘回归，返回 (斜率, R²)。values 为收盘价序列（x 取 0..n-1）。"""
    n = len(values)
    if n < 3:
        return 0.0, 0.0
    xm = (n - 1) / 2.0
    ym = sum(values) / n
    sxx = sum((i - xm) ** 2 for i in range(n))
    sxy = sum((i - xm) * (v - ym) for i, v in enumerate(values))
    slope = sxy / sxx
    ss_tot = sum((v - ym) ** 2 for v in values)
    if ss_tot == 0:
        return slope, 0.0
    ss_res = sum((v - (ym + slope * (i - xm))) ** 2 for i, v in enumerate(values))
    r2 = max(0.0, 1 - ss_res / ss_tot)
    return slope, r2


def annualized_volatility(closes, window=250):
    """近 window 日对数收益年化波动率（%）。"""
    seq = closes[-window:]
    if len(seq) < 20:
        return None
    rets = [math.log(seq[i] / seq[i - 1]) for i in range(1, len(seq)) if seq[i - 1] > 0]
    if len(rets) < 10:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(250) * 100
