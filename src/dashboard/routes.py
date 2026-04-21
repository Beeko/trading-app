"""
Dashboard API routes — endpoints for monitoring status, reviewing
signals, approving trades, and adjusting settings.
"""

import asyncio
import os
import signal as signal_mod
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from config import settings
from src.utils.logger import get_logger

log = get_logger("routes")

# These get populated by main.py at startup
_services = {}


def set_services(services: dict):
    """Called by main.py to inject service references."""
    global _services
    _services = services


# ── serialization helpers ────────────────────────────────────────────
def _serialize_position(ticker: str, pos) -> dict:
    """Convert a Position dataclass into the dict shape the UI expects."""
    return {
        "ticker":        ticker,
        "quantity":      pos.quantity,
        "qty":           pos.quantity,          # back-compat
        "avg_price":     pos.avg_price,
        "current_price": pos.current_price,
        "market_value":  pos.market_value,
        "unrealized_pnl": pos.unrealized_pnl,
        "pnl":           pos.unrealized_pnl,    # back-compat
        "pnl_pct":       pos.pnl_pct,
        "side":          pos.side,
        "asset_type":    pos.asset_type,
    }


def _serialize_circuit_breaker(cb) -> dict:
    """Extract UI-friendly state from the CircuitBreaker."""
    if cb is None:
        return {
            "armed": True,
            "tripped": False,
            "consecutive_losses": 0,
            "max_consecutive": 5,
        }
    return {
        "armed":               not cb.is_tripped,
        "tripped":             cb.is_tripped,
        "consecutive_losses":  cb.state.consecutive_losses,
        "max_consecutive":     cb._max_consecutive,
        "daily_loss_limit":    cb.state.loss_limit,
        "remaining_budget":    cb.get_remaining_risk_budget(),
        "reason":              cb.state.reason,
    }


