---
title: 02 Parser 与代码事实模块
status: canonical
implementation: partial
updated: 2026-07-12
---

# 02 Parser 与代码事实模块

## 1. 模块职责

Parser 将完整 ArkTS 文件转换为确定性代码事实：

```text
源码
-> imports / components / APIs / decorators / attributes
-> syntax / symbols / declarations / parser quality
```

Parser 只登记“代码中有什么”，不判断“代码是否写得好”。

## 2. 当前文件

| 文件 | 职责 |
|---|---|
| `lexical.py` | L0 词法 Parser |
| `arkts_tree_sitter_parser.py` | L1 Python 适配器和 L0/L1 合并 |
| `sidecars/arkts-parser/parse_arkts.js` | Node AST 遍历与 snapshot 输出 |
| `arkts_lexicon.py` | 组件、属性、生命周期和模块别名词典 |
| `text_utils.py` | 屏蔽注释字符串、行列换算和括号匹配 |
| `models.py` | `CodeFacts`、`Declaration` 等模型 |
| `parser_factory.py` | Parser 选择工厂 |
| `tree_sitter_parser.py` | TypeScript tree-sitter 实验实现，不是默认主链 |
| `parser_validation/golden.py` | Golden schema、精确评分和 baseline 校验 |
| `tools/evaluate_parser_golden.py` | L0/merged-L1 accuracy 评测入口 |

## 3. 当前真实数据流

```text
完整文件
   |
   v
L0 LexicalParser（始终执行）
   |
   v
基础 CodeFacts
   |
   v
ArktsTreeSitterParser 尝试启动 Node sidecar
   |
   +-- 失败 -> 保留 L0，标记 L0 或 parse_degraded
   |
   +-- 成功 -> 合并 snapshot，标记 L1
```

不是“先 L1，失败才跑 L0”；L0 永远先产生基线事实。

## 4. L0

技术：

```text
正则
注释和字符串屏蔽
import 解析
大括号匹配
声明父子关系推导
```

优势：无外部运行时依赖、可降级、速度稳定。

局限：

- 不是完整 AST。
- API owner 只能证明冻结全局调用和 SDK import 根绑定；尚不能完整表达局部 shadow occurrence。
- 复杂嵌套、泛型、错误代码和特殊语法可能导致声明边界不准确。
- struct 的前置装饰器不一定进入 declaration text。

## 5. L1

```text
Python ArktsTreeSitterParser
-> subprocess(node parse_arkts.js)
-> tree-sitter-arkts AST
-> 精简 snapshot JSON
-> Python 合并为 CodeFacts
```

sidecar 输出：

```text
parser_version
node_count
error_nodes
missing_nodes
components
calls
decorators
attributes
symbols
syntax
declarations
```

完整 AST 不落盘，也不跨进程返回。

L1 成功后，`ui_block` declarations 是 `components` 的权威来源，全部 declarations 的
`name/qualified_name` 投影是 `symbols` 的权威来源，sidecar 中绑定到真实 UI 链的 modifier
是 `attributes` 的权威来源。这三个字段不再与 L0 结果盲目取并集；其他事实仍按各自契约
合并。这样可以避免 L0 猜测残留污染 L1 精度。

## 6. CodeFacts

当前模型：

```text
path
imports
components
apis
decorators
attributes
symbols
syntax
declarations
parser_layer
warnings
```

详细语法和字段教学见 [ArkTS 入门、Parser 字段与 Tags 详解](../learning/arkts-parser-fields-tags.md)。

### 6.1 给 ReviewUnit 的冻结契约

ReviewUnit 当前可以依赖 declaration 的 kind、name、qualified name、parent、1-based
inclusive 起止行和 text。正式 Parser Golden 已验证行级 span，但没有验证列坐标。

