"""
OKX WebSocket Feed

Connects to OKX's public tickers channel.
No authentication required.
"""

from typing import Optional
from .base import ExchangeFeed


class OKXFeed(ExchangeFeed):
    """
    OKX ticker feed for any supported pair.

    URL: wss://ws.okx.com:8443/ws/v5/public
    Supported symbols: BTC, ETH, SOL, XRP, etc.
    """

    def __init__(self, symbol: str = "BTC"):
        self.symbol = symbol.upper()
        super().__init__(f"okx_{self.symbol.lower()}" if symbol != "BTC" else "okx")

    def _get_url(self) -> str:
        return "wss://ws.okx.com:8443/ws/v5/public"

    def _get_subscribe_message(self) -> Optional[dict]:
        inst_id = f"{self.symbol}-USDT"
        return {
            "op": "subscribe",
            "args": [{"channel": "tickers", "instId": inst_id}]
        }

    def _parse_message(self, data: dict) -> Optional[float]:
        if not isinstance(data, dict):
            return None

        # Check for data field (ticker updates have this)
        ticker_data = data.get("data")
        if not ticker_data or not isinstance(ticker_data, list):
            return None

        # First item in data array
        ticker = ticker_data[0] if ticker_data else None
        if not isinstance(ticker, dict):
            return None

        # OKX uses "last" for last trade price
        price_str = ticker.get("last")
        if price_str is None:
            return None

        try:
            price = float(price_str)

            # Extract bid/ask
            bid_str = ticker.get("bidPx")
            ask_str = ticker.get("askPx")
            if bid_str:
                self.bid = float(bid_str)
            if ask_str:
                self.ask = float(ask_str)

            return price
        except (ValueError, TypeError):
            return None
