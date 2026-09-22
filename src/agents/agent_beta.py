"""
Agent Beta: Health assessment — thermal de-weathering, power quality, domain isolation.
Consumes Agent Alpha's pattern contract. Emits isolated_failure_domain for Gamma.
"""

from typing import Optional, Tuple

from src.config import settings
from src.schemas.telemetry import TelemetryFrame
from src.schemas.predictions import CableCheckStatus, ThermalDeWeathering, ElectricalHealth
from src.models.anomaly_detector import AnomalyDetectorEngine
from src.models.live_cues import is_electrically_loaded, is_operating, max_thd_pct, ntc_delta_c, package_temp_c
from src.models.iso_20816 import evaluation_velocity_rms
from src.models.plant_cues import effective_load_pct, gateway_leak_fault, process_leak_overlay
from src.utils.logger import get_logger

logger = get_logger("Agents.Beta")


class AgentBeta:
    """Thermal + electrical fusion with a typed failure-domain handoff."""

    def __init__(self):
        self.anomaly_detector = AnomalyDetectorEngine()

    def process(
        self,
        frame: TelemetryFrame,
        cable_check: Optional[CableCheckStatus] = None,
    ) -> Tuple[ThermalDeWeathering, ElectricalHealth]:
        pattern = cable_check.pattern_recognition_status if cable_check else "STABLE"
        thermal = self.process_thermal(frame, pattern)
        electrical = self.process_electrical(frame, thermal, pattern)
        return thermal, electrical

    def process_thermal(
        self,
        frame: TelemetryFrame,
        pattern: str = "STABLE",
    ) -> ThermalDeWeathering:
        package = package_temp_c(frame)
        delta_temp = ntc_delta_c(frame)
        site_ref = settings.SITE_AMBIENT_REFERENCE_C
        ambient_drift = package - site_ref
        compensated_delta = delta_temp - max(0.0, ambient_drift)
        genuine_overheating = compensated_delta > settings.GENUINE_OVERHEAT_DELTA_C

        pm_hint = None
        if genuine_overheating and pattern in {"STEADY_CLIMB", "STUCK_HIGH"}:
            pm_hint = "GREASE"
        elif compensated_delta > 20.0 and not genuine_overheating:
            pm_hint = "CLEAN_COOLER"
        elif frame.field_was_sent("humidity") and float(frame.humidity) > 80.0:
            pm_hint = "DRYER_CHECK"

        if ambient_drift > settings.AMBIENT_DRIFT_THRESHOLD_C and not genuine_overheating:
            logger.info(
                f"[Agent Beta] Weather-compensated heat. Motor {frame.temp_motor}°C, "
                f"ambient {frame.temp_ambient}°C, true rise {compensated_delta:.1f}°C, pattern={pattern}."
            )

        return ThermalDeWeathering(
            delta_temperature_c=round(delta_temp, 2),
            ambient_drift_compensated=ambient_drift > settings.AMBIENT_DRIFT_THRESHOLD_C,
            genuine_thermal_overheating=genuine_overheating,
            pm_hint=pm_hint,
        )

    def process_electrical(
        self,
        frame: TelemetryFrame,
        thermal: Optional[ThermalDeWeathering] = None,
        pattern: str = "STABLE",
    ) -> ElectricalHealth:
        # Phase current imbalance calculation (IUF %). Idle CTs (0 A) are not an IUF event.
        # Defensive null-guards: the gateway may omit em_ir/iy/ib on non-electrical packets.
        loaded = is_electrically_loaded(frame)
        ir = float(frame.em_ir or 0.0)
        iy = float(frame.em_iy or 0.0)
        ib = float(frame.em_ib or 0.0)
        i_avg = (ir + iy + ib) / 3.0
        max_i_dev = max(abs(ir - i_avg), abs(iy - i_avg), abs(ib - i_avg))
        calc_iuf = (max_i_dev / max(i_avg, 1.0)) * 100.0
        if loaded and i_avg >= 0.5:
            current_imbalance = frame.em_current_imbalance if frame.em_current_imbalance is not None else calc_iuf
        else:
            current_imbalance = 0.0


        thd_val = max_thd_pct(frame)
        high_thd = thd_val > settings.THD_NORMAL_MAX
        high_iuf = current_imbalance > 10.0
        operating = is_operating(frame)
        pf_limit = settings.PF_NORMAL_MIN
        # PF is a current-side quantity. Process-running with 0 A CTs is not LOW_POWER_FACTOR.
        low_pf = loaded and frame.em_power_factor is not None and float(frame.em_power_factor) < pf_limit
        freq = float(frame.em_frequency or 0.0)
        freq_dev = abs(float(frame.em_frequency_deviation or 0.0))
        high_freq_dev = (
            freq_dev > 1.0
            or (freq > 0.0 and (freq < settings.FREQ_NORMAL_MIN or freq > settings.FREQ_NORMAL_MAX))
        )
        load_pct = effective_load_pct(frame)
        low_pressure = (
            operating
            and frame.field_was_sent("pressure")
            and float(frame.pressure) < 1.5
            and load_pct > 5.0
        )

        status = "NORMAL"
        if frame.em_voltage_imbalance > settings.VUF_DANGER_MIN or high_iuf:
            status = "PHASE_IMBALANCE_WARNING"
        elif frame.em_voltage_imbalance > settings.VUF_NORMAL_MAX:
            status = "PHASE_IMBALANCE_WARNING"
        elif load_pct > settings.LOAD_NORMAL_MAX:
            status = "MOTOR_OVERLOADED"
        elif high_thd:
            status = "HARMONIC_DISTORTION_WARNING"
        elif low_pf:
            status = "LOW_POWER_FACTOR"
        elif high_freq_dev:
            status = "FREQUENCY_DEVIATION_WARNING"
        elif low_pressure:
            status = "PROCESS_PRESSURE_LOW"

        kw = max(float(frame.em_power or 0.0), float(frame.em_power_estimated or 0.0))
        expected_kw = settings.MOTOR_RATED_KW * max(load_pct, 0.0) / 100.0
        energy_residual = round(kw - expected_kw, 2)

        genuine_heat = bool(thermal and thermal.genuine_thermal_overheating)
        high_vuf = frame.em_voltage_imbalance > settings.VUF_WARNING_MAX
        high_vib = evaluation_velocity_rms(frame) > settings.VIB_WARNING_MAX
        weather_only = bool(thermal and thermal.ambient_drift_compensated and not genuine_heat)
        severe_electrical = high_vuf or high_thd or high_iuf or low_pf
        humid = frame.field_was_sent("humidity") and float(frame.humidity) > 80.0

        if severe_electrical and (genuine_heat or not high_vib):
            domain = "ELECTRICAL"
        elif severe_electrical and high_vib:
            domain = "MIXED"
        elif genuine_heat and not severe_electrical:
            domain = "MECHANICAL"
        elif high_vib and not severe_electrical and not genuine_heat:
            domain = "MECHANICAL"
        elif (weather_only or humid or high_freq_dev) and not high_vib:
            domain = "ENVIRONMENTAL"
        elif pattern in {"NOISE_EXPANSION", "STEADY_CLIMB", "STUCK_HIGH", "SUDDEN_JUMP_FLAT", "REPEATED_SPIKES"}:
            domain = "MECHANICAL"
        else:
            domain = "MECHANICAL"

        # ISO 11011 / ISO 22096 leak is process/air, not a winding fault.
        leakish = gateway_leak_fault(frame) or process_leak_overlay(frame, [])
        if leakish and not (high_vuf or high_thd or high_iuf):
            domain = "MECHANICAL"

        logger.info(f"[Agent Beta] isolated_failure_domain={domain} pattern={pattern} VUF={frame.em_voltage_imbalance:.2f}% THD={thd_val:.1f}%")

        anomaly = self.anomaly_detector.compute_anomaly_score(
            vibration_rms=evaluation_velocity_rms(frame),
            temp_motor=frame.temp_motor,
            temp_ambient=frame.temp_ambient,
            voltage_unbalance=frame.em_voltage_imbalance,
            humidity=frame.humidity,
            load_pct=load_pct,
        )

        return ElectricalHealth(
            voltage_unbalance_pct=round(frame.em_voltage_imbalance, 2),
            machine_load_pct=round(load_pct, 1),
            stator_winding_status=status,
            isolated_failure_domain=domain,
            thd_pct=round(thd_val, 2),
            power_factor=round(frame.em_power_factor, 3) if frame.em_power_factor is not None else None,
            energy_residual_kw=energy_residual,
            anomaly_score=anomaly.get("anomaly_score"),
            isolation_forest_status=anomaly.get("isolation_forest_status"),
            isolation_forest_fitted=bool(anomaly.get("isolation_forest_fitted")),
        )
