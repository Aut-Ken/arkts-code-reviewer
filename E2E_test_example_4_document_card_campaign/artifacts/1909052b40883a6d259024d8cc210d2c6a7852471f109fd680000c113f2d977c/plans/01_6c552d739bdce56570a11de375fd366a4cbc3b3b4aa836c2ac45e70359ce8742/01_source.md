# 特性规格

> Func-05-08-01-Feat-05 基础内存优化：优化 Image 组件内部数据结构以减少单节点内存占用，不影响公共 API 和用户可见行为。

## 概述

| 属性 | 值 |
|------|-----|
| 特性名称 | 基础内存优化 (Base Memory Optimization) |
| 特性编号 | Func-05-08-01-Feat-05 |
| 所属 Epic | 无 |
| 优先级 | P1 |
| 目标版本 | 待定 |
| SIG 归属 | ArkUI SIG |
| 状态 | Draft |
| 复杂度 | 标准 |

本规格定义 Image 组件基础内存优化的行为规则、验收标准和验证映射。优化范围限定在 NG Image 组件的内部数据结构，不影响公共 API 和用户可见行为。

---

## 用户故事

### US-1: 减少单个 Image 节点的基础内存

**作为** OpenHarmony 应用开发者
**我想要** Image 组件在加载和显示图片时占用更少内存
**以便** 在列表/网格等大量使用 Image 的场景下，应用整体内存占用更低

### US-2: 保持功能完全兼容

**作为** Image 组件的现有使用者
**我想要** 升级后所有现有功能（加载/显示/事件/alt 回退）行为不变
**以便** 不需要修改任何现有代码

---

## 验收标准

### P0 验收标准

| AC编号 | 验收标准 | 类型 |
|--------|---------|------|
| AC-1.1 | WHEN 500 个静态 Image 节点（src 为资源图片）加载完成 THEN 真实设备上单节点基础内存较优化前减少 ≥5KB | 正常 |
| AC-1.2 | WHEN 500 个 PixelMap Image 节点加载完成 THEN 真实设备上单节点基础内存较优化前减少 ≥5KB | 正常 |
| AC-2.1 | WHEN Image 组件正常加载/显示/销毁 THEN 行为与优化前完全一致（无功能回归） | 正常 |
| AC-2.2 | WHEN 运行 Image 相关全部单元测试 THEN 全部通过 | 正常 |
| AC-2.3 | WHEN Image 触发 onError 进入 alt 回退链 THEN alt 显示和事件行为与优化前一致 | 异常 |

### P1 验收标准

| AC编号 | 验收标准 | 类型 |
|--------|---------|------|
| AC-3.1 | WHEN 编译完成后 THEN sizeof(ImageSourceInfo) 较优化前减少 30%+ | 正常 |
| AC-3.2 | WHEN ImageDfxConfig 改为共享 THEN 单节点中 ImageDfxConfig 实例从 5-6 份降至 1 份 | 正常 |
| AC-3.3 | WHEN ImageLoadingContext 完成加载并释放 THEN 其持有的 ImageObject/CanvasImage 被主动释放 | 正常 |
| AC-3.4 | WHEN Image 无 alt 配置 THEN alt 相关状态不分配内存 | 正常 |


## 规则定义

> **统一规则表，取消 FR/BR/EX/RC 四分类。** 类型标签：**行为**（正常路径下的系统行为）、**边界**（输入/状态的临界点）、**异常**（非法输入或异常状态的处理）、**恢复**（系统异常后的恢复策略）。

