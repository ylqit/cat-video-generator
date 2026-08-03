"""PostgreSQL只读投影。

本模块把八张核心表转换为CLI与HTTP共用结果，不推进状态、不编译Prompt，
也不调用Ark。所有运行状态都以PostgreSQL记录为准。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from ...application.ports import StoredAsset, StoredPrompt
from ...domain.contracts import EpisodeScript, RecentContentSummary
from ...domain.workflow import RunStatus
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
    asset_dict,
    delivery_package_dict,
    episode_dict,
    prompt_dict,
    review_dict,
    run_dict,
    step_dict,
    stored_asset,
    stored_prompt,
)
from .session import ALEMBIC_HEAD


class RecordNotFoundError(LookupError):
    """请求的工作流记录不存在。"""


def _current_stage(
    run: ProductionRun,
    episodes: tuple[Episode, ...],
    assets: tuple[Asset, ...],
) -> str:
    """推导创作台步进条的当前阶段，前端无需自行拼接状态。"""

    if "dayBrief" not in run.planning_json:
        return "dayBrief"
    if run.status == RunStatus.DRAFT.value:
        return "dayBriefReview"
    if run.status == RunStatus.PLANNING_REVIEW.value:
        return "script"
    statuses = {item.status for item in episodes}
    if statuses and statuses <= {"ready"}:
        return "done"
    if statuses & {"video_pending", "video_generating", "media_qc", "content_review"}:
        return "video"
    frames = [
        item
        for item in assets
        if item.role in {"first_frame", "last_frame"} and item.episode_id is not None
    ]
    if frames:
        return "keyframes"
    return "script"


def required_record(
    session: Session,
    model: type[Any],
    record_id: uuid.UUID,
) -> Any:
    """把SQLAlchemy的空结果转换为稳定的领域错误。"""

    row = session.get(model, record_id)
    if row is None:
        raise RecordNotFoundError(f"{model.__name__} {record_id} 不存在")
    return row


class SqlAlchemyReadRepository:
    """为QueryService提供统一只读投影。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def workflow_graph(self, run_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            run = required_record(session, ProductionRun, run_id)
            episodes = tuple(
                session.execute(
                    select(Episode)
                    .where(Episode.production_run_id == run_id)
                    .order_by(Episode.sort_order)
                ).scalars()
            )
            steps = tuple(
                session.execute(
                    select(WorkflowStep)
                    .where(WorkflowStep.production_run_id == run_id)
                    .order_by(WorkflowStep.created_at)
                ).scalars()
            )
            step_ids = tuple(item.id for item in steps)
            prompts = self._rows_for_steps(session, PromptRecord, step_ids)
            reviews = self._rows_for_steps(session, Review, step_ids)
            assets = tuple(
                session.execute(
                    select(Asset).where(Asset.production_run_id == run_id)
                ).scalars()
            )
            episode_payloads = [episode_dict(item) for item in episodes]
            contradictions = [
                issue
                for episode in episode_payloads
                for issue in episode["contradictions"]
            ]
            payload = run_dict(run)
            payload.update(
                {
                    "worldConsistencyStatus": (
                        "contradictory"
                        if contradictions
                        else "consistent"
                        if len(episode_payloads) == 3
                        else "not_available"
                    ),
                    "contradictions": contradictions,
                    "dayBrief": run.planning_json.get("dayBrief"),
                    "episodeDrafts": run.planning_json.get("episodeDrafts", {}),
                    "currentStage": _current_stage(run, episodes, assets),
                }
            )
            return {
                "run": payload,
                "episodes": episode_payloads,
                "steps": [step_dict(item) for item in steps],
                "prompts": [prompt_dict(item) for item in prompts],
                "assets": [asset_dict(item) for item in assets],
                "reviews": [review_dict(item) for item in reviews],
            }

    @staticmethod
    def _rows_for_steps(
        session: Session,
        model: type[PromptRecord] | type[Review],
        step_ids: tuple[uuid.UUID, ...],
    ) -> tuple[Any, ...]:
        if not step_ids:
            return ()
        order = model.created_at
        return tuple(
            session.execute(
                select(model).where(model.step_id.in_(step_ids)).order_by(order)
            ).scalars()
        )

    def list_run_summaries(self, limit: int, offset: int) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.execute(
                select(ProductionRun)
                .order_by(
                    ProductionRun.content_date.desc(),
                    ProductionRun.created_at.desc(),
                )
                .limit(limit)
                .offset(offset)
            ).scalars()
            return [run_dict(row) for row in rows]

    def list_recent_completed_summaries(
        self,
        *,
        limit: int,
    ) -> tuple[RecentContentSummary, ...]:
        """只使用已批准或交付Run的结构化字段进行选题冷却。"""

        if not 1 <= limit <= 6:
            raise ValueError("近期内容摘要limit必须在1至6之间")
        with self._sessions() as session:
            runs = tuple(
                session.execute(
                    select(ProductionRun)
                    .where(
                        ProductionRun.status.in_(
                            (RunStatus.READY.value, RunStatus.DELIVERED.value)
                        )
                    )
                    .order_by(
                        ProductionRun.content_date.desc(),
                        ProductionRun.created_at.desc(),
                    )
                    .limit(limit)
                ).scalars()
            )
            result: list[RecentContentSummary] = []
            for run in runs:
                rows = tuple(
                    session.execute(
                        select(Episode)
                        .where(Episode.production_run_id == run.id)
                        .order_by(Episode.sort_order)
                    ).scalars()
                )
                if len(rows) != 3:
                    continue
                scripts = [EpisodeScript.model_validate(row.script_json) for row in rows]
                keys = tuple(
                    dict.fromkeys(
                        entity.semantic_key
                        for script in scripts
                        for entity in script.visible_world.entities
                        if entity.semantic_key is not None
                        and entity.semantic_key.startswith("element:")
                    )
                )
                result.append(
                    RecentContentSummary(
                        content_date=run.content_date,
                        event_keys=tuple(item.event_key for item in scripts),
                        location_keys=tuple(item.location_key for item in scripts),
                        element_semantic_keys=keys,
                        summary_text=(
                            f"{run.content_date.isoformat()}主题="
                            f"{run.planning_json.get('dayBrief', {}).get('theme', '')}；"
                            f"事件={'、'.join(item.main_event for item in scripts)}"
                        ),
                    )
                )
            return tuple(result)

    def prompt_detail(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            prompt = required_record(session, PromptRecord, prompt_id)
            step = required_record(session, WorkflowStep, prompt.step_id)
            result = prompt_dict(prompt, full=True)
            result["inputSnapshot"] = step.input_snapshot_json
            return result

    def get_prompt_for_step(
        self,
        step_id: uuid.UUID,
        *,
        purpose: str,
    ) -> StoredPrompt:
        with self._sessions() as session:
            row = session.execute(
                select(PromptRecord)
                .where(
                    PromptRecord.step_id == step_id,
                    PromptRecord.purpose == purpose,
                )
                .order_by(PromptRecord.created_at.desc())
            ).scalar_one_or_none()
            if row is None:
                raise RecordNotFoundError(
                    f"Step {step_id}不存在purpose={purpose!r}的Prompt"
                )
            return stored_prompt(row)

    def asset_detail(self, asset_id: uuid.UUID) -> StoredAsset:
        with self._sessions() as session:
            return stored_asset(required_record(session, Asset, asset_id))

    def episode_detail(self, episode_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            return episode_dict(required_record(session, Episode, episode_id))

    def step_detail(self, step_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            return step_dict(required_record(session, WorkflowStep, step_id))

    def list_delivery_packages(self, run_id: uuid.UUID) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = tuple(
                session.execute(
                    select(DeliveryPackage)
                    .where(DeliveryPackage.production_run_id == run_id)
                    .order_by(DeliveryPackage.revision.desc())
                ).scalars()
            )
            return [self._delivery_dict(session, row) for row in rows]

    def delivery_package_detail(self, package_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            row = required_record(session, DeliveryPackage, package_id)
            return self._delivery_dict(session, row)

    @staticmethod
    def _delivery_dict(
        session: Session,
        package: DeliveryPackage,
    ) -> dict[str, Any]:
        items = tuple(
            session.execute(
                select(DeliveryItem)
                .where(DeliveryItem.delivery_package_id == package.id)
                .order_by(DeliveryItem.sort_order)
            ).scalars()
        )
        return delivery_package_dict(package, items)

    def health(self) -> dict[str, Any]:
        with self._sessions() as session:
            database = session.execute(text("SELECT current_database()" )).scalar_one()
            user = session.execute(text("SELECT current_user")).scalar_one()
            revision = session.execute(
                text("SELECT version_num FROM cat_video.alembic_version")
            ).scalar_one_or_none()
            return {
                "database": database,
                "user": user,
                "alembicRevision": revision,
                "expectedAlembicRevision": ALEMBIC_HEAD,
                "ready": revision == ALEMBIC_HEAD,
            }
