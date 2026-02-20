#!/usr/bin/env python3
"""
ERN Momentum Signal
-------------------
Early Retirement Now strategy: compares monthly closes to their
10-month Simple Moving Average to determine Risk-On / Risk-Off posture.

Momentum score (ERN Part 63): 12 signals per asset
  3 horizons (8, 9, 10 months) × 2 methods (SMA crossover + point-in-time)
  × 2 index versions (total return + price return), averaged to a 0–1 score.
"""

import sys
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup
from yahooquery import Ticker
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

ASSETS = {
    "^SP500TR": "S&P 500 Total Return",
    "IEF":      "iShares 7-10yr Treasury",
}
# Price-return counterpart for each asset (second index version for momentum scoring)
# IEF uses its own close vs adjclose as the two versions
ASSET_ALT_TICKER = {
    "^SP500TR": "^GSPC",
    "IEF":      None,
}

SMA_MONTHS        = 10
FETCH_MONTHS      = 15
MOMENTUM_HORIZONS = [8, 9, 10]

console = Console()


def fetch_cape() -> pd.Series:
    """Scrape monthly Shiller CAPE ratios from multpl.com, indexed by month-end."""
    r = requests.get(
        "https://www.multpl.com/shiller-pe/table/by-month",
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"},
        timeout=10,
    )
    r.raise_for_status()
    soup  = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", {"id": "datatable"})
    rows  = table.find_all("tr")[1:]   # skip header

    data = {}
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 2:
            continue
        date_str = cols[0].get_text(strip=True)
        val_str  = cols[1].get_text(strip=True)
        try:
            date = pd.to_datetime(date_str) + pd.offsets.MonthEnd(0)
            data[date] = float(val_str)
        except (ValueError, AttributeError):
            continue

    return pd.Series(data, name="CAPE").sort_index()


