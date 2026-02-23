#!/usr/bin/env python3
"""
Record L2 orderbook snapshots from Polymarket websocket.

Hooks into the existing WebSocketPriceFeed and writes every book event
to a JSONL file. Run alongside the bot or standalone.

Output: data/l2/{market_slug}.jsonl.gz
Each line: {"ts": 1234567890.123, "asset": "up"|"dn", "event": "book"|"price_change",
            "bids": [[price, size], ...], "asks": [[price, size], ...]}

Usage:
  # Standalone (records current + next windows continuously):
  python3 record_l2.py

  # Or import and attach to existing bot:
  from record_l2 import L2Recorder
  recorder = L2Recorder(market_slug)
  feed = WebSocketPriceFeed(on_price_update=recorder.on_update)
"""

import asyncio
import gzip
import json
import os
import time
import threading
from pathlib import Path
from dataclasses import dataclass

try:
    import websockets
except ImportError:
    print("pip install websockets")
    raise

import requests

GAMMA_API = "https://gamma-api.polymarket.com"
WSS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
DATA_DIR = Path("data/l2")


class L2Recorder:
    """Records every WS event to a compressed JSONL file."""

    def __init__(self, market_slug: str):
        self.market_slug = market_slug
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = DATA_DIR / f"{market_slug}.jsonl.gz"
        self._f = gzip.open(self.path, "at")
        self._lock = threading.Lock()
        self._count = 0
        self._book_count = 0
        self._tick_count = 0
        self._trade_count = 0
        self._start = time.time()
        self._last_status = 0
        self.up_token = None
        self.dn_token = None

    def write_event(self, asset_label: str, event_type: str, bids: list, asks: list):
        record = {
            "ts": round(time.time(), 3),
            "asset": asset_label,
            "event": event_type,
            "bids": bids,
            "asks": asks,
        }
        line = json.dumps(record, separators=(",", ":"))
        with self._lock:
            self._f.write(line + "\n")
            self._count += 1
            if event_type == "book":
                self._book_count += 1
            else:
                self._tick_count += 1
            if self._count % 100 == 0:
                self._f.flush()
            self._maybe_status()

    def write_trade(self, asset_label: str, price: float, size: float, side: str):
        """Record a last_trade_price event (maker/taker match)."""
        record = {
            "ts": round(time.time(), 3),
            "asset": asset_label,
            "event": "trade",
            "price": price,
            "size": size,
            "side": side,
        }
        line = json.dumps(record, separators=(",", ":"))
        with self._lock:
            self._f.write(line + "\n")
            self._count += 1
            self._trade_count += 1
            if self._count % 100 == 0:
                self._f.flush()
            self._maybe_status()

    def _maybe_status(self):
        now = time.time()
        if now - self._last_status >= 30:
            elapsed = now - self._start
            sz = self.path.stat().st_size / 1024
            print(f"  [{elapsed:.0f}s] {self._count} events "
                  f"({self._book_count} books, {self._tick_count} ticks, "
                  f"{self._trade_count} trades) "
                  f"| {sz:.0f}KB", flush=True)
            self._last_status = now

    def close(self):
        with self._lock:
            self._f.flush()
            self._f.close()
        print(f"  Saved {self._count} events to {self.path}")


async def record_market(slug: str, up_token: str, dn_token: str, duration_secs: float):
    """Connect to WS and record all L2 events for one market window."""
    recorder = L2Recorder(slug)
    recorder.up_token = up_token
    recorder.dn_token = dn_token

    token_label = {up_token: "up", dn_token: "dn"}
    deadline = time.time() + duration_secs

    try:
        async with websockets.connect(
            WSS_URL, ping_interval=20, ping_timeout=10, close_timeout=5
        ) as ws:
            sub = {"assets_ids": [up_token, dn_token], "type": "market"}
            await ws.send(json.dumps(sub))
            print(f"  WS connected. Recording {slug} for {duration_secs:.0f}s...", flush=True)

            async for raw in ws:
                if time.time() > deadline:
                    break

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                events = data if isinstance(data, list) else [data]
                for ev in events:
                    if not isinstance(ev, dict):
                        continue

                    etype = ev.get("event_type")
                    asset_id = ev.get("asset_id")

                    if etype == "book" and asset_id in token_label:
                        bids = [[float(b["price"]), float(b["size"])] for b in ev.get("bids", [])]
                        asks = [[float(a["price"]), float(a["size"])] for a in ev.get("asks", [])]
                        recorder.write_event(token_label[asset_id], "book", bids, asks)

                    elif etype == "last_trade_price" and asset_id in token_label:
                        price = ev.get("price")
                        size = ev.get("size")
                        side = ev.get("side", "")
                        if price and size:
                            recorder.write_trade(
                                token_label[asset_id],
                                float(price), float(size), side
                            )

                    elif etype == "price_change":
                        for change in ev.get("price_changes", []) or ev.get("changes", []):
                            cid = change.get("asset_id")
                            if cid in token_label:
                                # price_change only has best bid/ask, not full book
                                # Record as single-level book for tick tracking
                                bb = change.get("best_bid")
                                ba = change.get("best_ask")
                                if bb and ba:
                                    recorder.write_event(
                                        token_label[cid], "tick",
                                        [[float(bb), 0]], [[float(ba), 0]]
                                    )
    except Exception as e:
        print(f"  WS error: {e}")
    finally:
        recorder.close()


def get_current_market():
    """Get current BTC 15-min window tokens."""
    now = int(time.time())
    start_ts = (now // 900) * 900
    slug = f"btc-updown-15m-{start_ts}"
    elapsed = now - start_ts
    remaining = 900 - elapsed

    resp = requests.get(f"{GAMMA_API}/events?slug={slug}", timeout=10)
    data = resp.json()
    if not data or not data[0].get("markets"):
        return None, None, None, None, 0

    market = data[0]["markets"][0]
    tokens = json.loads(market.get("clobTokenIds", "[]"))
    return slug, tokens[0], tokens[1], start_ts, remaining


def run_continuous():
    """Record continuously, window after window."""
    print("L2 Recorder — continuous mode")
    print(f"Output dir: {DATA_DIR.resolve()}")
    print()

    while True:
        slug, up_token, dn_token, start_ts, remaining = get_current_market()
        if not slug:
            print("No market found, waiting 30s...")
            time.sleep(30)
            continue

        if remaining < 30:
            print(f"Window {slug} ending in {remaining}s, waiting for next...")
            time.sleep(remaining + 5)
            continue

        # Check if already recorded
        path = DATA_DIR / f"{slug}.jsonl.gz"
        if path.exists():
            print(f"Already have {slug}, waiting {remaining}s for next window...")
            time.sleep(remaining + 5)
            continue

        print(f"Window: {slug}  remaining: {remaining}s")
        asyncio.run(record_market(slug, up_token, dn_token, remaining + 10))

        # Brief gap between windows
        time.sleep(3)


if __name__ == "__main__":
    run_continuous()
