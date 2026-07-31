"""系列人物、猫咪与画风的长期视觉档案。

本模块只描述跨 Run 稳定的视觉不变量，不保存服装、鞋帽、背包等剧情外观。
这些档案属于业务资产元数据，不从 ``.env`` 读取，也不依赖数据库或 Ark。
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field


class VisualProfileModel(BaseModel):
    """视觉档案的严格契约基类。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SeriesVisualProfile(VisualProfileModel):
    """固定人物与猫咪的系列级身份边界。"""

    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    person_identity: str = Field(min_length=8, max_length=300)
    person_hair: str = Field(min_length=4, max_length=160)
    person_body: str = Field(min_length=4, max_length=160)
    cat_identity: str = Field(min_length=8, max_length=300)
    mutable_appearance: tuple[str, ...] = (
        "衣服",
        "鞋",
        "帽子",
        "外套",
        "背包",
        "配饰",
    )
    forbidden_identity_rewrites: tuple[str, ...] = (
        "固定女孩",
        "固定男孩",
        "少女",
        "少年",
        "马尾",
        "发髻",
        "妆容",
    )

    def fingerprint(self) -> str:
        """生成事件种子筛选和规划幂等使用的稳定摘要。"""

        return hashlib.sha256(
            self.model_dump_json(exclude_none=True).encode("utf-8")
        ).hexdigest()


class StyleProfile(VisualProfileModel):
    """可执行的二维绘本画风定义与允许使用的参考资产语义键。"""

    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    positive_features: tuple[str, ...] = Field(min_length=3, max_length=10)
    excluded_features: tuple[str, ...] = Field(min_length=2, max_length=10)
    line_reference_key: str = "style:line_texture"
    indoor_reference_key: str = "style:indoor"
    outdoor_reference_key: str = "style:outdoor"

    def prompt_positive(self) -> str:
        return "、".join(self.positive_features)

    def prompt_negative(self) -> str:
        return "、".join(self.excluded_features)


DEFAULT_SERIES_VISUAL_PROFILE = SeriesVisualProfile(
    profile_id="neutral-child-gray-cat-v1",
    person_identity="同一个中性儿童，主要面貌和整体年龄感稳定，不强化男性或女性特征",
    person_hair="沿用批准人物本体的真实短发长度与发色，不无故增长或改变发型",
    person_body="保持相同儿童比例、身高感和体型，可由剧情自然换装",
    cat_identity="同一只灰白猫，脸型、体型、尾巴和主要灰白斑纹稳定可辨识",
)

DEFAULT_STYLE_PROFILE = StyleProfile(
    profile_id="storybook-pencil-v2",
    positive_features=(
        "二维儿童绘本",
        "彩铅和蜡笔颗粒",
        "可见手绘线条",
        "哑光平涂",
        "简化形体",
        "克制明暗",
    ),
    excluded_features=(
        "CG或PBR材质",
        "塑料高光",
        "3D体积塑形",
        "写实景深",
        "光滑商业动画渲染",
    ),
)
