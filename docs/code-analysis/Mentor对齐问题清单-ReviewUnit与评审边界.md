---
title: Mentor 对齐问题清单 - ReviewUnit 与评审边界
module: code-analysis
project: arkts-code-reviewer
status: meeting-prep
created: 2026-07-09
updated: 2026-07-09
tags:
  - arkts-code-reviewer
  - code-analysis
  - review-unit
  - mentor-sync
  - ai-code-review
aliases:
  - ReviewUnit Mentor 对齐清单
  - ArkTS 代码评审边界问题
---

# Mentor 对齐问题清单 - ReviewUnit 与评审边界

> [!summary]
> 这份文档用于明天和 mentor 对齐 `arkts-code-reviewer` 后续设计方向。
>
> 当前技术上已经可以继续推进 `ReviewUnit` 模块，但真正影响后续设计的不是代码怎么写，而是：系统到底审什么、什么算好坏、评审边界在哪里、LLM 可以说到什么程度。
>
> 相关文档：[[ReviewUnit模块完整设计]]、[[Parser架构与结果详解]]、[[代码分析模块架构与数据流]]

## 1. 当前背景

当前项目已经初步完成：

```text
parser:
  提取 CodeFacts，包括 components / apis / decorators / attributes / syntax / declarations。

ReviewUnit:
  根据 parser 的 declaration 边界，把源码或 diff 切成适合 LLM 审查的代码单元。

parser-validation:
  原本通过 GLM 做 parser 质检，但当前 API key 暂时没有额度。
```

因此近期计划是：

```text
暂时跳过 GLM parser 质检，
先推进 ReviewUnit 的设计和 deterministic tests。
```

但在正式写 ReviewUnit 之前，需要和 mentor 对齐一些产品/评审层面的决策。

原因：

```text
ReviewUnit 切多大、给 LLM 哪些上下文、是否允许审旧代码、是否需要引用规范，
都取决于最终代码评审系统的目标和边界。
```

## 2. 会议希望得到的核心结论

这次会议最好能定下三件事：

```text
1. 第一版系统主要审什么？
   MR diff、整文件、整项目体检，还是几者都要？

2. 第一版优先审哪些问题？
   编译规范、ArkUI 正确性、健壮性、安全、性能、可维护性等如何排序？

3. LLM 输出边界是什么？
   哪些必须有规范依据，哪些可以作为建议，旧代码问题能不能报？
```

如果能把这三件事定下来，后续 `ReviewUnit`、规则库、RAG、prompt 设计都会清晰很多。

## 3. 待对齐问题一：项目第一版目标

### 3.1 需要问 mentor

```text
第一版到底以什么评审场景为主？
```

可选方向：

| 方向 | 含义 | 对 ReviewUnit 的影响 |
|---|---|---|
| MR diff 增量评审 | 只重点审本次改动 | ReviewUnit 要围绕 hunk 切片，避免跑题 |
| 整文件评审 | 对整个 ArkTS 文件做体检 | ReviewUnit 可以按 struct/class/method 切 |
| 整项目体检 | 扫描整个项目质量 | 需要 code graph / 配置 / 多文件关系，当前阶段较重 |
| 手动单文件分析 | 用户指定一个文件看问题 | 介于整文件和局部审查之间 |

### 3.2 推荐结论

建议第一版优先：

```text
MR diff 增量评审 + 手动单文件分析。
```

原因：

```text
MR diff 是代码评审系统最直接的使用场景。
手动单文件分析便于本地测试和 demo。
整项目体检需要跨文件图谱和更多规则，不适合作为第一阶段主目标。
```

### 3.3 需要会议确认

```text
第一版是否默认只评论本次改动相关问题？
如果发现旧代码问题，是否允许输出？
```

## 4. 待对齐问题二：ArkTS 代码好坏维度

### 4.1 需要问 mentor

```text
我们判断 ArkTS 代码好坏时，第一版应该优先看哪些维度？
```

建议拿下面这张表让 mentor 排优先级。

| 优先级候选 | 维度 | 示例问题 |
|---|---|---|
| P0 | 编译/语言规范阻塞 | `any`、`var`、ArkTS 禁用语法、类型不明确、装饰器误用 |
| P1 | ArkUI 正确性 | `build()` 使用限制、状态装饰器混用、`@Builder/@BuilderParam` 误用 |
| P1 | 健壮性 | async 错误处理、空值边界、异常路径、资源释放 |
| P1 | 安全/隐私/权限 | 权限声明、敏感日志、硬编码 token、网络/存储安全 |
| P2 | 性能 | build 中频繁创建对象、超深组件树、列表渲染、图片资源使用 |
| P2 | 兼容性 | deprecated API、API version、SysCap、`canIUse()` |
| P2 | 可维护性 | 组件过大、函数过长、重复逻辑、状态过多、职责混乱 |
| P3 | 体验类 | 无障碍、国际化、硬编码用户可见文本、深色模式、多设备适配 |

