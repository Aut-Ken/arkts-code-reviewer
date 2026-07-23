from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from arkts_code_reviewer.knowledge.document_first.catalog import (
    DocumentCatalogBuild,
    build_document_catalog,
    verify_document_catalog,
)
from arkts_code_reviewer.knowledge.document_first.live_smoke import (
    DocumentCardLiveReceipt,
    DocumentCardRunArtifacts,
    _verify_run_artifacts,
    build_document_card_smoke_bundle,
    materialize_document_card_inspection,
)
from arkts_code_reviewer.knowledge.document_first.models import (
    DocumentCard,
    load_document_card,
    load_document_card_draft,
)

ROOT = Path(__file__).resolve().parent
PLAN_DIGEST = "c911311d787e72252d7134acc1393259663ba78d061315d1c2003342124e7768"
ARTIFACTS = ROOT / "artifacts" / PLAN_DIGEST
REPORT = ROOT / "REPORT.md"

API_NAVIGATION_PROBES = (
    "@Concurrent",
    "ArrayBuffer",
    "SharedArrayBuffer",
    "Sendable",
    "Promise",
    "async/await",
    "onmessage",
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _assertion(name: str, condition: bool, evidence: str) -> dict[str, object]:
    if not condition:
        raise ValueError(f"E2E3 assertion failed: {name}: {evidence}")
    return {"name": name, "status": "PASS", "evidence": evidence}


def _render_report(
    *,
    explicit_api_navigation_probes: tuple[str, ...],
    card: DocumentCard,
    catalog: DocumentCatalogBuild,
    receipt: DocumentCardLiveReceipt,
) -> str:
    card_data = card.model_dump(mode="json")
    usage = receipt.usage
    usage_text = "Provider 未返回 usage"
    if usage is not None:
        usage_text = (
            f"输入 {usage.prompt_tokens}、输出 {usage.completion_tokens}、"
            f"合计 {usage.total_tokens} tokens"
        )
    section_rows = "\n".join(
        f"| {index} | `{item['section_id']}` | {item['summary']} |"
        for index, item in enumerate(card_data["section_summaries"], start=1)
    )
    topics = "、".join(f"`{item}`" for item in card_data["primary_topics"])
    api_hints = "、".join(f"`{item}`" for item in card_data["important_apis"]) or "—"
    probes = "、".join(f"`{item}`" for item in explicit_api_navigation_probes)
    artifact_rows = "\n".join(
        f"| {stage} | [{filename}](artifacts/{PLAN_DIGEST}/{filename}) |"
        for stage, filename in (
            ("Source manifest", "00_source-manifest.json"),
            ("Normalized Markdown", "01_source.md"),
            ("Static document map", "02_document-map.json"),
            ("Document Card request", "03_request.json"),
            ("Exact dispatch plan", "04_dispatch-plan.json"),
            ("Offline inspection", "05_inspection.json"),
            ("Raw provider response", "06_provider-response.raw.json"),
            ("Document Card draft", "07_document-card-draft.json"),
            ("Document Card", "08_document-card.json"),
            ("Live receipt", "09_receipt.json"),
            ("Contract assertions", "10_assertions.json"),
            ("Summary", "11_summary.json"),
            ("One-document navigation catalog", "12_document-catalog.json"),
        )
    )
    return f"""# TaskPool / Worker DeepSeek Document Card Smoke

> 固定 OpenHarmony 官方 Markdown，在精确 Plan、Body 和单次预算授权下调用
> DeepSeek V4 Pro，生成仅用于文档导航的 Document Card。

## 1. 汇报结论

- 执行合同：**PASS**。
- Provider 状态：**{receipt.status}**，1 次尝试、0 次重试。
- 结构覆盖：**5/5 sections**。
- 原始响应链：**PASS**，Raw Response → Draft → Card → Receipt 可从固定原文完整重建。
- 内容质量：**NOT QUALIFIED**。
- 已观察问题：`important_apis=[]`，但原文明确包含 {probes} 等 API/类/装饰器样式词，
  说明 API 导航提示存在漏召回。

`valid_card` 只表示模型输出满足严格结构和来源绑定合同，不表示摘要、主题或 API 提示已经达到生产质量。

## 2. 固定输入

- Source：`openharmony-docs`
- Revision：`c8f5fb6c2fe03cf66b8a41c196ad7fc5e7891c47`
- Path：`zh-cn/application-dev/arkts-utils/taskpool-vs-worker.md`
- Plan：`{receipt.plan_id}`
- Body：`{receipt.wire_body_sha256}`
- Model：`{receipt.provider_model}`
- 最大输出：4096 tokens
- 重试策略：不重试

## 3. 实际链路

```text
Pinned Git Markdown
  → NormalizedDocument
  → MarkdownDocumentMap（5 sections）
  → content-addressed Request / DispatchPlan
  → DeepSeek V4 Pro（1 attempt）
  → strict Raw Response reducer
  → DocumentCardDraft
  → DocumentCard
  → Receipt
  → DocumentCatalog（offline, 1 entry）
```

这条链生成了一份单文档导航 Catalog，但没有执行 Catalog 路由、Clause Retrieval、
EvidencePack、Finding 或 GitCode 发布。

## 4. 运行结果

- 延迟：{receipt.latency_ms} ms。
- Token：{usage_text}。
- Card ID：`{receipt.card_id}`。
- Catalog ID：`{catalog.catalog_id}`（1 entry）。
- 摘要：{card_data['summary']}
- 主题：{topics}
- API 导航提示：{api_hints}
- `use_scope`：`{card_data['use_scope']}`
- `evidence_eligible`：`false`

## 5. 章节导航摘要

| 序号 | Section ID | 摘要 |
|---:|---|---|
{section_rows}

## 6. 机器产物

| 阶段 | 文件 |
|---|---|
{artifact_rows}

## 7. 离线复核

```bash
cd /home/autken/Code/arkts-code-reviewer
uv run python E2E_test_example_3_document_card/run_e2e.py
```

该命令只读取固定 Git 对象和仓库内 00～09 产物，重新验证完整链，并重建断言、摘要、
单条目 Catalog 与本报告。
它不会读取 `.env`、API Key，也不会调用 DeepSeek。

## 8. 准确边界

本样例证明了：单篇固定 Markdown 可以安全生成结构化导航卡与单条目 Catalog；章节身份、
原始响应、模型输出和运行回执可以重放核对。

本样例没有证明：`important_apis` 召回合格、多文档分类准确、Document Catalog 路由有效、
真实文档 Recall/Precision 合格，或生产知识库已经 qualified。
"""


def main() -> int:
    bundle = build_document_card_smoke_bundle()
    inspection = materialize_document_card_inspection(
        bundle,
        output_root=ROOT / "artifacts",
    )
    if Path(str(inspection["artifact_directory"])) != ARTIFACTS:
        raise ValueError("E2E3 inspection resolved to an unexpected artifact directory")

    raw = (ARTIFACTS / "06_provider-response.raw.json").read_bytes()
    draft = load_document_card_draft(
        (ARTIFACTS / "07_document-card-draft.json").read_bytes()
    )
    card = load_document_card((ARTIFACTS / "08_document-card.json").read_bytes())
    receipt = DocumentCardLiveReceipt.model_validate_json(
        (ARTIFACTS / "09_receipt.json").read_bytes()
    )
    artifacts = DocumentCardRunArtifacts(
        receipt=receipt,
        raw_response_body=raw,
        draft=draft,
        card=card,
    )
    _verify_run_artifacts(bundle, artifacts)
    catalog_input = ((bundle.document, bundle.document_map, card),)
    catalog = build_document_catalog(catalog_input)
    verify_document_catalog(catalog_input, catalog)

    expected_sections = tuple(section.section_id for section in bundle.document_map.sections)
    actual_sections = tuple(item.section_id for item in card.section_summaries)
    present_api_probes = tuple(
        probe for probe in API_NAVIGATION_PROBES if probe in bundle.document.body
    )
    persisted_live_files = tuple(
        ARTIFACTS / filename
        for filename in (
            "00_source-manifest.json",
            "01_source.md",
            "02_document-map.json",
            "03_request.json",
            "04_dispatch-plan.json",
            "05_inspection.json",
            "06_provider-response.raw.json",
            "07_document-card-draft.json",
            "08_document-card.json",
            "09_receipt.json",
        )
    )
    forbidden_secret_markers = (b"authorization:", b"bearer sk-", b"deepseek_api_key")
    persisted_payload = b"\n".join(path.read_bytes().lower() for path in persisted_live_files)
    assertions = (
        _assertion(
            "A01_pinned_source_identity",
            card.source_ref == bundle.document.source_ref
            and card.source_ref.source_id == "openharmony-docs"
            and card.source_ref.revision
            == "c8f5fb6c2fe03cf66b8a41c196ad7fc5e7891c47"
            and card.source_ref.relative_path
            == "zh-cn/application-dev/arkts-utils/taskpool-vs-worker.md",
            card.source_ref.content_hash,
        ),
        _assertion(
            "A02_document_map_rebuilt",
            len(bundle.document_map.sections) == 5
            and not bundle.document_map.normalization_diagnostics
            and not bundle.document_map.diagnostics,
            "sections=5,normalization_diagnostics=0,structure_diagnostics=0",
        ),
        _assertion(
            "A03_exact_request_plan_and_body",
            receipt.plan_id == bundle.plan.plan_id
            and receipt.wire_body_sha256 == bundle.plan.wire_body_sha256,
            receipt.plan_id,
        ),
        _assertion(
            "A04_provider_completed_once",
            receipt.status == "valid_card"
            and receipt.http_status == 200
            and receipt.finish_reason == "stop"
            and receipt.attempt_count == 1
            and receipt.retry_count == 0,
            "status=valid_card,http=200,finish=stop,attempts=1,retries=0",
        ),
        _assertion(
            "A05_raw_draft_card_receipt_chain",
            receipt.card_id == card.card_id,
            receipt.receipt_id,
        ),
        _assertion(
            "A06_complete_section_coverage",
            actual_sections == expected_sections,
            f"sections={len(actual_sections)}/{len(expected_sections)}",
        ),
        _assertion(
            "A07_navigation_only_not_evidence",
            card.use_scope == "navigation_only_not_evidence"
            and card.evidence_eligible is False,
            "use_scope=navigation_only_not_evidence,evidence_eligible=false",
        ),
        _assertion(
            "A08_no_persisted_credentials",
            all(marker not in persisted_payload for marker in forbidden_secret_markers),
            "00-09 contain no key environment name, Bearer key, or Authorization header",
        ),
        _assertion(
            "A09_api_gap_preserved_with_quality_boundary",
            bool(present_api_probes) and not card.important_apis,
            "important_apis is empty while source probes exist; quality is not qualified",
        ),
        _assertion(
            "A10_catalog_remains_navigation_only",
            catalog.document_count == 1
            and catalog.use_scope == "navigation_only_not_evidence"
            and catalog.evidence_eligible is False
            and catalog.production_qualified is False,
            f"catalog_id={catalog.catalog_id},documents=1,evidence=false,production=false",
        ),
    )
    quality_issues = (
        {
            "code": "important_api_navigation_recall_observed_low",
            "severity": "warning",
            "evidence": {
                "explicit_api_navigation_probes": present_api_probes,
                "returned_important_apis": card.important_apis,
            },
            "qualification": "probe_observation_not_exhaustive_api_truth",
        },
    )
    assertion_payload = {
        "schema_version": "document-card-e2e-assertions-v1",
        "plan_id": bundle.plan.plan_id,
        "receipt_id": receipt.receipt_id,
        "contract_status": "PASS",
        "contract_assertions": assertions,
        "content_quality_status": "NOT_QUALIFIED",
        "quality_observations": quality_issues,
    }
    usage = None if receipt.usage is None else receipt.usage.model_dump(mode="json")
    summary = {
        "schema_version": "document-card-e2e-summary-v1",
        "status": "PASS",
        "chain_integrity_status": "PASS",
        "provider_result_status": receipt.status,
        "document_navigation_quality_status": "not_qualified",
        "important_api_spot_check_status": "gap_observed",
        "human_truth_available": False,
        "navigation_precision_recall_available": False,
        "production_eligible": False,
        "execution_status": "PASS",
        "content_quality_status": "NOT_QUALIFIED",
        "source_ref": card.source_ref.model_dump(mode="json"),
        "document_id": card.document_id,
        "document_map_id": card.document_map_id,
        "request_id": receipt.request_id,
        "plan_id": bundle.plan.plan_id,
        "wire_body_sha256": bundle.plan.wire_body_sha256,
        "receipt_id": receipt.receipt_id,
        "card_id": card.card_id,
        "attempt_count": receipt.attempt_count,
        "retry_count": receipt.retry_count,
        "latency_ms": receipt.latency_ms,
        "usage": usage,
        "source_line_count": bundle.document_map.source_line_count,
        "document_section_count": len(bundle.document_map.sections),
        "section_summary_count": len(card.section_summaries),
        "primary_topic_count": len(card.primary_topics),
        "important_api_count": len(card.important_apis),
        "explicit_api_navigation_probes": present_api_probes,
        "observed_issue_codes": tuple(item["code"] for item in quality_issues),
        "use_scope": card.use_scope,
        "evidence_eligible": card.evidence_eligible,
        "inspect_network_attempted": False,
        "live_network_attempted": receipt.network_attempted,
        "catalog_built": True,
        "catalog_id": catalog.catalog_id,
        "catalog_document_count": catalog.document_count,
        "catalog_routing_executed": False,
        "retrieval_executed": False,
        "evidence_pack_generated": False,
        "finding_generated": False,
        "excluded_stages": (
            "document_catalog_routing",
            "clause_retrieval",
            "evidence_pack",
            "finding",
            "gitcode_publication",
        ),
        "output_files": tuple(path.name for path in persisted_live_files)
        + (
            "10_assertions.json",
            "11_summary.json",
            "12_document-catalog.json",
            "REPORT.md",
        ),
        "report": "REPORT.md",
        "qualification": "single_document_navigation_smoke_not_population_quality_evidence",
    }

    report = _render_report(
        explicit_api_navigation_probes=present_api_probes,
        card=card,
        catalog=catalog,
        receipt=receipt,
    )
    with tempfile.TemporaryDirectory(prefix=".e2e3-report-", dir=ROOT) as temporary:
        staging = Path(temporary)
        (staging / "10_assertions.json").write_bytes(_json_bytes(assertion_payload))
        (staging / "11_summary.json").write_bytes(_json_bytes(summary))
        (staging / "12_document-catalog.json").write_bytes(
            _json_bytes(catalog.model_dump(mode="json"))
        )
        (staging / "REPORT.md").write_text(report, encoding="utf-8")
        os.replace(staging / "10_assertions.json", ARTIFACTS / "10_assertions.json")
        os.replace(staging / "11_summary.json", ARTIFACTS / "11_summary.json")
        os.replace(
            staging / "12_document-catalog.json",
            ARTIFACTS / "12_document-catalog.json",
        )
        os.replace(staging / "REPORT.md", REPORT)

    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
