"""
Production Industrial MQTT Ingestion & Alert Service
Subscribes to live sensor MQTT streams (pdm/integrated_json, pdm/integrated_data) and executes Agent Alpha & LangGraph DAG.
"""

import hashlib
import json
import os
import time
import uuid
import math
import paho.mqtt.client as mqtt
from typing import Optional, Dict, Any, List, Tuple

from src.config import settings
from src.schemas.telemetry import TelemetryFrame, HarmonicPeak
from src.agents.orchestrator import APMSOrchestrator
from src.database.telemetry_store import persist_mqtt_frame
from src.utils.logger import get_logger

logger = get_logger("Services.MQTTIngestion")

# Display names only — never used to invent machines that MQTT/API have not seen.
ASSET_LABELS: Dict[str, Tuple[str, str]] = {
    "compressor_unit_01": ("Compressor Unit 01 — Elson EL30 (720 RPM, 3HP, Reciprocating Piston)", "Reciprocating Piston Air Compressor"),
    "motor_drive_02": ("Induction Motor Drive 02 (30 kW)", "Electric Motor"),
    "chiller_pump_03": ("Centrifugal Chiller Pump 03", "Centrifugal Pump"),
}


# Asset id for the live rig when its payloads carry no machineId. Matches the only
# machine already persisted in Postgres, so history and live frames stay one asset.
DEFAULT_LIVE_MACHINE_ID = "compressor_unit_01"

# The gateway publishes one physical rig as Product A/B/C fragments.  Subscribe to
# the component topics as well as the integrated snapshot; never fill a missing
# component from a catalog profile.
PRODUCT_TOPICS = (
    "pdm/integrated_json",
    "pdm/integrated_data",
    "pdm/vibration",
    "pdm/orientation",
    "pdm/temperature_noise",
    "pdm/electricity",
    "pdm/pressure_humidity",
    "pdm/motor_fault",
)
FIELD_TTL_SECONDS = 30.0
# ISO 20816-3 group-2 zone D is 7.1 mm/s. Live IMU-Acceleration on this rig is ~0.1–0.3 mm/s.
# Occasional gateway ticks publish ~1100–1875 (Hz-range leftovers) into the same field.
IMU_VELOCITY_HARD_MAX_MM_S = 40.0
# ISO 20816-3 group-2 flexible B/C is 4.5 mm/s. A jump 0.12 → 9.6 used to pass
# because the old spike floor was 15 mm/s; last_good then unlocked 19 mm/s axes.
IMU_VELOCITY_SPIKE_MM_S = 4.5
IMU_HEALTHY_BASELINE_MM_S = 2.5
IMU_SPIKE_CONFIRM_COUNT = 8
# Keep the last plausible IMU-Acceleration RMS from pdm/vibration alive across
# pdm/integrated_data packets so the 4-agent DAG doesn't treat every integrated
# snapshot as IMU absent (which disables ISO zone grading and pins RUL).
IMU_HOLD_SECONDS = 300.0
AXIS_CACHE_KEYS = {
    "_axisX", "_axisY", "_axisZ",
    "xAxisVibration", "yAxisVibration", "zAxisVibration",
}


def _asset_label(machine_id: str) -> Tuple[str, str]:
    if machine_id in ASSET_LABELS:
        return ASSET_LABELS[machine_id]
    return machine_id.replace("_", " ").title(), "Industrial Asset"


def _mqtt_client_id() -> str:
    """Broker-safe unique id. A shared '{APP_NAME}_mqtt_client' lets another APMS steal this session."""
    return f"apms-{os.getpid()}-{uuid.uuid4().hex[:10]}"


