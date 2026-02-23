"""
Binance WebSocket Feed

Connects to Binance's public ticker stream.
No authentication required.
"""

from typing import Optional
from .base import ExchangeFeed


class BinanceFeed(ExchangeFeed):
    """
    Binance ticker feed for any supported pair.

    URL: wss://stream.binance.us:9443/ws/{symbol}usdt@ticker
    No subscription message needed (direct stream URL).

    Supported symbols: BTC, ETH, SOL, XRP, etc.
    """

    def __init__(self, symbol: str = "BTC"):
        self.symbol = symbol.upper()
        super().__init__(f"binance_{self.symbol.lower()}" if symbol != "BTC" else "binance")

    def _get_url(self) -> str:
        # Use Binance.US for US users (binance.com returns HTTP 451)
        pair = f"{self.symbol.lower()}usdt"
        return f"wss://stream.binance.us:9443/ws/{pair}@ticker"

    def _get_subscribe_message(self) -> Optional[dict]:
        # Direct stream URL, no subscription needed
        return None

    def _parse_message(self, data: dict) -> Optional[float]:
        # Check if this is a ticker message
        if not isinstance(data, dict):
            return None

        # Binance ticker uses "c" for last price
        price_str = data.get("c")
        if price_str is None:
            return None

        try:
            price = float(price_str)

            # Also extract bid/ask if available
            bid_str = data.get("b")
            ask_str = data.get("a")
            if bid_str:
                self.bid = float(bid_str)
            if ask_str:
                self.ask = float(ask_str)

            return price
        except (ValueError, TypeError):
            return None
