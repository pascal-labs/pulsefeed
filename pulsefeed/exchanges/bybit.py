"""
Bybit WebSocket Feed (v5)

Connects to Bybit's public spot ticker stream.
No authentication required.
"""

from typing import Optional
from .base import ExchangeFeed


class BybitFeed(ExchangeFeed):
    """
    Bybit v5 spot ticker feed for any supported pair.

    URL: wss://stream.bybit.com/v5/public/spot
    Supported symbols: BTC, ETH, SOL, XRP, etc.
    Push frequency: 50ms (fastest of all exchanges)
    """

    def __init__(self, symbol: str = "BTC"):
        self.symbol = symbol.upper()
        super().__init__(f"bybit_{self.symbol.lower()}" if symbol != "BTC" else "bybit")

    def _get_url(self) -> str:
        return "wss://stream.bybit.com/v5/public/spot"

    def _get_subscribe_message(self) -> Optional[dict]:
        ticker = f"tickers.{self.symbol}USDT"
        return {
            "op": "subscribe",
            "args": [ticker]
        }

    def _parse_message(self, data: dict) -> Optional[float]:
        if not isinstance(data, dict):
            return None

        # Check for tickers topic
        topic = data.get("topic", "")
        if not topic.startswith("tickers."):
            return None

        # Get data object
        ticker = data.get("data")
        if not isinstance(ticker, dict):
            return None

        # Bybit uses "lastPrice" for last trade price
        price_str = ticker.get("lastPrice")
        if price_str is None:
            return None

        try:
            price = float(price_str)

            # Extract bid/ask
            bid_str = ticker.get("bid1Price")
            ask_str = ticker.get("ask1Price")
            if bid_str:
                self.bid = float(bid_str)
            if ask_str:
                self.ask = float(ask_str)

            return price
        except (ValueError, TypeError):
            return None
