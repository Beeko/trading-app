"""
Tests for the signal confluence engine.
Verifies that signals only fire when multiple indicators agree.
"""

import pytest
from src.analysis.indicators import (
    IndicatorSnapshot, IndicatorSignal, Direction,
)
from src.analysis.sentiment import SentimentResult
from src.strategy.goal_engine import GoalEngine, TradingMode
from src.strategy.signals import SignalEngine


def make_indicator(
    name: str, direction: Direction, strength: float = 0.5
) -> IndicatorSignal:
    return IndicatorSignal(
        name=name,
        direction=direction,
        strength=strength,
        value=0.0,
        detail={},
    )


def make_snapshot(
    ticker: str = "TEST",
    macd_dir: Direction = Direction.NEUTRAL,
    rsi_dir: Direction = Direction.NEUTRAL,
    bb_dir: Direction = Direction.NEUTRAL,
    vwap_dir: Direction = Direction.NEUTRAL,
    sma_dir: Direction = Direction.NEUTRAL,
    strength: float = 0.5,
) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        ticker=ticker,
        macd=make_indicator("macd", macd_dir, strength),
        rsi=make_indicator("rsi", rsi_dir, strength),
        bollinger=make_indicator("bollinger", bb_dir, strength),
        vwap=make_indicator("vwap", vwap_dir, strength),
        sma_cross=make_indicator("sma_cross", sma_dir, strength),
    )


class TestConfluence:
    def setup_method(self):
        self.goal = GoalEngine(daily_target=200)
        self.goal.start_day(25000)
        self.engine = SignalEngine(
            self.goal, min_confluence=2, min_confidence=0.2
        )

    def test_no_signal_when_all_neutral(self):
        """All neutral indicators should produce no signal."""
        snapshot = make_snapshot()
        signal = self.engine.evaluate("TEST", snapshot)
        assert signal is None

    def test_no_signal_with_single_bullish(self):
        """One bullish indicator alone shouldn't trigger a signal."""
        snapshot = make_snapshot(macd_dir=Direction.BULLISH)
        signal = self.engine.evaluate("TEST", snapshot)
        assert signal is None

    def test_buy_signal_with_two_bullish(self):
        """Two bullish indicators should produce a buy signal."""
        snapshot = make_snapshot(
            macd_dir=Direction.BULLISH,
            rsi_dir=Direction.BULLISH,
        )
        signal = self.engine.evaluate("TEST", snapshot)
        assert signal is not None
        assert signal.direction == "buy"
        assert signal.indicators_agreeing >= 2

    def test_sell_signal_with_two_bearish(self):
        """Two bearish indicators should produce a sell signal."""
        snapshot = make_snapshot(
            macd_dir=Direction.BEARISH,
            vwap_dir=Direction.BEARISH,
        )
        signal = self.engine.evaluate("TEST", snapshot)
        assert signal is not None
        assert signal.direction == "sell"

    def test_no_signal_with_mixed_indicators(self):
        """Equal bullish and bearish shouldn't trigger a signal."""
        snapshot = make_snapshot(
            macd_dir=Direction.BULLISH,
            rsi_dir=Direction.BEARISH,
        )
        signal = self.engine.evaluate("TEST", snapshot)
        assert signal is None

    def test_stronger_signal_with_more_agreement(self):
        """More agreeing indicators should produce higher confidence."""
        snap_2 = make_snapshot(
            macd_dir=Direction.BULLISH,
            rsi_dir=Direction.BULLISH,
            strength=0.6,
        )
        snap_4 = make_snapshot(
            macd_dir=Direction.BULLISH,
            rsi_dir=Direction.BULLISH,
            bb_dir=Direction.BULLISH,
            vwap_dir=Direction.BULLISH,
            strength=0.6,
        )
        signal_2 = self.engine.evaluate("TEST2", snap_2)
        signal_4 = self.engine.evaluate("TEST4", snap_4)
        assert signal_2 is not None
        assert signal_4 is not None
        assert signal_4.confidence >= signal_2.confidence

    def test_sentiment_boosts_confidence(self):
        """Positive sentiment should increase buy signal confidence."""
        snapshot = make_snapshot(
            macd_dir=Direction.BULLISH,
            rsi_dir=Direction.BULLISH,
            strength=0.5,
        )
        no_sent = self.engine.evaluate("TEST", snapshot)

        sentiment = SentimentResult(
            score=0.8,
            confidence=0.7,
            label="bullish",
            tickers=["TEST"],
            source="news",
        )
        with_sent = self.engine.evaluate("TEST", snapshot, sentiment)

        assert no_sent is not None
        assert with_sent is not None
        assert with_sent.confidence >= no_sent.confidence

    def test_halted_mode_blocks_all_signals(self):
        """No signals should generate when trading is halted."""
        self.goal.halt_trading("test halt")
        snapshot = make_snapshot(
            macd_dir=Direction.BULLISH,
            rsi_dir=Direction.BULLISH,
            bb_dir=Direction.BULLISH,
            vwap_dir=Direction.BULLISH,
            sma_dir=Direction.BULLISH,
            strength=0.9,
        )
        signal = self.engine.evaluate("TEST", snapshot)
        assert signal is None

    def test_protect_gains_requires_high_confidence(self):
        """In protect_gains mode, only high-confidence signals pass."""
        # Simulate reaching the goal
        self.goal.update_pnl(
            realized_pnl=250, unrealized_pnl=0, current_balance=25250
        )
        assert self.goal.get_mode() == TradingMode.PROTECT_GAINS

        # Low-strength signal should be blocked
        weak_snapshot = make_snapshot(
            macd_dir=Direction.BULLISH,
            rsi_dir=Direction.BULLISH,
            strength=0.3,
        )
        signal = self.engine.evaluate("TEST", weak_snapshot)
        assert signal is None

    def test_signal_has_reasoning(self):
        """Generated signals should include human-readable reasoning."""
        snapshot = make_snapshot(
            macd_dir=Direction.BULLISH,
            rsi_dir=Direction.BULLISH,
            strength=0.6,
        )
        signal = self.engine.evaluate("TEST", snapshot)
        assert signal is not None
        assert len(signal.reasoning) >= 2
        assert any("MACD" in r for r in signal.reasoning)
        assert any("RSI" in r for r in signal.reasoning)

    def test_position_size_scales_with_confidence(self):
        """Higher confidence should suggest larger position sizes."""
        weak = make_snapshot(
            macd_dir=Direction.BULLISH,
            rsi_dir=Direction.BULLISH,
            strength=0.3,
        )
        strong = make_snapshot(
            macd_dir=Direction.BULLISH,
            rsi_dir=Direction.BULLISH,
            bb_dir=Direction.BULLISH,
            vwap_dir=Direction.BULLISH,
            strength=0.8,
        )
        sig_weak = self.engine.evaluate("W", weak)
        sig_strong = self.engine.evaluate("S", strong)
        if sig_weak and sig_strong:
            assert sig_strong.suggested_size_pct >= sig_weak.suggested_size_pct


