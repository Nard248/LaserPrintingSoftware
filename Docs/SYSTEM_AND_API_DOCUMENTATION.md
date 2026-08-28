# 2PP Laser Printing System & Labgate API Platform — Comprehensive System Documentation

## Executive Summary

This document serves as the master technical guide for the **Two-Photon Polymerization (2PP) Laser Printing & Ablation Control System** and the **Labgate API Platform**.

The system coordinates two physical instruments:
- **Phoebe PH2 Femtosecond Laser**: 515 nm wavelength, ~200.8 kHz repetition rate, controlled over HTTP REST API (`http://192.168.244.10/phoebe/v0/Basic`).
- **Standa 8MTL120XY 3-Axis Stage**: Precise positioning ($\pm 25\text{ mm}$ travel range), driven by an **ACS Motion Control SPiiPlus** controller over Ethernet TCP (`10.0.0.100:701`) using Python 3.12 C-bindings (`SPiiPlusPython` wheel).

---

## 1. System Architecture & Cognition-Control Safety Firewall

```
                +------------------------------------+
                |  AI Agent / Chat / User Client     |
                +-----------------+------------------+
                                  |
                   Declarative Experiment Spec (JSON)
                                  |
                                  v
+-------------------------------------------------------------------+
|                        LABGATE API GATEWAY                        |
|                                                                   |
|   1. Authentication & Role Check (auth.py)                        |
|      - Bearer Token Resolution (Operator vs Approver roles)       |
|                                                                   |
|   2. Deterministic Validation Engine (validation.py)              |
|      - Bounds Check: Attenuator [0, 100]%, Speed [0.1, 10] mm/s    |
|      - Geometry Check: Axis Range [-25, +25] mm                    |
|      - Interlock Check: White-Light OFF during laser exposure     |
|      - Uniformity Check: Line length >= 2x Accel Distance         |
|                                                                   |
|   3. Dry-Run & Toolpath Preview (dryrun.py / preview.py)          |
|      - Duration/Distance Prediction & PNG Toolpath Render         |
|                                                                   |
|   4. Plan Lifecycle & State Machine (lifecycle.py)                |
|      - Draft -> Validated -> Approved -> Queued -> Running -> Done|
|      - Dual-Identity Rule: Proposer != Approver                   |
|                                                                   |
|   5. Execution Engine (executor.py)                               |
|      - Single FIFO Worker Thread (Strict one-rig fairness)        |
|      - Safe-State First: Pre-run laser-off baseline               |
|      - Cooperative Abort Polling (Between ops, lines, chunks)    |
|      - Seam: SimExposure vs SyncExposure (sync.py)               |
+---------------------------------+---------------------------------+
                                  |
                     Hardware Adapter Transport
                                  |
         +------------------------+------------------------+
         |                                                 |
         v                                                 v
+------------------+                              +------------------+
| Phoebe PH2 Laser |                              | ACS SPiiPlus TCP |
| HTTP (192.168...) |                              | Stage (10.0.0...) |
+------------------+                              +------------------+
```

---

## 2. API Deliverables & Specifications

### 2.1 OpenAI-Compatible OpenAPI 3.1.0 Specification

The platform exposes an OpenAPI 3.1.0 specification compatible with OpenAI Custom GPT Actions, Assistant API tools, and external API gateways.

- **JSON Specification File**: [`Docs/openapi.json`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/openapi.json)
- **YAML Specification File**: [`Docs/openapi.yaml`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/openapi.yaml)
- **Live HTTP Endpoints**:
  - `GET http://127.0.0.1:8523/openapi.json`
  - `GET http://127.0.0.1:8523/openapi.yaml`

#### Key OpenAI Specification Standards:
- **`openapi`**: `"3.1.0"`
- **`servers`**: Configured base URL `http://127.0.0.1:8523`.
- **`securitySchemes`**: `bearerAuth` (HTTP Bearer token mapping).
- **`operationId`**: Explicit, clean camelCase identifiers for every endpoint:
  - `getHealth`: Check server liveness
  - `getCapabilities`: Query grounding bounds & machine specs
  - `getDevices`: Live device connection & position states
  - `submitPlan`: Submit declarative ExperimentSpec
  - `listPlans`: List plan summaries
  - `getPlan`: Query plan state & validation details
  - `dryRunPlan`: Predict timing & render toolpath PNG
  - `approvePlan`: Authorize plan (enforcing $\text{proposer} \neq \text{approver}$)
  - `executePlan`: Enqueue approved plan for execution
  - `getQueue`: View worker queue
  - `abortPlan`: Request execution abort
  - `getPlanResults`: Retrieve telemetry & artifact list
  - `getPlanArtifact`: Download image or log file
  - `rerunPlan`: Clone recipe into a fresh plan
  - `uploadModel`: Upload 3D STL file
  - `listModels`: List 3D models
  - `getModelInfo`: Query 3D mesh metadata

---

## 3. Postman Manual API Testing Suite

