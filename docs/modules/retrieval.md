# 检索模块详细设计

- 所属系统: ArkTS 代码评审系统（arkts-code-reviewer）
- 文档状态: Draft（用于模块对齐评审）
- 最后更新: 2026-07-03
- 上游文档: [docs/architecture.md](../architecture.md) 第 3.4 节
- 范围: 只覆盖检索模块。评审生成（Prompt）、知识沉淀回流等模块另行成文。

## 1. 模块定位与职责

### 1.1 在系统中的位置

```mermaid
flowchart LR
    B[代码分析<br/>静态解析] --> C[**检索模块**]
    KB[(arkui-specs 知识库<br/>registry + specs)] -->|离线索引| C
    C -->|条款上下文包| D[评审生成<br/>DeepSeek V4]
```

### 1.2 职责边界

**做什么**：

1. 离线：将知识库的 spec 条款解析、增强、构建为可检索索引
2. 在线：接收代码静态分析结果，返回与该代码最相关的知识库条款集合
   （带 ID、可溯源、按 token 预算裁剪）

**不做什么**：

- 不调用大模型做评审（那是评审生成模块的职责；本模块仅在离线增强时用一次 LLM）
- 不解析代码（静态解析属于代码分析模块，本模块消费其产出的特征标签）
- 不判断代码好坏

### 1.3 输入 / 输出契约

输入（来自代码分析模块）：

```jsonc
{
  "code_features": {
    "components": ["Image", "List"],        // 使用的 UI 组件
    "decorators": ["@State", "@Link"],      // 装饰器
    "apis": ["getContext", "animateTo"],    // API 调用
    "tags": ["has_image", "has_async"]      // 特征标签（与维度触发器共用）
  },
  "triggered_dimensions": ["DIM-01", "DIM-06"],  // 已触发的评价维度
  "intent_summary": "图片列表懒加载页面",          // 可选：代码意图摘要
  "token_budget": 8000                            // 条款上下文总预算
}
```

输出（供 Prompt 组装器消费）：

```jsonc
{
  "index_version": "idx-2026-07-03-001",
  "clauses": [
    {
      "rule_id": "04-01-01/Feat-01/FR-03",   // 全局唯一条款 ID
      "func_id": "04-01-01",
      "feat_id": "Feat-01",
      "rule_type": "FR",
      "status": "Baselined",
      "dimension": "DIM-06",                  // 归属的评价维度
      "text": "……条款原文……",
      "source_path": "04-common-capability/01-image-loading/.../Feat-01-...-spec.md",
      "score": 0.87
    }
  ]
}
```

## 2. 数据模型

### 2.1 条款 Chunk 结构

切块原则：**每条 FR / BR / AC / ER 一个 chunk**，不做整文件或整章节切块。
design.md 按 ADR 条目与小节切块。

| 字段 | 说明 | 来源 |
|---|---|---|
| `rule_id` | 全局唯一 ID：`{func_id}/{feat_id}/{rule_type}-{nn}` | 解析器生成 |
| `func_id` | L1-L2-L3 功能域 ID | registry/functions.yaml |
| `feat_id` | 特性 ID | registry/features.yaml |
| `rule_type` | FR / BR / AC / ER / ADR / SECTION | 解析器 |
| `status` | Baselined / Draft / Deprecated | features.yaml 继承 |
| `text` | 条款原文 | spec 文档 |
| `scenario` | 适用场景描述（离线增强生成） | LLM 增强 |
| `keywords` | 组件/API/装饰器关键词列表（离线增强生成） | LLM 增强 |
| `embedding` | scenario + text 的向量 | Embedding 模型 |
| `source_path` | 源文件路径 | 解析器 |
| `doc_hash` | 源文档内容哈希（增量更新判据） | 解析器 |

