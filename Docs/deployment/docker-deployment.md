# Docker Deployment Guide - Labgate 2PP Control Platform

This guide explains how to run the **Labgate 2PP Control Platform** inside a Docker container on the Lab PC or any development machine.

---

## 1. Overview

The Dockerized platform encapsulates:
- The **FastAPI REST API Server** (`labgate-serve` on port `8523`).
- The **Deterministic Safety Firewall** & 4-gate validation engine.
- The **Single-Worker Execution Engine** & queuing system.
- The **OpenAI-Compatible OpenAPI Spec Generator** (`/openapi.json` & `/openapi.yaml`).
- Persistent storage for experiment plans, telemetry logs, audit trails, and 3D STL models.

---

## 2. File Structure & Mount Points

```
LaserPrintingSoftware/
├── Dockerfile                   # Multi-stage/slim Python 3.12 Docker build
├── docker-compose.yml           # Docker Compose orchestration service
├── .dockerignore                # Excludes virtual environments & temporary files
├── config/                      # MOUNTED: Configuration & tokens
│   ├── default.yaml
│   └── tokens.yaml (or tokens.example.yaml)
└── labgate_data/                # MOUNTED: Persistent database & file storage
    ├── plans/                   # Stored experiment specifications & states
    ├── runs/                    # Execution telemetry logs & artifact images
    ├── models/                  # Uploaded 3D STL meshes
    └── audit.jsonl              # Append-only security audit ledger
```

---

## 3. Quickstart (Running with Docker Compose)

### Step 1: Start the Platform Container
Run the following command on the Lab PC:

```bash
docker compose up -d --build
```

This starts the `labgate_platform` container in detached mode, exposing port `8523`.

### Step 2: Verify System Health
Check that the platform is live and healthy:

```bash
curl http://localhost:8523/health
```

Expected response:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "mode": "sim"
}
```

### Step 3: Check Docker Container Logs
```bash
docker compose logs -f
```

### Step 4: Stop the Platform Container
```bash
docker compose down
```

---

## 4. Configuration & Database Persistence

### Database & Telemetry Persistence
The platform stores all plans, telemetry streams, audit logs, and 3D models in `./labgate_data`. The `docker-compose.yml` mounts `./labgate_data` directly into the container (`/app/labgate_data`), ensuring **100% data persistence across container restarts or upgrades**.

### Token & Hardware Configuration
The `./config` directory is also mounted into the container. To update hardware IPs, bounds, or authorization tokens:
1. Edit `config/default.yaml` or `config/tokens.yaml` on the host machine.
2. Restart the container (`docker compose restart`).

---

## 5. Network Modes & Rig Deployment

### Simulation Mode (`mode: "sim"`)
For development, AI integration testing, and CI/CD:
- Leaves `labgate.mode: "sim"` in `config/default.yaml`.
- Runs fully self-contained on any OS (Windows, Linux, macOS).

### Physical Rig Mode (`mode: "rig"`)
When deploying on the physical lab PC connected to the Phoebe laser (`192.168.244.10`) and ACS SPiiPlus stage (`10.0.0.100:701`):
1. In `config/default.yaml`, set `labgate.mode: "rig"`.
2. Enable host networking in `docker-compose.yml` if running on Linux:
   ```yaml
   network_mode: "host"
   ```
   Or keep port `8523:8523` for Windows Docker Desktop.
3. Restart the container (`docker compose restart`).
