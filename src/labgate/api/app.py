"""HTTP surface of the platform — the integration contract (requirement N1).

Pure JSON over HTTP with auto-generated OpenAPI. Any client — the
chat.photonics.ai agents, a script, a GUI — drives the same lifecycle:

    POST /plans          submit a spec (auto-validates)
    POST /plans/{id}/dry-run
    POST /plans/{id}/approve      (role: approver, != proposer)
    POST /plans/{id}/execute
    GET  /plans/{id}              poll status
    GET  /plans/{id}/results      telemetry + artifact list

Run with:  labgate-serve  (or uvicorn "labgate.api.app:create_app" --factory)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .. import __version__
from ..audit import AuditLog
from ..auth import Identity, Role, TokenStore, require_role
from ..config import LabgateConfig
from ..dryrun import DryRunEstimator
from ..errors import (
    AuthError,
    DeviceError,
    LabgateError,
    TransitionError,
    UnknownPlanError,
    ValidationFailed,
)
from ..executor import ExecutionEngine
from ..lifecycle import PlanStore
from ..registry import CapabilityRegistry
from ..results import RunResults, UnknownArtifactError
from ..spec import ExperimentSpec
from ..validation import ValidationEngine
from .schemas import InvokeActionRequest, PlanSummary, SubmitPlanRequest

_DESCRIPTION = """
Deterministic control platform for the 2PP laser fabrication rig.

