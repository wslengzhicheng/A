"""取数编排层：股票识别/搜索 + 回退链（腾讯→东财→新浪→akshare）+ 缓存 + 重试退避。
全部 IO 汇聚于此；分析层只见统一数据模型。
"""
import re
import time

from . import cache
from .errors import DataError, NetworkError, NotFoundError
from .sources import aksource, eastmoney, sina, tencent, index

RETRY_BACKOFF = [0.5, 1.0]  # 每源重试2次的退避间隔（秒）

_BJ_PREFIX = ("43", "83", "87", "92", "4", "8")


def resolve(query):
    """名称或代码 → (market, code, name)。无匹配抛 NotFoundError。"""
    q = query.strip()
    m = re.fullmatch(r"(?:(sh|sz|SH|SZ)\.?)?(\d{6})(?:\.(sh|sz|SH|SZ))?", q)
    if m:
        code = m.group(2)
        market = _market_of(code, (m.group(1) or m.group(3) or "").lower())
        return market, code, None
    if re.fullmatch(r"\d+", q):
        raise NotFoundError(f"'{q}' 不是有效的6位股票代码")
    return _search_by_name(q)


def _market_of(code, hint):
    if hint in ("sh", "sz"):
        return hint
    if code.startswith(("60", "68")):
        return "sh"
    if code.startswith(("00", "30")):
        return "sz"
    if code.startswith(_BJ_PREFIX):
        raise NotFoundError(f"代码 {code} 属北交所/三板，本工具暂不支持（仅沪深A股）")
    raise NotFoundError(f"无法识别代码 {code} 的市场（支持沪深A股：60/68/00/30 开头）")


def _search_by_name(keyword):
    candidates, errors = [], []
    for name, fn in (("东财搜索", eastmoney.search), ("腾讯搜索", tencent.search)):
        try:
            candidates = fn(keyword)
            if candidates:
                break
        except Exception as e:
            errors.append((name, f"{type(e).__name__}: {e}"))
    if not candidates:
        if errors and len(errors) == 2:
            raise NetworkError("搜索接口全部失败", attempts=errors)
        raise NotFoundError(
            f"未找到与 '{keyword}' 匹配的沪深A股，请检查名称或直接使用6位代码查询")
    # 只保留可识别市场的沪深标的
    valid = [c for c in candidates
             if c["code"].startswith(("60", "68", "00", "30"))]
    if not valid:
        raise NotFoundError(f"'{keyword}' 匹配到的均非沪深A股，本工具暂不支持")
    chosen = valid[0]
    if len(valid) > 1:
        lines = "\n".join(f"    {c['market']}{c['code']}  {c['name']}" for c in valid[:8])
        print(f"[提示] '{keyword}' 匹配到多只股票：\n{lines}\n"
              f"[提示] 已自动选择 {chosen['name']}({chosen['code']})，如需其他请用代码精确指定。")
    return chosen["market"], chosen["code"], chosen["name"]


def _is_trading_time():
    """判断当前是否在交易时间内"""
    from datetime import datetime, time
    now = datetime.now()
    # 交易时间：9:30-11:30, 13:00-15:00（周一至周五）
    if now.weekday() >= 5:  # 周六、周日
        return False
    current_time = now.time()
    morning_start = time(9, 30)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)

    return (morning_start <= current_time <= morning_end or
            afternoon_start <= current_time <= afternoon_end)

def _get_cache_expiry(kind):
    """根据数据类型和交易时间获取缓存过期时间"""
    if kind == "snapshot":
        # 快照数据在交易时间需要更频繁更新
        return 3 if _is_trading_time() else 30
    elif kind == "kline":
        # K线数据变化不频繁，可以缓存更久
        return 300 if _is_trading_time() else 3600
    else:
        # 其他数据
        return 60 if _is_trading_time() else 300

def _try_chain(kind, chain, market, code, fresh, cache_code=None, **kw):
    """带缓存与回退链的取数。chain = [(源名, 函数), ...]；cache_code 可覆盖缓存键。"""
    ckey = cache_code if cache_code is not None else code
    if not fresh:
        hit = cache.get(kind, ckey)
        if hit is not None:
            data, fetched_at = hit
            age = int(time.time() - fetched_at)
            expiry = _get_cache_expiry(kind)

            if age < expiry:
                print(f"[缓存] {kind} 命中（{age}秒前获取，有效期{expiry}秒，--fresh 可强制刷新）")
                return data, "缓存"
            else:
                print(f"[缓存] {kind} 过期（{age}秒前获取 > 有效期{expiry}秒，重新获取）")
    attempts = []
    for src_name, fn in chain:
        for attempt, backoff in enumerate([0] + RETRY_BACKOFF):
            if backoff:
                time.sleep(backoff)
            try:
                data = fn(market, code, **kw)
                cache.put(kind, ckey, data)
                return data, src_name
            except DataError as e:
                attempts.append((src_name, str(e)))
                break  # 数据性错误换源，不重试同源
            except Exception as e:
                attempts.append((src_name, f"{type(e).__name__}: {e}"))
    raise NetworkError(f"{kind} 全部数据源失败", attempts=attempts)


