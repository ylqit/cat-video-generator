from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from cat_video_generator.application.ports import (
    DirectorResult,
    StoredAsset,
    StoredProject,
    StoredScene,
    StoredShot,
    StoredStep,
    StoredVisualProfileRevision,
)
from cat_video_generator.application.shot_queue import (
    ProjectEditingService,
    RevisionConflictError,
    ShotProductionService,
)
from cat_video_generator.domain.contracts import (
    AnchorMode,
    ReferenceBinding,
    ReferenceRole,
    ReferenceTarget,
    ReferenceUsage,
    SceneDraft,
    SceneLookUsage,
    ShotAssistAnalysis,
    ShotAssistPatch,
    ShotCardDraft,
    ShotPromptContext,
    VisualProfileDraft,
)
from cat_video_generator.domain.prompts import compile_shot_video_prompt
from cat_video_generator.domain.rendering import build_shot_input_plan
from cat_video_generator.domain.shot_assistance import (
    analyze_shot_draft,
    apply_shot_assist_patch,
)
from cat_video_generator.domain.workflow import (
    RunStatus,
    SceneStatus,
    ShotStatus,
    StepKind,
    StepStatus,
)
from cat_video_generator.infrastructure.ark.gateway import _analysis_preview_data_url


def test_local_inspection_only_checks_numbered_subshot_structure() -> None:
    short = analyze_shot_draft(
        ShotCardDraft(
            title="猫咪观察",
            direction=(
                "1. 中景固定，猫咪看向人物，人物打开柜门。\n"
                "2. 近景轻微跟随，猫咪靠近水桶，人物扶稳水桶，"
                "猫咪停在旁边，保留室内环境音，画面稳定收尾。"
            ),
            durationSeconds=9,
        )
    )
    long = analyze_shot_draft(
        ShotCardDraft(
            title="猫咪观察",
            direction="1. 中景，猫咪看向人物。\n2. 近景，人物放下水桶。",
            durationSeconds=15,
        )
    )

    assert (short.suggested_subshot_min, short.suggested_subshot_max) == (2, 4)
    assert short.detected_subshot_count == 2
    assert short.has_sound is None
    assert short.has_stable_ending is None
    assert short.action_count is None
    assert short.camera_move_count is None
    assert (long.suggested_subshot_min, long.suggested_subshot_max) == (2, 4)
    assert long.findings == []

    multiple = analyze_shot_draft(
        ShotCardDraft(
            title="两个事件",
            direction="1. 猫咪观察水桶。\n2. 人物放下水桶，然后又转而打开柜门。",
            durationSeconds=10,
        )
    )
    assert multiple.findings == []


def test_shot_assist_contract_supports_field_level_patch_and_strict_output() -> None:
    asset_id = uuid.uuid4()
    analysis = ShotAssistAnalysis.model_validate(
        {
            "actionDensityAssessment": "当前动作略密",
            "pacingPlan": {
                "recommendedDurationSeconds": 12,
                "rationale": "保留猫咪观察与人物配合",
                "beats": [
                    {"ordinal": 1, "description": "建立人猫位置", "rhythm": "brief"},
                    {"ordinal": 2, "description": "猫咪完成观察", "rhythm": "expanded"},
                ],
            },
            "recommendedSceneLookUsage": "appearance_only",
            "recommendedAnchorMode": "text_only",
            "referenceDecisions": [
                {
                    "assetId": str(asset_id),
                    "decision": "keep",
                    "recommendedRole": "identity",
                    "reason": "用于猫咪身份",
                }
            ],
            "continuity": {
                "previousIssues": [],
                "nextIssues": ["下一片段不要重复拿取水桶"],
                "recommendation": "以水桶已放稳作为结束状态",
            },
            "promptRisks": ["主要运镜过多"],
            "patch": {
                "durationSeconds": 12,
                "sceneLookUsage": "appearance_only",
            },
        }
    )

    assert analysis.patch.duration_seconds == 12
    assert analysis.recommended_scene_look_usage is SceneLookUsage.APPEARANCE_ONLY
    assert analysis.reference_decisions[0].asset_id == asset_id

    with pytest.raises(ValidationError, match="at least one"):
        ShotAssistPatch()

    no_rewrite = ShotAssistAnalysis.model_validate(
        {**analysis.model_dump(mode="json", by_alias=True), "patch": None}
    )
    assert no_rewrite.patch is None


