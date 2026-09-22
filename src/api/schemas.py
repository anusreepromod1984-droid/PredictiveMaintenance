"""
Pydantic Schemas for 15-Parameter Telemetry, AI Agent Inputs, and Prediction Outputs.
Matches exact MQTT Topics: pdm/pressure_humidity, pdm/vibration, pdm/temperature_noise, pdm/orientation, pdm/electricity.
"""

from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field


# -------------------------------------------------------------
# 1. 15-PARAMETER TELEMETRY INPUT SCHEMAS (FROM MQTT / DB)
# -------------------------------------------------------------

class PressureHumidityPayload(BaseModel):
    pressure_bar: float = Field(..., example=7.5, description="Line Fluid/Hydraulic Pressure in Bar")
    pressure_psi: float = Field(..., example=108.7, description="Pressure in PSI")
    pressure_change_rate: float = Field(0.0, example=0.02, description="Pressure Change Rate (Delta P / Delta t)")
    humidity_rh_pct: float = Field(..., example=55.0, description="Cabinet Relative Humidity %")


class VibrationPayload(BaseModel):
    vibration_velocity_rms: float = Field(..., example=2.41, description="Vibration Velocity in mm/sec RMS")
    top_5_frequencies: List[float] = Field(default_factory=list, example=[25.0, 50.0, 100.0, 162.4, 300.0])
    top_5_amplitudes: List[float] = Field(default_factory=list, example=[0.12, 0.45, 0.08, 1.85, 0.05])
    shaft_rpm: float = Field(..., example=1480.0, description="Current Shaft Rotational Speed RPM")
    motor_fault_codes: List[str] = Field(default_factory=list, example=["F231"])


class TemperatureNoisePayload(BaseModel):
    motor_temp_celsius: float = Field(..., example=74.2, description="Motor Temperature in °C")
    compressor_head_temp_celsius: float = Field(..., example=82.1, description="Compressor Head Temperature in °C")
    ambient_temp_celsius: float = Field(25.0, example=28.5, description="Ambient Factory Temperature in °C")
    acoustic_noise_db: float = Field(..., example=84.5, description="Microphone Acoustic Noise in dB")
    acoustic_top5_frequencies: List[float] = Field(default_factory=list, example=[1200.0, 2400.0, 4800.0, 9600.0, 12000.0])


class OrientationPayload(BaseModel):
    roll_deg: float = Field(..., example=0.2, description="Shaft Roll Angle in Degrees")
    pitch_deg: float = Field(..., example=0.1, description="Shaft Pitch Angle in Degrees")
    yaw_deg: float = Field(..., example=0.4, description="Shaft Yaw Angle in Degrees")


class ElectricityPayload(BaseModel):
    current_irms_r_y_b: List[float] = Field(..., example=[64.2, 64.8, 63.9], description="3-Phase Current Ir, Iy, Ib in Amps")
    machine_load_pct: float = Field(..., example=82.0, description="Machine Load Percentage %")
    voltage_vrms_r_y_b: List[float] = Field(..., example=[415.0, 414.2, 415.5], description="3-Phase Voltage Vr, Vy, Vb in Volts")
    voltage_imbalance_pct: float = Field(..., example=0.31, description="3-Phase Voltage Imbalance %")
    power_kw: float = Field(..., example=45.2, description="Active Power in kW")
    energy_kwh: float = Field(..., example=12450.8, description="Cumulative Energy in kWh")
    power_factor_pf: float = Field(..., example=0.92, description="Average Power Factor (0 to 1.0)")
    thd_pct_r_y_b: List[float] = Field(..., example=[2.1, 2.3, 2.0], description="Harmonic Distortion %THD for Vr, Vy, Vb")
    frequency_hz: float = Field(..., example=50.02, description="Power Grid Frequency in Hz")
    frequency_deviation_pct: float = Field(..., example=0.04, description="Frequency Deviation %")


class IntegratedTelemetryFrame(BaseModel):
    machine_id: str = Field(..., example="COMP-04", description="Unique Machine Equipment Tag")
    timestamp: str = Field(..., example="2026-08-27T11:30:00Z")
    param_12_plc_status: int = Field(0, example=0, description="PLC Code Diagnostic Status: 0=OK, 1=Sensor Disconnect/Card Fault")
    cumulative_run_hours: float = Field(4520.0, example=4520.0, description="Total Machine Operating Hours")
    
    pressure_humidity: PressureHumidityPayload
    vibration: VibrationPayload
    temperature_noise: TemperatureNoisePayload
    orientation: OrientationPayload
    electricity: ElectricityPayload


# -------------------------------------------------------------
# 2. AI AGENT PREDICTION RESPONSE SCHEMAS
# -------------------------------------------------------------

class AIPredictionResponse(BaseModel):
    machine_id: str = Field(..., example="COMP-04")
    timestamp: str = Field(..., example="2026-08-27T11:30:00Z")
    overall_health_pct: float = Field(..., example=86.0, description="Machine Overall Health Index %")
    operational_status: str = Field(..., example="WARNING", description="HEALTHY, WARNING, URGENT, CRITICAL, SENSOR_FAULT")
    
    # Agent Alpha
    is_sensor_data_valid: bool = Field(True, description="False if wire broken or PLC Param 12 fault")
    suppressed_false_alarm: bool = Field(False, description="True if false wire alert suppressed")
    
    # Agent Beta & Gamma
    diagnosed_fault: str = Field(..., example="Drive-End SKF-6208 Bearing Inner Race Pitting (BPFI 162.4 Hz)")
    root_cause_domain: str = Field(..., example="MECHANICAL_FRICTION", description="MECHANICAL_FRICTION, ELECTRICAL_OVERLOAD, HYDRAULIC_LEAK, SENSOR_FAULT")
    rul_days: float = Field(..., example=11.5, description="Remaining Useful Life in Days")
    confidence_bounds_pct: float = Field(92.4, example=92.4, description="Statistical RUL Confidence Bounds %")
    
    # Agent Delta
    suggested_action: str = Field(..., example="Schedule SKF-6208 bearing replacement during Sunday shift break")
    sap_work_order_id: Optional[str] = Field("WO-8921-2026", example="WO-8921-2026")
    warehouse_bom_part_reserved: Optional[str] = Field("SKF-6208-2RS in Bin #A-14", example="SKF-6208-2RS in Bin #A-14")
    planned_downtime_window: Optional[str] = Field("Sunday 02:00 AM - 06:00 AM", example="Sunday 02:00 AM - 06:00 AM")
