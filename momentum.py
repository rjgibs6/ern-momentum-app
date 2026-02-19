#!/usr/bin/env python3
"""
ERN Momentum Signal
-------------------
Early Retirement Now strategy: compares monthly closes to their
10-month Simple Moving Average to determine Risk-On / Risk-Off posture,
and calculates a Dynamic Spending Ceiling for retirement planning.
"""

import argparse
import sys
from datetime import datetime

import pandas as pd
from yahooquery import Ticker
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

ASSETS = {
    "^SP500TR": "S&P 500 Total Return",
    "IEF":      "iShares 7-10yr Treasury",
}
SMA_MONTHS   = 10
FETCH_MONTHS = 15

RISK_ON_RATE  = 0.045   # 4.5% — Risk-On spending ceiling
SAFETY_FLOOR  = 0.025   # 2.5% — Risk-Off safety floor
CURRENT_AGE   = 62
TARGET_AGE    = 95

# Reference portfolio sizes for the spending ladder (when no --portfolio given)
LADDER_SIZES  = [8_000_000, 10_000_000, 12_000_000, 15_000_000, 20_000_000, 25_000_000]

console = Console()


def fetch_monthly_closes(ticker: str) -> pd.Series:
    """Download monthly closes for ticker, returning only completed calendar months."""
    df = Ticker(ticker).history(period="2y", interval="1mo")

    if isinstance(df, str) or df.empty:
        console.print(f"\n  [red]Could not fetch data for {ticker}: {df}[/red]")
        sys.exit(1)

    col = "adjclose" if "adjclose" in df.columns else "close"
    closes = df.loc[ticker, col]
    closes.index = pd.to_datetime(closes.index, utc=True).tz_convert(None)
    closes.index = closes.index + pd.offsets.MonthEnd(0)
    closes = closes.dropna()

    today = datetime.today()
    closes = closes[
        ~((closes.index.month == today.month) & (closes.index.year == today.year))
    ]

    return closes.tail(FETCH_MONTHS)


def make_table(ticker: str, label: str, closes: pd.Series, sma: pd.Series) -> Table:
    """Build a Rich table for one asset."""
    latest_date = closes.index[-1]

    table = Table(
        title=f"{label} ({ticker})  ·  10-Month SMA",
        box=box.ROUNDED,
        header_style="bold",
        show_lines=False,
        min_width=55,
    )
    table.add_column("Month End",    justify="left",  min_width=12)
    table.add_column("Close",        justify="right", min_width=12)
    table.add_column("10-Mo SMA",    justify="right", min_width=12)
    table.add_column("% from Trend", justify="right", min_width=13)

    for date in closes.index:
        price  = float(closes[date])
        s      = sma[date]
        weight = "bold" if date == latest_date else ""

        if pd.notna(s):
            pct      = (price - float(s)) / float(s) * 100
            color    = "green" if pct >= 0 else "red"
            sma_text = Text(f"${s:,.2f}",   style=weight)
            pct_text = Text(f"{pct:+.2f}%", style=f"{weight} {color}".strip())
        else:
            sma_text = Text("—", style="dim")
            pct_text = Text("—", style="dim")

        table.add_row(
            Text(date.strftime("%b %d, %Y"), style=weight),
            Text(f"${price:,.2f}",           style=weight),
            sma_text,
            pct_text,
        )

    return table