def test_assist_patch_changes_only_selected_fields_and_revalidates_strategy() -> None:
    original = ShotCardDraft(
        title="原片段",
        direction="1. 中景，猫咪观察人物。\n2. 近景，稳定收尾。",
        durationSeconds=10,
        sceneLookUsage="appearance_only",
    )

    updated = apply_shot_assist_patch(
        original,
        ShotAssistPatch(durationSeconds=12, sceneLookUsage="full_reference"),
    )

    assert updated.title == original.title
    assert updated.direction == original.direction
    assert updated.duration_seconds == 12
    assert updated.scene_look_usage is SceneLookUsage.FULL_REFERENCE

    with pytest.raises(ValidationError, match="derive_anchor"):
        apply_shot_assist_patch(
            original,
            ShotAssistPatch(sceneLookUsage="derive_anchor"),
        )


def test_video_prompt_uses_confirmed_body_without_system_story_rewrite() -> None:
    prompt = compile_shot_video_prompt(
        ShotPromptContext(
            project_title="湖泊的鱼",
            scene_title="出发前",
            scene_text="人物和猫咪整理装备。",
            shot_title="猫咪观察",
            direction="1. 中景，猫咪观察柜门。\n2. 近景，人物放下水桶并稳定收尾。",
            duration_seconds=14,
        ),
        build_shot_input_plan(
            resolution="720p",
            duration_seconds=14,
            anchor=None,
        ),
        binding_descriptions=(),
    )

    assert "【定性节奏】" not in prompt.text
    assert "不由系统补写剧情、动作、空间关系、节奏或声音" in prompt.text
    assert "1. 中景，猫咪观察柜门" in prompt.text
    assert "第1秒" not in prompt.text


def test_multimodal_analysis_uses_bounded_preview_without_changing_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "large.png"
    Image.new("RGB", (2200, 1600), color=(120, 160, 90)).save(source)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    data_url = _analysis_preview_data_url(source)

    preview = Image.open(BytesIO(base64.b64decode(data_url.split(",", 1)[1])))
    assert data_url.startswith("data:image/jpeg;base64,")
    assert max(preview.size) <= 1280
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


def _analysis_payload() -> dict[str, object]:
    return {
        "actionDensityAssessment": "动作密度适中",
        "pacingPlan": {
            "recommendedDurationSeconds": 12,
            "rationale": "保留观察和反馈",
            "beats": [
                {"ordinal": 1, "description": "建立位置", "rhythm": "brief"},
                {"ordinal": 2, "description": "完成互动", "rhythm": "expanded"},
            ],
        },
        "recommendedSceneLookUsage": "appearance_only",
        "recommendedAnchorMode": "text_only",
        "referenceDecisions": [],
        "continuity": {
            "previousIssues": [],
            "nextIssues": [],
            "recommendation": "保持水桶已放稳",
        },
        "promptRisks": [],
        "patch": {"durationSeconds": 12},
    }


class _AssistGateway:
    analysis_model = "fake-multimodal"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def analyze_structured(self, **kwargs: object) -> DirectorResult:
        self.calls.append(kwargs)
        return DirectorResult(
            payload=_analysis_payload(),
            response_id="response-1",
            model=self.analysis_model,
            request_hash="f" * 64,
        )


