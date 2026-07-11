---
title: 03 ReviewUnit 上下文规划模块
status: canonical
implementation: partial
updated: 2026-07-12
---

# 03 ReviewUnit 上下文规划模块

## 1. 模块职责

ReviewUnit 决定“为了评审本次改动，后续模块需要看到哪段代码”。

```text
ChangeSet / 当前 FileHunk
+ Parser File Facts
-> 找到改动所属语义 owner
-> 生成稳定、唯一、可解释的 ReviewUnit
-> 在后续阶段扩展相关上下文和预算裁剪
```

它负责上下文选择和坐标，不负责判断代码好坏、检索知识或生成 Finding。

当前实现已经完成 RU-0 独立 Golden harness 和 RU-1 collision-safe identity，但仍只是单
owner declaration 选择器，还不是完整上下文规划器。Parser v1 已可作为上游地基；Parser
Golden 与 ReviewUnit Golden 继续保持独立。

## 2. 当前文件和调用链

| 文件 | 当前职责 |
|---|---|
| `models.py` | `FileInput/FileHunk/ReviewUnit/HostSummary` 数据类 |
| `review_units.py` | full/diff declaration 选择、fallback、HostSummary 和去重 |
| `analyzer.py` | 完整文件 Parser、调用 Unit Builder、Unit 二次 Parser 和 RetrievalUnit 组装 |
| `tagger.py` | 从二次 Parser 的 Unit facts 派生 Tags，再合并 MR Dimensions |
| `parser_validation/packager.py` | GLM 质检 snapshot；不是 ReviewUnit accuracy oracle |
| `review_unit_validation/golden.py` | 独立 Golden schema、loader、evaluator 和 baseline 校验 |
| `tools/evaluate_review_unit_golden.py` | strict baseline、phase target 和 full target 门禁 |

真实调用链：

```text
CLI / CodeAnalyzer.analyze_file(s)
-> FileInput(path, full source, optional FileHunk)
-> Parser.parse(full file)                         第 1 次
-> ReviewUnitBuilder.build_full/build_diff
-> 对每个去重 Unit：
   -> 拼接 import-like lines + unit.full_text
   -> Parser.parse(synthetic unit source)          再执行 U 次
   -> derive_tags
   -> CodeFeatures / RetrievalUnit
-> 合并 Unit tags
-> MR-level triggered_dimensions
-> AnalysisResult
```

因此每文件当前执行 `1 + U` 次 Parser；`U` 是去重后的 Unit 数。

## 3. 当前输入

```python
FileInput(
    path="src/pages/A.ets",
    content="完整新文件源码",
    hunks=[FileHunk(new_start=40, new_lines=8)],
)
```

`path` 是仓库根目录相对的逻辑路径，不是机器绝对路径。统一使用 `/`；安全的内部 `..` 会
先规范化，逃出仓库根目录的 traversal、POSIX/Windows 绝对路径和空路径会 fail-closed。
Analyzer 会在 Parser 执行前拒绝同一批输入中的规范化别名重复。CLI 可接收当前工作目录内
的绝对文件名，但传入 Analyzer 前会转换成 cwd-relative POSIX 路径；cwd 外文件会拒绝。

当前 `FileHunk` 只表达新文件中的连续范围：

- 不区分 added line 和 Git hunk context line。
- 没有 old content、deleted old lines 或 diff position。
- deletion-only、rename 和 binary 没有正式输入契约。
- `mode="diff"` 但某文件 `hunks=[]` 时，Analyzer 当前会把该文件当 full review。

精确 Git diff 属于 Input 模块。ReviewUnit 不应自行解析原始 diff，也不能在缺少 old source
时伪造 deletion-only 上下文。

## 4. Parser v1 依赖契约

ReviewUnit 当前可以依赖：

```text
Declaration.kind
Declaration.name / qualified_name / parent_name
Declaration.span.start_line / end_line
Declaration.text
CodeFacts.parser_layer / warnings
```

位置约定是 1-based、end-inclusive 文件绝对行。Parser Golden 当前只验证起止行，不验证
`start_col/end_col`。

以下硬约束必须保持：

