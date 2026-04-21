# Trading App

A self-hosted, Docker-based algorithmic trading application with goal-based position sizing, multi-indicator signal confluence, news sentiment analysis, and comprehensive risk management.

## Overview

This application monitors the stock market, analyzes technical indicators and news sentiment, generates trade signals when multiple sources agree, and executes trades through a broker API — all gated by a strict risk management layer that protects your capital.

**Key design principles:**
- **Paper trading first** — start in simulation mode, validate your strategy with real data before risking money
- **Confluence-based signals** — no trade fires on a single indicator; multiple must agree
- **Anti-loss-chasing** — when behind the daily goal, the system gets *more* conservative, not less
- **Hard circuit breaker** — daily loss limit that cannot be overridden mid-session
- **Only trades when running** — `docker compose up` starts it, `docker compose down` stops everything

## Quick start

### 1. Clone and configure

```bash
cp .env.example .env
# Edit .env with your API keys and risk parameters
```

### 2. Run with Docker

```bash
docker compose up --build
```

The dashboard will be available at `http://localhost:3000`.

### 3. Stop trading

```bash
docker compose down
```

## Architecture

```
Data Sources          Analysis              Strategy           Risk              Execution
─────────────       ──────────────        ────────────       ──────────        ────────────
Market Data  ──►    Tech Indicators ──►   Signal      ──►   Risk       ──►   Paper Trader
News APIs    ──►    Sentiment Engine──►   Confluence        Manager           Alpaca API
Social Feeds ──►    Stock Scanner   ──►   Goal Engine       Circuit Breaker
```

### Layers

**Data** (`src/data/`) — Connects to Alpaca for market data, Finnhub for news, Reddit/Stocktwits for social sentiment.

**Analysis** (`src/analysis/`) — Computes MACD, RSI, Bollinger Bands, VWAP, and SMA crossovers. Runs sentiment analysis on news and social posts. Scans for trending stocks.

**Strategy** (`src/strategy/`) — The confluence engine requires 2+ indicators to agree before generating a signal. The goal engine manages daily profit targets and adjusts position sizing based on progress. The options module maps signals to safe, defined-risk options strategies.

**Risk** (`src/risk/`) — Every proposed trade passes through composable validator rules (position size, sector exposure, duplicate checks, etc.) and the circuit breaker. No trade reaches execution without risk manager approval.

**Execution** (`src/execution/`) — Abstract broker interface with paper trading and Alpaca implementations. Swap brokers by changing one config value.

**Dashboard** (`src/dashboard/`) — FastAPI web UI at localhost:3000 for monitoring P&L, reviewing signals, managing the watchlist, and approving trades.

## Configuration

All configuration is in `.env`. Key settings:

| Variable | Default | Description |
|---|---|---|
| `TRADING_MODE` | `paper` | `paper` for simulation, `live` for real trades |
| `RISK_PROFILE` | `conservative` | `conservative`, `moderate`, or `aggressive` |
| `DAILY_PROFIT_TARGET` | `200.00` | Daily profit goal in dollars |
| `DAILY_LOSS_LIMIT_PCT` | `2.0` | Circuit breaker threshold (% of account) |
| `MAX_SINGLE_TRADE_RISK_PCT` | `1.0` | Max risk per trade (% of account) |
| `MAX_OPEN_POSITIONS` | `10` | Maximum concurrent positions |

## Risk profiles

| Profile | Per-trade risk | Daily loss limit | Max positions |
|---|---|---|---|
| Conservative | 1% | 2% | 8 |
| Moderate | 2% | 4% | 12 |
| Aggressive | 3% | 6% | 15 |

## Supported options strategies

Only defined-risk strategies are allowed:
- Covered calls
- Cash-secured puts
- Bull call spreads
- Bear put spreads
- Iron condors
- Protective puts

Naked options, unlimited-risk strategies, and short-dated speculation are blocked at the code level.

## Running tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Dashboard API endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/status` | Current P&L, mode, positions, risk state |
| GET | `/api/signals` | Recent trade signals |
| POST | `/api/goal?target=300` | Update daily profit target |
| POST | `/api/approve/{id}` | Approve a pending signal |
| POST | `/api/reject/{id}` | Reject a pending signal |
| GET | `/api/watchlist` | Current watchlist |
| POST | `/api/watchlist/{ticker}` | Add ticker to watchlist |
| DELETE | `/api/watchlist/{ticker}` | Remove ticker |
| POST | `/api/close/{ticker}` | Close a specific position |
| POST | `/api/close-all` | Emergency: close all positions |
| GET | `/api/history` | Trade history |

## Development roadmap

- [ ] Backtesting engine — run strategies against historical data
- [ ] WebSocket real-time dashboard updates
- [ ] LLM-powered sentiment analysis (API integration)
- [ ] Multi-timeframe indicator analysis
- [ ] Sector classification for better exposure tracking
- [ ] Email/SMS alerts for circuit breaker events
- [ ] Performance analytics and reporting

## Disclaimer

This software is for educational and experimental purposes. Algorithmic trading carries significant financial risk. Always start with paper trading, validate your strategies thoroughly, and never risk money you cannot afford to lose. The authors are not financial advisors and this software does not constitute financial advice.
