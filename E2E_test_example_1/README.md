# E2E Test Example 1

这是一个可复现的 ArkTS Code Reviewer 端侧交付样例。它从
`applications_app_samples` 固定 revision 中复制一份 984 行 VideoPlayer 源码，生成
`+88/-21` 的合成 Diff，然后真实执行到 Retrieval `EvidencePack`。

先看汇报文档：[REPORT.md](REPORT.md)。各阶段未经删减的机器输出位于
[`artifacts/`](artifacts/)，输入和来源证明位于 [`inputs/`](inputs/)。

## 链路范围

```text
ChangeSet
  -> Parser
  -> ReviewUnit
  -> UnitFactScope
  -> Feature Routing
  -> Context Planning
  -> RetrievalRequest
  -> PostgreSQL KnowledgeIndex
  -> CUDA embedding + exact/vector/RRF retrieval
  -> EvidencePack
```

本样例在 `EvidencePack` 停止。Rules、Prompt 和 LLM 评审尚未进入这条可执行链路，
因此报告中的知识是候选证据，不是最终 Finding。

## 目录

```text
E2E_test_example_1/
├── README.md
├── REPORT.md
├── prepare_fixture.py
├── run_e2e.py
├── inputs/
│   ├── base.ets
│   ├── head.ets
│   ├── diff.patch
│   ├── expected_review_units.json
│   ├── mutation_spec.json
│   └── provenance.json
└── artifacts/
    ├── 00_run_manifest.json
    ├── 01_change_set.json
    ├── 02_parser_base.json
    ├── 03_parser_head.json
    ├── 04_review_unit_build.json
    ├── 05_unit_fact_scopes.json
    ├── 06_feature_routing.json
    ├── 07_context_plan.json
    ├── 08_retrieval_request.json
    ├── 09_knowledge_index_summary.json
    ├── 10_evidence_pack.json
    ├── 11_assertions.json
    └── 12_summary.json
```

## 重新生成输入

这一步只定点读取下列文件，不扫描也不写入外部仓库：

```text
/home/autken/Code/applications_app_samples/
code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets
```

外部仓库必须位于 revision
`8255a2987f70317cc3a2a4d46044c6b55f092bb3`，且目标文件哈希必须匹配。运行：

```bash
cd /home/autken/Code/arkts-code-reviewer
PYTHONPATH=src .venv/bin/python E2E_test_example_1/prepare_fixture.py
```

脚本会重新生成 `inputs/`，并对固定 revision、源码哈希、行数和 Diff 统计做校验。

## 重新运行端到端

前提：

- `arkts-retrieval-final-postgres-1` 容器健康，映射到本机 `55434`；
- alias `staging-knowledge-seed-v1` 已发布；
- `.venv-gpu` 已安装 CUDA ONNX Runtime 和本项目依赖；
- embedding 模型已存在于本地缓存。

数据库 URL 只通过环境变量传入。下面的本机命令从容器配置临时读取连接参数，不会把
密码写入样例文件：

```bash
cd /home/autken/Code/arkts-code-reviewer

db_env=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
  arkts-retrieval-final-postgres-1)
db_user=$(sed -n 's/^POSTGRES_USER=//p' <<<"$db_env")
db_password=$(sed -n 's/^POSTGRES_PASSWORD=//p' <<<"$db_env")
db_name=$(sed -n 's/^POSTGRES_DB=//p' <<<"$db_env")
export ARKTS_RETRIEVAL_DATABASE_URL="postgresql://${db_user}:${db_password}@127.0.0.1:55434/${db_name}"
export OMP_NUM_THREADS=2

PYTHONPATH=src .venv-gpu/bin/python E2E_test_example_1/run_e2e.py
unset ARKTS_RETRIEVAL_DATABASE_URL db_env db_user db_password db_name
```

成功时命令返回 `status: pass`，并重写 `artifacts/` 与 `REPORT.md`。正式 JSON 使用稳定
排序和确定性 ID；报告不写运行时间或数据库密码。

## 当前结果如何解读

- 工程链路通过 15 项跨阶段断言；15 个 ReviewUnit 没有未分配 ChangeAtom，且
  owner/span/changed lines/reason/顺序匹配人工 expected。
- base/head Parser 都是 L1，ERROR、missing、warning 均为 0。
- 109 条 Draft Clause 的 768 维 embedding 全部可用，实际 provider 为
  `CUDAExecutionProvider`。
- 本次返回 67 条 Unit/Clause 关系，但只有 1 条包含 Unit 级精确匹配，9 个需要的
  dimension 实例全部未覆盖。当前知识召回主要受宽泛 `file_hints` 驱动。
- 因为尚未为本样例建立人工 relevant/irrelevant 真值，不能从这次运行声称语义
  Precision 或 Recall。详细限制见报告第 14、15 节。
- 结论应表述为“链路完整性 PASS，语义检索质量 NOT QUALIFIED”，不能只汇报 PASS。
