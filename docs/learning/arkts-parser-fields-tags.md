---
title: ArkTS 零基础入门与 Parser 字段、Tags 详解
module: code-analysis
project: arkts-code-reviewer
status: learning-reference
created: 2026-07-10
updated: 2026-07-10
tags:
  - arkts
  - parser
  - code-facts
  - tags
  - review-unit
---

# ArkTS 零基础入门与 Parser 字段、Tags 详解

## 本文目标

本文面向没有学过 ArkTS 和 ArkUI 的读者。读完后应当能够：

1. 看懂本项目测试样例中最常见的 ArkTS 语法。
2. 理解 `CodeFacts` 每个字段表示什么，以及它是从哪段代码中提取出来的。
3. 理解 24 个 Tags 的触发条件、用途和对应评审方向。
4. 区分 Parser Facts、Tags、Dimensions、HostSummary 和最终 Finding。

本文使用仓库中的真实测试样例，并以当前 `LexicalParser` 的实际输出为准。

截至 2026-07-10，`LexicalParser` 已在相邻 `arkui_ace_engine` 的 63 个登记样本上全部
解析成功；L1 sidecar 的 npm 依赖尚未安装。11 个知识来源虽然已经 clone 和登记，但
Clause 构建、Retrieval 和正式 LLM 评审仍未实现。不要把下文的目标数据流误读为当前
已经运行的端到端系统。

---

## 1. 先建立全局心智模型

在本项目中，一段 ArkTS 代码会经过下面几次翻译：

```text
ArkTS 源代码
    |
    | Parser 读取代码
    v
CodeFacts
    代码中客观存在的组件、API、装饰器、语法和声明边界
    |
    | Tagger 把底层事实翻译成稳定的场景标签
    v
Tags
    has_timer / has_image / has_async ...
    |
    | trigger_dimensions 把场景映射为评审方向
    v
Dimensions
    资源管理 / 并发异步 / 无障碍 / 安全 ...
    |
    | Retrieval 根据具体事实和 Tags 查规范
    v
Evidence
    与当前代码相关的知识条款
    |
    | Final LLM 结合代码、背景和依据作出判断
    v
Findings
    真正的代码问题、影响和修改建议
```

外部资料在这条链路中的位置：

```text
arkui-specs / OpenHarmony docs / API metadata
    -> 离线解析、人工治理、版本化 Clause 和 API catalog
    -> Retrieval Evidence

XTS / Samples / Codelabs / arkui_ace_engine
    -> Parser、ReviewUnit、Rules 和最终评审的测试语料

Skills
    -> 人工审阅的规则/Prompt/工作流候选，不直接成为 Evidence
```

详细边界见 [多仓库工作区与知识来源架构](../architecture/workspace-and-sources.md)。

最重要的区别：

| 层 | 回答的问题 | 示例 | 是否代表代码有问题 |
|---|---|---|---|
| Parser Fact | 代码里有什么 | 存在 `setInterval` | 否 |
| Tag | 属于什么场景 | `has_timer` | 否 |
| Dimension | 应从什么方向检查 | 资源与内存管理 | 否 |
| Evidence | 应依据什么标准 | 定时器应及时清理 | 否 |
| Finding | 是否真的有问题 | 定时器没有清理 | 是 |

---

## 2. ArkTS 和 ArkUI 是什么

### 2.1 ArkTS

可以先把 ArkTS 理解为面向 HarmonyOS 应用开发的强类型语言。它的很多写法与 TypeScript 相似：

```ts
let count: number = 1
const title: string = 'Photo Wall'

function add(a: number, b: number): number {
  return a + b
}
```

这里：

```text
let              可重新赋值的变量
const            不重新赋值的变量
count: number    count 的类型必须是数字
title: string    title 的类型必须是字符串
: number         函数返回值是数字
```

本项目不是 ArkTS 编译器。Parser 主要提取与代码评审有关的结构化事实，不验证全部类型和编译规则。

### 2.2 ArkUI

ArkUI 是 HarmonyOS 的 UI 框架。本项目样例使用声明式 UI 写法：

```ts
build() {
  Column() {
    Text('Hello')
    Button('Save')
  }
}
```

