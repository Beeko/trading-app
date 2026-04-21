"""
Tests for the technical indicator engine.
Validates MACD, RSI, Bollinger outputs against known patterns.
"""

import pytest
import numpy as np
from src.analysis.indicators import IndicatorEngine, Direction
from src.data.market_data import PriceBar
from datetime import datetime, timedelta


def make_bars(prices: list[float]) -> list[PriceBar]:
    """Helper: create PriceBar list from a price series."""
    base = datetime(2025, 1, 1)
    return [
        PriceBar(
            timestamp=base + timedelta(days=i),
            open=p * 0.999,
            high=p * 1.005,
            low=p * 0.995,
            close=p,
            volume=1_000_000,
        )
        for i, p in enumerate(prices)
    ]


class TestMACD:
    def setup_method(self):
        self.engine = IndicatorEngine()

    def test_uptrend_produces_bullish(self):
        """Steady uptrend should produce bullish MACD."""
        prices = [100 + i * 0.5 for i in range(50)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        assert snapshot.macd is not None
        assert snapshot.macd.direction == Direction.BULLISH

    def test_downtrend_produces_bearish(self):
        """Steady downtrend should produce bearish MACD."""
        prices = [150 - i * 0.5 for i in range(50)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        assert snapshot.macd is not None
        assert snapshot.macd.direction == Direction.BEARISH

    def test_macd_detail_has_required_fields(self):
        """MACD detail should contain line, signal, histogram, crossover."""
        prices = [100 + i * 0.3 for i in range(50)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        detail = snapshot.macd.detail
        assert "macd_line" in detail
        assert "signal_line" in detail
        assert "histogram" in detail
        assert "crossover" in detail


class TestRSI:
    def setup_method(self):
        self.engine = IndicatorEngine()

    def test_strong_uptrend_overbought(self):
        """Strong uptrend should push RSI toward overbought."""
        prices = [100 + i * 2 for i in range(30)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        assert snapshot.rsi is not None
        assert snapshot.rsi.value > 60  # should be high

    def test_strong_downtrend_oversold(self):
        """Strong downtrend should push RSI toward oversold."""
        prices = [200 - i * 2 for i in range(30)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        assert snapshot.rsi is not None
        assert snapshot.rsi.value < 40  # should be low

    def test_rsi_bounded(self):
        """RSI should always be between 0 and 100."""
        prices = [100 + np.sin(i / 3) * 10 for i in range(50)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        assert 0 <= snapshot.rsi.value <= 100

    def test_rsi_zone_labels(self):
        """RSI zones should be labeled correctly."""
        # High RSI
        prices = [100 + i * 3 for i in range(30)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        if snapshot.rsi.value >= 70:
            assert snapshot.rsi.detail["zone"] == "overbought"
        elif snapshot.rsi.value <= 30:
            assert snapshot.rsi.detail["zone"] == "oversold"
        else:
            assert snapshot.rsi.detail["zone"] == "neutral"


class TestBollinger:
    def setup_method(self):
        self.engine = IndicatorEngine()

    def test_band_position_bounded(self):
        """Band position should be between 0 and ~1."""
        prices = [100 + np.sin(i / 5) * 5 for i in range(30)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        assert snapshot.bollinger is not None
        assert -0.5 <= snapshot.bollinger.value <= 1.5

    def test_bollinger_detail_has_bands(self):
        """Detail should contain upper, middle, lower bands."""
        prices = [100 + i * 0.1 for i in range(30)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        detail = snapshot.bollinger.detail
        assert "upper" in detail
        assert "middle" in detail
        assert "lower" in detail
        assert detail["upper"] > detail["middle"] > detail["lower"]


class TestIndicatorSnapshot:
    def setup_method(self):
        self.engine = IndicatorEngine()

    def test_full_snapshot_with_enough_data(self):
        """With 200+ bars, all indicators should compute."""
        prices = [100 + np.sin(i / 10) * 20 + i * 0.05 for i in range(250)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        assert snapshot.macd is not None
        assert snapshot.rsi is not None
        assert snapshot.bollinger is not None
        assert snapshot.vwap is not None
        assert snapshot.sma_cross is not None

    def test_insufficient_data_partial_snapshot(self):
        """With few bars, some indicators should be None."""
        prices = [100 + i for i in range(10)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        # SMA cross needs 200 bars, so should be None
        assert snapshot.sma_cross is None

    def test_strength_always_bounded(self):
        """All indicator strengths should be between 0 and 1."""
        prices = [100 + np.sin(i / 8) * 15 + i * 0.1 for i in range(250)]
        bars = make_bars(prices)
        snapshot = self.engine.compute_all("TEST", bars)
        for sig in [snapshot.macd, snapshot.rsi, snapshot.bollinger,
                    snapshot.vwap, snapshot.sma_cross]:
            if sig:
                assert 0 <= sig.strength <= 1.0, (
                    f"{sig.name} strength {sig.strength} out of bounds"
                )
