"""
Agent Gamma: ISO 13379 diagnosis + component RUL (physics wear law + Weibull).
Consumes Alpha pattern and Beta isolated_failure_domain.
"""

from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta, timezone

from src.config import settings
from src.schemas.telemetry import TelemetryFrame
from src.schemas.predictions import (
    CableCheckStatus,
    ThermalDeWeathering,
    ElectricalHealth,
    DefectLocalization,
    RULPrediction,
    ComponentRUL,
)
from src.signal_processing.envelope_fft import EnvelopeFFTEngine
from src.signal_processing.spectrogram import VibrationSpectrogramEngine
from src.models.pinns_rul import PINNsRULEngine
from src.models.weibull_survival import WeibullSurvivalModel
from src.models.fault_classifier import FaultClassifierEngine
from src.models.expert_repair_advisor import ExpertRepairAdvisor
from src.models.iso_20816 import evaluation_velocity_rms
from src.models.live_cues import (
    acoustic_two_x,
    dominant_mic_hz,
    is_operating,
    max_thd_pct,
)
from src.models.plant_cues import effective_load_pct
from src.models.rul_history import blend_rul_hours, rms_for_rul
from src.models.fault_reasoner import fuse_live_evidence
from src.utils.logger import get_logger

logger = get_logger("Agents.Gamma")


