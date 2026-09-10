from pathlib import Path
import re
root = Path(r"F:\Git\Source\Repos\wslengzhicheng\A\A")

# ---- 1) stock.py session_state ----
stock = root / "stock.py"
st = stock.read_text(encoding="utf-8")
old = (
    "    last_date = klines[-1][\"date\"]\n"
    "    session_state = \"盘中数据\" if snap.get(\"is_trading\") and _is_today(last_date) else \"收盘数据\"\n"
    "    if not snap.get(\"is_trading\"):\n"
    "        notes.append(\"该股当前无成交（可能停牌），分析基于最近有效交易日数据\")\n"
    "        session_state = \"最近交易日（疑似停牌）\"\n"
)
new = (
    "    last_date = klines[-1][\"date\"]\n"
    "    # 快照交易中 + K线为今天 + 当前在交易时段，才称盘中\n"
    "    if snap.get(\"is_trading\") and _is_today(last_date) and _in_trading_session_now():\n"
    "        session_state = \"盘中数据\"\n"
    "    elif _is_today(last_date):\n"
    "        session_state = \"收盘数据（今日已收盘）\"\n"
    "    else:\n"
    "        session_state = \"收盘数据\"\n"
    "    if not snap.get(\"is_trading\"):\n"
    "        notes.append(\"该股当前无成交（可能停牌），分析基于最近有效交易日数据\")\n"
    "        session_state = \"最近交易日（疑似停牌）\"\n"
)
if old not in st:
    raise SystemExit("stock session block not found")
st = st.replace(old, new, 1)
helper = (
    "\n\ndef _in_trading_session_now():\n"
    "    \"\"\"A股常规连续竞价：工作日 09:30-11:30、13:00-15:00（上海时区）。\"\"\"\n"
    "    from datetime import datetime\n"
    "    from stocklib.cache import CN_TZ\n"
    "    now = datetime.now(CN_TZ)\n"
    "    if now.weekday() >= 5:\n"
    "        return False\n"
    "    hm = now.hour * 100 + now.minute\n"
    "    return (930 <= hm <= 1130) or (1300 <= hm <= 1500)\n"
)
if "def _in_trading_session_now" not in st:
    anchor = (
        "def _is_today(date_str):\n"
        "    from datetime import datetime\n"
        "    from stocklib.cache import CN_TZ\n"
        "    return date_str == datetime.now(CN_TZ).strftime(\"%Y-%m-%d\")\n"
    )
    if anchor not in st:
        raise SystemExit("_is_today anchor missing")
    st = st.replace(anchor, anchor + helper, 1)
stock.write_text(st, encoding="utf-8")
print("OK stock.py")

# ---- 2) web.py messages filter + watchlist ----
web = root / "web.py"
wt = web.read_text(encoding="utf-8")
lines = wt.splitlines(True)
out = []
i = 0
replaced_msg = False
while i < len(lines):
    if (not replaced_msg) and "messages = captured.getvalue()" in lines[i] and i + 1 < len(lines) and 'result["messages"]' in lines[i+1]:
        out.append('            raw_messages = captured.getvalue().strip().split("\\n") if captured.getvalue().strip() else []\n')
        out.append('            messages = [ln for ln in raw_messages if ln and ("HTTP/1." not in ln) and (not ln.startswith("127.0.0.1")) and (not ln.startswith("::1"))]\n')
        out.append('            result["messages"] = messages\n')
        i += 2
        replaced_msg = True
        continue
    out.append(lines[i]); i += 1
wt = "".join(out)
print("messages filter", replaced_msg)
if "/api/watchlist" not in wt:
    wt = wt.replace(
        '        elif path == "/api/analyze":\n            self._handle_analyze(parsed)\n',
        '        elif path == "/api/analyze":\n            self._handle_analyze(parsed)\n        elif path == "/api/watchlist":\n            self._handle_watchlist(parsed)\n',
        1)
    print("route added")
if "def _handle_watchlist" not in wt:
    handler_path = Path(r"F:\Git\Source\Repos\wslengzhicheng\A\A\tools\_watchlist_handler.pyfrag")
    # handler written separately below by installer
    frag = Path(__file__).with_name("_watchlist_handler.pyfrag")
    if not frag.exists():
        frag = root / "tools" / "_watchlist_handler.pyfrag"
    handler = frag.read_text(encoding="utf-8")
    anchor = "    def _handle_spread(self, parsed):"
    if anchor not in wt:
        raise SystemExit("spread anchor missing")
    wt = wt.replace(anchor, handler + "\n" + anchor, 1)
    if "import re\n" not in wt:
        wt = wt.replace("import io\n", "import io\nimport re\n", 1)
    print("handler inserted")
web.write_text(wt, encoding="utf-8")
print("OK web.py")

# ---- 3) rename picker ----
picker = root / "static" / "stock_picker.html"
pt = picker.read_text(encoding="utf-8").replace("买入机会", "机会扫描")
if "机会扫描 / 观察名单" not in pt:
    disc = '<div style="background:#2d2410;border:1px solid #6b5420;color:#f0d78c;border-radius:8px;padding:12px 16px;margin:16px 20px 0;font-size:13px;line-height:1.6">本页为<strong>机会扫描 / 观察名单</strong>，仅供研究参考，不构成任何投资建议。市场有风险，决策请独立判断。</div>'
    pt = pt.replace('<div class="container">', '<div class="container">\n  ' + disc, 1)
picker.write_text(pt, encoding="utf-8")
print("OK picker")
navp = root / "tools" / "patch_navs.py"
if navp.exists():
    navp.write_text(navp.read_text(encoding="utf-8").replace('"买入机会"', '"机会扫描"'), encoding="utf-8")
    print("OK nav label")
