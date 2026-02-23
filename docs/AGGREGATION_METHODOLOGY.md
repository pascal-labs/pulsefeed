# Aggregation Methodology

Technical deep-dive into how PulseFeed computes a single reference price from 8 independent exchange feeds.

---

## Pipeline Overview

```
                         8 WebSocket Feeds
                               |
                    +----------+----------+
                    |                     |
              USD Exchanges         USDT Exchanges
           (Coinbase, Kraken,     (Binance, OKX, Bybit,
               Gemini)            KuCoin, Gate.io)
                    |                     |
                    v                     v
            +--------------+    +------------------+
            |  USD prices  |    | USDT prices      |
            |  (raw)       |    | (raw)            |
            +--------------+    +------------------+
                    |                     |
                    |          +----------+----------+
                    |          |  USDT Premium Calc  |
                    |          |  median(USDT) vs    |
                    |          |  median(USD)        |
                    |          +---------------------+
                    |                     |
                    |          +----------+----------+
                    |          |  Normalize USDT     |
                    |          |  price / (1 + prem) |
                    |          +---------------------+
                    |                     |
                    +----------+----------+
                               |
                    +----------+----------+
                    |  Staleness Filter   |
                    |  (drop > 2000ms)    |
                    +---------------------+
                               |
                    +----------+----------+
                    |  Median Calculation  |
                    +---------------------+
                               |
              +----------------+----------------+
              |                |                |
     +--------+------+ +------+------+ +-------+------+
     |  Divergence   | |  Confidence | |  Price       |
     |  (max spread) | |  (stdev)    | |  Report      |
     +---------------+ +-------------+ +--------------+
```

---

## Why Median Over Mean

The median is the foundation of the aggregation pipeline. The choice is deliberate.

### The Problem With Mean

A simple average of exchange prices is vulnerable to a single outlier. If Binance, Coinbase, and Kraken all report BTC at $97,000 but a lagging exchange reports $96,500 (stale quote, thin book, or flash crash), the mean shifts:

```
Mean:   (97000 + 97000 + 97000 + 96500) / 4 = $96,875.00
Median: sorted([96500, 97000, 97000, 97000])[1:2] = $97,000.00
```

The mean is dragged $125 lower by a single stale price. At scale, $125 on a $97k asset is ~13 basis points -- enough to trigger false signals in a momentum filter or misjudge a Polymarket contract fair value.

### The Problem Gets Worse With Manipulation

If an attacker pushes one exchange price artificially low (paint the tape, wash trade a thin pair), the mean amplifies the manipulation signal across the aggregate. The median ignores it entirely as long as the majority of sources agree.

### With 8 Sources, Median Tolerates 3 Bad Feeds

The median of 8 values is the average of the 4th and 5th sorted values. An attacker (or a cascade of disconnections) would need to compromise 4 of 8 exchanges to move the median. With a mean, corrupting 1 of 8 already introduces error proportional to the magnitude of the corrupt value.

### Where It Lives in Code

`aggregator.py` line 126:

```python
final_median = statistics.median(final_prices)
```

`statistics.median` handles both even and odd lists. For even-length lists (our typical case with 5-8 active sources), it averages the two middle values, which still provides outlier robustness.

---

## USDT Premium Detection

### What Is the USDT Premium

Not all exchanges trade against the same unit. Coinbase, Kraken, and Gemini trade BTC/USD -- real US dollars. Binance, OKX, Bybit, KuCoin, and Gate.io trade BTC/USDT -- Tether stablecoins.

USDT is *supposed* to be worth $1.00, but it frequently trades at a small premium or discount. When USDT is worth $1.0017, a BTC/USDT price of $97,165 actually means BTC is $97,000 in USD terms. Without correcting for this, the aggregator would compute a median biased upward by the USDT premium.

### Why It Matters for Cross-Exchange Arb

The USDT premium is itself a tradable signal:

- **Positive premium** (USDT > USD): Demand for USDT is high, often during Asian trading hours or periods of capital flight from local currencies. BTC/USDT quotes look higher than they should.
- **Negative premium** (USDT < USD): Tether FUD, redemption pressure, or regulatory action. BTC/USDT quotes look lower.
- **Magnitude**: Normal range is +/-0.05%. Spikes above 0.20% indicate stress. During the 2023 USDC depeg, USDT premiums exceeded 1%.

### How PulseFeed Computes It

The aggregator separates prices by quote currency, computes the median of each group, and derives the premium:

```python
usd_median  = median(coinbase, kraken, gemini)     # e.g., $97,000.00
usdt_median = median(binance, okx, bybit, kucoin, gateio)  # e.g., $97,164.90

usdt_premium = ((usdt_median - usd_median) / usd_median) * 100
# = ((97164.90 - 97000.00) / 97000.00) * 100
# = 0.17%
```