### 4.2 推荐结论

建议第一版优先级：

```text
P0:
  ArkTS 编译/语言规范阻塞。

P1:
  ArkUI 状态管理 / 生命周期 / 资源释放 / 权限与安全。

P2:
  性能和可维护性。

P3:
  无障碍、国际化、多设备适配等体验类建议。
```

### 4.3 需要会议确认

```text
哪些维度是第一版必须做？
哪些维度可以作为后续增强？
哪些维度不希望 LLM 随便提？
```

## 5. 待对齐问题三：ReviewUnit 切分粒度

### 5.1 需要问 mentor

```text
LLM 审代码时应该看多大的上下文？
```

具体问题：

```text
1. MR diff 中，是否只审改动行附近？
2. hunk 在普通 method 中，是否给完整 method？
3. hunk 在短 build() 中，是否给完整 build()？
4. hunk 在超长 build() 中，是给完整 build()，还是最近 UI 子树？
5. hunk 改的是 @State 字段，是否要把引用这个状态的 build/method 也带上？
6. 一个 MR 改多个文件，是按文件独立审，还是按功能聚合审？
```

### 5.2 推荐结论

建议第一版：

```text
普通 method/function:
  给完整 method/function。

短 build():
  给完整 build()。

超长 build():
  给最近 ui_block 子树。

@State / 字段区:
  第一版给 host struct；后续增强字段引用分析。

找不到边界:
  给 hunk 上下文窗口，标记 context_degraded。
```

### 5.3 需要会议确认

```text
mentor 是否接受“宁愿上下文稍大，也不要丢关键上下文”的策略？
超长 build() 局部切分是否符合他们对 ArkUI review 的预期？
```

## 6. 待对齐问题四：旧代码问题是否评论

### 6.1 需要问 mentor

```text
MR review 中，如果 LLM 发现旧代码已有问题，但本次 diff 没有直接改到，要不要评论？
```

选项：

| 策略 | 含义 | 优点 | 风险 |
|---|---|---|---|
| 只评本次改动 | 只报 changed_lines 直接相关问题 | 噪声少，适合 MR review | 可能漏掉被改动触发的上下文问题 |
| 评改动影响范围 | 旧代码如果被本次改动触发，也可以报 | 更合理 | 需要解释和证据 |
| 全文件体检 | 看到什么问题都报 | 覆盖广 | 容易惹 reviewer 反感，跑题 |

### 6.2 推荐结论

建议第一版：

```text
默认只报本次改动引入或直接触发的问题。

旧代码问题只有在满足下面条件时才报：
  1. 和本次改动有直接因果关系；
  2. 不修会影响本次改动行为；
  3. 能给出明确 file/line/evidence。
```

### 6.3 需要会议确认

```text
是否允许输出“非本次改动，但建议关注”的低优先级建议？
```

## 7. 待对齐问题五：评审依据来源

### 7.1 需要问 mentor

```text
什么东西可以作为系统评审依据？
```

候选依据：

```text
1. Huawei / OpenHarmony 官方文档。
2. ArkTS 编程规范。
3. Code Linter / HomeCheck / ArkAnalyzer 规则。
4. 部门内部规范。
5. mentor 或团队经验规则。
6. 历史优秀代码和 bad case。
7. LLM 通用代码质量判断。
```

### 7.2 推荐结论

建议分层：

```text
强结论问题:
  必须有官方文档、内部规范或确定性规则依据。

建议型问题:
  可以基于 LLM 通用代码质量判断，但必须标注为 suggestion。

高风险问题:
  必须给 file/line/evidence/fix，不能只说泛泛而谈。
```

### 7.3 需要会议确认

```text
没有明确规范依据，但 LLM 判断有维护性风险的问题，要不要输出？
如果输出，应该用什么 severity？
```

## 8. 待对齐问题六：确定性规则与 LLM 的边界

### 8.1 需要问 mentor

```text
哪些问题必须由 deterministic rules 检测？
哪些问题可以交给 LLM 判断？
```

建议分工：

| 类型 | 推荐检测方式 |
|---|---|
| `any`、`var`、禁用语法 | deterministic rule |
| 明确装饰器组合错误 | deterministic rule |
| deprecated API | deterministic rule + SDK/文档数据 |
| 权限声明缺失 | deterministic rule + 配置文件分析 |
| 资源释放配对 | rule + LLM 辅助判断 |
| async 错误处理 | LLM + 局部 rule |
| 可维护性 | LLM |
| 业务逻辑边界 | LLM |