def show_spending_ceiling(risk_on: bool, latest_date: pd.Timestamp,
                          portfolio: float | None) -> None:
    """Print the Dynamic Spending Ceiling section."""
    years         = TARGET_AGE - CURRENT_AGE
    current_rate  = RISK_ON_RATE if risk_on else SAFETY_FLOOR
    floor_rate    = SAFETY_FLOOR
    ceiling_rate  = RISK_ON_RATE
    regime_label  = "Risk-On" if risk_on else "Risk-Off"
    regime_style  = "bold green" if risk_on else "bold red"

    console.rule("[bold yellow]Dynamic Spending Ceiling[/bold yellow]")
    console.print()
    console.print(
        f"  Retirement horizon : Age [bold]{CURRENT_AGE}[/bold] → [bold]{TARGET_AGE}[/bold]"
        f"  ([bold]{years} years[/bold])"
    )
    console.print(f"  Success target     : 99% probability of portfolio lasting")
    console.print(f"  Signal (as of {latest_date.strftime('%b %Y')})  : ", end="")
    console.print(Text(f" {regime_label} ", style=f"bold white on {'green' if risk_on else 'red'}"))
    console.print(
        f"  Active rate        : [bold yellow]{current_rate*100:.1f}%[/bold yellow]"
        f"  ({'ceiling' if risk_on else 'safety floor'})"
    )
    console.print()

    if portfolio is not None:
        # ── Personalised spending ──────────────────────────────────────────
        annual   = portfolio * current_rate
        monthly  = annual / 12
        fl_ann   = portfolio * floor_rate
        fl_mo    = fl_ann / 12
        ceil_ann = portfolio * ceiling_rate
        ceil_mo  = ceil_ann / 12
        extra    = ceil_ann - fl_ann

        console.print(f"  Portfolio value    : [bold]${portfolio:,.0f}[/bold]")
        console.print()

        spend_table = Table(box=box.SIMPLE, show_header=True, header_style="bold",
                            title=f"Spending  ·  ${portfolio:,.0f} portfolio")
        spend_table.add_column("Regime",           justify="left",  min_width=20)
        spend_table.add_column("Rate",             justify="right", min_width=8)
        spend_table.add_column("Annual",           justify="right", min_width=14)
        spend_table.add_column("Monthly",          justify="right", min_width=14)

        for label, rate, ann, mo, is_active in [
            ("Safety Floor",      floor_rate,   fl_ann,   fl_mo,   not risk_on),
            ("Spending Ceiling",  ceiling_rate, ceil_ann, ceil_mo, risk_on),
        ]:
            style = "bold green" if is_active else ""
            marker = " ◀ active" if is_active else ""
            spend_table.add_row(
                Text(f"{label}{marker}", style=style),
                Text(f"{rate*100:.1f}%", style=style),
                Text(f"${ann:,.0f}",     style=style),
                Text(f"${mo:,.0f}",      style=style),
            )

        console.print(spend_table)

        if risk_on:
            console.print(
                f"  Extra spending vs. Safety Floor : "
                f"[bold green]+${extra:,.0f}/yr  (+${extra/12:,.0f}/mo)[/bold green]"
            )
        console.print()

    else:
        # ── Reference ladder ──────────────────────────────────────────────
        ladder = Table(
            title=f"Spending Reference  ·  Age {CURRENT_AGE}→{TARGET_AGE}  ·  99% success",
            box=box.ROUNDED,
            header_style="bold",
            show_lines=False,
        )
        ladder.add_column("Portfolio",            justify="right", min_width=13)
        ladder.add_column("Floor 2.5%  Annual",   justify="right", min_width=16)
        ladder.add_column("Floor 2.5%  Monthly",  justify="right", min_width=16)
        ladder.add_column("Ceil 4.5%  Annual",    justify="right", min_width=15)
        ladder.add_column("Ceil 4.5%  Monthly",   justify="right", min_width=15)

        for pv in LADDER_SIZES:
            fl_ann  = pv * floor_rate
            fl_mo   = fl_ann / 12
            c_ann   = pv * ceiling_rate
            c_mo    = c_ann / 12
            style   = "bold" if pv == 8_000_000 else ""
            ladder.add_row(
                Text(f"${pv:,.0f}",  style=style),
                Text(f"${fl_ann:,.0f}", style=style),
                Text(f"${fl_mo:,.0f}", style=style),
                Text(f"${c_ann:,.0f}", style=style),
                Text(f"${c_mo:,.0f}", style=style),
            )

        console.print(ladder)
        console.print(
            "  [dim]Run with --portfolio VALUE to see personalised spending amounts.[/dim]"
        )
        console.print()


