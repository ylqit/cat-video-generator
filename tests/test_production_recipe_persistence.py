from __future__ import annotations

import importlib.util
import json
import uuid
import zipfile
from datetime import UTC, datetime
from io import BytesIO, StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from cat_video_generator.application.production_recipes import ProductionRecipeService
from cat_video_generator.domain.production_recipes import (
    EpisodeRules,
    HumanReviewDraft,
    PaidRecipeRunRequest,
    ProductionRecipeInstanceDraft,
    ProductionRecipeInstancePatch,
    RecipeSequenceRunRequest,
)
from cat_video_generator.infrastructure.db.models import Base
from cat_video_generator.infrastructure.db.production_recipe_repository import (
    _fixed_ip_subject_documents,
    _sequence_candidate_json,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration() -> ModuleType:
    path = PROJECT_ROOT / "alembic" / "versions" / "0022_healing_child_cat_recipe.py"
    spec = importlib.util.spec_from_file_location("healing_child_cat_recipe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Repository:
    def __init__(self) -> None:
        self.project_id = uuid.uuid4()
        self.instance_id = uuid.uuid4()
        self.row = {
            "id": str(self.instance_id),
            "projectId": str(self.project_id),
            "recipeKey": "healing_child_cat_v1",
            "recipeVersion": 1,
            "revision": 1,
            "theme": "雨天收衣服",
            "targetDurationSeconds": 31,
            "qualityTier": "balanced",
            "canonProfileId": "canon-v2-healing-child-cat",
            "progress": {
                "storyApproved": False,
                "shotCount": 0,
                "approvedAnchorCount": 0,
                "approvedVideoCount": 0,
                "sequenceReady": False,
                "finalApproved": False,
            },
        }
        self.review: HumanReviewDraft | None = None
        self.episode_rules: EpisodeRules | None = None
        self.materialized_storyboard: dict[str, object] | None = None
        self.suggested_rules: EpisodeRules | None = None

    def create_instance(
        self, project_id: uuid.UUID, payload: ProductionRecipeInstanceDraft
    ) -> dict[str, object]:
        assert project_id == self.project_id
        self.row = {
            **self.row,
            **payload.model_dump(mode="json", by_alias=True),
        }
        return self.row

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, object]:
        assert instance_id == self.instance_id
        return self.row

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ProductionRecipeInstancePatch,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        if expected_revision != self.row["revision"]:
            raise ValueError("配方实例版本冲突")
        self.row = {
            **self.row,
            **payload.model_dump(mode="json", by_alias=True, exclude_none=True),
            "revision": expected_revision + 1,
        }
        return self.row

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: HumanReviewDraft,
        *,
        episode_rules: EpisodeRules | None = None,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.review = payload
        self.episode_rules = episode_rules
        return {
            "id": str(uuid.uuid4()),
            "recipeInstanceId": str(instance_id),
            **payload.model_dump(mode="json", by_alias=True),
        }

    def materialize_storyboard(
        self, instance_id: uuid.UUID, storyboard: dict[str, object]
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.materialized_storyboard = storyboard
        return {**storyboard, "materialized": True}

    def store_suggested_episode_rules(
        self,
        instance_id: uuid.UUID,
        candidate_ids: tuple[uuid.UUID, ...],
        rules: EpisodeRules,
    ) -> None:
        assert instance_id == self.instance_id
        assert len(candidate_ids) == 1
        self.suggested_rules = rules


class _StoryWorkflow:
    def __init__(self) -> None:
        self.story_command: object | None = None
        self.storyboard_durations: tuple[int, ...] | None = None

    def run_story_strategies(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.story_command = payload
        return {
            "id": str(uuid.uuid4()),
            "status": "succeeded",
            "candidates": [{"id": str(uuid.uuid4()), "title": "雨后的小发现"}],
        }

    def create_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        exact_durations: tuple[int, ...] | None = None,
        healing_recipe: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        assert healing_recipe is True
        assert idempotency_key == "board-run-0001"
        self.storyboard_durations = exact_durations
        return {"projectId": str(project_id), "beats": []}


class _ShotWorkflow:
    def __init__(self) -> None:
        self.anchor_calls: list[dict[str, object]] = []
        self.video_calls: list[dict[str, object]] = []

    def generate_anchor(self, shot_id: uuid.UUID, **values: object) -> dict[str, object]:
        self.anchor_calls.append({"shotId": shot_id, **values})
        return {"stepId": str(uuid.uuid4())}

    def generate_video(self, shot_id: uuid.UUID, **values: object) -> dict[str, object]:
        self.video_calls.append({"shotId": shot_id, **values})
        return {"stepId": str(uuid.uuid4())}


class _SequenceWorkflow:
    def __init__(self) -> None:
        self.values: dict[str, object] | None = None

    def build_project_sequence(self, project_id: uuid.UUID, **values: object) -> object:
        self.values = {"projectId": project_id, **values}
        return SimpleNamespace(
            id=uuid.uuid4(),
            project_id=project_id,
            revision=1,
            status=SimpleNamespace(value="content_review"),
            plan=SimpleNamespace(duration_ms=31_000),
            rendered_asset_id=uuid.uuid4(),
        )


def test_model_metadata_contains_recipe_review_and_revision_payloads() -> None:
    recipe = Base.metadata.tables["cat_video.production_recipe_instances"]
    review = Base.metadata.tables["cat_video.human_review_decisions"]
    story = Base.metadata.tables["cat_video.story_revisions"]
    shot = Base.metadata.tables["cat_video.shot_beats"]

    assert recipe.c.production_run_id.unique is True
    assert recipe.c.revision.nullable is False
    assert review.c.production_recipe_instance_id.nullable is False
    assert review.c.target_hash.type.length == 64
    assert "episode_rules_json" in story.c
    assert "temporal_beats_json" in shot.c


def test_fixed_ip_subject_documents_lock_child_and_cat_to_canon_roles() -> None:
    documents = _fixed_ip_subject_documents()

    assert [item["kind"] for item in documents] == ["person", "animal"]
    assert [item["role"] for item in documents] == ["protagonist", "co_protagonist"]
    assert [reference["semanticKey"] for reference in documents[0]["references"]] == [
        "person:headshot",
        "person:fullbody",
    ]
    assert [reference["semanticKey"] for reference in documents[1]["references"]] == [
        "cat:front",
        "cat:side",
    ]
    assert "四足" in "".join(documents[1]["immutableTraits"])


def test_sequence_candidate_exposes_review_pin_and_export_asset() -> None:
    sequence_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    document = _sequence_candidate_json(
        SimpleNamespace(
            id=sequence_id,
            revision=3,
            status="content_review",
            duration_ms=15_000,
            rendered_asset_id=asset_id,
            audio_policy="native_fades",
        ),
        SimpleNamespace(
            id=asset_id,
            sha256="a" * 64,
            metadata_json={"qc": {"passed": True}},
            created_at=datetime.now(UTC),
        ),
    )

    assert document["id"] == str(sequence_id)
    assert document["revision"] == 3
    assert document["renderedAssetId"] == str(asset_id)
    assert document["contentUrl"].endswith(f"/{asset_id}/content")
    assert document["qc"] == {"passed": True}


def test_migration_renders_recipe_tables_and_revision_payload_columns() -> None:
    migration = _load_migration()
    assert migration.down_revision == "0021_libtv_subject_assistant"  # type: ignore[attr-defined]
    migration._schema = lambda: "cat_video"  # type: ignore[attr-defined]
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )

    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "CREATE TABLE cat_video.production_recipe_instances" in sql
    assert "CREATE TABLE cat_video.human_review_decisions" in sql
    assert "ADD COLUMN episode_rules_json" in sql
    assert "ADD COLUMN temporal_beats_json" in sql


def test_service_lists_recipe_and_projects_derived_stage_without_duplicate_state() -> None:
    repository = _Repository()
    service = ProductionRecipeService(repository=repository)

    recipes = service.list_recipes()
    created = service.create_instance(
        repository.project_id,
        ProductionRecipeInstanceDraft(
            theme="雨天收衣服",
            targetDurationSeconds=31,
            qualityTier="balanced",
        ),
    )

    assert recipes[0]["key"] == "healing_child_cat_v1"
    assert created["shotDurations"] == [11, 10, 10]
    assert created["stage"] == "concept"
    assert created["primaryAction"] == "生成故事候选"
    assert "stage" not in repository.row


def test_service_derives_video_stage_from_reviewed_anchor_progress() -> None:
    repository = _Repository()
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 2,
        "approvedAnchorCount": 2,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
    }
    service = ProductionRecipeService(repository=repository)

    instance = service.get_instance(repository.instance_id)

    assert instance["stage"] == "video"
    assert instance["primaryAction"] == "生成下一镜视频"


def test_service_prompts_for_human_story_choice_when_candidates_exist() -> None:
    repository = _Repository()
    repository.row["storyCandidates"] = [{"id": str(uuid.uuid4()), "status": "candidate"}]
    service = ProductionRecipeService(repository=repository)

    instance = service.get_instance(repository.instance_id)

    assert instance["stage"] == "concept"
    assert instance["primaryAction"] == "选择并批准故事"
    assert instance["currentBlocker"] == "故事候选等待人工选择与规则确认"


def test_service_derives_final_review_action_after_sequence_render() -> None:
    repository = _Repository()
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 1,
        "approvedAnchorCount": 1,
        "approvedVideoCount": 1,
        "sequenceReady": True,
        "finalApproved": False,
    }
    service = ProductionRecipeService(repository=repository)

    instance = service.get_instance(repository.instance_id)

    assert instance["stage"] == "sequence"
    assert instance["primaryAction"] == "审核最终成片"


