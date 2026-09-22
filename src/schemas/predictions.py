"""
Prediction & AI Agent Response Contracts (Pydantic v2)
Standardized response formats for live diagnostic outputs, health status,
intelligent supply chain sourcing, and 30-year expert maintenance repair guidance.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class CableCheckStatus(BaseModel):
    """Agent Alpha sensor quality + degradation-pattern contract (NAMUR NE107)."""
    status: str = Field(..., description="VALID or a hardware fault code (HARDWARE_CABLE_FAULT, SENSOR_OUT_OF_RANGE, FIELD_NOT_PRESENT, ...)")
    namur_status: str = Field(default="GOOD", description="GOOD, UNCERTAIN, BAD, or MAINTENANCE")
    namur_ne43_signal_valid: bool = Field(default=True, description="True if software plausibility checks pass (Rules 1-4)")
    alert_suppressed: bool = Field(..., description="True if fake breakdown alarm was suppressed by Agent Alpha")
    pattern_recognition_status: str = Field(default="STABLE", description="STEADY_CLIMB, STUCK_HIGH, SUDDEN_JUMP_FLAT, NOISE_EXPANSION, REPEATED_SPIKES, STABLE, or HARDWARE_DISCONNECT")
    sensor_drift_rate_per_min: float = Field(default=0.0, description="Calculated slope dx/dt of sensor climbing trend")
    climbing_trend_detected: bool = Field(default=False, description="True if continuous upward slope or active pattern detected")
    buffer_write: str = Field(default="accepted", description="accepted or rejected — rejected samples never enter the ring buffer")
    fault_reason: Optional[str] = Field(default=None, description="Diagnostic description if cable fault or plausibility anomaly detected")
    trace_id: Optional[str] = Field(default=None, description="Unique execution trace ID")



class RepairOption(BaseModel):
    """Actionable repair option tailored by field severity."""
    option_title: str
    action_type: str = Field(description="IMMEDIATE_TRIAGE, PLANNED_OVERHAUL, or PREVENTIVE_ADJUSTMENT")
    estimated_duration_hours: float
    step_by_step_instructions: List[str]
    required_tools_and_materials: List[str]
    safety_precautions: List[str]


class MasterMaintenanceGuidance(BaseModel):
    """Comprehensive 30-Year Senior Expert Repair Playbook."""
    defect_code: str
    component_exact_location: str
    root_cause_mechanism: str
    severity_level: str
    immediate_field_triage: str
    planned_overhaul_playbook: RepairOption
    expert_tips: List[str]


class DefectLocalization(BaseModel):
    """Component-level fault diagnosis output."""
    defect_code: str = Field(..., description="Primary fault code: MF001 (Looseness), MF002 (Misalignment), MF003 (Imbalance), BPFI (Bearing Inner Race)")
    defect_name: str = Field(..., description="Human-readable defect title")
    failing_component: str = Field(..., description="Exact part name (e.g. SKF-6208 Bearing Inner Race)")
    confidence_percentage: float = Field(..., description="Rule/catalog confidence (not a calibrated ML probability unless model_version says so)")
    dominant_frequencies_hz: List[float] = Field(default_factory=list, description="Peak harmonic frequencies causing fault")
    diagnosis_method: str = Field(default="iso13379_catalog_rules", description="iso20816_rms_only when only IMU-Acceleration is present; iso13379 when X/Y/Z spectrum is present")
    prediction_mode: Optional[str] = Field(default=None, description="imu_rms, triaxial_rms, or triaxial_spectrum")
    spectrum_source: Optional[str] = Field(default=None, description="imu_rms, triaxial_rms, hilbert_envelope_fft, stft_spectrogram, or precomputed_harmonics")
    spectrogram_event: Optional[str] = Field(
        default=None,
        description="1x_persistent, 2x_persistent, broadband_stripe, or none — from the 0–50 Hz STFT",
    )
    expert_repair_guidance: Optional[MasterMaintenanceGuidance] = None
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Why this name and spare were chosen from live channels when the catalog had no code",
    )
    reasoned_part_key: Optional[str] = Field(
        default=None,
        description="Catalog spare key inferred from evidence fusion; None if inspect-first",
    )


class ComponentRUL(BaseModel):
    """Per-component remaining life."""
    component: str
    rul_operating_hours: float
    rul_days: float
    b10_hours: Optional[float] = None
    b50_hours: Optional[float] = None


class RULPrediction(BaseModel):
    """Remaining Useful Life countdown outputs."""
    rul_operating_hours: float = Field(..., description="Headline remaining hours = min(component clocks)")
    rul_days: float = Field(..., description="Headline remaining days")
    confidence_interval_bounds: str = Field(..., description="Weibull B10/B50 band when available")
    recommended_repair_by_date: str = Field(..., description="Recommended deadline date for maintenance")
    model_version: str = Field(default="physics_weibull_v1", description="Logged on every prediction")
    method: str = Field(default="physics_wear_law_plus_weibull", description="PINN is not used until a trained artifact exists")
    bearing_rul_days: Optional[float] = None
    winding_rul_days: Optional[float] = None
    alignment_rul_days: Optional[float] = None
    components: List[ComponentRUL] = Field(default_factory=list)
    weibull_survival_pct: Optional[float] = None


class ThermalDeWeathering(BaseModel):
    """Agent Beta Thermal Normalization Outputs."""
    delta_temperature_c: float = Field(..., description="T_motor - T_ambient heat rise above ambient")
    ambient_drift_compensated: bool = Field(..., description="True if site ambient drift was normalized")
    genuine_thermal_overheating: bool = Field(..., description="True if thermal rise is caused by internal mechanical friction")
    pm_hint: Optional[str] = Field(default=None, description="CLEAN_COOLER, GREASE, or None")


class ElectricalHealth(BaseModel):
    """Agent Beta Power Quality Outputs."""
    voltage_unbalance_pct: float = Field(..., description="Voltage unbalance factor VUF %")
    machine_load_pct: float = Field(..., description="Machine instant operational load %")
    stator_winding_status: str = Field(..., description="Normal, Overloaded, or Phase Imbalance Warning")
    isolated_failure_domain: str = Field(default="MECHANICAL", description="MECHANICAL, ELECTRICAL, MIXED, or ENVIRONMENTAL")
    thd_pct: Optional[float] = None
    power_factor: Optional[float] = None
    energy_residual_kw: Optional[float] = None
    anomaly_score: Optional[float] = Field(default=None, description="0–100. Isolation Forest when fitted; heuristic otherwise")
    isolation_forest_status: Optional[str] = None
    isolation_forest_fitted: bool = False


class SourcingStep(BaseModel):
    """One rung on Agent Delta's CRM → stores → vendor → marketplace ladder."""
    step: int
    name: str
    status: str = Field(..., description="pass, out, skip, open, or none")
    detail: str


