# ArkTS Code Reviewer 文档中心

本目录是项目架构、模块契约、学习材料和历史设计的统一入口。当前文档基线同时覆盖
主项目代码，以及相邻 `arkts-knowledge`、`arkts-corpora`、`arkts-tools`、
`arkui_ace_engine` 和 `arkts-review-data` 的多仓库工作区。

## 文档规则

1. `architecture/` 和 `modules/` 是当前架构的唯一真相源。
2. 每个模块只保留一份 canonical 文档，同时描述当前实现和目标设计。
3. `learning/` 只用于教学，不作为接口和架构决策依据。
4. `examples/` 用于端到端样例，不替代正式数据契约。
5. `archive/` 是历史快照，只读，不再更新，也不用于判断当前状态。
6. 代码行为与文档冲突时，以代码为当前事实，并立即修正文档的“当前实现”部分。
7. 目标设计发生变化时，更新对应模块文档、跨模块契约和本文索引，不新建第二份“最新架构”。

## 状态定义

| 状态 | 含义 |
|---|---|
| `complete` | 当前冻结交付边界、正式合同和模块 Golden 已完成 |
| `implemented` | 主路径已有代码并有测试覆盖 |
| `partial` | 已有可运行初版，但契约、边界或验证尚未稳定 |
| `designed` | 架构与契约已对齐，尚未编码 |
| `planned` | 只有方向，关键契约仍待确定 |

## 推荐阅读顺序

### 快速了解项目

1. [整体架构](architecture/overview.md)
2. [多仓库工作区与知识来源架构](architecture/workspace-and-sources.md)
3. [跨模块数据契约](architecture/data-contracts.md)
4. [配置与版本规范](architecture/configuration.md)

### 新开发会话基线

新建 Chat 或交接开发任务时，先让执行者阅读上面四份文档，再阅读本次要实现的模块
文档。不要以 `archive/`、聊天记录或外部仓库 README 代替 canonical 文档。

截至 2026-07-12 的可验证基线：

```text
主项目测试（npm ci 后）     全量 pytest 通过；精确计数以当前门禁输出为准
Parser Golden               15 个自包含人工标注样本；L0 strict baseline，L1 全字段 perfect
FileAnalysis Golden          15 个自包含人工标注样本；完整 occurrence truth perfect
ChangeSet Golden             14 个自包含人工标注样本；change-set-v1 全字段 perfect
ContextPlan Golden           16 个自包含人工标注样本；关系、分组和预算全字段 perfect
Feature Routing Golden       16 个自包含人工标注样本；正式 FeatureRouter 路由语义 perfect
arkui_ace_engine L0 批测    63/63 成功，0 missing，0 crash
arkui_ace_engine L1 批测    63/63 为 L1；7 文件有 ERROR、7 文件有 missing warning
L1 declarations             5,414；63/63 文件均有 declaration
Grok candidate              默认 23 例仅作 provisional 诊断；旧 evidence 尚未通过政策审计
ReviewUnit                  RU-0～RU-5 已完成；ContextPlan 是模块最终交付边界
Feature Routing             24 Tags、12 Dimensions、12 Review Questions 已完成并配置化
来源登记                    19 项：11 knowledge + 4 corpus + 4 tool
知识构建/在线检索/正式评审   尚未实现运行闭环
```

快速复核命令：

```bash
PYTHONPATH=src python -m pytest -q -rs
(cd sidecars/arkts-parser && npm ci)
PYTHONPATH=src python tools/evaluate_file_analysis_golden.py --require-perfect
PYTHONPATH=src python tools/evaluate_change_set_golden.py --require-perfect
PYTHONPATH=src python tools/evaluate_review_unit_v2_golden.py --require-perfect
PYTHONPATH=src python tools/evaluate_context_plan_golden.py --require-perfect
PYTHONPATH=src python tools/evaluate_feature_routing_golden.py --require-perfect
PYTHONPATH=src python tools/check_feature_routing_package.py
PYTHONPATH=src python tools/check_parser_v1.py \
  --source-root /home/autken/Code/arkui_ace_engine \
  --include-candidate-diagnostics
```

`check_parser_v1.py` 同时执行 L0 strict baseline、L1 perfect Golden、4 个外部 snapshot
provenance 和 R63 L0/L1 fail-closed 批测。candidate 分数仍标记为 provisional，不属于
Parser v1 准确率承诺。

