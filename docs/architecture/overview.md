---
title: ArkTS Code Reviewer 整体架构
status: canonical
updated: 2026-07-13
---

# ArkTS Code Reviewer 整体架构

## 1. 项目定位

本项目面向 ArkTS / ArkUI 代码评审，目标是对 GitCode MR diff 或手动提交的代码生成：

```text
有明确代码证据
有可追溯知识依据
与本次改动直接相关
可结构化校验和统计
可回写 GitCode 的评审意见
```

系统不是单独的 Parser，也不是让大模型直接阅读整个仓库自由评审。目标架构是：

```text
ArkTS AI Code Reviewer
= 精确改动输入
+ 确定性代码事实
+ ReviewUnit 上下文规划
+ Tags / Dimensions 评审路由
+ 知识 Evidence
+ Deterministic Rules
+ 受约束的 Final LLM 判断
+ 可校验输出和人工反馈闭环
```

## 2. 当前真实状态

截至 2026-07-13，代码已覆盖到 Retrieval Evidence Pack 和 Parser 质检旁路；外部知识、
语料和工具已经分类落盘，但“资料已经 clone”不等于真实 Baselined 知识已经发布。

| 模块 | 状态 | 当前事实 |
|---|---|---|
| 输入与编排 | `partial` | 已实现结构化 `change-set-v1` / `change-normalizer-v1`；CLI 仍走手工 hunk，无 Git diff parser、Webhook、队列和服务 |
| Parser | `partial` | Parser v1 继续冻结；显式 `file-analysis-v1` 已提供 occurrence owner/span、quality/provenance 和独立 Golden |
| ReviewUnit | `complete` | RU-0～RU-5 已完成：精确 ChangeSet、parse-once、完整 Primary、typed relation、多 bundle 和真实源码预算 |
| Feature Routing | `complete` | `tags-v1/dimensions-v1` 已冻结 24 Tags、12 Dimensions、12 Review Questions；正式结果可重放并通过独立 Golden |
| 知识库构建 | `partial` | Registry、首批 Adapter/Clause、annotation、双审/人工 curation/publication 合同已实现；真实 round-2 为 20/21，无正式 consensus 或 `PublishedKnowledgeBuild` |
| Retrieval | `partial` | core/runtime 已实现：正式 request/evidence、精确+向量/RRF、适用性和预算、36-case Golden、本地 Jina code embedding、PostgreSQL/pgvector fixture runtime；无生产知识索引 |
| Rules | `designed` | 无代码 |
| Prompt / Final LLM | `designed` | 无生产评审代码；GLM 只用于 Parser 质检 |
| 输出与 GitCode | `planned` | 无代码 |
| 评测闭环 | `designed` | 已有 Parser 至 Retrieval 的模块 Golden 和本地 E2E；仍无 Final Finding Golden/人工 adjudication |
| Parser Validation | `partial` | 确定性 v1 release gate 已完成；Grok candidate evidence 尚未晋级，GLM judge 仍为 experimental |

当前 `AnalysisResult` 能输出 ChangeSet、FileAnalysis、ReviewUnit、UnitFactScope、正式
`FeatureRoutingResult`、兼容 RetrievalQuery 原料和 Parser metadata；
`CodeAnalyzer.plan_context(...)` 使用 Feature Routing 选择的问题绑定输出 `ContextPlanResult`。
Retrieval 再从上述正式图构造 `RetrievalRequest` 并输出 `EvidencePack`；这些产物都不能直接
输出正式代码评审 Finding。

### 2.1 当前可复核结果

