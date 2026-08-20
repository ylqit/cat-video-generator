# Universal Media Canvas Design QA

- product-canvas source truth: `C:\Users\wwwab\AppData\Local\Temp\codex-clipboard-5ea08010-2c68-4a3d-893d-f5fedf3e55b2.png`
- video-editor source truth: `C:\Users\wwwab\AppData\Local\Temp\codex-clipboard-f51854ea-34fd-4d56-a3b4-e5403298148c.png`
- implemented product canvas: `D:\soft\code\OpenGit\cat-video-generator\design-qa-assets\universal-media-canvas-final.png`
- implemented compiled editor: `D:\soft\code\OpenGit\cat-video-generator\design-qa-assets\video-edit-workspace-compiled.png`
- combined product comparison: `D:\soft\code\OpenGit\cat-video-generator\design-qa-assets\comparison-product-canvas.png`
- combined editor comparison: `D:\soft\code\OpenGit\cat-video-generator\design-qa-assets\comparison-video-editor.png`
- verification viewport: 1600 × 1000 CSS px in the user-selected in-app browser
- verified route: `http://127.0.0.1:4173/canvas/project-demo`

## Combined comparison findings

The source and implementation were placed side by side in the two comparison images above before judging fidelity. The product view retains the source's dark infinite canvas, left-to-right references, compact batch candidate gallery, typed connectors, manual promotion and persistent generation control. The implementation deliberately continues the visible chain through selected image, video generation, video asset and local edit instead of stopping at the four image results.

The editor keeps the source's large media stage, text instruction, reference list and edit submission control, while adopting the requested full-screen dark production surface. The final verified state includes a normalized rectangle annotation, one 4.0-second interval, three explicitly included references and a visible two-stage Ark compilation plan with two image calls, one video call and estimated cost.

## Required fidelity surfaces

- Typography: neutral Chinese sans-serif hierarchy; small uppercase stage labels; compact metadata and strong action labels.
- Spacing: product references and batch candidates are the initial focus; later stages remain reachable by panning or the “聚焦全部节点” control.
- Color: black/charcoal canvas, restrained gray connectors, green ready/approved states and red-orange annotation geometry.
- Assets: all visible product/reference images are real image files; the preview video is a real 720 × 1280, 12-second MP4. No CSS art, placeholder boxes or emoji assets are used.
- Interaction: candidate promotion creates a distinct `ImageAssetNode`; video editing opens from `VideoAssetNode`; the annotation toolbar, interval inputs, reference inclusion and compile action all work.
- Audit: image/video nodes retain “查看 Prompt”; the execution path persists exact inputs before provider submission.

## Comparison history

### Iteration 1

- [P2] Fitting the complete nine-stage product chain made references and four candidates too small compared with the source.
- [P2] Remote hot-linked preview assets were inconsistent and one reference was routed outside the Vite proxy.
- [P2] The first demo video and its filmstrip did not depict the same product state.
- Fixes: initial product-stage focus at 0.7 zoom, local proxied real assets, and a consistent 12-second vertical product preview video.

### Iteration 2

- [P2] First annotation rectangle covered mostly empty background instead of the product label.
- Fix: redrew the normalized rectangle around the product face and recompiled, creating Recipe Revision 3 as expected.
- Result: no actionable P0/P1/P2 visual defect remains in the compared states.

## Interaction and runtime checks

- Loaded a product-ad template with product/model references, four batch candidates, one selected image, video, edit, review and timeline stages.
- Promoted a batch candidate and verified a second independent image card was created through the API; then reset the deterministic demo state.
- Opened the full-screen editor and verified all ten video elements report the same cache-busted source, 12-second duration and 720 × 1280 dimensions.
- Drew and undid/redrew a rectangle annotation; the undo state updated correctly.
- Entered the edit instruction and compiled the two-stage plan; the submit button became enabled only after cost and calls were shown.
- Browser logs contain no new error after the final reload; earlier hot-module/404 messages came from the preview server before its endpoint restart.

## Follow-up polish

- [P3] Split the large production bundle with route-level lazy imports; current build warns that the main JS chunk is about 1.46 MB.
- [P3] Add semantic review-frame extraction to the universal editor after the first real Ark end-to-end run.

final result: passed
