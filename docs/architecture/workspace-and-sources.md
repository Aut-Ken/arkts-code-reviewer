---
title: 多仓库工作区与知识来源架构
status: canonical
updated: 2026-07-12
---

# 多仓库工作区与知识来源架构

## 1. 本文负责什么

本文是本地多仓库布局、外部资料分类、来源权威度和数据落盘边界的唯一架构基线。

它回答四个问题：

1. 哪些内容属于评审系统代码，哪些属于知识源、代码语料或分析工具。
2. 19 个已登记仓库分别允许做什么，不允许做什么。
3. 原始仓库如何经过整理后进入知识索引、Prompt 和评测。
4. 后续代码应该通过什么契约访问这些仓库，而不是在业务代码中硬编码路径。

具体 commit、remote、branch、稀疏检出路径和 ingestion allowlist 以
`/home/autken/Code/arkts-knowledge/registry/sources.yaml` 为准。

## 2. 当前工作区

```text
/home/autken/Code/
├── arkts-code-reviewer/      # 本项目代码、配置、Prompt、测试和 canonical 文档
├── arkts-knowledge/          # 原始知识源、来源登记、人工整理和知识 Schema
├── arkts-corpora/            # XTS、Samples、Codelabs 代码语料
├── arkts-tools/              # 编译器、分析器和构建工具源码
├── arkts-models/             # 后续按模型制品方式下载；当前为空
├── arkts-review-data/        # 构建产物、索引、缓存、报告和评测运行结果
└── arkui_ace_engine/         # 独立的大型 ArkUI 实现语料
```

目录所有权：

| 目录 | 内容是否人工编辑 | 是否进入主项目 Git | 运行时角色 |
|---|---:|---:|---|
| `arkts-code-reviewer` | 是 | 是 | 应用和版本化策略 |
| `arkts-knowledge/sources` | 否，只跟随上游 | 否 | 离线知识构建输入 |
| `arkts-knowledge/curation` | 是 | 独立治理 | 人工确认的稳定知识层 |
| `arkts-corpora` | 否 | 否 | Parser/规则/检索/评审评测输入 |
| `arkts-tools` | 否 | 否 | 实现参考或经审批调用的工具 |
| `arkts-models` | 否 | 否 | 本地模型制品，不是知识源 |
| `arkts-review-data` | 由程序生成 | 否 | 可删除、可重建的运行产物 |

主项目不能通过扫描 `/home/autken/Code` 猜测仓库；目标实现必须读取来源登记，并允许
使用登记中的 `env_override` 覆盖机器相关路径。

## 3. 当前已登记资产

截至 2026-07-10，来源登记共 19 项：

```text
knowledge_source  11
code_corpus        4
analysis_tool      4
```

当前本地快照：

```text
知识资料       3.2 GiB，22,523 个文件
Markdown       7,535 个
API 声明       3,676 个 .d.ts/.d.ets
代码语料       4.8 GiB，加独立 arkui_ace_engine 451 MiB
ArkTS/TS 语料  约 40,051 个 .ets/.ts
分析工具       85 MiB
```

这些数量只描述当前稀疏检出快照，不是产品能力或知识条款数量。

## 4. 知识来源分层

### 4.1 A 层：规范与官方事实候选

| source id | 内容 | 进入知识库的方式 |
|---|---|---|
| `arkui-specs` | ArkUI 特性规格、设计和结构化条款 | 条款拆分、状态和版本校验后进入稳定 Clause |
| `openharmony-docs` | ArkTS/ArkUI、性能、安全、DFX、应用模型和发布文档 | 按标题和语义段落拆分，保留版本与锚点 |
| `arkcompiler-runtime-docs` | ArkTS 语言规格和运行时语义 | 必须按语言模式和版本过滤 |
| `openharmony-security` | 安全公告和安全流程 | 提取漏洞类型、影响版本和修复状态 |

`index_as_normative_knowledge=true` 表示“允许成为规范候选”，不是允许把原始文件直接
送进 Prompt。所有内容仍需经过解析、去重、版本适用性判断和人工治理。

### 4.2 B 层：结构化事实、规则和版本元数据

| source id | 内容 | 主要产物 |
|---|---|---|
| `interface-sdk-js` | API 定义、since/deprecated、权限和 SystemCapability | `ApiSymbolCatalog` 与 Retrieval 过滤条件 |
| `third-party-typescript` | ArkTS Linter 实现、限制项和测试 | 候选确定性规则及正反例，不直接作为规范 |
| `manifest` | OpenHarmony 版本到仓库 revision 的映射 | `ReleaseSourceMap` |
| `release-management` | 生命周期、维护状态和测试报告 | `ReleasePolicy` 与版本过滤元数据 |

这类资料主要产生机器可用的元数据和规则候选。规则激活前必须能追溯到规范、编译器
行为或经确认的团队策略。

