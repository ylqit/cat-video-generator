"""单次Ark导演调用、幂等意图和候选持久化。

本模块只拥有一次外部调用的生命周期。全天顺序、自动修复次数和Run状态仍由
PlanningService决定，避免Ark异常策略与剧情编排混在同一个大模块中。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, TypeVar

from pydantic import ValidationError

from ..domain.contracts import Slot, StrictModel
from ..domain.workflow import StepKind, StepStatus
from .ports import DirectorGateway, GatewayError, StoredStep, WorkflowRepository

ContractT = TypeVar("ContractT", bound=StrictModel)


class DirectorCandidateRejected(RuntimeError):
    """Ark已返回候选，但候选未通过本地结构校验。"""

    def __init__(
        self,
        *,
        candidate: dict[str, Any],
        errors: tuple[str, ...],
        step: StoredStep,
        prompt_id: uuid.UUID,
    ) -> None:
        super().__init__("；".join(errors))
        self.candidate = candidate
        self.errors = errors
        self.step = step
        self.prompt_id = prompt_id


class DirectorInvoker:
    """执行一次导演请求，并确保Prompt和收费意图先于Ark调用落库。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        director: DirectorGateway,
        provider_name: str,
    ) -> None:
        self._repository = repository
        self._director = director
        self._provider_name = provider_name

    def invoke(
        self,
        *,
        run_id: uuid.UUID,
        episode_id: uuid.UUID | None,
        parent_step_id: uuid.UUID | None,
        parent_prompt_id: uuid.UUID | None,
        phase: str,
        slot: Slot | None,
        attempt: int,
        prompt: str,
        contract: type[ContractT],
        repair_of_step_id: uuid.UUID | None = None,
    ) -> tuple[ContractT, StoredStep, uuid.UUID]:
        """执行一个结构化导演调用；未知提交结果绝不自动重发。"""

        input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        summary: dict[str, Any] = {"phase": phase}
        if slot is not None:
            summary["slot"] = slot.value
        if repair_of_step_id is not None:
            summary["directorRepairAttempted"] = True
            summary["repairOfStepId"] = str(repair_of_step_id)
        step = self._repository.create_step_intent(
            run_id=run_id,
            episode_id=episode_id,
            parent_step_id=parent_step_id,
            kind=StepKind.DIRECTOR,
            attempt=attempt,
            provider=self._provider_name,
            model=self._director.model,
            input_hash=input_hash,
            request_summary=summary,
        )
        prompt_id = self._repository.save_prompt(
            step_id=step.id,
            parent_prompt_id=parent_prompt_id,
            purpose="director",
            model=self._director.model,
            text=prompt,
        )
        if step.status is StepStatus.SUCCEEDED:
            saved = step.request_summary.get("directorOutput")
            if not isinstance(saved, dict):
                raise RuntimeError("导演步骤已成功但缺少持久化输出")
            return contract.model_validate(saved), step, prompt_id

        # 先提交收费意图，再调用Ark。进程即使在响应前中断，恢复流程也能通过
        # submission_unknown阻止第二次POST。
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            result = self._director.generate_structured(
                prompt=prompt,
                schema=contract.model_json_schema(),
                output_name=contract.__name__,
            )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
            )
            if exc.submission_unknown:
                raise RuntimeError(
                    "导演提交结果未知，必须先对账，禁止重复调用"
                ) from exc
            raise
        try:
            parsed = contract.model_validate(result.payload)
        except ValidationError as exc:
            errors = (_validation_summary(exc),)
            self._repository.fail_director_step(
                step_id=step.id,
                response_id=result.response_id,
                request_hash=result.request_hash,
                output=result.payload,
                code="invalid_director_output",
                message=errors[0],
            )
            raise DirectorCandidateRejected(
                candidate=result.payload,
                errors=errors,
                step=self._repository.get_step(step.id),
                prompt_id=prompt_id,
            ) from exc
        self._repository.finish_director_step(
            step_id=step.id,
            response_id=result.response_id,
            request_hash=result.request_hash,
            output=result.payload,
        )
        return parsed, self._repository.get_step(step.id), prompt_id


def _validation_summary(exc: ValidationError) -> str:
    """把Pydantic错误压缩为可直接反馈给导演的稳定中文文本。"""

    return "；".join(
        f"{'.'.join(str(item) for item in error['loc'])}:{error['msg']}"
        for error in exc.errors(include_url=False)
    )
