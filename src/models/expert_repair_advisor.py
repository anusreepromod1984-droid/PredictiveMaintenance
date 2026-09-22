"""
Senior Maintenance Expert Repair Knowledge Engine (30+ Years Field Experience)
Provides precise component localization, root-cause diagnosis, and actionable step-by-step
overhaul procedures, required tools, and safety protocols for each identified defect.
"""

from typing import Dict, Any, List
from src.schemas.predictions import MasterMaintenanceGuidance, RepairOption

EXPERT_PLAYBOOK_DB = {
    "BPFI": {
        "location": "Motor Drive-End (DE) Bearing Inner Race Track (Contact Angle Area)",
        "root_cause": "Microscopic subsurface fatigue spalling and grease degradation leading to micro-pitting at 162.4 Hz impact harmonics.",
        "severity": "CRITICAL - RUL < 14 Days",
        "triage": "Inject 15g of high-performance ISO VG 220 Polyurea synthetic grease to form an interim elastohydrodynamic oil film; de-rate motor speed by 10% if vibration exceeds 4.5 mm/s.",
        "overhaul": {
            "title": "Drive-End Precision Bearing Replacement & Shaft Interference Fit",
            "action_type": "PLANNED_OVERHAUL",
            "duration": 2.5,
            "steps": [
                "1. Lock-out / Tag-out (LOTO) 3-phase electrical isolator and verify zero stored mechanical energy.",
                "2. Decouple motor shaft from compressor gearbox using a 3-arm mechanical bearing puller; inspect shaft journal for fretting or score marks.",
                "3. Clean shaft journal with mineral solvent and measure journal diameter using a micrometer (verify tolerance: +0.015mm to +0.025mm).",
                "4. Heat replacement SKF 6208 bearing uniformly to exactly 110°C (230°F) using an induction heater with demagnetization cycle (NEVER use open flame or cold hammer blows).",
                "5. Slide heated bearing firmly against shaft abutment shoulder and hold until cool; fit new nitrile double-lip oil seals (2RS1).",
                "6. Re-couple motor and perform laser shaft alignment to < 0.03mm tolerance. Run 60-second baseline test to verify vibration < 1.2 mm/s."
            ],
            "tools": [
                "Induction Bearing Heater (with temp probe & auto-demag)",
                "3-Arm Mechanical / Hydraulic Bearing Puller",
                "Outside Micrometer (0-100mm) & Calibrated Torque Wrench (M16 @ 175 Nm)",
                "Laser Shaft Alignment Tool (e.g. Easy-Laser / Pruftechnik)",
                "Replacement SKF-6208-2RS1 Precision Bearing & High-Temp Polyurea Grease"
            ],
            "safety": [
                "Wear heat-resistant Kevlar gloves (rated to 250°C) when handling induction-heated bearing.",
                "Verify Electrical Lock-Out Tag-Out (LOTO) with calibrated multimeter before touching junction box.",
                "Wear safety glasses with side shields during bearing extraction."
            ]
        },
        "tips": [
            "Senior Tip: Check grease color during teardown. If grease is blackened with bronze sheen, check for shaft stray currents (install shaft grounding ring to prevent electrical discharge machining EDM).",
            "Senior Tip: Never pack bearing cavity more than 30% to 50% with grease; over-greasing causes churning and premature thermal failure."
        ]
    },
    "MF002": {
        "location": "Intermediate Flexible Shaft Coupling Joint between Electric Motor and Compressor",
        "root_cause": "Angular and parallel shaft misalignment causing 2X RPM radial vibration and excessive coupling shear stress.",
        "severity": "WARNING - Degraded State",
        "triage": "Inspect flexible coupling spider/elastomer insert for rubber shavings; tighten coupling set-screws.",
        "overhaul": {
            "title": "Precision Laser Coupling Realignment & Soft-Foot Correction",
            "action_type": "PLANNED_OVERHAUL",
            "duration": 1.5,
            "steps": [
                "1. Isolate motor power and unbolt coupling safety guard.",
                "2. Mount laser alignment brackets on motor and compressor shafts.",
                "3. Perform Soft-Foot check: loosen each foundation bolt one-by-one with dial indicator (deflection must be < 0.05mm).",
                "4. Add stainless steel precision shims under motor feet to eliminate angular and offset misalignment to < 0.03 mm/100mm.",
                "5. Re-torque M16 foundation bolts in cross-star sequence to 175 Nm; re-verify alignment after full torque."
            ],
            "tools": [
                "Laser Alignment System",
                "Pre-cut Stainless Steel Shims (0.05mm, 0.1mm, 0.2mm, 0.5mm)",
                "Calibrated Click Torque Wrench (175 Nm)",
                "Dial Test Indicator (0.01mm resolution)"
            ],
            "safety": [
                "Ensure machine cannot rotate during dial indicator setup.",
                "Use correct shim handling tools to prevent pinch injuries."
            ]
        },
        "tips": [
            "Senior Tip: Always account for thermal growth. As the compressor discharge warms up by 40°C, the compressor shaft rises by ~0.08mm relative to the cold motor."
        ]
    },
    "MF001": {
        "location": "Motor Baseplate Foundation Footing Bolts (#2 and #4 Foot)",
        "root_cause": "Mechanical structural looseness due to foundation bolt relaxation under cyclic load.",
        "severity": "WARNING - Structural Looseness",
        "triage": "Tighten motor footing bolts; inspect foundation grout bed for hairline cracking or oil intrusion.",
        "overhaul": {
            "title": "Foundation Bolt Re-Torque & Grout Bed Re-Leveling",
            "action_type": "PREVENTIVE_ADJUSTMENT",
            "duration": 1.0,
            "steps": [
                "1. Clean bolt threads and apply medium-strength threadlocker (Loctite 243).",
                "2. Torque all 4 foundation bolts to 175 Nm using calibrated torque wrench.",
                "3. Inspect concrete foundation grout bed for voids or oil soaking."
            ],
            "tools": ["Calibrated Torque Wrench (175 Nm)", "Loctite 243 Threadlocker", "Feeler Gauges"],
            "safety": ["LOTO before working near drive belts/pulleys."]
        },
        "tips": ["Senior Tip: If bolts loosen repeatedly, replace split spring washers with Nord-Lock wedge-locking washers."]
    },
    "MF003": {
        "location": "Motor / Compressor Drive Shaft Rotor Assembly",
        "root_cause": "Rotor dynamic unbalance causing 1X rotational vibration and elevated radial bearing load.",
        "severity": "WARNING - Dynamic Imbalance",
        "triage": "Clean compressor impellers/rotor lobes of particulate sludge build-up; inspect balance weight clip.",
        "overhaul": {
            "title": "Single-Plane Dynamic Field Balancing & Vibration Baseline Certification",
            "action_type": "PLANNED_OVERHAUL",
            "duration": 2.0,
            "steps": [
                "1. Clean motor cooling fan blades and compressor rotor lobes with solvent degreaser.",
                "2. Mount optical tachometer and vibration accelerometer on drive-end bearing housing.",
                "3. Perform trial run to record 1X amplitude and phase angle (polar vector).",
                "4. Add trial balance weight (15g @ 180° opposite unbalance vector) and measure response.",
                "5. Calculate and tack-weld permanent correction weight to achieve ISO 1940 Grade G2.5 (< 1.0 mm/s RMS)."
            ],
            "tools": ["Dynamic Field Balancing Kit (Adash / Schenck)", "Precision Scale (0.1g)", "Optical Phase Tachometer", "Correction Balance Weights"],
            "safety": ["Maintain 2-meter safety perimeter during open trial runs; wear eye and ear protection."]
        },
        "tips": ["Senior Tip: 80% of perceived dynamic unbalance is actually asymmetric dirt accumulation on the motor cooling fan. Always clean the fan before re-balancing!"]
    },
    "BPFO": {
        "location": "Non-Drive-End (NDE) Bearing Outer Race Ring",
        "root_cause": "Outer race micro-flaking and contact fatigue at 108.2 Hz pass frequency.",
        "severity": "WARNING - Bearing Outer Race Wear",
        "triage": "Purge bearing cavity and re-lubricate with 20g synthetic grease; monitor temperature trend.",
        "overhaul": {
            "title": "Non-Drive-End Bearing Extraction & End-Bell Housing Re-Sleeving",
            "action_type": "PLANNED_OVERHAUL",
            "duration": 2.0,
            "steps": [
                "1. Disconnect motor end-bell cover and extract old bearing using internal puller.",
                "2. Inspect bearing housing bore with inside micrometer (check for ovality or fretting).",
                "3. Press new SKF-6208 bearing into housing using mechanical fitting sleeve.",
                "4. Install new wave spring washer to maintain axial pre-load."
            ],
            "tools": ["Internal Bearing Extractor Kit", "Inside Micrometer (50-100mm)", "Hydraulic Fitting Sleeve"],
            "safety": ["Wear cut-resistant gloves during snap ring and wave washer removal."]
        },
        "tips": ["Senior Tip: If outer race rotates inside end-bell housing, install a bronze repair sleeve or Loctite 609 retaining compound."]
    },
    "EF001": {
        "location": "Electric Motor 3-Phase Stator Windings (Phase V/W)",
        "root_cause": "Voltage unbalance (> 2.0% VUF) inducing negative-sequence circulating currents and localized winding hot-spots.",
        "severity": "CRITICAL - Electrical Degradation",
        "triage": "Inspect breaker terminals for loose lugs or high-resistance contact oxidation; re-torque terminal studs.",
        "overhaul": {
            "title": "3-Phase Power Distribution Phase Re-Balancing & Insulation Megger Test",
            "action_type": "PLANNED_OVERHAUL",
            "duration": 1.5,
            "steps": [
                "1. Measure phase-to-phase voltages (Vr, Vy, Vb) at MCC distribution panel under load.",
                "2. Perform 1000V DC Insulation Resistance (Megger) test between windings and ground (> 100 MΩ required).",
                "3. Check for loose busbar connections or asymmetrical upstream transformer tapping.",
                "4. Redistribute single-phase auxiliary branch circuits across MCC phases to restore VUF < 1.0%."
            ],
            "tools": ["Fluke 1000V Insulation Tester (Megger)", "True-RMS Clamp Meter", "Infrared Thermal Camera"],
            "safety": ["Strict NFPA 70E Arc Flash PPE Category 2 required during MCC panel inspection."]
        },
        "tips": ["Senior Tip: A 3% voltage unbalance causes a 25% to 30% temperature rise in induction motor windings, cutting insulation life in half!"]
    },
    "SENSOR": {
        "location": "Vibration Transducer Cable & Connector Terminal Block",
        "root_cause": "Physical sensor disconnect or wire chafing — motor energized (> 10A) but acceleration reading flatlined at 0.0 mm/s.",
        "severity": "SENSOR_FAULT - False Alarm Suppressed",
        "triage": "Inspect M12 sensor connector pin continuity; verify 4-20mA loop excitation power supply.",
        "overhaul": {
            "title": "Transducer Cable Re-Termination & Shielded Cable Routing",
            "action_type": "PREVENTIVE_ADJUSTMENT",
            "duration": 0.5,
            "steps": [
                "1. Check BNC / M12 sensor connector pins for moisture, corrosion, or bent pins.",
                "2. Verify loop voltage is between 18V and 24V DC using calibrated multimeter.",
                "3. Inspect cable jacket along conveyor/motor frame for mechanical pinch points.",
                "4. Re-route shielded sensor cable away from high-voltage 400V VFD power leads to prevent EMI noise."
            ],
            "tools": ["Digital Multimeter", "Wire Stripper & Crimper", "Replacement Shielded M12 Cable"],
            "safety": ["Avoid pinch points near motor shaft while inspecting sensor mounting stud."]
        },
        "tips": ["Senior Tip: Always ground sensor shielding at the control panel end ONLY to eliminate 50Hz ground loop noise!"]
    },
    "NORMAL": {
        "location": "All Components Operating Within Baseline",
        "root_cause": "No active physical defects detected. Vibration, thermal delta, and power quality within ISO 10816-3 Class II limits.",
        "severity": "HEALTHY - Baseline Certified",
        "triage": "Maintain standard routine lubrication schedule (next grease replenishment due in 45 days).",
        "overhaul": {
            "title": "Routine Predictive Condition Monitoring & Baseline Verification",
            "action_type": "PREVENTIVE_ADJUSTMENT",
            "duration": 0.25,
            "steps": [
                "1. Continue 24/7 continuous autonomous monitoring via Agent Alpha, Beta, Gamma, and Delta.",
                "2. Perform monthly acoustic ultrasound grease listening during operational rotation."
            ],
            "tools": ["Ultrasound Grease Gun Listening Probe"],
            "safety": ["Standard plant PPE."]
        },
        "tips": ["Senior Tip: Consistent baseline conditions mean optimal MTBF. Do not over-grease bearings when operating normally!"]
    },
    "ANOMALY_UNCLASSIFIED": {
        "location": "Rotating Machinery Drive Train & Bearing Assemblies",
        "root_cause": "Elevated overall vibration amplitude exceeding ISO 10816 baseline without isolated discrete harmonic peaks. Indicates developing general mechanical wear or early-stage fatigue.",
        "severity": "WARNING - Elevated Vibration",
        "triage": "Perform tactile temperature check and verify lubrication grease levels. De-rate operational speed by 10% if vibration continues to rise.",
        "overhaul": {
            "title": "Comprehensive Vibration Diagnostic Survey & Mechanical Inspection",
            "action_type": "PREVENTIVE_ADJUSTMENT",
            "duration": 1.5,
            "steps": [
                "1. Perform multi-point tri-axial vibration survey at motor drive end, non-drive end, and compressor casing.",
                "2. Inspect mechanical fasteners, belt tension, and coupling spider for preliminary wear signs.",
                "3. Collect high-resolution waveform for demodulated envelope frequency analysis."
            ],
            "tools": ["Portable Tri-Axial Vibration Analyzer", "Stroboscope Tachometer", "Calibrated Torque Wrench"],
            "safety": ["Standard plant PPE and hearing protection around running machinery."]
        },
        "tips": ["Senior Tip: When broad-band vibration rises without single-frequency peaks, check lubrication quantity first before planning component teardown."]
    },
    "PF001": {
        "location": "Compressed-air discharge piping, fittings, drain traps, and cooler joints",
        "root_cause": "Process leakage on the air circuit (ISO 11011). Vibration RMS can stay ISO 20816 Zone A while energy and pressure are lost through fittings.",
        "severity": "WARNING - Compressed Air Leak",
        "triage": "Walk the discharge line with ultrasonic leak detector; soap-test unions, drain traps, and safety valve. Do not treat this as a bearing failure.",
        "overhaul": {
            "title": "Compressed-Air Leak Survey & Repair (ISO 11011)",
            "action_type": "PREVENTIVE_ADJUSTMENT",
            "duration": 1.0,
            "steps": [
                "1. Confirm package is loaded and record discharge pressure vs the last 30 minutes of historian data.",
                "2. Survey joints, flexible hoses, condensate drains, and cooler cores with an ultrasonic leak detector.",
                "3. Isolate and retighten or replace the leaking fitting; re-verify pressure hold after 10 minutes.",
                "4. Log leak class and estimated lost kWh for the energy KPI (ISO 11011 / ISO 50006)."
            ],
            "tools": [
                "Ultrasonic compressed-air leak detector",
                "Leak detection solution / soap spray",
                "Calibrated pressure gauge",
                "Replacement fittings / PTFE tape per plant spec"
            ],
            "safety": [
                "Wear hearing protection around blow-off.",
                "Do not isolate the safety valve while the vessel is pressurized."
            ]
        },
        "tips": [
            "Senior Tip: A 3 mm hole at 7 bar wastes several kW continuously. Fix leaks before planning a bearing change.",
            "Senior Tip: If PF001 persists after fittings are tight, check unloader/drain solenoid passing."
        ]
    },
    "UNKNOWN": {
        "location": "Not confirmed — inspect before buying parts",
        "root_cause": "The live rules did not name a catalog defect. Do not treat this as a bearing job.",
        "severity": "WATCH - Unclassified",
        "triage": "Verify live sensors, then walk vibration, pressure, and voltage before ordering any spare.",
        "overhaul": {
            "title": "Confirm the fault name before a parts order",
            "action_type": "PREVENTIVE_ADJUSTMENT",
            "duration": 0.5,
            "steps": [
                "1. Confirm the red-card name and the Trigger row on the AI tab.",
                "2. Do not default to SKF 6208 unless the code is BPFI/BPFO.",
                "3. If the name is still unknown, collect a spectrum and a pressure/electrical snapshot and re-run Diagnose."
            ],
            "tools": ["Live dashboard Trigger row", "Portable vibration meter", "Clamp meter"],
            "safety": ["Standard plant PPE."]
        },
        "tips": [
            "Senior Tip: Wrong spare is worse than a late spare. Name the fault first."
        ]
    }
}


