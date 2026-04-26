"""
Central configuration — single source of truth for all app settings.
Loads from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = Path(os.getenv("LOG_DIR", DATA_DIR / "logs"))

# ── Broker ──
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
# "iex" = free tier  |  "sip" = paid/live consolidated tape
ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex")

# ── Trading mode ──
TRADING_MODE = os.getenv("TRADING_MODE", "paper")  # "paper" | "live"

# ── Daily goals ──
DAILY_PROFIT_TARGET = float(os.getenv("DAILY_PROFIT_TARGET", "200.00"))

# ── Risk parameters ──
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "2.0"))
MAX_SINGLE_TRADE_RISK_PCT = float(os.getenv("MAX_SINGLE_TRADE_RISK_PCT", "1.0"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "10.0"))
MAX_SECTOR_EXPOSURE_PCT = float(os.getenv("MAX_SECTOR_EXPOSURE_PCT", "25.0"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "10"))
RISK_PROFILE = os.getenv("RISK_PROFILE", "conservative")

# ── News & sentiment ──
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
BENZINGA_API_KEY = os.getenv("BENZINGA_API_KEY", "")
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL", "local")

# ── Dashboard ──
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "3000"))

# ── Database ──
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'trades.db'}")

# ── Scheduling intervals (seconds) ──
MARKET_DATA_INTERVAL = int(os.getenv("MARKET_DATA_INTERVAL", "5"))
NEWS_POLL_INTERVAL = int(os.getenv("NEWS_POLL_INTERVAL", "300"))
SOCIAL_POLL_INTERVAL = int(os.getenv("SOCIAL_POLL_INTERVAL", "120"))
INDICATOR_RECALC_INTERVAL = int(os.getenv("INDICATOR_RECALC_INTERVAL", "10"))

# ── Logging ──
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Market hours (Eastern Time) ──
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0

# ── Options strategy whitelist ──
ALLOWED_OPTIONS_STRATEGIES = [
    "covered_call",
    "cash_secured_put",
    "bull_call_spread",
    "bear_put_spread",
    "iron_condor",
    "protective_put",
]

# ── Stock filters ──
MIN_STOCK_PRICE = 5.00       # no penny stocks
MIN_AVERAGE_VOLUME = 500_000  # liquidity floor

# ── Default watchlist (shown immediately on startup) ──
DEFAULT_WATCHLIST = [
    t.strip().upper()
    for t in os.getenv(
        "DEFAULT_WATCHLIST",
        "SPY,QQQ,AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL,AMD",
    ).split(",")
    if t.strip()
]
