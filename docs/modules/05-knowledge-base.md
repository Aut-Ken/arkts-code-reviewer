---
title: 05 知识库构建模块
status: canonical
implementation: partial
updated: 2026-07-23
---

# 05 知识库构建模块

## 1. 模块职责

将已经登记的外部规范、官方文档、API 元数据、规则候选和人工材料，转换为稳定、
可引用、可检索、可版本化的知识资产。

```text
Source Registry
-> Source Adapters
-> Normalized Documents / Metadata
-> Static MarkdownDocumentMap
-> controlled Document Card / Document Catalog (navigation only)
-> SourceAtomSet / deterministic L2 Document Projection (shadow only)
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
- 主项目已实现严格 Source Registry loader、固定 revision 校验和稳定
  `source_bundle_id`。
- `knowledge-seed-v1` 固定首批 3 个来源、3 个知识域和 24 个文件；Adapter 从
  Git object 读取，不受外部仓库工作树污染。
- 已实现 Document-First 导航切片：Markdown 原文先由确定性解析器生成
  `MarkdownDocumentMap`，再以内容寻址的 Request/DispatchPlan 受控调用 DeepSeek，模型响应经
  严格校验后只能生成 `navigation_only_not_evidence` 的 `DocumentCard`。
- 已实现 `DocumentCatalogBuild` 合同。Catalog 只把已验证的 Document Map 与 Card 按稳定来源
  身份排序并封装为导航目录，固定 `evidence_eligible=false`、`production_qualified=false`；它
  不是 Clause、KnowledgeIndex 或 EvidencePack。
- 已实现 L1/L2 Projection 的确定性基础合同：`SourceAtomSet` 将 L1 Markdown 建成可重建的原文
  Atom；封闭 Mapping 允许一个 Atom 绑定多个检索分类；编译器生成“多分类链接 + 单份原文单元
  库”的 L2 Markdown；机械 verifier 拒绝未知引用、Atom 漏覆盖、正文突变和 identity 漂移。
- 已建立独立 PostgreSQL `document_projection` schema、checksum migration runner 和不可变 Store。
  写入采用 `building -> mechanically_verified` 单向封存，封存前检查行数与分类覆盖，封存后禁止
  追加子行；完整 Record 与 Atom/Binding/多对多关系同时保存并互相校验。最终状态固定为
  `mechanically_verified`、`evidence_eligible=false`、`production_qualified=false`。真实临时 PostgreSQL
  已验证 migration、首次写入、封存、读取、幂等重放以及封存后追加拒绝，但数据库中尚无真实
  DeepSeek/Grok 产出的 L2 资产。
- `taskpool-vs-worker.md` 已完成一次受控真实 Document Card smoke：1 次尝试、0 次重试，Provider
  返回 `valid_card`，5/5 章节摘要通过结构和 provenance 重建，并生成 1 条目的 Document Catalog。
  这只证明执行合同与结构链可用；该 Card 的 `important_apis=[]`，而原文含有
  `@Concurrent`、`ArrayBuffer`、`SharedArrayBuffer`、`Sendable`、`Promise`、`async/await`、
  `onmessage` 等明确提示，因此内容质量仍是 `NOT QUALIFIED`。
- 已为另外 7 篇固定 Markdown 生成并执行受控 Campaign。Campaign ID 为
  `document-card-campaign:sha256:1909052b40883a6d259024d8cc210d2c6a7852471f109fd680000c113f2d977c`，
  Plan-set digest 为
  `document-card-campaign-plan-set:sha256:8b4b80e8eeff96b4010591d91c283e44b0bcc0c1cb5944f19e854fa79b63fe7a`；
  它固定 7 次最大尝试、163,932 bytes 请求正文、28,672 输出 token、14,000,000 bytes 响应正文
  和 840,000 ms 聚合超时预算。离线 inspect 本身没有读取 API Key 或发送网络请求；2026-07-22
  用户另行精确批准后，受控 live runner 按固定顺序逐篇尝试一次并全部返回 `valid_card`，无重试、
  无失败。根回执为
  `document-card-campaign-live-receipt:sha256:49e7609094eac9639c64ba29e9598e66fc3c143a8cf61c21636c66bcd5b88411`。
- 本次真实 Campaign 累计输入 45,992 tokens、输出 11,677 tokens、响应正文 42,194 bytes，
  Provider latency 合计 134,503 ms，Campaign elapsed 为 134,834 ms。7 组 raw response、Draft、
  Card、单篇 receipt 和根 receipt 均通过严格重建；115/115 个静态 section 完整且唯一绑定摘要。
  这些只证明运行、结构和 provenance 合同，不证明导航字段完整。
- 已实现 Markdown ClauseCandidate 解析器和独立 API 声明 sidecar；候选保留绝对
  行号、来源哈希、重载、dynamic/static 版本以及结构化诊断。
- 已实现版本化的确定性 Knowledge annotation：每个 Clause 和 API declaration 都有独立
  target，Tag、Dimension、Domain、API 及 provenance 可重放；同名 API 重载使用
  `declaration_id`，不会碰撞覆盖。
- 已实现本地 Grok 审核包和严格回传校验器。审核包绑定 extraction、annotation、Feature
  配置、Domain 规则、API catalog slice、Prompt、来源摘录及全部哈希；回传必须完整覆盖
  packet，并用 1-based 绝对行号逐字引用 packet 中的来源证据。
- Knowledge Golden 当前有 12 个独立人工 expected case。K-3 的 Clause/API
  structure gate 为 12/12，K-4 annotation gate 为 12/12；完整 gate当前为 4/12，
  其余普通 Clause 在正式 curation 前有意保持 `Draft`。
- 已实现两轮审核回执的 campaign 审计、确定性 consensus build 和严格 loader；两轮必须独立、
  完整覆盖同一 packet 且证据合法，分歧或 correction 会阻止自动发布。
- 已实现 `PublishedKnowledgeBuild` 合同。即使两轮模型都接受，也必须再有逐 Clause 的显式人工
  curation 决策；只有最终接受的 Clause 才会复制为 `Baselined`，内容和全部 provenance 都进入
  content-addressed build identity。

尚未完成：

- 2026-07-13 的模型外发边界仍固定为首批三个来源、24 个路径、`xai/grok-4.5` 和审核 Prompt
  版本；该授权不等于用户已经作出最终人工 curation 决策。
- 真实审核 campaign 的 round-1 已有 21/21 个有效 packet receipt，round-2 为 20/21；缺少的
  packet 使正式 consensus build 继续 fail-closed，当前没有可供 curation 的完整 consensus。
- 当前 Clause 都是候选；普通候选保持 `Draft`，不能直接称为 Baselined 知识。
- 7 文档 Campaign 已有一次真实 DeepSeek 结果，但尚无人工 Document Truth 或正式
  Recall/Precision。人工只读观察没有发现明显的大段编造，但长文档的数值限制、禁止项、例外和
  关键检索符号存在遗漏，TaskPool 卡片尤其明显。当前也没有由这 7 张 Card 构建并验证的多文档
  Document Catalog、Catalog Router 或 Document Card 到 Clause Retrieval 的生产连接。
- Document Card 和 Document Catalog 固定为导航层，不能成为知识证据，不能绕过 Clause
  publication，也不能据此声称 Retrieval 或 EvidencePack 已 qualified。
- L2 Projection 当前只接受调用方提供的 Mapping Draft；尚未实现 DeepSeek Mapping Prompt/runner、
  Grok 审核 receipt/correction loop、通过审核的 L2 Build 或文档检索 runtime。机械校验通过不能
  证明分类正确，也不能把 Projection 提升为 Knowledge Evidence。
- 仓库和本地 review-data 中尚无可供生产索引使用的真实 `PublishedKnowledgeBuild`；因此没有
  真实 Baselined Clause 数据集或 current production index。
- Retrieval 已实现数据库迁移、36-case Golden、本地 embedding 和索引发布运行时，但它不能
  绕过本模块 publication 合同索引 Draft 候选。

因此本模块状态仍是 `partial`：来源、标准化、Document-First 导航合同、受控多文档执行合同、
L1/L2 的静态 Atom/Mapping/编译/机械校验与隔离数据库基础、候选提取、确定性标注、双审
consensus 和 publication 代码合同已经实现；单文档和 7 文档 live 只证明固定输入上的 Card
结构执行链，L2 尚未执行真实双模型生成审核，真实多文档检索质量、人工策展与首个发布数据集仍
未完成。

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
6. `raw_prompt_use_allowed` 必须是显式布尔值；即使为 true，仍必须命中独立的精确
   provider/model/revision/path allowlist。

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

### 6.1 Document-First 导航切片

这一切片先保存整篇 Markdown 的结构和用途，再决定后续应深入处理哪份文档。当前实现链是：

```text
Pinned Git Markdown
-> NormalizedDocument
-> MarkdownDocumentMap              # 确定性标题树、章节 span/hash/identity
-> DocumentCardRequest
-> DocumentCardDispatchPlan         # Prompt、正文、模型和预算全部内容寻址
-> DeepSeek raw response            # 只有显式批准的 live runner 才会发送
-> DocumentCardDraft
-> DocumentCard                     # 摘要、主题、API 提示和逐节摘要
-> DocumentCatalogBuild             # 导航目录，不是知识证据
```

`MarkdownDocumentMap` 不依赖模型。它保留固定 `SourceRef`、正文哈希、标题层级、父子关系、
1-based 行范围和每节内容哈希，并可从同一 `NormalizedDocument` 确定性重建。

Document Card 外发同时受四层约束：Registry 的 `raw_prompt_use_allowed`、独立精确
source/revision/path allowlist、固定 Prompt/model policy，以及调用时对精确 Plan ID、wire body
hash、最大输出预算和 acknowledgement 的批准。单文档 live runner 最多一次 attempt、无重试；
离线 inspect 只生成要发送的精确正文和预算，不会读取 credential 或发起网络请求。

多文档 runner 不把 7 个 Plan 合并成一份大 Prompt。它先对整组 Campaign 做一次精确授权和完整
离线产物检查，再按 canonical 顺序复用单文档 runner。普通 transport、Provider 或 Card 结构失败
会生成该篇的 typed receipt 并继续下一篇；本地完整性、replay 或剩余总时限门禁失败则停止。每个
文件使用原子写入，`09_receipt.json` 作为该篇最后提交文件，根级 Campaign receipt 最后写入；这不
等于跨 `06`～`09` 的多文件事务，异常中断可能保留已消费 marker 和部分审计文件，不能重试。

Card 的结构校验要求每个 Map section 恰好有一条摘要，并将 Map、来源和正文哈希复制进
content-addressed identity。该校验只能证明结构完整和来源绑定，不能证明摘要正确、主题充分或
API 提示召回合格。Card 和 Catalog 都固定为：

```text
use_scope = navigation_only_not_evidence
evidence_eligible = false
production_qualified = false        # Catalog 固定字段
```

因此它们可以作为“先选文档”的导航候选，不能代替 `Baselined` Clause、不能为 Finding 提供
规范依据，也不能让现有 Retrieval 或 EvidencePack 自动获得质量资格。当前只构建了单条目
Catalog 合同产物；多文档 Router 和检索消费尚未实现。

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
内部资料只能发送到经过批准的模型服务。当前首批审核固定使用 Grok Build CLI 的
`grok-4.5`，并关闭 Web、Memory、子代理和工具访问；JSON Schema 约束输出后仍要经过
本地 packet/evidence validator，CLI 返回成功不等于审核结果可用。

这里描述的是 Clause annotation/enrichment；Document-First 的 DeepSeek Document Card 属于独立
导航合同，只生成整篇与逐节摘要、主题和 API 提示，不写入 Clause annotation，也不具备 Evidence
资格。

当前 K-4 的正式行为是：

```text
Clause / API declaration
-> 提取反引号、调用、装饰器和精确结构信号
-> 复用 tags-v1 匹配 Active Tag
-> 只派生有真实 Tag trigger 的 Dimension
-> 按 knowledge_annotations.yaml 派生 Domain 和少量受控 API alias
-> 为每个发布值写 provenance
-> 生成 content-addressed KnowledgeAnnotationBuild
```

Knowledge annotation 不会自动加入 `always_check` Dimension，也不会为了提高覆盖率猜测
模糊 API。Clause 的 `target_id` 是稳定 `rule_id`；API 的 `target_id` 是 collision-safe
`declaration_id`。来源 seed domain 只能作为 `source_metadata` Domain，不能反向制造 Tag
或 Dimension。

Grok 只审核 packet 中的 Clause primary target。相关 API catalog slice 用于核验 Clause
标注；644 个 API declaration 的结构和确定性 annotation 目前不声称已经经过模型审核。
单次模型审核也不能称为人工审核或直接把 `Draft` 晋升为 `Baselined`。

Embedding 文本建议：

```text
scenario + heading_path + applicability + clause text
```

Retrieval v1 已通过独立 Golden 候选评估选择本地 Jina code 768D 模型；模型属于可重建索引
元数据，不改变本模块保存的 Clause 原文、来源和人工 curation 决策。

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
/home/autken/Code/arkts-review-data/reports/knowledge-review
                                                 本地审核包、Prompt、schema 和哈希清单
PostgreSQL                                      发布后的稳定层和检索索引
```

