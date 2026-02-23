# Exchange Technical Notes

Per-exchange implementation notes, message formats, latency characteristics, and lessons learned from building and running PulseFeed across 8 exchanges.

---

## Binance

**Pair**: `BTCUSDT` (USDT quote)
**URL**: `wss://stream.binance.us:9443/ws/{pair}@ticker`
**Auth**: None required for public streams.

### Format

Binance uses a direct stream URL -- no subscription message needed. The symbol and channel are encoded in the path itself. Ticker messages arrive as flat JSON:

```json
{"c": "97000.50", "b": "97000.10", "a": "97000.90", ...}
```

- `c` = last price
- `b` = best bid
- `a` = best ask

### US vs International

Binance.com (`stream.binance.com:9443`) returns HTTP 451 (Unavailable For Legal Reasons) for US IPs. PulseFeed uses Binance.US (`stream.binance.us:9443`) instead. The feed format is identical but liquidity is significantly lower, which occasionally shows up as wider spreads in the aggregator.

### Latency

Ticker updates arrive every ~1 second. Trade stream (`@trade`) updates on every fill, which can be 10-50 messages per second during active markets. PulseFeed uses the ticker stream for consistency across exchanges.

---

## Coinbase

**Pair**: `BTC-USD` (real USD quote)
**URL**: `wss://ws-feed.exchange.coinbase.com`
**Auth**: None for public channels. Authenticated channels require API key + HMAC signature.

### Format

Coinbase requires an explicit subscription message after connecting:

```json
{
    "type": "subscribe",
    "channels": [{"name": "ticker", "product_ids": ["BTC-USD"]}]
}
```

Ticker responses include a `type` field that must be checked:

```json
{"type": "ticker", "price": "97000.50", "best_bid": "97000.10", "best_ask": "97000.90", ...}
```

Other message types (`subscriptions`, `heartbeat`, `error`) arrive on the same connection and must be filtered.

### REST vs WebSocket

Coinbase's REST API (`/products/BTC-USD/ticker`) is rate-limited to 10 req/s for public endpoints. The WebSocket is preferred for real-time feeds and has no rate limit for read-only subscriptions. The ticker channel pushes on every trade execution, not on a fixed interval.

### Latency

Updates are trade-driven. During quiet periods, updates can be several seconds apart. During active markets, 5-20 updates per second. Coinbase is one of the most reliable feeds -- disconnections are rare.

---

## Kraken

**Pair**: `BTC/USD` (real USD quote)
**URL**: `wss://ws.kraken.com/v2`
**Auth**: None for public channels.

### v1 to v2 Migration

Kraken's v1 WebSocket API (`wss://ws.kraken.com`) used `XBT` for Bitcoin (the ISO 4217 proposed code). The v2 API (`wss://ws.kraken.com/v2`) uses standard `BTC`. PulseFeed uses v2 exclusively.

**v1 subscription** (deprecated):
```json
{"event": "subscribe", "pair": ["XBT/USD"], "subscription": {"name": "ticker"}}
```

**v2 subscription** (current):
```json
{"method": "subscribe", "params": {"channel": "ticker", "symbol": ["BTC/USD"]}}
```

### Unique Message Format

Kraken v2 wraps ticker data in a `data` array, with a `channel` field at the top level:

```json
{
    "channel": "ticker",
    "type": "update",
    "data": [{"symbol": "BTC/USD", "last": 97000.50, "bid": 97000.10, "ask": 97000.90}]
}
```

The `type` can be `snapshot` (initial state on connect) or `update` (incremental). PulseFeed handles both identically since it only reads the latest price.

### Latency

Updates every ~1 second. Kraken's matching engine is fast but their WebSocket infrastructure adds slight latency compared to Binance or Bybit. Connection stability is good.

---

## OKX

**Pair**: `BTC-USDT` (USDT quote)
**URL**: `wss://ws.okx.com:8443/ws/v5/public`
**Auth**: None for public channels.

### WebSocket v5 Subscription

OKX uses an `op`/`args` pattern:

```json
{
    "op": "subscribe",
    "args": [{"channel": "tickers", "instId": "BTC-USDT"}]
}
```

