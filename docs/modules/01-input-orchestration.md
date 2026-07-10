---
title: 01 输入与编排模块
status: canonical
implementation: partial
updated: 2026-07-10
---

# 01 输入与编排模块

## 1. 模块职责

输入模块负责把 GitCode MR、CLI 文件或 API 请求统一转换成精确 `ChangeSet`，编排模块负责驱动一次评审任务的生命周期。

```text
外部输入
-> 鉴权和拉取代码
-> 标准化 base/head 和 diff
-> 创建 Review Job
-> 调用 Code Analysis / Retrieval / Rules / LLM / Output
```

不负责解析 ArkTS，也不判断代码质量。

这里的“外部输入”特指待评审的 repository/revision/diff。`arkts-knowledge`、
`arkts-corpora` 和 `arkts-tools` 是系统依赖资产，不属于一次 MR 的 `ChangeSet`，由独立
Source Registry 管理。

## 2. 当前实现

当前只有 `src/arkts_code_reviewer/code_analysis/cli.py`：

```bash
PYTHONPATH=src python -m arkts_code_reviewer.code_analysis.cli \
  src/pages/A.ets \
  --hunk src/pages/A.ets:40:8
```

CLI 行为：

1. 读取传入文件的当前内容。
2. 将 `PATH:START:LINES` 转为 `FileHunk(new_start, new_lines)`。
3. 任一文件存在 hunk 时，整次分析进入 `diff` 模式，否则进入 `full` 模式。
4. 直接同步调用 `CodeAnalyzer` 并打印 JSON。

当前缺失：

- Git diff 文本解析。
- 精确 added/deleted lines。
- base 版本旧文件。
- GitCode Webhook 和 API 调用。
- 目录递归、文件类型过滤和大小限制。
- Job Queue、幂等、取消、重试和状态查询。
- 鉴权、租户、审计和限流。

## 3. 当前输入契约

```python
FileInput(
    path="src/pages/A.ets",
    content="完整新文件源码",
    hunks=[FileHunk(new_start=40, new_lines=8)],
)
```

这个契约只适合本地 PoC，不能完整表达生产 MR。

## 4. 目标架构

```text
GitCode Webhook       CLI / Manual API
       |                      |
       v                      v
GitCodeAdapter          ManualAdapter
       |                      |
       +----------+-----------+
                  |
                  v
            ChangeNormalizer
        base/head/diff/rename/delete
                  |
                  v
              Job Store
                  |
                  v
            ReviewOrchestrator
                  |
      +-----------+-----------+
      |           |           |
 Code Analysis  Retrieval   Rules/LLM
      |           |           |
      +-----------+-----------+
                  |
                  v
             Output Adapter
```

## 5. ChangeSet 要求

目标契约见 [跨模块数据契约](../architecture/data-contracts.md)。输入模块必须提供：

```text
repository / change_id
base_revision / head_revision
old_path / new_path
change_type
old_content / new_content
精确 added_lines
精确 deleted_old_lines
Git diff_position 映射
```

ReviewUnit 不应自行解析原始 Git diff。

## 6. 编排状态机

```text
queued
-> fetching
-> analyzing
-> retrieving
-> reviewing
-> validating
-> publishing
-> completed
```

终止状态：

```text
failed | cancelled | superseded
```

MR push 新 commit 后，旧 head 对应任务应标记 `superseded`，避免旧结果覆盖新结果。

## 7. 幂等键

建议：

```text
repository + merge_request_id + head_revision + config_bundle_version
```

相同键重复触发时复用已有结果或返回同一 Job，不重复调用模型。

## 8. 文件过滤

第一版只处理：

```text
*.ets
*.ts（是否纳入需产品确认）
```

跳过：

```text
binary
generated code
vendor code
超过大小限制的文件
用户配置排除目录
```

跳过必须记录 diagnostics，不能静默丢失。

## 9. 降级与失败策略

| 场景 | 行为 |
|---|---|
| 单文件读取失败 | 标记文件失败，其他文件可继续 |
| diff 无法解析 | 整个 Job 失败，不伪装 full review |
| 文件过大 | 记录 skipped reason |
| Parser 降级 | 继续，但降低后续强结论权限 |
| Retrieval 无结果 | 允许 Rules 和 suggestion，禁止伪造依据 |
| LLM 暂时失败 | 按 Gateway 策略有限重试 |
| head revision 已更新 | 当前任务 superseded，不发布 |

## 10. 安全

- Webhook 验签。
- GitCode Token 仅在服务端注入。
- 拉取范围限制在目标 repository/revision。
- 防止路径穿越和任意本地文件读取。
- 日志不记录完整 Token 和未脱敏代码。
- 调模型前执行 provider 合规策略。

## 11. 配置

当前 CLI：

```text
--hunk PATH:START:LINES
--token-budget N
```

目标配置：

```text
GITCODE_BASE_URL
GITCODE_TOKEN
允许的文件扩展名
最大文件数/大小
任务超时和重试
队列并发
模型调用并发
忽略路径规则
```

## 12. 测试

需要补充：

- unified diff added/modified/deleted/rename fixture。
- 删除-only 行号映射。
- 多 hunk 和跨文件变更。
- Webhook 重放和幂等。
- MR head 更新导致旧任务 superseded。
- 路径安全和文件大小限制。

## 13. 下一步

1. 定义并实现 `ChangeSet` Pydantic 模型。
2. 实现纯函数 Git diff parser 和 Golden fixtures。
3. 让 CLI 也走统一 `ChangeSet`，删除手工 hunk 特例。
4. 在 Review Job metadata 中固定 `source_bundle_id/config_bundle_version`，但不把知识源
   内容复制进 ChangeSet。
5. 端到端闭环稳定后再加入 Webhook、队列和持久 Job Store。