1. `qualified_name` 和 `parent_name` 不是 occurrence 唯一 ID。
2. components、APIs、decorators、attributes、symbols、syntax 是文件级去重集合。
3. Unit components/symbols 只能从 Unit span 内 declarations 投影。
4. APIs/decorators/attributes/syntax/imports 没有 owner，只能称为 `file_hints`。
5. file hints 可以扩大候选路由，不能成为 Unit evidence。
6. `parser_layer=L1` 不代表没有 ERROR/missing node；warnings 必须可见。
7. Parser facts 只描述检测结果，不判断代码质量。
8. 缺少某个 fact 不能证明源码中一定不存在该事实。
9. Finding 必须回到 numbered source text 验证，并使用文件绝对行号。
10. ReviewUnit 第一阶段不得修改 Parser v1 行为或 Parser Golden/baseline。

## 5. 当前 full 算法

```text
找到所有 struct/class
-> 每个 struct/class 一个 ReviewUnit

没有 struct/class
-> 整个文件作为 fallback ReviewUnit
```

当前不会继续拆分超大 struct。普通 full Unit 的 changed lines 为空；整文件 fallback 却把
全文件行都标成 changed，二者语义不一致。

## 6. 当前 diff 算法

每个 hunk 只返回一个 Unit：

```text
找出与 hunk 范围重叠的 declarations
        |
        +-- 没找到
        |   -> 上下各扩 20 行
        |   -> context_degraded=true
        |
        +-- 命中 build_method
        |   -> build <= 160 行：完整 build
        |   -> build > 160 行：最小重叠 ui_block
        |
        +-- 其他
            -> 最小重叠 method/function/builder/struct/class
```

多个 hunk 现在按包含 path、kind、qualified name 和 span 的 `unit_id` 合并 changed lines。
旧 `unit_ref=qualified_name@path` 继续保留兼容，但不再参与去重。同名、不同 span 的 UI
occurrence 会产生不同 Unit，输出按 context/source span 和 `unit_id` 稳定排序。

Analyzer 返回结果前还会反查 Builder 输出：`unit.file` 必须对应当前 `FileInput`，span 不能
越界，`full_text` 必须等于 context span 源码切片，diff changed lines 必须来自输入 hunk，
整个 `AnalysisResult` 内也不允许重复 `unit_id`。空源码无法表达 1-based span，当前明确
fail-closed，而不是制造虚拟 `L1-L1` Unit。

## 7. 当前 HostSummary

```text
struct
decorators
states
lifecycle
imports
```

当前问题：

- decorators 和 lifecycle 来自文件级 facts，多 struct 文件会串扰。
- lifecycle 只有方法名，没有相关方法正文。
- states 使用正则从 host text 提取，属于 heuristic。
- imports 是文件级背景，不证明 Unit 实际使用。
- HostSummary 没有进入当前 RetrievalUnit。

## 8. 当前输出

```python
ReviewUnit(
    file="PhotoWall.ets",
    unit_id="PhotoWall.ets@method:PhotoWall.loadImages:L14-L20",
    unit_kind="method",
    unit_symbol="PhotoWall.loadImages",
    unit_ref="PhotoWall.loadImages@PhotoWall.ets",
    source_span=ReviewUnitSpan(start_line=14, end_line=20),
    context_span=ReviewUnitSpan(start_line=14, end_line=20),
    full_text="async loadImages() { ... }",
    changed_new_lines=[17, 18],
    changed_lines=[17, 18],
    file_changed_lines=[17, 18],
    unit_changed_lines=[4, 5],
    host_summary=HostSummary(...),
    selection_reason="innermost_changed_declaration",
    diagnostics=[],
    context_degraded=False,
)
```

`changed_lines` 与 `file_changed_lines` 仍作为兼容字段重复。新字段是当前过渡契约；
`full_text` 必须严格等于 `context_span` 的源码切片，越过 context 的粗 hunk 行通过结构化
`changed_lines_outside_context` 暴露，不再静默混入 `changed_new_lines`。

## 9. 已确认缺陷

| 严重度 | 问题 | 影响 |
|---|---|---|
| high | 一个 hunk 强制选择一个 declaration | 横跨两个方法/节点时静默丢 owner |
| high | Unit 二次 Parser 改变语义上下文 | method 可变成 function，行号和 owner 改变 |
| high | Unit parse layer/warning 未汇总 | metadata 可能错误报告 L1 且隐藏降级 |
| high | diff 文件无 hunk 时走 full | 未修改文件可能被审查 |
| medium | 多行 import 被逐行重建 | 合成 Unit source 可能语法残缺 |
| medium | Parser degraded 不进入 Unit diagnostics | `context_degraded=false` 可能误导下游 |
| medium | 固定 160/20 行阈值 | 不是 token/context budget |
| medium | token budget 只写入输出 | 不执行裁剪；传入 0 还会回退默认值 |
| medium | hunk 缺少精确 changed/deleted lines | context line 被当改动，删除场景不可表达 |