class ExpertRepairAdvisor:
    """Expert Repair Advisor Engine."""

    def get_repair_guidance(self, defect_code: str, component_name: str) -> MasterMaintenanceGuidance:
        """Retrieves 30-year expert repair playbook for the identified defect."""
        alias = {
            "HARDWARE_CABLE_FAULT": "SENSOR",
            "FIELD_NOT_PRESENT": "SENSOR",
        }
        key = alias.get(defect_code, defect_code)
        data = EXPERT_PLAYBOOK_DB.get(key) or EXPERT_PLAYBOOK_DB["UNKNOWN"]
        
        overhaul_data = data["overhaul"]
        overhaul_option = RepairOption(
            option_title=overhaul_data["title"],
            action_type=overhaul_data["action_type"],
            estimated_duration_hours=overhaul_data["duration"],
            step_by_step_instructions=overhaul_data["steps"],
            required_tools_and_materials=overhaul_data["tools"],
            safety_precautions=overhaul_data["safety"]
        )

        return MasterMaintenanceGuidance(
            defect_code=defect_code,
            component_exact_location=data["location"],
            root_cause_mechanism=data["root_cause"],
            severity_level=data["severity"],
            immediate_field_triage=data["triage"],
            planned_overhaul_playbook=overhaul_option,
            expert_tips=data["tips"]
        )
