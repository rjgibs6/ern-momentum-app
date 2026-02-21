#!/usr/bin/env python3
"""
run_quarterly.py
----------------
CLI entry point for the quarterly retirement review.

Interactive usage (prompts for portfolio values):
    python3 run_quarterly.py

Pass values directly as arguments:
    python3 run_quarterly.py --equity 850000 --bonds 150000 --quarter 2026-Q1

All three flags must be supplied together; omit all three to be prompted.

Optional flags:
    --annual-withdrawal 52000     Current annual withdrawal (if GK has adjusted it from initial)
    --cumulative-inflation 1.062  Compounded inflation factor since retirement start
    --config retirement_config.yaml  Path to config file (default: retirement_config.yaml)
"""

import argparse
import sys

from momentum import fetch_both_closes, compute_momentum_score, ASSET_ALT_TICKER
from quarterly_review import (
    PortfolioState,
    apply_quarterly_inflation,
    load_config,
    run_quarterly_review,
)


# ── Adapter ───────────────────────────────────────────────────────────────────

def get_momentum_signal() -> dict:
    """
    Adapts momentum.py's 12-signal score to the format quarterly_review.py expects.

    momentum.py produces a 0–1 score (bullish signals / total signals).
    quarterly_review.py expects:
        equity_signal   : float  positive = risk-on, negative = risk-off
        signal_strength : float  0.0 (neutral) → 1.0 (fully directional)

    Mapping:
        equity_signal   = score − 0.5          → range [−0.5, +0.5]
        signal_strength = |equity_signal| × 2  → range [0, 1]
    """
    adj, _     = fetch_both_closes("^SP500TR")
    alt_adj, _ = fetch_both_closes(ASSET_ALT_TICKER["^SP500TR"])   # ^GSPC

    bullish, total = compute_momentum_score([adj, alt_adj])
    score = bullish / total if total else 0.5

    equity_signal   = score - 0.5
    signal_strength = abs(equity_signal) * 2
    return {"equity_signal": equity_signal, "signal_strength": signal_strength}


# ── Input handling ────────────────────────────────────────────────────────────

def _parse_dollars(raw: str) -> float:
    """Accept values like 850000, 850,000, or $850,000."""
    return float(raw.strip().lstrip("$").replace(",", ""))


def prompt_inputs() -> tuple[float, float, str]:
    print()
    print("Enter your current portfolio values (commas and $ signs are fine):")
    equity  = _parse_dollars(input("  Equity value:     $"))
    bonds   = _parse_dollars(input("  Bond/cash value:  $"))
    quarter = input("  Quarter label (e.g. 2026-Q1): ").strip()
    return equity, bonds, quarter


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run the quarterly retirement review.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 run_quarterly.py\n"
            "  python3 run_quarterly.py --equity 850000 --bonds 150000 --quarter 2026-Q1\n"
            "  python3 run_quarterly.py --equity 850000 --bonds 150000 --quarter 2026-Q2 \\\n"
            "      --annual-withdrawal 52000 --cumulative-inflation 1.031\n"
        ),
    )
    p.add_argument("--equity",  type=str, help="Current equity value in dollars")
    p.add_argument("--bonds",   type=str, help="Current bond/cash value in dollars")
    p.add_argument("--quarter", type=str, help="Quarter label, e.g. 2026-Q1")

    p.add_argument(
        "--annual-withdrawal", type=float, default=None,
        help="Current annual withdrawal amount (use if GK rules have adjusted it from the initial). "
             "Defaults to initial_portfolio_value × base_withdrawal_rate from config.",
    )
    p.add_argument(
        "--cumulative-inflation", type=float, default=1.0, metavar="FACTOR",
        help="Compounded inflation factor since retirement start (default: 1.0). "
             "E.g. after 2 years at 3%%: 1.03^2 ≈ 1.061.",
    )
    p.add_argument(
        "--config", type=str, default="retirement_config.yaml", metavar="PATH",
        help="Path to the YAML config file (default: retirement_config.yaml).",
    )
    return p


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse = build_arg_parser().parse_args()

    cli_flags = [args.equity, args.bonds, args.quarter]
    if any(f is not None for f in cli_flags):
        if not all(f is not None for f in cli_flags):
            print("error: --equity, --bonds, and --quarter must all be provided together.")
            sys.exit(1)
        equity_value = _parse_dollars(args.equity)
        bond_value   = _parse_dollars(args.bonds)
        quarter      = args.quarter.strip()
    else:
        equity_value, bond_value, quarter = prompt_inputs()

    config = load_config(args.config)

    portfolio = PortfolioState(
        equity_value=equity_value,
        bond_value=bond_value,
        quarter=quarter,
        cumulative_inflation=args.cumulative_inflation,
    )

    annual_withdrawal = (
        args.annual_withdrawal
        if args.annual_withdrawal is not None
        else config.initial_portfolio_value * config.base_withdrawal_rate
    )

    # Advance inflation tracker for this quarter before running the review
    portfolio = apply_quarterly_inflation(portfolio, config.inflation_rate)

    print("\nFetching live momentum signal…")
    decision, portfolio, annual_withdrawal = run_quarterly_review(
        portfolio=portfolio,
        current_annual_withdrawal=annual_withdrawal,
        config=config,
        get_momentum_signal_fn=get_momentum_signal,
    )

    print("\n── Quarterly Decision ─────────────────────────────────")
    print(f"  Quarter            : {decision.quarter}")
    print(f"  Momentum signal    : {decision.momentum_signal:+.3f}  "
          f"(strength: {decision.signal_strength:.3f})")
    print(f"  Withdrawal source  : {decision.withdrawal_source}")
    print(f"  Withdrawal amount  : ${decision.withdrawal_amount:,.2f}  (this quarter)")
    print(f"  GK rule triggered  : {decision.gk_rule_triggered}")
    print(f"  Annual going fwd   : ${annual_withdrawal:,.2f}")
    print()
    print("── Portfolio After Withdrawal ─────────────────────────")
    print(f"  Total:  ${portfolio.total_value:,.2f}")
    print(f"  Equity: ${portfolio.equity_value:,.2f}  ({portfolio.equity_pct:.1%})")
    print(f"  Bonds:  ${portfolio.bond_value:,.2f}   ({portfolio.bond_pct:.1%})")
    print()
    print(f"  Audit log updated → {config.audit_log_path}")
