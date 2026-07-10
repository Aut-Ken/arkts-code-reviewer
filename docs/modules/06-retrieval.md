---
title: 06 Retrieval 检索模块
status: canonical
implementation: designed
updated: 2026-07-10
---

# 06 Retrieval 检索模块

## 1. 模块职责

接收 Unit 级代码特征，从版本化知识索引返回最相关、可引用、可调试的 Evidence Pack。

```text
Unit Query
-> 路由
-> 精确/语义召回
-> 融合和重排
-> token 预算组装
-> Evidence Pack
```

不解析代码、不判断代码好坏、不生成最终修改建议。

## 2. 当前状态

无运行时代码。当前 `models.py` 只提供初版 `RetrievalQuery` 数据结构。

外部 19 项来源已经登记，但 Retrieval 不读取原始 clone；它只读取 Knowledge Build 发布的
稳定 Clause、API catalog 和一个固定 `index_version/source_bundle_id`。当前还没有任何
已发布索引。

本文件是实现基线，替代归档中的旧 Retrieval 草案。

## 3. 输入契约

每个 Unit 独立提供：

```jsonc
{
  "unit_id": "PhotoWall.ets@method:PhotoWall.loadImages:L14-L20",
  "target_platform": {"release": "OpenHarmony-5.x", "api_level": 12},
  "code_features": {
    "components": [],
    "apis": ["setInterval", "router.pushUrl"],
    "decorators": [],
    "attributes": [],
    "syntax": ["async_fn", "arrow_fn"],
    "tags": ["has_timer", "has_async", "has_navigation"]
  },
  "dimensions": ["DIM-05", "DIM-06", "DIM-07"],
  "host_context": {
    "struct": "PhotoWall",
    "lifecycle": ["aboutToDisappear"],
    "imports": ["@ohos.router"]
  },
  "intent_summary": "组件异步加载数据并创建周期定时器",
  "parser_quality": {
    "layer": "L1",
    "context_degraded": false
  }
}
```

批量请求目标上限 50 Unit，实际值由压测和服务预算确定。

## 4. Dimensions 在检索中的作用

Dimensions 不是唯一查询键，主要用于：

```text
选择 dimension-specific 查询策略
对相关条款加权
控制各检查方向的 Evidence 覆盖
分配 token budget
标记 uncovered dimension
```

不允许仅用 `DIM-04 可维护性` 查询整个知识库。

`always_check=true` 只代表 Prompt 始终检查；`retrieval_policy=signal_required` 时没有信号不检索文档。

## 5. 在线架构

```text
Batch RetrievalQuery
        |
        v
Query Planner
为每个 Unit 构造结构化和语义查询
        |
        v
Domain Router
规则路由优先，语义路由兜底
        |
        +--------------------+
        |                    |
        v                    v
Exact Retriever        Vector Retriever
API catalog/组件/Tag    curated clause scenario embedding
        |                    |
        +---------+----------+
                  |
                  v
              RRF Fusion
                  |
                  v
       Applicability / Authority Rerank
                  |
                  v
          Evidence Assembler
      Unit 分组、去重、上下文还原、预算裁剪
                  |
                  v
             Evidence Pack
```

## 6. Query Planner

生成两类查询：

### 结构化查询

```text
apis
components
decorators
attributes
tags
func_ids
dimension_ids
status filters
```

### 语义查询

使用确定性模板优先：

```text
ArkUI PhotoWall 组件的方法中使用 setInterval 和 async/await，
宿主存在 aboutToDisappear，需要查找定时器生命周期和异步错误处理规范。
```

只有必要时使用 LLM 生成 `intent_summary`，并记录 provenance 和版本。

## 7. Domain Router

规则路由示例：

```text
setInterval / has_timer
-> resource-management, component-lifecycle

Image / image.*
-> image-loading

@State / @Link
-> state-management
```

规则未命中时，以 Unit intent embedding 与功能域描述做 top-N 语义路由。

功能域是“知识讲什么”，Dimension 是“从什么角度评审”，不能混为同一个分类。

功能域和路由配置属于主项目版本化策略，不能从某个 Skill 的目录名动态生成。

## 8. 精确召回

优先匹配：

```text
canonical API
组件名
装饰器
Tag
raw_keywords
规则 ID
```

canonical API 来自 `interface-sdk-js` 构建的 `ApiSymbolCatalog`，并与 Parser 共用版本。
如果请求带 API level，候选 Clause 和 API 必须先通过版本适用性过滤。

数据库：

```text
GIN 数组索引
pg_trgm 模糊兜底
status/func_id 元数据过滤
```

精确命中可解释，应该在排序中高于 LLM 关键词。

## 9. Embedding 召回

适合：

```text
组件拆分
可维护性
错误处理
多设备适配
没有共同标识符的语义场景
```

不适合单独处理：

```text
明确 API 规则
精确禁用语法
只嵌入 Dimension 标题
```

query embedding 使用 Unit 场景摘要，clause embedding 使用：

```text
scenario + heading_path + text
```

## 10. 融合与重排

第一版使用 RRF，避免直接混合不可比较的关键词分数和向量分数：

