"""
Gate.io Exchange WebSocket Feed

Connects to Gate.io's public ticker stream.
No authentication required.
"""

import time
from typing import Optional
from .base import ExchangeFeed


class GateIOFeed(ExchangeFeed):
    """
    Gate.io ticker feed for any supported pair.

    URL: wss://api.gateio.ws/ws/v4/
    Supported symbols: BTC, ETH, SOL, XRP, etc.

    Subscription message:
    {
        "time": 1234567890,
        "channel": "spot.tickers",
        "event": "subscribe",
        "payload": ["BTC_USDT"]
    }

    Ticker message format:
    {
        "time": 1234567890,
        "channel": "spot.tickers",
        "event": "update",
        "result": {
            "currency_pair": "BTC_USDT",
            "last": "97000.5",
            "highest_bid": "97000.0",
            "lowest_ask": "97001.0",
            ...
        }
    }
    """

    def __init__(self, symbol: str = "BTC"):
        self.symbol = symbol.upper()
        super().__init__(f"gateio_{self.symbol.lower()}" if symbol != "BTC" else "gateio")

    def _get_url(self) -> str:
        return "wss://api.gateio.ws/ws/v4/"

    def _get_subscribe_message(self) -> Optional[dict]:
        pair = f"{self.symbol}_USDT"
        return {
            "time": int(time.time()),
            "channel": "spot.tickers",
            "event": "subscribe",
            "payload": [pair]
        }

    def _parse_message(self, data: dict) -> Optional[float]:
        if not isinstance(data, dict):
            return None

        # Check if this is a ticker update
        if data.get("channel") != "spot.tickers":
            return None
        if data.get("event") != "update":
            return None

        result = data.get("result")
        if not result:
            return None

        price_str = result.get("last")
        if price_str:
            try:
                price = float(price_str)

                # Extract bid/ask
                bid_str = result.get("highest_bid")
                ask_str = result.get("lowest_ask")
                if bid_str:
                    self.bid = float(bid_str)
                if ask_str:
                    self.ask = float(ask_str)

                return price
            except (ValueError, TypeError):
                return None

        return None
