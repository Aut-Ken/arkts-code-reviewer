# Tag 识别方法与完整路由表（tags-v1）

> 当前配置真值：[`config/tags.yaml`](../../config/tags.yaml)  
> 当前运行时版本：`tags-v1`  
> 适用基线：`de1d4b4`  
> 本文是便于开发、审核和会议对齐的配置快照；配置与本文冲突时，以代码和 YAML 为当前事实

## 1. Tag 到底是什么

Tag 是对代码中**已经观察到的结构化事实**做的场景分类。例如：

```text
Unit 内精确出现 setTimeout
→ has_timer
→ 该 Unit 需要关注资源释放问题
```

Tag 只回答“这段代码涉及什么场景”，不回答：

- 代码有没有问题；
- 某条知识是否真的适用；
- Finding 是否成立；
- 严重度是多少。

当前有 24 个 Active Tag。它们是一个有限、版本化的路由词表，不是 ArkTS 全部能力的完整
目录。

## 2. 代码 Tag 是怎么得到的

### 2.1 完整链路

```text
完整文件 Parser / FileAnalysis
        │
        ├── declarations、regions、fact_occurrences
        │
        ▼
ReviewUnit 选择 Primary Unit
        │
        ▼
UnitFactProjector 按 owner + span 投影
        │
        ├── unit_exact：能回到当前 Unit occurrence 的精确事实
        └── file_hints：只能证明同文件存在的弱提示
                │
                ▼
FeatureRouter 对两个 scope 分别运行同一套 Tag 规则
        │
        ├── exact_tags / shadow_exact_tags
        └── routing_tags / shadow_routing_tags
```

这里没有对 ReviewUnit 源码再执行一次 Parser。Parser 只处理完整文件，随后按 occurrence 的
owner/span 投影。

### 2.2 `unit_exact` 与 `file_hints`

`unit_exact` 中的事实必须能证明属于当前 Unit。典型字段包括：

- components、apis、decorators、attributes、symbols、syntax；
- calls、import_uses、field_reads、field_writes、string_literals；
- resource_references。

`file_hints` 只说明同一个文件里出现过某类事实，不保证属于当前 Unit。例如一个文件头部有
`@State`，不能据此断言文件内每个方法都直接使用状态管理。

当前 Feature Matcher 实际只读取以下字段：

```text
components
apis
decorators
attributes
symbols
syntax
resource_references
```

虽然模型已经包含下面这些字段，但 `tags-v1` Matcher 还没有使用它们：

```text
calls
import_bindings
import_uses
field_reads
field_writes
string_literals
```

这是理解当前漏标问题的关键：真实 `connection.createNetConnection` 位于 `calls`，
`@ohos.net.connection#default` 位于 `import_uses`，所以只给 `any_api_prefix` 增加
`connection.` 并不会生效。

## 3. 当前匹配算法

### 3.1 支持的触发器

| YAML 运算符 | 读取字段 | 当前匹配方式 |
|---|---|---|
| `any_component` | `components` | 大小写敏感，完整字符串相等 |
| `any_api` | `apis` | 大小写敏感，完整字符串相等 |
| `any_api_prefix` | `apis` | `value.startswith(pattern)` |
| `any_api_suffix` | `apis` | `value.endswith(pattern)` |
| `any_decorator` | `decorators` | 大小写敏感，完整字符串相等 |
| `any_attribute` | `attributes` | 大小写敏感，完整字符串相等 |
| `any_symbol` | `symbols` | 大小写敏感，完整字符串相等 |
| `any_syntax` | `syntax` | 大小写敏感，完整字符串相等 |
| `has_resource_reference` | `resource_references` | 集合非空即命中 |

当前不做：

- 模糊匹配或拼写纠正；
- 正则匹配；
- symbol leaf/suffix 分段匹配；
- import alias 解析；
- 接收者类型推理；
- 任意调用链推理；
- 对源码、注释或字符串直接做 Tag 关键词扫描。

### 3.2 一条 Tag 内部的逻辑

同一个 Tag 下的所有 trigger 是 **OR**：任何一个触发器命中，Tag 就成立。所有实际命中的
signal 都保留在 `TagMatch.signals`，然后去重并确定性排序。

例如 `has_image`：

```yaml
any_component: [Image, ImageAnimator, ImageSpan]
any_api_prefix: [image.]
```

出现 `Image` 或 `image.createPixelMap` 任意一个都能命中；两者同时出现时，两个 signal 都会
记录。

### 3.3 exact 与 hint 独立匹配

