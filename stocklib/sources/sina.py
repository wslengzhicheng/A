"""新浪行情（备用②，仅实时快照；需 Referer 头）。估值/52周字段该源不提供 → None。"""
import re

from . import http_get, to_float
from ..errors import DataError


def fetch_snapshot(market, code):
    symbol = f"{market}{code}"
    r = http_get(f"https://hq.sinajs.cn/list={symbol}",
                 headers={"Referer": "https://finance.sina.com.cn"})
    r.encoding = "gbk"
    m = re.search(r'="([^"]*)"', r.text)
    if not m or not m.group(1):
        raise DataError("新浪实时接口无数据")
    f = m.group(1).split(",")
    if len(f) < 32:
        raise DataError(f"新浪实时接口字段数 {len(f)} < 32")
    price = to_float(f[3])
    prev_close = to_float(f[2])
    if not price or not prev_close:
        raise DataError("新浪实时接口现价缺失")
    volume_shou = (to_float(f[8]) or 0) / 100      # 股→手
    amount_wan = (to_float(f[9]) or 0) / 10000     # 元→万元
    return {
        "code": code,
        "name": f[0],
        "price": price,
        "prev_close": prev_close,
        "open": to_float(f[1]),
        "change_pct": round((price - prev_close) / prev_close * 100, 2),
        "high": to_float(f[4]),
        "low": to_float(f[5]),
        "volume": volume_shou,
        "amount": amount_wan,
        "turnover": None,
        "pe_ttm": None,
        "mktcap_total": None,
        "mktcap_float": None,
        "pb": None,
        "high_52w": None,
        "low_52w": None,
        "time": f"{f[30]} {f[31]}" if len(f) > 31 else "",
        "is_trading": volume_shou > 0,
    }
