---
title: 05 知识库构建模块
status: canonical
implementation: designed
updated: 2026-07-10
---

# 05 知识库构建模块

## 1. 模块职责

将外部规范、规则和案例转换为稳定、可引用、可检索、可版本化的知识条款。

```text
原始知识源
-> 条款级解析
-> 稳定知识层
-> 检索标注和 Embedding
-> 版本化索引
```

不负责在线代码检索，也不判断代码是否违反条款。

## 2. 当前状态

当前只有设计文档，没有知识解析器、数据库模型、索引构建脚本或真实索引。

规划中的主要数据源 `arkui-specs` 是外部只读仓库，通过 `KB_PATH` 引用，不复制到本仓库。

历史扫描快照显示 28 份 spec 约有 5300+ 条结构化条款，包含：

```text
AC / R / US / VM / ADR / BR / ER / FR / RC
```

该数字是历史设计输入，正式实施前必须重新扫描验证。

## 3. 核心原则

### 3.1 一条可引用规则一个 Clause

不按固定字符数切整篇文档。优先按规范中稳定编号切分：

```text
R-17
AC-9.2
VM-1
ADR-3
```

无编号内容按 SECTION/PARAGRAPH 降级，但必须保存标题和来源上下文。

### 3.2 原始知识和检索索引分离

```text
稳定知识：条款原文、来源、状态、版本
可重建索引：Tags、Dimensions、关键词、scenario、embedding
```

评审维度变化不能改变条款 source of truth。

### 3.3 Dimensions 是多对多标注

一条定时器规范可能同时属于：

```text
DIM-02 生命周期
DIM-05 健壮性
DIM-06 资源管理
```

不能只保存单个 `dimension`。

### 3.4 所有知识可追溯

进入 Prompt 的条款必须带：

```text
rule_id
source_path
source_anchor
status
index_version
```

## 4. 知识来源与权威度

建议优先级：

1. 部门内部 Baselined 规范。
2. 官方 ArkTS / HarmonyOS 文档。
3. CodeLinter、HomeCheck、ArkAnalyzer 等明确规则。
4. 团队已接受的历史评审案例。
5. 官方或经过审核的示例代码。
6. LLM 生成的通用经验，只能作为 Draft 候选。

权威度是排序特征，不等于自动适用。

## 5. 三层数据模型

### 5.1 稳定条款层 `kb_clauses`

```sql
CREATE TABLE kb_clauses (
    rule_id            TEXT PRIMARY KEY,
    func_id            TEXT,
    feat_id            TEXT,
    rule_type          TEXT NOT NULL,
    status             TEXT NOT NULL,
    authority          TEXT NOT NULL,
    text               TEXT NOT NULL,
    heading_path       TEXT,
    parent_section     TEXT,
    neighbor_rule_ids  TEXT[],
    source_path        TEXT NOT NULL,
    source_anchor      TEXT,
    source_version     TEXT,
    doc_hash           TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL
);
```

### 5.2 检索索引层 `kb_clause_index`

```sql
CREATE TABLE kb_clause_index (
    rule_id             TEXT NOT NULL REFERENCES kb_clauses(rule_id),
    index_version        TEXT NOT NULL,
    func_ids             TEXT[],
    dimension_ids        TEXT[],
    tags                 TEXT[],
    apis                 TEXT[],
    components           TEXT[],
    decorators           TEXT[],
    raw_keywords         TEXT[],
    llm_keywords         TEXT[],
    scenario             TEXT,
    embedding            vector(1024),
    annotation_version   TEXT NOT NULL,
    whitelist_version    TEXT,
    enhancer_version     TEXT,
    embedding_version    TEXT,
    PRIMARY KEY (rule_id, index_version)
);
```

向量维度随最终模型调整，不能把 `1024` 视为已定生产值。

### 5.3 映射审计层

需要追踪标注来源时增加：

```text
clause_concept_mapping
clause_dimension_mapping
```

字段：

```text
rule_id
mapping_type
mapping_value
mapping_source = parser | human | llm
mapping_version
review_status
```

## 6. Clause 字段

| 字段 | 含义 |
|---|---|
| `rule_id` | 全局稳定条款 ID |
| `func_id/feat_id` | 知识功能域和特性目录 |
| `rule_type` | R/AC/US/VM/ADR 等 |
| `status` | Draft/Baselined/Deprecated |
| `authority` | internal/official/tool/team_case/llm_draft |
| `text` | 条款原文 |
| `heading_path` | 文档标题链 |
| `parent_section` | 条款所属小节背景 |
| `neighbor_rule_ids` | 前后关联条款 |
| `source_path/anchor` | 来源文件和行锚点 |
| `doc_hash` | 增量更新判据 |

