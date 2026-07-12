---
title: 配置与版本规范
status: canonical
updated: 2026-07-12
---

# 配置与版本规范

## 1. 配置原则

1. 业务策略使用版本化 YAML，进入 Git。
2. 路径、端点和凭据使用环境变量，不硬编码机器信息。
3. Secret 不进入 YAML、日志、报告和测试 fixture。
4. 每次评审记录实际生效的配置版本。
5. 配置必须在服务启动和 CI 中做 schema 及交叉引用校验。
6. 当前未实现的配置在本文标记为“目标”，不能假装运行时已经读取。

## 2. 配置目录与当前状态

```text
config/
├── tags.yaml
├── dimensions.yaml
├── routing.yaml
├── retrieval.yaml
├── rules.yaml
├── reviewer.yaml
├── output.yaml
└── evaluation.yaml
```

当前已实现并由生产 FeatureRouter 读取：

```text
config/tags.yaml          tag-config-v1 / tags-v1
config/dimensions.yaml    dimension-config-v1 / dimensions-v1
```

`routing.yaml` 及其后的 Retrieval、Rules、Reviewer、Output、Evaluation 配置仍是目标设计，
当前仓库没有对应运行时 loader。`tagger.py` 只保留调用配置引擎的兼容函数，不再维护第二份
硬编码 Tag/Dimension 语义。

外部仓库不写进这个 `config/`。它们由
`/home/autken/Code/arkts-knowledge/registry/sources.yaml` 登记，具体契约见
[多仓库工作区与知识来源架构](workspace-and-sources.md)。

目标 Prompt 资产目录：

```text
prompts/
├── review/system.md
├── review/user-template.md
├── review/output-schema.json
└── parser-validation/        # 与正式评审 Prompt 分开版本化
```

## 3. 配置选择与优先级

```text
显式传入 FeatureConfig
> wheel 内 packaged defaults
> source checkout 的项目 YAML
```

当前 `FeatureRouter(config=...)` 支持显式注入已校验 `FeatureConfig`，便于单独测试和影子运行；
`CodeAnalyzer` v1 仍固定使用默认配置，并用同一默认配置重放结果，尚未提供生产运行时切换入口。
默认 loader 在安装环境优先读取 packaged defaults，在源码 checkout 读取仓库 `config/`。
Tags/Dimensions 语义不支持环境变量逐字段覆盖；生产变更必须走 Git 评审和版本升级。

## 4. 当前已使用环境变量

### Parser

| 变量 | 默认值 | 用途 |
|---|---|---|
| `ARKTS_PARSER_NODE` | `node` | Node 可执行文件 |
| `ARKTS_PARSER_TIMEOUT` | `20` | sidecar 超时秒数 |
| `ARKTS_PARSER_SIDECAR` | 仓库默认路径 | `parse_arkts.js` 路径 |

### Parser Validation GLM

| 变量 | 默认值 | 用途 |
|---|---|---|
| `GLM_API_KEY` | 无 | API Key |
| `GLM_BASE_URL` | 公网 GLM 地址 | 质检端点 |
| `GLM_MODEL` | `glm-5.2` | 模型 |
| `GLM_MAX_TOKENS` | `1200` | 最大输出 token |
| `GLM_THINKING_TYPE` | `disabled` | thinking 配置 |
| `GLM_RESPONSE_FORMAT` | `json_object` | 输出格式 |
| `GLM_RETRY_ATTEMPTS` | `4` | 重试次数 |
| `GLM_RETRY_BASE_DELAY_SECONDS` | `20` | 退避基数 |
| `GLM_RAW_RESPONSE_DIR` | 无 | 原始响应目录 |

Parser Validation 的公网默认端点不得用于未获批准的内部代码。

## 5. 目标环境变量

| 变量 | 模块 | 用途 |
|---|---|---|
| `ARKTS_SOURCE_REGISTRY` | Knowledge/Evaluation | `sources.yaml` 路径 |
| `ARKTS_REVIEW_DATA_ROOT` | 全局 | 构建、缓存、报告和评测产物根目录 |
| `DATABASE_URL` | Retrieval/Evaluation | PostgreSQL 连接串 |
| `SDK_WHITELIST_PATH` | Parser/KB | SDK 白名单产物 |
| `CURRENT_INDEX_ALIAS` | Retrieval | 当前索引别名 |
| `EMBEDDING_MODEL` | KB/Retrieval | Embedding 模型 ID |
| `EMBEDDING_BASE_URL` | KB/Retrieval | 内网 Embedding 服务 |
| `LLM_GATEWAY_BASE_URL` | Review/KB | 内网 LLM Gateway |
| `LLM_GATEWAY_API_KEY` | Review/KB | Gateway 凭据 |
| `GITCODE_BASE_URL` | Input/Output | GitCode API 地址 |
| `GITCODE_TOKEN` | Input/Output | Bot Token |

