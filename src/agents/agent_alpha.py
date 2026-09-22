"""
Agent Alpha: Pure Software Sensor Quality, 11-Point Hardware Gatekeeper & 5-Pattern Engine

Execution Flow:
1. Hardware rules 1–11 run FIRST without writing the ring buffer.
   - If FAULT found -> STOP, alert_suppressed=True, do NOT add to buffer, halt LangGraph.
2. If NO fault -> PUSH valid reading into the durable Redis ring buffer.
3. Temporal Pattern Recognition Engine evaluates 5 industrial patterns:
   - Pattern 1: STEADY_CLIMB (Progressive wear, slope dx/dt > 0)
   - Pattern 2: STUCK_HIGH (Persisting at elevated plateau)
   - Pattern 3: SUDDEN_JUMP_FLAT (Step-jump to high level followed by flatline)
   - Pattern 4: NOISE_EXPANSION (Growing swing / variance, structural looseness)
   - Pattern 5: REPEATED_SPIKES (Intermittent transients / loose connection)
4. Passes enriched informational pattern status to Agent Beta.
"""

import math
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
import numpy as np
from src.config import settings
from src.schemas.telemetry import TelemetryFrame
from src.schemas.predictions import CableCheckStatus
from src.services.ring_buffer import RingBufferStore, create_alpha_store
from src.utils.logger import get_logger

logger = get_logger("Agents.Alpha")


