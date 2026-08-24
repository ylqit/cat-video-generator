from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from cat_video_generator.interfaces.api import create_app
from cat_video_generator.interfaces.jobs import JobRegistry


class _CanvasService:
    def __init__(self) -> None:
        self.brief: dict[str, object] | None = None
        self.subject: dict[str, object] | None = None
        self.saved_layout: dict[str, object] | None = None
        self.asset_bindings: dict[str, object] | None = None
        self.approved_revision: uuid.UUID | None = None
        self.storyboard_mode: str | None = None
        self.storyboard_references: tuple[uuid.UUID, ...] = ()
        self.manual_storyboard: dict[str, object] | None = None
        self.prompt_compilation: dict[str, object] | None = None
        self.archived_node_id: uuid.UUID | None = None
        self.restored_node_id: uuid.UUID | None = None
        self.subject_completion_run_id = uuid.uuid4()
        self.visual_profile_revision = 1
        self.visual_profile_bindings = [
            {
                "assetId": str(uuid.uuid4()),
                "purpose": "person_identity",
                "instruction": "固定儿童面部身份",
            },
            {
                "assetId": str(uuid.uuid4()),
                "purpose": "person_body",
                "instruction": "固定儿童全身比例",
            },
            {
                "assetId": str(uuid.uuid4()),
                "purpose": "cat_identity",
                "instruction": "固定猫咪正面身份",
            },
            {
                "assetId": str(uuid.uuid4()),
                "purpose": "cat_identity",
                "instruction": "固定猫咪侧面身份",
            },
            {
                "assetId": str(uuid.uuid4()),
                "purpose": "style",
                "instruction": "只锁定线条、材质和光线",
            },
        ]

    def save_brief(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.brief = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        return {"id": str(uuid.uuid4()), "projectId": str(project_id), **self.brief}

    def create_subject(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.subject = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        return {"id": str(uuid.uuid4()), "projectId": str(project_id), **self.subject}

    def list_subjects(self, project_id: uuid.UUID) -> list[dict[str, object]]:
        return [
            {
                "id": str(uuid.uuid4()),
                "projectId": str(project_id),
                "revisionId": str(uuid.uuid4()),
                "revision": 1,
                "status": "approved",
                "name": "蓝色汽水罐",
                "kind": "product",
                "role": "hero_product",
                "identityAnchors": ["蓝色罐身"],
                "immutableTraits": ["标签文字不变"],
                "references": [],
            }
        ]

    def bind_canvas_node_assets(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: object,
    ) -> dict[str, object]:
        assert expected_revision == 1
        self.asset_bindings = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        return {
            "id": str(node_id),
            "type": "ReferenceAssetNode",
            "revision": 2,
            "status": "ready",
            "data": {"assets": self.asset_bindings["bindings"]},
        }

    def create_subject_completion_run(
        self, project_id: uuid.UUID, payload: object
    ) -> dict[str, object]:
        return {
            "id": str(self.subject_completion_run_id),
            "projectId": str(project_id),
            "subjectId": str(payload.subject_id),  # type: ignore[attr-defined]
            "status": "pending",
            "missingFields": ["immutableTraits", "dramaticFunction"],
        }

    def get_subject_completion_run(self, run_id: uuid.UUID) -> dict[str, object]:
        assert run_id == self.subject_completion_run_id
        return {
            "id": str(run_id),
            "status": "awaiting_review",
            "proposal": {"identityAnchors": ["灰白虎斑猫"]},
            "promptId": str(uuid.uuid4()),
        }

    def apply_subject_completion(self, run_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert run_id == self.subject_completion_run_id
        return {
            "runId": str(run_id),
            "status": "applied",
            "revision": 2,
            "acceptedFields": payload.accepted_fields,  # type: ignore[attr-defined]
        }

    def list_project_assets(
        self, project_id: uuid.UUID, *, media_kind: str | None = None
    ) -> list[dict[str, object]]:
        return [
            {
                "id": str(uuid.uuid4()),
                "projectId": str(project_id),
                "mediaType": media_kind or "image",
                "status": "ready",
            }
        ]

    def list_visual_presets(self) -> list[dict[str, object]]:
        return [
            {
                "key": "healing_child_cat_line_texture_v3",
                "canonProfileId": "canon-v3-healing-child-cat-line-texture",
                "title": "一人一猫 · 线条材质",
                "version": 3,
                "ready": True,
                "slots": [
                    {
                        "assetId": item["assetId"],
                        "semanticKey": semantic_key,
                        "title": title,
                        "contentUrl": f"/api/v1/assets/{item['assetId']}/content",
                        "thumbnailUrl": f"/api/v1/assets/{item['assetId']}/content",
                        "approvalStatus": "approved",
                        "sha256": "a" * 64,
                        "required": True,
                        "role": role,
                        "purpose": item["purpose"],
                        "instruction": item["instruction"],
                    }
                    for item, semantic_key, title, role in zip(
                        self.visual_profile_bindings,
                        (
                            "person:headshot",
                            "person:fullbody",
                            "cat:front",
                            "cat:side",
                            "style:line_texture",
                        ),
                        ("儿童面部", "儿童全身比例", "猫咪正面", "猫咪侧面", "线条材质"),
                        ("person", "person", "cat", "cat", "style"),
                        strict=True,
                    )
                ],
            }
        ]

    def apply_visual_preset(self, project_id: uuid.UUID, preset_key: str) -> dict[str, object]:
        return {
            "preset": self.list_visual_presets()[0],
            "visualProfile": self.get_episode_visual_profile(project_id),
            "canvasNodeId": str(uuid.uuid4()),
            "reusedAssetIds": [item["assetId"] for item in self.visual_profile_bindings],
        }

    def get_episode_visual_profile(self, project_id: uuid.UUID) -> dict[str, object]:
        return {
            "id": str(uuid.uuid4()),
            "projectId": str(project_id),
            "revision": self.visual_profile_revision,
            "sourceProfileId": "canon-v3-healing-child-cat-line-texture",
            "personIdentity": "固定儿童脸型、五官、年龄感与身份特征",
            "personHair": "固定儿童短发轮廓、发色与发际线",
            "personBody": "固定儿童全身比例与非成人化身体结构",
            "catIdentity": "固定猫咪脸部、毛色分区、体型与环纹尾巴",
            "stylePositive": ["克制轮廓线", "湿润半透明高光", "柔和漫射光"],
            "styleNegative": ["摄影写实", "复制参考物体或构图"],
            "referenceBindings": self.visual_profile_bindings,
            "references": [],
            "lockedSemanticKeys": [
                "person:headshot",
                "person:fullbody",
                "cat:front",
                "cat:side",
                "style:line_texture",
            ],
        }

    def update_episode_visual_profile(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: object,
    ) -> dict[str, object]:
        assert expected_revision == self.visual_profile_revision
        document = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        assert document["referenceBindings"] == self.visual_profile_bindings
        self.visual_profile_revision += 1
        return {
            **self.get_episode_visual_profile(project_id),
            **document,
            "revision": self.visual_profile_revision,
        }

    def create_video_filmstrip_run(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, object]:
        return {
            "assetId": str(asset_id),
            "frameCount": frame_count,
            "status": "pending",
            "stepId": str(uuid.uuid4()),
            "frames": [],
        }

    def get_video_filmstrip(self, asset_id: uuid.UUID, *, frame_count: int) -> dict[str, object]:
        return {
            "assetId": str(asset_id),
            "frameCount": frame_count,
            "status": "ready",
            "frames": [
                {
                    "assetId": str(uuid.uuid4()),
                    "timestampMs": index * 1_000,
                    "contentUrl": f"/api/v1/assets/frame-{index}/content",
                }
                for index in range(frame_count)
            ],
        }

    def save_node_generation_config(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: object,
    ) -> dict[str, object]:
        assert expected_revision == 2
        return {
            "id": str(uuid.uuid4()),
            "canvasNodeId": str(node_id),
            "revision": 3,
            **payload.model_dump(mode="json", by_alias=True),  # type: ignore[attr-defined]
        }

    def list_provider_capabilities(
        self, *, media_kind: str | None = None
    ) -> list[dict[str, object]]:
        return [
            {
                "provider": "ark",
                "model": "seedance-2",
                "mediaKind": media_kind or "video",
                "capabilities": {
                    "modes": ["text_to_video", "image_to_video"],
                    "aspectRatios": ["16:9", "9:16"],
                    "resolutions": ["720p"],
                    "durations": [5, 10],
                    "candidateCounts": [1],
                },
            }
        ]

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, object]:
        self.approved_revision = revision_id
        return {"id": str(revision_id), "status": "approved"}

    def create_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        idempotency_key: str | None = None,
        creation_mode: str = "from_story",
        reference_asset_ids: tuple[uuid.UUID, ...] = (),
        instruction: str | None = None,
    ) -> dict[str, object]:
        if self.approved_revision is None:
            raise ValueError("故事尚未人工批准，不能生成分镜")
        self.storyboard_mode = creation_mode
        self.storyboard_references = reference_asset_ids
        return {
            "projectId": str(project_id),
            "status": "ready",
            "beats": [],
            "creationMode": creation_mode,
            "instruction": instruction,
            "idempotencyKey": idempotency_key,
        }

    def save_manual_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: object,
    ) -> dict[str, object]:
        self.manual_storyboard = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        return {
            "projectId": str(project_id),
            "revision": expected_revision + 1,
            "status": "awaiting_review",
            "shotCount": len(self.manual_storyboard["shots"]),  # type: ignore[arg-type]
        }

    def compile_storyboard_prompts(
        self,
        project_id: uuid.UUID,
        payload: object,
    ) -> dict[str, object]:
        self.prompt_compilation = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        shot = self.prompt_compilation["shots"][0]  # type: ignore[index]
        prompt_id = uuid.uuid4()
        return {
            "projectId": str(project_id),
            "storyRevisionId": self.prompt_compilation["storyRevisionId"],
            "visualProfileRevisionId": self.prompt_compilation["visualProfileRevisionId"],
            "status": "compiled",
            "shots": [
                {
                    "beatId": shot["beatId"],  # type: ignore[index]
                    "order": shot["order"],  # type: ignore[index]
                    "promptId": str(prompt_id),
                    "finalPrompt": "【全局 Canon】固定儿童与猫咪\n【所属场景】雨后庭院",
                    "referenceBindings": [
                        {
                            "assetId": str(uuid.uuid4()),
                            "role": "identity",
                            "purpose": "person_identity",
                            "source": "canon",
                            "semanticKey": "person:headshot",
                            "title": "固定儿童面部",
                            "sha256": "a" * 64,
                        },
                        {
                            "assetId": str(uuid.uuid4()),
                            "role": "environment",
                            "purpose": "scene_look",
                            "source": "scene",
                            "semanticKey": "scene:rainy-yard",
                            "title": "雨后庭院",
                            "sha256": "b" * 64,
                        },
                    ],
                    "warnings": [],
                    "blockers": [],
                    "estimatedCost": {"currency": "CNY", "amountMicros": 0},
                    "inputHash": "c" * 64,
                }
            ],
        }

    def get_prompt_run(self, prompt_id: uuid.UUID) -> dict[str, object]:
        return {
            "id": str(prompt_id),
            "finalPrompt": "精确发送给供应商的 Prompt",
            "providerInternalTransform": "not_observable",
            "retryChain": [],
        }

    def get_canvas(self, project_id: uuid.UUID) -> dict[str, object]:
        return {
            "projectId": str(project_id),
            "layoutVersion": 3,
            "nodes": [],
            "edges": [],
            "syncStatus": "saved",
        }

    def save_canvas_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: object,
    ) -> dict[str, object]:
        assert expected_version == 3
        data = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        self.saved_layout = data
        return {
            "projectId": str(project_id),
            "layoutVersion": 4,
            "syncStatus": "saved",
            **data,
        }

    def archive_canvas_node(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        expected_version: int,
        reason: str | None = None,
    ) -> dict[str, object]:
        assert expected_version == 3
        self.archived_node_id = node_id
        return {
            "projectId": str(project_id),
            "nodeId": str(node_id),
            "archived": True,
            "reason": reason,
            "layoutVersion": 4,
        }

    def restore_canvas_node(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        expected_version: int,
    ) -> dict[str, object]:
        assert expected_version == 4
        self.restored_node_id = node_id
        return {
            "projectId": str(project_id),
            "nodeId": str(node_id),
            "archived": False,
            "layoutVersion": 5,
        }

    def list_canvas_templates(self) -> list[dict[str, object]]:
        return [
            {
                "key": "short_drama",
                "title": "AIGC 短剧",
                "defaultCandidateCount": 3,
                "nodeTypes": ["BriefNode", "StoryPlannerNode"],
            },
            {
                "key": "product_ad",
                "title": "产品广告",
                "defaultCandidateCount": 4,
                "nodeTypes": ["SubjectNode", "GenerationBatchNode"],
            },
            {
                "key": "blank",
                "title": "空白画布",
                "defaultCandidateCount": 4,
                "nodeTypes": [],
            },
        ]

    def instantiate_template(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        return {
            "projectId": str(project_id),
            "templateKey": payload.template_key.value,  # type: ignore[attr-defined]
            "graphVersion": 1,
        }

    def create_video_edit_recipe(self, payload: object) -> dict[str, object]:
        return {
            "id": str(uuid.uuid4()),
            "revision": 1,
            **payload.model_dump(by_alias=True, mode="json"),  # type: ignore[attr-defined]
        }

    def compile_video_edit_recipe(self, recipe_id: uuid.UUID) -> dict[str, object]:
        return {
            "recipeId": str(recipe_id),
            "mode": "two_stage",
            "imageCallCount": 2,
            "videoCallCount": 1,
            "estimatedCostMicros": 11_000,
        }

    def submit_video_edit_recipe(self, recipe_id: uuid.UUID, payload: object) -> dict[str, object]:
        return {
            "recipeId": str(recipe_id),
            "jobId": str(uuid.uuid4()),
            "status": "queued",
            "idempotencyKey": payload.idempotency_key,  # type: ignore[attr-defined]
        }


def _client(tmp_path: Path, service: _CanvasService) -> TestClient:
    container = SimpleNamespace(
        repository=object(),
        editing=object(),
        canvas_v2=service,
        runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
    )
    return TestClient(
        create_app(
            container,  # type: ignore[arg-type]
            job_registry=JobRegistry(inline=True),
        )
    )


def test_v2_brief_and_generic_subject_endpoints(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()

    brief_response = client.put(
        f"/api/v2/projects/{project_id}/brief",
        json={
            "theme": "小孩与猫在雨前收画",
            "audience": "亲子观众",
            "genre": "治愈短剧",
            "tone": "紧凑温暖",
            "aspectRatio": "9:16",
            "targetDurationSeconds": 60,
            "constraints": [],
        },
    )
    subject_response = client.post(
        f"/api/v2/projects/{project_id}/subjects",
        json={
            "name": "灰灰",
            "kind": "animal",
            "role": "co_protagonist",
            "identityAnchors": ["灰白虎斑猫"],
            "immutableTraits": ["尾巴纹路不变"],
        },
    )

    assert brief_response.status_code == 200
    assert brief_response.json()["targetDurationSeconds"] == 60
    assert subject_response.status_code == 201
    assert subject_response.json()["kind"] == "animal"


def test_v2_lists_subject_revisions_and_binds_assets_to_reference_nodes(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    node_id = uuid.uuid4()
    asset_id = uuid.uuid4()

    subjects = client.get(f"/api/v2/projects/{project_id}/subjects")
    bound = client.put(
        f"/api/v2/canvas/nodes/{node_id}/asset-bindings",
        headers={"If-Match": "1"},
        json={
            "bindings": [{"assetId": str(asset_id), "semanticRole": "packshot_front"}],
            "allowMove": False,
        },
    )

    assert subjects.status_code == 200
    assert subjects.json()[0]["revision"] == 1
    assert subjects.json()[0]["kind"] == "product"
    assert bound.status_code == 200
    assert bound.json()["revision"] == 2
    assert service.asset_bindings == {
        "bindings": [{"assetId": str(asset_id), "semanticRole": "packshot_front"}],
        "allowMove": False,
    }


def test_v2_storyboard_is_blocked_until_human_story_approval(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    revision_id = uuid.uuid4()

    blocked = client.post(
        f"/api/v2/projects/{project_id}/storyboard-runs",
        json={"idempotencyKey": "blocked-0001"},
    )
    approved = client.post(f"/api/v2/story-revisions/{revision_id}/approve", json={})
    ready = client.post(
        f"/api/v2/projects/{project_id}/storyboard-runs",
        json={"idempotencyKey": "ready-0001"},
    )

    assert blocked.status_code == 202
    assert blocked.json()["status"] == "failed"
    assert "人工批准" in blocked.json()["error"]["message"]
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert ready.status_code == 202
    assert ready.json()["kind"] == "storyboard"
    assert ready.json()["context"]["creationMode"] == "from_story"


def test_v2_character_storyboard_and_manual_draft_use_distinct_paths(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    child_asset_id = uuid.uuid4()
    cat_asset_id = uuid.uuid4()
    client.post(f"/api/v2/story-revisions/{revision_id}/approve", json={})

    generated = client.post(
        f"/api/v2/projects/{project_id}/storyboard-runs",
        json={
            "creationMode": "from_characters",
            "referenceAssetIds": [str(child_asset_id), str(cat_asset_id)],
            "instruction": "孩子与猫咪一起整理窗台",
            "idempotencyKey": "characters-0001",
        },
    )
    manual = client.put(
        f"/api/v2/projects/{project_id}/storyboard-drafts",
        headers={"If-Match": "1"},
        json={
            "healingRecipe": True,
            "shots": [
                {
                    "order": 1,
                    "durationSeconds": 15,
                    "title": "亮叶",
                    "action": "孩子蹲下看叶片，猫咪在旁边嗅闻水珠",
                    "shotSize": "中景",
                    "lighting": "雨后柔光",
                    "dialogue": "",
                    "soundEffect": "雨滴与猫咪脚步",
                    "camera": "固定机位",
                    "prompt": "固定儿童与猫咪，雨后水彩庭院",
                }
            ],
        },
    )

    assert generated.status_code == 202
    assert generated.json()["context"]["creationMode"] == "from_characters"
    assert service.storyboard_mode == "from_characters"
    assert service.storyboard_references == (child_asset_id, cat_asset_id)
    assert manual.status_code == 200
    assert manual.json()["status"] == "awaiting_review"
    assert service.manual_storyboard is not None


def test_v2_storyboard_prompt_compilation_preserves_versions_and_reference_roles(
    tmp_path: Path,
) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    story_revision_id = uuid.uuid4()
    profile_revision_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    beat_id = uuid.uuid4()

    response = client.post(
        f"/api/v2/projects/{project_id}/storyboard-prompt-compilations",
        json={
            "storyRevisionId": str(story_revision_id),
            "visualProfileRevisionId": str(profile_revision_id),
            "healingRecipe": True,
            "shots": [
                {
                    "beatId": str(beat_id),
                    "expectedRevision": 3,
                    "order": 1,
                    "sceneId": str(scene_id),
                    "durationSeconds": 15,
                    "title": "雨后亮叶",
                    "action": "孩子蹲下观察亮叶，猫咪在旁边嗅闻",
                    "shotSize": "中景",
                    "lighting": "雨后柔光",
                    "dialogue": "",
                    "soundEffect": "雨滴与猫咪脚步",
                    "camera": "固定机位",
                    "temporalBeats": [
                        {
                            "startSeconds": 0,
                            "endSeconds": 5,
                            "personAction": "孩子蹲下",
                            "catAction": "猫咪靠近",
                            "camera": "固定机位",
                        }
                    ],
                    "compositionAssetIds": [],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "compiled"
    assert service.prompt_compilation is not None
    assert service.prompt_compilation["shots"][0]["expectedRevision"] == 3  # type: ignore[index]
    roles = {
        item["role"] for item in response.json()["shots"][0]["referenceBindings"]
    }
    assert roles == {"identity", "environment"}
    assert response.json()["shots"][0]["inputHash"] == "c" * 64


def test_v2_prompt_and_optimistic_canvas_layout_endpoints(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    prompt_id = uuid.uuid4()

    prompt = client.get(f"/api/v2/prompt-runs/{prompt_id}")
    canvas = client.get(f"/api/v2/projects/{project_id}/canvas")
    saved = client.patch(
        f"/api/v2/projects/{project_id}/canvas/layout",
        headers={"If-Match": "3"},
        json={
            "nodes": [{"nodeId": str(uuid.uuid4()), "x": 120, "y": 80}],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "operations": [{"operationId": str(uuid.uuid4()), "type": "move_node"}],
        },
    )

    assert prompt.status_code == 200
    assert prompt.json()["providerInternalTransform"] == "not_observable"
    assert canvas.json()["layoutVersion"] == 3
    assert saved.status_code == 200
    assert saved.json()["layoutVersion"] == 4


def test_v2_layout_ignores_legacy_business_edge_snapshot(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    source_id = uuid.uuid4()
    target_id = uuid.uuid4()

    saved = client.patch(
        f"/api/v2/projects/{project_id}/canvas/layout",
        headers={"If-Match": "3"},
        json={
            "nodes": [],
            "edges": [
                {
                    "id": str(uuid.uuid4()),
                    "sourceNodeId": str(source_id),
                    "sourceNodeType": "ReferenceAssetNode",
                    "sourcePort": "media_reference[]",
                    "targetNodeId": str(target_id),
                    "targetNodeType": "GenerationBatchNode",
                    "targetPort": "media_reference[]",
                    "relationType": "media_reference[]->media_reference[]",
                    "revision": 1,
                }
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "operations": [{"operationId": str(uuid.uuid4()), "type": "move_node"}],
        },
    )

    assert saved.status_code == 200
    assert service.saved_layout is not None
    assert "edges" not in service.saved_layout


def test_v2_canvas_node_archive_and_restore_are_versioned(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    node_id = uuid.uuid4()

    archived = client.post(
        f"/api/v2/projects/{project_id}/canvas/nodes/{node_id}/archive",
        headers={"If-Match": "3"},
        json={"reason": "用户从画布移除"},
    )
    restored = client.post(
        f"/api/v2/projects/{project_id}/canvas/nodes/{node_id}/restore",
        headers={"If-Match": "4"},
    )

    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert archived.json()["layoutVersion"] == 4
    assert service.archived_node_id == node_id
    assert restored.status_code == 200
    assert restored.json()["archived"] is False
    assert restored.json()["layoutVersion"] == 5
    assert service.restored_node_id == node_id


def test_v2_template_library_and_product_default(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()

    templates = client.get("/api/v2/canvas-templates")
    instantiated = client.post(
        f"/api/v2/projects/{project_id}/template-instances",
        json={"templateKey": "product_ad"},
    )

    assert templates.status_code == 200
    product = next(item for item in templates.json() if item["key"] == "product_ad")
    assert product["defaultCandidateCount"] == 4
    assert instantiated.status_code == 201
    assert instantiated.json()["templateKey"] == "product_ad"


def test_v2_video_edit_recipe_compile_and_submit_contract(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    recipe = client.post(
        "/api/v2/video-edit-recipes",
        json={
            "projectId": str(uuid.uuid4()),
            "sourceAssetId": str(uuid.uuid4()),
            "startMs": 4_000,
            "endMs": 10_000,
            "instruction": "保持包装文字并修复手部",
            "referenceAssetIds": [str(uuid.uuid4())],
            "annotations": [
                {
                    "frameTimestampMs": 5_000,
                    "tool": "rectangle",
                    "points": [{"x": 0.2, "y": 0.2}, {"x": 0.6, "y": 0.7}],
                }
            ],
        },
    )
    recipe_id = recipe.json()["id"]
    compiled = client.post(f"/api/v2/video-edit-recipes/{recipe_id}/compile", json={})
    submitted = client.post(
        f"/api/v2/video-edit-recipes/{recipe_id}/submit",
        json={"idempotencyKey": "edit-test-0001", "acceptEstimatedCostMicros": 11_000},
    )

    assert recipe.status_code == 201
    assert compiled.json()["mode"] == "two_stage"
    assert submitted.status_code == 202
    assert submitted.json()["status"] == "queued"


def test_subject_assistant_is_explicit_async_and_requires_human_apply(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    subject_id = uuid.uuid4()

    queued = client.post(
        f"/api/v2/projects/{project_id}/subject-assistant-runs",
        json={
            "subjectId": str(subject_id),
            "idempotencyKey": "subject-assistant-0001",
            "instruction": "补齐跨镜头身份锚点",
        },
    )
    inspected = client.get(f"/api/v2/subject-assistant-runs/{service.subject_completion_run_id}")
    applied = client.post(
        f"/api/v2/subject-assistant-runs/{service.subject_completion_run_id}/apply",
        json={
            "acceptedFields": ["immutableTraits"],
            "finalDraft": {
                "name": "灰灰",
                "kind": "animal",
                "role": "co_protagonist",
                "identityAnchors": ["灰白虎斑猫"],
                "immutableTraits": ["额头 M 纹不变"],
                "relationshipNotes": "",
                "dramaticFunction": "",
                "visualRisks": [],
                "references": [],
            },
        },
    )

    assert queued.status_code == 202
    assert queued.json()["status"] == "pending"
    assert inspected.json()["status"] == "awaiting_review"
    assert applied.status_code == 201
    assert applied.json()["acceptedFields"] == ["immutableTraits"]


def test_v2_canvas_asset_history_filters_by_media_kind(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()

    response = client.get(f"/api/v2/projects/{project_id}/assets?kind=video")

    assert response.status_code == 200
    assert response.json()[0]["mediaType"] == "video"


def test_v2_visual_preset_reuses_assets_and_versions_episode_profile(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()

    presets = client.get("/api/v2/visual-presets")
    applied = client.post(
        f"/api/v2/projects/{project_id}/visual-presets/healing_child_cat_line_texture_v3/apply"
    )
    current = client.get(f"/api/v2/projects/{project_id}/visual-profile")
    draft = current.json()
    draft["stylePositive"] = ["克制轮廓线", "湿润半透明高光", "柔和漫射光", "自然层次"]
    updated = client.patch(
        f"/api/v2/projects/{project_id}/visual-profile",
        headers={"If-Match": "1"},
        json={
            key: value
            for key, value in draft.items()
            if key
            in {
                "personIdentity",
                "personHair",
                "personBody",
                "catIdentity",
                "stylePositive",
                "styleNegative",
                "referenceBindings",
            }
        },
    )

    assert presets.status_code == 200
    preset = presets.json()[0]
    assert preset["canonProfileId"] == "canon-v3-healing-child-cat-line-texture"
    assert [slot["semanticKey"] for slot in preset["slots"]] == [
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "cat:side",
        "style:line_texture",
    ]
    assert all(slot["contentUrl"] for slot in preset["slots"])
    assert applied.status_code == 200
    assert applied.json()["reusedAssetIds"] == [
        item["assetId"] for item in service.visual_profile_bindings
    ]
    assert current.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2


def test_video_filmstrip_queues_once_and_returns_distinct_cached_frames(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    asset_id = uuid.uuid4()

    queued = client.post(f"/api/v2/assets/{asset_id}/filmstrip-runs?frameCount=12")
    ready = client.get(f"/api/v2/assets/{asset_id}/filmstrip?frameCount=12")

    assert queued.status_code == 202
    assert queued.json()["status"] == "pending"
    assert ready.status_code == 200
    frames = ready.json()["frames"]
    assert len(frames) == 12
    assert len({frame["timestampMs"] for frame in frames}) == 12
    assert len({frame["contentUrl"] for frame in frames}) == 12


def test_generation_config_and_capabilities_are_server_driven(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    node_id = uuid.uuid4()
    reference_id = uuid.uuid4()

    capabilities = client.get("/api/v2/provider-capabilities?mediaKind=video")
    saved = client.put(
        f"/api/v2/canvas/nodes/{node_id}/generation-config",
        headers={"If-Match": "2"},
        json={
            "provider": "ark",
            "model": "seedance-2",
            "mode": "image_to_video",
            "aspectRatio": "9:16",
            "resolution": "720p",
            "durationSeconds": 5,
            "audioEnabled": True,
            "candidateCount": 1,
            "draftPrompt": "保持主体身份并缓慢推近",
            "autoValidate": True,
            "autoLink": True,
            "actualReferences": [
                {
                    "assetId": str(reference_id),
                    "semanticRole": "protagonist",
                    "providerIncluded": True,
                }
            ],
        },
    )

    assert capabilities.status_code == 200
    assert capabilities.json()[0]["capabilities"]["resolutions"] == ["720p"]
    assert saved.status_code == 201
    assert saved.json()["revision"] == 3