def register_routes(app: FastAPI):
    """Register all API routes on the FastAPI app."""

    # ── PAGE ─────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve the main dashboard page."""
        template = Path(__file__).parent / "templates" / "index.html"
        if template.exists():
            return template.read_text()
        return HTMLResponse(
            "<h1>Trading Dashboard</h1>"
            "<p>Dashboard UI loading...</p>"
            '<p><a href="/api/status">View API Status</a></p>'
        )

    # ── STATUS (primary polling endpoint) ────────────────────────────
    @app.get("/api/status")
    async def get_status():
        """Current system state: account, goal, positions, circuit breaker."""
        goal_engine     = _services.get("goal_engine")
        risk_manager    = _services.get("risk_manager")
        broker          = _services.get("broker")
        circuit_breaker = _services.get("circuit_breaker")

        goal       = goal_engine.get_goal()          if goal_engine  else None
        account    = await broker.get_account()      if broker       else None
        positions  = await broker.get_positions()    if broker       else {}
        risk_stats = risk_manager.get_daily_stats()  if risk_manager else {}

        current_pnl = (goal.realized_pnl + goal.unrealized_pnl) if goal else 0.0

        return {
            "timestamp":    datetime.utcnow().isoformat(),
            "mode":         settings.TRADING_MODE,
            "trading_mode": goal.mode.value if goal else "inactive",

            "account": {
                "balance":         account.balance,
                "buying_power":    account.buying_power,
                "equity":          account.equity,
                "cash":            account.cash,
                "positions_value": account.positions_value,
                "daily_pnl":       account.daily_pnl,
                "total_pnl":       account.total_pnl,
            } if account else None,

            "goal": {
                "target":         goal.target,
                "current":        round(current_pnl, 2),
                "progress_pct":   round(goal.progress_pct, 1),
                "realized_pnl":   round(goal.realized_pnl, 2),
                "unrealized_pnl": round(goal.unrealized_pnl, 2),
                "total_trades":   goal.total_trades,
                "win_rate":       round(goal.win_rate, 1),
            } if goal else None,

            "positions": {
                t: _serialize_position(t, p)
                for t, p in positions.items()
            },

            "circuit_breaker": _serialize_circuit_breaker(circuit_breaker),
            "risk":            risk_stats,
        }

    # ── SIGNALS (pending approval) ───────────────────────────────────
    @app.get("/api/signals")
    async def get_signals():
        """Signals awaiting human approval, shaped for the UI."""
        pending = _services.get("pending_signals", {})
        out = []
        for sig_id, sig in pending.items():
            if sig.get("approved") or sig.get("rejected"):
                continue
            direction = sig.get("direction") or sig.get("action") or ""
            out.append({
                "id":         sig_id,
                "timestamp":  sig.get("timestamp", ""),
                "ticker":     sig.get("ticker", ""),
                "action":     direction,
                "direction":  direction,
                "confidence": sig.get("confidence", 0),
                "confluence": sig.get("indicators_agreeing") or sig.get("confluence") or 0,
                "reasons":    sig.get("reasoning") or sig.get("reasons") or [],
                "strategy":   sig.get("strategy") or sig.get("asset_type") or "",
                "size":       sig.get("suggested_size_pct") or sig.get("size"),
                "status":     "pending",
            })
        return {"signals": out}

    # ── GOAL ─────────────────────────────────────────────────────────
    @app.post("/api/goal")
    async def set_goal(target: float):
        """Update the daily profit target."""
        goal_engine = _services.get("goal_engine")
        if goal_engine:
            goal_engine.set_daily_target(target)
            return {"status": "ok", "new_target": target}
        return {"status": "error", "message": "Goal engine not available"}

    # ── APPROVE / REJECT ─────────────────────────────────────────────
    @app.post("/api/approve/{signal_id}")
    async def approve_signal(signal_id: str):
        pending = _services.get("pending_signals", {})
        if signal_id in pending:
            pending[signal_id]["approved"] = True
            return {"status": "approved", "signal_id": signal_id}
        return {"status": "error", "message": "Signal not found"}

    @app.post("/api/reject/{signal_id}")
    async def reject_signal(signal_id: str):
        pending = _services.get("pending_signals", {})
        if signal_id in pending:
            pending[signal_id]["rejected"] = True
            return {"status": "rejected", "signal_id": signal_id}
        return {"status": "error", "message": "Signal not found"}

    # ── WATCHLIST ────────────────────────────────────────────────────
    @app.get("/api/watchlist")
    async def get_watchlist():
        """Return the watchlist as a flat list of tickers."""
        scanner = _services.get("scanner")
        if scanner:
            return list(scanner._watchlist)
        return []

    @app.post("/api/watchlist/{ticker}")
    async def add_to_watchlist(ticker: str):
        scanner = _services.get("scanner")
        if scanner:
            scanner.add_to_watchlist(ticker.upper())
            return {"status": "added", "ticker": ticker.upper()}
        return {"status": "error"}

    @app.delete("/api/watchlist/{ticker}")
    async def remove_from_watchlist(ticker: str):
        scanner = _services.get("scanner")
        if scanner:
            scanner.remove_from_watchlist(ticker.upper())
            return {"status": "removed", "ticker": ticker.upper()}
        return {"status": "error"}

    # ── POSITIONS ────────────────────────────────────────────────────
    @app.post("/api/close/{ticker}")
    async def close_position(ticker: str):
        broker = _services.get("broker")
        if broker:
            result = await broker.close_position(ticker.upper())
            if result:
                return {
                    "status":       "closed",
                    "ticker":       ticker.upper(),
                    "filled_price": result.filled_price,
                }
        return {"status": "error", "message": "Could not close position"}

    @app.post("/api/close-all")
    async def close_all():
        broker = _services.get("broker")
        if broker:
            results = await broker.close_all_positions()
            return {
                "status":           "all_closed",
                "positions_closed": len(results),
            }
        return {"status": "error"}

    # ── HISTORY ──────────────────────────────────────────────────────
    @app.get("/api/history")
    async def get_history():
        from src.utils.database import get_session, Trade
        session = get_session()
        try:
            trades = (
                session.query(Trade)
                .order_by(Trade.timestamp.desc())
                .limit(100)
                .all()
            )
            return {
                "trades": [
                    {
                        "id":        t.id,
                        "timestamp": t.timestamp.isoformat(),
                        "ticker":    t.ticker,
                        "side":      t.side,
                        "action":    t.side,        # UI alias
                        "quantity":  t.quantity,
                        "price":     t.price,
                        "pnl":       t.pnl,
                        "strategy":  t.strategy,
                    }
                    for t in trades
                ]
            }
        finally:
            session.close()

    # ── SHUTDOWN ─────────────────────────────────────────────────────
    @app.post("/api/shutdown")
    async def shutdown(close_positions: bool = False):
        """
        Gracefully shut down the trading terminal.

        close_positions=True   → liquidate all open positions first, then exit
        close_positions=False  → leave positions open, exit cleanly
        """
        broker      = _services.get("broker")
        trading_app = _services.get("app")
        closed_count = 0

        if close_positions and broker:
            try:
                log.warning("Shutdown requested — closing all positions first")
                results = await broker.close_all_positions()
                closed_count = len(results) if results else 0
                log.warning(f"Closed {closed_count} positions")
            except Exception as e:
                log.error(f"Error closing positions during shutdown: {e}")
        else:
            log.warning("Shutdown requested — positions preserved")

        # Clean up app state (scheduler, feeds, broker connection)
        if trading_app is not None:
            try:
                await trading_app.stop()
            except Exception as e:
                log.error(f"Error during app.stop(): {e}")

        # Fire SIGINT after the response has flushed, so uvicorn exits cleanly
        async def _terminate():
            await asyncio.sleep(0.5)
            log.warning("Sending SIGINT for final exit")
            os.kill(os.getpid(), signal_mod.SIGINT)

        asyncio.create_task(_terminate())

        return {
            "status":                  "shutting_down",
            "closed_positions":        close_positions,
            "positions_closed_count":  closed_count,
        }
