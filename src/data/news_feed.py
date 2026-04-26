"""
News feed service — polls financial news APIs and normalizes
articles into a standard format for sentiment analysis.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NewsFeedService:
    """Aggregates financial news from Finnhub and Benzinga."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15.0)
        self._article_cache: list[NewsArticle] = []
        self._seen_urls: set[str] = set()
        log.info("NewsFeedService initialized")

    # ── Finnhub ──────────────────────────────────────────────────────

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
                ts = item.get("datetime", 0)
                articles.append(NewsArticle(
                    headline=item.get("headline", ""),
                    summary=item.get("summary", ""),
                    source=item.get("source", "finnhub"),
                    url=url,
                    published_at=datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) if ts else _utcnow(),
                    tickers=self._extract_tickers(item.get("related", "")),
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
            today = _utcnow()
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
            for item in resp.json()[:20]:
                url = item.get("url", "")
                if url in self._seen_urls:
                    continue
                self._seen_urls.add(url)
                ts = item.get("datetime", 0)
                articles.append(NewsArticle(
                    headline=item.get("headline", ""),
                    summary=item.get("summary", ""),
                    source=item.get("source", "finnhub"),
                    url=url,
                    published_at=datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) if ts else _utcnow(),
                    tickers=[ticker],
                    category="company",
                ))
            log.info(f"Fetched {len(articles)} articles for {ticker}")
            return articles
        except Exception as e:
            log.error(f"Ticker news fetch for {ticker} failed: {e}")
            return []

    # ── Benzinga ─────────────────────────────────────────────────────

    async def fetch_benzinga_news(
        self,
        ticker: Optional[str] = None,
        page_size: int = 20,
    ) -> list[NewsArticle]:
        """
        Fetch financial news from Benzinga (free/trial tier).

        Free tier supports /api/v2/news with token auth.
        Pass a ticker to get company-specific news, or omit for general market news.
        """
        if not settings.BENZINGA_API_KEY:
            return []
        try:
            params: dict = {
                "token": settings.BENZINGA_API_KEY,
                "pageSize": page_size,
                "displayOutput": "abstract",  # includes teaser, not full body
            }
            if ticker:
                params["tickers"] = ticker

            resp = await self._client.get(
                "https://api.benzinga.com/api/v2/news",
                params=params,
            )
            resp.raise_for_status()

            articles = []
            for item in resp.json():
                url = item.get("url", "")
                if url in self._seen_urls:
                    continue
                self._seen_urls.add(url)

                # Tickers are in a "stocks" list: [{"name": "AAPL"}, ...]
                tickers = [
                    s["name"].upper()
                    for s in item.get("stocks", [])
                    if s.get("name")
                ]
                if ticker and ticker.upper() not in tickers:
                    tickers.insert(0, ticker.upper())

                articles.append(NewsArticle(
                    headline=item.get("title", ""),
                    summary=item.get("teaser", ""),
                    source="benzinga",
                    url=url,
                    published_at=self._parse_benzinga_date(item.get("created", "")),
                    tickers=tickers,
                    category=self._categorize_benzinga(item.get("channels", [])),
                ))

            log.info(
                f"Fetched {len(articles)} articles from Benzinga"
                + (f" for {ticker}" if ticker else "")
            )
            return articles
        except Exception as e:
            log.error(f"Benzinga news fetch failed: {e}")
            return []

    @staticmethod
    def _parse_benzinga_date(date_str: str) -> datetime:
        """Parse Benzinga's RFC-2822 or ISO date string to naive UTC datetime."""
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(date_str, fmt).astimezone(timezone.utc).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue
        return _utcnow()

    @staticmethod
    def _categorize_benzinga(channels: list) -> str:
        """Map Benzinga channel names to normalized article categories."""
        names = {c.get("name", "").lower() for c in channels}
        if "earnings" in names:
            return "earnings"
        if any(k in names for k in ("m&a", "mergers-acquisitions", "merger")):
            return "merger"
        if any(k in names for k in ("government-regulation", "sec", "legal")):
            return "regulation"
        return "general"

    # ── Aggregation ──────────────────────────────────────────────────

    async def poll_all_sources(self) -> list[NewsArticle]:
        """Poll all configured news sources and return new articles."""
        new_articles: list[NewsArticle] = []

        finnhub = await self.fetch_finnhub_news()
        new_articles.extend(finnhub)

        benzinga = await self.fetch_benzinga_news()
        new_articles.extend(benzinga)

        self._prune_cache()
        self._article_cache.extend(new_articles)
        return new_articles

    def get_recent_articles(
        self,
        ticker: Optional[str] = None,
        hours: int = 4,
    ) -> list[NewsArticle]:
        """Get cached articles, optionally filtered by ticker."""
        cutoff = _utcnow() - timedelta(hours=hours)
        articles = [a for a in self._article_cache if a.published_at >= cutoff]
        if ticker:
            articles = [a for a in articles if ticker in a.tickers]
        return sorted(articles, key=lambda a: a.published_at, reverse=True)

    def _prune_cache(self):
        """Remove articles older than 24 hours."""
        cutoff = _utcnow() - timedelta(hours=24)
        self._article_cache = [
            a for a in self._article_cache if a.published_at >= cutoff
        ]

    @staticmethod
    def _extract_tickers(related: str) -> list[str]:
        """Parse ticker symbols from a comma-separated string."""
        if not related:
            return []
        return [t.strip().upper() for t in related.split(",") if t.strip()]

    async def close(self):
        await self._client.aclose()
