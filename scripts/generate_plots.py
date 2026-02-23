#!/usr/bin/env python3
"""
Generate publication-quality figures for pulsefeed documentation.

Produces:
  - docs/figures/exchange_latency.png
  - docs/figures/aggregation_pipeline.png

Usage:
  python scripts/generate_plots.py
"""

import os
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as ticker

# ── Color palette ──────────────────────────────────────────────────────────
PRIMARY = "#2196F3"      # blue  — USD exchanges
SECONDARY = "#4CAF50"
ACCENT = "#E91E63"
ORANGE = "#FF9800"       # orange — USDT exchanges
BG_COLOR = "#FAFAFA"
GRID_COLOR = "#E0E0E0"

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "figures")
DPI = 150


def _apply_style(ax):
    """Apply consistent styling to an axes object."""
    ax.set_facecolor(BG_COLOR)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", color=GRID_COLOR, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)


def figure_exchange_latency():
    """Horizontal bar chart of WebSocket update frequency per exchange."""
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor(BG_COLOR)
    _apply_style(ax)
    ax.grid(axis="y", visible=False)

    # Data: (exchange, latency_ms, denomination)
    data = [
        ("Gemini", 1000, "USD"),
        ("Coinbase", 1000, "USD"),
        ("Kraken", 500, "USD"),
        ("KuCoin", 250, "USDT"),
        ("Gate.io", 200, "USDT"),
        ("OKX", 100, "USDT"),
        ("Binance", 100, "USDT"),
        ("Bybit", 50, "USDT"),
    ]

    labels = [d[0] for d in data]
    latencies = [d[1] for d in data]
    denoms = [d[2] for d in data]
    colors = [PRIMARY if d == "USD" else ORANGE for d in denoms]

    y = np.arange(len(data))
    bars = ax.barh(y, latencies, height=0.6, color=colors, edgecolor="white",
                   linewidth=0.8, zorder=3)

    # Value labels
    for i, (bar, lat) in enumerate(zip(bars, latencies)):
        suffix = "  (trade-driven)" if lat >= 1000 else ""
        ax.text(bar.get_width() + 20, bar.get_y() + bar.get_height() / 2,
                f"{lat}ms{suffix}", ha="left", va="center",
                fontsize=11, fontweight="bold", color=colors[i])

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=12, fontweight="medium")
    ax.set_xlabel("Typical Update Interval (ms)", fontsize=13)
    ax.set_title("WebSocket Update Frequency by Exchange",
                 fontsize=16, fontweight="bold", pad=15)
    ax.set_xlim(0, 1400)

    # Legend
    usd_patch = mpatches.Patch(color=PRIMARY, label="USD-denominated")
    usdt_patch = mpatches.Patch(color=ORANGE, label="USDT-denominated")
    ax.legend(handles=[usd_patch, usdt_patch], fontsize=11, loc="lower right",
              framealpha=0.9)

    # Fastest annotation
    ax.annotate("Fastest", xy=(50, 7), xytext=(200, 7.4),
                fontsize=11, fontweight="bold", color=SECONDARY,
                arrowprops=dict(arrowstyle="->", color=SECONDARY, lw=1.5))

    fig.tight_layout()
    path = os.path.join(OUTPUT_DIR, "exchange_latency.png")
    fig.savefig(path, dpi=DPI, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    print(f"  Saved {path}")


def figure_aggregation_pipeline():
    """Visual of 8 exchange prices converging to median with confidence band."""
    fig, ax = plt.subplots(figsize=(14, 7))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True)

    # Simulated BTC prices from each exchange
    base_price = 97_250.00
    np.random.seed(42)

    exchanges = [
        ("Binance", "USDT", ORANGE),
        ("Coinbase", "USD", PRIMARY),
        ("Kraken", "USD", PRIMARY),
        ("OKX", "USDT", ORANGE),
        ("Bybit", "USDT", ORANGE),
        ("Gemini", "USD", PRIMARY),
        ("KuCoin", "USDT", ORANGE),
        ("Gate.io", "USDT", ORANGE),
    ]

    # Generate realistic price offsets
    # USDT exchanges tend to trade at a slight premium
    usdt_premium = 12.0  # $12 USDT premium
    offsets = np.random.normal(0, 25, len(exchanges))
    offsets[0] = 15    # Binance slightly above
    offsets[1] = -8    # Coinbase slightly below
    offsets[2] = -12   # Kraken slightly below
    offsets[3] = 18    # OKX
    offsets[4] = 22    # Bybit
    offsets[5] = -5    # Gemini
    offsets[6] = 35    # KuCoin
    offsets[7] = 145   # Gate.io — outlier

    prices = []
    for i, (name, denom, color) in enumerate(exchanges):
        premium = usdt_premium if denom == "USDT" else 0
        prices.append(base_price + offsets[i] + premium)

    prices = np.array(prices)

    # Compute statistics (after USDT premium normalization)
    normalized = []
    for i, (name, denom, color) in enumerate(exchanges):
        adj = prices[i] - (usdt_premium if denom == "USDT" else 0)
        normalized.append(adj)
    normalized = np.array(normalized)

    median_price = np.median(normalized)
    std_price = np.std(normalized)

    # X positions for scatter
    x_positions = np.linspace(0.5, 3.5, len(exchanges))
    x_median = 5.5

    # Draw confidence band on right side
    band_x = 4.5
    band_width = 2.5
    ax.fill_between(
        [band_x, band_x + band_width],
        median_price - std_price, median_price + std_price,
        color=SECONDARY, alpha=0.12, zorder=1, label=r"$\pm 1\sigma$ confidence band"
    )

    # Outlier rejection zone
    ax.fill_between(
        [band_x, band_x + band_width],
        median_price + 2 * std_price, median_price + 3.5 * std_price,
        color=ACCENT, alpha=0.08, zorder=1
    )
    ax.fill_between(
        [band_x, band_x + band_width],
        median_price - 3.5 * std_price, median_price - 2 * std_price,
        color=ACCENT, alpha=0.08, zorder=1
    )

    # Draw exchange price points with connecting lines to median
    for i, (name, denom, color) in enumerate(exchanges):
        is_outlier = abs(normalized[i] - median_price) > 2 * std_price
        marker_size = 120 if not is_outlier else 150
        edge = ACCENT if is_outlier else "white"
        linestyle = "--" if is_outlier else "-"
        alpha = 0.3 if is_outlier else 0.4

        # Connecting line to median (normalized price)
        ax.plot([x_positions[i], x_median], [normalized[i], median_price],
                color=color, alpha=alpha, linewidth=1, linestyle=linestyle, zorder=2)

        # Exchange point
        ax.scatter(x_positions[i], normalized[i], s=marker_size, color=color,
                   edgecolors=edge, linewidths=1.5, zorder=4)

        # Label
        y_offset = 12 if i % 2 == 0 else -16
        ax.annotate(
            f"{name}\n${normalized[i]:,.0f}",
            xy=(x_positions[i], normalized[i]),
            xytext=(x_positions[i], normalized[i] + y_offset * 2),
            fontsize=8.5, ha="center", va="center", color=color, fontweight="bold",
            arrowprops=dict(arrowstyle="-", color=color, alpha=0.3, lw=0.5)
        )

    # Median marker
    ax.scatter(x_median, median_price, s=250, color=SECONDARY, edgecolors="white",
               linewidths=2, zorder=5, marker="D")
    ax.axhline(y=median_price, xmin=0.55, xmax=0.95, color=SECONDARY,
               linewidth=2.5, linestyle="-", zorder=3, alpha=0.8)
    ax.text(x_median + 0.3, median_price, f"Median: ${median_price:,.2f}",
            fontsize=13, fontweight="bold", color=SECONDARY, va="center")

    # Sigma boundaries
    ax.axhline(y=median_price + std_price, xmin=0.55, xmax=0.95,
               color=SECONDARY, linewidth=1, linestyle=":", alpha=0.5, zorder=2)
    ax.axhline(y=median_price - std_price, xmin=0.55, xmax=0.95,
               color=SECONDARY, linewidth=1, linestyle=":", alpha=0.5, zorder=2)
    ax.text(band_x + band_width + 0.1, median_price + std_price,
            f"+1$\\sigma$ (${median_price + std_price:,.0f})",
            fontsize=9, color=SECONDARY, va="center", alpha=0.7)
    ax.text(band_x + band_width + 0.1, median_price - std_price,
            f"-1$\\sigma$ (${median_price - std_price:,.0f})",
            fontsize=9, color=SECONDARY, va="center", alpha=0.7)

    # 2-sigma outlier rejection boundary
    ax.axhline(y=median_price + 2 * std_price, xmin=0.55, xmax=0.95,
               color=ACCENT, linewidth=1, linestyle="--", alpha=0.4, zorder=2)
    ax.axhline(y=median_price - 2 * std_price, xmin=0.55, xmax=0.95,
               color=ACCENT, linewidth=1, linestyle="--", alpha=0.4, zorder=2)
    ax.text(band_x + band_width + 0.1, median_price + 2 * std_price,
            r"$>2\sigma$: rejected", fontsize=9, color=ACCENT, va="center",
            fontweight="bold", alpha=0.7)

    # USDT premium annotation
    ax.annotate(
        f"USDT premium: +${usdt_premium:.0f}\n(normalized before aggregation)",
        xy=(1.0, base_price + usdt_premium + 20), xytext=(0.2, base_price + 100),
        fontsize=9, color=ORANGE, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.4", facecolor=ORANGE, alpha=0.1, edgecolor=ORANGE),
        arrowprops=dict(arrowstyle="->", color=ORANGE, lw=1.2)
    )

    # Section labels
    ax.text(2.0, ax.get_ylim()[1] - 5, "8 Exchange Feeds",
            fontsize=14, ha="center", fontweight="bold", color="#555555")
    ax.text(x_median, ax.get_ylim()[1] - 5, "Aggregated Output",
            fontsize=14, ha="center", fontweight="bold", color=SECONDARY)

    # Arrow between sections
    ax.annotate("", xy=(4.3, median_price), xytext=(3.8, median_price),
                arrowprops=dict(arrowstyle="->", color="#AAAAAA", lw=2.5))

    ax.set_xlim(-0.3, 8.5)
    ax.set_ylabel("Price (USD)", fontsize=13)
    ax.set_title("Multi-Exchange Price Aggregation Pipeline",
                 fontsize=16, fontweight="bold", pad=15)
    ax.set_xticks([])
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))

    # Legend
    usd_patch = mpatches.Patch(color=PRIMARY, label="USD exchanges")
    usdt_patch = mpatches.Patch(color=ORANGE, label="USDT exchanges (premium-adjusted)")
    conf_patch = mpatches.Patch(color=SECONDARY, alpha=0.15, label=r"$\pm 1\sigma$ confidence band")
    rej_patch = mpatches.Patch(color=ACCENT, alpha=0.15, label=r"$>2\sigma$ outlier rejection zone")
    ax.legend(handles=[usd_patch, usdt_patch, conf_patch, rej_patch],
              fontsize=10, loc="lower left", framealpha=0.9)

    fig.tight_layout()
    path = os.path.join(OUTPUT_DIR, "aggregation_pipeline.png")
    fig.savefig(path, dpi=DPI, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    print(f"  Saved {path}")


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("Generating pulsefeed figures...")
    figure_exchange_latency()
    figure_aggregation_pipeline()
    print("Done.")