def get_snapshot(market, code, fresh=False):
    chain = [("腾讯", tencent.fetch_snapshot),
             ("东财", eastmoney.fetch_snapshot),
             ("新浪", sina.fetch_snapshot)]
    return _try_chain("snapshot", chain, market, code, fresh)


def get_kline(market, code, count=320, fresh=False, period="day"):
    """K线。period=day|1|5|15|30|60；缓存键含 period（日K保持原 code 键兼容）。"""
    try:
        period_norm = eastmoney.normalize_period(period)
    except Exception:
        period_norm = str(period or "day").strip().lower() or "day"
        if period_norm in ("daily", "d", "101"):
            period_norm = "day"

    def _em(m, c, count=320, **_kw):
        return eastmoney.fetch_kline(m, c, count=count, period=period_norm)

    def _tx(m, c, count=320, **_kw):
        return tencent.fetch_kline(m, c, count=count, period=period_norm)

    def _ak(m, c, count=320, **_kw):
        return aksource.fetch_kline(m, c, count=count, period=period_norm)

    if period_norm == "day":
        chain = [("腾讯", _tx), ("东财", _em), ("akshare", _ak)]
        cache_code = code
    else:
        chain = [("东财", _em)]
        cache_code = "%s_p%s" % (code, period_norm)
    return _try_chain(
        "kline", chain, market, code, fresh, cache_code=cache_code, count=count
    )


def get_finance(market, code, fresh=False):
    """财务摘要，尽力而为：失败返回 (None, 原因) 而非抛错。"""
    try:
        data, src = _try_chain("finance", [("东财F10", eastmoney.fetch_finance)],
                               market, code, fresh)
        return data, src
    except NetworkError as e:
        return None, "；".join(f"{s}: {m[:60]}" for s, m in e.attempts) or "未知原因"


def get_industry_context(market, code, pe=None, fresh=False):
    """行业 + 同业 PE 分位。失败返回 (None, 原因)，不抛错。

    返回 data 形如：
      {industry, bk, peer_count, pe_percentile, peer_pes}
    """
    cache_key = code
    if not fresh:
        hit = cache.get("industry", cache_key)
        if hit is not None:
            data, _ = hit
            # 若调用方给了新 PE，用缓存 peer 列表重算分位
            if pe is not None and data and data.get("peer_pes"):
                pct = eastmoney.compute_pe_percentile(pe, data["peer_pes"])
                data = dict(data, pe_percentile=pct)
            return data, "缓存"
    try:
        meta = eastmoney.fetch_industry(market, code)
        industry = meta["industry"]
        board_hit = cache.get("industry_boards", "all")
        if board_hit is not None and not fresh:
            board_map, _ = board_hit
        else:
            board_map = eastmoney.fetch_industry_board_map()
            cache.put("industry_boards", "all", board_map)
        bk = eastmoney._resolve_bk(
            industry, board_map, candidates=meta.get("industry_candidates"))
        if not bk:
            return None, f"未匹配到行业板块代码（{industry}）"
        # 按板块缓存同业 PE，减轻限流
        peers_hit = cache.get("industry_peers", bk)
        if peers_hit is not None and not fresh:
            peers, _ = peers_hit
        else:
            last_err = None
            peers = None
            for attempt in range(3):
                try:
                    if attempt:
                        time.sleep(0.8 * attempt)
                    peers = eastmoney.fetch_industry_peer_pes(bk)
                    break
                except Exception as e:
                    last_err = e
            if peers is None:
                raise last_err or DataError("同业 PE 拉取失败")
            cache.put("industry_peers", bk, peers)
        peer_pes = [p["pe"] for p in peers]
        use_pe = pe
        if use_pe is None:
            for p in peers:
                if p.get("code") == code:
                    use_pe = p["pe"]
                    break
        pct = eastmoney.compute_pe_percentile(use_pe, peer_pes)
        data = {
            "industry": industry,
            "industry_raw": meta.get("industry_raw"),
            "bk": bk,
            "peer_count": len(peer_pes),
            "peer_pes": peer_pes,
            "pe_percentile": pct,
        }
        cache.put("industry", cache_key, data)
        return data, "东财"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def get_fund_flow(market, code):
    """实时资金流向（分钟级），尽力而为。"""
    try:
        data = eastmoney.fetch_fund_flow(market, code)
        return data, "东财"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def get_fund_flow_days(market, code):
    """日级资金流向（近30日），尽力而为。"""
    try:
        data = eastmoney.fetch_fund_flow_days(market, code)
        return data, "东财"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def get_index_snapshot(market, code, fresh=False):
    """
    获取指数快照
    参数:
        market: 'sh' 或 'sz'
        code: 指数代码（不含前缀），如 '000001'
        fresh: 是否强制刷新
    返回: (data, source)
    """
    cache_key = f"index_{market}{code}"
    if not fresh:
        hit = cache.get("index", cache_key)
        if hit is not None:
            data, fetched_at = hit
            age = int(time.time() - fetched_at)
            print(f"[缓存] 指数快照命中（{age}秒前获取）")
            return data, "缓存"

    try:
        data = index.fetch_index_snapshot(market, code)
        cache.put("index", cache_key, data)
        return data, "东财"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def get_market_overview(fresh=False):
    """
    获取市场整体概况
    参数:
        fresh: 是否强制刷新
    返回: (data, source)
    """
    cache_key = "market_overview"
    if not fresh:
        hit = cache.get("market", cache_key)
        if hit is not None:
            data, fetched_at = hit
            age = int(time.time() - fetched_at)
            print(f"[缓存] 市场概况命中（{age}秒前获取）")
            return data, "缓存"

    try:
        data = index.fetch_market_overview()
        cache.put("market", cache_key, data)
        return data, "东财"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def get_limit_stocks(direction="up", count=20, fresh=False):
    """
    获取涨跌停股票列表
    参数:
        direction: 'up' 或 'down'
        count: 返回数量
        fresh: 是否强制刷新
    返回: (data, source)
    """
    cache_key = f"limit_{direction}_{count}"
    if not fresh:
        hit = cache.get("market", cache_key)
        if hit is not None:
            data, fetched_at = hit
            age = int(time.time() - fetched_at)
            print(f"[缓存] {direction}股票命中（{age}秒前获取）")
            return data, "缓存"

    try:
        data = index.fetch_limit_stocks(direction, count)
        cache.put("market", cache_key, data)
        return data, "东财"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def get_index_kline(symbol="sh000300", count=320, fresh=False, period="day"):
    """指数K线。symbol 如 sh000300。"""
    market, code, _name = index.parse_index_symbol(symbol)
    cache_code = "idx_%s_%s" % (market + code, period)
    try:
        period_norm = eastmoney.normalize_period(period)
    except Exception:
        period_norm = "day"

    def _fn(m, c, count=320, **_kw):
        return index.fetch_index_kline(m, c, count=count, period=period_norm)

    return _try_chain(
        "index_kline", [("东财指数", _fn)], market, code, fresh,
        cache_code=cache_code, count=count,
    )


