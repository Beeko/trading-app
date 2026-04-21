"""
Task scheduler — manages periodic jobs for data polling,
indicator recalculation, and end-of-day cleanup.
"""

import asyncio
from datetime import datetime, time
from typing import Callable, Awaitable
from src.utils.logger import get_logger

log = get_logger("scheduler")


class TaskScheduler:
    """Async scheduler that runs periodic tasks during market hours."""

    def __init__(self):
        self._tasks: list[dict] = []
        self._running = False
        self._task_handles: list[asyncio.Task] = []

    def add_task(
        self,
        name: str,
        func: Callable[[], Awaitable],
        interval_seconds: int,
        market_hours_only: bool = True,
    ):
        """Register a periodic task."""
        self._tasks.append({
            "name": name,
            "func": func,
            "interval": interval_seconds,
            "market_hours_only": market_hours_only,
        })
        log.info(f"Registered task: {name} (every {interval_seconds}s)")

    @staticmethod
    def is_market_open() -> bool:
        """Check if US market is currently open (simplified, no holiday check)."""
        now = datetime.now()
        # Weekday check (0=Mon, 4=Fri)
        if now.weekday() > 4:
            return False
        market_open = time(9, 30)
        market_close = time(16, 0)
        return market_open <= now.time() <= market_close

    async def _run_task(self, task: dict):
        """Run a single task on its interval loop."""
        while self._running:
            try:
                if task["market_hours_only"] and not self.is_market_open():
                    await asyncio.sleep(30)  # check again in 30s
                    continue

                await task["func"]()
            except Exception as e:
                log.error(f"Task '{task['name']}' failed: {e}", exc_info=True)

            await asyncio.sleep(task["interval"])

    async def start(self):
        """Start all registered tasks."""
        self._running = True
        log.info(f"Starting scheduler with {len(self._tasks)} tasks")
        for task in self._tasks:
            handle = asyncio.create_task(self._run_task(task))
            self._task_handles.append(handle)

    async def stop(self):
        """Stop all running tasks gracefully."""
        log.info("Stopping scheduler...")
        self._running = False
        for handle in self._task_handles:
            handle.cancel()
        self._task_handles.clear()
        log.info("Scheduler stopped")
