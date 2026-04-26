"""
Paper trader — simulates trade execution against real market data
without placing real orders. Tracks a virtual portfolio with
realistic fill simulation and logs everything identically to live.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional
from src.execution.broker import (
    BaseBroker, OrderRequest, OrderResult, Position, AccountInfo,
)
from src.data.market_data import MarketDataService
from src.utils.logger import get_logger

log = get_logger("paper_trader")


class PaperTrader(BaseBroker):
    """Simulated broker for paper trading."""

    def __init__(
        self,
        market_data: MarketDataService,
        starting_balance: float = 25_000.0,
        slippage_pct: float = 0.05,
    ):
        self._market = market_data
        self._cash = starting_balance
        self._starting_balance = starting_balance
        self._slippage_pct = slippage_pct
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, OrderResult] = {}
        self._trade_history: list[dict] = []
        self._connected = False
        log.info(
            f"PaperTrader initialized: ${starting_balance:.2f} balance, "
            f"{slippage_pct}% slippage"
        )

    async def connect(self) -> bool:
        self._connected = True
        log.info("PaperTrader connected")
        return True

    async def disconnect(self):
        self._connected = False
        log.info("PaperTrader disconnected")

    async def get_account(self) -> AccountInfo:
        positions_value = sum(
            p.market_value for p in self._positions.values()
        )
        equity = self._cash + positions_value
        daily_pnl = equity - self._starting_balance

        return AccountInfo(
            balance=round(equity, 2),
            buying_power=round(self._cash, 2),
            equity=round(equity, 2),
            cash=round(self._cash, 2),
            positions_value=round(positions_value, 2),
            daily_pnl=round(daily_pnl, 2),
            total_pnl=round(equity - self._starting_balance, 2),
        )

    async def get_positions(self) -> dict[str, Position]:
        # Update current prices
        for ticker, pos in self._positions.items():
            quote = await self._market.get_quote(ticker)
            if quote:
                pos.current_price = quote.price
                pos.market_value = round(pos.quantity * quote.price, 2)
                pos.unrealized_pnl = round(
                    (quote.price - pos.avg_price) * pos.quantity, 2
                )
                pos.pnl_pct = round(
                    (quote.price - pos.avg_price) / pos.avg_price * 100, 2
                ) if pos.avg_price else 0

        return dict(self._positions)

    async def place_order(self, order: OrderRequest) -> OrderResult:
        """Simulate order execution with slippage."""
        quote = await self._market.get_quote(order.ticker)
        if not quote:
            result = OrderResult(
                order_id=str(uuid.uuid4())[:8],
                status="rejected",
                filled_qty=0,
                filled_price=0,
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                message=f"No quote available for {order.ticker}",
            )
            self._orders[result.order_id] = result
            return result

        # Simulate fill price with slippage
        if order.order_type == "market":
            if order.side == "buy":
                fill_price = quote.ask * (1 + self._slippage_pct / 100)
            else:
                fill_price = quote.bid * (1 - self._slippage_pct / 100)
        elif order.order_type == "limit" and order.limit_price:
            if order.side == "buy" and quote.ask <= order.limit_price:
                fill_price = min(quote.ask, order.limit_price)
            elif order.side == "sell" and quote.bid >= order.limit_price:
                fill_price = max(quote.bid, order.limit_price)
            else:
                result = OrderResult(
                    order_id=str(uuid.uuid4())[:8],
                    status="pending",
                    filled_qty=0,
                    filled_price=0,
                    timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                    message="Limit price not met",
                )
                self._orders[result.order_id] = result
                return result
        else:
            fill_price = quote.price

        fill_price = round(fill_price, 2)
        total_cost = fill_price * order.quantity

        # Execute the trade
        if order.side == "buy":
            if total_cost > self._cash:
                result = OrderResult(
                    order_id=str(uuid.uuid4())[:8],
                    status="rejected",
                    filled_qty=0,
                    filled_price=0,
                    timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                    message=(
                        f"Insufficient funds: need ${total_cost:.2f}, "
                        f"have ${self._cash:.2f}"
                    ),
                )
                self._orders[result.order_id] = result
                return result

            self._cash -= total_cost
            self._add_to_position(order.ticker, order.quantity, fill_price)

        elif order.side == "sell":
            pnl = self._remove_from_position(
                order.ticker, order.quantity, fill_price
            )
            self._cash += total_cost

        order_id = str(uuid.uuid4())[:8]
        result = OrderResult(
            order_id=order_id,
            status="filled",
            filled_qty=order.quantity,
            filled_price=fill_price,
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            message=f"Paper {order.side} {order.quantity} {order.ticker} @ ${fill_price}",
        )
        self._orders[order_id] = result

        # Log the trade
        self._trade_history.append({
            "order_id": order_id,
            "ticker": order.ticker,
            "side": order.side,
            "quantity": order.quantity,
            "price": fill_price,
            "total": total_cost,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        })

        log.info(
            f"Paper {order.side}: {order.quantity} {order.ticker} "
            f"@ ${fill_price:.2f} (total ${total_cost:.2f})",
            extra={"trade_data": {
                "order_id": order_id,
                "ticker": order.ticker,
                "side": order.side,
                "qty": order.quantity,
                "price": fill_price,
            }},
        )

        return result

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = "cancelled"
            return True
        return False

    async def get_order_status(self, order_id: str) -> OrderResult:
        if order_id in self._orders:
            return self._orders[order_id]
        return OrderResult(
            order_id=order_id,
            status="unknown",
            filled_qty=0,
            filled_price=0,
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            message="Order not found",
        )

    async def close_position(self, ticker: str) -> Optional[OrderResult]:
        if ticker not in self._positions:
            return None
        pos = self._positions[ticker]
        return await self.place_order(OrderRequest(
            ticker=ticker,
            side="sell",
            quantity=pos.quantity,
            order_type="market",
        ))

    async def close_all_positions(self) -> list[OrderResult]:
        results = []
        for ticker in list(self._positions.keys()):
            result = await self.close_position(ticker)
            if result:
                results.append(result)
        log.info(f"Closed all positions: {len(results)} trades")
        return results

    def _add_to_position(self, ticker: str, qty: int, price: float):
        if ticker in self._positions:
            pos = self._positions[ticker]
            total_cost = pos.avg_price * pos.quantity + price * qty
            pos.quantity += qty
            pos.avg_price = round(total_cost / pos.quantity, 2)
            pos.market_value = round(pos.quantity * price, 2)
        else:
            self._positions[ticker] = Position(
                ticker=ticker,
                quantity=qty,
                avg_price=price,
                current_price=price,
                market_value=round(qty * price, 2),
                unrealized_pnl=0,
                pnl_pct=0,
                side="long",
                asset_type="stock",
            )

    def _remove_from_position(
        self, ticker: str, qty: int, price: float
    ) -> float:
        if ticker not in self._positions:
            return 0.0
        pos = self._positions[ticker]
        pnl = (price - pos.avg_price) * min(qty, pos.quantity)
        pos.quantity -= qty
        if pos.quantity <= 0:
            del self._positions[ticker]
        return round(pnl, 2)
