from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from cat_video_generator.application.aigc_canvas import AigcCanvasService
from cat_video_generator.application.ports import DirectorResult
from cat_video_generator.domain.aigc_canvas import StoryBrief, SubjectDraft
from cat_video_generator.interfaces.api_v2 import StoryStrategyRunRequest


@dataclass
class _StoredSubject:
    id: uuid.UUID
    revision_id: uuid.UUID
    draft: SubjectDraft


class _Repository:
    def __init__(self) -> None:
        self.project_id = uuid.uuid4()
        self.story_id = uuid.uuid4()
        self.brief_id = uuid.uuid4()
        self.brief = StoryBrief(
            theme="小孩与猫在雨前收回晾晒的画",
            audience="亲子观众",
            genre="治愈短剧",
            tone="温暖紧凑",
            aspectRatio="9:16",
            targetDurationSeconds=60,
        )
        self.subjects = (
            _StoredSubject(
                uuid.uuid4(),
                uuid.uuid4(),
                SubjectDraft(
                    name="小满",
                    kind="person",
                    role="protagonist",
                    identityAnchors=["六岁小孩"],
                    immutableTraits=["年龄不变"],
                ),
            ),
            _StoredSubject(
                uuid.uuid4(),
                uuid.uuid4(),
                SubjectDraft(
                    name="灰灰",
                    kind="animal",
                    role="co_protagonist",
                    identityAnchors=["灰白虎斑猫"],
                    immutableTraits=["纹路不变"],
                ),
            ),
        )
        self.events: list[tuple[str, object]] = []
        self.saved: list[dict[str, Any]] = []
        self.storyboard: dict[str, Any] | None = None

    def get_current_brief(self, project_id: uuid.UUID) -> tuple[uuid.UUID, StoryBrief]:
        assert project_id == self.project_id
        return self.brief_id, self.brief

    def list_subjects(self, project_id: uuid.UUID) -> tuple[_StoredSubject, ...]:
        assert project_id == self.project_id
        return self.subjects

    def begin_generation_attempt(self, **values: object) -> tuple[dict[str, object], bool]:
        self.events.append(("attempt", values))
        return ({"id": str(uuid.uuid4()), "status": "pending"}, True)

    def begin_prompt_run(self, **values: object) -> tuple[uuid.UUID, uuid.UUID]:
        self.events.append(("prompt_started", values))
        return uuid.uuid4(), uuid.uuid4()

    def complete_prompt_run(self, prompt_id: uuid.UUID, **values: object) -> None:
        self.events.append(("prompt_completed", {"id": prompt_id, **values}))

    def save_story_candidate(self, **values: object) -> dict[str, object]:
        document = dict(values)
        document["id"] = str(uuid.uuid4())
        self.saved.append(document)
        self.events.append(("candidate_saved", document))
        return document

    def finish_generation_attempt(self, attempt_id: str, **values: object) -> None:
        self.events.append(("attempt_finished", {"id": attempt_id, **values}))

    def get_storyboard_context(self, project_id: uuid.UUID) -> dict[str, Any]:
        assert project_id == self.project_id
        return {
            "projectId": str(project_id),
            "storyId": str(self.story_id),
            "brief": self.brief.model_dump(mode="json", by_alias=True),
            "story": {
                "id": str(self.story_id),
                "title": "雨前收画",
                "synopsis": "小满和灰灰一起保住画作。",
                "scenes": [
                    {"title": "风起", "synopsis": "发现暴雨。", "durationWeight": 1},
                    {"title": "收画", "synopsis": "合作收画。", "durationWeight": 2},
                ],
            },
            "subjects": [
                item.draft.model_dump(mode="json", by_alias=True) for item in self.subjects
            ],
            "existing": self.storyboard,
        }

    def save_storyboard_plan(self, project_id: uuid.UUID, **values: object) -> dict[str, Any]:
        assert project_id == self.project_id
        plan = values["plan"]
        durations = values["durations"]
        self.storyboard = {
            "projectId": str(project_id),
            "storyRevisionId": str(self.story_id),
            "status": "ready",
            "targetDurationSeconds": sum(durations),
            "beats": [
                {
                    "id": str(uuid.uuid4()),
                    "title": beat.title,
                    "durationSeconds": duration,
                    "promptId": str(values["prompt_id"]),
                }
                for beat, duration in zip(plan.beats, durations, strict=True)
            ],
        }
        return self.storyboard


