"""
PulseFeed Configuration

Exchange URLs, timeouts, and aggregation thresholds.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class ExchangeConfig:
    """Configuration for a single exchange."""
    name: str
    url: str
    symbol: str
    subscribe_msg: dict
    price_path: str  # JSON path to price field
    enabled: bool = True


# Exchange WebSocket configurations
# Note: USD pairs (Coinbase, Kraken) are real dollars
#       USDT pairs (Binance, OKX, Bybit) are Tether stablecoin (can diverge from USD)
EXCHANGES: Dict[str, ExchangeConfig] = {
    "binance": ExchangeConfig(
        name="binance",
        url="wss://stream.binance.com:9443/ws/btcusdt@ticker",
        symbol="BTCUSDT",
        subscribe_msg={},  # No subscription needed for direct stream
        price_path="c",  # Last price field
    ),
    "coinbase": ExchangeConfig(
        name="coinbase",
        url="wss://ws-feed.exchange.coinbase.com",
        symbol="BTC-USD",
        subscribe_msg={
            "type": "subscribe",
            "channels": [{"name": "ticker", "product_ids": ["BTC-USD"]}]
        },
        price_path="price",
    ),
    "kraken": ExchangeConfig(
        name="kraken",
        url="wss://ws.kraken.com/v2",
        symbol="BTC/USD",  # v2 uses BTC not XBT
        subscribe_msg={
            "method": "subscribe",
            "params": {"channel": "ticker", "symbol": ["BTC/USD"]}
        },
        price_path="last",
    ),
    "okx": ExchangeConfig(
        name="okx",
        url="wss://ws.okx.com:8443/ws/v5/public",
        symbol="BTC-USDT",
        subscribe_msg={
            "op": "subscribe",
            "args": [{"channel": "tickers", "instId": "BTC-USDT"}]
        },
        price_path="data.0.last",
    ),
    "bybit": ExchangeConfig(
        name="bybit",
        url="wss://stream.bybit.com/v5/public/spot",
        symbol="BTCUSDT",
        subscribe_msg={
            "op": "subscribe",
            "args": ["tickers.BTCUSDT"]
        },
        price_path="data.lastPrice",
    ),
}

# USD vs USDT classification
USD_EXCHANGES = ["coinbase", "kraken"]  # Real USD pairs
USDT_EXCHANGES = ["binance", "okx", "bybit"]  # Tether pairs


# Aggregation settings
class AggregatorConfig:
    # Staleness filter: exclude prices older than this
    MAX_STALENESS_MS = 2000  # 2 seconds

    # Outlier filter: exclude prices deviating more than this from median
    MAX_DEVIATION_PCT = 1.0  # 1%

    # Minimum sources required for valid aggregation
    MIN_SOURCES = 2

    # Confidence calculation
    # confidence = 1.0 if all sources within TIGHT_SPREAD, scales down otherwise
    TIGHT_SPREAD_PCT = 0.1  # 0.1% = high confidence

    # Divergence warning thresholds
    DIVERGENCE_WARNING_PCT = 0.3  # 0.3% = warning
    DIVERGENCE_CRITICAL_PCT = 0.5  # 0.5% = potential manipulation


# Connection settings
class ConnectionConfig:
    # WebSocket connection timeout
    CONNECT_TIMEOUT_SEC = 5

    # Ping interval to keep connection alive
    PING_INTERVAL_SEC = 20

    # Reconnection settings
    RECONNECT_DELAY_SEC = 1.0
    MAX_RECONNECT_DELAY_SEC = 30.0
    RECONNECT_BACKOFF = 1.5
