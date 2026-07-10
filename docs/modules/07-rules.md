---
title: 07 Deterministic Rules 模块
status: canonical
implementation: designed
updated: 2026-07-10
---

# 07 Deterministic Rules 模块

## 1. 模块职责

对能够由确定性代码高置信判断的问题产生 `RuleFinding`。

```text
FileAnalysis + ReviewUnit + ChangeSet
-> 选择适用 Rules
-> 执行确定性检测
-> RuleFinding[]
```

Rules 不处理需要复杂语义推理的建议，也不替代 Final LLM。

## 2. 当前状态

无代码、无 `RuleFinding` 模型、无 registry。当前只有设计建议。

## 3. 适合 Rules 的问题

第一版候选：

```text
any / var 等明确语言限制
ArkTS 明确禁用语法
确定的装饰器组合错误
已知 deprecated API
明确缺少权限声明
部分高置信资源配对
硬编码敏感 Token 模式
```

不适合直接规则化：

```text
组件是否应该拆分
命名是否清晰
复杂并发竞态
跨模块资源所有权
通用“是否优雅”判断
```

## 4. 架构

```text
Rule Registry
元数据、状态、维度、知识引用
        |
        v
Rule Selector
根据 Unit Facts/Tags/模式选择候选
        |
        v
Rule Engine
执行 Python 检测器
        |
        v
RuleFinding Validator
校验行号、diff 相关性和 rule_id
        |
        v
RuleFinding[]
```

## 5. Rule 定义

```python
class Rule(Protocol):
    id: str

    def evaluate(self, context: RuleContext) -> list[RuleFinding]: ...
```

`RuleContext`：

```text
ChangeSet
FileAnalysis
ReviewUnit
Unit Facts/Tags/Dimensions
可选项目配置文件
```

## 6. Rule Registry

YAML 保存治理元数据，Python 保存检测逻辑：

```yaml
- id: ARKTS-NO-ANY
  status: Active
  severity: high
  dimensions: [DIM-01, DIM-02]
  triggers:
    syntax: [any_type]
  implementation: no_any_type
  reference_rule_ids: [LANGUAGE/TYPE/R-01]
  applies_to: [full, diff]
```

不建议把复杂检测表达式全部塞进 YAML，避免形成难以测试的自研规则语言。

## 7. RuleFinding

```jsonc
{
  "rule_id": "ARKTS-NO-ANY",
  "unit_id": "...",
  "file": "Main.ets",
  "line": 18,
  "severity": "high",
  "problem": "ArkTS 代码中使用了 any 类型",
  "code_evidence": "const value: any = input",
  "reference_rule_ids": ["LANGUAGE/TYPE/R-01"],
  "confidence": "deterministic",
  "is_diff_related": true
}
```

规则 Finding 必须带精确行号、代码证据和稳定规则 ID。

## 8. Rules 与知识库的关系

知识条款是规范 source of truth，Rule 是其中部分规范的可执行实现。

```text
知识 rule_id
<-> executable rule_id
```

不要在 Python 里复制一份无法追溯的规范文本。RuleFinding 应引用知识条款。

## 9. Rules 与 Final LLM

Prompt 中传入 RuleFinding，要求 LLM：

```text
不重复报告相同问题
可以补充影响和更具体的修改建议
不得降低确定性规则的事实置信度
不得把规则应用到其他无关代码行
```

输出层可以直接发布 RuleFinding，也可以与 LLM 解释合并为 `origin=merged`。

## 10. Diff 策略

规则必须区分：

```text
问题在 added line
问题由 added line 直接造成
问题只存在于未修改旧代码
```

第一版默认只发布前两类。全文件规则仍可运行，但旧代码问题只记录到 diagnostics 或 summary。

## 11. 降低误报

- 第一版只实现高 precision 规则。
- 跨文件、跨生命周期配对没有完整事实时不下确定结论。
- `setInterval` 未在同方法清理不等于泄漏。
- 规则需要 negative fixtures，而不只是正例。
- 规则状态支持 Draft 影子运行。

## 12. 配置与治理

```text
Draft -> Active -> Deprecated
```

每条规则记录：

```text
owner
created_version
reference_rule_ids
适用 ArkTS/SDK 版本
误报豁免
变更记录
```

配置见 [配置与版本规范](../architecture/configuration.md)。

## 13. 测试

每条 Rule 至少提供：

```text
应报样例
不应报样例
边界样例
diff-related 样例
Parser degraded 样例
版本适用样例
```

目标：高严重级确定性规则在 Golden Set 中误报接近 0。

## 14. 第一版规则集建议

先实现 10~20 条最高价值、最容易证明的规则：

```text
语言禁用项
明确 deprecated API
明显不合法装饰器组合
资源/权限中可确定的少数规则
```

不要先建设通用规则 DSL，也不要一次翻译所有外部工具规则。

## 15. 下一步

1. 定义 `RuleContext`、`RuleFinding` 和 registry loader。
2. 从已确认知识条款选择第一批规则。
3. 建立表驱动测试和 Draft 影子执行。
4. 将 RuleFinding 接入统一 Finding Validator。

