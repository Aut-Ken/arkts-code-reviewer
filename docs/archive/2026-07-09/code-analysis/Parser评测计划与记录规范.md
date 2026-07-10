---
title: Parser 评测计划与记录规范
module: code-analysis
project: arkts-code-reviewer
status: draft
created: 2026-07-07
updated: 2026-07-07
tags:
  - arkts-code-reviewer
  - parser-validation
  - test-plan
  - glm
---

# Parser 评测计划与记录规范

> [!summary]
> 本文档定义 `arkts-code-reviewer` 的 parser 评测计划、样本分组、测试批次、结果留存目录结构和人工判断流程。
> 目标是把临时测试升级为可复现、可追踪、可对比的评测体系。

## 1. 核心原则

### 1.1 每次测试都必须可追溯

每次测试至少要能回答：

```text
这次测了哪些文件？
使用哪个 parser？
使用哪个 GLM 模型？
使用什么参数？
输出文件在哪里？
发现了哪些问题？
哪些问题已经人工确认？
哪些问题已修复？
修复后是否复测通过？
```

### 1.2 确定性批测和 GLM 质检分开

确定性批测回答：

```text
parser 能不能跑？
有没有 crash？
有没有 missing sample？
有没有 empty features？
有没有 parse_degraded？
warning 分布如何？
```

GLM 质检回答：

```text
parser 输出是否可能漏提？
是否可能误提？
canonicalization 是否异常？
ReviewUnit 边界是否合理？
```

这两类报告都要留存，但不能混为一个结论。

### 1.3 GLM findings 不是最终真值

GLM 是质检员，不是事实裁判。

每个高价值 finding 都需要：

```text
1. 看 evidence_lines
2. 打开真实源码
3. 查看 parser 实际 CodeFacts
4. 人工确认 accepted / rejected / unclear
5. accepted 的问题再进入修复队列和 golden test
```

---

## 2. 重要路径

### 2.1 主项目

```text
D:\Code\RAG-test\arkts-code-reviewer
```

### 2.2 真实 ArkTS 源码库

```text
D:\Code\RAG-test\arkui_ace_engine
```

### 2.3 当前样本清单

```text
D:\Code\RAG-test\arkts-code-reviewer\tests\fixtures\arkui_ace_engine_samples.json
```

注意：

```text
这个 json 只保存相对路径，不保存源码。
真实源码路径 = D:\Code\RAG-test\arkui_ace_engine + sample.path
```

### 2.4 推荐测试结果根目录

```text
D:\Code\RAG-test\arkts-code-reviewer\reports\parser_validation\runs
```

---

## 3. Token 约定和文件长度分组

### 3.1 MaxTokens=20k 的含义

约定：

```text
GLM MaxTokens = 20000
```

但需要注意：

```text
MaxTokens 控制的是模型最大输出 token，不是输入 token。
```

输入大小主要由这些因素决定：

```text
source_excerpt 行数
parser_output 大小
review_units 数量
retrieval_units 数量
prompt schema 大小
```

所以即使 `MaxTokens=20000`，超长文件也不应该直接整文件批量丢给 GLM。

### 3.2 分组边界

当前推荐按源码行数分组：

| 组          |        行数范围 | 测试方式                | 推荐 MaxSourceLines | 推荐 GLM 批量 |
| ---------- | ----------: | ------------------- | ----------------: | --------: |
| G01 small  |     0-150 行 | 批量                  |               200 |   3-5 个/次 |
| G02 medium |   151-500 行 | 小批量                 |               500 |   2-3 个/次 |
| G03 long   |  501-1500 行 | 单文件或 2 个/次          |          800-1200 |   1-2 个/次 |
| G04 large  | 1501-3000 行 | 单文件                 |          600-1000 |     1 个/次 |
| G05 huge   |     3001+ 行 | 单文件 + 分片/ReviewUnit |           400-800 |     1 个/次 |

解释：

```text
G01/G02 适合看 parser 基础准确性。
G03 适合看真实组件复杂结构。
G04/G05 不适合一次性判断全文件准确性，更适合按疑点、review unit、局部片段测试。
```

### 3.3 当前 63 个 manifest 样本分布

当前样本清单统计：

```text
G01 small  0-150 行：14 个
G02 medium 151-500 行：4 个
G03 long 501-1500 行：7 个
G04 large 1501-3000 行：35 个
G05 huge 3001+ 行：3 个
missing：0 个
```

