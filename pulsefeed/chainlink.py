"""
Chainlink Data Streams - Real-time BTC/USD Feed

Connects to Chainlink's official Data Streams WebSocket API.
Updates every ~500ms with the exact price Polymarket uses.

Setup:
1. Sign up at https://cre.chain.link/getting-started
2. Get your API_KEY and API_SECRET
3. Set environment variables or pass to constructor

BTC/USD Stream ID: 0x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8
"""

import hashlib
import hmac
import json
import os
import time
import threading
from typing import Optional
import websocket
import requests


# BTC/USD Data Stream ID (from data.chain.link)
BTC_USD_STREAM_ID = "0x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8"


class ChainlinkFeed:
    """
    Chainlink Data Streams WebSocket client for real-time BTC/USD prices.

    If no API keys provided, falls back to Kraken REST polling.
    """

    # Chainlink Data Streams endpoints
    WS_URL_MAINNET = "wss://ws.dataengine.chain.link"
    WS_URL_TESTNET = "wss://ws.testnet-dataengine.chain.link"

    # Fallback
    KRAKEN_API = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = False,
        stream_id: str = BTC_USD_STREAM_ID,
        poll_interval: float = 1.0,  # Fallback polling interval
    ):
        """
        Args:
            api_key: Chainlink API key (or set CHAINLINK_API_KEY env var)
            api_secret: Chainlink API secret (or set CHAINLINK_API_SECRET env var)
            testnet: Use testnet endpoint (default: mainnet)
            stream_id: Data stream ID (default: BTC/USD)
            poll_interval: Fallback polling interval if no API keys
        """
        self.api_key = api_key or os.getenv("CHAINLINK_API_KEY")
        self.api_secret = api_secret or os.getenv("CHAINLINK_API_SECRET")
        self.testnet = testnet
        self.stream_id = stream_id
        self.poll_interval = poll_interval

        self.price: Optional[float] = None
        self.last_update: float = 0
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._ws: Optional[websocket.WebSocketApp] = None
        self._connected = False
        self._using_chainlink = False

    def start(self) -> bool:
        """Start the price feed."""
        self.running = True

        # Try Chainlink if we have credentials
        if self.api_key and self.api_secret:
            self._using_chainlink = True
            self._thread = threading.Thread(target=self._run_chainlink_ws, daemon=True)
        else:
            # Fallback to Kraken REST
            self._using_chainlink = False
            self._thread = threading.Thread(target=self._run_kraken_poll, daemon=True)

        self._thread.start()

        # Wait for first price
        start = time.time()
        while self.price is None and time.time() - start < 10:
            time.sleep(0.1)

        return self.price is not None

    def stop(self):
        """Stop the feed."""
        self.running = False
        if self._ws:
            try:
                self._ws.close()
            except:
                pass
        if self._thread:
            self._thread.join(timeout=2)

    # =========================================================================
    # Chainlink Data Streams WebSocket
    # =========================================================================

    def _generate_signature(self, method: str, path: str, body: str = "") -> tuple:
        """Generate HMAC-SHA256 signature for Chainlink auth."""
        timestamp = int(time.time() * 1000)

        # Body hash (empty for GET/WebSocket)
        body_hash = hashlib.sha256(body.encode()).hexdigest() if body else ""

        # String to sign: METHOD PATH BODY_HASH API_KEY TIMESTAMP
        string_to_sign = f"{method} {path} {body_hash} {self.api_key} {timestamp}"

        # HMAC-SHA256
        signature = hmac.new(
            self.api_secret.encode(),
            string_to_sign.encode(),
            hashlib.sha256
        ).hexdigest()

        return signature, timestamp

    def _run_chainlink_ws(self):
        """Run Chainlink Data Streams WebSocket connection."""
        ws_base = self.WS_URL_TESTNET if self.testnet else self.WS_URL_MAINNET
        path = f"/api/v1/ws?feedIDs={self.stream_id}"
        full_url = f"{ws_base}{path}"

        while self.running:
            try:
                # Generate auth headers
                signature, timestamp = self._generate_signature("GET", path)

                headers = {
                    "Authorization": self.api_key,
                    "X-Authorization-Timestamp": str(timestamp),
                    "X-Authorization-Signature-SHA256": signature,
                }

                self._ws = websocket.WebSocketApp(
                    full_url,
                    header=headers,
                    on_open=self._on_chainlink_open,
                    on_message=self._on_chainlink_message,
                    on_error=self._on_chainlink_error,
                    on_close=self._on_chainlink_close,
                )
                self._ws.run_forever(ping_interval=30)

            except Exception as e:
                print(f"[chainlink] Connection error: {e}")

            if self.running:
                time.sleep(2)

    def _on_chainlink_open(self, ws):
        """WebSocket connected."""
        self._connected = True
        print(f"[chainlink] Connected to Data Streams ({'testnet' if self.testnet else 'mainnet'})")

    def _on_chainlink_message(self, ws, message):
        """Parse Chainlink report."""
        try:
            data = json.loads(message)

            if "report" in data:
                report = data["report"]
                full_report = report.get("fullReport", "")

                # The fullReport is hex-encoded, need to decode
                # Format varies by schema version, but benchmark price is in there
                # For now, we'll use the REST API to get decoded price
                self._fetch_latest_report()

        except Exception as e:
            pass

    def _on_chainlink_error(self, ws, error):
        """Handle WebSocket error."""
        print(f"[chainlink] Error: {error}")
        self._connected = False

    def _on_chainlink_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket close."""
        self._connected = False
        if self.running:
            print(f"[chainlink] Disconnected, reconnecting...")

    def _fetch_latest_report(self):
        """Fetch and decode latest report via REST API."""
        try:
            host = "api.testnet-dataengine.chain.link" if self.testnet else "api.dataengine.chain.link"
            path = f"/api/v1/reports/latest?feedID={self.stream_id}"

            signature, timestamp = self._generate_signature("GET", path)

            headers = {
                "Authorization": self.api_key,
                "X-Authorization-Timestamp": str(timestamp),
                "X-Authorization-Signature-SHA256": signature,
            }

            resp = requests.get(f"https://{host}{path}", headers=headers, timeout=5)

            if resp.status_code == 200:
                data = resp.json()
                report = data.get("report", {})

                # Extract benchmark price (stored as big int, need to convert)
                # Price is in 18 decimals for crypto streams
                # This is simplified - full decode would use the SDK
                if "benchmarkPrice" in report:
                    raw_price = int(report["benchmarkPrice"])
                    self.price = raw_price / 1e18
                    self.last_update = time.time()

        except Exception as e:
            pass

    # =========================================================================
    # Kraken REST Fallback
    # =========================================================================

    def _run_kraken_poll(self):
        """Fallback: Poll Kraken REST API."""
        while self.running:
            try:
                resp = requests.get(self.KRAKEN_API, timeout=2)
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("result", {})
                    ticker = result.get("XXBTZUSD") or result.get("XBTUSD")
                    if ticker:
                        price = float(ticker["c"][0])
                        if price > 0:
                            self.price = price
                            self.last_update = time.time()
            except Exception:
                pass

            time.sleep(self.poll_interval)

    # =========================================================================
    # Public API
    # =========================================================================

    def get_price(self) -> Optional[float]:
        """Get current BTC/USD price."""
        return self.price

    def get_age(self) -> float:
        """Get age of last update in seconds."""
        if self.last_update == 0:
            return float('inf')
        return time.time() - self.last_update

    def is_using_chainlink(self) -> bool:
        """Check if using real Chainlink feed (vs fallback)."""
        return self._using_chainlink and self._connected


def calculate_oracle_lag(
    realtime_price: float,
    oracle_price: float
) -> dict:
    """
    Calculate divergence between real-time and oracle price.

    Returns:
        dict with divergence_pct, divergence_bps, signal, strength
    """
    if oracle_price <= 0:
        return {
            "divergence_pct": 0,
            "divergence_bps": 0,
            "signal": "NEUTRAL",
            "strength": 0,
        }

    divergence_pct = ((realtime_price - oracle_price) / oracle_price) * 100
    divergence_bps = divergence_pct * 100

    # Signal direction (5 bps threshold)
    if divergence_pct > 0.05:
        signal = "LONG"
    elif divergence_pct < -0.05:
        signal = "SHORT"
    else:
        signal = "NEUTRAL"

    # Strength: 0-1, maxes at 50 bps
    strength = min(1.0, abs(divergence_bps) / 50)

    return {
        "divergence_pct": divergence_pct,
        "divergence_bps": divergence_bps,
        "signal": signal,
        "strength": strength,
    }


# Test
if __name__ == "__main__":
    print("Testing Chainlink Feed...")
    print(f"API Key set: {bool(os.getenv('CHAINLINK_API_KEY'))}")
    print(f"API Secret set: {bool(os.getenv('CHAINLINK_API_SECRET'))}")

    feed = ChainlinkFeed()

    if feed.start():
        source = "Chainlink Data Streams" if feed.is_using_chainlink() else "Kraken REST (fallback)"
        print(f"\nUsing: {source}")
        print(f"Initial price: ${feed.get_price():,.2f}\n")

        try:
            for i in range(20):
                price = feed.get_price()
                age = feed.get_age()
                print(f"${price:,.2f} (age: {age:.2f}s)")
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")

        feed.stop()
    else:
        print("Failed to start feed")
