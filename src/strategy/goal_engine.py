"""
Goal engine — manages daily profit targets, tracks real-time P&L,
and adjusts position sizing based on progress toward the goal.
"""

from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from enum import Enum
from typing import Optional
from config import settings
from src.utils.logger import get_logger

log = get_logger("goal_engine")


class TradingMode(str, Enum):
    CONSERVATIVE = "conservative"     # default start-of-day mode
    NORMAL = "normal"                 # making progress toward goal
    PROTECT_GAINS = "protect_gains"   # goal met, protect profits
    REDUCED = "reduced"               # behind goal, get MORE cautious
    HALTED = "halted"                 # circuit breaker hit


@dataclass
class DailyGoal:
    """Tracks the day's profit target and progress."""
    date: date = field(default_factory=date.today)
    target: float = 0.0
    starting_balance: float = 0.0
    current_balance: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    mode: TradingMode = TradingMode.CONSERVATIVE

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def progress_pct(self) -> float:
        if self.target == 0:
            return 0.0
        return (self.total_pnl / self.target) * 100.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100.0


class GoalEngine:
    """Manages daily goals and dynamically adjusts trading behavior."""

    def __init__(self, daily_target: Optional[float] = None):
        self._daily_target = daily_target or settings.DAILY_PROFIT_TARGET
        self._goal: Optional[DailyGoal] = None
        self._position_size_multiplier = 1.0
        log.info(f"GoalEngine initialized (target=${self._daily_target:.2f})")

    def start_day(self, account_balance: float) -> DailyGoal:
        """Initialize a new trading day with a fresh goal."""
        self._goal = DailyGoal(
            date=date.today(),
            target=self._daily_target,
            starting_balance=account_balance,
            current_balance=account_balance,
            mode=TradingMode.CONSERVATIVE,
        )
        self._position_size_multiplier = 1.0
        log.info(
            f"Day started: balance=${account_balance:.2f}, "
            f"target=${self._daily_target:.2f}"
        )
        return self._goal

    def update_pnl(
        self,
        realized_pnl: float,
        unrealized_pnl: float,
        current_balance: float,
    ):
        """Update P&L and recalculate trading mode."""
        if not self._goal:
            return

        self._goal.realized_pnl = realized_pnl
        self._goal.unrealized_pnl = unrealized_pnl
        self._goal.current_balance = current_balance

        # Determine trading mode based on progress
        self._update_mode()

    def record_trade(self, pnl: float):
        """Record a completed trade's result."""
        if not self._goal:
            return

        self._goal.total_trades += 1
        if pnl >= 0:
            self._goal.winning_trades += 1
        else:
            self._goal.losing_trades += 1

    def get_position_size_multiplier(self) -> float:
        """Get the current position size multiplier based on mode."""
        return self._position_size_multiplier

    def get_mode(self) -> TradingMode:
        """Get the current trading mode."""
        if not self._goal:
            return TradingMode.CONSERVATIVE
        return self._goal.mode

    def get_goal(self) -> Optional[DailyGoal]:
        return self._goal

    def halt_trading(self, reason: str):
        """Emergency halt — called by circuit breaker."""
        if self._goal:
            self._goal.mode = TradingMode.HALTED
            self._position_size_multiplier = 0.0
            log.warning(f"Trading HALTED: {reason}")

    def set_daily_target(self, target: float):
        """Update the daily target (takes effect next day or immediately)."""
        self._daily_target = target
        if self._goal:
            self._goal.target = target
        log.info(f"Daily target updated to ${target:.2f}")

    def _update_mode(self):
        """Recalculate trading mode based on current P&L."""
        if not self._goal:
            return

        progress = self._goal.progress_pct
        total_pnl = self._goal.total_pnl
        loss_limit = (
            self._goal.starting_balance
            * settings.DAILY_LOSS_LIMIT_PCT / 100.0
        )

        # Circuit breaker check
        if total_pnl <= -loss_limit:
            self._goal.mode = TradingMode.HALTED
            self._position_size_multiplier = 0.0
            log.warning(
                f"Circuit breaker triggered: P&L ${total_pnl:.2f} "
                f"exceeded loss limit -${loss_limit:.2f}"
            )
            return

        # Mode transitions
        if progress >= 100:
            # Goal met — protect gains, minimal new trades
            self._goal.mode = TradingMode.PROTECT_GAINS
            self._position_size_multiplier = 0.5
            log.info(f"Goal met ({progress:.0f}%) — protect gains mode")

        elif progress >= 50:
            # Good progress — normal trading
            self._goal.mode = TradingMode.NORMAL
            self._position_size_multiplier = 1.0

        elif progress >= 0:
            # Early in the day or flat — conservative
            self._goal.mode = TradingMode.CONSERVATIVE
            self._position_size_multiplier = 0.8

        else:
            # Behind / losing — get MORE conservative, not less
            self._goal.mode = TradingMode.REDUCED
            self._position_size_multiplier = 0.5
            log.info(
                f"Behind target ({progress:.0f}%) — reduced mode, "
                f"smaller positions"
            )