class MQTTIngestionService:
    """
    Asynchronous MQTT Listener client for live IoT sensor telemetry.
    Configured via .env environment variables (MQTT_BROKER_HOST, MQTT_BROKER_PORT).
    """

    def __init__(self, orchestrator: Optional[APMSOrchestrator] = None):
        self.orchestrator = orchestrator or APMSOrchestrator()
        self._client_id = _mqtt_client_id()

        try:
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self._client_id,
                protocol=mqtt.MQTTv311,
                clean_session=True,
            )
        except AttributeError:
            self.client = mqtt.Client(client_id=self._client_id, clean_session=True)

        if settings.MQTT_USERNAME and settings.MQTT_PASSWORD:
            self.client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)

        self.client.reconnect_delay_set(min_delay=1, max_delay=12)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.is_connected = False
        self.discovered_assets: Dict[str, Dict[str, Any]] = {}
        self.latest_telemetry: Dict[str, Dict[str, Any]] = {}
        self._recent_payloads: Dict[str, Tuple[str, float]] = {}
        self._field_cache: Dict[str, Dict[str, Tuple[Any, float]]] = {}
        self._runtime_hours: Dict[str, float] = {}
        self._runtime_last_mono: Dict[str, float] = {}
        self._runtime_last_energy: Dict[str, float] = {}
        self._energy_history: Dict[str, List[Tuple[float, float]]] = {}
        self._estimated_kw: Dict[str, float] = {}
        self._last_rul_hours: Dict[str, float] = {}
        self._last_diagnosis_fault: Dict[str, Dict[str, Any]] = {}
        self._last_diagnosis: Dict[str, Dict[str, Any]] = {}
        self._last_orchestrator_run: Dict[str, float] = {}
        self._last_good_imu: Dict[str, float] = {}
        self._last_good_imu_at: Dict[str, float] = {}
        self._last_good_axes: Dict[str, Tuple[float, float, float]] = {}
        self._last_good_axes_at: Dict[str, float] = {}
        self._imu_seed_attempted: set[str] = set()
        self._imu_spike_count: Dict[str, int] = {}
        self._last_imu_reject_at = 0.0
        self.last_message_at_ms: Optional[int] = None
        self._last_parse_error_at = 0.0
        # ── Fault-debounce constants ──────────────────────────────────────────────────
        # ISO 13849 and good manufacturing practice both require that safety-
        # relevant alarms persist long enough for the operator to notice them.
        # 30 seconds prevents the UI from flickering when one MQTT packet in a
        # burst carries stale/NORMAL data while the fault is still active.
        self._FAULT_MIN_HOLD_SECONDS: float = 30.0
        # Three consecutive NORMAL results from the orchestrator are required
        # before a held fault is cleared.  This absorbs bursts of alternating
        # FAULT/NORMAL results that are common in noisy RF environments.
        self._NORMAL_CONFIRM_REQUIRED: int = 3
        # Tracks how many consecutive NORMAL orchestrator results have arrived
        # while a fault is still being held by the debounce window.
        self._normal_confirm_count: Dict[str, int] = {}

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        """Callback executed upon connecting to MQTT broker."""
        rc = getattr(reason_code, "value", reason_code)
        if rc == 0:
            self.is_connected = True
            logger.info(
                f"[MQTT Engine] Connected to {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT} "
                f"as client_id={self._client_id}"
            )
            topics = self._subscribe_topics()
            client.subscribe([(topic, 0) for topic in topics])
            logger.info(f"[MQTT Engine] Subscribed to live topics: {topics}")
            try:
                from src.api.socketio_server import emit_upstream_sync
                emit_upstream_sync()
            except Exception:
                pass
        else:
            logger.error(f"[MQTT Engine] Connection to {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT} refused with Code {rc}.")

    def _on_disconnect(self, client, userdata, flags=None, reason_code=0, properties=None):
        self.is_connected = False
        rc = getattr(reason_code, "value", reason_code)
        logger.warning(f"[MQTT Engine] Disconnected rc={rc}. Paho will reconnect as {self._client_id}.")
        try:
            from src.api.socketio_server import emit_upstream_sync
            emit_upstream_sync()
        except Exception:
            pass

    def _subscribe_topics(self) -> List[str]:
        """Subscribe once to every real Product A/B/C source topic."""
        topics: List[str] = []
        primary = (settings.MQTT_TOPIC_TELEMETRY or "").strip()
        if primary:
            topics.append(primary)
        for topic in PRODUCT_TOPICS:
            if topic not in topics:
                topics.append(topic)
        return topics

    def _is_control_topic(self, topic: str) -> bool:
        name = (topic or "").rstrip("/")
        return name in {settings.MQTT_TOPIC_ALERTS, "pdm/alerts", "pdm/predictions"} or name.endswith("/alerts")

    def _duplicate_payload(self, machine_id: str, payload: str) -> bool:
        """Drop broker/subscription redelivery of the same bytes within 1s so Postgres is not doubled."""
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        now = time.monotonic()
        previous = self._recent_payloads.get(machine_id)
        self._recent_payloads[machine_id] = (digest, now)
        return bool(previous and previous[0] == digest and (now - previous[1]) < 1.0)

    @staticmethod
    def _parameter_items(data: Any) -> List[Dict[str, Any]]:
        """Flatten gateway wrappers while leaving leaf value arrays untouched."""
        found: List[Dict[str, Any]] = []

        def visit(node: Any) -> None:
            if isinstance(node, list):
                for child in node:
                    visit(child)
                return
            if not isinstance(node, dict):
                return
            if "name" in node:
                found.append(node)
                return
            children = node.get("parameters")
            if isinstance(children, list):
                visit(children)

        visit(data)
        return found

    @staticmethod
    def _numbers(raw: Any) -> List[float]:
        values = raw if isinstance(raw, list) else [raw]
        result: List[float] = []
        for value in values:
            try:
                result.append(float(value))
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _faults(raw: Any) -> List[Dict[str, Any]]:
        values = raw if isinstance(raw, list) else [raw]
        faults: List[Dict[str, Any]] = []
        for value in values:
            if isinstance(value, dict):
                code = value.get("fault_code") or value.get("faultCode") or value.get("code")
                if code:
                    confidence = float(value.get("confidence") or 0.0)
                    if confidence > 1.0:
                        confidence = confidence / 100.0
                    faults.append({
                        "fault_code": str(code),
                        "description": str(value.get("description") or value.get("name") or code),
                        "confidence": confidence,
                        # Gateway lists MF001–MF003 every tick at confidence 0.0.
                        "active": confidence >= 0.5,
                    })
            elif value not in (None, "", 0, "0"):
                faults.append({
                    "fault_code": str(value),
                    "description": str(value),
                    "confidence": 1.0,
                    "active": True,
                })
        return faults

    @staticmethod
    def _map_flat_aliases(data: Dict[str, Any]) -> Dict[str, Any]:
        """Map Product A/B/C camelCase keys from a flat JSON object."""
        aliases = {
            "xAxisVibration": "_axisX",
            "yAxisVibration": "_axisY",
            "zAxisVibration": "_axisZ",
            "x_axis_vibration": "_axisX",
            "y_axis_vibration": "_axisY",
            "z_axis_vibration": "_axisZ",
            "vibrationLevelRms": "imuAcceleration",
            "vibration_level_rms": "imuAcceleration",
            "imuAcceleration": "imuAcceleration",
            "IMU-Acceleration": "imuAcceleration",
            "imu_acceleration": "imuAcceleration",
            "motorRpm": "rpm",
            "machineTemperature": "tempMotor",
            "soundDb": "soundLevel",
            "soundFrequency": "soundFrequency",
            "roll": "magRoll",
            "pitch": "magPitch",
            "yaw": "magYaw",
            "roll_deg": "magRoll",
            "pitch_deg": "magPitch",
            "yaw_deg": "magYaw",
            "relativePressureBar": "pressure",
            "pressure_bar": "pressure",
            "environmentHumidityPct": "humidity",
            "humidity_rh_pct": "humidity",
            "derivedLeakageRate": "leakage",
            "machineRunTimeHours": "runHours",
            "vibration_velocity_rms": "imuAcceleration",
            "shaft_rpm": "rpm",
            "motor_temp_celsius": "tempMotor",
            "acoustic_noise_db": "soundLevel",
        }
        fields: Dict[str, Any] = {}
        for src, dest in aliases.items():
            if src in data and data[src] is not None:
                try:
                    fields[dest] = float(data[src]) if dest != "runHours" else float(data[src])
                except (TypeError, ValueError):
                    continue
        if isinstance(data.get("top_5_frequencies"), list) and isinstance(data.get("top_5_amplitudes"), list):
            fields["vibrationHarmonics"] = [
                {"frequency": float(f), "amplitude": float(a)}
                for f, a in zip(data["top_5_frequencies"], data["top_5_amplitudes"])
            ]
        return fields

    def _parse_nested_blocks(self, data: Dict[str, Any]) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        for key in (
            "pressure_humidity",
            "vibration",
            "temperature_noise",
            "orientation",
            "electricity",
        ):
            block = data.get(key)
            if isinstance(block, dict):
                fields.update(self._map_flat_aliases(block))
        return fields

    def _parse_fragment(self, data: Any) -> Dict[str, Any]:
        """Translate Product A/B/C parameter names into canonical frame aliases."""
        fields: Dict[str, Any] = {}
        if isinstance(data, dict):
            fields.update(self._map_flat_aliases(data))
            fields.update(self._parse_nested_blocks(data))
        vib_freq: Dict[int, float] = {}
        vib_amp: Dict[int, float] = {}
        mic_freq: Dict[int, float] = {}
        mic_amp: Dict[int, float] = {}

        for item in self._parameter_items(data):
            name = str(item.get("name") or "").strip().lower()
            compact = "".join(ch for ch in name if ch.isalnum())
            energy_metric = name.split("-", 1)[1].strip() if "energymeter" in name and "-" in name else ""
            raw = item.get("parameters")

            if "motor fault" in name:
                fields["motorFaults"] = self._faults(raw)
                continue
            if "waveform" in name or "raw accel" in name:
                samples = self._numbers(raw)
                if samples:
                    if "x" in compact.replace("axis", "")[-2:]:
                        fields["waveformX"] = samples
                    elif "y" in compact.replace("axis", "")[-2:]:
                        fields["waveformY"] = samples
                    elif "z" in compact.replace("axis", "")[-2:]:
                        fields["waveformZ"] = samples
                    else:
                        fields["waveform"] = samples
                continue

            nums = self._numbers(raw)
            if not nums:
                continue
            num = nums[0]

            # Product A — acceleration/orientation/temperature/acoustics.
            if "microphone" in name and "frequency" in name:
                idx = next((int(ch) for ch in reversed(name) if ch.isdigit()), len(mic_freq) + 1)
                mic_freq[idx] = num
            elif "microphone" in name and "amplitude" in name:
                idx = next((int(ch) for ch in reversed(name) if ch.isdigit()), len(mic_amp) + 1)
                mic_amp[idx] = num
            elif "sound level" in name or ("sound" in name and "db" in name):
                fields["soundLevel"] = num
            elif "ntc temperature" in name or "machine temperature" in name:
                # NTC x2: channel 1 = motor/frame, channel 2 = package / air-end
                # (shown on the dashboard as compressor temp). Two values may arrive
                # in one array or as two successive items with the same gateway name.
                if "tempMotor" not in fields:
                    fields["tempMotor"] = num
                    if len(nums) > 1:
                        fields["tempAmbient"] = nums[1]
                        fields["tempCompressor"] = nums[1]
                else:
                    second = nums[-1]
                    fields["tempAmbient"] = second
                    fields["tempCompressor"] = second
            elif "temperature - sensor 1" in name:
                fields["tempMotor"] = num
            elif "environment temperature" in name:
                continue
            elif "temperature - sensor 2" in name:
                fields["tempAmbient"] = num
                fields["tempCompressor"] = num
            elif "roll" in name:
                fields["magRoll"] = num
            elif "pitch" in name:
                fields["magPitch"] = num
            elif "yaw" in name:
                fields["magYaw"] = num
            elif compact in {"xaxisvibration", "xaxis", "accx", "accelerationx", "imux", "imuaccelerationx", "vibrationx", "xaxisrms", "xrms", "velx"} or (
                ("x-axis" in name or "x axis" in name or name.strip() in {"x", "vx"})
                and ("acceleration" in name or "vibration" in name or "imu" in name or "rms" in name or compact in {"x", "vx"})
                and "frequency" not in name
                and "amplitude" not in name
                and "microphone" not in name
            ):
                fields["_axisX"] = num
            elif compact in {"yaxisvibration", "yaxis", "accy", "accelerationy", "imuy", "imuaccelerationy", "vibrationy", "yaxisrms", "yrms", "vely"} or (
                ("y-axis" in name or "y axis" in name or name.strip() in {"y", "vy"})
                and ("acceleration" in name or "vibration" in name or "imu" in name or "rms" in name or compact in {"y", "vy"})
                and "frequency" not in name
                and "amplitude" not in name
                and "microphone" not in name
            ):
                fields["_axisY"] = num
            elif compact in {"zaxisvibration", "zaxis", "accz", "accelerationz", "imuz", "imuaccelerationz", "vibrationz", "zaxisrms", "zrms", "velz"} or (
                ("z-axis" in name or "z axis" in name or name.strip() in {"z", "vz"})
                and ("acceleration" in name or "vibration" in name or "imu" in name or "rms" in name or compact in {"z", "vz"})
                and "frequency" not in name
                and "amplitude" not in name
                and "microphone" not in name
            ):
                fields["_axisZ"] = num
            elif ("vibration" in name and "frequency" in name) or ("acceleration" in name and "frequency" in name):
                idx = next((int(ch) for ch in reversed(name) if ch.isdigit()), len(vib_freq) + 1)
                vib_freq[idx] = num
            elif ("vibration" in name and "amplitude" in name) or ("acceleration" in name and "amplitude" in name):
                idx = next((int(ch) for ch in reversed(name) if ch.isdigit()), len(vib_amp) + 1)
                vib_amp[idx] = num
            elif (
                "vibration level" in name
                or "acceleration rms" in name
                or compact in {"imuacceleration", "imuaccel"}
                or ("imu" in name and "acceleration" in name and "rpm" not in name and "axis" not in name)
            ):
                # IMU-Acceleration is overall RMS, unless the gateway sends 3-axis
                # scalars in one array or a triggered capture (>=8 samples).
                if len(nums) >= 8:
                    fields["waveform"] = nums
                elif len(nums) == 3:
                    fields["_axisX"], fields["_axisY"], fields["_axisZ"] = nums[0], nums[1], nums[2]
                else:
                    fields["imuAcceleration"] = num

            # Product B — preserve every primary electrical parameter.
            elif energy_metric == "average power factor":
                fields["emPowerFactor"] = num
            elif energy_metric == "frequency deviation":
                fields["emFrequencyDeviation"] = num
            elif energy_metric == "frequency":
                fields["emFrequency"] = num
            elif energy_metric == "energy":
                fields["emEnergy"] = num
            elif energy_metric == "%thd vr":
                fields["emThdVr"] = num
            elif energy_metric == "%thd vy":
                fields["emThdVy"] = num
            elif energy_metric == "%thd vb":
                fields["emThdVb"] = num
            elif "current imbalance" in name or "iuf" in name:
                fields["emCurrentImbalance"] = num
            elif "voltage imbalance" in name:
                fields["emVoltageImbalance"] = num
            elif "machine load" in name:
                fields["emMachineLoad"] = num
            elif energy_metric == "ir" or compact.endswith("ir"):
                fields["emIr"] = num
            elif energy_metric == "iy" or compact.endswith("iy"):
                fields["emIy"] = num
            elif energy_metric == "ib" or compact.endswith("ib"):
                fields["emIb"] = num
            elif energy_metric == "vr" or compact.endswith("vr"):
                fields["emVr"] = num
            elif energy_metric == "vy" or compact.endswith("vy"):
                fields["emVy"] = num
            elif energy_metric == "vb" or compact.endswith("vb"):
                fields["emVb"] = num
            elif energy_metric == "power":
                fields["emPower"] = num

            # Product C — optional environmental/process module.
            elif "leakage" in name or "lekeage" in name:
                fields["leakage"] = num
            elif "pressure" in name:
                fields["pressure"] = num
            elif "humidity" in name:
                fields["humidity"] = num
            elif "runtime" in name or "run hour" in name or "runhour" in compact:
                fields["runHours"] = num
            elif "rpm" in name or compact == "rpm":
                fields["rpm"] = num
            elif "sample rate" in name or name in {"fs", "sampleratehz"}:
                fields["sampleRateHz"] = num
            elif "loop" in name or "namur" in name or "4-20" in name:
                fields["loopCurrentMa"] = num

        if vib_freq:
            fields["vibrationHarmonics"] = [
                {"frequency": freq, "amplitude": vib_amp.get(idx, 0.0)}
                for idx, freq in sorted(vib_freq.items())
            ]
        if mic_freq:
            fields["micHarmonics"] = [
                {"frequency": freq, "amplitude": mic_amp.get(idx, 0.0)}
                for idx, freq in sorted(mic_freq.items())
            ]
        return fields

    def _sanitize_imu_velocity(self, machine_id: str, value: float) -> Optional[float]:
        """Keep ISO velocity RMS; drop gateway ticks that are clearly not mm/s."""
        if not math.isfinite(value) or value < 0.0:
            return None
        if value > IMU_VELOCITY_HARD_MAX_MM_S:
            now = time.monotonic()
            if now - self._last_imu_reject_at >= 15.0:
                self._last_imu_reject_at = now
                logger.warning(
                    f"[MQTT Stream] Ignored implausible IMU-Acceleration {value:.1f} mm/s "
                    f"(not a velocity RMS) for {machine_id}"
                )
            return None
        last = self._last_good_imu.get(machine_id)
        if last is not None and last < IMU_HEALTHY_BASELINE_MM_S and value >= IMU_VELOCITY_SPIKE_MM_S:
            self._imu_spike_count[machine_id] = self._imu_spike_count.get(machine_id, 0) + 1
            if self._imu_spike_count[machine_id] < IMU_SPIKE_CONFIRM_COUNT:
                return None
        else:
            self._imu_spike_count[machine_id] = 0
        self._last_good_imu[machine_id] = value
        self._last_good_imu_at[machine_id] = time.monotonic()
        return value

    def _seed_held_imu_once(self, machine_id: str) -> None:
        """After API restart, reuse the last plausible RMS from Postgres — never a Hz-range spike."""
        if machine_id in self._last_good_imu or machine_id in self._imu_seed_attempted:
            return
        self._imu_seed_attempted.add(machine_id)
        seeded = self._seed_scalar_from_store(machine_id, "imuAcceleration", "imu_acceleration")
        if seeded is None or not math.isfinite(seeded) or seeded < 0.0 or seeded > IMU_VELOCITY_HARD_MAX_MM_S:
            seeded = self._last_plausible_imu_from_store(machine_id)
        if seeded is None:
            return
        self._last_good_imu[machine_id] = seeded
        self._last_good_imu_at[machine_id] = time.monotonic()

    def _last_plausible_imu_from_store(self, machine_id: str) -> Optional[float]:
        """Newest rows are often motor_fault/integrated_data (no IMU). Query vibration RMS directly."""
        try:
            from src.database.telemetry_store import latest_plausible_imu_from_db
            return latest_plausible_imu_from_db(
                machine_id,
                max_mm_s=IMU_VELOCITY_HARD_MAX_MM_S,
                hold_seconds=IMU_HOLD_SECONDS,
            )
        except Exception:
            return None

    def _held_imu(self, machine_id: str) -> Optional[float]:
        self._seed_held_imu_once(machine_id)
        last = self._last_good_imu.get(machine_id)
        seen = self._last_good_imu_at.get(machine_id)
        if last is None or seen is None:
            return None
        if time.monotonic() - seen > IMU_HOLD_SECONDS:
            return None
        return last

    def _aggregate_fragment(self, machine_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        now = time.monotonic()
        cache = self._field_cache.setdefault(machine_id, {})
        for key, value in fields.items():
            if key in {"imuAcceleration", "_axisX", "_axisY", "_axisZ"} and isinstance(value, (int, float)):
                accepted = self._sanitize_imu_velocity(machine_id, float(value))
                if accepted is None:
                    if key == "imuAcceleration":
                        held = self._held_imu(machine_id)
                        if held is not None:
                            cache["imuAcceleration"] = (held, now)
                        else:
                            cache.pop("imuAcceleration", None)
                    continue
                value = accepted
            cache[key] = (value, now)

        if "motorFaults" in fields:
            incoming_list = [item for item in (fields["motorFaults"] or []) if isinstance(item, dict)]
            active_items = [
                item for item in incoming_list
                if item.get("active") or float(item.get("confidence") or 0.0) >= 0.5
            ]
            if not active_items:
                held = self._last_diagnosis_fault.get(machine_id)
                if held and str(held.get("fault_code") or "").upper().startswith("MF"):
                    self._last_diagnosis_fault.pop(machine_id, None)

            # Preserve non-overlapping cached faults (e.g. PF001 or EF001) while updating
            # gateway motor faults (MF001, MF002, MF003)
            cached_faults = cache.get("motorFaults", ([], 0))[0] if isinstance(cache.get("motorFaults"), tuple) else []
            incoming_codes = {str(item.get("fault_code") or "").upper() for item in incoming_list}
            merged_faults = [
                item for item in (cached_faults or [])
                if isinstance(item, dict) and str(item.get("fault_code") or "").upper() not in incoming_codes
            ]
            merged_faults.extend(incoming_list)
            cache["motorFaults"] = (merged_faults, now)

        if "imuAcceleration" in fields and isinstance(fields["imuAcceleration"], (int, float)):
            if float(fields["imuAcceleration"]) < 2.5:
                held_axes = self._last_good_axes.get(machine_id)
                if held_axes and max(held_axes) >= 4.5:
                    self._last_good_axes.pop(machine_id, None)
                    for ax_key in ("_axisX", "_axisY", "_axisZ", "xAxisVibration", "yAxisVibration", "zAxisVibration"):
                        cache.pop(ax_key, None)

        expired = [
            key for key, (_, seen) in cache.items()
            if now - seen > (IMU_HOLD_SECONDS if key in AXIS_CACHE_KEYS else FIELD_TTL_SECONDS)
        ]
        for key in expired:
            cache.pop(key, None)

        merged = {key: value for key, (value, _) in cache.items()}
        axis_aliases = {"_axisX": "xAxisVibration", "_axisY": "yAxisVibration", "_axisZ": "zAxisVibration"}
        present_axes: List[float] = []
        for src, dest in axis_aliases.items():
            raw = merged.pop(src, None)
            if isinstance(raw, (int, float)):
                merged[dest] = float(raw)
                present_axes.append(float(raw))
            elif isinstance(merged.get(dest), (int, float)):
                present_axes.append(float(merged[dest]))
        # Only pdm/vibration (or a 3-value IMU array) may refresh the hold.
        # integrated_data merging cached axes must not extend the 300s window forever.
        incoming_triple = all(
            any(isinstance(fields.get(key), (int, float)) for key in group)
            for group in (
                ("_axisX", "xAxisVibration"),
                ("_axisY", "yAxisVibration"),
                ("_axisZ", "zAxisVibration"),
            )
        )
        if incoming_triple and len(present_axes) == 3:
            self._last_good_axes[machine_id] = (
                float(merged["xAxisVibration"]),
                float(merged["yAxisVibration"]),
                float(merged["zAxisVibration"]),
            )
            self._last_good_axes_at[machine_id] = now
        # Prefer the published 3-axis RMS. Only rebuild it if X/Y/Z arrived without IMU-Acceleration.
        if "imuAcceleration" not in merged and present_axes:
            rebuilt = math.sqrt(sum(value ** 2 for value in present_axes))
            accepted = self._sanitize_imu_velocity(machine_id, rebuilt)
            if accepted is not None:
                merged["imuAcceleration"] = accepted
        if "rpm" not in merged and merged.get("vibrationHarmonics"):
            try:
                merged["rpm"] = float(merged["vibrationHarmonics"][0]["frequency"]) * 60.0
            except (TypeError, ValueError, KeyError, IndexError):
                pass
        return merged

    def _parse_payload_to_frame(self, data: Any, machine_id: str = "compressor_unit_01") -> Optional[TelemetryFrame]:
        """Parse a complete flat frame or aggregate a Product A/B/C fragment."""
        if (
            isinstance(data, dict)
            and ("imuAcceleration" in data or "imu_acceleration" in data)
            and not self._parameter_items(data)
        ):
            data = dict(data)
            data["machineId"] = data.get("machineId") or machine_id
            frame = TelemetryFrame(**data)
            self._replace_implausible_frame_imu(frame)
            self._apply_derived_fields(frame)
            return frame

        fragment = self._parse_fragment(data)
        if not fragment:
            return None
        merged = self._aggregate_fragment(machine_id, fragment)
        frame_dict: Dict[str, Any] = {
            "machineId": machine_id,
            **merged,
            # Tell TelemetryFrame which fields were genuinely received.  Placeholder
            # zeros inserted by validation must never become measured values.
            "sourceKeys": list(merged.keys()),
        }
        frame = TelemetryFrame(**frame_dict)
        # Do not synthesize a waveform from IMU-Acceleration RMS — that invents spectrum faults.
        if frame.vibration_prediction_mode() in {"spectrum", "triaxial_spectrum"}:
            frame.ensure_triggered_waveform()
        self._apply_derived_fields(frame)
        return frame

    @staticmethod
    def _ensure_source_key(frame: TelemetryFrame, key: str) -> None:
        keys = list(frame.source_keys or [])
        if key not in keys:
            keys.append(key)
            frame.source_keys = keys

    @staticmethod
    def _machine_electrically_running(frame: TelemetryFrame) -> bool:
        from src.models.live_cues import is_operating
        return is_operating(frame)

    def _seed_scalar_from_store(self, machine_id: str, *aliases: str) -> Optional[float]:
        def _from_record(rec: Dict[str, Any]) -> Optional[float]:
            keys = rec.get("sourceKeys") or rec.get("source_keys") or []
            if isinstance(keys, str):
                try:
                    keys = json.loads(keys)
                except Exception:
                    keys = []
            sent = {str(k).replace("_", "").lower() for k in (keys or [])}
            wanted = {alias.replace("_", "").lower() for alias in aliases}
            if not sent or not (sent & wanted):
                return None
            for alias in aliases:
                raw = rec.get(alias)
                if isinstance(raw, (int, float)):
                    return float(raw)
            return None

        latest = self.latest_telemetry.get(machine_id) or {}
        found = _from_record(latest)
        if found is not None:
            return found
        try:
            from src.database.telemetry_store import list_asset_telemetry_from_db
            rows = list_asset_telemetry_from_db(machine_id, limit=1)
            if rows:
                return _from_record(rows[-1])
        except Exception:
            pass
        return None

    def _accumulate_runtime(self, frame: TelemetryFrame) -> None:
        machine_id = frame.machine_id
        now = time.monotonic()
        if frame.field_was_sent("runHours", "run_hours"):
            self._runtime_hours[machine_id] = float(frame.run_hours)
            self._runtime_last_mono[machine_id] = now
            if frame.em_energy is not None:
                self._runtime_last_energy[machine_id] = float(frame.em_energy)
            return
        if machine_id not in self._runtime_hours:
            seeded = self._seed_scalar_from_store(machine_id, "runHours", "run_hours")
            self._runtime_hours[machine_id] = seeded if seeded is not None else 0.0
        hours = self._runtime_hours[machine_id]
        prev_t = self._runtime_last_mono.get(machine_id)
        operating = self._machine_electrically_running(frame)
        if prev_t is not None and operating:
            hours += max(0.0, now - prev_t) / 3600.0
        energy = (
            float(frame.em_energy)
            if frame.em_energy is not None and frame.field_was_sent("emEnergy", "em_energy")
            else None
        )
        prev_e = self._runtime_last_energy.get(machine_id)
        if energy is not None and prev_e is not None and operating:
            delta_kwh = energy - prev_e
            if 0.02 <= delta_kwh < 50.0:
                kw = max(float(frame.em_power), self._estimated_kw.get(machine_id, 0.0), 0.25)
                hours += delta_kwh / kw
        if energy is not None:
            self._runtime_last_energy[machine_id] = energy
        self._runtime_hours[machine_id] = hours
        self._runtime_last_mono[machine_id] = now
        frame.run_hours = round(hours, 4)
        self._ensure_source_key(frame, "runHours")

    def _power_cap_kw(self) -> float:
        return max(float(settings.MOTOR_RATED_KW) * 1.2, 5.0)

    def _note_energy_sample(self, machine_id: str, energy: float, now: float) -> None:
        hist = self._energy_history.setdefault(machine_id, [])
        if hist and abs(hist[-1][1] - energy) < 1e-9 and now - hist[-1][0] < 20.0:
            return
        hist.append((now, energy))
        cutoff = now - 12.0 * 60.0
        self._energy_history[machine_id] = [(t, val) for t, val in hist if t >= cutoff][-48:]

    def _seed_energy_history_from_store(self, machine_id: str, now: float) -> None:
        if self._energy_history.get(machine_id):
            return
        try:
            from src.database.telemetry_store import list_recent_energy_samples
            rows = list_recent_energy_samples(machine_id, minutes=12)
        except Exception:
            return
        cutoff = now - 12.0 * 60.0
        compact: List[Tuple[float, float]] = []
        for wall, energy in rows or []:
            if wall < cutoff:
                continue
            if not compact or abs(compact[-1][1] - energy) > 1e-9:
                compact.append((wall, energy))
            else:
                compact[-1] = (wall, energy)
        if compact:
            self._energy_history[machine_id] = compact[-48:]

    def _window_kw(self, machine_id: str) -> float:
        hist = self._energy_history.get(machine_id) or []
        if len(hist) < 2:
            return 0.0
        delta = hist[-1][1] - hist[0][1]
        dt_h = (hist[-1][0] - hist[0][0]) / 3600.0
        if dt_h < (30.0 / 3600.0) or delta < 0.01:
            return 0.0
        return min(delta / dt_h, self._power_cap_kw())

    def _last_tick_kw(self, machine_id: str, now: float) -> float:
        distinct: List[Tuple[float, float]] = []
        for ts, energy in self._energy_history.get(machine_id) or []:
            if not distinct or abs(distinct[-1][1] - energy) > 1e-9:
                distinct.append((ts, energy))
        if len(distinct) < 2:
            return 0.0
        t0, e0 = distinct[-2]
        t1, e1 = distinct[-1]
        dt = t1 - t0
        if dt < 8.0:
            return 0.0
        delta = e1 - e0
        if delta < 0.005:
            return 0.0
        return min(delta / (dt / 3600.0), self._power_cap_kw())

    def _estimate_power_kw(self, frame: TelemetryFrame) -> float:
        """Instantaneous kW from CTs, else kWh slope when the power register stays 0."""
        from src.models.live_cues import is_process_running, three_phase_kw

        reported = float(frame.em_power or 0.0)
        calculated = three_phase_kw(frame)
        if reported > 0.05:
            self._estimated_kw[frame.machine_id] = reported
            return reported
        if calculated > 0.05:
            self._estimated_kw[frame.machine_id] = calculated
            return calculated
        machine_id = frame.machine_id
        now = time.time()
        energy = (
            float(frame.em_energy)
            if frame.em_energy is not None and frame.field_was_sent("emEnergy", "em_energy")
            else None
        )
        self._seed_energy_history_from_store(machine_id, now)
        if energy is not None:
            self._note_energy_sample(machine_id, energy, now)
        tick = self._last_tick_kw(machine_id, now)
        window = self._window_kw(machine_id)
        est = self._estimated_kw.get(machine_id, 0.0)
        running = is_process_running(frame)
        fresh = tick if tick > 0.05 else window
        if fresh > 0.05:
            est = fresh if est <= 0.05 else (0.4 * fresh + 0.6 * est)
        elif not running:
            est = 0.0
        self._estimated_kw[machine_id] = est
        return round(max(0.0, est), 3)

    def _publish_latest(self, machine_id: str, frame: TelemetryFrame) -> None:
        dump = frame.model_dump(by_alias=True)
        estimated = self._estimated_kw.get(machine_id)
        if estimated is None:
            estimated = self._estimate_power_kw(frame)
        dump["emPowerEstimated"] = estimated
        if float(dump.get("emPower") or 0.0) < 0.05 and estimated > 0.05:
            dump["emMachineLoadEstimated"] = round(
                min(100.0, estimated / max(float(settings.MOTOR_RATED_KW), 1.0) * 100.0),
                1,
            )
        self.latest_telemetry[machine_id] = dump

    def _seed_rul_hours(self, machine_id: str) -> Optional[float]:
        """Prefer Gamma's last inspect/RUL clock over leftover physics remainingHours in telemetry_reading."""
        try:
            from src.database.telemetry_store import list_agent_snapshots
            snaps = list_agent_snapshots(machine_id, limit=1)
            if snaps:
                hours = snaps[0].get("rulOperatingHours")
                if hours is None:
                    hours = snaps[0].get("rul_operating_hours")
                if isinstance(hours, (int, float)) and float(hours) >= 0.0:
                    return float(hours)
        except Exception:
            pass
        return self._seed_scalar_from_store(machine_id, "remainingHours", "remaining_hours")

    def _stamp_remaining_hours(self, frame: TelemetryFrame) -> None:
        cached = self._last_rul_hours.get(frame.machine_id)
        if cached is None:
            seeded = self._seed_rul_hours(frame.machine_id)
            if seeded is None:
                return
            cached = seeded
            self._last_rul_hours[frame.machine_id] = cached
        frame.remaining_hours = round(float(self._last_rul_hours[frame.machine_id]), 1)
        self._ensure_source_key(frame, "remainingHours")

    def _capture_rul(self, machine_id: str, prediction: Any) -> None:
        rul = getattr(prediction, "rul_prediction", None)
        hours = getattr(rul, "rul_operating_hours", None) if rul is not None else None
        if hours is None:
            return
        try:
            self._last_rul_hours[machine_id] = float(hours)
        except (TypeError, ValueError):
            return

    def _get_pressure_history(self, machine_id: str) -> List[Dict[str, Any]]:
        """Return a lightweight pressure-history list from the in-memory field cache.

        Builds a list of {"pressure": <float>} dicts from the time-stamped cache
        entries for the given machine.  This is the same shape that
        pressure_is_recovering() and pressure_decay_bar() consume, so no DB
        query is needed in the hot path — the cache already holds the most-recent
        field snapshots.

        The list is ordered newest-first (most-recent cache entry first) so the
        calling functions naturally sample the latest window.
        """
        history: List[Dict[str, Any]] = []
        cache = self._field_cache.get(machine_id, {})
        pressure_entry = cache.get("pressure")
        if isinstance(pressure_entry, tuple):
            p_val, _ = pressure_entry
            if isinstance(p_val, (int, float)) and float(p_val) > 0.0:
                history.append({"pressure": float(p_val)})
        # Supplement from latest_telemetry (one more data point at no cost).
        latest = self.latest_telemetry.get(machine_id, {})
        raw_latest = latest.get("pressure")
        if isinstance(raw_latest, (int, float)) and float(raw_latest) > 0.0:
            p_latest = float(raw_latest)
            if not history or abs(history[-1]["pressure"] - p_latest) > 0.01:
                history.append({"pressure": p_latest})
        # Supplement from recent DB snapshots only when we have very few readings.
        if len(history) < 4:
            try:
                from src.database.telemetry_store import list_asset_telemetry_from_db
                rows = list_asset_telemetry_from_db(machine_id, limit=8)
                for row in (rows or []):
                    raw_db = row.get("pressure")
                    if isinstance(raw_db, (int, float)) and float(raw_db) > 0.0:
                        history.append({"pressure": float(raw_db)})
            except Exception:
                pass  # DB unavailable — work with whatever we have in-memory
        return history

    def _merge_held_fault(self, frame: TelemetryFrame, extra: Dict[str, Any]) -> None:
        faults: List[Dict[str, Any]] = [
            dict(item) for item in (frame.motor_faults or []) if isinstance(item, dict)
        ]
        code = str(extra.get("fault_code") or "")
        replaced = False
        for item in faults:
            if str(item.get("fault_code") or "").upper() == code.upper():
                item["confidence"] = max(float(item.get("confidence") or 0.0), float(extra.get("confidence") or 0.0))
                item["active"] = True
                item["description"] = str(extra.get("description") or item.get("description") or code)
                replaced = True
                break
        if not replaced:
            faults.append(dict(extra))
        frame.motor_faults = faults
        self._ensure_source_key(frame, "motorFaults")

    def _stamp_diagnosis_faults(self, frame: TelemetryFrame) -> None:
        if frame.field_was_sent("motorFaults"):
            active_items = [
                item for item in (frame.motor_faults or [])
                if item.get("active") or float(item.get("confidence") or 0.0) >= 0.5
            ]
            if not active_items:
                held = self._last_diagnosis_fault.get(frame.machine_id)
                if held and str(held.get("fault_code") or "").upper().startswith("MF"):
                    self._last_diagnosis_fault.pop(frame.machine_id, None)
                    frame.motor_faults = [item for item in (frame.motor_faults or []) if item.get("active") or float(item.get("confidence") or 0.0) >= 0.5]
                    return
        extra = self._last_diagnosis_fault.get(frame.machine_id)
        if extra:
            now = time.monotonic()
            extra_time = float(extra.get("_set_at", now))
            if now - extra_time <= self._FAULT_MIN_HOLD_SECONDS:
                self._merge_held_fault(frame, extra)
            else:
                self._last_diagnosis_fault.pop(frame.machine_id, None)
                self._normal_confirm_count.pop(frame.machine_id, None)

    def _merge_diagnosis_faults(self, frame: TelemetryFrame, prediction: Any) -> None:
        """Put Gamma's defect on motorFaults so Overview Active Faults / gauges match the banner."""
        defect = getattr(prediction, "defect_localization", None)
        if defect is None:
            return
        code = getattr(defect, "defect_code", None)
        if code is None:
            return
        code_str = str(code).strip()
        if not code_str or code_str.upper() in {"NORMAL", "NONE", "HEALTHY"}:
            # Debounce: only clear an active fault after N consecutive NORMAL results
            # AND the fault has been held for the minimum hold duration.
            existing_fault = self._last_diagnosis_fault.get(frame.machine_id)
            if existing_fault:
                now = time.monotonic()
                fault_age = now - float(existing_fault.get("_set_at", now))
                confirm_count = self._normal_confirm_count.get(frame.machine_id, 0) + 1
                self._normal_confirm_count[frame.machine_id] = confirm_count
                if confirm_count < self._NORMAL_CONFIRM_REQUIRED or fault_age < self._FAULT_MIN_HOLD_SECONDS:
                    # Not yet confirmed as sustained normal — keep the fault displayed.
                    # But re-stamp the frame so the diagnosis card still shows the fault.
                    self._merge_held_fault(frame, existing_fault)
                    return
            # Fault is cleared (no existing fault, or confirmed sustained NORMAL).
            self._last_diagnosis_fault.pop(frame.machine_id, None)
            self._normal_confirm_count.pop(frame.machine_id, None)
            if frame.motor_faults:
                frame.motor_faults = [
                    item for item in frame.motor_faults
                    if str(item.get("fault_code") or "").upper() not in {
                        "PF001", "PF002", "PF003", "EF001", "BPFI", "BPFO", "BSF", "FTF", "MF001", "MF002", "MF003"
                    } or item.get("active")
                ]
            cache = self._field_cache.get(frame.machine_id)
            if cache and "motorFaults" in cache:
                clean_list = [
                    item for item in (cache["motorFaults"][0] or [])
                    if not item.get("active") and float(item.get("confidence") or 0.0) < 0.5
                ]
                cache["motorFaults"] = (clean_list, time.monotonic())
            try:
                from src.api.socketio_server import emit_alert_sync
                emit_alert_sync(frame.machine_id, [])
            except Exception as exc:
                logger.warning("[MQTT Fault-Clear] Could not emit clear-alert for %s: %s", frame.machine_id, exc)
            try:
                from src.agents.open_wo_tracker import OpenWOTracker
                OpenWOTracker.get_instance().clear_machine_orders(frame.machine_id)
            except Exception as exc:
                logger.warning("[MQTT Fault-Clear] Could not clear WO tracker for %s: %s", frame.machine_id, exc)
            return
        try:
            conf_pct = float(getattr(defect, "confidence_percentage", 0) or 0)
        except (TypeError, ValueError):
            return
        conf = conf_pct / 100.0 if conf_pct > 1.0 else conf_pct
        if conf < 0.1:
            return
        existing = self._last_diagnosis_fault.get(frame.machine_id)
        set_at = time.monotonic()
        # Preserve the original _set_at when the same fault code is re-confirmed
        # (keeps the 30s hold window anchored to when the fault first appeared).
        if existing and str(existing.get("fault_code") or "").upper() == code_str.upper():
            set_at = float(existing.get("_set_at", set_at))
        extra = {
            "fault_code": code_str,
            "description": str(getattr(defect, "defect_name", None) or code_str),
            "confidence": conf,
            "active": True,
            "_set_at": set_at,
        }
        # A real fault fires — reset the consecutive-NORMAL counter.
        self._normal_confirm_count.pop(frame.machine_id, None)
        self._last_diagnosis_fault[frame.machine_id] = extra
        self._merge_held_fault(frame, extra)

        # ── Refill Recovery fast-path (PF001 only) ─────────────────────────────
        # When the tank pressure has climbed back through the refill threshold
        # (default 6.5 bar, rising trend confirmed), clear PF001 immediately
        # without waiting for the orchestrator debounce or the gateway to retract
        # its own fault code.  This is safe because:
        #   • pressure_is_recovering() requires a confirmed upward trend (not noise)
        #   • All other fault codes (MF, EF, bearing) bypass this block entirely
        if code_str.upper() == "PF001" and frame.field_was_sent("pressure"):
            try:
                from src.models.plant_cues import pressure_is_recovering
                curr_p: Optional[float] = float(frame.pressure)
                pressure_history = self._get_pressure_history(frame.machine_id)
                if pressure_is_recovering(pressure_history, curr_p):
                    logger.info(
                        "[Refill Recovery] PF001 fast-cleared for %s — "
                        "pressure %.2f bar rising through threshold (gateway fault still asserted).",
                        frame.machine_id, curr_p,
                    )
                    self._last_diagnosis_fault.pop(frame.machine_id, None)
                    self._normal_confirm_count.pop(frame.machine_id, None)
                    # Remove PF001 from the live frame so the next emit is clean.
                    if frame.motor_faults:
                        frame.motor_faults = [
                            item for item in frame.motor_faults
                            if str(item.get("fault_code") or "").upper() != "PF001"
                        ]
            except Exception as exc:
                logger.warning("[Refill Recovery] Check failed for %s: %s", frame.machine_id, exc)

    def _apply_derived_fields(self, frame: TelemetryFrame) -> None:
        """Fill overview tiles that the gateway does not publish as named fields."""
        self._stamp_held_imu(frame)
        self._stamp_held_axes(frame)
        if (
            frame.remaining_hours is not None
            and frame.field_was_sent("remainingHours", "remaining_hours")
            and frame.machine_id not in self._last_rul_hours
        ):
            self._last_rul_hours[frame.machine_id] = float(frame.remaining_hours)
        self._estimate_power_kw(frame)
        estimated = self._estimated_kw.get(frame.machine_id)
        if estimated is not None:
            frame.em_power_estimated = float(estimated)
        self._accumulate_runtime(frame)
        self._stamp_remaining_hours(frame)
        self._stamp_diagnosis_faults(frame)

    def _replace_implausible_frame_imu(self, frame: TelemetryFrame) -> None:
        if not frame.field_was_sent("imuAcceleration"):
            return
        accepted = self._sanitize_imu_velocity(frame.machine_id, float(frame.imu_acceleration))
        if accepted is not None:
            frame.imu_acceleration = accepted
            return
        last = self._held_imu(frame.machine_id)
        if last is not None:
            frame.imu_acceleration = last
            return
        frame.source_keys = [
            key for key in (frame.source_keys or [])
            if str(key).replace("_", "").lower() != "imuacceleration"
        ]
        frame.imu_acceleration = 0.0

    def _held_axes(self, machine_id: str) -> Optional[Tuple[float, float, float]]:
        last = self._last_good_axes.get(machine_id)
        seen = self._last_good_axes_at.get(machine_id)
        if last is None or seen is None:
            return None
        if time.monotonic() - seen > IMU_HOLD_SECONDS:
            return None
        return last

    def _stamp_held_imu(self, frame: TelemetryFrame) -> None:
        """Reuse last pdm/vibration RMS when this packet has no IMU-Acceleration."""
        if frame.field_was_sent("imuAcceleration"):
            return
        held = self._held_imu(frame.machine_id)
        if held is None:
            return
        frame.imu_acceleration = held
        self._ensure_source_key(frame, "imuAcceleration")

    def _stamp_held_axes(self, frame: TelemetryFrame) -> None:
        """integrated_data is overall RMS only until the gateway sends XYZ. Use pdm/vibration."""
        if frame.has_triaxial_axes():
            return
        held = self._held_axes(frame.machine_id)
        if held is None:
            return
        frame.x_axis_vibration, frame.y_axis_vibration, frame.z_axis_vibration = held
        self._ensure_source_key(frame, "xAxisVibration")
        self._ensure_source_key(frame, "yAxisVibration")
        self._ensure_source_key(frame, "zAxisVibration")

    def _on_message(self, client, userdata, msg):
        """Callback executed whenever a live sensor packet arrives via MQTT."""
        try:
            topic = getattr(msg, "topic", "") or ""
            if self._is_control_topic(topic):
                return

            raw_payload = msg.payload.decode('utf-8')
            json_data = json.loads(raw_payload)

            machine_id, human_name, machine_type = self._identify_asset(topic, json_data)

            telemetry_frame = self._parse_payload_to_frame(json_data, machine_id=machine_id)
            if not telemetry_frame:
                return

            # Payload machineId wins over topic heuristic once the frame is parsed.
            if telemetry_frame.machine_id and telemetry_frame.machine_id != machine_id:
                machine_id = telemetry_frame.machine_id
                human_name, machine_type = _asset_label(machine_id)

            from datetime import datetime, timezone
            is_new_asset = machine_id not in self.discovered_assets
            self.last_message_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            self.discovered_assets[machine_id] = {
                "id": machine_id,
                "name": human_name,
                "type": machine_type,
                "status": "ONLINE",
                "location": "Factory Floor 1",
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "topic": topic,
                "vibration_rms": telemetry_frame.imu_acceleration,
                "temp_motor": telemetry_frame.temp_motor,
                "rpm": telemetry_frame.rpm,
                "source": "mqtt_live"
            }
            self._publish_latest(machine_id, telemetry_frame)

            is_integrated = topic in {"pdm/integrated_json", "pdm/integrated_data"}
            if (
                settings.TELEMETRY_PERSIST_ENABLED
                and not is_integrated
                and not self._duplicate_payload(machine_id, raw_payload)
            ):
                persist_mqtt_frame(telemetry_frame, mqtt_topic=topic)

            logger.info(
                "[MQTT Stream] %s | topic=%s | P=%.2f bar | Vib=%.3f mm/s | Temp=%.1f°C | ActiveFaults=%s",
                machine_id, topic,
                float(telemetry_frame.pressure or 0.0),
                float(telemetry_frame.imu_acceleration or 0.0),
                float(telemetry_frame.temp_motor or 0.0),
                [f.get("fault_code") for f in (telemetry_frame.motor_faults or []) if f.get("active")],
            )

            try:
                from src.api.socketio_server import emit_telemetry_sync, emit_machines_sync
                emit_telemetry_sync(machine_id, self.latest_telemetry[machine_id])
                if is_new_asset:
                    emit_machines_sync(list(self.discovered_assets.values()))
            except Exception:
                pass

            now = time.monotonic()
            time_since_dag = now - self._last_orchestrator_run.get(machine_id, 0.0)
            if is_integrated:
                self._last_orchestrator_run["__integrated__" + machine_id] = now
                should_run_dag = True
            elif topic in PRODUCT_TOPICS and machine_id == "compressor_unit_01":
                last_int = self._last_orchestrator_run.get("__integrated__" + machine_id, 0.0)
                if now - last_int > 10.0:
                    should_run_dag = time_since_dag >= 3.0
                else:
                    should_run_dag = False
            else:
                should_run_dag = False

            if not should_run_dag:
                return

            self._last_orchestrator_run[machine_id] = now

            # The frame must evaluate against true sensor/gateway data,
            # not a previous diagnosis held in memory.
            if telemetry_frame.motor_faults:
                extra = self._last_diagnosis_fault.get(machine_id)
                if extra:
                    extra_code = str(extra.get("fault_code") or "").upper()
                    telemetry_frame.motor_faults = [
                        item for item in telemetry_frame.motor_faults
                        if str(item.get("fault_code") or "").upper() != extra_code
                    ]

            prediction_response = self.orchestrator.run(telemetry_frame, notify_oem=False)
            self._capture_rul(machine_id, prediction_response)
            self._merge_diagnosis_faults(telemetry_frame, prediction_response)
            self._stamp_remaining_hours(telemetry_frame)
            self._publish_latest(machine_id, telemetry_frame)
            if settings.TELEMETRY_PERSIST_ENABLED and not self._duplicate_payload(machine_id, raw_payload):
                persist_mqtt_frame(telemetry_frame, mqtt_topic=topic)
            try:
                from src.api.socketio_server import emit_telemetry_sync, emit_diagnosis_sync, to_frontend_diagnosis
                emit_telemetry_sync(machine_id, self.latest_telemetry[machine_id])
                # If the orchestrator returned NORMAL but a fault is being held by the debounce
                # logic, emit the last known fault diagnosis rather than a misleading NORMAL.
                # This is the key fix for the flickering Normal ↔ Fault oscillation:
                # the UI stays stable at the held fault until it is genuinely confirmed cleared.
                held_fault = self._last_diagnosis_fault.get(machine_id)
                raw_code = str(getattr(
                    getattr(prediction_response, "defect_localization", None),
                    "defect_code", "NORMAL"
                ) or "NORMAL").upper()
                if held_fault and raw_code in {"NORMAL", "NONE", "HEALTHY", ""}:
                    # Orchestrator says NORMAL but debounce is still holding a fault —
                    # keep the last fault diagnosis card so the UI does not flicker.
                    last_held_diag = self._last_diagnosis.get(machine_id)
                    if last_held_diag:
                        emit_diagnosis_sync(machine_id, last_held_diag)
                    else:
                        diagnosis = to_frontend_diagnosis(machine_id, prediction_response)
                        self._last_diagnosis[machine_id] = diagnosis
                        emit_diagnosis_sync(machine_id, diagnosis)
                else:
                    diagnosis = to_frontend_diagnosis(machine_id, prediction_response)
                    self._last_diagnosis[machine_id] = diagnosis
                    emit_diagnosis_sync(machine_id, diagnosis)
            except Exception:
                pass

            # Publish Alert if Cable Fault or Critical Defect detected
            if prediction_response.cable_check.status != "VALID" or (
                prediction_response.defect_localization and prediction_response.defect_localization.defect_code != "NORMAL"
            ):
                alert_payload = prediction_response.model_dump_json()
                self.client.publish(settings.MQTT_TOPIC_ALERTS, alert_payload)
                logger.info(f"[MQTT Stream] Published alert notification to topic: '{settings.MQTT_TOPIC_ALERTS}'")
                try:
                    from src.api.socketio_server import emit_alert_sync
                    reason = prediction_response.cable_check.fault_reason or (
                        prediction_response.defect_localization.defect_code
                        if prediction_response.defect_localization else "ALERT"
                    )
                    status = prediction_response.cable_check.status
                    emit_alert_sync(machine_id, [{
                        "key": f"Sensors::{status}",
                        "groupName": "Sensors",
                        "paramName": status,
                        "unit": None,
                        "value": 0,
                        "min": None,
                        "max": None,
                        "threshold": None,
                        "direction": "none",
                        "message": reason,
                        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                    }])
                except Exception:
                    pass
            else:
                try:
                    from src.api.socketio_server import emit_alert_sync
                    emit_alert_sync(machine_id, [])
                except Exception:
                    pass

        except Exception as e:
            # Throttled to WARNING, not debug: get_logger pins the level at INFO, so a
            # debug line here made a broker that publishes constantly look like silence.
            now = time.monotonic()
            if now - self._last_parse_error_at >= 30.0:
                self._last_parse_error_at = now
                logger.warning(
                    f"[MQTT Stream] Dropped packet on '{getattr(msg, 'topic', '?')}': "
                    f"{type(e).__name__}: {e}"
                )

    def _identify_asset(self, topic: str, json_data: Any) -> Tuple[str, str, str]:
        """Identify a machine from the live payload or MQTT topic — never from a catalog seed."""
        payload_id = None
        if isinstance(json_data, dict):
            payload_id = json_data.get("machineId") or json_data.get("machine_id")
        if payload_id:
            payload_id = str(payload_id)
            name, mtype = _asset_label(payload_id)
            return payload_id, name, mtype

        # The plant's integrated feed carries no machineId — it is one physical rig
        # publishing on the topics we subscribe to. Naming it after the topic created a
        # phantom "pdm_integrated_data" asset alongside the real one. This is not a
        # catalog seed: it only ever applies to a packet that actually arrived.
        if topic and topic in self._subscribe_topics():
            name, mtype = _asset_label(DEFAULT_LIVE_MACHINE_ID)
            return DEFAULT_LIVE_MACHINE_ID, name, mtype

        slug = (topic or "unknown").strip("/").replace("/", "_") or "unknown_asset"
        if slug in ASSET_LABELS:
            name, mtype = ASSET_LABELS[slug]
        else:
            name = (topic or slug).replace("/", " / ")
            mtype = "Industrial Asset"
        return slug, name, mtype

    def get_discovered_assets(self) -> List[Dict[str, Any]]:
        """
        Return only machines that exist:
        1. Persisted MQTT rows in Postgres
        2. Live MQTT sensor streams (overlay)
        3. Remote AI Service API when an API key is configured
        """
        assets_map: Dict[str, Dict[str, Any]] = {}
        try:
            from src.database.telemetry_store import list_latest_assets_from_db
            for row in list_latest_assets_from_db():
                mid = row.get("id")
                if mid:
                    label_name, label_type = _asset_label(str(mid))
                    assets_map[mid] = {**row, "name": label_name, "type": label_type}
        except Exception:
            pass

        for mid, live in self.discovered_assets.items():
            assets_map[mid] = {**assets_map.get(mid, {}), **live}

        # Gbotz company machines (Compressor / Simulator 1–3) are a different product.
        # Do not mix them into this plant roster unless backfill is explicitly on.
        if settings.GBOTZ_BACKFILL_WRITE_DB and (settings.AI_SERVICE_API_KEY or "").strip():
            import urllib.request
            try:
                req = urllib.request.Request(
                    f"{(settings.AI_SERVICE_BASE_URL or '').rstrip('/')}/api/ai/query",
                    data=b'{"include":["meta"]}',
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": settings.AI_SERVICE_API_KEY.strip(),
                    },
                )
                with urllib.request.urlopen(req, timeout=1.2) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("machines", []):
                        meta = m.get("meta", {})
                        mid = m.get("machineId") or meta.get("id")
                        if mid:
                            label_name, label_type = _asset_label(str(mid))
                            assets_map[mid] = {
                                **assets_map.get(mid, {}),
                                "id": mid,
                                "name": meta.get("name") or label_name,
                                "type": assets_map.get(mid, {}).get("type", label_type),
                                "status": "ONLINE",
                                "location": meta.get("location", "Factory Floor 1"),
                                "source": "api_ai_query",
                            }
            except Exception:
                pass

        return list(assets_map.values())

    def get_asset_telemetry(self, machine_id: str) -> Dict[str, Any]:
        """Latest live MQTT telemetry, else last persisted frame."""
        if hasattr(self, "latest_telemetry") and machine_id in self.latest_telemetry:
            return self.latest_telemetry[machine_id]
        try:
            from src.database.telemetry_store import list_latest_assets_from_db
            for row in list_latest_assets_from_db():
                if row.get("id") == machine_id and row.get("telemetry"):
                    return row["telemetry"]
        except Exception:
            pass
        return {"machineId": machine_id}

    def start(self):
        """Starts non-blocking background MQTT listening loop."""
        if not settings.MQTT_ENABLED:
            logger.info("[MQTT Engine] MQTT is disabled in config settings.")
            return

        try:
            logger.info(f"[MQTT Engine] Connecting to Live Broker at {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}...")
            self.client.connect_async(settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, settings.MQTT_KEEPALIVE)
            self.client.loop_start()
        except Exception as e:
            logger.error(f"[MQTT Engine] Could not start MQTT client connection: {e}")

    def stop(self):
        """Stops the background MQTT loop so process shutdown does not hang or crash."""
        try:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("[MQTT Engine] Subscriber stopped.")
        except Exception as e:
            logger.warning(f"[MQTT Engine] Stop note: {e}")
