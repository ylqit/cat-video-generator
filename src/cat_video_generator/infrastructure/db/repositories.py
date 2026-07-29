"""SQLAlchemy工作流Repository。

本模块拥有并发插入、短事务、幂等键和查询形状；它不判断创意规则、不编译
Prompt，也不调用Ark或媒体工具。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert

from ...application.ports import (
    LandedAsset,
    StoredAsset,
    StoredEpisode,
    StoredRun,
    StoredStep,
)
from ...domain.contracts import DayBrief, DailyProductionPlan, EpisodePlan, Slot
from ...domain.workflow import (
    EpisodeStatus,
    RunStatus,
    StepKind,
    StepStatus,
    transition_episode,
    transition_run,
    transition_step,
)
from .models import (
    Asset,
    DeliveryItem,
    DeliveryPackage,
    Episode,
    ProductionRun,
    PromptRecord,
    Review,
    WorkflowStep,
)
from .records import (
    stored_asset,
    stored_episode,
    stored_step,
)
from .query_repository import (
    RecordNotFoundError,
    SqlAlchemyReadRepository,
    required_record as _required,
)


class SqlAlchemyWorkflowRepository(SqlAlchemyReadRepository):
    """远程PostgreSQL中的工作流唯一写入实现。"""

    def create_draft_run(self, content_date: date) -> uuid.UUID:
        with self._sessions.begin() as session:
            row = ProductionRun(
                content_date=content_date,
                status=RunStatus.DRAFT.value,
            )
            session.add(row)
            session.flush()
            return row.id

    def create_step_intent(
        self,
        *,
        run_id: uuid.UUID,
        episode_id: uuid.UUID | None,
        parent_step_id: uuid.UUID | None,
        kind: StepKind,
        attempt: int,
        provider: str | None,
        model: str | None,
        input_hash: str,
        request_summary: dict[str, Any],
    ) -> StoredStep:
        # 幂等键包含业务所有者、收费步骤和规范化输入。并发Worker即使同时
        # 领取同一任务，也只有一个INSERT能成功，避免重复产生Ark费用。
        raw_key = "|".join(
            (
                str(run_id),
                str(episode_id or ""),
                kind.value,
                str(attempt),
                input_hash,
            )
        )
        key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        values = {
            "id": uuid.uuid4(),
            "production_run_id": run_id,
            "episode_id": episode_id,
            "parent_step_id": parent_step_id,
            "kind": kind.value,
            "status": StepStatus.PENDING.value,
            "attempt": attempt,
            "idempotency_key": key,
            "provider": provider,
            "model": model,
            "input_hash": input_hash,
            "request_summary_json": request_summary,
        }
        with self._sessions.begin() as session:
            statement = (
                insert(WorkflowStep)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(WorkflowStep.id)
            )
            created_id = session.execute(statement).scalar_one_or_none()
            step_id = (
                created_id
                or session.execute(
                    select(WorkflowStep.id).where(WorkflowStep.idempotency_key == key)
                ).scalar_one()
            )
            row = session.get(WorkflowStep, step_id)
            assert row is not None
            return stored_step(row)

    def save_prompt(
        self,
        *,
        step_id: uuid.UUID,
        parent_prompt_id: uuid.UUID | None,
        purpose: str,
        model: str,
        text: str,
    ) -> uuid.UUID:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._sessions.begin() as session:
            existing = session.execute(
                select(PromptRecord.id).where(
                    PromptRecord.step_id == step_id,
                    PromptRecord.sha256 == digest,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            row = PromptRecord(
                step_id=step_id,
                parent_prompt_id=parent_prompt_id,
                purpose=purpose,
                model=model,
                prompt_text=text,
                sha256=digest,
                char_count=len(text),
                utf8_bytes=len(text.encode("utf-8")),
            )
            session.add(row)
            session.flush()
            return row.id

    def finish_director_step(
        self,
        *,
        step_id: uuid.UUID,
        response_id: str,
        request_hash: str,
        output: dict[str, Any],
    ) -> None:
        self.set_step_status(
            step_id,
            StepStatus.SUCCEEDED,
            request_summary_patch={
                "responseId": response_id,
                "providerRequestHash": request_hash,
                "directorOutput": output,
            },
        )

    def fail_step(
        self,
        step_id: uuid.UUID,
        *,
        code: str,
        message: str,
        submission_unknown: bool = False,
    ) -> None:
        target = (
            StepStatus.SUBMISSION_UNKNOWN if submission_unknown else StepStatus.FAILED
        )
        with self._sessions.begin() as session:
            row = _required(session, WorkflowStep, step_id)
            row.status = transition_step(StepStatus(row.status), target).value
            row.error_json = {"code": code, "message": message}
            row.completed_at = (
                None if submission_unknown else datetime.now(timezone.utc)
            )

    def finalize_plan(
        self,
        *,
        run_id: uuid.UUID,
        plan: DailyProductionPlan,
        selected_candidate: int,
    ) -> None:
        with self._sessions.begin() as session:
            run = _required(session, ProductionRun, run_id)
            run.status = transition_run(
                RunStatus(run.status),
                RunStatus.PLANNED,
            ).value
            run.theme = plan.theme
            run.context_json = {
                **run.context_json,
                "dayContext": plan.day_context,
            }
            run.plan_json = plan.model_dump(mode="json")
            run.selected_candidate = selected_candidate
            existing = session.execute(
                select(Episode.id).where(Episode.production_run_id == run_id)
            ).first()
            if existing is not None:
                return
            session.add_all(
                Episode(
                    production_run_id=run_id,
                    slot=episode.slot.value,
                    sort_order=episode.slot.sort_order,
                    title=episode.title,
                    script_json=episode.model_dump(mode="json"),
                    video_input_mode=episode.video_input_mode.value,
                    status=EpisodeStatus.PLANNED.value,
                )
                for episode in plan.episodes
            )

    def save_planning_context(
        self,
        *,
        run_id: uuid.UUID,
        day_brief: DayBrief,
        day_step_id: uuid.UUID,
        day_prompt_id: uuid.UUID,
        episode_drafts: dict[str, dict[str, Any]],
    ) -> None:
        """保存可恢复的导演上下文，不要求三个Episode已经全部成功。"""

        with self._sessions.begin() as session:
            run = _required(session, ProductionRun, run_id)
            run.theme = day_brief.theme
            run.context_json = {
                **run.context_json,
                "dayBrief": day_brief.model_dump(mode="json"),
                "dayDirectorStepId": str(day_step_id),
                "dayDirectorPromptId": str(day_prompt_id),
                "episodeDrafts": episode_drafts,
            }

    def get_planning_context(self, run_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            return dict(_required(session, ProductionRun, run_id).context_json)

    def next_director_attempt(
        self,
        *,
        run_id: uuid.UUID,
        phase: str,
        slot: Slot | None,
    ) -> int:
        statement = select(func.max(WorkflowStep.attempt)).where(
            WorkflowStep.production_run_id == run_id,
            WorkflowStep.kind == StepKind.DIRECTOR.value,
            WorkflowStep.request_summary_json["phase"].astext == phase,
        )
        if slot is not None:
            statement = statement.where(
                WorkflowStep.request_summary_json["slot"].astext == slot.value
            )
        with self._sessions() as session:
            return int(session.execute(statement).scalar_one_or_none() or 0) + 1

    def replace_episode_plan(
        self,
        *,
        run_id: uuid.UUID,
        episode: EpisodePlan,
    ) -> None:
        """局部重规划只替换指定时段脚本，旧步骤和媒体继续保留审计。"""

        with self._sessions.begin() as session:
            run = _required(session, ProductionRun, run_id)
            row = session.execute(
                select(Episode).where(
                    Episode.production_run_id == run_id,
                    Episode.slot == episode.slot.value,
                )
            ).scalar_one()
            current = EpisodeStatus(row.status)
            if current not in {EpisodeStatus.PLANNED, EpisodeStatus.FAILED}:
                raise ValueError(
                    f"{episode.slot.value}状态{current.value}不允许局部重规划"
                )
            if current is EpisodeStatus.FAILED:
                row.status = transition_episode(
                    current,
                    EpisodeStatus.PLANNED,
                ).value
            row.title = episode.title
            row.script_json = episode.model_dump(mode="json")
            row.video_input_mode = episode.video_input_mode.value
            if run.plan_json is None:
                raise ValueError("Run尚未形成完整方案")
            plan = DailyProductionPlan.model_validate(run.plan_json)
            payload = plan.model_dump(mode="json")
            payload["episodes"] = [
                (
                    episode.model_dump(mode="json")
                    if item.slot is episode.slot
                    else item.model_dump(mode="json")
                )
                for item in plan.episodes
            ]
            run.plan_json = DailyProductionPlan.model_validate(payload).model_dump(
                mode="json"
            )

    def get_run(self, run_id: uuid.UUID) -> StoredRun:
        with self._sessions() as session:
            row = _required(session, ProductionRun, run_id)
            plan = (
                None
                if row.plan_json is None
                else DailyProductionPlan.model_validate(row.plan_json)
            )
            return StoredRun(row.id, row.content_date, row.status, plan)

    def get_episode(self, run_id: uuid.UUID, slot: Slot) -> StoredEpisode:
        with self._sessions() as session:
            row = session.execute(
                select(Episode).where(
                    Episode.production_run_id == run_id,
                    Episode.slot == slot.value,
                )
            ).scalar_one_or_none()
            if row is None:
                raise RecordNotFoundError(f"Run {run_id}没有{slot.value} Episode")
            return stored_episode(row)

    def list_episodes(self, run_id: uuid.UUID) -> tuple[StoredEpisode, ...]:
        with self._sessions() as session:
            rows = session.execute(
                select(Episode)
                .where(Episode.production_run_id == run_id)
                .order_by(Episode.sort_order)
            ).scalars()
            return tuple(stored_episode(row) for row in rows)

    def get_step(self, step_id: uuid.UUID) -> StoredStep:
        with self._sessions() as session:
            return stored_step(_required(session, WorkflowStep, step_id))

    def list_resumable_steps(
        self,
        run_id: uuid.UUID | None,
    ) -> tuple[StoredStep, ...]:
        statuses = (
            StepStatus.SUBMISSION_UNKNOWN.value,
            StepStatus.QUEUED.value,
            StepStatus.RUNNING.value,
        )
        statement: Select[tuple[WorkflowStep]] = select(WorkflowStep).where(
            WorkflowStep.status.in_(statuses)
        )
        if run_id is not None:
            statement = statement.where(WorkflowStep.production_run_id == run_id)
        with self._sessions() as session:
            rows = session.execute(
                statement.order_by(WorkflowStep.created_at)
            ).scalars()
            return tuple(stored_step(row) for row in rows)

    def list_assets(
        self,
        *,
        run_id: uuid.UUID | None = None,
        episode_id: uuid.UUID | None = None,
        roles: tuple[str, ...] = (),
        statuses: tuple[str, ...] = (),
    ) -> tuple[StoredAsset, ...]:
        statement = select(Asset)
        if run_id is None:
            statement = statement.where(Asset.production_run_id.is_(None))
        else:
            statement = statement.where(
                (Asset.production_run_id == run_id) | (Asset.scope == "canon")
            )
        if episode_id is not None:
            statement = statement.where(
                (Asset.episode_id == episode_id) | (Asset.episode_id.is_(None))
            )
        if roles:
            statement = statement.where(Asset.role.in_(roles))
        if statuses:
            statement = statement.where(Asset.status.in_(statuses))
        with self._sessions() as session:
            rows = session.execute(statement.order_by(Asset.created_at)).scalars()
            return tuple(stored_asset(row) for row in rows)

    def set_episode_status(
        self,
        episode_id: uuid.UUID,
        target: EpisodeStatus,
    ) -> None:
        with self._sessions.begin() as session:
            row = _required(session, Episode, episode_id)
            row.status = transition_episode(
                EpisodeStatus(row.status),
                target,
            ).value

    def set_run_status(self, run_id: uuid.UUID, target: RunStatus) -> None:
        with self._sessions.begin() as session:
            row = _required(session, ProductionRun, run_id)
            row.status = transition_run(RunStatus(row.status), target).value

    def set_step_status(
        self,
        step_id: uuid.UUID,
        target: StepStatus,
        *,
        provider_task_id: str | None = None,
        request_summary_patch: dict[str, Any] | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            row = _required(session, WorkflowStep, step_id)
            row.status = transition_step(StepStatus(row.status), target).value
            if provider_task_id is not None:
                row.provider_task_id = provider_task_id
            if request_summary_patch:
                row.request_summary_json = {
                    **row.request_summary_json,
                    **request_summary_patch,
                }
            if target is StepStatus.SUBMITTING:
                row.submitted_at = datetime.now(timezone.utc)
            if target in {
                StepStatus.SUCCEEDED,
                StepStatus.FAILED,
                StepStatus.EXPIRED,
                StepStatus.CANCELLED,
            }:
                row.completed_at = datetime.now(timezone.utc)

    def save_asset(
        self,
        *,
        run_id: uuid.UUID | None,
        episode_id: uuid.UUID | None,
        step_id: uuid.UUID | None,
        role: str,
        scope: str,
        status: str,
        media_type: str,
        landed: LandedAsset,
        metadata: dict[str, Any],
    ) -> StoredAsset:
        with self._sessions.begin() as session:
            existing = session.execute(
                select(Asset).where(
                    Asset.sha256 == landed.sha256,
                    Asset.role == role,
                    Asset.production_run_id == run_id,
                    Asset.episode_id == episode_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return stored_asset(existing)
            row = Asset(
                production_run_id=run_id,
                episode_id=episode_id,
                producing_step_id=step_id,
                role=role,
                scope=scope,
                status=status,
                media_type=media_type,
                local_path=str(landed.path),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                metadata_json=metadata,
            )
            session.add(row)
            session.flush()
            return stored_asset(row)

    def select_video_asset(
        self,
        *,
        episode_id: uuid.UUID,
        asset_id: uuid.UUID,
    ) -> None:
        with self._sessions.begin() as session:
            episode = _required(session, Episode, episode_id)
            asset = _required(session, Asset, asset_id)
            if asset.episode_id != episode_id or asset.role != "video":
                raise ValueError("只能选择属于当前Episode的视频资产")
            episode.selected_video_asset_id = asset_id
            episode.status = transition_episode(
                EpisodeStatus(episode.status),
                EpisodeStatus.READY,
            ).value
            asset.status = "ready"

    def record_review(
        self,
        *,
        step_id: uuid.UUID,
        asset_id: uuid.UUID | None,
        source: str,
        decision: str,
        reason: str | None,
        warnings: list[dict[str, Any]],
        evidence: dict[str, Any],
    ) -> uuid.UUID:
        with self._sessions.begin() as session:
            row = Review(
                step_id=step_id,
                asset_id=asset_id,
                source=source,
                decision=decision,
                reason=reason,
                warnings_json=warnings,
                evidence_json=evidence,
            )
            session.add(row)
            session.flush()
            return row.id

    def set_asset_status(self, asset_id: uuid.UUID, status: str) -> None:
        with self._sessions.begin() as session:
            _required(session, Asset, asset_id).status = status

    def next_delivery_revision(self, run_id: uuid.UUID) -> int:
        with self._sessions() as session:
            revisions = session.execute(
                select(DeliveryPackage.revision).where(
                    DeliveryPackage.production_run_id == run_id
                )
            ).scalars()
            return max(revisions, default=0) + 1

    def save_delivery(
        self,
        *,
        run_id: uuid.UUID,
        revision: int,
        local_path: Path,
        manifest_sha256: str,
        items: tuple[dict[str, Any], ...],
    ) -> uuid.UUID:
        # 数据库事务只在三个文件和Manifest均已原子落盘后开始，因此不会出现
        # 指向半成品目录的交付记录。
        with self._sessions.begin() as session:
            package = DeliveryPackage(
                production_run_id=run_id,
                revision=revision,
                status="delivered",
                local_path=str(local_path),
                manifest_sha256=manifest_sha256,
            )
            session.add(package)
            session.flush()
            session.add_all(
                DeliveryItem(
                    delivery_package_id=package.id,
                    episode_id=uuid.UUID(str(item["episodeId"])),
                    asset_id=uuid.UUID(str(item["assetId"])),
                    slot=str(item["slot"]),
                    sort_order=int(item["sortOrder"]),
                    filename=str(item["filename"]),
                    sha256=str(item["sha256"]),
                )
                for item in items
            )
            run = _required(session, ProductionRun, run_id)
            run.status = transition_run(
                RunStatus(run.status),
                RunStatus.DELIVERED,
            ).value
            return package.id
