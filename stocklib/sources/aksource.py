"""akshare 第四级回退（PLAN.md §3.3）。
akshare 内部用普通 requests 直连东财，在目标机被间歇拦截，故仅作最后兜底；
导入时给 requests.get/post 打浏览器 UA 补丁以提高通过率。惰性导入（akshare 导入需数秒）。
"""
from ..errors import DataError

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_patched = False


def _akshare():
    global _patched
    import requests
    if not _patched:
        for name in ("get", "post"):
            orig = getattr(requests, name)

            def wrap(url, *a, _o=orig, **kw):
                h = kw.get("headers") or {}
                h.setdefault("User-Agent", _UA)
                kw["headers"] = h
                return _o(url, *a, **kw)

            setattr(requests, name, wrap)
        _patched = True
    import akshare
    return akshare


def fetch_kline(market, code, count=320, period="day", **kwargs):
    _p = str(period or "day").strip().lower()
    if _p not in ("day", "daily", "d", "101", ""):
        raise DataError("akshare kline day-only, period=%r" % (period,))
    ak = _akshare()
    df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
    if df is None or df.empty:
        raise DataError("akshare K线无数据")
    df = df.tail(count)
    cols = {"日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume"}
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise DataError(f"akshare K线缺列: {missing}")
    klines = []
    for _, row in df.iterrows():
        klines.append({
            "date": str(row["日期"])[:10],
            "open": float(row["开盘"]), "close": float(row["收盘"]),
            "high": float(row["最高"]), "low": float(row["最低"]),
            "volume": float(row["成交量"]),
        })
    if len(klines) < 30:
        raise DataError(f"akshare K线有效数据仅 {len(klines)} 根（<30）")
    return klines