## 7. 离线构建流程

```text
1. Source Discovery
   读取 registry、spec 和允许的规则源

2. Markdown Parsing
   markdown-it token 流 + 条款状态机

3. Clause Normalization
   生成 rule_id、标题链、上下文和来源锚点

4. Deterministic Enrichment
   提取代码块、反引号标识符、API、组件和装饰器

5. Canonicalization
   使用 Parser 同源 SDK whitelist 统一 API 名称

6. Policy Annotation
   映射 func_ids、tags、dimension_ids，支持人工审核

7. Optional LLM Enrichment
   生成 scenario 和 llm_keywords

8. Embedding
   对 scenario + heading + text 计算向量

9. Validation
   schema、引用、重复、状态和覆盖检查

10. Versioned Write
    写入新 index_version

11. Golden Evaluation
    运行代码样例 -> 应命中条款

12. Atomic Switch
    验证通过后切换 current alias
```

## 8. 确定性关键词

`raw_keywords` 来源：

- Markdown 代码块。
- 反引号标识符。
- API/组件/装饰器正则。
- SDK 白名单匹配。
- registry 中的结构化字段。

确定性关键词权重高于 LLM 生成关键词。

例子：

```text
文档写 createPixelMap
SDK whitelist 映射为 image.createPixelMap
代码 Parser 也输出 image.createPixelMap
```

## 9. LLM 离线增强

目的不是改写规范，而是解决中文规范和英文代码标识符之间的词汇鸿沟。

目标产物：

```json
{
  "scenario": "ArkUI 组件异步创建 PixelMap 并需要处理失败和资源释放",
  "llm_keywords": ["Image", "image.createPixelMap", "onError"]
}
```

约束：

- 原文不可被 LLM 覆盖。
- 产物必须带 `enhancer_version`。
- LLM keywords 不能成为唯一召回来源。
- 内部规范不得发送到未批准公网模型。
- 低质量增强可以删除并重建，不影响稳定条款层。

## 10. Embedding 文本

推荐：

```text
scenario
+ heading_path
+ 条款原文
```

不建议只嵌入 Dimension 名称，也不建议只嵌入脱离背景的单句条款。

## 11. 增量与原子切换

- `doc_hash` 未变化时复用稳定 Clause。
- whitelist、annotation、enhancer 或 embedding 版本变化时只重建索引层。
- 新索引全部写完、通过 Golden Set 后切换 alias。
- 在线请求固定读取一个 `index_version`，不读半成品。
- 旧索引按保留策略延迟清理，支持报告复现。

## 12. 数据质量检查

必须输出：

```text
无法解析文档
重复 rule_id
丢失 source_anchor
未知 rule_type/status
无任何关键词和 scenario 的条款
指向不存在 neighbor 的条款
Dimension/Tag/func_id 未知引用
Embedding 失败项
```

知识构建不能“部分失败后悄悄切换”。

## 13. 配置

环境：

```text
KB_PATH
DATABASE_URL
SDK_WHITELIST_PATH
EMBEDDING_MODEL
EMBEDDING_BASE_URL
LLM_GATEWAY_BASE_URL
LLM_GATEWAY_API_KEY
```

策略：

```text
config/dimensions.yaml
config/routing.yaml
config/retrieval.yaml
```

## 14. 技术栈

| 层 | 技术 |
|---|---|
| Markdown | markdown-it-py + 自研条款状态机 |
| 数据模型 | Pydantic v2 |
| DB | PostgreSQL |
| 向量 | pgvector |
| 模糊检索 | pg_trgm |
| DB 访问 | SQLAlchemy 2.x + psycopg3 |
| YAML | ruamel.yaml |
| Embedding | sentence-transformers 或内网服务 |
| LLM 增强 | 受控 LLM Gateway |
| 测试 | pytest + testcontainers |

## 15. 测试

- 每种 rule_type 的解析 fixture。
- 非统一历史 Markdown 容错。
- rule_id 稳定性和重复检测。
- canonical API 一对多映射。
- 增量重建范围。
- 失败时不切换 alias。
- 数据库迁移和真实 pgvector 契约测试。
- 30~50 组代码 -> 应命中条款 Golden Set。

## 16. 第一版范围

不要一开始全量自动化 5300 条：

```text
选择 3 个高价值域
整理 50~100 条 Baselined Clause
人工确认 Tags/Dimensions/API
先支持关键词精确检索
建立 Golden Set 后再加入 Embedding
```

优先域建议：

```text
状态管理和 ArkTS 语言
定时器/订阅/资源生命周期
async/taskpool/worker
```

