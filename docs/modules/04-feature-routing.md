---
title: 04 Tags 与评审维度模块
status: canonical
implementation: partial
updated: 2026-07-12
---

# 04 Tags 与评审维度模块

## 1. 模块职责

将 Parser Facts 转换为稳定场景和评审策略：

```text
CodeFacts / Unit Facts
-> Tags
-> Unit Dimensions
-> Retrieval 路由、Rules 选择和 Prompt 检查项
```

Tags 和 Dimensions 都不是代码问题。

## 2. 概念区别

| 层 | 问题 | 示例 |
|---|---|---|
| Fact | 代码里有什么 | `setInterval` |
| Tag | 属于什么场景 | `has_timer` |
| Dimension | 从什么角度检查 | DIM-06 资源管理 |
| Finding | 是否真的有问题 | 定时器未清理 |

## 3. 当前实现

`tagger.py` 硬编码：

- 24 个 Tags。
- DIM-01~05 始终触发。
- DIM-06~11 按部分 Tags 条件触发。
- DIM-12 始终触发。

`CodeAnalyzer` 对每个 ReviewUnit 二次 Parser 后生成 Tags，将所有 Unit Tags 合并，
再计算一个 MR 级 `triggered_dimensions` 并集。

当前问题：

- Tags/Dimensions 不是配置驱动。
- Dimensions 只有 MR 级并集，没有 Unit 级输出。
- `CodeFeatures` 缺少 attributes。
- Unit 二次 Parser 可能丢宿主状态和装饰器。

ReviewUnit RU-0/RU-1 没有改变 Tag 语义。后续删除二次 Parser 时必须区分：

```text
unit_exact
  只能由 Unit span 内 declarations/components/symbols 推导

file_hints
  APIs/decorators/attributes/syntax 等文件级 presence signals
```

`file_hints` 可以保守扩大 MR 或 Unit 候选路由，但不能显示成“这个 Unit 精确包含该事实”，
也不能成为 Finding evidence。若引入该双作用域，必须同步修改 CodeFeatures、Tagger 测试和
跨模块数据契约；不能只在 Analyzer 内静默复制集合。

## 4. 当前 24 Tags

| 类别 | Tags |
|---|---|
| 资源 | `has_image`, `has_timer`, `has_subscription`, `has_media`, `has_file_io` |
| 并发 | `has_async`, `has_taskpool`, `has_worker` |
| UI/体验 | `has_interactive_component`, `has_layout`, `has_responsive_api`, `has_text_display`, `has_resource_ref` |
| 安全/数据 | `has_permission_request`, `has_user_input`, `has_network`, `has_storage` |
| ArkTS/ArkUI | `has_state_management`, `has_lifecycle`, `has_list_render`, `has_animation`, `has_builder`, `has_navigation`, `has_logging` |

逐项语法和触发条件见 [教学文档](../learning/arkts-parser-fields-tags.md)。

## 5. 当前 12 Dimensions

| ID | 名称 | 触发策略 |
|---|---|---|
| DIM-01 | 规范符合度 | 始终 |
| DIM-02 | ArkTS 语言特性 | 始终 |
| DIM-03 | 性能 | 始终 |
| DIM-04 | 可维护性 | 始终 |
| DIM-05 | 健壮性 | 始终 |
| DIM-06 | 资源与内存管理 | image/subscription/timer/media/file_io |
| DIM-07 | 并发与异步 | async/taskpool/worker |
| DIM-08 | 无障碍 | interactive_component |
| DIM-09 | 多设备适配 | layout/responsive_api |
| DIM-10 | 国际化 | text_display/resource_ref |
| DIM-11 | 安全 | permission/user_input/network/storage |
| DIM-12 | DFX 与可测性 | 始终 |

## 6. 目标架构

```text
FileAnalysis facts with spans
        |
        v
Unit Fact Filter
        |
        v
TagEngine(tags.yaml)
        |
        v
DimensionEngine(dimensions.yaml)
        |
        +--> Unit Retrieval Policy
        +--> Rule Registry Selection
        +--> Prompt Checks
       +--> Report Classification
```

