"""磁盘缓存（PLAN.md §3.4）：
- kline：按 代码+日期 键，当日全天有效；
- snapshot：交易时段内 TTL=600 秒，收盘后当日有效；
- finance：当日全天有效。
缓存文件为 JSON，含 fetched_at 时间戳；--fresh 由调用方绕过。
"""
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
SNAPSHOT_TTL = 600  # 秒，仅交易时段生效


def _now_cn():
    return datetime.now(CN_TZ)


def is_trading_hours(dt=None):
    """粗判A股交易时段（含集合竞价与尾盘）：交易日 9:15~15:05。周末排除，节假日不判（宁可多刷新）。"""
    dt = dt or _now_cn()
    if dt.weekday() >= 5:
        return False
    hm = dt.hour * 60 + dt.minute
    return 9 * 60 + 15 <= hm <= 15 * 60 + 5


def _path(kind, code):
    date = _now_cn().strftime("%Y%m%d")
    return os.path.join(CACHE_DIR, f"{kind}_{code}_{date}.json")


def get(kind, code):
    """命中返回 (data, fetched_at)，未命中/过期返回 None。"""
    p = _path(kind, code)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    fetched_at = obj.get("fetched_at", 0)
    if kind == "snapshot" and is_trading_hours() and time.time() - fetched_at > SNAPSHOT_TTL:
        return None  # 盘中快照过期，防止旧价被当实时
    return obj.get("data"), fetched_at


def put(kind, code, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = _path(kind, code) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": time.time(), "data": data}, f, ensure_ascii=False)
    os.replace(tmp, _path(kind, code))
