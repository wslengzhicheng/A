"""????????/???/???A????? universe?"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from stocklib.paper.journal import paper_root

CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_UNIVERSE_NAME = "universe.json"
DEFAULT_PRECISE_LIMIT = 400
A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
CLIST_FIELDS = "f12,f14,f2,f3,f5,f6,f8,f9,f20,f21"
_CODE_RE = re.compile(r"^(?:sh|sz|bj)?(\d{6})$", re.I)
_ST_RE = re.compile(r"(?:^|[^\w])\*?\s*ST", re.I)


def _now_ts() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%dT%H:%M:%S")


def default_universe_path(base_dir: Optional[str] = None) -> str:
    return os.path.join(paper_root(base_dir), DEFAULT_UNIVERSE_NAME)


def normalize_code(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.startswith("#"):
        return None
    s = s.replace(" ", "")
    m = _CODE_RE.match(s)
    if m:
        return m.group(1)
    m2 = re.match(r"^(\d{6})(?:\.(?:SH|SZ|BJ))?$", s, re.I)
    if m2:
        return m2.group(1)
    digits = re.findall(r"\d{6}", s)
    if len(digits) == 1:
        return digits[0]
    return None


def normalize_codes(items) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items or []:
        if isinstance(item, dict):
            code = normalize_code(item.get("code") or item.get("symbol") or item.get("ts_code"))
        else:
            code = normalize_code(item)
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def load_universe_file(path: str) -> List[str]:
    """?? universe ???JSON list / {codes:[...]} / ??????? / CSV?"""
    path = os.path.abspath(str(path))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"universe file not found: {path}")
    raw = open(path, "r", encoding="utf-8").read().strip()
    if not raw:
        return []
    if raw[0] in "[{":
        data = json.loads(raw)
        if isinstance(data, list):
            return normalize_codes(data)
        if isinstance(data, dict):
            codes = data.get("codes") or data.get("universe") or data.get("symbols") or []
            return normalize_codes(codes)
        raise ValueError(f"unsupported universe JSON shape in {path}")
    parts: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "," in line or "\t" in line or ";" in line:
            for tok in re.split(r"[,;\t]+", line):
                parts.append(tok)
        else:
            parts.append(line)
    return normalize_codes(parts)


def save_universe(
    codes,
    path: Optional[str] = None,
    *,
    base_dir: Optional[str] = None,
    source: str = "file",
    extra: Optional[dict] = None,
) -> dict:
    """?? universe.json??? cache/paper/universe.json???? payload?"""
    codes = normalize_codes(codes)
    path = path or default_universe_path(base_dir)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    payload: Dict[str, Any] = {
        "codes": codes,
        "count": len(codes),
        "source": source,
        "updated_at": _now_ts(),
    }
    if extra:
        payload["extra"] = extra
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def merge_universe(existing, incoming, *, mode: str = "replace") -> List[str]:
    mode = (mode or "replace").lower().strip()
    a = normalize_codes(existing)
    b = normalize_codes(incoming)
    if mode in ("append", "union"):
        return normalize_codes(a + b)
    return b


def universe_from_picker(*, limit: int = 50, fresh: bool = False) -> dict:
    """???????????????????????

    ?? {codes, opportunities_count, candidates_count, note}?
    ? opportunities ????? get_candidates ?????? limit ????
    """
    from stocklib.stock_picker import find_buy_opportunities, get_candidates

    limit = max(1, int(limit))
    codes: List[str] = []
    opps_n = 0
    cands_n = 0
    note = ""
    try:
        result = find_buy_opportunities(fresh=fresh) or {}
        opps = result.get("opportunities") or []
        opps_n = len(opps)
        codes = normalize_codes(opps)
        if not codes:
            cands = get_candidates() or []
            cands_n = len(cands)
            codes = normalize_codes(cands)
            note = "no opportunities; fell back to get_candidates"
        else:
            note = "from find_buy_opportunities"
    except Exception as e:
        try:
            cands = get_candidates() or []
            cands_n = len(cands)
            codes = normalize_codes(cands)
            note = f"picker failed ({e}); used get_candidates"
        except Exception as e2:
            raise RuntimeError(
                f"universe_from_picker failed: {e}; fallback also failed: {e2}"
            ) from e2
    codes = codes[:limit]
    return {
        "codes": codes,
        "opportunities_count": opps_n,
        "candidates_count": cands_n,
        "note": note,
        "limit": limit,
    }


def _to_float(v, default=0.0) -> float:
    try:
        if v is None or v == "-" or v == "":
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _is_st_name(name: str) -> bool:
    n = str(name or "").strip()
    if not n:
        return False
    if n.upper().startswith("ST") or n.upper().startswith("*ST"):
        return True
    return bool(_ST_RE.search(n))


def _market_of(code: str) -> str:
    c = str(code)
    if c.startswith(("5", "6", "9")):
        return "sh"
    if c.startswith(("4", "8")):
        return "bj"
    return "sz"


def _parse_clist_item(it: dict) -> Optional[dict]:
    if not isinstance(it, dict):
        return None
    code = normalize_code(it.get("f12") or it.get("code"))
    if not code:
        return None
    name = str(it.get("f14") or it.get("name") or "")
    price = _to_float(it.get("f2") if "f2" in it else it.get("price"), 0.0)
    # fltt=2: f3 is percent number (e.g. 1.23 => 1.23%); normalize to fraction
    if "change_pct" in it and it.get("f3") is None:
        change_pct = _to_float(it.get("change_pct"), 0.0)
    else:
        change_pct = _to_float(it.get("f3") if "f3" in it else it.get("change_pct"), 0.0) / 100.0
    amount = _to_float(it.get("f6") if "f6" in it else it.get("amount"), 0.0)
    volume = _to_float(it.get("f5") if "f5" in it else it.get("volume"), 0.0)
    return {
        "code": code,
        "name": name,
        "market": _market_of(code),
        "price": price,
        "change_pct": change_pct,
        "amount": amount,
        "volume": volume,
        "raw": it,
    }


def fetch_all_a_codes(
    *,
    http_get: Optional[Callable] = None,
    page_size: int = 100,
    max_pages: int = 200,
    min_raw: Optional[int] = None,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """?????? clist ? A ???????/???/??????

    ?? {codes, items, total, pages, source}?http_get ?????????
    """
    if http_get is None:
        from stocklib.sources import http_get as http_get

    page_size = max(1, min(int(page_size or 100), 100))
    max_pages = max(1, int(max_pages or 200))
    domains = ["push2delay.eastmoney.com", "push2.eastmoney.com", "92.push2.eastmoney.com"]
    items: List[dict] = []
    total = None
    pages = 0
    last_err = None

    import time as _time
    for pn in range(1, max_pages + 1):
        data = None
        for attempt in range(4):
            for dom in domains:
                try:
                    r = http_get(
                        f"https://{dom}/api/qt/clist/get",
                        params={
                            "fid": "f6",
                            "po": "1",
                            "pz": str(page_size),
                            "pn": str(pn),
                            "np": "1",
                            "fltt": "2",
                            "invt": "2",
                            "fs": A_SHARE_FS,
                            "fields": CLIST_FIELDS,
                        },
                    )
                    data = r.json() if hasattr(r, "json") else r
                    break
                except Exception as e:
                    last_err = e
                    continue
            if data is not None:
                break
            _time.sleep(0.6 * (attempt + 1))
        if data is None:
            # fallback: reuse index._fetch_market_items once if page 1 failed hard
            if not items and pn == 1:
                try:
                    from stocklib.sources import index as index_source
                    raw_items = index_source._fetch_market_items(page_size=page_size)
                    for it in raw_items or []:
                        parsed = _parse_clist_item(it)
                        if parsed:
                            items.append(parsed)
                    pages = max(1, (len(items) + page_size - 1) // page_size)
                    codes = normalize_codes(items)
                    return {
                        "codes": codes,
                        "items": items,
                        "total": len(items),
                        "pages": pages,
                        "source": "eastmoney_clist_index_fallback",
                        "updated_at": _now_ts(),
                    }
                except Exception as e2:
                    last_err = f"{last_err}; fallback={e2}"
            if items:
                break
            raise RuntimeError(f"fetch_all_a_codes failed: {last_err}")
        page = (data.get("data") or {}) if isinstance(data, dict) else {}
        page_items = page.get("diff") or []
        if total is None:
            try:
                total = int(page.get("total") or 0) or None
            except (TypeError, ValueError):
                total = None
        if not page_items:
            break
        pages += 1
        for it in page_items:
            parsed = _parse_clist_item(it)
            if parsed:
                items.append(parsed)
        if progress_cb:
            try:
                progress_cb({"phase": "clist", "page": pn, "got": len(items), "total": total})
            except Exception:
                pass
        if min_raw and len(items) >= int(min_raw):
            break
        if len(page_items) < page_size:
            break
        if total and len(items) >= int(total):
            break

    codes = normalize_codes(items)
    return {
        "codes": codes,
        "items": items,
        "total": total or len(items),
        "pages": pages,
        "source": "eastmoney_clist",
        "updated_at": _now_ts(),
    }


def coarse_screen_all_a(
    items,
    *,
    precise_limit: int = DEFAULT_PRECISE_LIMIT,
    min_amount: float = 0.0,
    min_price: float = 0.5,
    exclude_st: bool = True,
    exclude_codes: Optional[List[str]] = None,
) -> dict:
    """???? ST/??/????????????? top precise_limit?

    items ?? fetch_all_a_codes ? items???? clist dict ???
    """
    precise_limit = max(1, int(precise_limit or DEFAULT_PRECISE_LIMIT))
    ban = set(normalize_codes(exclude_codes or []))
    screened: List[dict] = []
    dropped = {"st": 0, "price": 0, "amount": 0, "dup": 0, "ban": 0, "bad": 0}
    seen = set()

    for raw in items or []:
        row = raw if isinstance(raw, dict) and "code" in raw and "amount" in raw else _parse_clist_item(raw if isinstance(raw, dict) else {})
        if not row:
            dropped["bad"] += 1
            continue
        code = row["code"]
        if code in seen:
            dropped["dup"] += 1
            continue
        if code in ban:
            dropped["ban"] += 1
            continue
        if exclude_st and _is_st_name(row.get("name") or ""):
            dropped["st"] += 1
            continue
        price = float(row.get("price") or 0)
        if price < float(min_price or 0):
            dropped["price"] += 1
            continue
        amount = float(row.get("amount") or 0)
        if amount <= float(min_amount or 0):
            dropped["amount"] += 1
            continue
        seen.add(code)
        screened.append(row)

    screened.sort(key=lambda r: float(r.get("amount") or 0), reverse=True)
    top = screened[:precise_limit]
    codes = [r["code"] for r in top]
    return {
        "codes": codes,
        "screened": top,
        "screened_count": len(screened),
        "precise_limit": precise_limit,
        "dropped": dropped,
        "source": "coarse_amount",
    }


def resolve_universe_spec(
    spec: Any = None,
    *,
    precise_limit: int = DEFAULT_PRECISE_LIMIT,
    http_get: Optional[Callable] = None,
    base_dir: Optional[str] = None,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """????????

    - None / \"all_a\" / \"market_race\" / {mode: all_a|market_race} ? ? A clist + ?? top N
    - list / CSV ??? / {codes:[...]} ? ?????
    - {file: path} / ?????? ? load_universe_file
    """
    precise_limit = max(1, int(precise_limit or DEFAULT_PRECISE_LIMIT))

    def _race() -> dict:
        # clist 已按成交额降序(fid=f6)；拉取足够页粗筛后即可截断，无需默认扫完全市再排序
        min_raw = max(int(precise_limit) * 3, int(precise_limit) + 50)
        pack = fetch_all_a_codes(
            http_get=http_get, progress_cb=progress_cb, min_raw=min_raw,
        )
        screened = coarse_screen_all_a(pack.get("items") or [], precise_limit=precise_limit)
        return {
            "codes": screened["codes"],
            "source": "market_race",
            "mode": "market_race",
            "precise_limit": precise_limit,
            "clist_total": pack.get("total"),
            "clist_pages": pack.get("pages"),
            "screened_count": screened.get("screened_count"),
            "dropped": screened.get("dropped"),
            "items_preview": (screened.get("screened") or [])[:20],
            "updated_at": _now_ts(),
        }

    if spec is None:
        return _race()

    if isinstance(spec, (list, tuple)):
        codes = normalize_codes(spec)
        return {"codes": codes, "source": "list", "mode": "list", "precise_limit": precise_limit}

    if isinstance(spec, str):
        s = spec.strip()
        low = s.lower()
        if low in ("all_a", "market_race", "full_a", "alla", "race"):
            return _race()
        if os.path.isfile(s):
            codes = load_universe_file(s)
            return {"codes": codes, "source": f"file:{s}", "mode": "file", "precise_limit": precise_limit}
        # CSV of codes
        if re.search(r"\d{6}", s):
            parts = re.split(r"[,;\s]+", s)
            codes = normalize_codes(parts)
            if codes:
                return {"codes": codes, "source": "csv", "mode": "list", "precise_limit": precise_limit}
        raise ValueError(f"unsupported universe spec string: {spec!r}")

    if isinstance(spec, dict):
        mode = str(spec.get("mode") or spec.get("universe") or "").strip().lower()
        if mode in ("all_a", "market_race", "full_a", "alla", "race") or spec.get("all_a") or spec.get("market_race"):
            pl = int(spec.get("precise_limit") or precise_limit)
            return resolve_universe_spec("market_race", precise_limit=pl, http_get=http_get, progress_cb=progress_cb)
        if spec.get("file") or spec.get("path") or spec.get("universe_file"):
            path = spec.get("file") or spec.get("path") or spec.get("universe_file")
            codes = load_universe_file(path)
            return {"codes": codes, "source": f"file:{path}", "mode": "file", "precise_limit": precise_limit}
        codes = normalize_codes(spec.get("codes") or spec.get("universe") or spec.get("symbols") or [])
        if codes:
            return {"codes": codes, "source": "dict", "mode": "list", "precise_limit": precise_limit}
        raise ValueError(f"unsupported universe spec dict keys: {list(spec.keys())}")

    raise TypeError(f"unsupported universe spec type: {type(spec)}")
