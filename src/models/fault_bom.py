"""
Canonical fault prompt: every error knows what it is, what it is not, and which part.

Reader: a shop-floor maintenance person. They should understand the card on one read —
no data-science wording.

Rules (do not break):
- All text, figures, graph labels, and pictograms use plant language: what is wrong,
  where to look, which part, what to do. If a code or ISO name must appear, say what
  it means in the same sentence (e.g. "PF001 — air leak on the discharge line").
- Prefer fitting, coupling, bearing, cable, walk the line over RMS, Zone A, BPFI, DAG.
- Do not put agent names (Alpha / Beta / Gamma / Delta) on the operator card body.
- Pick the part from the defect CODE first, not from free text.
- Never default an unknown error to SKF-6208.
- A leak is fittings / seals. A cable cut is a sensor lead. Voltage unbalance is the supply.
- If the code is missing, fall back to keywords on the failing-component string.
- If still unknown, do not invent a part — return no spare.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class BomLine:
    defect_code: str
    part_key: Optional[str]
    part_label: str
    reason: str


# Canonical map: Gamma defect_code → catalog key (or None = no mechanical spare).
FAULT_BOM: dict[str, BomLine] = {
    "PF001": BomLine(
        "PF001", "AIR-LEAK-KIT", "Air-circuit fitting / seal kit",
        "Compressed-air leak — fittings, drains, cooler joints. Not a bearing.",
    ),
    "BPFI": BomLine(
        "BPFI", "SKF-6208", "SKF 6208-2RS1/C3 drive-end bearing",
        "Inner-race spall — replace the DE bearing.",
    ),
    "BPFO": BomLine(
        "BPFO", "SKF-6208", "SKF 6208-2RS1/C3 NDE bearing",
        "Outer-race flake — same 6208 envelope on this skid.",
    ),
    "MF001": BomLine(
        "MF001", "FOUNDATION-BOLT-KIT", "Foundation bolt / lock-washer kit",
        "Structural looseness — re-torque or replace hold-down hardware.",
    ),
    "MF002": BomLine(
        "MF002", "COUPLING-L100", "Lovejoy L-100 SOX elastomer + shims",
        "2× misalignment — coupling spider and motor-foot shims.",
    ),
    "MF003": BomLine(
        "MF003", None, "No off-the-shelf spare",
        "Rotor imbalance — clean / balance the rotor. Do not order a bearing first.",
    ),
    "EF001": BomLine(
        "EF001", None, "No mechanical spare",
        "Voltage unbalance is the incoming supply / MCC — fix the feeder, not the skid.",
    ),
    "SENSOR": BomLine(
        "SENSOR", None, "No machine spare",
        "Sensor / cable fault — inspect the transducer lead. Do not open the motor.",
    ),
    "HARDWARE_CABLE_FAULT": BomLine(
        "HARDWARE_CABLE_FAULT", None, "No machine spare",
        "Alpha halt — vibration cable is dead while the motor still runs.",
    ),
    "NORMAL": BomLine(
        "NORMAL", None, "No spare",
        "Healthy — no work order spare.",
    ),
    "ANOMALY_UNCLASSIFIED": BomLine(
        "ANOMALY_UNCLASSIFIED", None, "Inspect first",
        "Vibration is up but the name is not confirmed — do not buy a bearing yet.",
    ),
}

_KEYWORD_BOM: tuple[tuple[tuple[str, ...], str], ...] = (
    (("leak", "fitting", "pneumatic", "air-circuit", "air circuit", "pf001"), "PF001"),
    (("bpfi", "inner race", "inner-race"), "BPFI"),
    (("bpfo", "outer race", "outer-race"), "BPFO"),
    (("coupling", "spider", "shim", "misalign", "l-100", "l100", "mf002"), "MF002"),
    (("loosen", "soft foot", "foundation", "hold-down", "mf001"), "MF001"),
    (("imbalance", "unbalance mass", "mf003"), "MF003"),
    (("vuf", "voltage", "stator", "feeder", "ef001"), "EF001"),
    (("cable", "transducer", "sensor halt", "field_not_present"), "SENSOR"),
)


def resolve_bom(
    defect_code: Optional[str] = None,
    failing_component: Optional[str] = None,
    bearing_model: Optional[str] = None,
) -> BomLine:
    """Return the spare line for this error. Unknown codes never become a bearing."""
    code = (defect_code or "").strip().upper()
    if code in FAULT_BOM:
        return FAULT_BOM[code]

    # A named code that is not in the list is unknown. Do not guess from free text
    # (the default bearing model would otherwise look like a SKF-6208 job).
    if code:
        return BomLine(
            defect_code=code,
            part_key=None,
            part_label="Unknown — do not invent a part",
            reason=(
                f"Code {code} is not in the plant catalog. Inspect first; "
                "do not default to a bearing."
            ),
        )

    blob = f"{failing_component or ''} {bearing_model or ''}".lower()
    for tokens, mapped in _KEYWORD_BOM:
        if any(token in blob for token in tokens):
            return FAULT_BOM[mapped]

    return BomLine(
        defect_code="UNKNOWN",
        part_key=None,
        part_label="Unknown — do not invent a part",
        reason="No BOM rule for this error. Inspect first; do not default to a bearing.",
    )


# Operator-facing prompt for each code. Unknown codes use UNKNOWN.
# Write as if the reader is a fitter on the floor: short sentences, plant words,
# figures a person can act on (bar, °C, minutes). Not a lab report.
OPERATOR_PROMPT: dict[str, dict[str, str]] = {
    "PF001": {
        "means": (
            "Compressed air is escaping the discharge circuit — fittings, drain traps, "
            "cooler joints, or a passing solenoid. This is a piping / process leak, not a motor bearing."
        ),
        "notThis": (
            "Healthy or missing vibration does not clear this alert. A leak wastes air and energy "
            "while the screw can stay ISO 20816 Zone A."
        ),
        "why": (
            "Shown when the plant gateway publishes a PF* leak code, or discharge pressure falls, "
            "or the microphone sees a leak-band peak while the package is on."
        ),
        "rootCause": (
            "Process leakage on the air circuit (ISO 11011). Vibration RMS can stay Zone A "
            "while energy and pressure are lost through fittings."
        ),
        "risk": "Continuous air loss, extra compressor run hours, and wasted kWh until the leaking joint is found and sealed.",
    },
    "BPFI": {
        "means": "Inner-race bearing defect — rolling elements hit a spall on the inner race at ball-pass frequency (BPFI).",
        "notThis": "Do not treat this as a leak, a cable cut, or voltage unbalance. The spare is the drive-end bearing.",
        "why": "Shown when vibration RMS is high and a BPFI harmonic (about 5.4× shaft speed) appears with rising motor temperature.",
        "rootCause": "Fatigue, contaminated grease, or electrical fluting of the inner raceway.",
        "risk": "Spalls grow quickly; remaining life is short once BPFI is clear.",
    },
    "BPFO": {
        "means": "Outer-race bearing defect — rolling elements hit a spall on the outer race (BPFO).",
        "notThis": "This is not an air leak or a coupling job. The spare is the NDE bearing.",
        "why": "Shown when vibration RMS is high and a BPFO harmonic appears.",
        "rootCause": "Fatigue or contaminated grease on the outer raceway.",
        "risk": "Spalls grow; plan a bearing change in the RUL window.",
    },
    "MF001": {
        "means": "Mechanical looseness — a fastener, foot, or housing is allowing extra movement at 1× shaft speed.",
        "notThis": "Do not order a bearing first. Re-torque or replace hold-down hardware.",
        "why": "Shown when vibration RMS and 1× harmonic energy rise together while the motor is loaded.",
        "rootCause": "Loose hold-down bolts, soft foot, or a worn housing fit.",
        "risk": "Wear accelerates on bearings and coupling if the machine keeps running loose.",
    },
    "MF002": {
        "means": "Shaft misalignment — motor and compressor centerlines are not collinear, so the coupling sees a 2× shake.",
        "notThis": "This is a coupling / shim job, not an SKF 6208 replacement.",
        "why": "Shown when overall RMS is elevated and a 2× rotational harmonic dominates the vibration spectrum.",
        "rootCause": "Thermal growth, soft foot, foundation settle, or a worn elastomeric coupling spider.",
        "risk": "Cyclic fatigue on bearings, seals, and the coupling element.",
    },
    "MF003": {
        "means": "Rotor imbalance — mass is uneven around the shaft, producing a strong 1× vibration.",
        "notThis": "No catalog spare — clean / balance the rotor. Do not order a bearing first.",
        "why": "Shown when 1× vibration is high relative to other harmonics while the machine is running.",
        "rootCause": "Build-up on the rotor, a missing balance weight, or a bent shaft.",
        "risk": "Bearing overload and rising housing temperature if left unbalanced.",
    },
    "EF001": {
        "means": "Supply voltage unbalance — the three line voltages are unequal, so the motor sees a counter-rotating field.",
        "notThis": "No mechanical spare — fix the incoming supply / MCC, not the skid.",
        "why": "Shown when voltage unbalance (VUF) exceeds the NEMA / IEC warning band on the energy meter.",
        "rootCause": "Unequal single-phase loads, a loose feeder lug, or a degraded PF capacitor.",
        "risk": "Extra stator heating and winding damage if the unbalance stays high.",
    },
    "SENSOR": {
        "means": "The vibration sensor is not producing a real reading while the motor still looks alive.",
        "notThis": "Do not treat 0.0 mm/s as a seized machine. Inspect the transducer lead first.",
        "why": "Shown when acceleration is 0 or missing while current / power are live.",
        "rootCause": "Cut cable, unplugged M12, or a dead transducer.",
        "risk": "A fake machine-down alarm if this is treated as a seized motor. Mechanical faults stay hidden.",
    },
    "HARDWARE_CABLE_FAULT": {
        "means": "The vibration sensor is not producing a real reading while the motor still looks alive.",
        "notThis": "Do not treat 0.0 mm/s as a seized machine. Inspect the transducer lead first.",
        "why": "Agent Alpha halted because IMU vibration is missing or stuck at zero while the package still looks on.",
        "rootCause": "Cut cable, loose connector, or lost 24 V loop power on the accelerometer.",
        "risk": "Mechanical degradation cannot be detected until the sensor path is restored.",
    },
    "NORMAL": {
        "means": "All monitored parameters are inside the healthy envelope. No defect is named.",
        "notThis": "Do not raise a spare or a bearing job from a healthy card.",
        "why": "Shown when Agent Gamma reports NORMAL and remaining life is above the skip threshold.",
        "rootCause": "No active physical defect.",
        "risk": "None — keep the routine lubrication and monitoring schedule.",
    },
    "ANOMALY_UNCLASSIFIED": {
        "means": "Live channels show a developing fault, but no single ISO shortcut has locked yet. The agents still scored vibration, voltage, pressure, acoustic, and thermal evidence.",
        "notThis": "This is not a healthy machine. Do not skip the walkdown, and do not default to SKF 6208 unless BPFI/BPFO evidence appears.",
        "why": "Shown when overall RMS or another live channel is off-nominal without a named harmonic / gateway code.",
        "rootCause": "Unlocalized energy — lubrication, early wear, mixed signature, or a fault the current sensors only see as RMS.",
        "risk": "A work order is still drafted so the inspection is automated. Name the part on the walkdown before a stores pick.",
    },
    "UNKNOWN": {
        "means": "This name is not a catalog shortcut. The agents still reason from every live channel and will attach a spare when the evidence supports one.",
        "notThis": "Do not treat ‘unknown’ as ‘do nothing’. The work order is the automation; the spare is only held until evidence names it.",
        "why": "Shown when the pipeline emitted a code with no shortcut, then scored vibration, voltage, pressure, acoustic, and thermal.",
        "rootCause": "No catalog shortcut. Walk sensors first, then the mechanical / electrical / air-circuit families the evidence ranked.",
        "risk": "Ordering the wrong part is worse than a late part. The draft ticket keeps the job on the board.",
    },
}


def prompt_for_fault(
    defect_code: Optional[str] = None,
    failing_component: Optional[str] = None,
    bearing_model: Optional[str] = None,
    reasoned_part_key: Optional[str] = None,
    reasoning_summary: Optional[str] = None,
) -> dict[str, Any]:
    """Operator prompt for any error: meaning, what-not, spare, and why it is showing.

    Copy must be readable by a maintenance person on first pass — same voice as
    the module prompt: plant words, no unexplained codes, no agent jargon.
    """
    bom = resolve_bom(defect_code, failing_component, bearing_model)
    if not bom.part_key and reasoned_part_key:
        inferred = resolve_bom(None, reasoned_part_key, None)
        if inferred.part_key:
            bom = inferred
        else:
            bom = BomLine(
                defect_code=bom.defect_code,
                part_key=reasoned_part_key,
                part_label=reasoned_part_key,
                reason=reasoning_summary or bom.reason,
            )
    copy = OPERATOR_PROMPT.get(bom.defect_code if bom.defect_code in OPERATOR_PROMPT else "")
    if copy is None:
        copy = OPERATOR_PROMPT.get(str(defect_code or "").upper())
    if copy is None:
        live = (defect_code or bom.defect_code or "UNKNOWN").strip() or "UNKNOWN"
        named = (failing_component or "").strip()
        named_bit = f" (named {named})" if named else ""
        copy = {
            **OPERATOR_PROMPT["UNKNOWN"],
            "means": reasoning_summary
            or (
                f"Code {live}{named_bit} is not a named catalog shortcut. "
                "The agents scored live vibration, voltage, pressure, acoustic, and thermal channels."
            ),
            "why": (
                reasoning_summary
                or "Shown because live evidence left the healthy envelope without a shortcut code."
            ),
            "rootCause": reasoning_summary or bom.reason,
        }
    elif reasoning_summary and str(defect_code or "").upper() in {"ANOMALY_UNCLASSIFIED", "UNKNOWN"}:
        copy = {**copy, "means": reasoning_summary, "why": reasoning_summary, "rootCause": reasoning_summary}
    spare = bom.part_label
    if bom.part_key:
        spare = f"{bom.part_label} ({bom.part_key})"
    return {
        "defect_code": bom.defect_code,
        "part_key": bom.part_key,
        "whatIsIt": copy["means"],
        "whyShowing": copy["why"],
        "notThis": copy.get("notThis"),
        "sparePart": spare,
        "rootCause": copy.get("rootCause") or bom.reason,
        "riskImpact": copy.get("risk") or bom.reason,
        "reason": bom.reason,
    }