可以把它读成：

```text
创建一个纵向布局 Column
    放入一个 Text
    再放入一个 Button
```

它和传统的逐步创建控件不同，更像在代码中直接描述 UI 树：

```text
Column
├── Text
└── Button
```

---

## 3. 贯穿全文的真实代码样例

下面代码来自 `tests/test_code_analysis_parser.py` 中的 `SAMPLE`：

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

后面的所有字段和 Tags 都用这段代码解释。

---

## 4. 逐段学习样例中的 ArkTS 语法

### 4.1 import 导入模块

```ts
import img from '@ohos.multimedia.image'
import router from '@ohos.router'
```

第一行可以读成：

```text
从 @ohos.multimedia.image 模块导入默认对象
在当前文件中把它命名为 img
```

所以代码中写：

```ts
img.createPixelMap(buffer)
```

Parser 会结合 import 信息，将它规范化为：

```text
image.createPixelMap
```

`img` 是局部别名，`image` 是本项目希望保存的统一模块前缀。

ArkTS 中还可能出现两种常见导入方式：

```ts
// 命名导入
import { window, display } from '@kit.ArkUI'

// 命名空间导入
import * as http from '@ohos.net.http'
```

### 4.2 装饰器 Decorator

```ts
@Entry
@Component
struct PhotoWall {
```

以 `@` 开头的标记叫装饰器。可以先把它理解为贴在代码结构上的元数据标签。

```text
@Entry       这个组件可以作为页面入口
@Component   这个 struct 是 ArkUI 自定义组件
```

状态字段前还有：

```ts
@State photos: PixelMap[] = []
```

`@State` 表示 `photos` 是组件内部状态。状态发生变化时，依赖该状态的 UI 可能重新渲染。

注意：装饰器在 Parser 中只是客观事实。出现 `@State` 不等于状态管理写错了。

### 4.3 struct 自定义组件

```ts
struct PhotoWall {
  // 字段、方法和 build() 都属于 PhotoWall
}
```

在当前 ArkUI 场景中，可以把 `struct PhotoWall` 理解为一个自定义 UI 组件定义。

```text
PhotoWall
├── 状态字段 photos
├── 普通字段 timerId
├── 生命周期方法 aboutToDisappear
├── 业务方法 loadImages
└── UI 构建方法 build
```

### 4.4 字段、类型和数组

```ts
@State photos: PixelMap[] = []
private timerId: number = 0
```

逐项拆开：

```text
photos             字段名
PixelMap[]         PixelMap 数组类型
= []               初始值为空数组

private            只允许在当前结构内部访问
timerId            字段名
number             数字类型
= 0                初始值为 0
```

### 4.5 方法和生命周期方法

```ts
aboutToDisappear() {
  clearInterval(this.timerId)
}
```

这是一个方法。`aboutToDisappear` 还是本项目词典中认识的生命周期名称。

可以先理解为：组件即将消失时，框架会在合适的时机调用它。这里用它清理定时器。

```text
this.timerId
  this     当前 PhotoWall 实例
  timerId  当前实例的 timerId 字段
```

### 4.6 async 和 await

```ts
async loadImages() {
  const pixelMap = await img.createPixelMap(buffer)
}
```

含义：

```text
async    这个方法包含异步流程
await    等待异步操作完成后再继续执行
const    pixelMap 变量不会重新赋值
```

Parser 不判断异步写法是否正确，只记录：

```text
async_fn
await_expr
```

Tagger 再将这些事实转换成 `has_async`。

### 4.7 方法调用和链式调用

```ts
this.photos.push(pixelMap)
router.pushUrl({ url: 'pages/Detail' })
```

基本形式：

```text
对象.方法(参数)
```

在当前 L0 Parser 输出中，这两行会出现：

```text
router.pushUrl
```

`this.photos.push` 是内部对象调用，不属于平台 API。L0 和 L1 现在共用 receiver
ownership 规则，只保留冻结全局调用或能够追溯到 SDK import 根绑定的调用。

### 4.8 箭头函数和回调

```ts
setInterval(() => {
  router.pushUrl({ url: 'pages/Detail' })
}, 1000)
```

