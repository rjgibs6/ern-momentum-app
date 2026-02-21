"""
quarterly_review.py
-------------------
Quarterly Retirement Manager — library module.

Intended to be called from run_quarterly.py, which supplies a live
momentum signal via the get_momentum_signal_fn parameter.

Implements:
  - Momentum-directed withdrawal source (equity vs bond vs proportional)
  - Guyton-Klinger withdrawal rules (prosperity raise, capital preservation cut)
  - Fixed 85/15 equity/bond allocation (no rebalancing)
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import date
from typing import Literal

import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class RetirementConfig:
    """
    Edit these values to match your situation.
    All rates are ANNUAL; the module converts to quarterly internally.
    """
    # ── Portfolio ──────────────────────────────────────────────────────────────
    initial_portfolio_value: float = 1_000_000.0   # Value at retirement start
    equity_target: float = 0.85                    # Fixed equity allocation (informational)
    bond_target: float   = 0.15                    # Fixed bond/cash allocation (informational)

    # ── Guyton-Klinger withdrawal rules ───────────────────────────────────────
    base_withdrawal_rate: float = 0.05        # Initial annual withdrawal rate
    inflation_rate: float = 0.03              # Expected annual inflation for COLA
    prosperity_threshold: float = 1.20       # Portfolio > 120% of inflation-adj baseline → raise
    prosperity_increase: float = 0.10        # Raise withdrawal by 10% when prosperous
    capital_preservation_threshold: float = 0.80  # Portfolio < 80% of baseline → cut
    capital_preservation_decrease: float = 0.10   # Cut withdrawal by 10%
    max_withdrawal_rate: float = 0.06        # Hard annual ceiling
    min_withdrawal_rate: float = 0.025       # Hard annual floor

    # ── Momentum signal threshold ─────────────────────────────────────────────
    # If signal_strength < this, withdraw proportionally instead of directed
    signal_strength_threshold: float = 0.30

    # ── Audit log ─────────────────────────────────────────────────────────────
    audit_log_path: str = "retirement_audit.jsonl"


def load_config(path: str = "retirement_config.yaml") -> RetirementConfig:
    """Load RetirementConfig from a YAML file. Missing keys fall back to dataclass defaults."""
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return RetirementConfig(**data)


# ── Core data structures ──────────────────────────────────────────────────────

@dataclass
class PortfolioState:
    equity_value: float
    bond_value: float
    quarter: str              # e.g. "2026-Q1"
    cumulative_inflation: float = 1.0   # compounded inflation since retirement start

    @property
    def total_value(self) -> float:
        return self.equity_value + self.bond_value

    @property
    def equity_pct(self) -> float:
        return self.equity_value / self.total_value if self.total_value else 0.0

    @property
    def bond_pct(self) -> float:
        return self.bond_value / self.total_value if self.total_value else 0.0


@dataclass
class QuarterlyDecision:
    quarter: str
    momentum_signal: float
    signal_strength: float
    withdrawal_amount: float
    withdrawal_source: Literal["equity", "bond", "proportional"]
    gk_rule_triggered: str   # "none", "prosperity", "capital_preservation", "ceiling", "floor"
    equity_value_after: float
    bond_value_after: float
    new_annual_withdrawal: float


# ── Step 1: Withdrawal Source ─────────────────────────────────────────────────

def decide_withdrawal_source(
    signal: float,
    strength: float,
    config: RetirementConfig,
) -> Literal["equity", "bond", "proportional"]:
    """
    Risk-on  (positive signal, strong) → withdraw from equity
    Risk-off (negative signal, strong) → withdraw from bonds
    Weak signal → proportional withdrawal
    """
    if strength < config.signal_strength_threshold:
        return "proportional"
    return "equity" if signal > 0 else "bond"


# ── Step 2: Guyton-Klinger Withdrawal Amount ──────────────────────────────────

def compute_gk_withdrawal(
    portfolio: PortfolioState,
    current_annual_withdrawal: float,
    config: RetirementConfig,
) -> tuple[float, float, str]:
    """
    Returns (quarterly_withdrawal, new_annual_withdrawal, rule_triggered).
    """
    rule_triggered = "none"
    inflation_adj_baseline = config.initial_portfolio_value * portfolio.cumulative_inflation

    # Prosperity rule
    if portfolio.total_value > inflation_adj_baseline * config.prosperity_threshold:
        proposed = current_annual_withdrawal * (1 + config.prosperity_increase)
        if proposed / portfolio.total_value <= config.max_withdrawal_rate:
            current_annual_withdrawal = proposed
            rule_triggered = "prosperity"
            log.info(f"[GK] Prosperity rule: raising annual withdrawal to ${current_annual_withdrawal:,.0f}")

    # Capital preservation rule
    elif portfolio.total_value < inflation_adj_baseline * config.capital_preservation_threshold:
        proposed = current_annual_withdrawal * (1 - config.capital_preservation_decrease)
        if proposed / portfolio.total_value >= config.min_withdrawal_rate:
            current_annual_withdrawal = proposed
            rule_triggered = "capital_preservation"
            log.info(f"[GK] Capital preservation: cutting annual withdrawal to ${current_annual_withdrawal:,.0f}")
        else:
            current_annual_withdrawal = portfolio.total_value * config.min_withdrawal_rate
            rule_triggered = "floor"
            log.warning(f"[GK] Floor hit: withdrawal set to ${current_annual_withdrawal:,.0f}")

    # Hard ceiling (independent of rules above)
    if current_annual_withdrawal / portfolio.total_value > config.max_withdrawal_rate:
        current_annual_withdrawal = portfolio.total_value * config.max_withdrawal_rate
        rule_triggered = "ceiling"
        log.warning(f"[GK] Ceiling hit: withdrawal capped at ${current_annual_withdrawal:,.0f}")

    return current_annual_withdrawal / 4, current_annual_withdrawal, rule_triggered


# ── Step 3: Execute Withdrawal ────────────────────────────────────────────────

def execute_withdrawal(
    portfolio: PortfolioState,
    amount: float,
    source: Literal["equity", "bond", "proportional"],
) -> PortfolioState:
    """Deducts withdrawal from the specified source. Proportional splits by current weight."""
    if source == "equity":
        if amount > portfolio.equity_value:
            log.warning("Withdrawal exceeds equity value; taking remainder from bonds.")
            overflow = amount - portfolio.equity_value
            portfolio.equity_value = 0.0
            portfolio.bond_value  -= overflow
        else:
            portfolio.equity_value -= amount

    elif source == "bond":
        if amount > portfolio.bond_value:
            log.warning("Withdrawal exceeds bond value; taking remainder from equity.")
            overflow = amount - portfolio.bond_value
            portfolio.bond_value   = 0.0
            portfolio.equity_value -= overflow
        else:
            portfolio.bond_value -= amount

    else:  # proportional
        portfolio.equity_value -= amount * portfolio.equity_pct
        portfolio.bond_value   -= amount * portfolio.bond_pct

    return portfolio


# ── Inflation Update Helper ───────────────────────────────────────────────────

def apply_quarterly_inflation(portfolio: PortfolioState, annual_inflation: float) -> PortfolioState:
    """Call each quarter to keep the cumulative inflation tracker current."""
    quarterly_rate = (1 + annual_inflation) ** (1 / 4) - 1
    portfolio.cumulative_inflation *= (1 + quarterly_rate)
    return portfolio


# ── Audit Log ─────────────────────────────────────────────────────────────────

def _write_audit_log(decision: QuarterlyDecision, path: str) -> None:
    record = asdict(decision)
    record["logged_at"] = date.today().isoformat()
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    log.info(f"[Audit] Logged to {path}")


# ── Master Quarterly Runner ───────────────────────────────────────────────────

def run_quarterly_review(
    portfolio: PortfolioState,
    current_annual_withdrawal: float,
    config: RetirementConfig,
    get_momentum_signal_fn,   # callable → dict with 'equity_signal', 'signal_strength'
) -> tuple[QuarterlyDecision, PortfolioState, float]:
    """
    Orchestrates one full quarterly review cycle.

    get_momentum_signal_fn must return:
        {"equity_signal": float, "signal_strength": float}

    Returns:
        decision      (QuarterlyDecision) — full record of what happened
        portfolio     (PortfolioState)    — updated portfolio after withdrawal
        new_annual_wd (float)             — updated annual withdrawal for next quarter
    """
    log.info(f"\n{'='*60}")
    log.info(f"  Quarterly Review: {portfolio.quarter}")
    log.info(f"  Portfolio: ${portfolio.total_value:,.0f}  "
             f"(Eq: {portfolio.equity_pct:.1%} | Bd: {portfolio.bond_pct:.1%})")
    log.info(f"{'='*60}")

    # 1. Momentum signal (supplied by caller)
    momentum = get_momentum_signal_fn()
    signal   = momentum["equity_signal"]
    strength = momentum["signal_strength"]
    log.info(f"[Signal] equity_signal={signal:+.3f}, strength={strength:.3f}")

    # 2. Withdrawal source
    source = decide_withdrawal_source(signal, strength, config)
    log.info(f"[Withdrawal] Source: {source}")

    # 3. Guyton-Klinger amount
    q_withdrawal, new_annual_wd, gk_rule = compute_gk_withdrawal(
        portfolio, current_annual_withdrawal, config
    )
    log.info(f"[Withdrawal] Quarterly: ${q_withdrawal:,.0f}  "
             f"(Annual: ${new_annual_wd:,.0f})  GK rule: {gk_rule}")

    # 4. Execute withdrawal
    portfolio = execute_withdrawal(portfolio, q_withdrawal, source)
    log.info(f"[Post-withdrawal] ${portfolio.total_value:,.0f}  "
             f"(Eq: ${portfolio.equity_value:,.0f} | Bd: ${portfolio.bond_value:,.0f})")

    # 5. Record decision
    decision = QuarterlyDecision(
        quarter=portfolio.quarter,
        momentum_signal=signal,
        signal_strength=strength,
        withdrawal_amount=q_withdrawal,
        withdrawal_source=source,
        gk_rule_triggered=gk_rule,
        equity_value_after=portfolio.equity_value,
        bond_value_after=portfolio.bond_value,
        new_annual_withdrawal=new_annual_wd,
    )

    # 6. Audit log
    _write_audit_log(decision, config.audit_log_path)

    return decision, portfolio, new_annual_wd
