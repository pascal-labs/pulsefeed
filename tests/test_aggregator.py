"""
Unit tests for PulseFeed aggregation logic.

Tests the PriceAggregator, confidence scoring, staleness filtering,
USDT premium calculation, and momentum functions without requiring
any live exchange connections.
"""

import os
import sys
import statistics
import time
import types
import unittest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Import strategy: we need pulsefeed.aggregator, pulsefeed.config, and
# pulsefeed.models WITHOUT triggering pulsefeed/__init__.py, which imports
# exchange feeds and chainlink (requiring the `websocket` package).
#
# We register a minimal pulsefeed package manually so that
# ``from pulsefeed.aggregator import ...`` resolves to the submodule files
# without executing __init__.py.
# ---------------------------------------------------------------------------
_pkg_dir = os.path.join(os.path.dirname(__file__), os.pardir, "pulsefeed")
_pkg_dir = os.path.normpath(_pkg_dir)
_project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir))

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Create a hollow package so sub-module imports work.
if "pulsefeed" not in sys.modules:
    _pkg = types.ModuleType("pulsefeed")
    _pkg.__path__ = [_pkg_dir]
    _pkg.__package__ = "pulsefeed"
    sys.modules["pulsefeed"] = _pkg

from pulsefeed.config import AggregatorConfig, USD_EXCHANGES, USDT_EXCHANGES  # noqa: E402
from pulsefeed.models import PriceReport, SourceSnapshot                      # noqa: E402
from pulsefeed.aggregator import AggregatedPrice, PriceAggregator, calculate_momentum  # noqa: E402


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


def _make_snapshot(exchange: str, price: float, age_ms: int = 0) -> SourceSnapshot:
    """
    Helper to build a SourceSnapshot with a controlled age.

    Args:
        exchange: Exchange name (must align with USD_EXCHANGES / USDT_EXCHANGES).
        price: Mid price.
        age_ms: How old the snapshot should appear (0 = just now).
    """
    return SourceSnapshot(
        exchange=exchange,
        price=price,
        timestamp_ms=_now_ms() - age_ms,
    )


class TestMedianAggregation(unittest.TestCase):
    """Test 1 -- median aggregation with known exchange prices."""

    def test_median_three_usd_exchanges(self):
        """Three USD exchanges reporting 100.0, 100.5, 101.0 -> median 100.5."""
        agg = PriceAggregator()
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 100.0),
            "kraken": _make_snapshot("kraken", 100.5),
            # Add a third source classified as neither USD nor USDT so it is
            # included at face value (falls through to the else branch).
            "gemini": _make_snapshot("gemini", 101.0),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.price, 100.5, places=2)

    def test_median_even_number_of_sources(self):
        """Four sources: median is average of middle two."""
        agg = PriceAggregator()
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 100.0),
            "kraken": _make_snapshot("kraken", 102.0),
            "gemini": _make_snapshot("gemini", 101.0),
            "bitstamp": _make_snapshot("bitstamp", 103.0),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        # sorted: 100, 101, 102, 103 -> median = (101 + 102) / 2 = 101.5
        self.assertAlmostEqual(result.price, 101.5, places=2)


class TestStalenessFiltering(unittest.TestCase):
    """Test 2 -- prices older than MAX_STALENESS_MS are excluded."""

    def test_stale_prices_excluded(self):
        """A snapshot older than MAX_STALENESS_MS should be dropped."""
        agg = PriceAggregator()
        stale_age = AggregatorConfig.MAX_STALENESS_MS + 500  # well past threshold

        snapshots = {
            "coinbase": _make_snapshot("coinbase", 100.0),
            "kraken": _make_snapshot("kraken", 200.0, age_ms=stale_age),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        # Only coinbase should survive; kraken is stale.
        self.assertAlmostEqual(result.price, 100.0, places=2)
        self.assertNotIn("kraken", result.sources)

    def test_all_stale_returns_none(self):
        """If every snapshot is stale the aggregator should return None."""
        agg = PriceAggregator()
        stale_age = AggregatorConfig.MAX_STALENESS_MS + 1000

        snapshots = {
            "coinbase": _make_snapshot("coinbase", 100.0, age_ms=stale_age),
            "kraken": _make_snapshot("kraken", 101.0, age_ms=stale_age),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNone(result)


class TestConfidenceScoring(unittest.TestCase):
    """Test 3 -- more exchanges / tighter spread -> higher confidence."""

    def test_tight_spread_gives_full_confidence(self):
        """Prices within TIGHT_SPREAD_PCT of each other -> confidence 1.0."""
        agg = PriceAggregator()
        # All within 0.1% of each other
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 100.00),
            "kraken": _make_snapshot("kraken", 100.05),
            "gemini": _make_snapshot("gemini", 100.03),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        self.assertEqual(result.confidence, 1.0)

    def test_wide_spread_lowers_confidence(self):
        """Wider spread should reduce confidence below 1.0."""
        agg = PriceAggregator()
        # Spread around 0.5% -- beyond TIGHT_SPREAD_PCT
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 100.00),
            "kraken": _make_snapshot("kraken", 100.50),
            "gemini": _make_snapshot("gemini", 100.25),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        self.assertLess(result.confidence, 1.0)
        # But should still be at least 0.5 (the floor)
        self.assertGreaterEqual(result.confidence, 0.5)

    def test_single_source_confidence_is_one(self):
        """With fewer than 2 prices, _calculate_confidence returns 1.0."""
        agg = PriceAggregator()
        conf = agg._calculate_confidence([50000.0], 50000.0)
        self.assertEqual(conf, 1.0)


