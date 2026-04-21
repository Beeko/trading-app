"""
Database models and session management.
Stores trade history, signals, daily summaries, and watchlist state.
"""

from datetime import datetime, date
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    DateTime, Date, Boolean, Enum, Text, JSON,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from config import settings

engine = create_engine(settings.DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Trade(Base):
    """Every executed trade — paper or live."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    ticker = Column(String(10), nullable=False)
    side = Column(String(4), nullable=False)          # "buy" | "sell"
    asset_type = Column(String(10), default="stock")   # "stock" | "option"
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    total_value = Column(Float, nullable=False)
    order_type = Column(String(10), default="market")  # "market" | "limit"
    status = Column(String(12), default="filled")      # "filled" | "cancelled" | "rejected"
    strategy = Column(String(30), nullable=True)       # e.g. "covered_call"
    signal_id = Column(Integer, nullable=True)         # FK to the signal that triggered it
    broker_order_id = Column(String(50), nullable=True)
    pnl = Column(Float, nullable=True)                 # realized P&L if closing
    notes = Column(Text, nullable=True)


class Signal(Base):
    """Every signal the analysis engine generates, acted on or not."""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    ticker = Column(String(10), nullable=False)
    direction = Column(String(4), nullable=False)      # "buy" | "sell"
    strength = Column(Float, nullable=False)           # 0.0 – 1.0
    source = Column(String(20), nullable=False)        # "confluence" | "macd" | "sentiment"
    indicators = Column(JSON, nullable=True)           # snapshot of indicator values
    sentiment_score = Column(Float, nullable=True)
    acted_on = Column(Boolean, default=False)
    rejected_reason = Column(String(100), nullable=True)


class DailySummary(Base):
    """End-of-day rollup of performance."""
    __tablename__ = "daily_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, default=date.today, unique=True, nullable=False)
    starting_balance = Column(Float, nullable=False)
    ending_balance = Column(Float, nullable=False)
    realized_pnl = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    signals_generated = Column(Integer, default=0)
    signals_acted_on = Column(Integer, default=0)
    daily_goal = Column(Float, nullable=True)
    goal_met = Column(Boolean, default=False)
    circuit_breaker_hit = Column(Boolean, default=False)


class Watchlist(Base):
    """Tickers being actively tracked with their latest indicator state."""
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), unique=True, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)
    source = Column(String(20), default="manual")  # "manual" | "scanner" | "trending"
    sector = Column(String(30), nullable=True)
    last_price = Column(Float, nullable=True)
    last_macd = Column(Float, nullable=True)
    last_rsi = Column(Float, nullable=True)
    last_sentiment = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)


def get_session():
    """Get a new database session."""
    return SessionLocal()
