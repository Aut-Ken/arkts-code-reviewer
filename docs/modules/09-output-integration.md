---
title: 09 输出与 GitCode 集成模块
status: canonical
implementation: planned
updated: 2026-07-10
---

# 09 输出与 GitCode 集成模块

## 1. 模块职责

将经过验证的 RuleFinding/LLM Finding 统一为 ReviewReport，执行过滤、去重、行号映射、渲染和 GitCode 发布。

```text
Raw Findings
-> Validate
-> Merge/Deduplicate
-> Policy Filter
-> JSON ReviewReport
-> Markdown / GitCode
```

## 2. 当前状态

无 Finding 模型、Validator、Markdown Renderer 或 GitCode Adapter。

CLI 当前只打印 `AnalysisResult`，不是正式评审报告。

## 3. JSON source of truth

所有输出先形成版本化 `ReviewReport`：

```jsonc
{
  "review_id": "review-123",
  "change_id": "mr-456@head-sha",
  "status": "completed",
  "findings": [],
  "summary": {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 0,
    "suggestion": 1
  },
  "versions": {},
  "diagnostics": []
}
```

Markdown 和 GitCode 评论都是 JSON 的渲染视图，不能成为独立事实来源。

## 4. 输出流水线

```text
RuleFinding + LLM Finding
        |
        v
Schema Validator
        |
        v
Evidence/Line Validator
        |
        v
Finding Merger
规则与 LLM 对同一问题合并
        |
        v
Deduplicator
        |
        v
Publication Policy
严重级、置信度、diff 相关性、数量上限
        |
        +--> ReviewReport JSON
        +--> Markdown Renderer
        +--> GitCode Adapter
```

## 5. Finding 合并与去重

候选去重键：

```text
file
+ line/span
+ dimension/rule family
+ normalized problem signature
```

同一问题同时被 Rule 和 LLM 报告时：

```text
保留 Rule 的确定性证据
合并 LLM 的影响说明和修改建议
origin = merged
```

不同问题不能仅因同一行而合并。

## 6. 严重级

```text
critical    发布阻塞、安全高危、明确编译阻塞
high        明确 bug、资源泄漏、权限/隐私风险
medium      健壮性、性能、兼容性问题
low         可维护性和体验问题
suggestion  无明确规范依据的改进建议
```

严重级必须受规则和 schema 约束，不能完全接受模型自由文本。

## 7. Diff 相关性

行内评论默认要求：

```text
Finding 位于 added line
或问题由本次改动直接引发且能映射到 diff position
```

旧代码问题：

```text
默认不发布行内评论
可以进入 summary diagnostics
是否展示由产品策略决定
```

## 8. Git 行号映射

需要同时保存：

```text
新文件绝对行号
旧文件绝对行号
diff_position
head revision
```

发布前再次确认 MR head revision 未变化。旧任务不得向新 revision 写评论。

删除-only Finding 需要 GitCode API 支持旧行评论；不支持时放入总评并引用删除片段。

## 9. Markdown Renderer

建议结构：

```text
评审摘要
高优先级问题
其他问题
建议
版本和诊断信息（折叠或附录）
```

每条包含：

```text
文件和行号
严重级和维度
问题
代码证据
影响
修改建议
知识引用
置信度
```

## 10. GitCode Adapter

接口：

```python
class ReviewPublisher(Protocol):
    def publish_inline(self, report): ...
    def publish_summary(self, report): ...
    def update_or_replace(self, previous_review_id, report): ...
```

需要支持：

- Bot 身份。
- 幂等发布。
- 新 commit 后旧评论更新或过期标记。
- API 限流和有限重试。
- 评论发布失败的逐条结果。

## 11. 发布策略

建议第一版：

```text
每 Unit 最多 3~5 条
每 MR 最多 10 条
critical/high 优先
同类问题聚合
suggestion 超限时只进总评
```

具体值通过历史 MR 回放和用户反馈确定。

## 12. 配置

见 `config/output.yaml`：

```text
inline/summary 开关
严重级过滤
最低 confidence
每 Unit/MR 上限
旧评论更新策略
是否发布 suggestion
```

环境：

```text
GITCODE_BASE_URL
GITCODE_TOKEN
```

## 13. 安全与隐私

- 评论内容不得泄露不在 MR 可见范围内的敏感代码。
- Token 和内部 URL 不进入报告。
- 日志记录 Finding ID，不默认记录完整代码。
- ReviewReport 的存储和保留遵循内部合规策略。
- Markdown 转义用户代码，避免渲染注入。

## 14. 测试

- Finding schema 和引用 allowlist。
- 行号、rename 和 deletion-only 映射。
- Rule/LLM Finding 合并。
- 重复 Finding 去重。
- 数量上限和排序。
- Markdown snapshot。
- GitCode API mock/contract。
- MR head 更新时禁止发布旧结果。
- 部分评论失败后的幂等重试。

## 15. 下一步

1. 实现 Finding Validator 和 ReviewReport。
2. 实现 Markdown Renderer，先服务 CLI。
3. 用真实 diff fixture 验证行号。
4. 最后接 GitCode API 和评论生命周期管理。

