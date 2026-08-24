# Design QA — Canon-v3 角色素材库与显式预设节点

## Reference

- LibTV 素材库与角色库：用户现有 Chrome 标签页 `画布 1 - LibTV - 专业视频创作工具`。
- 录像：`C:\Users\wwwab\OneDrive\Desktop\素材角色库.mp4`。
- 关键帧：
  - `C:\Users\wwwab\AppData\Local\Temp\cvg-libtv-material-role-library-20260823\step-02.png`
  - `C:\Users\wwwab\AppData\Local\Temp\cvg-libtv-material-role-library-20260823\step-04.png`

## Implemented surface

- Canon-v3 必需证据槽位：儿童面部/全身、猫咪正面/侧面、线条材质。
- 角色库、风格库、最近使用三类视图与真实证据缩略图。
- “应用至画布”复用二进制资产并补建 Subject、版本化引用、节点、分组成员和血缘。
- 儿童、猫咪、画风专用证据编辑器；普通内容节点不再显示重复媒体工具条。
- VisualProfile 本集副本、版本冲突、必需 Canon 锁定及相关下游失效。

## Automated verification

- Backend: 244 tests passed.
- Frontend: 32 files / 94 tests passed.
- TypeScript typecheck: passed.
- Ruff: passed.
- Production build: passed; only the existing Vite chunk-size warning remains.
- `git diff --check`: passed; only line-ending notices were reported.

## Chrome comparison

- Passed: connected only to the user's existing Chrome window.
- Passed: inspected the existing LibTV canvas and opened its material-library entry without generating, applying, or consuming credits.
- Passed: verified the live LibTV role-library shell exposes a role filter, recent-use filter, application action, and an explicit empty state when the current project has no role material.
- Passed: restored the LibTV canvas after inspection.
- Blocked: the existing Chrome window did not contain a local cat-video-generator tab. Per the project constraint, no new tab or window was opened, so a same-viewport implementation screenshot could not be captured.
- Blocked: 1440×1024, 1920×1080, and 1280×720 live screenshot comparison remains pending until the user opens the local page in the existing Chrome window.

## Result

**Blocked for final visual sign-off.** Implementation and automated verification pass, and the LibTV reference behavior was inspected live. Final same-viewport visual acceptance is blocked only by the absence of an already-open local project tab in the user's Chrome window. Paid golden-sample validation was intentionally not executed.
