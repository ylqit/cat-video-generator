"""火山Ark导演、Seedream和Seedance网关。

本模块只负责协议映射、错误分类和供应商返回值，不修改Run或Episode状态。
调用意图、幂等和恢复由Application Service与Repository共同负责。
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime._exceptions import (
    ArkAPIConnectionError,
    ArkAPIError,
    ArkAPITimeoutError,
)

from ...application.ports import (
    DirectorResult,
    GatewayError,
    ImageResult,
    VideoDiagnosticResult,
    VideoTaskResult,
    VisualReviewResult,
)
from ...config import RuntimeSettings
from ...domain.rendering import MediaModality, VideoInputPlan
from .review_schemas import KEYFRAME_REVIEW_SCHEMA, VIDEO_DIAGNOSTIC_SCHEMA


class ArkGatewayError(GatewayError):
    """已脱敏且可用于恢复决策的Ark错误。"""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        submission_unknown: bool = False,
    ) -> None:
        super().__init__(
            message,
            code=code,
            retryable=retryable,
            submission_unknown=submission_unknown,
        )


class ArkGateway:
    """当前系统唯一的Ark运行时边界。"""

    def __init__(
        self,
        settings: RuntimeSettings,
        *,
        client: Any | None = None,
    ) -> None:
        settings.validate_for_ark_access()
        self._settings = settings
        self._client = client or Ark(
            api_key=settings.ark_api_key,
            base_url=settings.ark_base_url,
        )

    @property
    def model(self) -> str:
        return self._settings.ark_planning_model

    @property
    def image_model(self) -> str:
        return self._settings.ark_image_model

    @property
    def video_model(self) -> str:
        return self._settings.ark_video_model

    @property
    def review_model(self) -> str:
        return self._settings.ark_review_model

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
    ) -> DirectorResult:
        instructions, text_format = self._structured_output(
            prompt,
            schema,
            output_name,
        )
        request_hash = _json_hash(
            {
                "model": self.model,
                "instructions": instructions,
                "schema": schema,
                "outputName": output_name,
            }
        )
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=instructions,
                input=f"生成一个{output_name}对象。",
                text={"format": text_format},
                temperature=0.35,
                max_output_tokens=8000,
                # 导演结果必须是短小、可校验的JSON。关闭隐藏思考，避免推理内容
                # 消耗输出预算后只返回incomplete，创意约束仍由分层Prompt承担。
                thinking={"type": "disabled"},
                store=False,
                timeout=180.0,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if response.status != "completed":
            incomplete_reason = getattr(
                getattr(response, "incomplete_details", None),
                "reason",
                "",
            )
            suffix = f"，原因={incomplete_reason}" if incomplete_reason else ""
            raise ArkGatewayError(
                f"Ark导演任务状态为{response.status!r}{suffix}",
                code=(
                    f"director_incomplete_{incomplete_reason}"
                    if response.status == "incomplete" and incomplete_reason
                    else "director_not_completed"
                ),
                retryable=incomplete_reason == "max_output_tokens",
            )
        try:
            payload = json.loads(_response_text(response))
        except json.JSONDecodeError as exc:
            raise ArkGatewayError(
                "Ark导演没有返回合法JSON对象。",
                code="invalid_director_output",
                retryable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise ArkGatewayError(
                "Ark导演返回的JSON顶层必须是对象。",
                code="invalid_director_output",
                retryable=False,
            )
        return DirectorResult(
            payload=payload,
            response_id=response.id,
            model=response.model,
            request_hash=request_hash,
        )

    def generate_image(
        self,
        *,
        prompt: str,
        reference_paths: tuple[Path, ...],
    ) -> ImageResult:
        try:
            response = self._client.images.generate(
                model=self.image_model,
                prompt=prompt,
                image=[_asset_data_url(path) for path in reference_paths],
                response_format="url",
                size="2K",
                watermark=False,
                output_format="png",
                sequential_image_generation="disabled",
                timeout=120.0,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if not response.data or not response.data[0].url:
            raise ArkGatewayError(
                "Seedream没有返回可下载图片。",
                code="empty_image_result",
                retryable=False,
            )
        return ImageResult(
            url=response.data[0].url,
            model=getattr(response, "model", self.image_model),
        )

    def review_keyframe(
        self,
        *,
        prompt: str,
        image_path: Path,
    ) -> VisualReviewResult:
        """使用独立视觉模型审核关键帧，不复用导演输出语义。"""

        schema = KEYFRAME_REVIEW_SCHEMA
        instructions, text_format = self._structured_output(
            prompt,
            schema,
            "KeyframeSemanticReview",
        )
        image_sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest()
        request_hash = _json_hash(
            {
                "model": self.review_model,
                "instructions": instructions,
                "schema": schema,
                "imageSha256": image_sha256,
            }
        )
        try:
            response = self._client.responses.create(
                model=self.review_model,
                instructions=instructions,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "审核这张关键帧并只返回结构化结果。",
                            },
                            {
                                "type": "input_image",
                                "image_url": _asset_data_url(image_path),
                            },
                        ],
                    }
                ],
                text={"format": text_format},
                temperature=0,
                max_output_tokens=1800,
                thinking={"type": "disabled"},
                store=False,
                timeout=180.0,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if response.status != "completed":
            raise ArkGatewayError(
                f"Ark关键帧审核状态为{response.status!r}",
                code="visual_review_not_completed",
                retryable=False,
            )
        try:
            payload = json.loads(_response_text(response))
            return VisualReviewResult(
                identity_ok=bool(payload["identityOk"]),
                style_ok=bool(payload["styleOk"]),
                world_state_ok=bool(payload["worldStateOk"]),
                scene_topology_ok=bool(payload["sceneTopologyOk"]),
                confidence=float(payload["confidence"]),
                violations=tuple(str(item) for item in payload["violations"]),
                evidence=tuple(str(item) for item in payload["evidence"]),
                response_id=response.id,
                model=response.model,
                request_hash=request_hash,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ArkGatewayError(
                "Ark关键帧审核没有返回合法结构。",
                code="invalid_visual_review_output",
                retryable=False,
            ) from exc

    def diagnose_video_frames(
        self,
        *,
        prompt: str,
        frame_paths: tuple[Path, ...],
    ) -> VideoDiagnosticResult:
        """按时间顺序审核抽帧序列；诊断结果不直接批准最终视频。"""

        if not 4 <= len(frame_paths) <= 12:
            raise ArkGatewayError(
                "视频语义诊断需要4至12张有序抽帧",
                code="invalid_video_review_frame_count",
                retryable=False,
            )
        schema = VIDEO_DIAGNOSTIC_SCHEMA
        instructions, text_format = self._structured_output(
            prompt,
            schema,
            "VideoSemanticDiagnostic",
        )
        frame_hashes = [
            hashlib.sha256(path.read_bytes()).hexdigest() for path in frame_paths
        ]
        request_hash = _json_hash(
            {
                "model": self.review_model,
                "instructions": instructions,
                "schema": schema,
                "orderedFrameSha256": frame_hashes,
            }
        )
        content: list[dict[str, str]] = [
            {
                "type": "input_text",
                "text": "以下图片按视频时间顺序排列，请只返回结构化诊断。",
            }
        ]
        content.extend(
            {
                "type": "input_image",
                "image_url": _asset_data_url(path),
            }
            for path in frame_paths
        )
        try:
            response = self._client.responses.create(
                model=self.review_model,
                instructions=instructions,
                input=[{"role": "user", "content": content}],
                text={"format": text_format},
                temperature=0,
                max_output_tokens=2400,
                thinking={"type": "disabled"},
                store=False,
                timeout=240.0,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if response.status != "completed":
            raise ArkGatewayError(
                f"Ark视频语义诊断状态为{response.status!r}",
                code="video_diagnostic_not_completed",
                retryable=False,
            )
        try:
            payload = json.loads(_response_text(response))
            return VideoDiagnosticResult(
                identity_ok=bool(payload["identityOk"]),
                style_ok=bool(payload["styleOk"]),
                world_continuity_ok=bool(payload["worldContinuityOk"]),
                narrative_order_ok=bool(payload["narrativeOrderOk"]),
                confidence=float(payload["confidence"]),
                violations=tuple(str(item) for item in payload["violations"]),
                evidence=tuple(str(item) for item in payload["evidence"]),
                response_id=response.id,
                model=response.model,
                request_hash=request_hash,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ArkGatewayError(
                "Ark视频语义诊断没有返回合法结构。",
                code="invalid_video_diagnostic_output",
                retryable=False,
            ) from exc

    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: VideoInputPlan,
        input_paths: tuple[Path, ...],
    ) -> VideoTaskResult:
        if len(input_plan.bindings) != len(input_paths):
            raise ArkGatewayError(
                "多模态输入计划与本地文件数量不一致",
                code="invalid_visual_input_count",
                retryable=False,
            )
        if (
            sum(path.stat().st_size for path in input_paths if path.is_file())
            > 64 * 1024 * 1024
        ):
            raise ArkGatewayError(
                "多模态请求素材总大小超过64MB",
                code="reference_payload_too_large",
                retryable=False,
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path, binding in zip(
            input_paths,
            input_plan.bindings,
            strict=True,
        ):
            _validate_reference_file(path)
            field_name = {
                MediaModality.IMAGE: "image_url",
                MediaModality.VIDEO: "video_url",
                MediaModality.AUDIO: "audio_url",
            }[binding.modality]
            content.append(
                {
                    "type": field_name,
                    field_name: {"url": _asset_data_url(path)},
                    "role": binding.provider_role.value,
                }
            )
        try:
            response = self._client.content_generation.tasks.create(
                model=self.video_model,
                content=content,
                return_last_frame=False,
                generate_audio=True,
                watermark=False,
                resolution=input_plan.resolution,
                ratio="9:16",
                duration=input_plan.duration_seconds,
                timeout=120.0,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if not response.id:
            raise ArkGatewayError(
                "Seedance没有返回task ID。",
                code="empty_task_id",
                retryable=False,
            )
        return VideoTaskResult(task_id=response.id, status="queued")

    def get_video_task(self, task_id: str) -> VideoTaskResult:
        try:
            task = self._client.content_generation.tasks.get(
                task_id=task_id,
                timeout=120.0,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=False) from exc
        content = getattr(task, "content", None)
        error = getattr(task, "error", None)
        return VideoTaskResult(
            task_id=task.id,
            status=task.status,
            video_url=(
                None if content is None else getattr(content, "video_url", None)
            ),
            error_code=None if error is None else getattr(error, "code", None),
            error_message=(None if error is None else getattr(error, "message", None)),
        )

    def _structured_output(
        self,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
    ) -> tuple[str, dict[str, Any]]:
        if self._settings.ark_structured_output_mode == "json_schema":
            return prompt, {
                "type": "json_schema",
                "json_schema": {
                    "name": output_name,
                    "description": "三时段视频系统的结构化导演对象",
                    "schema": schema,
                    "strict": True,
                },
            }
        if self._settings.ark_structured_output_mode == "json_object_schema_prompt":
            return (
                "\n".join(
                    (
                        prompt,
                        "只允许输出下列JSON Schema定义的字段和类型：",
                        json.dumps(
                            schema,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                ),
                {"type": "json_object"},
            )
        raise ArkGatewayError(
            "不支持的Ark结构化输出模式。",
            code="invalid_structured_output_mode",
            retryable=False,
        )


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for output in response.output:
        if getattr(output, "type", None) != "message":
            continue
        for part in getattr(output, "content", ()):
            if getattr(part, "type", None) == "output_text":
                parts.append(part.text)
    if not parts:
        raise ArkGatewayError(
            "Ark Responses没有返回JSON文本。",
            code="empty_director_result",
            retryable=False,
        )
    return "".join(parts)


def _asset_data_url(path: Path) -> str:
    mime_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
    }.get(path.suffix.lower())
    if mime_type is None:
        raise ArkGatewayError(
            f"不支持的Ark参考素材类型: {path.suffix}",
            code="unsupported_input_asset",
            retryable=False,
        )
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ArkGatewayError(
            f"无法读取Ark输入素材: {path}",
            code="input_asset_read_failed",
            retryable=False,
        ) from exc
    return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"


def _validate_reference_file(path: Path) -> None:
    """在Base64编码前阻断缺失、空文件和超过官方单素材大小限制的输入。"""

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
    message = nested.get("message") or getattr(exc, "message", None) or "Ark请求失败"
    if isinstance(exc, (ArkAPIConnectionError, ArkAPITimeoutError)):
        # 提交阶段断线时无法判断供应商是否已创建收费任务，必须冻结对账；
        # 查询阶段则可以安全重试同一个task ID。
        return ArkGatewayError(
            message,
            code=code,
            retryable=not submission,
            submission_unknown=submission,
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
            status_code
            and (status_code >= 500 or status_code == 429)
            and not quota_error
        ),
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
