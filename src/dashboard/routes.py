"""
Dashboard API routes — endpoints for monitoring status, reviewing
signals, approving trades, and adjusting settings.
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from datetime import datetime
from pathlib import Path

# These get populated by main.py at startup
_services = {}


def set_services(services: dict):
    """Called by main.py to inject service references."""
    global _services
    _services = services


def register_routes(app: FastAPI):
    """Register all API routes on the FastAPI app."""

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

    @app.get("/api/status")
    async def get_status():
        """Current system status: P&L, mode, positions, risk state."""
        goal_engine = _services.get("goal_engine")
        risk_manager = _services.get("risk_manager")
        broker = _services.get("broker")

        goal = goal_engine.get_goal() if goal_engine else None
        account = await broker.get_account() if broker else None
        positions = await broker.get_positions() if broker else {}
        risk_stats = risk_manager.get_daily_stats() if risk_manager else {}

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "trading_mode": goal.mode.value if goal else "inactive",
            "account": {
                "balance": account.balance if account else 0,
                "buying_power": account.buying_power if account else 0,
                "daily_pnl": account.daily_pnl if account else 0,
            } if account else None,
            "goal": {
                "target": goal.target,
                "progress_pct": round(goal.progress_pct, 1),
                "realized_pnl": round(goal.realized_pnl, 2),
                "unrealized_pnl": round(goal.unrealized_pnl, 2),
                "total_trades": goal.total_trades,
                "win_rate": round(goal.win_rate, 1),
            } if goal else None,
            "positions": {
                ticker: {
                    "qty": pos.quantity,
                    "avg_price": pos.avg_price,
                    "current_price": pos.current_price,
                    "pnl": pos.unrealized_pnl,
                    "pnl_pct": pos.pnl_pct,
                }
                for ticker, pos in positions.items()
            },
            "risk": risk_stats,
        }

    @app.get("/api/signals")
    async def get_signals():
        """Get recent trade signals (pending and historical)."""
        signal_log = _services.get("signal_log", [])
        return {
            "signals": [
                {
                    "timestamp": s.get("timestamp", ""),
                    "ticker": s.get("ticker", ""),
                    "direction": s.get("direction", ""),
                    "confidence": s.get("confidence", 0),
                    "reasoning": s.get("reasoning", []),
                    "status": s.get("status", "pending"),
                }
                for s in signal_log[-50:]  # last 50
            ]
        }

    @app.post("/api/goal")
    async def set_goal(target: float):
        """Update the daily profit target."""
        goal_engine = _services.get("goal_engine")
        if goal_engine:
            goal_engine.set_daily_target(target)
            return {"status": "ok", "new_target": target}
        return {"status": "error", "message": "Goal engine not available"}

    @app.post("/api/approve/{signal_id}")
    async def approve_signal(signal_id: str):
        """Approve a pending trade signal for execution."""
        pending = _services.get("pending_signals", {})
        if signal_id in pending:
            pending[signal_id]["approved"] = True
            return {"status": "approved", "signal_id": signal_id}
        return {"status": "error", "message": "Signal not found"}

    @app.post("/api/reject/{signal_id}")
    async def reject_signal(signal_id: str):
        """Reject a pending trade signal."""
        pending = _services.get("pending_signals", {})
        if signal_id in pending:
            pending[signal_id]["rejected"] = True
            return {"status": "rejected", "signal_id": signal_id}
        return {"status": "error", "message": "Signal not found"}

    @app.get("/api/watchlist")
    async def get_watchlist():
        """Get the current watchlist."""
        scanner = _services.get("scanner")
        if scanner:
            return {"watchlist": list(scanner._watchlist)}
        return {"watchlist": []}

    @app.post("/api/watchlist/{ticker}")
    async def add_to_watchlist(ticker: str):
        """Add a ticker to the watchlist."""
        scanner = _services.get("scanner")
        if scanner:
            scanner.add_to_watchlist(ticker.upper())
            return {"status": "added", "ticker": ticker.upper()}
        return {"status": "error"}

    @app.delete("/api/watchlist/{ticker}")
    async def remove_from_watchlist(ticker: str):
        """Remove a ticker from the watchlist."""
        scanner = _services.get("scanner")
        if scanner:
            scanner.remove_from_watchlist(ticker.upper())
            return {"status": "removed", "ticker": ticker.upper()}
        return {"status": "error"}

    @app.post("/api/close/{ticker}")
    async def close_position(ticker: str):
        """Close a position in a specific ticker."""
        broker = _services.get("broker")
        if broker:
            result = await broker.close_position(ticker.upper())
            if result:
                return {
                    "status": "closed",
                    "ticker": ticker.upper(),
                    "filled_price": result.filled_price,
                }
        return {"status": "error", "message": "Could not close position"}

    @app.post("/api/close-all")
    async def close_all():
        """Emergency: close all positions."""
        broker = _services.get("broker")
        if broker:
            results = await broker.close_all_positions()
            return {
                "status": "all_closed",
                "positions_closed": len(results),
            }
        return {"status": "error"}

    @app.get("/api/history")
    async def get_history():
        """Get trade history for today."""
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
                        "id": t.id,
                        "timestamp": t.timestamp.isoformat(),
                        "ticker": t.ticker,
                        "side": t.side,
                        "quantity": t.quantity,
                        "price": t.price,
                        "pnl": t.pnl,
                        "strategy": t.strategy,
                    }
                    for t in trades
                ]
            }
        finally:
            session.close()
