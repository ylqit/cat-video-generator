"""PostgreSQL 只读投影实现。

本模块只负责把八张核心表转换成 CLI 与 HTTP 共用的查询结果；它不推进状态、
不编译 Prompt，也不调用 Ark。写入和并发语义仍由 ``repositories.py`` 持有。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from ...application.ports import StoredAsset, StoredPrompt
from ...domain.contracts import DailyProductionPlan, RecentContentSummary
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


def required_record(
    session: Session,
    model: type[Any],
    record_id: uuid.UUID,
) -> Any:
    """读取必需记录，并把 SQLAlchemy 的 ``None`` 转为稳定的领域错误。"""

    row = session.get(model, record_id)
    if row is None:
        raise RecordNotFoundError(f"{model.__name__} {record_id} 不存在")
    return row


class SqlAlchemyReadRepository:
    """为 QueryService 提供统一只读投影。"""

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
            step_ids = tuple(step.id for step in steps)
            prompts = (
                ()
                if not step_ids
                else tuple(
                    session.execute(
                        select(PromptRecord).where(PromptRecord.step_id.in_(step_ids))
                    ).scalars()
                )
            )
            assets = tuple(
                session.execute(select(Asset).where(Asset.production_run_id == run_id)).scalars()
            )
            reviews = (
                ()
                if not step_ids
                else tuple(
                    session.execute(
                        select(Review)
                        .where(Review.step_id.in_(step_ids))
                        .order_by(Review.created_at)
                    ).scalars()
                )
            )
            run_payload = run_dict(run)
            episode_payloads = [episode_dict(row) for row in episodes]
            director_repairs = [
                step
                for step in steps
                if bool(step.request_summary_json.get("directorRepairAttempted"))
            ]
            contradictions = [
                message for episode in episode_payloads for message in episode["contradictions"]
            ]
            if not contradictions and run.status == RunStatus.PLANNING_REVIEW.value:
                rejected_contract_reviews = [
                    review
                    for review in reviews
                    if review.decision == "rejected"
                    and review.evidence_json.get("phase") == "episode_contract"
                    and review.reason
                ]
                if rejected_contract_reviews:
                    contradictions.append(str(rejected_contract_reviews[-1].reason))
                failed_directors = [
                    step
                    for step in steps
                    if step.kind == "director" and step.error_json is not None
                ]
                if not contradictions and failed_directors:
                    message = failed_directors[-1].error_json.get("message")
                    if message:
                        contradictions.append(str(message))
            risk_order = {"unknown": -1, "low": 0, "medium": 1, "high": 2}
            risk_level = max(
                (episode["renderRiskLevel"] for episode in episode_payloads),
                key=lambda item: risk_order[item],
                default="unknown",
            )
            run_payload.update(
                {
                    "worldConsistencyStatus": (
                        "contradictory"
                        if contradictions
                        else ("consistent" if len(episode_payloads) == 3 else "not_available")
                    ),
                    "contradictions": contradictions,
                    "renderRiskLevel": risk_level,
                    "renderRiskReasons": list(
                        dict.fromkeys(
                            reason
                            for episode in episode_payloads
                            for reason in episode["renderRiskReasons"]
                        )
                    ),
                    "multiClipRecommended": any(
                        episode["multiClipRecommended"] for episode in episode_payloads
                    ),
                    "directorRepairAttempted": bool(director_repairs),
                }
            )
            return {
                "run": run_payload,
                "episodes": episode_payloads,
                "steps": [step_dict(row) for row in steps],
                "prompts": [prompt_dict(row) for row in prompts],
                "assets": [asset_dict(row) for row in assets],
                "reviews": [review_dict(row) for row in reviews],
            }

    def list_run_summaries(
        self,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.execute(
                select(ProductionRun)
                .order_by(ProductionRun.content_date.desc())
                .limit(limit)
                .offset(offset)
            ).scalars()
            return [run_dict(row) for row in rows]

    def list_recent_completed_summaries(
        self,
        *,
        limit: int,
    ) -> tuple[RecentContentSummary, ...]:
        """只把已完成审核或交付的Run用于选题冷却。"""

        if not 1 <= limit <= 6:
            raise ValueError("近期内容摘要limit必须在1至6之间")
        with self._sessions() as session:
            rows = tuple(
                session.execute(
                    select(ProductionRun)
                    .where(
                        ProductionRun.status.in_(
                            (RunStatus.READY.value, RunStatus.DELIVERED.value)
                        ),
                        ProductionRun.plan_json.is_not(None),
                    )
                    .order_by(
                        ProductionRun.content_date.desc(),
                        ProductionRun.created_at.desc(),
                    )
                    .limit(limit)
                ).scalars()
            )
        summaries: list[RecentContentSummary] = []
        for row in rows:
            plan = DailyProductionPlan.model_validate(row.plan_json)
            props = sorted(
                {
                    entity.display_name
                    for episode in plan.episodes
                    for entity in (
                        episode.visible_world.tracked_entities
                        if episode.visible_world is not None
                        else ()
                    )
                    if entity.entity_type in {"prop", "food", "container"}
                }
            )
            summaries.append(
                RecentContentSummary(
                    content_date=row.content_date,
                    event_keys=tuple(
                        item.event_key
                        for item in plan.episodes
                        if item.event_key is not None
                    ),
                    location_keys=tuple(
                        item.location_key
                        for item in plan.episodes
                        if item.location_key is not None
                    ),
                    element_semantic_keys=tuple(
                        dict.fromkeys(
                            key
                            for item in plan.episodes
                            for key in item.reference_semantic_keys
                        )
                    ),
                    summary_text=(
                        f"{row.content_date.isoformat()}主题={plan.theme}；"
                        f"主事件={'、'.join(item.main_event for item in plan.episodes)}；"
                        f"地点={'、'.join(item.scene for item in plan.episodes)}；"
                        f"关键道具={'、'.join(props) or '无'}；"
                        "构图="
                        + "、".join(
                            shot.dominant_view.value
                            for episode in plan.episodes
                            for shot in episode.shots
                        )
                    ),
                )
            )
        return tuple(summaries)

    def prompt_detail(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            prompt = required_record(session, PromptRecord, prompt_id)
            result = prompt_dict(prompt, full=True)
            step = required_record(session, WorkflowStep, prompt.step_id)
            result["inputPlan"] = step.request_summary_json.get("videoInputPlan")
            result["promptAliases"] = step.request_summary_json.get(
                "promptAliases",
                {},
            )
            return result

    def get_prompt_for_step(
        self,
        step_id: uuid.UUID,
        *,
        purpose: str,
    ) -> StoredPrompt:
        """取得某个Step真正使用的Prompt，供恢复和受控对比复用。"""

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
                raise RecordNotFoundError(f"Step {step_id}不存在purpose={purpose!r}的Prompt")
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

    def list_delivery_packages(
        self,
        run_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        with self._sessions() as session:
            packages = tuple(
                session.execute(
                    select(DeliveryPackage)
                    .where(DeliveryPackage.production_run_id == run_id)
                    .order_by(DeliveryPackage.revision.desc())
                ).scalars()
            )
            return [
                delivery_package_dict(
                    package,
                    self._delivery_items(session, package.id),
                )
                for package in packages
            ]

    def delivery_package_detail(
        self,
        package_id: uuid.UUID,
    ) -> dict[str, Any]:
        with self._sessions() as session:
            package = required_record(session, DeliveryPackage, package_id)
            return delivery_package_dict(
                package,
                self._delivery_items(session, package.id),
            )

    @staticmethod
    def _delivery_items(
        session: Session,
        package_id: uuid.UUID,
    ) -> tuple[DeliveryItem, ...]:
        return tuple(
            session.execute(
                select(DeliveryItem)
                .where(DeliveryItem.delivery_package_id == package_id)
                .order_by(DeliveryItem.sort_order)
            ).scalars()
        )

    def health(self) -> dict[str, Any]:
        with self._sessions() as session:
            row = session.execute(
                text(
                    "SELECT current_database(), current_user, "
                    "current_setting('server_version_num')::int, "
                    "(SELECT ssl FROM pg_stat_ssl "
                    "WHERE pid = pg_backend_pid())"
                )
            ).one()
            revision = session.execute(
                text("SELECT version_num FROM cat_video.alembic_version")
            ).scalar_one()
            return {
                "connected": True,
                "database": row[0],
                "user": row[1],
                "serverVersionNum": row[2],
                "ssl": bool(row[3]),
                "alembicRevision": revision,
                "alembicHead": ALEMBIC_HEAD,
                "migrationCurrent": revision == ALEMBIC_HEAD,
            }