components、APIs、decorators、attributes、symbols 和 syntax 仍是文件级去重集合；除
components/symbols 可从 span 内 declarations 重新投影外，不能声称它们属于某个 Unit。
ReviewUnit 第一阶段不得为获得 owner 而修改 Parser v1 或重新解释 Parser Golden。

## 7. API canonicalization

Parser 根据 import 别名统一 API：

```ts
import img from '@ohos.multimedia.image'
img.createPixelMap(buffer)
```

输出：

```text
image.createPixelMap
```

当前只保留两类 API：冻结的全局平台调用，以及调用根绑定能够由 `@ohos.*`、
`@kit.*` 或 `@system.*` SDK import 证明的静态成员链。`this`/`super`、参数、
局部变量、普通对象方法、相对路径和工程 module import 均不进入 `CodeFacts.apis`。

知识库构建必须使用同一 SDK 白名单和别名规范化规则，确保两侧词形一致。

## 8. Declaration

当前支持：

```text
struct
class
function
method
build_method
builder
ui_block
```

每条包含名称、限定名、父级、源码范围和原文。ReviewUnit 依赖这些范围选择上下文。

## 9. Parser quality

| `parser_layer` | 含义 |
|---|---|
| `L0` | 未获得 L1 结果 |
| `L1` | sidecar 成功并已合并 |
| `parse_degraded` | sidecar 存在但执行失败，退回 L0 |

当前实现中，L1 出现 `ERROR` 或 missing node 时只写 warning，仍标记 L1，没有“超过阈值降级”逻辑。

## 10. 当前调用次数

`CodeAnalyzer` 当前执行：

```text
每个文件完整 Parser 一次
+ 每个去重 ReviewUnit 再 Parser 一次
```

默认 L1 每次会启动 Node 进程，因此 Unit 数多时存在明显重复开销。

二次解析还会改变语义上下文：struct 内 method 的切片可能被解析为顶层 function，多行
import 可能被截断，第二次解析的 layer/warning 也未进入 Analysis metadata。因此删除
二次解析是正确性目标，但必须先定义 Unit exact facts 与 file hints，不能直接把文件级集合
复制到每个 Unit。

## 11. 目标架构

```text
每个变化文件只 Parser 一次
-> FileAnalysis
-> 每个 Fact 都带 span 和 owner
-> ReviewUnit 按 span 筛选 Unit Facts
-> 不再二次 Parser
```

目标 `FactOccurrence`：

```text
kind
name
canonical_name
span
owner_ref
provenance
```

目标还需要提取：

```text
字符串字面量与 $r 资源引用
组件参数
调用 occurrence
声明唯一 ID
更精确的父子关系
必要的类型和引用信号
```

## 12. 性能演进

按优先级：

1. 删除 Unit 二次 Parser。
2. Declaration 只保存 span，按需切片，减少重叠文本。
3. 按 `content_hash + parser_version` 缓存 FileAnalysis。
4. 测量后再决定常驻 Node worker。
5. 只有完整重解析成为瓶颈时才考虑 tree-sitter incremental edit。

当前声明扫描复杂度不是主要瓶颈，Node 进程启动和重复解析更重要。

## 12.1 外部语料和实现参考

当前已落盘但不进入 Parser 生产运行时的资料：

| 来源 | 用途 |
|---|---|
| `arkui-ace-engine` | 63 样本 manifest 和扩大 Parser 回归语料 |
| `xts-acts` | ArkTS 边界、限制项和异常语法样本 |
| `applications-app-samples` / `codelabs` | 更接近应用代码的语法分布 |
| `arkcompiler-ets-frontend` | Parser/Checker/Linter 实现和诊断真值参考 |
| `interface-sdk-js` | 目标 `ApiSymbolCatalog` 和 canonical API 白名单来源 |

这些仓库的 commit 由外部 `sources.yaml` 固定。Parser 测试引用外部路径时必须记录
`source_id + revision + relative_path`，不能只假设某个相邻目录永远是最新版本。

## 13. 配置

当前环境变量：

