---
title: ArkTS Code Reviewer 整体架构
status: canonical
updated: 2026-07-12
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

截至 2026-07-12，代码覆盖流水线前半段和 Parser 质检旁路；外部知识、语料和工具已经
完成分类落盘，但“资料已经 clone”不等于知识构建和在线检索已经实现。

| 模块 | 状态 | 当前事实 |
|---|---|---|
| 输入与编排 | `partial` | CLI 可读取文件和手工 hunk；无 Git diff 解析、Webhook、队列和服务 |
| Parser | `partial` | merged-L1 Parser v1 已在 `2c1df96` 冻结并通过确定性门禁；v2 occurrence owner/span 和独立分发仍未实现 |
| ReviewUnit | `partial` | 16-case 独立 Golden 和 span-qualified `unit_id` 已实现；多 owner、质量传播、事实作用域和二次解析尚未解决 |
| Tags / Dimensions | `partial` | 24 Tags 和 12 Dimensions 硬编码实现，尚未配置化 |
| 知识库构建 | `partial` | 11 个知识来源及固定 revision 已登记；无 registry loader、Clause parser、数据库或真实索引 |
| Retrieval | `designed` | 无代码 |
| Rules | `designed` | 无代码 |
| Prompt / Final LLM | `designed` | 无生产评审代码；GLM 只用于 Parser 质检 |
| 输出与 GitCode | `planned` | 无代码 |
| 评测闭环 | `designed` | 只有 Parser/前置链路测试，未形成最终评审 Golden Set |
| Parser Validation | `partial` | 确定性 v1 release gate 已完成；Grok candidate evidence 尚未晋级，GLM judge 仍为 experimental |

当前 `AnalysisResult` 能输出 ReviewUnit、RetrievalQuery 原料和 Parser metadata，不能输出正式代码评审 Finding。

### 2.1 当前可复核结果

```text
pytest after npm ci             78 passed, 64 subtests passed
Parser Golden                   15 cases / strict L0 baseline / perfect merged-L1
LexicalParser real samples     63 parsed / 0 missing / 0 crashed
Merged-L1 real samples          63 L1 / 0 missing / 0 crashed
R63 L1 AST warnings             7 files with ERROR / 7 files with missing nodes
Declarations                   L0 2,880 / merged-L1 5,414
Golden snapshot provenance      4/4 match the pinned external revision
Provisional candidates          23 cases; value fields exact except 2 known bad annotation spans
ReviewUnit Golden               16 cases; RU-1 target 9/9; 7 future-phase gaps visible
Source registry                19 entries / all revisions verified / clean worktrees
```

这里的 “perfect merged-L1” 只覆盖冻结的 v1 集合字段和 declaration 行级 span。它不代表
全 ArkTS 语法 99% 准确，也不覆盖 fact occurrence span/owner、raw-L1 diagnostics 或类型解析。
23 个候选样本的旧 symbol evidence 仍有 441 项不满足冻结证据政策，因此不能当作正式真值。

### 2.2 当前开发边界

Parser v1 足以为 ReviewUnit 提供经过验证的 declaration 行级 occurrence。当前集合字段仍是
文件级 presence signal，不能证明某个 API、modifier、decorator 或 syntax 属于某个 Unit。
ReviewUnit 的 RU-0/RU-1 已建立独立 Golden 并修复 span-qualified identity；下一步是 RU-2
多 owner 和质量传播。删除 Unit 二次 Parser 仍必须在 Unit exact facts 与 file hints 的契约
明确后单独验收。

当前真实调用次数是每文件 `1 + U` 次 Parser，其中 `U` 是去重后的 ReviewUnit 数。截取声明
并重新解析还可能把 struct method 改成顶层 function，且第二次解析的 layer/warning 没进入
Analysis metadata；这既是性能问题，也是正确性问题。

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
每个发生变化的完整文件解析一次，产出带位置的代码事实
        |
        +-----------------------------+
        |                             |
        v                             v
03 ReviewUnit                    04 Feature Routing
选择语义完整上下文              Facts -> Tags -> Unit Dimensions
        |                             |
        +--------------+--------------+
                       |
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
| Feature Routing | 将事实映射为场景和检查方向 | 判断是否违规 |
| Knowledge Base | 保存稳定、可追溯条款 | 在线检索和评审 |
| Retrieval | 返回相关 Evidence | 判断代码是否违反 Evidence |
| Rules | 输出高确定性静态问题 | 处理需要复杂语义的建议 |
| Prompt / LLM | 在约束内做语义判断和建议 | 编造条款、自由扩大评审范围 |
| Output | 校验、去重、渲染和回写 | 重新判断代码语义 |
| Evaluation | 度量、归因和回流 | 直接改变线上结论 |

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
| Embedding | 内网可部署模型，候选 bge-m3，需 Golden Set 选型 |
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
             多 owner + Parser quality diagnostics               RU-2 当前下一项
Milestone 1b Unit facts 作用域 + parse-once + Unit Tags/Dimensions
Milestone 1c 精确 ChangeSet + related context + token budget
Milestone 2  Source Registry Loader + 三类 Source Adapter
             + 50~100 条人工确认 Clause + 精确检索
Milestone 3  10~20 条高精度 Rules + Prompt v1 + JSON Finding
             + 一个可回放的 CLI tracer bullet
Milestone 4  Embedding 混合召回 + Final Review Golden Set
Milestone 5  GitCode 回写 + 人工 adjudication + 线上指标
Milestone 6  更多来源、知识沉淀、规模化评测和持续优化
```

Milestone 1 和 Milestone 2 可以并行开发，但二者都必须通过稳定契约汇合到 Milestone 3。
当前不应先做 Reranker、全量 Skills 导入、全量 XTS 索引或 GitCode 服务化。
