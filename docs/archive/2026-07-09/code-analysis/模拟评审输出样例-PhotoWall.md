# 模拟评审输出样例 — PhotoWall MR（开会演示用）

- 用途: 与 mentor 对齐"第一版评审输出长什么样"时的具体讨论标的
- 生成日期: 2026-07-09
- 诚实声明: 本样例分五个阶段，**阶段 1 是当前代码真实跑出来的产物**（parser + ReviewUnit + Tagger，L1 tree-sitter-arkts 成功解析）；**阶段 2~5 是按已定契约手工模拟的**（检索、规则层、LLM 终审尚未实现）。每节均有标注。

---

## 0. 演示场景

某同事提交 MR：给相册应用新增"照片墙"页面，改动集中在 `src/pages/PhotoWall.ets`，
3 个 hunk（新增 `aboutToAppear` 生命周期、新增 `loadPhotos` 网络加载、修改 `build` 中 Image 点击逻辑）。

源码（46 行，内含 6 处典型问题，覆盖资源泄漏 / 语言规范 / 健壮性 / 国际化）：

```typescript
import { BusinessError } from '@kit.BasicServicesKit';
import { emitter } from '@kit.BasicServicesKit';
import { http } from '@kit.NetworkKit';

@Entry
@Component
struct PhotoWall {
  @State photoUrls: string[] = [];
  @State refreshCount: number = 0;
  private timerId: number = -1;

  aboutToAppear(): void {                    // hunk 1: L12-L20（新增）
    emitter.on({ eventId: 1001 }, (data) => {
      this.refreshCount += 1;
    });
    this.timerId = setInterval(() => {
      this.loadPhotos();
    }, 30000);
    this.loadPhotos();
  }

  async loadPhotos() {                       // hunk 2: L22-L26（新增）
    let httpRequest = http.createHttp();
    let resp: any = await httpRequest.request('https://example.com/api/photos');
    this.photoUrls = JSON.parse(resp.result as string);
  }

  build() {
    Column() {
      Text('照片墙 (' + this.refreshCount + ')')
        .fontSize(20)
        .margin(10)
      Grid() {
        ForEach(this.photoUrls, (url: string) => {
          GridItem() {
            Image(url)
              .width('100%')
              .height(120)
              .onClick(() => {               // hunk 3: L38-L40（修改）
                this.refreshCount += 1;
              })
          }
        })
      }
      .columnsTemplate('1fr 1fr 1fr')
    }
  }
}
```

---

## 1. 代码分析产物 ✅ 真实输出

> 由 `arkts_code_reviewer.code_analysis.cli` 实际运行产生（L1 = tree-sitter-arkts
> sidecar 解析成功，零 warning）。单文件分析耗时 < 1 秒。

3 个 hunk → 3 个 Review Unit，每个 Unit 带独立特征与 tags：

```jsonc
{
  "retrieval_query": {
    "mr_context": {
      "triggered_dimensions": ["DIM-01","DIM-02","DIM-03","DIM-04","DIM-05",
                               "DIM-06","DIM-07","DIM-08","DIM-09","DIM-10","DIM-11","DIM-12"],
      "token_budget": 8000        // 透传自编排配置，本模块不决策
    },
    "units": [
      {
        "unit_ref": "PhotoWall.aboutToAppear@src/pages/PhotoWall.ets",
        "code_features": {
          "apis": ["emitter.on", "setInterval"],
          "tags": ["has_lifecycle", "has_subscription", "has_timer"]
        },
        "intent_summary": "apis: emitter.on, setInterval; tags: has_lifecycle, has_subscription, has_timer"
      },
      {
        "unit_ref": "PhotoWall.loadPhotos@src/pages/PhotoWall.ets",
        "code_features": {
          "apis": ["JSON.parse", "http.createHttp", "httpRequest.request"],
          "tags": ["has_async", "has_network"]
        },
        "intent_summary": "apis: JSON.parse, http.createHttp, httpRequest.request; tags: has_async, has_network"
      },
      {
        "unit_ref": "PhotoWall.build@src/pages/PhotoWall.ets",
        "code_features": {
          "components": ["Column","ForEach","Grid","GridItem","Image","Text"],
          "tags": ["has_image","has_interactive_component","has_layout","has_list_render","has_text_display"]
        },
        "intent_summary": "components: Column, ForEach, Grid, GridItem, Image; tags: has_image, ..."
      }
    ]
  },
  "metadata": { "parser_layer": "L1", "warnings": [] }
}
```