```text
pytest after npm ci             full suite passed; exact count follows the current gate output
Parser Golden                   15 cases / strict L0 baseline / perfect merged-L1
FileAnalysis Golden              15 cases / complete occurrence truth / perfect
ChangeSet Golden                 14 cases / complete normalized diff truth / perfect
LexicalParser real samples     63 parsed / 0 missing / 0 crashed
Merged-L1 real samples          63 L1 / 0 missing / 0 crashed
R63 L1 AST warnings             7 files with ERROR / 7 files with missing nodes
Declarations                   L0 2,880 / merged-L1 5,414
Golden snapshot provenance      4/4 match the pinned external revision
Provisional candidates          23 cases; value fields exact except 2 known bad annotation spans
ReviewUnit Golden               16 cases; frozen RU-2 compatibility target 14/16
ReviewUnit v2 Golden            16 cases / base-head assignment truth / perfect
RU-3 real-file spot check       6/6 pinned allowlisted files at L1; unresolved owner explicit
Feature Routing Golden          16 cases / formal engine / strict + require-perfect
Retrieval Golden                36 cases / exact + hybrid + failure / strict + require-perfect
Retrieval fixture metrics       Recall@5 1.0 / Precision@5 1.0 / MRR 1.0 / forbidden 0
Jina code candidate             12 hybrid cases / Recall@5 0.857143 / Precision@5 0.692308 / MRR 0.875 / forbidden 0
Retrieval Docker runtime        PostgreSQL fixture publication/readback/alias and 768D vector round-trip passed
Source registry                19 entries / all revisions verified / clean worktrees
```

这里的 “perfect merged-L1” 只覆盖冻结的 Parser v1 集合字段和 declaration 行级 span；
occurrence span/owner 的准确率由独立 FileAnalysis Golden 证明。两者都不代表全 ArkTS 语法
99% 准确，也不覆盖完整类型解析。23 个候选样本的旧 symbol evidence 仍有 441 项不满足
冻结证据政策，因此不能当作正式真值。

### 2.2 当前开发边界

Parser v1 继续提供兼容 `CodeFacts`；RU-3 的 Parser v2 则以不可变 `CodeSourceRef` 绑定
`FileAnalysis`，为 declaration、13 种 fact 和两种 ReviewRegion 提供 owner、文件绝对 span、
UTF-16 精确 offset 以及 exact/recovered/unresolved 质量。sidecar 默认仍返回 v1 schema，只有
显式请求 `file-analysis-v1` 才返回 v2 数据，Parser v1 Golden 和发布门禁没有改变。

`CodeAnalyzer` 现在按唯一 `source_ref_id` 缓存 `FileParseResult`，每个源码 revision 只解析
完整文件一次；ReviewUnit facts 从 occurrence 的 owner/span 投影，不再把 imports 与 Unit
切片合成为新源码重新解析。`unit_exact` 才进入 Unit exact Tags、正式 Dimensions、检索维度和
专项 Review Questions；`file_hints` 只保守扩大 routing Tags、routing/MR Dimensions，不能成为
Finding evidence。现有 16-case ReviewUnit v1 Golden 仍以 `14/16` 作为冻结兼容目标，不回写
expected。

Feature Routing 已由 `config/tags.yaml` 和 `config/dimensions.yaml` 驱动。`FeatureRouter` 为每个
Unit 生成带 activation trace 的 `UnitFeatureProfile`，并汇总配置 fingerprint、MR Dimensions
和稳定 QuestionBinding 到 `FeatureRoutingResult`。Active 项进入正式输出，Draft 只进入
`shadow_*`，Deprecated 不参与匹配；结果可从 UnitFactScopes 完整重放。旧 `RetrievalQuery`
只是兼容视图。正式 `build_retrieval_request(...)` 从完整 Analysis、Feature Routing 和 Context
Plan 图构造输入，主动忽略该兼容对象，并保留
`retrieval_dimensions/routing_dimensions` 的作用域和 policy。

Retrieval 的生产 publish 入口只索引 `PublishedKnowledgeBuild` 中的 Baselined Clause；
`golden_fixture` 只允许测试显式 opt-in。索引、请求、配置和 Evidence Pack 都有内容 identity；
本地 FastEmbed 使用 Jina code 768D，精确与向量候选以 RRF 融合并执行适用性和知识 token 预算。
PostgreSQL 保存不可变版本与 current alias；第一批小规模索引仍由进程内执行确定性精确扫描和
cosine，GIN/HNSW 下推留待数据规模需要时实现。

