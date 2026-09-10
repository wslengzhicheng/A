"""指数与市场整体数据源（扩展 eastmoney.py）。
支持获取三大指数快照和市场整体统计。
"""
import time
from datetime import datetime
from . import http_get, to_float
from ..errors import DataError
from ..cache import CN_TZ

# 主要指数代码映射
INDEX_CODES = {
    "sh000001": {"name": "上证指数", "market": "sh"},
    "sz399001": {"name": "深证成指", "market": "sz"},
    "sz399006": {"name": "创业板指", "market": "sz"},
    "sh000300": {"name": "沪深300", "market": "sh"},
}


def parse_index_symbol(symbol="sh000300"):
    """Parse sh000300 / 000300 / 沪深300 hint -> (market, code, name)."""
    s = str(symbol or "sh000300").strip().lower()
    if s in INDEX_CODES:
        meta = INDEX_CODES[s]
        return meta["market"], s[-6:], meta["name"]
    if s.isdigit() and len(s) == 6:
        # default shanghai for 000xxx indices
        key = "sh" + s if s.startswith("000") else ("sz" + s)
        if key in INDEX_CODES:
            meta = INDEX_CODES[key]
            return meta["market"], s, meta["name"]
        return ("sh" if s.startswith("000") else "sz"), s, s
    # bare code with prefix
    if len(s) >= 8 and s[:2] in ("sh", "sz"):
        code = s[2:8]
        key = s[:2] + code
        meta = INDEX_CODES.get(key) or {"name": key, "market": s[:2]}
        return meta["market"], code, meta.get("name") or key
    return "sh", "000300", "沪深300"


def fetch_index_kline(market, code, count=320, period="day"):
    """Index K-line via eastmoney (same shape as stock kline)."""
    from . import eastmoney
    return eastmoney.fetch_kline(market, code, count=count, period=period)


def _secid(market, code):
    """生成东财接口的secid格式"""
    return f"{'1' if market == 'sh' else '0'}.{code}"


def _get_rotating(domains, path, params):
    last = None
    for dom in domains:
        for attempt in range(3):
            try:
                r = http_get(f"https://{dom}{path}", params=params)
                return r.json()
            except Exception as e:
                last = e
                if attempt == 0:
                    time.sleep(0.8)
                elif attempt == 1:
                    time.sleep(1.8)
    raise DataError(f"东财接口全部域名失败: {last}")


def _fetch_market_items(page_size=100):
    """拉取全市场股票列表并按页合并。东财 clist 默认单页只有 100 条。"""
    domains = ["push2.eastmoney.com", "92.push2.eastmoney.com"]
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    fields = "f12,f14,f3,f104,f105,f62,f184,f152,f169,f2,f4,f5,f6"
    items = []
    total = None

    for pn in range(1, 200):
        data = _get_rotating(domains, "/api/qt/clist/get", {
            "fid": "f3",
            "po": "1",
            "pz": str(page_size),
            "pn": str(pn),
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fs": fs,
            "fields": fields,
        })
        page = (data.get("data") or {})
        page_items = page.get("diff") or []
        if total is None:
            total = page.get("total")
        if not page_items:
            break
        items.extend(page_items)
        if len(page_items) < page_size:
            break
        if total and len(items) >= int(total):
            break

    return items


def _load_akshare():
    import akshare as ak
    return ak


def fetch_index_snapshot(market, code):
    """
    获取指数实时快照
    参数:
        market: 'sh' 或 'sz'
        code: 指数代码，如 '000001'
    返回:
        Dict: 指数快照数据
    """
    PUSH2 = ["push2.eastmoney.com", "92.push2.eastmoney.com"]
    _SNAPSHOT_FIELDS = "f43,f44,f45,f46,f60,f170,f107,f162,f168,f174,f58"

    last_error = None
    for dom in PUSH2:
        try:
            r = http_get(f"https://{dom}/api/qt/stock/get", params={
                "secid": _secid(market, code),
                "fields": _SNAPSHOT_FIELDS
            })
            data = r.json().get("data")
            if not data:
                continue

            g = lambda k: to_float(data.get(k))
            price = g("f43")
            if price is None:
                continue

            return {
                "code": code,
                "name": data.get("f58", ""),
                "price": price / 100,
                "prev_close": g("f60") / 100,
                "open": g("f46") / 100,
                "high": g("f44") / 100,
                "low": g("f45") / 100,
                "change_pct": g("f170") / 100,
                "pe_ttm": g("f162") / 100,
                "amount": g("f168") / 10000,  # 亿元→万元
                "high_52w": g("f174") / 100,
                "volume": g("f107"),  # 成交量
            }
        except Exception as e:
            last_error = e
            continue

    raise DataError(f"指数快照获取失败: {code} ({last_error})")


