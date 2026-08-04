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
from ...domain.contracts import EpisodeScript, RecentContentSummary, Slot
from ...domain.workflow import PromptPurpose, RunStatus
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
    steps: tuple[WorkflowStep, ...],
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
    # 失败的故事板可能还没有产生任何Asset。仅依赖图片资产会让页面退回
    # “三集剧本”，因此以已经持久化的工作流意图作为阶段事实。
    if any(item.operation_key == "image:storyboard" for item in steps):
        return "storyboard"
    storyboards = [
        item for item in assets if item.role == "storyboard_panel" and item.episode_id is not None
    ]
    if storyboards:
        return "storyboard"
    return "script"


def _workflow_nodes(
    episodes: tuple[Episode, ...],
    steps: tuple[WorkflowStep, ...],
    prompts: tuple[PromptRecord, ...],
    assets: tuple[Asset, ...],
    reviews: tuple[Review, ...],
) -> list[dict[str, Any]]:
    """生成创作台使用的固定节点投影，不把前端变成第二套状态机。"""

    prompt_ids_by_step: dict[uuid.UUID, list[str]] = {}
    for prompt in prompts:
        prompt_ids_by_step.setdefault(prompt.step_id, []).append(str(prompt.id))
    review_ids_by_step: dict[uuid.UUID, list[str]] = {}
    for review in reviews:
        review_ids_by_step.setdefault(review.step_id, []).append(str(review.id))

    def latest_step(operation_key: str) -> WorkflowStep | None:
        matches = [item for item in steps if item.operation_key == operation_key]
        # 局部重规划会开始一组新的媒体attempt，数字可能重新从1开始。
        # 节点当前态因此必须按真实创建时间选择，不能让旧剧本的attempt=2遮住新剧本的attempt=1。
        return max(matches, key=lambda item: item.created_at, default=None)

    def step_node(
        *,
        node_id: str,
        node_type: str,
        slot: str | None,
        label: str,
        step: WorkflowStep | None,
        node_assets: tuple[Asset, ...] = (),
        provider_status: str | None = None,
        contract_status: str = "not_applicable",
        semantic_review_status: str = "not_applicable",
    ) -> dict[str, Any]:
        step_payload = None if step is None else step_dict(step)
        attempts = (
            []
            if step is None
            else [
                step_dict(item)
                for item in sorted(
                    (
                        item
                        for item in steps
                        if item.operation_key == step.operation_key
                        and item.episode_id == step.episode_id
                    ),
                    key=lambda item: item.created_at,
                )
            ]
        )
        return {
            "id": node_id,
            "type": node_type,
            "slot": slot,
            "label": label,
            "status": "pending" if step is None else step.status,
            "providerStatus": (
                provider_status
                if provider_status is not None
                else "pending"
                if step is None
                else step.status
            ),
            "contractStatus": contract_status,
            "semanticReviewStatus": semantic_review_status,
            "stepId": None if step is None else str(step.id),
            "promptIds": [] if step is None else prompt_ids_by_step.get(step.id, []),
            "assetIds": [str(item.id) for item in node_assets],
            "reviewIds": [] if step is None else review_ids_by_step.get(step.id, []),
            "error": None if step_payload is None else step_payload["error"],
            "nextAction": None if step_payload is None else step_payload["nextAction"],
            "availableActions": (
                [] if step_payload is None else step_payload["availableActions"]
            ),
            "attempts": attempts,
        }

    def latest_review(
        step: WorkflowStep | None,
        *,
        phase: str | None = None,
    ) -> Review | None:
        if step is None:
            return None
        matches = [item for item in reviews if item.step_id == step.id]
        if phase is not None:
            matches = [item for item in matches if item.evidence_json.get("phase") == phase]
        return max(matches, key=lambda item: item.created_at, default=None)

    def director_contract_status(step: WorkflowStep | None) -> str:
        if step is None:
            return "pending"
        if (step.error_json or {}).get("code") == "invalid_director_output":
            return "rejected"
        if step.status == "succeeded":
            return "parsed"
        return "not_available"

    def director_provider_status(step: WorkflowStep | None) -> str:
        if step is None:
            return "pending"
        # invalid_director_output说明Ark已经返回了响应，只是本地契约解析拒绝。
        if (step.error_json or {}).get("code") == "invalid_director_output":
            return "succeeded"
        return step.status

    day_step = latest_step("director:day")
    nodes = [
        step_node(
            node_id="director:day",
            node_type="director",
            slot=None,
            label="全天总导演",
            step=day_step,
            provider_status=director_provider_status(day_step),
            contract_status=director_contract_status(day_step),
        )
    ]
    episodes_by_slot = {item.slot: item for item in episodes}
    for slot_item in Slot:
        slot = slot_item.value
        episode = episodes_by_slot.get(slot)
        episode_assets = (
            ()
            if episode is None
            else tuple(item for item in assets if item.episode_id == episode.id)
        )
        director_step = latest_step(f"director:episode:{slot}")
        contract_status = director_contract_status(director_step)
        contract_review = latest_review(director_step, phase="episode_contract")
        semantic_status = (
            contract_review.decision
            if contract_review is not None
            else "approved"
            if director_step is not None
            and director_step.status == "succeeded"
            and episode is not None
            else "pending"
        )
        storyboard_steps = [
            item
            for item in steps
            if item.operation_key == "image:storyboard"
            and episode is not None
            and item.episode_id == episode.id
        ]
        storyboard_step = max(
            storyboard_steps,
            key=lambda item: item.created_at,
            default=None,
        )
        storyboards = tuple(
            item
            for item in episode_assets
            if item.role == "storyboard_panel"
            and storyboard_step is not None
            and item.producing_step_id == storyboard_step.id
        )
        video_steps = [
            item
            for item in steps
            if episode is not None
            and item.episode_id == episode.id
            and item.operation_key == "video:single_pass"
        ]
        video_step = max(
            video_steps,
            key=lambda item: item.created_at,
            default=None,
        )
        videos = tuple(
            item
            for item in episode_assets
            if item.role == "video"
            and video_step is not None
            and item.producing_step_id == video_step.id
        )
        director_node = step_node(
            node_id=f"director:{slot}",
            node_type="director",
            slot=slot,
            label=f"{slot}导演",
            step=director_step,
            provider_status=director_provider_status(director_step),
            contract_status=contract_status,
            semantic_review_status=semantic_status,
        )
        if contract_status == "rejected" or semantic_status == "rejected":
            director_node["status"] = "planning_rejected"
            director_node["nextAction"] = "查看契约或语义原因后显式重规划本时段"
        storyboard_status = (
            "approved"
            if len(storyboards) in {3, 4}
            and all(item.status in {"approved", "ready"} for item in storyboards)
            else "rejected"
            if any(item.status == "rejected" for item in storyboards)
            else "pending"
        )
        storyboard_review_status = storyboard_status
        storyboard_semantic_status = storyboard_status
        if storyboard_step is not None and storyboard_step.status in {
            "failed",
            "expired",
            "cancelled",
            "submission_unknown",
        }:
            # 生成未完成时语义审核根本没有发生。审核节点继承上游失败，
            # 但不能再谎报为pending或已经得到语义结论。
            storyboard_review_status = storyboard_step.status
            storyboard_semantic_status = "not_started"
        content_status = (
            "approved"
            if any(item.status == "ready" for item in videos)
            else "rejected"
            if any(item.status == "rejected" for item in videos)
            else "pending"
        )
        content_review_status = content_status
        content_semantic_status = content_status
        if video_step is not None and video_step.status in {
            "failed",
            "expired",
            "cancelled",
            "submission_unknown",
        }:
            content_review_status = video_step.status
            content_semantic_status = "not_started"
        nodes.extend(
            (
                director_node,
                step_node(
                    node_id=f"storyboard:{slot}",
                    node_type="storyboard",
                    slot=slot,
                    label=f"{slot}故事板",
                    step=storyboard_step,
                    node_assets=storyboards,
                ),
                {
                    **step_node(
                        node_id=f"storyboard-review:{slot}",
                        node_type="storyboard_review",
                        slot=slot,
                        label=f"{slot}故事板审核",
                        step=storyboard_step,
                        node_assets=storyboards,
                        semantic_review_status=storyboard_semantic_status,
                    ),
                    "status": storyboard_review_status,
                },
                step_node(
                    node_id=f"video:{slot}",
                    node_type="video",
                    slot=slot,
                    label=f"{slot}视频",
                    step=video_step,
                    node_assets=videos,
                ),
                {
                    **step_node(
                        node_id=f"content-review:{slot}",
                        node_type="content_review",
                        slot=slot,
                        label=f"{slot}内容审核",
                        step=video_step,
                        node_assets=videos,
                        semantic_review_status=content_semantic_status,
                    ),
                    "status": content_review_status,
                },
            )
        )
    return nodes


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
                session.execute(select(Asset).where(Asset.production_run_id == run_id)).scalars()
            )
            episode_payloads = [episode_dict(item) for item in episodes]
            contradictions = [
                issue for episode in episode_payloads for issue in episode["contradictions"]
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
                    "currentStage": _current_stage(run, episodes, steps, assets),
                }
            )
            return {
                "run": payload,
                "episodes": episode_payloads,
                "steps": [step_dict(item) for item in steps],
                "prompts": [prompt_dict(item) for item in prompts],
                "assets": [asset_dict(item) for item in assets],
                "reviews": [review_dict(item) for item in reviews],
                "workflowNodes": _workflow_nodes(
                    episodes,
                    steps,
                    prompts,
                    assets,
                    reviews,
                ),
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
                        ProductionRun.status.in_((RunStatus.READY.value, RunStatus.DELIVERED.value))
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
                        entity.entity_key
                        for script in scripts
                        for entity in script.continuity.entities
                        if entity.kind.value == "prop"
                    )
                )
                result.append(
                    RecentContentSummary(
                        content_date=run.content_date,
                        event_keys=tuple(item.event_key for item in scripts),
                        location_keys=tuple(item.location_key for item in scripts),
                        element_keys=keys,
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
        purpose: PromptPurpose,
    ) -> StoredPrompt:
        with self._sessions() as session:
            row = session.execute(
                select(PromptRecord)
                .where(
                    PromptRecord.step_id == step_id,
                    PromptRecord.purpose == purpose.value,
                )
                .order_by(PromptRecord.created_at.desc())
            ).scalar_one_or_none()
            if row is None:
                raise RecordNotFoundError(
                    f"Step {step_id}不存在purpose={purpose.value!r}的Prompt"
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
            database = session.execute(text("SELECT current_database()")).scalar_one()
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