FeatureRouter 对两个事实集合各运行一次规则：

```text
unit_exact → scope=unit_exact → exact_tags
file_hints → scope=file_hint → routing_tags
```

同一 Tag 可能同时出现于 exact 和 hint 中；两个 `TagMatch` 会保留。Dimension 路由计算时，
已经 exact 命中的 Tag 不会再重复计入 hint 归因。

### 3.4 Tag 状态

| 状态 | 行为 |
|---|---|
| `Active` | 进入正式 `exact_tags` 或 `routing_tags` |
| `Draft` | 仅进入 `shadow_exact_tags` 或 `shadow_routing_tags` |
| `Deprecated` | 不输出 |

当前 24 个 Tag 全部是 Active。

## 4. Tag 输出怎样影响下游

| 下游行为 | exact Tag | file-hint Tag |
|---|---:|---:|
| 声明当前 Unit 的正式场景 | 是 | 否 |
| 绑定专项 Review Question | 是 | 否 |
| 启动正式 review Dimension | 是 | 否 |
| 启动正式 retrieval Dimension | 是 | 否 |
| 扩大保守 routing Dimension | 是 | 是 |
| 扩大知识候选池 | 是 | 当前是 |
| 单独成为 Finding 事实依据 | 否；Tag 仍不是问题证据 | 绝对不可以 |

上表中的“绑定/启动”都以该 Tag 在 `dimensions.yaml` 中确实被对应 RQ 或 Dimension 引用为
前提。`has_animation`、`has_builder`、`has_list_render` 当前没有专项 RQ 或 Dimension；
`has_lifecycle`、`has_logging`、`has_navigation`、`has_state_management` 当前只有专项 RQ。

`RQ-correctness` 对所有 Unit 始终绑定，所以下表只列专项 Review Question。

## 5. 24 个 Tag 完整路由表

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

## 6. Tag 到 Dimension 与 Review Question 的分类

当前 24 个 Tag 可分为三类：

### 6.1 同时有 Dimension 和专项 RQ

```text
has_async
has_file_io
has_image
has_interactive_component
has_layout
has_media
has_network
has_permission_request
has_resource_ref
has_responsive_api
has_storage
has_subscription
has_taskpool
has_text_display
has_timer
has_user_input
has_worker
```

### 6.2 只有专项 RQ，没有 Dimension

```text
has_lifecycle         → RQ-lifecycle
has_logging           → RQ-dfx
has_navigation        → RQ-navigation
has_state_management  → RQ-state
```

这不是“永远无法检索”。exact Tag 仍能匹配同 Tag Clause，专项 RQ 当前进入 ContextPlan，
也会作为 review focus 进入 Retrieval 的向量 query；正式 Prompt 尚未实现，未来可继续消费它。
缺少的是 Dimension 级覆盖和按维优先保留能力。

### 6.3 当前既无 Dimension，也无专项 RQ

```text
has_animation
has_builder
has_list_render
```

它们目前只保留场景分类价值。后续必须由 taxonomy 决策明确它们是有意
`taxonomy_only`，还是遗漏了评审方向；不能由代码自行猜测。

## 7. 知识 Clause 的 Tag 是怎么得到的

代码 Tag 与知识 Tag 使用同一组 Tag ID，但输入和证据来源不同，不应混为一谈。

当前 Clause 标注链：

```text
Clause heading + parent context + text + examples
        │
        ├── 提取调用形式、decorator、backtick identifier
        ├── 识别已知 component/attribute/API signal
        ├── 使用 tags-v1 结构化规则
        ├── 使用 knowledge-annotations-v1 关键词规则
        └── 使用 API alias 规则
                │
                ▼
KnowledgeAnnotation.tags + provenance
```

当前知识关键词只额外覆盖六类：

```text
has_lifecycle
has_state_management
has_subscription
has_taskpool
has_timer
has_worker
```

每个 annotation 必须保存 provenance，说明 Tag 来自结构化 identifier、配置关键词、API
catalog 还是人工审核。知识文本关键词命中不能反向证明代码 Unit 中存在相同事实。

## 8. 当前已经实证的 Tag 漏洞

### 8.1 `calls/import_uses` 未被 Matcher 使用

真实 E2E 中网络和 AVSession 信号存在，但因字段不在 Matcher 输入中而静默漏标。修复不能只改
YAML，需要先增加结构化 trigger 与信号归一层。

### 8.2 qualified symbol 与裸配置不兼容

真实事实：

```text
Index.aboutToDisappear
```

当前配置：

