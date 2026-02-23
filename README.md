# pulsefeed

Real-time, multi-exchange crypto market data aggregation pipeline with tick-level capture and cross-exchange divergence detection. Supports **BTC, ETH, SOL, and XRP** across 8 exchanges.

## Overview

PulseFeed connects to 8+ cryptocurrency exchanges simultaneously via WebSocket, aggregates prices with outlier detection and USDT premium normalization, and provides a unified price feed with confidence scoring across multiple assets (BTC, ETH, SOL, XRP). Includes Chainlink oracle comparison for lead-lag signal generation.

## Architecture

```
Exchange WebSockets (8 sources)
    → Per-Exchange Feed Handlers
    → Price Aggregator (median + outlier filtering)
    → Unified Price Report (with confidence & divergence)
    → Data Capture (CSV + JSONL.gz tick data)
```

## Supported Exchanges
| Exchange | Pair | Protocol |
|----------|------|----------|
| Binance | BTC/USDT | WebSocket |
| Coinbase | BTC/USD | WebSocket |
| Kraken | BTC/USD | WebSocket v2 |
| OKX | BTC/USDT | WebSocket v5 |
| Bybit | BTC/USDT | WebSocket v5 |
| Gemini | BTC/USD | WebSocket |
| KuCoin | BTC/USDT | WebSocket |
| Gate.io | BTC/USDT | WebSocket |

## Key Features
- **Multi-exchange aggregation** with median pricing and outlier detection
- **USDT premium detection** — separates USD vs USDT pricing for accurate aggregation
- **Confidence scoring** (0-1) based on cross-exchange agreement
- **Divergence tracking** — real-time cross-exchange spread monitoring
- **Chainlink oracle comparison** — lead-lag signal generation vs on-chain reference
- **L2 orderbook recording** — tick-level bid/ask capture to compressed JSONL
- **Auto-reconnect** with exponential backoff
- **Market discovery** — automatic Polymarket event contract enumeration

## Quick Start

```python
from pulsefeed import PulseFeed

feed = PulseFeed(
    exchanges=["binance", "coinbase", "kraken", "okx"],
    symbol="BTC"
)
feed.start()

price = feed.get_price()          # Aggregated price
div = feed.get_divergence()       # Cross-exchange spread %
conf = feed.get_confidence()      # Agreement score (0-1)
signal = feed.get_oracle_signal() # 'LONG' | 'SHORT' | 'NEUTRAL'
```

## Data Capture

```python
from capture.capture import MultiMarketCapture

capture = MultiMarketCapture(
    assets=["btc", "eth"],
    exchanges=["binance", "coinbase", "kraken"]
)
capture.run(duration=3600)  # Capture for 1 hour
# Output: CSV files with 500ms tick resolution
```

## Project Structure

```
pulsefeed/
├── pulsefeed/              # Core package
│   ├── __init__.py         # PulseFeed class — main entry point
│   ├── aggregator.py       # Median + outlier filtering + confidence
│   ├── chainlink.py        # Chainlink oracle comparison
│   ├── config.py           # Exchange URLs, thresholds
│   ├── models.py           # PriceReport, SourceSnapshot dataclasses
│   ├── websocket_feed.py   # Polymarket WebSocket price feed
│   ├── btc_price_feed.py   # Standalone BTC price feed (Binance)
│   └── exchanges/          # Per-exchange WebSocket connectors
│       ├── base.py         # Abstract ExchangeFeed base class
│       ├── binance.py
│       ├── coinbase.py
│       ├── kraken.py
│       ├── okx.py
│       ├── bybit.py
│       ├── gemini.py
│       ├── kucoin.py
│       └── gateio.py
├── capture/                # Data capture pipeline
│   ├── capture.py          # Multi-market orchestrator (CSV output)
│   ├── market_discovery.py # Polymarket contract enumeration
│   └── record_l2.py        # L2 orderbook recorder (JSONL.gz)
├── examples/
│   └── basic_feed.py       # Minimal usage example
├── setup.py
├── requirements.txt
└── .env.example
```

## Tech Stack
- Python, `websockets`, `asyncio`
- Chainlink Data Streams API
- JSONL + gzip for tick storage
- CSV for time-series export

## Configuration
Copy `.env.example` to `.env` and set:
```
CHAINLINK_API_KEY=your_key_here
CHAINLINK_API_SECRET=your_secret_here
```

Without Chainlink credentials the oracle feed falls back to Kraken REST polling (1s interval).

## License

MIT
