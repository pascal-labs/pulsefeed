"""
PulseFeed Data Models

Dataclasses for price reports and source snapshots.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
import hashlib
import json
import time


@dataclass
class SourceSnapshot:
    """Snapshot of price data from a single exchange."""
    exchange: str
    price: float
    timestamp_ms: int
    bid: Optional[float] = None
    ask: Optional[float] = None

    def get_age_ms(self) -> int:
        """Get age of this snapshot in milliseconds."""
        return int(time.time() * 1000) - self.timestamp_ms

    def get_age(self) -> float:
        """Get age of this snapshot in seconds."""
        return self.get_age_ms() / 1000.0


@dataclass
class PriceReport:
    """Aggregated price report from multiple exchanges."""
    feed_id: str = "BTC-USD"
    price: float = 0.0  # Aggregated price
    price_int: int = 0  # Fixed-point: price * 1e8
    timestamp_ms: int = 0
    sequence_id: int = 0
    source_count: int = 0
    sources: Dict[str, float] = field(default_factory=dict)
    confidence: float = 1.0  # 0-1, based on exchange agreement
    divergence: float = 0.0  # Max spread % between exchanges
    hash: str = ""  # SHA256 for integrity

    def __post_init__(self):
        if self.price > 0 and self.price_int == 0:
            self.price_int = int(self.price * 1e8)
        if not self.hash:
            self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute SHA256 hash of report data."""
        data = {
            "feed_id": self.feed_id,
            "price_int": self.price_int,
            "timestamp_ms": self.timestamp_ms,
            "sequence_id": self.sequence_id,
            "source_count": self.source_count
        }
        data_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "feed_id": self.feed_id,
            "price": self.price,
            "price_int": self.price_int,
            "timestamp_ms": self.timestamp_ms,
            "sequence_id": self.sequence_id,
            "source_count": self.source_count,
            "sources": self.sources,
            "confidence": self.confidence,
            "divergence": self.divergence,
            "hash": self.hash
        }


@dataclass
class ExchangeState:
    """Internal state for tracking exchange health."""
    exchange: str
    connected: bool = False
    last_price: Optional[float] = None
    last_update_ms: int = 0
    error_count: int = 0
    reconnect_count: int = 0

    def is_healthy(self, max_staleness_ms: int = 2000) -> bool:
        """Check if exchange data is healthy (connected and fresh)."""
        if not self.connected or self.last_price is None:
            return False
        age_ms = int(time.time() * 1000) - self.last_update_ms
        return age_ms < max_staleness_ms

    def get_age(self) -> float:
        """Get age of last update in seconds."""
        if self.last_update_ms == 0:
            return float('inf')
        return (time.time() * 1000 - self.last_update_ms) / 1000.0