class TestUSDTPremium(unittest.TestCase):
    """Test 4 -- USDT premium calculation when mixing USD and USDT."""

    def test_positive_usdt_premium(self):
        """USDT exchanges trading higher than USD -> positive premium."""
        agg = PriceAggregator()
        # USD exchanges at 100.0, USDT exchanges at 100.20
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 100.00),
            "kraken": _make_snapshot("kraken", 100.00),
            "binance": _make_snapshot("binance", 100.20),
            "okx": _make_snapshot("okx", 100.20),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        # Premium should be positive (~0.2%)
        self.assertGreater(result.usdt_premium, 0)
        self.assertAlmostEqual(result.usdt_premium, 0.2, places=1)

    def test_no_usdt_premium_when_usd_only(self):
        """When only USD exchanges are present, USDT premium is 0."""
        agg = PriceAggregator()
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 100.0),
            "kraken": _make_snapshot("kraken", 100.1),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        self.assertEqual(result.usdt_premium, 0.0)

    def test_usdt_normalization_tightens_prices(self):
        """
        After normalization the USDT prices should be converted toward
        the USD level, so normalized spread is tighter than raw spread.
        """
        agg = PriceAggregator()
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 50000.0),
            "kraken": _make_snapshot("kraken", 50000.0),
            "binance": _make_snapshot("binance", 50100.0),  # 0.2% premium
            "okx": _make_snapshot("okx", 50100.0),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)

        # Raw spread: 50100 - 50000 = 100
        raw_spread = max(result.sources.values()) - min(result.sources.values())
        # Normalized spread should be smaller
        norm_spread = (
            max(result.sources_normalized.values())
            - min(result.sources_normalized.values())
        )
        self.assertLess(norm_spread, raw_spread)


class TestSingleExchange(unittest.TestCase):
    """Test 5 -- single exchange still produces a valid aggregate."""

    def test_single_exchange_produces_result(self):
        """One exchange should still yield a valid AggregatedPrice."""
        agg = PriceAggregator()
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 42000.0),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.price, 42000.0, places=2)
        self.assertEqual(result.source_count, 1)
        self.assertEqual(result.divergence, 0.0)

    def test_single_exchange_confidence_is_one(self):
        """Single source -> confidence should be 1.0 (no disagreement)."""
        agg = PriceAggregator()
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 42000.0),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        self.assertEqual(result.confidence, 1.0)


class TestNoExchanges(unittest.TestCase):
    """Test 6 -- empty input handled gracefully."""

    def test_empty_snapshots_returns_none(self):
        """Empty dict of snapshots -> None."""
        agg = PriceAggregator()
        result = agg.aggregate({})
        self.assertIsNone(result)

    def test_zero_price_filtered(self):
        """Snapshots with price 0 are treated as invalid and filtered out."""
        agg = PriceAggregator()
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 0.0),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNone(result)