class PurchaseOption(BaseModel):
    """A clickable buy / locator option when plant stores and tagged vendors fail."""
    tier: str = Field(..., description="oem_locator, india_b2b, or global_mro")
    name: str
    url: str
    what_it_finds: str
    quote_inr: Optional[int] = None
    quote_note: str = "Catalog estimate — confirm live stock and GST on the site"
    lead_note: Optional[str] = None
    genuine_oem: bool = False


class SupplierSourcingIntelligence(BaseModel):
    """Agent Delta OEM and Alternate Compatible Supplier Sourcing Intelligence."""
    oem_supplier_name: str = Field(default="SKF Bearings Ltd.", description="Original Equipment Manufacturer Name")
    oem_part_number: str = Field(default="SKF-6208-2RS1", description="Original BOM Part Number")
    oem_still_in_market: bool = Field(default=True, description="True if original supplier still active and supplies part")
    oem_lead_time_days: int = Field(default=3, description="Delivery lead time from original supplier in days")

    # Alternate / Compatible Part Matching Branch
    alternate_supplier_needed: bool = Field(default=False, description="True if OEM is discontinued, out of stock, or lead time exceeds RUL")
    alternate_supplier_name: Optional[str] = Field(default="NSK Corporation / FAG Schaeffler", description="Verified Alternate Supplier")
    compatible_part_number: Optional[str] = Field(default="NSK 6208-DDU / FAG 6208-2RSR", description="100% Form-Fit-Function Compatible Part Number")
    compatibility_standard: Optional[str] = Field(default="ISO 15 / DIN 625-1 Compliant", description="Interchangeability standard verification")
    alternate_lead_time_days: Optional[int] = Field(default=2, description="Delivery lead time from alternate supplier")
    sourcing_recommendation: str = Field(
        default="OEM_DIRECT_ORDER",
        description="RESERVE_FROM_STORES, TAGGED_VENDOR_ORDER, OEM_DIRECT_ORDER, COMPATIBLE_ALTERNATE_SOURCED, or MARKETPLACE_RFQ",
    )

    crm_status: str = Field(default="ADVISORY_DRAFT", description="ADVISORY_DRAFT until a live CMMS/CRM create returns a foreign id")
    stores_qty: int = 0
    stores_bin: Optional[str] = None
    tagged_vendor_name: Optional[str] = None
    tagged_vendor_qty: int = 0
    winning_source: Optional[str] = None
    ladder: List[SourcingStep] = Field(default_factory=list)
    purchase_options: List[PurchaseOption] = Field(default_factory=list)


class OemMailDraft(BaseModel):
    """Agent Delta close-the-loop mail: issue, spare, due date, warehouse, buy options."""
    subject: str
    message: str
    preview: str
    warehouse_in_stock: bool = False
    required_by: Optional[str] = None
    part_number: Optional[str] = None
    sent: bool = False
    sent_at: Optional[str] = None
    dispatch_attempted: bool = False


