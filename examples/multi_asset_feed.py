#!/usr/bin/env python3
"""
Multi-Asset Simultaneous Feed

Connects to BTC, ETH, and SOL feeds across multiple exchanges
simultaneously. Aggregates per-asset prices and prints a
consolidated dashboard showing cross-asset correlation signals.

Use case: Monitor whether BTC, ETH, and SOL are moving together
(correlated) or diverging (decorrelated), which affects hedging
and relative-value strategies in prediction markets.

Usage:
    python examples/multi_asset_feed.py
    python examples/multi_asset_feed.py --assets BTC ETH SOL XRP
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from pulsefeed import PulseFeed

EXCHANGES = ["binance", "coinbase", "kraken", "okx", "bybit"]


def compute_momentum(current: float, baseline: float) -> float:
    """Compute percentage change from baseline."""
    if baseline <= 0:
        return 0.0
    return ((current - baseline) / baseline) * 100


def correlation_label(momentums: Dict[str, float]) -> str:
    """Determine if assets are moving in the same direction."""
    values = list(momentums.values())
    if len(values) < 2:
        return "N/A"
    positive = sum(1 for v in values if v > 0.01)
    negative = sum(1 for v in values if v < -0.01)
    if positive == len(values):
        return "ALL UP"
    elif negative == len(values):
        return "ALL DOWN"
    elif positive > 0 and negative > 0:
        return "MIXED"
    return "FLAT"


def run_multi_asset(assets: list):
    """Run multi-asset feed with consolidated output."""
    print(f"Multi-Asset Feed: {', '.join(assets)}")
    print(f"Exchanges: {', '.join(EXCHANGES)}")
    print()

    # Start a separate PulseFeed per asset
    feeds: Dict[str, PulseFeed] = {}
    for asset in assets:
        # Gemini does not support XRP
        exchanges = [e for e in EXCHANGES if not (asset == "XRP" and e == "gemini")]
        feed = PulseFeed(exchanges=exchanges, symbol=asset, enable_chainlink=False)
        if feed.start():
            feeds[asset] = feed
        else:
            print(f"WARNING: {asset} feed failed to start")

    if len(feeds) < 2:
        print("Need at least 2 asset feeds. Exiting.")
        for f in feeds.values():
            f.stop()
        return

    # Wait for prices to populate
    print("\nWaiting for initial prices...")
    time.sleep(3)

    # Capture baseline prices for momentum
    baselines: Dict[str, float] = {}
    for asset, feed in feeds.items():
        price = feed.get_price()
        if price:
            baselines[asset] = price

    header = "Time     | " + " | ".join(f"{a:>14}" for a in feeds.keys())
    separator = "-" * len(header)
    print(f"\n{separator}")
    print(header)
    print(separator)

    try:
        while True:
            ts = datetime.utcnow().strftime("%H:%M:%S")
            momentums: Dict[str, float] = {}
            cells = []

            for asset, feed in feeds.items():
                price = feed.get_price()
                div = feed.get_divergence()

                if price and asset in baselines:
                    mom = compute_momentum(price, baselines[asset])
                    momentums[asset] = mom
                    cells.append(f"${price:>9,.0f} {mom:+.2f}%")
                elif price:
                    baselines[asset] = price
                    cells.append(f"${price:>9,.0f}   ---")
                else:
                    cells.append(f"{'---':>14}")

            corr = correlation_label(momentums)
            row = f"{ts} | " + " | ".join(f"{c:>14}" for c in cells)
            print(f"{row}  [{corr}]")

            time.sleep(2)

    except KeyboardInterrupt:
        print(f"\n{separator}")
        print("Final snapshot:")
        for asset, feed in feeds.items():
            price = feed.get_price()
            sources = feed.get_source_count()
            div = feed.get_divergence()
            if price and asset in baselines:
                mom = compute_momentum(price, baselines[asset])
                print(f"  {asset}: ${price:,.2f} | "
                      f"mom={mom:+.3f}% | div={div:.3f}% | "
                      f"{sources} sources")
        print()

    for feed in feeds.values():
        feed.stop()
    print("Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-asset simultaneous price feed"
    )
    parser.add_argument(
        "--assets", nargs="+", default=["BTC", "ETH", "SOL"],
        help="Assets to monitor (default: BTC ETH SOL)"
    )
    args = parser.parse_args()

    run_multi_asset([a.upper() for a in args.assets])


if __name__ == "__main__":
    main()