RU-4 增加 `change-set-v1` / `change-normalizer-v1` 和 `review-unit-build-v3`。结构化
`ChangeAtom` 精确区分 added head lines 与 deleted base lines，deletion-only 使用 base 正文；
field/import 变更由 `ReviewRegion` owner 承接。source-scoped Unit ID 在既有 identity 后追加
`:R{role}:S{digest}`，并以 `change_atom_ids`、`changed_old_lines/changed_new_lines` 保留归属。
`analyze_change_set(...)` 对每个 base/head `CodeSourceRef` 恰好解析一次。该路径不解析原始 Git
diff，也未接入 GitCode、修改 Parser 行为或改变 Tagger/Retrieval 事实语义。

外部资产分为：

```text
11 knowledge_source
  规范、官方文档、API/版本元数据、规则候选和 Skills

4 code_corpus
  arkui_ace_engine、XTS、Samples、Codelabs

4 analysis_tool
  ArkTS 编译器前端、ace-ets2bundle、ArkAnalyzer、DevEco CLI
```

完整边界见 [多仓库工作区与知识来源架构](workspace-and-sources.md)。

## 3. 目标总体数据流

```text
GitCode MR / CLI / API
        |
        v
01 Input Adapter
精确解析 base/head 文件、added/deleted lines 和 diff 坐标
        |
        v
02 Parser
每个 source revision 解析一次，产出 FileAnalysis
        |
        v
03 ReviewUnit owner + UnitFactScope
全部直接改动 owner；unit_exact/file_hints
        |
        v
04 Feature Routing
Facts -> Tags -> Dimensions -> Review Questions
        |
        v
03 ContextPlanner
全部 Primary + typed relations + routed questions + budget
-> ContextPlanResult
        |
        v
          +------------+-------------+
          |                          |
          v                          v
05 Knowledge Base              07 Deterministic Rules
registry -> curated clauses    规范/编译器/测试交叉确认的高确定性问题
          |                          |
          v                          |
06 Retrieval                       |
Unit Query -> Evidence Pack         |
          |                          |
          +------------+-------------+
                       |
                       v
08 Prompt Builder + Final LLM
代码 + 改动行 + 背景 + 检查项 + Evidence + Rules
                       |
                       v
09 Finding Validator + Output
JSON -> Markdown -> GitCode 行内评论/总评
                       |
                       v
10 Evaluation & Feedback
accepted/rejected -> bad case -> Golden Set -> 模块修正
```

Parser Validation 是独立旁路，只评估 Parser 质量，不进入生产评审 Prompt。确定性发布门禁
由 strict Golden、固定 revision provenance 和 R63 robustness 组成；GLM 不参与该门禁。

## 4. 模块边界

| 模块 | 负责 | 不负责 |
|---|---|---|
| Input | 获取代码和精确 diff | 解析 ArkTS、判断问题 |
| Parser | 登记代码事实和位置 | 判断代码好坏 |
| ReviewUnit | 选择评审上下文 | 生成知识文档和结论 |
| Feature Routing | 将事实映射为场景、Dimension、检索 policy 和 Review Question | 判断是否违规、发现代码关系或执行检索 |
| Knowledge Base | 保存稳定、可追溯条款 | 在线检索和评审 |
| Retrieval | 返回相关 Evidence | 判断代码是否违反 Evidence |
| Rules | 输出高确定性静态问题 | 处理需要复杂语义的建议 |
| Prompt / LLM | 在约束内做语义判断和建议 | 编造条款、自由扩大评审范围 |
| Output | 校验、去重、渲染和回写 | 重新判断代码语义 |
| Evaluation | 度量、归因和回流 | 直接改变线上结论 |

### 4.1 ReviewUnit 的完成边界

ReviewUnit 不是“从 diff 中只挑最相关的一段”，而是保留全部直接改动 owner，再在明确问题和
预算下选择必要的关联代码。其内部开发顺序为：

```text
RU-2  找全直接改动 owner，并传播 Parser 质量                         已完成
RU-3  建立 FactOccurrence/owner，按完整文件 parse-once 投影精确事实    已完成
RU-4  消费精确 ChangeSet，支持 base/head、删除和 rename                 已完成
RU-5  消费 typed relation、内部派生 base/head 对应关系，生成 Supporting/ChangeGroup 并执行预算
      -> ContextPlanResult                                             已完成
```