| 规则ID | 类型 | 触发条件 | 预期行为 | 边界/约束 | 关联AC |
|--------|------|----------|----------|-----------|--------|
| R-1 | 行为 | — | .1: `ImageLayoutProperty` 中的 `propImageSourceInfo_`、`propAlt_`、`propAltError_`、`propAltPlaceholder_` 改为 `std::optional<RefPtr<ImageSourceInfo>>` | — | — |
| R-2 | 行为 | — | .2: `ImageLoadingContext::src_` 改为 `RefPtr<ImageSourceInfo>`，创建时传入 Pattern 持有的引用 | — | — |
| R-3 | 行为 | — | .3: ImageSourceInfo 在共享后必须保持不可变；属性更新时创建新的 ImageSourceInfo 实例 | — | — |
| R-4 | 行为 | — | .4: `GetImageSourceInfo()` 返回 `const RefPtr<ImageSourceInfo>&` 而非值拷贝 | — | — |
| R-5 | 行为 | — | .1: `ImagePattern` 中 3 个 ImageDfxConfig 改为 `RefPtr<ImageDfxConfig>`，首次需要时创建 | — | — |
| R-6 | 行为 | — | .2: `ImageSourceInfo::imageDfxConfig_` 改为 `RefPtr<ImageDfxConfig>` | — | — |
| R-7 | 行为 | — | .3: `ImageLoadingContext::imageDfxConfig_` 改为 `RefPtr<ImageDfxConfig>` | — | — |
| R-8 | 行为 | — | .4: DFX 日志写入前检查 RefPtr 是否已初始化，未初始化时跳过该条日志（不崩溃） | — | — |
| R-9 | 行为 | — | .1: `ImagePattern` 中 alt 相关字段合并为 `std::unique_ptr<AltImageState>` | — | — |
| R-10 | 行为 | — | .2: `ImagePattern` 中 altError 相关字段合并为 `std::unique_ptr<AltErrorState>` | — | — |
| R-11 | 行为 | — | .3: 仅在 alt/altError src 被设置时才创建对应状态对象 | — | — |
| R-12 | 行为 | — | .4: alt 回退链逻辑不变，只是通过 `altState_->loadingCtx` 替代 `altLoadingCtx_` 访问 | — | — |
| R-13 | 行为 | — | .1: `ImagePattern::keyEventCallback_` 和 `onProgressCallback_` 改为按需分配 | — | — |
| R-14 | 行为 | — | .2: 注册回调时检查并分配，未注册时零开销 | — | — |
| R-15 | 行为 | — | .1: `ImagePattern` 中 22 个 bool 合并为 `uint32_t` 位掩码结构体 | — | — |
| R-16 | 行为 | — | .2: 确认所有 bool 均在 UI 线程访问（不涉及跨线程原子操作需求） | — | — |
| R-17 | 行为 | — | .3: 提供内联 getter/setter 保持代码可读性 | — | — |
| R-18 | 异常 | — | .1: 当 ImageDfxConfig 的 RefPtr 为空时，DFX 日志跳过该条记录 | — | — |
| R-19 | 异常 | — | .2: 不因 DFX 配置未初始化而阻塞正常加载和显示流程 | — | — |
| R-20 | 异常 | — | .1: 如果代码路径需要修改共享的 ImageSourceInfo，必须创建新实例（copy-on-write） | — | — |
| R-21 | 异常 | — | .2: 不得在共享引用上直接修改成员 | — | — |
| R-22 | 恢复 | — | .1: 当 src 属性更新时，旧的 ImageSourceInfo RefPtr 引用计数自然递减，无需手动释放 | — | — |
| R-23 | 恢复 | — | .2: 当 alt 被移除时，`altState_` 的 unique_ptr 析构自动释放所有关联资源 | — | — |
| R-24 | 恢复 | — | .1: ImagePattern 析构时，所有 RefPtr 和 unique_ptr 自然释放 | — | — |
| R-25 | 恢复 | — | .2: ImageLoadingContext 在不再被引用时释放 ImageObject 和 CanvasImage | — | — |

---

## 接口规格

### 接口定义

> 本特性为已有实现补录，接口行为定义详见上方规则定义和用户故事。

无新增接口规格。

---

## 兼容性

| 维度 | 影响 |
|------|------|
| ArkTS API | 无变化 |
| C API | 无变化 |
| 默认行为 | 无变化 |
| 序列化/IPC | ImageSourceInfo 和 ImageDfxConfig 为内部类，不涉及 IPC |
| 单元测试 | 现有测试逻辑不变，可能需要调整 mock 数据构造方式 |

---

## 验证映射

| VM ID | AC编号 | 验证方法 | 验证命令/工具 |
|-------|-------|---------|-------------|
| VM-1 | AC-1.1 | 500 Image 真实设备内存实测 | SpecTest 测试用例 + 设备内存 profiling |
| VM-2 | AC-1.2 | 500 PixelMap Image 真实设备内存实测 | SpecTest 测试用例 + 设备内存 profiling |
| VM-3 | AC-2.1 | Image 相关单元测试全量 | `./build.sh --product-name rk3568 --build-target //foundation/arkui/ace_engine/test/unittest/core/pattern:image_pattern_test --ccache` |
| VM-4 | AC-2.2 | Image 相关单元测试全量 | 同上 |
| VM-5 | AC-2.3 | Alt fallback 单元测试 | 同上（gtest_filter=*Alt*） |
| VM-6 | AC-3.1 | sizeof 编译期检查 | `static_assert(sizeof(ImageSourceInfo) < X)` |
| VM-7 | AC-3.2 | 调试期实例计数 | 添加临时计数器验证 |
| VM-8 | AC-3.3 | 单元测试验证释放 | Mock ImageLoadingContext 生命周期 |
| VM-9 | AC-3.4 | 单元测试验证无 alt 时不分配 | 检查 altState_ == nullptr |

---

## SpecTest 适用性

**N/A** — 本次优化为内部数据结构重构，不改变用户可见行为，不改变 Inspector 可观测的节点树/属性/布局。无法通过 SpecTest Host Preview 断言。

替代验证：sizeof 对比 + 单元测试 + 真实设备内存 profiling。

---

## 不涉及项

- 图片解码/加载底层流程
- 动画图 / SVG 代码路径
- Legacy Image 组件
- 渲染/绘制路径优化
- 公共 API 变更

---

## 多设备适配声明

| 设备类型 | 行为差异 | 规格/约束 | 验证方式 | 证据 |
|----------|----------|-----------|----------|------|
| 手机 | 无差异 | — | — | — |
| 平板 | 无差异 | — | — | — |
| 折叠屏 | 无差异 | — | — | — |

---

## context-references

```yaml
context-queries:
  - repo: "openharmony/arkui_ace_engine"
    query: "ImagePattern memory layout, ImageSourceInfo struct size, ImageDfxConfig sharing, alt state lazy allocation"
  - repo: "openharmony/arkui_ace_engine"
    query: "ImageLoadingContext lifecycle and ImageObject/CanvasImage ownership"
```

**关键文档：** `design.md`（同目录）、`docs/kb/components/media/image.md`（Image 组件 KB）
