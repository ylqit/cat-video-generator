"""Lease-based PostgreSQL worker queue for durable workflow steps."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from ...domain.workflow import StepStatus
from .models import CanvasEvent, WorkflowStep
from .repositories import RecordNotFoundError, WorkflowConflictError

_RECOVERABLE_STATUSES = frozenset(
    {
        StepStatus.PENDING.value,
        StepStatus.SUBMITTING.value,
        StepStatus.QUEUED.value,
        StepStatus.RUNNING.value,
    }
)


@dataclass(frozen=True, slots=True)
class DurableLease:
    step_id: uuid.UUID
    project_id: uuid.UUID
    operation_key: str
    status: str
    attempt: int
    input_snapshot: dict[str, object]
    lease_owner: str
    lease_expires_at: datetime
    provider_task_id: str | None
    progress: dict[str, object]


def is_claimable(
    *,
    status: str,
    lease_expires_at: datetime | None,
    next_retry_at: datetime | None,
    now: datetime,
) -> bool:
    if status not in _RECOVERABLE_STATUSES:
        return False
    if next_retry_at is not None and next_retry_at > now:
        return False
    return lease_expires_at is None or lease_expires_at <= now


def operation_matches(operation_key: str, prefixes: tuple[str, ...]) -> bool:
    return not prefixes or operation_key.startswith(prefixes)


class DurableWorkflowQueue:
    """Claims work with ``FOR UPDATE SKIP LOCKED`` and renewable leases."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        operation_prefixes: tuple[str, ...] = (),
    ) -> DurableLease | None:
        normalized_worker = worker_id.strip()
        if not normalized_worker:
            raise ValueError("worker_id cannot be empty")
        if lease_seconds < 10 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 10 and 3600")
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=lease_seconds)
        with self._sessions.begin() as session:
            query = select(WorkflowStep).where(
                WorkflowStep.status.in_(_RECOVERABLE_STATUSES),
                or_(WorkflowStep.next_retry_at.is_(None), WorkflowStep.next_retry_at <= now),
                or_(
                    WorkflowStep.lease_expires_at.is_(None),
                    WorkflowStep.lease_expires_at <= now,
                ),
            )
            if operation_prefixes:
                query = query.where(
                    or_(
                        *(WorkflowStep.operation_key.startswith(prefix)
                          for prefix in operation_prefixes)
                    )
                )
            row = session.scalar(
                query
                .order_by(WorkflowStep.next_retry_at.nullsfirst(), WorkflowStep.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            if row.status in {StepStatus.PENDING.value, StepStatus.QUEUED.value}:
                row.status = StepStatus.RUNNING.value
            row.lease_owner = normalized_worker
            row.lease_expires_at = expires
            row.heartbeat_at = now
            _record_task_event(session, row, "task_running")
            return _lease(row)

    def heartbeat(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> DurableLease:
        if lease_seconds < 10 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 10 and 3600")
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            self._require_owner(row, worker_id)
            if row.status not in _RECOVERABLE_STATUSES:
                raise WorkflowConflictError("completed workflow steps cannot renew a lease")
            row.heartbeat_at = now
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return _lease(row)

    def update_progress(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
        current_step: int,
        total_steps: int,
        percent: int,
        message: str,
    ) -> DurableLease:
        if total_steps < 1:
            raise ValueError("total_steps must be positive")
        if current_step < 0 or current_step > total_steps:
            raise ValueError("current_step must be between zero and total_steps")
        if percent < 0 or percent > 100:
            raise ValueError("percent must be between zero and 100")
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("progress message cannot be empty")
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            self._require_owner(row, worker_id)
            if row.status not in _RECOVERABLE_STATUSES:
                raise WorkflowConflictError("completed workflow steps cannot report progress")
            row.progress_json = {
                **dict(row.progress_json or {}),
                "currentStep": current_step,
                "totalSteps": total_steps,
                "percent": percent,
                "message": normalized_message,
            }
            _record_task_event(session, row, "task_progress")
            return _lease(row)

    def finish(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
        status: StepStatus,
        error: dict[str, object] | None = None,
        next_retry_at: datetime | None = None,
        result_summary: dict[str, object] | None = None,
        progress_update: dict[str, object] | None = None,
    ) -> None:
        if status not in {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.SUBMISSION_UNKNOWN,
            StepStatus.PENDING,
            StepStatus.AWAITING_REVIEW,
            StepStatus.QUEUED,
        }:
            raise ValueError("lease finish status is not supported")
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            self._require_owner(row, worker_id)
            row.status = status.value
            row.error_json = error
            row.next_retry_at = next_retry_at
            if progress_update is not None:
                row.progress_json = {
                    **dict(row.progress_json or {}),
                    **progress_update,
                }
            if result_summary is not None:
                row.progress_json = {
                    **dict(row.progress_json or {}),
                    "resultSummary": result_summary,
                }
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = now
            if status in {
                StepStatus.SUCCEEDED,
                StepStatus.FAILED,
                StepStatus.SUBMISSION_UNKNOWN,
                StepStatus.AWAITING_REVIEW,
            }:
                row.completed_at = now
            _record_task_event(session, row, _event_type_for_status(status))
            if status in {StepStatus.SUCCEEDED, StepStatus.AWAITING_REVIEW}:
                session.add(
                    CanvasEvent(
                        production_run_id=row.production_run_id,
                        event_type="canvas_projection_changed",
                        data_json={
                            "stepId": str(row.id),
                            "canvasNodeId": row.input_snapshot_json.get("canvasNodeId"),
                            "canvasGroupId": row.input_snapshot_json.get("canvasGroupId"),
                            "recipeInstanceId": row.input_snapshot_json.get("recipeInstanceId"),
                        },
                    )
                )

    @staticmethod
    def _locked_step(session: Session, step_id: uuid.UUID) -> WorkflowStep:
        row = session.scalar(
            select(WorkflowStep).where(WorkflowStep.id == step_id).with_for_update()
        )
        if row is None:
            raise RecordNotFoundError(f"WorkflowStep {step_id} was not found")
        return row

    @staticmethod
    def _require_owner(row: WorkflowStep, worker_id: str) -> None:
        if row.lease_owner != worker_id:
            raise WorkflowConflictError("workflow lease is owned by another worker")


def _lease(row: WorkflowStep) -> DurableLease:
    if row.lease_owner is None or row.lease_expires_at is None:
        raise RuntimeError("claimed workflow row has no lease metadata")
    return DurableLease(
        step_id=row.id,
        project_id=row.production_run_id,
        operation_key=row.operation_key,
        status=row.status,
        attempt=row.attempt,
        input_snapshot=row.input_snapshot_json,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        provider_task_id=row.provider_task_id,
        progress=dict(row.progress_json or {}),
    )


def _event_type_for_status(status: StepStatus) -> str:
    return {
        StepStatus.SUCCEEDED: "task_succeeded",
        StepStatus.FAILED: "task_failed",
        StepStatus.SUBMISSION_UNKNOWN: "task_submission_unknown",
        StepStatus.AWAITING_REVIEW: "task_awaiting_review",
        StepStatus.PENDING: "task_progress",
        StepStatus.QUEUED: "task_progress",
    }[status]


def _record_task_event(
    session: Session,
    row: WorkflowStep,
    event_type: str,
) -> None:
    snapshot = dict(row.input_snapshot_json or {})
    progress = dict(row.progress_json or {})
    session.add(
        CanvasEvent(
            production_run_id=row.production_run_id,
            event_type=event_type,
            data_json={
                "stepId": str(row.id),
                "projectId": str(row.production_run_id),
                "status": row.status,
                "operationKey": row.operation_key,
                "kind": row.kind,
                "canvasNodeId": snapshot.get("canvasNodeId"),
                "canvasGroupId": snapshot.get("canvasGroupId"),
                "recipeInstanceId": snapshot.get("recipeInstanceId"),
                "businessObjectId": snapshot.get("businessObjectId"),
                "parentStepId": snapshot.get("parentStepId"),
                "childStepIds": progress.get("childStepIds", []),
                "sceneId": None if row.scene_id is None else str(row.scene_id),
                "shotId": None if row.shot_card_id is None else str(row.shot_card_id),
                "phase": snapshot.get("phase") or snapshot.get("workflowStage"),
                "creationMode": snapshot.get("creationMode"),
                "progress": progress,
                "resultSummary": progress.get("resultSummary"),
                "providerStatus": progress.get("providerStatus"),
                "providerTaskId": row.provider_task_id,
                "error": row.error_json,
                "completedAt": row.completed_at.isoformat() if row.completed_at else None,
            },
        )
    )
