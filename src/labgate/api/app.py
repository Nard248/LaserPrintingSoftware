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
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
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
)
from ..executor import ExecutionEngine
from ..lifecycle import PlanStore
from ..registry import CapabilityRegistry
from ..results import RunResults, UnknownArtifactError
from ..spec import ExperimentSpec
from ..validation import ValidationEngine
from .schemas import PlanSummary, SubmitPlanRequest

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
        self.validator = ValidationEngine(cfg)
        self.estimator = DryRunEstimator(cfg)
        self.engine = ExecutionEngine(self.registry, self.store, self.audit, cfg,
                                      exposure=exposure)


def create_app(cfg: LabgateConfig | None = None) -> FastAPI:
    if cfg is None:
        cfg = LabgateConfig.load(os.environ.get("LABGATE_CONFIG"))
    platform = Platform(cfg)
    app = FastAPI(
        title="labgate — 2PP lab control platform",
        version=__version__,
        description=_DESCRIPTION,
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
        elif isinstance(exc, DeviceError):
            status = 502
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    def require_read(identity: Identity) -> None:
        """Reads need SOME platform role — a token with no roles sees nothing."""
        if not (identity.has_role(Role.OPERATOR) or identity.has_role(Role.APPROVER)):
            raise AuthError(
                f"user '{identity.user_id}' has no role granting read access")

    # ------------------------------------------------------------ routes
    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "mode": platform.cfg.mode}

    @app.get("/capabilities")
    def capabilities(identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        return platform.registry.snapshot()

    @app.get("/devices")
    def devices(identity: Identity = Depends(current_identity)) -> list[dict]:
        require_read(identity)
        return [s.model_dump() for s in platform.registry.device_states()]

    @app.post("/plans", status_code=201)
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

    @app.get("/plans")
    def list_plans(identity: Identity = Depends(current_identity)) -> list[PlanSummary]:
        require_read(identity)
        return [PlanSummary.from_record(r) for r in platform.store.list()]

    @app.get("/plans/{plan_id}")
    def get_plan(plan_id: str, identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        return platform.store.snapshot(plan_id).model_dump()

    @app.post("/plans/{plan_id}/dry-run")
    def dry_run(plan_id: str, identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        record = platform.store.snapshot(plan_id)
        return platform.estimator.estimate(record.spec).model_dump()

    @app.post("/plans/{plan_id}/approve")
    def approve(plan_id: str, identity: Identity = Depends(current_identity)) -> PlanSummary:
        require_role(identity, Role.APPROVER)
        record = platform.store.approve(plan_id, identity)
        platform.audit.append("plan_approved", identity.user_id, {"plan_id": plan_id})
        return PlanSummary.from_record(record)

    @app.post("/plans/{plan_id}/execute", status_code=202)
    def execute(plan_id: str, identity: Identity = Depends(current_identity)) -> PlanSummary:
        require_role(identity, Role.OPERATOR)
        platform.engine.start(plan_id, identity.user_id)
        return PlanSummary.from_record(platform.store.get(plan_id))

    @app.post("/plans/{plan_id}/abort")
    def abort(plan_id: str, identity: Identity = Depends(current_identity)) -> dict:
        require_role(identity, Role.OPERATOR)
        platform.engine.abort(plan_id, identity.user_id)
        return {"status": "abort requested"}

    @app.get("/plans/{plan_id}/results")
    def results(plan_id: str, identity: Identity = Depends(current_identity)) -> dict:
        require_read(identity)
        platform.store.get(plan_id)  # 404 for unknown plans
        run = RunResults(Path(platform.cfg.storage_dir), plan_id)
        return {"manifest": run.manifest(), "events": run.events()}

    @app.get("/plans/{plan_id}/results/artifacts/{name}")
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

    @app.on_event("shutdown")
    def _shutdown() -> None:
        platform.engine.shutdown()

    return app


def main() -> None:  # console entry point: labgate-serve
    import uvicorn

    uvicorn.run("labgate.api.app:create_app", factory=True,
                host=os.environ.get("LABGATE_HOST", "127.0.0.1"),
                port=int(os.environ.get("LABGATE_PORT", "8523")))