class CMMSWorkOrder(BaseModel):
    """Agent Delta advisory ticket. CMMS remains system of record when connected."""
    work_order_id: str = Field(..., description="Foreign CMMS id when connected; otherwise ADVISORY-DRAFT-*")
    ticket_type: str = Field(default="PDM_CORRECTIVE", description="PDM_CORRECTIVE, PM_CONDITION, or PM_USAGE")
    is_advisory_draft: bool = Field(default=True, description="True until a live CMMS create returns a foreign id")
    scheduled_repair_window: str = Field(..., description="Next approved window; not a hardcoded Sunday slot")
    scheduled_repair_at: Optional[str] = Field(default=None, description="ISO start of the approved crew window")
    scheduled_repair_end: Optional[str] = Field(default=None, description="ISO end of the approved crew window")
    pm_due_date: Optional[str] = Field(default=None, description="Date the repair or standing PM is due")
    repair_crew: Optional[str] = Field(default=None, description="Crew named on the shift calendar")
    reserved_spare_part_bom: str = Field(..., description="Requested spare; not an ERP lock until ATP is live")
    reserved_warehouse_bin: str = Field(default="UNCONFIRMED_ERP", description="ERP bin when ATP is connected")
    is_duplicate: bool = Field(default=False, description="True if this defect already has an active open work order")
    sourcing_intelligence: Optional[SupplierSourcingIntelligence] = Field(default_factory=SupplierSourcingIntelligence)
    oem_mail: Optional[OemMailDraft] = Field(default=None, description="Agent-composed OEM inventory email; sent when the loop is closed")


class PredictRULResponse(BaseModel):
    """Complete 4-Agent End-to-End Prediction Response."""
    machine_id: str
    trace_id: str = Field(default="", description="Unique distributed tracing identifier for this evaluation")
    timestamp: datetime
    overall_health_score: float = Field(default=100.0, description="ISO 20816-3 zone-mapped health percentage")
    overall_health_status: str = Field(default="HEALTHY", description="HEALTHY, WATCH, DEGRADED, CRITICAL, or SENSOR_FAULT")
    iso_vibration_zone: Optional[str] = Field(default=None, description="ISO 20816-3 zone A/B/C/D from velocity RMS")
    iso_machine_group: Optional[str] = Field(default=None, description="ISO 20816-3 machine group 1 or 2")
    iso_support: Optional[str] = Field(default=None, description="rigid or flexible foundation")
    card_type: str = Field(default="MONITOR", description="SENSOR_FAULT, MACHINE_RISK, PM_DUE, or MONITOR")
    skip_reason: Optional[str] = Field(default=None, description="HEALTHY, OPEN_WO, POLICY, or SIGNAL_QUALITY when Delta is skipped")
    cable_check: CableCheckStatus
    defect_localization: Optional[DefectLocalization] = None
    rul_prediction: Optional[RULPrediction] = None
    thermal_de_weathering: Optional[ThermalDeWeathering] = None
    electrical_health: Optional[ElectricalHealth] = None
    cmms_work_order: Optional[CMMSWorkOrder] = None


class MaintenanceCompleteRequest(BaseModel):
    machine_id: str = Field(..., alias="machineId")
    work_order_id: Optional[str] = Field(default=None, alias="workOrderId")
    new_bearing_model: Optional[str] = Field(default=None, alias="newBearingModel")
    component: str = Field(default="DE_bearing")
    asset_class: Optional[str] = Field(default=None, alias="assetClass")
    hours_at_install: float = Field(default=0.0, alias="hoursAtInstall")
    hours_at_event: Optional[float] = Field(default=None, alias="hoursAtEvent")
    event_type: str = Field(default="replaced_pm", alias="eventType", description="failed | replaced_pm | still_running")

    model_config = {"populate_by_name": True}


class MaintenanceCompleteResponse(BaseModel):
    machine_id: str
    buffer_reset: bool
    baseline_id: str
    life_event_recorded: bool
    message: str


class HealthStatusResponse(BaseModel):
    """Quick high-level health response."""
    status: str = Field(default="UP")
    app_name: str = Field(default="Agentic AI Predictive & Preventive Maintenance System (APMS)")
    version: str = Field(default="1.0.0")
    database_connected: bool = Field(default=False)
    redis_connected: bool = Field(default=False)
    mqtt_configured: bool = Field(default=False)
    ai_models_loaded: bool = Field(default=True)
    fitted_artifacts_loaded: bool = Field(default=False, description="True only when weibull.json / classifier.ubj / pinn.pt exist")
    rul_model_version: str = Field(default="physics_weibull_v1")
    detail: Optional[str] = None
