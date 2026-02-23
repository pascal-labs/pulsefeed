"""
Binance BTC/USDT WebSocket Price Feed

Real-time BTC price for momentum filtering.
Uses Binance's free public websocket.
"""

import asyncio
import json
import time
import threading
from typing import Optional

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

WSS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"


class BTCPriceFeed:
    """Real-time BTC price feed from Binance."""

    def __init__(self):
        self.price: Optional[float] = None
        self.last_update: float = 0
        self.connected = False
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Track window start price for momentum calculation
        self.window_start_price: Optional[float] = None
        self.window_start_time: Optional[float] = None

    def start(self) -> bool:
        """Start the websocket feed in background thread."""
        if not WEBSOCKETS_AVAILABLE:
            print("websockets library not installed")
            return False

        self.running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()

        # Wait for connection
        start = time.time()
        while not self.connected and time.time() - start < 5:
            time.sleep(0.1)

        return self.connected

    def stop(self):
        """Stop the feed."""
        self.running = False
        self.connected = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=1)

    def _run_event_loop(self):
        """Run asyncio event loop in background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except Exception as e:
            if self.running:
                print(f"BTC feed error: {e}")
        finally:
            self._loop.close()

    async def _connect_loop(self):
        """Connection loop with auto-reconnect."""
        while self.running:
            try:
                await self._connect()
            except Exception as e:
                print(f"BTC WebSocket disconnected: {e}")
                self.connected = False

            if self.running:
                await asyncio.sleep(1)

    async def _connect(self):
        """Connect and process messages."""
        async with websockets.connect(WSS_URL, ping_interval=20) as ws:
            self.connected = True
            print("  ₿ BTC price feed connected (Binance)")

            async for message in ws:
                if not self.running:
                    break

                try:
                    data = json.loads(message)
                    # Binance trade message: {"p": "97000.50", ...}
                    if "p" in data:
                        self.price = float(data["p"])
                        self.last_update = time.time()
                except Exception:
                    pass

    def get_price(self) -> Optional[float]:
        """Get current BTC price."""
        return self.price

    def get_age(self) -> float:
        """Get age of last price update in seconds."""
        if self.last_update == 0:
            return float('inf')
        return time.time() - self.last_update

    def mark_window_start(self):
        """Mark current price as window start for momentum calculation."""
        self.window_start_price = self.price
        self.window_start_time = time.time()

    def get_momentum(self) -> Optional[float]:
        """
        Get price change since window start as percentage.
        Returns None if no window start marked or no current price.

        Positive = price up, Negative = price down
        """
        if self.window_start_price is None or self.price is None:
            return None

        return ((self.price - self.window_start_price) / self.window_start_price) * 100

    def get_momentum_abs(self) -> Optional[float]:
        """Get absolute momentum (ignores direction)."""
        m = self.get_momentum()
        return abs(m) if m is not None else None


# Test
if __name__ == "__main__":
    print("Testing BTC Price Feed...")

    feed = BTCPriceFeed()
    if feed.start():
        print(f"Connected! Waiting for prices...\n")

        # Mark window start
        time.sleep(1)
        feed.mark_window_start()
        print(f"Window start price: ${feed.window_start_price:,.2f}")

        for i in range(20):
            price = feed.get_price()
            age = feed.get_age()
            momentum = feed.get_momentum()

            if price:
                mom_str = f"{momentum:+.4f}%" if momentum is not None else "N/A"
                print(f"[{i}s] BTC=${price:,.2f}  age={age:.2f}s  momentum={mom_str}")
            else:
                print(f"[{i}s] Waiting...")

            time.sleep(1)

        feed.stop()
        print("\nFeed stopped.")
    else:
        print("Failed to connect")
