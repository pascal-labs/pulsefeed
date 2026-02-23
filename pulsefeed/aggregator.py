"""
Price Aggregator

Aggregates prices from multiple exchanges with:
- Staleness filtering
- Outlier rejection
- Median calculation
- Divergence detection
- Confidence scoring
"""

import statistics
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .config import AggregatorConfig, USD_EXCHANGES, USDT_EXCHANGES
from .models import PriceReport, SourceSnapshot


@dataclass
class AggregatedPrice:
    """Result of price aggregation."""
    price: float
    sources: Dict[str, float]  # Raw prices from each exchange
    sources_normalized: Dict[str, float]  # USDT prices converted to USD
    source_count: int
    divergence: float  # Max spread % between exchanges (after normalization)
    confidence: float  # 0-1 based on agreement
    usdt_premium: float  # USDT premium over USD (e.g., 0.17 = 0.17%)
    timestamp_ms: int


class PriceAggregator:
    """
    Aggregates prices from multiple exchanges.

    Algorithm:
    1. Filter stale prices (> MAX_STALENESS_MS old)
    2. Prioritize USD exchanges (Coinbase, Kraken) over USDT (OKX, Bybit)
    3. Filter outliers (> MAX_DEVIATION_PCT from median)
    4. Compute median of remaining prices
    5. Calculate divergence (max spread %)
    6. Calculate confidence (based on agreement)
    """

    def __init__(self, usd_only: bool = False):
        """
        Args:
            usd_only: If True, use only USD exchanges (Coinbase, Kraken) for price.
                     If False (default), use all exchanges with USDT normalized to USD.
        """
        self.sequence_id = 0
        self.config = AggregatorConfig()
        self.usd_only = usd_only

    def aggregate(
        self,
        snapshots: Dict[str, SourceSnapshot]
    ) -> Optional[AggregatedPrice]:
        """
        Aggregate prices from multiple exchange snapshots.

        Args:
            snapshots: Dict mapping exchange name to SourceSnapshot

        Returns:
            AggregatedPrice or None if not enough valid sources
        """
        now_ms = int(time.time() * 1000)

        # Step 1: Filter stale prices
        fresh_prices: Dict[str, float] = {}
        for name, snapshot in snapshots.items():
            age_ms = now_ms - snapshot.timestamp_ms
            if age_ms < self.config.MAX_STALENESS_MS and snapshot.price > 0:
                fresh_prices[name] = snapshot.price

        if not fresh_prices:
            return None

        # Step 2: Separate USD and USDT prices
        # Use prefix matching to handle symbol suffixes (e.g., "coinbase_eth" matches "coinbase")
        def is_usd_exchange(name: str) -> bool:
            return any(name == ex or name.startswith(f"{ex}_") for ex in USD_EXCHANGES)

        def is_usdt_exchange(name: str) -> bool:
            return any(name == ex or name.startswith(f"{ex}_") for ex in USDT_EXCHANGES)

        usd_prices: Dict[str, float] = {
            k: v for k, v in fresh_prices.items() if is_usd_exchange(k)
        }
        usdt_prices: Dict[str, float] = {
            k: v for k, v in fresh_prices.items() if is_usdt_exchange(k)
        }

        # Step 3: Calculate USDT premium (how much USDT is above/below USD)
        usdt_premium = 0.0
        if usd_prices and usdt_prices:
            usd_median = statistics.median(list(usd_prices.values()))
            usdt_median = statistics.median(list(usdt_prices.values()))
            usdt_premium = ((usdt_median - usd_median) / usd_median) * 100

        # Step 4: Normalize USDT prices to USD
        normalized_prices: Dict[str, float] = {}
        for name, price in fresh_prices.items():
            if is_usd_exchange(name):
                normalized_prices[name] = price
            elif is_usdt_exchange(name) and usdt_premium != 0:
                # Convert USDT to USD by removing premium
                normalized_prices[name] = price / (1 + usdt_premium / 100)
            else:
                normalized_prices[name] = price

        # Step 5: Calculate final price from normalized prices
        if self.usd_only and usd_prices:
            # Use USD exchanges only
            final_prices = list(usd_prices.values())
        else:
            # Use all normalized prices
            final_prices = list(normalized_prices.values())

        if not final_prices:
            return None

        final_median = statistics.median(final_prices)

        # Step 6: Calculate divergence on NORMALIZED prices (should be tight now)
        all_normalized = list(normalized_prices.values())
        min_price = min(all_normalized)
        max_price = max(all_normalized)
        divergence = (max_price - min_price) / final_median * 100

        # Step 7: Calculate confidence
        confidence = self._calculate_confidence(final_prices, final_median)

        self.sequence_id += 1

        return AggregatedPrice(
            price=final_median,
            sources=fresh_prices,  # Raw prices
            sources_normalized=normalized_prices,  # USDT→USD converted
            source_count=len(final_prices),
            divergence=divergence,
            confidence=confidence,
            usdt_premium=usdt_premium,
            timestamp_ms=now_ms,
        )

    def _calculate_confidence(
        self,
        prices: List[float],
        median: float
    ) -> float:
        """
        Calculate confidence score based on price agreement.

        Returns 1.0 if all prices within TIGHT_SPREAD_PCT of median.
        Scales down based on standard deviation.
        """
        if len(prices) < 2:
            return 1.0

        # Calculate how tight the prices cluster
        try:
            stdev = statistics.stdev(prices)
            spread_pct = (stdev / median) * 100
        except statistics.StatisticsError:
            return 1.0

        # Perfect agreement = 1.0
        # Wider spread = lower confidence
        if spread_pct <= self.config.TIGHT_SPREAD_PCT:
            return 1.0
        elif spread_pct >= self.config.DIVERGENCE_CRITICAL_PCT:
            return 0.5
        else:
            # Linear interpolation
            range_pct = self.config.DIVERGENCE_CRITICAL_PCT - self.config.TIGHT_SPREAD_PCT
            excess = spread_pct - self.config.TIGHT_SPREAD_PCT
            return max(0.5, 1.0 - (excess / range_pct) * 0.5)

    def create_report(
        self,
        aggregated: AggregatedPrice,
        feed_id: str = "BTC-USD"
    ) -> PriceReport:
        """Create a full PriceReport from aggregated data."""
        return PriceReport(
            feed_id=feed_id,
            price=aggregated.price,
            price_int=int(aggregated.price * 1e8),
            timestamp_ms=aggregated.timestamp_ms,
            sequence_id=self.sequence_id,
            source_count=aggregated.source_count,
            sources=aggregated.sources,
            confidence=aggregated.confidence,
            divergence=aggregated.divergence,
        )


def calculate_momentum(
    current_price: float,
    start_price: float
) -> float:
    """
    Calculate price momentum as percentage change.

    Positive = price up, Negative = price down.
    """
    if start_price <= 0:
        return 0.0
    return ((current_price - start_price) / start_price) * 100
