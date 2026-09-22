"""
Modular Multi-Product Telemetry Contracts (Pydantic v2)
Supports Modular Product Strategy:
- Product A: Mechanical Vibration, 3D Orientation, Machine Temp & Sound (FREEZED)
- Product B: Electrical Power Quality & Energy Monitor (FREEZED)
- Product C: Environmental Pressure, Humidity, Temp & Dust (PLUG & PLAY SLOT)
"""

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class HarmonicPeak(BaseModel):
    """Single harmonic frequency peak."""
    frequency: float = Field(..., description="Frequency in Hz")
    amplitude: float = Field(..., description="Amplitude in dB or mm/s")


class ProductATelemetry(BaseModel):
    """
    Product A: Mechanical Vibration, 3D Orientation, Machine Temp & Sound [FREEZED]
    """
    machine_id: str = Field(default="compressor_unit_01", alias="machineId")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # Acceleration & Vibration
    x_axis_vibration: float = Field(..., alias="xAxisVibration", description="X-Axis Vibration Acceleration (g / mm/s RMS)")
    y_axis_vibration: float = Field(..., alias="yAxisVibration", description="Y-Axis Vibration Acceleration (g / mm/s RMS)")
    z_axis_vibration: float = Field(..., alias="zAxisVibration", description="Z-Axis Vibration Acceleration (g / mm/s RMS)")
    vibration_level_rms: float = Field(..., alias="vibrationLevelRms", description="Derived Overall Vibration Level (mm/sec RMS)")
    motor_rpm: float = Field(default=1480.0, alias="motorRpm", description="Derived Motor Speed (RPM)")
    vibration_harmonics: List[HarmonicPeak] = Field(default_factory=list, alias="vibrationHarmonics")
    
    # 3D Orientation
    roll: float = Field(..., description="Roll Angle (Degrees)")
    pitch: float = Field(..., description="Pitch Angle (Degrees)")
    yaw: float = Field(..., description="Yaw Angle (Degrees)")

    # Temperature
    machine_temperature: float = Field(..., alias="machineTemperature", description="Machine Operating Temperature (°C)")

    # Sound & Ultrasound
    sound_db: float = Field(..., alias="soundDb", description="Acoustic Sound Pressure Level (dB)")
    sound_frequency: float = Field(..., alias="soundFrequency", description="Dominant Acoustic Frequency (Hz)")
    sound_harmonics: List[HarmonicPeak] = Field(default_factory=list, alias="soundHarmonics")


class ProductBTelemetry(BaseModel):
    """
    Product B: Electrical Power Quality & Energy Monitor [FREEZED]
    """
    machine_id: str = Field(default="compressor_unit_01", alias="machineId")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # 3-Phase Electrical Supply
    voltage_r: float = Field(..., alias="voltageR", description="R-Phase Line Voltage (Volts)")
    voltage_y: float = Field(..., alias="voltageY", description="Y-Phase Line Voltage (Volts)")
    voltage_b: float = Field(..., alias="voltageB", description="B-Phase Line Voltage (Volts)")
    current_r: float = Field(..., alias="currentR", description="R-Phase Line Current (Amps)")
    current_y: float = Field(..., alias="currentY", description="Y-Phase Line Current (Amps)")
    current_b: float = Field(..., alias="currentB", description="B-Phase Line Current (Amps)")
    
    # Energy & Power Quality Metrics
    active_power_kw: float = Field(..., alias="activePowerKw", description="Active Power (kW)")
    cumulative_energy_kwh: float = Field(..., alias="cumulativeEnergyKwh", description="Cumulative Energy (kWh)")
    power_factor: float = Field(default=0.92, alias="powerFactor", description="Power Factor (0.0 to 1.0 pf)")
    total_harmonic_distortion_thd: float = Field(default=1.8, alias="totalHarmonicDistortionThd", description="Total Harmonic Distortion %THD")
    grid_frequency_hz: float = Field(default=50.0, alias="gridFrequencyHz", description="Grid Power Frequency (Hz)")
    
    # Derived Electrical Metrics
    voltage_unbalance_pct: float = Field(..., alias="voltageUnbalancePct", description="Calculated 3-Phase Voltage Unbalance VUF %")
    machine_run_time_hours: float = Field(..., alias="machineRunTimeHours", description="Software Calculated Machine Runtime (Hours)")


class ProductCTelemetry(BaseModel):
    """
    Product C: Environmental & Process Pressure Module [UNDER MARKET STUDY - PLUG & PLAY SLOT]
    """
    machine_id: str = Field(default="compressor_unit_01", alias="machineId")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    relative_pressure_bar: Optional[float] = Field(default=None, alias="relativePressureBar")
    environment_humidity_pct: Optional[float] = Field(default=None, alias="environmentHumidityPct")
    environment_temperature_c: Optional[float] = Field(default=None, alias="environmentTemperatureC")
    environment_dust_ppm: Optional[float] = Field(default=None, alias="environmentDustPpm")
    machine_rpm: Optional[float] = Field(default=None, alias="machineRpm")
    derived_leakage_rate: Optional[float] = Field(default=None, alias="derivedLeakageRate")
