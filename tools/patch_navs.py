# -*- coding: utf-8 -*-
from pathlib import Path
import re

ROOT = Path(r"F:\Git\Source\Repos\wslengzhicheng\A\A\static")

NAV_BY_FILE = {
    "index.html": "index",
    "compare.html": "compare",
    "backtest.html": "backtest",
    "paper.html": "paper",
    "watchlist.html": "watch",
    "market_overview.html": "overview",
    "market.html": "market",
    "stock_picker.html": "picker",
}

def make_nav(active: str) -> str:
    items = [
        ("/", "index", "个股分析"),
        ("/compare.html", "compare", "选股对比"),
        ("/backtest.html", "backtest", "回测验证"),
        ("/paper.html", "paper", "模拟盘"),
        ("/watchlist.html", "watch", "我的盯盘"),
        ("/market_overview.html", "overview", "市场概览"),
        ("/market.html", "market", "板块排行"),
        ("/stock_picker.html", "picker", "机会扫描"),
    ]
    parts = ['  <div class="nav">']
    for href, key, label in items:
        cls = ' class="active"' if key == active else ''
        parts.append(f'    <a href="{href}"{cls}>{label}</a>')
    parts.append('  </div>')
    return "\n".join(parts)

def replace_nav(html: str, active: str) -> str:
    nav = make_nav(active)
    # replace existing nav block
    new_html, n = re.subn(
        r'<div class="nav">[\s\S]*?</div>',
        nav,
        html,
        count=1,
    )
    if n:
        return new_html
    # insert after h1 inside header if possible
    new_html, n = re.subn(
        r'(<div class="header">[\s\S]*?<h1[^>]*>[\s\S]*?</h1>)',
        r'\1\n' + nav,
        html,
        count=1,
    )
    if n:
        return new_html
    raise RuntimeError('nav not found')

def main():
    for name, active in NAV_BY_FILE.items():
        p = ROOT / name
        if not p.exists():
            print('MISSING', name)
            continue
        t = p.read_text(encoding='utf-8')
        t2 = replace_nav(t, active)
        p.write_text(t2, encoding='utf-8')
        print('OK', name)

if __name__ == '__main__':
    main()
