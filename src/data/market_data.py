"""
Market data service — streams real-time quotes, pulls historical bars,
and fetches options chains from Alpaca's API.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
import httpx
from config import settings
from src.utils.logger import get_logger

log = get_logger("market_data")

_OCC_RE = re.compile(r"^[A-Z]{1,6}(\d{6})([CP])(\d{8})$")


def _parse_occ_symbol(symbol: str) -> tuple[float, str, str]:
    """
    Parse an OCC option symbol and return (strike, expiry, option_type).

    OCC format: {UNDERLYING}{YYMMDD}{C|P}{STRIKE*1000 padded to 8 digits}
    Example: AAPL240119C00150000 → (150.0, "2024-01-19", "call")
    Returns (0, "", "") if the symbol doesn't match the expected format.
    """
    m = _OCC_RE.match(symbol)
    if not m:
        return 0.0, "", ""
    date_raw = m.group(1)
    expiry = f"20{date_raw[:2]}-{date_raw[2:4]}-{date_raw[4:6]}"
    option_type = "call" if m.group(2) == "C" else "put"
    strike = int(m.group(3)) / 1000.0
    return strike, expiry, option_type


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
            headers=self._headers,
            timeout=httpx.Timeout(10.0, read=30.0),
        )
        self._price_cache: dict[str, list[PriceBar]] = {}
        self._quote_cache: dict[str, Quote] = {}
        log.info("MarketDataService initialized")

    async def get_quote(self, ticker: str) -> Optional[Quote]:
        """Fetch the latest quote for a ticker."""
        try:
            resp = await self._client.get(
                f"{self._base_url}/v2/stocks/{ticker}/quotes/latest",
                params={"feed": settings.ALPACA_DATA_FEED},
            )
            resp.raise_for_status()
            data = resp.json().get("quote", {})
            ask = data.get("ap", 0) or 0
            bid = data.get("bp", 0) or 0
            quote = Quote(
                ticker=ticker,
                price=(ask + bid) / 2 if (ask + bid) > 0 else 0,
                bid=bid,
                ask=ask,
                volume=data.get("as", 0) + data.get("bs", 0),
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
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
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=limit * 2)  # buffer for weekends/holidays
            resp = await self._client.get(
                f"{self._base_url}/v2/stocks/{ticker}/bars",
                params={
                    "timeframe": timeframe,
                    "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "limit": limit,
                    "feed": settings.ALPACA_DATA_FEED,
                    "sort": "asc",
                },
            )
            resp.raise_for_status()
            bars_data = resp.json().get("bars", [])
            bars = [
                PriceBar(
                    timestamp=datetime.fromisoformat(b["t"].replace("Z", "+00:00")).replace(tzinfo=None),
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
            params: dict = {"limit": 100}
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
                # Strike, expiry, and type must be parsed from the OCC symbol.
                # Alpaca's snapshot body does not include them as fields.
                # OCC format: UNDERLYING YYMMDD C/P STRIKE*1000 (8 digits)
                # Example: AAPL240119C00150000 → call, $150.00, 2024-01-19
                strike, expiry, option_type = _parse_occ_symbol(symbol)
                if strike == 0:
                    continue

                greeks = snap.get("greeks", {})
                latest = snap.get("latestQuote", {})
                trade = snap.get("latestTrade", {})

                contracts.append(OptionContract(
                    ticker=ticker,
                    contract_symbol=symbol,
                    strike=strike,
                    expiration=expiry,
                    option_type=option_type,
                    bid=latest.get("bp", 0) or 0,
                    ask=latest.get("ap", 0) or 0,
                    last_price=trade.get("p", 0) or 0,
                    volume=trade.get("s", 0) or 0,
                    open_interest=snap.get("openInterest", 0) or 0,
                    # impliedVolatility is a top-level field, not inside greeks
                    implied_volatility=snap.get("impliedVolatility", 0) or 0,
                    delta=greeks.get("delta", 0) or 0,
                    gamma=greeks.get("gamma", 0) or 0,
                    theta=greeks.get("theta", 0) or 0,
                    vega=greeks.get("vega", 0) or 0,
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
