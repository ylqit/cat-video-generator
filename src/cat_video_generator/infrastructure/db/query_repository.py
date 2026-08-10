"""PostgreSQL 只读投影。

本模块把核心表转换为 CLI 与 Web 共用结果，不推进状态、不编译 Prompt，
也不调用 Ark。流程图只展示当前生产内核：导演、定妆、开场锚点、视频与审核。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from ...application.ports import StoredAsset, StoredPrompt
from ...domain.contracts import (
    CURRENT_CONTRACT_VERSION,
    EpisodePlan,
    EpisodeScript,
    RecentContentSummary,
    Slot,
)
from ...domain.rendering import build_render_plan
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
    ensure_current_contract,
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


def _trace_input_bindings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """把三种Step快照投影为统一素材顺序，不在查询层猜测业务用途。"""

    input_plan = snapshot.get("input_plan")
    if isinstance(input_plan, dict) and isinstance(input_plan.get("bindings"), list):
        return [
            {
                "assetId": str(item.get("asset_id")),
                "semanticKey": item.get("semantic_key"),
                "modality": item.get("modality"),
                "providerRole": item.get("provider_role"),
                "ordinal": item.get("ordinal"),
                "sha256": item.get("sha256"),
            }
            for item in input_plan["bindings"]
            if isinstance(item, dict) and item.get("asset_id") is not None
        ]
    asset_ids = snapshot.get("reference_asset_ids")
    hashes = snapshot.get("reference_sha256")
    if not isinstance(asset_ids, list | tuple):
        return []
    hash_values = hashes if isinstance(hashes, list | tuple) else ()
    return [
        {
            "assetId": str(asset_id),
            "ordinal": index,
            "sha256": str(hash_values[index - 1]) if index <= len(hash_values) else None,
        }
        for index, asset_id in enumerate(asset_ids, 1)
    ]


def _current_stage(
    run: ProductionRun,
    episodes: tuple[Episode, ...],
    steps: tuple[WorkflowStep, ...],
) -> str:
    if "dayBrief" not in run.planning_json:
        return "dayBrief"
    if run.status == RunStatus.DRAFT.value:
        return "dayBrief"
    if run.status == RunStatus.PLANNING_REVIEW.value:
        return "script"
    statuses = {item.status for item in episodes}
    if statuses and statuses <= {"ready"}:
        return "review"
    if statuses & {"video_pending", "video_generating", "media_qc", "content_review"}:
        return "video"
    if any(item.kind == "video" for item in steps):
        return "video"
    if any(item.operation_key in {"image:look", "image:opening_anchor"} for item in steps):
        return "visual"
    return "script"


def _workflow_nodes(
    episodes: tuple[Episode, ...],
    steps: tuple[WorkflowStep, ...],
    prompts: tuple[PromptRecord, ...],
    assets: tuple[Asset, ...],
    reviews: tuple[Review, ...],
) -> list[dict[str, Any]]:
    prompt_ids: dict[uuid.UUID, list[str]] = {}
    review_ids: dict[uuid.UUID, list[str]] = {}
    for prompt in prompts:
        prompt_ids.setdefault(prompt.step_id, []).append(str(prompt.id))
    for review in reviews:
        review_ids.setdefault(review.step_id, []).append(str(review.id))

    def latest(operation_key: str, episode_id: uuid.UUID | None = None) -> WorkflowStep | None:
        matches = [
            item
            for item in steps
            if item.operation_key == operation_key
            and (episode_id is None or item.episode_id == episode_id)
        ]
        return max(matches, key=lambda item: item.created_at, default=None)

    def node(
        *,
        node_id: str,
        node_type: str,
        label: str,
        step: WorkflowStep | None,
        slot: str | None = None,
        node_assets: tuple[Asset, ...] = (),
        status: str | None = None,
        contract_status: str = "not_applicable",
        semantic_status: str = "not_applicable",
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
            "status": status or ("pending" if step is None else step.status),
            "providerStatus": "pending" if step is None else step.status,
            "contractStatus": contract_status,
            "semanticReviewStatus": semantic_status,
            "stepId": None if step is None else str(step.id),
            "promptIds": [] if step is None else prompt_ids.get(step.id, []),
            "assetIds": [str(item.id) for item in node_assets],
            "reviewIds": [] if step is None else review_ids.get(step.id, []),
            "error": None if step_payload is None else step_payload["error"],
            "nextAction": None if step_payload is None else step_payload["nextAction"],
            "availableActions": [] if step_payload is None else step_payload["availableActions"],
            "attempts": attempts,
        }

    def director_state(step: WorkflowStep | None) -> tuple[str, str]:
        if step is None:
            return "pending", "pending"
        if (step.error_json or {}).get("code") == "invalid_director_output":
            return "succeeded", "rejected"
        return step.status, "parsed" if step.status == "succeeded" else "not_available"

    day_step = latest("director:day")
    provider, contract = director_state(day_step)
    nodes = [
        {
            **node(
                node_id="director:day",
                node_type="director",
                label="全天总导演",
                step=day_step,
                contract_status=contract,
            ),
            "providerStatus": provider,
        }
    ]
    episodes_by_slot = {item.slot: item for item in episodes}
    for slot_item in Slot:
        slot = slot_item.value
        episode = episodes_by_slot.get(slot)
        episode_id = None if episode is None else episode.id
        episode_assets = tuple(
            item for item in assets if episode_id is not None and item.episode_id == episode_id
        )
        director = latest(f"director:episode:{slot}")
        provider, contract = director_state(director)
        director_node = node(
            node_id=f"director:{slot}",
            node_type="director",
            slot=slot,
            label=f"{slot}导演",
            step=director,
            contract_status=contract,
            semantic_status=("approved" if contract == "parsed" and episode else "pending"),
        )
        director_node["providerStatus"] = provider
        if contract == "rejected":
            director_node["status"] = "planning_rejected"
            director_node["nextAction"] = "查看契约原因后显式重规划本时段"
        nodes.append(director_node)
        if episode is None:
            nodes.extend(
                (
                    node(
                        node_id=f"look:{slot}",
                        node_type="look",
                        slot=slot,
                        label=f"{slot}定妆图",
                        step=None,
                    ),
                    node(
                        node_id=f"opening-anchor:{slot}",
                        node_type="opening_anchor",
                        slot=slot,
                        label=f"{slot}开场锚点",
                        step=None,
                    ),
                    node(
                        node_id=f"video:{slot}:1",
                        node_type="video",
                        slot=slot,
                        label=f"{slot}视频",
                        step=None,
                    ),
                    node(
                        node_id=f"content-review:{slot}",
                        node_type="content_review",
                        slot=slot,
                        label=f"{slot}内容审核",
                        step=None,
                    ),
                )
            )
            continue
        for operation_key, role, node_type, label in (
            ("image:look", "look_reference", "look", f"{slot}定妆图"),
            ("image:opening_anchor", "opening_anchor", "opening_anchor", f"{slot}开场锚点"),
        ):
            operation_steps = tuple(
                item
                for item in steps
                if item.operation_key == operation_key and item.episode_id == episode.id
            )
            approved_assets = tuple(
                item
                for item in episode_assets
                if item.role == role and item.status in {"approved", "ready"}
            )
            # 当前节点优先展示真正可供生产使用的批准资产；较晚的失败尝试仍完整
            # 出现在 attempts 中，但不能把一个已经可继续的视频节点伪装成 rejected。
            effective_asset = max(
                approved_assets,
                key=lambda item: item.created_at,
                default=None,
            )
            step = (
                next(
                    (
                        item
                        for item in operation_steps
                        if effective_asset is not None
                        and item.id == effective_asset.producing_step_id
                    ),
                    None,
                )
                or latest(operation_key, episode.id)
            )
            node_assets = tuple(
                item
                for item in episode_assets
                if item.role == role and step is not None and item.producing_step_id == step.id
            )
            semantic = (
                "approved"
                if any(item.status in {"approved", "ready"} for item in node_assets)
                else "rejected"
                if any(item.status == "rejected" for item in node_assets)
                else "pending"
            )
            nodes.append(
                node(
                    node_id=f"{node_type.replace('_', '-')}:{slot}",
                    node_type=node_type,
                    slot=slot,
                    label=label,
                    step=step,
                    node_assets=node_assets,
                    status=None if semantic == "pending" else semantic,
                    semantic_status=semantic,
                )
            )
        render_plan = build_render_plan(
            EpisodePlan(slot=slot_item, script=EpisodeScript.model_validate(episode.script_json))
        )
        final_video_assets: tuple[Asset, ...] = ()
        final_video_step: WorkflowStep | None = None
        for section in render_plan.sections:
            operation_key = (
                "video:single_pass" if section.order == 1 else f"video:extend:{section.order}"
            )
            step = latest(operation_key, episode.id)
            node_assets = tuple(
                item
                for item in episode_assets
                if step is not None
                and item.producing_step_id == step.id
                and item.media_type == "video"
            )
            nodes.append(
                node(
                    node_id=f"video:{slot}:{section.order}",
                    node_type="video" if section.order == 1 else "video_extension",
                    slot=slot,
                    label=(f"{slot}视频" if section.order == 1 else f"{slot}延展{section.order}"),
                    step=step,
                    node_assets=node_assets,
                )
            )
            if section.order == len(render_plan.sections):
                final_video_assets = tuple(item for item in node_assets if item.role == "video")
                final_video_step = step
        content_status = (
            "approved"
            if any(item.status == "ready" for item in final_video_assets)
            else "rejected"
            if any(item.status == "rejected" for item in final_video_assets)
            else "pending"
        )
        nodes.append(
            node(
                node_id=f"content-review:{slot}",
                node_type="content_review",
                slot=slot,
                label=f"{slot}内容审核",
                step=final_video_step,
                node_assets=final_video_assets,
                status=None if content_status == "pending" else content_status,
                semantic_status=content_status,
            )
        )
    return nodes


def required_record(
    session: Session,
    model: type[Any],
    record_id: uuid.UUID,
) -> Any:
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
            step_ids = tuple(item.id for item in steps)
            prompts = self._rows_for_steps(session, PromptRecord, step_ids)
            reviews = self._rows_for_steps(session, Review, step_ids)
            assets = tuple(
                session.execute(select(Asset).where(Asset.production_run_id == run_id)).scalars()
            )
            payload = run_dict(run)
            payload.update(
                {
                    "dayBrief": run.planning_json.get("dayBrief"),
                    "episodeDrafts": run.planning_json.get("episodeDrafts", {}),
                    "planningMetadata": run.planning_json.get("planningMetadata", {}),
                    "currentStage": _current_stage(run, episodes, steps),
                }
            )
            return {
                "run": payload,
                "episodes": [episode_dict(item) for item in episodes],
                "steps": [step_dict(item) for item in steps],
                "prompts": [prompt_dict(item) for item in prompts],
                "assets": [asset_dict(item) for item in assets],
                "reviews": [review_dict(item) for item in reviews],
                "workflowNodes": _workflow_nodes(episodes, steps, prompts, assets, reviews),
            }

    def get_planning_context(self, run_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            run = required_record(session, ProductionRun, run_id)
            ensure_current_contract(run)
            return dict(run.planning_json or {})

    @staticmethod
    def _rows_for_steps(
        session: Session,
        model: type[PromptRecord] | type[Review],
        step_ids: tuple[uuid.UUID, ...],
    ) -> tuple[Any, ...]:
        if not step_ids:
            return ()
        return tuple(
            session.execute(
                select(model).where(model.step_id.in_(step_ids)).order_by(model.created_at)
            ).scalars()
        )

    def list_run_summaries(self, limit: int, offset: int) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.execute(
                select(ProductionRun)
                .where(ProductionRun.contract_version == CURRENT_CONTRACT_VERSION)
                .order_by(ProductionRun.content_date.desc(), ProductionRun.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).scalars()
            return [run_dict(row) for row in rows]

    def list_recent_completed_summaries(
        self,
        *,
        limit: int,
    ) -> tuple[RecentContentSummary, ...]:
        if not 1 <= limit <= 6:
            raise ValueError("近期内容摘要 limit 必须在 1 至 6 之间")
        with self._sessions() as session:
            runs = tuple(
                session.execute(
                    select(ProductionRun)
                    .where(
                        ProductionRun.contract_version == CURRENT_CONTRACT_VERSION,
                        ProductionRun.status.in_((RunStatus.READY.value, RunStatus.DELIVERED.value))
                    )
                    .order_by(ProductionRun.content_date.desc(), ProductionRun.created_at.desc())
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
                result.append(
                    RecentContentSummary(
                        content_date=run.content_date,
                        event_keys=tuple(item.event_key for item in scripts),
                        location_keys=tuple(item.location_key for item in scripts),
                        summary_text=(
                            f"{run.content_date.isoformat()}主题="
                            f"{run.planning_json.get('dayBrief', {}).get('theme', '')}；"
                            f"剧情={'、'.join(item.story_text for item in scripts)}"
                        ),
                    )
                )
            return tuple(result)

    def prompt_detail(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            prompt = required_record(session, PromptRecord, prompt_id)
            step = required_record(session, WorkflowStep, prompt.step_id)
            ensure_current_contract(
                required_record(session, ProductionRun, step.production_run_id)
            )
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
            step = required_record(session, WorkflowStep, step_id)
            ensure_current_contract(
                required_record(session, ProductionRun, step.production_run_id)
            )
            row = session.execute(
                select(PromptRecord)
                .where(PromptRecord.step_id == step_id, PromptRecord.purpose == purpose.value)
                .order_by(PromptRecord.created_at.desc())
            ).scalar_one_or_none()
            if row is None:
                raise RecordNotFoundError(
                    f"Step {step_id} 不存在 purpose={purpose.value!r} 的 Prompt"
                )
            return stored_prompt(row)

    def asset_detail(self, asset_id: uuid.UUID) -> StoredAsset:
        with self._sessions() as session:
            asset = required_record(session, Asset, asset_id)
            if asset.production_run_id is not None:
                ensure_current_contract(
                    required_record(session, ProductionRun, asset.production_run_id)
                )
            return stored_asset(asset)

    def episode_detail(self, episode_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            episode = required_record(session, Episode, episode_id)
            ensure_current_contract(
                required_record(session, ProductionRun, episode.production_run_id)
            )
            return episode_dict(episode)

    def step_detail(self, step_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            step = required_record(session, WorkflowStep, step_id)
            ensure_current_contract(
                required_record(session, ProductionRun, step.production_run_id)
            )
            return step_dict(step)

    def step_trace(self, step_id: uuid.UUID) -> dict[str, Any]:
        """返回节点追踪投影；大正文只在打开抽屉时读取，不膨胀Run Graph。"""

        with self._sessions() as session:
            current = required_record(session, WorkflowStep, step_id)
            ensure_current_contract(
                required_record(session, ProductionRun, current.production_run_id)
            )
            attempt_query = select(WorkflowStep).where(
                WorkflowStep.production_run_id == current.production_run_id,
                WorkflowStep.operation_key == current.operation_key,
            )
            if current.episode_id is None:
                attempt_query = attempt_query.where(WorkflowStep.episode_id.is_(None))
            else:
                attempt_query = attempt_query.where(WorkflowStep.episode_id == current.episode_id)
            attempts = tuple(
                session.execute(attempt_query.order_by(WorkflowStep.attempt)).scalars()
            )
            attempt_ids = tuple(item.id for item in attempts)
            prompts = self._rows_for_steps(session, PromptRecord, attempt_ids)
            reviews = self._rows_for_steps(session, Review, attempt_ids)
            assets = (
                ()
                if not attempt_ids
                else tuple(
                    session.execute(
                        select(Asset)
                        .where(Asset.producing_step_id.in_(attempt_ids))
                        .order_by(Asset.created_at)
                    ).scalars()
                )
            )
            snapshot = dict(current.input_snapshot_json or {})
            provider_output = snapshot.get("provider_output")
            normalized_output = snapshot.get("normalized_output")
            input_summary = {
                key: value
                for key, value in snapshot.items()
                if key
                not in {
                    "provider_output",
                    "normalized_output",
                    "normalization_warnings",
                    "response_id",
                    "request_hash",
                }
            }
            return {
                "step": step_dict(current),
                "inputSummary": input_summary,
                "actualPrompts": [prompt_dict(item, full=True) for item in prompts],
                "providerOutput": provider_output,
                "normalizedOutput": normalized_output,
                "effectiveOutput": normalized_output or provider_output,
                "normalizationWarnings": snapshot.get("normalization_warnings", []),
                "inputBindings": _trace_input_bindings(snapshot),
                "assets": [asset_dict(item) for item in assets],
                "reviews": [review_dict(item) for item in reviews],
                "attempts": [step_dict(item) for item in attempts],
            }

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
            return self._delivery_dict(
                session,
                required_record(session, DeliveryPackage, package_id),
            )

    @staticmethod
    def _delivery_dict(session: Session, package: DeliveryPackage) -> dict[str, Any]:
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
