"""东方财富（备用①，目标机实测有波段性拦截 → curl_cffi + 域名轮换 + 上层重试）。
- 快照：push2 stock/get ；K线：push2his kline/get ；搜索：searchapi suggest
- 财务摘要：datacenter F10 主要财务数据
"""
from . import http_get, to_float
from ..errors import DataError

PUSH2 = ["push2.eastmoney.com", "92.push2.eastmoney.com"]
PUSH2HIS = ["push2his.eastmoney.com", "92.push2his.eastmoney.com", "23.push2his.eastmoney.com"]

_SNAPSHOT_FIELDS = ("f43,f44,f45,f46,f47,f48,f57,f58,f60,f107,f116,f117,"
                    "f162,f167,f168,f169,f170,f174,f175")


def _secid(market, code):
    return f"{'1' if market == 'sh' else '0'}.{code}"


def _get_rotating(domains, path, params):
    last = None
    for dom in domains:
        try:
            r = http_get(f"https://{dom}{path}", params=params)
            return r.json()
        except Exception as e:  # 连接被断/超时 → 换域名
            last = e
    raise DataError(f"东财接口全部域名失败: {last}")


def _div(v, n):
    return v / n if v is not None else None


def fetch_snapshot(market, code):
    d = _get_rotating(PUSH2, "/api/qt/stock/get",
                      {"secid": _secid(market, code), "fields": _SNAPSHOT_FIELDS})
    data = d.get("data")
    if not data:
        raise DataError("东财快照无数据")
    g = lambda k: to_float(data.get(k)) if data.get(k) != "-" else None
    price = _div(g("f43"), 100)
    if price is None:
        raise DataError("东财快照现价缺失")
    prev_close = _div(g("f60"), 100)
    change_pct = _div(g("f170"), 100)
    return {
        "code": str(data.get("f57", code)),
        "name": data.get("f58", ""),
        "price": price,
        "prev_close": prev_close,
        "open": _div(g("f46"), 100),
        "change_pct": change_pct,
        "high": _div(g("f44"), 100),
        "low": _div(g("f45"), 100),
        "volume": g("f47"),                    # 手
        "amount": _div(g("f48"), 10000),       # 元→万元
        "turnover": _div(g("f168"), 100),
        "pe_ttm": _div(g("f162"), 100),
        "mktcap_total": _div(g("f116"), 1e8),  # 元→亿
        "mktcap_float": _div(g("f117"), 1e8),
        "pb": _div(g("f167"), 100),
        "high_52w": _div(g("f174"), 100),
        "low_52w": _div(g("f175"), 100),
        "time": "",
        "is_trading": True,
    }


# period -> eastmoney klt: day=101; minute=1/5/15/30/60
PERIOD_TO_KLT = {
    "day": 101, "daily": 101, "d": 101, "101": 101,
    "1": 1, "5": 5, "15": 15, "30": 30, "60": 60,
}


def normalize_period(period="day"):
    """Normalize period to day|1|5|15|30|60."""
    p = str(period or "day").strip().lower()
    if p in ("day", "daily", "d", "101"):
        return "day"
    if p in PERIOD_TO_KLT:
        return p
    raise DataError(
        "unsupported kline period=%r (day/1/5/15/30/60)" % (period,)
    )


def period_to_klt(period="day"):
    p = normalize_period(period)
    return int(PERIOD_TO_KLT["day" if p == "day" else p])


def _normalize_bar_date(raw, klt):
    """Minute bars keep ISO-like datetime; day bars keep YYYY-MM-DD."""
    s = str(raw or "").strip().replace("/", "-")
    if int(klt) == 101:
        return s[:10] if len(s) >= 10 else s
    if " " in s:
        date, tm = s.split(" ", 1)
        tm = tm[:5] if len(tm) >= 5 else tm
        return "%sT%s" % (date, tm)
    if "T" in s:
        return s[:16] if len(s) >= 16 else s
    return s