For manual API testing prior to AI model integration, complete Postman collections and environments are provided in two locations:

- **Root Directory**:
  - Collection: [`postman/labgate.postman_collection.json`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/postman/labgate.postman_collection.json)
  - Environment: [`postman/labgate.postman_environment.json`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/postman/labgate.postman_environment.json)
- **Docs Directory**:
  - Collection: [`Docs/postman/labgate.postman_collection.json`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/postman/labgate.postman_collection.json)
  - Environment: [`Docs/postman/labgate.postman_environment.json`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/postman/labgate.postman_environment.json)

### Collection Folders & Test Scenarios

1. **`01 - System & Discovery`**:
   - `GET /health` (Open health status check)
   - `GET /capabilities` (Capability grounding check)
   - `GET /devices` (Real-time device position check)
2. **`02 - Single Command Execution`**:
   - `POST /plans`: Test single `move_stage` command (`[1.0, 2.0, 6.0]`)
   - `POST /plans`: Test single `set_laser_power` command (25%)
   - `POST /plans`: Test single `write_line` command
   - `POST /plans`: Test single `capture_image` command
3. **`03 - Full Plan Lifecycle (Multi-Op Sweep)`**:
   - `POST /plans`: Submit `write_power_sweep_array` + `capture_image` recipe
   - `POST /plans/{planId}/dry-run`: Predict timing & render toolpath PNG
   - `POST /plans/{planId}/approve`: Authorize plan using Approver Token
   - `POST /plans/{planId}/execute`: Enqueue execution using Operator Token
   - `GET /queue`: Check queue status
   - `GET /plans/{planId}`: Poll execution status
   - `GET /plans/{planId}/results`: Retrieve telemetry & artifact list
   - `GET /plans/{planId}/results/artifacts/sweep_result.png`: Download output image
   - `POST /plans/{planId}/rerun`: Clone plan
   - `POST /plans/{planId}/abort`: Test cooperative abort
4. **`04 - 3D Printing & STL Management`**:
   - `POST /models`: Upload STL file (stores `modelId` in Postman environment)
   - `GET /models`: List 3D models
   - `GET /models/{modelId}`: Query mesh metadata
   - `POST /plans`: Submit 3D `print_stl` plan

---

## 4. Dockerization & Container Deployment

The application is dockerized for zero-dependency deployment on the Lab PC.

- **`Dockerfile`**: Based on `python:3.12-slim` with system libraries for OpenCV (`libgl1`, `libglib2.0-0`), package installation, port 8523, and healthcheck.
- **`docker-compose.yml`**: Docker Compose configuration.

### Data & Database Persistence
The platform uses file-ledger database storage under `./labgate_data`:
- `plans/`: Stored plan records and state machine transitions.
- `runs/`: Execution telemetry streams (`telemetry.jsonl`) and artifact files.
- `models/`: Content-addressed 3D STL file storage.
- `audit.jsonl`: Append-only security audit log.

Both `./labgate_data` and `./config` are mounted as volumes in `docker-compose.yml`, preserving all data and settings across container restarts.

### Docker Commands:

```bash
# Build and start container in detached mode
docker compose up -d

# Check system health
curl http://localhost:8523/health

# View logs
docker compose logs -f

# Stop container
docker compose down
```

---

## 5. Interactive Jupyter Testing Notebook

An interactive Jupyter notebook is available for step-by-step API testing and inline visualization:

- **Notebook Path**: [`notebooks/02_api_platform_testing.ipynb`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/notebooks/02_api_platform_testing.ipynb)

### Features:
- Exercises all 20 API endpoints step-by-step.
- Renders toolpath dry-run previews directly in Jupyter output cells.
- Renders captured sample photo artifacts inline.
- Tests STL model uploads and slicing specs.

To run:
```bash
labgate-serve  # start server
jupyter lab notebooks/02_api_platform_testing.ipynb
```

---

## 6. Field Testing Readiness Checklist

Before running physical experiments on the rig:

1. **Read-Only Verification**:
   ```cmd
   .venv312\Scripts\python scripts\verify_hardware.py
   ```
   Check HTTP GET latency to Phoebe laser and TCP read latency to SPiiPlus stage.

2. **Conservative Jog Test**:
   ```cmd
   .venv312\Scripts\python scripts\smoke_test.py
   ```
   Verify 50 μm jog on X axis and laser toggle settlement times.

3. **Stage Halt Verification**:
   In an interactive Python shell on the Windows PC, connect `StageController`, start a slow move, and invoke `.halt()`. Confirm motion stops smoothly.

4. **Motion Profiling Calibration**:
   Run `MotionProfiling` at 1.0, 3.0, and 5.0 mm/s and record `accel_time_s` and `accel_distance_mm` in `config/default.yaml` under `synchronization.calibration`.

5. **Live Rig Mode**:
   Set `labgate.mode: "rig"` in `config/default.yaml`, restart `labgate-serve` (or Docker container), and execute a low-power single-line test plan with an operator present at the physical E-stop.