class TestGoalModes:
    def test_starts_conservative(self):
        goal = GoalEngine(daily_target=200)
        goal.start_day(25000)
        assert goal.get_mode() == TradingMode.CONSERVATIVE

    def test_progresses_to_normal(self):
        goal = GoalEngine(daily_target=200)
        goal.start_day(25000)
        goal.update_pnl(120, 0, 25120)  # 60% progress
        assert goal.get_mode() == TradingMode.NORMAL

    def test_protect_gains_when_goal_met(self):
        goal = GoalEngine(daily_target=200)
        goal.start_day(25000)
        goal.update_pnl(210, 0, 25210)
        assert goal.get_mode() == TradingMode.PROTECT_GAINS

    def test_reduced_when_losing(self):
        goal = GoalEngine(daily_target=200)
        goal.start_day(25000)
        goal.update_pnl(-100, 0, 24900)
        assert goal.get_mode() == TradingMode.REDUCED

    def test_halts_on_circuit_breaker(self):
        goal = GoalEngine(daily_target=200)
        goal.start_day(25000)
        goal.halt_trading("test")
        assert goal.get_mode() == TradingMode.HALTED
        assert goal.get_position_size_multiplier() == 0.0

    def test_reduced_mode_shrinks_positions(self):
        goal = GoalEngine(daily_target=200)
        goal.start_day(25000)
        goal.update_pnl(-100, 0, 24900)
        assert goal.get_position_size_multiplier() < 1.0