这个分布很重要：

```text
当前 manifest 里大文件占比很高。
后续扩展到 100 个中长代码文件时，必须控制 GLM 运行方式，否则成本和噪声都会上升。
```

---

## 4. 测试计划分组

下面的“测试 1 / 测试 2 / 测试 3 ...”是建议的长期计划。

### 4.1 测试 1：G01 small 批量测试

目标：

```text
验证基础事实抽取是否稳定：
components / apis / decorators / attributes / ReviewUnit 边界。
```

样本范围：

```text
0-150 行
当前 14 个
```

执行策略：

```text
每次 3-5 个文件。
先用 GLM 跑 3 个，稳定后跑剩下 11 个。
```

推荐参数：

```powershell
-ResponseFormat omit
-MaxTokens 20000
-MaxSourceLines 200
```

当前代表样本：

```text
examples/Animation/entry/src/main/ets/pages/animateTo.ets
examples/Animation/entry/src/main/ets/pages/animation.ets
examples/Animation/entry/src/main/ets/pages/transition.ets
frameworks/bridge/declarative_frontend/state_mgmt/test/unittest/entry/src/main/ets/pages/v1_tests.ets
advanced_ui_component/downloadfilebutton/source/DownloadFileButton.ets
```

验收标准：

```text
invalid_output = 0
likely_parser_bug 尽量为 0
所有 findings 都有 evidence_lines
人工确认后 accepted bug 必须沉淀回归测试
```

### 4.2 测试 2：G02 medium 小批量测试

目标：

```text
验证中等复杂页面和示例是否稳定。
```

样本范围：

```text
151-500 行
当前 4 个
```

执行策略：

```text
每次 2 个文件，分 2 次跑。
```

推荐参数：

```powershell
-ResponseFormat omit
-MaxTokens 20000
-MaxSourceLines 500
```

当前样本：

```text
examples/Wearable/entry/src/main/ets/pages/Index.ets
examples/Wearable/entry/src/main/ets/pages/ArcButton/ArcButtonExample001.ets
examples/components/feature/src/main/ets/pages/DialogBoxes/BindSheetBootcamp.ets
examples/ImageAnimator/entry/src/main/ets/pages/example/ImageAnimatorTest012.ets
```

验收标准：

```text
ReviewUnit boundary 不能明显 too_small / too_large。
components / attributes / router / resource / state 类事实不能有高置信漏提。
```

### 4.3 测试 3：G03 long 单文件测试

目标：

```text
验证 500-1500 行真实复杂组件。
```

样本范围：

```text
501-1500 行
当前 7 个
```

执行策略：

```text
优先单文件测试。
同类文件最多 2 个一组。
每个 finding 都要人工看源码。
```

推荐参数：

```powershell
-ResponseFormat omit
-MaxTokens 20000
-MaxSourceLines 800
```

当前样本：

```text
advanced_ui_component/toolbar/source/toolbar.ets
advanced_ui_component/foldsplitcontainer/source/foldsplitcontainer.ets
advanced_ui_component/arcslider/source/arcslider.ets
advanced_ui_component/imageGeneratorDialog/source/image_generator_dialog_project/image_generator_dialog/src/main/ets/common/utils/ImageProcessUtils.ets
examples/components/feature/src/main/ets/pages/DialogBoxes/CustomDialogBootcamp.ets
examples/components/feature/src/main/ets/pages/DialogBoxes/AlertDialogBootcamp.ets
examples/Wearable/entry/src/main/ets/pages/ArcSlider/ArcSliderExample001.ets
```

验收标准：

```text
每个文件至少产出一个合理 ReviewUnit。
parser_layer 必须是 L1。
不能出现大量重复、超长、无意义 apis。
GLM findings 需要按字段分类：真实 parser bug / prompt policy 问题 / GLM 误判。
```

### 4.4 测试 4：G04 large 单文件测试

目标：

```text
验证 1501-3000 行大型示例文件。
```

样本范围：

```text
1501-3000 行
当前 35 个
```

执行策略：

```text
一律单文件测试。
不要一次跑多个 large 文件。
先确定性 batch，再 GLM。
GLM 只能判断 source_excerpt 覆盖到的片段，不代表全文件完全通过。
```

推荐参数：

```powershell
-ResponseFormat omit
-MaxTokens 20000
-MaxSourceLines 600
```