`() => { ... }` 是匿名箭头函数。这里表示每次定时器触发时执行这段回调。

```text
第一个参数  回调函数
第二个参数  1000 毫秒
```

Parser 会记录语法事实：

```text
arrow_fn
```

### 4.9 build() 和声明式 UI

```ts
build() {
  Column() {
    Grid() {
      // 子 UI
    }
  }
}
```

`build()` 描述组件需要渲染的 UI。当前 Parser 会专门把它标记为：

```text
build_method
```

而 `Column`、`Grid` 这类带 UI 内容块的结构会被标记为：

```text
ui_block
```

### 4.10 ForEach 列表渲染

```ts
ForEach(this.photos, (photo: PixelMap) => {
  Image(photo)
})
```

可以读成：

```text
遍历 this.photos
每次取出一个 PixelMap，命名为 photo
为每个 photo 创建一个 Image
```

它类似下面的伪代码：

```text
for photo in this.photos:
    render Image(photo)
```

### 4.11 ArkUI 属性和事件

```ts
Image(photo)
  .objectFit(ImageFit.Cover)
  .onClick(() => this.loadImages())
```

`Image(photo)` 创建图片组件，后面的链式调用配置它：

```text
objectFit  图片如何适配显示区域
onClick    点击事件
```

本项目把这些链式配置统一放入 `attributes` 字段。

这里的 `attribute` 是项目内部命名，既包括视觉属性，也包括事件回调和其他 ArkUI modifier。

---

## 5. Parser 核心产物 CodeFacts

`CodeFacts` 是 Parser 对一个文件的结构化描述：

```python
class CodeFacts:
    path: str
    imports: list[ImportInfo]
    components: set[str]
    apis: set[str]
    decorators: set[str]
    attributes: set[str]
    symbols: set[str]
    syntax: set[str]
    declarations: list[Declaration]
    parser_layer: "L0" | "L1" | "parse_degraded"
    warnings: list[str]
```

以下逐字段解释。

### 5.1 path

```json
{
  "path": "src/pages/PhotoWall.ets"
}
```

含义：当前事实来自哪个文件。

它不参与 ArkTS 语法判断，但会用于：

```text
生成 unit_ref
在报告中定位文件
把 Finding 回写到正确文件
区分不同文件中的同名组件和方法
```

### 5.2 imports

真实输出：

```json
{
  "imports": [
    {
      "module": "@ohos.multimedia.image",
      "default_name": "img",
      "namespace_name": null,
      "named": {}
    },
    {
      "module": "@ohos.router",
      "default_name": "router",
      "namespace_name": null,
      "named": {}
    }
  ]
}
```

`ImportInfo` 的字段：

| 字段 | 含义 | 示例 |
|---|---|---|
| `module` | 模块真实名称 | `@ohos.router` |
| `default_name` | 默认导入在本文件中的名字 | `router` |
| `namespace_name` | `import * as xxx` 中的 `xxx` | `http` |
| `named` | 命名导入的本地名到原始名映射 | `{"win": "window"}` |

命名导入例子：

```ts
import { window as win, display } from '@kit.ArkUI'
```

概念上对应：

```json
{
  "module": "@kit.ArkUI",
  "default_name": null,
  "namespace_name": null,
  "named": {
    "win": "window",
    "display": "display"
  }
}
```

用途：把局部别名调用统一成可检索的 API 名称。

### 5.3 components

真实 L0 输出：

```json
{
  "components": [
    "Column",
    "ForEach",
    "Grid",
    "Image"
  ]
}
```

含义：Parser 认为代码中出现了哪些 ArkUI 组件或 UI 构造结构。

来源：

```ts
Column()    // -> Column
Grid()      // -> Grid
ForEach()   // -> ForEach
Image()     // -> Image
```

当前字段是一个集合，所以只回答：

```text
有没有 Image
```

它不能回答：

```text
有几个 Image
Image 在第几行
Image 属于哪个 ReviewUnit
```

这也是当前系统需要对 ReviewUnit 二次 Parser 的根本原因之一。

### 5.4 apis

真实 L0 输出：

```json
{
  "apis": [
    "clearInterval",
    "image.createPixelMap",
    "router.pushUrl",
    "setInterval"
  ]
}
```

