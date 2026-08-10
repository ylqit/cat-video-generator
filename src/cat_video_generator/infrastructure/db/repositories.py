"""SQLAlchemy工作流Repository；拥有并发、短事务和幂等，不处理创意或Ark。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
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
from ...domain.contracts import DailyProductionPlan, Slot
from ...domain.snapshots import validate_input_snapshot
from ...domain.workflow import (
    EpisodeStatus,
    PromptPurpose,
    RunStatus,
    StepKind,
    StepStatus,
    transition_episode,
    transition_run,
    transition_step,
    validate_prompt_purpose,
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

_GENERATION_PROMPT_PURPOSES = frozenset(
    {
        PromptPurpose.DIRECTOR,
        PromptPurpose.IMAGE,
        PromptPurpose.VIDEO,
    }
)


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

    def create_step_with_prompt_intent(
        self,
        *,
        run_id: uuid.UUID,
        episode_id: uuid.UUID | None,
        parent_step_id: uuid.UUID | None,
        kind: StepKind,
        attempt: int,
        operation_key: str,
        provider: str | None,
        model: str | None,
        input_hash: str,
        input_snapshot: dict[str, Any],
        prompt_purpose: PromptPurpose,
        prompt_model: str,
        prompt_text: str,
        parent_prompt_id: uuid.UUID | None,
    ) -> tuple[StoredStep, uuid.UUID]:
        """在同一事务内持久化外部调用意图与实际Prompt。

        Prompt是收费输入的一部分，不能在Step之后用第二个事务补写；否则数据库
        约束或进程中断会留下无法解释、也无法安全恢复的孤立收费意图。
        """

        # 幂等键包含业务所有者、收费步骤和规范化输入。并发Worker即使同时
        # 领取同一任务，也只有一个INSERT能成功，避免重复产生Ark费用。
        if not operation_key.strip():
            raise ValueError("WorkflowStep.operation_key不能为空")
        if not prompt_text.strip():
            raise ValueError("Prompt正文不能为空")
        validate_prompt_purpose(kind, prompt_purpose, generation_intent=True)
        snapshot = validate_input_snapshot(input_snapshot).model_dump(mode="json")
        prompt_digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
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
            "operation_key": operation_key,
            "idempotency_key": key,
            "provider": provider,
            "model": model,
            "input_hash": input_hash,
            "input_snapshot_json": snapshot,
        }
        with self._sessions.begin() as session:
            # operation_key是正式关系列；同一业务操作和输入只允许存在一个收费意图。
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
                WorkflowStep.operation_key == operation_key,
            )
            compatible_id = session.execute(
                compatible.order_by(WorkflowStep.created_at, WorkflowStep.id).limit(1)
            ).scalar_one_or_none()
            if compatible_id is not None:
                existing_row = session.get(WorkflowStep, compatible_id)
                assert existing_row is not None
                prompt_id = self._ensure_prompt_record(
                    session,
                    step=existing_row,
                    parent_prompt_id=parent_prompt_id,
                    purpose=prompt_purpose,
                    model=prompt_model,
                    text=prompt_text,
                    digest=prompt_digest,
                )
                return stored_step(existing_row), prompt_id

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
            prompt_id = self._ensure_prompt_record(
                session,
                step=row,
                parent_prompt_id=parent_prompt_id,
                purpose=prompt_purpose,
                model=prompt_model,
                text=prompt_text,
                digest=prompt_digest,
            )
            return stored_step(row), prompt_id

    def save_prompt(
        self,
        *,
        step_id: uuid.UUID,
        parent_prompt_id: uuid.UUID | None,
        purpose: PromptPurpose,
        model: str,
        text: str,
    ) -> uuid.UUID:
        if not text.strip():
            raise ValueError("Prompt正文不能为空")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._sessions.begin() as session:
            step = required_record(session, WorkflowStep, step_id)
            validate_prompt_purpose(StepKind(step.kind), purpose)
            return self._ensure_prompt_record(
                session,
                step=step,
                parent_prompt_id=parent_prompt_id,
                purpose=purpose,
                model=model,
                text=text,
                digest=digest,
            )

    @staticmethod
    def _ensure_prompt_record(
        session: Any,
        *,
        step: WorkflowStep,
        parent_prompt_id: uuid.UUID | None,
        purpose: PromptPurpose,
        model: str,
        text: str,
        digest: str,
    ) -> uuid.UUID:
        existing = session.execute(
            select(PromptRecord).where(
                PromptRecord.step_id == step.id,
                PromptRecord.sha256 == digest,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.purpose != purpose.value
                or existing.model != model
                or existing.parent_prompt_id != parent_prompt_id
            ):
                raise ValueError("相同Step与Prompt哈希对应了不同用途、模型或父Prompt")
            return existing.id
        if purpose in _GENERATION_PROMPT_PURPOSES:
            main_prompt = session.execute(
                select(PromptRecord).where(
                    PromptRecord.step_id == step.id,
                    PromptRecord.purpose == purpose.value,
                )
            ).scalar_one_or_none()
            if main_prompt is not None:
                # 一个收费Step只能有一个主生成Prompt。若相同input_hash却传入不同
                # 正文，说明调用端幂等输入构造有缺陷，必须显式失败而不是留下歧义。
                raise ValueError("同一Step不能绑定两个不同的生成Prompt")
        row = PromptRecord(
            step_id=step.id,
            parent_prompt_id=parent_prompt_id,
            purpose=purpose.value,
            model=model,
            prompt_text=text,
            sha256=digest,
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
        request_id: str | None = None,
        input_snapshot_patch: dict[str, Any] | None = None,
    ) -> None:
        target = StepStatus.SUBMISSION_UNKNOWN if submission_unknown else StepStatus.FAILED
        with self._sessions.begin() as session:
            row = required_record(session, WorkflowStep, step_id)
            row.status = transition_step(StepStatus(row.status), target).value
            row.error_json = {
                "code": code,
                "message": message,
                **({"requestId": request_id} if request_id else {}),
            }
            if input_snapshot_patch:
                merged = {**row.input_snapshot_json, **input_snapshot_patch}
                row.input_snapshot_json = validate_input_snapshot(merged).model_dump(mode="json")
            row.completed_at = None if submission_unknown else datetime.now(timezone.utc)

    def next_director_attempt(
        self,
        *,
        run_id: uuid.UUID,
        phase: str,
        slot: Slot | None,
    ) -> int:
        operation_key = "director:day" if slot is None else f"director:episode:{slot.value}"
        statement = select(func.max(WorkflowStep.attempt)).where(
            WorkflowStep.production_run_id == run_id,
            WorkflowStep.kind == StepKind.DIRECTOR.value,
            WorkflowStep.operation_key == operation_key,
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
            WorkflowStep.operation_key == operation_key,
        )
        with self._sessions() as session:
            return int(session.execute(statement).scalar_one_or_none() or 0) + 1

    def get_run(self, run_id: uuid.UUID) -> StoredRun:
        with self._sessions() as session:
            row = required_record(session, ProductionRun, run_id)
            episode_rows = tuple(
                session.execute(
                    select(Episode)
                    .where(Episode.production_run_id == run_id)
                    .order_by(Episode.sort_order)
                ).scalars()
            )
            day_brief = row.planning_json.get("dayBrief")
            plan = None
            if day_brief is not None and len(episode_rows) == 3:
                plan = DailyProductionPlan(
                    day_brief=day_brief,
                    episodes=[stored_episode(item).plan for item in episode_rows],
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

    def find_step_by_provider_task_id(
        self,
        provider_task_id: str,
    ) -> StoredStep | None:
        """查询供应商任务的唯一所有者，防止对账时跨Step重复绑定。"""

        with self._sessions() as session:
            row = session.execute(
                select(WorkflowStep).where(WorkflowStep.provider_task_id == provider_task_id)
            ).scalar_one_or_none()
            return None if row is None else stored_step(row)

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
            rows = session.execute(statement.order_by(WorkflowStep.created_at)).scalars()
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
            rows = session.execute(statement.order_by(Asset.created_at, Asset.id)).scalars()
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
                WorkflowStep.input_hash == input_hash,
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
        input_snapshot_patch: dict[str, Any] | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            row = required_record(session, WorkflowStep, step_id)
            row.status = transition_step(StepStatus(row.status), target).value
            if provider_task_id is not None:
                row.provider_task_id = provider_task_id
            if input_snapshot_patch:
                merged = {**row.input_snapshot_json, **input_snapshot_patch}
                row.input_snapshot_json = validate_input_snapshot(merged).model_dump(mode="json")
            if target is StepStatus.SUBMITTING:
                row.submitted_at = datetime.now(timezone.utc)
            if target in {
                StepStatus.SUCCEEDED,
                StepStatus.FAILED,
                StepStatus.EXPIRED,
                StepStatus.CANCELLED,
            }:
                row.completed_at = datetime.now(timezone.utc)

    def reopen_video_step_for_local_recovery(self, step_id: uuid.UUID) -> None:
        """恢复已有成功Ark task的本地落盘，不创建新attempt或新收费请求。"""

        with self._sessions.begin() as session:
            row = required_record(session, WorkflowStep, step_id)
            error_code = (row.error_json or {}).get("code")
            provider_status = row.input_snapshot_json.get("provider_task_status")
            if (
                row.kind != StepKind.VIDEO.value
                or row.status != StepStatus.FAILED.value
                or not row.provider_task_id
                or error_code != "media_qc_failed"
                or provider_status != "succeeded"
            ):
                raise ValueError("只有供应商已成功且仅本地QC失败的视频Step可以无付费恢复")
            row.status = transition_step(StepStatus.FAILED, StepStatus.RUNNING).value
            row.error_json = None
            row.completed_at = None
            row.input_snapshot_json = validate_input_snapshot(
                {
                    **row.input_snapshot_json,
                    "local_recovery_started_at": datetime.now(timezone.utc),
                }
            ).model_dump(mode="json")

    def patch_step_snapshot(
        self,
        step_id: uuid.UUID,
        patch: dict[str, Any],
    ) -> None:
        """不改变生命周期状态地记录轮询、自动重试或对账事实。"""

        if not patch:
            return
        with self._sessions.begin() as session:
            row = required_record(session, WorkflowStep, step_id)
            merged = {**row.input_snapshot_json, **patch}
            row.input_snapshot_json = validate_input_snapshot(merged).model_dump(mode="json")

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
                if scope == "canon" and status == "approved":
                    # Canon语义键在生产侧只能有一个活动版本。旧行仍保留给历史Step按ID审计，
                    # 但降级为rejected后不会再被自动选图或出现在Canon工作台。
                    existing.status = "approved"
                    for previous in session.scalars(
                        select(Asset).where(
                            Asset.scope == "canon",
                            Asset.semantic_key == semantic_key,
                            Asset.id != existing.id,
                            Asset.status == "approved",
                        )
                    ):
                        previous.status = "rejected"
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
            if scope == "canon" and status == "approved":
                # 新版本写入和旧版本退役处于同一事务，避免并发期间同时暴露两套主体或画风。
                for previous in session.scalars(
                    select(Asset).where(
                        Asset.scope == "canon",
                        Asset.semantic_key == semantic_key,
                        Asset.id != row.id,
                        Asset.status == "approved",
                    )
                ):
                    previous.status = "rejected"
            return stored_asset(row)
