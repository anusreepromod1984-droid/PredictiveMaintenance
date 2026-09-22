"""
APMS Event & Diagnostic Historian Service
Persists Agent Alpha sensor halt events and end-to-end diagnosis runs with unique trace IDs.
Provides compliance audit trails and root-cause post-mortem history.
"""

import os
import json
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("Services.EventHistorian")

EVENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "events")


class EventHistorianService:
    """Audit historian for hardware cable faults, sensor halts, and AI diagnostic runs."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(EventHistorianService, cls).__new__(cls)
                cls._instance._init_service()
            return cls._instance

    def _init_service(self):
        self.events_dir = os.path.abspath(EVENTS_DIR)
        os.makedirs(self.events_dir, exist_ok=True)
        self._mem_events: Dict[str, List[Dict[str, Any]]] = {}
        self._mem_diagnoses: Dict[str, List[Dict[str, Any]]] = {}
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
                logger.info("[EventHistorian] Connected to Redis event list backend.")
            except Exception as exc:
                logger.warning(f"[EventHistorian] Redis unavailable ({exc}). Using local JSONL and memory fallback.")
                self._redis_client = None

    def _append_jsonl(self, filename: str, record: Dict[str, Any]):
        path = os.path.join(self.events_dir, filename)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            logger.error(f"[EventHistorian] Failed writing to {path}: {exc}")

    def persist_halt_event(self, trace_id: str, machine_id: str, cable_check: Any) -> Dict[str, Any]:
        """
        Persists a sensor cable break, ADC lock, or plausibility halt event.
        """
        status_val = getattr(cable_check, "status", str(cable_check))
        reason = getattr(cable_check, "fault_reason", None) or "Sensor signal rejected by Agent Alpha"
        suppressed = getattr(cable_check, "alert_suppressed", True)

        event = {
            "event_type": "SENSOR_HALT",
            "trace_id": trace_id,
            "machine_id": machine_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status_val,
            "fault_reason": reason,
            "false_alarm_suppressed": suppressed,
        }

        # 1. In-memory ring
        with self._mem_lock:
            buf = self._mem_events.setdefault(machine_id, [])
            buf.append(event)
            if len(buf) > 100:
                buf.pop(0)

        # 2. Redis list
        if self._redis_client:
            try:
                key = f"{settings.REDIS_KEY_PREFIX}:events:{machine_id}"
                self._redis_client.lpush(key, json.dumps(event))
                self._redis_client.ltrim(key, 0, 99)
            except Exception as exc:
                logger.warning(f"[EventHistorian] Redis lpush error: {exc}")

        # 3. File logging
        self._append_jsonl(f"{machine_id}_sensor_events.jsonl", event)
        logger.info(f"[EventHistorian] Persisted sensor halt event for {machine_id} (Trace: {trace_id}, Status: {status_val})")
        return event

    @staticmethod
    def _is_alert_or_fault(record: Dict[str, Any]) -> bool:
        """Returns True if the record represents an active defect, alert, or fault."""
        defect = record.get("defect_code")
        status = record.get("health_status")
        wo_id = record.get("work_order_id")
        event_type = record.get("event_type")

        if event_type in ("DIAGNOSIS_FAULT", "SENSOR_HALT"):
            return True
        if defect not in ("NORMAL", None, ""):
            return True
        if status not in ("HEALTHY", "NORMAL", "RESOLVED", None):
            return True
        if wo_id is not None:
            return True
        return False

    def _filter_incident_history(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filters diagnostic history to strictly preserve ONLY active alerts and faults.
        All NORMAL, HEALTHY, BASELINE, and RESOLVED records are excluded.
        """
        if not records:
            return []
        return [rec for rec in records if self._is_alert_or_fault(rec)]

    def persist_diagnosis_run(self, trace_id: str, machine_id: str, response: Any) -> Optional[Dict[str, Any]]:
        """
        Persists an end-to-end APMS diagnostic run.
        STRICTLY records only alerts and faults. Normal/healthy/resolved runs are suppressed
        so the diagnosis history strictly contains active defect and alert incidents.
        """
        defect_code = "NORMAL"
        component = "Baseline"
        rul_days = 999.0
        wo_id = None
        is_dup = False

        if hasattr(response, "defect_localization") and response.defect_localization:
            defect_code = response.defect_localization.defect_code
            component = response.defect_localization.failing_component

        if hasattr(response, "rul_prediction") and response.rul_prediction:
            rul_days = response.rul_prediction.rul_days

        if hasattr(response, "cmms_work_order") and response.cmms_work_order:
            wo_id = response.cmms_work_order.work_order_id
            is_dup = getattr(response.cmms_work_order, "is_duplicate", False)

        health_status = getattr(response, "overall_health_status", "HEALTHY")
        health_score = getattr(response, "overall_health_score", 100.0)
        card_type = getattr(response, "card_type", None)
        cable_status = getattr(response.cable_check, "status", "VALID") if hasattr(response, "cable_check") and response.cable_check else "VALID"

        is_alert_or_fault = (
            defect_code not in ("NORMAL", None, "") or
            health_status not in ("HEALTHY", "NORMAL", "RESOLVED") or
            card_type in ("ALERT", "FAULT") or
            cable_status not in ("VALID", "STABLE", None) or
            wo_id is not None
        )

        # Strictly keep ONLY faults and alerts in diagnosis history
        if not is_alert_or_fault:
            logger.debug(f"[EventHistorian] Suppressed nominal run for {machine_id} — history strictly retains alerts and faults.")
            return None

        event_type = "DIAGNOSIS_FAULT"

        record = {
            "event_type": event_type,
            "trace_id": trace_id,
            "machine_id": machine_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "health_score": health_score,
            "health_status": health_status,
            "defect_code": defect_code,
            "failing_component": component,
            "rul_days": rul_days,
            "work_order_id": wo_id,
            "is_duplicate_wo": is_dup,
            "skip_reason": getattr(response, "skip_reason", None),
        }

        with self._mem_lock:
            buf = self._mem_diagnoses.setdefault(machine_id, [])
            buf.append(record)
            if len(buf) > 100:
                buf.pop(0)

        if self._redis_client:
            try:
                key = f"{settings.REDIS_KEY_PREFIX}:diagnoses:{machine_id}"
                self._redis_client.lpush(key, json.dumps(record))
                self._redis_client.ltrim(key, 0, 99)
            except Exception as exc:
                logger.warning(f"[EventHistorian] Redis lpush error: {exc}")

        self._append_jsonl(f"{machine_id}_diagnoses.jsonl", record)
        logger.info(f"[EventHistorian] Persisted fault/alert diagnosis event for {machine_id} (Status: {health_status}, Defect: {defect_code})")
        return record

    def get_recent_events(self, machine_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves recent sensor fault / halt events."""
        if self._redis_client:
            try:
                key = f"{settings.REDIS_KEY_PREFIX}:events:{machine_id}"
                items = self._redis_client.lrange(key, 0, limit - 1)
                if items:
                    return [json.loads(i) for i in items]
            except Exception:
                pass

        with self._mem_lock:
            items = list(self._mem_events.get(machine_id, []))
            return list(reversed(items[-limit:]))

    def get_recent_diagnoses(self, machine_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves recent diagnosis prediction runs (filtered to alerts, faults, and resolutions)."""
        raw_items: List[Dict[str, Any]] = []
        if self._redis_client:
            try:
                key = f"{settings.REDIS_KEY_PREFIX}:diagnoses:{machine_id}"
                raw = self._redis_client.lrange(key, 0, 99)
                if raw:
                    raw_items = [json.loads(i) for i in raw]
            except Exception:
                pass

        if not raw_items:
            with self._mem_lock:
                buf = list(self._mem_diagnoses.get(machine_id, []))
                raw_items = list(reversed(buf))

        filtered = self._filter_incident_history(raw_items)
        return filtered[:limit]


# Global singleton instance
event_historian = EventHistorianService()
