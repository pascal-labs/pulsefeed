"""
PulseFeed Exchange Connectors

WebSocket connectors for each supported exchange.
"""

from .base import ExchangeFeed
from .binance import BinanceFeed
from .coinbase import CoinbaseFeed
from .kraken import KrakenFeed
from .okx import OKXFeed
from .bybit import BybitFeed
from .gemini import GeminiFeed
from .kucoin import KuCoinFeed
from .gateio import GateIOFeed

__all__ = [
    "ExchangeFeed",
    "BinanceFeed",
    "CoinbaseFeed",
    "KrakenFeed",
    "OKXFeed",
    "BybitFeed",
    "GeminiFeed",
    "KuCoinFeed",
    "GateIOFeed",
]

# Registry for easy iteration
EXCHANGE_FEEDS = {
    "binance": BinanceFeed,
    "coinbase": CoinbaseFeed,
    "kraken": KrakenFeed,
    "okx": OKXFeed,
    "bybit": BybitFeed,
    "gemini": GeminiFeed,
    "kucoin": KuCoinFeed,
    "gateio": GateIOFeed,
}
