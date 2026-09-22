"""Closed-loop repair markers for the RUL history chart."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("Models.RepairEvents")


class RepairEventLog:
    _lock = threading.Lock()
    _memory: List[Dict[str, Any]] = []
    _redis = None
    _redis_ready = False

    def _client(self):
        if self._redis_ready:
            return self._redis
        self._redis_ready = True
        if not settings.REDIS_ENABLED:
            return None
        try:
            import redis
            client = redis.Redis.from_url(
                settings.get_redis_url(),
                decode_responses=True,
                socket_timeout=1.5,
            )
            client.ping()
            self._redis = client
        except Exception as exc:
            logger.warning("Repair event Redis unavailable (%s)", exc)
            self._redis = None
        return self._redis

    def _key(self, machine_id: str) -> str:
        return f"{settings.REDIS_KEY_PREFIX}:repair_events:{machine_id}"

    def record(
        self,
        machine_id: str,
        work_order_id: Optional[str] = None,
        at: Optional[datetime] = None,
        note: str = "Repair signed off",
    ) -> Dict[str, Any]:
        stamp = at or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        event = {
            "machine_id": machine_id,
            "work_order_id": work_order_id,
            "timestamp": int(stamp.timestamp() * 1000),
            "iso": stamp.isoformat(),
            "note": note,
            "kind": "repair",
        }
        client = self._client()
        if client is not None:
            try:
                client.lpush(self._key(machine_id), json.dumps(event))
                client.ltrim(self._key(machine_id), 0, 99)
            except Exception as exc:
                logger.warning("Repair event Redis write failed: %s", exc)
        with self._lock:
            self._memory = [row for row in self._memory if not (
                row.get("machine_id") == machine_id and row.get("timestamp") == event["timestamp"]
            )]
            self._memory.append(event)
        return event

    def list_for(self, machine_id: str, limit: int = 40) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        client = self._client()
        if client is not None:
            try:
                raw = client.lrange(self._key(machine_id), 0, max(0, limit - 1))
                for item in raw or []:
                    rows.append(json.loads(item))
            except Exception as exc:
                logger.debug("Repair event Redis read failed: %s", exc)
        if not rows:
            with self._lock:
                rows = [dict(row) for row in self._memory if row.get("machine_id") == machine_id]
        rows.sort(key=lambda row: int(row.get("timestamp") or 0))
        return rows[-limit:]


repair_events = RepairEventLog()