def test_service_updates_optimistically_and_records_pinned_review() -> None:
    repository = _Repository()
    service = ProductionRecipeService(repository=repository)
    updated = service.update_instance(
        repository.instance_id,
        expected_revision=1,
        payload=ProductionRecipeInstancePatch(qualityTier="premium"),
    )
    target_id = uuid.uuid4()
    review = service.record_review(
        repository.instance_id,
        HumanReviewDraft(
            targetType="story_revision",
            targetId=target_id,
            targetRevision=3,
            decision="approve",
        ),
    )

    assert updated["revision"] == 2
    assert updated["qualityTier"] == "premium"
    assert review["targetRevision"] == 3
    assert repository.review is not None

    with pytest.raises(ValueError, match="版本冲突"):
        service.update_instance(
            repository.instance_id,
            expected_revision=1,
            payload=ProductionRecipeInstancePatch(theme="旧页面提交"),
        )


def test_recipe_story_and_storyboard_runs_add_fixed_ip_constraints() -> None:
    repository = _Repository()
    story_workflow = _StoryWorkflow()
    service = ProductionRecipeService(
        repository=repository,
        story_workflow=story_workflow,
    )
    story = service.run_story(
        repository.instance_id,
        PaidRecipeRunRequest(
            idempotencyKey="story-run-0001",
            acceptEstimatedCostMicros=0,
        ),
    )

    assert story["suggestedEpisodeRules"]["environment"] in {"indoor", "outdoor"}
    assert story_workflow.story_command is not None
    assert "低压力" in story_workflow.story_command.rewrite_instruction  # type: ignore[attr-defined]
    assert "无对白" in story_workflow.story_command.rewrite_instruction  # type: ignore[attr-defined]

    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 0,
        "approvedAnchorCount": 0,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
        "episodeRulesLocked": True,
    }
    storyboard = service.run_storyboard(
        repository.instance_id,
        PaidRecipeRunRequest(
            idempotencyKey="board-run-0001",
            acceptEstimatedCostMicros=0,
        ),
    )

    assert story_workflow.storyboard_durations == (11, 10, 10)
    assert storyboard["materialized"] is True


