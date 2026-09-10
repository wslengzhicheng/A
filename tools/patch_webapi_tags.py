from pathlib import Path
import re, json

path = Path(r"F:\Git\Source\Repos\wslengzhicheng\A\A\stocklib\paper\webapi.py")
text = path.read_text(encoding="utf-8")

helper = """

_NAME_HINTS = {
    "600000": "浦发银行",
    "600519": "贵州茅台",
    "000802": "北京文化",
    "002422": "科伦药业",
    "002881": "美格智能",
    "000938": "紫光股份",
}


def classify_run_tag(meta: dict, run_id: str) -> dict:
    status = str(meta.get("status") or "")
    note = str(meta.get("note") or "")
    strategies = meta.get("strategies") or []
    low = (run_id + " " + status + " " + note).lower()
    if status.startswith("demo") or "demo" in strategies or "demo" in low or "stub" in low:
        return {"tag": "demo", "tag_label": "演示", "is_demo": True}
    if "smoke" in low or "smoke" in run_id or run_id.endswith("_live") or (bool(meta.get("force_poll")) and status == "once_done"):
        return {"tag": "smoke", "tag_label": "实盘行情烟测", "is_demo": False}
    return {"tag": "formal", "tag_label": "正式模拟", "is_demo": False}


def enrich_universe(codes):
    out = []
    for c in codes or []:
        code = str(c)
        out.append({"code": code, "name": _NAME_HINTS.get(code)})
    return out
"""

if "classify_run_tag" not in text:
    text = text.replace(
        "from stocklib.paper.metrics import summarize\n",
        "from stocklib.paper.metrics import summarize\n" + helper,
        1,
    )

start = text.find("def list_runs(")
if start < 0:
    raise SystemExit("list_runs not found")
m = re.search(r"\ndef get_run\(", text)
if not m:
    raise SystemExit("get_run not found")
end = m.start() + 1

new_fn_lines = [
    'def list_runs(base_dir: Optional[str] = None) -> dict:',
    '    """GET /paper/runs"""',
    '    root = paper_root(base_dir)',
    '    run_ids = PaperJournal.list_runs(base_dir)',
    '    active = read_active(base_dir) or {}',
    '    known = set(run_ids)',
    '    ignored = None',
    '    if active and active.get("run_id") not in known:',
    '        ignored = {',
    '            "run_id": active.get("run_id"),',
    '            "status": "orphan_stub",',
    '            "note": "active points to missing run (test residue); ignored",',
    '            "ignored": True,',
    '        }',
    '        active = {}',
    '    runs: List[dict] = []',
    '    for rid in run_ids:',
    '        j = PaperJournal(rid, base_dir=base_dir)',
    '        try:',
    '            meta = j.read_meta()',
    '        except FileNotFoundError:',
    '            meta = {"run_id": rid, "status": "unknown"}',
    '        tag = classify_run_tag(meta, rid)',
    '        uni = meta.get("universe") or []',
    '        runs.append({',
    '            "run_id": rid,',
    '            "status": meta.get("status"),',
    '            "strategies": meta.get("strategies") or [],',
    '            "universe": uni,',
    '            "universe_named": enrich_universe(uni),',
    '            "init_cash": meta.get("init_cash"),',
    '            "created_at": meta.get("created_at"),',
    '            "updated_at": meta.get("last_poll") or meta.get("stopped_at") or meta.get("resumed_at"),',
    '            "is_active": bool(active) and active.get("run_id") == rid,',
    '            **tag,',
    '        })',
    '    runs.sort(key=lambda r: (1 if r.get("is_demo") else 0, r.get("created_at") or ""))',
    '    return {',
    '        "runs": runs,',
    '        "count": len(runs),',
    '        "active": active or None,',
    '        "active_ignored": ignored,',
    '        "root": root,',
    '    }',
    '',
    '',
]
new_fn = "\n".join(new_fn_lines) + "\n"
text = text[:start] + new_fn + text[end:]
path.write_text(text, encoding="utf-8")
print("OK webapi")

active_path = Path(r"F:\Git\Source\Repos\wslengzhicheng\A\A\cache\paper\_active.json")
if active_path.exists():
    a = json.loads(active_path.read_text(encoding="utf-8"))
    rid = a.get("run_id")
    if rid and not (active_path.parent / rid).is_dir():
        active_path.unlink()
        print("removed orphan _active.json", rid)
