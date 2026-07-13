# ArkTS Code Reviewer 端到端示例 1

> 固定的 984 行 VideoPlayer 文件，经人工构造约 100 行 Diff 后，真实执行 ChangeSet → Parser → ReviewUnit → Unit Facts → Feature Routing → Context Planning → PostgreSQL staging KnowledgeIndex → GPU 向量/精确融合检索 → EvidencePack。

## 1. 汇报结论

- 链路完整性：**PASS**
- 语义检索质量：**NOT QUALIFIED（当前不能作为高质量评审知识）**
- 输入规模：base `984` 行，head `1051` 行，原始 Diff `+88/-21`。
- 正式变更行：新增 `83`、删除 `21`；另有 `5` 行纯空白排版保留在补丁中，但不伪造代码 owner。
- Parser：base/head 均为 `L1` / `L1`。
- ReviewUnit：`15` 个 source-role Unit；未分配 ChangeAtom `0` 个。
- Feature Routing：`24` 个 Unit/评审问题绑定。
- Context Plan：`18` 个 bundle，其中 `18` 个可派发。
- 知识索引：staging alias `staging-knowledge-seed-v1`，`109` 条 Draft Clause，GPU provider `CUDAExecutionProvider`。
- Retrieval：`15` 个检索 Unit，返回 `67` 条 Unit/Clause 证据关系；`production_eligible=false`。
- 检索结构信号：`19` 条不同 Clause；`1` 条含 Unit 精确匹配，`60` 条含文件级提示，`29` 条含向量召回。
- 向量相似度：中位数 `0.32736857`；`22/29` 条低于 `0.35`。
- 知识覆盖：要求的 dimension 实例 covered `0`、uncovered `9`；`15/15` 个 Unit 在知识 token 上限处截断候选。

本结果证明的是：各阶段正式数据结构可以贯通并找到候选知识。它**不是最终代码评审结论**；本示例没有执行 Rules、Prompt 组装或 LLM。
本样例也暴露了当前知识质量缺口：召回主要由宽泛的 `file_hints` 驱动，timer/network/AVSession 专项知识不足，不能把 67 条候选关系当成 67 条有效规范。

## 2. 输入与可复现性

- 原仓库：`applications_app_samples`
- 固定 revision：`8255a2987f70317cc3a2a4d46044c6b55f092bb3`
- 原始路径：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets`
- base SHA-256：`sha256:6d9f373ca3ea6cf1b0386f4e92dd9fe785cc421263e3d7c6500d5a35fb808c1a`
- synthetic head SHA-256：`sha256:8609950ac243718cf843ec7314c1725529bebb452d8d9d7a0e17acce076a5c60`
- 文件：[base.ets](inputs/base.ets) · [head.ets](inputs/head.ets) · [完整 Diff](inputs/diff.patch) · [变更说明](inputs/mutation_spec.json) · [来源证明](inputs/provenance.json) · [ReviewUnit 人工 expected](inputs/expected_review_units.json)

## 3. 链路与阶段产物

```text
base/head source
  → SequenceMatcher(autojunk=False) / ChangeSet
  → Parser L1 (base 与 head 各解析一次)
  → ReviewUnitBuild v3 (base/head owner 分开保留)
  → UnitFactScope (unit_exact 与 file_hints 分离)
  → Feature Routing (Tags → Dimensions → Review Questions)
  → Context Plan (Primary Unit / bundle / 代码预算)
  → RetrievalRequest (精确信号 + 小型 semantic excerpt)
  → PostgreSQL staging KnowledgeIndex + CUDA embedding
  → Exact + Vector + RRF
  → EvidencePack (候选知识，不是 Finding)
