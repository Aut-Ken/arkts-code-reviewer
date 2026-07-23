# E2E Example 4：DeepSeek 7 文档 Document Card Campaign

这是一次真实、受控、可离线复核的 7 文档 Document Card Campaign。7 篇固定 Markdown 先由
确定性代码生成章节目录和精确请求，再按 canonical 顺序分别发送给 DeepSeek V4 Pro；每篇模型
输出都被严格转换为 `navigation_only_not_evidence` 的 Document Card。

先看人工可读报告：[REPORT.md](REPORT.md)。固定输入、请求、原始响应、Card 和 Receipt 位于
[`artifacts/`](artifacts/) 的内容寻址 Campaign 目录中。

## 当前结果

- Campaign：`document-card-campaign:sha256:1909052b40883a6d259024d8cc210d2c6a7852471f109fd680000c113f2d977c`
- Plan-set：`document-card-campaign-plan-set:sha256:8b4b80e8eeff96b4010591d91c283e44b0bcc0c1cb5944f19e854fa79b63fe7a`
- Campaign Receipt：`document-card-campaign-live-receipt:sha256:49e7609094eac9639c64ba29e9598e66fc3c143a8cf61c21636c66bcd5b88411`
- Provider 结果：7/7 `valid_card`，7 次尝试、0 次重试、0 个失败。
- 章节绑定：115/115，每个静态 section 恰好对应一条摘要，顺序一致。
- 实际 Token：输入 45,992、输出 11,677、合计 57,669。
- 实际响应正文：42,194 bytes。
- 总 Provider 延迟：134,503 ms；Campaign elapsed：134,834 ms。
- 完整重建：PASS。
- 内容质量：`NOT QUALIFIED`。

`valid_card` 只说明 JSON、身份、章节覆盖和来源绑定满足合同，不说明摘要已经完整。人工只读检查
没有发现明显的大段编造，但发现长文档中的数值限制、禁止项、例外条件和检索符号有不同程度的
遗漏，其中 TaskPool 文档最明显。

## 产物分层

```text
00～01  Campaign selection 与离线 inspection
02      Campaign live receipt
plans/<ordinal>_<plan-digest>/
  00～05  固定来源、正文、静态目录、请求、精确 Plan 和离线 inspection
  06～09  唯一一次真实调用的原始响应、Draft、Card 和 Receipt
REPORT  面向人工阅读的运行与内容检查报告
```

每篇文档单独调用，未把 7 篇原文合并进同一个 Prompt。`00`～`05` 证明“计划发送什么”，
`06`～`09` 记录“实际返回什么”，根级 `02` 汇总整次 Campaign；三层不能互相替代。

## 离线检查

重新生成和核对离线 `00`～`05`：

```bash
uv run python tools/prepare_deepseek_document_card_campaign.py
```

只读重建 Campaign 并核对固定离线产物：

```bash
uv run python tools/run_deepseek_document_card_campaign.py
```

以上命令不会读取 `.env` 或 API Key，也不会再次调用 DeepSeek。7 个 Plan 的唯一 live attempt
已经由 replay marker 消费，不能重试。

## 准确边界

本样例证明：7 篇固定 Markdown 可以在聚合预算和一次性授权下生成结构完整、可追溯、可重建的
Document Card；批量 runner、原始响应 reducer、逐篇 receipt 和 Campaign receipt 的运行链成立。

本样例没有证明：Document Card 的字段召回完整、文档分类或路由准确、Document Recall/Precision
合格、Card 可以成为 Evidence，或生产知识库已经 qualified。当前也没有由这 7 张 Card 构建并验证
多文档 Catalog，更没有执行 Catalog Router、Clause Retrieval、EvidencePack、Finding 或 GitCode
发布。
