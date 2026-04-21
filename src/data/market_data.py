"""
Market data service — streams real-time quotes, pulls historical bars,
and fetches options chains from Alpaca's API.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import httpx
from config import settings
from src.utils.logger import get_logger

log = get_logger("market_data")


@dataclass
class PriceBar:
    """Single OHLCV bar."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class Quote:
    """Real-time quote snapshot."""
    ticker: str
    price: float
    bid: float
    ask: float
    volume: int
    timestamp: datetime


@dataclass
class OptionContract:
    """Single options contract with Greeks."""
    ticker: str
    contract_symbol: str
    strike: float
    expiration: str
    option_type: str          # "call" | "put"
    bid: float
    ask: float
    last_price: float
    volume: int
    open_interest: int
    implied_volatility: float
    delta: float
    gamma: float
    theta: float
    vega: float


class MarketDataService:
    """Fetches market data from Alpaca's API."""

    def __init__(self):
        self._base_url = "https://data.alpaca.markets"
        self._trading_url = settings.ALPACA_BASE_URL
        self._headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }
        self._client = httpx.AsyncClient(
            headers=self._headers, timeout=10.0
        )
        self._price_cache: dict[str, list[PriceBar]] = {}
        self._quote_cache: dict[str, Quote] = {}
        log.info("MarketDataService initialized")

    async def get_quote(self, ticker: str) -> Optional[Quote]:
        """Fetch the latest quote for a ticker."""
        try:
            resp = await self._client.get(
                f"{self._base_url}/v2/stocks/{ticker}/quotes/latest"
            )
            resp.raise_for_status()
            data = resp.json().get("quote", {})
            quote = Quote(
                ticker=ticker,
                price=(data.get("ap", 0) + data.get("bp", 0)) / 2,
                bid=data.get("bp", 0),
                ask=data.get("ap", 0),
                volume=data.get("as", 0) + data.get("bs", 0),
                timestamp=datetime.utcnow(),
            )
            self._quote_cache[ticker] = quote
            return quote
        except Exception as e:
            log.error(f"Failed to get quote for {ticker}: {e}")
            return self._quote_cache.get(ticker)

    async def get_bars(
        self,
        ticker: str,
        timeframe: str = "1Day",
        limit: int = 100,
    ) -> list[PriceBar]:
        """Fetch historical OHLCV bars."""
        try:
            end = datetime.utcnow()
            start = end - timedelta(days=limit * 2)  # buffer for weekends
            resp = await self._client.get(
                f"{self._base_url}/v2/stocks/{ticker}/bars",
                params={
                    "timeframe": timeframe,
                    "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "limit": limit,
                    "feed": "iex",
                },
            )
            resp.raise_for_status()
            bars_data = resp.json().get("bars", [])
            bars = [
                PriceBar(
                    timestamp=datetime.fromisoformat(b["t"].replace("Z", "")),
                    open=b["o"],
                    high=b["h"],
                    low=b["l"],
                    close=b["c"],
                    volume=b["v"],
                )
                for b in bars_data
            ]
            self._price_cache[ticker] = bars
            log.info(f"Fetched {len(bars)} bars for {ticker}")
            return bars
        except Exception as e:
            log.error(f"Failed to get bars for {ticker}: {e}")
            return self._price_cache.get(ticker, [])

    async def get_options_chain(
        self,
        ticker: str,
        expiration_gte: Optional[str] = None,
    ) -> list[OptionContract]:
        """Fetch the options chain for a ticker."""
        try:
            params = {
                "underlying_symbols": ticker,
                "feed": "indicative",
                "limit": 100,
            }
            if expiration_gte:
                params["expiration_date_gte"] = expiration_gte

            resp = await self._client.get(
                f"{self._base_url}/v1beta1/options/snapshots/{ticker}",
                params=params,
            )
            resp.raise_for_status()
            snapshots = resp.json().get("snapshots", {})

            contracts = []
            for symbol, snap in snapshots.items():
                greeks = snap.get("greeks", {})
                latest = snap.get("latestQuote", {})
                trade = snap.get("latestTrade", {})
                contracts.append(OptionContract(
                    ticker=ticker,
                    contract_symbol=symbol,
                    strike=snap.get("strikePrice", 0),
                    expiration=snap.get("expirationDate", ""),
                    option_type=snap.get("type", "call"),
                    bid=latest.get("bp", 0),
                    ask=latest.get("ap", 0),
                    last_price=trade.get("p", 0),
                    volume=trade.get("s", 0),
                    open_interest=snap.get("openInterest", 0),
                    implied_volatility=greeks.get("impliedVolatility", 0),
                    delta=greeks.get("delta", 0),
                    gamma=greeks.get("gamma", 0),
                    theta=greeks.get("theta", 0),
                    vega=greeks.get("vega", 0),
                ))
            log.info(
                f"Fetched {len(contracts)} option contracts for {ticker}"
            )
            return contracts
        except Exception as e:
            log.error(f"Failed to get options chain for {ticker}: {e}")
            return []

    async def get_account(self) -> dict:
        """Fetch account info (balance, buying power, etc.)."""
        try:
            resp = await self._client.get(
                f"{self._trading_url}/v2/account"
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"Failed to get account info: {e}")
            return {}

    async def close(self):
        """Clean up HTTP client."""
        await self._client.aclose()
