"""各数据源解析器：只负责 原始响应 → 统一数据模型（Kline/Snapshot/Finance），
不做缓存与回退（由 datasource.py 编排）。全部 HTTP 走 curl_cffi 模拟 Chrome TLS 指纹。
统一数据模型见 PLAN.md §4.2。
"""
import time

from curl_cffi import requests as _cr

_last_request_ts = 0.0
MIN_INTERVAL = 0.3  # 同进程连续请求最小间隔（秒），网络礼仪见 PLAN.md §3.4


def http_get(url, params=None, timeout=10, headers=None):
    """统一 GET：Chrome TLS 指纹 + 最小请求间隔。"""
    global _last_request_ts
    wait = MIN_INTERVAL - (time.time() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    r = _cr.get(url, params=params, timeout=timeout, headers=headers, impersonate="chrome")
    _last_request_ts = time.time()
    return r


def to_float(s):
    """字段转 float，空/无效返回 None。"""
    try:
        v = float(s)
        return v
    except (TypeError, ValueError):
        return None
