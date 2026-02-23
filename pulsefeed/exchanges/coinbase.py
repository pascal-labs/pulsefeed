"""
Coinbase Exchange WebSocket Feed

Connects to Coinbase Exchange's public ticker channel.
No authentication required for public ticker data.
"""

from typing import Optional
from .base import ExchangeFeed


class CoinbaseFeed(ExchangeFeed):
    """
    Coinbase Exchange ticker feed for any supported pair.

    URL: wss://ws-feed.exchange.coinbase.com
    Supported symbols: BTC, ETH, SOL, XRP, etc.
    """

    def __init__(self, symbol: str = "BTC"):
        self.symbol = symbol.upper()
        super().__init__(f"coinbase_{self.symbol.lower()}" if symbol != "BTC" else "coinbase")

    def _get_url(self) -> str:
        return "wss://ws-feed.exchange.coinbase.com"

    def _get_subscribe_message(self) -> Optional[dict]:
        product_id = f"{self.symbol}-USD"
        return {
            "type": "subscribe",
            "channels": [{"name": "ticker", "product_ids": [product_id]}]
        }

    def _parse_message(self, data: dict) -> Optional[float]:
        if not isinstance(data, dict):
            return None

        # Only process ticker messages
        msg_type = data.get("type")
        if msg_type != "ticker":
            return None

        # Coinbase uses "price" for last trade price
        price_str = data.get("price")
        if price_str is None:
            return None

        try:
            price = float(price_str)

            # Extract bid/ask
            bid_str = data.get("best_bid")
            ask_str = data.get("best_ask")
            if bid_str:
                self.bid = float(bid_str)
            if ask_str:
                self.ask = float(ask_str)

            return price
        except (ValueError, TypeError):
            return None
