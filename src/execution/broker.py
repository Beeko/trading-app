"""
Abstract broker interface — defines the contract all broker
implementations must follow. Makes it easy to swap between
paper trading, Alpaca, IBKR, or any future broker.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class OrderRequest:
    """A request to place an order."""
    ticker: str
    side: str              # "buy" | "sell"
    quantity: int
    order_type: str        # "market" | "limit" | "stop" | "stop_limit"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "day"  # "day" | "gtc" | "ioc"
    asset_type: str = "stock"   # "stock" | "option"
    contract_symbol: Optional[str] = None  # for options


@dataclass
class OrderResult:
    """Result of placing an order."""
    order_id: str
    status: str            # "filled" | "partial" | "pending" | "rejected"
    filled_qty: int
    filled_price: float
    timestamp: datetime
    message: str


@dataclass
class Position:
    """A current holding."""
    ticker: str
    quantity: int
    avg_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    pnl_pct: float
    side: str              # "long" | "short"
    asset_type: str


@dataclass
class AccountInfo:
    """Account summary."""
    balance: float
    buying_power: float
    equity: float
    cash: float
    positions_value: float
    daily_pnl: float
    total_pnl: float


class BaseBroker(ABC):
    """Abstract base class for all broker implementations."""

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the broker."""
        ...

    @abstractmethod
    async def disconnect(self):
        """Clean up connection."""
        ...

    @abstractmethod
    async def get_account(self) -> AccountInfo:
        """Fetch account summary."""
        ...

    @abstractmethod
    async def get_positions(self) -> dict[str, Position]:
        """Fetch all current positions."""
        ...

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResult:
        """Submit an order to the broker."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> OrderResult:
        """Check the status of an order."""
        ...

    @abstractmethod
    async def close_position(self, ticker: str) -> Optional[OrderResult]:
        """Close an entire position in a ticker."""
        ...

    @abstractmethod
    async def close_all_positions(self) -> list[OrderResult]:
        """Close all open positions (end-of-day safety)."""
        ...