def resolve_index_symbol(symbol="sh000300"):
    return index.parse_index_symbol(symbol)


def get_announcements(market, code, count=8, fresh=False):
    """最近公告，尽力而为：失败返回 ([], 原因)。"""
    cache_key = "%s_ann" % code
    if not fresh:
        hit = cache.get("announcements", cache_key)
        if hit is not None:
            data, fetched_at = hit
            age = int(time.time() - fetched_at)
            if age < 1800:
                return data, "缓存"
    try:
        data = eastmoney.fetch_announcements(market, code, count=count)
        cache.put("announcements", cache_key, data)
        return data, "东财"
    except Exception as e:
        return [], "%s: %s" % (type(e).__name__, e)


def build_risk_events(market, code, *, result=None, industry_ctx=None, fresh=False):
    """组装 risk_events：优先真实公告标题/类型 + 题材，再回退波动/notes。"""
    risks = []
    anns, _src = get_announcements(market, code, count=5, fresh=fresh)
    for a in anns[:3]:
        title = (a.get("title") or "").strip()
        typ = (a.get("type") or "公告").strip()
        date = (a.get("date") or "")[:10]
        if not title:
            continue
        prefix = "[%s]" % typ if typ else "[公告]"
        if date:
            risks.append("%s %s %s" % (prefix, date, title))
        else:
            risks.append("%s %s" % (prefix, title))

    themes = eastmoney.theme_hints_from_industry(industry_ctx or (result or {}).get("industry_ctx"))
    for h in themes[:2]:
        if h not in risks:
            risks.append(h)

    result = result or {}
    for n in (result.get("notes") or []):
        if any(k in str(n) for k in ("停牌", "异常", "风险", "亏损", "退市", "警示")):
            s = str(n)
            if s not in risks:
                risks.append(s)
    grade = str(result.get("grade") or "")
    if any(k in grade for k in ("偏弱", "很弱", "谨慎", "回避", "弱势")):
        s = "综合等级「%s」，注意回撤与追高风险" % grade
        if s not in risks:
            risks.append(s)
    vol = result.get("volatility")
    try:
        if vol is not None:
            v = float(vol)
            ratio = v / 100.0 if v > 1 else v
            if ratio >= 0.04:
                s = "波动偏高（约 %.1f%%），题材/情绪扰动可能较大" % (ratio * 100)
                # keep similar to prior wording
                s = "波动偏高（约 %.1f%%），题材/情绪扰动可能较大" % (ratio * 100)
                # actually prior used {:.1%} which is fine
                s = "波动偏高（约 %s），题材/情绪扰动可能较大" % ("{:.1%}".format(ratio))
                if s not in risks:
                    risks.append(s)
    except Exception:
        pass
    if not risks:
        risks.append("暂无公告摘要（占位）：请另行核对半年报/监管与异常波动公告")
    return risks[:4]
