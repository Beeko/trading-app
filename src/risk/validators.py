"""
Trade validators — composable rule functions that each check one
aspect of a proposed trade. The risk manager runs all of them.
"""

from dataclasses import dataclass
from typing import Callable
from src.strategy.signals import TradeSignal
from config import settings
from config.risk_profiles import get_profile
from src.utils.logger import get_logger

log = get_logger("validators")


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    passed: bool
    rule_name: str
    message: str


# Type alias for validator functions
Validator = Callable[
    [TradeSignal, dict, float],  # signal, current_positions, account_balance
    ValidationResult,
]


def validate_position_size(
    signal: TradeSignal,
    positions: dict,
    balance: float,
) -> ValidationResult:
    """Ensure a single position doesn't exceed max allocation."""
    profile = get_profile(settings.RISK_PROFILE)
    max_alloc = balance * (profile.max_position_pct / 100.0)
    proposed_alloc = balance * (signal.suggested_size_pct / 100.0)

    if proposed_alloc > max_alloc:
        return ValidationResult(
            passed=False,
            rule_name="position_size",
            message=(
                f"Proposed allocation ${proposed_alloc:.2f} "
                f"({signal.suggested_size_pct}%) exceeds max "
                f"${max_alloc:.2f} ({profile.max_position_pct}%)"
            ),
        )
    return ValidationResult(True, "position_size", "OK")


def validate_max_open_positions(
    signal: TradeSignal,
    positions: dict,
    balance: float,
) -> ValidationResult:
    """Ensure we don't exceed the maximum number of open positions."""
    profile = get_profile(settings.RISK_PROFILE)
    current = len(positions)

    if current >= profile.max_open_positions:
        return ValidationResult(
            passed=False,
            rule_name="max_open_positions",
            message=(
                f"Already at max positions ({current}/"
                f"{profile.max_open_positions})"
            ),
        )
    return ValidationResult(True, "max_open_positions", "OK")


def validate_sector_exposure(
    signal: TradeSignal,
    positions: dict,
    balance: float,
) -> ValidationResult:
    """Ensure we don't concentrate too heavily in one sector."""
    profile = get_profile(settings.RISK_PROFILE)
    max_sector = balance * (profile.max_sector_exposure_pct / 100.0)

    # Sum existing exposure in the same sector
    sector = positions.get(signal.ticker, {}).get("sector", "unknown")
    sector_total = sum(
        pos.get("market_value", 0)
        for pos in positions.values()
        if pos.get("sector") == sector
    )
    proposed = balance * (signal.suggested_size_pct / 100.0)

    if sector_total + proposed > max_sector:
        return ValidationResult(
            passed=False,
            rule_name="sector_exposure",
            message=(
                f"Sector '{sector}' exposure would reach "
                f"${sector_total + proposed:.2f}, "
                f"exceeding max ${max_sector:.2f}"
            ),
        )
    return ValidationResult(True, "sector_exposure", "OK")


def validate_no_penny_stocks(
    signal: TradeSignal,
    positions: dict,
    balance: float,
) -> ValidationResult:
    """Block trades on stocks below the minimum price."""
    price = signal.indicator_data.get("vwap", {}).get("detail", {}).get("price", 0)
    if price and price < settings.MIN_STOCK_PRICE:
        return ValidationResult(
            passed=False,
            rule_name="no_penny_stocks",
            message=(
                f"Stock price ${price:.2f} below minimum "
                f"${settings.MIN_STOCK_PRICE:.2f}"
            ),
        )
    return ValidationResult(True, "no_penny_stocks", "OK")


def validate_options_whitelist(
    signal: TradeSignal,
    positions: dict,
    balance: float,
) -> ValidationResult:
    """Ensure options strategy is on the approved list."""
    if signal.asset_type != "option":
        return ValidationResult(True, "options_whitelist", "N/A — stock trade")

    strategy = signal.indicator_data.get("options_strategy", "")
    if strategy and strategy not in settings.ALLOWED_OPTIONS_STRATEGIES:
        return ValidationResult(
            passed=False,
            rule_name="options_whitelist",
            message=(
                f"Strategy '{strategy}' not in approved list: "
                f"{settings.ALLOWED_OPTIONS_STRATEGIES}"
            ),
        )
    return ValidationResult(True, "options_whitelist", "OK")


def validate_duplicate_position(
    signal: TradeSignal,
    positions: dict,
    balance: float,
) -> ValidationResult:
    """Warn if we already have a position in this ticker."""
    if signal.ticker in positions and signal.direction == "buy":
        existing = positions[signal.ticker]
        return ValidationResult(
            passed=False,
            rule_name="duplicate_position",
            message=(
                f"Already holding {signal.ticker}: "
                f"{existing.get('qty', 0)} shares @ "
                f"${existing.get('avg_price', 0):.2f}"
            ),
        )
    return ValidationResult(True, "duplicate_position", "OK")


def validate_min_confidence(
    signal: TradeSignal,
    positions: dict,
    balance: float,
) -> ValidationResult:
    """Block low-confidence signals."""
    if signal.confidence < 0.3:
        return ValidationResult(
            passed=False,
            rule_name="min_confidence",
            message=(
                f"Signal confidence {signal.confidence:.2f} "
                f"below minimum threshold 0.30"
            ),
        )
    return ValidationResult(True, "min_confidence", "OK")


# All validators to run, in order
ALL_VALIDATORS: list[Validator] = [
    validate_min_confidence,
    validate_position_size,
    validate_max_open_positions,
    validate_sector_exposure,
    validate_no_penny_stocks,
    validate_options_whitelist,
    validate_duplicate_position,
]