def test_story_review_can_lock_edited_episode_rules() -> None:
    repository = _Repository()
    service = ProductionRecipeService(repository=repository)
    rules = EpisodeRules(
        personWardrobe="米白色针织衫与棕色背带裤",
        timeWeather="雨后黄昏",
        mainScene="厨房窗边",
        environment="indoor",
        coreProps=["布篮"],
        catBehaviorMode="natural",
        soundPlan={
            "ambient": ["雨滴"],
            "foley": ["布料摩擦"],
            "musicMood": "轻柔",
            "dialoguePolicy": "none",
        },
        stylePositive=["原创水彩", "柔和纸张纹理", "低对比暖色"],
        styleExcluded=["准写实", "3D塑料感"],
        canonProfileId="canon-v2-healing-child-cat",
    )

    service.record_review(
        repository.instance_id,
        HumanReviewDraft(
            targetType="story_revision",
            targetId=uuid.uuid4(),
            targetRevision=1,
            decision="approve",
        ),
        episode_rules=rules,
    )

    assert repository.review is not None
    assert repository.episode_rules == rules


def test_recipe_candidate_runs_forward_stable_per_candidate_idempotency_keys() -> None:
    repository = _Repository()
    repository.row["qualityTier"] = "premium"
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 1,
        "approvedAnchorCount": 0,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
    }
    workflow = _ShotWorkflow()
    service = ProductionRecipeService(repository=repository, shot_workflow=workflow)
    shot_id = uuid.uuid4()
    request = PaidRecipeRunRequest(
        idempotencyKey="paid-stage-0001",
        acceptEstimatedCostMicros=0,
    )

    service.run_anchor(repository.instance_id, shot_id, request)

    assert [item["request_idempotency_key"] for item in workflow.anchor_calls] == [
        "paid-stage-0001:anchor:1",
        "paid-stage-0001:anchor:2",
        "paid-stage-0001:anchor:3",
        "paid-stage-0001:anchor:4",
    ]


