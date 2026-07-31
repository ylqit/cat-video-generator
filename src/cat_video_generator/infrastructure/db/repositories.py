"""SQLAlchemy工作流Repository；拥有并发、短事务和幂等，不处理创意或Ark。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from ...application.ports import (
    LandedAsset,
    StoredAsset,
    StoredEpisode,
    StoredRun,
    StoredStep,
)
from ...domain.contracts import DailyProductionPlan, Slot
from ...domain.workflow import (
    EpisodeStatus,
    RunStatus,
    StepKind,
    StepStatus,
    transition_episode,
    transition_run,
    transition_step,
)
from .delivery_repository import DeliveryPersistenceMixin
from .director_repository import DirectorStepPersistenceMixin
from .models import (
    Asset,
    Episode,
    ProductionRun,
    PromptRecord,
    WorkflowStep,
)
from .plan_repository import PlanPersistenceMixin
from .query_repository import (
    RecordNotFoundError,
    SqlAlchemyReadRepository,
    required_record,
)
from .records import (
    stored_asset,
    stored_episode,
    stored_step,
)
from .review_repository import ReviewPersistenceMixin


class SqlAlchemyWorkflowRepository(
    DirectorStepPersistenceMixin,
    DeliveryPersistenceMixin,
    PlanPersistenceMixin,
    ReviewPersistenceMixin,
    SqlAlchemyReadRepository,
):
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
        operation_key = str(request_summary.get("operationKey", ""))
        raw_key = "|".join(
            (
                str(run_id),
                str(episode_id or ""),
                kind.value,
                operation_key,
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
            # 兼容本次升级前未把operationKey写入幂等摘要的历史Step。
            # 先按完整业务字段复用旧记录，再使用新幂等键处理并发创建；否则部署
            # 新版本后的第一次run-day可能绕过旧唯一键并重复产生收费任务。
            compatible = select(WorkflowStep.id).where(
                WorkflowStep.production_run_id == run_id,
                (
                    WorkflowStep.episode_id.is_(None)
                    if episode_id is None
                    else WorkflowStep.episode_id == episode_id
                ),
                WorkflowStep.kind == kind.value,
                WorkflowStep.attempt == attempt,
                WorkflowStep.input_hash == input_hash,
            )
            if operation_key:
                compatible = compatible.where(
                    WorkflowStep.request_summary_json["operationKey"].astext
                    == operation_key
                )
            else:
                phase = request_summary.get("phase")
                slot = request_summary.get("slot")
                if phase is not None:
                    compatible = compatible.where(
                        WorkflowStep.request_summary_json["phase"].astext == str(phase)
                    )
                if slot is not None:
                    compatible = compatible.where(
                        WorkflowStep.request_summary_json["slot"].astext == str(slot)
                    )
            compatible_id = session.execute(
                compatible.order_by(WorkflowStep.created_at, WorkflowStep.id).limit(1)
            ).scalar_one_or_none()
            if compatible_id is not None:
                existing_row = session.get(WorkflowStep, compatible_id)
                assert existing_row is not None
                return stored_step(existing_row)

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
            row = required_record(session, WorkflowStep, step_id)
            row.status = transition_step(StepStatus(row.status), target).value
            row.error_json = {"code": code, "message": message}
            row.completed_at = (
                None if submission_unknown else datetime.now(timezone.utc)
            )

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

    def next_step_attempt(
        self,
        *,
        episode_id: uuid.UUID,
        kind: StepKind,
        operation_key: str,
    ) -> int:
        """按稳定业务操作计算新attempt，Segment序号不再冒充重试次数。"""

        statement = select(func.max(WorkflowStep.attempt)).where(
            WorkflowStep.episode_id == episode_id,
            WorkflowStep.kind == kind.value,
            WorkflowStep.request_summary_json["operationKey"].astext
            == operation_key,
        )
        with self._sessions() as session:
            return int(session.execute(statement).scalar_one_or_none() or 0) + 1

    def get_run(self, run_id: uuid.UUID) -> StoredRun:
        with self._sessions() as session:
            row = required_record(session, ProductionRun, run_id)
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
            return stored_step(required_record(session, WorkflowStep, step_id))

    def latest_retryable_step(
        self,
        episode_id: uuid.UUID,
    ) -> StoredStep | None:
        """返回Episode最近一个可由用户显式重试的终态步骤。"""

        with self._sessions() as session:
            row = session.execute(
                select(WorkflowStep)
                .where(
                    WorkflowStep.episode_id == episode_id,
                    WorkflowStep.status.in_(
                        (
                            StepStatus.FAILED.value,
                            StepStatus.EXPIRED.value,
                            StepStatus.CANCELLED.value,
                        )
                    ),
                )
                .order_by(
                    WorkflowStep.created_at.desc(),
                    WorkflowStep.attempt.desc(),
                )
                .limit(1)
            ).scalar_one_or_none()
            return None if row is None else stored_step(row)

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
        semantic_keys: tuple[str, ...] = (),
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
        if semantic_keys:
            statement = statement.where(Asset.semantic_key.in_(semantic_keys))
        with self._sessions() as session:
            # 选择器按升序覆盖同语义键；UUID负责稳定处理相同created_at。
            rows = session.execute(
                statement.order_by(Asset.created_at, Asset.id)
            ).scalars()
            return tuple(stored_asset(row) for row in rows)

    def find_reusable_asset(
        self,
        *,
        episode_id: uuid.UUID,
        role: str,
        input_hash: str,
        statuses: tuple[str, ...],
    ) -> StoredAsset | None:
        """只复用与当前Prompt、素材和Canon哈希完全相同的未拒绝资产。"""

        statement = (
            select(Asset)
            .join(
                WorkflowStep,
                Asset.producing_step_id == WorkflowStep.id,
            )
            .where(
                Asset.episode_id == episode_id,
                Asset.role == role,
                Asset.status.in_(statuses),
                or_(
                    WorkflowStep.input_hash == input_hash,
                    WorkflowStep.request_summary_json["baseInputHash"].astext
                    == input_hash,
                ),
            )
            .order_by(Asset.created_at.desc(), Asset.id.desc())
            .limit(1)
        )
        with self._sessions() as session:
            row = session.execute(statement).scalar_one_or_none()
            return None if row is None else stored_asset(row)

    def set_episode_status(
        self,
        episode_id: uuid.UUID,
        target: EpisodeStatus,
    ) -> None:
        with self._sessions.begin() as session:
            row = required_record(session, Episode, episode_id)
            row.status = transition_episode(
                EpisodeStatus(row.status),
                target,
            ).value

    def set_run_status(self, run_id: uuid.UUID, target: RunStatus) -> None:
        with self._sessions.begin() as session:
            row = required_record(session, ProductionRun, run_id)
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
            row = required_record(session, WorkflowStep, step_id)
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
        semantic_key: str | None,
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
                    Asset.semantic_key == semantic_key,
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
                semantic_key=semantic_key,
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
            episode = required_record(session, Episode, episode_id)
            asset = required_record(session, Asset, asset_id)
            if asset.episode_id != episode_id or asset.role != "video":
                raise ValueError("只能选择属于当前Episode的视频资产")
            episode.selected_video_asset_id = asset_id
            episode.status = transition_episode(
                EpisodeStatus(episode.status),
                EpisodeStatus.READY,
            ).value
            asset.status = "ready"

    def set_asset_status(self, asset_id: uuid.UUID, status: str) -> None:
        with self._sessions.begin() as session:
            required_record(session, Asset, asset_id).status = status
