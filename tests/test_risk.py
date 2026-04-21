"""
Tests for the risk management layer.
Verifies circuit breaker, position limits, and validators.
"""

import pytest
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.validators import (
    validate_position_size, validate_max_open_positions,
    validate_duplicate_position, validate_min_confidence,
)
from src.strategy.signals import TradeSignal
from datetime import datetime


def make_signal(
    ticker="AAPL", direction="buy", confidence=0.6, size_pct=5.0
) -> TradeSignal:
    return TradeSignal(
        timestamp=datetime.utcnow(),
        ticker=ticker,
        direction=direction,
        asset_type="stock",
        confidence=confidence,
        suggested_size_pct=size_pct,
        indicators_agreeing=3,
        indicators_total=5,
        sentiment_score=0.5,
        reasoning=["test signal"],
        indicator_data={},
        requires_approval=True,
    )


class TestCircuitBreaker:
    def test_starts_not_tripped(self):
        cb = CircuitBreaker(account_balance=25000)
        assert not cb.is_tripped

    def test_trips_on_daily_loss(self):
        cb = CircuitBreaker(account_balance=25000, daily_loss_pct=2.0)
        # Loss limit = $500
        cb.record_trade_result(-200)
        assert not cb.is_tripped
        cb.record_trade_result(-200)
        assert not cb.is_tripped
        cb.record_trade_result(-150)  # cumulative = -$550 > $500
        assert cb.is_tripped
        assert "loss limit" in cb.state.reason.lower()

    def test_trips_on_consecutive_losses(self):
        cb = CircuitBreaker(
            account_balance=100000, max_consecutive_losses=3
        )
        cb.record_trade_result(-10)
        cb.record_trade_result(-10)
        assert not cb.is_tripped
        cb.record_trade_result(-10)
        assert cb.is_tripped
        assert "consecutive" in cb.state.reason.lower()

    def test_winning_trade_resets_consecutive(self):
        cb = CircuitBreaker(
            account_balance=100000, max_consecutive_losses=3
        )
        cb.record_trade_result(-10)
        cb.record_trade_result(-10)
        cb.record_trade_result(50)  # win resets counter
        cb.record_trade_result(-10)
        assert not cb.is_tripped

    def test_pre_trade_check_blocks_when_tripped(self):
        cb = CircuitBreaker(account_balance=10000, daily_loss_pct=1.0)
        cb.record_trade_result(-150)  # trips at $100 limit
        allowed, reason = cb.check_pre_trade(50)
        assert not allowed

    def test_pre_trade_check_blocks_oversized_trade(self):
        cb = CircuitBreaker(
            account_balance=10000, max_single_loss_pct=2.0
        )
        allowed, reason = cb.check_pre_trade(300)  # > $200 limit
        assert not allowed
        assert "single trade" in reason.lower()

    def test_reset_clears_state(self):
        cb = CircuitBreaker(account_balance=10000, daily_loss_pct=1.0)
        cb.record_trade_result(-150)
        assert cb.is_tripped
        cb.reset(new_balance=10000)
        assert not cb.is_tripped

    def test_remaining_budget(self):
        cb = CircuitBreaker(account_balance=25000, daily_loss_pct=2.0)
        assert cb.get_remaining_risk_budget() == 500.0
        cb.record_trade_result(-200)
        assert cb.get_remaining_risk_budget() == 300.0

    def test_callback_fires_on_trip(self):
        cb = CircuitBreaker(account_balance=10000, daily_loss_pct=1.0)
        tripped_states = []
        cb.on_trip(lambda state: tripped_states.append(state))
        cb.record_trade_result(-150)
        assert len(tripped_states) == 1
        assert tripped_states[0].is_tripped


class TestValidators:
    def test_min_confidence_rejects_low(self):
        signal = make_signal(confidence=0.1)
        result = validate_min_confidence(signal, {}, 25000)
        assert not result.passed

    def test_min_confidence_passes_high(self):
        signal = make_signal(confidence=0.6)
        result = validate_min_confidence(signal, {}, 25000)
        assert result.passed

    def test_duplicate_position_blocks(self):
        signal = make_signal(ticker="AAPL", direction="buy")
        positions = {"AAPL": {"qty": 10, "avg_price": 150}}
        result = validate_duplicate_position(signal, positions, 25000)
        assert not result.passed

    def test_duplicate_allows_sell(self):
        signal = make_signal(ticker="AAPL", direction="sell")
        positions = {"AAPL": {"qty": 10, "avg_price": 150}}
        result = validate_duplicate_position(signal, positions, 25000)
        assert result.passed

    def test_max_positions_blocks_when_full(self):
        signal = make_signal()
        positions = {f"TICK{i}": {} for i in range(10)}
        result = validate_max_open_positions(signal, positions, 25000)
        assert not result.passed