Lifecycle: draft -> validated -> approved -> queued -> running -> completed.
A plan is a declarative Experiment Specification; it is validated against
hard bounds, dry-runnable without hardware, approved by a qualified human
(never its own proposer), executed by the platform's deterministic engine,
and fully audited. AI planners are ordinary clients of this API.
"""


class Platform:
    """Wires the core components together; one instance per process."""

    def __init__(self, cfg: LabgateConfig) -> None:
        self.cfg = cfg
        Path(cfg.storage_dir).mkdir(parents=True, exist_ok=True)
        self.registry = CapabilityRegistry()
        if cfg.mode == "sim":
            from ..devices.sim import build_sim_adapters
            adapters = build_sim_adapters(cfg)
        else:
            from ..devices.rig import build_rig_adapters
            adapters = build_rig_adapters(cfg)
        for adapter in adapters:
            self.registry.register(adapter)
        stage = self.registry.by_kind("stage")
        laser = self.registry.by_kind("laser")
        if cfg.mode == "sim":
            from ..exposure import SimExposure
            exposure = SimExposure(stage, laser)
        else:
            from ..exposure import SyncExposure
            exposure = SyncExposure(stage, laser, cfg.hardware)
        self.tokens = TokenStore.load(cfg.tokens_file)
        self.audit = AuditLog(Path(cfg.storage_dir))
        self.store = PlanStore(Path(cfg.storage_dir))
        from ..geometry import GeometryService, ModelStore
        self.models = ModelStore(Path(cfg.storage_dir))
        self.geometry = GeometryService(self.models, Path(cfg.storage_dir))
        self.validator = ValidationEngine(cfg, geometry=self.geometry)
        self.estimator = DryRunEstimator(cfg, geometry=self.geometry)
        self.engine = ExecutionEngine(self.registry, self.store, self.audit, cfg,
                                      exposure=exposure, geometry=self.geometry)
        from ..devicectl import DeviceControl
        self.devicectl = DeviceControl(
            self.registry, self.audit, self.engine,
            allow_manual_beam=cfg.allow_manual_beam)
        self.started_at = time.time()


from contextlib import asynccontextmanager
import yaml
from fastapi.responses import FileResponse, Response
from fastapi.openapi.utils import get_openapi


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    app.state.platform.engine.shutdown()


def custom_openapi_schema(app: FastAPI) -> dict:
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="labgate — 2PP Lab Control Platform",
        version=__version__,
        description=_DESCRIPTION,
        routes=app.routes,
    )
    openapi_schema["openapi"] = "3.1.0"
    openapi_schema["servers"] = [
        {
            "url": f"http://{os.environ.get('LABGATE_HOST', '127.0.0.1')}:{os.environ.get('LABGATE_PORT', '8523')}",
            "description": "Local Labgate API Server",
        }
    ]
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "Token",
            "description": "Bearer token identifying the operator or approver user identity",
        }
    }
    # Clean up HTTPBearer references to bearerAuth for standard OpenAPI / OpenAI Actions compatibility
    openapi_schema["components"]["securitySchemes"].pop("HTTPBearer", None)
    for path_data in openapi_schema.get("paths", {}).values():
        for method_data in path_data.values():
            if isinstance(method_data, dict) and "security" in method_data:
                new_sec = []
                for sec in method_data["security"]:
                    if "HTTPBearer" in sec:
                        new_sec.append({"bearerAuth": []})
                    else:
                        new_sec.append(sec)
                method_data["security"] = new_sec
    openapi_schema["security"] = [{"bearerAuth": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


def create_app(cfg: LabgateConfig | None = None) -> FastAPI:
    if cfg is None:
        cfg = LabgateConfig.load(os.environ.get("LABGATE_CONFIG"))
    platform = Platform(cfg)
    app = FastAPI(
        title="labgate — 2PP lab control platform",
        version=__version__,
        description=_DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.platform = platform
    bearer = HTTPBearer(auto_error=False)

    # ------------------------------------------------------------- auth
    def current_identity(
        creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> Identity:
        if creds is None:
            raise HTTPException(401, "missing bearer token")
        try:
            return platform.tokens.resolve(creds.credentials)
        except AuthError:
            raise HTTPException(401, "invalid token") from None

    # ---------------------------------------------------- error mapping
    @app.exception_handler(LabgateError)
    async def _labgate_error(request: Request, exc: LabgateError):
        from fastapi.responses import JSONResponse
        status = 500
        if isinstance(exc, AuthError):
            status = 403
        elif isinstance(exc, UnknownPlanError):
            status = 404
        elif isinstance(exc, TransitionError):
            status = 409
        elif isinstance(exc, ValidationFailed):
            status = 422
        elif isinstance(exc, DeviceError):
            status = 502
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    def require_read(identity: Identity) -> None:
        """Reads need SOME platform role — a token with no roles sees nothing."""
        if not (identity.has_role(Role.OPERATOR) or identity.has_role(Role.APPROVER)):
            raise AuthError(
                f"user '{identity.user_id}' has no role granting read access")

    # ------------------------------------------------------------ routes
    @app.get("/health", operation_id="getHealth", summary="Check system health", description="Check system liveness, version, and platform mode (sim or rig).", openapi_extra={"security": []})
    def health() -> dict:
        return {"status": "ok", "version": __version__, "mode": platform.cfg.mode}

    @app.get("/capabilities", operation_id="getCapabilities", summary="Get hardware capabilities", description="Query machine-readable grounding bounds, devices, and operations supported by this rig.")
    def capabilities(identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        return platform.registry.snapshot()

    @app.get("/devices", operation_id="getDevices", summary="Get device states", description="Get real-time live connection status and coordinates for all active devices.")
    def devices(identity: Identity = Depends(current_identity)) -> list[dict]:
        require_read(identity)
        return [s.model_dump() for s in platform.registry.device_states()]

    # ------------------------------------------- device control plane
    # Plans are for experiments: validated, approved by a second person,
    # queued. These endpoints are the other half — bringing the rig up,
    # aligning it, diagnosing it. Governed by risk tier (labgate.actions)
    # rather than by approval; the shutter stays plan-only.

    def _adapter_or_404(device_id: str):
        try:
            return platform.registry.adapter(device_id)
        except KeyError:
            known = sorted(a.device_id for a in platform.registry.adapters())
            raise HTTPException(404, f"unknown device '{device_id}'; known: {known}")

    @app.get("/devices/{device_id}", operation_id="getDevice",
             summary="Get one device in detail",
             description="Live state, declared plan capabilities and declared device actions.")
    def device_detail(device_id: str,
                      identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        adapter = _adapter_or_404(device_id)
        return {
            "state": adapter.state().model_dump(),
            "capabilities": [c.model_dump() for c in adapter.capabilities()],
            "actions": [a.model_dump() for a in platform.devicectl.describe(device_id)],
        }

    @app.get("/devices/{device_id}/actions", operation_id="listDeviceActions",
             summary="List device actions",
             description="Declared interactive actions with their tiers, parameters and bounds.")
    def device_actions(device_id: str,
                       identity: Identity = Depends(current_identity)) -> list[dict]:
        require_read(identity)
        _adapter_or_404(device_id)
        return [a.model_dump() for a in platform.devicectl.describe(device_id)]

    @app.post("/devices/{device_id}/connect", operation_id="connectDevice",
              summary="Connect a device",
              description="Open the link to the instrument. For the stage this also enables and commutates the servo axes.")
    def device_connect(device_id: str,
                       identity: Identity = Depends(current_identity)) -> dict:
        require_role(identity, Role.OPERATOR)
        adapter = _adapter_or_404(device_id)
        adapter.connect()
        platform.audit.append("device_connect", identity.user_id,
                              {"device_id": device_id})
        return adapter.state().model_dump()

    @app.post("/devices/{device_id}/disconnect", operation_id="disconnectDevice",
              summary="Disconnect a device",
              description="Safe-state then release the instrument.")
    def device_disconnect(device_id: str,
                          identity: Identity = Depends(current_identity)) -> dict:
        require_role(identity, Role.OPERATOR)
        adapter = _adapter_or_404(device_id)
        if platform.devicectl._run_is_active():
            raise TransitionError(
                "a plan is running or queued; disconnecting now would pull the rig "
                "out from under it")
        adapter.disconnect()
        platform.audit.append("device_disconnect", identity.user_id,
                              {"device_id": device_id})
        return adapter.state().model_dump()

    @app.post("/devices/{device_id}/diagnose", operation_id="diagnoseDevice",
              summary="Run a device self-test",
              description="Read-only probe of reachability, driver availability and reported health. Never actuates.")
    def device_diagnose(device_id: str,
                        identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        adapter = _adapter_or_404(device_id)
        checks = adapter.diagnose()
        blockers = [c for c in checks if not c.ok and c.severity == "blocker"]
        return {"device_id": device_id, "ok": not blockers,
                "checks": [c.model_dump() for c in checks]}

    @app.post("/devices/{device_id}/actions/{action}", operation_id="invokeDeviceAction",
              summary="Invoke a device action",
              description="Run one declared action (home, jog, set_velocity, snapshot, ...). Bounds-checked, interlocked and audited.")
    def invoke_action(device_id: str, action: str,
                      body: InvokeActionRequest | None = None,
                      identity: Identity = Depends(current_identity)) -> dict:
        _adapter_or_404(device_id)
        params = (body.params if body else {}) or {}
        result = platform.devicectl.invoke(device_id, action, params, identity)
        payload = result.model_dump()
        # A snapshot returns raw image bytes; persist them and hand back a URL
        # instead of stuffing binary into JSON.
        image = payload.get("data", {}).pop("image_bytes", None)
        if image:
            name = _save_snapshot(device_id, params.get("label"), image)
            payload["data"]["snapshot"] = name
            payload["data"]["url"] = f"/snapshots/{name}"
        return payload

    def _save_snapshot(device_id: str, label: str | None, image: bytes) -> str:
        import re
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        raw = f"{device_id}_{label or 'snapshot'}_{stamp}"
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", raw) + ".png"
        folder = Path(platform.cfg.storage_dir) / "snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(image)
        return name

    @app.get("/snapshots/{name}", operation_id="getSnapshot",
             summary="Fetch a device snapshot",
             description="Download an image captured by a camera snapshot action.")
    def get_snapshot(name: str, identity: Identity = Depends(current_identity)):
        require_read(identity)
        safe = Path(name).name
        if safe != name or not safe.endswith(".png"):
            raise HTTPException(404, f"no snapshot named '{name}'")
        path = Path(platform.cfg.storage_dir) / "snapshots" / safe
        if not path.exists():
            raise HTTPException(404, f"no snapshot named '{name}'")
        return FileResponse(path)

    # ------------------------------------------------ system diagnostics
    @app.get("/system/status", operation_id="getSystemStatus",
             summary="Get full system status",
             description="Mode, version, uptime, policy, per-device rollup, queue depth and storage — everything at a glance.")
    def system_status(identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        states = platform.registry.device_states()
        return {
            "version": __version__,
            "mode": platform.cfg.mode,
            "uptime_s": round(time.time() - platform.started_at, 1),
            "policy": {"allow_manual_beam": platform.cfg.allow_manual_beam},
            "devices": {
                s.device_id: {"kind": s.kind, "connected": s.connected,
                              "detail": s.detail}
                for s in states
            },
            "devices_connected": sum(1 for s in states if s.connected),
            "devices_total": len(states),
            "queue": platform.engine.queue_snapshot(),
            "plans_total": len(platform.store.list()),
            "storage_dir": str(platform.cfg.storage_dir),
        }

    @app.get("/system/preflight", operation_id="getPreflight",
             summary="Check readiness to run",
             description="Aggregated readiness checklist. Each failure carries a remedy — either an API call, or a physical instruction flagged 'manual'.")
    def system_preflight(identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        from ..preflight import run_preflight
        report = run_preflight(platform.registry, platform.engine, platform.cfg.mode)
        return report.model_dump()

    @app.post("/system/estop", operation_id="emergencyStop",
              summary="Emergency stop",
              description="Immediately safe-state every device (laser first, then stage, then illumination) and abort any running plan.")
    def emergency_stop(identity: Identity = Depends(current_identity)) -> dict:
        require_role(identity, Role.OPERATOR)
        platform.audit.append("estop", identity.user_id, {})
        aborted, errors = [], []
        snapshot = platform.engine.queue_snapshot()
        for plan_id in filter(None, [snapshot.get("running"), *(snapshot.get("queued") or [])]):
            try:
                platform.engine.abort(plan_id, identity.user_id)
                aborted.append(plan_id)
            except Exception as exc:  # noqa: BLE001 — report, never mask
                errors.append(f"abort {plan_id}: {exc}")
        for adapter in platform.engine._ordered_adapters():
            try:
                adapter.safe_state()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{adapter.device_id}: {exc}")
        return {"ok": not errors, "aborted_plans": aborted, "errors": errors,
                "devices": [s.model_dump() for s in platform.registry.device_states()]}

    @app.post("/plans", status_code=201, operation_id="submitPlan", summary="Submit experiment plan", description="Submit a declarative Experiment Specification for validation and approval. Auto-runs validation checks.")
    def submit_plan(
        body: SubmitPlanRequest, identity: Identity = Depends(current_identity),
    ) -> PlanSummary:
        require_role(identity, Role.OPERATOR)
        record = platform.store.create(body.spec, identity)
        platform.audit.append("plan_submitted", identity.user_id,
                              {"plan_id": record.plan_id, "title": body.spec.title})
        report = platform.validator.validate(body.spec)
        record = platform.store.set_validated(record.plan_id, report, "validator")
        platform.audit.append("plan_validated", "validator",
                              {"plan_id": record.plan_id, "ok": report.ok})
        return PlanSummary.from_record(record)

    @app.get("/plans", operation_id="listPlans", summary="List experiment plans", description="List summaries of all submitted experiment plans and their status.")
    def list_plans(identity: Identity = Depends(current_identity)) -> list[PlanSummary]:
        require_read(identity)
        return [PlanSummary.from_record(r) for r in platform.store.list()]

    @app.get("/plans/{plan_id}", operation_id="getPlan", summary="Get plan details", description="Get full plan record including validation report, state, and identity history.")
    def get_plan(plan_id: str, identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        return platform.store.snapshot(plan_id).model_dump()

    @app.post("/plans/{plan_id}/dry-run", operation_id="dryRunPlan", summary="Dry run & preview plan", description="Estimate motion distance, duration, and render toolpath preview PNG for approval.")
    def dry_run(plan_id: str, identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        record = platform.store.snapshot(plan_id)
        report = platform.estimator.estimate(record.spec)
        # Render the toolpath the approver is being asked to sign off on.
        from ..preview import PREVIEW_ARTIFACT, render_preview
        run = RunResults(Path(platform.cfg.storage_dir), plan_id)
        try:
            if render_preview(record.spec, platform.geometry,
                              run.artifact_path(PREVIEW_ARTIFACT)):
                report.preview_artifact = PREVIEW_ARTIFACT
        except Exception as exc:  # noqa: BLE001 — preview is best-effort
            platform.audit.append("preview_error", identity.user_id,
                                  {"plan_id": plan_id, "error": str(exc)})
        return report.model_dump()

    @app.post("/plans/{plan_id}/rerun", status_code=201, operation_id="rerunPlan", summary="Clone & rerun plan", description="Clone an existing plan's recipe into a fresh plan that starts from draft/validated state.")
    def rerun(plan_id: str, identity: Identity = Depends(current_identity)) -> PlanSummary:
        """Reproducibility: clone a plan's spec into a NEW plan (fresh
        validation + fresh approval). The recipe re-runs without any AI."""
        require_role(identity, Role.OPERATOR)
        source = platform.store.snapshot(plan_id)
        record = platform.store.create(source.spec, identity)
        platform.audit.append("plan_rerun", identity.user_id,
                              {"plan_id": record.plan_id, "source": plan_id})
        report = platform.validator.validate(source.spec)
        record = platform.store.set_validated(record.plan_id, report, "validator")
        return PlanSummary.from_record(record)

    @app.get("/queue", operation_id="getQueue", summary="Get execution queue", description="View current running plan and queued plans in the execution engine.")
    def execution_queue(identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        return platform.engine.queue_snapshot()

    @app.post("/models", status_code=201, operation_id="uploadModel", summary="Upload STL model", description="Upload a 3D STL mesh model file for print_stl operations.")
    async def upload_model(
        file: UploadFile, identity: Identity = Depends(current_identity),
    ) -> dict:
        """Upload an STL model; returns a content-addressed model_id that
        print_stl operations reference."""
        require_role(identity, Role.OPERATOR)
        data = await file.read()
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(413, "model larger than 50 MB")
        try:
            info = platform.models.save(file.filename or "model.stl", data)
        except ValidationFailed as exc:
            raise HTTPException(422, str(exc)) from None
        platform.audit.append("model_uploaded", identity.user_id,
                              info.model_dump())
        return info.model_dump()

    @app.get("/models", operation_id="listModels", summary="List STL models", description="List all uploaded STL 3D models stored on the platform.")
    def list_models(identity: Identity = Depends(current_identity)) -> list[dict]:
        require_read(identity)
        return [m.model_dump() for m in platform.models.list()]

    @app.get("/models/{model_id}", operation_id="getModelInfo", summary="Get STL model info", description="Get metadata, dimensions, and point count for an uploaded STL model.")
    def model_info(model_id: str,
                   identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        try:
            return platform.models.info(model_id).model_dump()
        except ValidationFailed as exc:
            raise HTTPException(404, str(exc)) from None

    @app.post("/plans/{plan_id}/approve", operation_id="approvePlan", summary="Approve experiment plan", description="Human approver authorizes a plan for execution. Enforces proposer != approver.")
    def approve(plan_id: str, identity: Identity = Depends(current_identity)) -> PlanSummary:
        require_role(identity, Role.APPROVER)
        record = platform.store.approve(plan_id, identity)
        platform.audit.append("plan_approved", identity.user_id, {"plan_id": plan_id})
        return PlanSummary.from_record(record)

    @app.post("/plans/{plan_id}/execute", status_code=202, operation_id="executePlan", summary="Execute approved plan", description="Enqueue an approved plan for execution on hardware or simulator.")
    def execute(plan_id: str, identity: Identity = Depends(current_identity)) -> PlanSummary:
        require_role(identity, Role.OPERATOR)
        platform.engine.start(plan_id, identity.user_id)
        return PlanSummary.from_record(platform.store.get(plan_id))

    @app.post("/plans/{plan_id}/abort", operation_id="abortPlan", summary="Abort plan execution", description="Cooperatively abort a queued or executing plan and immediately safe-state hardware.")
    def abort(plan_id: str, identity: Identity = Depends(current_identity)) -> dict:
        require_role(identity, Role.OPERATOR)
        platform.engine.abort(plan_id, identity.user_id)
        return {"status": "abort requested"}

    @app.get("/plans/{plan_id}/results", operation_id="getPlanResults", summary="Get plan results", description="Fetch telemetry events and list of generated artifacts for a completed run.")
    def results(plan_id: str, identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        platform.store.get(plan_id)  # 404 for unknown plans
        run = RunResults(Path(platform.cfg.storage_dir), plan_id)
        return {"manifest": run.manifest(), "events": run.events()}

    @app.get("/plans/{plan_id}/results/artifacts/{name}", operation_id="getPlanArtifact", summary="Fetch artifact file", description="Download a generated image, toolpath preview, or log artifact file.")
    def artifact(
        plan_id: str, name: str, identity: Identity = Depends(current_identity),
    ):
        require_read(identity)
        platform.store.get(plan_id)
        run = RunResults(Path(platform.cfg.storage_dir), plan_id)
        try:
            path = run.artifact_path(name)
        except UnknownArtifactError:
            raise HTTPException(404, f"no artifact named '{name}'") from None
        if not path.exists():
            raise HTTPException(404, f"no artifact named '{name}'")
        return FileResponse(path)

    @app.get("/openapi.yaml", include_in_schema=False)
    def openapi_yaml() -> Response:
        schema = custom_openapi_schema(app)
        return Response(content=yaml.dump(schema, sort_keys=False), media_type="application/x-yaml")

    app.openapi = lambda: custom_openapi_schema(app)

    return app

    return app


def main() -> None:  # console entry point: labgate-serve
    import uvicorn

    uvicorn.run("labgate.api.app:create_app", factory=True,
                host=os.environ.get("LABGATE_HOST", "127.0.0.1"),
                port=int(os.environ.get("LABGATE_PORT", "8523")))
