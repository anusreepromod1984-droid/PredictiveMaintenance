"""Redis ring-buffer contract tests (fakeredis — no local Redis required)."""

import fakeredis

from src.agents.agent_alpha import AgentAlpha
from src.services.ring_buffer import RedisRingBuffer, MemoryRingBuffer
from tests.test_pipeline import _frame


def test_redis_ring_trims_to_window():
    client = fakeredis.FakeRedis()
    store = RedisRingBuffer(client, window_size=3, prefix="test", ttl_seconds=60)
    for i in range(5):
        store.append("m1", {"vibration": float(i), "temp_motor": 40.0, "current_ir": 10.0})
    window = store.get_window("m1")
    assert len(window) == 3
    assert [s["vibration"] for s in window] == [2.0, 3.0, 4.0]


def test_redis_pop_last_rolls_back_frozen_sample():
    client = fakeredis.FakeRedis()
    store = RedisRingBuffer(client, window_size=5, prefix="test", ttl_seconds=60)
    store.append("m1", {"vibration": 1.0})
    store.append("m1", {"vibration": 2.0})
    popped = store.pop_last("m1")
    assert popped["vibration"] == 2.0
    assert store.length("m1") == 1


def test_redis_reset_and_baseline():
    client = fakeredis.FakeRedis()
    store = RedisRingBuffer(client, window_size=5, prefix="test", ttl_seconds=60)
    store.append("skid-01", {"vibration": 3.0})
    store.reset("skid-01")
    store.set_baseline_id("skid-01", "skid-01-new")
    assert store.length("skid-01") == 0
    assert store.get_baseline_id("skid-01") == "skid-01-new"


def test_two_alpha_instances_share_redis_window():
    client = fakeredis.FakeRedis()
    store = RedisRingBuffer(client, window_size=30, prefix="shared", ttl_seconds=60)
    api_alpha = AgentAlpha(store=store)
    mqtt_alpha = AgentAlpha(store=store)
    api_alpha.process(_frame(machineId="equipment_1", imuAcceleration=2.1))
    mqtt_alpha.process(_frame(machineId="equipment_1", imuAcceleration=2.4))
    assert store.length("equipment_1") == 2
    window = store.get_window("equipment_1")
    assert [s["vibration"] for s in window] == [2.1, 2.4]


def test_memory_fallback_is_isolated_per_instance():
    a = AgentAlpha(store=MemoryRingBuffer(30))
    b = AgentAlpha(store=MemoryRingBuffer(30))
    a.process(_frame(machineId="equipment_1", imuAcceleration=2.0))
    assert a.store.length("equipment_1") == 1
    assert b.store.length("equipment_1") == 0
