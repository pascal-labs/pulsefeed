#!/usr/bin/env python3
"""
Cross-Exchange Divergence Monitor

Connects to multiple exchanges and monitors for price divergence
exceeding a configurable threshold. Prints alerts when spreads
between exchanges are abnormally wide, which can indicate:
  - Exchange-specific liquidity events
  - Stale feeds / connectivity issues
  - Potential manipulation
  - Flash crashes on a single venue

Usage:
    python examples/divergence_monitor.py
    python examples/divergence_monitor.py --threshold 0.20
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pulsefeed import PulseFeed


def format_spread(high_exchange: str, low_exchange: str,
                  high_price: float, low_price: float,
                  spread_pct: float) -> str:
    """Format a divergence alert line."""
    return (
        f"  {high_exchange:>10} ${high_price:>10,.2f}  |  "
        f"{low_exchange:>10} ${low_price:>10,.2f}  |  "
        f"spread: {spread_pct:.3f}%"
    )


def run_monitor(symbol: str, threshold_pct: float, exchanges: list):
    """Run the divergence monitor loop."""
    print(f"Divergence Monitor - {symbol}")
    print(f"Threshold: {threshold_pct:.2f}%")
    print(f"Exchanges: {', '.join(exchanges)}")
    print()

    feed = PulseFeed(exchanges=exchanges, symbol=symbol, enable_chainlink=False)

    if not feed.start():
        print("Failed to connect to enough exchanges.")
        return

    print()
    alert_count = 0

    try:
        while True:
            prices = feed.get_prices_normalized()
            divergence = feed.get_divergence()
            confidence = feed.get_confidence()
            agg_price = feed.get_price()

            if not prices or agg_price is None:
                time.sleep(0.5)
                continue

            ts = datetime.utcnow().strftime("%H:%M:%S")

            if divergence > threshold_pct:
                alert_count += 1
                sorted_prices = sorted(prices.items(), key=lambda x: x[1])
                low_name, low_price = sorted_prices[0]
                high_name, high_price = sorted_prices[-1]

                print(f"[{ts}] ALERT #{alert_count} | "
                      f"divergence={divergence:.3f}% | "
                      f"confidence={confidence:.2f}")
                print(format_spread(
                    high_name, low_name, high_price, low_price, divergence
                ))
                print(f"  Aggregated: ${agg_price:,.2f} "
                      f"({len(prices)} sources)")
                print()
            else:
                # Quiet status line every 5 seconds
                print(f"\r[{ts}] OK | ${agg_price:,.2f} | "
                      f"div={divergence:.3f}% conf={confidence:.2f} "
                      f"sources={len(prices)} alerts={alert_count}",
                      end="", flush=True)

            time.sleep(1)

    except KeyboardInterrupt:
        print(f"\n\nStopping. Total alerts: {alert_count}")

    feed.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Monitor cross-exchange price divergence"
    )
    parser.add_argument(
        "--symbol", default="BTC",
        help="Asset symbol (BTC, ETH, SOL, XRP). Default: BTC"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.15,
        help="Divergence threshold in percent. Default: 0.15"
    )
    parser.add_argument(
        "--exchanges", nargs="+",
        default=["binance", "coinbase", "kraken", "okx", "bybit"],
        help="Exchanges to monitor"
    )
    args = parser.parse_args()

    run_monitor(args.symbol, args.threshold, args.exchanges)


if __name__ == "__main__":
    main()