## 10. ReviewUnit v1 过渡输出

RU-1 已增加以下稳定字段，并暂时保留旧字段供现有消费者迁移：

```text
unit_id
unit_kind
source_span
context_span
changed_new_lines
selection_reason
diagnostics
```

推荐 identity：

```text
{normalized_path}@{unit_kind}:{qualified_name}:L{start_line}-L{end_line}
```

普通 ArkTS path/symbol 的可读格式保持不变；身份分隔符 `@`、`:`、`%` 及其他非安全字符
使用 UTF-8 percent-encoding，避免 path 与 symbol 通过分隔符注入形成同一 ID。fallback ID
同时包含 source span 与 context span。

约束：

- 去重以 `unit_id` 为 source of truth。
- 同一 occurrence 的多个 hunk 合并 changed lines。
- 同名但 span 不同的 occurrence 保持不同 Unit。
- `source_span` 是 owner declaration；`context_span` 是实际输出文本覆盖范围。
- `full_text` 必须等于 `context_span` 对应的完整源码切片。
- `changed_new_lines` 使用文件绝对行号并稳定排序去重。
- 旧 `unit_ref/changed_lines/file_changed_lines/unit_changed_lines` 先保留兼容，不继续扩散。
- 兼容承诺针对序列化输出字段；直接调用旧 `ReviewUnit(...)` 构造器必须补齐 RU-1 必填字段，
  不允许再生成 `unit_id=""` 的半合法对象。
- unsupported 能力写进 diagnostics，不能用空数组伪装已经支持。

第一批 `selection_reason` 至少区分：

```text
full_top_level_declaration
innermost_changed_declaration
large_build_ui_block
fallback_window
```

第一批 diagnostics 至少考虑：

```text
no_matching_declaration
parser_degraded
parser_error_nodes
parser_missing_nodes
diff_file_without_hunks
unsupported_deletion_only
budget_not_enforced
```

具体枚举应由 Golden contract 冻结，不允许实现和 expected 各写一套自由文本。

## 11. 分阶段开发顺序

### RU-0：Golden harness（已完成）

先建立独立 `tests/golden/review_unit/`，expected 与 current baseline 分离。第一提交只建设
测量工具、schema、fixture 和当前 baseline，不修改选择算法。

验收：

- 12～16 个自包含 case。
- duplicate JSON key、重复 case、未知字段和缺少必填字段 fail-closed。
- exact 比较 Unit ID/kind、source/context span、changed lines、selection reason、degraded 和
  diagnostics。
- baseline 只能记录实现行为，不能反向覆盖人工 expected。
- 同输入重复运行结果完全一致。
- 每次 evaluate 重验 manifest/source hash，拒绝加载后漂移、语义重复 case 和伪造 provenance。
- expected owner/span/changed lines 必须能回到冻结 declaration 与输入 hunk；比较类型敏感。
- baseline writer 只能刷新 `baselines/current.json`，不能覆盖 expected 或历史 baseline。

### RU-1：collision-safe identity（已完成）

增加过渡字段并把去重键切换为 `unit_id`，保留现有兼容字段。

验收：

- 同 qualified name、不同 span 的 UI occurrence 产生两个 Unit。
- 同一 occurrence 的多个 hunk 合为一个 Unit。
- hunk 输入顺序不影响输出顺序和 ID。
- `full_text == source[context_span]`。
- 所有 assigned changed lines 位于 context span，或有明确 diagnostic。

### RU-2：多 owner 和质量传播

- 一个 change region 可以产生多个 owner。
- diff 模式无 hunk 的文件不再静默进入 full review。
- Parser layer/warnings 转为 file/Unit diagnostics。
- HostSummary 只从对应 host declaration 提取，避免多 struct 串扰。

### RU-3：Unit fact scope 与 parse-once

删除二次 Parser 前必须先冻结二选一的事实契约：

1. Parser v2 提供 FactOccurrence span/owner；或
2. 引入 `unit_exact` 与 `file_hints` 双作用域。

双作用域方案中，Unit span 内 declarations/components/symbols 是 exact；API、decorator、
attribute、syntax 仍只是 file hints。file hints 不得显示为 Unit evidence。此阶段要增加
CountingParser 测试，保证每文件严格 Parser 一次，并同步 CodeFeatures/Tagger 契约。

### RU-4：精确 ChangeSet

