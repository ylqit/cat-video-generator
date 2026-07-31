"""导演响应在PostgreSQL中的原子持久化。

Ark返回候选后，响应身份、候选正文和成功/失败状态必须在同一个短事务内提交。
这样自动修复和人工审核都不会读取到“有失败原因但没有原候选”的半状态。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

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
        output: dict[str, Any],
    ) -> None:
        with self._sessions.begin() as session:
            row = required_record(session, WorkflowStep, step_id)
            row.status = transition_step(
                StepStatus(row.status),
                StepStatus.SUCCEEDED,
            ).value
            row.request_summary_json = {
                **row.request_summary_json,
                "responseId": response_id,
                "providerRequestHash": request_hash,
                "directorOutput": output,
            }
            row.completed_at = datetime.now(timezone.utc)

    def fail_director_step(
        self,
        *,
        step_id: uuid.UUID,
        response_id: str,
        request_hash: str,
        output: dict[str, Any],
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
            row.request_summary_json = {
                **row.request_summary_json,
                "responseId": response_id,
                "providerRequestHash": request_hash,
                "rejectedDirectorOutput": output,
            }
            row.error_json = {"code": code, "message": message}
            row.completed_at = datetime.now(timezone.utc)
