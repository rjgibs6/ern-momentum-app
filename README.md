# ERN Momentum Signal

CLI app implementing the [Early Retirement Now](https://earlyretirementnow.com) momentum strategy.
Compares monthly closes to their 10-month SMA to determine Risk-On / Risk-Off posture for equities and bonds.

## Features

- **12-signal momentum score** (ERN Part 63): 3 horizons × 2 methods × 2 indices per asset
- **Momentum score history table**: signal strength for each past month including live current month
- **CAPE-based SWR** (ERN Part 54): Recommended and Conservative models with optional dividend subtraction
- **Withdrawal action**: draws from stocks (Risk-On) or bonds (Risk-Off) based on equity signal

## Usage

```bash
python3 momentum.py                  # no dividend adjustment
python3 momentum.py --dividend 1.5   # subtract 1.5% dividend yield from CAPE SWR
```

## Install dependencies

```bash
python3 -m pip install -r requirements.txt
```

## Assets tracked

| Ticker | Description |
|--------|-------------|
| `^SP500TR` | S&P 500 Total Return (primary equity index) |
| `^GSPC` | S&P 500 Price Return (alt equity index for scoring) |
| `IEF` | iShares 7-10yr Treasury (bond index) |

## CAPE SWR models (ERN Part 54)

Formula: `SWR = intercept + slope / adjusted_CAPE`
Adjusted CAPE = Shiller CAPE × 0.775 (ERN earnings adjustment)

| Model | Intercept | Slope |
|-------|-----------|-------|
| Recommended | +1.75% | 0.50 |
| Conservative | −0.25% | 0.90 |
