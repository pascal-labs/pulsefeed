"""
PulseFeed: Multi-Exchange BTC Price Aggregator

Drop-in replacement for BTCPriceFeed with:
- Aggregated price from 5 exchanges (Binance, Coinbase, Kraken, OKX, Bybit)
- Cross-exchange divergence detection
- Confidence scoring
- Manipulation alerting

Usage:
    from pulsefeed import PulseFeed

    feed = PulseFeed()
    feed.start()

    # BTCPriceFeed-compatible interface
    price = feed.get_price()
    momentum = feed.get_momentum()

    # New aggregation features
    divergence = feed.get_divergence()
    confidence = feed.get_confidence()
"""

import logging
import time
from typing import Dict, List, Optional

from .aggregator import PriceAggregator, calculate_momentum
from .chainlink import ChainlinkFeed, calculate_oracle_lag
from .config import EXCHANGES, AggregatorConfig
from .exchanges import (
    BinanceFeed,
    BybitFeed,
    CoinbaseFeed,
    GateIOFeed,
    GeminiFeed,
    KrakenFeed,
    KuCoinFeed,
    OKXFeed,
)
from .models import PriceReport, SourceSnapshot

logger = logging.getLogger(__name__)

__version__ = "1.0.0"
__all__ = ["PulseFeed", "PriceReport", "SourceSnapshot"]


