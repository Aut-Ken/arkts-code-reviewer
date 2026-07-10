---
title: 03 ReviewUnit 上下文规划模块
status: canonical
implementation: partial
updated: 2026-07-10
---

# 03 ReviewUnit 上下文规划模块

## 1. 模块职责

ReviewUnit 决定“为了评审本次改动，模型需要看到哪段代码”。

```text
ChangeSet + FileAnalysis
-> 找到改动所属语义结构
-> 扩展必要宿主和关联上下文
-> 在 token budget 内形成 ReviewUnit
```

它不是最终评审结果，也不负责检索知识。

## 2. 当前输入

```text
path
完整新文件 source
CodeFacts.declarations
FileHunk(new_start, new_lines)
```

## 3. 当前 full 算法

```text
找到所有 struct/class
-> 每个 struct/class 一个 ReviewUnit

没有 struct/class
-> 整个文件作为 fallback ReviewUnit
```

当前不会把超大 struct 继续按方法拆分。

## 4. 当前 diff 算法

对每个 hunk：

```text
找出所有与 hunk 行范围重叠的 Declaration
        |
        +-- 没找到
        |   -> hunk 上下各扩 20 行
        |   -> context_degraded=true
        |
        +-- 命中 build_method
        |   -> build <= 160 行：完整 build
        |   -> build > 160 行：最小重叠 ui_block
        |
        +-- 其他
            -> 最小重叠 method/function/builder/struct/class
```

多个 hunk 得到相同 `unit_ref` 时合并 changed lines。

## 5. 当前 HostSummary

```text
struct
decorators
states
lifecycle
imports
```

作用是给局部方法补充宿主背景。例如 `loadImages()` 本身看不到 `@State photos`，
HostSummary 将状态声明和宿主组件名称附加到 Unit。

当前限制：

- decorators 和 lifecycle 来自文件级 facts，多个 struct 可能互相污染。
- lifecycle 只有方法名，没有方法正文。
- states 通过正则从宿主文本提取。
- HostSummary 尚未进入当前 RetrievalUnit。

## 6. 当前输出

```python
ReviewUnit(
    file="PhotoWall.ets",
    unit_symbol="PhotoWall.loadImages",
    unit_ref="PhotoWall.loadImages@PhotoWall.ets",
    full_text="async loadImages() { ... }",
    file_changed_lines=[17, 18],
    unit_changed_lines=[4, 5],
    host_summary=HostSummary(...),
    context_degraded=False,
)
```

## 7. 当前设计漏洞

### 7.1 最小语法块不等于完整语义上下文

`setInterval` 在 `startTimer()`，`clearInterval` 可能在 `aboutToDisappear()`。
只选择最小方法会使模型看不到资源配对代码。

### 7.2 unit_ref 不唯一

同一 build 中两个同级 `Column` 可能都得到：

```text
Page.build.Column@Main.ets
```

去重可能合并错误代码块。目标 ID 必须加入 kind 和 source span。

### 7.3 一个 hunk 强制选一个声明

hunk 横跨两个方法时，当前只选择其中最小声明，可能丢失另一半。

### 7.4 固定 160 行不是 token budget

代码行长度和 token 差异很大，UI block 自身也可能超大。

### 7.5 当前 hunk 不是精确改动

`new_start/new_lines` 不能区分 Git hunk 中的上下文行、added lines 和 deleted lines。

## 8. 目标定位

ReviewUnit 应从“声明选择器”演进为“确定性上下文规划器”。

```text
精确改动行
-> 识别一个或多个语义 owner
-> 生成候选上下文
-> 按评审依赖扩展关联代码
-> token 预算裁剪
-> 输出选择原因和诊断
```

## 9. 目标算法

### 9.1 Change owner 识别

每个精确 added/deleted line 映射到最内层 owner：

```text
method
build_method
builder
ui_block
field_region
import_region
fallback_window
```

一个 change region 可以产生多个 owner。

### 9.2 基础上下文

| owner | 基础上下文 |
|---|---|
| 普通 method | 完整方法 |
| build | 改动 UI 节点及必要父链 |
| builder | 完整 Builder + 宿主签名 |
| field region | 字段声明 + 使用它的改动方法 |
| import | import + 与该符号有关的改动声明 |
| deletion-only | base 声明 + head 锚点上下文 |

### 9.3 关联上下文

根据场景受限扩展：

```text
has_timer/subscription/media
-> 对应生命周期方法正文

has_state_management
-> 相关状态字段和装饰器

方法调用本地 helper
-> 在预算允许时加入直接依赖
```

不能无界递归展开调用图。

### 9.4 Token budget

优先级：

```text
改动行
> owner 完整语义
> 宿主签名和状态
> 高相关关联代码
> 低相关上下文
```

超预算时记录被裁剪内容和 diagnostics，不能静默截断大括号结构。

## 10. 目标输出

见 [跨模块数据契约](../architecture/data-contracts.md)，关键新增字段：

```text
unit_id
unit_kind
source_span
context_span
changed_new_lines
deleted_old_lines
numbered_text
related_context
selection_reason
estimated_tokens
diagnostics
```

## 11. 行号约定

- `numbered_text` 使用文件绝对行号。
- Finding 主坐标使用文件绝对行号。
- Unit 相对行号只用于内部调试。
- 删除代码使用 base 文件行号并保存 Git diff position 映射。

## 12. 性能

当前选择复杂度约为 `O(H * D)`，H 是 hunk 数，D 是 Declaration 数，通常不是瓶颈。

优先优化：

- Parser 只运行一次。
- 按 span 过滤 facts。
- Unit 构建完成后再做去重。
- 用 content hash 缓存候选上下文。

不需要在第一版引入 interval tree，除非真实数据证明声明扫描成为瓶颈。

## 13. 配置

建议配置项：

```yaml
review_unit:
  fallback_context_lines: 20
  max_tokens_per_unit: 4000
  max_related_contexts: 3
  max_context_rounds: 1
  full_mode_strategy: struct_then_method
```

固定 160 行策略应被 token 预算替代。

## 14. Golden Set

每个用例包含：

```text
base/head 代码
精确 diff
期望 unit_kind
期望 source/context span
期望 changed lines
期望 related context
期望 selection_reason
```

必须覆盖：

- 普通方法。
- 短/长 build。
- 同名 UI block。
- 跨方法 hunk。
- 字段和 import 修改。
- deletion-only。
- Parser degraded。
- 超预算上下文。

## 15. 下一步

1. 先修复 `unit_id` 唯一性。
2. 引入 `unit_kind/source_span/selection_reason/diagnostics`。
3. 使用精确 ChangeSet，不再把 hunk 全范围当 changed lines。
4. 建立 30~50 个 ReviewUnit Golden 用例。
5. 再实现关联上下文和 token budget。