含义：Parser 识别出的函数或方法调用。

逐项来源：

| API 结果 | 源代码 | 含义 |
|---|---|---|
| `clearInterval` | `clearInterval(this.timerId)` | 清理定时器 |
| `image.createPixelMap` | `img.createPixelMap(buffer)` | 图片模块 API，已规范化别名 |
| `router.pushUrl` | `router.pushUrl(...)` | 页面跳转 |
| `setInterval` | `setInterval(...)` | 创建周期定时器 |

注意：`photos.push`、`this.loadImages`、`console.log` 和相对路径工程 import
上的方法不会进入 `apis`。这能避免把业务调用误当成平台 API。

### 5.5 decorators

真实输出：

```json
{
  "decorators": [
    "@Component",
    "@Entry",
    "@State"
  ]
}
```

来源：

```ts
@Entry
@Component
@State photos: PixelMap[] = []
```

用途：

```text
识别组件身份
识别状态管理方式
触发 has_state_management
为 HostSummary 提供宿主背景
```

当前字段只保存装饰器名称，不保存装饰器具体属于哪个字段或 struct。

### 5.6 attributes

真实输出：

```json
{
  "attributes": [
    "objectFit",
    "onClick"
  ]
}
```

来源：

```ts
Image(photo)
  .objectFit(ImageFit.Cover)
  .onClick(() => this.loadImages())
```

在本项目中，`attributes` 包含：

```text
布局和样式 modifier，例如 width、height、padding
事件，例如 onClick、onChange、onTouch
可访问性属性，例如 accessibilityText
动画和过渡属性，例如 animation、transition
```

它不是普通对象所有属性的完整集合，只记录词典中关心的 ArkUI modifier 和 `onXxx` 事件。

### 5.7 symbols

真实 L0 输出的一部分：

```json
{
  "symbols": [
    "PhotoWall",
    "PhotoWall.aboutToDisappear",
    "PhotoWall.loadImages",
    "PhotoWall.build",
    "PhotoWall.build.Column",
    "PhotoWall.build.Grid",
    "aboutToDisappear",
    "loadImages",
    "build"
  ]
}
```

`symbols` 是一个比 API 更宽泛的名称索引，目前主要包含声明名称和限定名称。

用途示例：

```text
symbols 中存在 aboutToDisappear
-> Tagger 识别 has_lifecycle

symbols 中存在 ThreadWorker
-> Tagger 识别 has_worker
```

不要把 `symbols` 理解成完整编译器符号表。它目前没有类型、定义引用关系和作用域解析。

### 5.8 syntax

真实输出：

```json
{
  "syntax": [
    "arrow_fn",
    "async_fn",
    "await_expr"
  ]
}
```

字段值与语法对应关系：

| syntax | 对应代码 | 含义 |
|---|---|---|
| `async_fn` | `async loadImages()` | 异步函数或方法 |
| `await_expr` | `await img.createPixelMap(...)` | 等待异步结果 |
| `promise` | `Promise<T>` | 使用 Promise |
| `arrow_fn` | `() => { ... }` | 箭头函数 |
| `try_catch` | `try { ... } catch (...) { ... }` | 异常捕获结构 |

当前 `has_async` 由下面任意事实触发：

```text
async_fn
await_expr
promise
```

`arrow_fn` 本身不会触发 `has_async`，因为箭头函数也可以是同步函数。

### 5.9 declarations

`declarations` 是 ReviewUnit 最依赖的字段。它描述代码中每个重要结构的类型、名称、范围和原文。

示例：

```json
{
  "kind": "method",
  "name": "loadImages",
  "qualified_name": "PhotoWall.loadImages",
  "span": {
    "start_line": 14,
    "end_line": 20,
    "start_col": 3,
    "end_col": 3
  },
  "parent_name": "PhotoWall",
  "text": "async loadImages() { ... }"
}
```

#### kind

当前支持 7 种：

