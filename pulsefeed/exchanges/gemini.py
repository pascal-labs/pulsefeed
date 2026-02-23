"""
Gemini Exchange WebSocket Feed

Connects to Gemini's public marketdata stream.
No authentication required.
"""

from typing import Optional
from .base import ExchangeFeed


class GeminiFeed(ExchangeFeed):
    """
    Gemini ticker feed for any supported pair.

    URL: wss://api.gemini.com/v1/marketdata/{symbol}usd
    No subscription needed - symbol is in URL, stream starts immediately.

    Supported: BTC, ETH, SOL (NO XRP on Gemini)
    """

    def __init__(self, symbol: str = "BTC"):
        self.symbol = symbol.upper()
        super().__init__(f"gemini_{self.symbol.lower()}" if symbol != "BTC" else "gemini")

    def _get_url(self) -> str:
        # Gemini uses lowercase symbol in URL
        pair = f"{self.symbol.lower()}usd"
        return f"wss://api.gemini.com/v1/marketdata/{pair}"

    def _get_subscribe_message(self) -> Optional[dict]:
        # No subscription needed - stream starts on connect
        return None

    def _parse_message(self, data: dict) -> Optional[float]:
        if not isinstance(data, dict):
            return None

        msg_type = data.get("type")

        # Handle trade events (actual executed trades)
        if msg_type == "trade":
            price_str = data.get("price")
            if price_str:
                try:
                    return float(price_str)
                except (ValueError, TypeError):
                    return None

        # Handle change events (orderbook updates) - extract best bid/ask
        elif msg_type == "change":
            side = data.get("side")
            price_str = data.get("price")
            if price_str and side:
                try:
                    price = float(price_str)
                    if side == "bid":
                        self.bid = price
                    elif side == "ask":
                        self.ask = price
                except (ValueError, TypeError):
                    pass
            # Don't return price from change events - only trades

        # Initial snapshot has events array
        elif "events" in data:
            for event in data.get("events", []):
                if event.get("type") == "trade":
                    price_str = event.get("price")
                    if price_str:
                        try:
                            return float(price_str)
                        except (ValueError, TypeError):
                            pass

        return None
