"""
Social feed service — monitors Reddit, Stocktwits, and other social
sources to detect trending stocks and sentiment shifts.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import Counter
from typing import Optional
import httpx
from src.utils.logger import get_logger

log = get_logger("social_feed")


@dataclass
class SocialPost:
    """Normalized social media post."""
    text: str
    source: str          # "reddit" | "stocktwits"
    author: str
    tickers: list[str]
    upvotes: int
    timestamp: datetime
    url: str
    subreddit: Optional[str] = None


@dataclass
class TrendingTicker:
    """A ticker that's gaining unusual social attention."""
    ticker: str
    mention_count: int
    velocity: float           # mentions per hour
    avg_sentiment: float      # -1.0 to 1.0
    top_source: str           # where most mentions are coming from
    first_seen: datetime


class SocialFeedService:
    """Tracks social media for stock mentions and trending detection."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15.0)
        self._post_cache: list[SocialPost] = []
        self._mention_history: dict[str, list[datetime]] = {}
        log.info("SocialFeedService initialized")

    async def fetch_reddit_posts(
        self,
        subreddit: str = "wallstreetbets",
        limit: int = 25,
    ) -> list[SocialPost]:
        """Fetch recent posts from a subreddit via Reddit's JSON API."""
        try:
            resp = await self._client.get(
                f"https://www.reddit.com/r/{subreddit}/hot.json",
                params={"limit": limit},
                headers={"User-Agent": "TradingApp/1.0"},
            )
            resp.raise_for_status()
            posts = []
            for child in resp.json().get("data", {}).get("children", []):
                data = child.get("data", {})
                text = f"{data.get('title', '')} {data.get('selftext', '')}"
                tickers = self._extract_tickers_from_text(text)
                if not tickers:
                    continue
                posts.append(SocialPost(
                    text=text[:500],
                    source="reddit",
                    author=data.get("author", ""),
                    tickers=tickers,
                    upvotes=data.get("ups", 0),
                    timestamp=datetime.fromtimestamp(
                        data.get("created_utc", 0)
                    ),
                    url=f"https://reddit.com{data.get('permalink', '')}",
                    subreddit=subreddit,
                ))
            log.info(
                f"Fetched {len(posts)} posts with tickers from r/{subreddit}"
            )
            return posts
        except Exception as e:
            log.error(f"Reddit fetch from r/{subreddit} failed: {e}")
            return []

    async def fetch_stocktwits(
        self, ticker: str
    ) -> list[SocialPost]:
        """Fetch recent Stocktwits messages for a ticker."""
        try:
            resp = await self._client.get(
                f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
            )
            resp.raise_for_status()
            messages = resp.json().get("messages", [])
            posts = []
            for msg in messages[:20]:
                posts.append(SocialPost(
                    text=msg.get("body", "")[:500],
                    source="stocktwits",
                    author=msg.get("user", {}).get("username", ""),
                    tickers=[ticker],
                    upvotes=msg.get("likes", {}).get("total", 0),
                    timestamp=datetime.strptime(
                        msg.get("created_at", ""),
                        "%Y-%m-%dT%H:%M:%SZ",
                    ) if msg.get("created_at") else datetime.utcnow(),
                    url=f"https://stocktwits.com/message/{msg.get('id', '')}",
                ))
            log.info(f"Fetched {len(posts)} Stocktwits posts for {ticker}")
            return posts
        except Exception as e:
            log.error(f"Stocktwits fetch for {ticker} failed: {e}")
            return []

    async def poll_social_sources(self) -> list[SocialPost]:
        """Poll all social sources and track mention frequency."""
        new_posts = []

        # Scan popular investing subreddits
        for sub in ["wallstreetbets", "stocks", "options", "investing"]:
            posts = await self.fetch_reddit_posts(sub, limit=15)
            new_posts.extend(posts)

        # Update mention tracking
        for post in new_posts:
            for ticker in post.tickers:
                if ticker not in self._mention_history:
                    self._mention_history[ticker] = []
                self._mention_history[ticker].append(post.timestamp)

        self._post_cache.extend(new_posts)
        self._prune_cache()
        return new_posts

    def get_trending(self, min_mentions: int = 5) -> list[TrendingTicker]:
        """Identify tickers with unusual social activity."""
        cutoff = datetime.utcnow() - timedelta(hours=4)
        recent_mentions: Counter[str] = Counter()

        for post in self._post_cache:
            if post.timestamp >= cutoff:
                for ticker in post.tickers:
                    recent_mentions[ticker] += 1

        trending = []
        for ticker, count in recent_mentions.most_common(20):
            if count < min_mentions:
                continue
            timestamps = self._mention_history.get(ticker, [])
            recent = [t for t in timestamps if t >= cutoff]
            hours = max(
                (datetime.utcnow() - min(recent)).total_seconds() / 3600,
                0.1,
            ) if recent else 1.0

            trending.append(TrendingTicker(
                ticker=ticker,
                mention_count=count,
                velocity=round(count / hours, 1),
                avg_sentiment=0.0,  # filled by sentiment engine
                top_source=self._top_source_for(ticker),
                first_seen=min(recent) if recent else datetime.utcnow(),
            ))

        return sorted(trending, key=lambda t: t.velocity, reverse=True)

    def _top_source_for(self, ticker: str) -> str:
        """Find which source mentions this ticker most."""
        sources: Counter[str] = Counter()
        for post in self._post_cache:
            if ticker in post.tickers:
                sources[post.source] += 1
        return sources.most_common(1)[0][0] if sources else "unknown"

    @staticmethod
    def _extract_tickers_from_text(text: str) -> list[str]:
        """Extract likely stock tickers from text ($AAPL or all-caps 2-5 chars)."""
        import re
        # Match $TICKER pattern
        dollar_tickers = re.findall(r'\$([A-Z]{1,5})\b', text)
        # Match standalone all-caps words (2-5 chars, likely tickers)
        caps_words = re.findall(r'\b([A-Z]{2,5})\b', text)

        # Filter common non-ticker words
        noise = {
            "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL",
            "CAN", "HER", "WAS", "ONE", "OUR", "OUT", "HAS", "HIS",
            "HOW", "ITS", "LET", "MAY", "NEW", "NOW", "OLD", "SEE",
            "WAY", "WHO", "DID", "GET", "HIM", "HIT", "PUT", "SAY",
            "SHE", "TOO", "USE", "IMO", "TBH", "LMAO", "EDIT", "JUST",
            "LIKE", "THIS", "THAT", "WITH", "HAVE", "FROM", "BEEN",
            "SOME", "WHAT", "WHEN", "WILL", "MORE", "VERY", "MUCH",
            "THAN", "INTO", "OVER", "ALSO", "BACK", "THEM", "THEY",
            "YOLO", "HODL", "MOON", "CALL", "PUTS", "LONG", "SHORT",
            "SELL", "HOLD", "GAIN", "LOSS", "BEAR", "BULL", "PUMP",
        }
        valid_caps = [w for w in caps_words if w not in noise]

        # Combine and deduplicate
        all_tickers = list(dict.fromkeys(dollar_tickers + valid_caps))
        return all_tickers[:5]  # cap at 5 per post

    def _prune_cache(self):
        cutoff = datetime.utcnow() - timedelta(hours=24)
        self._post_cache = [
            p for p in self._post_cache if p.timestamp >= cutoff
        ]
        for ticker in list(self._mention_history):
            self._mention_history[ticker] = [
                t for t in self._mention_history[ticker] if t >= cutoff
            ]
            if not self._mention_history[ticker]:
                del self._mention_history[ticker]

    async def close(self):
        await self._client.aclose()