由 Input 模块提供 added/deleted lines、old/new source、rename 和 diff position。ReviewUnit
只消费标准化 ChangeSet，不自行解析 Git diff。

### RU-5：related context 和 token budget

最后实现生命周期配对、状态/helper 受限扩展和真正 token budget。不能无界递归调用图，
也不能静默截断大括号结构。

## 12. ReviewUnit Golden Set

第一批 case 矩阵：

| 场景 | 必须验证 |
|---|---|
| 普通 method | owner、span、changed lines |
| short build | 完整 build |
| long build | 最小重叠 ui_block |
| 同名 UI occurrence | ID 不冲突、不错误合并 |
| 同 owner 多 hunk | 合并且稳定排序 |
| 跨两个 method hunk | 当前缺陷可见，后续应产生两个 owner |
| full mode 多 struct/class | 每个顶层 host 独立 |
| 无 declaration fallback | context span 与 degraded diagnostic |
| field/import region | 当前 fallback/unsupported 行为明确 |
| diff 文件无 hunk | 不允许静默 full review |
| Parser L0/degraded/warning | diagnostics 传播 |
| invalid/out-of-range hunk | fail-closed 或结构化 diagnostic |
| deletion-only | 第一阶段明确 unsupported |
| budget 超限 | 第一阶段明确 `budget_not_enforced` |

以合成 fixture 为主。需要真实边界时，第一批最多定点使用 R63-008、R63-009、R63-038、
R63-044、R63-050、R63-055；固定来源和 revision 见
[多仓库工作区与知识来源架构](../architecture/workspace-and-sources.md)。

外部源码应复制最小稳定片段并保存 provenance。R63、Parser output、Grok candidate 和当前
ReviewUnit baseline 都不是 expected 真值，expected 必须人工标注。

## 13. 目标算法

### 13.1 Change owner

精确 added/deleted line 最终映射到一个或多个最内层 owner：

```text
method
build_method
builder
ui_block
field_region
import_region
fallback_window
```

### 13.2 基础上下文

| owner | 基础上下文 |
|---|---|
| method | 完整方法 |
| build | 改动 UI 节点及必要父链 |
| builder | 完整 Builder + 宿主签名 |
| field region | 字段声明 + 相关改动方法 |
| import | import + 与该符号有关的改动声明 |
| deletion-only | base declaration + head anchor context |

### 13.3 关联上下文

只在预算内受限扩展：

```text
timer/subscription/media -> 对应生命周期方法正文
state management         -> 相关状态字段和装饰器
local helper             -> 最多一层直接依赖
```

### 13.4 Token budget

优先级：

```text
改动行
> owner 完整语义
> 宿主签名和状态
> 高相关关联代码
> 低相关上下文
```

超预算必须记录裁剪原因和 diagnostics。

## 14. 行号与质量约定

- `numbered_text` 和 Finding 使用文件绝对行号。
- Unit 相对行号只用于调试和兼容。
- 删除代码使用 base 文件行号。
- `context_degraded` 不能只表示 fallback，还要结合 Parser 质量和裁剪诊断。
- L0、parse_degraded 或带 AST warning 的 Unit 必须让下游看到质量信息。

## 15. 性能

declaration 选择本身约为 `O(H * D)`，当前不是主要瓶颈。主要成本来自每文件 `1 + U`
次 Parser，尤其每次 L1 都启动 Node 进程。

第一阶段不需要 interval tree、调用图或常驻 worker。正确顺序是先用 Golden 锁定行为，再
修 identity/multi-owner，最后在事实作用域契约下实现 parse-once。

## 16. 第一阶段非目标

以下内容不得混入第一个 ReviewUnit 提交：

- Knowledge、Retrieval、Rules、Prompt、GitCode。
- Parser v1 行为、Parser Golden expected 或 baseline 修改。
- deletion-only/base source。
- related-context 调用扩展。
- 真正 token budget。
- FactOccurrence/owner Parser v2。
- Tag/Dimension 配置化。
- 递归扫描外部仓库。

## 17. 第一阶段完成条件

```text
ReviewUnit Golden harness 存在且 fail-closed
12～16 cases 有人工 expected
collision-safe unit_id 通过 Golden
同名 occurrence 不再错误合并
兼容字段和当前调用方仍工作
所有对外行号为 1-based 文件绝对行
全量 pytest 通过
Parser v1 release gate 无漂移
```

完成 RU-1 后先停下来复核 Golden 差异，再决定 RU-2 和 parse-once 的具体范围。
