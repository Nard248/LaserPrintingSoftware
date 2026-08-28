# Laser Printing Software & Labgate Control Platform

Control software and API gate platform for a **Two-Photon Polymerization (2PP) laser printing and ablation** system.

It coordinates two physical instruments:
- **Phoebe PH2 Femtosecond Laser** (515 nm, ~200.8 kHz, HTTP REST API @ `192.168.244.10`)
- **Standa 8MTL120XY 3-Axis Stage** (ACS SPiiPlus motion controller Ethernet TCP @ `10.0.0.100:701` via `SPiiPlusPython` C-bindings)

---

## Key Features

- **Cognition vs. Control Boundary**: AI models/chat clients propose declarative JSON recipe specifications (`ExperimentSpec`). The deterministic `labgate` platform checks physical bounds, travel ranges, and interlocks before executing.
- **4-Gate Safety Validation**: Automatic verification of numeric bounds, stage geometry limits ($\pm 25\text{ mm}$), operation ordering, and exposure uniformity.
- **Human Approval & Dual-Identity Enforcement**: Approval requires a qualified identity distinct from the proposer ($\text{proposer} \neq \text{approver}$).
- **Dry-Run Toolpath Preview**: Predicts duration, distance, exposure count, and renders a 2D/3D toolpath preview image for approval.
- **OpenAI OpenAPI 3.1.0 Compatible**: Built-in support for OpenAI Custom GPT Actions, Assistant API tools, and custom HTTP clients.
- **Postman Collection for Local Manual Testing**: Pre-built Postman collection & environment for manual end-to-end testing before AI wiring.

---

## Deliverables

### 1. OpenAI-Compatible OpenAPI Specification
- **JSON Spec**: [`Docs/openapi.json`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/openapi.json)
- **YAML Spec**: [`Docs/openapi.yaml`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/openapi.yaml)
- **Live Endpoints**:
  - `GET http://127.0.0.1:8523/openapi.json`
  - `GET http://127.0.0.1:8523/openapi.yaml`

Features standard `openapi: 3.1.0`, `servers` array, `bearerAuth` security scheme, and clean explicit `operationId`s on every route (`getHealth`, `getCapabilities`, `submitPlan`, `approvePlan`, `executePlan`, etc.).

### 2. Postman Testing Collection & Environment
Located under [`Docs/postman/`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/postman/):
- **Collection**: [`Docs/postman/labgate.postman_collection.json`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/postman/labgate.postman_collection.json)
- **Environment**: [`Docs/postman/labgate.postman_environment.json`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/postman/labgate.postman_environment.json)

Supports manual testing for:
- System health (`GET /health`), hardware capabilities (`GET /capabilities`), live device status (`GET /devices`).
- Single command execution (`MoveStage`, `SetLaserPower`, `WriteLine`, `CaptureImage`).
- Full multi-op experiment lifecycle (`submit` -> `dry-run` -> `approve` -> `execute` -> `poll status` -> `get results` -> `download artifacts`).
- 3D STL model upload & slicing workflows.

---

## Quickstart

### 1. Installation

```bash
cd LaserPrintingSoftware
pip install -e ".[dev]"
```

### 2. Run Tests (Simulation Mode, Off-Rig)

```bash
pytest tests/
```

### 3. Launch Local Server

```bash
labgate-serve
# Server starts at http://127.0.0.1:8523
```

Check liveness:
```bash
curl http://127.0.0.1:8523/health
```

### 4. Run with Docker Compose

```bash
docker compose up -d
curl http://localhost:8523/health
```

Persists all plans, telemetry logs, audit trails, and uploaded models to `./labgate_data`.

---

## Rig & Docker Deployment Guides

- **Docker Deployment Guide**: [`Docs/deployment/docker-deployment.md`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/deployment/docker-deployment.md)
- **On-Rig Physical Verification Guide**: [`Docs/deployment/rig-install.md`](file:///Users/narekmeloyan/PycharmProjects/Software%20Engineering/LaserPrintingSoftware/Docs/deployment/rig-install.md)