class TestMomentumCalculation(unittest.TestCase):
    """Test 7 -- momentum (percentage change) with sequential prices."""

    def test_positive_momentum(self):
        """Price going up should yield positive momentum."""
        mom = calculate_momentum(current_price=105.0, start_price=100.0)
        self.assertAlmostEqual(mom, 5.0, places=4)

    def test_negative_momentum(self):
        """Price going down should yield negative momentum."""
        mom = calculate_momentum(current_price=95.0, start_price=100.0)
        self.assertAlmostEqual(mom, -5.0, places=4)

    def test_zero_momentum(self):
        """Unchanged price -> 0% momentum."""
        mom = calculate_momentum(current_price=100.0, start_price=100.0)
        self.assertAlmostEqual(mom, 0.0, places=4)

    def test_zero_start_price_returns_zero(self):
        """Guard against division by zero when start_price is 0."""
        mom = calculate_momentum(current_price=100.0, start_price=0.0)
        self.assertEqual(mom, 0.0)

    def test_negative_start_price_returns_zero(self):
        """Negative start_price (invalid) should return 0."""
        mom = calculate_momentum(current_price=100.0, start_price=-1.0)
        self.assertEqual(mom, 0.0)


class TestExchangeWeightsAndPriority(unittest.TestCase):
    """Test 8 -- USD exchange priority is respected when usd_only=True."""

    def test_usd_only_mode_excludes_usdt(self):
        """
        When usd_only=True, the final price should come from USD
        exchanges only (coinbase, kraken), ignoring USDT exchanges.
        """
        agg = PriceAggregator(usd_only=True)
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 100.0),
            "kraken": _make_snapshot("kraken", 102.0),
            "binance": _make_snapshot("binance", 110.0),  # Should be ignored
            "okx": _make_snapshot("okx", 112.0),           # Should be ignored
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        # Median of USD only: (100 + 102) / 2 = 101
        self.assertAlmostEqual(result.price, 101.0, places=2)
        # source_count reflects only USD sources used for final price
        self.assertEqual(result.source_count, 2)

    def test_usd_only_falls_back_to_all_when_no_usd(self):
        """
        If usd_only=True but no USD exchanges are present, the aggregator
        still uses all available normalized prices (fallback branch).
        """
        agg = PriceAggregator(usd_only=True)
        snapshots = {
            "binance": _make_snapshot("binance", 50000.0),
            "okx": _make_snapshot("okx", 50100.0),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        # Falls back to normalized prices (no USD reference so no premium adjustment)
        expected_median = statistics.median([50000.0, 50100.0])
        self.assertAlmostEqual(result.price, expected_median, places=2)

    def test_exchange_name_prefix_matching(self):
        """
        Exchanges with symbol suffixes (e.g., 'coinbase_eth') should
        still be classified as USD or USDT via prefix matching.
        """
        agg = PriceAggregator()
        snapshots = {
            "coinbase_eth": _make_snapshot("coinbase_eth", 3000.0),
            "binance_eth": _make_snapshot("binance_eth", 3005.0),
        }
        result = agg.aggregate(snapshots)
        self.assertIsNotNone(result)
        # coinbase_eth -> USD, binance_eth -> USDT
        # With one of each, a premium should be computed
        self.assertAlmostEqual(
            result.usdt_premium,
            ((3005.0 - 3000.0) / 3000.0) * 100,
            places=4,
        )


class TestCreateReport(unittest.TestCase):
    """Additional coverage: PriceAggregator.create_report."""

    def test_create_report_returns_price_report(self):
        """create_report should produce a PriceReport with correct fields."""
        agg = PriceAggregator()
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 50000.0),
            "kraken": _make_snapshot("kraken", 50010.0),
        }
        aggregated = agg.aggregate(snapshots)
        self.assertIsNotNone(aggregated)

        report = agg.create_report(aggregated, feed_id="BTC-USD")
        self.assertIsInstance(report, PriceReport)
        self.assertEqual(report.feed_id, "BTC-USD")
        self.assertAlmostEqual(report.price, aggregated.price, places=2)
        self.assertEqual(report.price_int, int(aggregated.price * 1e8))
        self.assertEqual(report.source_count, aggregated.source_count)
        self.assertGreater(len(report.hash), 0)

    def test_sequence_id_increments(self):
        """Each aggregate() call should bump the sequence_id."""
        agg = PriceAggregator()
        snapshots = {
            "coinbase": _make_snapshot("coinbase", 100.0),
        }
        agg.aggregate(snapshots)
        first_seq = agg.sequence_id

        agg.aggregate(snapshots)
        second_seq = agg.sequence_id

        self.assertEqual(second_seq, first_seq + 1)


if __name__ == "__main__":
    unittest.main()
