"""
Technical indicator engine — computes MACD, RSI, Bollinger Bands,
VWAP, and moving averages from price data, then emits standardized
signal objects for the confluence engine.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import numpy as np
from src.data.market_data import PriceBar
from src.utils.logger import get_logger

log = get_logger("indicators")


class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class IndicatorSignal:
    """Standardized output from any indicator."""
    name: str
    direction: Direction
    strength: float       # 0.0 – 1.0
    value: float          # raw indicator value
    detail: dict          # indicator-specific data


@dataclass
class IndicatorSnapshot:
    """Complete indicator state for a ticker at a point in time."""
    ticker: str
    macd: Optional[IndicatorSignal] = None
    rsi: Optional[IndicatorSignal] = None
    bollinger: Optional[IndicatorSignal] = None
    vwap: Optional[IndicatorSignal] = None
    sma_cross: Optional[IndicatorSignal] = None


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Compute exponential moving average."""
    k = 2.0 / (period + 1)
    result = np.zeros_like(data, dtype=float)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = data[i] * k + result[i - 1] * (1 - k)
    return result


def _sma(data: np.ndarray, period: int) -> np.ndarray:
    """Compute simple moving average."""
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        result[i] = np.mean(data[i - period + 1 : i + 1])
    return result


class IndicatorEngine:
    """Computes all technical indicators from price bars."""

    def __init__(
        self,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        rsi_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
        sma_short: int = 50,
        sma_long: int = 200,
    ):
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.rsi_period = rsi_period
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.sma_short = sma_short
        self.sma_long = sma_long
        log.info("IndicatorEngine initialized")

    def compute_all(
        self, ticker: str, bars: list[PriceBar]
    ) -> IndicatorSnapshot:
        """Compute all indicators and return a complete snapshot."""
        if len(bars) < self.sma_long:
            log.warning(
                f"{ticker}: only {len(bars)} bars, need {self.sma_long}+"
            )

        closes = np.array([b.close for b in bars], dtype=float)
        highs = np.array([b.high for b in bars], dtype=float)
        lows = np.array([b.low for b in bars], dtype=float)
        volumes = np.array([b.volume for b in bars], dtype=float)

        snapshot = IndicatorSnapshot(ticker=ticker)

        if len(closes) >= self.macd_slow + self.macd_signal:
            snapshot.macd = self.compute_macd(closes)

        if len(closes) >= self.rsi_period + 1:
            snapshot.rsi = self.compute_rsi(closes)

        if len(closes) >= self.bb_period:
            snapshot.bollinger = self.compute_bollinger(closes)

        if len(closes) >= 1 and len(volumes) >= 1:
            snapshot.vwap = self.compute_vwap(closes, highs, lows, volumes)

        if len(closes) >= self.sma_long:
            snapshot.sma_cross = self.compute_sma_cross(closes)

        return snapshot

    def compute_macd(self, closes: np.ndarray) -> IndicatorSignal:
        """MACD with signal line crossover detection."""
        ema_fast = _ema(closes, self.macd_fast)
        ema_slow = _ema(closes, self.macd_slow)
        macd_line = ema_fast - ema_slow
        signal_line = _ema(macd_line, self.macd_signal)
        histogram = macd_line - signal_line

        # Current values
        macd_val = macd_line[-1]
        sig_val = signal_line[-1]
        hist_val = histogram[-1]

        # Crossover detection
        prev_diff = macd_line[-2] - signal_line[-2]
        curr_diff = macd_line[-1] - signal_line[-1]
        cross_up = prev_diff <= 0 and curr_diff > 0
        cross_down = prev_diff >= 0 and curr_diff < 0

        if cross_up:
            direction = Direction.BULLISH
            strength = min(abs(curr_diff) / (abs(closes[-1]) * 0.01 + 1e-9), 1.0)
        elif cross_down:
            direction = Direction.BEARISH
            strength = min(abs(curr_diff) / (abs(closes[-1]) * 0.01 + 1e-9), 1.0)
        else:
            # No crossover — trend continuation
            direction = Direction.BULLISH if curr_diff > 0 else Direction.BEARISH
            strength = min(abs(curr_diff) / (abs(closes[-1]) * 0.02 + 1e-9), 0.5)

        return IndicatorSignal(
            name="macd",
            direction=direction,
            strength=round(strength, 3),
            value=round(macd_val, 4),
            detail={
                "macd_line": round(macd_val, 4),
                "signal_line": round(sig_val, 4),
                "histogram": round(hist_val, 4),
                "crossover": "up" if cross_up else ("down" if cross_down else "none"),
            },
        )

    def compute_rsi(self, closes: np.ndarray) -> IndicatorSignal:
        """RSI with overbought/oversold zones."""
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.mean(gains[: self.rsi_period])
        avg_loss = np.mean(losses[: self.rsi_period])

        for i in range(self.rsi_period, len(deltas)):
            avg_gain = (avg_gain * (self.rsi_period - 1) + gains[i]) / self.rsi_period
            avg_loss = (avg_loss * (self.rsi_period - 1) + losses[i]) / self.rsi_period

        if avg_loss == 0:
            rsi_val = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_val = 100.0 - (100.0 / (1.0 + rs))

        if rsi_val >= 70:
            direction = Direction.BEARISH  # overbought
            strength = min((rsi_val - 70) / 30.0, 1.0)
        elif rsi_val <= 30:
            direction = Direction.BULLISH  # oversold
            strength = min((30 - rsi_val) / 30.0, 1.0)
        else:
            direction = Direction.NEUTRAL
            strength = 0.0

        return IndicatorSignal(
            name="rsi",
            direction=direction,
            strength=round(strength, 3),
            value=round(rsi_val, 2),
            detail={
                "rsi": round(rsi_val, 2),
                "zone": (
                    "overbought" if rsi_val >= 70
                    else "oversold" if rsi_val <= 30
                    else "neutral"
                ),
            },
        )

    def compute_bollinger(self, closes: np.ndarray) -> IndicatorSignal:
        """Bollinger Bands — detect price relative to bands."""
        sma = _sma(closes, self.bb_period)
        std = np.full_like(closes, np.nan, dtype=float)
        for i in range(self.bb_period - 1, len(closes)):
            std[i] = np.std(closes[i - self.bb_period + 1 : i + 1])

        upper = sma[-1] + self.bb_std * std[-1]
        lower = sma[-1] - self.bb_std * std[-1]
        price = closes[-1]
        bandwidth = (upper - lower) / sma[-1] if sma[-1] else 0

        # Position within bands: 0 = lower, 1 = upper
        band_position = (
            (price - lower) / (upper - lower)
            if upper != lower else 0.5
        )

        if band_position >= 0.95:
            direction = Direction.BEARISH
            strength = min((band_position - 0.95) / 0.05 * 0.8, 1.0)
        elif band_position <= 0.05:
            direction = Direction.BULLISH
            strength = min((0.05 - band_position) / 0.05 * 0.8, 1.0)
        else:
            direction = Direction.NEUTRAL
            strength = 0.0

        return IndicatorSignal(
            name="bollinger",
            direction=direction,
            strength=round(strength, 3),
            value=round(band_position, 3),
            detail={
                "upper": round(upper, 2),
                "middle": round(sma[-1], 2),
                "lower": round(lower, 2),
                "band_position": round(band_position, 3),
                "bandwidth": round(bandwidth, 4),
            },
        )

    def compute_vwap(
        self,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        volumes: np.ndarray,
    ) -> IndicatorSignal:
        """VWAP — volume-weighted average price as intraday benchmark."""
        typical_price = (highs + lows + closes) / 3.0
        cumulative_tp_vol = np.cumsum(typical_price * volumes)
        cumulative_vol = np.cumsum(volumes)
        vwap = cumulative_tp_vol / np.where(cumulative_vol > 0, cumulative_vol, 1)

        price = closes[-1]
        vwap_val = vwap[-1]
        deviation_pct = (price - vwap_val) / vwap_val * 100 if vwap_val else 0

        if price > vwap_val:
            direction = Direction.BULLISH
        elif price < vwap_val:
            direction = Direction.BEARISH
        else:
            direction = Direction.NEUTRAL

        strength = min(abs(deviation_pct) / 2.0, 1.0)

        return IndicatorSignal(
            name="vwap",
            direction=direction,
            strength=round(strength, 3),
            value=round(vwap_val, 2),
            detail={
                "vwap": round(vwap_val, 2),
                "price": round(price, 2),
                "deviation_pct": round(deviation_pct, 2),
            },
        )

    def compute_sma_cross(self, closes: np.ndarray) -> IndicatorSignal:
        """SMA 50/200 golden/death cross detection."""
        sma_short = _sma(closes, self.sma_short)
        sma_long = _sma(closes, self.sma_long)

        curr_short = sma_short[-1]
        curr_long = sma_long[-1]
        prev_short = sma_short[-2]
        prev_long = sma_long[-2]

        golden_cross = prev_short <= prev_long and curr_short > curr_long
        death_cross = prev_short >= prev_long and curr_short < curr_long

        if golden_cross:
            direction = Direction.BULLISH
            strength = 0.9
        elif death_cross:
            direction = Direction.BEARISH
            strength = 0.9
        else:
            direction = (
                Direction.BULLISH if curr_short > curr_long
                else Direction.BEARISH
            )
            strength = min(
                abs(curr_short - curr_long) / (closes[-1] * 0.05 + 1e-9),
                0.4,
            )

        return IndicatorSignal(
            name="sma_cross",
            direction=direction,
            strength=round(strength, 3),
            value=round(curr_short - curr_long, 2),
            detail={
                "sma_short": round(curr_short, 2),
                "sma_long": round(curr_long, 2),
                "crossover": (
                    "golden" if golden_cross
                    else "death" if death_cross
                    else "none"
                ),
            },
        )
