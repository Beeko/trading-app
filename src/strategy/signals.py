"""
Signal confluence engine — combines outputs from technical indicators
and sentiment analysis to generate actionable trade signals.
Only fires when multiple sources agree.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from src.analysis.indicators import IndicatorSnapshot, Direction
from src.analysis.sentiment import SentimentResult
from src.strategy.goal_engine import GoalEngine, TradingMode
from src.utils.logger import get_logger

log = get_logger("signals")


@dataclass
class TradeSignal:
    """A trade the system is recommending."""
    timestamp: datetime
    ticker: str
    direction: str           # "buy" | "sell"
    asset_type: str          # "stock" | "option"
    confidence: float        # 0.0 – 1.0
    suggested_size_pct: float  # % of account to allocate
    indicators_agreeing: int
    indicators_total: int
    sentiment_score: Optional[float]
    reasoning: list[str]     # human-readable explanations
    indicator_data: dict     # raw indicator snapshot
    requires_approval: bool  # True = needs user confirmation


class SignalEngine:
    """Generates trade signals from indicator + sentiment confluence."""

    def __init__(
        self,
        goal_engine: GoalEngine,
        min_confluence: int = 2,
        min_confidence: float = 0.3,
        base_position_pct: float = 5.0,
    ):
        self._goal = goal_engine
        self._min_confluence = min_confluence
        self._min_confidence = min_confidence
        self._base_position_pct = base_position_pct
        log.info(
            f"SignalEngine initialized "
            f"(min_confluence={min_confluence}, "
            f"min_confidence={min_confidence})"
        )

    def evaluate(
        self,
        ticker: str,
        snapshot: IndicatorSnapshot,
        sentiment: Optional[SentimentResult] = None,
    ) -> Optional[TradeSignal]:
        """
        Evaluate a ticker's indicators and sentiment for a trade signal.
        Returns None if no actionable signal is found.
        """
        mode = self._goal.get_mode()

        # Don't generate signals if trading is halted
        if mode == TradingMode.HALTED:
            return None

        # Collect all indicator signals
        indicators = {
            "macd": snapshot.macd,
            "rsi": snapshot.rsi,
            "bollinger": snapshot.bollinger,
            "vwap": snapshot.vwap,
            "sma_cross": snapshot.sma_cross,
        }
        active = {k: v for k, v in indicators.items() if v is not None}

        if not active:
            return None

        # Count bullish vs bearish signals
        bullish = []
        bearish = []
        reasoning = []

        for name, sig in active.items():
            if sig.direction == Direction.BULLISH and sig.strength > 0.1:
                bullish.append(sig)
                reasoning.append(
                    f"{name.upper()}: bullish "
                    f"(strength {sig.strength:.2f}, "
                    f"value {sig.value})"
                )
            elif sig.direction == Direction.BEARISH and sig.strength > 0.1:
                bearish.append(sig)
                reasoning.append(
                    f"{name.upper()}: bearish "
                    f"(strength {sig.strength:.2f}, "
                    f"value {sig.value})"
                )

        # Add sentiment if available
        sentiment_boost = 0.0
        if sentiment and sentiment.confidence > 0.2:
            if sentiment.label == "bullish":
                sentiment_boost = sentiment.score * sentiment.confidence * 0.3
                reasoning.append(
                    f"Sentiment: bullish "
                    f"(score {sentiment.score:.2f}, "
                    f"confidence {sentiment.confidence:.2f})"
                )
            elif sentiment.label == "bearish":
                sentiment_boost = sentiment.score * sentiment.confidence * 0.3
                reasoning.append(
                    f"Sentiment: bearish "
                    f"(score {sentiment.score:.2f}, "
                    f"confidence {sentiment.confidence:.2f})"
                )

        # Determine direction by majority
        if len(bullish) > len(bearish) and len(bullish) >= self._min_confluence:
            direction = "buy"
            agreeing = len(bullish)
            avg_strength = sum(s.strength for s in bullish) / len(bullish)
        elif len(bearish) > len(bullish) and len(bearish) >= self._min_confluence:
            direction = "sell"
            agreeing = len(bearish)
            avg_strength = sum(s.strength for s in bearish) / len(bearish)
        else:
            # No confluence — no signal
            return None

        # Calculate confidence
        confluence_ratio = agreeing / len(active) if active else 0
        confidence = (
            avg_strength * 0.5
            + confluence_ratio * 0.3
            + abs(sentiment_boost) * 0.2
        )
        confidence = round(min(confidence, 1.0), 3)

        if confidence < self._min_confidence:
            return None

        # Adjust position size based on confidence and goal mode
        mode_multiplier = self._goal.get_position_size_multiplier()
        suggested_size = (
            self._base_position_pct
            * confidence
            * mode_multiplier
        )
        suggested_size = round(min(suggested_size, 10.0), 2)

        # In protect_gains mode, only take very high confidence signals
        if mode == TradingMode.PROTECT_GAINS and confidence < 0.6:
            return None

        # In reduced mode, raise the bar
        if mode == TradingMode.REDUCED and confidence < 0.5:
            return None

        # Build indicator data dict for logging
        indicator_data = {}
        for name, sig in active.items():
            indicator_data[name] = {
                "direction": sig.direction.value,
                "strength": sig.strength,
                "value": sig.value,
                "detail": sig.detail,
            }

        signal = TradeSignal(
            timestamp=datetime.utcnow(),
            ticker=ticker,
            direction=direction,
            asset_type="stock",
            confidence=confidence,
            suggested_size_pct=suggested_size,
            indicators_agreeing=agreeing,
            indicators_total=len(active),
            sentiment_score=(
                sentiment.score if sentiment else None
            ),
            reasoning=reasoning,
            indicator_data=indicator_data,
            requires_approval=True,  # always require approval initially
        )

        log.info(
            f"Signal: {direction.upper()} {ticker} "
            f"(confidence={confidence}, "
            f"{agreeing}/{len(active)} indicators, "
            f"size={suggested_size}%)",
            extra={"signal_data": indicator_data},
        )

        return signal
