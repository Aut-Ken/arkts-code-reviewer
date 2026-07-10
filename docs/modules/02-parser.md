---
title: 02 Parser 与代码事实模块
status: canonical
implementation: partial
updated: 2026-07-10
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
- 可能把 `this.loadImages`、`photos.push` 当成外部 API。
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
- 63 个 `arkui_ace_engine` 样本 manifest。
- Parser Validation 工具链。

当前验证限制：

- sidecar npm 依赖未安装，L1 测试跳过。
- 相邻 `arkui_ace_engine` 仓库不存在，真实样本测试跳过。
- 批测在 63 个样本全缺失时仍退出 0，可能导致假绿。

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

1. 安装并固定 sidecar 依赖，在 63 个真实样本上运行 L1。
2. 让缺失全部真实语料时测试和批测失败。
3. 引入带 span 的 FactOccurrence。
4. 修改 Analyzer，删除 ReviewUnit 二次 Parser。
5. 明确 `tree_sitter_parser.py` 实验实现的保留或删除策略。