def fetch_kline(market, code, count=320, period="day", klt=None):
    """K-line. period=day|1|5|15|30|60; or pass klt directly.

    Minute bar date is ISO-like (e.g. 2024-01-02T09:35) for strategy dedupe.
    """
    if klt is None:
        klt_val = period_to_klt(period)
    else:
        klt_val = int(klt)
    d = _get_rotating(PUSH2HIS, "/api/qt/stock/kline/get", {
        "secid": _secid(market, code), "klt": str(klt_val), "fqt": "1",
        "beg": "0", "end": "20500101", "lmt": str(count),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
    })
    rows = (d.get("data") or {}).get("klines")
    if not rows:
        raise DataError("东财K线无数据")
    klines = []
    for row in rows:
        parts = row.split(",")
        if len(parts) < 6:
            continue
        o, c, h, l, v = map(to_float, (parts[1], parts[2], parts[3], parts[4], parts[5]))
        if None in (o, c, h, l, v):
            continue
        klines.append({
            "date": _normalize_bar_date(parts[0], klt_val),
            "open": o, "close": c, "high": h, "low": l, "volume": v,
        })
    min_bars = 10 if klt_val != 101 else 30
    if len(klines) < min_bars:
        raise DataError("东财K线有效数据仅 %s 根（<%s）" % (len(klines), min_bars))
    return klines



def search(keyword):
    """suggest 搜索，返回 [{code, name, market}]，仅沪深A股。"""
    r = http_get("https://searchapi.eastmoney.com/api/suggest/get",
                 params={"input": keyword, "type": "14", "count": "10"})
    items = (r.json().get("QuotationCodeTable") or {}).get("Data") or []
    out = []
    for it in items:
        sec_type = it.get("SecurityTypeName", "")
        market_id = str(it.get("MktNum", ""))
        if sec_type not in ("沪A", "深A"):
            continue
        out.append({
            "code": it.get("Code", ""),
            "name": it.get("Name", ""),
            "market": "sh" if market_id == "1" or sec_type == "沪A" else "sz",
        })
    return out


def fetch_fund_flow(market, code):
    """实时资金流向（分钟级）：主力/超大单/大单/中单/小单净流入。"""
    PUSH2_DELAY = ["push2delay.eastmoney.com"]
    d = _get_rotating(PUSH2_DELAY, "/api/qt/stock/fflow/kline/get", {
        "secid": _secid(market, code),
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "klt": "1", "lmt": "0",
    })
    data = d.get("data") or {}
    rows = data.get("klines") or []
    if not rows:
        raise DataError("东财资金流向无数据")
    # 每行格式: "时间,主力净流入,小单净流入,中单净流入,大单净流入,超大单净流入"
    # 东财页面验证口径: 第 5 列为大单净流入, 第 6 列为超大单净流入。
    minutes = []
    for row in rows:
        p = row.split(",")
        if len(p) < 6:
            continue
        minutes.append({
            "time": p[0],
            "main_net": to_float(p[1]),       # 主力净流入
            "small_net": to_float(p[2]),       # 小单净流入（与主力互为对手方）
            "mid_net": to_float(p[3]),         # 中单净流入
            "big_net": to_float(p[4]),         # 大单净流入
            "super_net": to_float(p[5]),       # 超大单净流入
        })
    # 最新一条即为截至当前的累计值
    latest = minutes[-1] if minutes else {}
    return {
        "main_net": latest.get("main_net", 0),
        "super_net": latest.get("super_net", 0),
        "big_net": latest.get("big_net", 0),
        "mid_net": latest.get("mid_net", 0),
        "small_net": latest.get("small_net", 0),
        "minutes": minutes,
        "name": data.get("name", ""),
    }


def fetch_fund_flow_days(market, code):
    """日级资金流向：最近交易日的主力/超大单/大单净流入。"""
    PUSH2_DELAY = ["push2delay.eastmoney.com"]
    d = _get_rotating(PUSH2_DELAY, "/api/qt/stock/fflow/daykline/get", {
        "secid": _secid(market, code),
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "klt": "101", "lmt": "30",
    })
    data = d.get("data") or {}
    rows = data.get("klines") or []
    if not rows:
        raise DataError("东财日级资金流向无数据")
    # 日级格式: 日期,主力净流入,小单净流入,中单净流入,大单净流入,超大单净流入,
    #          主力净流入占比,小单占比,中单占比,超大单占比,大单占比,收盘价,涨跌幅,...
    days = []
    for row in rows:
        p = row.split(",")
        if len(p) < 12:
            continue
        days.append({
            "date": p[0],
            "main_net": to_float(p[1]),
            "small_net": to_float(p[2]),
            "mid_net": to_float(p[3]),
            "big_net": to_float(p[4]),
            "super_net": to_float(p[5]),
            "main_pct": to_float(p[6]),
            "small_pct": to_float(p[7]),
            "mid_pct": to_float(p[8]),
            "big_pct": to_float(p[9]),
            "super_pct": to_float(p[10]),
            "close": to_float(p[11]),
            "change_pct": to_float(p[12]),
        })
    return days


