from tdxhub.consts import MARKET_BJ
from tdxhub.consts import MARKET_SH
from tdxhub.consts import MARKET_SZ


def get_stock_market(symbol: str = "", string: bool = False):
    """Return the TDX market for a stock code without importing heavy helpers."""

    assert isinstance(symbol, str), "stock code need str type"

    market = "sh"
    if symbol.startswith(("sh", "sz", "SH", "SZ")):
        market = symbol[:2].lower()
    elif symbol.startswith(("50", "51", "60", "68", "90", "110", "113", "132", "204")):
        market = "sh"
    elif symbol.startswith(("00", "12", "13", "18", "15", "16", "18", "20", "30", "39", "115", "1318")):
        market = "sz"
    elif symbol.startswith(("5", "6", "9", "7")):
        market = "sh"
    elif symbol.startswith(("4", "8")):
        market = "bj"

    if string:
        return market
    if market == "sh":
        return MARKET_SH
    if market == "sz":
        return MARKET_SZ
    if market == "bj":
        return MARKET_BJ
    return market


def get_stock_markets(symbols=None):
    results = []

    assert isinstance(symbols, list), "stock code need list type"

    for symbol in symbols:
        results.append([get_stock_market(symbol, string=False), symbol.strip("sh").strip("sz")])

    return results