class _AssistRepository:
    def __init__(self, tmp_path: Path) -> None:
        project_id = uuid.uuid4()
        scene_id = uuid.uuid4()
        image_path = tmp_path / "scene-look.png"
        image_path.write_bytes(b"image")
        self.project = StoredProject(
            id=project_id,
            title="湖泊的鱼",
            content_date=date(2026, 8, 13),
            status=RunStatus.ACTIVE,
        )
        self.scene = StoredScene(
            id=scene_id,
            project_id=project_id,
            order=1,
            draft=SceneDraft(title="出发前", sourceText="人物和猫咪准备出门。"),
            status=SceneStatus.READY,
        )
        self.shots = tuple(
            StoredShot(
                id=uuid.uuid4(),
                scene_id=scene_id,
                project_id=project_id,
                order=index,
                draft=ShotCardDraft(
                    title=title,
                    direction=(
                        "1. 中景，猫咪观察人物。\n"
                        "2. 近景，人物完成动作并稳定收尾，保留环境音。"
                    ),
                    durationSeconds=12,
                ),
                status=ShotStatus.READY,
                draft_revision=3 if index == 2 else 1,
            )
            for index, title in enumerate(("上一片段", "当前片段", "下一片段"), 1)
        )
        self.asset = StoredAsset(
            id=uuid.uuid4(),
            project_id=project_id,
            scene_id=scene_id,
            shot_card_id=None,
            step_id=None,
            role="scene_look",
            media_type="image",
            scope="scene",
            status="approved",
            path=image_path,
            sha256="a" * 64,
            metadata={},
            semantic_key="scene:look",
        )
        self.assets = {self.asset.id: self.asset}
        self.profile = StoredVisualProfileRevision(
            id=uuid.uuid4(),
            project_id=project_id,
            revision=1,
            profile_hash="b" * 64,
            source_profile_id="canon-v1",
            draft=VisualProfileDraft(),
        )
        self.step: StoredStep | None = None

    def get_shot(self, shot_id: uuid.UUID) -> StoredShot:
        return next(item for item in self.shots if item.id == shot_id)

    def get_scene(self, scene_id: uuid.UUID) -> StoredScene:
        assert scene_id == self.scene.id
        return self.scene

    def get_project(self, project_id: uuid.UUID) -> StoredProject:
        assert project_id == self.project.id
        return self.project

    def get_visual_profile(self, project_id: uuid.UUID) -> StoredVisualProfileRevision:
        assert project_id == self.project.id
        return self.profile

    def list_shots(self, scene_id: uuid.UUID) -> tuple[StoredShot, ...]:
        assert scene_id == self.scene.id
        return self.shots

    def list_assets(self, **_kwargs: object) -> tuple[StoredAsset, ...]:
        return ()

    def get_asset(self, asset_id: uuid.UUID) -> StoredAsset:
        return self.assets[asset_id]

    def list_steps(self, **_kwargs: object) -> tuple[StoredStep, ...]:
        return () if self.step is None else (self.step,)

    def next_attempt(self, **_kwargs: object) -> int:
        return 1

    def create_step_with_prompt(self, **kwargs: object) -> tuple[StoredStep, object]:
        self.step = StoredStep(
            id=uuid.uuid4(),
            project_id=self.project.id,
            scene_id=self.scene.id,
            shot_card_id=self.shots[1].id,
            kind=StepKind.DIRECTOR,
            status=StepStatus.PENDING,
            attempt=1,
            operation_key="director:shot-assistance",
            input_snapshot=kwargs["input_snapshot"],  # type: ignore[arg-type]
            model=str(kwargs["model"]),
        )
        return self.step, object()

    def update_step(self, step_id: uuid.UUID, **kwargs: object) -> StoredStep:
        assert self.step is not None and step_id == self.step.id
        self.step = replace(
            self.step,
            status=kwargs["status"],  # type: ignore[arg-type]
            input_snapshot=kwargs.get("input_snapshot", self.step.input_snapshot),  # type: ignore[arg-type]
            error=kwargs.get("error", self.step.error),  # type: ignore[arg-type]
        )
        return self.step

    def get_step(self, step_id: uuid.UUID) -> StoredStep:
        assert self.step is not None and step_id == self.step.id
        return self.step

    def accept_shot_assistance(self, **kwargs: object) -> StoredShot:
        shot = self.shots[1]
        assert kwargs["source_draft_revision"] == shot.draft_revision
        updated = replace(
            shot,
            draft=apply_shot_assist_patch(
                shot.draft,
                kwargs["patch"],  # type: ignore[arg-type]
            ),
            draft_revision=shot.draft_revision + 1,
        )
        self.shots = (self.shots[0], updated, self.shots[2])
        return updated


