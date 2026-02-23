#!/usr/bin/env python3
"""
Multi-Market Data Capture Orchestrator

Captures synchronized exchange and Polymarket data for multiple assets.

Markets tracked:
- BTC, ETH, SOL, XRP
- Timeframes: 5m, 15m, 1hr

Data saved:
- {asset}_{timeframe}_data.csv (e.g., btc_15m_data.csv)

CSV columns:
- timestamp: Unix timestamp
- datetime: Human-readable timestamp
- market_slug: Polymarket market identifier
- exchange_price: Median price from exchanges
- up_price: Polymarket UP token price
- down_price: Polymarket DOWN token price
- spread: up_price + down_price - 1.0
- time_remaining: Seconds until resolution
- source_count: Number of exchange sources
- divergence: Cross-exchange divergence %

Usage:
    python capture.py                  # Run all markets
    python capture.py --assets btc eth # Run specific assets
    python capture.py --duration 3600  # Run for 1 hour
"""

import argparse
import csv
import os
import re
import sys
import time
import threading
import urllib.request
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Load environment variables - check current dir and parent
_script_dir = Path(__file__).parent
_env_path = _script_dir / '.env'
if not _env_path.exists():
    _env_path = _script_dir.parent / '.env'
load_dotenv(_env_path)

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from pulsefeed import PulseFeed
from capture.market_discovery import MarketDiscovery, Market, ASSETS, TIMEFRAMES
from pulsefeed.websocket_feed import WebSocketPriceFeed

# Configuration
DEFAULT_EXCHANGES = ["binance", "coinbase", "kraken", "okx", "bybit", "kucoin", "gateio"]
POLL_INTERVAL = 0.5  # Seconds between data captures (500ms = 2/sec)
DATA_DIR = Path(__file__).parent / "data"
COVERAGE_CHECK_INTERVAL = 900  # Check coverage every 15 min (1 window)
COVERAGE_ALERT_THRESHOLD = 95  # Alert if below this %

# Telegram notifications (credentials loaded at call time)
def send_telegram(message: str):
    """Send a Telegram notification."""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML'
        }).encode()
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[Telegram] Failed: {e}")


def get_capture_coverage(data_dir: Path) -> dict:
    """Get coverage stats for last 16 windows (~4 hours)."""
    stats = {}
    for asset in ["btc", "eth", "sol", "xrp"]:
        csv_path = data_dir / f"{asset}_15m_data.csv"
        if not csv_path.exists():
            continue

        windows = defaultdict(int)
        try:
            with open(csv_path, "r") as f:
                header = f.readline()
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) > 2:
                        slug = parts[2] if len(parts) > 2 else ""
                        match = re.search(r'-(\d+)$', slug)
                        if match:
                            windows[match.group(1)] += 1
        except:
            continue

        if windows:
            sorted_wins = sorted(windows.keys(), key=int, reverse=True)[1:17]
            expected_ticks = 900 / POLL_INTERVAL  # 15m window / poll interval
            coverages = [min(100, (windows[w] / expected_ticks) * 100) for w in sorted_wins]
            stats[asset] = round(sum(coverages) / len(coverages), 1) if coverages else 0

    if stats:
        avg = round(sum(stats.values()) / len(stats), 1)
        return {"btc": stats.get("btc", 0), "eth": stats.get("eth", 0), "avg": avg}
    return {"btc": 0, "eth": 0, "avg": 0}


