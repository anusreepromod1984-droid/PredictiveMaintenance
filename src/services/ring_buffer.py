"""
Durable per-machine ring buffer for Agent Alpha.

Redis LIST is the production store (shared across API workers and MQTT).
Falls back to process-local memory when Redis is disabled or unreachable.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Dict, List, Optional, Protocol

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("Services.RingBuffer")

_redis_store: Optional["RedisRingBuffer"] = None


def _safe_machine_id(machine_id: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in (machine_id or "default"))
    return cleaned[:128] or "default"


class RingBufferStore(Protocol):
    backend: str

    def ping(self) -> bool: ...
    def get_window(self, machine_id: str) -> List[Dict[str, Any]]: ...
    def append(self, machine_id: str, sample: Dict[str, Any]) -> None: ...
    def pop_last(self, machine_id: str) -> Optional[Dict[str, Any]]: ...
    def reset(self, machine_id: str) -> None: ...
    def length(self, machine_id: str) -> int: ...
    def set_baseline_id(self, machine_id: str, baseline_id: str) -> None: ...
    def get_baseline_id(self, machine_id: str) -> Optional[str]: ...


class MemoryRingBuffer:
    """Process-local deque. Isolated per instance; used when Redis is down."""

    backend = "memory"

    def __init__(self, window_size: int):
        self.window_size = window_size
        self._windows: Dict[str, deque] = {}
        self._baselines: Dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def _buf(self, machine_id: str) -> deque:
        mid = _safe_machine_id(machine_id)
        if mid not in self._windows:
            self._windows[mid] = deque(maxlen=self.window_size)
        return self._windows[mid]

    def get_window(self, machine_id: str) -> List[Dict[str, Any]]:
        return list(self._buf(machine_id))

    def append(self, machine_id: str, sample: Dict[str, Any]) -> None:
        self._buf(machine_id).append(sample)

    def pop_last(self, machine_id: str) -> Optional[Dict[str, Any]]:
        buf = self._buf(machine_id)
        if not buf:
            return None
        return buf.pop()

    def reset(self, machine_id: str) -> None:
        mid = _safe_machine_id(machine_id)
        self._windows[mid] = deque(maxlen=self.window_size)

    def length(self, machine_id: str) -> int:
        return len(self._buf(machine_id))

    def set_baseline_id(self, machine_id: str, baseline_id: str) -> None:
        self._baselines[_safe_machine_id(machine_id)] = baseline_id

    def get_baseline_id(self, machine_id: str) -> Optional[str]:
        return self._baselines.get(_safe_machine_id(machine_id))


class RedisRingBuffer:
    """Redis LIST ring buffer. Oldest at index 0, newest at the tail (RPUSH)."""

    backend = "redis"

    def __init__(self, client: Any, window_size: int, prefix: Optional[str] = None, ttl_seconds: Optional[int] = None):
        self.client = client
        self.window_size = window_size
        self.prefix = prefix or settings.REDIS_KEY_PREFIX
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.ALPHA_BUFFER_TTL_SECONDS

    def _buffer_key(self, machine_id: str) -> str:
        return f"{self.prefix}:alpha:buffer:{_safe_machine_id(machine_id)}"

    def _baseline_key(self, machine_id: str) -> str:
        return f"{self.prefix}:alpha:baseline:{_safe_machine_id(machine_id)}"

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def get_window(self, machine_id: str) -> List[Dict[str, Any]]:
        raw = self.client.lrange(self._buffer_key(machine_id), 0, -1)
        samples = []
        for item in raw:
            if isinstance(item, bytes):
                item = item.decode("utf-8")
            samples.append(json.loads(item))
        return samples

    def append(self, machine_id: str, sample: Dict[str, Any]) -> None:
        key = self._buffer_key(machine_id)
        payload = json.dumps(sample)
        pipe = self.client.pipeline()
        pipe.rpush(key, payload)
        pipe.ltrim(key, -self.window_size, -1)
        if self.ttl_seconds:
            pipe.expire(key, self.ttl_seconds)
        pipe.execute()

    def pop_last(self, machine_id: str) -> Optional[Dict[str, Any]]:
        raw = self.client.rpop(self._buffer_key(machine_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def reset(self, machine_id: str) -> None:
        pipe = self.client.pipeline()
        pipe.delete(self._buffer_key(machine_id))
        pipe.delete(self._baseline_key(machine_id))
        pipe.execute()

    def length(self, machine_id: str) -> int:
        return int(self.client.llen(self._buffer_key(machine_id)))

    def set_baseline_id(self, machine_id: str, baseline_id: str) -> None:
        key = self._baseline_key(machine_id)
        self.client.set(key, baseline_id, ex=self.ttl_seconds or None)

    def get_baseline_id(self, machine_id: str) -> Optional[str]:
        raw = self.client.get(self._baseline_key(machine_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)


def _connect_redis():
    import redis

    client = redis.Redis.from_url(
        settings.get_redis_url(),
        decode_responses=False,
        socket_connect_timeout=1.5,
        socket_timeout=1.5,
    )
    client.ping()
    return client


def create_alpha_store(window_size: Optional[int] = None) -> RingBufferStore:
    """Redis when reachable; otherwise a new in-process memory buffer."""
    global _redis_store
    size = window_size or settings.ALPHA_BUFFER_SIZE

    if not settings.REDIS_ENABLED:
        return MemoryRingBuffer(size)

    if _redis_store is not None and _redis_store.ping():
        return _redis_store

    try:
        client = _connect_redis()
        _redis_store = RedisRingBuffer(client, size)
        logger.info(f"[RingBuffer] Alpha window on Redis ({settings.get_redis_url()})")
        return _redis_store
    except Exception as exc:
        logger.warning(f"[RingBuffer] Redis unavailable ({exc}). Alpha buffer is process-local until Redis is up.")
        _redis_store = None
        return MemoryRingBuffer(size)


def redis_is_connected() -> bool:
    if not settings.REDIS_ENABLED:
        return False
    if _redis_store is not None:
        return _redis_store.ping()
    try:
        client = _connect_redis()
        client.close()
        return True
    except Exception:
        return False