```

| 阶段 | 完整机器可读结果 |
|---|---|
| 运行配置 | [artifacts/00_run_manifest.json](artifacts/00_run_manifest.json) |
| ChangeSet | [artifacts/01_change_set.json](artifacts/01_change_set.json) |
| Parser base | [artifacts/02_parser_base.json](artifacts/02_parser_base.json) |
| Parser head | [artifacts/03_parser_head.json](artifacts/03_parser_head.json) |
| ReviewUnit build | [artifacts/04_review_unit_build.json](artifacts/04_review_unit_build.json) |
| Unit facts | [artifacts/05_unit_fact_scopes.json](artifacts/05_unit_fact_scopes.json) |
| Feature Routing | [artifacts/06_feature_routing.json](artifacts/06_feature_routing.json) |
| Context Plan | [artifacts/07_context_plan.json](artifacts/07_context_plan.json) |
| RetrievalRequest | [artifacts/08_retrieval_request.json](artifacts/08_retrieval_request.json) |
| KnowledgeIndex 摘要 | [artifacts/09_knowledge_index_summary.json](artifacts/09_knowledge_index_summary.json) |
| EvidencePack | [artifacts/10_evidence_pack.json](artifacts/10_evidence_pack.json) |
| 端到端断言 | [artifacts/11_assertions.json](artifacts/11_assertions.json) |
| 汇总 | [artifacts/12_summary.json](artifacts/12_summary.json) |

## 4. ChangeSet：Diff 被标准化成什么

`change_set_id`: `change-set:sha256:92e6e64f2abcd12eb96ac7dff47df1b30d4211d08a8e27b44ad00a4a47220761`

| # | kind | base 行 | head 行 | ChangeAtom ID |
|---:|---|---|---|---|
| 1 | addition | — | 67-69 | `change-atom:sha256:…1c04476523838611` |
| 2 | addition | — | 76-77 | `change-atom:sha256:…025a8a3c17a8e1db` |
| 3 | addition | — | 121, 123-135 | `change-atom:sha256:…a6792eb2ed5bc4c2` |
| 4 | addition | — | 165 | `change-atom:sha256:…199c3df45d294577` |
| 5 | addition | — | 172, 174-179, 181-201 | `change-atom:sha256:…f0a9fb5b145ef26e` |
| 6 | addition | — | 294-300, 302-307 | `change-atom:sha256:…de462a7473c6d297` |
| 7 | addition | — | 311-313 | `change-atom:sha256:…c60f8bc87e74b8d9` |
| 8 | addition | — | 339-343 | `change-atom:sha256:…123daf986804c28e` |
| 9 | addition | — | 1010-1012 | `change-atom:sha256:…cc5c1406e4333d21` |
| 10 | replacement | 90-104 | 95 | `change-atom:sha256:…e61f289c444336da` |
| 11 | replacement | 162-165 | 169-170 | `change-atom:sha256:…d1b49b843e9caa52` |
| 12 | replacement | 953 | 1014-1017 | `change-atom:sha256:…0e2a1e4a52645682` |
| 13 | replacement | 956 | 1020-1023 | `change-atom:sha256:…500f7ffec0336405` |

## 5. Parser：完整文件结构与事实

Parser 对 base/head 各执行一次，行号全部是源文件 1-based 绝对行号。

| side | layer | ERROR | missing | declarations | review regions | facts | warnings |
|---|---|---:|---:|---:|---:|---:|---|
| base | L1 | 0 | 0 | 51 | 50 | 1435 | — |
| head | L1 | 0 | 0 | 56 | 53 | 1495 | — |

## 6. ReviewUnit：Diff 最终切成哪些代码段

同一个 replacement 会在 base 与 head 各形成 owner Unit，便于后续比较变更前后。`full_text` 的完整正文位于 04 JSON；下表展示身份、范围和归属。

| side | kind / symbol | source span | context span | changed lines | atoms | reason | diagnostics |
|---|---|---|---|---|---:|---|---|
| base | `method` / `Index.aboutToAppear` | L71-L130 | L71-L130 | 90-104 | 1 | innermost_changed_declaration | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.aboutToAppear:L71-L130:Rbase:S040c6b57210863e01ca2d71363032cb3a5927b59b9da00b3fca43c8124c4125a -->
| base | `method` / `Index.addNetworkListener` | L146-L167 | L146-L167 | 162-165 | 1 | innermost_changed_declaration | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.addNetworkListener:L146-L167:Rbase:S040c6b57210863e01ca2d71363032cb3a5927b59b9da00b3fca43c8124c4125a -->
| base | `ui_block` / `Index.build.Column.Flex.Row.Flex.Flex.Slider` | L913-L959 | L913-L959 | 953, 956 | 2 | large_build_ui_block | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@ui_block:Index.build.Column.Flex.Row.Flex.Flex.Slider:L913-L959:Rbase:S040c6b57210863e01ca2d71363032cb3a5927b59b9da00b3fca43c8124c4125a -->
| head | `field_region` / `Index.networkRetryTimer` | L67-L67 | L67-L67 | 67 | 1 | changed_review_region | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@field_region:Index.networkRetryTimer:L67-L67:O3021-3055:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `field_region` / `Index.isDisposed` | L68-L68 | L68-L68 | 68 | 1 | changed_review_region | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@field_region:Index.isDisposed:L68-L68:O3059-3094:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `field_region` / `Index.networkRetryDelayMs` | L69-L69 | L69-L69 | 69 | 1 | changed_review_region | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@field_region:Index.networkRetryDelayMs:L69-L69:O3098-3149:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `method` / `Index.aboutToAppear` | L74-L121 | L74-L121 | 76-77, 95, 121 | 3 | innermost_changed_declaration | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.aboutToAppear:L74-L121:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `method` / `Index.updatePlaybackProgress` | L123-L136 | L123-L136 | 123-135 | 1 | innermost_changed_declaration | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.updatePlaybackProgress:L123-L136:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `method` / `Index.addNetworkListener` | L152-L172 | L152-L172 | 165, 169-170, 172 | 3 | innermost_changed_declaration | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.addNetworkListener:L152-L172:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `method` / `Index.clearNetworkRetryTimer` | L174-L179 | L174-L179 | 174-179 | 1 | innermost_changed_declaration | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.clearNetworkRetryTimer:L174-L179:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `method` / `Index.scheduleNetworkRecovery` | L181-L202 | L181-L202 | 181-201 | 1 | innermost_changed_declaration | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.scheduleNetworkRecovery:L181-L202:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `method` / `Index.clearRuntimeTimers` | L294-L300 | L294-L300 | 294-300 | 1 | innermost_changed_declaration | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.clearRuntimeTimers:L294-L300:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `method` / `Index.removePlayerListeners` | L302-L307 | L302-L307 | 302-307 | 1 | innermost_changed_declaration | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.removePlayerListeners:L302-L307:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `method` / `Index.aboutToDisappear` | L309-L344 | L309-L344 | 311-313, 339-343 | 2 | innermost_changed_declaration | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.aboutToDisappear:L309-L344:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->
| head | `ui_block` / `Index.build.Column.Flex.Row.Flex.Flex.Slider` | L971-L1026 | L971-L1026 | 1010-1012, 1014-1017, 1020-1023 | 3 | large_build_ui_block | — |
<!-- unit_id: code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@ui_block:Index.build.Column.Flex.Row.Flex.Flex.Slider:L971-L1026:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68 -->

## 7. Unit Facts：代码段自身事实与文件提示分开

`unit_exact` 是能定位到该 Unit span 的事实，可参与精确检索；`file_hints` 只是整文件提示，只能保守扩大路由，不能直接当 Finding 证据。

| side / symbol | unit_exact APIs | calls | exact decorators | file_hints APIs | diagnostics |
|---|---|---|---|---|---|
| base / `Index.aboutToAppear` | promptAction.showToast | Log.info, newDate().getTime, this.addNetworkListener, this.audioUtils.init, this.audioUtils.setVideoLoadedCallback, this.autoStartAll, this.avPlayer?.on, this.avPlayer?.pause, this.readLRCFile, this.session?.setAVMetadata, this.session?.setAVPlaybackState, this.setAudioManager, this.setPlayState | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| base / `Index.addNetworkListener` | — | JSON.stringify, Log.error, Log.info, connection.createNetConnection, connection.getAllNets, connection.getAllNets().then, this.netCon?.on, this.netCon?.register | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| base / `Index.build.Column.Flex.Row.Flex.Flex.Slider` | clearTimeout, setTimeout | JSON.stringify, Log.info, Math.floor, Slider, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor('rgba(255,255,255,1)').trackColor, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor('rgba(255,255,255,1)').trackColor('rgba(255,255,255,0.3)').selectedColor, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor('rgba(255,255,255,1)').trackColor('rgba(255,255,255,0.3)').selectedColor('rgba(255,255,255,0.9)').showSteps, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor('rgba(255,255,255,1)').trackColor('rgba(255,255,255,0.3)').selectedColor('rgba(255,255,255,0.9)').showSteps(false).showTips, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor('rgba(255,255,255,1)').trackColor('rgba(255,255,255,0.3)').selectedColor('rgba(255,255,255,0.9)').showSteps(false).showTips(false).onChange, mode.toString, newDate().getTime, this.avPlayer?.seek, this.castController?.sendControlCommand, this.session?.setAVPlaybackState | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.networkRetryTimer` | — | — | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.isDisposed` | — | — | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.networkRetryDelayMs` | — | — | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.aboutToAppear` | promptAction.showToast | Log.info, this.addNetworkListener, this.audioUtils.init, this.audioUtils.setVideoLoadedCallback, this.autoStartAll, this.avPlayer?.on, this.avPlayer?.pause, this.clearRuntimeTimers, this.readLRCFile, this.session?.setAVMetadata, this.setAudioManager, this.setPlayState, this.updatePlaybackProgress | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.updatePlaybackProgress` | — | Log.info, newDate().getTime, this.session?.setAVPlaybackState | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.addNetworkListener` | — | JSON.stringify, Log.error, Log.info, connection.createNetConnection, connection.getAllNets, connection.getAllNets().then, this.clearNetworkRetryTimer, this.netCon?.on, this.netCon?.register, this.scheduleNetworkRecovery | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.clearNetworkRetryTimer` | clearTimeout | — | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.scheduleNetworkRecovery` | setTimeout | JSON.stringify, Log.error, connection.getAllNets, connection.getAllNets().then, connection.getAllNets().then(data=&gt;{this.hasNetwork=data?.length&gt;0;if(!this.hasNetwork){this.scheduleNetworkRecovery();}}).catch, this.clearNetworkRetryTimer, this.scheduleNetworkRecovery | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.clearRuntimeTimers` | clearTimeout | this.clearNetworkRetryTimer | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.removePlayerListeners` | — | this.avPlayer?.off | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.aboutToDisappear` | — | JSON.stringify, Log.info, this.avPlayer?.release, this.castController?.off, this.clearRuntimeTimers, this.controller.destroy, this.controller.off, this.netCon?.unregister, this.removePlayerListeners, this.session?.destroy, this.session?.stopCasting | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |
| head / `Index.build.Column.Flex.Row.Flex.Flex.Slider` | clearTimeout, setTimeout | JSON.stringify, Log.info, Math.floor, Slider, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor('rgba(255,255,255,1)').trackColor, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor('rgba(255,255,255,1)').trackColor('rgba(255,255,255,0.3)').selectedColor, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor('rgba(255,255,255,1)').trackColor('rgba(255,255,255,0.3)').selectedColor('rgba(255,255,255,0.9)').showSteps, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor('rgba(255,255,255,1)').trackColor('rgba(255,255,255,0.3)').selectedColor('rgba(255,255,255,0.9)').showSteps(false).showTips, Slider({value:this.seedPosition,min:0,max:100,style:SliderStyle.OutSet}).trackThickness(4).blockColor('rgba(255,255,255,1)').trackColor('rgba(255,255,255,0.3)').selectedColor('rgba(255,255,255,0.9)').showSteps(false).showTips(false).onChange, mode.toString, newDate().getTime, this.avPlayer?.seek, this.castController?.sendControlCommand, this.session?.setAVPlaybackState | — | $r, audio.getAudioManager, clearTimeout, fs.open, promptAction.showToast, setTimeout | — |