class AgentAlpha:
    """
    Agent Alpha evaluates sensor hardware integrity and dynamic temporal pattern recognition
    purely at the software layer using Product A & Product B telemetry.
    """

    def __init__(self, window_size: Optional[int] = None, store: Optional[RingBufferStore] = None):
        self.window_size = window_size or settings.ALPHA_BUFFER_SIZE
        self.store = store or create_alpha_store(self.window_size)

    def reset_buffer(self, machine_id: str) -> str:
        """Closed-loop: after WO complete, flush history and mint a new baseline_id."""
        self.store.reset(machine_id)
        baseline = f"{machine_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        self.store.set_baseline_id(machine_id, baseline)
        logger.info(
            f"[Agent Alpha] Buffer reset for {machine_id} on {self.store.backend}. baseline_id={baseline}"
        )
        return baseline

    def _quality_fault(
        self,
        status: str,
        reason: str,
        pattern: Optional[str] = None,
        namur_status: str = "BAD",
    ) -> CableCheckStatus:
        return CableCheckStatus(
            status=status,
            namur_status=namur_status,
            namur_ne43_signal_valid=False,
            alert_suppressed=True,
            pattern_recognition_status=pattern or status,
            sensor_drift_rate_per_min=0.0,
            climbing_trend_detected=False,
            buffer_write="rejected",
            fault_reason=reason,
        )

    @staticmethod
    def _is_bad_number(value: float) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return True
        return math.isnan(number) or math.isinf(number)

    @staticmethod
    def _electrical_running(frame: TelemetryFrame) -> bool:
        i_max = max(float(frame.em_ir), float(frame.em_iy), float(frame.em_ib))
        return i_max > settings.MOTOR_RUNNING_CURRENT_A or float(frame.em_power) > settings.MOTOR_RUNNING_POWER_KW

    @staticmethod
    def _fatal_missing_blocks(frame: TelemetryFrame) -> List[str]:
        """Halt only when core vibration/mechanical telemetry is completely missing.

        Product A (Acceleration, Orientation, Temperature, Sound) is the core mechanical PdM block.
        Product B (Electricity) and Product C (Pressure, Humidity) are modular hardware products
        that can be installed independently.
        """
        return [block for block in (frame.missing_blocks or []) if block in {"product_a"}]

    @staticmethod
    def _expected_three_phase_power_kw(frame: TelemetryFrame) -> float:
        v_avg = (float(frame.em_vr) + float(frame.em_vy) + float(frame.em_vb)) / 3.0
        i_avg = (float(frame.em_ir) + float(frame.em_iy) + float(frame.em_ib)) / 3.0
        pf = float(frame.em_power_factor) if frame.em_power_factor is not None else 0.88
        return (math.sqrt(3.0) * v_avg * i_avg * pf) / 1000.0

    @staticmethod
    def _calculated_vuf_pct(frame: TelemetryFrame) -> float:
        vr, vy, vb = float(frame.em_vr), float(frame.em_vy), float(frame.em_vb)
        v_avg = (vr + vy + vb) / 3.0
        if v_avg <= 0.0:
            return 0.0
        v_max_dev = max(abs(vr - v_avg), abs(vy - v_avg), abs(vb - v_avg))
        return (v_max_dev / v_avg) * 100.0

    def _hardware_fault(self, frame: TelemetryFrame, prev_readings: List[Dict[str, Any]]) -> Optional[CableCheckStatus]:
        """Rules 1–11. Returns a halt status or None if the sample may enter the buffer."""
        vib = float(frame.imu_acceleration)
        temp_motor = float(frame.temp_motor)
        temp_comp = float(frame.temp_compressor)
        current = float(frame.em_ir)
        rpm = float(frame.rpm)
        power = float(frame.em_power)
        plc_status = int(frame.plc_param_12_status)
        prev = prev_readings[-1] if prev_readings else None
        electrical_running = self._electrical_running(frame)

        fatal_missing = self._fatal_missing_blocks(frame)
        if fatal_missing:
            blocks = ", ".join(fatal_missing)
            logger.warning(f"[Agent Alpha] FIELD_NOT_PRESENT: missing {blocks} on {frame.machine_id}.")
            return self._quality_fault(
                "FIELD_NOT_PRESENT",
                f"Expected telemetry block missing from the message: {blocks}. Structural/transmission drop, not a zero reading.",
            )
        if frame.missing_blocks:
            logger.info(
                f"[Agent Alpha] Optional sensors not on this packet: {', '.join(frame.missing_blocks)} "
                f"— continue with published fields."
            )

        if frame.loop_current_ma is not None:
            loop_ma = float(frame.loop_current_ma)
            if loop_ma < settings.NAMUR_NE43_FAULT_LOW:
                logger.warning(f"[Agent Alpha] NAMUR NE43 Loop Fault: {loop_ma} mA < {settings.NAMUR_NE43_FAULT_LOW} mA. Suppressing alarm.")
                return self._quality_fault(
                    "HARDWARE_CABLE_FAULT",
                    f"NAMUR NE43 loop current ({loop_ma} mA) below {settings.NAMUR_NE43_FAULT_LOW} mA limit. Sensor cable wire severed/unplugged.",
                )
            if loop_ma > settings.NAMUR_NE43_FAULT_HIGH:
                logger.warning(f"[Agent Alpha] NAMUR NE43 Short Circuit: {loop_ma} mA > {settings.NAMUR_NE43_FAULT_HIGH} mA. Suppressing alarm.")
                return self._quality_fault(
                    "HARDWARE_CABLE_FAULT",
                    f"NAMUR NE43 loop current ({loop_ma} mA) exceeds {settings.NAMUR_NE43_FAULT_HIGH} mA limit. Transducer short-circuit.",
                )

        if plc_status != 0:
            logger.warning(f"[Agent Alpha] Sensor Cable Fault via PLC Register {plc_status}. Suppressing breakdown alarm.")
            return self._quality_fault(
                "HARDWARE_CABLE_FAULT",
                f"PLC Param 12 hardware fault register active (Code: {plc_status}). Sensor cable cut/disconnected.",
            )

        range_failures: List[str] = []
        analog_checks: List[Tuple[str, float, bool, Optional[float], Optional[float]]] = [
            ("imuAcceleration", vib, True, 0.0, None),
            ("tempMotor", temp_motor, False, settings.TEMP_TRANSDUCER_OPEN_MIN, settings.TEMP_TRANSDUCER_OPEN_MAX),
            ("tempCompressor", temp_comp, False, settings.TEMP_TRANSDUCER_OPEN_MIN, settings.TEMP_TRANSDUCER_OPEN_MAX),
            ("emIr", float(frame.em_ir), True, 0.0, None),
            ("emIy", float(frame.em_iy), True, 0.0, None),
            ("emIb", float(frame.em_ib), True, 0.0, None),
            ("emVr", float(frame.em_vr), True, 0.0, None),
            ("emVy", float(frame.em_vy), True, 0.0, None),
            ("emVb", float(frame.em_vb), True, 0.0, None),
            ("emPower", power, True, 0.0, None),
            ("rpm", rpm, True, 0.0, settings.RPM_SENSOR_MAX),
        ]
        skip_range = set()
        if not frame.field_was_sent("imuAcceleration"):
            skip_range.add("imuAcceleration")
        if not frame.field_was_sent("rpm"):
            skip_range.add("rpm")
        if not frame.field_was_sent("tempCompressor"):
            skip_range.add("tempCompressor")
        for name, value, non_negative, lo, hi in analog_checks:
            if name in skip_range:
                continue
            if self._is_bad_number(value):
                range_failures.append(f"{name}=NaN/Inf")
                continue
            if non_negative and value < 0.0:
                range_failures.append(f"{name}={value} (< 0)")
            if lo is not None and not non_negative and value < lo:
                range_failures.append(f"{name}={value} (< {lo})")
            if hi is not None and value > hi:
                range_failures.append(f"{name}={value} (> {hi})")
        if range_failures:
            logger.warning(f"[Agent Alpha] SENSOR_OUT_OF_RANGE: {range_failures}")
            return self._quality_fault(
                "SENSOR_OUT_OF_RANGE",
                f"Reading outside physically possible range: {', '.join(range_failures)}. Sensor malfunction or ADC fault.",
            )

        if frame.field_was_sent("bearingModel", "bearing_model") and prev and prev.get("bearing_model"):
            expected = str(prev["bearing_model"])
            received = str(frame.bearing_model)
            if received != expected:
                logger.warning(f"[Agent Alpha] STATIC_METADATA_CHANGED for {frame.machine_id}: {expected} -> {received}")
                return self._quality_fault(
                    "STATIC_METADATA_CHANGED",
                    f"Fixed equipment metadata changed for {frame.machine_id}: expected bearing '{expected}', received '{received}'.",
                )

        if prev is not None:
            if frame.field_was_sent("runHours", "run_hours") and prev.get("run_hours") is not None:
                prior_hours = float(prev["run_hours"])
                if float(frame.run_hours) < prior_hours:
                    logger.warning(f"[Agent Alpha] COUNTER_NON_MONOTONIC runHours {prior_hours} -> {frame.run_hours}")
                    return self._quality_fault(
                        "COUNTER_NON_MONOTONIC",
                        f"Cumulative runHours decreased from {prior_hours:.2f} to {float(frame.run_hours):.2f}. Clock fault, meter reset, or data corruption.",
                    )
            if frame.field_was_sent("emEnergy", "em_energy") and frame.em_energy is not None and prev.get("em_energy") is not None:
                prior_energy = float(prev["em_energy"])
                if float(frame.em_energy) < prior_energy:
                    logger.warning(f"[Agent Alpha] COUNTER_NON_MONOTONIC emEnergy {prior_energy} -> {frame.em_energy}")
                    return self._quality_fault(
                        "COUNTER_NON_MONOTONIC",
                        f"Cumulative emEnergy decreased from {prior_energy:.2f} to {float(frame.em_energy):.2f} kWh. Meter reset glitch or data corruption.",
                    )

        imu_sent = frame.field_was_sent("imuAcceleration")
        if prev is not None and electrical_running and imu_sent:
            last_vib = float(prev.get("vibration", 0.0))
            if last_vib > 1.0 and vib == 0.0:
                logger.warning(f"[Agent Alpha] Step-Drop Cable Cut: Vibration dropped from {last_vib} mm/s to 0.0 mm/s in 1 sample while motor active.")
                return self._quality_fault(
                    "HARDWARE_CABLE_FAULT",
                    "Step-Drop Wire Cut Signature: Instantaneous drop to 0.0 mm/s without deceleration.",
                )

        if electrical_running and imu_sent and (vib == 0.0 or temp_motor <= 0.0):
            logger.warning(f"[Agent Alpha] Cross-Domain Cable Cut: Motor running ({current}A, {power}kW) but Vibration is {vib} mm/s. Suppressing false alarm.")
            return self._quality_fault(
                "HARDWARE_CABLE_FAULT",
                "Cross-Domain Sensor Mismatch: Motor drawing active electrical power while vibration reads 0.0 mm/s or motor temperature is ≤ 0°C. Physical sensor cable is disconnected/severed.",
            )

        if electrical_running and rpm == 0.0 and frame.field_was_sent("rpm"):
            logger.warning(f"[Agent Alpha] RPM_SENSOR_MISMATCH: electrical energy present but rpm=0 on {frame.machine_id}")
            return self._quality_fault(
                "RPM_SENSOR_MISMATCH",
                f"Current/power confirm the motor is drawing energy (I={current:.2f}A, P={power:.2f}kW) but RPM reads 0. Broken tachometer or slipping coupling.",
            )

        i_avg = (float(frame.em_ir) + float(frame.em_iy) + float(frame.em_ib)) / 3.0
        v_avg = (float(frame.em_vr) + float(frame.em_vy) + float(frame.em_vb)) / 3.0
        if i_avg > settings.POWER_CALC_MIN_CURRENT_A and v_avg > settings.POWER_CALC_MIN_VOLTAGE_V:
            expected_kw = self._expected_three_phase_power_kw(frame)
            discrepancy = abs(power - expected_kw) / max(expected_kw, 1.0)
            if discrepancy > settings.POWER_CALC_MISMATCH_FRAC:
                logger.warning(f"[Agent Alpha] POWER_CALCULATION_MISMATCH: reported={power:.2f}kW calculated={expected_kw:.2f}kW")
                return self._quality_fault(
                    "POWER_CALCULATION_MISMATCH",
                    f"Reported emPower {power:.2f} kW disagrees with √3·V·I·PF = {expected_kw:.2f} kW ({discrepancy * 100:.1f}% error).",
                )

        if v_avg > 0.0:
            calc_vuf = self._calculated_vuf_pct(frame)
            reported_vuf = float(frame.em_voltage_imbalance)
            if abs(reported_vuf - calc_vuf) > settings.VUF_DERIVED_MISMATCH_PCT:
                logger.warning(f"[Agent Alpha] DERIVED_VALUE_MISMATCH: reported VUF={reported_vuf:.2f}% calculated={calc_vuf:.2f}%")
                return self._quality_fault(
                    "DERIVED_VALUE_MISMATCH",
                    f"Reported emVoltageImbalance {reported_vuf:.2f}% disagrees with {calc_vuf:.2f}% calculated from emVr/emVy/emVb.",
                )

        if (
            electrical_running
            and frame.field_was_sent("tempCompressor")
            and abs(temp_motor - temp_comp) > settings.TEMP_CORRELATION_MAX_DELTA_C
        ):
            delta = abs(temp_motor - temp_comp)
            logger.warning(f"[Agent Alpha] TEMP_CORRELATION_MISMATCH: ΔT={delta:.1f}°C")
            return self._quality_fault(
                "TEMP_CORRELATION_MISMATCH",
                f"tempMotor ({temp_motor:.1f}°C) and tempCompressor ({temp_comp:.1f}°C) diverged by {delta:.1f}°C (> {settings.TEMP_CORRELATION_MAX_DELTA_C}°C). One sensor is stuck or drifting.",
            )

        return None

    def _frozen_flatline(self, window: List[Dict[str, Any]], electrical_running: bool) -> Optional[CableCheckStatus]:
        if not electrical_running or len(window) < settings.SENSOR_FLATLINE_MIN_SAMPLES:
            return None
        series = {
            "imuAcceleration": [r.get("vibration") for r in window],
            "tempMotor": [r.get("temp_motor") for r in window],
            "tempCompressor": [r.get("temp_compressor") for r in window],
            "emIr": [r.get("current_ir") for r in window],
            "emIy": [r.get("current_iy") for r in window],
            "emIb": [r.get("current_ib") for r in window],
            "emPower": [r.get("power") for r in window],
            "rpm": [r.get("rpm") for r in window],
        }
        for name, values in series.items():
            nums = [float(v) for v in values if v is not None]
            if len(nums) < settings.SENSOR_FLATLINE_MIN_SAMPLES:
                continue
            recent = nums[-settings.SENSOR_FLATLINE_MIN_SAMPLES:]
            variance = float(np.var(recent))
            if variance < settings.SENSOR_FLATLINE_MAX_VARIANCE and recent[-1] != 0.0:
                logger.warning(f"[Agent Alpha] SENSOR_FROZEN_FLATLINE on {name} (variance={variance:.8f})")
                return self._quality_fault(
                    "SENSOR_FROZEN_FLATLINE",
                    f"{name} frozen/flatline at {recent[-1]} across {len(recent)} consecutive running samples (variance={variance:.8f}). Transducer or comm chip locked.",
                )
        return None

    def _calculate_slope(self, values: List[float]) -> float:
        """Calculates linear regression slope (dx/dt rate of change)."""
        if len(values) < 3:
            return 0.0
        n = len(values)
        x = np.arange(n)
        y = np.array(values)
        denom = np.sum((x - np.mean(x)) ** 2)
        if denom == 0:
            return 0.0
        slope = np.sum((x - np.mean(x)) * (y - np.mean(y))) / denom
        return float(slope)

    def _detect_patterns(self, recent_vib: List[float], warning_threshold: float = 4.0) -> Dict[str, Any]:
        """
        Rule 5: Evaluates the 5 Industrial Temporal Patterns:
        1. STEADY_CLIMB (Progressive wear)
        2. STUCK_HIGH (Unresolved elevated plateau)
        3. SUDDEN_JUMP_FLAT (Instant jump followed by stabilization)
        4. NOISE_EXPANSION (Growing peak-to-peak swings / looseness)
        5. REPEATED_SPIKES (Intermittent transients / chattering)
        """
        n = len(recent_vib)
        if n < 5:
            return {
                "pattern": "STABLE",
                "slope": 0.0,
                "is_anomaly": False,
                "description": "Insufficient window data (warming up in-memory buffer)."
            }

        slope = self._calculate_slope(recent_vib)
        mean_val = float(np.mean(recent_vib))
        std_val = float(np.std(recent_vib))
        min_val = float(np.min(recent_vib))
        max_val = float(np.max(recent_vib))
        p2p_swing = max_val - min_val

        # ---------------------------------------------------------------------
        # PATTERN 3: SUDDEN JUMP, THEN FLAT
        # Number jumps straight to a high level in 1 shot, then stays there
        # e.g., 2.0 -> 5.5 -> 5.5 -> 5.5
        # ---------------------------------------------------------------------
        if n >= 6:
            first_half = recent_vib[:n//2]
            second_half = recent_vib[n//2:]
            step_delta = np.mean(second_half) - np.mean(first_half)
            second_half_var = np.var(second_half)
            if step_delta >= 2.5 and second_half_var < 0.8:
                return {
                    "pattern": "SUDDEN_JUMP_FLAT",
                    "slope": slope,
                    "is_anomaly": True,
                    "description": f"Pattern 3 Detected: Sudden step-jump of +{step_delta:.2f} mm/s stabilized at high plateau ({np.mean(second_half):.2f} mm/s). Mechanical shift/fracture."
                }

        # ---------------------------------------------------------------------
        # PATTERN 2: STUCK HIGH
        # Sitting consistently at an elevated/warning level without dropping
        # e.g., 4.5 -> 4.6 -> 4.5 -> 4.6 (sitting above threshold with low variance)
        # ---------------------------------------------------------------------
        if mean_val >= warning_threshold and std_val < 0.6:
            return {
                "pattern": "STUCK_HIGH",
                "slope": slope,
                "is_anomaly": True,
                "description": f"Pattern 2 Detected: Persisting at elevated plateau ({mean_val:.2f} mm/s >= {warning_threshold} mm/s). Unresolved underlying fault."
            }

        # ---------------------------------------------------------------------
        # PATTERN 5: REPEATED SHORT SPIKES
        # Value spikes up briefly, comes back to normal, spikes again later
        # ---------------------------------------------------------------------
        if n >= 10:
            median_val = float(np.median(recent_vib))
            spikes = [v for v in recent_vib if (v - median_val) > 2.0]
            if len(spikes) >= 2:
                return {
                    "pattern": "REPEATED_SPIKES",
                    "slope": slope,
                    "is_anomaly": True,
                    "description": f"Pattern 5 Detected: {len(spikes)} transient impact spikes above baseline. Intermittent chattering / loose connection."
                }

        # ---------------------------------------------------------------------
        # PATTERN 4: GETTING NOISIER (NOISE EXPANSION)
        # Mean is relatively constant, but swings / standard deviation expands over time
        # e.g., was bouncing 1.9-2.1 (std 0.1), now bouncing 1.5-3.5 (std 0.8)
        # ---------------------------------------------------------------------
        if n >= 10:
            early_std = float(np.std(recent_vib[:n//2]))
            late_std = float(np.std(recent_vib[n//2:]))
            if late_std > 2.0 * early_std and late_std >= 0.8:
                return {
                    "pattern": "NOISE_EXPANSION",
                    "slope": slope,
                    "is_anomaly": True,
                    "description": f"Pattern 4 Detected: Signal swing expanding (std grew from {early_std:.2f} to {late_std:.2f} mm/s). Structural looseness / imbalance."
                }

        # ---------------------------------------------------------------------
        # PATTERN 1: STEADY CLIMB
        # Number keeps going up bit by bit over time (e.g. 2.0 -> 2.5 -> 3.0 -> 3.5)
        # ---------------------------------------------------------------------
        if slope > 0.05:
            return {
                "pattern": "STEADY_CLIMB",
                "slope": slope,
                "is_anomaly": True,
                "description": f"Pattern 1 Detected: Monotonic steady climb (Slope dx/dt: +{slope:.4f} mm/s/window). Progressive mechanical wear."
            }

        # Normal Healthy State
        return {
            "pattern": "STABLE",
            "slope": slope,
            "is_anomaly": False,
            "description": "Signal is stable and operating within normal random variation bounds."
        }

    def process(self, frame: TelemetryFrame) -> CableCheckStatus:
        """
        Executes hardware rules 1–11 FIRST.
        If valid -> Appends to buffer, checks frozen flatline, then runs 5-pattern recognition.
        """
        machine_id = frame.machine_id or "default_machine"
        prev_readings = self.store.get_window(machine_id)
        hardware = self._hardware_fault(frame, prev_readings)
        if hardware is not None:
            return hardware

        self.store.append(machine_id, {
            "vibration": float(frame.imu_acceleration),
            "temp_motor": float(frame.temp_motor),
            "temp_compressor": float(frame.temp_compressor),
            "current_ir": float(frame.em_ir),
            "current_iy": float(frame.em_iy),
            "current_ib": float(frame.em_ib),
            "power": float(frame.em_power),
            "rpm": float(frame.rpm),
            "run_hours": float(frame.run_hours) if frame.field_was_sent("runHours", "run_hours") else None,
            "em_energy": float(frame.em_energy) if frame.em_energy is not None and frame.field_was_sent("emEnergy", "em_energy") else None,
            "bearing_model": frame.bearing_model if frame.field_was_sent("bearingModel", "bearing_model") else None,
        })

        window = self.store.get_window(machine_id)
        frozen = self._frozen_flatline(window, self._electrical_running(frame))
        if frozen is not None:
            self.store.pop_last(machine_id)
            return frozen

        recent_vib = [float(r["vibration"]) for r in window]
        # Drop leftover gateway spikes so Pattern 3 cannot fire on 0.12 → 19 mm/s glitches.
        if recent_vib:
            median_vib = float(np.median(recent_vib))
            if median_vib < 2.5:
                cap = max(4.5, median_vib * 4.0 + 1.0)
                filtered = [v for v in recent_vib if v <= cap]
                if len(filtered) >= 5:
                    recent_vib = filtered

        # =====================================================================
        # RULE 5: EVALUATE 5 TEMPORAL INDUSTRIAL PATTERNS
        # =====================================================================
        pattern_res = self._detect_patterns(recent_vib, warning_threshold=settings.VIB_WARNING_MAX)
        pattern_name = pattern_res["pattern"]
        slope = pattern_res["slope"]
        is_anomaly = pattern_res["is_anomaly"]
        pattern_desc = pattern_res["description"]

        if is_anomaly:
            logger.info(f"[Agent Alpha] Rule 5 Anomaly: {pattern_desc}")
        else:
            logger.info(f"[Agent Alpha] Sensor plausibility passed. Pattern: {pattern_name}, Slope: {slope:+.4f}")

        # Pattern is a contract: Beta and Gamma must read it.
        return CableCheckStatus(
            status="VALID",
            namur_status="GOOD",
            namur_ne43_signal_valid=True,
            alert_suppressed=False,
            pattern_recognition_status=pattern_name,
            sensor_drift_rate_per_min=round(slope * 60.0, 4),
            climbing_trend_detected=(pattern_name == "STEADY_CLIMB"),
            buffer_write="accepted",
            fault_reason=pattern_desc if is_anomaly else None
        )
