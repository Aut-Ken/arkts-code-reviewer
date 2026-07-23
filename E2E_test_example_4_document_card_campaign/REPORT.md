# DeepSeek 7 文档 Document Card Campaign 报告

> 7 篇固定 Markdown 在精确 Campaign、Plan-set 和聚合预算授权下分别调用
> DeepSeek V4 Pro，生成仅用于文档导航的 Document Card。

## 1. 汇报结论

- 批量执行合同：**PASS**。
- Provider 状态：**7/7 `valid_card`**，7 次尝试、0 次重试、0 个失败。
- 章节身份覆盖：**115/115**，每个静态 section 恰好对应一条模型摘要，顺序一致。
- 原始响应链：**PASS**，7 组 Raw Response → Draft → Card → Receipt 及根级 Campaign Receipt
  均可从固定输入严格重建。
- 安全落盘：**PASS**，73 个 artifact 文件均为 `0600`、目录为 `0700`，没有 symlink、临时文件或
  API Key 命中；7 个 replay marker 均存在并绑定精确 Plan。
- 内容质量：**NOT QUALIFIED**。

本次结果说明“先为整篇 Markdown 建导航卡”的技术链已经真实跑通。人工只读检查没有发现明显的
大段编造，7 张卡的总摘要也基本能说明对应文档在讲什么；但长文档中的数值限制、禁止项、例外
条件和关键检索符号存在遗漏，不能把 `valid_card` 等同于内容完整或检索质量合格。

## 2. 固定授权

- Campaign：`document-card-campaign:sha256:1909052b40883a6d259024d8cc210d2c6a7852471f109fd680000c113f2d977c`
- Plan-set：`document-card-campaign-plan-set:sha256:8b4b80e8eeff96b4010591d91c283e44b0bcc0c1cb5944f19e854fa79b63fe7a`
- 最大文档数 / 尝试次数：7 / 7。
- 最大请求正文：163,932 bytes。
- 最大输出：28,672 tokens。
- 最大响应正文：14,000,000 bytes。
- Campaign 总时限：840,000 ms。
- 执行顺序：canonical sequential。
- 重试策略：每个 Plan 只尝试一次，不重试。

## 3. 实际链路

```text
Pinned Git Markdown × 7
  → NormalizedDocument
  → MarkdownDocumentMap
  → content-addressed Request / DispatchPlan
  → exact Campaign approval and replay preflight
  → DeepSeek V4 Pro（canonical sequential, one attempt per Plan）
  → strict Raw Response reducer
  → DocumentCardDraft
  → DocumentCard
  → per-document Receipt
  → Campaign Receipt
```

这条链只到多文档导航卡，没有构建多文档 Catalog，没有执行 Catalog Router、Clause Retrieval、
EvidencePack、Finding 或 GitCode 发布。

## 4. 聚合运行结果

- Campaign Receipt：
  `document-card-campaign-live-receipt:sha256:49e7609094eac9639c64ba29e9598e66fc3c143a8cf61c21636c66bcd5b88411`
- Outcome：`all_valid`。
- 总 Provider 延迟：134,503 ms。
- Campaign elapsed：134,834 ms。
- Token：输入 45,992、输出 11,677、合计 57,669。
- 原始响应正文：42,194 bytes。
- `use_scope`：`navigation_only_not_evidence`。
- `evidence_eligible`：`false`。
- `production_qualified`：`false`。
- Qualification：`live_navigation_campaign_not_document_quality_evidence`。

## 5. 逐篇结果

