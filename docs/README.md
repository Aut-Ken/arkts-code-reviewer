# ArkTS Code Reviewer 文档中心

本目录是项目架构、模块契约、学习材料和历史设计的统一入口。当前文档基线同时覆盖
主项目代码，以及相邻 `arkts-knowledge`、`arkts-corpora`、`arkts-tools`、
`arkui_ace_engine` 和 `arkts-review-data` 的多仓库工作区。

## 文档规则

1. `architecture/` 和 `modules/` 是当前架构的唯一真相源。
2. 每个模块只保留一份 canonical 文档，同时描述当前实现和目标设计。
3. `learning/` 只用于教学，不作为接口和架构决策依据。
4. `examples/` 用于端到端样例，不替代正式数据契约。
5. `archive/` 是历史快照，只读，不再更新，也不用于判断当前状态。
6. 代码行为与文档冲突时，以代码为当前事实，并立即修正文档的“当前实现”部分。
7. 目标设计发生变化时，更新对应模块文档、跨模块契约和本文索引，不新建第二份“最新架构”。

## 状态定义

| 状态 | 含义 |
|---|---|
| `implemented` | 主路径已有代码并有测试覆盖 |
| `partial` | 已有可运行初版，但契约、边界或验证尚未稳定 |
| `designed` | 架构与契约已对齐，尚未编码 |
| `planned` | 只有方向，关键契约仍待确定 |

## 推荐阅读顺序

### 快速了解项目

1. [整体架构](architecture/overview.md)
2. [多仓库工作区与知识来源架构](architecture/workspace-and-sources.md)
3. [跨模块数据契约](architecture/data-contracts.md)
4. [配置与版本规范](architecture/configuration.md)

### 新开发会话基线

新建 Chat 或交接开发任务时，先让执行者阅读上面四份文档，再阅读本次要实现的模块
文档。不要以 `archive/`、聊天记录或外部仓库 README 代替 canonical 文档。

截至 2026-07-11 的可验证基线：

```text
主项目测试（npm ci 后）     30 passed，20 subtests passed
Python-only checkout         26 passed, 4 skipped（L1 可选测试）
Parser Golden               12 个自包含人工标注样本，L0/L1 完整逐 case baseline
arkui_ace_engine L0 批测    63/63 成功，0 missing，0 crash
arkui_ace_engine L1 批测    63/63 为 L1；7 文件有 ERROR、7 文件有 missing warning
来源登记                    19 项：11 knowledge + 4 corpus + 4 tool
知识构建/在线检索/正式评审   尚未实现运行闭环
```

快速复核命令：

```bash
PYTHONPATH=src python -m pytest -q -rs
PYTHONPATH=src python tools/evaluate_parser_golden.py \
  --parser lexical \
  --baseline tests/golden/parser/baselines/lexical.json \
  --require-layer L0
(cd sidecars/arkts-parser && npm ci)
PYTHONPATH=src python tools/evaluate_parser_golden.py \
  --parser arkts-tree-sitter \
  --baseline tests/golden/parser/baselines/arkts-tree-sitter-merged.json \
  --require-layer L1
PYTHONPATH=src python tools/run_arkts_parser_batch.py --parser lexical --require-layer L0
```

### 按流水线阅读模块

| 顺序 | 模块 | 当前状态 | 文档 |
|---|---|---|---|
| 01 | 输入与编排 | `partial` | [01-input-orchestration.md](modules/01-input-orchestration.md) |
| 02 | Parser 与代码事实 | `partial` | [02-parser.md](modules/02-parser.md) |
| 03 | ReviewUnit 上下文规划 | `partial` | [03-review-unit.md](modules/03-review-unit.md) |
| 04 | Tags 与评审维度 | `partial` | [04-feature-routing.md](modules/04-feature-routing.md) |
| 05 | 知识库构建 | `partial` | [05-knowledge-base.md](modules/05-knowledge-base.md) |
| 06 | Retrieval 检索 | `designed` | [06-retrieval.md](modules/06-retrieval.md) |
| 07 | Deterministic Rules | `designed` | [07-rules.md](modules/07-rules.md) |
| 08 | Prompt 与 Final LLM | `designed` | [08-prompt-review.md](modules/08-prompt-review.md) |
| 09 | 输出与 GitCode 集成 | `planned` | [09-output-integration.md](modules/09-output-integration.md) |
| 10 | 评测与反馈闭环 | `designed` | [10-evaluation-feedback.md](modules/10-evaluation-feedback.md) |
| 11 | Parser Validation 旁路 | `partial` | [11-parser-validation.md](modules/11-parser-validation.md) |

## 学习材料

| 文档 | 适用读者 |
|---|---|
| [ArkTS 入门、Parser 字段与 Tags 详解](learning/arkts-parser-fields-tags.md) | 没有 ArkTS/ArkUI 基础，需要理解 Parser 输出的读者 |

## 示例

| 文档 | 内容 |
|---|---|
| [PhotoWall 端到端数据流](examples/photowall-end-to-end.md) | 一段 ArkTS 代码如何经过 Parser、ReviewUnit、Retrieval 和 Final LLM |

## 历史归档

`archive/2026-07-09/` 保存本次整理前的全部架构草案、会议问题、实现详解和模拟输出。
归档文件中的接口、模块状态和结论可能已经失效。

## 文档更新检查表

修改模块代码或契约时，至少检查：

- 对应 `modules/NN-*.md` 的当前实现和目标设计是否仍准确。
- `architecture/data-contracts.md` 是否需要同步。
- `architecture/configuration.md` 是否增加了新配置或环境变量。
- `architecture/workspace-and-sources.md` 和外部 `sources.yaml` 是否需要同步。
- `architecture/overview.md` 的模块状态是否需要更新。
- 是否需要新增或更新 Golden Set、示例和迁移说明。

外部来源变化时还必须记录新的不可变 commit，并确认 ingestion allowlist、权威度和
Prompt 使用边界没有被上游内容改变。