### 当前开发交接：ReviewUnit 与 Feature Routing

Parser v1 已由提交 `2c1df96` 冻结。RU-3 在不改变其默认输出和 Golden 的前提下，增加了
显式选择的 `file-analysis-v1` sidecar schema：`CodeSourceRef`、`FileAnalysis`、带 UTF-16
精确 offset 的 declaration/fact occurrence、`field_region/import_region` 和质量 provenance。
文件级兼容集合仍只能作为 `file_hints`；只有可回到 owner/span 的 occurrence 才能投影为
`unit_exact`。

ReviewUnit 模块的完成路线固定为：

```text
RU-0  ReviewUnit Golden harness                         已完成
RU-1  collision-safe unit_id 和可解释选择字段            已完成（9/9）
RU-2  多 owner + Parser quality diagnostics              已完成（14/16 phase target）
RU-3  FactOccurrence + Unit exact facts + parse-once      已完成
RU-4  精确 ChangeSet + base/head ReviewUnit                已完成
RU-5  related context + ChangeGroup + token budget
      -> ContextPlanResult                              已完成；模块边界
```

现有 `tests/golden/review_unit/` 16-case v1 Golden 必须保留，继续作为 RU-1/RU-2 的兼容
回归集；baseline 只能记录当前行为，不能覆盖人工 expected。RU-2 的阶段目标是其中 14/16
匹配；其中 deletion-only 仍按冻结 expected 显示为 unsupported，不因 RU-4 新合同反向改写。
RU-4 已建立相互独立的 14-case ChangeSet Golden 和 16-case ReviewUnit v2 Golden；RU-5 的
16-case ContextPlan Golden 独立冻结 Primary coverage、typed relation、按问题分 bundle、真实预算、
遗漏原因和输入排列稳定性。

RU-3 的准确率合同位于 `tests/golden/file_analysis/`，与 Parser v1/ReviewUnit Golden 独立。
15 个 case 完整比较全部 7 种 declaration、13 种 fact、两种 ReviewRegion、owner、1-based
inclusive 行号、0-based end-exclusive UTF-16 offset、quality/provenance、diagnostics 和稳定
顺序；loader 对重复 key/case、未知或缺失字段、hash/offset/owner 漂移 fail-closed。
`CodeAnalyzer` 现在对每个唯一 `CodeSourceRef` 只解析完整文件一次，再按 owner/span 投影
Unit facts，不再拼接 Unit 源码做二次 Parser。

RU-4 的 `change-set-v1` / `change-normalizer-v1` 接受调用方提供的结构化 base/head source 与
diff，不解析原始 Git 文本。`CodeAnalyzer.analyze_change_set(...)` 对每个 base/head
`CodeSourceRef` 各解析一次，并输出 `review-unit-build-v3`：Unit 通过 `source_role`、
`change_atom_ids`、`changed_old_lines/changed_new_lines` 和 region/declaration owner 保留完整
变更来源；source-scoped ID 追加 `:R{role}:S{digest}`，不会把同路径 base/head Unit 合并。

ReviewUnit 完成时只交付 `ContextPlanResult`，即“本次改动的全部 Primary、必要 Supporting、
关系、分组、预算结果和降级诊断”。Knowledge、Retrieval、Rules、Prompt、模型调用、Finding
和 GitCode 回写均是下游模块。`CodeAnalyzer.plan_context(...)` 只接受完整的 RU-4
`AnalysisResult`，因此调用方不能把直接改动 Unit 降为可选 Supporting。候选关系和额外
`FileAnalysis` 必须显式注入并绑定固定 source revision；Supporting span 必须精确等于
declaration/region occurrence，不能传任意字符串中段。`arkts-code-token-v1` 对每个 bundle
实际执行源码预算：按 review question 拆分，全部 Primary 在每个 bundle 中保留，required
上下文重复携带，helpful 上下文确定性分箱；共享 ChangeAtom 的 base/head Primary 通过自动
`change_correspondence` 同组，模型不会分开看改前和改后。RU-5 没有接入 Git/GitCode、知识检索或 Prompt。

ReviewUnit 完成后，Feature Routing 已把 `unit_exact/file_hints` 转换成正式
`feature-routing-v1`：每个 Unit 都有可重放的 `UnitFeatureProfile`，顶层
`FeatureRoutingResult` 保存配置 fingerprint、Unit/MR Dimensions 和稳定
`ReviewQuestionBinding`。24 Tags 来自 `tags-v1`，12 Dimensions 与 12 Review Questions 来自
`dimensions-v1`；`CodeAnalyzer.plan_context(...)` 只能消费这份正式问题绑定，调用方不能自行
覆盖适用性。

