---
title: 05 知识库构建模块
status: canonical
implementation: partial
updated: 2026-07-10
---

# 05 知识库构建模块

## 1. 模块职责

将已经登记的外部规范、官方文档、API 元数据、规则候选和人工材料，转换为稳定、
可引用、可检索、可版本化的知识资产。

```text
Source Registry
-> Source Adapters
-> Normalized Documents / Metadata
-> Clause Parsing and Curation
-> Stable Knowledge
-> Retrieval Annotation and Embedding
-> Versioned Index
```

本模块负责离线构建，不负责在线检索，也不负责判断被评审代码是否违反条款。

## 2. 当前真实状态

已经完成：

- `/home/autken/Code/arkts-knowledge/registry/sources.yaml` 登记 19 个仓库。
- 其中 11 项属于 `knowledge_source`，均固定 remote、branch、commit 和检出范围。
- 原始资料、代码语料、分析工具、模型和运行产物已经分目录隔离。
- ingestion allowlist、权威度、人工整理要求和 Prompt 禁用边界已登记。
- 当前知识资料约 22,523 个文件，包括 7,535 个 Markdown 和 3,676 个 API 声明。

尚未完成：

- 主项目没有 Source Registry loader。
- 没有 Source Adapter、Clause parser、API catalog builder 或 curation workflow。
- 没有数据库迁移、真实 Clause 数据集、Embedding 或在线索引。
- 没有知识构建 Golden Set 和发布门禁。

因此本模块状态是 `partial`：来源管理基线已经存在，知识构建执行链尚未实现。

## 3. 来源分类和处理策略

详细多仓库边界见
[多仓库工作区与知识来源架构](../architecture/workspace-and-sources.md)。

### 3.1 规范和官方事实候选

| source id | Adapter | 主要产物 |
|---|---|---|
| `arkui-specs` | `ArkuiSpecAdapter` | 有稳定编号的 Feature/Rule Clause |
| `openharmony-docs` | `OpenHarmonyDocsAdapter` | 官方开发指导 Clause |
| `arkcompiler-runtime-docs` | `ArktsLanguageSpecAdapter` | 带语言模式和版本的语言 Clause |
| `openharmony-security` | `SecurityAdvisoryAdapter` | 安全 Clause、漏洞类型和影响版本 |

### 3.2 API、版本和规则元数据

| source id | Adapter | 主要产物 |
|---|---|---|
| `interface-sdk-js` | `InterfaceSdkAdapter` | `ApiSymbolCatalog`、since/deprecated、权限和 SystemCapability |
| `third-party-typescript` | `ArktsLinterAdapter` | `RuleCandidate` 和正反测试引用 |
| `manifest` | `ManifestAdapter` | release 到 repository revision 的映射 |
| `release-management` | `ReleasePolicyAdapter` | 生命周期、维护状态和测试报告元数据 |

### 3.3 候选 Skills

```text
openharmony-stability-tools
developtools-dfx-skills
openharmony-skills
```

Skills 第一版不进入在线 Clause 索引。后续只通过人工流程提取：

```text
候选检查项
候选问题 taxonomy
候选 Rule
Prompt/工作流设计模式
```

事实性内容必须找到独立规范来源；Prompt 模式必须进入主项目评审和版本化，不能在运行时
动态加载整份 `SKILL.md`。

## 4. 明确不属于规范知识的内容

以下内容虽然已经 clone，但默认不能成为规范 Clause：

```text
arkui_ace_engine / XTS / Samples / Codelabs 代码
编译器、ArkAnalyzer、DevEco CLI 等工具源码
Skill 指令文本
LLM 自己生成的经验
```

它们可以用于实现参考、规则验证和 Golden Set。只有经过人工审核、补充权威来源并进入
curation 后，才可能形成团队策略 Clause。

## 5. Source Registry Loader

目标包：

```text
src/arkts_code_reviewer/knowledge/
├── registry.py
├── models.py
├── adapters/
├── parsing/
├── curation/
├── indexing/
└── cli.py
```

`SourceRegistryLoader` 输入：

```text
ARKTS_SOURCE_REGISTRY
或开发环境显式路径
```

