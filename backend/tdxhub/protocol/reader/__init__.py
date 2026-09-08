from .block_reader import BlockReader
from .block_reader import CustomerBlockReader

__all__ = [
    "TdxNotAssignVipdocPathException",
    "TdxFileNotFoundException",
    "HistoryFinancialReader",
    "TdxExHqDailyBarReader",
    "CustomerBlockReader",
    "TdxLCMinBarReader",
    "TdxDailyBarReader",
    "TdxMinBarReader",
    "BlockReader",
    # "GBBQReader",
]


def __getattr__(name):
    if name in {
        "TdxDailyBarReader",
        "TdxFileNotFoundException",
        "TdxNotAssignVipdocPathException",
    }:
        from .daily_bar_reader import (
            TdxDailyBarReader,
            TdxFileNotFoundException,
            TdxNotAssignVipdocPathException,
        )

        values = {
            "TdxDailyBarReader": TdxDailyBarReader,
            "TdxFileNotFoundException": TdxFileNotFoundException,
            "TdxNotAssignVipdocPathException": TdxNotAssignVipdocPathException,
        }
        return values[name]
    if name == "TdxExHqDailyBarReader":
        from .exhq_daily_bar_reader import TdxExHqDailyBarReader

        return TdxExHqDailyBarReader
    if name == "HistoryFinancialReader":
        from .history_financial_reader import HistoryFinancialReader

        return HistoryFinancialReader
    if name == "TdxLCMinBarReader":
        from .lc_min_bar_reader import TdxLCMinBarReader

        return TdxLCMinBarReader
    if name == "TdxMinBarReader":
        from .min_bar_reader import TdxMinBarReader

        return TdxMinBarReader
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
