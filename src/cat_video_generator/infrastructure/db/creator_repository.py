"""Transactional persistence for the Creator-only workflow."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from ...application.ports import LandedAsset
from ...domain.creator_core import CreatorReference
from ...domain.rendering import MediaSource, build_shot_input_plan
from .models import (
    CreatorProject,
    CreatorShot,
    CreatorTimeline,
    GenerationSnapshot,
    GenerationTask,
    GenerationTaskEvent,
    MediaAsset,
)


class RecordNotFoundError(LookupError):
    pass


class WorkflowConflictError(RuntimeError):
    pass


class SqlAlchemyCreatorRepository:
    """Own Creator content, immutable paid inputs, tasks, assets and timelines."""

    def __init__(self, sessions: sessionmaker[Session], *, asset_root: Path) -> None:
        self._sessions = sessions
        self._asset_root = asset_root.expanduser().resolve()

    def list_projects(self) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(
                select(CreatorProject).order_by(
                    CreatorProject.updated_at.desc(), CreatorProject.created_at.desc()
                )
            )
            return [self._project_summary(row) for row in rows]

    def canon_options(self) -> dict[str, Any]:
        with self._sessions() as session:
            assets = list(
                session.scalars(
                    select(MediaAsset)
                    .where(
                        MediaAsset.project_id.is_(None),
                        MediaAsset.status == "approved",
                        MediaAsset.semantic_key.in_(
                            (
                                "person:headshot",
                                "cat:front",
                                "style:healing_line_texture_v4",
                            )
                        ),
                    )
                    .order_by(MediaAsset.created_at)
                )
            )
            roles = {
                "person:headshot": "child_identity",
                "cat:front": "cat_identity",
                "style:healing_line_texture_v4": "style_board",
            }
            references = [
                {
                    "assetId": str(asset.id),
                    "role": roles[str(asset.semantic_key)],
                    "providerEligible": (asset.metadata_json or {}).get("providerEligible", True),
                    "title": self._asset_title(asset),
                    "instruction": "",
                }
                for asset in assets
            ]
            return {
                "ready": {item["role"] for item in references}
                == {"child_identity", "cat_identity", "style_board"},
                "references": references,
            }

    def asset_library(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        with self._sessions() as session:
            state = self._require_project(session, project_id)
            shots = list(
                session.scalars(select(CreatorShot).where(CreatorShot.project_id == project_id))
            )
            referenced_ids = {
                uuid.UUID(str(item["assetId"]))
                for item in [
                    *state.reference_bindings_json,
                    *(item for shot in shots for item in shot.reference_bindings_json),
                ]
                if item.get("assetId")
            }
            referenced_ids.update(
                shot.selected_video_asset_id
                for shot in shots
                if shot.selected_video_asset_id is not None
            )
            assets = list(
                session.scalars(
                    select(MediaAsset)
                    .where(
                        or_(
                            MediaAsset.project_id == project_id,
                            MediaAsset.id.in_(referenced_ids) if referenced_ids else False,
                        )
                    )
                    .order_by(MediaAsset.created_at.desc())
                )
            )
            return [self._asset_dict(asset) for asset in assets]

    def create_project(self, payload: Any) -> dict[str, Any]:
        with self._sessions.begin() as session:
            references = self._validated_reference_payload(session, payload.references)
            project = CreatorProject(
                title=payload.title.strip(),
                content_date=payload.content_date or date.today(),
                version=1,
                brief_body=payload.brief.body,
                story_candidates_json=[],
                current_story_json={},
                target_duration_seconds=payload.brief.duration_seconds,
                aspect_ratio=payload.brief.aspect_ratio,
                quality_tier=payload.brief.quality_tier,
                reference_bindings_json=references,
            )
            session.add(project)
            session.flush()
            return {
                "projectId": str(project.id),
                "title": project.title,
                "version": project.version,
                "providerCallCount": 0,
            }

    def get_state(self, project_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            return self._state_dict(self._require_project(session, project_id))

    def update_state(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._locked_project(session, project_id, expected_version)
            changes = payload.model_dump(exclude_none=True)
            if "reference_bindings" in changes:
                project.reference_bindings_json = self._validated_reference_payload(
                    session, payload.reference_bindings
                )
            for request_name, column_name in (
                ("brief_body", "brief_body"),
                ("target_duration_seconds", "target_duration_seconds"),
                ("aspect_ratio", "aspect_ratio"),
                ("quality_tier", "quality_tier"),
            ):
                if request_name in changes:
                    setattr(project, column_name, changes[request_name])
            project.version += 1
            return self._state_dict(project)

    def save_story(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._locked_project(session, project_id, expected_version)
            project.current_story_json = payload.story.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
            project.version += 1
            return self._state_dict(project)

    def list_shots(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            rows = session.scalars(
                select(CreatorShot)
                .where(CreatorShot.project_id == project_id)
                .order_by(CreatorShot.sort_order)
            )
            return [self._shot_dict(row) for row in rows]

    def replace_shots(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._locked_project(session, project_id, expected_version)
            existing = {
                row.id: row
                for row in session.scalars(
                    select(CreatorShot)
                    .where(CreatorShot.project_id == project_id)
                    .with_for_update()
                )
            }
            retained: set[uuid.UUID] = set()
            result: list[CreatorShot] = []
            for order, draft in enumerate(payload.shots, start=1):
                row = existing.get(draft.id) if draft.id else None
                if draft.id is not None and row is None:
                    raise WorkflowConflictError("镜头不存在或不属于当前项目")
                if row is None:
                    row = CreatorShot(id=uuid.uuid4(), project_id=project_id, version=1)
                    session.add(row)
                else:
                    if draft.version is not None and row.version != draft.version:
                        raise WorkflowConflictError(
                            f"镜头版本冲突：expected {draft.version}, current {row.version}"
                        )
                    retained.add(row.id)
                    row.version += 1
                row.sort_order = order
                row.title = draft.title
                row.direction = draft.direction
                row.duration_seconds = draft.duration_seconds
                row.scene_label = draft.scene_label
                row.reference_bindings_json = [
                    item.model_dump(by_alias=True, mode="json") for item in draft.reference_bindings
                ]
                row.prompt_draft = draft.prompt_draft
                result.append(row)

            for row_id, row in existing.items():
                if row_id in retained:
                    continue
                if row.selected_video_asset_id is not None or self._shot_has_execution(
                    session, row.id
                ):
                    raise WorkflowConflictError("已有生成记录的镜头不能直接删除")
                session.delete(row)
            session.flush()
            project.version += 1
            return {
                "projectVersion": project.version,
                "shots": [self._shot_dict(row) for row in result],
            }

    def update_shot(
        self, shot_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            shot = session.scalar(
                select(CreatorShot).where(CreatorShot.id == shot_id).with_for_update()
            )
            if shot is None:
                raise RecordNotFoundError(f"creator shot {shot_id} not found")
            if shot.version != expected_version:
                raise WorkflowConflictError(
                    f"镜头版本冲突：expected {expected_version}, current {shot.version}"
                )
            changes = payload.model_dump(exclude_none=True)
            for field in (
                "title",
                "direction",
                "duration_seconds",
                "scene_label",
                "prompt_draft",
            ):
                if field in changes:
                    setattr(shot, field, changes[field])
            if "reference_bindings" in changes:
                shot.reference_bindings_json = [
                    item.model_dump(by_alias=True, mode="json")
                    for item in payload.reference_bindings
                ]
            shot.version += 1
            return self._shot_dict(shot)

    def project_id_for_shot(self, shot_id: uuid.UUID) -> uuid.UUID:
        with self._sessions() as session:
            project_id = session.scalar(
                select(CreatorShot.project_id).where(CreatorShot.id == shot_id)
            )
            if project_id is None:
                raise RecordNotFoundError(f"creator shot {shot_id} not found")
            return project_id

    def create_snapshot(
        self,
        *,
        project_id: uuid.UUID,
        creator_shot_id: uuid.UUID | None,
        kind: str,
        prompt_text: str,
        ordered_references: list[dict[str, Any]],
        provider_config: dict[str, Any],
        input_hash: str,
        estimated_cost_micros: int | None,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id)
            shot = None
            if creator_shot_id is not None:
                shot = session.get(CreatorShot, creator_shot_id)
                if shot is None or shot.project_id != project_id:
                    raise RecordNotFoundError(f"creator shot {creator_shot_id} not found")
            self._validate_snapshot_references(
                session, project, shot, ordered_references, kind=kind
            )
            snapshot = GenerationSnapshot(
                project_id=project_id,
                creator_shot_id=creator_shot_id,
                kind=kind,
                prompt_text=prompt_text,
                ordered_references_json=ordered_references,
                provider_config_json=provider_config,
                input_hash=input_hash,
                estimated_cost_micros=estimated_cost_micros,
            )
            session.add(snapshot)
            session.flush()
            return self._snapshot_dict(snapshot)

    def submit_snapshot(
        self,
        snapshot_id: uuid.UUID,
        *,
        idempotency_key: str,
        input_hash: str,
        accepted_estimated_cost_micros: int,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            snapshot = session.scalar(
                select(GenerationSnapshot)
                .where(GenerationSnapshot.id == snapshot_id)
                .with_for_update()
            )
            if snapshot is None:
                raise RecordNotFoundError(f"generation snapshot {snapshot_id} not found")
            existing = session.scalar(
                select(GenerationTask).where(
                    or_(
                        GenerationTask.generation_snapshot_id == snapshot_id,
                        GenerationTask.idempotency_key == idempotency_key,
                    )
                )
            )
            if existing is not None:
                if existing.generation_snapshot_id != snapshot_id:
                    raise WorkflowConflictError("幂等键已用于另一份生成输入")
                return self._task_dict(existing)
            if snapshot.confirmed_at is not None:
                raise WorkflowConflictError("生成快照已确认但任务缺失，必须先对账")
            if snapshot.input_hash != input_hash:
                raise WorkflowConflictError("确认时的输入哈希与冻结快照不一致")
            expected_cost = snapshot.estimated_cost_micros or 0
            if accepted_estimated_cost_micros != expected_cost:
                raise WorkflowConflictError(
                    f"确认费用不一致：expected {expected_cost}, accepted "
                    f"{accepted_estimated_cost_micros}"
                )
            config = snapshot.provider_config_json
            task = GenerationTask(
                project_id=snapshot.project_id,
                creator_shot_id=snapshot.creator_shot_id,
                generation_snapshot_id=snapshot.id,
                status="local_queued",
                attempt=1,
                idempotency_key=idempotency_key,
                provider=str(config.get("provider") or "ark"),
                model=str(config.get("model") or ""),
                input_hash=snapshot.input_hash,
                input_snapshot_json=self._snapshot_dict(snapshot),
                provider_status="not_submitted",
            )
            snapshot.confirmed_at = datetime.now(UTC)
            session.add(task)
            session.flush()
            self._record_event(session, task, "task_created", {"providerCallCount": 0})
            return self._task_dict(task)

    def generation_work(self, task_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            task = session.get(GenerationTask, task_id)
            if task is None:
                raise RecordNotFoundError(f"creator task {task_id} not found")
            snapshot = session.get(GenerationSnapshot, task.generation_snapshot_id)
            if snapshot is None or snapshot.confirmed_at is None:
                raise WorkflowConflictError("Creator 任务缺少已确认冻结输入")
            if task.input_hash != snapshot.input_hash:
                raise WorkflowConflictError("Creator 任务输入哈希不一致")
            assets: list[MediaAsset] = []
            for item in snapshot.ordered_references_json:
                asset = session.get(MediaAsset, uuid.UUID(str(item["assetId"])))
                if asset is None:
                    raise WorkflowConflictError("冻结参考素材不存在")
                if asset.status not in {"approved", "ready"}:
                    raise WorkflowConflictError("冻结参考素材尚未采用")
                if (asset.metadata_json or {}).get("providerEligible") is False:
                    raise WorkflowConflictError("冻结参考素材不允许提交 Provider")
                assets.append(asset)
            work: dict[str, object] = {
                "kind": snapshot.kind,
                "prompt": snapshot.prompt_text,
                "providerConfig": dict(snapshot.provider_config_json),
                "inputSources": tuple(
                    _resolve_creator_asset_path(asset.storage_key, self._asset_root)
                    for asset in assets
                ),
                "providerTaskId": task.provider_task_id,
            }
            if snapshot.kind == "video":
                config = dict(snapshot.provider_config_json)
                mode = str(config.get("mode") or "reference_media")
                sources = tuple(
                    MediaSource(
                        asset_id=asset.id,
                        semantic_key=asset.semantic_key or f"asset:{asset.id}",
                        media_type=asset.media_type,
                        sha256=asset.sha256,
                        metadata=asset.metadata_json,
                    )
                    for asset in assets
                )
                if mode == "first_frame" and len(sources) != 1:
                    raise WorkflowConflictError("首帧模式必须且只能冻结一张开场图")
                if mode == "first_last_frame" and len(sources) != 2:
                    raise WorkflowConflictError("首尾帧模式必须冻结两张控制图")
                work["inputPlan"] = build_shot_input_plan(
                    resolution=str(config.get("resolution") or "720p").lower(),
                    duration_seconds=int(config.get("durationSeconds") or 8),
                    anchor=sources[0] if mode in {"first_frame", "first_last_frame"} else None,
                    last_frame=sources[1] if mode == "first_last_frame" else None,
                    references=() if mode in {"first_frame", "first_last_frame"} else sources,
                )
            return work

    def complete_story_candidates(
        self,
        task_id: uuid.UUID,
        *,
        candidates: list[dict[str, object]],
        raw_response: object,
        provider_model: str,
        request_hash: str,
    ) -> None:
        with self._sessions.begin() as session:
            task, snapshot = self._locked_task_snapshot(session, task_id)
            project = self._require_project(session, snapshot.project_id)
            project.story_candidates_json = candidates
            project.version += 1
            task.provider_status = "succeeded"
            task.error_json = None
            self._record_event(
                session,
                task,
                "story_candidates_ready",
                {
                    "candidateCount": len(candidates),
                    "providerModel": provider_model,
                    "requestHash": request_hash,
                    "rawResponseType": type(raw_response).__name__,
                },
            )

    def record_provider_submission(
        self,
        task_id: uuid.UUID,
        *,
        provider_task_id: str,
        provider_status: str,
    ) -> None:
        with self._sessions.begin() as session:
            task = self._locked_task(session, task_id)
            if task.provider_task_id not in {None, provider_task_id}:
                raise WorkflowConflictError("Provider task ID 与已记录提交不一致")
            task.provider_task_id = provider_task_id
            task.submitted_at = task.submitted_at or datetime.now(UTC)
            task.provider_status = provider_status
            task.status = "provider_running" if provider_status == "running" else "provider_queued"
            self._record_event(
                session,
                task,
                "provider_submitted",
                {"providerTaskId": provider_task_id, "providerStatus": provider_status},
            )

    def complete_media_asset(
        self,
        task_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
        last_frame_landed: LandedAsset | None,
        last_frame_provider_url: str | None,
    ) -> str:
        with self._sessions.begin() as session:
            task, snapshot = self._locked_task_snapshot(session, task_id)
            media_type = "video" if snapshot.kind in {"video", "video_edit"} else "image"
            role = "video" if media_type == "video" else "generated_reference"
            asset = MediaAsset(
                project_id=snapshot.project_id,
                creator_shot_id=snapshot.creator_shot_id,
                generation_snapshot_id=snapshot.id,
                generation_task_id=task.id,
                role=role,
                semantic_key=f"creator:{snapshot.kind}:{snapshot.id}",
                status="candidate",
                media_type=media_type,
                storage_key=_creator_asset_storage_key(landed.path, self._asset_root),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                metadata_json={
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "inputHash": snapshot.input_hash,
                },
            )
            session.add(asset)
            session.flush()
            if last_frame_landed is not None:
                session.add(
                    MediaAsset(
                        project_id=snapshot.project_id,
                        creator_shot_id=snapshot.creator_shot_id,
                        generation_snapshot_id=snapshot.id,
                        generation_task_id=task.id,
                        role="shot_tail_frame",
                        semantic_key=f"creator:tail:{asset.id}",
                        status="ready",
                        media_type="image",
                        storage_key=_creator_asset_storage_key(
                            last_frame_landed.path, self._asset_root
                        ),
                        sha256=last_frame_landed.sha256,
                        byte_size=last_frame_landed.byte_size,
                        metadata_json={
                            "sourceVideoAssetId": str(asset.id),
                            "providerUrl": last_frame_provider_url,
                            "providerModel": provider_model,
                        },
                    )
                )
            task.provider_status = "succeeded"
            task.error_json = None
            self._record_event(
                session, task, "media_ready", {"assetId": str(asset.id), "role": role}
            )
            return str(asset.id)

    def decide_asset(self, asset_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        with self._sessions.begin() as session:
            asset = session.scalar(
                select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update()
            )
            if asset is None:
                raise RecordNotFoundError(f"asset {asset_id} not found")
            asset.status = "approved" if payload.decision == "adopt" else "rejected"
            asset.decided_at = datetime.now(UTC)
            asset.decision_reason = payload.reason
            return {
                "assetId": str(asset.id),
                "decision": payload.decision,
                "status": asset.status,
            }

    def select_video(
        self, shot_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            shot = session.scalar(
                select(CreatorShot).where(CreatorShot.id == shot_id).with_for_update()
            )
            if shot is None:
                raise RecordNotFoundError(f"creator shot {shot_id} not found")
            if shot.version != expected_version:
                raise WorkflowConflictError(
                    f"镜头版本冲突：expected {expected_version}, current {shot.version}"
                )
            asset = session.get(MediaAsset, payload.asset_id)
            if (
                asset is None
                or asset.project_id != shot.project_id
                or asset.creator_shot_id != shot.id
                or asset.media_type != "video"
                or asset.status != "approved"
            ):
                raise WorkflowConflictError("只能选择当前镜头已采用的视频候选")
            shot.selected_video_asset_id = asset.id
            shot.version += 1
            return self._shot_dict(shot)

    def diagnostics(self, project_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            project = self._require_project(session, project_id)
            shots = list(
                session.scalars(
                    select(CreatorShot)
                    .where(CreatorShot.project_id == project_id)
                    .order_by(CreatorShot.sort_order)
                )
            )
            tasks = list(
                session.scalars(
                    select(GenerationTask)
                    .where(GenerationTask.project_id == project_id)
                    .order_by(GenerationTask.created_at.desc())
                )
            )
            items: list[dict[str, Any]] = []
            if not project.current_story_json.get("body"):
                items.append(
                    {
                        "code": "story_missing",
                        "severity": "blocker",
                        "message": "请先保存当前故事",
                    }
                )
            if not shots:
                items.append(
                    {
                        "code": "shots_missing",
                        "severity": "blocker",
                        "message": "至少需要一个镜头",
                    }
                )
            duration = sum(shot.duration_seconds for shot in shots)
            if shots and duration != project.target_duration_seconds:
                items.append(
                    {
                        "code": "duration_mismatch",
                        "severity": "warning",
                        "message": (
                            f"镜头总时长 {duration}s 与目标 "
                            f"{project.target_duration_seconds}s 不一致"
                        ),
                    }
                )
            return {
                "projectId": str(project_id),
                "items": items,
                "tasks": [self._task_dict(task) for task in tasks],
            }

    def list_tasks(self, project_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        with self._sessions() as session:
            query = select(GenerationTask).order_by(GenerationTask.created_at.desc())
            if project_id is not None:
                query = query.where(GenerationTask.project_id == project_id)
            return [self._task_dict(row) for row in session.scalars(query)]

    def asset_path(self, asset_id: uuid.UUID) -> Path:
        with self._sessions() as session:
            asset = session.get(MediaAsset, asset_id)
            if asset is None:
                raise RecordNotFoundError(f"asset {asset_id} not found")
            return _resolve_creator_asset_path(asset.storage_key, self._asset_root)

    def timeline(self, project_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            row = session.scalar(
                select(CreatorTimeline).where(CreatorTimeline.project_id == project_id)
            )
            return self._timeline_dict(row, project_id)

    def save_timeline(
        self, project_id: uuid.UUID, *, expected_version: int, clips: list[dict[str, Any]]
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            row = session.scalar(
                select(CreatorTimeline)
                .where(CreatorTimeline.project_id == project_id)
                .with_for_update()
            )
            current_version = 0 if row is None else row.version
            if current_version != expected_version:
                raise WorkflowConflictError(
                    f"时间线版本冲突：expected {expected_version}, current {current_version}"
                )
            self._validate_timeline_clips(session, project_id, clips)
            if row is None:
                row = CreatorTimeline(
                    project_id=project_id, version=1, clips_json=clips, status="draft"
                )
                session.add(row)
            else:
                row.version += 1
                row.clips_json = clips
                row.status = "draft"
                row.final_asset_id = None
            session.flush()
            return self._timeline_dict(row, project_id)

    def _validate_timeline_clips(
        self, session: Session, project_id: uuid.UUID, clips: list[dict[str, Any]]
    ) -> None:
        if not clips:
            return
        shot_ids = [uuid.UUID(str(item["creatorShotId"])) for item in clips]
        if len(shot_ids) != len(set(shot_ids)):
            raise WorkflowConflictError("时间线不能重复引用同一个镜头")
        shots = {
            row.id: row
            for row in session.scalars(
                select(CreatorShot).where(
                    CreatorShot.project_id == project_id, CreatorShot.id.in_(shot_ids)
                )
            )
        }
        if set(shot_ids) != set(shots):
            raise WorkflowConflictError("时间线包含不属于当前项目的镜头")
        for item, shot_id in zip(clips, shot_ids, strict=True):
            asset_id = uuid.UUID(str(item["assetId"]))
            shot = shots[shot_id]
            if shot.selected_video_asset_id != asset_id:
                raise WorkflowConflictError("时间线只能使用镜头当前选择的视频")

    def _locked_project(
        self, session: Session, project_id: uuid.UUID, expected_version: int
    ) -> CreatorProject:
        project = session.scalar(
            select(CreatorProject).where(CreatorProject.id == project_id).with_for_update()
        )
        if project is None:
            raise RecordNotFoundError(f"project {project_id} not found")
        if project.version != expected_version:
            raise WorkflowConflictError(
                f"项目版本冲突：expected {expected_version}, current {project.version}"
            )
        return project

    @staticmethod
    def _require_project(session: Session, project_id: uuid.UUID) -> CreatorProject:
        project = session.get(CreatorProject, project_id)
        if project is None:
            raise RecordNotFoundError(f"project {project_id} not found")
        return project

    def _validated_reference_payload(
        self, session: Session, references: list[CreatorReference]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for reference in references:
            asset = session.get(MediaAsset, reference.asset_id)
            if asset is None:
                raise RecordNotFoundError(f"asset {reference.asset_id} not found")
            if asset.status not in {"approved", "ready"}:
                raise WorkflowConflictError(f"参考 {reference.title} 尚未采用")
            metadata = asset.metadata_json or {}
            if reference.provider_eligible and metadata.get("providerEligible") is False:
                raise WorkflowConflictError(f"参考 {reference.title} 不允许提交 Provider")
            result.append(reference.model_dump(by_alias=True, mode="json"))
        return result

    def _validate_snapshot_references(
        self,
        session: Session,
        project: CreatorProject,
        shot: CreatorShot | None,
        ordered_references: list[dict[str, Any]],
        *,
        kind: str,
    ) -> None:
        if kind == "story_text":
            if ordered_references:
                raise WorkflowConflictError("故事文本生成不接收媒体参考")
            return
        current = {
            str(item["assetId"]): item
            for item in [
                *project.reference_bindings_json,
                *(shot.reference_bindings_json if shot is not None else []),
            ]
        }
        for item in ordered_references:
            if item.get("role") == "style_source":
                raise WorkflowConflictError("style_source 不能进入日常 Provider 输入")
            asset_id = str(item.get("assetId") or "")
            if asset_id not in current:
                raise WorkflowConflictError("冻结输入包含未绑定的参考素材")
            asset = session.get(MediaAsset, uuid.UUID(asset_id))
            if asset is None or asset.status not in {"approved", "ready"}:
                raise WorkflowConflictError("冻结参考素材不存在或尚未采用")
            if (asset.metadata_json or {}).get("providerEligible") is False:
                raise WorkflowConflictError("冻结参考素材不允许提交 Provider")

    @staticmethod
    def _shot_has_execution(session: Session, shot_id: uuid.UUID) -> bool:
        return bool(
            session.scalar(
                select(func.count())
                .select_from(GenerationSnapshot)
                .where(GenerationSnapshot.creator_shot_id == shot_id)
            )
        )

    @staticmethod
    def _locked_task(session: Session, task_id: uuid.UUID) -> GenerationTask:
        task = session.scalar(
            select(GenerationTask).where(GenerationTask.id == task_id).with_for_update()
        )
        if task is None:
            raise RecordNotFoundError(f"creator task {task_id} not found")
        return task

    def _locked_task_snapshot(
        self, session: Session, task_id: uuid.UUID
    ) -> tuple[GenerationTask, GenerationSnapshot]:
        task = self._locked_task(session, task_id)
        snapshot = session.get(GenerationSnapshot, task.generation_snapshot_id)
        if snapshot is None:
            raise WorkflowConflictError("Creator 任务缺少冻结输入")
        return task, snapshot

    @staticmethod
    def _record_event(
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
    def _project_summary(project: CreatorProject) -> dict[str, Any]:
        story = project.current_story_json or {}
        return {
            "id": str(project.id),
            "title": project.title,
            "contentDate": project.content_date.isoformat(),
            "status": "active",
            "storyTitle": story.get("title"),
            "updatedAt": project.updated_at.isoformat(),
        }

    @staticmethod
    def _state_dict(project: CreatorProject) -> dict[str, Any]:
        return {
            "projectId": str(project.id),
            "projectTitle": project.title,
            "contentDate": project.content_date.isoformat(),
            "version": project.version,
            "briefBody": project.brief_body,
            "storyCandidates": project.story_candidates_json,
            "currentStory": project.current_story_json,
            "targetDurationSeconds": project.target_duration_seconds,
            "aspectRatio": project.aspect_ratio,
            "qualityTier": project.quality_tier,
            "referenceBindings": project.reference_bindings_json,
            "updatedAt": project.updated_at.isoformat(),
        }

    @staticmethod
    def _shot_dict(shot: CreatorShot) -> dict[str, Any]:
        return {
            "id": str(shot.id),
            "projectId": str(shot.project_id),
            "sortOrder": shot.sort_order,
            "version": shot.version,
            "title": shot.title,
            "direction": shot.direction,
            "durationSeconds": shot.duration_seconds,
            "sceneLabel": shot.scene_label,
            "referenceBindings": shot.reference_bindings_json,
            "promptDraft": shot.prompt_draft,
            "selectedVideoAssetId": (
                str(shot.selected_video_asset_id) if shot.selected_video_asset_id else None
            ),
        }

    @staticmethod
    def _snapshot_dict(snapshot: GenerationSnapshot) -> dict[str, Any]:
        return {
            "id": str(snapshot.id),
            "projectId": str(snapshot.project_id),
            "creatorShotId": (str(snapshot.creator_shot_id) if snapshot.creator_shot_id else None),
            "kind": snapshot.kind,
            "promptText": snapshot.prompt_text,
            "orderedReferences": snapshot.ordered_references_json,
            "providerConfig": snapshot.provider_config_json,
            "inputHash": snapshot.input_hash,
            "estimatedCostMicros": snapshot.estimated_cost_micros,
            "confirmedAt": (snapshot.confirmed_at.isoformat() if snapshot.confirmed_at else None),
            "createdAt": snapshot.created_at.isoformat(),
        }

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
            "submittedAt": task.submitted_at.isoformat() if task.submitted_at else None,
            "createdAt": task.created_at.isoformat(),
        }

    def _asset_dict(self, asset: MediaAsset) -> dict[str, Any]:
        return {
            "id": str(asset.id),
            "projectId": str(asset.project_id) if asset.project_id else None,
            "creatorShotId": str(asset.creator_shot_id) if asset.creator_shot_id else None,
            "generationSnapshotId": (
                str(asset.generation_snapshot_id) if asset.generation_snapshot_id else None
            ),
            "mediaType": asset.media_type,
            "role": asset.role,
            "status": asset.status,
            "semanticKey": asset.semantic_key,
            "sha256": asset.sha256,
            "byteSize": asset.byte_size,
            "title": self._asset_title(asset),
            "metadata": asset.metadata_json,
            "contentUrl": f"/api/v2/media-assets/{asset.id}/content",
            "createdAt": asset.created_at.isoformat(),
        }

    @staticmethod
    def _asset_title(asset: MediaAsset) -> str:
        value = (asset.metadata_json or {}).get("displayName")
        return str(value) if value else str(asset.semantic_key or asset.role)

    @staticmethod
    def _timeline_dict(timeline: CreatorTimeline | None, project_id: uuid.UUID) -> dict[str, Any]:
        if timeline is None:
            return {
                "id": None,
                "projectId": str(project_id),
                "version": 0,
                "clips": [],
                "status": "draft",
                "finalAssetId": None,
            }
        return {
            "id": str(timeline.id),
            "projectId": str(timeline.project_id),
            "version": timeline.version,
            "clips": timeline.clips_json,
            "status": timeline.status,
            "finalAssetId": (str(timeline.final_asset_id) if timeline.final_asset_id else None),
        }


def _creator_asset_storage_key(path: Path, asset_root: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        key = resolved.relative_to(asset_root).as_posix()
    except ValueError as exc:
        raise ValueError("managed asset path is outside the configured asset root") from exc
    _resolve_creator_asset_path(key, asset_root)
    return key


def _resolve_creator_asset_path(storage_key: str, asset_root: Path) -> Path:
    pure = PurePosixPath(storage_key.strip())
    if not storage_key.strip() or pure.is_absolute() or ".." in pure.parts:
        raise ValueError("asset storage key is invalid")
    resolved = asset_root.joinpath(*pure.parts).resolve()
    try:
        resolved.relative_to(asset_root)
    except ValueError as exc:
        raise ValueError("asset storage key escapes the configured root") from exc
    if not resolved.is_file():
        raise ValueError(f"asset content is unavailable: {storage_key}")
    return resolved