```text
aboutToDisappear
```

两者不会完整相等，所以当前 E2E 的该 Unit 没有 `has_lifecycle` 和 `RQ-lifecycle`。同类风险
也适用于 `ForEach/LazyForEach/Repeat/ThreadWorker`。

### 8.3 API 接收者文本不是模块身份

直接匹配 `connection.` 会把项目自定义的同名对象误认为系统网络模块。可靠规则应同时验证：

```text
import source
+ local binding/alias
+ call receiver
```

无法解析时应 abstain，不能凭命名习惯产生强 Tag。

### 8.4 现有 Golden 没覆盖完整生产形状

Feature Routing Golden 能证明：给定人工冻结的 facts，Matcher 和路由政策是确定且正确的。
它不能证明 Parser 实际产生的 canonical/qualified signal 一定能命中规则。需要增加跨层集成
Golden。

## 9. tags-v2 的稳健演进方案

### 9.1 先建真实 truth

每个 Tag 至少准备：

- 10～20 个真实正例；
- 10～20 个近似反例；
- alias/import 变体；
- qualified/unqualified 变体；
- unit exact/file hint 双作用域；
- Parser degraded/fallback；
- wrapper 和间接调用边界。

### 9.2 增加 FeatureSignalNormalizer

建议由版本化归一层输出带 provenance 的信号：

```text
symbols      → symbol_leaf
import_uses  → import_source + imported binding
calls        → receiver + member + optional-chain normalization
```

新增规则需要支持 `all_of`，例如：

```yaml
any_of:
  - any_api_prefix: [http., rcp., socket.]
  - all_of:
      any_import_source: ['@ohos.net.connection']
      any_call_receiver_from_import: ['@ohos.net.connection']
```

这只是目标表达示例，不是当前 YAML 合同。

### 9.3 建 API Symbol Catalog

目录至少包含：

```text
module source
export/canonical API
local aliases
Tag IDs
platform/API level
source revision
```

Tag 应匹配模块身份与 canonical API，而不是只匹配局部变量名字。

### 9.4 影子发布与升级

```text
先增加失败的真实 Golden
→ 实现 Normalizer/schema/config
→ 升级 tags-v2
→ 新规则以 Draft/shadow 运行
→ 真实 P/R 达标
→ 转 Active
→ 重冻结 fingerprint 与 expected
```

baseline 只能记录当前行为，不得反向覆盖人工 expected。

## 10. Tag 质量怎么量化

| 指标 | 含义 |
|---|---|
| per-tag Precision | 被打上某 Tag 的 Unit 中，真正属于该场景的比例 |
| per-tag Recall | 真正属于某场景的 Unit 中，被成功打 Tag 的比例 |
| macro P/R | 每个 Tag 等权平均，防止高频 Tag 掩盖低频漏标 |
| micro P/R | 按所有样例总体统计，反映整体吞吐质量 |
| unknown signal rate | 已识别 calls/imports 中没有任何 Tag 消费的比例 |
| hint-only rate | 只有文件提示、没有 Unit 精确信号的 Tag 比例 |
| abstain correctness | 无法证明模块/owner 时，选择不打强 Tag 是否正确 |

建议 Active 新规则的初始门槛：Precision ≥ 0.95、Recall ≥ 0.90；安全、资源、并发等关键域
还应对已知高风险前缀设置“已知静默漏标为 0”的额外门禁。阈值最终必须由真实样本规模和
业务容错共同冻结。

## 11. 复核命令

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_feature_config.py \
  tests/test_feature_routing_engine.py \
  tests/test_feature_routing_policy.py \
  tests/test_feature_routing_golden.py

PYTHONPATH=src .venv/bin/python \
  tools/evaluate_feature_routing_golden.py --require-perfect

PYTHONPATH=src .venv/bin/python tools/check_feature_routing_package.py
```

当前 16-case Golden perfect 证明配置合同、scope 隔离、状态策略、稳定排序和重放正确；它不
证明 24 个 Tag 已覆盖真实 ArkTS 全域，也不证明跨层真实 P/R 已达到交付标准。

## 12. 相关文档

- [Dimension 推导方法与完整路由表](feature-routing-dimensions-v1.md)
- [质量缺口与改进方案](../audits/2026-07-14-quality-gaps-and-remediation.md)
- [Feature Routing canonical 文档](../modules/04-feature-routing.md)
- [Knowledge Base canonical 文档](../modules/05-knowledge-base.md)
- [Retrieval canonical 文档](../modules/06-retrieval.md)