def fetch_finance(market, code):
    """F10 主要财务指标（最新报告期）。任何异常由编排层降级。"""
    secucode = f"{code}.{'SH' if market == 'sh' else 'SZ'}"
    r = http_get("https://datacenter.eastmoney.com/securities/api/data/v1/get", params={
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": "SECUCODE,REPORT_DATE,ROEJQ,TOTALOPERATEREVETZ,PARENTNETPROFITTZ,XSMLL",
        "filter": f'(SECUCODE="{secucode}")',
        "pageNumber": "1", "pageSize": "1",
        "sortTypes": "-1", "sortColumns": "REPORT_DATE",
        "source": "HSF10", "client": "PC",
    })
    rows = ((r.json().get("result") or {}).get("data")) or []
    if not rows:
        raise DataError("东财财务摘要无数据")
    row = rows[0]
    return {
        "roe": to_float(row.get("ROEJQ")),
        "revenue_yoy": to_float(row.get("TOTALOPERATEREVETZ")),
        "profit_yoy": to_float(row.get("PARENTNETPROFITTZ")),
        "gross_margin": to_float(row.get("XSMLL")),
        "report_date": str(row.get("REPORT_DATE", ""))[:10],
    }


# ---- 行业 PE 分位 ----
def _industry_candidates(raw):
    """从东财行业字符串拆出可匹配板块名（叶→根）。"""
    if not raw:
        return []
    s = str(raw).strip()
    if not s or s == "-":
        return []
    parts = []
    for sep in ("-", "—", "/", "＞", ">"):
        if sep in s:
            parts = [p.strip() for p in s.split(sep) if p.strip()]
            break
    out = []
    if parts:
        # 叶节点优先，再逐级向上
        for p in reversed(parts):
            if p not in out:
                out.append(p)
        joined = "-".join(parts)
        if joined not in out:
            out.append(joined)
    if s not in out:
        out.append(s)
    return out


def fetch_industry(market, code):
    """个股所属行业名称（东财 F10 公司概况）。失败抛 DataError。"""
    prefix = "SH" if market == "sh" else "SZ"
    r = http_get(
        "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax",
        params={"code": f"{prefix}{code}"},
        timeout=12,
    )
    d = r.json() if hasattr(r, "json") else {}
    jbzl = d.get("jbzl") or d.get("jbzlData") or {}
    if isinstance(jbzl, list) and jbzl:
        jbzl = jbzl[0] if isinstance(jbzl[0], dict) else {}
    if not isinstance(jbzl, dict):
        jbzl = {}

    # EM2016 例：食品饮料-饮料-白酒；INDUSTRYCSRC1 例：制造业-酒、饮料和精制茶制造业
    raw_em = jbzl.get("EM2016") or jbzl.get("em2016")
    raw_csrc = (
        jbzl.get("INDUSTRYCSRC1") or jbzl.get("INDUSTRYCSRC2023")
        or jbzl.get("INDUSTRYCSRC") or jbzl.get("sshy")
    )
    candidates = _industry_candidates(raw_em) + _industry_candidates(raw_csrc)
    # 去重保序
    seen, uniq = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    if not uniq:
        raise DataError("东财 F10 未返回所属行业")
    # display 用最细粒度（EM2016 叶或首候选）
    display = uniq[0]
    return {
        "industry": display,
        "industry_raw": raw_em or raw_csrc,
        "industry_candidates": uniq,
        "code": code,
        "market": market,
    }


def fetch_industry_board_map():
    """行业板块名称 → BK 代码（分页拉取）。"""
    mapping = {}
    for pn in range(1, 8):
        d = _get_rotating(PUSH2, "/api/qt/clist/get", {
            "fid": "f3", "po": "1", "pz": "100", "pn": str(pn), "np": "1",
            "fltt": "2", "invt": "2",
            "fs": "m:90+t:2",
            "fields": "f12,f14",
        })
        items = (d.get("data") or {}).get("diff") or []
        if not items:
            break
        for it in items:
            bk, name = it.get("f12"), it.get("f14")
            if bk and name:
                mapping[str(name).strip()] = str(bk).strip()
        if len(items) < 100:
            break
    if not mapping:
        raise DataError("东财行业板块列表为空")
    return mapping