class PulseFeed:
    """
    Multi-exchange BTC price aggregator.

    Drop-in replacement for BTCPriceFeed with additional
    aggregation features like divergence and confidence.
    """

    def __init__(self, exchanges: Optional[List[str]] = None, enable_chainlink: bool = True, symbol: str = "BTC"):
        """
        Initialize PulseFeed.

        Args:
            exchanges: List of exchange names to use.
                      Default: all 8 (binance, coinbase, kraken, okx, bybit, gemini, kucoin, gateio)
            enable_chainlink: Whether to fetch Chainlink/reference price for comparison
            symbol: Trading symbol (BTC, ETH, SOL, XRP). Default: BTC
        """
        self.exchanges = exchanges or ["binance", "coinbase", "kraken", "okx", "bybit", "gemini", "kucoin", "gateio"]
        self.enable_chainlink = enable_chainlink
        self.symbol = symbol.upper()

        # Create exchange feeds
        self._feeds: Dict[str, object] = {}
        self._feed_classes = {
            "binance": BinanceFeed,
            "coinbase": CoinbaseFeed,
            "kraken": KrakenFeed,
            "okx": OKXFeed,
            "bybit": BybitFeed,
            "gemini": GeminiFeed,
            "kucoin": KuCoinFeed,
            "gateio": GateIOFeed,
        }

        # Chainlink/oracle reference feed
        self._chainlink: Optional[ChainlinkFeed] = None

        # Aggregator
        self._aggregator = PriceAggregator()

        # Current state
        self._snapshots: Dict[str, SourceSnapshot] = {}
        self._last_aggregated = None
        self._last_report: Optional[PriceReport] = None

        # Momentum tracking (BTCPriceFeed compatibility)
        self.window_start_price: Optional[float] = None
        self.window_start_time: Optional[float] = None

        # Status
        self.connected = False
        self.running = False

    def start(self) -> bool:
        """
        Start all exchange feeds.

        Returns:
            True if at least MIN_SOURCES connected
        """
        self.running = True
        connected_count = 0

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def connect_exchange(name):
            if name not in self._feed_classes:
                return name, None, f"unknown exchange"
            try:
                feed = self._feed_classes[name](symbol=self.symbol)
                if feed.start(on_price_update=self._on_price_update):
                    return name, feed, None
                else:
                    return name, None, "failed to connect"
            except Exception as e:
                return name, None, str(e)

        with ThreadPoolExecutor(max_workers=len(self.exchanges)) as executor:
            futures = {executor.submit(connect_exchange, name): name for name in self.exchanges}
            for future in as_completed(futures):
                name, feed, error = future.result()
                if feed:
                    self._feeds[feed.name] = feed
                    connected_count += 1
                    print(f"  ✓ {name.capitalize()} connected")
                else:
                    print(f"  ✗ {name.capitalize()} {error}")

        self.connected = connected_count >= AggregatorConfig.MIN_SOURCES
        if self.connected:
            print(f"  ⚡ PulseFeed {self.symbol} active ({connected_count}/{len(self.exchanges)} exchanges)")
        else:
            print(f"  ⚠️  PulseFeed {self.symbol} degraded ({connected_count}/{len(self.exchanges)} exchanges)")

        # Start oracle reference feed (Kraken REST, polls every 1s)
        if self.enable_chainlink:
            try:
                self._chainlink = ChainlinkFeed(poll_interval=1.0)
                if self._chainlink.start():
                    print(f"  ✓ Oracle reference feed active (1s polling)")
                else:
                    print(f"  ✗ Oracle reference feed failed")
                    self._chainlink = None
            except Exception as e:
                print(f"  ✗ Oracle error: {e}")
                self._chainlink = None

        return self.connected

    def stop(self):
        """Stop all exchange feeds."""
        self.running = False
        self.connected = False

        for name, feed in self._feeds.items():
            try:
                feed.stop()
            except Exception as e:
                logger.debug(f"Error stopping {name}: {e}")

        self._feeds.clear()

        # Stop Chainlink feed
        if self._chainlink:
            self._chainlink.stop()
            self._chainlink = None

    def _on_price_update(self, exchange: str, price: float):
        """Callback when an exchange price updates."""
        feed = self._feeds.get(exchange)
        if not feed:
            return

        bid, ask = feed.get_bid_ask()
        self._snapshots[exchange] = SourceSnapshot(
            exchange=exchange,
            price=price,
            timestamp_ms=int(time.time() * 1000),
            bid=bid,
            ask=ask,
        )

        # Re-aggregate
        self._aggregate()

    def _aggregate(self):
        """Run aggregation on current snapshots."""
        if not self._snapshots:
            return

        result = self._aggregator.aggregate(self._snapshots)
        if result:
            self._last_aggregated = result
            self._last_report = self._aggregator.create_report(result)

    # =========================================================================
    # BTCPriceFeed-compatible interface
    # =========================================================================

    def get_price(self) -> Optional[float]:
        """Get current aggregated price."""
        if self._last_aggregated:
            return self._last_aggregated.price
        return None

    @property
    def price(self) -> Optional[float]:
        """BTCPriceFeed-compatible property."""
        return self.get_price()

    def get_age(self) -> float:
        """Get age of last aggregated price in seconds."""
        if self._last_aggregated:
            age_ms = int(time.time() * 1000) - self._last_aggregated.timestamp_ms
            return age_ms / 1000.0
        return float('inf')

    def mark_window_start(self):
        """Mark current price as window start for momentum calculation."""
        self.window_start_price = self.get_price()
        self.window_start_time = time.time()

    def get_momentum(self) -> Optional[float]:
        """
        Get price change since window start as percentage.

        Positive = price up, Negative = price down.
        """
        current = self.get_price()
        if self.window_start_price is None or current is None:
            return None
        return calculate_momentum(current, self.window_start_price)

    def get_momentum_abs(self) -> Optional[float]:
        """Get absolute momentum (ignores direction)."""
        m = self.get_momentum()
        return abs(m) if m is not None else None

    # =========================================================================
    # New aggregation features
    # =========================================================================

    def get_prices(self) -> Dict[str, float]:
        """Get raw prices from all exchanges."""
        if self._last_aggregated:
            return self._last_aggregated.sources.copy()
        return {}

    def get_prices_normalized(self) -> Dict[str, float]:
        """Get normalized prices (USDT converted to USD)."""
        if self._last_aggregated:
            return self._last_aggregated.sources_normalized.copy()
        return {}

    def get_usdt_premium(self) -> float:
        """
        Get USDT premium over USD as percentage.

        Positive = USDT trading above USD (e.g., 0.17 = USDT is 0.17% higher)
        Negative = USDT trading below USD (rare)
        """
        if self._last_aggregated:
            return self._last_aggregated.usdt_premium
        return 0.0

    def get_divergence(self) -> float:
        """
        Get cross-exchange divergence as percentage.

        Higher values indicate potential manipulation or market stress.
        - < 0.3%: Normal
        - 0.3-0.5%: Elevated (caution)
        - > 0.5%: High (potential manipulation)
        """
        if self._last_aggregated:
            return self._last_aggregated.divergence
        return 0.0

    def get_confidence(self) -> float:
        """
        Get confidence score (0-1) based on exchange agreement.

        - 1.0: All exchanges within tight spread
        - 0.5-1.0: Normal variation
        - < 0.5: High disagreement (use caution)
        """
        if self._last_aggregated:
            return self._last_aggregated.confidence
        return 0.0

    def get_source_count(self) -> int:
        """Get number of active exchange sources."""
        if self._last_aggregated:
            return self._last_aggregated.source_count
        # Before first aggregation, return connected feed count
        return len(self._feeds)

    def get_signed_report(self) -> Optional[PriceReport]:
        """Get full price report with hash."""
        return self._last_report

    def is_manipulation_warning(self) -> bool:
        """Check if divergence indicates potential manipulation."""
        return self.get_divergence() > AggregatorConfig.DIVERGENCE_WARNING_PCT

    def is_manipulation_critical(self) -> bool:
        """Check if divergence is critically high."""
        return self.get_divergence() > AggregatorConfig.DIVERGENCE_CRITICAL_PCT

    # =========================================================================
    # Oracle/Chainlink comparison (trading signal)
    # =========================================================================

    def get_oracle_price(self) -> Optional[float]:
        """Get current oracle/Chainlink reference price."""
        if self._chainlink:
            return self._chainlink.get_price()
        return None

    def get_oracle_lag(self) -> dict:
        """
        Get divergence between real-time price and oracle.

        This is the ALPHA signal for Polymarket trading.

        Returns:
            dict with:
            - divergence_pct: % difference (positive = realtime above oracle)
            - divergence_bps: Basis points difference
            - signal: 'LONG' (YES underpriced), 'SHORT' (NO underpriced), 'NEUTRAL'
            - strength: 0-1 signal strength
        """
        realtime = self.get_price()
        oracle = self.get_oracle_price()

        if realtime is None or oracle is None:
            return {
                "divergence_pct": 0,
                "divergence_bps": 0,
                "signal": "NEUTRAL",
                "strength": 0,
            }

        return calculate_oracle_lag(realtime, oracle)

    def get_oracle_signal(self) -> str:
        """
        Get trading signal based on oracle lag.

        Returns:
            'LONG' - Real price above oracle, YES is underpriced
            'SHORT' - Real price below oracle, NO is underpriced
            'NEUTRAL' - No significant divergence
        """
        return self.get_oracle_lag()["signal"]

    def get_oracle_divergence_bps(self) -> float:
        """Get oracle divergence in basis points."""
        return self.get_oracle_lag()["divergence_bps"]

    # =========================================================================
    # Status and diagnostics
    # =========================================================================

    def get_status(self) -> dict:
        """Get full status of all feeds."""
        status = {
            "connected": self.connected,
            "running": self.running,
            "source_count": self.get_source_count(),
            "price": self.get_price(),
            "divergence": self.get_divergence(),
            "confidence": self.get_confidence(),
            "feeds": {},
        }

        for name, feed in self._feeds.items():
            status["feeds"][name] = feed.get_stats()

        return status

    def print_status(self):
        """Print formatted status to console."""
        price = self.get_price()
        div = self.get_divergence()
        conf = self.get_confidence()
        sources = self.get_source_count()

        print(f"\n{'='*50}")
        print(f"PulseFeed Status")
        print(f"{'='*50}")
        print(f"Price:      ${price:,.2f}" if price else "Price:      N/A")
        print(f"Sources:    {sources}/{len(self.exchanges)}")
        print(f"Divergence: {div:.3f}%")
        print(f"Confidence: {conf:.2f}")
        print(f"Age:        {self.get_age():.2f}s")

        if self.is_manipulation_critical():
            print(f"⚠️  CRITICAL: High divergence detected!")
        elif self.is_manipulation_warning():
            print(f"⚠️  WARNING: Elevated divergence")

        print(f"\nPer-exchange prices:")
        for name, p in self.get_prices().items():
            print(f"  {name:12} ${p:,.2f}")
        print(f"{'='*50}\n")


# Test function
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("Testing PulseFeed...")
    feed = PulseFeed()

    if feed.start():
        print("\nPulseFeed started! Waiting for prices...\n")

        # Mark window start for momentum
        time.sleep(2)
        feed.mark_window_start()
        print(f"Window start: ${feed.window_start_price:,.2f}\n")

        for i in range(20):
            price = feed.get_price()
            div = feed.get_divergence()
            conf = feed.get_confidence()
            mom = feed.get_momentum()
            sources = feed.get_source_count()

            if price:
                mom_str = f"{mom:+.4f}%" if mom else "N/A"
                print(
                    f"[{i:2d}s] ${price:,.2f} | "
                    f"div={div:.3f}% conf={conf:.2f} "
                    f"mom={mom_str} sources={sources}"
                )
            else:
                print(f"[{i:2d}s] Waiting for prices...")

            time.sleep(1)

        feed.print_status()
        feed.stop()
        print("\nPulseFeed stopped.")
    else:
        print("Failed to start PulseFeed")