| # | 固定文档 | Sections | Topics | API 提示 | Token | 延迟 |
|---:|---|---:|---:|---:|---:|---:|
| 0 | Tabs 事件规格 | 31 | 14 | 20 | 12,723 | 31,718 ms |
| 1 | Image 基础内存优化规格 | 17 | 10 | 8 | 6,947 | 20,236 ms |
| 2 | Sendable 使用约束 | 30 | 14 | 7 | 12,819 | 30,738 ms |
| 3 | TaskPool 简介 | 12 | 5 | 6 | 8,707 | 13,644 ms |
| 4 | Timer API | 10 | 9 | 4 | 5,087 | 12,793 ms |
| 5 | 自定义组件生命周期 | 5 | 8 | 5 | 4,043 | 9,391 ms |
| 6 | 状态管理概述 | 10 | 8 | 33 | 7,343 | 15,983 ms |

Card IDs、单篇 Receipt IDs、每次请求的 token、延迟和响应字节由根级
[Campaign Receipt](artifacts/1909052b40883a6d259024d8cc210d2c6a7852471f109fd680000c113f2d977c/02_campaign-live-receipt.json)
完整记录。

## 6. 内容只读检查

| 文档 | 当前观察 |
|---|---|
| Tabs 事件规格 | 总摘要、Topics、回调 API 和章节摘要整体覆盖较好；遗漏部分 controller、fallback 和内部实现线索。 |
| Image 基础内存优化规格 | 共享对象、按需分配、位掩码、兼容性和验收指标覆盖较好；公共 API 与内部 C++ 类型混在同一 API 字段。 |
| Sendable 使用约束 | 大类规则与章节覆盖较完整；部分例外、诊断规则名和具体禁用接口没有进入导航字段。 |
| TaskPool 简介 | 七篇中压缩损失最大；多个时长、内存、线程、Promise 和 Worker 替代条件没有保留，当前只适合粗粒度导航。 |
| Timer API | 核心 API、最大延迟、后台冻结和共享 ID 池正确；参数细则、线程限制和版本信息有所遗漏。 |
| 自定义组件生命周期 | 生命周期和主要流程正确；状态变量修改禁令、GC 风险和特殊顺序等评审价值较高的约束有所遗漏。 |
| 状态管理概述 | V1/V2、观测粒度和主要装饰器覆盖较好；主线程限制以及部分装饰器和函数符号遗漏。 |

共同事实：摘要层适合回答“这篇文档大致讲什么”，但细粒度规则不能只依赖 Card。`important_apis`
目前混合公共 API、装饰器、语法和内部类型，字段口径也尚未用人工 Truth 冻结。上述检查是一次非盲
人工阅读，不是 Precision/Recall 评测。

## 7. 机器产物

| 层级 | 文件或目录 |
|---|---|
| Campaign selection | [00_campaign-selection.json](artifacts/1909052b40883a6d259024d8cc210d2c6a7852471f109fd680000c113f2d977c/00_campaign-selection.json) |
| Offline Campaign inspection | [01_campaign-inspection.json](artifacts/1909052b40883a6d259024d8cc210d2c6a7852471f109fd680000c113f2d977c/01_campaign-inspection.json) |
| Live Campaign receipt | [02_campaign-live-receipt.json](artifacts/1909052b40883a6d259024d8cc210d2c6a7852471f109fd680000c113f2d977c/02_campaign-live-receipt.json) |
| Seven exact Plan directories | [plans/](artifacts/1909052b40883a6d259024d8cc210d2c6a7852471f109fd680000c113f2d977c/plans/) |

每个 Plan 目录内，`00`～`05` 是固定来源和离线计划，`06`～`09` 是该 Plan 唯一一次真实调用的
Raw Response、Draft、Card 和 Receipt。

## 8. 准确边界

本 Campaign 证明：固定多文档输入、逐篇模型调用、严格输出约束、一次性执行、原子落盘、内容
寻址身份和完整重建链可以共同工作。

本 Campaign 没有证明：Document Card 字段的真实 Recall/Precision、多文档路由效果、知识条款
质量或生产检索质量。Document Card 仍只是导航层，不能成为 Finding evidence，不能绕过 Clause
切分、审核、curation 和 publication，也不能据此宣称 KnowledgeIndex 或完整评审产品 qualified。
