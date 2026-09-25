"""
Enterprise System Configuration Module
Siemens / Bosch Rexroth Industrial Grade Settings Management
"""

import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Production application settings with environment variable override support."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # General App Info
    APP_NAME: str = "Agentic AI Predictive & Preventive Maintenance System (APMS)"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False

    # Server Configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # API security — set APMS_API_KEY to require X-API-Key / Bearer on /api/v1
    APMS_API_KEY: Optional[str] = None
    AUTH_ENABLED: Optional[bool] = None
    CORS_ORIGINS: str = "http://127.0.0.1:8000,http://localhost:8000"
    DOCS_ENABLED: bool = True

    def auth_required(self) -> bool:
        if self.AUTH_ENABLED is False:
            return False
        if self.AUTH_ENABLED is True:
            return True
        return bool(self.APMS_API_KEY)

    def cors_origin_list(self) -> list:
        raw = (self.CORS_ORIGINS or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    # Database Configuration (PostgreSQL / TimescaleDB)
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "pdm_telemetry"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    DATABASE_URL: Optional[str] = None
    TELEMETRY_PERSIST_ENABLED: bool = True

    # MQTT Broker Configuration (Live Stream Fetch)
    MQTT_BROKER_HOST: str = "localhost"
    MQTT_BROKER_PORT: int = 1883
    MQTT_KEEPALIVE: int = 60
    MQTT_USERNAME: Optional[str] = None
    MQTT_PASSWORD: Optional[str] = None
    MQTT_TOPIC_TELEMETRY: str = "pdm/integrated_json"
    MQTT_TOPIC_ALERTS: str = "pdm/alerts"
    MQTT_ENABLED: bool = True

    # Gbotz PDM Sensor API (persisted history via POST /api/ai/query)
    AI_SERVICE_BASE_URL: str = "http://168.144.37.186:4000"
    AI_SERVICE_API_KEY: Optional[str] = None
    # Off: do not call Gbotz or insert history into Postgres.
    GBOTZ_BACKFILL_WRITE_DB: bool = False

    # Redis — durable Alpha ring buffer (survives restart and multi-worker)
    REDIS_ENABLED: bool = True
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None
    REDIS_URL: Optional[str] = None
    REDIS_KEY_PREFIX: str = "apms"
    ALPHA_BUFFER_TTL_SECONDS: int = 604800  # 7 days, refreshed on each write

    def get_redis_url(self) -> str:
        if self.REDIS_URL:
            return self.REDIS_URL
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # OEM close-the-loop mail (Agent Delta inventory briefing)
    OEM_MAIL_API_URL: str = "https://report.preprod.splus.one/v1/Export/SendMailAsync"
    OEM_MAIL_TO: str = ""
    OEM_MAIL_CC: str = ""
    OEM_MAIL_BCC: str = ""
    OEM_MAIL_FROM: str = ""
    OEM_MAIL_PWD: str = ""
    OEM_MAIL_SP_CLIENT_ID: str = "60B14D50-AC47-4A6D-81B0-48AB86A53F51"
    OEM_MAIL_TIMEOUT_S: float = 30.0

    # WhatsApp Business API Configurations
    WHATSAPP_TOKEN: Optional[str] = None
    WHATSAPP_PHONE_NUMBER_ID: Optional[str] = None
    WHATSAPP_VERIFY_TOKEN: str = "pdm_verify_token"
    WHATSAPP_ALERT_TO: str = "919731139900"
    WHATSAPP_API_VERSION: str = "v18.0"
    WHATSAPP_TIMEOUT_S: float = 15.0

    # Multi-channel Alert Dispatcher Settings (WhatsApp + Email)
    ALERT_NOTIFICATION_ENABLED: bool = True
    ALERT_MAIL_TO: str = ""
    ALERT_MIN_SEVERITY: str = "WARNING"  # CRITICAL, SEVERE, WARNING
    ALERT_NOTIFICATION_COOLDOWN_SECONDS: int = 1800  # 30 mins debounce per defect/machine

    # ---------------------------------------------------------
    # NAMUR NE43 Hardware Diagnostic Standards (mA)
    # ---------------------------------------------------------
    NAMUR_NE43_FAULT_LOW: float = 3.6
    NAMUR_NE43_UNDER_RANGE: float = 3.8
    NAMUR_NE43_OVER_RANGE: float = 20.5
    NAMUR_NE43_FAULT_HIGH: float = 21.0

    # Sensor Plausibility & Signal Quality Checks (Agent Alpha)
    TEMP_TRANSDUCER_OPEN_MIN: float = -20.0
    TEMP_TRANSDUCER_OPEN_MAX: float = 180.0
    SENSOR_FLATLINE_MIN_SAMPLES: int = 15
    SENSOR_FLATLINE_MAX_VARIANCE: float = 1e-6
    MOTOR_RUNNING_CURRENT_A: float = 3.0
    MOTOR_RUNNING_POWER_KW: float = 5.0
    POWER_CALC_MISMATCH_FRAC: float = 0.25
    VUF_DERIVED_MISMATCH_PCT: float = 1.5
    TEMP_CORRELATION_MAX_DELTA_C: float = 45.0
    RPM_SENSOR_MAX: float = 6000.0
    POWER_CALC_MIN_CURRENT_A: float = 5.0
    POWER_CALC_MIN_VOLTAGE_V: float = 100.0

    # ---------------------------------------------------------
    # 19 Industrial Telemetry Parameter Thresholds (Product A & B)
    # Based on ISO 10816-3, IEC 60034-1, IEEE 519-2022, IEC 61000-4-30
    # ---------------------------------------------------------
    # Product A: Mechanical / Thermal / Orientation / Sound (7 Parameters)
    VIB_NORMAL_MAX: float = 2.80        # ISO 10816-3 Zone A/B Safe Limit (Good/Satisfactory)
    VIB_WARNING_MAX: float = 4.50       # ISO 10816-3 Zone C Warning Threshold (Unsatisfactory)
    VIB_DANGER_MIN: float = 7.10        # ISO 10816-3 Zone D Danger / Trip Threshold (Unacceptable)
    VIBRATION_WARNING_MM_S: float = 4.50
    VIBRATION_CRITICAL_MM_S: float = 7.10
    VIBRATION_POST_REPAIR_MAX: float = 1.20

    TEMP_MOTOR_NORMAL_MAX: float = 75.0 # Motor Stator Normal Running Temperature (°C)
    TEMP_MOTOR_WARNING_MAX: float = 85.0 # Motor Stator Class F Warning Limit (°C)
    TEMP_MOTOR_DANGER_MIN: float = 95.0  # Motor Stator Critical Overheating Trip Limit (°C)

    TEMP_COMPRESSOR_NORMAL_MAX: float = 70.0 # Compressor Air-End Normal Temperature (°C)
    TEMP_COMPRESSOR_WARNING_MAX: float = 80.0 # Compressor Air-End Warning Temperature (°C)
    TEMP_COMPRESSOR_DANGER_MIN: float = 90.0 # Compressor Air-End Critical Trip Temperature (°C)

    SITE_AMBIENT_REFERENCE_C: float = 25.0
    GENUINE_OVERHEAT_DELTA_C: float = 35.0
    AMBIENT_DRIFT_THRESHOLD_C: float = 5.0

    SOUND_NORMAL_MAX: float = 82.0      # Normal sound level (dB)
    SOUND_WARNING_MAX: float = 85.0     # OSHA Warning level (dB)
    SOUND_DANGER_MIN: float = 90.0      # OSHA Permissible Exposure Limit (dB)

    TILT_ROLL_NORMAL_MAX: float = 2.0   # API 670 Base Orientation Roll (deg)
    TILT_ROLL_DANGER_MIN: float = 5.0

    TILT_PITCH_NORMAL_MAX: float = 2.0  # API 670 Base Orientation Pitch (deg)
    TILT_PITCH_DANGER_MIN: float = 5.0

    TILT_YAW_NORMAL_MAX: float = 2.0    # API 670 Base Orientation Yaw (deg)
    TILT_YAW_DANGER_MIN: float = 5.0

    # Product B: 3-Phase Electrical Power Quality (12 Parameters)
    VOLTAGE_NORMAL_MIN: float = 380.0   # IEC 60038 400V/415V Grid (V RMS)
    VOLTAGE_NORMAL_MAX: float = 435.0
    VOLTAGE_DANGER_UNDER: float = 360.0 # Severe Undervoltage (< 360V)
    VOLTAGE_DANGER_OVER: float = 455.0  # Severe Overvoltage (> 455V)

    CURRENT_NORMAL_MIN: float = 5.0     # 37kW Motor Line Current (Amps RMS)
    CURRENT_NORMAL_MAX: float = 60.0
    CURRENT_WARNING_MAX: float = 68.0   # Full Load Amps (FLA) rating
    CURRENT_DANGER_OVER: float = 75.0   # Overcurrent / Stalled Rotor (> 75A)

    POWER_NORMAL_MAX: float = 37.0      # Active Power Rating for 37 kW Motor (kW)
    POWER_WARNING_MAX: float = 42.0
    POWER_DANGER_MIN: float = 45.0      # Overload (> 45 kW)

    PF_NORMAL_MIN: float = 0.85         # Power Factor cos phi
    PF_WARNING_MIN: float = 0.70
    PF_DANGER_UNDER: float = 0.65       # Poor PF (< 0.65)

    LOAD_NORMAL_MIN: float = 40.0       # Operating Machine Load %
    LOAD_NORMAL_MAX: float = 85.0
    LOAD_DANGER_OVER: float = 105.0     # Sustained Overload (> 105%)

    VUF_NORMAL_MAX: float = 1.5         # IEC 61000-4-30 Voltage Unbalance (VUF %)
    VUF_WARNING_MAX: float = 2.5        # Warning unbalance (> 2.5%)
    VUF_DANGER_MIN: float = 3.0         # Danger Unbalance (> 3.0%)

    THD_NORMAL_MAX: float = 5.0         # IEEE 519 Voltage %THD
    THD_WARNING_MAX: float = 8.0
    THD_DANGER_MIN: float = 10.0        # Severe Harmonic Distortion (> 10.0%)

    FREQ_NORMAL_MIN: float = 49.5       # 50 Hz Grid Line Frequency (Hz)
    FREQ_NORMAL_MAX: float = 50.5
    FREQ_DANGER_UNDER: float = 48.5
    FREQ_DANGER_OVER: float = 51.5

    # Default Machine Baselines
    DEFAULT_RPM: float = 720.0           # Elson EL30 compressor pump shaft (nameplate: UNIT R.P.M. 720)
    DEFAULT_BEARING_TYPE: str = "SKF-6206"  # 30mm bore on Elson EL30 crankshaft (NBC/SKF 6206)
    MOTOR_RATED_KW: float = 2.24         # Elson EL30 drive motor: 3 HP = 2.24 kW
    # ISO 10816-6 covers reciprocating machines (Elson EL30 = reciprocating piston). Group 2, flexible skid.
    ISO_MACHINE_GROUP: str = "2"
    ISO_SUPPORT: str = "flexible"
    ALPHA_BUFFER_SIZE: int = 30
    RUL_SKIP_THRESHOLD_DAYS: float = 45.0
    RUL_MODEL_VERSION: str = "physics_weibull_v1"
    TRAINING_DATA_DIR: str = "data/training"
    MIN_WEIBULL_FAILURES: int = 8
    MIN_WEIBULL_RECOMMENDED: int = 20
    MIN_CLASSIFIER_PER_CLASS: int = 30
    MIN_CLASSIFIER_PRODUCTION: int = 100
    MIN_PINN_TRAJECTORIES: int = 5
    MIN_PINN_POINTS: int = 20
    BASELINE_MIN_RPM: float = 500.0
    MIN_BASELINE_SAMPLES: int = 50
    MIN_BASELINE_PRODUCTION: int = 500
    AI_SERVICE_TIMEOUT_S: float = 30.0

    # Triggered raw-waveform envelope FFT (ISO 13373-2). Not on every MQTT tick.
    WAVEFORM_SAMPLE_RATE_HZ: float = 25600.0
    ENVELOPE_BANDPASS_LOW_HZ: float = 2000.0
    ENVELOPE_BANDPASS_HIGH_HZ: float = 10000.0
    ENVELOPE_MIN_SAMPLES: int = 4096
    ENVELOPE_MAX_SAMPLES: int = 131072
    ENVELOPE_MAX_FREQ_HZ: float = 500.0
    ENVELOPE_DECIMATE_HZ: float = 2000.0
    SPECTROGRAM_MIN_SAMPLES: int = 128
    SPECTROGRAM_MAX_FREQ_HZ: float = 60.0
    SPECTROGRAM_NPERSEG: int = 4096

    def get_database_url(self) -> str:
        """Returns PostgreSQL connection string."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    def get_thresholds_dict(self) -> dict:
        """Returns organized dictionary of all operational thresholds for API / Dashboard."""
        return {
            "vibration": {
                "normal_max": self.VIB_NORMAL_MAX,
                "warning_max": self.VIB_WARNING_MAX,
                "danger_min": self.VIB_DANGER_MIN,
                "post_repair_max": self.VIBRATION_POST_REPAIR_MAX,
                "unit": "mm/s RMS",
                "standard": "ISO 20816-3 / ISO 10816-3",
                "iso_machine_group": self.ISO_MACHINE_GROUP,
                "iso_support": self.ISO_SUPPORT,
            },
            "temperature_motor": {
                "normal_max": self.TEMP_MOTOR_NORMAL_MAX,
                "warning_max": self.TEMP_MOTOR_WARNING_MAX,
                "danger_min": self.TEMP_MOTOR_DANGER_MIN,
                "unit": "°C",
                "standard": "IEC 60034-1"
            },
            "temperature_compressor": {
                "normal_max": self.TEMP_COMPRESSOR_NORMAL_MAX,
                "warning_max": self.TEMP_COMPRESSOR_WARNING_MAX,
                "danger_min": self.TEMP_COMPRESSOR_DANGER_MIN,
                "unit": "°C"
            },
            "thermal_dewethering": {
                "site_ambient_reference": self.SITE_AMBIENT_REFERENCE_C,
                "genuine_overheat_delta": self.GENUINE_OVERHEAT_DELTA_C,
                "ambient_drift_threshold": self.AMBIENT_DRIFT_THRESHOLD_C,
                "unit": "°C"
            },
            "acoustics": {
                "normal_max": self.SOUND_NORMAL_MAX,
                "warning_max": self.SOUND_WARNING_MAX,
                "danger_min": self.SOUND_DANGER_MIN,
                "unit": "dB",
                "standard": "OSHA 1910.95 / ISO 1996"
            },
            "orientation_tilt": {
                "roll_normal_max": self.TILT_ROLL_NORMAL_MAX,
                "roll_danger_min": self.TILT_ROLL_DANGER_MIN,
                "pitch_normal_max": self.TILT_PITCH_NORMAL_MAX,
                "pitch_danger_min": self.TILT_PITCH_DANGER_MIN,
                "yaw_normal_max": self.TILT_YAW_NORMAL_MAX,
                "yaw_danger_min": self.TILT_YAW_DANGER_MIN,
                "unit": "deg",
                "standard": "API 670"
            },
            "electrical_pq": {
                "voltage_min": self.VOLTAGE_NORMAL_MIN,
                "voltage_max": self.VOLTAGE_NORMAL_MAX,
                "current_warning_max": self.CURRENT_WARNING_MAX,
                "power_warning_max": self.POWER_WARNING_MAX,
                "pf_warning_min": self.PF_WARNING_MIN,
                "load_normal_max": self.LOAD_NORMAL_MAX,
                "vuf_warning_max": self.VUF_WARNING_MAX,
                "vuf_danger_min": self.VUF_DANGER_MIN,
                "thd_warning_max": self.THD_WARNING_MAX,
                "thd_danger_min": self.THD_DANGER_MIN,
                "freq_min": self.FREQ_NORMAL_MIN,
                "freq_max": self.FREQ_NORMAL_MAX,
                "standards": "IEC 60038 / IEEE 519 / IEC 61000-4-30"
            },
            "namur_ne43": {
                "fault_low": self.NAMUR_NE43_FAULT_LOW,
                "under_range": self.NAMUR_NE43_UNDER_RANGE,
                "over_range": self.NAMUR_NE43_OVER_RANGE,
                "fault_high": self.NAMUR_NE43_FAULT_HIGH,
                "unit": "mA"
            },
            "sensor_quality": {
                "temp_open_min": self.TEMP_TRANSDUCER_OPEN_MIN,
                "temp_open_max": self.TEMP_TRANSDUCER_OPEN_MAX,
                "flatline_samples": self.SENSOR_FLATLINE_MIN_SAMPLES,
                "flatline_max_variance": self.SENSOR_FLATLINE_MAX_VARIANCE,
                "motor_running_current_a": self.MOTOR_RUNNING_CURRENT_A,
                "motor_running_power_kw": self.MOTOR_RUNNING_POWER_KW,
                "power_calc_mismatch_frac": self.POWER_CALC_MISMATCH_FRAC,
                "vuf_derived_mismatch_pct": self.VUF_DERIVED_MISMATCH_PCT,
                "temp_correlation_max_delta_c": self.TEMP_CORRELATION_MAX_DELTA_C
            },
            "pdm_policy": {
                "rul_skip_threshold_days": self.RUL_SKIP_THRESHOLD_DAYS,
                "default_rpm": self.DEFAULT_RPM,
                "default_bearing": self.DEFAULT_BEARING_TYPE,
                "motor_rated_kw": self.MOTOR_RATED_KW
            }
        }


# Singleton instance
settings = AppSettings()

