"""
KuCoin Exchange WebSocket Feed

Connects to KuCoin's public ticker stream.
Requires getting a WebSocket token from REST API first.
"""

import requests
import time
from typing import Optional
from .base import ExchangeFeed


class KuCoinFeed(ExchangeFeed):
    """
    KuCoin ticker feed for any supported pair.

    Flow:
    1. POST to /api/v1/bullet-public to get WebSocket token and endpoint
    2. Connect to WebSocket with token
    3. Subscribe to /market/ticker:{SYMBOL}-USDT

    Supported symbols: BTC, ETH, SOL, XRP, etc.
    """

    def __init__(self, symbol: str = "BTC"):
        self.symbol = symbol.upper()
        super().__init__(f"kucoin_{self.symbol.lower()}" if symbol != "BTC" else "kucoin")
        self._ws_token = None
        self._ws_endpoint = None
        self._ping_interval = 30  # KuCoin requires ping every 30s

    def _get_ws_token(self) -> bool:
        """Get WebSocket token from REST API."""
        try:
            resp = requests.post(
                "https://api.kucoin.com/api/v1/bullet-public",
                timeout=10
            )
            if resp.status_code != 200:
                return False

            data = resp.json()
            if data.get("code") != "200000":
                return False

            instance = data["data"]["instanceServers"][0]
            self._ws_endpoint = instance["endpoint"]
            self._ws_token = data["data"]["token"]
            self._ping_interval = instance.get("pingInterval", 30000) // 1000

            return True
        except Exception as e:
            print(f"KuCoin token error: {e}")
            return False

    def _get_url(self) -> str:
        # Get fresh token if needed
        if not self._ws_token:
            self._get_ws_token()

        if self._ws_endpoint and self._ws_token:
            return f"{self._ws_endpoint}?token={self._ws_token}"
        return ""

    def _get_subscribe_message(self) -> Optional[dict]:
        topic = f"/market/ticker:{self.symbol}-USDT"
        return {
            "id": int(time.time() * 1000),
            "type": "subscribe",
            "topic": topic,
            "privateChannel": False,
            "response": True
        }

    def _parse_message(self, data: dict) -> Optional[float]:
        if not isinstance(data, dict):
            return None

        # Handle ticker messages
        if data.get("type") == "message" and data.get("subject") == "trade.ticker":
            ticker = data.get("data", {})
            price_str = ticker.get("price")
            if price_str:
                try:
                    price = float(price_str)

                    # Extract bid/ask
                    bid_str = ticker.get("bestBid")
                    ask_str = ticker.get("bestAsk")
                    if bid_str:
                        self.bid = float(bid_str)
                    if ask_str:
                        self.ask = float(ask_str)

                    return price
                except (ValueError, TypeError):
                    return None

        return None
