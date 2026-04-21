"""
Alpaca broker implementation — connects to Alpaca's API for
live or paper trading. Implements the BaseBroker interface.
"""

import httpx
from datetime import datetime
from typing import Optional
from src.execution.broker import (
    BaseBroker, OrderRequest, OrderResult, Position, AccountInfo,
)
from config import settings
from src.utils.logger import get_logger

log = get_logger("alpaca_broker")


class AlpacaBroker(BaseBroker):
    """Live broker implementation using Alpaca's API."""

    def __init__(self):
        self._base_url = settings.ALPACA_BASE_URL
        self._headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
            "Content-Type": "application/json",
        }
        self._client: Optional[httpx.AsyncClient] = None
        log.info(f"AlpacaBroker initialized (url={self._base_url})")

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            headers=self._headers, timeout=10.0
        )
        try:
            resp = await self._client.get(f"{self._base_url}/v2/account")
            resp.raise_for_status()
            acct = resp.json()
            log.info(
                f"Connected to Alpaca: "
                f"equity=${acct.get('equity', 'N/A')}, "
                f"status={acct.get('status', 'N/A')}"
            )
            return True
        except Exception as e:
            log.error(f"Failed to connect to Alpaca: {e}")
            return False

    async def disconnect(self):
        if self._client:
            await self._client.aclose()
        log.info("Disconnected from Alpaca")

    async def get_account(self) -> AccountInfo:
        resp = await self._client.get(f"{self._base_url}/v2/account")
        resp.raise_for_status()
        a = resp.json()
        return AccountInfo(
            balance=float(a.get("equity", 0)),
            buying_power=float(a.get("buying_power", 0)),
            equity=float(a.get("equity", 0)),
            cash=float(a.get("cash", 0)),
            positions_value=float(a.get("long_market_value", 0)),
            daily_pnl=float(a.get("equity", 0)) - float(a.get("last_equity", 0)),
            total_pnl=float(a.get("equity", 0)) - float(a.get("cash", 0)),
        )

    async def get_positions(self) -> dict[str, Position]:
        resp = await self._client.get(f"{self._base_url}/v2/positions")
        resp.raise_for_status()
        positions = {}
        for p in resp.json():
            ticker = p["symbol"]
            positions[ticker] = Position(
                ticker=ticker,
                quantity=int(p["qty"]),
                avg_price=float(p["avg_entry_price"]),
                current_price=float(p["current_price"]),
                market_value=float(p["market_value"]),
                unrealized_pnl=float(p["unrealized_pl"]),
                pnl_pct=float(p["unrealized_plpc"]) * 100,
                side="long" if int(p["qty"]) > 0 else "short",
                asset_type=p.get("asset_class", "us_equity"),
            )
        return positions

    async def place_order(self, order: OrderRequest) -> OrderResult:
        body = {
            "symbol": order.contract_symbol or order.ticker,
            "qty": str(order.quantity),
            "side": order.side,
            "type": order.order_type,
            "time_in_force": order.time_in_force,
        }
        if order.limit_price:
            body["limit_price"] = str(order.limit_price)
        if order.stop_price:
            body["stop_price"] = str(order.stop_price)

        try:
            resp = await self._client.post(
                f"{self._base_url}/v2/orders", json=body
            )
            resp.raise_for_status()
            o = resp.json()
            result = OrderResult(
                order_id=o["id"],
                status=o.get("status", "pending"),
                filled_qty=int(o.get("filled_qty") or 0),
                filled_price=float(o.get("filled_avg_price") or 0),
                timestamp=datetime.utcnow(),
                message=f"Order placed: {order.side} {order.quantity} {order.ticker}",
            )
            log.info(
                f"Order placed: {order.side} {order.quantity} "
                f"{order.ticker} ({result.status})",
                extra={"trade_data": {
                    "order_id": result.order_id,
                    "ticker": order.ticker,
                    "side": order.side,
                    "qty": order.quantity,
                    "type": order.order_type,
                }},
            )
            return result
        except httpx.HTTPStatusError as e:
            error_body = e.response.json() if e.response else {}
            msg = error_body.get("message", str(e))
            log.error(f"Order rejected: {msg}")
            return OrderResult(
                order_id="",
                status="rejected",
                filled_qty=0,
                filled_price=0,
                timestamp=datetime.utcnow(),
                message=msg,
            )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            resp = await self._client.delete(
                f"{self._base_url}/v2/orders/{order_id}"
            )
            resp.raise_for_status()
            log.info(f"Order {order_id} cancelled")
            return True
        except Exception as e:
            log.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def get_order_status(self, order_id: str) -> OrderResult:
        resp = await self._client.get(
            f"{self._base_url}/v2/orders/{order_id}"
        )
        resp.raise_for_status()
        o = resp.json()
        return OrderResult(
            order_id=o["id"],
            status=o.get("status", "unknown"),
            filled_qty=int(o.get("filled_qty") or 0),
            filled_price=float(o.get("filled_avg_price") or 0),
            timestamp=datetime.utcnow(),
            message=o.get("status", ""),
        )

    async def close_position(self, ticker: str) -> Optional[OrderResult]:
        try:
            resp = await self._client.delete(
                f"{self._base_url}/v2/positions/{ticker}"
            )
            resp.raise_for_status()
            o = resp.json()
            log.info(f"Closed position in {ticker}")
            return OrderResult(
                order_id=o.get("id", ""),
                status="filled",
                filled_qty=int(o.get("qty", 0)),
                filled_price=float(o.get("filled_avg_price", 0)),
                timestamp=datetime.utcnow(),
                message=f"Position closed: {ticker}",
            )
        except Exception as e:
            log.error(f"Failed to close position {ticker}: {e}")
            return None

    async def close_all_positions(self) -> list[OrderResult]:
        try:
            resp = await self._client.delete(
                f"{self._base_url}/v2/positions"
            )
            resp.raise_for_status()
            log.info("All positions closed")
            return []
        except Exception as e:
            log.error(f"Failed to close all positions: {e}")
            return []
