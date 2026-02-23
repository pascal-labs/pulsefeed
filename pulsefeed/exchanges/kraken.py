"""
Kraken WebSocket Feed (v2)

Connects to Kraken's public ticker channel.
No authentication required.
"""

from typing import Optional
from .base import ExchangeFeed


class KrakenFeed(ExchangeFeed):
    """
    Kraken ticker feed (WebSocket API v2).

    URL: wss://ws.kraken.com/v2
    Supported symbols: BTC, ETH, SOL, XRP, etc.
    """

    def __init__(self, symbol: str = "BTC"):
        self.symbol = symbol.upper()
        super().__init__(f"kraken_{self.symbol.lower()}" if symbol != "BTC" else "kraken")

    def _get_url(self) -> str:
        return "wss://ws.kraken.com/v2"

    def _get_subscribe_message(self) -> Optional[dict]:
        # Kraken v2 uses standard symbols (BTC not XBT)
        pair = f"{self.symbol}/USD"
        return {
            "method": "subscribe",
            "params": {"channel": "ticker", "symbol": [pair]}
        }

    def _parse_message(self, data: dict) -> Optional[float]:
        if not isinstance(data, dict):
            return None

        # Check for ticker channel (handles both "snapshot" and "update" types)
        channel = data.get("channel")
        if channel != "ticker":
            return None

        # Get data array
        ticker_data = data.get("data")
        if not ticker_data or not isinstance(ticker_data, list):
            return None

        # First item in data array
        ticker = ticker_data[0] if ticker_data else None
        if not isinstance(ticker, dict):
            return None

        # Kraken v2 uses "last" for last trade price
        try:
            price = ticker.get("last")
            if price is None:
                return None

            price = float(price)

            # Extract bid/ask
            bid = ticker.get("bid")
            ask = ticker.get("ask")
            if bid is not None:
                self.bid = float(bid)
            if ask is not None:
                self.ask = float(ask)

            return price
        except (ValueError, TypeError):
            return None
