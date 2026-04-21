"""
Web dashboard — FastAPI application serving the trading dashboard
at localhost. Provides real-time status, signal feed, and controls.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from config import settings

app = FastAPI(
    title="Trading Dashboard",
    description="Self-hosted algorithmic trading monitor",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def get_app() -> FastAPI:
    """Return the configured FastAPI app instance."""
    from src.dashboard.routes import register_routes
    register_routes(app)
    return app