### 2.2 存储 Schema（pgvector）

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE kb_clauses (
    rule_id      TEXT PRIMARY KEY,
    func_id      TEXT NOT NULL,
    feat_id      TEXT NOT NULL,
    rule_type    TEXT NOT NULL,
    status       TEXT NOT NULL,
    text         TEXT NOT NULL,
    scenario     TEXT,
    keywords     TEXT[],                -- GIN 索引，精确/前缀匹配
    embedding    vector(1024),          -- 维度随 embedding 模型定
    source_path  TEXT NOT NULL,
    doc_hash     TEXT NOT NULL,
    index_version TEXT NOT NULL
);

CREATE INDEX ON kb_clauses USING gin (keywords);
CREATE INDEX ON kb_clauses USING gin (text gin_trgm_ops);   -- 模糊兜底
CREATE INDEX ON kb_clauses USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON kb_clauses (func_id, status);
```

中文分词规避策略：关键词匹配走 `keywords` 字段（离线增强产出，以英文标识符
为主：组件名/API 名/装饰器名），配合 pg_trgm 模糊兜底，**不依赖** zhparser /
pg_jieba 等中文分词扩展。

## 3. 离线索引管道

```mermaid
flowchart LR
    A[arkui-specs<br/>registry + specs] --> B[条款解析器<br/>Markdown → chunks]
    B --> C[LLM 离线增强<br/>scenario + keywords]
    C --> D[Embedding 计算]
    D --> E[(pgvector<br/>新 index_version)]
    E --> F[原子切换<br/>别名指向新版本]
```

### 3.1 条款解析器

- 输入：`registry/functions.yaml`、`registry/features.yaml`、各 `Feat-*.md` / `design.md`
- 按 spec 文档的结构化条款（FR/BR/AC/ER 编号条目）切块；历史文档格式不完全
  统一，解析器需容错并输出"未能解析的文档"清单供人工处理
- `status` 从 features.yaml 继承到条款级

### 3.2 LLM 离线增强（解决词汇鸿沟）

- 问题：条款是中文规范语言，查询是代码特征（英文标识符），直接匹配召回率低
- 方案：离线为每条款生成
  1. `scenario`：一段"什么代码场景适用本条款"的自然语言描述
  2. `keywords`：本条款涉及的组件 / API / 装饰器标识符列表
- 每条款只在首次入库或源文档变更时增强一次，在线零成本
- 增强 Prompt 与产出 schema 待与沉淀模块 Prompt 一并设计（见待定项）

### 3.3 增量更新与原子切换

- **增量**：按 `doc_hash` 比对，只重建变更文档的 chunks（知识沉淀回流会持续
  产生新 spec，全量重建不可持续）
- **原子**：新批次写入带新 `index_version`，全部完成后切换"当前版本"别名；
  在线检索永不读到半成品
- **可追溯**：每份评审报告记录所用 `index_version`
- **触发**：挂 CI —— 知识库 registry 或 spec 变更即触发增量构建
  （工具形态：`tools/build_search_index.py`）

## 4. 在线检索流程

```mermaid
flowchart TB
    Q[代码特征 + 触发维度 + token 预算] --> R1[① 域路由]
    R1 --> R2[② 域内混合召回]
    R2 --> R3[③ 融合与重排]
    R3 --> R4[④ 上下文组装]
    R4 --> OUT[条款上下文包]