### 5.1 当前来源路径覆盖机制

`sources.yaml` 的 19 项来源都保存 `local_path` 和 `env_override`。例如：

```yaml
id: arkui-specs
local_path: /home/autken/Code/arkts-knowledge/sources/internal-specs/arkui-specs
env_override: ARKUI_SPECS_PATH
```

当前只是登记数据，主项目尚未读取这些变量。目标 loader 的解析顺序：

```text
SourceRecord.env_override 对应环境变量
> SourceRecord.local_path
```

来源路径变量包括：

```text
知识：ARKUI_SPECS_PATH、OPENHARMONY_DOCS_PATH、INTERFACE_SDK_JS_PATH、
      ARKCOMPILER_RUNTIME_DOCS_PATH、OPENHARMONY_SECURITY_PATH 等
语料：ARKUI_ENGINE_PATH、XTS_ACTS_PATH、OPENHARMONY_APP_SAMPLES_PATH、
      OPENHARMONY_CODELABS_PATH
工具：ARKCOMPILER_ETS_FRONTEND_PATH、ACE_ETS2BUNDLE_PATH、
      ARKANALYZER_PATH、DEVECO_CLI_PATH
```

完整变量名以 `sources.yaml` 为准，业务代码不能自行维护第二份来源路径清单。

## 6. 已实现的 tags.yaml

当前结构：

```yaml
schema_version: tag-config-v1
version: tags-v1

tags:
  - id: has_timer
    status: Active
    description: 代码创建或清理定时器
    triggers:
      any_api: [clearInterval, clearTimeout, setInterval, setTimeout, systemTimer.setInterval]

  - id: has_image
    status: Active
    description: 代码使用图片组件或 image API
    triggers:
      any_component: [Image, ImageAnimator, ImageSpan]
      any_api_prefix: [image.]
```

支持的 trigger operator 当前冻结为：

```text
any_component
any_api
any_api_prefix
any_api_suffix
any_decorator
any_attribute
any_symbol
any_syntax
has_resource_reference
```

Tag 是代码场景，不包含 severity 和最终问题描述。`has_resource_reference` 直接消费结构化
resource occurrence，不需要把资源引用伪装为 `$r` API。全部触发数组必须排序去重；空规则、
未知 operator、重复 YAML key 和未知字段都会失败。

## 7. 已实现的 dimensions.yaml

```yaml
schema_version: dimension-config-v1
version: dimensions-v1

review_questions:
  - id: RQ-correctness
    title: 改动是否正确且不会引入直接回归
    status: Active
    always_bind: true
    triggers: {}

dimensions:
  - id: DIM-06
    title: 资源与内存管理
    status: Active
    always_check: false
    retrieval_policy: signal_required
    triggers:
      any_tag:
        - has_file_io
        - has_image
        - has_media
        - has_subscription
        - has_timer
```

当前 Feature Routing 配置只拥有适用性：Tag 触发、`always_check`、`retrieval_policy` 和 Review
Question binding。知识 `func_scope/top_k/token_weight`、Prompt 文本和 severity 属于下游模块，
尚未由这份配置执行，也不能伪装成已实现字段。

`retrieval_policy`：

```text
signal_required  有具体 Facts/Tags 才检索
always           始终检索，原则上不建议用于抽象维度
disabled         不参与知识检索，只进入 Prompt 检查项
```

实际执行还区分：

```text
review_enabled      always_check 或有 exact Tag
retrieval_enabled   policy 允许且有 exact Tag（或 policy=always）
routing_enabled     policy 允许且有 exact/file-hint Tag（或 policy=always）
```

hint-only signal 只能扩大 `routing_dimensions`，不能进入正式 `retrieval_dimensions`。当前
DIM-02/03/04/05/12 都是 `always_check=true + signal_required`，但 trigger 列表为空，因此会进入
评审方向，却不会启动知识检索。DIM-01 是 `always_check=true + disabled`。

`dimensions.yaml` 还冻结 12 个 Review Questions。`RQ-correctness` 始终绑定，其余问题只由
`unit_exact` Active Tags 触发；file hint 不绑定专项问题。

### 7.1 状态治理

Tag、Dimension 和 Review Question 都支持：

```text
Active      正式生效
Draft       只进入 shadow 输出
Deprecated  运行时不匹配、不输出
```

Active Dimension/Question 不得引用 Draft 或 Deprecated Tag。历史 ID 不得删除后复用；迁移应先
Deprecated，并通过新配置版本完成。

### 7.2 组合 fingerprint 与 wheel

Loader 对按 ID 排序规范化后的两份配置计算：

```text
feature-config:sha256:<digest>
```