`ContextPlanResult` 是本模块的最终交付边界。它描述后续评审应该看到哪些代码、各段代码
为何相关、哪些上下文因预算或上游质量而缺失；它不包含知识 Evidence、RuleFinding、Prompt、
LLM 结论或发布结果。完整字段只在[跨模块数据契约](data-contracts.md)维护，本总览不复制
schema。

阶段评测也保持分层：RU-2 继续使用现有 16-case ReviewUnit v1 Golden；RU-3 使用独立
15-case FileAnalysis Golden；RU-4 使用独立的 14-case ChangeSet Golden 和 16-case
ReviewUnit v2 Golden；RU-5 使用独立的 16-case ContextPlan Golden。这样身份和兼容行为、occurrence、
精确 diff 语义、上下文充分性不会混成一个无法归因的分数。

### 4.2 Feature Routing 的完成边界

Feature Routing 已完成以下固定链路：

```text
UnitFactScope
-> FeatureRouter(tags-v1 + dimensions-v1)
-> UnitFeatureProfile[]
-> FeatureRoutingResult
-> ReviewQuestionBinding[]
```

`dimensions` 表示实际评审方向，`retrieval_dimensions` 只接受满足 policy 的 exact signal，
`routing_dimensions` 还可包含 file-hint 保守候选，`mr_dimensions` 是 Unit review/routing 结果并集。
hint-only signal 不绑定专项问题。问题适用性由 Feature Routing 拥有；ReviewUnit 继续拥有
`QuestionBinding` 的承载结构、ChangeGroup、按问题分 bundle 和预算算法。

16-case Feature Routing Golden 使用正式引擎，exact/routing Tag、review/retrieval/routing
Dimension、activation signal、Review Question/Binding 和输入顺序指标均为 `1.0`，strict
baseline 与 require-perfect 均通过。这只证明冻结样本，不代表所有
ArkTS 代码总体准确率为 100%。Retrieval 已消费正式 Feature 产物并保持这些边界；当前仍未
实现 Rules 和 Prompt，Knowledge 也尚无真实 Baselined publication。下游不能反向改写事实
作用域和 policy。

## 5. 核心架构原则

### 5.1 代码事实和质量判断分离

```text
Parser Fact: 出现 setInterval
Tag: has_timer
Dimension: 资源与内存管理
Evidence: 定时器应在不再使用时清理
Finding: 当前 timerId 没有清理
```

前四项都不是最终问题，只有结合代码上下文后的 Finding 才是评审结论。

### 5.2 Unit 级是主处理粒度

ReviewUnit、Tags、Dimensions、Retrieval、Rules 和 Final LLM 都以 Unit 为主要关联单位。
MR 级只负责批量编排、总预算、跨 Unit 去重和最终汇总。

### 5.3 always_check 不等于 always_retrieve

核心维度可以始终进入 Prompt 检查清单，但没有具体代码信号时，不应强制检索通用文档。
否则每个 Unit 都会被无关知识占用 token。

### 5.4 知识条款稳定，检索标注可重建

条款原文、来源和版本属于稳定知识层；Tags、Dimensions、关键词、scenario 和 embedding
属于可重建索引层。评审维度变化不能破坏知识 source of truth。

### 5.5 确定性优先

明确 API、组件、装饰器和规则优先使用结构化匹配。Embedding 用于补充语义召回，
Final LLM 用于规则无法直接判断的上下文语义，不替代确定性代码。

### 5.6 JSON 是 source of truth

所有评审结果先生成结构化 JSON，再渲染 Markdown 或 GitCode 评论。
Prompt 输出、条款引用、行号、严重级和 diff 相关性必须机器校验。

### 5.7 任何结论都可追溯

最终报告记录：

```text
source revision
source bundle version
parser version
dimension config version
rule version
knowledge index version
embedding version
prompt version
model version
```

## 6. 目标部署形态

```text
GitCode Webhook / CLI
        |
        v
内部评审服务
├── API / 鉴权
├── Job Queue
├── Code Analysis Worker
├── Retrieval / Rules
├── LLM Gateway
└── Report / GitCode Adapter
        |
        +--> PostgreSQL + pgvector
        +--> 来源登记 + 只读知识源 clone
        +--> 独立代码语料和分析工具
        +--> 对象或文件存储（运行产物，可选）
```