Embedding、模型响应、缓存和构建临时文件不能写回上游 clone。

## 16. 技术栈

| 层 | 技术 |
|---|---|
| 模型和校验 | Pydantic v2 |
| Markdown | markdown-it-py + source-specific 状态机 |
| YAML | ruamel.yaml |
| DB | PostgreSQL + psycopg3；Retrieval publication index 与 L2 `document_projection` shadow schema 隔离 |
| 向量 | pgvector，精确检索基线通过后启用 |
| 模糊检索 | pg_trgm |
| Embedding | Retrieval 当前使用本地 Jina code 768D；生产运行仍需安全审批 |
| 测试 | pytest + 显式 Docker/PostgreSQL 集成测试 |

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
- Annotation 配置 duplicate key、未知引用、排序和 provenance fail-closed。
- 958 个真实 target 完整覆盖，同名 API overload 不覆盖，重复构建逐字节一致。
- 审核 packet 身份、跨包 coverage、来源 exact quote/hash、外发双锁和回传完整性。
- `MarkdownDocumentMap` 标题树、章节 span/hash、父子关系、内容寻址 identity 和严格 loader。
- Document Card Request/Plan 的 Registry + 精确 source/revision/path export policy 双锁、Prompt/body
  hash、单次预算、无重试、原始响应到 Card/Receipt 的完整重建。
