# 火山方舟多模态能力与Prompt基线

本项目以仓库内五份火山方舟PDF和`docs/SKILL.md`为开发依据。运行时不解析PDF，
能力边界由代码中的版本化档案和测试固定。

## 当前启用模型

```text
ARK_VIDEO_MODEL=doubao-seedance-2-0-mini-260615
ARK_IMAGE_MODEL=doubao-seedream-5-0-260128
ARK_VIDEO_RESOLUTION=720p
```

当前不做Fast、完整Seedance 2.0或Seedream 5.0 Pro自动路由。切换模型前必须新增
对应能力档案、请求快照测试和一次显式付费烟测，不能只修改模型字符串。

## Seedance Mini输入边界

- 产品视频时长为8～15秒整数；供应商能力为4～15秒。
- 当前输出只允许480p或720p、9:16、原生音频和关闭水印。
- 多模态参考最多9张图片、3个视频、3个音频。
- 不支持纯音频，也不支持只有文本和音频而没有视觉素材。
- 参考视频和参考音频各自必须为2～15秒。
- 单素材不超过30MB，请求素材总量不超过64MB。
- 输入图片边长为300～6000像素，宽高比为0.4～2.5。

业务层进一步限制一次多模态任务默认最多使用5项重要素材。素材越多不等于控制
越强；弱相关或冲突输入会分散模型注意力。

## 三种视频输入模式

### `multimodal_reference`

默认模式。人物、猫咪、画风、元素、动作和声音素材统一使用`reference_image`、
`reference_video`或`reference_audio`。关键帧也可以作为普通参考图，并在Prompt
中声明为语义开场或结尾。

该模式更适合同时保持角色身份、画风和动作意图，但不承诺输出首尾帧与参考图逐像素
一致。

### `strict_first_frame`

只发送一张`first_frame`。适合开场构图必须精确的内容，不混入其他参考媒体。

### `strict_first_last`

只发送`first_frame`与`last_frame`。适合包含、交接、封闭边界等结果状态必须准确的
内容。人物、猫咪和画风应先由Seedream融合进两张批准关键帧。

## Prompt方言

Seedream使用`图1`、`图2`描述输入图用途。

Seedance使用：

```text
@图片1
@视频1
@音频1
<主体1>
<主体2>
```

素材编号必须与Ark content数组顺序一致，裸数据库Asset ID不得进入Prompt。

简单单空间、单连续动作采用一个自然段。复杂事件采用：

1. 整体设定与素材绑定。
2. `镜头1/镜头2/镜头3`顺序，每镜一种运镜。
3. 质量、物理、声音和去重后的禁止项。

不输出`00:00-00:03`等绝对时间码。视频总时长由API的`duration`参数决定，Prompt
只表达先后和相对节奏。

## Seedream 5.0 Pro

官方Pro模型支持1K/2K、最多10张参考图以及`<point>/<bbox>`交互编辑，适合局部
文字、道具或定位修复。当前用户选择继续只使用Seedream Lite，因此本项目仅记录
该能力，不包含不可执行的Pro分支。

## 本地官方资料

- [Doubao Seedance 2.0系列教程](../火山方舟_Doubao%20Seedance%202.0%20系列教程_1783419593.pdf)
- [Doubao Seedance 2.0系列提示词指南](../火山方舟_Doubao%20Seedance%202.0%20系列提示词指南_1784531609.pdf)
- [视频生成教程](../火山方舟_视频生成教程_1785228579.pdf)
- [Doubao Seedream 5.0 Pro教程](../火山方舟_Doubao%20Seedream%205.0%20pro%20教程_1784604838.pdf)
- [图片生成教程](../火山方舟_图片生成教程_1784082872.pdf)
- [Seedance 2.0 Prompt Skill](../SKILL.md)