def test_shot_assistance_requires_explicit_payment_and_preserves_saved_revision(
    tmp_path: Path,
) -> None:
    repository = _AssistRepository(tmp_path)
    repository.scene = replace(
        repository.scene,
        selected_look_asset_id=repository.asset.id,
    )
    gateway = _AssistGateway()
    service = ProjectEditingService(
        repository=repository,  # type: ignore[arg-type]
        director=gateway,  # type: ignore[arg-type]
        provider_name="fake",
    )
    shot = repository.shots[1]

    with pytest.raises(ValueError, match="explicit"):
        service.assist_shot(
            shot.id,
            source_draft_revision=shot.draft_revision,
            candidate_asset_ids=(repository.asset.id,),
            allow_paid_generation=False,
        )
    assert gateway.calls == []

    result = service.assist_shot(
        shot.id,
        source_draft_revision=shot.draft_revision,
        candidate_asset_ids=(repository.asset.id,),
        allow_paid_generation=True,
    )

    assert result.analysis.patch.duration_seconds == 12
    assert repository.get_shot(shot.id).draft_revision == 3
    assert repository.step is not None
    assert repository.step.input_snapshot["sourceDraftRevision"] == 3
    assert repository.step.input_snapshot["providerOutput"] == _analysis_payload()
    assert gateway.calls[0]["image_paths"] == (repository.asset.path,)
    assert "上一片段" in str(gateway.calls[0]["prompt"])
    assert "下一片段" in str(gateway.calls[0]["prompt"])

    with pytest.raises(RevisionConflictError):
        service.assist_shot(
            shot.id,
            source_draft_revision=2,
            candidate_asset_ids=(),
            allow_paid_generation=True,
        )


def test_shot_assistance_uses_canonical_context_order_and_rejects_unrelated_images(
    tmp_path: Path,
) -> None:
    repository = _AssistRepository(tmp_path)
    repository.scene = replace(
        repository.scene,
        selected_look_asset_id=repository.asset.id,
    )

    def add_image(name: str, *, role: str, semantic_key: str) -> StoredAsset:
        path = tmp_path / f"{name}.png"
        path.write_bytes(name.encode())
        asset = StoredAsset(
            id=uuid.uuid4(),
            project_id=repository.project.id,
            scene_id=None,
            shot_card_id=None,
            step_id=None,
            role=role,
            media_type="image",
            scope="project",
            status="approved",
            path=path,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            metadata={},
            semantic_key=semantic_key,
        )
        repository.assets[asset.id] = asset
        return asset

    anchor = add_image("anchor", role="shot_anchor", semantic_key="shot:anchor")
    prop = add_image("prop", role="reference", semantic_key="prop:basket")
    canon = add_image("canon", role="canon", semantic_key="person:headshot")
    unrelated = add_image("unrelated", role="reference", semantic_key="prop:other")
    current = repository.shots[1]
    current = replace(
        current,
        selected_anchor_asset_id=anchor.id,
        draft=current.draft.model_copy(
            update={
                "reference_bindings": [
                    ReferenceBinding(
                        assetId=prop.id,
                        usage=ReferenceUsage.GENERATION_REFERENCE,
                        role=ReferenceRole.PROP,
                        applyTo=ReferenceTarget.BOTH,
                    )
                ]
            }
        ),
    )
    repository.shots = (repository.shots[0], current, repository.shots[2])
    repository.project = replace(
        repository.project,
        default_reference_bindings=(
            ReferenceBinding(
                assetId=canon.id,
                usage=ReferenceUsage.GENERATION_REFERENCE,
                role=ReferenceRole.IDENTITY,
                applyTo=ReferenceTarget.BOTH,
            ),
        ),
    )
    gateway = _AssistGateway()
    service = ProjectEditingService(
        repository=repository,  # type: ignore[arg-type]
        director=gateway,  # type: ignore[arg-type]
        provider_name="fake",
    )

    service.assist_shot(
        current.id,
        source_draft_revision=current.draft_revision,
        candidate_asset_ids=(
            canon.id,
            repository.asset.id,
            prop.id,
            anchor.id,
        ),
        allow_paid_generation=True,
    )

    assert gateway.calls[0]["image_paths"] == (
        anchor.path,
        prop.path,
        repository.asset.path,
        canon.path,
    )
    assert repository.step is not None
    assert [
        item["assetId"] for item in repository.step.input_snapshot["candidateAssets"]
    ] == [str(anchor.id), str(prop.id), str(repository.asset.id), str(canon.id)]

    repository.step = None
    with pytest.raises(ValueError, match="current shot input context"):
        service.assist_shot(
            current.id,
            source_draft_revision=current.draft_revision,
            candidate_asset_ids=(unrelated.id,),
            allow_paid_generation=True,
        )
    assert len(gateway.calls) == 1


