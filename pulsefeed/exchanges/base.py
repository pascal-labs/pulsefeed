"""
Base Exchange Feed

Abstract base class for all exchange WebSocket connectors.
Follows the same pattern as btc_price_feed.py for consistency.
"""

import asyncio
import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional, Callable

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

logger = logging.getLogger(__name__)


class ExchangeFeed(ABC):
    """
    Abstract base class for exchange WebSocket price feeds.

    Each exchange connector inherits from this and implements:
    - _get_url(): Return WebSocket URL
    - _get_subscribe_message(): Return subscription message (or None)
    - _parse_message(): Extract price from exchange-specific message format
    """

    def __init__(self, name: str):
        self.name = name
        self.price: Optional[float] = None
        self.bid: Optional[float] = None
        self.ask: Optional[float] = None
        self.last_update: float = 0
        self.connected = False
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws = None

        # Reconnection settings
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0
        self._reconnect_backoff = 1.5

        # Statistics
        self.message_count = 0
        self.error_count = 0
        self.reconnect_count = 0

        # Callback for price updates
        self._on_price_update: Optional[Callable[[str, float], None]] = None

    @abstractmethod
    def _get_url(self) -> str:
        """Return the WebSocket URL for this exchange."""
        pass

    @abstractmethod
    def _get_subscribe_message(self) -> Optional[dict]:
        """Return subscription message to send after connecting, or None."""
        pass

    @abstractmethod
    def _parse_message(self, data: dict) -> Optional[float]:
        """
        Parse exchange-specific message and return price, or None if not a price message.

        Should also update self.bid and self.ask if available.
        """
        pass

    def start(self, on_price_update: Optional[Callable[[str, float], None]] = None) -> bool:
        """Start the WebSocket feed in background thread."""
        if not WEBSOCKETS_AVAILABLE:
            logger.error(f"[{self.name}] websockets library not installed")
            return False

        self._on_price_update = on_price_update
        self.running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()

        # Wait for connection (up to 5 seconds)
        start = time.time()
        while not self.connected and time.time() - start < 5:
            time.sleep(0.1)

        return self.connected

    def stop(self):
        """Stop the WebSocket feed gracefully."""
        self.running = False
        self.connected = False

        # Close WebSocket
        if self._ws and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
                time.sleep(0.1)
            except Exception:
                pass

        # Stop event loop
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        # Wait for thread
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
                logger.error(f"[{self.name}] Event loop error: {e}")
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            self._loop.close()

    async def _connect_loop(self):
        """Connection loop with auto-reconnect."""
        while self.running:
            try:
                await self._connect_and_subscribe()
            except Exception as e:
                logger.warning(f"[{self.name}] Disconnected: {e}")
                self.connected = False
                self.error_count += 1

            if self.running:
                self.reconnect_count += 1
                await asyncio.sleep(self._reconnect_delay)
                # Backoff but cap at max
                self._reconnect_delay = min(
                    self._reconnect_delay * self._reconnect_backoff,
                    self._max_reconnect_delay
                )

    async def _connect_and_subscribe(self):
        """Connect to WebSocket and subscribe to price updates."""
        url = self._get_url()
        logger.debug(f"[{self.name}] Connecting to {url}")

        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0  # Reset on success

            # Send subscription message if needed
            subscribe_msg = self._get_subscribe_message()
            if subscribe_msg:
                await ws.send(json.dumps(subscribe_msg))

            self.connected = True
            logger.info(f"[{self.name}] Connected")

            # Process messages
            async for message in ws:
                if not self.running:
                    break

                try:
                    self._handle_message(message)
                except Exception as e:
                    logger.debug(f"[{self.name}] Message parse error: {e}")

    def _handle_message(self, raw_message: str):
        """Parse and handle incoming WebSocket message."""
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        self.message_count += 1

        # Let subclass parse the message
        price = self._parse_message(data)
        if price is not None and price > 0:
            self.price = price
            self.last_update = time.time()

            # Notify callback
            if self._on_price_update:
                self._on_price_update(self.name, price)

    def get_price(self) -> Optional[float]:
        """Get current price."""
        return self.price

    def get_bid_ask(self) -> tuple[Optional[float], Optional[float]]:
        """Get current bid and ask prices."""
        return self.bid, self.ask

    def get_age(self) -> float:
        """Get age of last price update in seconds."""
        if self.last_update == 0:
            return float('inf')
        return time.time() - self.last_update

    def is_healthy(self, max_age: float = 2.0) -> bool:
        """Check if feed is connected and data is fresh."""
        return self.connected and self.get_age() < max_age

    def get_stats(self) -> dict:
        """Get feed statistics."""
        return {
            "name": self.name,
            "connected": self.connected,
            "price": self.price,
            "age": self.get_age(),
            "messages": self.message_count,
            "errors": self.error_count,
            "reconnects": self.reconnect_count,
        }