class _Director:
    model = "fake-planning"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
    ) -> DirectorResult:
        del schema
        self.calls.append(output_name)
        if output_name == "CanvasStoryCandidateOutput":
            strategy = ("relationship", "problem_solving", "twist_hook")[
                self.calls.count(output_name) - 1
            ]
            payload = {
                "title": f"候选-{strategy}",
                "logline": "小孩和猫必须共同在雨前收回画作。",
                "synopsis": "风起后两者分工合作，最后保住画作。",
                "scenes": [
                    {
                        "title": "风起",
                        "purpose": "建立钩子",
                        "synopsis": "猫发现雨滴，小孩注意到画作。",
                        "durationWeight": 1,
                    },
                    {
                        "title": "收画",
                        "purpose": "完成行动",
                        "synopsis": "两者配合收回画作。",
                        "durationWeight": 2,
                    },
                ],
            }
        elif output_name == "CanvasStoryCriticOutput":
            payload = {
                "openingHook": 8,
                "causalCompleteness": 8,
                "subjectNecessity": 9,
                "emotionalArc": 8,
                "visualizability": 9,
                "durationFit": 8,
                "continuityRisk": 7,
                "safety": 10,
                "rationale": "两个主体都不可替代。",
                "warnings": [],
            }
        else:
            payload = {
                "beats": [
                    {
                        "sceneOrder": 1 if index < 2 else 2,
                        "title": f"Beat {index + 1}",
                        "action": "一个连续且可拍摄的动作。",
                        "camera": "中景稳定推进。",
                        "dialogue": "",
                        "durationWeight": 1,
                    }
                    for index in range(5)
                ]
            }
        return DirectorResult(
            payload=payload,
            response_id=f"response-{len(self.calls)}",
            model=self.model,
            request_hash=f"hash-{len(self.calls)}",
        )


def test_story_strategy_run_persists_prompt_before_each_llm_call_and_returns_three_candidates(
) -> None:
    repository = _Repository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    result = service.run_story_strategies(
        repository.project_id,
        StoryStrategyRunRequest(idempotencyKey="strategy-run-001"),
    )

    assert result["status"] == "succeeded"
    assert len(result["candidates"]) == 3
    assert director.calls == [
        "CanvasStoryCandidateOutput",
        "CanvasStoryCriticOutput",
        "CanvasStoryCandidateOutput",
        "CanvasStoryCriticOutput",
        "CanvasStoryCandidateOutput",
        "CanvasStoryCriticOutput",
    ]
    assert len(repository.saved) == 3
    event_names = [name for name, _payload in repository.events]
    starts = [index for index, name in enumerate(event_names) if name == "prompt_started"]
    completions = [
        index for index, name in enumerate(event_names) if name == "prompt_completed"
    ]
    assert len(starts) == len(completions) == 6
    assert all(start < completion for start, completion in zip(starts, completions, strict=True))


def test_storyboard_director_creates_audited_provider_bounded_beats() -> None:
    repository = _Repository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    result = service.create_storyboard(repository.project_id)

    assert result["targetDurationSeconds"] == 60
    assert len(result["beats"]) == 5
    assert all(8 <= beat["durationSeconds"] <= 15 for beat in result["beats"])
    assert director.calls == ["CanvasStoryboardPlanOutput"]
    assert all(beat["promptId"] for beat in result["beats"])
    assert [name for name, _payload in repository.events].count("prompt_started") == 1
