# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

The application is designed to run in Docker — that is the only fully wired path. Local dev is supported for tests and isolated module work.

```bash
# Run the full app (build + dashboard at http://localhost:3000)
docker compose up --build
docker compose down

# Tests (use a local venv — pytest-asyncio is in requirements.txt)
pip install -r requirements.txt
pytest tests/ -v
pytest tests/test_signals.py::TestName -v   # single test
pytest tests/ -v -k "circuit"               # by keyword

# Run the entry point directly (requires .env populated)
python -m src.main
```

`.env` is required at the repo root (copy from `.env.example`). `config/settings.py` calls `load_dotenv()` at import time, so missing values silently fall back to defaults — verify config when behavior seems off.

## Architecture

The app is a layered pipeline orchestrated by `src/main.py`'s `TradingApp`. Data flows in one direction; the risk layer is the gate every trade must pass through.

```
Data → Analysis → Strategy (Signal + Goal) → Risk → Execution (Broker)
```

### How the pieces connect

`TradingApp.start()` (src/main.py) is the wiring diagram. Read it first when adding features — services are constructed in dependency order, then registered with the dashboard via `set_services()` and driven by `TaskScheduler` periodic jobs (`_poll_news`, `_poll_social`, `_scan_and_analyze`, `_update_account_state`).

The pipeline runs every `INDICATOR_RECALC_INTERVAL` seconds inside `_scan_and_analyze`:
1. `StockScanner.full_scan()` produces candidates.
2. `IndicatorEngine.compute_all()` builds an `IndicatorSnapshot` (MACD, RSI, Bollinger, VWAP, SMA cross).
3. `SentimentEngine.aggregate_sentiment()` aggregates recent news per ticker.
4. `SignalEngine.evaluate()` requires **confluence** — `min_confluence=2` indicators agreeing in the same direction with `strength > 0.1`. Fewer = no signal, no exception.
5. `RiskManager.evaluate()` runs the circuit breaker first, then every validator in `ALL_VALIDATORS` (src/risk/validators.py). All must pass.
6. Approved signals with `requires_approval=False` are executed; otherwise they sit in `pending_signals` for the dashboard to approve.

### Three concepts that matter when changing behavior

**Trading mode (src/strategy/goal_engine.py).** `GoalEngine._update_mode()` sets one of `CONSERVATIVE | NORMAL | PROTECT_GAINS | REDUCED | HALTED` based on progress toward the daily target. The mode drives a `position_size_multiplier` consumed by `SignalEngine.evaluate()` — and `SignalEngine` also enforces higher confidence thresholds in `PROTECT_GAINS`/`REDUCED`. **Critical invariant: when behind goal (`REDUCED`), position size shrinks. Never invert this — chasing losses is explicitly prohibited and `goal_chase_enabled=False` across all profiles in `config/risk_profiles.py`.**

**Circuit breaker (src/risk/circuit_breaker.py).** Hard safety rail. Trips on cumulative daily loss, consecutive losing trades, or single-trade loss. When tripped, it calls the callback registered via `circuit_breaker.on_trip(...)` in `main.py`, which puts `GoalEngine` into `HALTED` and zeros the size multiplier. Do not add ways to bypass or reset the breaker mid-session — the only legitimate reset is `reset_daily()` for a new day.

**Risk profile (config/risk_profiles.py).** `conservative | moderate | aggressive`. Selected via `RISK_PROFILE` env var. Profile defines per-trade risk %, position caps, sector exposure cap, max DTE for options, and whether earnings plays are allowed. Validators look up the active profile each call via `get_profile(settings.RISK_PROFILE)` — if you add a new risk dimension, add it to the `RiskProfile` dataclass and to every profile entry.

### Broker abstraction

`src/execution/broker.py` defines `BaseBroker` (abstract) plus shared dataclasses (`OrderRequest`, `OrderResult`, `Position`, `AccountInfo`). `PaperTrader` and `AlpacaBroker` implement it. `main.py` selects which based on `TRADING_MODE`. New broker integrations should subclass `BaseBroker` and be wired in `TradingApp.__init__`.

### Dashboard

FastAPI app in `src/dashboard/app.py`; routes in `src/dashboard/routes.py`. Routes access live services through the module-level `_services` dict populated by `set_services()` in `main.py` — there is no DI framework. Endpoints are listed in README.md; the static `index.html` at the repo root is served separately.

### Options strategies

Only defined-risk strategies are allowed (`ALLOWED_OPTIONS_STRATEGIES` in `config/settings.py`: covered call, cash-secured put, bull call/bear put spreads, iron condor, protective put). `OptionsStrategySelector` in `src/strategy/options.py` enforces this — naked options and unlimited-risk plays are blocked at the code level, not just by convention.

## Conventions worth knowing

- **Logging.** Get loggers via `from src.utils.logger import get_logger; log = get_logger("name")`. Structured fields go in `extra={"...": ...}`.
- **Async everywhere.** Brokers, data feeds, and scheduler tasks are async. Keep new I/O on the same model.
- **Imports.** Code imports settings as `from config import settings` (re-exported via the top-level `__init__.py`) and individual settings via `settings.NAME`.
- **No DB migrations framework.** `src/utils/database.py:init_db()` is called once at startup; SQLAlchemy models are simple. Treat schema changes carefully.