`TagEngine` 的 API/组件触发器最终来自版本化配置和共享 `ApiSymbolCatalog`。外部文档、
Skills 或代码语料不能在运行时自行新增 Tag；新触发条件必须进入主项目配置、测试和
`feature_config_version`。

## 7. Unit 级维度

目标输出：

```json
{
  "unit_id": "...",
  "tags": ["has_timer", "has_async"],
  "dimensions": ["DIM-05", "DIM-06", "DIM-07"],
  "feature_config_version": "features-v1"
}
```

MR 级维度并集只用于：

```text
总体报告分类
全局 token budget
统计
```

不能把一个 Unit 的 `has_network` 传播到其他 Unit 的 Retrieval 和 Prompt。

## 8. always_check 与 retrieval_policy

必须分离：

```text
always_check
  是否始终把该维度的检查项加入 Prompt

retrieval_policy
  是否有具体 Facts/Tags 时才检索知识
```

例如 DIM-04：

```yaml
always_check: true
retrieval_policy: signal_required
```

AI 始终关注可维护性，但只有检测到长方法、深嵌套、重复代码等信号时才检索相关知识。

## 9. 各维度检索策略

### 精确检索优先

```text
DIM-02 ArkTS 语言特性
DIM-06 资源与内存管理
DIM-07 并发与异步
```

这些维度有明确 API、组件或装饰器。

### 混合检索

```text
DIM-03 性能
DIM-05 健壮性
DIM-08 无障碍
DIM-09 多设备适配
DIM-10 国际化
DIM-11 安全
```

需要结构化信号、关键词、Embedding 和适用性重排。

### 不直接作为检索入口

```text
DIM-01 规范符合度
DIM-04 可维护性
DIM-12 可测试性部分
```

它们应先转化为具体度量和场景，或作为 Prompt 检查原则。

## 10. 需要补充的静态信号

| 维度 | 目标信号 |
|---|---|
| 性能 | build 长度、UI 深度、列表构造、循环内对象创建 |
| 可维护性 | 方法长度、职责数量、重复子树、依赖数量 |
| 健壮性 | try/catch、错误回调、nullable、返回路径 |
| 无障碍 | 可见标签、accessibility 属性 occurrence |
| 多设备 | mediaquery/display、固定尺寸、断点 API |
| 国际化 | 字符串字面量与 `$r` occurrence |
| 安全 | permission/network/storage occurrence 和配置交叉信息 |
| 可测试性 | 全局状态、静态依赖、不可替换外部调用 |

## 11. 配置

配置 schema 见 [配置与版本规范](../architecture/configuration.md)：

```text
config/tags.yaml
config/dimensions.yaml
```

每次输出必须记录配置版本。

## 12. 治理

Tag 和 Dimension 状态：

```text
Draft -> Active -> Deprecated
```

Draft 维度可以影子运行，但不影响正式结论。删除旧 ID 会破坏历史报告，必须 Deprecated 而非物理删除。

外部来源的角色：

```text
interface-sdk-js                 提供 canonical API、版本、权限和 SystemCapability
arkui-specs / openharmony-docs   提供场景与检查方向候选
Skills                           只提供待人工评审的 taxonomy 候选
四类代码语料                    提供 Tag 正例、反例和串扰测试
```

来源变化不会自动修改线上 Tags/Dimensions；必须经过配置版本升级。

## 13. 测试

### Tag 表驱动测试

```text
构造 CodeFacts
-> 断言精确 Tags 集合
```

每个 Tag 至少包含正例、反例和易混淆例。

### Dimension 配置测试

- Tag 引用存在。
- trigger 表达式可解析。
- Unit 之间不串扰。
- always_check 与 retrieval_policy 独立生效。
- Draft/Deprecated 行为正确。

## 14. 下一步

1. ReviewUnit Golden 和 Unit identity 已稳定；等 RU-2 多 owner/质量传播完成后，冻结
   Unit exact/file hints 契约。
2. 将 Tags/Dimensions 迁移为版本化 YAML。
3. 输出 Unit 级 Dimensions。
4. 将 attributes 和带位置 facts 纳入 CodeFeatures。
5. 为 24 Tags 建完整表驱动测试。
6. 用 `interface-sdk-js` 生成共享 API catalog，替代散落的名称表。
7. 为抽象维度补充静态度量信号，再接 Retrieval。