## 8. Feature Routing：Tags、Dimensions 和评审问题

| side / symbol | exact tags | routing tags | retrieval dimensions | routing dimensions | review questions |
|---|---|---|---|---|---|
| base / `Index.aboutToAppear` | has_async | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | DIM-07 | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-concurrency, RQ-correctness |
| base / `Index.addNetworkListener` | — | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | — | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness |
| base / `Index.build.Column.Flex.Row.Flex.Flex.Slider` | has_interactive_component, has_timer | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | DIM-06, DIM-08 | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-accessibility, RQ-correctness, RQ-resource |
| head / `Index.networkRetryTimer` | — | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | — | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness |
| head / `Index.isDisposed` | — | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | — | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness |
| head / `Index.networkRetryDelayMs` | — | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | — | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness |
| head / `Index.aboutToAppear` | has_async | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | DIM-07 | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-concurrency, RQ-correctness |
| head / `Index.updatePlaybackProgress` | — | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | — | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness |
| head / `Index.addNetworkListener` | — | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | — | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness |
| head / `Index.clearNetworkRetryTimer` | has_timer | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | DIM-06 | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness, RQ-resource |
| head / `Index.scheduleNetworkRecovery` | has_timer | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | DIM-06 | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness, RQ-resource |
| head / `Index.clearRuntimeTimers` | has_timer | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | DIM-06 | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness, RQ-resource |
| head / `Index.removePlayerListeners` | — | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | — | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness |
| head / `Index.aboutToDisappear` | — | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | — | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-correctness |
| head / `Index.build.Column.Flex.Row.Flex.Flex.Slider` | has_interactive_component, has_timer | has_async, has_file_io, has_image, has_interactive_component, has_layout, has_lifecycle, has_media, has_resource_ref, has_state_management, has_text_display, has_timer | DIM-06, DIM-08 | DIM-06, DIM-07, DIM-08, DIM-09, DIM-10 | RQ-accessibility, RQ-correctness, RQ-resource |

## 9. Context Plan：哪些代码会进入后续评审上下文

代码预算 `32768` tokens；Primary `10698`；Supporting `0`。

| bundle | primary Units | questions | supporting | tokens | dispatch | diagnostics |
|---|---:|---:|---:|---:|---|---|
| `review-context-bundle:sha256:…75a5ed7eeb496b04` | 1 | 1 | 0 | 191/32768 | true | — |
| `review-context-bundle:sha256:…0e163aabe4507251` | 1 | 1 | 0 | 74/32768 | true | — |
| `review-context-bundle:sha256:…268b5d53f2663fee` | 2 | 2 | 0 | 1713/32768 | true | — |
| `review-context-bundle:sha256:…adb5354053c1c47a` | 1 | 1 | 0 | 21/32768 | true | — |
| `review-context-bundle:sha256:…ec7a4b307a2cb0a6` | 1 | 1 | 0 | 17/32768 | true | — |
| `review-context-bundle:sha256:…e401c11d4ba15673` | 2 | 2 | 0 | 1677/32768 | true | — |
| `review-context-bundle:sha256:…1aec0ba1195274f1` | 1 | 1 | 0 | 422/32768 | true | — |
| `review-context-bundle:sha256:…b86c6378c1b8436a` | 2 | 2 | 0 | 1677/32768 | true | — |
| `review-context-bundle:sha256:…1764ea537063a3b4` | 2 | 2 | 0 | 1677/32768 | true | — |
| `review-context-bundle:sha256:…9b8f0181a78832e2` | 1 | 1 | 0 | 80/32768 | true | — |
| `review-context-bundle:sha256:…003e3e4f9c51de32` | 1 | 1 | 0 | 262/32768 | true | — |
| `review-context-bundle:sha256:…2cba3e3f1044293d` | 1 | 1 | 0 | 15/32768 | true | — |
| `review-context-bundle:sha256:…529aeb379c5a002c` | 2 | 2 | 0 | 664/32768 | true | — |
| `review-context-bundle:sha256:…0c35257befdf9fd8` | 1 | 1 | 0 | 262/32768 | true | — |
| `review-context-bundle:sha256:…cd1a1a05ef7dc01a` | 1 | 1 | 0 | 74/32768 | true | — |
| `review-context-bundle:sha256:…43ffb89908f0cee9` | 2 | 2 | 0 | 1713/32768 | true | — |
| `review-context-bundle:sha256:…d62a7051f1d3e3a9` | 1 | 1 | 0 | 79/32768 | true | — |
| `review-context-bundle:sha256:…650680369c9426b5` | 1 | 1 | 0 | 80/32768 | true | — |

本次 `supporting_segments=0`：它验证了所有直接变更 owner 的组织与预算，但没有证明相关未修改 helper/调用方的扩展召回能力。

## 10. RetrievalRequest：真正发给检索器的内容

完整 ReviewUnit 不会被直接拿去做向量查询；检索器使用精确信号、路由结果和最多 16 行/1600 字符的 `semantic_code_excerpt`。完整字段见 08 JSON。

### head · `Index.isDisposed`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@field_region:Index.isDisposed:L68-L68:O3059-3094:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-correctness`
- Exact tags：`—`
- Retrieval dimensions：`—`
- Knowledge budget：`547`
- Semantic excerpt：

```text
L68: private isDisposed: boolean = false;
```

