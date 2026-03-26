"""Reads logged positions from Google Sheets and generates a static matplotlib P&L chart at session end."""

import os
import datetime

import matplotlib.pyplot as plt
from sheets_logger import get_sheet


def generate_chart():
    # ── STEP 1: Read data from Google Sheets ─────────────────────────────────
    sheet = get_sheet()
    if sheet is None:
        print("Error: could not connect to Google Sheets.")
        return

    rows = sheet.get_all_records()
    if not rows:
        print("No data in sheet yet.")
        return

    # ── STEP 2: Group rows by timestamp ──────────────────────────────────────
    trades = {}
    for row in rows:
        key = (row["Timestamp"], row["Market"])
        if key not in trades:
            trades[key] = []
        trades[key].append(row)

    # ── STEP 3: Calculate expected profit per trade ───────────────────────────
    sorted_timestamps = sorted(trades.keys())
    trade_profits = []
    for ts in sorted_timestamps:
        group = trades[ts]
        ts = ts[0]  # unpack (timestamp, market) → just timestamp for labeling
        total_deployed = sum(float(row["Stake"]) for row in group)
        expected_profit = 10000.0 - total_deployed
        trade_profits.append((ts, expected_profit))

    # ── STEP 4: Calculate cumulative P&L ─────────────────────────────────────
    cumulative = 0.0
    cumulative_pnl = []
    for ts, profit in trade_profits:
        cumulative += profit
        cumulative_pnl.append((ts, cumulative))

    # ── STEP 5: Generate the chart ────────────────────────────────────────────
    x = list(range(1, len(cumulative_pnl) + 1))
    y = [pnl for _, pnl in cumulative_pnl]

    fig, ax = plt.subplots(figsize=(12, 6))

    ax.plot(x, y, color="#3B6D11", linewidth=2,
            marker="o", markersize=6, markerfacecolor="#3B6D11")

    ax.axhline(y=0, color="#888888", linewidth=0.8, linestyle="--")

    ax.set_title("PolymarketBot — Cumulative Simulated P&L")
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Cumulative P&L ($)")
    ax.grid(axis="y", alpha=0.3)

    total_pnl = cumulative_pnl[-1][1] if cumulative_pnl else 0.0
    summary_text = f"Total trades: {len(cumulative_pnl)}\nTotal P&L: ${total_pnl:,.2f}"
    ax.text(
        0.02, 0.97, summary_text,
        transform=ax.transAxes,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.8)
    )

    plt.tight_layout()

    chart_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pnl_chart.png")
    plt.savefig(chart_path)
    print(f"Chart saved to pnl_chart.png")
    plt.show()


if __name__ == "__main__":
    generate_chart()