| kind | 含义 | 示例 |
|---|---|---|
| `struct` | ArkUI struct 或结构声明 | `struct PhotoWall` |
| `class` | class 声明 | `class ImageService` |
| `function` | 顶层函数 | `function formatName()` |
| `method` | struct/class 内普通方法 | `loadImages()` |
| `build_method` | ArkUI 的 `build()` 方法 | `build()` |
| `builder` | 带 `@Builder` 的构建函数 | `@Builder itemBuilder()` |
| `ui_block` | 有 UI 内容块的组件表达式 | `Column() { ... }` |

#### name

当前声明自己的简单名称：

```text
loadImages
Column
PhotoWall
```

#### qualified_name

带父级路径的名称：

```text
PhotoWall.loadImages
PhotoWall.build
PhotoWall.build.Column
```

它比简单名称更容易区分不同宿主下的同名方法，但当前还不能区分同一父级下两个同名 `Column`。

#### span

声明在源文件中的位置：

```text
start_line  起始行
end_line    结束行
start_col   起始列
end_col     结束列
```

ReviewUnit 使用它判断第 17 行改动属于哪个方法或 UI 块。

#### parent_name

直接父级声明：

```text
PhotoWall.loadImages 的 parent_name = PhotoWall
PhotoWall.build.Column 的 parent_name = PhotoWall.build
```

#### text

该声明对应的原始代码文本。ReviewUnit 当前直接使用它作为 `full_text`。

### 5.10 parser_layer

```json
{
  "parser_layer": "L0"
}
```

三个可能值：

| 值 | 含义 |
|---|---|
| `L0` | 只得到 Python 词法 Parser 结果 |
| `L1` | Node tree-sitter-arkts 成功，结果已合并 |
| `parse_degraded` | L1 本来可调用但执行失败，退回 L0 |

本文实际输出是 `L0`，因为当前环境没有安装 sidecar 的 npm 依赖。

### 5.11 warnings

```json
{
  "warnings": []
}
```

用于记录解析质量和降级情况，例如：

```text
arkts_tree_sitter_unavailable
arkts_tree_sitter_failed
arkts_tree_sitter_error_nodes: 3
arkts_tree_sitter_missing_nodes: 1
```

`warnings` 不等于代码质量问题，而是告诉后续模块：Parser 结果可能不完整或不可靠。

---

## 6. L0 和 L1 输出有什么区别

### L0

```text
技术：Python 正则、注释字符串屏蔽、大括号匹配
优势：不需要 Node，部署简单，失败概率低
缺点：不真正理解 AST，可能出现内部 API 误提和边界误判
```

L0 仍不理解完整 AST 和词法作用域，但 API 字段已经使用与 L1 相同的 SDK
receiver-binding 白名单；它的主要差距集中在 UI、attribute 和 declaration 边界。

### L1

```text
技术：Node sidecar + tree-sitter-arkts
优势：理解 AST 节点、父子关系和更准确的声明边界
缺点：依赖 Node/npm，多一次进程调用和故障点
```

L1 会把 AST 压缩成 snapshot JSON，再由 Python 合并到 L0 `CodeFacts`。

---

## 7. 24 个 Tags 逐项解释

Tagger 输入 `CodeFacts`，输出一个字符串集合。

Tag 只表示代码场景，不表示已经发现问题。

### 7.1 资源类 Tags

| Tag | 当前触发条件 | ArkTS 示例 | 为什么需要关注 | 当前新增维度 |
|---|---|---|---|---|
| `has_image` | `Image/ImageSpan/ImageAnimator` 或 `image.*` | `Image(photo)` | 图片解码、内存和释放 | DIM-06 |
| `has_timer` | `setInterval/setTimeout/systemTimer.setInterval` | `setInterval(fn, 1000)` | 重复创建、生命周期清理 | DIM-06 |
| `has_subscription` | `emitter./sensor.` 或 API 以 `.on/.off/.once` 结尾 | `emitter.on(...)` | 注册与解注册配对 | DIM-06 |
| `has_media` | `media./audio./camera.` 或 `Video/XComponent` | `Video({ src })` | 媒体资源和生命周期 | DIM-06 |
| `has_file_io` | `fs.` 或 `fileIo.` | `fs.open(path)` | 文件句柄、异常和关闭 | DIM-06 |

### 7.2 并发和异步 Tags