### head · `Index.networkRetryDelayMs`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@field_region:Index.networkRetryDelayMs:L69-L69:O3098-3149:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-correctness`
- Exact tags：`—`
- Retrieval dimensions：`—`
- Knowledge budget：`547`
- Semantic excerpt：

```text
L69: private readonly networkRetryDelayMs: number = 1000;
```

### head · `Index.networkRetryTimer`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@field_region:Index.networkRetryTimer:L67-L67:O3021-3055:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-correctness`
- Exact tags：`—`
- Retrieval dimensions：`—`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L67: private networkRetryTimer?: number;
```

### base · `Index.aboutToAppear`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.aboutToAppear:L71-L130:Rbase:S040c6b57210863e01ca2d71363032cb3a5927b59b9da00b3fca43c8124c4125a`
- Review questions：`RQ-concurrency, RQ-correctness`
- Exact tags：`has_async`
- Retrieval dimensions：`DIM-07`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L89: this.avPlayer?.on('timeUpdate', (time: number) => { | L90: Log.info('timeUpdate time: ' + time); | L91: if (!this.isProgressSliding) { | L92: if (this.duration == 0) { | L93: this.seedPosition = 0; | L94: } else { | L95: this.seedPosition = time / this.duration * 100; | L96: } | L98: position: { | L99: elapsedTime: time, | L100: updateTime: new Date().getTime() | L101: }, | L102: }; | L103: this.session?.setAVPlaybackState(params); | L104: } | L105: })
```

### head · `Index.aboutToAppear`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.aboutToAppear:L74-L121:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-concurrency, RQ-correctness`
- Exact tags：`has_async`
- Retrieval dimensions：`DIM-07`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L75: Log.info('about to appear'); | L76: this.isDisposed = false; | L77: this.clearRuntimeTimers(); | L78: this.songList = this.urlVideoList; | L94: this.avPlayer?.on('timeUpdate', (time: number) => { | L95: this.updatePlaybackProgress(time); | L96: }) | L120: Log.info('about to appear done: ' + !!this.avPlayer); | L121: }
```

### head · `Index.aboutToDisappear`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.aboutToDisappear:L309-L344:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-correctness`
- Exact tags：`—`
- Retrieval dimensions：`—`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L310: Log.info('about to disappear'); | L311: this.isDisposed = true; | L312: this.clearRuntimeTimers(); | L313: this.removePlayerListeners(); | L314: if (this.controller) { | L338: }) | L339: this.netCon = undefined; | L340: this.controller = undefined; | L341: this.castController = undefined; | L342: this.session = undefined; | L343: this.avPlayer = undefined; | L344: }
```

### base · `Index.addNetworkListener`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.addNetworkListener:L146-L167:Rbase:S040c6b57210863e01ca2d71363032cb3a5927b59b9da00b3fca43c8124c4125a`
- Review questions：`RQ-correctness`
- Exact tags：`—`
- Retrieval dimensions：`—`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L161: Log.info('network Lost: ' + JSON.stringify(data)); | L162: connection.getAllNets().then(data => { | L163: Log.info('get all network: ' + JSON.stringify(data)); | L164: this.hasNetwork = data?.length > 0; | L165: }); | L166: })
```

### head · `Index.addNetworkListener`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.addNetworkListener:L152-L172:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-correctness`
- Exact tags：`—`
- Retrieval dimensions：`—`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L164: this.hasNetwork = true; | L165: this.clearNetworkRetryTimer(); | L166: }) | L168: Log.info('network Lost: ' + JSON.stringify(data)); | L169: this.hasNetwork = false; | L170: this.scheduleNetworkRecovery(); | L171: }) | L172: }
```

### head · `Index.clearNetworkRetryTimer`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.clearNetworkRetryTimer:L174-L179:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-correctness, RQ-resource`
- Exact tags：`has_timer`
- Retrieval dimensions：`DIM-06`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L174: private clearNetworkRetryTimer(): void { | L175: if (this.networkRetryTimer !== undefined) { | L176: clearTimeout(this.networkRetryTimer); | L177: this.networkRetryTimer = undefined; | L178: } | L179: }
```

### head · `Index.clearRuntimeTimers`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.clearRuntimeTimers:L294-L300:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-correctness, RQ-resource`
- Exact tags：`has_timer`
- Retrieval dimensions：`DIM-06`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L294: private clearRuntimeTimers(): void { | L295: if (this.sliderTimer !== undefined) { | L296: clearTimeout(this.sliderTimer); | L297: this.sliderTimer = undefined; | L298: } | L299: this.clearNetworkRetryTimer(); | L300: }
```

### head · `Index.removePlayerListeners`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.removePlayerListeners:L302-L307:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-correctness`
- Exact tags：`—`
- Retrieval dimensions：`—`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L302: private removePlayerListeners(): void { | L303: this.avPlayer?.off('audioInterrupt'); | L304: this.avPlayer?.off('timeUpdate'); | L305: this.avPlayer?.off('durationUpdate'); | L306: this.avPlayer?.off('videoSizeChange'); | L307: }
```

### head · `Index.scheduleNetworkRecovery`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.scheduleNetworkRecovery:L181-L202:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-correctness, RQ-resource`
- Exact tags：`has_timer`
- Retrieval dimensions：`DIM-06`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L181: private scheduleNetworkRecovery(): void { | L182: this.clearNetworkRetryTimer(); | L184: return; | L185: } | L187: if (this.isDisposed) { | L188: return; | L189: } | L191: .then(data => { | L192: this.hasNetwork = data?.length > 0; | L194: this.scheduleNetworkRecovery(); | L195: } | L196: }) | L198: Log.error('network recovery failed: ' + JSON.stringify(error)); | L199: this.scheduleNetworkRecovery(); | L201: }, this.networkRetryDelayMs); | L202: }
```

### head · `Index.updatePlaybackProgress`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.updatePlaybackProgress:L123-L136:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-correctness`
- Exact tags：`—`
- Retrieval dimensions：`—`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L123: private updatePlaybackProgress(time: number): void { | L124: if (this.isDisposed || this.isProgressSliding) { | L125: return; | L126: } | L127: Log.info('timeUpdate time: ' + time); | L128: this.seedPosition = this.duration === 0 ? 0 : time / this.duration * 100; | L129: const params: avSession.AVPlaybackState = { | L130: position: { | L131: elapsedTime: time, | L132: updateTime: new Date().getTime() | L133: }, | L134: }; | L135: this.session?.setAVPlaybackState(params); | L136: }
```

### base · `Index.build.Column.Flex.Row.Flex.Flex.Slider`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@ui_block:Index.build.Column.Flex.Row.Flex.Flex.Slider:L913-L959:Rbase:S040c6b57210863e01ca2d71363032cb3a5927b59b9da00b3fca43c8124c4125a`
- Review questions：`RQ-accessibility, RQ-correctness, RQ-resource`
- Exact tags：`has_interactive_component, has_timer`
- Retrieval dimensions：`DIM-06, DIM-08`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L952: this.sliderTimer = setTimeout(() => { | L953: this.isProgressSliding = false; | L954: }, 200); | L955: } else { | L956: clearTimeout(this.sliderTimer); | L957: this.isProgressSliding = true;
```

### head · `Index.build.Column.Flex.Row.Flex.Flex.Slider`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@ui_block:Index.build.Column.Flex.Row.Flex.Flex.Slider:L971-L1026:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Review questions：`RQ-accessibility, RQ-correctness, RQ-resource`
- Exact tags：`has_interactive_component, has_timer`
- Retrieval dimensions：`DIM-06, DIM-08`
- Knowledge budget：`546`
- Semantic excerpt：

```text
L1009: if (event.type === TouchType.Up) { | L1010: if (this.sliderTimer !== undefined) { | L1011: clearTimeout(this.sliderTimer); | L1012: } | L1013: this.sliderTimer = setTimeout(() => { | L1014: if (!this.isDisposed) { | L1015: this.isProgressSliding = false; | L1016: } | L1017: this.sliderTimer = undefined; | L1018: }, 200); | L1019: } else { | L1020: if (this.sliderTimer !== undefined) { | L1021: clearTimeout(this.sliderTimer); | L1022: this.sliderTimer = undefined; | L1023: } | L1024: this.isProgressSliding = true;
```

## 11. KnowledgeIndex：检索所用知识

- PostgreSQL alias：`staging-knowledge-seed-v1`
- Index version：`knowledge-index:sha256:aa792a335c07f03f740f8f0f790f16782c7dba93bc4c94af6666214b985e291a`
- Origin：`evaluation_fixture`
- Knowledge build：`evaluation-knowledge:sha256:91fce00dabed89136dce40eaab881f33037ad5b1099bf81956e93b956805d2dc`
- Clause：`109` 条，状态全部为 Draft
- Embedding：`jinaai/jina-embeddings-v2-base-code` / `768` 维
- 实际执行 provider：`CUDAExecutionProvider`
- Production eligible：`false`

为避免在汇报目录重复保存 109×768 个浮点数，09 JSON 只保存索引元数据、统计和 rule_id；运行时已对数据库加载出的完整 KnowledgeIndex 做正式 loader 往返校验。

## 12. EvidencePack：每个 Unit 最后搜到的知识

下列 Clause 是候选知识证据。`vector_rank` 表示向量召回名次；`matched_by` 同时展示 API/Tag/Dimension 等精确匹配。完整正文、来源与分数见 10 JSON。

### head · `Index.isDisposed`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@field_region:Index.isDisposed:L68-L68:O3059-3094:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `—`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理V1现状以及V2优点:01` | 状态变量不能独立于UI存在，同一个数据被多个视图代理时，其中一个视图的更改不会通知其他视图更新。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 8/3 | 0.30582087 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:装饰器总览:01@heading-54c036c9f066` | \[\@Once\](arkts-new-once.md)：\@Once装饰的变量仅初始化时同步一次，需要与\@Param一起使用。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 14/1 | 0.42827633 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:装饰器总览:01@heading-b7ac1cd733ef` | \[\@Provide/\@Consume\](arkts-provide-and-consume.md)：\@Provide/\@Consume装饰的变量用于跨组件层级（多层组件）同步状态变量，可以不需要通过参数命名机制传递，通过alias（别名）或者属性名绑定。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 15/2 | 0.33698279 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:装饰器总览:02` | \[\@Observed\](arkts-observed-and-objectlink.md)：\@Observed装饰class，需要观察多层嵌套场景的class需要被\@Observed装饰。单独使用\@Observed没有任何作用，需要和\@ObjectLink、\@Prop联用。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 16/4 | 0.30358321 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 5 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |

### head · `Index.networkRetryDelayMs`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@field_region:Index.networkRetryDelayMs:L69-L69:O3098-3149:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `—`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/arkts-utils/sendable-constraints.md:Sendable类的内部不允许使用当前模块内上下文环境中定义的变量:01` | Sendable类的内部不允许使用当前模块内上下文环境中定义的变量 | vector:jinaai/jina-embeddings-v2-base-code (semantic) | None/1 | 0.33784303 | `zh-cn/application-dev/arkts-utils/sendable-constraints.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:02` | \[\@StorageLink\](./arkts-appstorage.md#storagelink)/\[\@StorageProp\](./arkts-appstorage.md#storageprop)/\[\@LocalStorageLink\](./arkts-localstorage.md#localstoragelink)/\[\@LocalStorageProp\](./arkts-localstorage.md#localstorageprop)/\[\@Consume\](./arkts-provide-and-consume.md)变量的初始化规则为不可以被外部初始化，当组件开发者希望当前变量被外部初始化而使用public修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 2/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:03` | \[\@Link\](./arkts-link.md)/\[\@ObjectLink\](./arkts-observed-and-objectlink.md)变量的初始化规则为必须被外部初始化，禁止本地初始化。当组件开发者使用private对变量进行修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 3/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |

### head · `Index.networkRetryTimer`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@field_region:Index.networkRetryTimer:L67-L67:O3021-3055:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `—`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:自定义组件成员属性访问限定符使用限制:01` | 在状态管理V1版本中，完成自定义组件封装后，调用方难以明确知晓应传入哪些变量作为组件的输入参数。当组件开发者不希望状态变量被外部初始化时，可以使用private限定符来限制当前变量不允许被外部初始化。外部初始化也需要遵循装饰器自身的规则，具体规则见\[使用限制\](#使用限制)。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 5/4 | 0.30199966 | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理V1现状以及V2优点:01` | 状态变量不能独立于UI存在，同一个数据被多个视图代理时，其中一个视图的更改不会通知其他视图更新。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 8/3 | 0.30382068 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:装饰器总览:01@heading-54c036c9f066` | \[\@Once\](arkts-new-once.md)：\@Once装饰的变量仅初始化时同步一次，需要与\@Param一起使用。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 14/2 | 0.33367176 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |

### base · `Index.aboutToAppear`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.aboutToAppear:L71-L130:Rbase:S040c6b57210863e01ca2d71363032cb3a5927b59b9da00b3fca43c8124c4125a`
- Dimension coverage：covered `—`；uncovered `DIM-07`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:装饰器总览:01@heading-54c036c9f066` | \[\@Once\](arkts-new-once.md)：\@Once装饰的变量仅初始化时同步一次，需要与\@Param一起使用。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 14/1 | 0.32736857 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:02` | \[\@StorageLink\](./arkts-appstorage.md#storagelink)/\[\@StorageProp\](./arkts-appstorage.md#storageprop)/\[\@LocalStorageLink\](./arkts-localstorage.md#localstoragelink)/\[\@LocalStorageProp\](./arkts-localstorage.md#localstorageprop)/\[\@Consume\](./arkts-provide-and-consume.md)变量的初始化规则为不可以被外部初始化，当组件开发者希望当前变量被外部初始化而使用public修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 2/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:03` | \[\@Link\](./arkts-link.md)/\[\@ObjectLink\](./arkts-observed-and-objectlink.md)变量的初始化规则为必须被外部初始化，禁止本地初始化。当组件开发者使用private对变量进行修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 3/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 5 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理版本介绍:01` | 对于新开发的应用，建议直接使用V2版本范式来进行开发。 | tag:has_state_management (file_hint) | 13/None | — | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |

### head · `Index.aboutToAppear`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.aboutToAppear:L74-L121:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `DIM-07`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:装饰器总览:01@heading-54c036c9f066` | \[\@Once\](arkts-new-once.md)：\@Once装饰的变量仅初始化时同步一次，需要与\@Param一起使用。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 14/1 | 0.31948265 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理版本介绍:01` | 对于新开发的应用，建议直接使用V2版本范式来进行开发。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 13/2 | 0.31810037 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:02` | \[\@StorageLink\](./arkts-appstorage.md#storagelink)/\[\@StorageProp\](./arkts-appstorage.md#storageprop)/\[\@LocalStorageLink\](./arkts-localstorage.md#localstoragelink)/\[\@LocalStorageProp\](./arkts-localstorage.md#localstorageprop)/\[\@Consume\](./arkts-provide-and-consume.md)变量的初始化规则为不可以被外部初始化，当组件开发者希望当前变量被外部初始化而使用public修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 2/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 5 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:03` | \[\@Link\](./arkts-link.md)/\[\@ObjectLink\](./arkts-observed-and-objectlink.md)变量的初始化规则为必须被外部初始化，禁止本地初始化。当组件开发者使用private对变量进行修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 3/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |

### head · `Index.aboutToDisappear`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.aboutToDisappear:L309-L344:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `—`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md:自定义组件的删除:01` | 在删除组件之前，将调用其aboutToDisappear生命周期函数，标记着该节点将要被销毁。ArkUI的节点删除机制是：后端节点直接从组件树上摘下，后端节点被销毁，对前端节点解引用，前端节点已经没有引用时，将被Ark虚拟机垃圾回收。 | keyword:aboutToDisappear (unit_exact), tag:has_lifecycle (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 1/1 | 0.3922274 | `zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 2/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 3 | `arkui-specs:05-ui-components/03-scroll-container-components/09-tabs-tab-content/Feat-06-tabs-cache-scroll-spec.md/R-17` | 触发条件：`js_tabs.cpp`；预期行为：TabContent.onWillHide 回调在内容区即将隐藏时触发 | vector:jinaai/jina-embeddings-v2-base-code (semantic) | None/2 | 0.30195662 | `05-ui-components/03-scroll-container-components/09-tabs-tab-content/Feat-06-tabs-cache-scroll-spec.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:02` | \[\@StorageLink\](./arkts-appstorage.md#storagelink)/\[\@StorageProp\](./arkts-appstorage.md#storageprop)/\[\@LocalStorageLink\](./arkts-localstorage.md#localstoragelink)/\[\@LocalStorageProp\](./arkts-localstorage.md#localstorageprop)/\[\@Consume\](./arkts-provide-and-consume.md)变量的初始化规则为不可以被外部初始化，当组件开发者希望当前变量被外部初始化而使用public修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 3/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |

### base · `Index.addNetworkListener`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.addNetworkListener:L146-L167:Rbase:S040c6b57210863e01ca2d71363032cb3a5927b59b9da00b3fca43c8124c4125a`
- Dimension coverage：covered `—`；uncovered `—`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理V1现状以及V2优点:02` | 只能感知对象属性第一层的变化，无法做到深度观测和深度监听。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 9/4 | 0.31425235 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:装饰器总览:03` | \[\@ObjectLink\](arkts-observed-and-objectlink.md)：\@ObjectLink装饰的变量接收\@Observed装饰的class的实例，应用于观察多层嵌套场景，和父组件的数据源构建双向同步。**说明：**仅\[\@Observed/\@ObjectLink\](arkts-observed-and-objectlink.md)可以观察嵌套场景，其他的状态变量仅能观察第一层，详情见各个装饰器章节的“观察变化和行为表现”小节。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 17/3 | 0.32437092 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/arkts-utils/sendable-constraints.md:Sendable类的内部不允许使用当前模块内上下文环境中定义的变量:01` | Sendable类的内部不允许使用当前模块内上下文环境中定义的变量 | vector:jinaai/jina-embeddings-v2-base-code (semantic) | None/1 | 0.35027327 | `zh-cn/application-dev/arkts-utils/sendable-constraints.md` |

### head · `Index.addNetworkListener`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.addNetworkListener:L152-L172:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `—`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理V1现状以及V2优点:02` | 只能感知对象属性第一层的变化，无法做到深度观测和深度监听。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 9/4 | 0.30598583 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:装饰器总览:03` | \[\@ObjectLink\](arkts-observed-and-objectlink.md)：\@ObjectLink装饰的变量接收\@Observed装饰的class的实例，应用于观察多层嵌套场景，和父组件的数据源构建双向同步。**说明：**仅\[\@Observed/\@ObjectLink\](arkts-observed-and-objectlink.md)可以观察嵌套场景，其他的状态变量仅能观察第一层，详情见各个装饰器章节的“观察变化和行为表现”小节。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 17/3 | 0.31075234 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/arkts-utils/sendable-constraints.md:Sendable类的内部不允许使用当前模块内上下文环境中定义的变量:01` | Sendable类的内部不允许使用当前模块内上下文环境中定义的变量 | vector:jinaai/jina-embeddings-v2-base-code (semantic) | None/1 | 0.36321307 | `zh-cn/application-dev/arkts-utils/sendable-constraints.md` |

### head · `Index.clearNetworkRetryTimer`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.clearNetworkRetryTimer:L174-L179:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `DIM-06`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:02` | \[\@StorageLink\](./arkts-appstorage.md#storagelink)/\[\@StorageProp\](./arkts-appstorage.md#storageprop)/\[\@LocalStorageLink\](./arkts-localstorage.md#localstoragelink)/\[\@LocalStorageProp\](./arkts-localstorage.md#localstorageprop)/\[\@Consume\](./arkts-provide-and-consume.md)变量的初始化规则为不可以被外部初始化，当组件开发者希望当前变量被外部初始化而使用public修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 2/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:03` | \[\@Link\](./arkts-link.md)/\[\@ObjectLink\](./arkts-observed-and-objectlink.md)变量的初始化规则为必须被外部初始化，禁止本地初始化。当组件开发者使用private对变量进行修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 3/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md:自定义组件的创建和渲染流程:01` | 初始化自定义组件的成员变量：通过本地默认值或者构造方法传递参数来初始化自定义组件的成员变量，初始化顺序为成员变量的定义顺序。 | tag:has_lifecycle (file_hint) | 6/None | — | `zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md` |

### head · `Index.clearRuntimeTimers`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.clearRuntimeTimers:L294-L300:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `DIM-06`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md:自定义组件的删除:01` | 在删除组件之前，将调用其aboutToDisappear生命周期函数，标记着该节点将要被销毁。ArkUI的节点删除机制是：后端节点直接从组件树上摘下，后端节点被销毁，对前端节点解引用，前端节点已经没有引用时，将被Ark虚拟机垃圾回收。 | tag:has_lifecycle (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 7/1 | 0.36512825 | `zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:02` | \[\@StorageLink\](./arkts-appstorage.md#storagelink)/\[\@StorageProp\](./arkts-appstorage.md#storageprop)/\[\@LocalStorageLink\](./arkts-localstorage.md#localstoragelink)/\[\@LocalStorageProp\](./arkts-localstorage.md#localstorageprop)/\[\@Consume\](./arkts-provide-and-consume.md)变量的初始化规则为不可以被外部初始化，当组件开发者希望当前变量被外部初始化而使用public修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 2/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理版本介绍:01` | 对于新开发的应用，建议直接使用V2版本范式来进行开发。 | tag:has_state_management (file_hint) | 13/None | — | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |

### head · `Index.removePlayerListeners`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.removePlayerListeners:L302-L307:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `—`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 2 | `arkui-specs:05-ui-components/03-scroll-container-components/09-tabs-tab-content/Feat-05-tabs-events-spec.md/AC-5.11` | WHEN animationMode=NoAnimation THEN onAnimationStart/onAnimationEnd 不触发 | vector:jinaai/jina-embeddings-v2-base-code (semantic) | None/1 | 0.34412643 | `05-ui-components/03-scroll-container-components/09-tabs-tab-content/Feat-05-tabs-events-spec.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:02` | \[\@StorageLink\](./arkts-appstorage.md#storagelink)/\[\@StorageProp\](./arkts-appstorage.md#storageprop)/\[\@LocalStorageLink\](./arkts-localstorage.md#localstoragelink)/\[\@LocalStorageProp\](./arkts-localstorage.md#localstorageprop)/\[\@Consume\](./arkts-provide-and-consume.md)变量的初始化规则为不可以被外部初始化，当组件开发者希望当前变量被外部初始化而使用public修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 2/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `arkui-specs:05-ui-components/03-scroll-container-components/09-tabs-tab-content/Feat-05-tabs-events-spec.md/AC-5.5` | WHEN 标签取消选中 THEN onUnselected 回调被触发，参数为取消选中标签的 index | vector:jinaai/jina-embeddings-v2-base-code (semantic) | None/2 | 0.33696302 | `05-ui-components/03-scroll-container-components/09-tabs-tab-content/Feat-05-tabs-events-spec.md` |
| 5 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:03` | \[\@Link\](./arkts-link.md)/\[\@ObjectLink\](./arkts-observed-and-objectlink.md)变量的初始化规则为必须被外部初始化，禁止本地初始化。当组件开发者使用private对变量进行修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 3/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |

### head · `Index.scheduleNetworkRecovery`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.scheduleNetworkRecovery:L181-L202:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `DIM-06`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:02` | \[\@StorageLink\](./arkts-appstorage.md#storagelink)/\[\@StorageProp\](./arkts-appstorage.md#storageprop)/\[\@LocalStorageLink\](./arkts-localstorage.md#localstoragelink)/\[\@LocalStorageProp\](./arkts-localstorage.md#localstorageprop)/\[\@Consume\](./arkts-provide-and-consume.md)变量的初始化规则为不可以被外部初始化，当组件开发者希望当前变量被外部初始化而使用public修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 2/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:03` | \[\@Link\](./arkts-link.md)/\[\@ObjectLink\](./arkts-observed-and-objectlink.md)变量的初始化规则为必须被外部初始化，禁止本地初始化。当组件开发者使用private对变量进行修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 3/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md:自定义组件的创建和渲染流程:01` | 初始化自定义组件的成员变量：通过本地默认值或者构造方法传递参数来初始化自定义组件的成员变量，初始化顺序为成员变量的定义顺序。 | tag:has_lifecycle (file_hint) | 6/None | — | `zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md` |

### head · `Index.updatePlaybackProgress`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@method:Index.updatePlaybackProgress:L123-L136:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `—`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理版本介绍:01` | 对于新开发的应用，建议直接使用V2版本范式来进行开发。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 13/1 | 0.3002471 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:02` | \[\@StorageLink\](./arkts-appstorage.md#storagelink)/\[\@StorageProp\](./arkts-appstorage.md#storageprop)/\[\@LocalStorageLink\](./arkts-localstorage.md#localstoragelink)/\[\@LocalStorageProp\](./arkts-localstorage.md#localstorageprop)/\[\@Consume\](./arkts-provide-and-consume.md)变量的初始化规则为不可以被外部初始化，当组件开发者希望当前变量被外部初始化而使用public修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 2/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:03` | \[\@Link\](./arkts-link.md)/\[\@ObjectLink\](./arkts-observed-and-objectlink.md)变量的初始化规则为必须被外部初始化，禁止本地初始化。当组件开发者使用private对变量进行修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 3/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 5 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:装饰器总览:01@heading-54c036c9f066` | \[\@Once\](arkts-new-once.md)：\@Once装饰的变量仅初始化时同步一次，需要与\@Param一起使用。 | tag:has_state_management (file_hint) | 14/None | — | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |

### base · `Index.build.Column.Flex.Row.Flex.Flex.Slider`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@ui_block:Index.build.Column.Flex.Row.Flex.Flex.Slider:L913-L959:Rbase:S040c6b57210863e01ca2d71363032cb3a5927b59b9da00b3fca43c8124c4125a`
- Dimension coverage：covered `—`；uncovered `DIM-06, DIM-08`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md:自定义组件的创建和渲染流程:01` | 初始化自定义组件的成员变量：通过本地默认值或者构造方法传递参数来初始化自定义组件的成员变量，初始化顺序为成员变量的定义顺序。 | tag:has_lifecycle (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 6/2 | 0.32980646 | `zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理V1现状以及V2优点:01` | 状态变量不能独立于UI存在，同一个数据被多个视图代理时，其中一个视图的更改不会通知其他视图更新。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 8/3 | 0.32947511 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-two-way-sync.md:使用规则:01` | 当前`$$`支持基础类型变量，当该变量使用\[\@State\](arkts-state.md)、\[\@Link\](arkts-link.md)、\[\@Prop\](arkts-prop.md)、\[\@Provide\](arkts-provide-and-consume.md)等状态管理V1装饰器装饰，或者\[\@Local\](arkts-new-local.md)等状态管理V2装饰器装饰时，变量值的变化会触发UI刷新。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 18/6 | 0.31197905 | `zh-cn/application-dev/ui/state-management/arkts-two-way-sync.md` |
| 4 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 5 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理版本介绍:01` | 对于新开发的应用，建议直接使用V2版本范式来进行开发。 | tag:has_state_management (file_hint) | 13/None | — | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |

### head · `Index.build.Column.Flex.Row.Flex.Flex.Slider`

- Unit ID：`code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets@ui_block:Index.build.Column.Flex.Row.Flex.Flex.Slider:L971-L1026:Rhead:S5c344785976e70663124818a7c500ca38f2a9d1531b8ddec1bab024763bafa68`
- Dimension coverage：covered `—`；uncovered `DIM-06, DIM-08`
- Diagnostics：`budget_exhausted`

| rank | rule_id | 规则正文 | matched_by | exact/vector rank | similarity | source |
|---:|---|---|---|---|---:|---|
| 1 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md:状态管理V1现状以及V2优点:01` | 状态变量不能独立于UI存在，同一个数据被多个视图代理时，其中一个视图的更改不会通知其他视图更新。 | tag:has_state_management (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 8/2 | 0.35012795 | `zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md` |
| 2 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md:自定义组件的创建和渲染流程:01` | 初始化自定义组件的成员变量：通过本地默认值或者构造方法传递参数来初始化自定义组件的成员变量，初始化顺序为成员变量的定义顺序。 | tag:has_lifecycle (file_hint), vector:jinaai/jina-embeddings-v2-base-code (semantic) | 6/5 | 0.32459911 | `zh-cn/application-dev/ui/state-management/arkts-page-custom-components-lifecycle.md` |
| 3 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:01` | \[\@State\](./arkts-state.md)/\[\@Prop\](./arkts-prop.md)/\[\@Provide\](./arkts-provide-and-consume.md)/\[\@BuilderParam\](./arkts-builderparam.md)/常规成员变量(不涉及更新的普通变量)的初始化规则为可以被外部初始化，也可以使用本地值进行初始化。当组件开发者不希望当前变量被外部初始化时，可以使用private进行修饰，在这种情况下，错误进行外部初始化会有编译告警日志提示。 | tag:has_state_management (file_hint) | 1/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |
| 4 | `arkui-specs:05-ui-components/03-scroll-container-components/09-tabs-tab-content/Feat-05-tabs-events-spec.md/AC-5.13` | WHEN 滑动距离为 0 THEN onGestureSwipe 不触发 | vector:jinaai/jina-embeddings-v2-base-code (semantic) | None/1 | 0.36117939 | `05-ui-components/03-scroll-container-components/09-tabs-tab-content/Feat-05-tabs-events-spec.md` |
| 5 | `openharmony-docs:zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md:使用限制:03` | \[\@Link\](./arkts-link.md)/\[\@ObjectLink\](./arkts-observed-and-objectlink.md)变量的初始化规则为必须被外部初始化，禁止本地初始化。当组件开发者使用private对变量进行修饰时，与装饰器本身的初始化规则不符，会有编译告警日志提示。 | tag:has_state_management (file_hint) | 3/None | — | `zh-cn/application-dev/ui/state-management/arkts-custom-components-access-restrictions.md` |

## 13. 自动校验结果

| ID | 断言 | 结果 |
|---|---|---|
| A00 | 固定来源、base/head 内容哈希和原始/正式 Diff 统计均无漂移 | PASS |
| A01 | base/head Parser 都实际使用 L1，且无 ERROR、missing 或 warning | PASS |
| A02 | ChangeSet 身份贯穿 AnalysisResult、ReviewUnitBuild 和 ContextPlan | PASS |
| A03 | 所有 ChangeAtom 在 base/head 适用侧均被分配给 ReviewUnit | PASS |
| A04 | 每个 ReviewUnit.full_text 严格等于 context_span 对应源码切片 | PASS |
| A05 | 每个 assigned changed line 都位于 Unit context_span 内 | PASS |
| A06 | ReviewUnit 身份唯一且 UnitFactScope/FeatureProfile 按 unit_id 完整覆盖 | PASS |
| A06-GOLDEN | ReviewUnit owner、span、changed lines、reason 和输出顺序匹配人工 expected | PASS |
| A07 | ContextPlan 保留全部 Feature Routing 问题绑定且没有阻塞变更 | PASS |
| A08 | PostgreSQL alias 解析到人工冻结的不可变 staging index | PASS |
| A09 | KnowledgeIndex 向量完整，且运行时 GPU provider 与索引版本完全匹配 | PASS |
| A10 | RetrievalRequest 与 EvidencePack 按 unit_id 和 request_id 保持身份对齐 | PASS |
| A11 | 真实 EvidencePack 至少包含一个向量召回结果 | PASS |
| A12 | evaluation fixture 与 EvidencePack 均明确不可用于生产 | PASS |
| A13 | KnowledgeIndex、RetrievalRequest、EvidencePack 正式 loader 往返无漂移 | PASS |

## 14. 如何解读本次准确性

- **结构准确性已自动验证**：来源哈希、Parser 质量、ChangeAtom 归属、ReviewUnit 人工 expected、源码切片、跨阶段 ID、GPU/索引身份和正式 loader 共 15 项断言通过。
- **本样例的检索覆盖不理想**：dimension coverage 为 `0/9`；只有 `1/67` 条候选关系含 Unit 精确匹配。
- **宽泛提示占主导**：`60/67` 条关系含 `file_hint`，会把整文件的 state/lifecycle 等提示扩散到无关 Unit。
- **人工抽查发现明显错域候选**：`removePlayerListeners` 召回 Tabs 的 `animationMode/onUnselected`，Slider timer 召回 Tabs `onGestureSwipe`，network 代码召回 Sendable 约束。这些仅用于揭示问题，不作为正式 Precision 标注。
- **语义准确率不能由本次运行单独给出**：这个样例没有人工标注“每个 Unit 应该命中哪些 Clause”，所以不能伪造 Precision/Recall。需要为该样例增加人工或独立模型审阅的 relevant / irrelevant 标签后再计算。
- `budget_exhausted` 表示候选知识被每 Unit 的 token 配额截断，不表示程序故障；本次 EvidencePack 的 `degraded=false`。

## 15. 本示例没有证明什么

- 没有执行 Rules，因此没有规则引擎 Finding。
- 没有组装 Prompt，也没有调用 LLM，因此没有优点/问题/影响评审结论。
- 没有运行 ArkTS 编译、类型检查或应用运行测试；Parser L1 干净不等于代码一定可编译。
- 当前索引是 evaluation fixture，知识 Clause 为 Draft，不能当生产规范。
- AVSession 专项知识覆盖仍有限；没有召回不代表代码没有问题。
- EvidencePack 的 Clause 需要由后续评审模块结合具体代码判断是否适用，不能把“检索到了”直接写成“代码违规”。

## 16. 交付前独立复核记录

以下命令是在本目录提交前独立执行的，不属于 `run_e2e.py` 内部步骤。代码或知识索引变化后，必须重新执行并更新本节。

| 检查 | 结果 |
|---|---|
| 全量 pytest | 712 passed，3 skipped |
| 本样例静态合同测试 | 5/5 passed |
| FileAnalysis / ChangeSet Golden | 15/15 · 14/14 |
| ReviewUnit v2 / ContextPlan Golden | 16/16 · 16/16 |
| Feature Routing Golden | 16/16，全指标 1.0 |
| Retrieval Golden | 36/36，Recall@5 / Precision@5 / MRR 均 1.0 |
| Parser v1 release gate | L1 15/15 perfect；R63 L0/L1 均 63/63 |
| PostgreSQL live integration | 3/3 passed |
| CUDA real-embedding 12-case gate | Recall@5 0.857143；Precision@5 0.692308；MRR 0.875；forbidden 0 |
| 本样例确定性重跑 | 13 个 JSON artifact + REPORT.md 字节哈希不变 |
| ruff / git diff --check | passed |

注意：冻结 Golden 的 perfect 表示测试夹具上的算法合同成立；本次真实 109 条Draft 知识样例仍然是 NOT QUALIFIED，两者不能混为一谈。