每个 ReviewUnit 同时携带（供最终 Prompt 使用，不发给检索）：

```jsonc
{
  "unit_ref": "PhotoWall.aboutToAppear@src/pages/PhotoWall.ets",
  "full_text": "  aboutToAppear(): void {\n    emitter.on({ eventId: 1001 }, ...）",   // 完整方法源码
  "unit_changed_lines": [1,2,3,4,5,6,7,8,9],
  "host_summary": {
    "struct": "PhotoWall",
    "decorators": ["@Component", "@Entry"],
    "states": ["@State photoUrls: string[] = []", "@State refreshCount: number = 0"],
    "lifecycle": ["aboutToAppear"],
    "imports": ["@kit.BasicServicesKit", "@kit.NetworkKit"]
  }
}
```

**给 mentor 看的重点**：host_summary 里 `lifecycle` 只有 `aboutToAppear` 没有
`aboutToDisappear` —— 这个"缺失"本身就是资源泄漏问题的关键证据，检索和 LLM
都能利用。

---

## 2. Evidence Pack 🎭 模拟（格式忠实于 retrieval.md §1.3 已定契约）

> 检索模块未实现。以下条款按 arkui-specs 真实条款体系（R/AC 为主）模拟，
> rule_id / source_path 为虚构示意。每 Unit 3~5 条、按 unit_ref 回挂。

```jsonc
{
  "index_version": "idx-2026-07-09-001",
  "units": [
    {
      "unit_ref": "PhotoWall.aboutToAppear@src/pages/PhotoWall.ets",
      "clauses": [
        {
          "rule_id": "02-03-01/Feat-02/R-08",
          "rule_type": "RULE", "status": "Baselined", "dimension": "DIM-06",
          "text": "使用 emitter.on / sensor.on 等订阅型 API 时，必须在组件 aboutToDisappear 或对应销毁时机调用配对的 off 接口注销订阅，避免回调持有组件引用导致内存泄漏。",
          "heading_path": "Feat-02 事件订阅管理 > 功能规则",
          "score": 0.91, "matched_by": ["keyword:emitter.on", "tag:has_subscription"]
        },
        {
          "rule_id": "02-03-01/Feat-02/R-11",
          "rule_type": "RULE", "status": "Baselined", "dimension": "DIM-06",
          "text": "setInterval 创建的定时器必须保存句柄并在页面/组件销毁时 clearInterval；周期性任务应评估是否随页面不可见（onPageHide）暂停。",
          "heading_path": "Feat-02 事件订阅管理 > 功能规则",
          "score": 0.88, "matched_by": ["keyword:setInterval", "tag:has_timer"]
        }
      ]
    },
    {
      "unit_ref": "PhotoWall.loadPhotos@src/pages/PhotoWall.ets",
      "clauses": [
        {
          "rule_id": "01-01-02/Feat-01/R-03",
          "rule_type": "RULE", "status": "Baselined", "dimension": "DIM-02",
          "text": "ArkTS 禁止使用 any 类型（arkts-no-any）。网络响应等外部数据应定义 interface 并显式收窄类型。",
          "heading_path": "Feat-01 ArkTS 语言约束 > 功能规则",
          "score": 0.93, "matched_by": ["keyword:any", "vector"]
        },
        {
          "rule_id": "05-02-01/Feat-03/AC-2.1",
          "rule_type": "AC", "status": "Baselined", "dimension": "DIM-05",
          "text": "发起 HTTP 请求的异步函数，当网络不可达或响应非 2xx 时，应用不崩溃且用户可感知失败状态（空态/重试提示）。",
          "heading_path": "Feat-03 网络请求 > 验收准则",
          "score": 0.85, "matched_by": ["tag:has_network", "vector"]
        },
        {
          "rule_id": "05-02-01/Feat-03/R-07",
          "rule_type": "RULE", "status": "Baselined", "dimension": "DIM-06",
          "text": "http.createHttp() 创建的 HttpRequest 实例使用完毕后必须调用 destroy() 释放。",
          "heading_path": "Feat-03 网络请求 > 功能规则",
          "score": 0.82, "matched_by": ["keyword:http.createHttp"]
        }
      ]
    },
    {
      "unit_ref": "PhotoWall.build@src/pages/PhotoWall.ets",
      "clauses": [
        {
          "rule_id": "04-01-01/Feat-01/R-17",
          "rule_type": "RULE", "status": "Baselined", "dimension": "DIM-06",
          "text": "网络图片加载应处理失败场景：Image 组件应设置 onError 回调或 alt 占位图。",
          "heading_path": "Feat-01 图片加载机制 > 功能规则",
          "score": 0.86, "matched_by": ["keyword:Image", "tag:has_image"]
        },
        {
          "rule_id": "03-01-04/Feat-02/R-05",
          "rule_type": "RULE", "status": "Baselined", "dimension": "DIM-10",
          "text": "用户可见文本禁止硬编码，应使用 $r 资源引用以支持多语言。",
          "heading_path": "Feat-02 资源与国际化 > 功能规则",
          "score": 0.71, "matched_by": ["tag:has_text_display", "vector"]
        }
      ]
    }
  ]
}
```

