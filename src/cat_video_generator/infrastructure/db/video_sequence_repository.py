"""非破坏性视频版本的PostgreSQL事务。

本模块拥有版本号分配、EDL校验和正式版本选择；它不生成媒体，也不解释前端时间轴。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from ...application.ports import StoredVideoSequence, VideoSequenceSelectionResult
from ...domain.contracts import Slot
from ...domain.rendering import (
    SequenceStatus,
    VideoSequencePlan,
    transition_sequence,
)
from ...domain.workflow import RunStatus, transition_run
from .models import Asset, Episode, ProductionRun, VideoSequence
from .query_repository import RecordNotFoundError, required_record


def stored_video_sequence(row: VideoSequence) -> StoredVideoSequence:
    return StoredVideoSequence(
        id=row.id,
        episode_id=row.episode_id,
        revision=row.revision,
        parent_sequence_id=row.parent_sequence_id,
        base_asset_id=row.base_asset_id,
        rendered_asset_id=row.rendered_asset_id,
        status=SequenceStatus(row.status),
        plan=VideoSequencePlan(duration_ms=row.duration_ms, clips=row.clips_json),
        audio_policy=row.audio_policy,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class VideoSequencePersistenceMixin:
    """要求宿主提供``_sessions``的版本持久化实现。"""

    def next_video_sequence_revision(self, episode_id: uuid.UUID) -> int:
        with self._sessions() as session:  # type: ignore[attr-defined]
            required_record(session, Episode, episode_id)
            value = session.execute(
                select(func.coalesce(func.max(VideoSequence.revision), 0)).where(
                    VideoSequence.episode_id == episode_id
                )
            ).scalar_one()
            return int(value) + 1

    def create_video_sequence(
        self,
        *,
        episode_id: uuid.UUID,
        parent_sequence_id: uuid.UUID | None,
        base_asset_id: uuid.UUID,
        rendered_asset_id: uuid.UUID | None = None,
        status: SequenceStatus,
        plan: VideoSequencePlan,
    ) -> StoredVideoSequence:
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            # 锁定Episode后再分配revision，避免两个编辑请求得到相同版本号。
            episode = session.execute(
                select(Episode).where(Episode.id == episode_id).with_for_update()
            ).scalar_one_or_none()
            if episode is None:
                raise RecordNotFoundError(f"Episode {episode_id} 不存在")
            base = required_record(session, Asset, base_asset_id)
            if base.episode_id != episode_id or base.media_type != "video":
                raise ValueError("视频版本的基础资产必须属于同一Episode")
            rendered = base if rendered_asset_id == base_asset_id else None
            if rendered_asset_id is not None and rendered is None:
                rendered = required_record(session, Asset, rendered_asset_id)
                if rendered.episode_id != episode_id or rendered.media_type != "video":
                    raise ValueError("视频版本的渲染资产必须属于同一Episode")
            if rendered_asset_id is not None:
                existing = session.execute(
                    select(VideoSequence).where(
                        VideoSequence.episode_id == episode_id,
                        VideoSequence.rendered_asset_id == rendered_asset_id,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return stored_video_sequence(existing)
            parent = None
            if parent_sequence_id is not None:
                parent = required_record(session, VideoSequence, parent_sequence_id)
                if parent.episode_id != episode_id:
                    raise ValueError("父视频版本不属于同一Episode")
            revision = int(
                session.execute(
                    select(func.coalesce(func.max(VideoSequence.revision), 0)).where(
                        VideoSequence.episode_id == episode_id
                    )
                ).scalar_one()
            ) + 1
            row = VideoSequence(
                episode_id=episode_id,
                revision=revision,
                parent_sequence_id=parent_sequence_id,
                base_asset_id=base_asset_id,
                rendered_asset_id=rendered_asset_id,
                status=status.value,
                duration_ms=plan.duration_ms,
                audio_policy="preserve_original",
                clips_json=[item.model_dump(mode="json") for item in plan.clips],
            )
            session.add(row)
            session.flush()
            return stored_video_sequence(row)

    def get_video_sequence(self, sequence_id: uuid.UUID) -> StoredVideoSequence:
        with self._sessions() as session:  # type: ignore[attr-defined]
            row = session.get(VideoSequence, sequence_id)
            if row is None:
                raise RecordNotFoundError(f"VideoSequence {sequence_id} 不存在")
            return stored_video_sequence(row)

    def list_video_sequences(self, episode_id: uuid.UUID) -> tuple[StoredVideoSequence, ...]:
        with self._sessions() as session:  # type: ignore[attr-defined]
            rows = session.execute(
                select(VideoSequence)
                .where(VideoSequence.episode_id == episode_id)
                .order_by(VideoSequence.revision)
            ).scalars()
            return tuple(stored_video_sequence(item) for item in rows)

    def update_video_sequence(
        self,
        *,
        sequence_id: uuid.UUID,
        status: SequenceStatus,
        rendered_asset_id: uuid.UUID | None = None,
        plan: VideoSequencePlan | None = None,
    ) -> StoredVideoSequence:
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            row = session.execute(
                select(VideoSequence).where(VideoSequence.id == sequence_id).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise RecordNotFoundError(f"VideoSequence {sequence_id} 不存在")
            row.status = transition_sequence(SequenceStatus(row.status), status).value
            if rendered_asset_id is not None:
                asset = required_record(session, Asset, rendered_asset_id)
                if asset.episode_id != row.episode_id or asset.media_type != "video":
                    raise ValueError("渲染资产必须属于视频版本的Episode")
                row.rendered_asset_id = rendered_asset_id
            if plan is not None:
                row.duration_ms = plan.duration_ms
                row.clips_json = [item.model_dump(mode="json") for item in plan.clips]
            session.flush()
            return stored_video_sequence(row)

    def select_video_sequence(
        self,
        sequence_id: uuid.UUID,
        *,
        revoke_confirmed_outcome: bool,
        keep_confirmed_outcome: bool,
    ) -> VideoSequenceSelectionResult:
        if revoke_confirmed_outcome and keep_confirmed_outcome:
            raise ValueError("结果卡处理方式只能选择一种")
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            row = session.execute(
                select(VideoSequence).where(VideoSequence.id == sequence_id).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise RecordNotFoundError(f"VideoSequence {sequence_id} 不存在")
            if SequenceStatus(row.status) is not SequenceStatus.APPROVED:
                raise ValueError("只有已批准的视频版本可以成为Episode正式视频")
            if row.rendered_asset_id is None:
                raise ValueError("视频版本没有可选择的渲染资产")
            episode = session.execute(
                select(Episode).where(Episode.id == row.episode_id).with_for_update()
            ).scalar_one()
            run = session.execute(
                select(ProductionRun)
                .where(ProductionRun.id == episode.production_run_id)
                .with_for_update()
            ).scalar_one()
            slot = Slot(episode.slot)
            outcomes = dict(run.planning_json.get("acceptedOutcomes", {}))
            confirmed = slot.value in outcomes
            if confirmed and not (revoke_confirmed_outcome or keep_confirmed_outcome):
                raise ValueError("该时段结果卡已确认，必须明确保留事实或撤销确认")
            if RunStatus(run.status) is RunStatus.DELIVERED:
                raise ValueError("已交付Run不可切换正式视频版本；请新建Run保留交付不可变性")

            # 正式视频与结果卡属于同一条叙事事实链。两者必须在同一事务中切换，
            # 否则进程中断会留下“新视频+旧结果卡”这种无法判断的恢复状态。
            episode.selected_video_asset_id = row.rendered_asset_id
            if confirmed and revoke_confirmed_outcome:
                outcomes.pop(slot.value)
                stale_slots = set(run.planning_json.get("staleSlots", []))
                stale_slots.update(
                    item.value for item in Slot if item.sort_order > slot.sort_order
                )
                run.planning_json = {
                    **run.planning_json,
                    "acceptedOutcomes": outcomes,
                    "staleSlots": sorted(stale_slots),
                }
                if RunStatus(run.status) is RunStatus.READY:
                    run.status = transition_run(
                        RunStatus.READY,
                        RunStatus.GENERATING,
                    ).value
            session.flush()
            return VideoSequenceSelectionResult(
                sequence=stored_video_sequence(row),
                outcome_kept=bool(confirmed and keep_confirmed_outcome),
                outcome_revoked=bool(confirmed and revoke_confirmed_outcome),
            )