| Tag | 当前触发条件 | ArkTS 示例 | 为什么需要关注 | 当前新增维度 |
|---|---|---|---|---|
| `has_async` | `async_fn/await_expr/promise` | `await http.request(...)` | 异常处理、并发顺序 | DIM-07 |
| `has_taskpool` | `taskpool.*` | `taskpool.execute(task)` | 任务调度和可传输对象 | DIM-07 |
| `has_worker` | `worker.*` 或 `ThreadWorker` | `new worker.ThreadWorker(...)` | 线程通信和资源释放 | DIM-07 |

### 7.3 UI 和体验 Tags

| Tag | 当前触发条件 | ArkTS 示例 | 为什么需要关注 | 当前新增维度 |
|---|---|---|---|---|
| `has_interactive_component` | Button 等交互组件，或任意 `onXxx` attribute | `.onClick(...)` | 无障碍、交互反馈 | DIM-08 |
| `has_layout` | Column/Row/Flex/Grid/GridRow/GridCol/Stack/RelativeContainer | `Column() { ... }` | 布局层级和设备适配 | DIM-09 |
| `has_responsive_api` | `mediaquery./display.` 或 GridRow/GridCol | `GridRow() { ... }` | 屏幕尺寸和多设备适配 | DIM-09 |
| `has_text_display` | Text/TextInput/TextArea/Search 或 placeholder | `Text('Hello')` | 国际化和硬编码文本 | DIM-10 |
| `has_resource_ref` | `$r` 或 `$rawfile` | `$r('app.string.title')` | 资源引用和国际化 | DIM-10 |

### 7.4 安全和数据 Tags

| Tag | 当前触发条件 | ArkTS 示例 | 为什么需要关注 | 当前新增维度 |
|---|---|---|---|---|
| `has_permission_request` | `requestPermissionsFromUser` 或 `abilityAccessCtrl.*` | `requestPermissionsFromUser(...)` | 最小权限和授权时机 | DIM-11 |
| `has_user_input` | TextInput/TextArea/Search | `TextInput({ text })` | 输入校验和敏感数据 | DIM-11 |
| `has_network` | `http./socket./rcp.` | `http.request(url)` | 明文、超时、敏感数据 | DIM-11 |
| `has_storage` | `preferences./relationalStore.` | `preferences.put(...)` | 数据持久化和隐私 | DIM-11 |

### 7.5 ArkTS 和 ArkUI 结构 Tags

下面这些 Tag 当前不会额外新增条件维度，因为相关核心维度或 DIM-12 本来就会触发。但它们仍可用于精确检索和 Prompt 检查项。

| Tag | 当前触发条件 | ArkTS 示例 | 主要评审关注 |
|---|---|---|---|
| `has_state_management` | 存在 `@State/@Link/@Prop/...` | `@State count: number = 0` | DIM-02 状态管理语义 |
| `has_lifecycle` | 存在已知生命周期方法名 | `aboutToDisappear()` | DIM-02/DIM-05 生命周期行为 |
| `has_list_render` | List/Grid/WaterFlow 或 ForEach/LazyForEach/Repeat | `ForEach(items, ...)` | DIM-03 列表性能和稳定键 |
| `has_animation` | `animateTo` 或 `transition` | `.transition(...)` | DIM-03 动画性能 |
| `has_builder` | `@Builder/@BuilderParam` | `@Builder itemBuilder()` | DIM-02/DIM-04 Builder 使用 |
| `has_navigation` | Navigation/NavDestination 或 `router.*` | `router.pushUrl(...)` | DIM-05 路由健壮性 |
| `has_logging` | `hilog.*` | `hilog.info(...)` | DIM-12 日志和 DFX |

---

## 8. 12 个 Dimensions 逐项解释

Dimensions 是评审方向，不是 Parser 字段。

### 8.1 永远触发的核心维度

| ID | 名称 | 通俗理解 |
|---|---|---|
| DIM-01 | 规范符合度 | 是否违反明确的 ArkTS、ArkUI 或团队规范 |
| DIM-02 | ArkTS 语言特性 | 装饰器、状态管理、生命周期和语言限制是否正确 |
| DIM-03 | 性能 | 是否有多余渲染、重计算、资源和列表性能问题 |
| DIM-04 | 可维护性 | 代码是否过大、重复、难读或职责混乱 |
| DIM-05 | 健壮性 | 异常、空值、失败路径和边界是否处理 |

