"""腾讯行情（主数据源，目标机实测 6/6 稳定）。
- 实时：qt.gtimg.cn/q=sh600519  GBK 编码，~ 分隔，字段映射见 PLAN.md 附录A
- K线：web.ifzq.gtimg.cn/appstock/app/fqkline/get  前复权日K
- 搜索：smartbox.gtimg.cn/s3/
"""
import re

from . import http_get, to_float
from ..errors import DataError

MIN_FIELDS = 49  # 与 PLAN.md 附录A 一致，不足视为源异常


def fetch_snapshot(market, code):
    symbol = f"{market}{code}"
    r = http_get(f"https://qt.gtimg.cn/q={symbol}")
    r.encoding = "gbk"
    m = re.search(r'="([^"]*)"', r.text)
    if not m:
        raise DataError(f"腾讯实时接口返回无法解析: {r.text[:80]}")
    f = m.group(1).split("~")
    if len(f) < MIN_FIELDS:
        raise DataError(f"腾讯实时接口字段数 {len(f)} < {MIN_FIELDS}")
    price = to_float(f[3])
    if not price:  # 现价缺失/为0 → 停牌或源无效，交由编排层决定
        volume = to_float(f[36]) or 0
        if f[1] and volume == 0:
            return _build(f, price=to_float(f[4]), is_trading=False)  # 停牌：用昨收占位
        raise DataError("腾讯实时接口现价缺失")
    return _build(f, price=price, is_trading=True)


def _build(f, price, is_trading):
    return {
        "code": f[2],
        "name": f[1],
        "price": price,
        "prev_close": to_float(f[4]),
        "open": to_float(f[5]),
        "change_pct": to_float(f[32]),
        "high": to_float(f[33]),
        "low": to_float(f[34]),
        "volume": to_float(f[36]),        # 手
        "amount": to_float(f[37]),        # 万元
        "turnover": to_float(f[38]),      # %
        "pe_ttm": to_float(f[39]),
        "mktcap_float": to_float(f[44]),  # 亿元
        "mktcap_total": to_float(f[45]),  # 亿元
        "pb": to_float(f[46]),
        "high_52w": to_float(f[47]),
        "low_52w": to_float(f[48]),
        "time": f[30],
        "is_trading": is_trading,
    }


def fetch_kline(market, code, count=320, period="day", **kwargs):
    """Day kline fallback. Minute periods use eastmoney."""
    _p = str(period or "day").strip().lower()
    if _p not in ("day", "daily", "d", "101", ""):
        raise DataError("tencent kline day-only, period=%r" % (period,))
    symbol = f"{market}{code}"
    r = http_get(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        params={"param": f"{symbol},day,,,{count},qfq"},
    )
    d = r.json()
    node = d.get("data", {}).get(symbol, {})
    rows = node.get("qfqday") or node.get("day")
    if not rows:
        raise DataError("腾讯K线接口无数据")
    klines = []
    for row in rows:
        if len(row) < 6:
            continue
        o, c, h, l, v = (to_float(row[1]), to_float(row[2]), to_float(row[3]),
                         to_float(row[4]), to_float(row[5]))
        if None in (o, c, h, l, v):
            continue
        klines.append({"date": row[0], "open": o, "close": c, "high": h, "low": l, "volume": v})
    if len(klines) < 30:
        raise DataError(f"腾讯K线有效数据仅 {len(klines)} 根（<30）")
    return klines


def search(keyword):
    """smartbox 搜索，返回 [{code, name, market}]，仅沪深A股。"""
    r = http_get("https://smartbox.gtimg.cn/s3/", params={"v": "2", "q": keyword, "t": "all"})
    r.encoding = "gbk"
    m = re.search(r'="([^"]*)"', r.text)
    if not m or m.group(1) in ("", "N;"):
        return []
    out = []
    for item in m.group(1).split("^"):
        parts = item.split("~")
        if len(parts) < 3 or parts[0] not in ("sh", "sz"):
            continue
        name = parts[2].encode("latin1", "ignore").decode("unicode_escape", "ignore") \
            if "\\u" in parts[2] else parts[2]
        if len(parts) >= 5 and "GP" not in parts[4]:
            continue  # 只要股票类
        out.append({"code": parts[1], "name": name, "market": parts[0]})
    return out