def fetch_market_overview():
    """
    获取市场整体概况
    返回:
        Dict: 包含涨跌停统计、涨跌家数、市场总市值等
    """
    try:
        try:
            ak = _load_akshare()
            activity = ak.stock_market_activity_legu()
            stats = {str(row.item).strip(): to_float(row.value) for row in activity.itertuples(index=False)}

            up_count = int(stats.get("上涨") or 0)
            down_count = int(stats.get("下跌") or 0)
            flat_count = int(stats.get("平盘") or 0)
            halt_count = int(stats.get("停牌") or 0)
            total_stocks = up_count + down_count + flat_count + halt_count

            # 以“真实涨停/真实跌停”为主，保留原始涨停/跌停数据在扩展字段中
            limit_up_raw = int(stats.get("涨停") or 0)
            limit_down_raw = int(stats.get("跌停") or 0)
            real_limit_up = int(stats.get("真实涨停") or limit_up_raw)
            real_limit_down = int(stats.get("真实跌停") or limit_down_raw)

            total_market_cap = None
            try:
                cap_df = ak.macro_china_stock_market_cap()
                valid = cap_df.dropna(subset=["市价总值-上海", "市价总值-深圳"])
                if not valid.empty:
                    row = valid.iloc[0]
                    total_market_cap = (float(row["市价总值-上海"]) + float(row["市价总值-深圳"])) / 10000
            except Exception:
                total_market_cap = None

            return {
                "total_stocks": total_stocks,
                "up_count": up_count,
                "down_count": down_count,
                "flat_count": flat_count,
                "halt_count": halt_count,
                "limit_up": real_limit_up,
                "limit_down": real_limit_down,
                "limit_up_raw": limit_up_raw,
                "limit_down_raw": limit_down_raw,
                "avg_change_pct": None,
                "total_main_net": None,
                "total_market_cap": total_market_cap,
                "timestamp": int(time.time()),
                "source": "AkShare",
            }
        except Exception:
            pass

        items = _fetch_market_items(page_size=100)

        if not items:
            return {
                "total_stocks": 0,
                "up_count": 0,
                "down_count": 0,
                "flat_count": 0,
                "limit_up": 0,
                "limit_down": 0,
                "avg_change_pct": 0,
                "total_main_net": 0,
                "total_market_cap": 0,
                "timestamp": int(time.time())
            }

        # 统计涨跌停（±9.9%或10%）
        limit_up = sum(1 for it in items if (to_float(it.get("f3")) or 0) >= 9.9)
        limit_down = sum(1 for it in items if (to_float(it.get("f3")) or 0) <= -9.9)

        # 统计涨跌家数
        up_count = sum(to_float(it.get("f104")) or 0 for it in items)
        down_count = sum(to_float(it.get("f105")) or 0 for it in items)

        # 资金流向
        total_main_net = sum(to_float(it.get("f62")) or 0 for it in items)

        # 平均涨跌
        changes = [to_float(it.get("f3")) for it in items if it.get("f3") is not None]
        avg_change = sum(changes) / len(changes) if changes else 0

        # 总市值（万元→亿）
        total_market_cap = sum(to_float(it.get("f2")) or 0 for it in items) / 10000

        return {
            "total_stocks": len(items),
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": len(items) - up_count - down_count,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "avg_change_pct": round(avg_change, 2),
            "total_main_net": total_main_net,
            "total_market_cap": round(total_market_cap, 2),
            "timestamp": int(time.time()),
            "source": "Eastmoney",
        }
    except Exception as e:
        # 返回默认值而不是抛出异常
        return {
            "total_stocks": 0,
            "up_count": 0,
            "down_count": 0,
            "flat_count": 0,
            "limit_up": 0,
            "limit_down": 0,
            "avg_change_pct": 0,
            "total_main_net": 0,
            "total_market_cap": 0,
            "timestamp": int(time.time()),
            "error": str(e)
        }


def fetch_limit_stocks(direction="up", count=20):
    """
    获取涨跌停股票列表
    参数:
        direction: 'up' 或 'down'
        count: 返回数量
    返回:
        List[Dict]: 涨跌停股票列表
    """
    try:
        try:
            ak = _load_akshare()
            date = datetime.now(CN_TZ).strftime("%Y%m%d")
            if direction == "up":
                df = ak.stock_zt_pool_em(date=date)
            else:
                df = ak.stock_zt_pool_dtgc_em(date=date)

            if not df.empty:
                sort_col = "涨跌幅"
                df = df.sort_values(by=sort_col, ascending=(direction != "up"))
                stocks = []
                for _, row in df.head(count).iterrows():
                    stocks.append({
                        "code": str(row.get("代码", "")),
                        "name": str(row.get("名称", "")),
                        "price": to_float(row.get("最新价")),
                        "change_pct": to_float(row.get("涨跌幅")) or 0,
                        "open": None,
                        "high": None,
                        "low": None,
                        "main_net": to_float(row.get("封板资金")) if direction == "up" else to_float(row.get("封单资金")),
                        "up_count": None,
                        "down_count": None,
                        "is_limit": True,
                        "reason": "涨停" if direction == "up" else "跌停",
                        "industry": row.get("所属行业", ""),
                    })
                return stocks
        except Exception:
            pass

        # 兜底：先拉取全市场，再从全量里筛选涨跌停，避免单页 100 条导致遗漏。
        items = _fetch_market_items(page_size=100)

        stocks = []
        for it in items:
            change = to_float(it.get("f3")) or 0
            if (direction == "up" and change >= 9.8) or (direction == "down" and change <= -9.8):
                stocks.append({
                    "code": it.get("f12"),
                    "name": it.get("f14"),
                    "price": (to_float(it.get("f2")) or 0) / 100,
                    "change_pct": change,
                    "open": (to_float(it.get("f4")) or 0) / 100,
                    "high": (to_float(it.get("f5")) or 0) / 100,
                    "low": (to_float(it.get("f6")) or 0) / 100,
                    "main_net": it.get("f62"),
                    "up_count": to_float(it.get("f104")) or 0,
                    "down_count": to_float(it.get("f105")) or 0,
                    "is_limit": True,
                    "reason": "涨停" if direction == "up" else "跌停"
                })
                if len(stocks) >= count:
                    break

        return stocks
    except Exception as e:
        # 返回空列表而不是抛出异常
        return []