USDT prices are then normalized to USD before median calculation:

```python
normalized_price = usdt_price / (1 + usdt_premium / 100)
```

This ensures the final aggregated price reflects true USD value regardless of which exchanges contribute to the median.

### Configuration

`config.py` classifies exchanges:

```python
USD_EXCHANGES  = ["coinbase", "kraken"]   # Real USD pairs
USDT_EXCHANGES = ["binance", "okx", "bybit"]  # Tether pairs
```

Gemini also trades USD but is handled through prefix matching in the aggregator since each exchange connector uses a naming convention like `gemini_btc`.

---

## Confidence Scoring Algorithm

Confidence measures how much the exchanges agree with each other. The score maps from 0.5 to 1.0.

### Algorithm

1. Compute the standard deviation of all (normalized) prices.
2. Express it as a percentage of the median: `spread_pct = (stdev / median) * 100`.
3. Map to a score:

```
spread_pct <= 0.10%  -->  confidence = 1.0  (tight agreement)
spread_pct >= 0.50%  -->  confidence = 0.5  (critical divergence)
0.10% < spread < 0.50%  -->  linear interpolation between 1.0 and 0.5
```

### Intuition

| spread_pct | confidence | Interpretation |
|------------|------------|----------------|
| 0.00-0.10% | 1.00 | All exchanges agree within ~$97 on a $97k asset. Normal. |
| 0.15% | 0.94 | Slight disagreement. Possibly one exchange lagging. |
| 0.30% | 0.75 | Warning zone. Could be a slow exchange or thin liquidity. |
| 0.50%+ | 0.50 | Critical. Possible manipulation, exchange down, or flash event. |

### Why Not Go Below 0.5

A confidence of 0.5 means "use this price with extreme caution." Going to 0.0 would imply "this price is meaningless," but even in a high-divergence scenario the median of remaining sources is still the best available estimate. The floor at 0.5 reflects that.

### Implementation

```python
def _calculate_confidence(self, prices, median):
    stdev = statistics.stdev(prices)
    spread_pct = (stdev / median) * 100

    if spread_pct <= TIGHT_SPREAD_PCT:       # 0.1%
        return 1.0
    elif spread_pct >= DIVERGENCE_CRITICAL_PCT:  # 0.5%
        return 0.5
    else:
        range_pct = DIVERGENCE_CRITICAL_PCT - TIGHT_SPREAD_PCT
        excess = spread_pct - TIGHT_SPREAD_PCT
        return max(0.5, 1.0 - (excess / range_pct) * 0.5)
```

---

## Staleness Detection

Every exchange snapshot carries a `timestamp_ms`. The aggregator rejects any price older than `MAX_STALENESS_MS` (default: 2000ms).

### Why 2 Seconds

- WebSocket ticker updates arrive every 50-500ms under normal conditions (Bybit is fastest at ~50ms, Gemini is slowest).
- A 2-second window accommodates brief network hiccups without admitting genuinely stale data.
- During a fast market move, a price that is 2+ seconds old can be off by 50+ bps on BTC -- enough to corrupt the aggregate.

### What Happens When an Exchange Goes Stale

The aggregator simply excludes it. If Gemini stops updating for 3 seconds, the median is computed from the remaining 7 sources. If 6 of 8 go stale, and only 2 remain, aggregation still proceeds (MIN_SOURCES = 2). Below 2, aggregation returns `None`.

### Health Monitoring

The `ExchangeState` model tracks per-exchange health:

```python
def is_healthy(self, max_staleness_ms: int = 2000) -> bool:
    if not self.connected or self.last_price is None:
        return False
    age_ms = int(time.time() * 1000) - self.last_update_ms
    return age_ms < max_staleness_ms
```

Each `ExchangeFeed` also exposes `get_age()` for diagnostics.

---

## Cross-Exchange Divergence as a Trading Signal

### What Divergence Measures

Divergence is the spread between the highest and lowest exchange price, expressed as a percentage of the median:

```python
divergence = (max_price - min_price) / median * 100
```

This is computed on *normalized* prices (after USDT premium correction), so the divergence reflects genuine disagreement between exchanges, not just quote currency differences.

### What Divergence Means

| Divergence | Regime | Interpretation |
|------------|--------|----------------|
| < 0.10% | Tight | All exchanges in sync. Low-risk to trade. |
| 0.10-0.30% | Normal | Minor lag differences. Expected during moderate moves. |
| 0.30-0.50% | Elevated | One or more exchanges significantly lagging. Exercise caution. |
| > 0.50% | Critical | Potential manipulation, exchange outage, or extreme volatility. |