### 8.2 推荐结论

建议：

```text
确定性强、规则明确、误报可控的问题，用 rule。
需要理解上下文和意图的问题，用 LLM。
```

### 8.3 需要会议确认

```text
第一版是否需要做 deterministic rules？
还是先只做 parser + RAG + LLM reviewer？
```

## 9. 待对齐问题七：输出格式和严重级

### 9.1 需要问 mentor

```text
最终评审结果应该长什么样？
```

需要确认：

```text
1. 输出 GitCode 行内评论，还是 Markdown 报告？
2. 是否需要 JSON 作为 source of truth？
3. severity 分几档？
4. 每条 finding 必须包含哪些字段？
5. 是否限制每个 MR 的评论数量？
```

### 9.2 推荐输出字段

建议每条 finding 至少包含：

```text
file
line
severity
category
problem
evidence
recommendation
rule_or_doc_reference
confidence
is_diff_related
```

建议 severity：

```text
critical:
  编译失败、安全高风险、明显发布阻塞。

high:
  明确 bug、资源泄漏、权限/隐私风险。

medium:
  健壮性、性能、兼容性问题。

low:
  可维护性、体验类建议。

suggestion:
  无明确规范依据，但可改善代码质量。
```

### 9.3 需要会议确认

```text
是否允许 suggestion？
是否限制低优先级建议数量？
critical/high 是否必须引用规则或文档？
```

## 10. 待对齐问题八：质量评估标准

### 10.1 需要问 mentor

```text
我们如何判断这个 AI code reviewer 做得好？
```

候选指标：

```text
1. 高价值问题发现率。
2. 误报率。
3. 人工 reviewer 接受率。
4. 每个 MR 平均评论数量。
5. parser / ReviewUnit 边界准确率。
6. RAG 命中依据准确率。
7. LLM 输出格式稳定性。
```

### 10.2 推荐第一版验收指标

建议第一版先看：

```text
1. 每条 finding 有明确 file/line/evidence/fix。
2. 高严重问题误报率低。
3. 每个 MR 评论数量可控。
4. 人工 reviewer 不觉得明显跑题。
5. ReviewUnit 边界人工抽检基本合理。
```

### 10.3 需要会议确认

```text
第一版更看重“少误报”还是“多发现问题”？
mentor 能否提供 5-10 个他们认为高质量/低质量的 ArkTS review 示例？
```

## 11. 待对齐问题九：知识库范围

### 11.1 需要问 mentor

```text
第一版 RAG 知识库应该收哪些材料？
```

候选材料：

```text
1. OpenHarmony 官方 ArkTS 文档。
2. Huawei HarmonyOS 官方文档。
3. ArkTS 编码规范。
4. ArkTS 高性能编程实践。
5. Code Linter 规则。
6. HomeCheck / ArkAnalyzer 规则。
7. 部门内部规范。
8. 团队历史 review case。
```

### 11.2 推荐结论

建议第一版：

```text
官方文档 + ArkTS 规范 + 高性能实践 + HomeCheck/CodeLinter 规则分类。
```

部门内部规范如果有，优先级最高。

### 11.3 需要会议确认

```text
有没有内部 ArkTS review 规范？
有没有历史 code review 评论可以作为样例？
这些资料是否允许进入本地知识库？
```

## 12. 待对齐问题十：安全和隐私边界

### 12.1 需要问 mentor

```text
真实部门代码是否允许发送给外部 LLM API？
```

需要确认：

```text
1. PoC 阶段是否只能用开源代码？
2. 内部代码是否需要脱敏？
3. API key 和请求日志如何管理？
4. LLM 原始响应是否可以落盘？
5. 评审记录是否可以长期保存？
```

### 12.2 推荐结论

建议：

```text
PoC 阶段只用开源样本。
内部代码必须走安全审批或本地/内网模型。
请求和响应日志默认不进入 git。
```

## 13. 待对齐问题十一：ReviewUnit 与检索模块契约

### 13.1 为什么要对齐

ReviewUnit 和检索模块的关系可以概括为：

```text
ReviewUnit 决定审哪段代码。
Retrieval 决定给这段代码配哪些评审依据。
```

这两个模块必须用同一个 `unit_ref` 对齐。

否则会出现：

```text
LLM 看到的是 A 代码片段，
RAG 给的是 B 场景文档，
最终 review 就会跑偏。
```

### 13.2 必须对齐的最小契约

