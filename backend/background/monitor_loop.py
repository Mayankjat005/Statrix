# This file is a part of Statrix
# Coding : Priyanshu Dey [@irisXDR]

import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..cache import CacheUnavailableError
from ..config import settings
from ..database import GRACE_PERIOD_MINUTES, db
from ..utils.cache import invalidate_status_cache
from ..utils.email import send_down_alert, send_up_alert
from ..utils.time import utcnow

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}"

_alerted_monitors: set[str] = set()
_active_compression_jobs: set[str] = set()
_compression_job_lock = asyncio.Lock()

_COMPRESSION_JOB_SPACING_SECONDS = 120
_COMPRESSION_STARTUP_DELAY_SECONDS = 300
_COMPRESSION_DISPATCH_LOCK_TTL_SECONDS = 3600
_COMPRESSION_RECONCILE_GRACE_SECONDS = 180
_COMPRESSION_RECONCILE_JOB_ID = "compression_reconcile"
_COMPRESSION_STARTUP_JOB_ID = "compression_startup_recovery"


def _floor_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _single_worker_lock_fallback_allowed() -> bool:
    raw = (os.getenv("WEB_CONCURRENCY") or "").strip()
    if not raw:
        return True
    try:
        return int(raw) <= 1
    except ValueError:
        return True


def _monitor_key(monitor_id) -> str:
    return str(monitor_id)


def _monitor_target(monitor: dict, monitor_type: str) -> str:
    if monitor_type == "uptime":
        return monitor.get("target") or ""
    if monitor_type == "server":
        return monitor.get("hostname") or monitor.get("sid") or ""
    return monitor.get("sid") or ""


async def _probe_uptime_target(monitor: dict) -> bool:
    url = monitor.get("target", "")
    timeout_seconds = max(5.0, min(60.0, float(monitor.get("timeout") or 5)))
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=bool(monitor.get("follow_redirects", True)),
            verify=bool(monitor.get("verify_ssl", True)),
        ) as client:
            response = await client.get(url)
            elapsed_ms = None
            try:
                elapsed_ms = int(round(response.elapsed.total_seconds() * 1000))
            except Exception:
                logger.debug("Failed to compute response elapsed time for %s", url, exc_info=True)
            try:
                status_str = "up" if 200 <= response.status_code < 400 else "down"
                await db.create_uptime_check(
                    monitor["id"],
                    status=status_str,
                    response_time_ms=elapsed_ms,
                    status_code=response.status_code,
                )
            except Exception:
                logger.debug("Failed to record uptime check for monitor %s", monitor["id"], exc_info=True)
            return 200 <= response.status_code < 400
    except Exception:
        try:
            await db.create_uptime_check(
                monitor["id"], status="down", error_message="Connection failed"
            )
        except Exception:
            logger.debug("Failed to record down check after connection error for monitor %s", monitor["id"], exc_info=True)
        return False


async def _probe_all_uptime_monitors(monitors: list) -> dict[str, bool]:
    if not monitors:
        return {}
    tasks = []
    keys = []
    for m in monitors:
        key = _monitor_key(m["id"])
        keys.append(key)
        tasks.append(_probe_uptime_target(m))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return {keys[i]: (results[i] is True) for i in range(len(keys))}