def _clean_df(raw_df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Normalize index to month-end, drop NaNs and current in-progress month."""
    df = raw_df.loc[ticker].copy()
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
    df.index = df.index + pd.offsets.MonthEnd(0)
    df = df.dropna(how="all")
    today = datetime.today()
    mask = ~((df.index.month == today.month) & (df.index.year == today.year))
    return df[mask].tail(FETCH_MONTHS)


def fetch_both_closes(ticker: str) -> tuple[pd.Series, pd.Series]:
    """
    Download monthly closes for ticker.
    Returns (adj_close, raw_close) — both normalized to month-end.
    adj_close uses adjclose when available; raw_close is always the unadjusted close.
    """
    raw_df = Ticker(ticker).history(period="2y", interval="1mo")

    if isinstance(raw_df, str) or raw_df.empty:
        console.print(f"\n  [red]Could not fetch data for {ticker}: {raw_df}[/red]")
        sys.exit(1)

    df  = _clean_df(raw_df, ticker)
    adj = df["adjclose"] if "adjclose" in df.columns else df["close"]
    raw = df["close"]

    return adj.rename(ticker), raw.rename(ticker)


def fetch_monthly_closes(ticker: str) -> pd.Series:
    """Convenience wrapper — returns adj close only."""
    adj, _ = fetch_both_closes(ticker)
    return adj


def compute_momentum_score(series_list: list[pd.Series]) -> tuple[int, int]:
    """
    ERN Part 63 momentum score across all provided price series.

    For each series and each horizon n in MOMENTUM_HORIZONS:
      Method 1 (SMA crossover)  : latest price > n-month rolling SMA
      Method 2 (point-in-time)  : latest price > price exactly n months ago

    Returns (n_bullish, total_signals).
    Total signals = len(series_list) × len(MOMENTUM_HORIZONS) × 2 methods.
    """
    bullish = 0
    total   = 0

    for series in series_list:
        latest = float(series.iloc[-1])

        for n in MOMENTUM_HORIZONS:
            # Method 1: SMA crossover
            if len(series) >= n:
                sma_val = series.rolling(window=n).mean().iloc[-1]
                if pd.notna(sma_val):
                    bullish += int(latest > float(sma_val))
                    total   += 1

            # Method 2: point-in-time (price n months ago = iloc[-(n+1)])
            if len(series) > n:
                past = float(series.iloc[-(n + 1)])
                bullish += int(latest > past)
                total   += 1

    return bullish, total


def make_table(ticker: str, label: str, closes: pd.Series, sma: pd.Series,
               cape: pd.Series | None = None) -> Table:
    """Build a Rich table for one asset, optionally with a CAPE column."""
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
    if cape is not None:
        table.add_column("CAPE", justify="right", min_width=8)

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

        row = [
            Text(date.strftime("%b %d, %Y"), style=weight),
            Text(f"${price:,.2f}",           style=weight),
            sma_text,
            pct_text,
        ]

        if cape is not None:
            cape_val = cape.get(date)
            row.append(
                Text(f"{cape_val:.2f}", style=weight) if cape_val is not None
                else Text("—", style="dim")
            )

        table.add_row(*row)

    return table


def main() -> None:
    console.print()
    console.rule("[bold cyan]ERN Momentum Signal[/bold cyan]")
    console.print()

    results    = {}   # ticker -> (label, adj, raw, sma)
    alt_series = {}   # ticker -> alt adj close (second index version)

    for ticker, label in ASSETS.items():
        console.print(f"  [dim]Fetching {label} ({ticker})…[/dim]")
        adj, raw = fetch_both_closes(ticker)
        if len(adj) < SMA_MONTHS:
            console.print(f"  [red]Insufficient data for {ticker}.[/red]")
            sys.exit(1)
        sma = adj.rolling(window=SMA_MONTHS).mean()
        results[ticker] = (label, adj, raw, sma)

        alt_ticker = ASSET_ALT_TICKER.get(ticker)
        if alt_ticker:
            console.print(f"  [dim]Fetching alt index {alt_ticker}…[/dim]")
            alt_adj, _ = fetch_both_closes(alt_ticker)
            alt_series[ticker] = alt_adj

    console.print(f"  [dim]Fetching Shiller CAPE (multpl.com)…[/dim]")
    cape = fetch_cape()

    # ── Signal summary ────────────────────────────────────────────────────
    console.print()
    for ticker, (label, adj, raw, sma) in results.items():
        latest_price = float(adj.iloc[-1])
        latest_sma   = float(sma.iloc[-1])
        pct          = (latest_price - latest_sma) / latest_sma * 100
        latest_date  = adj.index[-1]
        risk_on      = latest_price > latest_sma

        # Two index versions per asset:
        #   Equities: ^SP500TR (total return) + ^GSPC (price return)
        #   Bonds:    IEF adjclose (total return) + IEF close (price-only)
        if ticker in alt_series:
            series_list = [adj, alt_series[ticker]]
        else:
            series_list = [adj, raw]

        bullish, total = compute_momentum_score(series_list)
        score          = bullish / total if total else 0.0
        score_color    = "green" if score > 0.5 else ("yellow" if score == 0.5 else "red")

        signal    = Text("  RISK-ON  " if risk_on else "  RISK-OFF  ",
                         style="bold white on green" if risk_on else "bold white on red")
        pct_color = "green" if pct >= 0 else "red"

        console.print(f"  {label}")
        console.print(f"    Signal    : ", end="")
        console.print(signal)
        console.print(
            f"    Momentum  : [{score_color}]{bullish}/{total}  ({score:.0%})[/{score_color}]"
            f"  [dim](12 signals: 3 horizons × 2 methods × 2 indices)[/dim]"
        )
        console.print(f"    As of     : [bold]{latest_date.strftime('%B %Y')}[/bold]")
        console.print(f"    Close     : [bold]${latest_price:,.2f}[/bold]")
        console.print(
            f"    10-Mo SMA : [bold]${latest_sma:,.2f}[/bold]  "
            f"([{pct_color}]{pct:+.2f}%[/{pct_color}] from trend)"
        )
        console.print()

    # ── Individual tables ─────────────────────────────────────────────────
    for ticker, (label, adj, raw, sma) in results.items():
        cape_col = cape if ticker == "^SP500TR" else None
        console.print(make_table(ticker, label, adj, sma, cape=cape_col))
        console.print()

    # ── Combined index table ──────────────────────────────────────────────
    tickers    = list(results.keys())
    all_closes = {t: results[t][1] for t in tickers}   # adj close
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


if __name__ == "__main__":
    main()
