# Agentic AI Predictive & Preventive Maintenance System (APMS)
**Enterprise Industrial Production Platform (Siemens / Bosch Rexroth Market Standards)**

---

## 🌟 Solution Overview
The **Agentic AI Predictive & Preventive Maintenance System (APMS)** replaces manual diagnostic guesswork with 4 autonomous AI micro-agents orchestrated via a **LangGraph StateGraph Directed Acyclic Graph (DAG)**. It continuously analyzes 15 multi-physics telemetry parameters, suppresses hardware false alarms, calculates Remaining Useful Life (RUL) countdowns on Day 1, and auto-orchestrates SAP PM work orders during planned shift breaks.

---

## 🏗️ Enterprise Architecture Layout
```
src/
├── config.py                       # Pydantic-settings environment configuration
├── schemas/                        # Pydantic v2 telemetry & prediction data contracts
│   ├── telemetry.py                # 15-parameter TelemetryFrame schema
│   └── predictions.py              # Defect, RUL & Diagnostics response models
├── data/                           # Embedded static databases
│   └── bearing_catalog.json        # SKF/FAG bearing kinematics catalog (BPFI, BPFO, BSF, FTF)
├── utils/                          # Structured logging & math utilities
│   └── logger.py                   # Industrial thread-safe logger
├── signal_processing/              # High-frequency signal demodulation
│   └── envelope_fft.py             # Hilbert Envelope Spectrogram FFT engine
├── models/                         # ML & Physics models
│   ├── pinns_rul.py                # PyTorch PINNs (Rotordynamics + Paris Law)
│   ├── weibull_survival.py         # Weibull Proportional Hazards Survival RUL
│   ├── fault_classifier.py         # XGBoost / Random Forest defect classifier
│   └── anomaly_detector.py         # Isolation Forest multivariate anomaly scoring
├── agents/                         # The 4 Autonomous AI Micro-Agents
│   ├── agent_alpha.py              # NAMUR NE43 & Wire Check Gatekeeper
│   ├── agent_beta.py               # Dynamic Thermal De-Weathering & Power Quality
│   ├── agent_gamma.py              # PyTorch PINNs & Weibull RUL Core
│   ├── agent_delta.py              # SAP Work Order & Google OR-Tools Downtime Solver
│   └── orchestrator.py             # LangGraph StateGraph Execution Engine
└── api/                            # Production FastAPI Web Service
    ├── main.py                     # App entry point & middleware
    └── routes/                     # REST API endpoints (/api/v1/predict_rul, /health)
```

---

## 🚀 Quick Start Guide

### 1. Activate Virtual Environment & Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Test Suite (100% Passing)
```bash
./venv/bin/pytest -v
```

### 3. Launch FastAPI Server
```bash
./venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive OpenAPI Swagger Documentation: `http://localhost:8000/docs`

---

## 🔌 API Endpoints Reference

| HTTP Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/health` | Health check returning DB status & PyTorch model versions. |
| `POST` | `/api/v1/predict_rul` | Runs 4-Agent LangGraph pipeline for real-time diagnosis & RUL countdown. |
| `POST` | `/api/v1/de_weather` | Agent Beta $T_{\text{motor}} - T_{\text{ambient}}$ thermal normalization. |
