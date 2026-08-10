"""Application层可向CLI/API稳定暴露的生命周期错误。"""

from __future__ import annotations

import uuid


class StepRetryRequired(RuntimeError):
    """原步骤已经终止，只有显式retry-step才能创建新attempt。"""

    def __init__(self, step_id: uuid.UUID, operation_key: str) -> None:
        super().__init__(
            f"步骤{step_id}已经终止；如需重做，请执行cvg retry-step {step_id} --reason <原因>"
        )
        self.step_id = step_id
        self.operation_key = operation_key
