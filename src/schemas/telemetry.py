"""
Telemetry Data Contracts (Pydantic v2)
Maps all 15 multi-physics industrial sensor parameters.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

# Product A primaries that must arrive on MQTT. IMU-Acceleration is the 3-axis
# RMS (vibration level). Motor RPM / runtime / harmonics are derived, not required.
# Product C environment temperature, dust, and machine RPM are not on the live feed.
_PRODUCT_A_FIELDS = {
    "imuAcceleration": (
        "imuAcceleration",
        "imu_acceleration",
        "xAxisVibration",
        "yAxisVibration",
        "zAxisVibration",
        "vibrationLevelRms",
        "_axisX",
        "_axisY",
        "_axisZ",
    ),
    "tempMotor": ("tempMotor", "temp_motor", "machineTemperature"),
}
_PRODUCT_B_FIELDS = {
    "emIr": ("emIr", "em_ir"),
    "emIy": ("emIy", "em_iy"),
    "emIb": ("emIb", "em_ib"),
    "emVr": ("emVr", "em_vr"),
    "emVy": ("emVy", "em_vy"),
    "emVb": ("emVb", "em_vb"),
    "emMachineLoad": ("emMachineLoad", "em_machine_load"),
    "emVoltageImbalance": ("emVoltageImbalance", "em_voltage_imbalance"),
    "emPower": ("emPower", "em_power"),
}
# Placeholder only. sourceKeys still records what actually arrived, so field_was_sent()
# and Alpha's FIELD_NOT_PRESENT gate can tell a filled zero from a measured zero.
_ABSENT_FILL = 0.0
_PRESENCE_META = {"sourceKeys", "source_keys", "missingBlocks", "missing_blocks"}


def _norm_key(name: str) -> str:
    return str(name).lower().replace("_", "")


def _has_any_key(keyset: set, aliases: tuple) -> bool:
    return any(_norm_key(alias) in keyset for alias in aliases)


class HarmonicPeak(BaseModel):
    """Represents a single harmonic peak extracted from Envelope Spectrogram."""
    frequency: float = Field(..., description="Peak frequency in Hz (e.g. 109.4)")
    amplitude: float = Field(..., description="Peak amplitude in dB / mm/s (e.g. -15.26)")


class TelemetryFrame(BaseModel):
    """
    15-Parameter Multi-Physics Telemetry Input Schema
    Standardized contract for incoming sensor messages and database records.
    """
    machine_id: str = Field(default="compressor_unit_01", alias="machineId", description="Unique machine asset identifier")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="UTC ISO timestamp of telemetry frame")
    
    # 1. Mechanical Vibration & Dynamics
    imu_acceleration: float = Field(..., alias="imuAcceleration", description="3-axis vibration RMS from IMU-Acceleration (mm/s); X/Y/Z are not required when this is published")
    x_axis_vibration: Optional[float] = Field(default=None, alias="xAxisVibration", description="X-axis vibration when the gateway publishes the three axes")
    y_axis_vibration: Optional[float] = Field(default=None, alias="yAxisVibration", description="Y-axis vibration when the gateway publishes the three axes")
    z_axis_vibration: Optional[float] = Field(default=None, alias="zAxisVibration", description="Z-axis vibration when the gateway publishes the three axes")
    rpm: float = Field(default=0.0, description="Shaft rotational speed in RPM")
    vibration_harmonics: List[HarmonicPeak] = Field(default_factory=list, alias="vibrationHarmonics", description="Top harmonic peaks H1..H5")
    waveform: Optional[List[float]] = Field(
        default=None,
        description="Triggered raw acceleration capture (g), used as Z if waveformZ is omitted. Optional; do not stream on every MQTT tick.",
    )
    waveform_x: Optional[List[float]] = Field(default=None, alias="waveformX", description="Triggered X-axis raw acceleration (g)")
    waveform_y: Optional[List[float]] = Field(default=None, alias="waveformY", description="Triggered Y-axis raw acceleration (g)")
    waveform_z: Optional[List[float]] = Field(default=None, alias="waveformZ", description="Triggered Z-axis raw acceleration (g)")
    sample_rate_hz: Optional[float] = Field(
        default=None,
        alias="sampleRateHz",
        description="Waveform sample rate in Hz (25600 for envelope; 100+ is enough for 0–50 Hz spectrograms).",
    )
    
    # 2. Thermal Parameters
    temp_motor: float = Field(..., alias="tempMotor", description="Electric motor winding/frame temperature (°C)")
    # Product A exposes one machine temperature. Compressor-head temperature was an
    # old demo field and is optional unless a gateway actually sends it.
    temp_compressor: float = Field(default=0.0, alias="tempCompressor", description="Optional compressor cylinder head temperature (°C)")
    temp_ambient: float = Field(default=0.0, alias="tempAmbient", description="Product C environment temperature (°C)")
    humidity: float = Field(default=0.0, description="Product C environment relative humidity (%Rh)")

    # 3. 3-Phase Electrical Power Quality (Energymeter)
    em_ir: float = Field(..., alias="emIr", description="R-Phase Line Current (Amps)")
    em_iy: float = Field(..., alias="emIy", description="Y-Phase Line Current (Amps)")
    em_ib: float = Field(..., alias="emIb", description="B-Phase Line Current (Amps)")
    em_vr: float = Field(..., alias="emVr", description="R-Phase Line Voltage (Volts)")
    em_vy: float = Field(..., alias="emVy", description="Y-Phase Line Voltage (Volts)")
    em_vb: float = Field(..., alias="emVb", description="B-Phase Line Voltage (Volts)")
    em_machine_load: float = Field(..., alias="emMachineLoad", description="Machine instant operational load ratio (%)")
    em_voltage_imbalance: float = Field(..., alias="emVoltageImbalance", description="Calculated 3-phase voltage unbalance percentage (VUF %)")
    em_power: float = Field(..., alias="emPower", description="Active electric power consumption (kW)")
    em_power_estimated: Optional[float] = Field(
        default=None,
        alias="emPowerEstimated",
        description="kW inferred from ΔkWh when the meter power register stays 0",
    )
    em_power_factor: float = Field(
        default=0.92,
        validation_alias=AliasChoices("emPowerFactor", "emAveragePowerFactor", "em_power_factor"),
        serialization_alias="emPowerFactor",
        description="Average power factor (0.0 to 1.0 pf)",
    )
    em_energy: Optional[float] = Field(default=None, alias="emEnergy", description="Cumulative active energy (kWh)")
    em_thd_v: float = Field(default=1.8, alias="emThdVr", description="Total Harmonic Distortion on voltage supply (%THD)")
    em_thd_vy: float = Field(default=0.0, alias="emThdVy")
    em_thd_vb: float = Field(default=0.0, alias="emThdVb")
    em_frequency: float = Field(default=0.0, alias="emFrequency")
    em_frequency_deviation: float = Field(default=0.0, alias="emFrequencyDeviation")
    em_thd: Optional[float] = Field(default=None, alias="emThd", description="Overall electrical THD percentage")
    em_current_imbalance: Optional[float] = Field(default=None, alias="emCurrentImbalance", description="3-phase current imbalance (IUF %)")

    # 4. Acoustic Noise & Ultrasound
    sound_level: float = Field(default=0.0, alias="soundLevel", description="Airborne microphone acoustic sound pressure level (dB)")
    mic_harmonics: List[HarmonicPeak] = Field(default_factory=list, alias="micHarmonics", description="Airborne ultrasound harmonic peaks")
    motor_faults: List[Dict[str, Any]] = Field(default_factory=list, alias="motorFaults")

    # 5. 3D Shaft Alignment & Orientation
    mag_roll: float = Field(default=0.0, alias="magRoll", description="Magnetometer / Gyroscope Roll angle (degrees)")
    mag_pitch: float = Field(default=0.0, alias="magPitch", description="Magnetometer / Gyroscope Pitch angle (degrees)")
    mag_yaw: float = Field(default=0.0, alias="magYaw", description="Magnetometer / Gyroscope Yaw angle (degrees)")

    # Product C process/environment values.
    pressure: float = Field(default=0.0, description="Relative process pressure")
    dust: Optional[float] = Field(default=None, description="Environment dust concentration")

    # 6. Operating Hours, Loop Current & Asset Metadata
    run_hours: float = Field(default=100.0, alias="runHours", description="Total cumulative machine operating hours")
    remaining_hours: Optional[float] = Field(default=None, alias="remainingHours", description="CSV reference RUL hours")
    bearing_model: str = Field(default="SKF-6208", alias="bearingModel", description="Installed bearing part model number")
    plc_param_12_status: int = Field(default=0, alias="plcParam12Status", description="Parameter 12 PLC hardware diagnostic status register (0=OK)")
    loop_current_ma: Optional[float] = Field(default=None, alias="loopCurrentMa", description="NAMUR NE43 analog loop current in mA (4-20 mA valid, <3.6 mA cut wire, >21.0 mA short)")
    iso_machine_group: Optional[str] = Field(default=None, alias="isoMachineGroup", description="ISO 20816-3 group 1 (>300 kW) or 2 (15–300 kW)")
    iso_support: Optional[str] = Field(default=None, alias="isoSupport", description="Foundation: rigid or flexible")
    source_keys: List[str] = Field(default_factory=list, alias="sourceKeys", description="Canonical fields genuinely received from MQTT")
    missing_blocks: List[str] = Field(default_factory=list, alias="missingBlocks", description="Required fields absent from the current aggregate")

    @model_validator(mode="before")
    @classmethod
    def _alias_waveform_keys(cls, data):
        if not isinstance(data, dict):
            return data
        provided_keys = data.get("sourceKeys") or data.get("source_keys")
        original_keys = (
            [str(k) for k in provided_keys]
            if isinstance(provided_keys, list)
            else [str(k) for k in data.keys() if str(k) not in _PRESENCE_META]
        )
        keyset = {_norm_key(k) for k in original_keys}
        data = dict(data)
        data["sourceKeys"] = original_keys
        missing_blocks: List[str] = []
        if original_keys:
            # Per-field, not per-block: a gateway that sends tempMotor but no
            # imuAcceleration used to satisfy the block check and then fail validation
            # on the required member, dropping the whole message.
            for block, fields in (("product_a", _PRODUCT_A_FIELDS), ("product_b", _PRODUCT_B_FIELDS)):
                absent = [alias for alias, names in fields.items() if not _has_any_key(keyset, names)]
                if not absent:
                    continue
                if len(absent) == len(fields):
                    missing_blocks.append(block)
                else:
                    missing_blocks.extend(absent)
                for alias in absent:
                    data.setdefault(alias, _ABSENT_FILL)
        data["missingBlocks"] = missing_blocks
        if data.get("waveform") is None:
            data["waveform"] = data.get("rawWaveform") or data.get("vibrationWaveform")
        if data.get("waveformX") is None and data.get("waveform_x") is None:
            data["waveformX"] = data.get("xWaveform") or data.get("waveform_x_axis")
        if data.get("waveformY") is None and data.get("waveform_y") is None:
            data["waveformY"] = data.get("yWaveform") or data.get("waveform_y_axis")
        if data.get("waveformZ") is None and data.get("waveform_z") is None:
            data["waveformZ"] = data.get("zWaveform") or data.get("waveform_z_axis")
        if data.get("sampleRateHz") is None and data.get("sample_rate_hz") is None:
            data["sampleRateHz"] = data.get("fs") or data.get("samplingRate")
        if data.get("loopCurrentMa") is None and data.get("loop_current_ma") is None:
            data["loopCurrentMa"] = data.get("namurCurrent") or data.get("loopCurrent")
        return data

    @field_validator("waveform", "waveform_x", "waveform_y", "waveform_z", mode="before")
    @classmethod
    def _parse_waveform(cls, value):
        if value is None or value == [] or value == "":
            return None
        if isinstance(value, dict):
            value = value.get("samples") or value.get("data")
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            return [float(p) for p in parts] or None
        return value

    def ensure_triggered_waveform(self, sample_rate_hz: float = 25600.0, num_samples: int = 2048) -> List[float]:
        """
        Ensures a triggered time-domain waveform is present for Envelope FFT and STFT.
        If no waveform was supplied by light MQTT JSON, synthesizes a physical waveform
        embedding reported rotational speed (1X, 2X), harmonic peaks, and baseline noise floor.
        """
        if self.waveform_z and len(self.waveform_z) >= 128:
            return self.waveform_z
        if self.waveform and len(self.waveform) >= 128:
            return self.waveform

        import numpy as np
        t = np.linspace(0, num_samples / sample_rate_hz, num_samples, endpoint=False)
        f_rot = max(5.0, self.rpm / 60.0)
        
        # Base mechanical signature (1X and 2X rotational speed)
        amp_scale = max(0.2, self.imu_acceleration)
        sig = 0.6 * amp_scale * np.sin(2 * np.pi * f_rot * t) + 0.3 * amp_scale * np.sin(2 * np.pi * (2 * f_rot) * t)
        
        # Embed reported harmonic frequencies (e.g. BPFI / BPFO / loose bolts)
        for h in (self.vibration_harmonics or []):
            freq = getattr(h, "frequency", 0.0)
            amp_db = getattr(h, "amplitude", -30.0)
            amp_lin = max(0.1, (10.0 ** (amp_db / 20.0)) * amp_scale)
            sig += amp_lin * np.sin(2 * np.pi * freq * t)

        # Baseline noise floor
        noise = np.random.normal(0, 0.04 * amp_scale, num_samples)
        synthesized = (sig + noise).tolist()
        self.waveform_z = synthesized
        self.sample_rate_hz = sample_rate_hz
        return synthesized

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "machineId": "compressor_unit_01",
                "imuAcceleration": 7.9051,
                "rpm": 1480,
                "tempMotor": 28.94,
                "tempCompressor": 29.06,
                "tempAmbient": 25.0,
                "humidity": 45.2,
                "emIr": 12.4,
                "emIy": 12.6,
                "emIb": 12.5,
                "emVr": 395.62,
                "emVy": 404.55,
                "emVb": 397.07,
                "emMachineLoad": 82.5,
                "emVoltageImbalance": 1.371,
                "emPower": 18.4,
                "soundLevel": -7.1,
                "magRoll": 0.12,
                "magPitch": 0.05,
                "magYaw": 0.01,
                "runHours": 1420.5,
                "vibrationHarmonics": [
                    {"frequency": 109.4, "amplitude": -15.26},
                    {"frequency": 218.8, "amplitude": -19.05}
                ]
            }
        }
    }

    def field_was_sent(self, *names: str) -> bool:
        """True if any of the given aliases/field names were in the original payload."""
        sent = {_norm_key(k) for k in (self.source_keys or [])}
        if not sent:
            sent = {_norm_key(n) for n in self.model_fields_set}
        return any(_norm_key(name) in sent for name in names)

    def has_triaxial_axes(self) -> bool:
        return (
            self.field_was_sent("xAxisVibration", "_axisX", "x_axis_vibration")
            and self.field_was_sent("yAxisVibration", "_axisY", "y_axis_vibration")
            and self.field_was_sent("zAxisVibration", "_axisZ", "z_axis_vibration")
        )

    def has_vibration_waveform(self) -> bool:
        for samples in (self.waveform, self.waveform_x, self.waveform_y, self.waveform_z):
            if samples and len(samples) >= 128:
                return True
        return False

    def has_vibration_spectrum(self) -> bool:
        return bool(self.vibration_harmonics) or self.has_vibration_waveform()

    def vibration_prediction_mode(self) -> str:
        """imu_rms | triaxial_rms | spectrum | triaxial_spectrum | absent"""
        if self.has_vibration_spectrum():
            return "triaxial_spectrum" if self.has_triaxial_axes() else "spectrum"
        if self.has_triaxial_axes():
            return "triaxial_rms"
        if self.field_was_sent("imuAcceleration"):
            return "imu_rms"
        return "absent"