建议 `ReviewUnit`、`RetrievalUnit`、`EvidencePack` 三者都围绕 `unit_ref` 绑定：

```text
ReviewUnit.unit_ref
  == RetrievalUnit.unit_ref
  == EvidencePack.units[].unit_ref
```

建议最小输入契约：

```json
{
  "unit_ref": "xxx@file.ets",
  "unit_kind": "method | build_method | ui_block | struct | fallback_window",
  "code_features": {
    "components": [],
    "apis": [],
    "decorators": [],
    "attributes": [],
    "tags": []
  },
  "host_summary": {
    "struct": "",
    "decorators": [],
    "states": [],
    "lifecycle": [],
    "imports": []
  },
  "intent_summary": "",
  "changed_lines": []
}
```

其中需要特别注意：

```text
attributes 建议正式加入 CodeFeatures。
```

原因：

```text
ArkUI 的很多 review 依据和 modifier 强相关：
  onClick
  animation
  rotate
  objectFit
  width / height / margin

如果检索模块看不到 attributes，可能漏掉动画、交互、图片展示、布局相关规则。
```

### 13.3 需要问 mentor

```text
检索应该按 ReviewUnit 单元检索，还是按 MR 全局检索？
```

可选策略：

| 策略 | 含义 | 优点 | 风险 |
|---|---|---|---|
| Unit 级检索 | 每个 ReviewUnit 单独找依据 | 证据和代码片段强绑定 | 多 unit 时检索次数更多 |
| MR 全局检索 | 整个 MR 共用一批依据 | 成本低 | 容易给错上下文 |
| Unit 级为主 + MR 公共依据 | 每个 unit 有专属依据，同时补通用规范 | 平衡准确性和成本 | 实现稍复杂 |

推荐第一版：

```text
Unit 级检索为主，MR 级公共依据为辅。
```

### 13.4 ReviewUnit.full_text 是否参与检索

需要问 mentor 或团队：

```text
检索模块是否应该直接使用 ReviewUnit.full_text？
```

可选策略：

| 策略 | 含义 | 优点 | 风险 |
|---|---|---|---|
| 不用 full_text | 只用 CodeFeatures + tags + intent_summary | 稳定、可控、成本低 | 语义信息较少 |
| 使用 full_text 摘要 | 先生成 intent_summary，再检索 | 语义更丰富 | 需要摘要质量稳定 |
| 直接用 full_text 向量检索 | 把代码原文作为 query | 简单直接 | 长代码噪声大，容易召回跑偏 |

推荐第一版：

```text
不要直接用 full_text 做主检索输入。
使用 CodeFeatures + host_summary + intent_summary。
```

原因：

```text
源码原文主要给最终 LLM reviewer 阅读。
检索模块更适合消费稳定、结构化、低噪声的代码特征。
```

### 13.5 host_summary 是否进入检索

建议进入。

尤其这些字段：

```text
@State / @Local / @Param / @Link
aboutToAppear / aboutToDisappear
imports
@Component / @ComponentV2
```

原因：

```text
这些信息会直接影响状态管理、生命周期、资源释放、组件规范、API 使用规范的检索。
```

需要会议确认：

```text
检索模块是否消费 host_summary？
如果消费，哪些字段进入检索，哪些只进入 final prompt？
```

### 13.6 Evidence Pack 返回格式

检索结果不应该是全局一坨文档，而应该按 `unit_ref` 回挂：

```json
{
  "units": [
    {
      "unit_ref": "AnimationExample@animation.ets",
      "clauses": [
        {
          "evidence_id": "arkui-state-management-001",
          "source_type": "official_doc",
          "dimension": "state_management",
          "title": "ArkUI 状态变量使用规范",
          "text": "...",
          "matched_by": ["decorator:@State", "tag:has_state_management"],
          "source_path": "...",
          "priority": "high"
        }
      ]
    }
  ]
}
```

需要会议确认：

```text
每个 ReviewUnit 默认返回多少条 evidence？
每个维度是否至少返回 1 条？
是否限制总 token budget？
```

推荐第一版：

```text
每个 unit 返回 3-8 条核心依据。
高优先级规则优先。
每个触发维度尽量至少 1 条。
超出 token budget 时，内部规范和官方文档优先保留。
```

### 13.7 检索依据优先级

需要和 mentor 对齐证据优先级。

推荐排序：

```text
1. 部门内部规范
2. 官方 ArkTS / HarmonyOS 文档
3. CodeLinter / HomeCheck / ArkAnalyzer 规则
4. 团队历史 review case
5. 官方/开源示例代码
6. LLM 通用经验
```

需要会议确认：

```text
高严重问题是否必须引用前 3 类依据？
示例代码能否作为强依据，还是只能作为参考写法？
```