如果 GLM finding 指向 excerpt 外，应该：

```text
调大 MaxSourceLines 或做专门分片测试。
```

验收标准：

```text
不追求一次 GLM 判断全文件所有事实。
重点看 parser 是否产生荒谬结果：
  超长 API 链
  大量 false positive
  ReviewUnit 边界明显错误
  parse_degraded
```

### 4.5 测试 5：G05 huge 分片测试

目标：

```text
验证 3001+ 行超大文件，但不要求单次覆盖全文件。
```

当前样本：

```text
advanced_ui_component/treeviewv2/source/treeviewv2.ets                         5261 行
examples/components/feature/src/main/ets/pages/ScrollAndSwipe/GridBootcamp.ets 3141 行
frameworks/bridge/arkts_frontend/koala_projects/arkoala-arkts/arkui-ohos/generated/component/navigation.ets 3784 行
```

执行策略：

```text
一律单文件。
先跑确定性 batch。
再根据 parser 输出的 ReviewUnit / declarations 挑片段。
GLM 不建议整文件一次性判断。
```

推荐参数：

```powershell
-ResponseFormat omit
-MaxTokens 20000
-MaxSourceLines 400-800
```

后续建议补工具：

```text
按 ReviewUnit 生成 validation request。
支持 start_line / max_source_lines 组合。
支持一个大文件拆成多个 run segment。
```

---

## 5. 测试记录目录结构

### 5.1 总目录

推荐所有测试记录放在：

```text
D:\Code\RAG-test\arkts-code-reviewer\reports\parser_validation\runs
```

### 5.2 按测试组建文件夹

目录结构：

```text
reports/parser_validation/runs/
  T01-small-batch/
  T02-medium-batch/
  T03-long-single/
  T04-large-single/
  T05-huge-segmented/
```

每个测试组目录下放：

```text
group-plan.md
samples.json
runs/
```

### 5.3 每组每次测试建 run 文件夹

示例：

```text
reports/parser_validation/runs/T01-small-batch/
  group-plan.md
  samples.json
  runs/
    run-001-20260707-1430/
    run-002-20260707-1605/
```

### 5.4 每次测试文件夹内容

每个 run 文件夹建议包含：

```text
run-001-20260707-1430/
  run-meta.json
  command.ps1
  selected-samples.json
  deterministic-batch.json
  glm-findings.jsonl
  glm-findings.pretty.json
  raw_glm/
  summary.md
  adjudication.md
```

字段说明：

| 文件 | 作用 |
|---|---|
| `run-meta.json` | 记录模型、参数、parser 版本、时间、样本数量 |
| `command.ps1` | 保存本次运行命令，方便复现 |
| `selected-samples.json` | 本次实际跑了哪些文件 |
| `deterministic-batch.json` | parser 批测结果 |
| `glm-findings.jsonl` | 机器处理用，一行一个结果 |
| `glm-findings.pretty.json` | 人工阅读用 |
| `raw_glm/` | GLM 原始 HTTP 响应 |
| `summary.md` | 本次测试人工总结 |
| `adjudication.md` | 对 findings 的人工裁决 |

---

## 6. run-meta.json 模板

```json
{
  "run_id": "run-001-20260707-1430",
  "group_id": "T01-small-batch",
  "created_at": "2026-07-07T14:30:00+08:00",
  "engine_root": "D:/Code/RAG-test/arkui_ace_engine",
  "manifest": "D:/Code/RAG-test/arkts-code-reviewer/tests/fixtures/arkui_ace_engine_samples.json",
  "parser": "arkts-tree-sitter",
  "parser_layer_expected": "L1",
  "glm": {
    "model": "glm-5.1",
    "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
    "thinking": "enabled",
    "response_format": "omit",
    "max_tokens": 20000,
    "max_source_lines": 200
  },
  "sample_policy": {
    "bucket": "small",
    "line_range": "0-150",
    "batch_size": 3
  },
  "status": "planned | running | completed | blocked",
  "notes": ""
}
```

---

## 7. summary.md 模板