async def _run_monitor_sweep() -> None:
    lock_acquired = False
    try:
        if settings.MONITOR_LEADER_LOCK_ENABLED and db.cache_service:
            try:
                lock_acquired = await db.cache_service.try_acquire_leader_lock(
                    lock_name="monitor_sweep",
                    owner=_INSTANCE_ID,
                    ttl_seconds=settings.MONITOR_LEADER_LOCK_TTL_SECONDS,
                )
            except CacheUnavailableError:
                if _single_worker_lock_fallback_allowed():
                    logger.warning(
                        "Proceeding without Redis leader lock for monitor sweep (single-worker fallback)"
                    )
                else:
                    logger.warning("Skipped monitor sweep: Redis leader lock unavailable")
                    return
            if not lock_acquired and not _single_worker_lock_fallback_allowed():
                logger.debug("Skipped monitor sweep on non-leader worker")
                return

        try:
            await db.ensure_cache_available()
        except CacheUnavailableError:
            logger.debug("Monitor sweep skipped: cache unavailable", exc_info=True)
            return

        now = utcnow()
        now_minute = _floor_to_minute(now)
        grace_delta = timedelta(minutes=GRACE_PERIOD_MINUTES)

        uptime_monitors = await db.get_uptime_monitors(enabled_only=True)
        server_monitors = await db.get_server_monitors(enabled_only=True)
        heartbeat_monitors = await db.get_heartbeat_monitors(enabled_only=True)

        probe_results = await _probe_all_uptime_monitors(uptime_monitors)

        all_monitors = []
        for m in uptime_monitors:
            all_monitors.append((m, "uptime", "website"))
        for m in server_monitors:
            all_monitors.append((m, "server", "heartbeat-server-agent"))
        for m in heartbeat_monitors:
            if m.get("heartbeat_type") == "server_agent":
                continue
            all_monitors.append((m, "heartbeat", "heartbeat-cronjob"))

        open_incidents = await db.get_open_incidents(source="monitor")
        incidents_by_monitor: dict[str, dict] = {}
        for inc in open_incidents:
            mid = inc.get("monitor_id")
            if mid:
                incidents_by_monitor[_monitor_key(mid)] = inc

        minute_records = []

        for monitor, db_type, display_type in all_monitors:
            monitor_id = monitor.get("id")
            if not monitor_id:
                continue

            key = _monitor_key(monitor_id)
            name = monitor.get("name") or str(monitor_id)
            target = _monitor_target(monitor, db_type)
            cache_kind = (
                "uptime"
                if db_type == "uptime"
                else ("server" if db_type == "server" else "heartbeat")
            )

            notifications_enabled = monitor.get("notifications_enabled", True)

            if monitor.get("maintenance_mode"):
                minute_records.append((monitor_id, now_minute, "maintenance"))
                _alerted_monitors.discard(key)
                continue

            if db_type == "uptime":
                is_up = probe_results.get(key, False)
                if is_up:
                    last_checkin = now_minute
                    current_status = monitor.get("status", "unknown")
                    if current_status == "down":
                        down_since = monitor.get("down_since") or now_minute
                        _alerted_monitors.discard(key)
                        existing = incidents_by_monitor.get(key)
                        if existing:
                            try:
                                await db.resolve_incident(existing["id"])
                                if notifications_enabled:
                                    await send_up_alert(
                                        monitor_name=name,
                                        monitor_type=display_type,
                                        target=target,
                                        down_since=down_since,
                                        recovered_at=now,
                                    )
                                logger.info("UP: %s recovered", name)
                            except Exception:
                                logger.exception(
                                    "Failed to resolve incident for %s", name
                                )
                    await db.update_monitor_status(
                        cache_kind,
                        monitor_id,
                        "up",
                        last_checkin_at=last_checkin,
                        down_since=None,
                    )
                    minute_records.append((monitor_id, now_minute, "up"))
                    continue

            live = await db.get_cached_monitor_state(cache_kind, monitor_id)
            last_checkin_at = live.get("last_checkin_at") if live else monitor.get("last_checkin_at")
            current_status = live.get("status", "unknown") if live else monitor.get("status", "unknown")

            if last_checkin_at is None:
                continue

            elapsed = now_minute - _floor_to_minute(last_checkin_at)

            if elapsed <= grace_delta:
                minute_records.append((monitor_id, now_minute, "up"))
                if current_status != "up":
                    await db.update_monitor_status(
                        cache_kind,
                        monitor_id,
                        "up",
                        last_checkin_at=last_checkin_at,
                        down_since=None,
                    )
            else:
                if current_status != "down":
                    down_since = _floor_to_minute(last_checkin_at) + grace_delta
                    transitioned = True
                    if db_type in {"server", "heartbeat"}:
                        transitioned = await db.mark_monitor_down_if_unchanged(
                            cache_kind=cache_kind,
                            monitor_id=monitor_id,
                            expected_last_checkin_at=last_checkin_at,
                            stale_before=now_minute - grace_delta,
                            down_since=down_since,
                        )
                    else:
                        await db.update_monitor_status(
                            cache_kind,
                            monitor_id,
                            "down",
                            last_checkin_at=last_checkin_at,
                            down_since=down_since,
                        )

                    if not transitioned:
                        continue

                    minute_records.append((monitor_id, now_minute, "down"))

                    if notifications_enabled and key not in _alerted_monitors:
                        existing = incidents_by_monitor.get(key)
                        if not existing:
                            try:
                                await db.create_incident(
                                    monitor_type=(
                                        "uptime" if db_type == "uptime" else "heartbeat"
                                    ),
                                    monitor_id=monitor_id,
                                    incident_type="down",
                                    title=f"{name} is down",
                                    description=f"{name} ({target}) stopped responding.",
                                    source="monitor",
                                )
                                await send_down_alert(
                                    monitor_name=name,
                                    monitor_type=display_type,
                                    target=target,
                                    down_since=down_since,
                                )
                                _alerted_monitors.add(key)
                                logger.info("DOWN: %s (email sent)", name)
                            except Exception:
                                logger.exception(
                                    "Failed to create DOWN incident for %s", name
                                )
                        else:
                            _alerted_monitors.add(key)
                else:
                    minute_records.append((monitor_id, now_minute, "down"))

        if minute_records:
            await db.write_monitor_minutes_batch(minute_records)

            if db.cache_service:
                today = now_minute.date()
                pipe = db.cache_service.backend.client.pipeline() if hasattr(db.cache_service.backend, 'client') else None
                for monitor_id, minute, status in minute_records:
                    monitor_id_str = str(monitor_id)
                    if pipe:
                        key = db.cache_service._k(f"daily:{monitor_id_str}:{today.isoformat()}")
                        pipe.hincrby(key, status, 1)
                        pipe.expire(key, 86400 * db.cache_service.daily_stats_ttl_days)
                    else:
                        await db.cache_service.increment_daily_stat(monitor_id_str, today, status)

                if pipe:
                    await pipe.execute()

        invalidate_status_cache()

    except Exception:
        logger.exception("Monitor sweep failed")
    finally:
        if lock_acquired and settings.MONITOR_LEADER_LOCK_ENABLED and db.cache_service:
            try:
                await db.cache_service.release_leader_lock(
                    lock_name="monitor_sweep",
                    owner=_INSTANCE_ID,
                )
            except Exception:
                logger.exception("Failed releasing monitor sweep leader lock")


