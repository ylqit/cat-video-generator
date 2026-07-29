"""CLI和HTTP共用的只读查询服务。"""

from __future__ import annotations

import uuid
from typing import Any

from .ports import StoredAsset, WorkflowRepository


class QueryService:
    """稳定工作流读模型，避免不同接口各自猜测状态。"""

    def __init__(self, repository: WorkflowRepository) -> None:
        self._repository = repository

    def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """分页返回 Run 摘要，供 CLI 与 HTTP 使用同一排序。"""

        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit必须在1至200之间且offset不能为负数")
        return self._repository.list_run_summaries(limit, offset)

    def run_graph(self, run_id: uuid.UUID) -> dict[str, Any]:
        """返回一个 Run 的 Episode、Step、Prompt、资产和审核关系图。"""

        return self._repository.workflow_graph(run_id)

    def prompt(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        """返回实际持久化且可审计的完整 Prompt。"""

        return self._repository.prompt_detail(prompt_id)

    def asset(self, asset_id: uuid.UUID) -> StoredAsset:
        """返回资产元数据；文件路径安全仍由接口层校验。"""

        return self._repository.asset_detail(asset_id)

    def episode(self, episode_id: uuid.UUID) -> dict[str, Any]:
        """返回单个 Episode 只读投影。"""

        return self._repository.episode_detail(episode_id)

    def step(self, step_id: uuid.UUID) -> dict[str, Any]:
        """返回收费意图、Ark task ID 与恢复状态。"""

        return self._repository.step_detail(step_id)

    def list_canon(self) -> list[dict[str, Any]]:
        """返回人物、猫咪和画风三类已批准Canon资产。"""

        assets = self._repository.list_assets(
            run_id=None,
            roles=("person", "cat", "style"),
            statuses=("approved", "ready"),
        )
        return [
            {
                "id": str(asset.id),
                "role": asset.role,
                "scope": asset.scope,
                "status": asset.status,
                "sha256": asset.sha256,
                "metadata": asset.metadata,
            }
            for asset in assets
        ]

    def list_deliveries(self, run_id: uuid.UUID) -> list[dict[str, Any]]:
        """返回一个 Run 的全部交付包及其条目。"""

        return self._repository.list_delivery_packages(run_id)

    def delivery_detail(self, package_id: uuid.UUID) -> dict[str, Any]:
        """返回单个交付包及其条目。"""

        return self._repository.delivery_package_detail(package_id)

    def health(self) -> dict[str, Any]:
        """返回数据库身份、版本、SSL 与迁移状态。"""

        return self._repository.health()
