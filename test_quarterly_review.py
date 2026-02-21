"""
test_quarterly_review.py
------------------------
Tests for quarterly_review.py using a stub momentum signal.

Run with:
    python3 -m pytest test_quarterly_review.py -v
or:
    python3 test_quarterly_review.py
"""

import tempfile
import unittest

from quarterly_review import (
    RetirementConfig,
    PortfolioState,
    run_quarterly_review,
)


def _stub_signal():
    """Weak/neutral signal → proportional withdrawal. GK rules are signal-independent."""
    return {"equity_signal": 0.0, "signal_strength": 0.0}


def _make_config(tmp_path: str) -> RetirementConfig:
    return RetirementConfig(
        initial_portfolio_value=1_000_000.0,
        base_withdrawal_rate=0.05,
        inflation_rate=0.0,        # zero inflation keeps the baseline fixed at $1M
        audit_log_path=tmp_path,   # avoid leaving files in the project root during tests
    )


class TestGKCapitalPreservation(unittest.TestCase):

    def test_two_quarter_simulation(self):
        """
        Q1: Portfolio at full value → no GK rule fires.
        Q2: Portfolio drops to 75% of initial ($750k) → capital preservation must fire
            and annual withdrawal must be cut by 10%.

        Capital preservation threshold is 80% of the inflation-adjusted baseline.
        At 75% < 80%, the rule cuts the annual withdrawal by 10% (to $45,000).
        """
        with tempfile.NamedTemporaryFile(suffix=".jsonl") as tmp:
            config = _make_config(tmp.name)
            annual_withdrawal = 50_000.0   # 5% of $1M

            # ── Quarter 1: healthy portfolio, no rule should fire ──────────────
            portfolio = PortfolioState(
                equity_value=850_000.0,   # 85% of $1M
                bond_value=150_000.0,     # 15% of $1M
                quarter="2026-Q1",
                cumulative_inflation=1.0,
            )

            decision1, portfolio, annual_withdrawal = run_quarterly_review(
                portfolio=portfolio,
                current_annual_withdrawal=annual_withdrawal,
                config=config,
                get_momentum_signal_fn=_stub_signal,
            )

            self.assertEqual(
                decision1.gk_rule_triggered, "none",
                "Q1: no GK rule should fire when portfolio is at full value",
            )

            # ── Simulate a portfolio crash to 75% of initial before Q2 ─────────
            # 75% of $1M = $750k, split 85/15
            portfolio.equity_value = 637_500.0
            portfolio.bond_value   = 112_500.0
            portfolio.quarter      = "2026-Q2"

            decision2, _, new_annual = run_quarterly_review(
                portfolio=portfolio,
                current_annual_withdrawal=annual_withdrawal,
                config=config,
                get_momentum_signal_fn=_stub_signal,
            )

            # Capital preservation must fire (75% < 80% threshold)
            self.assertEqual(
                decision2.gk_rule_triggered, "capital_preservation",
                "Q2: capital preservation should fire when portfolio is at 75% of initial",
            )

            # Annual withdrawal must be cut by 10%
            self.assertAlmostEqual(
                new_annual,
                annual_withdrawal * 0.90,
                places=2,
                msg="Capital preservation rule must cut annual withdrawal by 10%",
            )


if __name__ == "__main__":
    unittest.main()