def _resolve_bk(industry_name, board_map, candidates=None):
    """行业名模糊匹配 BK 代码。"""
    names = []
    if candidates:
        names.extend(candidates)
    if industry_name:
        names.append(industry_name)
        names.extend(_industry_candidates(industry_name))
    # 常见别名
    aliases = {
        "白酒": ["白酒", "酿酒行业", "酿酒"],
        "电池": ["电池", "能源金属", "锂电池"],
        "半导体": ["半导体", "芯片", "电子元件"],
    }
    expanded = []
    for n in names:
        expanded.append(n)
        for a in aliases.get(n, []):
            expanded.append(a)
    seen = set()
    for n in expanded:
        if not n or n in seen:
            continue
        seen.add(n)
        if n in board_map:
            return board_map[n]
        for name, bk in board_map.items():
            if n in name or name in n:
                return bk
    return None


def fetch_industry_peer_pes(bk_code, max_pages=5):
    """行业成份股 PE 列表（优先 TTM f115，其次动态 f9）。fltt=2 时为数值。"""
    if not bk_code:
        raise DataError("缺少行业板块代码")
    peers = []
    for pn in range(1, max_pages + 1):
        d = _get_rotating(PUSH2, "/api/qt/clist/get", {
            "fid": "f3", "po": "1", "pz": "100", "pn": str(pn), "np": "1",
            "fltt": "2", "invt": "2",
            "fs": f"b:{bk_code} f:!50",
            "fields": "f12,f14,f9,f115",
        })
        items = (d.get("data") or {}).get("diff") or []
        if not items:
            break
        for it in items:
            pe = to_float(it.get("f115"))
            if pe is None or pe <= 0:
                pe = to_float(it.get("f9"))
            if pe is not None and pe > 0:
                peers.append({
                    "code": it.get("f12", ""),
                    "name": it.get("f14", ""),
                    "pe": pe,
                })
        if len(items) < 100:
            break
    if len(peers) < 3:
        raise DataError(f"行业 {bk_code} 有效 PE 成份过少（{len(peers)}）")
    return peers


def compute_pe_percentile(pe, peer_pes):
    """经验分位：同业中 PE ≤ 本品 的占比，∈[0,1]。同业过少返回 None。"""
    if pe is None or pe <= 0:
        return None
    vals = [p for p in peer_pes if p is not None and p > 0]
    if len(vals) < 8:
        return None
    return sum(1 for p in vals if p <= pe) / len(vals)


# ---- 板块分析 ----
PUSH2_DELAY = ["push2delay.eastmoney.com"]

_BOARD_FIELDS = "f12,f14,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f104,f105"


def fetch_board_ranking(board_type="industry", count=20, sort="main_net"):
    """板块资金流向排行。
    board_type: 'industry'(行业) / 'concept'(概念)
    sort: 'main_net'(主力净流入) / 'change'(涨跌幅)
    """
    fs = "m:90+t:2" if board_type == "industry" else "m:90+t:3"
    fid = "f62" if sort == "main_net" else "f3"
    d = _get_rotating(PUSH2_DELAY, "/api/qt/clist/get", {
        "fid": fid, "po": "1", "pz": str(count), "pn": "1", "np": "1",
        "fltt": "2", "invt": "2", "fs": fs, "fields": _BOARD_FIELDS,
    })
    items = (d.get("data") or {}).get("diff") or []
    result = []
    for it in items:
        result.append({
            "code": it.get("f12", ""),
            "name": it.get("f14", ""),
            "change_pct": to_float(it.get("f3")),
            "main_net": to_float(it.get("f62")),
            "main_pct": to_float(it.get("f184")),
            "super_net": to_float(it.get("f66")),
            "super_pct": to_float(it.get("f69")),
            "big_net": to_float(it.get("f72")),
            "big_pct": to_float(it.get("f75")),
            "mid_net": to_float(it.get("f78")),
            "mid_pct": to_float(it.get("f81")),
            "small_net": to_float(it.get("f84")),
            "small_pct": to_float(it.get("f87")),
            "up_count": it.get("f104", 0),
            "down_count": it.get("f105", 0),
        })
    return result


def fetch_board_ranking_down(board_type="industry", count=20):
    """板块资金流出排行（净流出最多的板块）。"""
    fs = "m:90+t:2" if board_type == "industry" else "m:90+t:3"
    d = _get_rotating(PUSH2_DELAY, "/api/qt/clist/get", {
        "fid": "f62", "po": "0", "pz": str(count), "pn": "1", "np": "1",
        "fltt": "2", "invt": "2", "fs": fs, "fields": _BOARD_FIELDS,
    })
    items = (d.get("data") or {}).get("diff") or []
    result = []
    for it in items:
        result.append({
            "code": it.get("f12", ""),
            "name": it.get("f14", ""),
            "change_pct": to_float(it.get("f3")),
            "main_net": to_float(it.get("f62")),
            "main_pct": to_float(it.get("f184")),
            "up_count": it.get("f104", 0),
            "down_count": it.get("f105", 0),
        })
    return result


