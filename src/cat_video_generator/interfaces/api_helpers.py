"""写操作端点共享的任务登记与序列化辅助。

独立于具体Router，避免api_write与api_studio之间产生循环依赖。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from .jobs import JobConflictError, JobRecord, JobRegistry


def _submit(
    registry: JobRegistry,
    *,
    kind: str,
    dedup_key: str,
    fn: Callable[[], Any],
) -> JobRecord:
    """登记后台任务并把去重冲突映射为409。"""

    try:
        return registry.submit(kind=kind, dedup_key=dedup_key, fn=fn)
    except JobConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "jobId": exc.job_id},
        ) from exc


def _accepted(record: JobRecord) -> dict[str, Any]:
    return {
        "jobId": record.job_id,
        "kind": record.kind,
        "dedupKey": record.dedup_key,
        "status": record.status,
    }


def _jsonable(result: Any) -> Any:
    """把Service返回值规整为可JSON序列化结构。"""

    if result is None or isinstance(result, (str, int, float, bool)):
        return result
    if isinstance(result, uuid.UUID):
        return str(result)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if hasattr(result, "_asdict"):
        return _jsonable(result._asdict())
    if isinstance(result, dict):
        return {str(key): _jsonable(value) for key, value in result.items()}
    if isinstance(result, (list, tuple)):
        return [_jsonable(item) for item in result]
    return str(result)
