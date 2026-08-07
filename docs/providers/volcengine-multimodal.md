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

人物外观先由一次Seedream单图请求融合`person:headshot + person:fullbody + 画风`成为日内定妆图；外观相同的后续时段复用该图。故事板输入默认选择3～5张必要素材：人物大头照、已批准定妆图、猫咪匹配视角、1～2张画风裁片，以及至多一张必要关键元素/场景图。整张人物三视图、带文字九宫格和弱相关素材不会直接发送。

Prompt使用`图1`、`图2`说明每个输入职责，并把内部semantic key翻译为人物面貌、服饰、猫咪斑纹或画风等模型可理解职责。它要求输出3～4张独立、无文字、无编号、无边框、9:16面板。原始比例偏差不超过1%直接使用，1%～2%按全组同一居中裁切归一化，超过2%、尺寸不一致、黑边或部分失败则阻断视频；原始文件和派生哈希均保留。

## Seedance三种输入模式

**供应商硬约束（真实请求验证）**：同一次请求内 `first_frame`/`last_frame`
不能与 `reference_image` 混用（Ark返回 `InvalidParameter: first/last frame
content cannot be mixed with reference media content`）。因此身份参考直喂
只出现在全参考模式；严格帧模式完全依赖首尾帧素材自身承载身份。

### storyboard_reference（含身份直喂）

普通Episode按顺序发送全部3～4张批准面板，均映射为`reference_image`；
人物大头照（锁面容发型本体，不锁衣着）与猫咪Canon同时作为
`reference_image`前置直喂，身份不再经故事板转译。Prompt中的`@图片N`
与实际content数组顺序完全一致，身份基准与面板构图职责分开描述。

### strict_first_last

当`ending.visualCritical=true`时，只发送故事板第一张和最后一张，分别映射为`first_frame`和`last_frame`。中间面板用于导演规划与整组审核，不与严格帧混传。

### per_shot逐镜头（默认，ARK_VIDEO_GENERATION_MODE=per_shot）

多镜头Episode逐镜头生成短片：镜头1以第一张面板为`first_frame`；
后续镜头以上一镜头已批准视频的**真实尾帧**（FFmpeg提取）为`first_frame`；
末镜头额外以最后一张面板为`last_frame`双端锚定。每镜头独立Seedance任务、
独立抽帧诊断（高置信自动批准、高置信违规带反馈重修一次、否则转人工），
全部批准后FFmpeg拼接为成片进入人工终审。单镜头时长不足
`ARK_SHOT_MINIMUM_SECONDS`（默认4秒）的Episode回退single-pass。
身份由首帧素材传递（故事板生成阶段已注入身份参考与四足姿态护栏）。

## Prompt方言

Seedance执行Prompt只描述本Episode：

1. 输出和二维画风。
2. 人物、猫咪、外观和场景。
3. 单镜头简单事件编译为一个聚焦自然段；多镜头事件按`镜头1/镜头2/随后/最后`表达顺序，不写绝对秒数。
4. 关键道具、位置、持有与容器连续性。
5. 原生声音及少量硬禁止。

素材使用`@图片N`绑定，裸数据库Asset ID不得进入Prompt。每镜最多一种运镜。本地保存字符数和UTF-8字节数，但不设置任意告警或阻断阈值，也不截断合法Prompt。

## 审核和成片

故事板整组先通过技术硬门，再由一次Responses请求判断身份、二维画风、动作顺序、关键连续性和结尾。角色数量、关键道具类别/数量、动作错序、座位来源、同场景服饰和结尾兑现属于硬门；普通背景、植物、轻微姿势、构图和面貌差异写入`warnings`。明确失败整组拒绝；低置信转人工。

single-pass与逐镜头两种模式都以技术QC为第一道门：通过后原始MP4直通本地
资产目录。single-pass成片直接进入`content_review`；逐镜头模式的镜头片段
先经抽帧诊断自动审（或人工审），全部批准后FFmpeg拼接成片再进入
`content_review`等待人工终审。FFmpeg负责抽帧诊断、尾帧提取与同规格拼接。

## 资料

- [Seedance 2.0系列教程](../火山方舟_Doubao%20Seedance%202.0%20系列教程_1783419593.pdf)
- [Seedance 2.0提示词指南](../火山方舟_Doubao%20Seedance%202.0%20系列提示词指南_1784531609.pdf)
- [视频生成教程](../火山方舟_视频生成教程_1785228579.pdf)
- [Seedream 5.0 Pro教程](../火山方舟_Doubao%20Seedream%205.0%20pro%20教程_1784604838.pdf)
- [图片生成教程](../火山方舟_图片生成教程_1784082872.pdf)
- [本地Seedance Prompt Skill](../SKILL.md)