```

### ① 域路由（coarse）

- **规则优先**：特征 → FuncID 映射表（如 `Image` → `04-01` / `05-08`），
  registry 三级功能域即路由表。规则可解释、可人工修正，准确率优先于纯语义
- **语义兜底**：规则未命中时，用 `intent_summary` 的 embedding 与功能域
  description 做相似度分类
- 输出：候选功能域集合（top 3~5），作为下一步的元数据过滤条件
- 路由映射表配置化（YAML），与 dimensions.yaml 同套治理

### ② 域内混合召回

两路并行，均限定在候选功能域内且 `status != 'Deprecated'`：

| 召回路 | 匹配对象 | 说明 |
|---|---|---|
| 关键词路 | `keywords` 数组精确/前缀匹配 + pg_trgm 兜底 | API/组件名命中权重高 |
| 向量路 | `embedding` 余弦相似度（HNSW） | query = intent_summary 或特征拼接文本 |

### ③ 融合与重排

- 两路结果 **RRF**（Reciprocal Rank Fusion）融合
- 可选 **reranker**（cross-encoder）精排 —— 是否引入待消融实验（小规模下
  收益可能有限，见待定项）
- 状态加权：`Baselined` 优先，`Draft` 降权

### ④ 上下文组装

- 按触发维度分配 token 配额，保证每个命中维度至少有条款（避免单一维度
  垄断预算）
- 同一 Feat 相邻条款去重合并
- 每条带 `rule_id`，供评审 Prompt 强制引用与机器校验

### 缓存

`(文件内容 hash, index_version)` → 检索结果缓存。时延大头是 LLM 调用（秒级），
检索本身不是瓶颈，不做过度优化。

## 5. Retriever 接口抽象

```python
class Retriever(Protocol):
    def index(self, chunks: list[Clause], index_version: str) -> None: ...
    def search(self, query: Query, filters: Filters, top_k: int) -> list[ScoredClause]: ...
    def delete(self, rule_ids: list[str]) -> None: ...
    def switch_version(self, index_version: str) -> None: ...
```

- 上层（路由、融合、组装）只依赖此接口，不感知后端实现
- 当前唯一实现：`PgVectorRetriever`
- **契约测试**：一套针对接口语义的测试集，任何后端实现必须全部通过；
  未来若需更换后端（如 Milvus），新实现过契约测试后以**影子对比**方式切换
  （新旧并行、比对 recall 一致性），不做硬切
- 明确否决的方案：按数据量阈值在 SQLite+Faiss / pgvector 间运行时切换的
  双栈方案 —— 双实现、双测试矩阵、高风险迁移点，成本高于收益

## 6. 质量度量与回归

| 机制 | 内容 |
|---|---|
| golden set | 人工标注 30~50 组「代码样例 → 应命中条款」，CI 跑 recall@K 回归；**一切检索调优以此为依据** |
| 同步增长 | 知识沉淀回流的新条款，要求作者附 1 个"应命中案例"进 golden set |
| bad case 回流 | 评审被人工纠错时记录，反哺路由映射表与增强字段 |
| 契约测试 | 保证后端实现符合接口语义 |

## 7. 性能与容量预估

| 规模 | 存储 | 查询时延 | 结论 |
|---|---|---|---|
| 当前（数百条款） | 向量数据 < 5MB | < 10ms | 单 Postgres 容器（2GB 内存）绰绰有余 |
| 1 万条款 | ~40MB（1024 维 × 4B） | < 50ms | HNSW 索引构建秒级，无需调参 |
| 百万条款 | ~4GB | < 100ms | 调 HNSW 参数即可，仍单实例 |

## 8. 决策状态

已定（架构决策，模块对齐的基线）：

- [x] 条款级切块 + 元数据（func_id / feat_id / rule_type / status）
- [x] LLM 离线增强（scenario + keywords）解决词汇鸿沟
- [x] 规则域路由优先、语义分类兜底，路由表配置化
- [x] 关键词 + 向量双路召回 → RRF 融合，状态加权
- [x] Retriever 接口抽象 + pgvector 单后端 + 契约测试
- [x] 增量索引 + 原子版本切换 + 报告记录索引版本
- [x] golden set 回归机制与同步增长规则
- [x] 中文分词规避：keywords 字段 + pg_trgm，不引入中文分词扩展

待定（实现/调优阶段决定）：

| 待定项 | 决策方式 |
|---|---|
| Embedding 模型选型（候选 bge-m3，需确认内网可部署性） | golden set 对比 2~3 个模型 |
| Reranker 取舍与选型 | 消融实验（有无 rerank 的 recall 对比） |
| top-K、token 预算默认值、维度配额策略 | golden set 调参 |
| 条款解析器对历史非统一格式文档的容错细则 | 对真实文档迭代 |
| 离线增强 Prompt 与产出字段 schema | 与沉淀模块 Prompt 一并设计 |
| 路由映射表初版规则集 | 领域专家 + 对知识库现有 28 份 spec 归纳 |
| golden set 首批标注 | 需领域同事投入，建议尽早排期 |
