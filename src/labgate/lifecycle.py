"""Plan lifecycle — the server-enforced state machine (requirement F4).

draft -> validated -> approved -> queued -> running -> completed
              \\-> rejected                    \\-> failed / aborted

Approval is where proposer != approver is enforced (requirement F10):
the qualified human's sign-off is an authenticated API action, whatever
UI it arrives from — a chat confirmation on chat.photonics.ai ultimately
lands here with a real approver identity, or it doesn't count.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from .auth import Identity
from .errors import AuthError, TransitionError, UnknownPlanError
from .ids import new_id
from .spec import ExperimentSpec
from .validation import ValidationReport


class PlanState(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    REJECTED = "rejected"
    APPROVED = "approved"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


_ALLOWED: dict[PlanState, set[PlanState]] = {
    PlanState.DRAFT: {PlanState.VALIDATED, PlanState.REJECTED},
    PlanState.VALIDATED: {PlanState.APPROVED, PlanState.REJECTED},
    PlanState.REJECTED: set(),
    PlanState.APPROVED: {PlanState.QUEUED},
    PlanState.QUEUED: {PlanState.RUNNING, PlanState.ABORTED},
    PlanState.RUNNING: {PlanState.COMPLETED, PlanState.FAILED, PlanState.ABORTED},
    PlanState.COMPLETED: set(),
    PlanState.FAILED: set(),
    PlanState.ABORTED: set(),
}


class PlanRecord(BaseModel):
    plan_id: str
    spec: ExperimentSpec
    proposer: str
    approver: str | None = None
    state: PlanState = PlanState.DRAFT
    validation_report: ValidationReport | None = None
    created_at: str = ""
    history: list[dict] = Field(default_factory=list)


class PlanStore:
    """In-memory store with JSON persistence per plan under storage_dir/plans."""

    def __init__(self, storage_dir: Path) -> None:
        self._dir = storage_dir / "plans"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._plans: dict[str, PlanRecord] = {}
        self._lock = threading.Lock()
        for path in self._dir.glob("*.json"):
            record = PlanRecord.model_validate_json(path.read_text())
            self._plans[record.plan_id] = record

    def _persist(self, record: PlanRecord) -> None:
        (self._dir / f"{record.plan_id}.json").write_text(record.model_dump_json(indent=2))

    def _stamp(self, record: PlanRecord, event: str, actor: str) -> None:
        record.history.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "actor": actor,
            "state": record.state,
        })

    def create(self, spec: ExperimentSpec, proposer: Identity) -> PlanRecord:
        with self._lock:
            record = PlanRecord(
                plan_id=new_id("plan"), spec=spec, proposer=proposer.user_id,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            self._stamp(record, "created", proposer.user_id)
            self._plans[record.plan_id] = record
            self._persist(record)
            return record

    def get(self, plan_id: str) -> PlanRecord:
        try:
            return self._plans[plan_id]
        except KeyError:
            raise UnknownPlanError(f"unknown plan: {plan_id}") from None

    def snapshot(self, plan_id: str) -> PlanRecord:
        """Deep copy under the lock — safe to serialize while a run mutates."""
        with self._lock:
            return self.get(plan_id).model_copy(deep=True)

    def list(self) -> list[PlanRecord]:
        with self._lock:
            return sorted(
                (r.model_copy(deep=True) for r in self._plans.values()),
                key=lambda r: r.created_at, reverse=True,
            )

    def transition(self, plan_id: str, to_state: PlanState, actor: str) -> PlanRecord:
        with self._lock:
            record = self.get(plan_id)
            if to_state not in _ALLOWED[record.state]:
                raise TransitionError(
                    f"cannot go {record.state} -> {to_state} for {plan_id}")
            record.state = to_state
            self._stamp(record, f"-> {to_state}", actor)
            self._persist(record)
            return record

    def set_validated(self, plan_id: str, report: ValidationReport, actor: str) -> PlanRecord:
        with self._lock:
            record = self.get(plan_id)
            if record.state != PlanState.DRAFT:
                raise TransitionError(f"can only validate a draft plan (is {record.state})")
            record.validation_report = report
            record.state = PlanState.VALIDATED if report.ok else PlanState.REJECTED
            self._stamp(record, "validated" if report.ok else "rejected", actor)
            self._persist(record)
            return record

    def approve(self, plan_id: str, approver: Identity) -> PlanRecord:
        with self._lock:
            record = self.get(plan_id)
            if record.state != PlanState.VALIDATED:
                raise TransitionError(f"can only approve a validated plan (is {record.state})")
            if approver.user_id == record.proposer:
                raise AuthError("proposer and approver must be different users")
            record.approver = approver.user_id
            record.state = PlanState.APPROVED
            self._stamp(record, "approved", approver.user_id)
            self._persist(record)
            return record
