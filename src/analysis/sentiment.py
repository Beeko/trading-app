"""
Sentiment analysis engine — scores news articles and social posts
as bullish, bearish, or neutral using keyword analysis or LLM API.
"""

from dataclasses import dataclass
from typing import Optional
import re
from src.data.news_feed import NewsArticle
from src.data.social_feed import SocialPost
from src.utils.logger import get_logger

log = get_logger("sentiment")


@dataclass
class SentimentResult:
    """Sentiment score for a piece of text."""
    score: float          # -1.0 (very bearish) to 1.0 (very bullish)
    confidence: float     # 0.0 to 1.0
    label: str            # "bullish" | "bearish" | "neutral"
    tickers: list[str]
    source: str


# Keyword dictionaries for local sentiment scoring
BULLISH_WORDS = {
    "upgrade": 0.7, "beat": 0.6, "beats": 0.6, "surpass": 0.6,
    "growth": 0.4, "profit": 0.4, "revenue": 0.3, "record": 0.5,
    "bullish": 0.8, "rally": 0.6, "surge": 0.7, "soar": 0.7,
    "breakout": 0.6, "momentum": 0.4, "outperform": 0.6,
    "buy": 0.5, "long": 0.4, "moon": 0.3, "calls": 0.3,
    "innovation": 0.3, "partnership": 0.4, "acquisition": 0.3,
    "dividend": 0.3, "approved": 0.5, "exceeded": 0.5,
    "strong": 0.3, "positive": 0.4, "optimistic": 0.5,
    "recovery": 0.4, "expansion": 0.4, "milestone": 0.4,
}

BEARISH_WORDS = {
    "downgrade": -0.7, "miss": -0.6, "misses": -0.6, "decline": -0.5,
    "loss": -0.5, "losses": -0.5, "bearish": -0.8, "crash": -0.8,
    "plunge": -0.7, "drop": -0.5, "sell": -0.5, "short": -0.4,
    "puts": -0.3, "overvalued": -0.5, "underperform": -0.6,
    "warning": -0.4, "risk": -0.3, "lawsuit": -0.5, "fraud": -0.8,
    "investigation": -0.5, "bankruptcy": -0.9, "layoffs": -0.5,
    "recall": -0.5, "weak": -0.4, "negative": -0.4, "concern": -0.3,
    "debt": -0.3, "default": -0.7, "inflation": -0.3,
    "recession": -0.6, "sanctions": -0.5, "penalty": -0.5,
}


class SentimentEngine:
    """Scores text sentiment for trading signals."""

    def __init__(self, model: str = "local"):
        self._model = model
        log.info(f"SentimentEngine initialized (model={model})")

    def score_text(self, text: str, tickers: list[str]) -> SentimentResult:
        """Score a piece of text for financial sentiment."""
        if self._model == "local":
            return self._score_local(text, tickers)
        else:
            # Placeholder for LLM API-based scoring
            return self._score_local(text, tickers)

    def score_article(self, article: NewsArticle) -> SentimentResult:
        """Score a news article."""
        text = f"{article.headline} {article.summary}"
        result = self.score_text(text, article.tickers)
        result.source = f"news:{article.source}"
        return result

    def score_post(self, post: SocialPost) -> SentimentResult:
        """Score a social media post."""
        result = self.score_text(post.text, post.tickers)
        result.source = f"social:{post.source}"
        # Weight by engagement (upvotes)
        engagement_boost = min(post.upvotes / 100.0, 0.3)
        result.confidence = min(result.confidence + engagement_boost, 1.0)
        return result

    def aggregate_sentiment(
        self,
        ticker: str,
        results: list[SentimentResult],
        staleness_hours: float = 4.0,
    ) -> Optional[SentimentResult]:
        """Aggregate multiple sentiment results for a ticker."""
        relevant = [
            r for r in results
            if ticker in r.tickers and r.confidence > 0.1
        ]
        if not relevant:
            return None

        # Weighted average by confidence
        total_weight = sum(r.confidence for r in relevant)
        if total_weight == 0:
            return None

        weighted_score = sum(
            r.score * r.confidence for r in relevant
        ) / total_weight

        avg_confidence = total_weight / len(relevant)

        if weighted_score > 0.15:
            label = "bullish"
        elif weighted_score < -0.15:
            label = "bearish"
        else:
            label = "neutral"

        return SentimentResult(
            score=round(weighted_score, 3),
            confidence=round(avg_confidence, 3),
            label=label,
            tickers=[ticker],
            source="aggregate",
        )

    def _score_local(
        self, text: str, tickers: list[str]
    ) -> SentimentResult:
        """Score using keyword matching (fast, no API needed)."""
        text_lower = text.lower()
        words = re.findall(r'\b[a-z]+\b', text_lower)

        bullish_score = 0.0
        bearish_score = 0.0
        matches = 0

        for word in words:
            if word in BULLISH_WORDS:
                bullish_score += BULLISH_WORDS[word]
                matches += 1
            elif word in BEARISH_WORDS:
                bearish_score += abs(BEARISH_WORDS[word])
                matches += 1

        total = bullish_score + bearish_score
        if total == 0:
            return SentimentResult(
                score=0.0,
                confidence=0.1,
                label="neutral",
                tickers=tickers,
                source="local",
            )

        # Net sentiment: positive = bullish, negative = bearish
        net_score = (bullish_score - bearish_score) / max(total, 1.0)
        confidence = min(matches / 5.0, 1.0) * 0.6  # cap at 0.6 for keyword-only

        if net_score > 0.15:
            label = "bullish"
        elif net_score < -0.15:
            label = "bearish"
        else:
            label = "neutral"

        return SentimentResult(
            score=round(net_score, 3),
            confidence=round(confidence, 3),
            label=label,
            tickers=tickers,
            source="local",
        )
