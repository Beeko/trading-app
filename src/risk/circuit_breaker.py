"""
Circuit breaker — monitors cumulative daily P&L and halts all trading
when losses exceed the configured threshold. This is the hardest
safety rail in the system and cannot be overridden while running.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from config import settings
from src.utils.logger import get_logger

log = get_logger("circuit_breaker")


@dataclass
class BreakerState:
    """Current state of the circuit breaker."""
    is_tripped: bool = False
    tripped_at: Optional[datetime] = None
    reason: str = ""
    loss_at_trip: float = 0.0
    loss_limit: float = 0.0
    consecutive_losses: int = 0


class CircuitBreaker:
    """
    Monitors P&L and halts trading when safety thresholds are breached.
    
    Three trip conditions:
    1. Daily loss exceeds configured percentage of account
    2. Too many consecutive losing trades
    3. Single trade loss exceeds maximum allowed
    """

    def __init__(
        self,
        account_balance: float,
        daily_loss_pct: Optional[float] = None,
        max_consecutive_losses: int = 5,
        max_single_loss_pct: Optional[float] = None,
    ):
        self._account_balance = account_balance
        self._daily_loss_pct = daily_loss_pct or settings.DAILY_LOSS_LIMIT_PCT
        self._max_consecutive = max_consecutive_losses
        self._max_single_pct = (
            max_single_loss_pct or settings.MAX_SINGLE_TRADE_RISK_PCT * 2
        )

        self._loss_limit = account_balance * (self._daily_loss_pct / 100.0)
        self._state = BreakerState(loss_limit=self._loss_limit)
        self._cumulative_pnl = 0.0
        self._consecutive_losses = 0
        self._on_trip_callbacks: list = []

        log.info(
            f"CircuitBreaker armed: "
            f"daily limit -${self._loss_limit:.2f} "
            f"({self._daily_loss_pct}% of ${account_balance:.2f}), "
            f"max consecutive losses: {max_consecutive_losses}"
        )

    @property
    def is_tripped(self) -> bool:
        return self._state.is_tripped

    @property
    def state(self) -> BreakerState:
        return self._state

    def on_trip(self, callback):
        """Register a callback to be called when breaker trips."""
        self._on_trip_callbacks.append(callback)

    def check_pre_trade(self, potential_loss: float) -> tuple[bool, str]:
        """
        Check if a proposed trade would violate safety limits.
        Returns (allowed, reason).
        """
        if self._state.is_tripped:
            return False, f"Circuit breaker tripped: {self._state.reason}"

        # Would this single trade risk too much?
        single_limit = self._account_balance * (self._max_single_pct / 100.0)
        if abs(potential_loss) > single_limit:
            return False, (
                f"Single trade risk ${abs(potential_loss):.2f} exceeds "
                f"limit ${single_limit:.2f} "
                f"({self._max_single_pct}% of account)"
            )

        # Would cumulative losses breach the daily limit?
        projected = self._cumulative_pnl - abs(potential_loss)
        if projected < -self._loss_limit:
            return False, (
                f"Trade would push daily P&L to ${projected:.2f}, "
                f"breaching limit -${self._loss_limit:.2f}"
            )

        return True, "OK"

    def record_trade_result(self, pnl: float):
        """
        Record a completed trade's P&L and check trip conditions.
        """
        if self._state.is_tripped:
            return

        self._cumulative_pnl += pnl

        # Track consecutive losses
        if pnl < 0:
            self._consecutive_losses += 1
            self._state.consecutive_losses = self._consecutive_losses
        else:
            self._consecutive_losses = 0
            self._state.consecutive_losses = 0

        # Check trip conditions
        if self._cumulative_pnl <= -self._loss_limit:
            self._trip(
                f"Daily loss limit breached: "
                f"P&L ${self._cumulative_pnl:.2f} "
                f"< limit -${self._loss_limit:.2f}"
            )
        elif self._consecutive_losses >= self._max_consecutive:
            self._trip(
                f"Max consecutive losses reached: "
                f"{self._consecutive_losses} losses in a row"
            )

    def _trip(self, reason: str):
        """Trip the circuit breaker — halts all trading."""
        self._state.is_tripped = True
        self._state.tripped_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self._state.reason = reason
        self._state.loss_at_trip = self._cumulative_pnl

        log.warning(f"CIRCUIT BREAKER TRIPPED: {reason}")

        # Notify all registered callbacks
        for callback in self._on_trip_callbacks:
            try:
                callback(self._state)
            except Exception as e:
                log.error(f"Trip callback failed: {e}")

    def reset(self, new_balance: Optional[float] = None):
        """
        Reset the circuit breaker for a new trading day.
        Should only be called when the app restarts, not mid-session.
        """
        if new_balance:
            self._account_balance = new_balance
            self._loss_limit = new_balance * (self._daily_loss_pct / 100.0)

        self._state = BreakerState(loss_limit=self._loss_limit)
        self._cumulative_pnl = 0.0
        self._consecutive_losses = 0
        log.info(
            f"Circuit breaker reset: new limit -${self._loss_limit:.2f}"
        )

    def get_remaining_risk_budget(self) -> float:
        """How much more can be lost before the breaker trips."""
        if self._state.is_tripped:
            return 0.0
        return self._loss_limit + self._cumulative_pnl