### 13.8 旧代码相关依据是否检索

如果是 MR diff 评审，检索应围绕本次改动。

需要确认：

```text
检索模块是否应该为整个 host struct 找依据，
还是只围绕 changed_lines 和当前 ReviewUnit 的特征找依据？
```

推荐第一版：

```text
以当前 ReviewUnit + changed_lines 相关特征为主。
host_summary 只作为补充上下文，不无限扩散到整个旧代码。
```

这样可以减少：

```text
检索到旧代码相关文档，
导致 LLM 评论非本次改动问题。
```

### 13.9 需要会议确认

```text
1. 检索粒度：Unit 级、MR 级，还是 Unit 级为主 + MR 公共依据？
2. attributes 是否正式进入 CodeFeatures？
3. host_summary 是否作为检索输入？
4. ReviewUnit.full_text 是否参与检索，还是只进入 final prompt？
5. 每个 unit 默认返回多少条 evidence？
6. 内部规范、官方文档、规则、示例代码的优先级如何排序？
7. 示例代码能否作为强依据？
8. MR diff 场景下是否过滤旧代码相关依据？
```

## 14. 明天会议优先提问清单

如果时间有限，优先问这 15 个问题：

```text
1. 第一版主要做 MR diff 评审，还是整文件/整项目体检？
2. ArkTS review 维度里，哪些是第一优先级？
3. 哪些问题必须有官方/内部规范依据才能报？
4. LLM 可以输出可维护性 suggestion 吗？
5. ReviewUnit 应该宁可上下文大一点，还是尽量小？
6. build() 里的改动，应该看完整 build 还是局部 UI 子树？
7. @State 字段改动，是否需要带上所有引用它的代码？
8. 旧代码问题能不能在 MR review 中评论？
9. 第一版更看重低误报，还是尽量多发现问题？
10. mentor 能否提供几条他们认可的高质量 ArkTS review 示例？
11. 检索模块应该按 ReviewUnit 单元检索，还是按 MR 全局检索？
12. attributes 是否要正式进入 CodeFeatures，参与检索？
13. ReviewUnit.full_text 是否参与检索，还是只进入 final prompt？
14. 每个 ReviewUnit 默认返回多少条 evidence，如何控制 token budget？
15. 内部规范、官方文档、静态规则、示例代码的证据优先级如何排序？
```

## 15. 建议会议产出

会议结束后最好形成如下结论：

```text
1. 第一版评审场景：
   例如 MR diff + 手动单文件。

2. 第一版评审维度优先级：
   例如 P0 语言规范、P1 状态/生命周期/权限、P2 性能/维护性。

3. ReviewUnit 粒度策略：
   例如 method 完整、短 build 完整、长 build 切 ui_block。

4. 旧代码评论边界：
   例如只报本次改动直接相关问题。

5. 依据要求：
   例如 high 以上必须有文档/规则依据。

6. 输出 schema：
   例如 JSON 为主，Markdown/GitCode 为渲染。

7. 第一版验收标准：
   例如每个 MR 评论不超过 N 条，高严重误报率低。

8. ReviewUnit 与检索模块契约：
   例如 unit_ref 绑定、CodeFeatures 字段、attributes 是否加入、host_summary 是否进入检索。

9. Evidence Pack 策略：
   例如 Unit 级回挂、每个 unit topK、证据优先级、token budget。
```

## 16. 会后对开发的影响

如果上述问题对齐清楚，后续开发可以按下面顺序推进：

```text
1. ReviewUnit deterministic tests。
2. ReviewUnit 增加 unit_kind / source_span / selection_reason。
3. 明确 diff 模式切分策略。
4. 补齐 ReviewUnit -> RetrievalUnit 最小契约。
5. 将 attributes / host_summary 等字段纳入检索输入设计。
6. 第一批 deterministic rules registry。
7. RAG 知识库条款切分与 Evidence Pack schema。
8. Final LLM Reviewer prompt schema。
```

如果没有对齐，容易出现的问题：

```text
ReviewUnit 不知道该切多大。
LLM 不知道能不能审旧代码。
RAG 不知道该检索什么维度的文档。
RAG 证据和 ReviewUnit 代码片段无法按 unit_ref 对上。
输出结果 mentor 觉得“不是他们想要的 review”。
```

## 17. 一句话总结

```text
明天最重要的不是讨论 parser 细节，而是和 mentor 对齐：
我们第一版到底要审什么、什么算问题、LLM 可以说到哪里、ReviewUnit 应该给多大上下文，以及检索模块该给每个 ReviewUnit 配哪些依据。
```