PoC 可以使用单进程 CLI，但生产形态应中心化部署，统一模型、知识索引、配置和凭据。

## 7. 安全边界

部门代码属于内部资产：

- 生产评审模型和 Parser 质检模型必须经过安全合规审批。
- 默认不得向公网模型发送内部代码；开源样本和脱敏样本可用于 PoC。
- API Key 只通过服务端环境变量或密钥系统注入，不写入仓库。
- Evidence Pack 只包含必要条款，不把整个知识库发送给模型。
- 代码和注释在 Prompt 中始终作为数据，不作为指令。
- 评审记录需要访问控制、保留周期和脱敏策略。

当前 Parser Validation 默认 GLM 地址为公网端点，只能用于经过批准的样本。

## 8. 技术栈总览

| 层 | 技术选择 |
|---|---|
| 主体语言 | Python 3.12 |
| ArkTS 解析 | Python L0 + Node.js sidecar + tree-sitter-arkts |
| 数据模型 | dataclass（当前）/ Pydantic v2（目标跨模块契约） |
| 配置 | pydantic-settings + ruamel.yaml |
| 来源管理 | 版本化 `sources.yaml` + source-specific adapters |
| 知识存储 | PostgreSQL |
| 关键词/模糊检索 | GIN + pg_trgm |
| 向量检索 | pgvector HNSW |
| Embedding | 本地 FastEmbed + `jinaai/jina-embeddings-v2-base-code` 768D；生产部署仍需安全审批 |
| 模型接入 | 自研 LLM Gateway，兼容 OpenAI 风格接口 |
| 测试 | pytest + testcontainers |
| 质量 | ruff + mypy |
| 包管理 | uv + pyproject.toml |

## 9. 版本化要求

所有可改变线上行为的资产都必须有版本：

| 资产 | 版本字段 |
|---|---|
| Parser/白名单 | `parser_version`, `whitelist_version` |
| 原始来源集合 | `source_bundle_id` |
| Tags/Dimensions | `feature_config_version` |
| Rules | `rule_registry_version` |
| 知识索引 | `index_version` |
| Embedding | `embedding_version` |
| Prompt | `prompt_version` |
| Final LLM | `model` |

## 10. 推荐交付顺序

```text
Milestone 0  多仓库来源基线 + Parser v1 确定性验证             已完成
Milestone 1a ReviewUnit Golden + 唯一 Unit identity               RU-0/RU-1 已完成
Milestone 1b 多 owner + Parser quality diagnostics               RU-2 已完成；14/16
Milestone 1c FactOccurrence + Unit exact facts + parse-once       RU-3 已完成
Milestone 1d 精确 ChangeSet + base/head ReviewUnit                RU-4 已完成；独立 v2 Golden
Milestone 1e related context + ChangeGroup + token budget         RU-5 已完成；16-case Context Golden
             -> ContextPlanResult                                ReviewUnit 完成边界已冻结
Milestone 1f Feature Routing config + profiles + questions        已完成；16-case Feature Golden
             -> FeatureRoutingResult                             Feature 完成边界已冻结
Milestone 2a Source Registry + 首批 Adapter/Clause + annotation       已实现
Milestone 2b 双审 consensus + 人工 curation publication 合同         代码合同已实现；真实 round-2 20/21
Milestone 2c Retrieval request/evidence + exact/hybrid + Docker runtime core/runtime 已实现；fixture tested
Milestone 2d 50~100 条人工确认 Clause + 首个真实索引                  待真实 curation
Milestone 3  10~20 条高精度 Rules + Prompt v1 + JSON Finding
             + 一个可回放的 CLI tracer bullet
Milestone 4  扩展真实 Retrieval/Final Review Golden Set
Milestone 5  GitCode 回写 + 人工 adjudication + 线上指标
Milestone 6  更多来源、知识沉淀、规模化评测和持续优化
```

Milestone 1 和 Milestone 2 可以并行开发，但二者都必须通过稳定契约汇合到 Milestone 3。
当前不应先做 Reranker、全量 Skills 导入、全量 XTS 索引或 GitCode 服务化。
