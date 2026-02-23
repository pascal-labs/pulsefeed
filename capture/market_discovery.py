"""
Multi-Market Discovery Module for Polymarket

Discovers and tracks active markets for:
- Assets: BTC, ETH, SOL, XRP
- Timeframes: 15m, 1hr

Market slug patterns:
- 15m: {asset}-updown-15m-{timestamp} (e.g., btc-updown-15m-1769521500)
- 1hr: {fullname}-up-or-down-{month}-{day}-{hour}am-et (e.g., bitcoin-up-or-down-january-27-8am-et)
"""

import requests
import time
from datetime import datetime, timezone
from typing import Dict, Optional, List
from dataclasses import dataclass

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Asset configurations
ASSETS = ["btc", "eth", "sol", "xrp"]

# Full asset names for 1hr market slugs
ASSET_FULL_NAMES = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
    "xrp": "xrp",
}

TIMEFRAMES = {
    "5m": 300,     # 5 minutes in seconds
    "15m": 900,    # 15 minutes in seconds
    "1hr": 3600,   # 1 hour in seconds
}

# Month name mapping for 1hr slugs
MONTH_NAMES = [
    "", "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december"
]


@dataclass
class Market:
    """Represents a single Polymarket prediction market."""
    slug: str
    asset: str
    timeframe: str
    start_timestamp: int
    up_token: str
    down_token: str
    up_price: Optional[float] = None
    down_price: Optional[float] = None
    volume: float = 0.0
    liquidity: float = 0.0

    @property
    def end_timestamp(self) -> int:
        """Market resolution time."""
        return self.start_timestamp + TIMEFRAMES[self.timeframe]

    @property
    def time_remaining(self) -> float:
        """Seconds until resolution."""
        return max(0, self.end_timestamp - time.time())

    @property
    def is_active(self) -> bool:
        """Market hasn't resolved yet."""
        return self.time_remaining > 0


