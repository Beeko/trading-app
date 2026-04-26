"""
Risk manager — the central gate every proposed trade must pass through.
Combines circuit breaker checks, validator rules, and account state
to approve or reject trades with full reasoning.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from src.strategy.signals import TradeSignal
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.validators import ALL_VALIDATORS, ValidationResult
from src.utils.logger import get_logger

log = get_logger("risk_manager")


@dataclass
class RiskDecision:
    """The risk manager's verdict on a proposed trade."""
    approved: bool
    signal: TradeSignal
    timestamp: datetime
    reasons: list[str]           # why approved or rejected
    failed_rules: list[str]      # which specific rules failed
    adjusted_size_pct: float     # may be reduced from original
    remaining_risk_budget: float


class RiskManager:
    """
    Central risk gate. Every proposed trade passes through here.
    No trade reaches execution without risk manager approval.
    """

    def __init__(self, circuit_breaker: CircuitBreaker):
        self._breaker = circuit_breaker
        self._positions: dict = {}
        self._account_balance: float = 0.0
        self._daily_trades: list[RiskDecision] = []
        log.info("RiskManager initialized")

    def update_state(
        self,
        positions: dict,
        account_balance: float,
    ):
        """Update current account state for risk calculations."""
        self._positions = positions
        self._account_balance = account_balance

    def evaluate(self, signal: TradeSignal) -> RiskDecision:
        """
        Evaluate a trade signal against all risk rules.
        Returns an approval or rejection with full reasoning.
        """
        reasons = []
        failed_rules = []
        approved = True

        # 1. Circuit breaker check
        potential_loss = (
            self._account_balance
            * signal.suggested_size_pct / 100.0
        )
        breaker_ok, breaker_msg = self._breaker.check_pre_trade(
            potential_loss
        )
        if not breaker_ok:
            approved = False
            reasons.append(f"Circuit breaker: {breaker_msg}")
            failed_rules.append("circuit_breaker")

        # 2. Run all validator rules
        for validator in ALL_VALIDATORS:
            result: ValidationResult = validator(
                signal, self._positions, self._account_balance
            )
            if not result.passed:
                approved = False
                reasons.append(f"{result.rule_name}: {result.message}")
                failed_rules.append(result.rule_name)

        # 3. Adjust position size if near limits
        adjusted_size = signal.suggested_size_pct
        remaining_budget = self._breaker.get_remaining_risk_budget()

        if approved and remaining_budget > 0:
            max_size_from_budget = (
                remaining_budget / self._account_balance * 100.0
            ) if self._account_balance > 0 else 0
            if adjusted_size > max_size_from_budget:
                adjusted_size = round(max_size_from_budget * 0.8, 2)
                reasons.append(
                    f"Position size reduced from "
                    f"{signal.suggested_size_pct}% to {adjusted_size}% "
                    f"to stay within risk budget"
                )

        if approved:
            reasons.append(
                f"Approved: {signal.direction.upper()} {signal.ticker} "
                f"at {adjusted_size}% allocation "
                f"(confidence {signal.confidence:.2f})"
            )

        decision = RiskDecision(
            approved=approved,
            signal=signal,
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            reasons=reasons,
            failed_rules=failed_rules,
            adjusted_size_pct=adjusted_size if approved else 0.0,
            remaining_risk_budget=round(remaining_budget, 2),
        )

        self._daily_trades.append(decision)

        if approved:
            log.info(
                f"APPROVED: {signal.direction} {signal.ticker} "
                f"({adjusted_size}%)",
                extra={"risk_data": {
                    "ticker": signal.ticker,
                    "direction": signal.direction,
                    "size_pct": adjusted_size,
                    "confidence": signal.confidence,
                }},
            )
        else:
            log.info(
                f"REJECTED: {signal.direction} {signal.ticker} — "
                f"{', '.join(failed_rules)}",
                extra={"risk_data": {
                    "ticker": signal.ticker,
                    "failed_rules": failed_rules,
                    "reasons": reasons,
                }},
            )

        return decision

    def record_trade_result(self, pnl: float):
        """Pass trade result to circuit breaker."""
        self._breaker.record_trade_result(pnl)

    def get_daily_stats(self) -> dict:
        """Summary of today's risk decisions."""
        total = len(self._daily_trades)
        approved = sum(1 for d in self._daily_trades if d.approved)
        rejected = total - approved

        rejection_reasons: dict[str, int] = {}
        for d in self._daily_trades:
            for rule in d.failed_rules:
                rejection_reasons[rule] = rejection_reasons.get(rule, 0) + 1

        return {
            "total_evaluated": total,
            "approved": approved,
            "rejected": rejected,
            "approval_rate": round(approved / total * 100, 1) if total else 0,
            "rejection_reasons": rejection_reasons,
            "circuit_breaker_tripped": self._breaker.is_tripped,
            "remaining_risk_budget": round(
                self._breaker.get_remaining_risk_budget(), 2
            ),
        }

    def reset_daily(self, new_balance: Optional[float] = None):
        """Reset for a new trading day."""
        self._daily_trades.clear()
        self._breaker.reset(new_balance)
        log.info("Risk manager reset for new day")