def test_shot_assistance_accepts_only_selected_provider_fields(tmp_path: Path) -> None:
    repository = _AssistRepository(tmp_path)
    service = ProjectEditingService(
        repository=repository,  # type: ignore[arg-type]
        director=_AssistGateway(),  # type: ignore[arg-type]
        provider_name="fake",
    )
    shot = repository.shots[1]
    result = service.assist_shot(
        shot.id,
        source_draft_revision=shot.draft_revision,
        candidate_asset_ids=(),
        allow_paid_generation=True,
    )

    with pytest.raises(ValueError, match="实际提出"):
        service.accept_shot_assistance(
            result.step_id,
            source_draft_revision=shot.draft_revision,
            patch=ShotAssistPatch(title="LLM没有提出这个标题"),
        )

    updated = service.accept_shot_assistance(
        result.step_id,
        source_draft_revision=shot.draft_revision,
        patch=ShotAssistPatch(durationSeconds=12),
    )

    assert updated.draft_revision == 4
    assert updated.draft.duration_seconds == 12
    assert updated.draft.title == shot.draft.title
    with pytest.raises(RevisionConflictError):
        service.accept_shot_assistance(
            result.step_id,
            source_draft_revision=updated.draft_revision,
            patch=ShotAssistPatch(title="LLM没有提出这个标题"),
        )


def test_prompt_preview_returns_free_rules_pacing_and_source_layers(tmp_path: Path) -> None:
    repository = _AssistRepository(tmp_path)
    service = ShotProductionService(
        repository=repository,  # type: ignore[arg-type]
        gateway=None,
        asset_store=object(),  # type: ignore[arg-type]
        media_probe=object(),  # type: ignore[arg-type]
        frame_extractor=None,
        provider_name="fake",
        resolution="720p",
    )

    preview = service.preview_shot_prompt(repository.shots[1].id)

    assert preview["draftRevision"] == 3
    assert preview["localAnalysis"]["detectedSubshotCount"] == 2
    assert preview["qualitativePacing"].startswith("由视觉与Prompt审稿LLM")
    assert preview["creativeBody"] == repository.shots[1].draft.direction
    assert "正文由片段已确认正文注入" in preview["systemShell"]
    assert preview["references"] == []
    assert preview["previousTail"]["available"] is False
    assert preview["target"] == "video"
    assert preview["ready"] is True
    assert preview["inputHash"]
    assert preview["sourceRevisionHash"]

    anchor_preview = service.preview_shot_prompt(
        repository.shots[1].id,
        target=ReferenceTarget.ANCHOR,
    )
    assert anchor_preview["target"] == "anchor"
    assert anchor_preview["ready"] is False
    assert "生成新锚点" in anchor_preview["blockers"][0]
    assert anchor_preview["inputPlan"] is None
    assert anchor_preview["inputHash"] != preview["inputHash"]

    retry_preview = service.preview_shot_prompt(
        repository.shots[1].id,
        target=ReferenceTarget.ANCHOR,
        regeneration_instruction="只修正柜门开场状态",
    )
    assert "只修正柜门开场状态" in retry_preview["prompt"]
    assert retry_preview["inputHash"] != anchor_preview["inputHash"]
    compiled = service._compile_shot_generation(
        repository.shots[1],
        target=ReferenceTarget.ANCHOR,
        regeneration_instruction="只修正柜门开场状态",
        require_ready=False,
    )
    assert retry_preview["prompt"] == compiled.prompt.text
    assert retry_preview["inputHash"] == compiled.input_hash