class MarketDiscovery:
    """
    Discovers and tracks active Polymarket prediction markets.

    Usage:
        discovery = MarketDiscovery()
        markets = discovery.get_active_markets()
        for m in markets:
            print(f"{m.slug}: UP={m.up_price} DOWN={m.down_price}")
    """

    def __init__(self, assets: List[str] = None, timeframes: List[str] = None):
        """
        Initialize market discovery.

        Args:
            assets: List of assets to track (default: all)
            timeframes: List of timeframes to track (default: all)
        """
        self.assets = [a.lower() for a in (assets or ASSETS)]
        self.timeframes = timeframes or list(TIMEFRAMES.keys())
        self._cache: Dict[str, Market] = {}
        self._last_fetch: Dict[str, float] = {}

    def _build_slug(self, asset: str, timeframe: str, timestamp: int) -> str:
        """
        Build market slug from components.

        15m format: {asset}-updown-15m-{timestamp}
        1hr format: {fullname}-up-or-down-{month}-{day}-{hour}am-et
        """
        asset = asset.lower()

        if timeframe in ("5m", "15m"):
            return f"{asset}-updown-{timeframe}-{timestamp}"
        elif timeframe == "1hr":
            # Convert timestamp to ET and build readable slug
            # ET is UTC-5 (EST) or UTC-4 (EDT) - using UTC-5 as approximation
            from datetime import timezone, timedelta
            et_offset = timedelta(hours=-5)  # EST
            dt_utc = datetime.utcfromtimestamp(timestamp)
            dt_et = dt_utc + et_offset

            month = MONTH_NAMES[dt_et.month]
            day = dt_et.day
            hour = dt_et.hour

            # Format hour as 12-hour with am/pm
            if hour == 0:
                hour_str = "12am"
            elif hour < 12:
                hour_str = f"{hour}am"
            elif hour == 12:
                hour_str = "12pm"
            else:
                hour_str = f"{hour - 12}pm"

            full_name = ASSET_FULL_NAMES.get(asset, asset)
            return f"{full_name}-up-or-down-{month}-{day}-{hour_str}-et"
        else:
            return f"{asset}-updown-{timeframe}-{timestamp}"

    def _get_current_start_timestamp(self, timeframe: str) -> int:
        """Get the start timestamp for the current market window."""
        interval = TIMEFRAMES[timeframe]
        now = int(time.time())
        return (now // interval) * interval

    def _fetch_market_data(self, slug: str) -> Optional[dict]:
        """Fetch market data from Gamma API."""
        import json as json_module

        try:
            url = f"{GAMMA_API}/events?slug={slug}"
            response = requests.get(url, timeout=3)  # Reduced from 10s to 3s

            if response.status_code != 200:
                return None

            data = response.json()
            if not data:
                return None

            event = data[0]
            markets = event.get("markets", [])
            if not markets:
                return None

            market = markets[0]

            # Get token IDs from clobTokenIds (may be JSON string or list)
            clob_token_ids = market.get("clobTokenIds", [])
            outcomes = market.get("outcomes", [])
            outcome_prices = market.get("outcomePrices", [])

            # Parse if they're strings (API sometimes returns JSON strings)
            if isinstance(clob_token_ids, str):
                clob_token_ids = json_module.loads(clob_token_ids)
            if isinstance(outcomes, str):
                outcomes = json_module.loads(outcomes)
            if isinstance(outcome_prices, str):
                outcome_prices = json_module.loads(outcome_prices)

            if len(clob_token_ids) < 2 or len(outcomes) < 2:
                # Fallback to old tokens field
                tokens = market.get("tokens", [])
                if len(tokens) < 2:
                    return None

                up_token = down_token = None
                for token in tokens:
                    outcome = token.get("outcome", "").upper()
                    if outcome == "UP":
                        up_token = token.get("token_id")
                    elif outcome == "DOWN":
                        down_token = token.get("token_id")
            else:
                # Map outcomes to token IDs
                up_token = down_token = None
                up_price = down_price = None

                for i, outcome in enumerate(outcomes):
                    outcome_upper = outcome.upper()
                    if outcome_upper == "UP":
                        up_token = clob_token_ids[i]
                        if i < len(outcome_prices):
                            up_price = float(outcome_prices[i])
                    elif outcome_upper == "DOWN":
                        down_token = clob_token_ids[i]
                        if i < len(outcome_prices):
                            down_price = float(outcome_prices[i])

            if not up_token or not down_token:
                return None

            result = {
                "up_token": up_token,
                "down_token": down_token,
                "volume": float(market.get("volume", 0) or 0),
                "liquidity": float(market.get("liquidity", 0) or 0),
            }

            # Include prices if available from outcomePrices
            if up_price is not None:
                result["up_price"] = up_price
            if down_price is not None:
                result["down_price"] = down_price

            return result
        except Exception as e:
            print(f"Error fetching market {slug}: {e}")
            return None

    def _fetch_prices(self, up_token: str, down_token: str) -> tuple:
        """Fetch current prices from CLOB API."""
        up_price = down_price = None

        try:
            # Fetch UP token price
            resp = requests.get(f"{CLOB_API}/book?token_id={up_token}", timeout=2)  # Reduced from 5s to 2s
            if resp.status_code == 200:
                book = resp.json()
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                if bids and asks:
                    best_bid = float(bids[0].get("price", 0))
                    best_ask = float(asks[0].get("price", 0))
                    up_price = (best_bid + best_ask) / 2

            # Fetch DOWN token price
            resp = requests.get(f"{CLOB_API}/book?token_id={down_token}", timeout=2)  # Reduced from 5s to 2s
            if resp.status_code == 200:
                book = resp.json()
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                if bids and asks:
                    best_bid = float(bids[0].get("price", 0))
                    best_ask = float(asks[0].get("price", 0))
                    down_price = (best_bid + best_ask) / 2
        except Exception as e:
            print(f"Error fetching prices: {e}")

        return up_price, down_price

    def get_market(self, asset: str, timeframe: str) -> Optional[Market]:
        """
        Get the current active market for an asset/timeframe.

        Args:
            asset: Asset symbol (btc, eth, sol, xrp)
            timeframe: Timeframe (15m, 1hr)

        Returns:
            Market object or None if not found
        """
        asset = asset.lower()
        start_ts = self._get_current_start_timestamp(timeframe)
        slug = self._build_slug(asset, timeframe, start_ts)

        # Check cache freshness (5 second TTL for prices, market data cached longer)
        if slug in self._cache:
            cached = self._cache[slug]
            age = time.time() - self._last_fetch.get(slug, 0)

            # Refresh prices every 5 seconds
            if age < 5:
                return cached

            # Just refresh prices, not full market data
            up_price, down_price = self._fetch_prices(cached.up_token, cached.down_token)
            if up_price and down_price:
                cached.up_price = up_price
                cached.down_price = down_price
                self._last_fetch[slug] = time.time()
            return cached

        # Fetch full market data
        market_data = self._fetch_market_data(slug)
        if not market_data:
            return None

        # Use prices from API if available, otherwise fetch from CLOB
        up_price = market_data.get("up_price")
        down_price = market_data.get("down_price")

        if up_price is None or down_price is None:
            fetched_up, fetched_down = self._fetch_prices(
                market_data["up_token"],
                market_data["down_token"]
            )
            up_price = fetched_up if up_price is None else up_price
            down_price = fetched_down if down_price is None else down_price

        market = Market(
            slug=slug,
            asset=asset,
            timeframe=timeframe,
            start_timestamp=start_ts,
            up_token=market_data["up_token"],
            down_token=market_data["down_token"],
            up_price=up_price,
            down_price=down_price,
            volume=market_data["volume"],
            liquidity=market_data["liquidity"],
        )

        self._cache[slug] = market
        self._last_fetch[slug] = time.time()
        return market

    def _get_next_market(self, asset: str, timeframe: str, next_start: int) -> Optional[Market]:
        """
        Get the next window's market for pre-connection.

        Args:
            asset: Asset symbol (btc, eth, sol, xrp)
            timeframe: Timeframe (15m, 1hr)
            next_start: Start timestamp of the next window

        Returns:
            Market object or None if not found
        """
        asset = asset.lower()
        slug = self._build_slug(asset, timeframe, next_start)

        # Check if already cached
        if slug in self._cache:
            return self._cache[slug]

        # Fetch market data for next window
        market_data = self._fetch_market_data(slug)
        if not market_data:
            return None

        market = Market(
            slug=slug,
            asset=asset,
            timeframe=timeframe,
            start_timestamp=next_start,
            up_token=market_data["up_token"],
            down_token=market_data["down_token"],
            up_price=market_data.get("up_price"),
            down_price=market_data.get("down_price"),
            volume=market_data.get("volume", 0),
            liquidity=market_data.get("liquidity", 0),
        )

        self._cache[slug] = market
        self._last_fetch[slug] = time.time()
        return market

    def get_active_markets(self) -> List[Market]:
        """
        Get all active markets for configured assets/timeframes.

        Returns:
            List of Market objects for current windows
        """
        markets = []

        for asset in self.assets:
            for timeframe in self.timeframes:
                market = self.get_market(asset, timeframe)
                if market and market.is_active:
                    markets.append(market)

        return markets

    def cleanup_old_markets(self):
        """Remove expired markets from cache."""
        now = time.time()
        expired = [
            slug for slug, market in self._cache.items()
            if not market.is_active
        ]
        for slug in expired:
            del self._cache[slug]
            if slug in self._last_fetch:
                del self._last_fetch[slug]

    def print_status(self):
        """Print current market status."""
        markets = self.get_active_markets()

        print(f"\n{'='*60}")
        print(f"Active Markets: {len(markets)}")
        print(f"{'='*60}")

        for m in markets:
            mins_left = m.time_remaining / 60
            spread = (m.up_price or 0) + (m.down_price or 0) - 1.0

            up_str = f"${m.up_price:.4f}" if m.up_price else "N/A"
            down_str = f"${m.down_price:.4f}" if m.down_price else "N/A"

            print(f"{m.asset.upper():4} {m.timeframe:4} | UP={up_str} DOWN={down_str} | "
                  f"spread={spread:+.4f} | {mins_left:.1f}m left")

        print(f"{'='*60}\n")


if __name__ == "__main__":
    print("Testing MarketDiscovery...")

    discovery = MarketDiscovery()

    # Test getting all active markets
    markets = discovery.get_active_markets()
    discovery.print_status()

    print(f"\nFound {len(markets)} active markets")
    for m in markets:
        print(f"  {m.slug}")
