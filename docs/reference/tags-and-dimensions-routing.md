## 24 个 Tag 完整路由表

| Tag | 场景 | 当前触发条件 | Dimension | 专项 RQ | 当前已知边界 |
|---|---|---|---|---|---|
| `has_animation` | ArkUI 动画 | API=`animateTo`；attribute=`transition` | 无 | 无 | 只覆盖两个显式 signal，其他动画接口未形成目录 |
| `has_async` | 异步代码 | syntax=`async_fn`、`await_expr`、`promise` | DIM-07 | RQ-concurrency | 表示异步语法，不等于 TaskPool/Worker |
| `has_builder` | ArkUI Builder | decorator=`@Builder`、`@BuilderParam` | 无 | 无 | 当前是 taxonomy-only，未进入专项评审 |
| `has_file_io` | 文件读写 | API prefix=`fileIo.`、`fs.` | DIM-06 | RQ-resource | 模块 import/call 若没投影成 API 会漏标 |
| `has_image` | 图片能力 | component=`Image`、`ImageAnimator`、`ImageSpan`；API prefix=`image.` | DIM-06 | RQ-resource | 图片组件命中不代表存在内存问题 |
| `has_interactive_component` | 用户交互组件 | Button、Checkbox、Radio、Search、Slider、TextArea、TextInput、Toggle；onBlur/onChange/onClick/onFocus/onTouch | DIM-08 | RQ-accessibility | 交互只启动无障碍方向，不证明无障碍缺陷 |
| `has_layout` | 布局组件 | Column、Flex、Grid、GridCol、GridRow、RelativeContainer、Row、Stack | DIM-09 | RQ-adaptability | 常见布局会广泛触发，需由知识/语义进一步收窄 |
| `has_lifecycle` | 组件/页面生命周期 | symbol=`aboutToAppear`、`aboutToDisappear`、`onBackPress`、`onPageHide`、`onPageShow`、`onReady` | 无 | RQ-lifecycle | 真实 qualified symbol 当前会漏标，见第 8 节 |
| `has_list_render` | 列表与重复渲染 | component=`Grid`、`List`、`WaterFlow`；symbol=`ForEach`、`LazyForEach`、`Repeat` | 无 | 无 | qualified symbol 也存在同类风险；当前 taxonomy-only |
| `has_logging` | hilog 日志 | API prefix=`hilog.` | 无 | RQ-dfx | 自定义 `Log.info` 不会命中，避免仅凭方法名误判 |
| `has_media` | 音频、相机、媒体 | component=`Video`、`XComponent`；API prefix=`audio.`、`camera.`、`media.` | DIM-06 | RQ-resource | `@ohos.multimedia.avsession` 和实例调用当前可能漏标 |
| `has_navigation` | 页面导航 | component=`NavDestination`、`Navigation`；API prefix=`router.` | 无 | RQ-navigation | 有专项 RQ，无 Dimension |
| `has_network` | 网络访问 | API prefix=`http.`、`rcp.`、`socket.` | DIM-11 | RQ-network、RQ-security | `@ohos.net.connection` 的 calls/import uses 当前漏标 |
| `has_permission_request` | 权限申请 | API=`requestPermissionsFromUser`；prefix=`abilityAccessCtrl.` | DIM-11 | RQ-security | 只有被投影成 API 时才命中 |
| `has_resource_ref` | 资源引用 | API=`$r`、`$rawfile`；或 resource occurrence 非空 | DIM-10 | RQ-internationalization | 资源引用不等同于文本一定可国际化 |
| `has_responsive_api` | 响应式布局 | component=`GridCol`、`GridRow`；API prefix=`display.`、`mediaquery.` | DIM-09 | RQ-adaptability | 组件命中只是适配评审入口 |
| `has_state_management` | ArkUI 状态管理 | decorators=`@BuilderParam`、`@Consume`、`@Link`、`@Local`、`@ObjectLink`、`@Observed`、`@ObservedV2`、`@Once`、`@Param`、`@Prop`、`@Provide`、`@Require`、`@State`、`@StorageLink`、`@StorageProp`、`@Trace`、`@Watch` | 无 | RQ-state | Unit 不含 decorator 时，文件级 decorator 只能形成 hint |
| `has_storage` | 偏好与关系存储 | API prefix=`preferences.`、`relationalStore.` | DIM-11 | RQ-security | 目前未覆盖全部存储模块目录 |
| `has_subscription` | emitter/sensor 订阅 | API=`emitter.off`、`emitter.on`、`emitter.once`、`sensor.off`、`sensor.on`、`sensor.once` | DIM-06 | RQ-resource | 任意对象 `.on()` 不应命中；实例型订阅需要模块/类型证明 |
| `has_taskpool` | TaskPool | API prefix=`taskpool.` | DIM-07 | RQ-concurrency | 与通用 async 同维但不是同义词 |
| `has_text_display` | 文本显示/输入 | component=`Search`、`Text`、`TextArea`、`TextInput`；attribute=`placeholder` | DIM-10 | RQ-internationalization | 只路由检查，不表示存在硬编码文本 |
| `has_timer` | 定时器创建/清理 | API=`clearInterval`、`clearTimeout`、`setInterval`、`setTimeout`、`systemTimer.setInterval` | DIM-06 | RQ-resource | 当前未完整覆盖 systemTimer API 家族 |
| `has_user_input` | 用户文本输入 | component=`Search`、`TextArea`、`TextInput` | DIM-11 | RQ-security | 输入组件只启动安全检查，不证明输入不安全 |
| `has_worker` | Worker | API prefix=`worker.`；symbol=`ThreadWorker` | DIM-07 | RQ-concurrency | qualified symbol/import identity 可能需要 v2 归一 |

## 12 个 Dimension 完整路由表

| ID | 名称 | always_check | retrieval_policy | 触发 Tags | 当前实际行为 |
|---|---|---:|---|---|---|
| DIM-01 | 规范符合度 | 是 | `disabled` | 无 | 每个 Unit 都评审；永不因该维度检索 |
| DIM-02 | ArkTS 语言特性 | 是 | `signal_required` | 无 | 每个 Unit 都评审；当前没有触发 Tag，所以不产生正式/保守检索维度 |
| DIM-03 | 性能 | 是 | `signal_required` | 无 | 同上 |
| DIM-04 | 可维护性 | 是 | `signal_required` | 无 | 同上 |
| DIM-05 | 健壮性 | 是 | `signal_required` | 无 | 同上 |
| DIM-06 | 资源与内存管理 | 否 | `signal_required` | `has_file_io`、`has_image`、`has_media`、`has_subscription`、`has_timer` | exact 启动评审与检索；hint 只启动保守路由 |
| DIM-07 | 并发与异步 | 否 | `signal_required` | `has_async`、`has_taskpool`、`has_worker` | 同上 |
| DIM-08 | 无障碍 | 否 | `signal_required` | `has_interactive_component` | 同上 |
| DIM-09 | 多设备适配 | 否 | `signal_required` | `has_layout`、`has_responsive_api` | 同上 |
| DIM-10 | 国际化 | 否 | `signal_required` | `has_resource_ref`、`has_text_display` | 同上 |
| DIM-11 | 安全 | 否 | `signal_required` | `has_network`、`has_permission_request`、`has_storage`、`has_user_input` | 同上 |
| DIM-12 | DFX 与可测性 | 是 | `signal_required` | 无 | 每个 Unit 都评审；当前不产生维度检索 |