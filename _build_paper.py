# -*- coding: utf-8 -*-
from pathlib import Path
import re
STATIC = Path("static")
# Chinese via unicode escapes to avoid PS encoding issues
U = {
  "paper": "\u6a21\u62df\u76d8",
  "stock": "\u4e2a\u80a1\u5206\u6790",
  "compare": "\u6279\u91cf\u5bf9\u6bd4",
  "backtest": "\u56de\u6d4b\u9a8c\u8bc1",
  "market": "\u5e02\u573a\u6982\u89c8",
  "picker": "\u4e70\u5165\u673a\u4f1a",
  "runs": "\u8fd0\u884c\u5217\u8868",
  "refresh": "\u5237\u65b0",
  "tip": "\u542f\u52a8/\u7eed\u8dd1\u8bf7\u7528 CLI `stock.bat --paper start ...`\uff08Web \u53ea\u8bfb\uff09",
  "empty": "\u6682\u65e0\u6a21\u62df\u76d8\u8fd0\u884c\u3002\u8bf7\u7528 CLI `stock.bat --paper start ...` \u542f\u52a8\uff08Web \u53ea\u8bfb\uff09",
  "detail": "\u8fd0\u884c\u8be6\u60c5",
  "pos": "\u6301\u4ed3",
  "trades": "\u6210\u4ea4",
  "cmp": "\u7b56\u7565\u5bf9\u7167",
  "loading": "\u52a0\u8f7d\u4e2d...",
  "status": "\u72b6\u6001",
  "cash": "\u73b0\u91d1",
  "equity": "\u603b\u6743\u76ca",
  "pos_n": "\u6301\u4ed3\u6570",
  "init_cash": "\u521d\u59cb\u8d44\u91d1",
  "strategy": "\u7b56\u7565",
  "code": "\u4ee3\u7801",
  "qty": "\u6570\u91cf",
  "avail": "\u53ef\u7528",
  "cost": "\u6210\u672c",
  "price": "\u73b0\u4ef7",
  "mv": "\u5e02\u503c",
  "time": "\u65f6\u95f4",
  "day": "\u4ea4\u6613\u65e5",
  "side": "\u65b9\u5411",
  "fee": "\u8d39\u7528",
  "notional": "\u91d1\u989d",
  "ret": "\u603b\u6536\u76ca",
  "bench": "\u57fa\u51c6",
  "excess": "\u8d85\u989d",
  "mdd": "\u6700\u5927\u56de\u64a4",
  "win": "\u80dc\u7387",
  "no_pos": "\u6682\u65e0\u6301\u4ed3",
  "no_tr": "\u6682\u65e0\u6210\u4ea4",
  "no_cmp": "\u6682\u65e0\u7b56\u7565\u5bf9\u7167\u6570\u636e",
  "acct": "\u7b56\u7565\u8d26\u6237",
  "title": "\u6a21\u62df\u76d8 - A\u80a1\u5206\u6790",
}
parts = []
parts.append("""<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
<meta charset=\"UTF-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
<title>""" + U["title"] + """</title>
<style>
:root { --bg:#0f1923; --card:#1a2634; --border:#2a3a4a; --text:#e0e6ed; --text2:#8899aa; --red:#f5475b; --green:#2ec47c; --blue:#3b82f6; --gold:#f0b90b; }
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,\"Microsoft YaHei\",sans-serif; background:var(--bg); color:var(--text); min-height:100vh; }
.header { background:var(--card); border-bottom:1px solid var(--border); padding:16px 24px; display:flex; align-items:center; gap:16px; position:sticky; top:0; z-index:100; flex-wrap:wrap; }
.header h1 { font-size:18px; white-space:nowrap; color:var(--gold); }
.nav { display:flex; gap:8px; margin-right:12px; flex-wrap:wrap; }
.nav a { padding:6px 16px; border-radius:6px; font-size:14px; color:var(--text2); text-decoration:none; border:1px solid var(--border); transition:all .2s; }
.nav a:hover, .nav a.active { color:#fff; border-color:var(--blue); background:var(--blue); }
.container { max-width:1400px; margin:0 auto; padding:20px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:18px 22px; margin-bottom:16px; }
.card-title { font-size:15px; font-weight:600; margin-bottom:12px; color:var(--gold); display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.tip { font-size:12px; color:var(--text2); line-height:1.6; margin-top:4px; }
.btn { padding:8px 16px; border:none; border-radius:6px; font-size:13px; cursor:pointer; transition:all .2s; font-weight:500; }
.btn-primary { background:var(--blue); color:#fff; }
.btn-primary:hover { background:#2563eb; }
.stats-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; }
.stat-card { padding:16px; background:var(--bg); border-radius:8px; text-align:center; }
.stat-card .label { font-size:12px; color:var(--text2); margin-bottom:6px; }
.stat-card .value { font-size:20px; font-weight:700; word-break:break-all; }
.stat-card .sub { font-size:11px; color:var(--text2); margin-top:4px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:right; padding:10px; color:var(--text2); font-weight:500; border-bottom:2px solid var(--border); white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
td { text-align:right; padding:8px 10px; border-bottom:1px solid #1e2e3e; }
tr.clickable { cursor:pointer; }
tr.clickable:hover td { background:#15202b; }
tr.selected td { background:#1a2f4a; }
.up { color:var(--red); }
.down { color:var(--green); }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; border:1px solid var(--border); color:var(--text2); }
.badge.active { color:#fff; border-color:var(--green); background:rgba(46,196,124,.2); }
.error-box { display:none; background:#2d1b1b; border:1px solid #5c2020; border-radius:8px; padding:16px 20px; margin:0 0 16px; color:var(--red); }
.error-box.active { display:block; }
.empty { text-align:center; padding:36px 16px; color:var(--text2); }
.tabs { display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; }
.tab { padding:8px 14px; border-radius:6px; border:1px solid var(--border); background:transparent; color:var(--text2); cursor:pointer; font-size:13px; }
.tab.active { color:#fff; border-color:var(--blue); background:var(--blue); }
.panel { display:none; }
.panel.active { display:block; }
.muted { color:var(--text2); font-size:12px; }
.toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
</style>
</head>
<body>
""")
Path("_build_paper_part1.txt").write_text("PART1_OK", encoding="utf-8")
print("part1 defs ok")
parts.append("""
<div class=\"header\">
  <h1>""" + U["paper"] + """</h1>
  <div class=\"nav\">
    <a href=\"/\">""" + U["stock"] + """</a>
    <a href=\"/compare.html\">""" + U["compare"] + """</a>
    <a href=\"/backtest.html\">""" + U["backtest"] + """</a>
    <a href=\"/paper.html\" class=\"active\">""" + U["paper"] + """</a>
    <a href=\"/market_overview.html\">""" + U["market"] + """</a>
    <a href=\"/stock_picker.html\">""" + U["picker"] + """</a>
  </div>
</div>

<div class=\"container\">
  <div class=\"card\">
    <div class=\"card-title\">
      <span>""" + U["runs"] + """</span>
      <div class=\"toolbar\">
        <button class=\"btn btn-primary\" onclick=\"refreshAll()\">""" + U["refresh"] + """</button>
      </div>
    </div>
    <p class=\"tip\">""" + U["tip"] + """</p>
  </div>

  <div class=\"error-box\" id=\"errorBox\"></div>

  <div class=\"card\">
    <div class=\"card-title\">""" + U["paper"] + """ Runs</div>
    <div style=\"overflow-x:auto\">
      <table id=\"runsTable\">
        <thead>
          <tr>
            <th>run_id</th>
            <th>status</th>
            <th>strategies</th>
            <th>universe</th>
            <th>created_at</th>
          </tr>
        </thead>
        <tbody id=\"runsBody\">
          <tr><td colspan=\"5\" class=\"empty\">""" + U["loading"] + """</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div id=\"detailSection\" style=\"display:none\">
    <div class=\"card\">
      <div class=\"card-title\">
        <span>""" + U["detail"] + """ · <span id=\"selectedRunId\" class=\"muted\"></span></span>
        <span id=\"activeBadge\" class=\"badge\" style=\"display:none\">active</span>
      </div>
      <div class=\"stats-grid\" id=\"summaryGrid\"></div>
    </div>

    <div class=\"card\">
      <div class=\"tabs\">
        <button class=\"tab active\" data-tab=\"positions\" onclick=\"switchTab('positions')\">""" + U["pos"] + """</button>
        <button class=\"tab\" data-tab=\"trades\" onclick=\"switchTab('trades')\">""" + U["trades"] + """</button>
        <button class=\"tab\" data-tab=\"compare\" onclick=\"switchTab('compare')\">""" + U["cmp"] + """</button>
      </div>
      <div class=\"panel active\" id=\"panel-positions\">
        <div style=\"overflow-x:auto\"><table id=\"positionsTable\"></table></div>
      </div>
      <div class=\"panel\" id=\"panel-trades\">
        <div style=\"overflow-x:auto\"><table id=\"tradesTable\"></table></div>
      </div>
      <div class=\"panel\" id=\"panel-compare\">
        <div style=\"overflow-x:auto\"><table id=\"compareTable\"></table></div>
      </div>
    </div>
  </div>
</div>
""")
print("part2 appended marker")
LABELS = {
  "empty": U["empty"],
  "status": U["status"],
  "cash": U["cash"],
  "equity": U["equity"],
  "pos_n": U["pos_n"],
  "init_cash": U["init_cash"],
  "acct": U["acct"],
  "strategy": U["strategy"],
  "code": U["code"],
  "qty": U["qty"],
  "avail": U["avail"],
  "cost": U["cost"],
  "price": U["price"],
  "mv": U["mv"],
  "time": U["time"],
  "day": U["day"],
  "side": U["side"],
  "fee": U["fee"],
  "notional": U["notional"],
  "ret": U["ret"],
  "bench": U["bench"],
  "excess": U["excess"],
  "mdd": U["mdd"],
  "win": U["win"],
  "no_pos": U["no_pos"],
  "no_tr": U["no_tr"],
  "no_cmp": U["no_cmp"],
  "trades": U["trades"],
}
import json as _json
_lab = _json.dumps(LABELS, ensure_ascii=False)