def test_recipe_redo_reason_forces_a_new_first_candidate_attempt() -> None:
    repository = _Repository()
    repository.row["qualityTier"] = "quick"
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 1,
        "approvedAnchorCount": 0,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
    }
    workflow = _ShotWorkflow()
    service = ProductionRecipeService(repository=repository, shot_workflow=workflow)

    service.run_anchor(
        repository.instance_id,
        uuid.uuid4(),
        PaidRecipeRunRequest(
            idempotencyKey="redo-anchor-0001",
            acceptEstimatedCostMicros=0,
            reason="人物身份错误，退回锚点重做",
        ),
    )

    assert workflow.anchor_calls[0]["regenerate"] is True
    assert workflow.anchor_calls[0]["reason"] == "人物身份错误，退回锚点重做"


def test_recipe_sequence_forwards_request_idempotency_key() -> None:
    repository = _Repository()
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 1,
        "approvedAnchorCount": 1,
        "approvedVideoCount": 1,
        "sequenceReady": False,
        "finalApproved": False,
    }
    workflow = _SequenceWorkflow()
    service = ProductionRecipeService(
        repository=repository,
        sequence_workflow=workflow,
    )

    result = service.run_sequence(
        repository.instance_id,
        RecipeSequenceRunRequest(
            idempotencyKey="sequence-stage-0001",
            acceptEstimatedCostMicros=0,
        ),
    )

    assert result["status"] == "content_review"
    assert workflow.values is not None
    assert workflow.values["request_idempotency_key"] == "sequence-stage-0001"


def test_group_download_contains_only_safe_successful_assets_and_review_manifest(
    tmp_path: Path,
) -> None:
    group_id = uuid.uuid4()
    valid_asset_id = uuid.uuid4()
    escaped_asset_id = uuid.uuid4()
    missing_asset_id = uuid.uuid4()
    media_dir = tmp_path / "sequence"
    media_dir.mkdir()
    (media_dir / "final.mp4").write_bytes(b"video-bytes")

    class _DownloadRepository(_Repository):
        def group_download_assets(self, requested_group_id: uuid.UUID) -> dict[str, object]:
            assert requested_group_id == group_id
            return {
                "groupId": str(group_id),
                "title": "一人一猫:雨后",
                "assets": [
                    {
                        "id": str(valid_asset_id),
                        "role": "final_sequence",
                        "storageKey": "sequence/final.mp4",
                    },
                    {
                        "id": str(escaped_asset_id),
                        "role": "video",
                        "storageKey": "../outside.mp4",
                    },
                    {
                        "id": str(missing_asset_id),
                        "role": "anchor",
                        "storageKey": "missing/anchor.png",
                    },
                ],
            }

    service = ProductionRecipeService(
        repository=_DownloadRepository(),
        asset_root=tmp_path,
    )

    content, filename = service.build_group_download(group_id)

    assert filename == "一人一猫雨后-assets.zip"
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        assert f"media/final_sequence/{valid_asset_id}.mp4" in names
        assert "人工审核清单.md" in names
        manifest = json.loads(archive.read("manifest.json"))
        assert {item["assetId"] for item in manifest["missing"]} == {
            str(escaped_asset_id),
            str(missing_asset_id),
        }
        assert "分镜已批准" in archive.read("人工审核清单.md").decode("utf-8")
