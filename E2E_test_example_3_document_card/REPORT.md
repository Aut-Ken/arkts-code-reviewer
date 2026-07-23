# TaskPool / Worker DeepSeek Document Card Smoke

> 固定 OpenHarmony 官方 Markdown，在精确 Plan、Body 和单次预算授权下调用
> DeepSeek V4 Pro，生成仅用于文档导航的 Document Card。

## 1. 汇报结论

- 执行合同：**PASS**。
- Provider 状态：**valid_card**，1 次尝试、0 次重试。
- 结构覆盖：**5/5 sections**。
- 原始响应链：**PASS**，Raw Response → Draft → Card → Receipt 可从固定原文完整重建。
- 内容质量：**NOT QUALIFIED**。
- 已观察问题：`important_apis=[]`，但原文明确包含 `@Concurrent`、`ArrayBuffer`、`SharedArrayBuffer`、`Sendable`、`Promise`、`async/await`、`onmessage` 等 API/类/装饰器样式词，
  说明 API 导航提示存在漏召回。

`valid_card` 只表示模型输出满足严格结构和来源绑定合同，不表示摘要、主题或 API 提示已经达到生产质量。

## 2. 固定输入

- Source：`openharmony-docs`
- Revision：`c8f5fb6c2fe03cf66b8a41c196ad7fc5e7891c47`
- Path：`zh-cn/application-dev/arkts-utils/taskpool-vs-worker.md`
- Plan：`document-card-plan:sha256:c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768`
- Body：`sha256:a4bcc046b64f2eedc98b8d97cf3cc42636ab230dc242feb57f528cbe255fb13d`
- Model：`deepseek-v4-pro`
- 最大输出：4096 tokens
- 重试策略：不重试

## 3. 实际链路

```text
Pinned Git Markdown
  → NormalizedDocument
  → MarkdownDocumentMap（5 sections）
  → content-addressed Request / DispatchPlan
  → DeepSeek V4 Pro（1 attempt）
  → strict Raw Response reducer
  → DocumentCardDraft
  → DocumentCard
  → Receipt
  → DocumentCatalog（offline, 1 entry）
```

这条链生成了一份单文档导航 Catalog，但没有执行 Catalog 路由、Clause Retrieval、
EvidencePack、Finding 或 GitCode 发布。

## 4. 运行结果

- 延迟：7829 ms。
- Token：输入 2566、输出 557、合计 3123 tokens。
- Card ID：`document-card:sha256:5a321b5fbacf1db8bd4ce941fb631ad8be8e4174182cc913941ba3008c1a16b5`。
- Catalog ID：`document-catalog:sha256:c3aaff27622a50fd271bf49b00bd5ce5613c11c939ca6ac13cba7dac32b9e5b5`（1 entry）。
- 摘要：本文对比了ArkTS中TaskPool和Worker两种多线程方案的实现特点与适用场景，帮助开发者根据任务特性选择合适的并发模型。
- 主题：`TaskPool`、`Worker`、`任务调度`、`多线程`、`并发`、`生命周期管理`
- API 导航提示：—
- `use_scope`：`navigation_only_not_evidence`
- `evidence_eligible`：`false`

## 5. 章节导航摘要

| 序号 | Section ID | 摘要 |
|---:|---|---|
| 1 | `document-section:sha256:cf85b5b7503163332111c4fc79910a5dd391107bc65e11febcc67923a9573289` | 概述TaskPool和Worker的作用，并说明本文将从实现特点和适用场景两方面进行对比。 |
| 2 | `document-section:sha256:ae910434e655a89120dbeee4c0b9b35b9776a04bf8759230311a56e1a9a28f65` | 以表格形式对比TaskPool和Worker在内存模型、参数传递、方法调用、生命周期、任务管理功能等方面的实现差异。 |
| 3 | `document-section:sha256:8e8cfe38be3c91b20b666f1fa7e0a2c50d5eeb5fd81c7ac09411d245af906305` | 总体说明TaskPool和Worker的适用倾向，指出TaskPool适合独立任务且自动管理，Worker适合需长时间运行或手动管理线程的场景。 |
| 4 | `document-section:sha256:b605cfb2ea785e648c0aa7415805666d36c16371fa1b914631a3617367a3b696` | 列举建议使用Worker的具体场景，包括运行时间超过3分钟的任务和有强关联的同步任务。 |
| 5 | `document-section:sha256:a96fd0b8fbd5e2ae14de3e2d0a1d50f4fd26c6f900dae421e0ded05f74e12153` | 列举建议使用TaskPool的具体场景，包括需设置优先级、频繁取消、大量或调度点分散的任务。 |

## 6. 机器产物

| 阶段 | 文件 |
|---|---|
| Source manifest | [00_source-manifest.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/00_source-manifest.json) |
| Normalized Markdown | [01_source.md](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/01_source.md) |
| Static document map | [02_document-map.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/02_document-map.json) |
| Document Card request | [03_request.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/03_request.json) |
| Exact dispatch plan | [04_dispatch-plan.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/04_dispatch-plan.json) |
| Offline inspection | [05_inspection.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/05_inspection.json) |
| Raw provider response | [06_provider-response.raw.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/06_provider-response.raw.json) |
| Document Card draft | [07_document-card-draft.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/07_document-card-draft.json) |
| Document Card | [08_document-card.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/08_document-card.json) |
| Live receipt | [09_receipt.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/09_receipt.json) |
| Contract assertions | [10_assertions.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/10_assertions.json) |
| Summary | [11_summary.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/11_summary.json) |
| One-document navigation catalog | [12_document-catalog.json](artifacts/c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768/12_document-catalog.json) |

## 7. 离线复核

```bash
cd /home/autken/Code/arkts-code-reviewer
uv run python E2E_test_example_3_document_card/run_e2e.py
```

该命令只读取固定 Git 对象和仓库内 00～09 产物，重新验证完整链，并重建断言、摘要、
单条目 Catalog 与本报告。
它不会读取 `.env`、API Key，也不会调用 DeepSeek。

## 8. 准确边界

本样例证明了：单篇固定 Markdown 可以安全生成结构化导航卡与单条目 Catalog；章节身份、
原始响应、模型输出和运行回执可以重放核对。

本样例没有证明：`important_apis` 召回合格、多文档分类准确、Document Catalog 路由有效、
真实文档 Recall/Precision 合格，或生产知识库已经 qualified。
