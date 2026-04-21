"""
Predefined risk profiles that control how aggressively the system trades.
Users select a profile or customize individual parameters.
"""

from dataclasses import dataclass


@dataclass
class RiskProfile:
    name: str
    max_single_trade_risk_pct: float   # max % of account on one trade
    daily_loss_limit_pct: float        # circuit breaker threshold
    max_position_pct: float            # max % of account in one position
    max_sector_exposure_pct: float     # max % in one sector
    max_open_positions: int            # total concurrent positions
    max_options_delta: float           # max absolute delta for options
    min_days_to_expiry: int            # minimum DTE for options
    allow_earnings_plays: bool         # trade around earnings?
    goal_chase_enabled: bool           # increase risk when behind goal?


PROFILES = {
    "conservative": RiskProfile(
        name="conservative",
        max_single_trade_risk_pct=1.0,
        daily_loss_limit_pct=2.0,
        max_position_pct=8.0,
        max_sector_exposure_pct=20.0,
        max_open_positions=8,
        max_options_delta=0.40,
        min_days_to_expiry=14,
        allow_earnings_plays=False,
        goal_chase_enabled=False,  # never increase risk when behind
    ),
    "moderate": RiskProfile(
        name="moderate",
        max_single_trade_risk_pct=2.0,
        daily_loss_limit_pct=4.0,
        max_position_pct=12.0,
        max_sector_exposure_pct=30.0,
        max_open_positions=12,
        max_options_delta=0.50,
        min_days_to_expiry=7,
        allow_earnings_plays=False,
        goal_chase_enabled=False,
    ),
    "aggressive": RiskProfile(
        name="aggressive",
        max_single_trade_risk_pct=3.0,
        daily_loss_limit_pct=6.0,
        max_position_pct=15.0,
        max_sector_exposure_pct=35.0,
        max_open_positions=15,
        max_options_delta=0.60,
        min_days_to_expiry=3,
        allow_earnings_plays=True,
        goal_chase_enabled=False,  # still false — chasing losses is bad
    ),
}


def get_profile(name: str) -> RiskProfile:
    profile = PROFILES.get(name)
    if not profile:
        raise ValueError(
            f"Unknown risk profile '{name}'. "
            f"Available: {list(PROFILES.keys())}"
        )
    return profile