### Trading Applications

1. **Pause trading**: When divergence exceeds 0.3%, the price signal is unreliable. A prediction market bot should widen its bid-ask or sit out.
2. **Arb detection**: Persistent divergence between specific exchange pairs suggests a cross-exchange arbitrage opportunity (or a broken feed).
3. **Volatility proxy**: Divergence spikes precede or coincide with large moves. It can serve as a real-time volatility indicator.

### Thresholds

```python
DIVERGENCE_WARNING_PCT  = 0.3   # Caution
DIVERGENCE_CRITICAL_PCT = 0.5   # Stop trading
```

---

## Chainlink Oracle as Independent Reference

### Architecture

PulseFeed optionally runs a `ChainlinkFeed` alongside the exchange feeds. If Chainlink Data Streams credentials are available, it connects to the Chainlink WebSocket for ~500ms resolution BTC/USD updates. Otherwise, it falls back to Kraken REST polling at 1-second intervals.

### The Lead-Lag Signal

On-chain oracles like Chainlink update on a heartbeat (every few seconds) or when price moves beyond a deviation threshold. Exchange WebSocket feeds update in real-time (50-500ms). The difference between the two is the "oracle lag":

```python
divergence_pct = ((realtime_price - oracle_price) / oracle_price) * 100
```

### Signal Interpretation

| Oracle Lag | Signal | Meaning |
|------------|--------|---------|
| > +5 bps | LONG | Real price above oracle. Market is moving up, oracle hasn't caught up. |
| < -5 bps | SHORT | Real price below oracle. Market is moving down, oracle hasn't caught up. |
| -5 to +5 bps | NEUTRAL | Prices in sync. No edge. |

### Why This Matters for Polymarket

Polymarket's BTC Up/Down contracts resolve based on a reference price. If the reference price lags the live market by even a few seconds, a trader who knows the current real-time price has an informational advantage. The oracle lag signal quantifies that edge.

### Signal Strength

Strength scales linearly from 0 to 1 as the divergence grows from 0 to 50 bps:

```python
strength = min(1.0, abs(divergence_bps) / 50)
```

At 50+ bps, strength is maxed. Below 5 bps (the neutral zone), the signal is not actionable.

---

## Full Aggregation Sequence

For reference, the complete sequence executed on every price update:

```
1. Exchange WebSocket message arrives
2. ExchangeFeed._parse_message() extracts price, bid, ask
3. PulseFeed._on_price_update() creates SourceSnapshot with timestamp
4. PriceAggregator.aggregate() runs:
   a. Filter stale prices (> 2000ms old)
   b. Separate USD vs USDT exchanges
   c. Compute USDT premium (median-of-USDT / median-of-USD)
   d. Normalize USDT prices to USD equivalent
   e. Compute final median of all normalized prices
   f. Calculate divergence (max - min) / median
   g. Calculate confidence from stdev / median
5. PriceReport generated with SHA256 integrity hash
6. Available via get_price(), get_divergence(), get_confidence()
```

### Latency Budget

```
WebSocket message receipt:          ~0ms (event-driven)
JSON parse + price extraction:      ~0.1ms
Snapshot creation:                  ~0.01ms
Aggregation (median + stats):       ~0.05ms
Report creation + SHA256:           ~0.02ms
-----------------------------------------------
Total per-update:                   < 0.2ms
```

The aggregator re-runs on *every* price update from any exchange, so the aggregate price reflects the latest available data within sub-millisecond latency.

---

## Configuration Reference

All thresholds are in `config.py`:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `MAX_STALENESS_MS` | 2000 | Reject prices older than this |
| `MAX_DEVIATION_PCT` | 1.0 | Outlier rejection threshold |
| `MIN_SOURCES` | 2 | Minimum exchanges for valid aggregation |
| `TIGHT_SPREAD_PCT` | 0.1 | Below this = confidence 1.0 |
| `DIVERGENCE_WARNING_PCT` | 0.3 | Warning threshold |
| `DIVERGENCE_CRITICAL_PCT` | 0.5 | Critical / manipulation threshold |
| `CONNECT_TIMEOUT_SEC` | 5 | WebSocket connect timeout |
| `PING_INTERVAL_SEC` | 20 | WebSocket keepalive ping |
| `RECONNECT_DELAY_SEC` | 1.0 | Initial reconnect delay |
| `MAX_RECONNECT_DELAY_SEC` | 30.0 | Maximum reconnect delay |
| `RECONNECT_BACKOFF` | 1.5 | Exponential backoff multiplier |
