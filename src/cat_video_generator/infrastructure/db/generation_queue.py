"""Durable task leasing and cancellation for Creator Provider work."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from ...application.ports import VideoTaskResult
from .creator_repository import RecordNotFoundError, WorkflowConflictError
from .models import GenerationTask, GenerationTaskEvent


class CancellationGateway(Protocol):
    def get_video_task(self, task_id: str) -> VideoTaskResult: ...
    def cancel_video_task(self, task_id: str) -> VideoTaskResult: ...


@dataclass(frozen=True, slots=True)
class TaskLease:
    task_id: uuid.UUID
    worker_id: str
    expires_at: datetime


class CreatorTaskQueue:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        gateway: CancellationGateway | None = None,
    ) -> None:
        self._sessions = sessions
        self._gateway = gateway

    def claim_next(self, *, worker_id: str, lease_seconds: int = 90) -> TaskLease | None:
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            task = session.scalar(
                select(GenerationTask)
                .where(
                    GenerationTask.status.in_(
                        ("local_queued", "provider_queued", "provider_running")
                    ),
                    or_(
                        GenerationTask.next_attempt_at.is_(None),
                        GenerationTask.next_attempt_at <= now,
                    ),
                    or_(
                        GenerationTask.lease_expires_at.is_(None),
                        GenerationTask.lease_expires_at <= now,
                    ),
                )
                .order_by(GenerationTask.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return None
            expires = now + timedelta(seconds=lease_seconds)
            task.lease_owner = worker_id
            task.lease_expires_at = expires
            if task.status == "local_queued":
                task.status = "submitting"
            self._event(session, task, "task_claimed", {"workerId": worker_id})
            return TaskLease(task.id, worker_id, expires)

    def finish(
        self,
        lease: TaskLease,
        *,
        status: str,
        next_attempt_at: datetime | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            task = self._locked_owned_task(session, lease)
            task.status = status
            task.next_attempt_at = next_attempt_at
            task.lease_owner = None
            task.lease_expires_at = None
            self._event(session, task, "task_progressed", payload or {"status": status})

    def fail(self, lease: TaskLease, error: BaseException) -> None:
        submission_unknown = bool(getattr(error, "submission_unknown", False))
        status = "submission_unknown" if submission_unknown else "failed"
        with self._sessions.begin() as session:
            task = self._locked_owned_task(session, lease)
            task.status = status
            task.provider_status = "unknown" if submission_unknown else "failed"
            task.error_json = {
                "type": type(error).__name__,
                "message": str(error),
                "code": getattr(error, "code", None),
                "retryable": bool(getattr(error, "retryable", False)),
            }
            task.next_attempt_at = None
            task.lease_owner = None
            task.lease_expires_at = None
            self._event(session, task, "task_failed", task.error_json)

    def cancellation_for(self, task_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            task = session.get(GenerationTask, task_id)
            if task is None:
                raise RecordNotFoundError(f"creator task {task_id} not found")
            return self._cancellation_policy(task)

    def cancel(
        self,
        task_id: uuid.UUID,
        *,
        expected_status: str,
        expected_provider_task_id: str | None,
        reason: str | None,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            task = session.scalar(
                select(GenerationTask).where(GenerationTask.id == task_id).with_for_update()
            )
            if task is None:
                raise RecordNotFoundError(f"creator task {task_id} not found")
            if task.status != expected_status:
                raise WorkflowConflictError(
                    f"任务状态已变化：expected {expected_status}, current {task.status}"
                )
            if task.provider_task_id != expected_provider_task_id:
                raise WorkflowConflictError("Provider task ID 已变化，请刷新后重试")
            policy = self._cancellation_policy(task)
            if not policy["allowed"]:
                return {**self._task_dict(task), "cancellation": policy}
            if policy["mode"] == "local_before_provider":
                task.status = "cancelled"
                task.provider_status = "not_submitted"
                task.next_attempt_at = None
                self._event(
                    session,
                    task,
                    "task_cancelled_before_provider",
                    {"reason": reason, "providerCallCount": 0},
                )
                return {
                    **self._task_dict(task),
                    "cancellation": self._cancellation_policy(task),
                }
            provider_task_id = task.provider_task_id
            if self._gateway is None or provider_task_id is None:
                raise WorkflowConflictError("Provider 取消网关不可用")
            observed = self._gateway.get_video_task(provider_task_id)
            if observed.status == "running":
                task.status = "provider_running"
                task.provider_status = "running"
                self._event(session, task, "provider_running_cannot_cancel", {})
                return {
                    **self._task_dict(task),
                    "cancellation": self._cancellation_policy(task),
                }
            if observed.status != "queued":
                task.status = "cancellation_unknown"
                task.provider_status = observed.status or "unknown"
                self._event(
                    session,
                    task,
                    "provider_cancellation_requires_reconciliation",
                    {"observedStatus": observed.status},
                )
                return {
                    **self._task_dict(task),
                    "cancellation": self._cancellation_policy(task),
                }
            cancelled = self._gateway.cancel_video_task(provider_task_id)
            if cancelled.status not in {"cancelled", "deleted"}:
                task.status = "cancellation_unknown"
                task.provider_status = cancelled.status or "unknown"
            else:
                task.status = "cancelled"
                task.provider_status = "cancelled"
            self._event(
                session,
                task,
                "provider_cancellation_observed",
                {"providerStatus": task.provider_status, "reason": reason},
            )
            return {
                **self._task_dict(task),
                "cancellation": self._cancellation_policy(task),
            }

    @staticmethod
    def _cancellation_policy(task: GenerationTask) -> dict[str, Any]:
        if task.status == "local_queued" and task.provider_task_id is None:
            return {
                "allowed": task.lease_owner is None,
                "mode": "local_before_provider" if task.lease_owner is None else "unavailable",
                "label": (
                    "取消，尚未提交 Provider" if task.lease_owner is None else "Worker 正在准备提交"
                ),
                "disabledReason": None if task.lease_owner is None else "Worker 已领取任务",
                "providerStatus": "not_submitted",
                "costMayAlreadyApply": False,
            }
        if task.status == "provider_queued" and task.provider_task_id:
            return {
                "allowed": True,
                "mode": "provider_queued",
                "label": "取消 Provider 排队任务",
                "disabledReason": None,
                "providerStatus": "queued",
                "costMayAlreadyApply": True,
            }
        if task.status in {"submitting", "submission_unknown", "cancellation_unknown"}:
            return {
                "allowed": False,
                "mode": "reconcile_required",
                "label": "先对账再处理",
                "disabledReason": "无法证明 Provider 未提交或取消完成",
                "providerStatus": "unknown",
                "costMayAlreadyApply": True,
            }
        return {
            "allowed": False,
            "mode": "unavailable",
            "label": (
                "Provider 已运行，无法取消" if task.status == "provider_running" else "任务不可取消"
            ),
            "disabledReason": (
                "Provider 已开始生成" if task.status == "provider_running" else "任务已进入终态"
            ),
            "providerStatus": task.provider_status,
            "costMayAlreadyApply": task.provider_task_id is not None,
        }

    @staticmethod
    def _locked_owned_task(session: Session, lease: TaskLease) -> GenerationTask:
        task = session.scalar(
            select(GenerationTask).where(GenerationTask.id == lease.task_id).with_for_update()
        )
        if task is None:
            raise RecordNotFoundError(f"creator task {lease.task_id} not found")
        if task.lease_owner != lease.worker_id:
            raise WorkflowConflictError("任务 lease 已失效")
        return task

    @staticmethod
    def _event(
        session: Session,
        task: GenerationTask,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        session.add(
            GenerationTaskEvent(
                task_id=task.id,
                project_id=task.project_id,
                event_type=event_type,
                payload_json=payload,
            )
        )

    @staticmethod
    def _task_dict(task: GenerationTask) -> dict[str, Any]:
        return {
            "taskId": str(task.id),
            "projectId": str(task.project_id),
            "creatorShotId": str(task.creator_shot_id) if task.creator_shot_id else None,
            "generationSnapshotId": str(task.generation_snapshot_id),
            "status": task.status,
            "providerStatus": task.provider_status,
            "providerTaskId": task.provider_task_id,
            "provider": task.provider,
            "model": task.model,
            "inputHash": task.input_hash,
            "error": task.error_json,
        }
