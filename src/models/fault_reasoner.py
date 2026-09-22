"""
Evidence fusion for faults the catalog has not named yet.

The named ISO codes (PF001, BPFI, MF002, …) are shortcuts. When Gamma cannot
name a code, this still reads every live channel — vibration, voltage, pressure,
acoustic, thermal, gateway flags — and returns a working diagnosis plus a spare
when the evidence supports one. That is the automation path: detect, reason, draft.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.config import settings
from src.models.fault_bom import FAULT_BOM, resolve_bom
from src.models.iso_20816 import evaluation_velocity_rms
from src.models.live_cues import acoustic_two_x, is_operating, max_thd_pct
from src.models.plant_cues import process_leak_overlay
from src.schemas.predictions import ElectricalHealth, ThermalDeWeathering
from src.schemas.telemetry import TelemetryFrame


PROMOTE_MIN = 62.0


@dataclass(frozen=True)
class Hypothesis:
    defect_code: str
    defect_name: str
    failing_component: str
    part_key: Optional[str]
    confidence: float
    reason: str
    diagnosis_method: str = "evidence_fusion"

    def as_classifier(self) -> Dict[str, Any]:
        return {
            "defect_code": self.defect_code,
            "defect_name": self.defect_name,
            "failing_component": self.failing_component,
            "confidence_percentage": round(self.confidence, 1),
            "diagnosis_method": self.diagnosis_method,
        }


def _bom_part(code: str) -> Optional[str]:
    line = FAULT_BOM.get(code)
    return line.part_key if line else None


def _score_hypotheses(
    frame: TelemetryFrame,
    pattern: str,
    domain: str,
    electrical: Optional[ElectricalHealth],
    thermal: Optional[ThermalDeWeathering],
    history: Optional[List[Dict[str, Any]]],
) -> List[Hypothesis]:
    rows: List[Hypothesis] = []
    rms = evaluation_velocity_rms(frame)
    vuf = float(
        electrical.voltage_unbalance_pct if electrical else frame.em_voltage_imbalance or 0.0
    )
    thd = max_thd_pct(frame)
    leak = process_leak_overlay(frame, history)
    two_x = acoustic_two_x(frame)
    running = is_operating(frame)
    harmonics = list(frame.vibration_harmonics or [])
    shaft_hz = (float(frame.rpm) / 60.0) if float(frame.rpm or 0) > 0 else 0.0

    def near(freq: float, target: float, tol: float = 8.0) -> bool:
        return abs(freq - target) <= tol

    if leak:
        rows.append(
            Hypothesis(
                defect_code="PF001",
                defect_name=str(leak["defect_name"]),
                failing_component=str(leak["failing_component"]),
                part_key="AIR-LEAK-KIT",
                confidence=float(leak["confidence_percentage"]),
                reason=str(leak["defect_name"]),
                diagnosis_method=str(leak["diagnosis_method"]),
            )
        )

    if vuf > settings.VUF_NORMAL_MAX or domain in {"ELECTRICAL", "MIXED"}:
        conf = 86.0 if vuf > settings.VUF_NORMAL_MAX else 64.0
        rows.append(
            Hypothesis(
                defect_code="EF001",
                defect_name="3-Phase Voltage Unbalance & Stator Stress",
                failing_component="Electric Motor Stator Winding / Power Supply",
                part_key=None,
                confidence=conf,
                reason=f"Supply VUF is {vuf:.2f}% (domain {domain}). Fix the feeder / MCC, not the skid.",
                diagnosis_method="evidence_fusion_electrical",
            )
        )

    if thd > settings.THD_WARNING_MAX:
        rows.append(
            Hypothesis(
                defect_code="EF001",
                defect_name="Supply harmonic distortion stressing the stator",
                failing_component="Electric Motor Stator Winding / Power Supply",
                part_key=None,
                confidence=70.0,
                reason=f"Voltage THD {thd:.1f}% is above the IEEE 519 warning band.",
                diagnosis_method="evidence_fusion_thd",
            )
        )

    if two_x is not None and running and rms >= 3.0:
        rows.append(
            Hypothesis(
                defect_code="MF002",
                defect_name="Shaft Angular & Parallel Misalignment (acoustic 2X)",
                failing_component="Motor-to-Compressor Coupling & Shimming",
                part_key="COUPLING-L100",
                confidence=68.0 if rms < 3.0 else 78.0,
                reason=f"Microphone 2× line peak at {float(two_x.frequency):.0f} Hz with RMS {rms:.2f} mm/s.",
                diagnosis_method="acoustic_2x_plus_rms",
            )
        )

    if shaft_hz > 0 and harmonics:
        amp_1x = max((float(h.amplitude) for h in harmonics if near(float(h.frequency), shaft_hz)), default=None)
        amp_2x = max((float(h.amplitude) for h in harmonics if near(float(h.frequency), 2.0 * shaft_hz)), default=None)
        # Precomputed harmonics often store dB (negative). A larger (less negative / positive) 2X wins.
        if amp_2x is not None and (amp_1x is None or amp_2x > amp_1x) and rms >= 2.0:
            rows.append(
                Hypothesis(
                    defect_code="MF002",
                    defect_name="Shaft Angular & Parallel Misalignment",
                    failing_component="Motor-to-Compressor Coupling & Shimming",
                    part_key="COUPLING-L100",
                    confidence=80.0,
                    reason=f"2× shaft harmonic dominates at ~{2.0 * shaft_hz:.0f} Hz.",
                    diagnosis_method="evidence_fusion_2x",
                )
            )
        bpfi_hz = 5.43 * shaft_hz
        if any(near(float(h.frequency), bpfi_hz, 12.0) for h in harmonics) and rms >= 2.8:
            rows.append(
                Hypothesis(
                    defect_code="BPFI",
                    defect_name="Bearing Inner Race Fatigue Pitting",
                    failing_component="Drive-End SKF-6208 Bearing Inner Race",
                    part_key="SKF-6208",
                    confidence=82.0,
                    reason=f"BPFI-band energy near {bpfi_hz:.0f} Hz with RMS {rms:.2f} mm/s.",
                    diagnosis_method="evidence_fusion_bpfi",
                )
            )

    if thermal and thermal.genuine_thermal_overheating and rms >= settings.VIB_WARNING_MAX:
        rows.append(
            Hypothesis(
                defect_code="BPFI",
                defect_name="Progressive bearing wear (heat + vibration, confirm with spectra)",
                failing_component="Drive-End SKF-6208 Bearing Inner Race",
                part_key="SKF-6208",
                confidence=66.0,
                reason=f"Genuine motor heat rise {thermal.delta_temperature_c:.1f}°C with RMS {rms:.2f} mm/s.",
                diagnosis_method="evidence_fusion_thermal",
            )
        )
    elif thermal and thermal.pm_hint == "GREASE":
        rows.append(
            Hypothesis(
                defect_code="ANOMALY_UNCLASSIFIED",
                defect_name="Lubrication / bearing heat — grease before teardown",
                failing_component="Drive-end bearing housing grease path",
                part_key=None,
                confidence=60.0,
                reason="Beta heat pattern points to grease first, not a catalog spare.",
                diagnosis_method="evidence_fusion_grease",
            )
        )
    elif thermal and thermal.pm_hint == "CLEAN_COOLER":
        rows.append(
            Hypothesis(
                defect_code="ANOMALY_UNCLASSIFIED",
                defect_name="Cooler / airflow restriction (weather-compensated heat)",
                failing_component="Package cooler / intake",
                part_key=None,
                confidence=58.0,
                reason="Heat is ambient-driven. Clean the cooler before ordering a mechanical spare.",
                diagnosis_method="evidence_fusion_cooler",
            )
        )

    if pattern in {"NOISE_EXPANSION", "REPEATED_SPIKES"} and rms >= settings.VIB_WARNING_MAX:
        rows.append(
            Hypothesis(
                defect_code="MF001",
                defect_name="Mechanical Structural Looseness",
                failing_component="Compressor Mounting Base Bolts",
                part_key="FOUNDATION-BOLT-KIT",
                confidence=74.0,
                reason=f"Alpha pattern {pattern} with RMS {rms:.2f} mm/s.",
                diagnosis_method="evidence_fusion_looseness",
            )
        )

    if rms >= settings.VIB_WARNING_MAX and not rows:
        rows.append(
            Hypothesis(
                defect_code="ANOMALY_UNCLASSIFIED",
                defect_name=f"ISO 20816 elevated RMS {rms:.2f} mm/s — mechanical energy, name not confirmed",
                failing_component="Mechanical Drive Assembly (coupling / feet / bearings)",
                part_key=None,
                confidence=58.0,
                reason=(
                    f"Vibration RMS {rms:.2f} mm/s is in the warning band, but no leak, VUF, "
                    "2×, or BPFI cue named the part yet. Walk the coupling, feet, and bearing housings."
                ),
                diagnosis_method="evidence_fusion_rms",
            )
        )

    rows.sort(key=lambda item: item.confidence, reverse=True)
    return rows


def fuse_live_evidence(
    frame: TelemetryFrame,
    classified: Dict[str, Any],
    pattern: str = "STABLE",
    domain: str = "MECHANICAL",
    electrical: Optional[ElectricalHealth] = None,
    thermal: Optional[ThermalDeWeathering] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return classifier-shaped dict. Promotes a named fault+spare when evidence wins."""
    code = str(classified.get("defect_code") or "")
    named = code in FAULT_BOM and code not in {"ANOMALY_UNCLASSIFIED", "NORMAL", "UNKNOWN"}
    hypotheses = _score_hypotheses(frame, pattern, domain, electrical, thermal, history)
    best = hypotheses[0] if hypotheses else None

    if named:
        classified = dict(classified)
        classified["reasoning_summary"] = (
            f"Catalog match {code}: {classified.get('defect_name')}. "
            "Live channels agree with a named ISO / plant rule."
        )
        classified["reasoned_part_key"] = _bom_part(code)
        classified["hypotheses"] = [best.as_classifier()] if best else []
        return classified

    if best and best.confidence >= PROMOTE_MIN and best.defect_code not in {"ANOMALY_UNCLASSIFIED", "UNKNOWN"}:
        out = best.as_classifier()
        out["reasoning_summary"] = best.reason
        out["reasoned_part_key"] = best.part_key
        out["hypotheses"] = [h.as_classifier() for h in hypotheses[:3]]
        return out

    classified = dict(classified)
    if code == "NORMAL" and (not best or best.defect_code in {"ANOMALY_UNCLASSIFIED", "NORMAL"}):
        classified["reasoning_summary"] = (
            classified.get("reasoning_summary")
            or "Live vibration, voltage, pressure, acoustic, and thermal channels are inside the healthy envelope."
        )
        classified["reasoned_part_key"] = None
        classified["hypotheses"] = [h.as_classifier() for h in hypotheses[:3]]
        return classified

    if best:
        classified["reasoning_summary"] = best.reason
        classified["reasoned_part_key"] = best.part_key
        classified["hypotheses"] = [h.as_classifier() for h in hypotheses[:3]]
        return classified

    live = code or "UNKNOWN"
    classified["reasoning_summary"] = (
        f"Code {live} is outside the named catalog. Live vibration, voltage, pressure, "
        "acoustic, and thermal channels were scored; none crossed a promote threshold. "
        "A work order is still drafted so the walkdown is automated."
    )
    classified["reasoned_part_key"] = None
    classified["hypotheses"] = []
    return classified


def spare_for_reasoned(classified: Dict[str, Any]) -> Optional[str]:
    key = classified.get("reasoned_part_key")
    if key:
        return str(key)
    return resolve_bom(classified.get("defect_code")).part_key
