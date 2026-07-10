---
title: 配置与版本规范
status: canonical
updated: 2026-07-10
---

# 配置与版本规范

## 1. 配置原则

1. 业务策略使用版本化 YAML，进入 Git。
2. 路径、端点和凭据使用环境变量，不硬编码机器信息。
3. Secret 不进入 YAML、日志、报告和测试 fixture。
4. 每次评审记录实际生效的配置版本。
5. 配置必须在服务启动和 CI 中做 schema 及交叉引用校验。
6. 当前未实现的配置在本文标记为“目标”，不能假装运行时已经读取。

## 2. 目标配置目录

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

当前仓库尚未创建这些运行时配置；Tags 和 Dimensions 仍硬编码在 `tagger.py`。

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

## 3. 配置优先级

```text
显式函数参数
> 环境变量
> 项目 YAML
> 代码默认值
```

评审策略字段不允许通过普通环境变量任意覆盖；生产变更必须走 Git 评审和版本升级。

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

## 6. tags.yaml

目标结构：

```yaml
version: tags-v1

tags:
  - id: has_timer
    status: Active
    description: 代码使用定时器 API
    triggers:
      any_api:
        - setInterval
        - setTimeout
        - systemTimer.setInterval

  - id: has_image
    status: Active
    description: 代码使用图片组件或 image API
    triggers:
      any_component:
        - Image
        - ImageSpan
        - ImageAnimator
      any_api_prefix:
        - image.
```

Tag 是代码场景，不包含 severity 和最终问题描述。

## 7. dimensions.yaml

```yaml
version: dimensions-v1

dimensions:
  - id: DIM-06
    title: 资源与内存管理
    status: Active
    always_check: false
    retrieval_policy: signal_required
    triggers:
      any_tag:
        - has_image
        - has_timer
        - has_subscription
        - has_media
        - has_file_io
    retrieval:
      func_scopes:
        - resource-management
      top_k: 5
      token_weight: 1.5
    prompt_checks:
      - 检查资源是否重复创建
      - 检查资源是否在适当生命周期释放
    severity_weight: high
```

`retrieval_policy`：

```text
signal_required  有具体 Facts/Tags 才检索
always           始终检索，原则上不建议用于抽象维度
disabled         不参与知识检索，只进入 Prompt 检查项
```

## 8. routing.yaml

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

## 9. retrieval.yaml

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

## 10. rules.yaml

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

## 11. reviewer.yaml

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

## 12. output.yaml

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

## 13. evaluation.yaml

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

配置 CI 至少检查：

- ID 全局唯一。
- Dimension 引用的 Tag 存在且 Active。
- Rule 引用的 Dimension 和知识 `rule_id` 存在。
- Routing 引用的 func_id 存在。
- Deprecated 项不会被 Active 配置强依赖。
- 配置版本变更与内容变更一致。
- YAML 能通过 Pydantic schema 验证。
- SourceRecord 的本地 Git remote、branch、revision 与登记一致。
- ingestion include/exclude 不越过来源仓库根目录。
- `raw_prompt_use_allowed` 和 `execute_repository_scripts` 不得被无审核开启。