`unit_exact` 只生成 exact Tags、正式检索维度和专项问题；`file_hints` 只生成 routing Tags 与
保守 routing/MR Dimensions。hint-only signal 不绑定专项 RQ，也不能成为 Finding evidence。
Active 配置进入正式输出，Draft 只进入 `shadow_*`，Deprecated 不参与运行时匹配。旧
`RetrievalQuery` 仍保留为兼容视图，后续 Retrieval 必须使用 profile 中分离的
`retrieval_dimensions/routing_dimensions`，不能绕过 `retrieval_policy`。

Feature Routing 的独立 16-case Golden 已使用正式引擎通过 strict baseline 和
`--require-perfect`。配置 loader 拒绝重复 YAML key、未知字段、悬空引用和 Active 对非 Active
Tag 的依赖；`AnalysisResult` 会从原始 UnitFactScopes 重放 Feature 结果。两份 YAML 同时通过
wheel `force-include` 打包，source checkout 与安装环境共享相同配置语义。

当前下一阶段是 relation discovery 与 Knowledge/Retrieval，而不是继续扩张 ReviewUnit 或
Feature Routing。Knowledge、Retrieval、Rules、Prompt、最终 Finding 和 GitCode 仍未形成运行闭环。
新会话除四份架构文档外，还应依次阅读模块 01、02、03、04、10 和 11。

### 按流水线阅读模块

| 顺序 | 模块 | 当前状态 | 文档 |
|---|---|---|---|
| 01 | 输入与编排 | `partial` | [01-input-orchestration.md](modules/01-input-orchestration.md) |
| 02 | Parser 与代码事实 | `partial` | [02-parser.md](modules/02-parser.md) |
| 03 | ReviewUnit 上下文规划 | `complete` | [03-review-unit.md](modules/03-review-unit.md) |
| 04 | Tags、评审维度与问题路由 | `complete` | [04-feature-routing.md](modules/04-feature-routing.md) |
| 05 | 知识库构建 | `partial` | [05-knowledge-base.md](modules/05-knowledge-base.md) |
| 06 | Retrieval 检索 | `designed` | [06-retrieval.md](modules/06-retrieval.md) |
| 07 | Deterministic Rules | `designed` | [07-rules.md](modules/07-rules.md) |
| 08 | Prompt 与 Final LLM | `designed` | [08-prompt-review.md](modules/08-prompt-review.md) |
| 09 | 输出与 GitCode 集成 | `planned` | [09-output-integration.md](modules/09-output-integration.md) |
| 10 | 评测与反馈闭环 | `designed` | [10-evaluation-feedback.md](modules/10-evaluation-feedback.md) |
| 11 | Parser Validation 旁路 | `partial` | [11-parser-validation.md](modules/11-parser-validation.md) |

## 学习材料

| 文档 | 适用读者 |
|---|---|
| [ArkTS 入门、Parser 字段与 Tags 详解](learning/arkts-parser-fields-tags.md) | 没有 ArkTS/ArkUI 基础，需要理解 Parser 输出的读者 |

## 示例

| 文档 | 内容 |
|---|---|
| [PhotoWall 端到端数据流](examples/photowall-end-to-end.md) | 一段 ArkTS 代码如何经过 Parser、ReviewUnit、Retrieval 和 Final LLM |

## 历史归档

`archive/2026-07-09/` 保存本次整理前的全部架构草案、会议问题、实现详解和模拟输出。
归档文件中的接口、模块状态和结论可能已经失效。

## 文档更新检查表

修改模块代码或契约时，至少检查：

- 对应 `modules/NN-*.md` 的当前实现和目标设计是否仍准确。
- `architecture/data-contracts.md` 是否需要同步。
- `architecture/configuration.md` 是否增加了新配置或环境变量。
- `architecture/workspace-and-sources.md` 和外部 `sources.yaml` 是否需要同步。
- `architecture/overview.md` 的模块状态是否需要更新。
- 是否需要新增或更新 Golden Set、示例和迁移说明。

外部来源变化时还必须记录新的不可变 commit，并确认 ingestion allowlist、权威度和
Prompt 使用边界没有被上游内容改变。