class DataCapture:
    """
    Orchestrates multi-market data capture.
    """

    def __init__(
        self,
        assets: List[str] = None,
        timeframes: List[str] = None,
        exchanges: List[str] = None,
        data_dir: Path = None,
    ):
        """
        Initialize data capture.

        Args:
            assets: List of assets to capture (default: all)
            timeframes: List of timeframes (default: all)
            exchanges: List of exchanges for price feeds
            data_dir: Directory to save CSV files
        """
        self.assets = [a.lower() for a in (assets or ASSETS)]
        self.timeframes = timeframes or list(TIMEFRAMES.keys())
        self.exchanges = exchanges or DEFAULT_EXCHANGES
        self.data_dir = data_dir or DATA_DIR

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        self.discovery = MarketDiscovery(assets=self.assets, timeframes=self.timeframes)
        self.price_feeds: Dict[str, PulseFeed] = {}  # Exchange price feeds
        self.ws_feeds: Dict[str, WebSocketPriceFeed] = {}  # Polymarket WebSocket feeds
        self.ws_tokens: Dict[str, tuple] = {}  # Track (up_token, down_token) per market
        self.csv_writers: Dict[str, csv.DictWriter] = {}
        self.csv_files: Dict[str, object] = {}

        # State tracking
        self.running = False
        self.stats = {
            "captures": 0,
            "errors": 0,
            "start_time": None,
        }

        # Window tracking for momentum calculation
        self.window_open_prices: Dict[str, float] = {}  # {asset}_{timeframe} -> open price
        self.last_window_start: Dict[str, int] = {}  # {asset}_{timeframe} -> window start timestamp
        self.window_row_counts: Dict[str, int] = {}  # {asset}_{timeframe} -> rows logged this window

        # Pre-connection for seamless window transitions
        self.pending_ws_feeds: Dict[str, WebSocketPriceFeed] = {}  # Pre-connected feeds for next window
        self.pending_ws_tokens: Dict[str, tuple] = {}  # Tokens for pending feeds

        # Background thread for non-blocking operations
        self._bg_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bg_refresh")

    def _get_csv_path(self, asset: str, timeframe: str) -> Path:
        """Get CSV file path for market."""
        return self.data_dir / f"{asset}_{timeframe}_data.csv"

    def _init_csv(self, asset: str, timeframe: str) -> csv.DictWriter:
        """Initialize CSV file and writer."""
        key = f"{asset}_{timeframe}"
        if key in self.csv_writers:
            return self.csv_writers[key]

        path = self._get_csv_path(asset, timeframe)
        file_exists = path.exists()

        # Open file in append mode
        f = open(path, "a", newline="")
        self.csv_files[key] = f

        fieldnames = [
            "timestamp",
            "datetime",
            "market_slug",
            "exchange_price",
            "exchange_open",    # Price at window start
            "momentum",         # % change from open
            "up_price",
            "down_price",
            "spread",
            "time_remaining",
            "source_count",
            "divergence",
            "price_source",  # WS or HTTP
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)

        # Write header if new file
        if not file_exists:
            writer.writeheader()

        self.csv_writers[key] = writer
        return writer

    def _get_exchange_feed(self, asset: str) -> PulseFeed:
        """Get or create exchange price feed for asset."""
        asset = asset.lower()
        if asset not in self.price_feeds:
            # Note: Gemini doesn't support XRP
            exchanges = self.exchanges.copy()
            if asset == "xrp" and "gemini" in exchanges:
                exchanges.remove("gemini")

            feed = PulseFeed(
                exchanges=exchanges,
                symbol=asset.upper(),
                enable_chainlink=False,
            )
            self.price_feeds[asset] = feed

        return self.price_feeds[asset]

    def _get_ws_feed(self, asset: str, timeframe: str, market: Market) -> Optional[WebSocketPriceFeed]:
        """Get or create Polymarket WebSocket feed for a market."""
        key = f"{asset}_{timeframe}"

        # Check if we already have a feed for these tokens
        current_tokens = (market.up_token, market.down_token)
        cached_tokens = self.ws_tokens.get(key)

        if cached_tokens == current_tokens and key in self.ws_feeds:
            return self.ws_feeds[key]

        # Need to create new feed (market rolled over or first time)
        if key in self.ws_feeds:
            # Stop old feed
            try:
                self.ws_feeds[key].stop()
            except Exception:
                pass

        # Create new WebSocket feed
        ws_feed = WebSocketPriceFeed()
        if ws_feed.start(market.up_token, market.down_token):
            self.ws_feeds[key] = ws_feed
            self.ws_tokens[key] = current_tokens
            return ws_feed
        else:
            print(f"    WARNING: WebSocket feed for {key} failed to start")
            return None

    def _connect_ws_feed(self, asset: str, timeframe: str, up_token: str, down_token: str) -> Optional[WebSocketPriceFeed]:
        """Connect a single WebSocket feed (for parallel execution)."""
        ws_feed = WebSocketPriceFeed()
        if ws_feed.start(up_token, down_token):
            return ws_feed
        return None

    def _refresh_ws_feeds(self):
        """
        Refresh WebSocket feeds for any markets that have rolled over.

        FAST PATH: Uses cached tokens to check for rollover without HTTP.
        Only does HTTP calls in background when actually needed.
        """
        now = time.time()
        reconnect_tasks = []

        for asset in self.assets:
            for timeframe in self.timeframes:
                key = f"{asset}_{timeframe}"
                cached_tokens = self.ws_tokens.get(key)

                # FAST: Check if we have a pre-connected feed ready (no HTTP needed)
                if key in self.pending_ws_feeds:
                    pending_tokens = self.pending_ws_tokens.get(key)
                    if pending_tokens and pending_tokens != cached_tokens:
                        # Window rolled over - use pre-connected feed (instant swap!)
                        print(f"  ⚡ {asset.upper()} {timeframe} using pre-connected feed")
                        if key in self.ws_feeds:
                            try:
                                self.ws_feeds[key].stop()
                            except Exception:
                                pass
                        self.ws_feeds[key] = self.pending_ws_feeds.pop(key)
                        self.ws_tokens[key] = self.pending_ws_tokens.pop(key)
                        continue

                # FAST: Check if current window just started (tokens should have changed)
                interval = TIMEFRAMES[timeframe]
                current_start = (int(now) // interval) * interval
                time_in_window = now - current_start

                # Only check for rollover in first 15 seconds of new window
                # This avoids repeated HTTP calls throughout the window
                if time_in_window < 15 and cached_tokens:
                    # Need to verify tokens - but do it in background to not block
                    reconnect_tasks.append((key, asset, timeframe, None, None, True))  # True = needs lookup

        # Execute token lookups and reconnections in background (non-blocking)
        if reconnect_tasks:
            self._bg_executor.submit(self._do_reconnect_tasks, reconnect_tasks)

    def _do_reconnect_tasks(self, tasks):
        """Background task to handle reconnections without blocking main loop."""
        for key, asset, timeframe, up_token, down_token, needs_lookup in tasks:
            try:
                if needs_lookup:
                    # Fetch market data (HTTP call - but we're in background thread)
                    market = self.discovery.get_market(asset, timeframe)
                    if not market:
                        continue
                    up_token, down_token = market.up_token, market.down_token

                current_tokens = (up_token, down_token)
                cached_tokens = self.ws_tokens.get(key)

                if cached_tokens == current_tokens:
                    continue  # No change needed

                print(f"  🔄 {asset.upper()} {timeframe} market rolled over...")
                if key in self.ws_feeds:
                    try:
                        self.ws_feeds[key].stop()
                    except Exception:
                        pass

                # Connect new feed
                ws_feed = self._connect_ws_feed(asset, timeframe, up_token, down_token)
                if ws_feed:
                    self.ws_feeds[key] = ws_feed
                    self.ws_tokens[key] = current_tokens
                    print(f"  ✓ {asset.upper()} {timeframe} reconnected")
                else:
                    print(f"  ✗ {asset.upper()} {timeframe} failed to reconnect")
            except Exception as e:
                print(f"  ✗ {asset.upper()} {timeframe} error: {e}")

    def _preconnect_next_windows(self):
        """
        Pre-connect to next window's WebSocket feeds before current ends.

        Only runs when at least one window has <30 seconds remaining.
        This prevents the expensive HTTP calls from running every iteration.
        """
        now = time.time()

        # FAST CHECK: Only proceed if any window is ending soon
        should_preconnect = False
        for timeframe in self.timeframes:
            interval = TIMEFRAMES[timeframe]
            current_start = (int(now) // interval) * interval
            time_remaining = current_start + interval - now
            if time_remaining < 30:
                should_preconnect = True
                break

        if not should_preconnect:
            return  # Exit early - no windows ending soon

        preconnect_tasks = []

        for asset in self.assets:
            for timeframe in self.timeframes:
                key = f"{asset}_{timeframe}"
                interval = TIMEFRAMES[timeframe]

                # Calculate time remaining in current window
                current_start = (int(now) // interval) * interval
                time_remaining = current_start + interval - now

                # Pre-connect when <30 seconds remain (and not already pre-connected)
                if time_remaining < 30 and key not in self.pending_ws_feeds:
                    # Get next window's market info
                    next_start = current_start + interval
                    next_market = self.discovery._get_next_market(asset, timeframe, next_start)
                    if next_market:
                        preconnect_tasks.append((key, asset, timeframe, next_market.up_token, next_market.down_token))

        # Execute pre-connections in parallel with shorter timeout
        if preconnect_tasks:
            print(f"  🔮 Pre-connecting {len(preconnect_tasks)} feeds for next window...")
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(self._connect_ws_feed, asset, tf, up, down): (key, up, down)
                    for key, asset, tf, up, down in preconnect_tasks
                }
                for future in futures:
                    key, up, down = futures[future]
                    try:
                        ws_feed = future.result(timeout=2)  # Reduced from 5s to 2s
                        if ws_feed:
                            self.pending_ws_feeds[key] = ws_feed
                            self.pending_ws_tokens[key] = (up, down)
                    except Exception:
                        pass

    def start_feeds(self) -> bool:
        """Start all exchange price feeds (parallelized for speed)."""
        print("\nStarting exchange price feeds...")
        all_started = True

        # Start exchange feeds in parallel
        def start_exchange(asset):
            feed = self._get_exchange_feed(asset)
            success = feed.start()
            return asset, success

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(start_exchange, self.assets))

        for asset, success in results:
            if success:
                print(f"  ✓ {asset.upper()} exchange feed started")
            else:
                print(f"  ✗ {asset.upper()} exchange feed failed")
                all_started = False

        # Start Polymarket WebSocket feeds in parallel
        print("\nStarting Polymarket WebSocket feeds...")

        def start_ws(args):
            asset, timeframe = args
            try:
                market = self.discovery.get_market(asset, timeframe)
                if market:
                    ws_feed = self._get_ws_feed(asset, timeframe, market)
                    return (asset, timeframe, ws_feed is not None)
            except Exception as e:
                print(f"  ✗ {asset.upper()} {timeframe} error: {e}")
            return (asset, timeframe, False)

        tasks = [(a, t) for a in self.assets for t in self.timeframes]
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(start_ws, tasks))

        for asset, timeframe, success in results:
            if success:
                print(f"  ✓ {asset.upper()} {timeframe} WebSocket connected")
            else:
                print(f"  ✗ {asset.upper()} {timeframe} WebSocket failed")
                all_started = False

        return all_started

    def stop_feeds(self):
        """Stop all exchange and Polymarket feeds."""
        # Shutdown background executor first
        try:
            self._bg_executor.shutdown(wait=False)
        except Exception:
            pass

        print("\nStopping exchange feeds...")
        for asset, feed in self.price_feeds.items():
            try:
                feed.stop()
                print(f"  {asset.upper()}: stopped")
            except Exception as e:
                print(f"  {asset.upper()}: error stopping - {e}")

        print("\nStopping Polymarket WebSocket feeds...")
        for key, ws_feed in self.ws_feeds.items():
            try:
                ws_feed.stop()
                print(f"  {key}: stopped")
            except Exception as e:
                print(f"  {key}: error - {e}")

        # Stop pending feeds too
        for key, ws_feed in self.pending_ws_feeds.items():
            try:
                ws_feed.stop()
            except Exception:
                pass

        # Close CSV files
        for key, f in self.csv_files.items():
            try:
                f.close()
            except Exception:
                pass

        self.csv_files.clear()
        self.csv_writers.clear()
        self.price_feeds.clear()
        self.ws_feeds.clear()
        self.ws_tokens.clear()
        self.pending_ws_feeds.clear()
        self.pending_ws_tokens.clear()

    def capture_once(self) -> dict:
        """
        Capture one data point for all markets.
        Optimized: Gets exchange price once per asset (not per market).

        Returns:
            Dict with capture results per market
        """
        results = {}
        now = time.time()
        dt = datetime.utcfromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")

        # Get exchange prices once per asset (4 total, not 8)
        exchange_data = {}
        for asset in self.assets:
            feed = self.price_feeds.get(asset)
            if feed:
                exchange_data[asset] = {
                    "price": feed.get_price(),
                    "source_count": feed.get_source_count(),
                    "divergence": feed.get_divergence(),
                }

        # Capture each market using cached exchange data
        for asset in self.assets:
            ex_data = exchange_data.get(asset, {})
            exchange_price = ex_data.get("price")
            source_count = ex_data.get("source_count", 0)
            divergence = ex_data.get("divergence", 0)

            for timeframe in self.timeframes:
                key = f"{asset}_{timeframe}"

                try:
                    # Build market slug (always, regardless of WS state)
                    interval = TIMEFRAMES[timeframe]
                    start_ts = (int(now) // interval) * interval
                    market_slug = self.discovery._build_slug(asset, timeframe, start_ts)
                    time_remaining = start_ts + interval - now

                    # Get WebSocket feed (may be None if disconnected)
                    ws_feed = self.ws_feeds.get(key)
                    cached_tokens = self.ws_tokens.get(key)

                    # Detect window transition and capture opening price
                    last_start = self.last_window_start.get(key)
                    if last_start != start_ts:
                        # New window started - capture opening price and reset row count
                        prev_rows = self.window_row_counts.get(key, 0)
                        if prev_rows > 0:
                            print(f"  ✅ {key} window complete: {prev_rows} rows logged")
                        if exchange_price:
                            self.window_open_prices[key] = exchange_price
                            print(f"  📊 {key} window start: {asset.upper()} @ ${exchange_price:,.2f}")
                        self.last_window_start[key] = start_ts
                        self.window_row_counts[key] = 0  # Reset for new window

                    # Calculate momentum from window open
                    exchange_open = self.window_open_prices.get(key)
                    momentum = None
                    if exchange_open and exchange_price:
                        momentum = (exchange_price - exchange_open) / exchange_open * 100

                    # Get Polymarket prices with WS->HTTP fallback (like btc_arb.py)
                    up_price, down_price = None, None
                    price_source = "none"

                    # Watchdog: restart WS if thread died silently
                    if ws_feed and hasattr(ws_feed, '_thread') and ws_feed._thread and not ws_feed._thread.is_alive():
                        print(f"  ⚠️  WS thread died for {key}, will reconnect...")
                        try:
                            ws_feed.stop()
                        except Exception:
                            pass
                        # Remove stale feed, will be recreated on next refresh
                        if key in self.ws_feeds:
                            del self.ws_feeds[key]
                        if key in self.ws_tokens:
                            del self.ws_tokens[key]
                        ws_feed = None

                    # Step 1: Try WebSocket first (instant, <50ms latency)
                    if ws_feed and hasattr(ws_feed, 'connected') and ws_feed.connected:
                        ws_up, ws_down = ws_feed.get_prices()

                        # Use WS prices if valid (don't care about age - quiet markets are fine)
                        # WS connection itself handles reconnection if truly dead
                        if ws_up is not None and ws_down is not None:
                            up_price, down_price = ws_up, ws_down
                            price_source = "WS"

                    # Step 2: HTTP fallback - only every 30 seconds to avoid blocking
                    if up_price is None or down_price is None:
                        http_key = f"{key}_http_last"
                        last_http = getattr(self, http_key, 0)
                        if now - last_http > 30:  # Only try HTTP every 30s
                            setattr(self, http_key, now)
                            try:
                                market = self.discovery.get_market(asset, timeframe)
                                if market and market.up_price and market.down_price:
                                    up_price, down_price = market.up_price, market.down_price
                                    price_source = "HTTP"
                            except Exception:
                                pass  # Don't block on HTTP errors

                    # Skip if no prices
                    if not exchange_price and not up_price:
                        continue

                    # Calculate spread
                    spread = None
                    if up_price and down_price:
                        spread = up_price + down_price - 1.0

                    # Build row
                    row = {
                        "timestamp": int(now),
                        "datetime": dt,
                        "market_slug": market_slug,
                        "exchange_price": f"{exchange_price:.2f}" if exchange_price else "",
                        "exchange_open": f"{exchange_open:.2f}" if exchange_open else "",
                        "momentum": f"{momentum:.4f}" if momentum is not None else "",
                        "up_price": f"{up_price:.4f}" if up_price else "",
                        "down_price": f"{down_price:.4f}" if down_price else "",
                        "spread": f"{spread:.4f}" if spread is not None else "",
                        "time_remaining": f"{time_remaining:.0f}",
                        "source_count": source_count,
                        "divergence": f"{divergence:.4f}" if divergence is not None else "",
                        "price_source": price_source,
                    }

                    # Write to CSV
                    writer = self._init_csv(asset, timeframe)
                    writer.writerow(row)
                    self.csv_files[key].flush()

                    results[key] = row
                    self.stats["captures"] += 1
                    self.window_row_counts[key] = self.window_row_counts.get(key, 0) + 1

                except Exception as e:
                    self.stats["errors"] += 1
                    print(f"  Error capturing {key}: {e}")

        return results

    def run(self, duration: float = None, verbose: bool = True):
        """
        Run data capture loop.

        Args:
            duration: How long to run in seconds (None = forever)
            verbose: Print status updates
        """
        self.running = True

        # Start feeds
        if not self.start_feeds():
            print("\nWARNING: Some feeds failed to start, continuing anyway...")

        print("\n" + "=" * 60)
        print("Multi-Market Data Capture Started")
        print("=" * 60)
        print(f"Assets: {', '.join(a.upper() for a in self.assets)}")
        print(f"Timeframes: {', '.join(self.timeframes)}")
        print(f"Data directory: {self.data_dir}")
        print(f"Duration: {'unlimited' if duration is None else f'{duration}s'}")
        print("=" * 60 + "\n")

        # Wait for initial data
        print("Waiting for initial prices...")
        time.sleep(3)

        # Start timer AFTER feeds are ready (not during startup)
        self.stats["start_time"] = time.time()

        # Coverage monitoring
        last_coverage_check = 0
        last_coverage_alert = 100  # Track last coverage to detect drops

        try:
            iteration = 0
            last_ws_check = time.time()  # Don't refresh immediately
            next_tick = time.time() + POLL_INTERVAL  # Tick-based timing
            while self.running:
                # Check duration
                if duration and (time.time() - self.stats["start_time"]) >= duration:
                    print("\nDuration reached, stopping...")
                    break

                # Check for pre-connection opportunity (when window ending soon)
                now = time.time()
                self._preconnect_next_windows()

                # Refresh WebSocket feeds every 5 seconds (fast rollover detection)
                if now - last_ws_check > 5:
                    self._refresh_ws_feeds()
                    last_ws_check = now

                # Coverage check every 15 min - only alert on drops
                if now - last_coverage_check > COVERAGE_CHECK_INTERVAL:
                    last_coverage_check = now
                    coverage = get_capture_coverage(self.data_dir)
                    btc_cov = coverage.get("btc", 0)

                    # Only alert if coverage dropped significantly (>5% drop or below threshold)
                    if btc_cov < last_coverage_alert - 5 or (btc_cov < COVERAGE_ALERT_THRESHOLD and last_coverage_alert >= COVERAGE_ALERT_THRESHOLD):
                        cov_emoji = "✅" if btc_cov >= 95 else "⚠️" if btc_cov >= 85 else "🟡" if btc_cov >= 70 else "❌"
                        send_telegram(f"{cov_emoji} <b>CAPTURE DROP</b>\n"
                                     f"BTC: {btc_cov:.0f}% (was {last_coverage_alert:.0f}%)\n"
                                     f"ETH: {coverage.get('eth', 0):.0f}%")
                    last_coverage_alert = btc_cov

                # Capture data
                results = self.capture_once()

                # Print status every 10 iterations
                iteration += 1
                if verbose and iteration % 10 == 0:
                    self._print_status(results)

                # Tick-based sleep: wait until next scheduled tick
                sleep_time = next_tick - time.time()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                next_tick += POLL_INTERVAL

        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
        finally:
            self.running = False
            self._print_summary()
            self.stop_feeds()

    def _print_status(self, results: dict):
        """Print current capture status."""
        dt = datetime.utcnow().strftime("%H:%M:%S")
        total_rows = sum(self.window_row_counts.values())

        # Calculate average source count across all assets
        source_counts = []
        for asset in self.assets:
            feed = self.price_feeds.get(asset)
            if feed:
                source_counts.append(feed.get_source_count())
        avg_sources = sum(source_counts) / len(source_counts) if source_counts else 0

        print(f"\n[{dt}] Captured {len(results)} markets | {total_rows} rows | avg {avg_sources:.1f} sources:")

        for key, row in results.items():
            asset, tf = key.split("_")
            ex_price = row.get("exchange_price", "N/A")
            up = row.get("up_price", "N/A")
            down = row.get("down_price", "N/A")
            time_left = int(float(row.get("time_remaining", 0)))
            rows = self.window_row_counts.get(key, 0)
            src = row.get("source_count", 0)

            print(f"  {asset.upper():4} {tf:4} | ${ex_price:>10} | "
                  f"UP={up:>6} DOWN={down:>6} | {time_left:4}s left | {rows:4} rows | {src} src")

    def _print_summary(self):
        """Print capture summary."""
        elapsed = time.time() - self.stats["start_time"]
        print("\n" + "=" * 60)
        print("Capture Summary")
        print("=" * 60)
        print(f"Duration: {elapsed:.0f}s ({elapsed/60:.1f}m)")
        print(f"Total captures: {self.stats['captures']}")
        print(f"Errors: {self.stats['errors']}")
        print(f"Rate: {self.stats['captures']/max(1, elapsed):.2f}/s")
        print("=" * 60)

        print("\nData files:")
        for asset in self.assets:
            for tf in self.timeframes:
                path = self._get_csv_path(asset, tf)
                if path.exists():
                    lines = sum(1 for _ in open(path)) - 1  # -1 for header
                    print(f"  {path.name}: {lines} rows")


def main():
    parser = argparse.ArgumentParser(description="Multi-market data capture")
    parser.add_argument("--assets", nargs="+", default=ASSETS,
                        help="Assets to capture (btc eth sol xrp)")
    parser.add_argument("--timeframes", nargs="+", default=list(TIMEFRAMES.keys()),
                        help="Timeframes to capture (5m 15m 1hr)")
    parser.add_argument("--duration", type=int, default=None,
                        help="Duration in seconds (default: unlimited)")
    parser.add_argument("--quiet", action="store_true",
                        help="Reduce output verbosity")

    args = parser.parse_args()

    capture = DataCapture(
        assets=args.assets,
        timeframes=args.timeframes,
    )

    capture.run(duration=args.duration, verbose=not args.quiet)


if __name__ == "__main__":
    main()