### 4.3 C 层：候选 Skills 和工作流

| source id | 内容 | 允许用途 |
|---|---|---|
| `openharmony-stability-tools` | Mentor 提供的稳定性知识和 Skills | 候选检查项、分类和工作流 |
| `developtools-dfx-skills` | 崩溃、冻结、泄漏等 DFX 分析流程 | DFX taxonomy 和候选检查方法 |
| `openharmony-skills` | 大量 OpenHarmony 开发、评审和测试 Skills | Prompt 模式、工作流和规则候选 |

Skills 的处理边界：

```text
可以：提取检查流程、问题分类、候选规则和 Prompt 设计模式
不可以：把 SKILL.md 当成官方规范
不可以：运行仓库脚本后直接信任结果
不可以：把整个 Skill 动态拼进生产 Prompt
```

Prompt 模板最终属于 `arkts-code-reviewer`，经过评审和版本化后使用；Skill 只作为设计
输入。Skill 中的事实性内容需要独立官方来源佐证。

## 5. 代码语料

| source id | 当前检出范围 | 主要用途 | 特别约束 |
|---|---|---|---|
| `arkui-ace-engine` | 完整浅克隆 | Parser、ReviewUnit、Tag 和实现参考 | 实现不是规范，样本偏框架代码 |
| `xts-acts` | ArkTS/ArkUI、并发、安全、DFX、Ability 等评审相关子树 | 一致性、边界、负例、规则回放 | 测试文件不能默认视为正确写法 |
| `applications-app-samples` | 除超大 DocsSample 外的主要 ArkTS 分类 | 真实应用模式和端到端评测 | 示例可能面向不同 API 版本 |
| `codelabs` | 完整浅克隆 | 完整教学应用和 ArkUI 模式 | 教学简化写法不能自动升级为规范 |

代码语料不进入规范 Clause 表。它们用于：

```text
Parser corpus
ReviewUnit Golden Set
Tag/Dimension 表驱动样本
Rule 正例、反例和边界例
Retrieval query -> expected rule_id 评测
Final Review Golden Set
```

测试或 Golden Case 必须记录 `source_id + revision + relative_path`。需要长期稳定的最小
片段可以复制进 `tests/golden`，但必须同时保存来源和许可信息。

### 5.1 ReviewUnit 第一阶段抽样边界

ReviewUnit Golden 优先使用主仓库中的小型合成 source。第一批真实边界样本只允许从
`tests/fixtures/arkui_ace_engine_samples.json` 已登记的 R63 路径中定点读取，固定 revision：

```text
arkui-ace-engine@39f2c7cc8e25019ce5d0934980b7721614b7eaa2
```

建议第一批最多使用以下 6 个文件，不做目录递归扫描：

| Case | 用途 |
|---|---|
| R63-008 | 普通方法、Builder、短 build |
| R63-009 | 生命周期和 HostSummary |
| R63-038 | 长 Builder、重复自定义组件 |
| R63-044 | 小文件和重复 UI occurrence |
| R63-050 | 超过当前 160 行阈值的长 build |
| R63-055 | 状态字段、生命周期和重复 Text |

外部文件只用于选择、复制和人工标注最小稳定片段；普通测试不得依赖完整外部 checkout。
Golden 必须保存 `source_id + revision + relative_path + content hash + origin lines`。R63 源码、
Parser 输出和 declaration 边界都不能自动变成 ReviewUnit expected。

第一阶段不得扫描 `arkts-knowledge/sources`、`arkts-tools`、XTS、Codelabs 或 Samples。
`applications-app-samples` 虽登记为 ReviewUnit corpus，也必须先建立 selected-path manifest
再引入。`tests/Grok_Expected` 只保存 Parser candidate，不是 ReviewUnit truth。

## 6. 分析工具

| source id | 当前检出范围 | 决策用途 |
|---|---|---|
| `arkcompiler-ets-frontend` | Parser、Checker、Linter、AST verifier 和测试 | 编译器诊断真值、未来 Parser 适配评估 |
| `developtools-ace-ets2bundle` | `compiler/` | ArkUI 语法转换和构建诊断参考 |
| `arkanalyzer` | `src/docs/tests/config/packages` | AST、IR、调用图和数据流方案评估 |
| `deveco-cli` | 完整浅克隆 | 工程结构、构建命令和诊断采集评估 |

这些仓库不是在线知识索引。第一阶段只读源码和文档，不自动执行脚本或构建工具。
需要接入某个工具时，必须新增独立 adapter、固定版本、超时、资源限制和输出 schema。

## 7. 来源登记契约

当前 `sources.yaml` 每项至少记录：

