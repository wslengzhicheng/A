"""模拟盘：账户 / 撮合 / 流水 / 指标 / 引擎（P0–P3）。"""
from .account import Account, target_qty_for_pct
from .broker import BrokerSim, DEFAULT_FEES, calc_commission, calc_stamp_tax, max_affordable_lots
from .journal import PaperJournal, new_run_id, paper_root
from .metrics import (
    summarize, total_return, max_drawdown, trade_stats,
    equal_weight_benchmark_return, excess_return,
)
from .engine import PaperEngine, is_trading_session, compare_run
from .learning import learning_fields, snapshot_indicators, backfill_run_learning
from .universe import (
    load_universe_file,
    save_universe,
    default_universe_path,
    universe_from_picker,
    fetch_all_a_codes,
    coarse_screen_all_a,
    resolve_universe_spec,
    DEFAULT_PRECISE_LIMIT,
)
from .strategies_adapter import (
    make_strategy,
    collect_new_signals,
    filter_latest_bar_signals,
    signal_dedupe_key,
    list_strategy_ids,
)

__all__ = [
    "Account",
    "target_qty_for_pct",
    "BrokerSim",
    "DEFAULT_FEES",
    "calc_commission",
    "calc_stamp_tax",
    "max_affordable_lots",
    "PaperJournal",
    "new_run_id",
    "paper_root",
    "summarize",
    "total_return",
    "max_drawdown",
    "trade_stats",
    "equal_weight_benchmark_return",
    "excess_return",
    "PaperEngine",
    "is_trading_session",
    "compare_run",
    "make_strategy",
    "collect_new_signals",
    "filter_latest_bar_signals",
    "signal_dedupe_key",
    "list_strategy_ids",
    "learning_fields",
    "snapshot_indicators",
    "backfill_run_learning",
    "load_universe_file",
    "save_universe",
    "default_universe_path",
    "universe_from_picker",
    "fetch_all_a_codes",
    "coarse_screen_all_a",
    "resolve_universe_spec",
    "DEFAULT_PRECISE_LIMIT",
]
