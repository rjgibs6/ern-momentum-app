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

import argparse
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

# ERN Part 54 — CAPE-based SWR models
# SWR = intercept + slope / adjusted_CAPE
# Adjusted CAPE = Shiller CAPE × CAPE_ADJUSTMENT (ERN's earnings-adjusted factor)
CAPE_ADJUSTMENT = 0.775   # ERN adjustment: accounts for ~29% higher reported earnings
CAPE_SWR_MODELS = [
    ("Recommended",  0.0175,  0.50),   # intercept, slope
    ("Conservative", -0.0025, 0.90),
]

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


def _clean_df(raw_df: pd.DataFrame, ticker: str,
              include_current: bool = False) -> pd.DataFrame:
    """Normalize index to month-end, drop NaNs, and optionally drop current in-progress month."""
    df = raw_df.loc[ticker].copy()
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
    df.index = df.index + pd.offsets.MonthEnd(0)
    df.index = df.index.normalize()              # strip intraday time → midnight
    df = df.dropna(how="all")
    df = df[~df.index.duplicated(keep="last")]   # keep latest bar per month-end
    if not include_current:
        today = datetime.today()
        mask = ~((df.index.month == today.month) & (df.index.year == today.year))
        df = df[mask]
    return df.tail(FETCH_MONTHS)


def fetch_both_closes(ticker: str,
                      include_current: bool = False) -> tuple[pd.Series, pd.Series]:
    """
    Download monthly closes for ticker.
    Returns (adj_close, raw_close) — both normalized to month-end.
    adj_close uses adjclose when available; raw_close is always the unadjusted close.
    Pass include_current=True to retain the current in-progress month.
    """
    raw_df = Ticker(ticker).history(period="2y", interval="1mo")

    if isinstance(raw_df, str) or raw_df.empty:
        console.print(f"\n  [red]Could not fetch data for {ticker}: {raw_df}[/red]")
        sys.exit(1)

    df  = _clean_df(raw_df, ticker, include_current=include_current)
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
                Text(f"{cape_val * 0.775:.2f}", style=weight) if cape_val is not None
                else Text("—", style="dim")
            )

        table.add_row(*row)

    return table


def compute_momentum_history(
    series_lists: dict[str, list[pd.Series]],
) -> pd.DataFrame:
    """
    For each completed month in the data, compute the 12-signal momentum score
    as of that month by slicing each series up to that date.

    Returns a DataFrame indexed by date with columns
    ``{ticker}_bullish`` and ``{ticker}_total``.
    """
    first_ticker = next(iter(series_lists))
    dates = series_lists[first_ticker][0].index

    rows = []
    for date in dates:
        row: dict = {"date": date}
        for ticker, slist in series_lists.items():
            sliced  = [s[s.index <= date] for s in slist]
            bullish, total = compute_momentum_score(sliced)
            row[f"{ticker}_bullish"] = bullish
            row[f"{ticker}_total"]   = total
        rows.append(row)

    return pd.DataFrame(rows).set_index("date")


def make_score_history_table(
    history_df: pd.DataFrame,
    labels: dict[str, str],
) -> Table:
    """Build a Rich table showing the momentum score for each past month."""
    tickers     = list(labels.keys())
    latest_date = history_df.index[-1]

    table = Table(
        title="Momentum Score History  ·  12 Signals per Asset",
        box=box.ROUNDED,
        header_style="bold",
        show_lines=False,
    )
    table.add_column("Month End", justify="left",   min_width=21)
    for ticker in tickers:
        table.add_column(labels[ticker],  justify="right",  min_width=14)
        table.add_column("Signal",        justify="center", min_width=9)

    today = datetime.today()

    for date, row in history_df.iterrows():
        # Skip months with no computable signals (insufficient historical data)
        if all(int(row[f"{ticker}_total"]) == 0 for ticker in tickers):
            continue

        is_live  = (date.month == today.month and date.year == today.year)
        weight   = "bold" if date == latest_date else ""
        date_str = date.strftime("%b %d, %Y") + (" (live)" if is_live else "")
        cells    = [Text(date_str, style=weight)]

        for ticker in tickers:
            bullish = int(row[f"{ticker}_bullish"])
            total   = int(row[f"{ticker}_total"])
            score   = bullish / total if total else 0.0
            color   = "green" if score > 0.5 else ("yellow" if score == 0.5 else "red")

            cells.append(Text(
                f"{bullish}/{total}  ({score:.0%})",
                style=f"{weight} {color}".strip(),
            ))

            if score > 0.5:
                sig = Text("Risk-On",  style=f"{weight} green".strip())
            elif score < 0.5:
                sig = Text("Risk-Off", style=f"{weight} red".strip())
            else:
                sig = Text("Neutral",  style=f"{weight} yellow".strip())
            cells.append(sig)

        table.add_row(*cells)

    return table