```yaml
- id: openharmony-docs
  group: knowledge_source
  kind: official_documentation
  remote: https://gitcode.com/openharmony/docs.git
  local_path: /home/autken/Code/arkts-knowledge/sources/official-docs/openharmony-docs
  env_override: OPENHARMONY_DOCS_PATH
  branch: master
  revision: <immutable commit>
  shallow_clone: true
  checkout:
    mode: sparse
    include: [...]
  use_for: [...]
  ingestion:
    include: [...]
    exclude: [...]
    execute_repository_scripts: false
    index_as_normative_knowledge: true
  governance:
    authority: official_documentation
    curation_required: true
    raw_prompt_use_allowed: false
```

实现 `SourceRegistryLoader` 时必须校验：

- `id` 唯一。
- 本地目录、Git remote、branch 和 revision 与登记一致。
- ingestion 路径不能越过仓库根目录。
- `execute_repository_scripts` 当前必须为 `false`。
- `raw_prompt_use_allowed` 当前必须为 `false`。
- 构建日志记录实际读取的 source revision，而不是只记录 branch。

## 8. 从原始来源到在线 Evidence

```text
sources.yaml
    |
    v
Source Registry Loader
校验路径、commit、allowlist、authority
    |
    v
Source Adapters
Spec / Markdown / API / Security / Release / Rule / Skill
    |
    v
NormalizedDocument + SourceRef
    |
    v
Clause Parser / Metadata Extractor
    |
    +--> ApiSymbolCatalog / ReleaseMetadata / RuleCandidate
    |
    v
Curation
去重、冲突、版本适用性、人工状态
    |
    v
Stable Clause Store
    |
    v
Index Enrichment
Tags / Dimensions / API / scenario / embedding
    |
    v
Versioned Retrieval Index
    |
    v
Evidence Pack
```

在线 Retrieval 只能读取已发布的稳定 Clause 和索引，不能临时扫描原始仓库。

## 9. SourceRef 与版本束

任何 Clause、API 元数据、规则候选和 Golden Case 都必须携带：

```jsonc
{
  "source_id": "openharmony-docs",
  "revision": "c8f5fb6c...",
  "relative_path": "zh-cn/application-dev/.../example.md",
  "anchor": "heading-or-lines",
  "authority": "official_documentation",
  "content_hash": "sha256:..."
}
```

一次知识构建生成 `source_bundle_id`：

```text
hash(sorted(source_id + revision + ingestion profile))
```

`index_version` 必须引用这个 bundle。这样上游仓库更新后，旧 ReviewReport 仍能复现当时
看到的知识版本。

## 10. 落盘边界

```text
arkts-knowledge/sources/     原始上游，只读
arkts-knowledge/curation/    人工维护的稳定条款和映射
arkts-review-data/normalized/ Adapter 输出和候选 Clause 等可重建中间产物
arkts-review-data/reports/knowledge-builds/  单次构建清单、诊断和质量报告
PostgreSQL                                已发布 Clause、元数据和在线索引
arkts-review-data/reports/reviews/        评审 JSON、渲染结果和审计信息
```

Embedding、切块、缓存、数据库导出和模型响应不能写回任一上游 clone。

## 11. Clone 与更新策略

当前全部仓库使用浅克隆；大型仓库使用 sparse-checkout，并在克隆时设置
`GIT_LFS_SKIP_SMUDGE=1`。因此当前工作区保证代码和文本可用，但部分图片或二进制资产
可能只是 LFS 指针。

更新流程：

```text
选择单个 source
-> git pull --ff-only
-> 记录新 commit
-> 检查 sparse profile 和 ingestion allowlist
-> 更新 sources.yaml
-> 构建新 source_bundle/index_version
-> Golden Set 通过
-> 原子切换 current alias
```

禁止无登记地自动跟踪 `master/main` 最新内容并覆盖线上索引。

## 12. 第一阶段接入顺序

第一条可运行知识链路只接三类来源：

1. `arkui-specs`：提供稳定编号条款。
2. `openharmony-docs`：补充官方开发指导。
3. `interface-sdk-js`：提供 API 名称、版本和权限元数据。

先选择状态管理、定时器/资源生命周期、async/taskpool/worker 三个域，人工确认
50~100 条 Clause，完成精确检索和 Golden Set。之后依次接入语言规格、安全公告、
Linter 规则、版本元数据；Skills 最后进入人工候选流程。

## 13. 当前未完成事项

- 主项目还没有 `SourceRegistryLoader` 和各类 Source Adapter。
- `arkts-knowledge/curation` 尚未形成可运行 Clause 数据集。
- PostgreSQL/pgvector schema、迁移和 index builder 尚未实现。
- API 版本与目标 HarmonyOS/OpenHarmony 版本的产品策略尚未确认。
- 各来源许可允许的内部索引、引用和派生数据范围还需要正式审查。
- 模型权重和内网 Embedding/LLM 服务尚未选型或落盘。

这些缺口必须在文档中保持为未实现状态，不能因为仓库已经 clone 就视为知识库已经建成。
