"""导演响应在PostgreSQL中的原子持久化。

Ark返回候选后，响应身份、候选正文和成功/失败状态必须在同一个短事务内提交。
这样自动修复和人工审核都不会读取到“有失败原因但没有原候选”的半状态。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ...domain.snapshots import DirectorInputSnapshot
from ...domain.workflow import StepStatus, transition_step
from .models import WorkflowStep
from .query_repository import required_record


class DirectorStepPersistenceMixin:
    """为主Repository提供导演结果的原子成功与拒绝写入。"""

    _sessions: sessionmaker[Session]

    def finish_director_step(
        self,
        *,
        step_id: uuid.UUID,
        response_id: str,
        request_hash: str,
        provider_output: dict[str, Any],
        normalized_output: dict[str, Any] | None,
        normalization_warnings: tuple[str, ...],
    ) -> None:
        with self._sessions.begin() as session:
            row = required_record(session, WorkflowStep, step_id)
            row.status = transition_step(
                StepStatus(row.status),
                StepStatus.SUCCEEDED,
            ).value
            snapshot = DirectorInputSnapshot.model_validate(row.input_snapshot_json)
            row.input_snapshot_json = snapshot.model_copy(
                update={
                    "response_id": response_id,
                    "request_hash": request_hash,
                    "provider_output": provider_output,
                    "normalized_output": normalized_output,
                    "normalization_warnings": normalization_warnings,
                }
            ).model_dump(mode="json")
            row.completed_at = datetime.now(timezone.utc)

    def fail_director_step(
        self,
        *,
        step_id: uuid.UUID,
        response_id: str,
        request_hash: str,
        provider_output: dict[str, Any],
        normalized_output: dict[str, Any] | None,
        normalization_warnings: tuple[str, ...],
        code: str,
        message: str,
    ) -> None:
        """保存被拒绝候选，使下一次修复可引用完整原始输出。"""

        with self._sessions.begin() as session:
            row = required_record(session, WorkflowStep, step_id)
            row.status = transition_step(
                StepStatus(row.status),
                StepStatus.FAILED,
            ).value
            snapshot = DirectorInputSnapshot.model_validate(row.input_snapshot_json)
            row.input_snapshot_json = snapshot.model_copy(
                update={
                    "response_id": response_id,
                    "request_hash": request_hash,
                    "provider_output": provider_output,
                    "normalized_output": normalized_output,
                    "normalization_warnings": normalization_warnings,
                }
            ).model_dump(mode="json")
            row.error_json = {"code": code, "message": message}
            row.completed_at = datetime.now(timezone.utc)