---

## 3. 确定性规则层命中 🎭 模拟

> 规则层未实现（**待对齐：部门 CI 是否已跑 CodeLinter/HomeCheck，决定此层做不做**）。
> 此类问题特点：零 LLM 参与、零误报、可直接给 fix。

| rule_id | 位置 | 命中 | 置信 |
|---|---|---|---|
| arkts-no-any | L24 | `let resp: any` 违反 ArkTS 强类型约束 | 确定 |
| unused-import | L1 | `BusinessError` 导入未使用 | 确定 |

---

## 4. Final LLM Reviewer 输出 🎭 模拟（字段按 Mentor 清单 §9.2）

```jsonc
{
  "mr": "album-app!247",
  "model": "deepseek-v4",
  "findings": [
    {
      "file": "src/pages/PhotoWall.ets", "line": 13,
      "severity": "high", "category": "resource-leak",
      "problem": "emitter.on 注册的事件订阅没有对应的注销逻辑，组件销毁后回调仍持有 this 引用，造成内存泄漏且 refreshCount 可能在组件销毁后被修改。",
      "evidence": "host_summary.lifecycle 仅有 aboutToAppear，全文件无 aboutToDisappear，无任何 emitter.off 调用。",
      "recommendation": "增加 aboutToDisappear()，其中调用 emitter.off(1001) 注销订阅。",
      "rule_or_doc_reference": "02-03-01/Feat-02/R-08",
      "confidence": "high", "is_diff_related": true
    },
    {
      "file": "src/pages/PhotoWall.ets", "line": 16,
      "severity": "high", "category": "resource-leak",
      "problem": "setInterval 定时器保存了句柄 timerId 但从未 clearInterval，页面退出后每 30 秒仍发起一次网络请求。",
      "evidence": "timerId 仅在 aboutToAppear 中赋值，全文件无 clearInterval。",
      "recommendation": "在 aboutToDisappear() 中 clearInterval(this.timerId)。",
      "rule_or_doc_reference": "02-03-01/Feat-02/R-11",
      "confidence": "high", "is_diff_related": true
    },
    {
      "file": "src/pages/PhotoWall.ets", "line": 24,
      "severity": "critical", "category": "language-spec",
      "problem": "resp 声明为 any，违反 ArkTS 禁用 any 约束（arkts-no-any），无法通过编译期类型检查。",
      "evidence": "let resp: any = await httpRequest.request(...)（规则层确定性命中）。",
      "recommendation": "使用 http.HttpResponse 类型：let resp: http.HttpResponse = await ...。",
      "rule_or_doc_reference": "01-01-02/Feat-01/R-03 + rule:arkts-no-any",
      "confidence": "high", "is_diff_related": true
    },
    {
      "file": "src/pages/PhotoWall.ets", "line": 23,
      "severity": "medium", "category": "robustness",
      "problem": "loadPhotos 无任何错误处理：网络失败时 Promise reject 未捕获；JSON.parse 对非法响应体会抛异常；HttpRequest 实例未 destroy()。",
      "evidence": "async 函数体内无 try/catch，无 .catch()，无 destroy() 调用。",
      "recommendation": "try/catch 包裹请求与解析，catch 中设置空态提示；finally 中 httpRequest.destroy()。",
      "rule_or_doc_reference": "05-02-01/Feat-03/AC-2.1, R-07",
      "confidence": "high", "is_diff_related": true
    },
    {
      "file": "src/pages/PhotoWall.ets", "line": 36,
      "severity": "medium", "category": "robustness",
      "problem": "网络图片 Image(url) 未设置 onError 回调和 alt 占位，加载失败时格子空白。",
      "evidence": "Image 链式调用仅 width/height/onClick。",
      "recommendation": "增加 .alt($r('app.media.placeholder')) 与 .onError() 兜底。",
      "rule_or_doc_reference": "04-01-01/Feat-01/R-17",
      "confidence": "medium", "is_diff_related": true
    },
    {
      "file": "src/pages/PhotoWall.ets", "line": 30,
      "severity": "suggestion", "category": "i18n",
      "problem": "Text 内容硬编码中文'照片墙'，不支持多语言。",
      "evidence": "字符串字面量拼接，未使用 $r 资源引用。",
      "recommendation": "改用 $r('app.string.photo_wall_title')。",
      "rule_or_doc_reference": "03-01-04/Feat-02/R-05",
      "confidence": "high",
      "is_diff_related": false   // ← L30 不在本次 hunk 内，示例"旧代码问题降级为 suggestion"策略
    }
  ],
  "summary": { "critical": 1, "high": 2, "medium": 2, "suggestion": 1 }
}
```