The `instId` (instrument ID) uses a dash separator: `BTC-USDT`, not `BTCUSDT` or `BTC/USDT`.

### Response Format

Ticker data arrives nested under `data[0]`:

```json
{
    "arg": {"channel": "tickers", "instId": "BTC-USDT"},
    "data": [{"last": "97000.5", "bidPx": "97000.1", "askPx": "97000.9", ...}]
}
```

Note: `bidPx` and `askPx`, not `bid` and `ask`. OKX uses a `Px` suffix convention for price fields.

### Latency

Updates approximately every 100ms. OKX is one of the higher-volume USDT exchanges, so ticker updates are frequent. The port 8443 is occasionally blocked by corporate firewalls.

---

## Bybit

**Pair**: `BTCUSDT` (USDT quote)
**URL**: `wss://stream.bybit.com/v5/public/spot`
**Auth**: None for public channels.

### v5 API

Bybit's v5 unified API serves spot, linear, and inverse markets through the same WebSocket. PulseFeed connects to the `/spot` path to get spot market tickers.

**Subscription**:
```json
{"op": "subscribe", "args": ["tickers.BTCUSDT"]}
```

The `args` value is a simple string (not a nested object like OKX).

### Inverse vs Linear Pairs

Bybit offers both inverse perpetuals (quoted in USD, settled in BTC) and linear perpetuals (quoted and settled in USDT). PulseFeed uses spot tickers exclusively to avoid futures basis contamination.

### Response Format

```json
{
    "topic": "tickers.BTCUSDT",
    "type": "snapshot",
    "data": {"lastPrice": "97000.50", "bid1Price": "97000.10", "ask1Price": "97000.90", ...}
}
```

Note the `1` suffix on bid/ask fields (`bid1Price`, `ask1Price`), referring to the top-of-book level.

### Latency

Bybit pushes ticker updates approximately every 50ms -- the fastest of all exchanges in PulseFeed. This makes it a good "first mover" indicator. During extreme volatility, the update rate can exceed 20 messages per second.

---

## Gemini

**Pair**: `btcusd` (real USD quote)
**URL**: `wss://api.gemini.com/v1/marketdata/{pair}`
**Auth**: None for public marketdata.

### Simpler WebSocket

Gemini uses a URL-per-symbol design -- no subscription message required. Connecting to the URL immediately starts the data stream. The initial message is a full orderbook snapshot.

### Message Types

Gemini sends three event types on the same stream:

- `trade` -- executed trades (PulseFeed uses this for price)
- `change` -- orderbook level updates (PulseFeed uses for bid/ask)
- Initial snapshot with `events` array containing both

```json
{"type": "trade", "price": "97000.50", "quantity": "0.1", ...}
```

PulseFeed only uses trade events for the price field. Change events update the internal bid/ask tracking.

### Asset Support

Gemini supports BTC, ETH, and SOL but does NOT support XRP. PulseFeed's capture pipeline (`capture.py`) explicitly removes Gemini from the exchange list when processing XRP.

### Latency

Lower throughput than other exchanges. Trade events can be seconds apart during quiet periods. Gemini is a US-regulated exchange with lower volume than Binance or Bybit, resulting in sparser updates. Useful as a USD reference but not as a lead indicator.

---

## KuCoin

**Pair**: `BTC-USDT` (USDT quote)
**URL**: Dynamic (obtained via REST API)
**Auth**: Token-based. Requires pre-flight REST call.

### Token-Based WebSocket Auth

KuCoin is unique among the supported exchanges: you cannot connect to the WebSocket directly. A pre-flight REST call is required to obtain a connection token and endpoint:

```
POST https://api.kucoin.com/api/v1/bullet-public
```

Response:
```json
{
    "code": "200000",
    "data": {
        "token": "2neAiuYvAU...",
        "instanceServers": [{"endpoint": "wss://ws-api-spot.kucoin.com", "pingInterval": 30000}]
    }
}
```

The WebSocket URL is then `{endpoint}?token={token}`. Tokens expire, so PulseFeed re-fetches on reconnection.

### Subscription Format