启动时必须校验：

1. source id 唯一。
2. 本地仓库存在，remote、branch 和 `HEAD` 与登记一致。
3. 环境变量覆盖路径后仍然指向同一 remote/revision。
4. include/exclude 路径不越过仓库根目录。
5. 当前所有来源 `execute_repository_scripts=false`。
6. 当前所有来源 `raw_prompt_use_allowed=false`。

校验成功后计算 `source_bundle_id`：

```text
sha256(sorted(source_id + revision + ingestion_profile_hash))
```

## 6. Adapter 契约

所有文本型 Adapter 输出 `NormalizedDocument`：

```python
class SourceAdapter(Protocol):
    def discover(self, source: SourceRecord) -> list[SourceObject]: ...
    def load(self, obj: SourceObject) -> NormalizedDocument: ...
```

`NormalizedDocument` 至少包含：

```text
document_id
source_ref
media_type
title
heading_tree
body
language
release/api_level/language_mode
adapter_version
diagnostics
```

Adapter 只负责忠实读取和结构化，不负责让 LLM 改写文档，也不直接决定最终 Dimensions。

API 和 release Adapter 可以输出专用结构化模型，不强制伪装成 Markdown 文档。

## 7. SourceRef

任何知识和元数据都必须追溯到：

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

数据库不保存依赖当前机器的绝对路径。`local_path` 只用于本轮离线构建。

## 8. Clause 切分策略

### 8.1 有稳定编号的规格

优先按原始编号切分：

```text
R / AC / US / VM / ADR / BR / ER / FR / RC
```

一个可独立引用的规则对应一个 Clause，保留 heading path、父节和相邻规则 ID。

### 8.2 官方开发文档

不能按固定字符数粗切。顺序是：

```text
标题层级
-> 列表/注意/约束块
-> 语义段落
-> 超长段落才按 token 安全切分
```

代码示例与说明关联保存，但示例代码默认不是规范正文。

### 8.3 语言规格和安全公告

额外保存：

```text
ArkTS language mode
API level / OpenHarmony release
受影响版本
修复状态
适用范围
```

缺少适用版本时不得由系统自行猜测。

## 9. 稳定知识模型

目标 `KnowledgeClause`：

```text
rule_id
rule_type
status = Draft | Baselined | Deprecated
authority
text
heading_path
parent_context
neighbor_rule_ids
applicability
source_ref
doc_hash
curation_version
created_at / updated_at
```

`rule_id` 必须稳定。首选来源自带 ID；无稳定 ID 时使用
`source_id + normalized path + heading anchor + local sequence`，并保存重命名映射。

## 10. API Symbol Catalog

`interface-sdk-js` 产生与 Parser 共用的 canonical catalog：

```text
canonical_name
aliases
module
kind
signature
since
deprecated_since
permissions
system_capabilities
source_ref
catalog_version
```

用途：

- Parser 过滤内部业务调用和统一 import alias。
- Tags/Rules 识别 API 场景。
- Retrieval 精确匹配和 API level 过滤。
- Finding Validator 检查建议中的 API 是否真实存在。

不能分别在 Parser、Retrieval 和 Prompt 中维护三份 API 白名单。

## 11. 三层存储

### 11.1 稳定层

```text
kb_clauses
api_symbols
release_metadata
rule_candidates
source_snapshots
```

保存来源事实和人工治理结果，不因 Embedding 或 Dimensions 调整而重写原文。

### 11.2 可重建索引层

```text
rule_id
index_version
func_ids[]
dimension_ids[]
tags[]
apis[]
components[]
decorators[]
raw_keywords[]
llm_keywords[]
scenario
embedding
annotation/enhancer/embedding versions
```

### 11.3 审计映射层

记录每个 Tag、Dimension、API 和关键词由谁产生：

```text
deterministic parser
source metadata
human curator
approved LLM enrichment
```

## 12. 权威度、冲突和适用性

排序不能只看“官方/非官方”，必须同时判断版本和场景。

推荐优先级：