`FeatureRoutingResult` 和每个 `UnitFeatureProfile` 同时记录该 fingerprint、`tags-v1` 和
`dimensions-v1`。声明顺序变化不改变语义 fingerprint，内容或声明版本变化会改变它。

`pyproject.toml` 使用 wheel `force-include` 将仓库两份 YAML 安装为：

```text
arkts_code_reviewer/feature_routing/defaults/tags.yaml
arkts_code_reviewer/feature_routing/defaults/dimensions.yaml
```

安装环境优先读取 packaged defaults；这避免 wheel 离开仓库根目录后退回另一套隐式规则。

## 8. routing.yaml（目标，未实现）

```yaml
version: routing-v1

routes:
  - id: timer-resource
    match:
      any_api: [setInterval, clearInterval]
      any_tag: [has_timer]
    func_ids:
      - resource-management
      - component-lifecycle

  - id: image-loading
    match:
      any_component: [Image, ImageAnimator, ImageSpan]
      any_api_prefix: [image.]
    func_ids:
      - image-loading
```

## 9. retrieval.yaml（目标，未实现）

```yaml
version: retrieval-v1

batch:
  max_units: 50

keyword:
  top_k: 20

vector:
  enabled: true
  top_k: 20
  model: candidate_embedding_model

fusion:
  method: rrf
  rrf_k: 60

ranking:
  status_weights:
    Baselined: 1.2
    Draft: 0.7
    Deprecated: 0.0

assembly:
  max_clauses_per_unit: 8
  total_token_budget: 8000
  include_parent_context: true
```

上述数值是初始候选，必须通过 Golden Set 调优后才能成为生产默认值。

## 10. rules.yaml（目标，未实现）

```yaml
version: rules-v1

rules:
  - id: ARKTS-NO-ANY
    status: Active
    severity: high
    dimensions: [DIM-01, DIM-02]
    reference_rule_ids: [LANGUAGE/TYPE/R-01]
    implementation: no_any_type
    applies_to: [full, diff]
```

YAML 保存元数据和治理状态，复杂检测逻辑由 Python 实现并通过注册表引用。

## 11. reviewer.yaml（目标，未实现）

```yaml
version: reviewer-v1

model:
  name: approved-review-model
  temperature: 0
  max_output_tokens: 4000

prompt:
  version: review-v1
  max_context_rounds: 1
  require_reference_for: [critical, high]
  allow_unreferenced_suggestion: true

limits:
  max_findings_per_unit: 5
  max_findings_per_mr: 10
```

## 12. output.yaml（目标，未实现）

```yaml
version: output-v1

gitcode:
  inline_comments: true
  summary_comment: true
  update_strategy: replace_bot_comments

filtering:
  include_severities: [critical, high, medium, low, suggestion]
  require_diff_related_for_inline: true
  minimum_confidence: medium
```

## 13. evaluation.yaml（目标，未实现）

```yaml
version: evaluation-v1

retrieval:
  metrics: [recall_at_5, precision_at_5, mrr]

review:
  metrics: [accepted_rate, false_positive_rate, findings_per_mr]

gates:
  parser_real_sample_required: true
  retrieval_recall_at_5_min: 0.90
```

阈值必须由真实数据确定，示例中的 `0.90` 不是当前验收结论。

## 14. 配置版本记录

每份 `ReviewReport` 保存：

```json
{
  "versions": {
    "tags": "tags-v1",
    "dimensions": "dimensions-v1",
    "feature_config": "feature-config:sha256:...",
    "routing": "routing-v1",
    "source_bundle": "src-bundle-sha256",
    "retrieval": "retrieval-v1",
    "rules": "rules-v1",
    "reviewer": "reviewer-v1",
    "output": "output-v1",
    "index": "idx-2026-07-10-001"
  }
}
```

## 15. CI 校验

Feature Routing 当前 loader、测试和发布门禁已经检查：

- YAML duplicate key、未知字段和未知 enum fail-closed。
- Tag/Dimension/Question ID 唯一且格式正确。
- trigger 数组去重、排序并使用已登记 operator。
- Dimension/Question 引用的 Tag 存在。
- Active Dimension/Question 不依赖非 Active Tag。
- 配置 fingerprint、结果 identity、输出顺序和 replay 可复现。
- `tools/check_feature_routing_package.py` 构建并解包 wheel，再用隔离导入验证 packaged defaults。

完整系统 CI 后续还至少检查：

- Rule 引用的 Dimension 和知识 `rule_id` 存在。
- Routing 引用的 func_id 存在。
- 配置版本变更与内容变更一致。
- SourceRecord 的本地 Git remote、branch、revision 与登记一致。
- ingestion include/exclude 不越过来源仓库根目录。
- `raw_prompt_use_allowed` 和 `execute_repository_scripts` 不得被无审核开启。