def _compression_job_pending(job_id: str) -> bool:
    return job_id in _active_compression_jobs or (
        _scheduler is not None and _scheduler.get_job(job_id) is not None
    )


async def _run_monitor_compression_job(
    monitor_kind: str,
    monitor_id: str,
    window_start_iso: str,
    window_end_iso: str,
) -> None:
    job_id = db.make_compression_job_id(
        monitor_kind,
        monitor_id,
        datetime.fromisoformat(window_start_iso),
        datetime.fromisoformat(window_end_iso),
    )
    _active_compression_jobs.add(job_id)
    try:
        async with _compression_job_lock:
            window_start = datetime.fromisoformat(window_start_iso)
            window_end = datetime.fromisoformat(window_end_iso)
            logger.info(
                "Compression job started kind=%s monitor=%s window=%s..%s",
                monitor_kind,
                monitor_id,
                window_start_iso,
                window_end_iso,
            )
            results = await db.compress_monitor_window(
                monitor_kind,
                monitor_id,
                window_start,
                window_end,
            )
            logger.info(
                "Compression job finished kind=%s monitor=%s window=%s..%s results=%s",
                monitor_kind,
                monitor_id,
                window_start_iso,
                window_end_iso,
                results,
            )
    except Exception:
        logger.exception(
            "Compression job failed kind=%s monitor=%s window=%s..%s",
            monitor_kind,
            monitor_id,
            window_start_iso,
            window_end_iso,
        )
    finally:
        _active_compression_jobs.discard(job_id)


