---
title: PhotoWall 端到端数据流示例
status: canonical-example
updated: 2026-07-10
---

# PhotoWall 端到端数据流示例

本文用一段代码说明各模块交换什么数据。Parser/ReviewUnit 前半段基于当前实现，
Knowledge/Retrieval/Rules/Final LLM 部分是目标契约示例，不表示已经运行。

本例引用的代码可以来自已登记代码语料，但代码来源和知识来源必须分开：代码语料用于
构造评审请求，只有经过 curation 的知识 Clause 才能成为 Evidence。

## 1. ArkTS 代码

```ts
1  import img from '@ohos.multimedia.image'
2  import router from '@ohos.router'
3
4  @Entry
5  @Component
6  struct PhotoWall {
7    @State photos: PixelMap[] = []
8    private timerId: number = 0
9
10   aboutToDisappear() {
11     clearInterval(this.timerId)
12   }
13
14   async loadImages() {
15     const pixelMap = await img.createPixelMap(buffer)
16     this.photos.push(pixelMap)
17     this.timerId = setInterval(() => {
18       router.pushUrl({ url: 'pages/Detail' })
19     }, 1000)
20   }
21
22   build() {
23     Column() {
24       Grid() {
25         ForEach(this.photos, (photo: PixelMap) => {
26           Image(photo)
27             .objectFit(ImageFit.Cover)
28             .onClick(() => this.loadImages())
29         })
30       }
31     }
32   }
33 }
```

假设 MR 修改第 17~19 行的定时器逻辑。

## 2. Input

当前 PoC：

```json
{
  "path": "src/pages/PhotoWall.ets",
  "content": "完整新文件",
  "hunks": [{"new_start": 17, "new_lines": 3}]
}
```

目标 ChangeSet 还会包含精确 added/deleted lines、base/head revision 和 diff positions。

## 3. 第一次 Parser

当前 L0 实际能提取：

```jsonc
{
  "components": ["Column", "ForEach", "Grid", "Image"],
  "apis": [
    "clearInterval",
    "image.createPixelMap",
    "router.pushUrl",
    "setInterval"
  ],
  "decorators": ["@Component", "@Entry", "@State"],
  "attributes": ["objectFit", "onClick"],
  "syntax": ["arrow_fn", "async_fn", "await_expr"],
  "declarations": [
    "PhotoWall@L6-L33",
    "PhotoWall.aboutToDisappear@L10-L12",
    "PhotoWall.loadImages@L14-L20",
    "PhotoWall.build@L22-L32"
  ],
  "parser_layer": "L0"
}
```

目标 L1 FileAnalysis 会让每个 API/component 都带 span 和 owner。

## 4. ReviewUnit

第 17~19 行落在 `PhotoWall.loadImages@L14-L20`，当前算法选择完整方法：

```ts
14 | async loadImages() {
15 |   const pixelMap = await img.createPixelMap(buffer)
16 |   this.photos.push(pixelMap)
17*|   this.timerId = setInterval(() => {
18*|     router.pushUrl({ url: 'pages/Detail' })
19*|   }, 1000)
20 | }
```

HostSummary：

```json
{
  "struct": "PhotoWall",
  "decorators": ["@Component", "@Entry"],
  "states": ["@State photos: PixelMap[] = []"],
  "lifecycle": ["aboutToDisappear"],
  "imports": ["@ohos.multimedia.image", "@ohos.router"]
}
```

## 5. Unit Facts、Tags 和 Dimensions

当前实现会对 Unit + imports 二次 Parser，目标实现直接按 occurrence span 筛选。

```text
Unit APIs:
  image.createPixelMap, setInterval, router.pushUrl

Unit syntax:
  async_fn, await_expr, arrow_fn

Tags:
  has_image, has_timer, has_async, has_navigation

Dimensions:
  DIM-01~05, DIM-06, DIM-07, DIM-12
```

`has_image` 是否属于该 Unit 取决于 `image.createPixelMap` 的 canonical 识别，不需要把 build 中的 `Image` 带入。

## 6. RetrievalQuery

```jsonc
{
  "unit_id": "PhotoWall.ets@method:PhotoWall.loadImages:L14-L20",
  "target_platform": {"release": "OpenHarmony-5.x", "api_level": 12},
  "code_features": {
    "apis": ["image.createPixelMap", "setInterval", "router.pushUrl"],
    "tags": ["has_image", "has_timer", "has_async", "has_navigation"]
  },
  "dimensions": ["DIM-05", "DIM-06", "DIM-07"],
  "host_context": {
    "struct": "PhotoWall",
    "lifecycle": ["aboutToDisappear"]
  },
  "intent_summary": "组件异步创建图片并启动周期定时器"
}
```

## 7. Knowledge 和 Retrieval

假设索引中存在：

```jsonc
{
  "rule_id": "RESOURCE/TIMER/R-01",
  "dimension_ids": ["DIM-05", "DIM-06"],
  "tags": ["has_timer", "has_lifecycle"],
  "apis": ["setInterval", "clearInterval"],
  "text": "组件创建的定时器应在不再使用时主动清理。",
  "status": "Baselined",
  "source_ref": {
    "source_id": "arkui-specs",
    "revision": "98bbe6578e0f...",
    "relative_path": "timer/Feat-01-spec.md",
    "anchor": "R-01",
    "authority": "feature_spec"
  }
}
```

精确检索通过 `setInterval + has_timer` 命中，Embedding 可补充生命周期语义。
这条 Clause 是示例数据，不代表当前本地 11 个知识来源已经完成解析和发布。

## 8. Prompt 第一轮

Prompt 包含：

```text
ReviewUnit loadImages
改动行 17~19
HostSummary 表示存在 aboutToDisappear
DIM-06 检查项
RESOURCE/TIMER/R-01
```

此时模型只知道生命周期方法存在，不知道方法正文。正确行为不是直接报告泄漏，而是请求：

```json
{
  "context_requests": [
    {
      "symbol": "PhotoWall.aboutToDisappear",
      "reason": "需要确认是否调用 clearInterval"
    }
  ]
}
```

## 9. 补充关联上下文

编排层加入：

```ts
10 | aboutToDisappear() {
11 |   clearInterval(this.timerId)
12 | }
```

## 10. Final LLM 判断

模型现在能确认已有 `clearInterval`，因此不应生成“定时器未清理”Finding。

它仍可以根据其他 Evidence 检查：

```text
是否会重复调用 loadImages 导致旧 timerId 被覆盖
await createPixelMap 是否需要错误处理
路由调用是否与定时器回调设计相符
```

没有足够代码证据时返回空 Findings 或受限 suggestion，而不是强行制造问题。

## 11. 数据流总结

```text
完整文件 + diff
-> Parser 文件事实
-> ReviewUnit loadImages
-> Unit Tags/Dimensions
-> Retrieval 定时器条款
-> Prompt 发现上下文不足
-> 补 aboutToDisappear
-> LLM 排除错误的泄漏结论
-> Finding Validator
-> ReviewReport（记录 source_bundle/index/prompt/model versions）
```

这个例子说明 Retrieval 找到相关规范只是提供依据，最终是否构成问题仍取决于完整代码上下文。