```text
经过确认的内部 Baselined 规范
适用版本的 ArkUI feature spec / ArkTS language spec
适用版本的 OpenHarmony 官方文档和安全公告
官方 API 元数据
经交叉验证的编译器/Linter 规则
团队审核案例
Skills/LLM 候选
```

冲突时不能静默选一条。构建报告必须输出冲突组，由领域 owner 决定：

```text
保留新版本、旧版本 Deprecated
按 API level 并存
标记为待确认，不发布到 current index
```

## 13. Enrichment 和 Embedding

确定性 enrichment 优先提取：

```text
反引号标识符
代码块中的 API/组件/装饰器
API catalog 精确匹配
原文关键词
来源结构化字段
```

可选 LLM enrichment 只生成 `scenario` 和补充关键词，不允许覆盖原文和版本信息。
内部资料只能发送到经过批准的模型服务。

Embedding 文本建议：

```text
scenario + heading_path + applicability + clause text
```

Embedding 模型尚未选型。`bge-m3` 只是候选，不是当前依赖或生产结论。

## 14. 增量构建和发布

```text
读取固定 source bundle
-> 发现变更对象
-> 未变化 content_hash 复用稳定层
-> 重建受影响 Clause/metadata
-> 运行 schema、冲突、引用和覆盖检查
-> 构建新 index_version
-> 运行 Retrieval Golden Set
-> 原子切换 current alias
```

任何 Adapter 部分失败、来源 revision 漂移、重复 rule id 或 Golden Gate 失败，都禁止切换
线上 alias。

## 15. 落盘边界

```text
/home/autken/Code/arkts-knowledge/sources      原始只读输入
/home/autken/Code/arkts-knowledge/curation     人工稳定知识和映射
/home/autken/Code/arkts-review-data/normalized Adapter 输出和候选 Clause 中间产物
/home/autken/Code/arkts-review-data/reports/knowledge-builds
                                                 构建清单、诊断和质量报告
PostgreSQL                                      发布后的稳定层和检索索引
```

Embedding、模型响应、缓存和构建临时文件不能写回上游 clone。

## 16. 技术栈

| 层 | 技术 |
|---|---|
| 模型和校验 | Pydantic v2 |
| Markdown | markdown-it-py + source-specific 状态机 |
| YAML | ruamel.yaml |
| DB | PostgreSQL + SQLAlchemy 2.x + psycopg3 |
| 向量 | pgvector，精确检索基线通过后启用 |
| 模糊检索 | pg_trgm |
| Embedding | 内网服务或本地模型，待 Golden Set 选型 |
| 测试 | pytest + testcontainers |

## 17. 第一版范围

第一版只接入：

```text
arkui-specs
openharmony-docs
interface-sdk-js
```

选择三个知识域：

```text
状态管理和 ArkTS 语言限制
定时器/订阅/资源生命周期
async/taskpool/worker
```

交付 50~100 条人工确认 Clause、一个 API catalog 切片、精确检索和 30~50 个
`query -> expected rule_id` Golden Case。没有 Golden Set 前不接 Embedding，不批量导入 Skills。

## 18. 测试和质量门禁

- Registry 路径、remote、revision 和 allowlist 校验。
- 每类 Adapter 的最小真实 fixture。
- Markdown 标题、编号条款、表格、代码块和异常格式。
- `rule_id` 稳定性、重复和重命名映射。
- SourceRef 锚点和 content hash 可复现。
- API alias、since/deprecated 和权限字段解析。
- 版本冲突和 Deprecated 行为。
- 增量重建范围和失败不切 alias。
- PostgreSQL/pgvector 真实契约测试。
- Source bundle 和 index version 可复现。

## 19. 下一步

1. 在主项目定义 `SourceRecord`、`SourceRef`、`SourceBundle` 和 registry loader。
2. 先实现 `ArkuiSpecAdapter`、`OpenHarmonyDocsAdapter`、`InterfaceSdkAdapter`。
3. 为三个域人工确认第一批 Clause 和 API metadata。
4. 实现稳定层迁移和不依赖 Embedding 的精确索引。
5. 与 Retrieval 模块共同建立 Golden Set，再决定 Embedding 模型。