def main() -> None:
    parser = argparse.ArgumentParser(description="ERN Momentum Signal & Spending Ceiling")
    parser.add_argument(
        "--portfolio", "-p", type=float, metavar="VALUE",
        help="Your portfolio value (e.g. 1000000) for personalised spending output",
    )
    args = parser.parse_args()

    console.print()
    console.rule("[bold cyan]ERN Momentum Signal[/bold cyan]")
    console.print()

    results = {}
    for ticker, label in ASSETS.items():
        console.print(f"  [dim]Fetching {label} ({ticker})…[/dim]")
        closes = fetch_monthly_closes(ticker)
        if len(closes) < SMA_MONTHS:
            console.print(f"  [red]Insufficient data for {ticker}.[/red]")
            sys.exit(1)
        sma = closes.rolling(window=SMA_MONTHS).mean()
        results[ticker] = (label, closes, sma)

    # ── Signal summary ────────────────────────────────────────────────────
    console.print()
    for ticker, (label, closes, sma) in results.items():
        latest_price = float(closes.iloc[-1])
        latest_sma   = float(sma.iloc[-1])
        pct          = (latest_price - latest_sma) / latest_sma * 100
        latest_date  = closes.index[-1]
        risk_on      = latest_price > latest_sma

        signal    = Text("  RISK-ON  " if risk_on else "  RISK-OFF  ",
                         style="bold white on green" if risk_on else "bold white on red")
        pct_color = "green" if pct >= 0 else "red"

        console.print(f"  {label}")
        console.print(f"    Signal  : ", end="")
        console.print(signal)
        console.print(f"    As of   : [bold]{latest_date.strftime('%B %Y')}[/bold]")
        console.print(f"    Close   : [bold]${latest_price:,.2f}[/bold]")
        console.print(
            f"    10-Mo SMA: [bold]${latest_sma:,.2f}[/bold]  "
            f"([{pct_color}]{pct:+.2f}%[/{pct_color}] from trend)"
        )
        console.print()

    # ── Individual tables ─────────────────────────────────────────────────
    for ticker, (label, closes, sma) in results.items():
        console.print(make_table(ticker, label, closes, sma))
        console.print()

    # ── Combined index table ──────────────────────────────────────────────
    tickers    = list(results.keys())
    all_closes = {t: results[t][1] for t in tickers}
    all_labels = {t: results[t][0] for t in tickers}

    common_idx = all_closes[tickers[0]].index
    for t in tickers[1:]:
        common_idx = common_idx.intersection(all_closes[t].index)

    base = common_idx[0]
    idx_table = Table(
        title=f"Performance Index  ·  Base 100 = {base.strftime('%b %d, %Y')}",
        box=box.ROUNDED, header_style="bold", show_lines=False,
    )
    idx_table.add_column("Month End", justify="left", min_width=12)
    for t in tickers:
        idx_table.add_column(all_labels[t], justify="right", min_width=14)

    latest_date = common_idx[-1]
    for date in common_idx:
        weight = "bold" if date == latest_date else ""
        row = [Text(date.strftime("%b %d, %Y"), style=weight)]
        for t in tickers:
            val = float(all_closes[t][date]) / float(all_closes[t][base]) * 100
            row.append(Text(f"{val:.2f}", style=weight))
        idx_table.add_row(*row)

    console.print(idx_table)
    console.print()

    # ── Dynamic Spending Ceiling ──────────────────────────────────────────
    sp500_closes = results["^SP500TR"][1]
    sp500_sma    = results["^SP500TR"][2]
    risk_on      = float(sp500_closes.iloc[-1]) > float(sp500_sma.iloc[-1])
    show_spending_ceiling(risk_on, sp500_closes.index[-1], args.portfolio)


if __name__ == "__main__":
    main()