- inspect-only Campaign 的 canonical 排序、7 个独立 Plan、Plan-set digest、聚合预算重算，以及
  `credential_accessed=false`、`network_attempted=false`、`execution_authorized=false`。
- Campaign live runner 的全量精确批准、离线产物篡改拒绝、全 Plan replay 预检、固定顺序、一次
  attempt、零重试、typed failure 继续、逐篇落盘、聚合 receipt 与总预算门禁；测试使用注入
  transport证明失败与边界合同；2026-07-22 的 7 文档真实 Campaign 证明一次真实执行链可用，
  仍没有证明内容质量。
- Document Catalog 的稳定排序、重复/错绑拒绝和导航-only/evidence-false 边界。
- SourceAtom/Region 全物理行覆盖、YAML front matter 隔离、严格原文切片、重复标题与异常
  Markdown block；只有标题/Region 而没有 eligible 内容的文档会在 AtomSet 边界明确拒绝。
- L2 Mapping 的未知引用、漏覆盖、classified/unclassified 互斥、多分类和 canonical identity。
- L2 编译的正文零突变、每个 Atom 正文恰好一次、Markdown 注入转义和完整 rebuild。
- `document_projection` migration checksum/prefix/immutability、Store 规范化行交叉校验，以及真实
  PostgreSQL migration/write/load/idempotency round-trip。

## 19. 下一步

1. 补齐真实 round-2 的最后一个有效 packet receipt，并生成正式 consensus；模型不能代替
   后续人工 curation 权限。
2. 在 curation 前冻结可发布 Clause selection：当前 publication 合同要求完整 packet build
   全部 release-ready，若只发布 50～100 条，必须先建立窄化 packet build 或独立的部分发布合同。
3. 对选中 Clause 作出逐条人工 curation 决策，生成并保存首个真实 `PublishedKnowledgeBuild`。
4. 用 Retrieval runtime 构建 exact + Jina code index，运行 Golden/真实查询门禁后原子切换
   current alias。
5. 持续扩展不同来源、冲突和版本适用性的人工 adjudication 数据。
