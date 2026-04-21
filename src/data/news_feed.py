"""
News feed service — polls financial news APIs and normalizes
articles into a standard format for sentiment analysis.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import httpx
from config import settings
from src.utils.logger import get_logger

log = get_logger("news_feed")


@dataclass
class NewsArticle:
    """Normalized news article."""
    headline: str
    summary: str
    source: str
    url: str
    published_at: datetime
    tickers: list[str]
    category: str            # "earnings" | "merger" | "regulation" | "general"
    sentiment: Optional[float] = None  # filled by sentiment engine
    relevance: float = 0.0


class NewsFeedService:
    """Aggregates financial news from multiple sources."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15.0)
        self._article_cache: list[NewsArticle] = []
        self._seen_urls: set[str] = set()
        log.info("NewsFeedService initialized")

    async def fetch_finnhub_news(
        self,
        category: str = "general",
    ) -> list[NewsArticle]:
        """Fetch market news from Finnhub."""
        if not settings.FINNHUB_API_KEY:
            return []
        try:
            resp = await self._client.get(
                "https://finnhub.io/api/v1/news",
                params={
                    "category": category,
                    "token": settings.FINNHUB_API_KEY,
                },
            )
            resp.raise_for_status()
            articles = []
            for item in resp.json():
                url = item.get("url", "")
                if url in self._seen_urls:
                    continue
                self._seen_urls.add(url)
                articles.append(NewsArticle(
                    headline=item.get("headline", ""),
                    summary=item.get("summary", ""),
                    source=item.get("source", "finnhub"),
                    url=url,
                    published_at=datetime.fromtimestamp(
                        item.get("datetime", 0)
                    ),
                    tickers=self._extract_tickers(
                        item.get("related", "")
                    ),
                    category=item.get("category", "general"),
                ))
            log.info(f"Fetched {len(articles)} new articles from Finnhub")
            return articles
        except Exception as e:
            log.error(f"Finnhub news fetch failed: {e}")
            return []

    async def fetch_ticker_news(self, ticker: str) -> list[NewsArticle]:
        """Fetch news specifically about a ticker from Finnhub."""
        if not settings.FINNHUB_API_KEY:
            return []
        try:
            today = datetime.utcnow()
            week_ago = today - timedelta(days=7)
            resp = await self._client.get(
                "https://finnhub.io/api/v1/company-news",
                params={
                    "symbol": ticker,
                    "from": week_ago.strftime("%Y-%m-%d"),
                    "to": today.strftime("%Y-%m-%d"),
                    "token": settings.FINNHUB_API_KEY,
                },
            )
            resp.raise_for_status()
            articles = []
            for item in resp.json()[:20]:  # cap at 20
                url = item.get("url", "")
                if url in self._seen_urls:
                    continue
                self._seen_urls.add(url)
                articles.append(NewsArticle(
                    headline=item.get("headline", ""),
                    summary=item.get("summary", ""),
                    source=item.get("source", "finnhub"),
                    url=url,
                    published_at=datetime.fromtimestamp(
                        item.get("datetime", 0)
                    ),
                    tickers=[ticker],
                    category="company",
                ))
            log.info(f"Fetched {len(articles)} articles for {ticker}")
            return articles
        except Exception as e:
            log.error(f"Ticker news fetch for {ticker} failed: {e}")
            return []

    async def poll_all_sources(self) -> list[NewsArticle]:
        """Poll all configured news sources and return new articles."""
        new_articles = []

        # General market news
        articles = await self.fetch_finnhub_news()
        new_articles.extend(articles)

        # Prune old seen URLs (keep last 24h worth)
        self._prune_cache()

        # Add to cache
        self._article_cache.extend(new_articles)
        return new_articles

    def get_recent_articles(
        self,
        ticker: Optional[str] = None,
        hours: int = 4,
    ) -> list[NewsArticle]:
        """Get cached articles, optionally filtered by ticker."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        articles = [
            a for a in self._article_cache
            if a.published_at >= cutoff
        ]
        if ticker:
            articles = [
                a for a in articles if ticker in a.tickers
            ]
        return sorted(articles, key=lambda a: a.published_at, reverse=True)

    def _prune_cache(self):
        """Remove articles older than 24 hours from cache."""
        cutoff = datetime.utcnow() - timedelta(hours=24)
        self._article_cache = [
            a for a in self._article_cache
            if a.published_at >= cutoff
        ]

    @staticmethod
    def _extract_tickers(related: str) -> list[str]:
        """Parse ticker symbols from a comma-separated string."""
        if not related:
            return []
        return [t.strip().upper() for t in related.split(",") if t.strip()]

    async def close(self):
        await self._client.aclose()