async def _dispatch_data_compression(
    reason: str = "daily",
    schedule_reconcile: bool = True,
) -> int:
    if _scheduler is None:
        return 0

    lock_acquired = False
    try:
        if settings.MONITOR_LEADER_LOCK_ENABLED and db.cache_service:
            try:
                lock_acquired = await db.cache_service.try_acquire_leader_lock(
                    lock_name="data_compression_dispatch",
                    owner=_INSTANCE_ID,
                    ttl_seconds=_COMPRESSION_DISPATCH_LOCK_TTL_SECONDS,
                )
            except CacheUnavailableError:
                if _single_worker_lock_fallback_allowed():
                    logger.warning(
                        "Proceeding without Redis leader lock for compression dispatch (single-worker fallback)"
                    )
                else:
                    logger.warning("Skipped data compression dispatch: Redis leader lock unavailable")
                    return 0
            if not lock_acquired and not _single_worker_lock_fallback_allowed():
                logger.debug("Skipped data compression dispatch on non-leader worker")
                return 0

        cutoff = utcnow() - timedelta(days=settings.DATA_RETENTION_DAYS)
        jobs = await db.discover_overdue_compression_jobs(cutoff)
        if not jobs:
            logger.info("Data compression dispatch found no overdue jobs (reason=%s)", reason)
            return 0

        base_run_at = utcnow()
        scheduled_count = 0
        last_run_at: datetime | None = None
        for job in jobs:
            if _compression_job_pending(job.job_id):
                continue

            run_at = base_run_at + timedelta(
                seconds=scheduled_count * _COMPRESSION_JOB_SPACING_SECONDS
            )
            _scheduler.add_job(
                _run_monitor_compression_job,
                "date",
                run_date=run_at,
                id=job.job_id,
                kwargs={
                    "monitor_kind": job.monitor_kind,
                    "monitor_id": str(job.monitor_id),
                    "window_start_iso": job.window_start.isoformat(),
                    "window_end_iso": job.window_end.isoformat(),
                },
                max_instances=1,
                misfire_grace_time=max(300, _COMPRESSION_JOB_SPACING_SECONDS * 2),
            )
            scheduled_count += 1
            last_run_at = run_at

        if schedule_reconcile and last_run_at is not None:
            _scheduler.add_job(
                _dispatch_data_compression,
                "date",
                run_date=last_run_at + timedelta(seconds=_COMPRESSION_RECONCILE_GRACE_SECONDS),
                id=_COMPRESSION_RECONCILE_JOB_ID,
                kwargs={"reason": "reconcile", "schedule_reconcile": False},
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=max(300, _COMPRESSION_RECONCILE_GRACE_SECONDS),
            )

        logger.info(
            "Data compression dispatch scheduled %s jobs (reason=%s cutoff=%s)",
            scheduled_count,
            reason,
            cutoff.isoformat(),
        )
        return scheduled_count
    except CacheUnavailableError:
        logger.warning("Data compression dispatch skipped: cache unavailable (reason=%s)", reason)
        return 0
    except Exception:
        logger.exception("Data compression dispatch failed (reason=%s)", reason)
        return 0
    finally:
        if lock_acquired and settings.MONITOR_LEADER_LOCK_ENABLED and db.cache_service:
            try:
                await db.cache_service.release_leader_lock(
                    lock_name="data_compression_dispatch",
                    owner=_INSTANCE_ID,
                )
            except Exception:
                logger.exception("Failed releasing data compression dispatch leader lock")


async def _run_data_compression() -> None:
    await _dispatch_data_compression(reason="daily", schedule_reconcile=True)


def schedule_startup_compression_dispatch(delay_seconds: int = _COMPRESSION_STARTUP_DELAY_SECONDS) -> None:
    if _scheduler is None:
        return

    delay = max(1, int(delay_seconds or _COMPRESSION_STARTUP_DELAY_SECONDS))
    _scheduler.add_job(
        _dispatch_data_compression,
        "date",
        run_date=utcnow() + timedelta(seconds=delay),
        id=_COMPRESSION_STARTUP_JOB_ID,
        kwargs={"reason": "startup_recovery", "schedule_reconcile": True},
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=max(300, delay),
    )
    logger.info("Startup data compression recovery scheduled (+%ss)", delay)


def start_monitor_loop() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _run_monitor_sweep,
        "interval",
        seconds=60,
        id="monitor_sweep",
        max_instances=1,
        coalesce=True,
        next_run_time=utcnow(),  # run immediately on start
    )
    _scheduler.add_job(
        _run_data_compression,
        "cron",
        hour=settings.DATA_COMPRESSION_HOUR_UTC,
        minute=0,
        id="data_compression",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("Monitor loop started (interval=60s, grace=%dm)", GRACE_PERIOD_MINUTES)


def stop_monitor_loop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def handle_checkin(
    monitor_id, cache_kind: str, db_type: str, display_type: str, name: str, target: str
) -> None:
    now = utcnow()
    now_minute = _floor_to_minute(now)
    key = _monitor_key(monitor_id)

    monitor = await db.get_cached_monitor_state(cache_kind, monitor_id)
    current_status = monitor.get("status", "unknown")
    down_since = monitor.get("down_since")

    if current_status == "down":
        _alerted_monitors.discard(key)
        try:
            open_incidents = await db.get_open_incidents(source="monitor")
            for inc in open_incidents:
                if _monitor_key(inc.get("monitor_id")) == key:
                    await db.resolve_incident(inc["id"])
                    notifications_enabled = True
                    m = await db.get_cached_monitor_state(cache_kind, monitor_id)
                    if m:
                        notifications_enabled = m.get("notifications_enabled", True)
                    if notifications_enabled:
                        await send_up_alert(
                            monitor_name=name,
                            monitor_type=display_type,
                            target=target,
                            down_since=down_since or now,
                            recovered_at=now,
                        )
                    logger.info("UP: %s recovered (via check-in)", name)
                    break
        except Exception:
            logger.exception("Failed to resolve incident for %s", name)

    await db.update_monitor_status(
        cache_kind, monitor_id, "up", last_checkin_at=now_minute, down_since=None
    )

    await db.write_monitor_minute(monitor_id, now_minute, "up")

    invalidate_status_cache()