```text
ARKTS_PARSER_NODE
ARKTS_PARSER_TIMEOUT
ARKTS_PARSER_SIDECAR
```

目标新增：

```text
parser mode
允许的 ERROR/missing 阈值
SDK whitelist path/version
缓存目录或后端
最大文件大小
```

## 14. 测试现状

已有：

- L0 固定样例事实测试。
- L1 sidecar 条件测试。
- 15 个自包含、人工复核的 Parser Golden cases，覆盖全部 7 种 declaration kind 和 5 种
  冻结 syntax kind。
- L0 和 merged-L1 的逐 case 完整 baseline。
- 63 个固定 revision 的 `arkui_ace_engine` 稳定性/性能样本。
- Golden provenance、candidate evidence 和统一 release gate。

2026-07-12 当前实测：

```text
pytest after npm ci             60 passed, 64 subtests passed
Golden L0                       15/15 L0，完整 baseline 精确匹配
Golden merged-L1                15/15 L1，全部评分字段 FP=0/FN=0
Golden L1 declarations          TP 93 / FP 0 / FN 0
LexicalParser engine batch     63 parsed / 0 missing / 0 crashed
Merged-L1 engine batch          63 L1 / 0 missing / 0 crashed
R63 L1 AST warnings             ERROR 7 files / missing 7 files
declarations_total             L0 2,880 / merged-L1 5,414
```

统一复核：

```bash
(cd sidecars/arkts-parser && npm ci)
PYTHONPATH=src python tools/check_parser_v1.py \
  --source-root /home/autken/Code/arkui_ace_engine \
  --include-candidate-diagnostics
```

当前限制：

- 普通 pytest 在未执行 `npm ci` 时会跳过可选 L1 测试；正式 L1 验收必须运行 strict baseline 命令。
- 当前 L1 分数是 L0+L1 合并结果，不是 raw-L1 分数。
- R63 只证明稳定性、layer 和警告分布，不提供字段 accuracy 真值。
- Golden v1 尚不评分 occurrence span、owner、结构化 diagnostics 或 raw-L1 snapshot。
- 默认 23 个 Grok candidate 仍是 provisional：集合字段与 syntax 已 exact；declarations
  只剩 B010 两个违反冻结 decorator-span 政策的候选标注冲突。其旧 symbol evidence 有
  441 项未使用匹配 declaration 的完整 span，不能晋级为正式 Golden。
- 当前外部语料都是浅克隆快照；更新 revision 后需要重新跑基线。

## 15. 质量门槛

目标指标：

```text
crash rate
parse_degraded rate
declaration boundary precision/recall
API/component precision/recall
empty facts rate
平均/尾部解析时延
```

## 16. 已知决策

- 生产主 Parser 使用 ArkTS tree-sitter sidecar，L0 作为 fallback。
- Parser 不直接产生 Finding。
- FileAnalysis 是文件级 source of truth。
- API 与知识库关键词共用 canonical whitelist。

## 17. 下一步

ReviewUnit RU-0/RU-1 已以当前 Parser v1 为只读依赖完成；RU-2 仍不得借质量传播重开
Parser 行为。只有新 Parser Golden 证明必要时才允许修改 Parser。Parser 自身后续顺序：

1. 人工裁决 B010 两个 `@Styles` span，并按冻结政策重建 B001-B006/B010 evidence；在此
   之前 candidate 只作诊断。
2. 为 raw-L1 snapshot 增加独立公共评测路径、真正 ERROR/missing recovery Golden 和
   diagnostics 门槛。
3. 引入带 span/owner 的 FactOccurrence，并先扩展 Golden 契约。
4. 与 ReviewUnit 的 Unit fact scope 契约对齐后，修改 Analyzer 删除二次 Parser。
5. 从 `interface-sdk-js` 构建共享 `ApiSymbolCatalog`，替代分散白名单。
6. 明确 `tree_sitter_parser.py` 实验实现的保留或删除策略。
