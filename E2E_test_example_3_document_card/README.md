# E2E Test Example 3 — DeepSeek Document Card

这是一次真实、受控、可离线复核的单文档 Document Card smoke。固定的 OpenHarmony
官方 Markdown 经静态章节解析后发送给 DeepSeek V4 Pro，模型输出被严格转换为
`navigation_only_not_evidence` 的文档导航卡。

先看汇报文档：[REPORT.md](REPORT.md)。全部固定输入、请求、原始响应、Card 和 Receipt
位于 [`artifacts/`](artifacts/) 的内容寻址 Plan 目录中。

## 当前结论

- DeepSeek 请求：1 次成功，0 次重试，状态为 `valid_card`。
- 章节覆盖：5/5。
- Raw Response → Draft → Card → Receipt：离线重建验证通过。
- 内容质量：`NOT_QUALIFIED`。
- 已观察问题：原文存在多个明确 API/类/装饰器样式词，但 `important_apis` 为空。
- 已从真实 Card 构建 1 条目的导航 Catalog；它仍然不是 Evidence，也没有执行路由检索。
- 本样例没有执行多文档 Catalog 路由、Clause Retrieval、Finding 或 GitCode 发布。

## 产物分层

```text
00～05  固定来源、正文、静态目录、请求、精确 Plan 和离线 inspect
06～09  该 Plan 唯一一次真实调用的原始响应、Draft、Card 和 Receipt
10～11  离线重建后的合同断言与摘要
12      由真实 Card 构建的单文档导航 Catalog
REPORT  面向人工阅读的事实报告
```

`00～05` 证明“计划发送什么”，`06～09` 记录“这一次实际返回什么”，两者不能互相替代。
`valid_card` 只说明响应满足结构与身份合同；当前内容质量仍为 `NOT_QUALIFIED`，Card 也始终
保持 `evidence_eligible=false`。

## 离线复核

```bash
cd /home/autken/Code/arkts-code-reviewer
uv run python E2E_test_example_3_document_card/run_e2e.py
```

该命令不会读取 `.env` 或 API Key，不会发送网络请求，也不会再次调用 DeepSeek。