def test_derive_anchor_uses_scene_look_only_for_anchor_target(tmp_path: Path) -> None:
    repository = _AssistRepository(tmp_path)
    project_identity_path = tmp_path / "person.png"
    project_identity_path.write_bytes(b"person")
    project_identity = StoredAsset(
        id=uuid.uuid4(),
        project_id=repository.project.id,
        scene_id=None,
        shot_card_id=None,
        step_id=None,
        role="canon_reference",
        media_type="image",
        scope="canon",
        status="approved",
        path=project_identity_path,
        sha256="c" * 64,
        metadata={},
        semantic_key="person:headshot",
    )
    repository.assets[project_identity.id] = project_identity
    repository.project = replace(
        repository.project,
        default_reference_bindings=(
            ReferenceBinding(
                assetId=project_identity.id,
                usage="generation_reference",
                role="identity",
                applyTo="both",
            ),
        ),
    )
    repository.scene = replace(
        repository.scene,
        selected_look_asset_id=repository.asset.id,
    )
    current = repository.shots[1]
    current = replace(
        current,
        draft=current.draft.model_copy(
            update={
                "anchor_mode": AnchorMode.GENERATE,
                "scene_look_usage": SceneLookUsage.DERIVE_ANCHOR,
            }
        ),
    )
    repository.shots = (repository.shots[0], current, repository.shots[2])
    service = ShotProductionService(
        repository=repository,  # type: ignore[arg-type]
        gateway=None,
        asset_store=object(),  # type: ignore[arg-type]
        media_probe=object(),  # type: ignore[arg-type]
        frame_extractor=None,
        provider_name="fake",
        resolution="720p",
    )

    anchor = service.preview_shot_prompt(current.id, target=ReferenceTarget.ANCHOR)
    video = service.preview_shot_prompt(current.id, target=ReferenceTarget.VIDEO)

    assert [item["assetId"] for item in anchor["references"]] == [
        str(repository.asset.id),
        str(project_identity.id),
    ]
    assert [item["assetId"] for item in video["references"]] == [
        str(project_identity.id),
    ]
    assert anchor["ready"] is False
    assert "接受开场静态画面稿" in anchor["blockers"][0]
    assert video["ready"] is False
    assert video["blockers"] == ["请先生成、批准并选择片段开场锚点"]


def test_approved_anchor_is_the_only_seedance_video_image(tmp_path: Path) -> None:
    repository = _AssistRepository(tmp_path)
    identity_path = tmp_path / "person.png"
    identity_path.write_bytes(b"person")
    identity = StoredAsset(
        id=uuid.uuid4(),
        project_id=repository.project.id,
        scene_id=None,
        shot_card_id=None,
        step_id=None,
        role="canon_reference",
        media_type="image",
        scope="canon",
        status="approved",
        path=identity_path,
        sha256="c" * 64,
        metadata={},
        semantic_key="person:headshot",
    )
    anchor_path = tmp_path / "approved-anchor.png"
    anchor_path.write_bytes(b"anchor")
    anchor = StoredAsset(
        id=uuid.uuid4(),
        project_id=repository.project.id,
        scene_id=repository.scene.id,
        shot_card_id=repository.shots[1].id,
        step_id=None,
        role="shot_anchor",
        media_type="image",
        scope="shot",
        status="approved",
        path=anchor_path,
        sha256="d" * 64,
        metadata={},
        semantic_key=f"shot:{repository.shots[1].id}:anchor",
    )
    repository.assets.update({identity.id: identity, anchor.id: anchor})
    repository.project = replace(
        repository.project,
        default_reference_bindings=(
            ReferenceBinding(
                assetId=identity.id,
                usage="generation_reference",
                role="identity",
                applyTo="both",
            ),
        ),
    )
    current = replace(
        repository.shots[1],
        selected_anchor_asset_id=anchor.id,
        draft=repository.shots[1].draft.model_copy(
            update={"anchor_mode": AnchorMode.GENERATE}
        ),
    )
    repository.shots = (repository.shots[0], current, repository.shots[2])
    service = ShotProductionService(
        repository=repository,  # type: ignore[arg-type]
        gateway=None,
        asset_store=object(),  # type: ignore[arg-type]
        media_probe=object(),  # type: ignore[arg-type]
        frame_extractor=None,
        provider_name="fake",
        resolution="720p",
    )

    preview = service.preview_shot_prompt(current.id, target=ReferenceTarget.VIDEO)

    assert preview["ready"] is True
    assert [item["assetId"] for item in preview["references"]] == [str(anchor.id)]
    assert [item["provider_role"] for item in preview["inputPlan"]["bindings"]] == [
        "first_frame"
    ]
    assert "@图片2" not in preview["prompt"]


def test_generic_upload_cannot_claim_managed_identity_or_scene_role(tmp_path: Path) -> None:
    service = ShotProductionService(
        repository=object(),  # type: ignore[arg-type]
        gateway=None,
        asset_store=object(),  # type: ignore[arg-type]
        media_probe=object(),  # type: ignore[arg-type]
        frame_extractor=None,
        provider_name="fake",
        resolution="720p",
    )
    source = tmp_path / "reference.png"
    source.write_bytes(b"image")

    for managed_role in ("identity", "scene"):
        with pytest.raises(ValueError, match="managed sources"):
            service.import_reference(
                project_id=uuid.uuid4(),
                path=source,
                usage="generation_reference",
                role=managed_role,
            )