# ---- 公告 / 风险事件 ----
def fetch_announcements(market, code, count=8):
    """东财最近公告（免费接口）。返回 [{title, type, date, url?}...]；失败抛 DataError。"""
    # np-anotice-stock 常用接口
    last = None
    urls = [
        (
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            {
                "sr": "-1",
                "page_size": str(count),
                "page_index": "1",
                "ann_type": "A",
                "client_source": "web",
                "stock_list": str(code),
                "f_node": "0",
                "s_node": "0",
            },
        ),
        (
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            {
                "sortColumns": "NOTICE_DATE,SECURITY_CODE",
                "sortTypes": "-1,-1",
                "pageSize": str(count),
                "pageNumber": "1",
                "reportName": "RPT_PUBLIC_OP_NEWSTOCK",
                "columns": "ALL",
                "filter": "(SECURITY_CODE=\"%s\")" % code,
            },
        ),
    ]
    # Prefer anotice
    try:
        r = http_get(urls[0][0], params=urls[0][1], timeout=12)
        d = r.json() if hasattr(r, "json") else {}
        data = d.get("data") or {}
        items = data.get("list") or data.get("items") or []
        out = []
        for it in items[:count]:
            title = (
                it.get("title")
                or it.get("notice_title")
                or it.get("NOTICE_TITLE")
                or ""
            )
            if not title:
                continue
            typ = (
                it.get("columns")
                or it.get("notice_type")
                or it.get("ann_type")
                or it.get("type")
                or ""
            )
            if isinstance(typ, list):
                typ = ",".join(
                    str(x.get("column_name") or x) for x in typ if x
                )[:40]
            date = (
                str(it.get("notice_date") or it.get("display_time") or it.get("date") or "")
                [:19]
            )
            out.append({
                "title": str(title).strip(),
                "type": str(typ).strip() if typ else "公告",
                "date": date,
                "source": "eastmoney_ann",
            })
        if out:
            return out
        last = "anotice empty"
    except Exception as e:
        last = e

    # Fallback datacenter notice-like
    try:
        r = http_get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params={
                "param": '{"uid":"","keyword":"%s","type":["cmsArticleWebOld"],'
                         '"client":"web","clientType":"web","clientVersion":"curr",'
                         '"param":{"cmsArticleWebOld":{"searchScope":"default",'
                         '"sort":"default","pageIndex":1,"pageSize":%s}}}'
                         % (code, count),
            },
            timeout=12,
        )
        # may not be jsonp-clean; ignore
    except Exception:
        pass

    # F10 notice list
    try:
        prefix = "SH" if market == "sh" else "SZ"
        r = http_get(
            "https://emweb.securities.eastmoney.com/PC_HSF10/NewsBulletin/PageAjax",
            params={"code": "%s%s" % (prefix, code)},
            timeout=12,
        )
        d = r.json() if hasattr(r, "json") else {}
        rows = d.get("data") or d.get("list") or d.get("gsgg") or []
        if isinstance(rows, dict):
            rows = rows.get("data") or rows.get("list") or []
        out = []
        for it in (rows or [])[:count]:
            if not isinstance(it, dict):
                continue
            title = it.get("title") or it.get("NOTICE_TITLE") or it.get("Title") or ""
            if not title:
                continue
            out.append({
                "title": str(title).strip(),
                "type": str(it.get("type") or it.get("NOTICE_TYPE") or "公告"),
                "date": str(it.get("date") or it.get("NOTICE_DATE") or it.get("rDate") or "")[:19],
                "source": "eastmoney_f10",
            })
        if out:
            return out
    except Exception as e:
        last = e

    raise DataError("东财公告拉取失败: %s" % last)


def theme_hints_from_industry(industry_ctx, board_top_names=None):
    """从行业上下文/概念热榜拼题材提示字符串列表。"""
    hints = []
    if industry_ctx:
        ind = industry_ctx.get("industry") or industry_ctx.get("industry_raw")
        if ind:
            hints.append("所属行业：%s" % ind)
        pct = industry_ctx.get("pe_percentile")
        if pct is not None:
            try:
                hints.append("同业PE分位约 %.0f%%" % (float(pct) * 100))
            except Exception:
                pass
    for name in (board_top_names or [])[:3]:
        if name:
            hints.append("相关题材/板块：%s" % name)
    return hints
