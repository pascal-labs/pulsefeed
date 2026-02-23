#!/usr/bin/env python3
"""
Basic PulseFeed Example

Connects to multiple exchanges and prints aggregated BTC price
with divergence and confidence metrics every second.

Usage:
    python examples/basic_feed.py
"""

import sys
import time
from pathlib import Path

# Ensure repo root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pulsefeed import PulseFeed


def main():
    print("Starting PulseFeed...")
    print()

    feed = PulseFeed(
        exchanges=["binance", "coinbase", "kraken", "okx", "bybit"],
        symbol="BTC",
        enable_chainlink=True,
    )

    if not feed.start():
        print("Failed to connect to enough exchanges.")
        return

    print()
    time.sleep(2)
    feed.mark_window_start()

    try:
        while True:
            price = feed.get_price()
            div = feed.get_divergence()
            conf = feed.get_confidence()
            mom = feed.get_momentum()
            sources = feed.get_source_count()
            oracle = feed.get_oracle_signal()

            if price:
                mom_str = f"{mom:+.4f}%" if mom is not None else "N/A"
                print(
                    f"${price:,.2f} | "
                    f"div={div:.3f}% conf={conf:.2f} "
                    f"mom={mom_str} sources={sources} "
                    f"oracle={oracle}"
                )
            else:
                print("Waiting for prices...")

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping...")

    feed.stop()
    print("Done.")


if __name__ == "__main__":
    main()