### 8.2 条件触发和固定低权重维度

| ID | 名称 | 主要由哪些 Tags 触发 |
|---|---|---|
| DIM-06 | 资源与内存管理 | image/timer/subscription/media/file_io |
| DIM-07 | 并发与异步 | async/taskpool/worker |
| DIM-08 | 无障碍 | interactive_component |
| DIM-09 | 多设备适配 | layout/responsive_api |
| DIM-10 | 国际化 | text_display/resource_ref |
| DIM-11 | 安全 | permission/user_input/network/storage |
| DIM-12 | DFX 与可测性 | 当前总是触发 |

---

## 9. 用真实样例完整推导 Tags 和 Dimensions

### 9.1 Parser Facts

```text
components:
  Column, ForEach, Grid, Image

apis:
  clearInterval, image.createPixelMap, router.pushUrl, setInterval

decorators:
  @Component, @Entry, @State

attributes:
  objectFit, onClick

syntax:
  arrow_fn, async_fn, await_expr

symbols:
  包含 aboutToDisappear
```

### 9.2 Facts 到 Tags

```text
Image
-> has_image

setInterval
-> has_timer

async_fn / await_expr
-> has_async

onClick
-> has_interactive_component

Column / Grid
-> has_layout

aboutToDisappear
-> has_lifecycle

Grid / ForEach
-> has_list_render

router.pushUrl
-> has_navigation

@State
-> has_state_management
```

最终真实 Tags：

```json
[
  "has_async",
  "has_image",
  "has_interactive_component",
  "has_layout",
  "has_lifecycle",
  "has_list_render",
  "has_navigation",
  "has_state_management",
  "has_timer"
]
```

### 9.3 Tags 到 Dimensions

```text
始终加入：
  DIM-01, DIM-02, DIM-03, DIM-04, DIM-05, DIM-12

has_image / has_timer：
  加入 DIM-06

has_async：
  加入 DIM-07

has_interactive_component：
  加入 DIM-08

has_layout：
  加入 DIM-09
```

最终真实 Dimensions：

```json
[
  "DIM-01",
  "DIM-02",
  "DIM-03",
  "DIM-04",
  "DIM-05",
  "DIM-06",
  "DIM-07",
  "DIM-08",
  "DIM-09",
  "DIM-12"
]
```

没有触发：

```text
DIM-10：样例没有 Text、TextInput、$r 等文本资源特征
DIM-11：样例没有权限、用户输入、网络或存储特征
```

`router.pushUrl` 是导航 API，不属于当前 `has_network` 的 `http/socket/rcp` 网络数据场景。

---

## 10. HostSummary 不是 Parser 原始字段

`HostSummary` 由 `ReviewUnitBuilder` 使用完整文件的 `CodeFacts` 和宿主声明生成。

模型：

```python
class HostSummary:
    struct: str | None
    decorators: list[str]
    states: list[str]
    lifecycle: list[str]
    imports: list[str]
```

如果 ReviewUnit 是 `PhotoWall.loadImages`，HostSummary 大致是：

```json
{
  "struct": "PhotoWall",
  "decorators": ["@Component", "@Entry"],
  "states": ["@State photos: PixelMap[] = []"],
  "lifecycle": ["aboutToDisappear"],
  "imports": ["@ohos.multimedia.image", "@ohos.router"]
}
```

它解决的问题：

```text
ReviewUnit 只有 loadImages() 方法正文
但评审模型还需要知道它属于哪个组件、有哪些状态和生命周期
```

它的限制：

```text
lifecycle 中只有方法名，没有方法正文
decorators 和 lifecycle 当前可能受同文件其他 struct 污染
它目前还没有进入 RetrievalUnit
```

---

## 11. 不要混淆的下游数据模型

### 11.1 CodeFeatures

从 Unit 级 `CodeFacts` 精简而来：

```python
class CodeFeatures:
    components: list[str]
    decorators: list[str]
    apis: list[str]
    tags: list[str]
```

当前没有包含 `attributes`，这是已知待对齐项。

