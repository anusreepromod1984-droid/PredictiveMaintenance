"""
Open Work Order Tracker & De-Duplication Engine
Prevents duplicate SAP PM / Maximo maintenance tickets from being spammed
for the same ongoing defect until post-repair re-commissioning clears it.
"""

import json
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("Agents.OpenWOTracker")


class OpenWorkOrderTracker:
    """Thread-safe, Redis-backed (with in-memory fallback) Open Work Order Tracker."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(OpenWorkOrderTracker, cls).__new__(cls)
                cls._instance._init_tracker()
            return cls._instance

    def _init_tracker(self):
        self._memory_store: Dict[str, Dict[str, Any]] = {}
        self._mem_lock = threading.Lock()
        self._redis_client = None

        if settings.REDIS_ENABLED:
            try:
                import redis
                self._redis_client = redis.Redis.from_url(
                    settings.get_redis_url(),
                    decode_responses=True,
                    socket_timeout=1.5,
                )
                self._redis_client.ping()
                logger.info("[OpenWOTracker] Redis backend connected for distributed open WO tracking.")
            except Exception as exc:
                logger.warning(f"[OpenWOTracker] Redis connection unavailable ({exc}). Using in-memory fallback.")
                self._redis_client = None

    def _make_key(self, machine_id: str, defect_code: str) -> str:
        return f"{settings.REDIS_KEY_PREFIX}:open_wo:{machine_id}:{defect_code}"

    def has_open_work_order(self, machine_id: str, defect_code: str) -> Optional[Dict[str, Any]]:
        """
        Returns active open work order details if one already exists for this machine and defect.
        """
        if defect_code == "NORMAL":
            return None

        key = self._make_key(machine_id, defect_code)

        # 1. Try Redis
        if self._redis_client:
            try:
                raw = self._redis_client.get(key)
                if raw:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning(f"[OpenWOTracker] Redis get error: {exc}")

        # 2. In-memory fallback
        with self._mem_lock:
            mem_data = self._memory_store.get(f"{machine_id}:{defect_code}")
            if mem_data:
                return dict(mem_data)

        return None

    def register_open_work_order(self, machine_id: str, defect_code: str, work_order_data: Dict[str, Any]) -> None:
        """
        Registers a new open work order to suppress future duplicates.
        """
        if defect_code == "NORMAL":
            return

        record = {
            **work_order_data,
            "machine_id": machine_id,
            "defect_code": defect_code,
            "status": "OPEN",
            "registered_at": datetime.now(timezone.utc).isoformat()
        }

        key = self._make_key(machine_id, defect_code)
        ttl = 14 * 86400  # 14 days retention

        if self._redis_client:
            try:
                self._redis_client.set(key, json.dumps(record), ex=ttl)
            except Exception as exc:
                logger.warning(f"[OpenWOTracker] Redis set error: {exc}")

        with self._mem_lock:
            self._memory_store[f"{machine_id}:{defect_code}"] = record

        logger.info(f"[OpenWOTracker] Registered active open WO for {machine_id} ({defect_code}): {work_order_data.get('work_order_id')}")

    def mark_oem_mail_sent(self, machine_id: str, defect_code: str, sent_at: str) -> Optional[Dict[str, Any]]:
        existing = self.has_open_work_order(machine_id, defect_code)
        if not existing:
            return None
        mail = dict(existing.get("oem_mail") or {})
        mail["sent"] = True
        mail["sent_at"] = sent_at
        existing["oem_mail"] = mail
        self.register_open_work_order(machine_id, defect_code, existing)
        return existing

    def clear_open_work_order(self, machine_id: str, defect_code: Optional[str] = None) -> int:
        """
        Clears open work order(s) when machine is re-commissioned post-repair or reset.
        """
        cleared_count = 0

        with self._mem_lock:
            if defect_code:
                k = f"{machine_id}:{defect_code}"
                if k in self._memory_store:
                    del self._memory_store[k]
                    cleared_count += 1
            else:
                to_delete = [k for k in self._memory_store if k.startswith(f"{machine_id}:")]
                for k in to_delete:
                    del self._memory_store[k]
                    cleared_count += 1

        if self._redis_client:
            try:
                pattern = self._make_key(machine_id, defect_code or "*")
                keys = self._redis_client.keys(pattern)
                if keys:
                    self._redis_client.delete(*keys)
                    cleared_count = max(cleared_count, len(keys))
            except Exception as exc:
                logger.warning(f"[OpenWOTracker] Redis delete error: {exc}")

        logger.info(f"[OpenWOTracker] Cleared {cleared_count} open work order(s) for machine '{machine_id}'.")
        return cleared_count

    def list_open_work_orders(self, machine_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists open work orders from Redis (when connected) and the in-memory fallback."""
        results: List[Dict[str, Any]] = []
        seen: set[str] = set()

        if self._redis_client:
            try:
                pattern = (
                    f"{settings.REDIS_KEY_PREFIX}:open_wo:{machine_id}:*"
                    if machine_id
                    else f"{settings.REDIS_KEY_PREFIX}:open_wo:*"
                )
                for key in self._redis_client.keys(pattern):
                    raw = self._redis_client.get(key)
                    if not raw:
                        continue
                    row = json.loads(raw)
                    oid = str(row.get("work_order_id") or key)
                    if oid in seen:
                        continue
                    seen.add(oid)
                    results.append(row)
            except Exception as exc:
                logger.warning(f"[OpenWOTracker] Redis list error: {exc}")

        with self._mem_lock:
            for k, val in self._memory_store.items():
                if machine_id is not None and not k.startswith(f"{machine_id}:"):
                    continue
                oid = str(val.get("work_order_id") or k)
                if oid in seen:
                    continue
                seen.add(oid)
                results.append(val)
        return results

    def latest_open_work_order(self, machine_id: str) -> Optional[Dict[str, Any]]:
        rows = self.list_open_work_orders(machine_id=machine_id)
        if not rows:
            return None
        return rows[0]


# Global singleton instance
wo_tracker = OpenWorkOrderTracker()