```markdown
# Run Summary

## Basic

- Run ID:
- Group:
- Date:
- Parser:
- GLM model:
- Samples:

## Deterministic Result

- parsed:
- crashed:
- empty_features:
- parser_layers:
- warning_counts:

## GLM Result

- pass:
- needs_human_review:
- likely_parser_bug:
- invalid_output:

## Accepted Findings

| sample | kind | field | value | priority | owner |
|---|---|---|---|---|---|

## Rejected / Prompt Policy Findings

| sample | finding | reason |
|---|---|---|

## Next Action

- [ ] add regression test
- [ ] fix parser
- [ ] rerun same group
- [ ] expand to next group
```

---

## 8. adjudication.md 模板

```markdown
# Adjudication

## Finding 1

- sample:
- source_path:
- verdict:
- kind:
- field:
- value:
- evidence_lines:
- confidence:
- retrieval_impact:
- decision: accepted | rejected | unclear | prompt_policy
- reason:
- fix_target:
- regression_test:

## Notes

```

判断原则：

```text
accepted:
  源码确实存在，parser 确实漏提/误提/边界错。

rejected:
  GLM 看错了，或者 evidence 不成立。

prompt_policy:
  GLM 的字段理解和我们的 parser policy 不一致。
  例如 ArkUI modifier 应属于 attributes，不属于 apis。

unclear:
  需要更多源码上下文或更明确的字段定义。
```

---

## 9. 建议执行顺序

### 阶段 A：当前 63 样本收敛

```text
A1. T01-small-batch 全部跑完
A2. T02-medium-batch 全部跑完
A3. T03-long-single 跑 3 个代表文件
A4. 修 parser
A5. 回归跑 T01/T02/T03
```

目标：

```text
建立 parser 质量基线。
确认 GLM prompt 和字段 policy 不再频繁误判。
```

### 阶段 B：扩展到 100 个中长文件

样本选择建议：

```text
100-1500 行优先。
覆盖 category，而不是只按文件长度。
优先选有 @Component / build / 状态 / 导航 / 列表 / 图片 / 动画 / 弹窗 / 输入的文件。
generated_component 可以少量选，不宜占比过高。
```

推荐比例：

```text
small: 10
medium: 20
long: 40
large: 25
huge: 5
```

但 GLM 执行时仍然按组和批次跑，不建议一次跑 100。

### 阶段 C：超长文件分片

目标：

```text
treeviewv2.ets 等 3000+ 行文件不做整文件 GLM 一次性判断。
改成按 ReviewUnit / declaration / 行区间分片。
```

需要补工具：

```text
1. 生成 declaration index
2. 按 review unit 选择 source_excerpt
3. GLM request 中标注 slice 范围
4. findings 关联到具体 slice
```

---

## 10. 当前建议

近期最稳的做法：

```text
1. 先按 T01-small-batch 把 14 个小文件全跑完。
2. 如果 pass / 可解释 findings 比例稳定，再跑 T02-medium-batch。
3. T03 long 每次只跑 1-2 个。
4. T04/T05 不急，先作为单文件专题测试。
```

不要一上来直接跑：

```text
100 个文件 + GLM
```

更好的节奏：

```text
确定性 batch 100 个
GLM 抽检 5 个
修 parser
GLM 抽检 20 个
再扩展
```

---

## 11. GLM 429 和断点续跑

如果命令中出现：

```text
RuntimeError: GLM HTTP 429
该模型当前访问量过大，请您稍后再试
```

这不是 parser 失败，而是 GLM 服务端限流或模型繁忙。

判断方式：

```text
1. 先看 deterministic-batch.json 是否已经生成。
2. 如果 deterministic-batch.json 正常，说明 parser 本地批测已经通过。
3. 再看 glm-findings.jsonl 是否为空。
4. 如果为空，说明 GLM 在第一个样本前失败，直接重跑同一个 command.ps1 即可。
5. 如果不为空，说明 GLM 已经完成部分样本，必须使用 --resume 续跑。
```

当前工具默认策略：

```text
retry_attempts: 4
retry_base_delay_seconds: 20
retry HTTP status: 429, 500, 502, 503, 504
delay: 20s, 40s, 80s...
```

每个新生成的 run 命令都会带：

```text
--retry-attempts
--retry-base-delay-seconds
--resume
```

如果高峰期仍然失败，可以手动把生成的 `command.ps1` 中重试参数调大，例如：

```powershell
--retry-attempts '6'
--retry-base-delay-seconds '30.0'
```

但不建议无限重试。遇到连续 429 时，更好的做法是暂停几分钟后重跑同一个 run 的 `command.ps1`。
