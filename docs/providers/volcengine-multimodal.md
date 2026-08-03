# 火山方舟故事板组图与Seedance输入基线

本项目以仓库内火山方舟PDF、`docs/SKILL.md`和官方Seedream多图指南为开发依据。运行时不解析文档，能力边界由代码和请求体测试固定。

## 当前模型和输出

```text
ARK_IMAGE_MODEL=doubao-seedream-5-0-260128
ARK_VIDEO_MODEL=doubao-seedance-2-0-mini-260615
# 也可显式配置已开通的doubao-seedance-2-0-260128
ARK_VIDEO_RESOLUTION=480p或720p
```

视频为9:16、8～15秒整数、原生音频、关闭水印。模型切换会进入输入哈希，不复用旧收费步骤。

## Seedream故事板组图

每条Episode只发送一次组图请求：

```text
sequential_image_generation=auto
sequential_image_generation_options.max_images=3或4
```

输入默认选择3～5张必要素材：人物单视图、猫咪单视图、1～2张画风裁片和必要关键元素/场景图。整张人物三视图、带文字九宫格和弱相关素材不会直接发送。

Prompt使用`图1`、`图2`说明每个输入职责，并要求输出3～4张独立、无文字、无编号、无边框、9:16面板。响应中的每张图分别下载和哈希；数量不足或部分失败阻断视频。

## Seedance两种输入模式

### storyboard_reference

普通Episode按顺序发送全部3～4张批准面板，均映射为`reference_image`。Prompt中的`@图片N`与实际content数组顺序完全一致。

### strict_first_last

当`ending.visualCritical=true`时，只发送故事板第一张和最后一张，分别映射为`first_frame`和`last_frame`。中间面板用于导演规划与整组审核，不与严格帧混传。

人物、猫咪、画风和元素Canon不再额外发送给Seedance，因为这些信息已融合进批准故事板，避免重复输入分散注意力。

## Prompt方言

Seedance执行Prompt只描述本Episode：

1. 输出和二维画风。
2. 人物、猫咪、外观和场景。
3. 按`镜头1/镜头2/随后/最后`表达故事板顺序，不写绝对秒数。
4. 关键道具、位置、持有与容器连续性。
5. 原生声音及少量硬禁止。

素材使用`@图片N`绑定，裸数据库Asset ID不得进入Prompt。每镜最多一种运镜。本地保存字符数和UTF-8字节数，但不设置任意告警或阻断阈值，也不截断合法Prompt。

## 审核和成片

故事板整组先通过技术硬门，再由一次Responses请求判断身份、二维画风、动作顺序、关键连续性和结尾。角色数量、关键道具类别/数量、动作错序、座位来源、同场景服饰和结尾兑现属于硬门；普通背景、植物、轻微姿势、构图和面貌差异写入`warnings`。明确失败整组拒绝；低置信转人工。

Seedance仍以single-pass生成一条完整视频。技术QC通过后原始MP4直通本地资产目录，最终内容停在`content_review`等待人工批准。FFmpeg只用于抽帧诊断，不负责固定拼接。

## 资料

- [Seedance 2.0系列教程](../火山方舟_Doubao%20Seedance%202.0%20系列教程_1783419593.pdf)
- [Seedance 2.0提示词指南](../火山方舟_Doubao%20Seedance%202.0%20系列提示词指南_1784531609.pdf)
- [视频生成教程](../火山方舟_视频生成教程_1785228579.pdf)
- [Seedream 5.0 Pro教程](../火山方舟_Doubao%20Seedream%205.0%20pro%20教程_1784604838.pdf)
- [图片生成教程](../火山方舟_图片生成教程_1784082872.pdf)
- [本地Seedance Prompt Skill](../SKILL.md)
