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
    person_personality: str = Field(
        default="好奇心旺盛、做事认真，容易被小意外逗笑",
        min_length=8,
        max_length=200,
    )
    cat_personality: str = Field(
        default="表面高冷、其实贪玩，常常先假装不在意再忍不住凑近的反差萌",
        min_length=8,
        max_length=200,
    )
    humor_style: str = Field(
        default="每集至少一个意外、反差或幽默节拍，靠可见动作与表情呈现，不靠对白",
        min_length=8,
        max_length=200,
    )
    person_reference_keys: tuple[str, str] = (
        "person:headshot",
        "person:fullbody",
    )
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


class CreativeProfileOverride(VisualProfileModel):
    """单个Run可覆盖的行为偏好；不允许改写人物或猫咪身份。"""

    person_personality: str | None = Field(default=None, min_length=4, max_length=200)
    cat_personality: str | None = Field(default=None, min_length=4, max_length=200)
    humor_style: str | None = Field(default=None, min_length=4, max_length=200)

    def apply_to(self, profile: SeriesVisualProfile) -> SeriesVisualProfile:
        """在不改变Canon边界的前提下生成本Run有效档案。"""

        updates = self.model_dump(exclude_none=True)
        return profile.model_copy(update=updates) if updates else profile


class StyleProfile(VisualProfileModel):
    """可执行的系列画风定义与允许使用的参考资产语义键。"""

    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    positive_features: tuple[str, ...] = Field(min_length=3, max_length=10)
    excluded_features: tuple[str, ...] = Field(min_length=2, max_length=10)
    line_reference_key: str = "style:line_texture"
    indoor_reference_key: str = "style:indoor"
    outdoor_reference_key: str = "style:outdoor"

    @property
    def reference_keys(self) -> tuple[str, str, str]:
        """返回生产链唯一允许使用的定稿画风资产键。"""

        return (
            self.line_reference_key,
            self.indoor_reference_key,
            self.outdoor_reference_key,
        )

    def prompt_positive(self) -> str:
        return "、".join(self.positive_features)

    def prompt_negative(self) -> str:
        return "、".join(self.excluded_features)


DEFAULT_SERIES_VISUAL_PROFILE = SeriesVisualProfile(
    profile_id="final-neutral-short-hair-child-gray-cat-v1",
    person_identity=(
        "同一个偏中性呈现的东亚儿童，保持批准人物正面图中的柔和椭圆脸、五官比例、"
        "肤色和自然儿童年龄感，不强化男性或女性特征"
    ),
    person_hair=(
        "保持深棕黑色、齐耳至下颌长度的顺直短波波头与轻薄刘海，"
        "不得无故变成长发、马尾或发髻"
    ),
    person_body="保持约九至十二岁儿童的身高感、头身比例和纤细自然体型，可由剧情自然换装",
    cat_identity=(
        "同一只圆润灰白短毛猫，保持白色口鼻胸腹与四肢、灰色头顶和背部虎斑、"
        "灰白环纹、自然中等粗细且从后躯正常连接的尾巴、圆形琥珀棕眼睛及稳定体型"
    ),
    person_personality="好奇心旺盛、做事认真，容易被小意外逗笑",
    cat_personality="表面高冷、其实贪玩，常常先假装不在意再忍不住凑近的反差萌",
    humor_style="每集至少一个意外、反差或幽默节拍，靠可见动作与表情而非对白",
)

DEFAULT_STYLE_PROFILE = StyleProfile(
    profile_id="final-healing-2d-watercolor-v1",
    positive_features=(
        "日系二维治愈生活插画",
        "细腻干净的手绘轮廓线",
        "柔和哑光的水彩式数字绘制",
        "清新低至中饱和自然色",
        "温和自然光与空气透视",
        "克制景深和轻微远景虚化",
    ),
    excluded_features=(
        "真人写实摄影",
        "CG或PBR三维材质",
        "塑料高光",
        "强烈3D体积塑形",
        "过度油亮平滑表面",
        "高反差商业动画灯光",
        "光滑商业动画渲染",
    ),
)
