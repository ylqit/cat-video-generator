"""Minimal Volcengine Ark transport for Creator snapshots.

The gateway accepts the model frozen in each immutable snapshot. It owns
Provider serialization and error classification only; project state, task
leases, idempotency and asset adoption remain application responsibilities.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime._exceptions import (
    ArkAPIConnectionError,
    ArkAPIError,
    ArkAPITimeoutError,
)

from ...application.ports import (
    CreativeDirectorResult,
    GatewayError,
    ImageResult,
    VideoTaskResult,
)
from ...config import RuntimeSettings
from ...domain.rendering import AudioPolicy, VideoInputPlan


class ArkGatewayError(GatewayError):
    """Sanitized Provider failure with retry and reconciliation semantics."""


class ArkGateway:
    """The only Ark protocol boundary used by Creator generation tasks."""

    def __init__(self, settings: RuntimeSettings, *, client: Any | None = None) -> None:
        settings.validate_for_ark_access()
        self._settings = settings
        self._client = client or Ark(
            api_key=settings.ark_api_key,
            base_url=settings.ark_base_url,
        )

    def generate_creative_text(
        self,
        *,
        prompt: str,
        output_name: str,
        model: str,
    ) -> CreativeDirectorResult:
        normalized_model = _required_model(model, "故事")
        request_document = {
            "model": normalized_model,
            "instructions": prompt,
            "outputName": output_name,
        }
        try:
            response = self._client.responses.create(
                model=normalized_model,
                instructions=prompt,
                input=f"生成一个{output_name}。",
                text={"format": {"type": "text"}},
                temperature=0.35,
                max_output_tokens=8000,
                thinking={"type": "disabled"},
                store=False,
                timeout=self._settings.ark_director_request_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if response.status != "completed":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "")
            raise ArkGatewayError(
                f"Ark 创作任务状态为 {response.status!r}"
                + (f"，原因={reason}" if reason else ""),
                code=(f"creative_incomplete_{reason}" if reason else "creative_not_completed"),
                retryable=reason == "max_output_tokens",
            )
        response_text = _response_text(response)
        try:
            decoded = json.loads(response_text)
        except json.JSONDecodeError:
            payload: dict[str, Any] | str = response_text
        else:
            payload = decoded if isinstance(decoded, dict) else response_text
        return CreativeDirectorResult(
            payload=payload,
            response_id=response.id,
            model=response.model,
            request_hash=_json_hash(request_document),
        )

    def generate_image(
        self,
        *,
        prompt: str,
        reference_paths: tuple[Path, ...],
        model: str,
    ) -> ImageResult:
        normalized_model = _required_model(model, "图片")
        request: dict[str, Any] = {
            "model": normalized_model,
            "prompt": prompt,
            "response_format": "url",
            "size": "2K",
            "watermark": False,
            "output_format": "png",
            "timeout": self._settings.ark_image_request_timeout_seconds,
        }
        if reference_paths:
            request["image"] = [_asset_data_url(path) for path in reference_paths]
        try:
            response = self._client.images.generate(**request)
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        except (AttributeError, TypeError) as exc:
            raise ArkGatewayError(
                "Seedream 请求参数无法由 Ark SDK 序列化。",
                code="provider_request_serialization_failed",
                retryable=False,
            ) from exc
        if len(response.data or ()) != 1 or not getattr(response.data[0], "url", None):
            raise ArkGatewayError(
                "Seedream 没有返回唯一可下载图片。",
                code="invalid_image_result",
                retryable=False,
            )
        return ImageResult(
            url=response.data[0].url,
            model=getattr(response, "model", normalized_model),
        )

    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: VideoInputPlan,
        input_sources: tuple[Path | str, ...],
        model: str,
    ) -> VideoTaskResult:
        normalized_model = _required_model(model, "视频")
        if len(input_plan.bindings) != len(input_sources):
            raise ArkGatewayError(
                "多模态输入计划与实际素材数量不一致",
                code="invalid_visual_input_count",
                retryable=False,
            )
        local_bytes = sum(
            source.stat().st_size
            for source in input_sources
            if isinstance(source, Path) and source.is_file()
        )
        if local_bytes > 64 * 1024 * 1024:
            raise ArkGatewayError(
                "多模态请求素材总大小超过 64MB",
                code="reference_payload_too_large",
                retryable=False,
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for source, binding in zip(input_sources, input_plan.bindings, strict=True):
            field_name = "image_url" if binding.modality.value == "image" else "video_url"
            if isinstance(source, Path):
                if binding.modality.value != "image":
                    raise ArkGatewayError(
                        "视频参考必须使用 Provider HTTPS URL",
                        code="reference_video_url_required",
                        retryable=False,
                    )
                url = _asset_data_url(source)
            else:
                if binding.modality.value != "video" or not source.startswith("https://"):
                    raise ArkGatewayError(
                        "Provider 视频参考必须是 HTTPS URL",
                        code="invalid_reference_video_url",
                        retryable=False,
                    )
                url = source
            content.append(
                {
                    "type": field_name,
                    field_name: {"url": url},
                    "role": binding.provider_role.value,
                }
            )
        try:
            response = self._client.content_generation.tasks.create(
                model=normalized_model,
                content=content,
                return_last_frame=True,
                generate_audio=input_plan.audio_policy is AudioPolicy.NATIVE_REQUIRED,
                watermark=False,
                resolution=input_plan.resolution,
                ratio="9:16",
                duration=input_plan.duration_seconds,
                timeout=self._settings.ark_video_api_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if not response.id:
            raise ArkGatewayError(
                "Seedance 没有返回 task ID。",
                code="empty_task_id",
                retryable=False,
            )
        return VideoTaskResult(
            task_id=response.id,
            status="queued",
            model=normalized_model,
        )

    def get_video_task(self, task_id: str) -> VideoTaskResult:
        try:
            task = self._client.content_generation.tasks.get(
                task_id=task_id,
                timeout=self._settings.ark_video_api_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=False) from exc
        return _video_task_result(task)

    def cancel_video_task(self, task_id: str) -> VideoTaskResult:
        normalized_task_id = task_id.strip()
        if not normalized_task_id:
            raise ValueError("Ark 视频任务 ID 不能为空")
        try:
            self._client.content_generation.tasks.delete(
                task_id=normalized_task_id,
                timeout=self._settings.ark_video_api_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=False) from exc
        return VideoTaskResult(task_id=normalized_task_id, status="cancelled")


def _required_model(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"冻结输入缺少{label}模型")
    return normalized


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for output in response.output:
        if getattr(output, "type", None) != "message":
            continue
        for part in getattr(output, "content", ()):
            if getattr(part, "type", None) == "output_text":
                parts.append(part.text)
    text = _repair_utf8_mojibake("".join(parts)).strip()
    if not text:
        raise ArkGatewayError(
            "Ark Responses 没有返回文本。",
            code="empty_creative_result",
            retryable=False,
        )
    return text


def _repair_utf8_mojibake(value: str) -> str:
    best = value
    best_cjk = _cjk_count(value)
    for encoding in ("latin-1", "cp1252"):
        try:
            candidate = value.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        candidate_cjk = _cjk_count(candidate)
        if candidate_cjk > best_cjk:
            best = candidate
            best_cjk = candidate_cjk
    return best


def _cjk_count(value: str) -> int:
    return sum(0x3400 <= ord(character) <= 0x9FFF for character in value)


def _asset_data_url(path: Path) -> str:
    _validate_reference_file(path)
    mime_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower())
    if mime_type is None:
        raise ArkGatewayError(
            f"不支持的 Ark 参考素材类型: {path.suffix}",
            code="unsupported_input_asset",
            retryable=False,
        )
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ArkGatewayError(
            f"无法读取 Ark 输入素材: {path}",
            code="input_asset_read_failed",
            retryable=False,
        ) from exc
    return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"


def _validate_reference_file(path: Path) -> None:
    if not path.is_file():
        raise ArkGatewayError(
            f"参考素材不存在: {path.name}",
            code="missing_reference_file",
            retryable=False,
        )
    size = path.stat().st_size
    if size <= 0 or size > 30 * 1024 * 1024:
        raise ArkGatewayError(
            f"参考素材大小不合法: {path.name}",
            code="invalid_reference_file_size",
            retryable=False,
        )


def _provider_error(exc: ArkAPIError, *, submission: bool) -> ArkGatewayError:
    body = getattr(exc, "body", None)
    nested = (
        body.get("error")
        if isinstance(body, dict) and isinstance(body.get("error"), dict)
        else {}
    )
    code = getattr(exc, "code", None) or nested.get("code") or type(exc).__name__
    message = nested.get("message") or getattr(exc, "message", None) or "Ark 请求失败"
    request_id = getattr(exc, "request_id", None)
    if request_id:
        message = f"{message} (requestId={request_id})"
    if isinstance(exc, (ArkAPIConnectionError, ArkAPITimeoutError)):
        return ArkGatewayError(
            message,
            code=str(code),
            retryable=not submission,
            submission_unknown=submission,
            request_id=request_id,
            timed_out=isinstance(exc, ArkAPITimeoutError),
        )
    status_code = getattr(exc, "status_code", None)
    quota_error = any(
        marker in str(code).lower()
        for marker in ("quota", "balance", "insufficient", "account")
    )
    return ArkGatewayError(
        message,
        code=str(code),
        retryable=bool(
            status_code and (status_code >= 500 or status_code == 429) and not quota_error
        ),
        request_id=request_id,
    )


def _video_task_result(task: Any) -> VideoTaskResult:
    content = getattr(task, "content", None)
    error = getattr(task, "error", None)
    created_at = getattr(task, "created_at", None)
    raw_duration = getattr(task, "duration", None)
    try:
        duration_seconds = None if raw_duration is None else int(raw_duration)
    except (TypeError, ValueError):
        duration_seconds = None
    return VideoTaskResult(
        task_id=str(task.id),
        status=str(task.status),
        video_url=None if content is None else getattr(content, "video_url", None),
        last_frame_url=None if content is None else getattr(content, "last_frame_url", None),
        error_code=None if error is None else getattr(error, "code", None),
        error_message=None if error is None else getattr(error, "message", None),
        model=getattr(task, "model", None),
        created_at=(
            datetime.fromtimestamp(created_at, tz=timezone.utc)
            if isinstance(created_at, int | float)
            else None
        ),
        duration_seconds=duration_seconds,
        ratio=getattr(task, "ratio", None),
        resolution=getattr(task, "resolution", None),
        generate_audio=getattr(task, "generate_audio", None),
    )


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