```text
score = sum(1 / (rrf_k + rank))
```

后处理信号：

```text
Baselined > Draft > Deprecated
适用版本的内部规范/feature spec/语言规格/官方文档权威度
API exact match
Tag match
Dimension overlap
Parser/context quality
```

Reranker 不是第一版必需项。只有 Golden Set 显示融合后排序仍明显不足时才加入。

## 11. Evidence 组装

目标：返回足够判断的最少条款，而不是文档越多越好。

步骤：

1. 按 `unit_id` 分组。
2. 同一 rule 去重。
3. 相邻条款按 `neighbor_rule_ids` 合并。
4. 条款脱离背景时补 `parent_section`。
5. 每个有真实信号的 Dimension 保留必要覆盖。
6. 按 authority、相关性和 token 裁剪。
7. 保存未覆盖维度和检索 trace。

## 12. 输出 EvidencePack

```jsonc
{
  "index_version": "idx-2026-07-10-001",
  "source_bundle_id": "sha256:...",
  "units": [
    {
      "unit_id": "...",
      "clauses": [
        {
          "rule_id": "RESOURCE/TIMER/R-01",
          "dimension_ids": ["DIM-05", "DIM-06"],
          "text": "组件创建的定时器应在不再使用时主动清理。",
          "status": "Baselined",
          "source_ref": {
            "source_id": "arkui-specs",
            "revision": "98bbe6578e0f...",
            "relative_path": "timer/Feat-01-spec.md",
            "anchor": "L40-L47",
            "authority": "feature_spec"
          },
          "matched_by": ["api:setInterval", "tag:has_timer"],
          "match_reason": "...",
          "score": 0.91,
          "rank_detail": {}
        }
      ],
      "uncovered_dimensions": []
    }
  ]
}
```

进入 Prompt：

```text
rule_id / text / status / source label / dimension_ids
```

只进入审计记录：

```text
matched_by / match_reason / score / rank_detail / source_ref.anchor
```

`source_id/revision/rule_id` 必须进入 Prompt allowlist 和 ReviewReport 审计信息。原始
Markdown、Skill、XTS 或工具源码不能在在线请求时临时加入 Evidence Pack。

## 13. Retriever 接口

```python
class Retriever(Protocol):
    def index(self, clauses, index_version): ...
    def search(self, query, filters, top_k): ...
    def delete(self, rule_ids): ...
    def switch_version(self, index_version): ...
```

Query Planner、Fusion 和 Assembler 不依赖具体后端。

第一版唯一后端：PostgreSQL + pgvector，不维护 SQLite/Faiss 双栈。

## 14. 缓存

```text
cache key =
hash(UnitQuery)
+ index_version
+ routing_version
+ retrieval_version
+ embedding_version
```

缓存保存 Unit 级候选和最终 Evidence，索引切换后自然失效。

## 15. 无结果和降级

| 场景 | 行为 |
|---|---|
| 精确无结果、向量有结果 | 返回向量结果并标明 matched_by |
| 两路都无结果 | 返回空 clauses 和 uncovered_dimensions |
| Embedding 服务失败 | 精确检索继续，记录 degraded |
| DB 不可用 | Retrieval 失败；不得伪造 Evidence |
| Parser degraded | 可检索，但降低强结论权限和排序置信度 |

无 Evidence 时，Final LLM 只能给受限 suggestion，不能声称违反具体规范。

## 16. 配置

见 [配置与版本规范](../architecture/configuration.md)：

```text
config/routing.yaml
config/retrieval.yaml
config/dimensions.yaml
```

环境：

```text
DATABASE_URL
CURRENT_INDEX_ALIAS
EMBEDDING_MODEL
EMBEDDING_BASE_URL
```

## 17. 技术栈

| 层 | 技术 |
|---|---|
| 主体 | Python 3.12 |
| 模型 | Pydantic v2 |
| DB | PostgreSQL |
| 精确/模糊 | GIN + pg_trgm |
| 向量 | pgvector HNSW |
| DB 访问 | SQLAlchemy 2.x + psycopg3 |
| Embedding | sentence-transformers 或内网服务 |
| 配置 | ruamel.yaml + pydantic-settings |
| 测试 | pytest + testcontainers |

## 18. 质量指标

```text
Recall@5
Precision@5
MRR
empty result rate
wrong domain route rate
irrelevant clauses per Unit
Dimension evidence coverage
p50/p95 latency
```

Golden Set 形式：

```text
ReviewUnit Query
-> 应命中的 rule_id 集合
-> 可接受但非必需 rule_id
-> 明确不应命中的 rule_id
```

## 19. 实现顺序

```text
1. 固定 `SourceRef/SourceBundle/Clause/ApiSymbolCatalog` 契约
2. 读取 Knowledge Build 发布的 50~100 条人工确认 Clause
3. Pydantic Query/Evidence 契约和 API level 过滤
4. API/组件/Tag/规则 ID 精确检索
5. 30~50 个 Golden Case 和指标
6. scenario + Embedding
7. RRF 融合
8. 数据证明需要时再上 Reranker
9. 缓存、增量索引和服务化
```