parts.append("""
<script>
const LABELS = """ + _lab + """;
const $ = id => document.getElementById(id);
let selectedRunId = null;
let currentTab = 'positions';

function fmt(v, nd=2) {
  if (v == null || v === '') return '\\u2014';
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(nd) : String(v);
}
function fmtMoney(v) {
  if (v == null || v === '') return '\\u2014';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}
function fmtPct(v) {
  if (v == null || v === '') return '\\u2014';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  const pct = Math.abs(n) <= 2 ? n * 100 : n;
  return (pct > 0 ? '+' : '') + pct.toFixed(2) + '%';
}
function cls(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? 'up' : 'down';
}
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}
function joinList(v) {
  if (Array.isArray(v)) return v.length ? v.join(', ') : '\\u2014';
  if (v == null || v === '') return '\\u2014';
  return String(v);
}
function showError(msg) {
  const box = $('errorBox');
  box.textContent = '\\u274c ' + msg;
  box.classList.add('active');
}
function clearError() {
  const box = $('errorBox');
  box.textContent = '';
  box.classList.remove('active');
}
async function fetchJson(url) {
  const resp = await fetch(url);
  let data = null;
  try { data = await resp.json(); } catch (e) { data = null; }
  if (!resp.ok) {
    const err = (data && (data.error || data.message)) || ('HTTP ' + resp.status);
    throw new Error(err);
  }
  return data || {};
}
async function refreshAll() {
  clearError();
  try {
    await loadRuns();
    if (selectedRunId) await loadRunDetail(selectedRunId);
  } catch (e) {
    showError('refresh failed: ' + e.message);
  }
}
async function loadRuns() {
  const data = await fetchJson('/paper/runs');
  const runs = data.runs || [];
  const body = $('runsBody');
  if (!runs.length) {
    body.innerHTML = '<tr><td colspan=\"5\" class=\"empty\">' + esc(LABELS.empty) + '</td></tr>';
    $('detailSection').style.display = 'none';
    selectedRunId = null;
    return;
  }
  body.innerHTML = runs.map(r => {
    const rid = r.run_id || '';
    const sel = rid === selectedRunId ? ' selected' : '';
    const status = r.status || '\\u2014';
    const badge = r.is_active ? ' <span class=\"badge active\">active</span>' : '';
    return '<tr class=\"clickable' + sel + '\" data-run=\"' + esc(rid) + '\" onclick=\"selectRun(\\'' + esc(rid) + '\\')\">' +
      '<td>' + esc(rid) + badge + '</td>' +
      '<td><span class=\"badge\">' + esc(status) + '</span></td>' +
      '<td>' + esc(joinList(r.strategies)) + '</td>' +
      '<td>' + esc(joinList(r.universe)) + '</td>' +
      '<td>' + esc(r.created_at || '\\u2014') + '</td></tr>';
  }).join('');
  if (selectedRunId && !runs.some(r => r.run_id === selectedRunId)) {
    selectedRunId = null;
    $('detailSection').style.display = 'none';
  }
}
async function selectRun(runId) {
  selectedRunId = runId;
  clearError();
  Array.from(document.querySelectorAll('#runsBody tr')).forEach(tr => {
    tr.classList.toggle('selected', tr.getAttribute('data-run') === runId);
  });
  try { await loadRunDetail(runId); }
  catch (e) { showError('load run failed: ' + e.message); }
}
async function loadRunDetail(runId) {
  $('detailSection').style.display = '';
  $('selectedRunId').textContent = runId;
  const detail = await fetchJson('/paper/runs/' + encodeURIComponent(runId));
  renderSummary(detail);
  await loadTabData(runId, currentTab);
}
function renderSummary(detail) {
  const meta = detail.meta || {};
  const accounts = detail.accounts || [];
  const badge = $('activeBadge');
  if (detail.active) { badge.style.display = ''; badge.className = 'badge active'; badge.textContent = 'active'; }
  else { badge.style.display = 'none'; }
  let cash = null, equity = null, mv = null, posCount = 0;
  accounts.forEach(a => {
    if (a.cash != null) cash = (cash == null ? 0 : cash) + Number(a.cash);
    if (a.equity != null) equity = (equity == null ? 0 : equity) + Number(a.equity);
    if (a.market_value != null) mv = (mv == null ? 0 : mv) + Number(a.market_value);
    posCount += Number(a.positions_count || 0);
  });
  if (cash == null && detail.cash != null) cash = detail.cash;
  if (equity == null && detail.equity != null) equity = detail.equity;
  const cards = [
    { label: LABELS.status, value: meta.status || detail.status || '\\u2014', sub: joinList(meta.strategies || detail.strategies) },
    { label: LABELS.cash, value: fmtMoney(cash), sub: accounts.length ? (accounts.length + ' ' + LABELS.acct) : '' },
    { label: LABELS.equity, value: fmtMoney(equity), sub: mv != null ? (LABELS.mv + ' ' + fmtMoney(mv)) : '' },
    { label: LABELS.pos_n, value: String(posCount), sub: joinList(meta.universe || []) },
    { label: LABELS.init_cash, value: fmtMoney(meta.init_cash), sub: meta.created_at || '' },
  ];
  let html = cards.map(c => '<div class=\"stat-card\"><div class=\"label\">' + esc(c.label) + '</div><div class=\"value\">' + esc(c.value) + '</div><div class=\"sub\">' + esc(c.sub || '') + '</div></div>').join('');
  if (accounts.length > 1) {
    html += accounts.map(a => '<div class=\"stat-card\"><div class=\"label\">' + esc(a.strategy_id || 'strategy') + '</div><div class=\"value\">' + esc(fmtMoney(a.equity)) + '</div><div class=\"sub\">' + esc(LABELS.cash + ' ' + fmtMoney(a.cash)) + '</div></div>').join('');
  }
  $('summaryGrid').innerHTML = html;
}
function switchTab(name) {
  currentTab = name;
  document.querySelectorAll('.tab').forEach(btn => btn.classList.toggle('active', btn.getAttribute('data-tab') === name));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
  if (selectedRunId) loadTabData(selectedRunId, name).catch(e => showError('load failed: ' + e.message));
}
async function loadTabData(runId, tab) {
  if (tab === 'positions') {
    const data = await fetchJson('/paper/runs/' + encodeURIComponent(runId) + '/positions');
    renderPositions(data.positions || []);
  } else if (tab === 'trades') {
    const data = await fetchJson('/paper/runs/' + encodeURIComponent(runId) + '/trades');
    renderTrades(data.trades || data.fills || []);
  } else if (tab === 'compare') {
    const data = await fetchJson('/paper/runs/' + encodeURIComponent(runId) + '/compare');
    renderCompare(data.compare || data.rows || []);
  }
}
function renderPositions(rows) {
  const table = $('positionsTable');
  if (!rows.length) { table.innerHTML = '<tr><td class=\"empty\">' + esc(LABELS.no_pos) + '</td></tr>'; return; }
  let html = '<thead><tr><th>' + esc(LABELS.strategy) + '</th><th>' + esc(LABELS.code) + '</th><th>' + esc(LABELS.qty) + '</th><th>' + esc(LABELS.avail) + '</th><th>' + esc(LABELS.cost) + '</th><th>' + esc(LABELS.price) + '</th><th>' + esc(LABELS.mv) + '</th></tr></thead><tbody>';
  rows.forEach(p => {
    const qty = p.qty ?? p.quantity ?? p.shares;
    const avail = p.available_qty ?? p.available;
    const cost = p.avg_cost ?? p.cost ?? p.cost_price;
    const price = p.last_price ?? p.price;
    const mv = (qty != null && price != null && Number.isFinite(Number(qty)) && Number.isFinite(Number(price))) ? Number(qty) * Number(price) : null;
    html += '<tr><td>' + esc(p.strategy_id || '\\u2014') + '</td><td>' + esc(p.code || '\\u2014') + '</td><td>' + esc(qty ?? '\\u2014') + '</td><td>' + esc(avail ?? '\\u2014') + '</td><td>' + esc(fmt(cost, 4)) + '</td><td>' + esc(fmt(price, 3)) + '</td><td>' + esc(fmtMoney(mv)) + '</td></tr>';
  });
  table.innerHTML = html + '</tbody>';
}
function renderTrades(rows) {
  const table = $('tradesTable');
  if (!rows.length) { table.innerHTML = '<tr><td class=\"empty\">' + esc(LABELS.no_tr) + '</td></tr>'; return; }
  let html = '<thead><tr><th>' + esc(LABELS.time) + '</th><th>' + esc(LABELS.day) + '</th><th>' + esc(LABELS.strategy) + '</th><th>' + esc(LABELS.code) + '</th><th>' + esc(LABELS.side) + '</th><th>' + esc(LABELS.price) + '</th><th>' + esc(LABELS.qty) + '</th><th>' + esc(LABELS.fee) + '</th><th>' + esc(LABELS.notional) + '</th></tr></thead><tbody>';
  rows.forEach(t => {
    const side = (t.side || '').toLowerCase();
    const sideCls = side === 'buy' ? 'up' : (side === 'sell' ? 'down' : '');
    const fee = (Number(t.fee || 0) + Number(t.tax || 0)) || t.fee;
    html += '<tr><td>' + esc(t.ts || t.time || '\\u2014') + '</td><td>' + esc(t.asof_date || t.date || '\\u2014') + '</td><td>' + esc(t.strategy_id || '\\u2014') + '</td><td>' + esc(t.code || '\\u2014') + '</td><td class=\"' + sideCls + '\">' + esc(t.side || '\\u2014') + '</td><td>' + esc(fmt(t.price, 3)) + '</td><td>' + esc(t.qty ?? '\\u2014') + '</td><td>' + esc(fmt(fee, 2)) + '</td><td>' + esc(fmtMoney(t.notional)) + '</td></tr>';
  });
  table.innerHTML = html + '</tbody>';
}
function renderCompare(rows) {
  const table = $('compareTable');
  if (!rows.length) { table.innerHTML = '<tr><td class=\"empty\">' + esc(LABELS.no_cmp) + '</td></tr>'; return; }
  let html = '<thead><tr><th>' + esc(LABELS.strategy) + '</th><th>' + esc(LABELS.equity) + '</th><th>' + esc(LABELS.ret) + '</th><th>' + esc(LABELS.bench) + '</th><th>' + esc(LABELS.excess) + '</th><th>' + esc(LABELS.mdd) + '</th><th>' + esc(LABELS.win) + '</th><th>' + esc(LABELS.trades) + '</th><th>' + esc(LABELS.cash) + '</th><th>' + esc(LABELS.pos_n) + '</th></tr></thead><tbody>';
  rows.forEach(r => {
    const wr = r.win_rate == null ? '\\u2014' : (fmt(Number(r.win_rate) <= 1 ? Number(r.win_rate)*100 : r.win_rate, 1) + '%');
    html += '<tr><td>' + esc(r.strategy_id || '\\u2014') + '</td><td>' + esc(fmtMoney(r.equity)) + '</td><td class=\"' + cls(r.total_return) + '\">' + esc(fmtPct(r.total_return)) + '</td><td class=\"' + cls(r.benchmark_return) + '\">' + esc(fmtPct(r.benchmark_return)) + '</td><td class=\"' + cls(r.excess_return) + '\">' + esc(fmtPct(r.excess_return)) + '</td><td class=\"down\">' + esc(fmtPct(r.max_drawdown)) + '</td><td>' + esc(wr) + '</td><td>' + esc(r.trades ?? r.fill_count ?? '\\u2014') + '</td><td>' + esc(fmtMoney(r.cash)) + '</td><td>' + esc(r.positions ?? '\\u2014') + '</td></tr>';
  });
  table.innerHTML = html + '</tbody>';
}
refreshAll().catch(e => showError('load failed: ' + e.message));
</script>
</body>
</html>
""")

(STATIC / "paper.html").write_text("".join(parts), encoding="utf-8")
print("wrote paper.html", (STATIC / "paper.html").stat().st_size)