---

## 5. GitCode 评论渲染效果 🎭 模拟

**MR 总评（1 条）：**

> ## 🤖 AI 代码评审 · PhotoWall.ets
> 共发现 **5 个问题 + 1 条建议**（critical 1 / high 2 / medium 2 / suggestion 1）
>
> | 严重级 | 位置 | 问题 | 依据 |
> |---|---|---|---|
> | 🔴 critical | L24 | ArkTS 禁用 `any` | R-03 / arkts-no-any |
> | 🟠 high | L13 | emitter 订阅未注销（内存泄漏） | R-08 |
> | 🟠 high | L16 | 定时器未清理（后台空转请求） | R-11 |
> | 🟡 medium | L23 | 网络请求无错误处理、连接未释放 | AC-2.1 / R-07 |
> | 🟡 medium | L36 | 网络图片无加载失败兜底 | R-17 |
> | 💡 suggestion | L30 | 硬编码文本（非本次改动） | R-05 |
>
> <sub>模型: deepseek-v4 · 索引: idx-2026-07-09-001 · 维度配置: dims-v1 · 本评审仅供参考</sub>

**行内评论示例（挂在 L13）：**

> 🟠 **high · 资源泄漏** — `emitter.on` 注册的订阅没有配对的注销逻辑。
> 组件销毁后回调仍持有 `this`，造成内存泄漏。
>
> 建议增加：
> ```typescript
> aboutToDisappear(): void {
>   emitter.off(1001);
>   clearInterval(this.timerId);
> }
> ```
> 📖 依据: 部门规范 `02-03-01/Feat-02/R-08`《事件订阅管理》

---

## 6. 已知局限（诚实展示，也是待对齐问题）

用同一文件实测发现的真实边界问题：

1. **hunk 横跨多个方法时只选最小 declaration**：把 L12-L26 作为单个 hunk 输入时，
   系统只生成 `loadPhotos` 一个 Unit，`aboutToAppear` 的改动（两个泄漏问题所在）
   被漏掉。真实 MR 的 hunk 常常跨方法 → 需要改为"每个被覆盖的 declaration 各生成
   一个 Unit"。（对应清单 §5 粒度问题）
2. **host_summary 取自全文件而非宿主 struct**：多 struct 文件中会互相污染。（已列入
   Phase 1 修复计划）
3. **suggestion / is_diff_related=false 是否输出**：样例第 6 条即为此场景，请 mentor
   当场表态。（对应清单 §6.3）
4. **模拟部分的成本未验证**：Evidence Pack 条款质量取决于知识库建设，LLM finding
   质量取决于 prompt 工程，两者均未实现，本样例代表的是**目标形态**而非当前能力。

---

## 7. 会上用法建议

拿本样例逐节对着问：

| 样例章节 | 对应清单问题 |
|---|---|
| §4 findings 字段与 severity 分档 | 清单 §9（输出格式） |
| §4 第 6 条 is_diff_related=false | 清单 §6（旧代码边界） |
| §5 评论数量与渲染形态 | 清单 §9.1（行内 vs 报告、数量上限） |
| §2 条款依据 | 清单 §7（哪些 severity 必须有依据）、§11（知识库范围） |
| §3 规则层 | 清单 §8（rules 要不要做 / CI 现状） |
| §6 局限 1 | 清单 §5（ReviewUnit 粒度） |
