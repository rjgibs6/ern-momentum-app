#!/usr/bin/env python3
"""
ERN Momentum Signal
-------------------
Early Retirement Now strategy: compares ^SP500TR monthly close to its
10-month Simple Moving Average to determine Risk-On / Risk-Off posture.
"""

import sys
from datetime import datetime

import pandas as pd
from yahooquery import Ticker
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

TICKER = "^SP500TR"
SMA_MONTHS = 10
FETCH_MONTHS = 15   # 10 for SMA + 5 buffer ensures last 3 rows always have valid SMA
DISPLAY_MONTHS = 3

console = Console()


def fetch_monthly_closes() -> pd.Series:
    """Download ^SP500TR monthly closes, returning only completed calendar months."""
    df = Ticker(TICKER).history(period="2y", interval="1mo")

    if isinstance(df, str) or df.empty:
        console.print(f"\n  [red]Could not fetch data: {df}[/red]")
        sys.exit(1)

    # yahooquery returns a MultiIndex (symbol, date); drop the symbol level
    closes = df.loc[TICKER, "adjclose"]
    closes.index = pd.to_datetime(closes.index, utc=True).tz_convert(None)
    closes = closes.dropna()

    # Drop the current month if it is still in progress
    today = datetime.today()
    if (
        not closes.empty
        and closes.index[-1].month == today.month
        and closes.index[-1].year == today.year
    ):
        closes = closes.iloc[:-1]

    return closes.tail(FETCH_MONTHS)


def main() -> None:
    console.print()
    console.rule("[bold cyan]ERN Momentum Signal[/bold cyan]")
    console.print()
    console.print(f"  [dim]Fetching {TICKER} monthly data from Yahoo Finance…[/dim]")

    closes = fetch_monthly_closes()

    if len(closes) < SMA_MONTHS:
        console.print(
            f"  [red]Insufficient data: need {SMA_MONTHS} months, got {len(closes)}.[/red]"
        )
        sys.exit(1)

    # ── 10-month SMA ──────────────────────────────────────────────────────
    sma = closes.rolling(window=SMA_MONTHS).mean()

    latest_date = closes.index[-1]
    latest_price = float(closes.iloc[-1])
    latest_sma = float(sma.iloc[-1])
    pct_from_sma = (latest_price - latest_sma) / latest_sma * 100

    # ── Signal ────────────────────────────────────────────────────────────
    risk_on = latest_price > latest_sma
    if risk_on:
        signal = Text("  RISK-ON  ", style="bold white on green")
        note = f"{TICKER} is [green]above[/green] its 10-month SMA"
    else:
        signal = Text("  RISK-OFF  ", style="bold white on red")
        note = f"{TICKER} is [red]below[/red] its 10-month SMA"

    pct_color = "green" if pct_from_sma >= 0 else "red"

    console.print()
    console.print("  Signal    : ", end="")
    console.print(signal)
    console.print(f"  As of     : [bold]{latest_date.strftime('%B %Y')}[/bold]")
    console.print(f"  {TICKER}  : [bold]${latest_price:,.2f}[/bold]")
    console.print(
        f"  10-Mo SMA : [bold]${latest_sma:,.2f}[/bold]  "
        f"([{pct_color}]{pct_from_sma:+.2f}%[/{pct_color}] from trendline)"
    )
    console.print(f"  Note      : {note}")
    console.print()

    # ── Table: last 3 months ──────────────────────────────────────────────
    table = Table(
        title=f"Last {DISPLAY_MONTHS} Months  ·  {TICKER} vs. 10-Month SMA",
        box=box.ROUNDED,
        header_style="bold",
        show_lines=False,
        min_width=55,
    )

    table.add_column("Month",      justify="left",  min_width=10)
    table.add_column("Index Close", justify="right", min_width=12)
    table.add_column("10-Mo SMA",  justify="right", min_width=12)
    table.add_column("% from SMA", justify="right", min_width=12)

    for date in closes.tail(DISPLAY_MONTHS).index:
        price = float(closes[date])
        s = float(sma[date])
        pct = (price - s) / s * 100
        color = "green" if pct >= 0 else "red"
        is_latest = date == latest_date
        weight = "bold " if is_latest else ""

        table.add_row(
            Text(date.strftime("%b %Y"), style=weight.strip()),
            Text(f"${price:,.2f}",       style=weight.strip()),
            Text(f"${s:,.2f}",           style=weight.strip()),
            Text(f"{pct:+.2f}%",         style=f"{weight}{color}".strip()),
        )

    console.print(table)
    console.print(
        f"  [dim]Signal is Risk-On when {TICKER} Close > 10-Mo SMA; Risk-Off when below.[/dim]"
    )
    console.print()


if __name__ == "__main__":
    main()
