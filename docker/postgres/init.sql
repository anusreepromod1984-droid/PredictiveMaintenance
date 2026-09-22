-- Applied on first empty Postgres volume. Runtime also calls ensure_telemetry_schema().
CREATE TABLE IF NOT EXISTS public.telemetry_reading (
    id                  BIGSERIAL PRIMARY KEY,
    "machineId"         TEXT NOT NULL,
    "timestamp"         TIMESTAMPTZ NOT NULL,
    "receivedAt"        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "mqttTopic"         TEXT,
    "imuAcceleration"   DOUBLE PRECISION,
    rpm                 DOUBLE PRECISION,
    "tempMotor"         DOUBLE PRECISION,
    "tempCompressor"    DOUBLE PRECISION,
    "tempAmbient"       DOUBLE PRECISION,
    humidity            DOUBLE PRECISION,
    "emIr"              DOUBLE PRECISION,
    "emIy"              DOUBLE PRECISION,
    "emIb"              DOUBLE PRECISION,
    "emVr"              DOUBLE PRECISION,
    "emVy"              DOUBLE PRECISION,
    "emVb"              DOUBLE PRECISION,
    "emMachineLoad"     DOUBLE PRECISION,
    "emVoltageImbalance" DOUBLE PRECISION,
    "emPower"           DOUBLE PRECISION,
    "emAveragePowerFactor" DOUBLE PRECISION,
    "emThdVr"           DOUBLE PRECISION,
    "soundLevel"        DOUBLE PRECISION,
    "magRoll"           DOUBLE PRECISION,
    "magPitch"          DOUBLE PRECISION,
    "magYaw"            DOUBLE PRECISION,
    "runHours"          DOUBLE PRECISION,
    "remainingHours"    DOUBLE PRECISION,
    "sensorOk"          BOOLEAN,
    "vibrationHarmonics" JSONB,
    "micHarmonics"      JSONB
);

CREATE INDEX IF NOT EXISTS telemetry_reading_machine_ts_idx
    ON public.telemetry_reading ("machineId", "timestamp" DESC);

CREATE INDEX IF NOT EXISTS telemetry_reading_received_idx
    ON public.telemetry_reading ("receivedAt" DESC);

CREATE TABLE IF NOT EXISTS public.agent_snapshot (
    id                      BIGSERIAL PRIMARY KEY,
    "traceId"               TEXT NOT NULL,
    "machineId"             TEXT NOT NULL,
    "timestamp"             TIMESTAMPTZ NOT NULL,
    "receivedAt"            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "patternStatus"         TEXT,
    "isolatedFailureDomain" TEXT,
    "imuAcceleration"       DOUBLE PRECISION,
    "runHours"              DOUBLE PRECISION,
    "tempMotor"             DOUBLE PRECISION,
    "tempCompressor"        DOUBLE PRECISION,
    "emEnergy"              DOUBLE PRECISION,
    "emPower"               DOUBLE PRECISION,
    pressure                DOUBLE PRECISION,
    "soundLevel"            DOUBLE PRECISION,
    "defectCode"            TEXT,
    "diagnosisMethod"       TEXT,
    "predictionMode"        TEXT,
    "rulOperatingHours"     DOUBLE PRECISION,
    "rulDays"               DOUBLE PRECISION,
    "rulMethod"             TEXT,
    "healthScore"           DOUBLE PRECISION,
    "healthStatus"          TEXT,
    "skipReason"            TEXT,
    "sourceKeys"            JSONB,
    inputs                  JSONB NOT NULL,
    outputs                 JSONB NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS agent_snapshot_trace_idx
    ON public.agent_snapshot ("traceId");

CREATE INDEX IF NOT EXISTS agent_snapshot_machine_ts_idx
    ON public.agent_snapshot ("machineId", "timestamp" DESC);
