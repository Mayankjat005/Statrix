# This file is a part of Statrix
# Coding : Priyanshu Dey [@irisXDR]

import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from typing import Any

from ..config import settings
from ..utils.time import utcnow
from .base import CacheBackend, CacheUnavailableError, SnapshotLoader
from .inmemory_backend import InMemoryCacheBackend
from .redis_backend import RedisCacheBackend

logger = logging.getLogger(__name__)


class CacheService:

    def __init__(self) -> None:
        backend_name = str(getattr(settings, "CACHE_BACKEND", "redis") or "redis").strip().lower()
        self.key_prefix = str(getattr(settings, "CACHE_KEY_PREFIX", "statrix:v1") or "statrix:v1")
        self.fail_fast = bool(getattr(settings, "CACHE_FAIL_FAST", True))
        self.warmup_full = bool(getattr(settings, "CACHE_WARMUP_FULL", True))
        raw_disabled_series = str(
            getattr(
                settings,
                "CACHE_DISABLED_SERIES",
                "server_history,heartbeat_pings,uptime_checks",
            )
            or ""
        )
        self.disabled_series: set[str] = {
            item.strip().lower()
            for item in raw_disabled_series.split(",")
            if item and item.strip()
        }
        self.daily_stats_ttl_days = max(
            400,
            int(getattr(settings, "DAILY_STATS_CACHE_TTL_DAYS", 400) or 400),
        )
        self.cache_backend_name = backend_name
        self.connected = False
        self.healthy = False
        self.last_error: str | None = None
        self.loaded_at: str | None = None
        self._last_ping_ok_at: datetime | None = None
        self._ping_check_interval_seconds = 5

        if backend_name == "redis":
            redis_url = str(getattr(settings, "REDIS_URL", "") or "").strip()
            if not redis_url:
                # Compatibility fallbacks for common Upstash env var names.
                redis_url = str(
                    os.getenv("UPSTASH_REDIS_TLS_URL")
                    or os.getenv("UPSTASH_REDIS_URL")
                    or ""
                ).strip()
            if not redis_url:
                raise RuntimeError("CACHE_BACKEND=redis requires REDIS_URL")
            parsed = urlparse(redis_url)
            if parsed.scheme in {"http", "https"}:
                raise RuntimeError(
                    "REDIS_URL must be Redis protocol (redis:// or rediss://), "
                    "not HTTP REST URL. Use Upstash TLS endpoint."
                )
            if parsed.scheme not in {"redis", "rediss"}:
                raise RuntimeError(
                    f"Unsupported REDIS_URL scheme '{parsed.scheme}'. "
                    "Expected redis:// or rediss://"
                )
            self.backend: CacheBackend = RedisCacheBackend(
                redis_url=redis_url,
                key_prefix=self.key_prefix,
                warmup_batch_size=int(getattr(settings, "CACHE_WARMUP_BATCH_SIZE", 500) or 500),
            )
        else:
            self.backend = InMemoryCacheBackend()

    def _k(self, suffix: str) -> str:
        return f"{self.key_prefix}:{suffix}"

    def is_series_enabled(self, series_kind: str) -> bool:
        return str(series_kind or "").strip().lower() not in self.disabled_series

    def _ping_error_detail(self) -> str | None:
        if isinstance(self.backend, RedisCacheBackend):
            return self.backend.last_ping_error
        return None

    async def connect(self) -> None:
        await self.backend.connect()
        self.connected = await self.backend.ping()
        if not self.connected:
            self.healthy = False
            details = None
            if isinstance(self.backend, RedisCacheBackend):
                details = self.backend.last_ping_error
            self.last_error = "Cache ping failed" + (f": {details}" if details else "")
            if self.fail_fast:
                raise RuntimeError(self.last_error)
        else:
            self.healthy = True
            self.last_error = None
            self._last_ping_ok_at = utcnow()

    async def close(self) -> None:
        await self.backend.close()
        self.connected = False
        self.healthy = False

    async def purge_disabled_series(self) -> None:
        if not isinstance(self.backend, RedisCacheBackend):
            return
        for series_kind in sorted(self.disabled_series):
            try:
                await self.backend.purge_series_kind(series_kind)
            except Exception:
                logger.exception("Failed to purge disabled Redis series kind=%s", series_kind)

    async def warmup_from_loader(self, loader_fn: SnapshotLoader) -> None:
        try:
            if self.warmup_full:
                await self.backend.rebuild_from_db(loader_fn)
            self.loaded_at = utcnow().isoformat()
            self.healthy = True
            self.last_error = None
            await self.backend.set_json(
                self._k("meta:healthy"),
                {"healthy": True, "updated_at": self.loaded_at},
            )
            await self.backend.set_json(
                self._k("meta:loaded_at"),
                {"loaded_at": self.loaded_at},
            )
            await self.backend.delete_key(self._k("meta:last_error"))
        except Exception as exc:
            await self.mark_unhealthy(str(exc))
            raise

    async def mark_unhealthy(self, error: str) -> None:
        self.healthy = False
        self.last_error = str(error)
        self._last_ping_ok_at = None
        payload = {
            "healthy": False,
            "updated_at": utcnow().isoformat(),
        }
        try:
            await self.backend.set_json(self._k("meta:healthy"), payload)
            await self.backend.set_json(
                self._k("meta:last_error"),
                {"error": self.last_error, "updated_at": payload["updated_at"]},
            )
        except Exception:
            logger.exception("Failed to persist unhealthy cache metadata")

    async def mark_healthy(self) -> None:
        self.connected = True
        self.healthy = True
        self.last_error = None
        now = utcnow().isoformat()
        self.loaded_at = self.loaded_at or now
        self._last_ping_ok_at = utcnow()
        try:
            await self.backend.set_json(self._k("meta:healthy"), {"healthy": True, "updated_at": now})
            await self.backend.delete_key(self._k("meta:last_error"))
        except Exception:
            logger.exception("Failed to persist healthy cache metadata")

    async def ensure_available(self) -> None:
        if not self.fail_fast:
            return

        now = utcnow()
        if (
            self.connected
            and self.healthy
            and self._last_ping_ok_at
            and (now - self._last_ping_ok_at).total_seconds() < self._ping_check_interval_seconds
        ):
            return

        ok = await self.backend.ping()
        if not ok:
            # One quick retry to avoid flapping unhealthy on transient network blips.
            await asyncio.sleep(0.15)
            ok = await self.backend.ping()

        if ok:
            self.connected = True
            self._last_ping_ok_at = utcnow()
            if not self.healthy:
                await self.mark_healthy()
            return

        detail = self._ping_error_detail()
        error = "Cache ping failed" + (f": {detail}" if detail else "")
        self.connected = False
        await self.mark_unhealthy(error)
        raise CacheUnavailableError(error)

    async def stats(self) -> dict[str, Any]:
        backend_stats = await self.backend.stats()
        return {
            "backend": self.cache_backend_name,
            "connected": self.connected,
            "healthy": self.healthy,
            "last_error": self.last_error,
            "loaded_at": self.loaded_at,
            "counts": backend_stats.get("counts", {}),
        }

    def _status_live_key(self, cache_key: str) -> str:
        return self._k(f"status:live:{cache_key}")

    def _status_stale_key(self, cache_key: str) -> str:
        return self._k(f"status:stale:{cache_key}")

    def _status_keys_set(self) -> str:
        return self._k("status:keys")

    async def get_status_live(self, cache_key: str) -> dict[str, Any] | None:
        await self.ensure_available()
        return await self.backend.get_json(self._status_live_key(cache_key))

    async def set_status_live(self, cache_key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        await self.ensure_available()
        live_key = self._status_live_key(cache_key)
        stale_key = self._status_stale_key(cache_key)
        await self.backend.set_json(live_key, payload, ttl_seconds=max(0, int(ttl_seconds or 0)) or None)
        await self.backend.set_json(stale_key, payload)
        await self.backend.add_set_member(self._status_keys_set(), cache_key)

    async def get_status_stale(self, cache_key: str) -> dict[str, Any] | None:
        await self.ensure_available()
        return await self.backend.get_json(self._status_stale_key(cache_key))

    async def invalidate_status_cache(self) -> None:
        await self.ensure_available()
        set_key = self._status_keys_set()
        keys = await self.backend.get_set_members(set_key)
        for cache_key in keys:
            # Keep stale snapshots so public status can serve a fallback while
            # a fresh payload is being rebuilt.
            await self.backend.delete_key(self._status_live_key(cache_key))
            await self.backend.remove_set_member(set_key, cache_key)

    async def get_prefixed_json(self, suffix: str) -> dict[str, Any] | None:
        await self.ensure_available()
        return await self.backend.get_json(self._k(suffix))

    async def set_prefixed_json(
        self,
        suffix: str,
        payload: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> None:
        await self.ensure_available()
        await self.backend.set_json(self._k(suffix), payload, ttl_seconds=ttl_seconds)

    async def delete_prefixed_key(self, suffix: str) -> None:
        await self.ensure_available()
        await self.backend.delete_key(self._k(suffix))

    async def add_prefixed_set_member(self, suffix: str, member: str) -> None:
        await self.ensure_available()
        await self.backend.add_set_member(self._k(suffix), str(member))

    async def remove_prefixed_set_member(self, suffix: str, member: str) -> None:
        await self.ensure_available()
        await self.backend.remove_set_member(self._k(suffix), str(member))

    async def get_prefixed_set_members(self, suffix: str) -> set[str]:
        await self.ensure_available()
        return await self.backend.get_set_members(self._k(suffix))

    async def write_series_kind(
        self,
        series_kind: str,
        grouped: dict,
    ) -> int:
        if not self.is_series_enabled(series_kind):
            return 0
        await self.ensure_available()
        return await self.backend.write_series_kind(series_kind, grouped)

    async def write_warmup_meta(self, counts: dict[str, int]) -> None:
        if isinstance(self.backend, RedisCacheBackend):
            await self.backend._write_warmup_meta(counts)

    async def get_entity(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        await self.ensure_available()
        return await self.backend.get_entity(kind, str(entity_id))

    async def list_entities(self, kind: str) -> list[dict[str, Any]]:
        await self.ensure_available()
        return await self.backend.list_entities(kind)

    async def set_entity(self, kind: str, entity_id: str, value: dict[str, Any]) -> None:
        await self.ensure_available()
        await self.backend.set_entity(kind, str(entity_id), value)

    async def delete_entity(self, kind: str, entity_id: str) -> None:
        await self.ensure_available()
        await self.backend.delete_entity(kind, str(entity_id))

    async def get_index(self, index: str, key: str) -> str | None:
        await self.ensure_available()
        return await self.backend.get_index(index, str(key))

    async def set_index(self, index: str, key: str, value: str) -> None:
        await self.ensure_available()
        await self.backend.set_index(index, str(key), str(value))

    async def delete_index(self, index: str, key: str) -> None:
        await self.ensure_available()
        await self.backend.delete_index(index, str(key))

    async def append_series(
        self,
        series_kind: str,
        monitor_id: str,
        item: dict[str, Any],
        score: float,
        monitor_type: str | None = None,
    ) -> None:
        if not self.is_series_enabled(series_kind):
            return
        await self.ensure_available()
        await self.backend.append_series(series_kind, str(monitor_id), item, score, monitor_type=monitor_type)

    async def range_series(
        self,
        series_kind: str,
        monitor_id: str,
        start_score: float,
        end_score: float,
        limit: int | None = None,
        monitor_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.is_series_enabled(series_kind):
            return []
        await self.ensure_available()
        return await self.backend.range_series(series_kind, str(monitor_id), start_score, end_score, limit=limit, monitor_type=monitor_type)

    async def tail_series(
        self,
        series_kind: str,
        monitor_id: str,
        count: int,
        monitor_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.is_series_enabled(series_kind):
            return []
        await self.ensure_available()
        return await self.backend.tail_series(series_kind, str(monitor_id), count, monitor_type=monitor_type)

    async def delete_series_group(
        self,
        series_kind: str,
        monitor_id: str,
        monitor_type: str | None = None,
    ) -> None:
        if not self.is_series_enabled(series_kind):
            return
        await self.ensure_available()
        await self.backend.delete_series_group(series_kind, str(monitor_id), monitor_type=monitor_type)

    async def delete_series_range(
        self,
        series_kind: str,
        monitor_id: str,
        max_score: float,
        min_score: float | None = None,
        monitor_type: str | None = None,
    ) -> int:
        if not self.is_series_enabled(series_kind):
            return 0
        await self.ensure_available()
        return await self.backend.delete_series_range(
            series_kind,
            str(monitor_id),
            max_score,
            min_score=min_score,
            monitor_type=monitor_type,
        )

    async def update_series_item(
        self,
        series_kind: str,
        monitor_id: str,
        item: dict[str, Any],
        score: float,
        monitor_type: str | None = None,
    ) -> None:
        if not self.is_series_enabled(series_kind):
            return
        await self.ensure_available()
        await self.backend.update_series_item(series_kind, str(monitor_id), item, score, monitor_type=monitor_type)

    async def try_acquire_leader_lock(self, lock_name: str, owner: str, ttl_seconds: int) -> bool:
        if not isinstance(self.backend, RedisCacheBackend):
            return True
        await self.ensure_available()
        if self.backend.client is None:
            return False
        lock_key = self._k(f"lock:{lock_name}")
        try:
            acquired = await self.backend.client.set(
                lock_key,
                owner,
                nx=True,
                ex=max(1, int(ttl_seconds or 1)),
            )
            return bool(acquired)
        except Exception as exc:
            error = f"Leader lock write failed: {exc}"
            self.connected = False
            await self.mark_unhealthy(error)
            raise CacheUnavailableError(error) from exc

    async def release_leader_lock(self, lock_name: str, owner: str) -> None:
        if not isinstance(self.backend, RedisCacheBackend):
            return
        if self.backend.client is None:
            return
        lock_key = self._k(f"lock:{lock_name}")
        current = await self.backend.client.get(lock_key)
        if current == owner:
            await self.backend.client.delete(lock_key)

    async def increment_daily_stat(
        self,
        monitor_id: str,
        day: date | datetime,
        status: str
    ) -> None:
        """Increment daily status counter. Very cheap O(1) operation.

        This should be called after each monitor check to maintain
        pre-aggregated daily status counts for fast report loading.
        """
        if isinstance(day, datetime):
            day = day.date()
        elif not isinstance(day, date):
            raise TypeError(f"Expected date or datetime, got {type(day)}")

        if not isinstance(self.backend, RedisCacheBackend):
            return
        if self.backend.client is None:
            return

        key = self._k(f"daily:{monitor_id}:{day.isoformat()}")
        await self.backend.client.hincrby(key, status, 1)
        await self.backend.client.expire(key, 86400 * self.daily_stats_ttl_days)

    async def get_daily_stats(
        self,
        monitor_id: str,
        start_date: date | datetime,
        end_date: date | datetime
    ) -> dict[date, dict[str, int]]:
        """Get daily status stats for date range. Returns pre-aggregated data.

        Returns a dict mapping date to {"up": int, "down": int, "maintenance": int}.
        Dates with no data return zero counts.
        """
        if isinstance(start_date, datetime):
            start_date = start_date.date()
        if isinstance(end_date, datetime):
            end_date = end_date.date()
        if not isinstance(start_date, date) or not isinstance(end_date, date):
            raise TypeError("Expected date or datetime for start_date and end_date")

        try:
            from ..daily_stats import daily_stats_service
            await daily_stats_service.ensure_ready(self, timeout=0.1)
        except Exception:
            pass

        if not isinstance(self.backend, RedisCacheBackend):
            return {}

        if self.backend.client is None:
            return {}

        keys = []
        current = start_date
        while current <= end_date:
            keys.append(self._k(f"daily:{monitor_id}:{current.isoformat()}"))
            current += timedelta(days=1)

        if not keys:
            return {}

        pipe = self.backend.client.pipeline()
        for key in keys:
            pipe.hgetall(key)
        results = await pipe.execute()

        daily_map = {}
        current = start_date
        for result in results:
            if result:
                daily_map[current] = {
                    "up": int(result.get(b"up", 0)) if result.get(b"up") else 0,
                    "down": int(result.get(b"down", 0)) if result.get(b"down") else 0,
                    "maintenance": int(result.get(b"maintenance", 0)) if result.get(b"maintenance") else 0,
                }
                if not daily_map[current]["up"] and not daily_map[current]["down"] and not daily_map[current]["maintenance"]:
                    daily_map[current] = {
                        "up": int(result.get("up", 0)),
                        "down": int(result.get("down", 0)),
                        "maintenance": int(result.get("maintenance", 0)),
                    }
            else:
                daily_map[current] = {"up": 0, "down": 0, "maintenance": 0}
            current += timedelta(days=1)

        return daily_map

    async def get_multi_period_uptime_from_daily(
        self,
        monitor_id: str,
        first_data_at: "datetime | None"
    ) -> dict:
        """Calculate exact multi-period uptime from cached aggregates.

        Uses a hybrid strategy:
        - full days from cached daily stats
        - partial boundary windows from cached minute series

        Returns a dict with keys: "24h", "7d", "30d", "year", "total", "first_data_at"
        """
        now = utcnow()
        year_start = datetime(now.year, 1, 1)
        total_start = first_data_at or year_start

        period_starts: dict[str, datetime] = {
            "24h": now - timedelta(hours=24),
            "7d": now - timedelta(days=7),
            "30d": now - timedelta(days=30),
            "year": year_start,
            "total": total_start,
        }

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        minute_window_cache: dict[tuple[datetime, datetime], dict[str, int]] = {}
        cache_had_any_records = False

        async def _get_minute_counts(start: datetime, end: datetime) -> dict[str, int]:
            nonlocal cache_had_any_records

            if start >= end:
                return {"up": 0, "down": 0, "maintenance": 0}

            key = (start, end)
            if key in minute_window_cache:
                return minute_window_cache[key]

            items = await self.range_series(
                "monitor_minutes",
                monitor_id,
                start.timestamp(),
                end.timestamp(),
            )
            counts = {"up": 0, "down": 0, "maintenance": 0}
            for row in items:
                minute = row.get("minute")
                if minute is None or minute < start or minute >= end:
                    continue
                status = str(row.get("status") or "")
                if status in counts:
                    counts[status] += 1

            if counts["up"] or counts["down"] or counts["maintenance"]:
                cache_had_any_records = True
            minute_window_cache[key] = counts
            return counts

        # Fetch full-day stats once for the union range across all periods.
        daily_map: dict = {}
        full_day_spans: list[tuple] = []
        for start in period_starts.values():
            if start >= now or start.date() == now.date():
                continue
            middle_start_dt = start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            middle_end_dt = today_start - timedelta(days=1)
            if middle_start_dt <= middle_end_dt:
                full_day_spans.append((middle_start_dt.date(), middle_end_dt.date()))

        if full_day_spans:
            daily_start = min(span[0] for span in full_day_spans)
            daily_end = max(span[1] for span in full_day_spans)
            daily_map = await self.get_daily_stats(monitor_id, daily_start, daily_end)
            for stats in daily_map.values():
                if (stats.get("up", 0) + stats.get("down", 0) + stats.get("maintenance", 0)) > 0:
                    cache_had_any_records = True
                    break

        async def _calc_uptime(start: datetime) -> float:
            # Keep legacy semantics: if a period has no up/down samples, treat as 100%.
            if start >= now:
                return 100.0

            up = 0
            down = 0

            if start.date() == now.date():
                partial = await _get_minute_counts(start, now)
                up += partial["up"]
                down += partial["down"]
            else:
                # Start-day partial [start, next midnight)
                start_next_midnight = start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                start_partial = await _get_minute_counts(start, start_next_midnight)
                up += start_partial["up"]
                down += start_partial["down"]

                # Full middle days from daily cache.
                middle_start = start_next_midnight.date()
                middle_end = (today_start - timedelta(days=1)).date()
                if middle_start <= middle_end:
                    current = middle_start
                    while current <= middle_end:
                        stats = daily_map.get(current, {"up": 0, "down": 0, "maintenance": 0})
                        up += int(stats.get("up", 0) or 0)
                        down += int(stats.get("down", 0) or 0)
                        current += timedelta(days=1)

                # End-day partial [today midnight, now)
                end_partial = await _get_minute_counts(today_start, now)
                up += end_partial["up"]
                down += end_partial["down"]

            total = up + down
            return round((up / total) * 100, 4) if total > 0 else 100.0

        result = {
            "24h": await _calc_uptime(period_starts["24h"]),
            "7d": await _calc_uptime(period_starts["7d"]),
            "30d": await _calc_uptime(period_starts["30d"]),
            "year": await _calc_uptime(period_starts["year"]),
            "total": await _calc_uptime(period_starts["total"]),
            "first_data_at": first_data_at.isoformat() if first_data_at else None,
        }

        # If cache appears cold/incomplete, surface an unusable payload so callers can fall back.
        if not cache_had_any_records and first_data_at and (now - first_data_at) > timedelta(hours=1):
            logger.debug("Exact uptime cache has no records for monitor %s; returning empty for fallback", monitor_id)
            return {
                "24h": None,
                "7d": None,
                "30d": None,
                "year": None,
                "total": None,
                "first_data_at": first_data_at.isoformat(),
            }

        return result

