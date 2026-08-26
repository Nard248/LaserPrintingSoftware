"""Request/response models for the HTTP surface."""

from __future__ import annotations

from pydantic import BaseModel

from ..lifecycle import PlanRecord
from ..spec import ExperimentSpec


class SubmitPlanRequest(BaseModel):
    spec: ExperimentSpec


class PlanSummary(BaseModel):
    plan_id: str
    title: str
    state: str
    proposer: str
    approver: str | None
    validation_summary: str | None
    created_at: str

    @classmethod
    def from_record(cls, record: PlanRecord) -> "PlanSummary":
        return cls(
            plan_id=record.plan_id,
            title=record.spec.title,
            state=record.state,
            proposer=record.proposer,
            approver=record.approver,
            validation_summary=(
                record.validation_report.summary() if record.validation_report else None
            ),
            created_at=record.created_at,
        )
