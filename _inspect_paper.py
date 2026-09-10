import sys, pathlib, json, inspect
sys.path.insert(0, ".")
# find compare_run
for p in pathlib.Path("stocklib").rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "def compare_run" in t:
        print("FOUND", p)
        # print function
        lines = t.splitlines()
        for i,l in enumerate(lines):
            if "def compare_run" in l:
                print("\n".join(lines[i:i+80]))
                break
from stocklib.paper import webapi
data = webapi.list_runs()
print("--- runs count", data.get("count"))
if data.get("runs"):
    rid = data["runs"][0]["run_id"]
    print("sample run", json.dumps(data["runs"][0], ensure_ascii=False, default=str)[:600])
    r = webapi.get_run(rid)
    accts = r.get("accounts") or []
    print("accounts[0]", json.dumps(accts[0] if accts else {}, ensure_ascii=False, default=str)[:900])
    p = webapi.get_positions(rid)
    print("pos", json.dumps((p.get("positions") or [None])[0], ensure_ascii=False, default=str)[:600])
    t = webapi.get_trades(rid)
    print("trade", json.dumps((t.get("trades") or [None])[0], ensure_ascii=False, default=str)[:900])
    c = webapi.get_compare(rid)
    print("compare", json.dumps((c.get("compare") or [None])[0], ensure_ascii=False, default=str)[:900])
else:
    print("no runs")