class AgentGamma:
    def __init__(self):
        self.fft_engine = EnvelopeFFTEngine()
        self.spectrogram_engine = VibrationSpectrogramEngine()
        self.physics_rul = PINNsRULEngine()
        self.weibull_model = WeibullSurvivalModel()
        self.classifier = FaultClassifierEngine()
        self.expert_advisor = ExpertRepairAdvisor()

    def process(
        self,
        frame: TelemetryFrame,
        cable_check: Optional[CableCheckStatus] = None,
        thermal: Optional[ThermalDeWeathering] = None,
        electrical: Optional[ElectricalHealth] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[DefectLocalization, RULPrediction]:
        pattern = cable_check.pattern_recognition_status if cable_check else "STABLE"
        domain = electrical.isolated_failure_domain if electrical else "MECHANICAL"
        mode = frame.vibration_prediction_mode()

        if mode == "absent":
            cls_result = {
                "defect_code": "NORMAL",
                "defect_name": "Vibration prediction skipped — IMU-Acceleration not on this packet",
                "failing_component": "Vibration transducer not publishing",
                "confidence_percentage": 0.0,
                "diagnosis_method": "imu_absent",
            }
            if domain in {"ELECTRICAL", "MIXED"} and frame.em_voltage_imbalance > settings.VUF_NORMAL_MAX:
                cls_result = self.classifier.classify_from_rms(
                    vibration_rms=0.0,
                    voltage_unbalance=frame.em_voltage_imbalance,
                    temp_motor=frame.temp_motor,
                    temp_ambient=frame.temp_ambient,
                    isolated_domain=domain,
                    group=frame.iso_machine_group,
                    support=frame.iso_support,
                )
                cls_result["diagnosis_method"] = "imu_absent_electrical"
            envelope_peaks: list = []
            spectrum = {"method": mode, "spectrogram_event": None}
        elif mode in {"imu_rms", "triaxial_rms"}:
            # IMU-Acceleration (or XYZ scalars without a waveform) → ISO zone + RUL only.
            cls_result = self.classifier.classify_from_rms(
                vibration_rms=evaluation_velocity_rms(frame),
                voltage_unbalance=frame.em_voltage_imbalance,
                temp_motor=frame.temp_motor,
                temp_ambient=frame.temp_ambient,
                isolated_domain=domain,
                group=frame.iso_machine_group,
                support=frame.iso_support,
            )
            envelope_peaks: list = []
            spectrum = {"method": mode, "spectrogram_event": None}
        else:
            waveform = frame.ensure_triggered_waveform()
            fft_result = self.fft_engine.analyze(
                waveform=waveform,
                sample_rate_hz=frame.sample_rate_hz,
                harmonics=frame.vibration_harmonics,
                rpm=frame.rpm,
                bearing_model=frame.bearing_model,
            )
            spec_result = self.spectrogram_engine.analyze(
                rpm=frame.rpm,
                sample_rate_hz=frame.sample_rate_hz,
                waveform=waveform,
                waveform_x=frame.waveform_x or waveform,
                waveform_y=frame.waveform_y or waveform,
                waveform_z=waveform,
            )
            spectrum = self._merge_spectrum(fft_result, spec_result)
            envelope_peaks = [p["frequency"] for p in spectrum.get("peaks") or []]
            cls_result = self.classifier.classify_defect(
                vibration_rms=frame.imu_acceleration,
                matched_signal_fault=spectrum.get("matched_fault_code", "NONE"),
                voltage_unbalance=frame.em_voltage_imbalance,
                load_pct=frame.em_machine_load,
                temp_motor=frame.temp_motor,
                temp_ambient=frame.temp_ambient,
                pattern=pattern,
                isolated_domain=domain,
            )

        prior = history or []
        fused = fuse_live_evidence(
            frame,
            cls_result,
            pattern=pattern,
            domain=domain,
            electrical=electrical,
            thermal=thermal,
            history=prior,
        )
        cls_result = fused
        expert_guidance = self.expert_advisor.get_repair_guidance(
            defect_code=cls_result["defect_code"],
            component_name=cls_result["failing_component"],
        )

        two_x = acoustic_two_x(frame)
        mic_hz = dominant_mic_hz(frame)
        peak_hz = envelope_peaks or [h.frequency for h in frame.vibration_harmonics] or mic_hz

        defect_out = DefectLocalization(
            defect_code=cls_result["defect_code"],
            defect_name=cls_result["defect_name"],
            failing_component=cls_result["failing_component"],
            confidence_percentage=cls_result["confidence_percentage"],
            dominant_frequencies_hz=peak_hz,
            diagnosis_method=cls_result.get("diagnosis_method", "iso13379_catalog_rules"),
            prediction_mode=mode,
            spectrum_source=spectrum.get("method") or mode,
            spectrogram_event=spectrum.get("spectrogram_event") or (
                "2x_persistent"
                if two_x is not None and is_operating(frame) and float(frame.imu_acceleration) >= 1.5
                else None
            ),
            expert_repair_guidance=expert_guidance,
            reasoning_summary=cls_result.get("reasoning_summary"),
            reasoned_part_key=cls_result.get("reasoned_part_key"),
        )

        leak_hours = None
        if defect_out.defect_code == "PF001":
            from src.models.plant_cues import process_leak_overlay
            leak = process_leak_overlay(frame, prior)
            if leak:
                leak_hours = float(leak["inspect_hours"])

        imu_sent = frame.field_was_sent("imuAcceleration") or frame.has_triaxial_axes()
        eval_rms = evaluation_velocity_rms(frame)
        rms_rul, rms_source = rms_for_rul(eval_rms, imu_sent, prior)
        physics = self.physics_rul.predict_rul(
            vibration_rms=rms_rul,
            temp_compressor=frame.temp_compressor,
            run_hours=frame.run_hours,
            rpm=frame.rpm,
            load_pct=effective_load_pct(frame),
        )
        weibull = self.weibull_model.predict_weibull_rul(
            frame.run_hours,
            rms_rul,
            asset_class=frame.bearing_model or settings.DEFAULT_BEARING_TYPE,
        )

        bearing_hours = 0.6 * physics["rul_operating_hours"] + 0.4 * weibull["weibull_rul_hours"]
        winding_hours = bearing_hours * 1.15
        if domain == "ELECTRICAL" or (electrical and electrical.voltage_unbalance_pct > 2.5):
            winding_hours = min(winding_hours, bearing_hours * 0.55)
        if thermal and thermal.genuine_thermal_overheating:
            winding_hours = min(winding_hours, bearing_hours * 0.7)
        thd = max_thd_pct(frame)
        if thd > settings.THD_WARNING_MAX:
            winding_hours = min(winding_hours, bearing_hours * 0.8)
        if is_operating(frame) and frame.em_power_factor is not None and float(frame.em_power_factor) < 0.75:
            winding_hours = min(winding_hours, bearing_hours * 0.9)

        alignment_hours = bearing_hours * 1.2
        if defect_out.defect_code == "MF002" or pattern == "SUDDEN_JUMP_FLAT":
            alignment_hours = min(alignment_hours, bearing_hours * 0.65)
        if pattern == "NOISE_EXPANSION" or (
            two_x is not None and is_operating(frame) and float(frame.imu_acceleration) >= 1.5
        ):
            alignment_hours = min(alignment_hours, bearing_hours * 0.8)

        bearing_hours = max(24.0, bearing_hours)
        winding_hours = max(24.0, winding_hours)
        alignment_hours = max(24.0, alignment_hours)

        headline_hours = min(bearing_hours, winding_hours, alignment_hours)
        headline_hours, used_prior_rul = blend_rul_hours(headline_hours, prior, float(frame.run_hours))
        if leak_hours is not None:
            # An air leak increases compressor runtime / motor duty cycle, imposing
            # an accelerated wear penalty (15% reduction) on structural components
            # rather than abruptly crashing physical asset RUL from ~190 days to 2 days.
            headline_hours = max(24.0, headline_hours * 0.85)
            urgent_days = max(1.0, leak_hours / 24.0)
            repair_date = (datetime.now(timezone.utc) + timedelta(days=urgent_days)).strftime("%Y-%m-%d")
        else:
            headline_days = headline_hours / 24.0
            repair_date = (datetime.now(timezone.utc) + timedelta(days=headline_days)).strftime("%Y-%m-%d")
        headline_days = headline_hours / 24.0

        b10 = weibull["weibull_rul_hours"]
        b50 = b10 * 1.8

        rul_out = RULPrediction(
            rul_operating_hours=round(headline_hours, 1),
            rul_days=round(headline_days, 1),
            confidence_interval_bounds=f"B10 {round(b10 / 24.0, 1)}d / B50 {round(b50 / 24.0, 1)}d (Weibull, not a calibrated 95% CI)",
            recommended_repair_by_date=repair_date,
            model_version=settings.RUL_MODEL_VERSION,
            method=(
                ("pinn_plus_weibull" if physics.get("pinn_used") else "physics_wear_law_plus_weibull")
                + ("_with_history" if (rms_source != "current" or used_prior_rul) else "")
            ),
            bearing_rul_days=round(bearing_hours / 24.0, 1),
            winding_rul_days=round(winding_hours / 24.0, 1),
            alignment_rul_days=round(alignment_hours / 24.0, 1),
            weibull_survival_pct=weibull["survival_probability_pct"],
            components=[
                ComponentRUL(component="bearing", rul_operating_hours=round(bearing_hours, 1), rul_days=round(bearing_hours / 24.0, 1), b10_hours=round(b10, 1), b50_hours=round(b50, 1)),
                ComponentRUL(component="winding", rul_operating_hours=round(winding_hours, 1), rul_days=round(winding_hours / 24.0, 1)),
                ComponentRUL(component="alignment", rul_operating_hours=round(alignment_hours, 1), rul_days=round(alignment_hours / 24.0, 1)),
            ],
        )

        logger.info(
            f"[Agent Gamma] {defect_out.defect_code} mode={mode} domain={domain} pattern={pattern} "
            f"headline RUL {rul_out.rul_days}d method={rul_out.method} rms={rms_rul} ({rms_source})"
        )
        return defect_out, rul_out

    @staticmethod
    def _merge_spectrum(fft_result, spec_result):
        bearing = {
            "BPFI_BEARING_INNER_RACE_PITTING",
            "BPFO_BEARING_OUTER_RACE_CRACK",
            "BSF_BALL_SPIN_WEAR",
            "FTF_CAGE_WEAR",
        }
        fft_code = (fft_result or {}).get("matched_fault_code", "NONE")
        if fft_code in bearing:
            merged = dict(fft_result)
            merged["spectrogram_event"] = (spec_result or {}).get("spectrogram_event")
            return merged
        if spec_result and spec_result.get("defect_label") not in {None, "NONE"}:
            return spec_result
        merged = dict(fft_result or {})
        merged.setdefault("spectrogram_event", None)
        return merged