### 11.2 RetrievalUnit

给未来检索模块的一条 Unit 级查询原料：

```python
class RetrievalUnit:
    unit_ref: str
    code_features: CodeFeatures
    intent_summary: str
```

### 11.3 MrContext

当前保存整个分析请求的维度并集和预算：

```python
class MrContext:
    triggered_dimensions: list[str]
    token_budget: int
```

当前维度是所有 Unit Tags 的并集。未来如果按 Unit 评审，建议同时保存 Unit 级 Dimensions，避免一个 Unit 的网络特征污染其他 Unit。

---

## 12. 阅读 Parser JSON 的固定顺序

拿到一份 Parser 结果时，按下面顺序阅读：

```text
第一步：parser_layer + warnings
  先判断结果是否可靠、是否发生降级

第二步：declarations
  看 Parser 是否正确理解 struct、方法、build 和 UI 边界

第三步：imports + apis
  看外部 API 是否正确规范化，是否混入内部调用

第四步：components + attributes
  看 UI 组件、布局、事件和 modifier 是否正确

第五步：decorators + syntax + symbols
  看状态、生命周期和异步语法是否正确

第六步：tags
  检查底层 Facts 是否正确映射为场景

第七步：dimensions
  检查评审方向是否符合 Tags
```

---

## 13. 常见问题

### Q1：出现 `has_timer` 就一定有定时器泄漏吗？

不是。它只表示代码使用了定时器。是否泄漏要结合创建位置、清理代码和生命周期判断。

### Q2：为什么 `Image` 同时会影响 Parser、Tag 和 Dimension？

```text
Parser：components 中记录 Image
Tagger：生成 has_image
Dimension：has_image 触发 DIM-06
Retrieval：检索图片资源相关规范
LLM：最后判断是否真的存在释放或内存问题
```

### Q3：为什么 Parser 不直接输出“图片没有释放”？

因为 Parser 只提取客观事实。图片是否需要释放、由谁持有、在哪个生命周期释放，需要规则或语义判断。

### Q4：`components` 和 `ui_block` 有什么区别？

`components` 是去重后的组件名称集合；`ui_block` 是带源码范围、父级和代码原文的声明对象。

### Q5：`attributes` 为什么包含 `onClick`？

这是项目内部对 ArkUI 链式 modifier 的统一命名。它不仅表示视觉属性，也表示事件和可访问性配置。

### Q6：Tags 为什么不是直接从源代码正则生成？

因为 Parser 已经完成 import 规范化、注释屏蔽和结构识别。Tagger 只依赖统一的 `CodeFacts`，可以减少重复解析逻辑。

### Q7：为什么同一个 Tag 可能不新增 Dimension？

因为 DIM-01 到 DIM-05 和 DIM-12 本来就始终触发。例如 `has_state_management` 仍可用于精确检索状态管理文档，但不需要再次添加已经存在的 DIM-02。

### Q8：Parser 结果中为什么没有每个 API 的行号？

当前 `apis/components/decorators` 是集合模型，没有 occurrence span。这是当前架构的重要限制，也是后续建议改造成带位置事实的原因。

---

## 14. 最终速查表

```text
path          文件位置
imports       导入了哪些模块，局部别名是什么
components    出现了哪些 ArkUI 组件
apis          调用了哪些函数和方法
decorators    出现了哪些 @ 装饰器
attributes    使用了哪些 ArkUI modifier 和事件
symbols       Parser 记录的声明和特殊名称索引
syntax        async/await/arrow/try-catch 等语法事实
declarations  struct、方法、build、UI 块的边界和原文
parser_layer  当前使用 L0、L1 还是发生降级
warnings      Parser 自身的质量告警

Tags          把底层事实翻译成代码场景
Dimensions    把场景翻译成评审方向
HostSummary   给局部 ReviewUnit 补充宿主背景
Evidence      检索出的知识依据
Finding       最终确认的问题和修改建议
```

最核心的一句话：

```text
Parser 负责把 ArkTS 代码翻译成事实；
Tags 负责把事实翻译成场景；
Dimensions 负责把场景翻译成评审角度；
LLM 才负责根据代码和依据判断是否真的有问题。
```
