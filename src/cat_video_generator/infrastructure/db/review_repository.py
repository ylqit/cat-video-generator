"""资产最终审核的PostgreSQL原子事务。

本Mixin独占Review、Asset、Step与Episode的联合状态提交。它不负责生成媒体，
也不允许调用方分步写入最终决定，避免审核异常留下半状态。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ...application.ports import ReviewCommitResult
from ...domain.workflow import (
    EpisodeStatus,
    StepStatus,
    transition_episode,
    transition_step,
)
from .models import Asset, Episode, Review, WorkflowStep
from .query_repository import RecordNotFoundError


class ReviewPersistenceMixin:
    """要求宿主提供``_sessions``的审核持久化实现。"""

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
        """记录非终态诊断；最终批准或拒绝必须调用``commit_asset_review``。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
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

    def commit_asset_review(
        self,
        *,
        asset_id: uuid.UUID,
        source: str,
        decision: str,
        reason: str | None,
        warnings: list[dict[str, Any]],
        evidence: dict[str, Any],
    ) -> ReviewCommitResult:
        """锁定资产、步骤和Episode后一次提交不可覆盖的最终决定。"""

        if decision not in {"approved", "rejected"}:
            raise ValueError("最终审核决定只能是approved或rejected")
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            asset = session.execute(
                select(Asset).where(Asset.id == asset_id).with_for_update()
            ).scalar_one_or_none()
            if asset is None:
                raise RecordNotFoundError(f"Asset {asset_id} 不存在")
            if asset.producing_step_id is None:
                raise ValueError("该资产没有可审核的生产步骤")
            step = session.execute(
                select(WorkflowStep)
                .where(WorkflowStep.id == asset.producing_step_id)
                .with_for_update()
            ).scalar_one()
            episode = (
                None
                if asset.episode_id is None
                else session.execute(
                    select(Episode).where(Episode.id == asset.episode_id).with_for_update()
                ).scalar_one()
            )
            existing = (
                session.execute(
                    select(Review)
                    .where(
                        Review.asset_id == asset.id,
                        Review.decision.in_(("approved", "rejected")),
                    )
                    .order_by(Review.created_at.desc(), Review.id.desc())
                )
                .scalars()
                .first()
            )
            if existing is not None:
                if existing.decision != decision:
                    raise ValueError("资产已有相反最终审核决定，不能覆盖")
                return ReviewCommitResult(existing.id, decision, True)
            if StepStatus(step.status) is not StepStatus.AWAITING_REVIEW:
                raise ValueError("资产生产步骤当前不在等待审核状态")

            review = Review(
                step_id=step.id,
                asset_id=asset.id,
                source=source,
                decision=decision,
                reason=reason,
                warnings_json=warnings,
                evidence_json=evidence,
            )
            session.add(review)
            asset.status = (
                ("ready" if asset.role == "video" else "approved")
                if decision == "approved"
                else "rejected"
            )
            step.status = transition_step(
                StepStatus(step.status),
                (StepStatus.SUCCEEDED if decision == "approved" else StepStatus.FAILED),
            ).value
            step.completed_at = datetime.now(timezone.utc)
            if episode is not None:
                if decision == "rejected":
                    episode.status = transition_episode(
                        EpisodeStatus(episode.status),
                        EpisodeStatus.FAILED,
                    ).value
                elif asset.role == "video":
                    if asset.episode_id != episode.id:
                        raise ValueError("视频资产不属于被锁定的Episode")
                    episode.selected_video_asset_id = asset.id
                    episode.status = transition_episode(
                        EpisodeStatus(episode.status),
                        EpisodeStatus.READY,
                    ).value
            session.flush()
            return ReviewCommitResult(review.id, decision, False)

    def commit_storyboard_review(
        self,
        *,
        step_id: uuid.UUID,
        asset_ids: tuple[uuid.UUID, ...],
        source: str,
        decision: str,
        reason: str | None,
        warnings: list[dict[str, Any]],
        evidence: dict[str, Any],
    ) -> ReviewCommitResult:
        """一次锁定并批准或拒绝完整故事板，禁止出现部分面板终态。"""

        if decision not in {"approved", "rejected"}:
            raise ValueError("故事板最终审核决定只能是approved或rejected")
        if len(asset_ids) not in {3, 4} or len(set(asset_ids)) != len(asset_ids):
            raise ValueError("故事板审核必须提供3或4张互不重复的面板")
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            step = session.execute(
                select(WorkflowStep).where(WorkflowStep.id == step_id).with_for_update()
            ).scalar_one_or_none()
            if step is None:
                raise RecordNotFoundError(f"WorkflowStep {step_id} 不存在")
            assets = tuple(
                session.execute(
                    select(Asset)
                    .where(Asset.id.in_(asset_ids))
                    .order_by(Asset.created_at, Asset.id)
                    .with_for_update()
                ).scalars()
            )
            if len(assets) != len(asset_ids):
                raise RecordNotFoundError("故事板面板不完整")
            if any(
                asset.producing_step_id != step.id or asset.role != "storyboard_panel"
                for asset in assets
            ):
                raise ValueError("故事板面板必须属于同一个image:storyboard步骤")
            episode_ids = {asset.episode_id for asset in assets}
            if len(episode_ids) != 1 or None in episode_ids:
                raise ValueError("故事板面板必须属于同一个Episode")
            episode_id = next(iter(episode_ids))
            episode = session.execute(
                select(Episode).where(Episode.id == episode_id).with_for_update()
            ).scalar_one()
            existing = (
                session.execute(
                    select(Review)
                    .where(
                        Review.step_id == step.id,
                        Review.asset_id.is_(None),
                        Review.decision.in_(("approved", "rejected")),
                    )
                    .order_by(Review.created_at.desc(), Review.id.desc())
                )
                .scalars()
                .first()
            )
            if existing is not None:
                if existing.decision != decision:
                    raise ValueError("故事板已有相反最终审核决定，不能覆盖")
                return ReviewCommitResult(existing.id, decision, True)
            if StepStatus(step.status) is not StepStatus.AWAITING_REVIEW:
                raise ValueError("故事板步骤当前不在等待审核状态")

            review = Review(
                step_id=step.id,
                asset_id=None,
                source=source,
                decision=decision,
                reason=reason,
                warnings_json=warnings,
                evidence_json={
                    **evidence,
                    "orderedPanelIds": [str(asset.id) for asset in assets],
                },
            )
            session.add(review)
            for asset in assets:
                asset.status = decision
            step.status = transition_step(
                StepStatus(step.status),
                StepStatus.SUCCEEDED if decision == "approved" else StepStatus.FAILED,
            ).value
            step.completed_at = datetime.now(timezone.utc)
            if decision == "rejected":
                episode.status = transition_episode(
                    EpisodeStatus(episode.status), EpisodeStatus.FAILED
                ).value
            session.flush()
            return ReviewCommitResult(review.id, decision, False)
