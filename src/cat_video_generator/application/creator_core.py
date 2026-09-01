"""Application policy for the minimal creator workflow."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from ..domain.creator_core import (
    GenerationSnapshotDraft,
    assert_project_reference_authority,
    generation_input_hash,
)


class CreatorRepository(Protocol):
    def list_projects(self) -> list[dict[str, Any]]: ...
    def canon_options(self) -> dict[str, Any]: ...
    def create_project(self, payload: Any) -> dict[str, Any]: ...
    def get_state(self, project_id: uuid.UUID) -> dict[str, Any]: ...
    def asset_library(self, project_id: uuid.UUID) -> list[dict[str, Any]]: ...
    def update_state(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]: ...
    def save_story(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]: ...
    def list_shots(self, project_id: uuid.UUID) -> list[dict[str, Any]]: ...
    def replace_shots(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]: ...
    def update_shot(
        self, shot_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]: ...
    def project_id_for_shot(self, shot_id: uuid.UUID) -> uuid.UUID: ...
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
    ) -> dict[str, Any]: ...
    def submit_snapshot(
        self,
        snapshot_id: uuid.UUID,
        *,
        idempotency_key: str,
        input_hash: str,
        accepted_estimated_cost_micros: int,
    ) -> dict[str, Any]: ...
    def decide_asset(self, asset_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...
    def select_video(
        self, shot_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]: ...
    def diagnostics(self, project_id: uuid.UUID) -> dict[str, Any]: ...
    def list_tasks(self, project_id: uuid.UUID | None = None) -> list[dict[str, Any]]: ...
    def timeline(self, project_id: uuid.UUID) -> dict[str, Any]: ...
    def save_timeline(
        self, project_id: uuid.UUID, *, expected_version: int, clips: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


class CreatorCoreService:
    """Owns flexible creative parsing and strict paid-input confirmation policy."""

    def __init__(
        self,
        repository: CreatorRepository,
        *,
        provider_configs: dict[str, dict[str, Any]] | None = None,
        estimated_costs_micros: dict[str, int | None] | None = None,
    ) -> None:
        self._repository = repository
        self._provider_configs = provider_configs or {}
        self._estimated_costs_micros = estimated_costs_micros or {}

    def create_project(self, payload: Any) -> dict[str, Any]:
        assert_project_reference_authority(payload.references)
        return self._repository.create_project(payload)

    def list_projects(self) -> list[dict[str, Any]]:
        return self._repository.list_projects()

    def canon_options(self) -> dict[str, Any]:
        return self._repository.canon_options()

    def get_state(self, project_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_state(project_id)

    def asset_library(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        return self._repository.asset_library(project_id)

    def update_state(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]:
        if payload.reference_bindings is not None:
            assert_project_reference_authority(payload.reference_bindings)
        return self._repository.update_state(
            project_id, expected_version=expected_version, payload=payload
        )

    def create_story_candidates(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        prompt = (
            "你是原创一人一猫治愈短视频编剧。请根据以下创作要求生成1至5个完整、"
            "彼此明显不同且可以直接编辑的故事候选。每个候选只需要标题、可选摘要和"
            "完整正文；不要输出评分、数据库字段或系统说明。\n\n创作要求：\n"
            f"{payload.brief_body}"
        )
        draft = GenerationSnapshotDraft(
            kind="story_text",
            promptText=prompt,
            orderedReferences=[],
            providerConfig=self._provider_config(
                "story_text",
                {
                    "requestedCandidateCount": payload.requested_count,
                },
            ),
        )
        return self._store_snapshot(project_id=project_id, creator_shot_id=None, draft=draft)

    def save_story(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]:
        return self._repository.save_story(
            project_id, expected_version=expected_version, payload=payload
        )

    def list_shots(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        return self._repository.list_shots(project_id)

    def replace_shots(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]:
        return self._repository.replace_shots(
            project_id, expected_version=expected_version, payload=payload
        )

    def update_shot(
        self, shot_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]:
        return self._repository.update_shot(
            shot_id, expected_version=expected_version, payload=payload
        )

    def create_snapshot(
        self, shot_id: uuid.UUID, payload: GenerationSnapshotDraft
    ) -> dict[str, Any]:
        return self._store_snapshot(
            project_id=self._repository.project_id_for_shot(shot_id),
            creator_shot_id=shot_id,
            draft=payload,
        )

    def _store_snapshot(
        self,
        *,
        project_id: uuid.UUID,
        creator_shot_id: uuid.UUID | None,
        draft: GenerationSnapshotDraft,
    ) -> dict[str, Any]:
        provider_config = self._provider_config(draft.kind, draft.provider_config)
        effective_draft = draft.model_copy(update={"provider_config": provider_config})
        return self._repository.create_snapshot(
            project_id=project_id,
            creator_shot_id=creator_shot_id,
            kind=draft.kind,
            prompt_text=draft.prompt_text,
            ordered_references=[
                item.model_dump(by_alias=True, mode="json") for item in draft.ordered_references
            ],
            provider_config=provider_config,
            input_hash=generation_input_hash(effective_draft),
            estimated_cost_micros=self._estimated_costs_micros.get(draft.kind),
        )

    def _provider_config(self, kind: str, requested: dict[str, Any]) -> dict[str, Any]:
        """Keep creative parameters while pinning server-owned transport choices."""

        return {**requested, **self._provider_configs.get(kind, {})}

    def submit_snapshot(
        self,
        snapshot_id: uuid.UUID,
        *,
        idempotency_key: str,
        input_hash: str,
        accepted_estimated_cost_micros: int,
    ) -> dict[str, Any]:
        return self._repository.submit_snapshot(
            snapshot_id,
            idempotency_key=idempotency_key,
            input_hash=input_hash,
            accepted_estimated_cost_micros=accepted_estimated_cost_micros,
        )

    def decide_asset(self, asset_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.decide_asset(asset_id, payload)

    def select_video(
        self, shot_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]:
        return self._repository.select_video(
            shot_id, expected_version=expected_version, payload=payload
        )

    def diagnostics(self, project_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.diagnostics(project_id)

    def list_tasks(self, project_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        return self._repository.list_tasks(project_id)

    def timeline(self, project_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.timeline(project_id)

    def save_timeline(
        self, project_id: uuid.UUID, *, expected_version: int, clips: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self._repository.save_timeline(
            project_id, expected_version=expected_version, clips=clips
        )
