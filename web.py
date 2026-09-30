"""A股分析工具 Web 界面。
用法：
  stock.bat --web              启动 Web 服务（默认端口 8888）
  stock.bat --web --port 9000  指定端口
浏览器打开 http://localhost:8888 即可使用。
"""
import io
import re
import json
import math
import os
import sys
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stock import DAYS_DEFAULT, DAYS_MAX, DAYS_MIN, build_result
from stocklib import datasource
from stocklib import stock_picker
from stocklib import s2 as s2_engine
from stocklib import ocr_portfolio
from stocklib.stock_picker_simple import find_buy_opportunities_simple
from stocklib.errors import NetworkError, NotFoundError, StockError
from stocklib.sources import index as index_source
from stocklib.paper import webapi as paper_webapi

AANALYST_TOKEN = os.environ.get("AANALYST_TOKEN", "")
S2_REQUIRE_TOKEN_READ = True

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}


class StockHandler(SimpleHTTPRequestHandler):
    """处理 API 请求和静态文件。"""

    def log_message(self, format, *args):
        print(f"[web] {args[0]}" if args else "")

    # ---------- auth helpers ----------

    def _check_token(self):
        """Validate Bearer token or ?token= query param.  Returns True if OK."""
        if not AANALYST_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == AANALYST_TOKEN:
            return True
        parsed = urlparse(self.path)
        token_qs = parse_qs(parsed.query).get("token", [""])[0]
        if token_qs == AANALYST_TOKEN:
            return True
        return False

    def _reject_token(self):
        self._json_response(401, {"error": "需要有效的 AANALYST_TOKEN"})

    def _clean_json_data(self, data):
        """递归清理JSON数据中的Infinity和NaN值"""
        if isinstance(data, dict):
            cleaned = {}
            for k, v in data.items():
                # 特殊处理profit_factor
                if k == "profit_factor" and isinstance(v, (int, float)):
                    if math.isinf(v) or math.isnan(v):
                        cleaned[k] = None
                        continue
                cleaned[k] = self._clean_json_data(v)
            return cleaned
        elif isinstance(data, list):
            return [self._clean_json_data(item) for item in data]
        elif isinstance(data, (int, float)):
            if math.isinf(data) or math.isnan(data):
                return None
            else:
                return data
        else:
            return data

    def _json_response_safe(self, code, data):
        """安全的JSON响应，确保数据可以序列化且Content-Length正确。"""
        self._json_response(code, data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._handle_health(parsed)
        elif path == "/api/s2":
            self._handle_s2_single(parsed)
        elif path == "/api/s2/scan":
            self._handle_s2_scan(parsed)
        elif path == "/api/s2/diff":
            self._handle_s2_diff(parsed)
        elif path == "/api/analyze":
            self._handle_analyze(parsed)
        elif path == "/api/watchlist":
            self._handle_watchlist(parsed)
        elif path == "/api/kline":
            self._handle_kline(parsed)
        elif path == "/api/search":
            self._handle_search(parsed)
        elif path == "/api/stock_picker":
            self._handle_stock_picker(parsed)
        elif path == "/api/stock_picker_demo":
            self._handle_stock_picker_demo(parsed)
        elif path == "/api/fundflow":
            self._handle_fundflow(parsed)
        elif path == "/api/boards":
            self._handle_boards(parsed)
        elif path == "/api/market":
            self._handle_market(parsed)
        elif path == "/api/compare":
            self._handle_compare(parsed)
        elif path == "/api/backtest":
            self._handle_backtest(parsed)
        elif path == "/api/spread":
            self._handle_spread(parsed)
        elif path == "/paper/runs" or path.startswith("/paper/runs/"):
            self._handle_paper(parsed)
        else:
            self._serve_static(path)

    def _json_response(self, code, data):
        try:
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            cleaned_data = self._clean_json_data(data)
            body = json.dumps(cleaned_data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_all(body)

    def _write_all(self, payload):
        """确保响应体完整写入，避免大包在某些环境下被截断。"""
        try:
            self.connection.sendall(payload)
        except OSError:
            # 客户端中途断开时忽略写出异常，避免污染后续请求。
            return

    def _demo_stock_picker_result(self):
        """返回不依赖网络的演示筛选结果。"""
        return {
            "timestamp": int(__import__("time").time()),
            "candidates_count": 5,
            "analyzed_count": 5,
            "score_source": "demo",
            "score_note": "演示数据，非实盘评分",
            "opportunities": [
                {
                    "success": True,
                    "code": "300750",
                    "name": "宁德时代",
                    "market": "sz",
                    "price": 80.0,
                    "change_pct": -0.02,
                    "score": 72.0,
                    "reason": "基于跌势和资金流入的综合评分",
                    "has_buy_point": True,
                    "signals": [
                        {"name": "均线排列", "score": 1, "text": "短期均线向上"},
                        {"name": "MACD", "score": 1, "text": "MACD金叉"}
                    ],
                    "buy_reason": "综合评分72.0分，基于跌势和资金流入的综合评分，主力资金净流入2000万元",
                    "candidate_reason": "超跌反弹",
                    "bullish_count": 2,
                    "bearish_count": 0,
                    "main_net": 2000.0,
                },
                {
                    "success": True,
                    "code": "600036",
                    "name": "招商银行",
                    "market": "sh",
                    "price": 115.0,
                    "change_pct": 0.015,
                    "score": 69.0,
                    "reason": "基于温和上涨和资金流入的综合评分",
                    "has_buy_point": True,
                    "signals": [
                        {"name": "均线排列", "score": 1, "text": "短期均线向上"},
                        {"name": "MACD", "score": 1, "text": "MACD金叉"}
                    ],
                    "buy_reason": "综合评分69.0分，基于温和上涨和资金流入的综合评分，主力资金净流入4000万元",
                    "candidate_reason": "温和上涨",
                    "bullish_count": 2,
                    "bearish_count": 0,
                    "main_net": 4000.0,
                },
                {
                    "success": True,
                    "code": "600519",
                    "name": "贵州茅台",
                    "market": "sh",
                    "price": 1800.0,
                    "change_pct": 0.03,
                    "score": 78.0,
                    "reason": "基于强势上涨和估值水平的综合评分",
                    "has_buy_point": True,
                    "signals": [
                        {"name": "均线排列", "score": 2, "text": "均线多头排列"},
                        {"name": "MACD", "score": 1, "text": "MACD金叉"}
                    ],
                    "buy_reason": "综合评分78.0分，基于强势上涨和估值水平的综合评分，主力资金净流入5000万元",
                    "candidate_reason": "强势上涨",
                    "bullish_count": 3,
                    "bearish_count": 0,
                    "main_net": 5000.0,
                }
            ],
            "summary": {
                "total_analyzed": 5,
                "success_count": 3,
                "failed_count": 0,
                "opportunities_count": 3,
                "avg_score": 73.0,
            },
        }

    def _handle_analyze(self, parsed):
        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0].strip()
        if not query:
            self._json_response(400, {"error": "请输入股票代码或名称"})
            return
        days_str = params.get("days", [str(DAYS_DEFAULT)])[0]
        try:
            days = int(days_str)
            days = max(DAYS_MIN, min(DAYS_MAX, days))
        except ValueError:
            days = DAYS_DEFAULT
        fresh = params.get("fresh", ["0"])[0] == "1"

        # 捕获 print 输出作为 messages
        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            market, code, _ = datasource.resolve(query)
            klines, k_src = datasource.get_kline(market, code, count=DAYS_MAX, fresh=fresh)
            snapshot, s_src = datasource.get_snapshot(market, code, fresh=fresh)
            finance, f_src = datasource.get_finance(market, code, fresh=fresh)
            industry_ctx, i_src = datasource.get_industry_context(
                market, code, pe=(snapshot or {}).get("pe_ttm"), fresh=fresh)
            sources = f"K线:{k_src} 快照:{s_src}" + (f" 财务:{f_src}" if finance else "")
            if industry_ctx:
                sources += f" 行业:{i_src}"
            result = build_result(
                market, code, klines, snapshot, finance, days, sources,
                industry_ctx=industry_ctx)
            # 实时资金流向（尽力而为，不阻塞主分析）
            result["fund_flow"] = None
            result["fund_flow_days"] = None
            raw_messages = captured.getvalue().strip().splitlines() if captured.getvalue().strip() else []
            messages = [ln for ln in raw_messages if ln and ("HTTP/1." not in ln) and (not ln.startswith("127.0.0.1")) and (not ln.startswith("::1"))]
            result["messages"] = messages
            try:
                result["risk_events"] = datasource.build_risk_events(
                    market, code, result=result, industry_ctx=industry_ctx, fresh=fresh)
            except Exception:
                pass
            self._json_response_safe(200, result)
        except NotFoundError as e:
            self._json_response(404, {"error": str(e)})
        except NetworkError as e:
            detail = [f"{src}: {msg[:120]}" for src, msg in e.attempts]
            self._json_response(502, {"error": str(e), "attempts": detail})
        except StockError as e:
            self._json_response(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": f"服务端错误：{type(e).__name__}: {e}"})
        finally:
            sys.stdout = old_stdout

    def _handle_watchlist(self, parsed):
        """批量盯盘分析：?codes=000802,002422,002881&days=120&fresh=0"""
        params = parse_qs(parsed.query)
        codes_raw = params.get("codes", [""])[0].strip() or "000802,002422,002881"
        codes = [c.strip() for c in re.split(r"[,\s]+", codes_raw) if c.strip()]
        days_str = params.get("days", [str(DAYS_DEFAULT)])[0]
        try:
            days = max(DAYS_MIN, min(DAYS_MAX, int(days_str)))
        except ValueError:
            days = DAYS_DEFAULT
        fresh = params.get("fresh", ["0"])[0] == "1"
        items = []
        for query in codes:
            old_stdout = sys.stdout
            sys.stdout = captured = io.StringIO()
            try:
                market, code, _ = datasource.resolve(query)
                klines, k_src = datasource.get_kline(market, code, count=DAYS_MAX, fresh=fresh)
                snapshot, s_src = datasource.get_snapshot(market, code, fresh=fresh)
                finance, f_src = datasource.get_finance(market, code, fresh=fresh)
                industry_ctx, i_src = datasource.get_industry_context(
                    market, code, pe=(snapshot or {}).get("pe_ttm"), fresh=fresh)
                sources = f"K线:{k_src} 快照:{s_src}" + (f" 财务:{f_src}" if finance else "")
                if industry_ctx:
                    sources += f" 行业:{i_src}"
                result = build_result(
                    market, code, klines, snapshot, finance, days, sources,
                    industry_ctx=industry_ctx)
                result["fund_flow"] = None
                result["fund_flow_days"] = None
                raw_messages = captured.getvalue().strip().splitlines() if captured.getvalue().strip() else []
                result["messages"] = [
                    ln for ln in raw_messages
                    if ln and "HTTP/1." not in ln and not ln.startswith(("127.0.0.1", "::1"))
                ]
                # 真实公告/题材优先；失败不阻断分析
                try:
                    result["risk_events"] = datasource.build_risk_events(
                        market, code, result=result, industry_ctx=industry_ctx, fresh=fresh)
                except Exception:
                    result["risk_events"] = [
                        "暂无公告摘要（占位）：请另行核对半年报/监管与异常波动公告"
                    ]
                result["query"] = query
                result["code"] = code
                items.append({"ok": True, "code": code, "query": query, "data": result})
            except Exception as e:
                items.append({"ok": False, "query": query, "error": f"{type(e).__name__}: {e}"})
            finally:
                sys.stdout = old_stdout
        self._json_response_safe(200, {"codes": codes, "count": len(items), "items": items})

    def _handle_spread(self, parsed):
        """单票做差价建议（查询即出，复用分析链路）。"""
        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0].strip()
        if not query:
            self._json_response(400, {"error": "请输入股票代码或名称"})
            return
        days_str = params.get("days", [str(DAYS_DEFAULT)])[0]
        try:
            days = int(days_str)
            days = max(DAYS_MIN, min(DAYS_MAX, days))
        except ValueError:
            days = DAYS_DEFAULT
        fresh = params.get("fresh", ["0"])[0] == "1"
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            market, code, _ = datasource.resolve(query)
            klines, _ = datasource.get_kline(market, code, count=DAYS_MAX, fresh=fresh)
            snapshot, _ = datasource.get_snapshot(market, code, fresh=fresh)
            finance, _ = datasource.get_finance(market, code, fresh=fresh)
            result = build_result(market, code, klines, snapshot, finance, days, "spread")
            sp = result.get("spread") or {}
            self._json_response_safe(200, {
                "market": market,
                "code": result["snapshot"].get("code"),
                "name": result["snapshot"].get("name"),
                "spread": sp,
            })
        except NotFoundError as e:
            self._json_response(404, {"error": str(e)})
        except NetworkError as e:
            self._json_response(502, {"error": str(e)})
        except StockError as e:
            self._json_response(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": f"服务端错误：{type(e).__name__}: {e}"})
        finally:
            sys.stdout = old_stdout

    def _handle_kline(self, parsed):
        """独立K线API：为前端图表异步加载K线，减小主分析响应体积。"""
        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0].strip()
        if not query:
            self._json_response(400, {"error": "请输入股票代码或名称"})
            return

        days_str = params.get("days", [str(DAYS_DEFAULT)])[0]
        try:
            days = int(days_str)
            days = max(DAYS_MIN, min(DAYS_MAX, days))
        except ValueError:
            days = DAYS_DEFAULT
        chart_points = min(days, 160)
        fresh = params.get("fresh", ["0"])[0] == "1"
        period = (params.get("period", ["day"])[0] or "day").strip().lower()

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            market, code, _ = datasource.resolve(query)
            count = DAYS_MAX if period in ("day", "daily", "d", "101") else max(DAYS_MAX, 240)
            klines, src = datasource.get_kline(
                market, code, count=count, fresh=fresh, period=period)
            self._json_response(200, {
                "market": market,
                "code": code,
                "source": src,
                "period": period,
                "klines_data": klines[-chart_points:],
            })
        except NotFoundError as e:
            self._json_response(404, {"error": str(e)})
        except NetworkError as e:
            detail = [f"{src}: {msg[:120]}" for src, msg in e.attempts]
            self._json_response(502, {"error": str(e), "attempts": detail})
        except StockError as e:
            self._json_response(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": f"服务端错误：{type(e).__name__}: {e}"})
        finally:
            sys.stdout = old_stdout

    def _handle_stock_picker_demo(self, parsed):
        """股票筛选演示API：不依赖网络，使用示例数据"""
        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            result = self._demo_stock_picker_result()
            messages = captured.getvalue().strip().split("\n") if captured.getvalue().strip() else []
            result["messages"] = messages
            self._json_response_safe(200, result)
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": str(e)})
        finally:
            sys.stdout = old_stdout

    def _handle_stock_picker(self, parsed):
        """股票筛选API：收盘前买入机会分析"""
        params = parse_qs(parsed.query)
        fresh = params.get("fresh", ["0"])[0] == "1"
        mode = params.get("mode", ["full"])[0]  # "full" 或 "simple" 或 "demo"

        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            if mode == "demo":
                # 使用演示版，不依赖网络
                result = self._demo_stock_picker_result()
                result.setdefault("score_source", "demo")
                result.setdefault("score_note", "演示数据，非实盘评分")
                print("[筛选] 使用演示模式进行股票筛选")
            elif mode == "simple":
                # 使用简化版，避免网络问题
                result = find_buy_opportunities_simple(fresh=fresh)
                result.setdefault("score_source", "analyzer")
                result.setdefault(
                    "score_note",
                    "简化候选池；评分默认来自 analyzer，启发式降级时不可与完整分比较",
                )
                print("[筛选] 使用简化模式进行股票筛选")
            else:
                # 使用完整版（可能会因网络问题失败）
                try:
                    result = stock_picker.find_buy_opportunities(fresh=fresh)
                    result.setdefault("score_source", "analyzer")
                    print("[筛选] 使用完整版进行股票筛选")
                except Exception as full_err:
                    print(f"[筛选] 完整版失败，已降级简化版: {type(full_err).__name__}: {full_err}")
                    result = find_buy_opportunities_simple(fresh=fresh)
                    result["fallback_from"] = "full"
                    result.setdefault("score_source", result.get("score_source") or "simple")
                    result["score_note"] = (
                        "完整筛选失败后已降级简化路径；"
                        + result.get("score_note", "分数口径可能与 analyzer 完整路径不同")
                    )

            messages = captured.getvalue().strip().split("\n") if captured.getvalue().strip() else []
            result["messages"] = messages
            self._json_response_safe(200, result)
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": str(e)})
        finally:
            sys.stdout = old_stdout

    def _handle_search(self, parsed):
        params = parse_qs(parsed.query)
        keyword = params.get("q", [""])[0].strip()
        if not keyword:
            self._json_response(400, {"error": "请输入搜索关键词"})
            return
        try:
            from stocklib.sources import eastmoney, tencent
            candidates = []
            for name, fn in (("东财", eastmoney.search), ("腾讯", tencent.search)):
                try:
                    candidates = fn(keyword)
                    if candidates:
                        break
                except Exception:
                    pass
            valid = [c for c in candidates if c["code"].startswith(("60", "68", "00", "30"))]
            self._json_response(200, {"results": valid[:20]})
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_fundflow(self, parsed):
        """独立的资金流向 API，支持单独刷新。"""
        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0].strip()
        if not query:
            self._json_response(400, {"error": "请输入股票代码或名称"})
            return
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            market, code, _ = datasource.resolve(query)
            flow, f_src = datasource.get_fund_flow(market, code)
            flow_days, d_src = datasource.get_fund_flow_days(market, code)
            self._json_response(200, {
                "fund_flow": flow,
                "fund_flow_days": flow_days,
                "source": f_src,
            })
        except NotFoundError as e:
            self._json_response(404, {"error": str(e)})
        except Exception as e:
            self._json_response(500, {"error": str(e)})
        finally:
            sys.stdout = old_stdout

    def _handle_boards(self, parsed):
        """板块资金流向排行。"""
        params = parse_qs(parsed.query)
        board_type = params.get("type", ["industry"])[0]
        count = int(params.get("count", ["30"])[0])
        try:
            from stocklib.sources import eastmoney
            inflow = eastmoney.fetch_board_ranking(board_type, count=count, sort="main_net")
            top_change = eastmoney.fetch_board_ranking(board_type, count=10, sort="change")
            outflow = eastmoney.fetch_board_ranking_down(board_type, count=10)
            self._json_response(200, {
                "board_type": board_type,
                "inflow": inflow,
                "top_change": top_change,
                "outflow": outflow,
            })
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_market(self, parsed):
        """市场概况API（指数快照 + 市场统计 + 涨跌停）"""
        params = parse_qs(parsed.query)
        fresh = params.get("fresh", ["0"])[0] == "1"

        def _overview_invalid(data):
            if not isinstance(data, dict):
                return True
            if data.get("error"):
                return True
            # 坏缓存常见形态：全部为0或只有单页 100 条，无法反映当日市场状态
            total_stocks = data.get("total_stocks") or 0
            return total_stocks <= 0 or total_stocks <= 100

        try:
            # 获取市场整体概况
            overview, src = datasource.get_market_overview(fresh=fresh)
            warning = None
            if _overview_invalid(overview):
                # 缓存明显异常时自动强刷一次，避免页面长期展示 0 数据。
                overview2, src2 = datasource.get_market_overview(fresh=True)
                if not _overview_invalid(overview2):
                    overview, src = overview2, src2
                else:
                    warning = f"市场概况获取失败：{src2 if src2 else src}"
                    overview = overview2 if isinstance(overview2, dict) else overview

            if _overview_invalid(overview):
                overview = {
                    "total_stocks": 0,
                    "up_count": 0,
                    "down_count": 0,
                    "flat_count": 0,
                    "limit_up": 0,
                    "limit_down": 0,
                    "avg_change_pct": 0,
                    "total_main_net": 0,
                    "total_market_cap": 0,
                    "timestamp": None,
                }

            # 获取主要指数快照
            indices = []
            for code, info in index_source.INDEX_CODES.items():
                try:
                    idx_data, _ = datasource.get_index_snapshot(info["market"], code[2:], fresh=fresh)
                    indices.append(idx_data)
                except Exception as e:
                    indices.append({
                        "code": code,
                        "name": info["name"],
                        "error": str(e)[:50]
                    })

            # 获取涨跌停股票（各Top 10）
            limit_up, src_up = datasource.get_limit_stocks("up", count=10, fresh=fresh)
            limit_down, src_down = datasource.get_limit_stocks("down", count=10, fresh=fresh)

            # 若结果为空，尝试强制刷新一次，避免页面长期显示空表
            if not limit_up:
                limit_up2, src_up2 = datasource.get_limit_stocks("up", count=10, fresh=True)
                if limit_up2:
                    limit_up, src_up = limit_up2, src_up2
            if not limit_down:
                limit_down2, src_down2 = datasource.get_limit_stocks("down", count=10, fresh=True)
                if limit_down2:
                    limit_down, src_down = limit_down2, src_down2

            # 若概况中的涨跌停统计缺失，使用列表长度兜底
            if (overview.get("limit_up") or 0) == 0 and limit_up:
                overview["limit_up"] = len(limit_up)
            if (overview.get("limit_down") or 0) == 0 and limit_down:
                overview["limit_down"] = len(limit_down)

            self._json_response(200, {
                "overview": overview,
                "indices": indices,
                "limit_up": limit_up or [],
                "limit_down": limit_down or [],
                "source": src,
                "timestamp": overview.get("timestamp"),
                "warning": warning,
            })
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": str(e)})

    def _handle_compare(self, parsed):
        """多股票对比API"""
        params = parse_qs(parsed.query)
        codes = params.get("codes", [""])[0].strip()
        if not codes:
            self._json_response(400, {"error": "请输入股票代码，逗号分隔"})
            return

        codes_list = [c.strip() for c in codes.split(",") if c.strip()]
        if len(codes_list) > 20:
            self._json_response(400, {"error": "最多支持同时对比20只股票"})
            return

        days_str = params.get("days", [str(DAYS_DEFAULT)])[0]
        days = int(days_str) if days_str.isdigit() else DAYS_DEFAULT
        days = max(DAYS_MIN, min(DAYS_MAX, days))

        fresh = params.get("fresh", ["0"])[0] == "1"

        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            from stocklib import compare
            results = compare.batch_analyze(codes_list, days=days, fresh=fresh)
            summary = compare.compute_summary(results)
            matrix = compare.extract_comparison_matrix(results)

            messages = captured.getvalue().strip().split("\n") if captured.getvalue().strip() else []

            self._json_response(200, {
                "results": results,
                "summary": summary,
                "matrix": matrix,
                "messages": messages,
                "query": codes
            })
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": str(e)})
        finally:
            sys.stdout = old_stdout

    def _handle_backtest(self, parsed):
        """回测API"""
        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0].strip()
        if not query:
            self._json_response(400, {"error": "请输入股票代码或名称"})
            return

        strategy_type = params.get("strategy", ["chanlun"])[0]
        days_str = params.get("days", [str(DAYS_MAX)])[0]
        hold_days = int(params.get("hold_days", ["10"])[0])
        stop_loss = float(params.get("stop_loss", ["0.05"])[0])
        fresh = params.get("fresh", ["0"])[0] == "1"

        days = int(days_str) if days_str.isdigit() else DAYS_MAX
        days = max(DAYS_MIN, min(DAYS_MAX, days))

        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            from stocklib import backtest
            result = backtest.run_backtest(
                query,
                strategy_name=strategy_type,
                days=days,
                hold_days=hold_days,
                stop_loss_pct=stop_loss,
                fresh=fresh
            )
            messages = captured.getvalue().strip().split("\n") if captured.getvalue().strip() else []
            result["messages"] = messages

            # 清理JSON数据中的Infinity和NaN值
            cleaned_result = self._clean_json_data(result)
            self._json_response(200, cleaned_result)
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": str(e)})
        finally:
            sys.stdout = old_stdout


    def _handle_paper(self, parsed):
        """只读模拟盘 API：/paper/runs[/<id>[/positions|/trades|/compare]]。"""
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/paper/runs":
                self._json_response_safe(200, paper_webapi.list_runs())
                return
            # /paper/runs/<id> or /paper/runs/<id>/<action>
            parts = [p for p in path.split("/") if p]
            # parts: paper, runs, <id>, [action]
            if len(parts) < 3 or parts[0] != "paper" or parts[1] != "runs":
                self._json_response(404, {"error": "unknown paper route", "path": path})
                return
            run_id = parts[2]
            action = parts[3] if len(parts) > 3 else ""
            if len(parts) > 4:
                self._json_response(404, {"error": "unknown paper route", "path": path})
                return
            if action == "":
                data = paper_webapi.get_run(run_id)
            elif action == "positions":
                data = paper_webapi.get_positions(run_id)
            elif action == "trades":
                data = paper_webapi.get_trades(run_id)
            elif action == "compare":
                data = paper_webapi.get_compare(run_id)
            else:
                self._json_response(404, {"error": f"unknown paper action: {action}"})
                return
            if isinstance(data, dict) and data.get("error") == "run_not_found":
                self._json_response(404, data)
                return
            self._json_response_safe(200, data)
        except Exception as e:
            self._json_response(500, {"error": f"{type(e).__name__}: {e}"})


    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/s2/scan":
            self._handle_s2_scan_post(parsed)
            return
        if path == "/api/s2/diff":
            self._handle_s2_diff_post(parsed)
            return
        if path == "/api/portfolio/ocr":
            self._handle_portfolio_ocr(parsed)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        try:
            if path == "/paper/runs":
                data = paper_webapi.start_run(payload)
                code = 400 if data.get("error") else 200
                self._json_response_safe(code, data)
                return
            parts = [p for p in path.split("/") if p]
            if len(parts) == 4 and parts[0] == "paper" and parts[1] == "runs" and parts[3] == "stop":
                data = paper_webapi.stop_run(parts[2])
                code = 404 if data.get("error") == "run_not_found" else 200
                self._json_response_safe(code, data)
                return
            self._json_response(404, {"error": "unknown POST route", "path": path})
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": "%s: %s" % (type(e).__name__, e)})

    # ---------- health ----------

    def _handle_health(self, parsed):
        ds_ok = True
        ds_error = None
        try:
            from stocklib.sources import tencent
            tencent.fetch_snapshot("sh", "000001")
        except Exception as e:
            ds_ok = False
            ds_error = f"{type(e).__name__}: {e}"
        body = {
            "status": "ok" if ds_ok else "degraded",
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "datasource_ok": ds_ok,
        }
        if ds_error:
            body["datasource_error"] = ds_error
        try:
            from stocklib.paper import webapi as pw
            runs = pw.list_runs()
            active = [r for r in runs if r.get("status") == "running"]
            if active:
                body["paper_active_run_id"] = active[0].get("id")
        except Exception:
            pass
        body["token_configured"] = bool(AANALYST_TOKEN)
        self._json_response(200, body)

    # ---------- S2 helpers ----------

    def _fetch_klines_for_s2(self, code):
        """Resolve code → klines for S2. Returns (klines, market, name, {})."""
        market, resolved_code, name = datasource.resolve(code)
        klines, _src = datasource.get_kline(market, resolved_code, count=DAYS_MAX)
        if not name:
            try:
                snap, _ = datasource.get_snapshot(market, resolved_code)
                name = (snap or {}).get("name")
            except Exception:
                pass
        return klines, market, name or "", {}

    def _handle_s2_single(self, parsed):
        if S2_REQUIRE_TOKEN_READ and not self._check_token():
            self._reject_token()
            return
        params = parse_qs(parsed.query)
        q = params.get("q", [""])[0].strip()
        if not q:
            self._json_response(400, {"error": "请传入 q=股票代码"})
            return
        cost = params.get("cost", [None])[0]
        if cost is not None:
            try:
                cost = float(cost)
            except ValueError:
                cost = None
        try:
            klines, market, name, _ = self._fetch_klines_for_s2(q)
            result = s2_engine.compute_s2(klines, cost=cost)
            result["code"] = q
            result["name"] = name
            result["market"] = market
            self._json_response(200, result)
        except NotFoundError as e:
            self._json_response(404, {"error": str(e)})
        except NetworkError as e:
            self._json_response(502, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": f"{type(e).__name__}: {e}"})

    def _parse_scan_items(self, params=None, payload=None):
        """Parse scan items from query string or JSON body."""
        if payload and isinstance(payload, dict):
            if "positions" in payload:
                return payload["positions"]
            if "codes" in payload:
                codes = payload["codes"]
                if isinstance(codes, str):
                    codes = [c.strip() for c in re.split(r"[,\s]+", codes) if c.strip()]
                return [{"code": c} for c in codes]
        if params:
            codes_raw = params.get("codes", [""])[0].strip()
            if codes_raw:
                codes = [c.strip() for c in re.split(r"[,\s]+", codes_raw) if c.strip()]
                return [{"code": c} for c in codes]
        return []

    def _handle_s2_scan(self, parsed):
        if S2_REQUIRE_TOKEN_READ and not self._check_token():
            self._reject_token()
            return
        params = parse_qs(parsed.query)
        items = self._parse_scan_items(params=params)
        if not items:
            self._json_response(400, {"error": "请传入 codes 或 positions"})
            return
        try:
            results = s2_engine.scan(items, self._fetch_klines_for_s2)
            self._json_response(200, {"count": len(results), "results": results})
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": f"{type(e).__name__}: {e}"})

    def _handle_s2_scan_post(self, parsed):
        if not self._check_token():
            self._reject_token()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        items = self._parse_scan_items(payload=payload)
        if not items:
            self._json_response(400, {"error": "请传入 codes 或 positions"})
            return
        try:
            results = s2_engine.scan(items, self._fetch_klines_for_s2)
            self._json_response(200, {"count": len(results), "results": results})
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": f"{type(e).__name__}: {e}"})

    def _handle_s2_diff(self, parsed):
        self._json_response(200, {"info": "POST /api/s2/diff with {current, previous} arrays"})

    def _handle_s2_diff_post(self, parsed):
        if not self._check_token():
            self._reject_token()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        current = payload.get("current", [])
        previous = payload.get("previous", [])
        changes = s2_engine.diff_snapshots(current, previous)
        notify_count = sum(1 for c in changes if c.get("need_notify"))
        self._json_response(200, {
            "changes": changes,
            "notify_count": notify_count,
        })

    # ---------- OCR ----------

    def _handle_portfolio_ocr(self, parsed):
        if not self._check_token():
            self._reject_token()
            return
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._json_response(400, {"error": "需要 multipart/form-data 上传图片"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 20 * 1024 * 1024:
            self._json_response(400, {"error": "图片大小需在 20MB 以内"})
            return
        raw = self.rfile.read(length)
        image_bytes, filename = self._extract_multipart_file(raw, content_type)
        if not image_bytes:
            self._json_response(400, {"error": "未找到上传的图片文件"})
            return
        raw_text, engine, warnings = ocr_portfolio.ocr_image(image_bytes, filename)
        positions = ocr_portfolio.parse_positions(raw_text)
        self._json_response(200, {
            "positions": positions,
            "raw_text": raw_text[:2000] if raw_text else None,
            "engine": engine,
            "warnings": warnings,
        })

    def _extract_multipart_file(self, raw, content_type):
        """Minimal multipart parser to extract the first file's bytes and filename."""
        boundary_match = re.search(r"boundary=([^\s;]+)", content_type)
        if not boundary_match:
            return None, ""
        boundary = boundary_match.group(1).encode()
        parts = raw.split(b"--" + boundary)
        for part in parts:
            if b"Content-Disposition" not in part:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end < 0:
                continue
            header = part[:header_end].decode("utf-8", errors="replace")
            body = part[header_end + 4:]
            if body.endswith(b"\r\n"):
                body = body[:-2]
            if b"filename=" not in part[:header_end]:
                continue
            fn_match = re.search(r'filename="?([^";\r\n]+)"?', header)
            filename = fn_match.group(1) if fn_match else "upload.png"
            return body, filename
        return None, ""

    def _serve_static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        # 安全检查：防止目录遍历
        safe_path = os.path.normpath(path.lstrip("/"))
        if ".." in safe_path or safe_path.startswith(("/", "\\")):
            self.send_error(403)
            return
        full_path = os.path.join(STATIC_DIR, safe_path)
        if not os.path.isfile(full_path):
            self.send_error(404)
            return
        ext = os.path.splitext(full_path)[1].lower()
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        with open(full_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        if ext in (".html", ".js", ".css"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
        self.end_headers()
        self._write_all(body)


def main(port=8888):
    server = ThreadingHTTPServer(("0.0.0.0", port), StockHandler)
    print(f"[web] A股分析工具 Web 界面已启动")
    print(f"[web] 请在浏览器打开 http://localhost:{port}")
    print(f"[web] 按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[web] 服务已停止")
        server.server_close()


if __name__ == "__main__":
    port = 8888
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                print("[错误] --port 需要一个数字参数", file=sys.stderr)
                sys.exit(1)
    main(port)
