"""
Stock scanner — finds trading candidates from multiple sources:
trending stocks, unusual volume, sector momentum, and watchlist.
"""

from dataclasses import dataclass
from typing import Optional
from src.data.market_data import MarketDataService
from src.data.social_feed import SocialFeedService
from src.analysis.indicators import IndicatorEngine, IndicatorSnapshot
from config import settings
from src.utils.logger import get_logger

log = get_logger("scanner")


@dataclass
class ScanResult:
    """A stock that passed the scanner's filters."""
    ticker: str
    source: str           # "trending" | "watchlist" | "scanner"
    reason: str
    priority: float       # 0.0 – 1.0
    snapshot: Optional[IndicatorSnapshot] = None


class StockScanner:
    """Scans for actionable trading candidates."""

    def __init__(
        self,
        market_data: MarketDataService,
        social_feed: SocialFeedService,
        indicator_engine: IndicatorEngine,
    ):
        self._market = market_data
        self._social = social_feed
        self._indicators = indicator_engine
        self._watchlist: set[str] = set()
        log.info("StockScanner initialized")

    def add_to_watchlist(self, ticker: str):
        self._watchlist.add(ticker.upper())
        log.info(f"Added {ticker} to watchlist")

    def remove_from_watchlist(self, ticker: str):
        self._watchlist.discard(ticker.upper())
        log.info(f"Removed {ticker} from watchlist")

    async def scan_trending(self, min_mentions: int = 5) -> list[ScanResult]:
        """Find stocks trending on social media."""
        trending = self._social.get_trending(min_mentions=min_mentions)
        results = []
        for t in trending[:10]:
            # Basic filter: skip if too low velocity
            if t.velocity < 2.0:
                continue
            results.append(ScanResult(
                ticker=t.ticker,
                source="trending",
                reason=f"{t.mention_count} mentions, {t.velocity}/hr velocity",
                priority=min(t.velocity / 20.0, 1.0),
            ))
        log.info(f"Found {len(results)} trending candidates")
        return results

    async def scan_watchlist(self) -> list[ScanResult]:
        """Analyze all stocks on the watchlist."""
        results = []
        for ticker in self._watchlist:
            bars = await self._market.get_bars(ticker, limit=200)
            if len(bars) < 30:
                continue

            snapshot = self._indicators.compute_all(ticker, bars)

            # Check for any non-neutral signals
            signals = [
                s for s in [
                    snapshot.macd, snapshot.rsi, snapshot.bollinger,
                    snapshot.vwap, snapshot.sma_cross,
                ]
                if s and s.direction.value != "neutral"
            ]

            if signals:
                avg_strength = sum(s.strength for s in signals) / len(signals)
                results.append(ScanResult(
                    ticker=ticker,
                    source="watchlist",
                    reason=f"{len(signals)} active signals",
                    priority=round(avg_strength, 3),
                    snapshot=snapshot,
                ))

        log.info(f"Scanned {len(self._watchlist)} watchlist tickers, "
                 f"{len(results)} have active signals")
        return results

    async def apply_filters(self, ticker: str) -> bool:
        """Check if a ticker passes basic quality filters."""
        quote = await self._market.get_quote(ticker)
        if not quote:
            return False

        # Price floor — no penny stocks
        if quote.price < settings.MIN_STOCK_PRICE:
            log.debug(f"{ticker} rejected: price ${quote.price:.2f} "
                      f"< min ${settings.MIN_STOCK_PRICE}")
            return False

        return True

    async def full_scan(self) -> list[ScanResult]:
        """Run all scan sources and return combined candidates."""
        all_results = []

        # Watchlist stocks
        watchlist_results = await self.scan_watchlist()
        all_results.extend(watchlist_results)

        # Trending stocks (filtered)
        trending_results = await self.scan_trending()
        for result in trending_results:
            if result.ticker not in self._watchlist:
                if await self.apply_filters(result.ticker):
                    all_results.append(result)

        # Sort by priority
        all_results.sort(key=lambda r: r.priority, reverse=True)
        log.info(f"Full scan complete: {len(all_results)} candidates")
        return all_results
