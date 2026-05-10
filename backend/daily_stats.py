# This file is a part of Statrix
# Coding : Priyanshu Dey [@irisXDR]

"""Daily stats aggregation service - pre-computes daily status for fast report loading."""

import asyncio
import logging
from datetime import date, datetime, time, timedelta

from .config import settings
from .utils.time import utcnow

logger = logging.getLogger(__name__)


class DailyStatsService:
    """Pre-computes daily status stats for fast report loading."""

    def __init__(self) -> None:
        self.enabled = bool(getattr(settings, "DAILY_STATS_WARMUP_ENABLED", True))
        self.warmup_delay = max(
            10, int(getattr(settings, "DAILY_STATS_WARMUP_DELAY_SECONDS", 70) or 70)
        )
        self.lookback_days = max(
            400,
            int(getattr(settings, "DAILY_STATS_WARMUP_LOOKBACK_DAYS", 400) or 400),
        )
        self.ttl_days = max(
            400,
            int(getattr(settings, "DAILY_STATS_CACHE_TTL_DAYS", 400) or 400),
        )

        self._ready = False
        self._warmup_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self, db, cache_service) -> None:
        """Start the service and schedule delayed warmup."""
        if not self.enabled or not cache_service:
            return

        self._stop_event.clear()
        self._schedule_warmup(db, cache_service)
        logger.info(
            "Daily stats service started (warmup=%ss, lookback=%s days)",
            self.warmup_delay,
            self.lookback_days,
        )

    async def stop(self) -> None:
        """Stop the service and cancel warmup."""
        self._stop_event.set()
        if self._warmup_task and not self._warmup_task.done():
            self._warmup_task.cancel()
            try:
                await self._warmup_task
            except asyncio.CancelledError:
                pass

    def _schedule_warmup(self, db, cache_service) -> None:
        """Schedule warmup task after delay."""
        if self._warmup_task and not self._warmup_task.done():
            return

        async def warmup_job():
            try:
                await asyncio.sleep(self.warmup_delay)
                await self.warmup_from_cache(db, cache_service)
            except asyncio.CancelledError:
                logger.debug("Daily stats warmup job cancelled")
                raise

        try:
            loop = asyncio.get_running_loop()
            self._warmup_task = loop.create_task(warmup_job())
        except RuntimeError:
            logger.warning("Failed to schedule daily stats warmup: no running event loop")

    async def warmup_from_cache(self, db, cache_service) -> bool:
        """Build daily stats from cached minute data."""
        if not self.enabled or not cache_service:
            return False

        if self._ready:
            return True

        started = utcnow()
        logger.info("Daily stats warmup started")

        try:
            monitors = []
            for kind in ("uptime", "server", "heartbeat"):
                try:
                    entities = await cache_service.list_entities(kind)
                    monitors.extend([(kind, m) for m in entities])
                except Exception as e:
                    logger.debug("Failed to list %s entities for daily stats warmup: %s", kind, e)

            processed = 0
            for kind, monitor in monitors:
                monitor_id = str(monitor["id"])
                created_at = monitor.get("created_at")

                now = utcnow()
                start_date = (now - timedelta(days=self.lookback_days)).date()
                if created_at:
                    created_date = created_at.date()
                    start_date = max(start_date, created_date)

                end_dt = datetime.combine(now.date(), time.max)
                start_dt = datetime.combine(start_date, time.min)

                try:
                    minutes = await cache_service.range_series(
                        "monitor_minutes", monitor_id,
                        start_dt.timestamp(), end_dt.timestamp()
                    )

                    daily_counts: dict[date, dict[str, int]] = {}
                    for minute in minutes:
                        minute_dt = minute.get("minute")
                        if not minute_dt:
                            continue
                        day = minute_dt.date()
                        if day not in daily_counts:
                            daily_counts[day] = {"up": 0, "down": 0, "maintenance": 0}
                        status = minute.get("status", "")
                        if status in daily_counts[day]:
                            daily_counts[day][status] += 1

                    if daily_counts:
                        pipe = cache_service.backend.client.pipeline()
                        for day, counts in daily_counts.items():
                            key = cache_service._k(f"daily:{monitor_id}:{day.isoformat()}")
                            for status, count in counts.items():
                                if count > 0:
                                    pipe.hset(key, status, count)
                            pipe.expire(key, 86400 * self.ttl_days)
                        await pipe.execute()
                        processed += 1

                except Exception as e:
                    logger.debug("Failed to warmup daily stats for monitor %s: %s", monitor_id, e)

            elapsed = (utcnow() - started).total_seconds()
            self._ready = True
            logger.info(
                "Daily stats warmup complete monitors=%s/%s duration=%.3fs",
                processed,
                len(monitors),
                elapsed,
            )
            return True

        except Exception as e:
            logger.exception("Daily stats warmup failed: %s", e)
            return False

    async def ensure_ready(self, cache_service, timeout: int = 1) -> bool:
        """Wait for warmup to complete (with short timeout)."""
        if not self.enabled:
            return True  # Disabled = ready
        if self._ready:
            return True

        if self._warmup_task and not self._warmup_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._warmup_task), timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass  # Timeout is OK - we'll use lazy fallback

        return self._ready


daily_stats_service = DailyStatsService()
