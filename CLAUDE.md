# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
python3 momentum.py
```

## Installing dependencies

```bash
python3 -m pip install -r requirements.txt
```

## Architecture

Single-file CLI app (`momentum.py`). All logic lives there:

- **Data layer** — `fetch_monthly_closes()` fetches `^SP500TR` via `yf.Ticker` with up to 3 retries (5 s / 10 s backoff). Trims the current in-progress month and returns the last 15 completed monthly closes as a `pd.Series`. Yahoo Finance is the only free source carrying this index; there is no secondary fallback.
- **Signal logic** — `main()` computes the 10-month SMA with `pd.Series.rolling(10).mean()` and compares the latest `^SP500TR` close to it. Above → Risk-On, below → Risk-Off.
- **Output** — uses the `rich` library: colored badge for the signal, inline stats, and a `rich.table.Table` showing the last 3 months with price, SMA, and % distance. Table cells use `rich.text.Text` objects (not markup strings) to avoid nested-tag parsing errors.

## Key design decisions

- The current (incomplete) calendar month is always dropped so the signal is based on finished monthly candles.
- `FETCH_MONTHS = 15` ensures the last 3 displayed rows always have a valid 10-month SMA (10 + 5 buffer).
- `yf.Ticker(session=...)` passes a browser-like `User-Agent` to reduce Yahoo Finance rate-limiting.
- Stooq returns dates in descending order and uses end-of-month timestamps; the fetch function re-normalizes to month-start to stay consistent with yfinance.
