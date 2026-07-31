"""本地HTTP接口的进程内后台任务登记。

视频与规划都是分钟级长任务，HTTP请求必须立即返回；真实状态由
PostgreSQL工作流表持有，这里只登记任务句柄用于去重、进度展示和错误呈现。
付费任务经过进程级串行门，避免并发提交造成重复扣费。进程重启后任务列表
清空，恢复语义仍由数据库中的provider task ID与resume用例兜底。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..application.ports import GatewayError

PAID_KINDS = frozenset(
    {
        "plan_day",
        "run_day",
        "resume_planning",
        "replan_episode",
        "compare_resolution",
        "retry_step",
        "prepare_keyframes",
    }
)

_ACTIVE_STATUSES = frozenset({"queued", "running"})


class JobConflictError(RuntimeError):
    """相同去重键的任务正在执行，禁止重复提交。"""

    def __init__(self, message: str, *, job_id: str) -> None:
        super().__init__(message)
        self.job_id = job_id


@dataclass(slots=True)
class JobRecord:
    """一次后台任务的可序列化状态。"""

    job_id: str
    kind: str
    dedup_key: str
    status: str = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: Any = None
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为HTTP响应使用的camelCase字典。"""

        return {
            "jobId": self.job_id,
            "kind": self.kind,
            "dedupKey": self.dedup_key,
            "status": self.status,
            "createdAt": self.created_at.isoformat(),
            "startedAt": (
                None if self.started_at is None else self.started_at.isoformat()
            ),
            "finishedAt": (
                None if self.finished_at is None else self.finished_at.isoformat()
            ),
            "result": self.result,
            "error": self.error,
        }


class JobRegistry:
    """登记、去重并串行执行付费后台任务。"""

    def __init__(
        self,
        *,
        executor: ThreadPoolExecutor | None = None,
        inline: bool = False,
    ) -> None:
        self._executor = executor
        self._inline = inline
        self._records: dict[str, JobRecord] = {}
        self._records_lock = threading.Lock()
        self._paid_gate = threading.Lock()

    def submit(
        self,
        *,
        kind: str,
        dedup_key: str,
        fn: Callable[[], Any],
    ) -> JobRecord:
        """登记并启动任务；相同去重键的活跃任务存在时拒绝。"""

        with self._records_lock:
            for record in self._records.values():
                if record.dedup_key == dedup_key and record.status in _ACTIVE_STATUSES:
                    raise JobConflictError(
                        "相同任务正在执行，请等待完成后再提交",
                        job_id=record.job_id,
                    )
            record = JobRecord(
                job_id=uuid.uuid4().hex,
                kind=kind,
                dedup_key=dedup_key,
            )
            self._records[record.job_id] = record
        if self._inline:
            self._execute(record, fn)
            return record
        if self._executor is None:
            raise RuntimeError("非内联模式必须提供线程池执行器")
        self._executor.submit(self._execute, record, fn)
        return record

    def get(self, job_id: str) -> JobRecord:
        """按ID返回任务；未知ID抛出LookupError。"""

        try:
            return self._records[job_id]
        except KeyError as exc:
            raise LookupError(f"任务{job_id}不存在") from exc

    def list(self, *, limit: int = 50) -> list[JobRecord]:
        """按创建时间倒序返回最近任务。"""

        return sorted(
            self._records.values(),
            key=lambda record: record.created_at,
            reverse=True,
        )[:limit]

    def _execute(self, record: JobRecord, fn: Callable[[], Any]) -> None:
        try:
            if record.kind in PAID_KINDS:
                with self._paid_gate:
                    self._invoke(record, fn)
            else:
                self._invoke(record, fn)
        except Exception as exc:  # 任务错误必须落进记录而不是丢失
            record.status = "failed"
            record.error = _classify_error(exc)
        finally:
            record.finished_at = datetime.now(UTC)

    @staticmethod
    def _invoke(record: JobRecord, fn: Callable[[], Any]) -> None:
        record.status = "running"
        record.started_at = datetime.now(UTC)
        record.result = fn()
        record.status = "succeeded"


def _classify_error(exc: Exception) -> dict[str, str]:
    """把异常分级为前端可展示的稳定错误码。"""

    if isinstance(exc, GatewayError):
        code = exc.code
    elif isinstance(exc, ValueError):
        code = "invalid_request"
    elif isinstance(exc, TimeoutError):
        code = "provider_timeout"
    else:
        code = "internal"
    return {"code": code, "message": str(exc)}