```json
{
    "id": 1234567890,
    "type": "subscribe",
    "topic": "/market/ticker:BTC-USDT",
    "privateChannel": false,
    "response": true
}
```

The `id` field is required and must be unique per subscription. PulseFeed uses the current Unix timestamp in milliseconds.

### Response Format

```json
{
    "type": "message",
    "subject": "trade.ticker",
    "data": {"price": "97000.50", "bestBid": "97000.10", "bestAsk": "97000.90", ...}
}
```

### Latency

Moderate. The pre-flight token fetch adds 200-500ms to initial connection time. Once connected, ticker updates arrive every ~100ms. KuCoin requires a ping every 30 seconds (server-specified); missing the ping window causes disconnection.

---

## Gate.io

**Pair**: `BTC_USDT` (USDT quote, underscore separator)
**URL**: `wss://api.gateio.ws/ws/v4/`
**Auth**: None for public channels.

### Format Quirks

Gate.io uses underscores in pair names (`BTC_USDT`) where most exchanges use dashes or no separator. The subscription message uses an `event`/`channel`/`payload` pattern:

```json
{
    "time": 1234567890,
    "channel": "spot.tickers",
    "event": "subscribe",
    "payload": ["BTC_USDT"]
}
```

The `time` field is a Unix timestamp (seconds, not milliseconds).

### Response Format

```json
{
    "time": 1234567890,
    "channel": "spot.tickers",
    "event": "update",
    "result": {"currency_pair": "BTC_USDT", "last": "97000.5", "highest_bid": "97000.0", "lowest_ask": "97001.0"}
}
```

The price fields use `highest_bid` and `lowest_ask` rather than `best_bid`/`best_ask` or `bidPx`/`askPx`.

### Latency

Updates approximately every 100-200ms. Gate.io has moderate volume. Connection stability is generally good but the exchange has had occasional extended outages.

---

## Latency Comparison

| Exchange | Typical Update Interval | Connection Setup | Notes |
|----------|------------------------|------------------|-------|
| Bybit | ~50ms | Fast | Fastest ticker updates |
| OKX | ~100ms | Fast | Consistent and reliable |
| Binance | ~1s (ticker) | Fast | Trade stream is faster |
| Gate.io | ~100-200ms | Fast | Moderate volume |
| KuCoin | ~100ms | Slow (token fetch) | 200-500ms extra for pre-flight |
| Coinbase | Trade-driven | Fast | Sparse in quiet markets |
| Kraken | ~1s | Fast | Reliable but not fastest |
| Gemini | Trade-driven | Fast | Lowest throughput, US-only |

---

## Reconnection Strategies

All exchanges share the same reconnection logic via the `ExchangeFeed` base class:

### Exponential Backoff

```
Attempt 1: wait 1.0s
Attempt 2: wait 1.5s   (1.0 * 1.5)
Attempt 3: wait 2.25s  (1.5 * 1.5)
Attempt 4: wait 3.375s (2.25 * 1.5)
...
Maximum:   wait 30.0s  (capped)
```

The delay resets to 1.0s on successful reconnection.

### Per-Exchange Considerations

- **KuCoin**: Must re-fetch the WebSocket token on every reconnection. Old tokens may have expired.
- **Binance**: The stream URL encodes the symbol, so reconnection is simply re-establishing the WebSocket.
- **Coinbase/Kraken/OKX/Bybit**: Must re-send the subscription message after reconnecting. The base class handles this automatically via `_get_subscribe_message()`.
- **Gemini**: No subscription needed (URL-based), so reconnection is the simplest.
- **Gate.io**: The `time` field in the subscription must be refreshed to the current timestamp.

### Health Tracking

Each feed tracks `error_count` and `reconnect_count` for diagnostics:

```python
feed.get_stats()
# {"name": "binance", "connected": True, "price": 97000.5,
#  "age": 0.12, "messages": 4521, "errors": 1, "reconnects": 1}
```

### Connection Timeout

All exchanges use a 5-second connection timeout. If the WebSocket handshake does not complete within 5 seconds, the connection attempt fails and triggers a backoff retry. Ping interval is 20 seconds, ping timeout is 10 seconds. These values work reliably across all 8 exchanges.
