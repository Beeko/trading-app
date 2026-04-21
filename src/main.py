"""
Main entry point — initializes all services, wires them together,
starts the scheduler, and launches the dashboard.
"""

import asyncio
import signal
import sys
import uvicorn
from config import settings
from config.risk_profiles import get_profile
from src.utils.database import init_db
from src.utils.logger import setup_logging, get_logger
from src.utils.scheduler import TaskScheduler
from src.data.market_data import MarketDataService
from src.data.news_feed import NewsFeedService
from src.data.social_feed import SocialFeedService
from src.analysis.indicators import IndicatorEngine
from src.analysis.sentiment import SentimentEngine
from src.analysis.scanner import StockScanner
from src.strategy.goal_engine import GoalEngine
from src.strategy.signals import SignalEngine
from src.strategy.options import OptionsStrategySelector
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.manager import RiskManager
from src.execution.paper_trader import PaperTrader
from src.execution.alpaca_broker import AlpacaBroker
from src.dashboard.app import get_app
from src.dashboard.routes import set_services


setup_logging()
log = get_logger("main")


class TradingApp:
    """Main application orchestrator."""

    def __init__(self):
        self._running = False
        self._scheduler = TaskScheduler()
        self._signal_log: list[dict] = []
        self._pending_signals: dict = {}

        # ── Data layer ──
        self.market_data = MarketDataService()
        self.news_feed = NewsFeedService()
        self.social_feed = SocialFeedService()

        # ── Analysis layer ──
        self.indicators = IndicatorEngine()
        self.sentiment = SentimentEngine(model=settings.SENTIMENT_MODEL)
        self.scanner = StockScanner(
            self.market_data, self.social_feed, self.indicators
        )

        # ── Strategy layer ──
        self.goal_engine = GoalEngine()
        self.signal_engine = SignalEngine(self.goal_engine)
        self.options_selector = OptionsStrategySelector()

        # ── Execution layer (selected based on trading mode) ──
        if settings.TRADING_MODE == "paper":
            self.broker = PaperTrader(self.market_data)
            log.info("Using PAPER trading mode")
        else:
            self.broker = AlpacaBroker()
            log.info("Using LIVE trading mode — be careful!")

        # ── Risk layer (initialized after broker connects) ──
        self.circuit_breaker = None
        self.risk_manager = None

    async def start(self):
        """Initialize everything and start trading."""
        log.info("=" * 60)
        log.info("  TRADING APP STARTING")
        log.info(f"  Mode: {settings.TRADING_MODE.upper()}")
        log.info(f"  Risk profile: {settings.RISK_PROFILE}")
        log.info(f"  Daily target: ${settings.DAILY_PROFIT_TARGET:.2f}")
        log.info("=" * 60)

        # Initialize database
        init_db()
        log.info("Database initialized")

        # Connect to broker
        connected = await self.broker.connect()
        if not connected:
            log.error("Failed to connect to broker — exiting")
            return

        # Get account info
        account = await self.broker.get_account()
        log.info(f"Account balance: ${account.balance:.2f}")

        # Initialize risk layer with actual balance
        self.circuit_breaker = CircuitBreaker(account.balance)
        self.risk_manager = RiskManager(self.circuit_breaker)
        self.risk_manager.update_state(
            await self.broker.get_positions(), account.balance
        )

        # Wire circuit breaker to goal engine
        self.circuit_breaker.on_trip(
            lambda state: self.goal_engine.halt_trading(state.reason)
        )

        # Start daily goal
        self.goal_engine.start_day(account.balance)

        # Inject services into dashboard
        set_services({
            "goal_engine":     self.goal_engine,
            "risk_manager":    self.risk_manager,
            "circuit_breaker": self.circuit_breaker,
            "broker":          self.broker,
            "scanner":         self.scanner,
            "signal_log":      self._signal_log,
            "pending_signals": self._pending_signals,
            "app":             self,
        })

        # ── Schedule periodic tasks ──
        self._scheduler.add_task(
            "poll_news",
            self._poll_news,
            settings.NEWS_POLL_INTERVAL,
        )
        self._scheduler.add_task(
            "poll_social",
            self._poll_social,
            settings.SOCIAL_POLL_INTERVAL,
        )
        self._scheduler.add_task(
            "scan_and_analyze",
            self._scan_and_analyze,
            settings.INDICATOR_RECALC_INTERVAL,
        )
        self._scheduler.add_task(
            "update_account_state",
            self._update_account_state,
            30,  # every 30 seconds
        )

        await self._scheduler.start()
        self._running = True
        log.info("All systems running. Dashboard available at "
                 f"http://localhost:{settings.DASHBOARD_PORT}")

    async def stop(self):
        """Graceful shutdown."""
        log.info("Shutting down...")
        self._running = False
        await self._scheduler.stop()

        # Close open positions based on end-of-session policy
        positions = await self.broker.get_positions()
        if positions:
            log.warning(
                f"{len(positions)} open positions at shutdown. "
                "Set stop-losses or close manually."
            )

        await self.market_data.close()
        await self.news_feed.close()
        await self.social_feed.close()
        await self.broker.disconnect()
        log.info("Shutdown complete")

    # ── Periodic task implementations ──

    async def _poll_news(self):
        """Poll news sources and run sentiment analysis."""
        articles = await self.news_feed.poll_all_sources()
        for article in articles:
            result = self.sentiment.score_article(article)
            article.sentiment = result.score
            log.debug(
                f"News: [{result.label}] {article.headline[:60]}... "
                f"({', '.join(article.tickers)})"
            )

    async def _poll_social(self):
        """Poll social sources for trending stocks."""
        await self.social_feed.poll_social_sources()
        trending = self.social_feed.get_trending()
        if trending:
            log.info(
                f"Trending: {', '.join(t.ticker for t in trending[:5])}"
            )

    async def _scan_and_analyze(self):
        """Run the full scan → analyze → signal pipeline."""
        candidates = await self.scanner.full_scan()

        for candidate in candidates[:5]:  # process top 5
            # Get fresh bars and compute indicators
            bars = await self.market_data.get_bars(
                candidate.ticker, limit=200
            )
            if len(bars) < 30:
                continue

            snapshot = self.indicators.compute_all(candidate.ticker, bars)

            # Get sentiment
            articles = self.news_feed.get_recent_articles(
                candidate.ticker, hours=4
            )
            sentiment_results = [
                self.sentiment.score_article(a) for a in articles
            ]
            agg_sentiment = self.sentiment.aggregate_sentiment(
                candidate.ticker, sentiment_results
            )

            # Generate signal
            signal = self.signal_engine.evaluate(
                candidate.ticker, snapshot, agg_sentiment
            )

            if signal:
                # Run through risk manager
                decision = self.risk_manager.evaluate(signal)

                self._signal_log.append({
                    "timestamp": signal.timestamp.isoformat(),
                    "ticker": signal.ticker,
                    "direction": signal.direction,
                    "confidence": signal.confidence,
                    "reasoning": signal.reasoning,
                    "status": "approved" if decision.approved else "rejected",
                    "risk_reasons": decision.reasons,
                })

                if decision.approved and not signal.requires_approval:
                    await self._execute_signal(signal, decision)

    async def _execute_signal(self, signal, decision):
        """Execute an approved trade signal."""
        from src.execution.broker import OrderRequest

        account = await self.broker.get_account()
        allocation = account.balance * (decision.adjusted_size_pct / 100.0)
        quote = await self.market_data.get_quote(signal.ticker)
        if not quote or quote.price <= 0:
            return

        quantity = int(allocation / quote.price)
        if quantity <= 0:
            return

        order = OrderRequest(
            ticker=signal.ticker,
            side=signal.direction,
            quantity=quantity,
            order_type="market",
        )

        result = await self.broker.place_order(order)
        if result.status == "filled":
            log.info(
                f"Trade executed: {signal.direction} {quantity} "
                f"{signal.ticker} @ ${result.filled_price:.2f}"
            )

    async def _update_account_state(self):
        """Periodically refresh account state for risk calculations."""
        account = await self.broker.get_account()
        positions = await self.broker.get_positions()
        self.risk_manager.update_state(
            {t: {"market_value": p.market_value, "sector": "unknown"}
             for t, p in positions.items()},
            account.balance,
        )
        # Update goal engine
        self.goal_engine.update_pnl(
            realized_pnl=account.daily_pnl,
            unrealized_pnl=sum(p.unrealized_pnl for p in positions.values()),
            current_balance=account.balance,
        )


async def run():
    """Main async entry point."""
    app_instance = TradingApp()

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(app_instance.stop())
        )

    await app_instance.start()

    # Run the FastAPI dashboard
    dashboard = get_app()
    config = uvicorn.Config(
        dashboard,
        host=settings.DASHBOARD_HOST,
        port=settings.DASHBOARD_PORT,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(run())