def main() -> None:
    parser = argparse.ArgumentParser(description="ERN Momentum Signal")
    parser.add_argument(
        "--dividend", type=float, default=0.0, metavar="PCT",
        help="Annual dividend yield %% to subtract from CAPE SWR (e.g. 1.5)",
    )
    args = parser.parse_args()
    dividend = args.dividend / 100.0

    console.print()
    console.rule("[bold cyan]ERN Momentum Signal[/bold cyan]")
    console.print()

    results          = {}   # ticker -> (label, adj, raw, sma)       completed months
    results_live     = {}   # ticker -> (adj_live, raw_live)          including current month
    alt_series       = {}   # ticker -> alt adj close                 completed months
    alt_series_live  = {}   # ticker -> alt adj close                 including current month

    today = datetime.today()

    for ticker, label in ASSETS.items():
        console.print(f"  [dim]Fetching {label} ({ticker})…[/dim]")
        adj_live, raw_live = fetch_both_closes(ticker, include_current=True)

        # Completed months: drop current in-progress month for SMA tables
        not_current = ~((adj_live.index.month == today.month) &
                        (adj_live.index.year  == today.year))
        adj = adj_live[not_current]
        raw = raw_live[not_current]

        if len(adj) < SMA_MONTHS:
            console.print(f"  [red]Insufficient data for {ticker}.[/red]")
            sys.exit(1)
        sma = adj.rolling(window=SMA_MONTHS).mean()
        results[ticker]      = (label, adj, raw, sma)
        results_live[ticker] = (adj_live, raw_live)

        alt_ticker = ASSET_ALT_TICKER.get(ticker)
        if alt_ticker:
            console.print(f"  [dim]Fetching alt index {alt_ticker}…[/dim]")
            alt_adj_live, _ = fetch_both_closes(alt_ticker, include_current=True)
            not_current_alt  = ~((alt_adj_live.index.month == today.month) &
                                  (alt_adj_live.index.year  == today.year))
            alt_series[ticker]      = alt_adj_live[not_current_alt]
            alt_series_live[ticker] = alt_adj_live

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

        if ticker == "^SP500TR":
            raw_cape = cape.iloc[-1] if cape is not None and len(cape) else None
            if raw_cape is not None:
                adj_cape = raw_cape * CAPE_ADJUSTMENT
                div_str  = f" − {dividend*100:.2f}% div" if dividend else ""
                for model_name, intercept, slope in CAPE_SWR_MODELS:
                    gross = intercept + slope / adj_cape
                    net   = gross - dividend
                    if dividend:
                        console.print(
                            f"    CAPE SWR  : [bold]{gross:.2%}[/bold] gross"
                            f" → [bold]{net:.2%}[/bold] net{div_str}  "
                            f"[dim]({model_name}: {intercept*100:+.2f}% + "
                            f"{slope} / adj.CAPE {adj_cape:.1f})[/dim]"
                        )
                    else:
                        console.print(
                            f"    CAPE SWR  : [bold]{gross:.2%}[/bold]  "
                            f"[dim]({model_name}: {intercept*100:+.2f}% + "
                            f"{slope} / adj.CAPE {adj_cape:.1f})[/dim]"
                        )

            if risk_on:
                withdraw_text = Text("  Withdraw from: STOCKS  ", style="bold white on green")
            else:
                withdraw_text = Text("  Withdraw from: BONDS  ", style="bold white on red")
            console.print(f"    Action    : ", end="")
            console.print(withdraw_text)

        console.print()

    # ── Momentum score history table ──────────────────────────────────────
    series_lists_hist = {}
    for ticker in results:
        adj_live, raw_live = results_live[ticker]
        if ticker in alt_series_live:
            series_lists_hist[ticker] = [adj_live, alt_series_live[ticker]]
        else:
            series_lists_hist[ticker] = [adj_live, raw_live]

    history_df = compute_momentum_history(series_lists_hist)
    console.print(make_score_history_table(
        history_df,
        labels={t: results[t][0] for t in results},
    ))
    console.print()

    # ── Individual tables ─────────────────────────────────────────────────
    for ticker, (label, adj, raw, sma) in results.items():
        cape_col = cape if ticker == "^SP500TR" else None
        console.print(make_table(ticker, label, adj, sma, cape=cape_col))
        console.print()



if __name__ == "__main__":
    main()
