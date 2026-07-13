from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.annotation import (
    KnowledgeAnnotationBuild,
    build_knowledge_annotations,
)
from arkts_code_reviewer.knowledge.annotation_config import (
    load_knowledge_annotation_config,
)
from arkts_code_reviewer.knowledge.build import (
    NormalizedKnowledgeBuild,
    NormalizedSeedDocument,
)
from arkts_code_reviewer.knowledge.extraction import (
    ExtractedKnowledgeDocument,
    KnowledgeExtractionBuild,
)
from arkts_code_reviewer.knowledge.models import (
    ApiSymbol,
    Applicability,
    ClauseCandidate,
    NormalizedDocument,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.knowledge.parsing import ExtractedClause
from arkts_code_reviewer.knowledge.registry import (
    CheckoutProfile,
    GovernanceProfile,
    IngestionProfile,
    SourceRecord,
    SourceRegistry,
)
from arkts_code_reviewer.knowledge.review_packets import (
    ExternalModelPolicy,
    KnowledgeModelExportPolicy,
    KnowledgeReviewPacket,
    KnowledgeReviewPacketBuild,
    ModelExportSourceRule,
    build_knowledge_review_packets,
    load_knowledge_model_export_policy,
    load_knowledge_review_prompt,
)

ROOT = Path(__file__).resolve().parents[1]
REVISION = "1" * 40
SOURCE_ID = "synthetic-knowledge"
DOMAIN = "state-management-arkts"


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _source_ref(path: str, body: str, anchor: str = "document") -> SourceRef:
    return SourceRef(
        source_id=SOURCE_ID,
        revision=REVISION,
        relative_path=path,
        anchor=anchor,
        authority="synthetic_test",
        content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )


def _source_registry() -> SourceRegistry:
    return SourceRegistry(
        schema_version=1,
        updated_at=date(2026, 7, 13),
        sources=(
            SourceRecord(
                id=SOURCE_ID,
                group="knowledge_source",
                kind="synthetic",
                remote="https://example.com/synthetic-knowledge.git",
                local_path=Path("/tmp/synthetic-knowledge"),
                env_override="SYNTHETIC_KNOWLEDGE_PATH",
                branch="main",
                revision=REVISION,
                shallow_clone=True,
                checkout=CheckoutProfile(mode="full"),
                use_for=("knowledge-test",),
                ingestion=IngestionProfile(
                    include=("docs/**/*.md",),
                    exclude=(),
                    execute_repository_scripts=False,
                    index_as_normative_knowledge=True,
                ),
                governance=GovernanceProfile(
                    authority="synthetic_test",
                    curation_required=True,
                    raw_prompt_use_allowed=False,
                ),
            ),
        ),
    )


def _artifacts(
    clause_count: int = 26,
) -> tuple[
    NormalizedKnowledgeBuild,
    KnowledgeExtractionBuild,
    KnowledgeAnnotationBuild,
]:
    normalized_documents: list[NormalizedSeedDocument] = []
    extracted_documents: list[ExtractedKnowledgeDocument] = []
    for document_index in range(24):
        path = f"docs/doc-{document_index:02d}.md"
        if document_index == 0:
            rule_lines = [
                f"调用方必须执行状态检查 {rule_index:03d}。"
                for rule_index in range(clause_count)
            ]
            body = "# Rules\n\n" + "\n".join(rule_lines) + "\n"
        else:
            body = f"# Background {document_index:02d}\n"
        document_ref = _source_ref(path, body)
        normalized_documents.append(
            NormalizedSeedDocument(
                domains=(DOMAIN,),
                document=NormalizedDocument(
                    document_id=f"{SOURCE_ID}:{path}",
                    source_ref=document_ref,
                    media_type="text/markdown",
                    title=f"Document {document_index:02d}",
                    heading_tree=(),
                    body=body,
                    language="zh-CN",
                    adapter_version="synthetic-markdown-v1",
                ),
            )
        )
        clauses: list[ExtractedClause] = []
        if document_index == 0:
            for rule_index, text in enumerate(rule_lines):
                line = rule_index + 3
                candidate = ClauseCandidate.create(
                    native_rule_id=f"R-{rule_index:03d}",
                    rule_type="constraint",
                    text=text,
                    heading_path=("Rules",),
                    parent_context=None,
                    neighbor_candidate_ids=(),
                    applicability=Applicability(),
                    source_ref=_source_ref(path, body, f"L{line}-L{line}"),
                    source_span=SourceSpan(start_line=line, end_line=line),
                )
                clauses.append(
                    ExtractedClause(
                        rule_id=f"SYN/R-{rule_index:03d}",
                        proposed_status="Draft",
                        candidate=candidate,
                    )
                )
        extracted_documents.append(
            ExtractedKnowledgeDocument(
                document_id=f"{SOURCE_ID}:{path}",
                domains=(DOMAIN,),
                clauses=tuple(clauses),
                api_symbols=(),
                diagnostics=(),
            )
        )
    ordered_normalized = tuple(
        sorted(normalized_documents, key=lambda item: item.document.document_id)
    )
    normalized_payload = {
        "seed_id": "knowledge-seed-v1",
        "seed_fingerprint": "knowledge-seed:sha256:" + "2" * 64,
        "source_bundle_id": "source-bundle:sha256:" + "3" * 64,
        "adapter_versions": ("synthetic-markdown-v1",),
        "documents": [
            {
                "document_id": item.document.document_id,
                "content_hash": item.document.source_ref.content_hash,
                "domains": item.domains,
                "adapter_version": item.document.adapter_version,
            }
            for item in ordered_normalized
        ],
    }
    normalized = NormalizedKnowledgeBuild(
        build_id=_canonical_hash("knowledge-build", normalized_payload),
        seed_id="knowledge-seed-v1",
        seed_fingerprint="knowledge-seed:sha256:" + "2" * 64,
        source_bundle_id="source-bundle:sha256:" + "3" * 64,
        adapter_versions=("synthetic-markdown-v1",),
        documents=ordered_normalized,
    )
    ordered_extracted = tuple(
        sorted(extracted_documents, key=lambda item: item.document_id)
    )
    extraction_payload = {
        "normalized_build_id": normalized.build_id,
        "source_bundle_id": normalized.source_bundle_id,
        "seed_fingerprint": normalized.seed_fingerprint,
        "parser_versions": ("synthetic-clause-parser-v1",),
        "documents": [item.model_dump(mode="json") for item in ordered_extracted],
    }
    extraction = KnowledgeExtractionBuild(
        build_id=_canonical_hash("knowledge-extraction", extraction_payload),
        normalized_build_id=normalized.build_id,
        source_bundle_id=normalized.source_bundle_id,
        seed_fingerprint=normalized.seed_fingerprint,
        parser_versions=("synthetic-clause-parser-v1",),
        documents=ordered_extracted,
    )
    features = load_default_feature_config()
    annotation_config = load_knowledge_annotation_config(feature_config=features)
    annotations = build_knowledge_annotations(
        extraction,
        feature_config=features,
        config=annotation_config,
    )
    return normalized, extraction, annotations


def _build_local(clause_count: int = 26) -> KnowledgeReviewPacketBuild:
    normalized, extraction, annotations = _artifacts(clause_count)
    features = load_default_feature_config()
    annotation_config = load_knowledge_annotation_config(feature_config=features)
    return build_knowledge_review_packets(
        normalized,
        extraction,
        annotations,
        registry=_source_registry(),
        feature_config=features,
        annotation_config=annotation_config,
        policy=load_knowledge_model_export_policy(),
        prompt=load_knowledge_review_prompt(),
    )


def test_local_packets_are_deterministic_complete_and_exact() -> None:
    first = _build_local()
    second = _build_local()

    assert first == second
    assert len(first.packets) == 2
    assert sum(len(packet.clauses) for packet in first.packets) == 26
    assert all(1 <= len(packet.clauses) <= 25 for packet in first.packets)
    assert all(packet.distribution == "local_only" for packet in first.packets)
    assert all(packet.model_provider is None for packet in first.packets)
    assert len({clause.rule_id for packet in first.packets for clause in packet.clauses}) == 26
    for packet in first.packets:
        assert packet.packet_id == packet.expected_packet_id()
        assert packet.tag_registry
        assert packet.dimension_registry
        assert packet.source_domain_ids
        for excerpt in packet.source_excerpts:
            assert excerpt.start_line >= 1
            assert excerpt.end_line >= excerpt.start_line
            assert excerpt.exact_text_hash == (
                "sha256:" + hashlib.sha256(excerpt.exact_text.encode()).hexdigest()
            )
            assert excerpt.excerpt_id == excerpt.expected_excerpt_id()


def test_packet_rejects_excerpt_registry_and_catalog_drift() -> None:
    packet = _build_local(1).packets[0]
    excerpt_drift = packet.model_dump()
    excerpt_drift["source_excerpts"][0]["exact_text"] += "伪造"
    with pytest.raises(ValidationError, match="exact_text_hash does not match"):
        KnowledgeReviewPacket.model_validate(excerpt_drift)

    registry_drift = packet.model_dump()
    registry_drift["tag_registry"] = registry_drift["tag_registry"][1:]
    with pytest.raises(ValidationError, match="packet_id does not match"):
        KnowledgeReviewPacket.model_validate(registry_drift)

    fake_ref = packet.clauses[0].candidate.source_ref
    fake_api = ApiSymbol.create(
        canonical_name="fake.api",
        module="fake",
        kind="function",
        signature="function fake(): void",
        since=None,
        deprecated_since=None,
        source_ref=fake_ref,
        source_span=packet.clauses[0].candidate.source_span,
        catalog_version="fake-v1",
    )
    catalog_drift = packet.model_dump()
    catalog_drift["api_catalog_slice"] = [fake_api.model_dump()]
    with pytest.raises(ValidationError, match="API snapshot does not cover"):
        KnowledgeReviewPacket.model_validate(catalog_drift)


def test_default_external_model_export_is_fail_closed() -> None:
    normalized, extraction, annotations = _artifacts(1)
    features = load_default_feature_config()
    annotation_config = load_knowledge_annotation_config(feature_config=features)

    with pytest.raises(ValueError, match="external Knowledge model export is disabled"):
        build_knowledge_review_packets(
            normalized,
            extraction,
            annotations,
            registry=_source_registry(),
            feature_config=features,
            annotation_config=annotation_config,
            policy=load_knowledge_model_export_policy(),
            prompt=load_knowledge_review_prompt(),
            distribution="external_model",
            model_provider="xai",
            model_name="grok-test",
        )


def test_external_export_requires_source_governance_even_when_allowlisted() -> None:
    normalized, extraction, annotations = _artifacts(1)
    features = load_default_feature_config()
    annotation_config = load_knowledge_annotation_config(feature_config=features)
    source_path = extraction.documents[0].clauses[0].candidate.source_ref.relative_path
    policy = KnowledgeModelExportPolicy(
        schema_version="knowledge-model-export-policy-v1",
        version="test-enabled-v1",
        max_clauses_per_packet=25,
        max_source_ids_per_packet=3,
        context_lines_before=2,
        context_lines_after=2,
        max_excerpt_lines=120,
        max_packet_excerpt_characters=100_000,
        external_model=ExternalModelPolicy(
            enabled=True,
            provider="xai",
            allowed_models=("grok-test",),
            allowed_prompt_versions=("grok-knowledge-auditor-v1",),
            source_allowlist=(
                ModelExportSourceRule(
                    source_id=SOURCE_ID,
                    revision=REVISION,
                    relative_paths=(source_path,),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="raw prompt use is not allowed"):
        build_knowledge_review_packets(
            normalized,
            extraction,
            annotations,
            registry=_source_registry(),
            feature_config=features,
            annotation_config=annotation_config,
            policy=policy,
            prompt=load_knowledge_review_prompt(),
            distribution="external_model",
            model_provider="xai",
            model_name="grok-test",
        )


def test_export_policy_loader_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(
        (ROOT / "config/knowledge_model_export.yaml").read_text(encoding="utf-8")
        + "version: duplicate\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid Knowledge model export policy"):
        load_knowledge_model_export_policy(path)


def test_packet_tool_reads_json_mode_and_writes_self_contained_local_bundle(
    tmp_path: Path,
) -> None:
    normalized, extraction, annotations = _artifacts(2)
    normalized_path = tmp_path / "normalized.json"
    extraction_path = tmp_path / "extraction.json"
    annotation_path = tmp_path / "annotations.json"
    normalized_path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    extraction_path.write_text(extraction.model_dump_json(indent=2), encoding="utf-8")
    annotation_path.write_text(annotations.model_dump_json(indent=2), encoding="utf-8")
    registry_path = tmp_path / "sources.yaml"
    registry_path.write_text(
        f"""schema_version: 1
updated_at: 2026-07-13
sources:
  - id: {SOURCE_ID}
    group: knowledge_source
    kind: synthetic
    remote: https://example.com/synthetic-knowledge.git
    local_path: /tmp/synthetic-knowledge
    env_override: SYNTHETIC_KNOWLEDGE_PATH
    branch: main
    revision: "{REVISION}"
    shallow_clone: true
    checkout:
      mode: full
      include: []
      profile: null
    use_for: [knowledge-test]
    ingestion:
      include: [docs/**/*.md]
      exclude: []
      execute_repository_scripts: false
      index_as_normative_knowledge: true
    governance:
      authority: synthetic_test
      curation_required: true
      raw_prompt_use_allowed: false
      compiler_or_doc_cross_check_required: false
      positive_example_assumption_allowed: false
      prompt_pattern_review_required: false
      version_and_language_mode_gating_required: false
""",
        encoding="utf-8",
    )
    output = tmp_path / "local-packets"
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/build_knowledge_review_packets.py"),
            "--normalized",
            str(normalized_path),
            "--extraction",
            str(extraction_path),
            "--annotations",
            str(annotation_path),
            "--registry",
            str(registry_path),
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (output / "LOCAL_ONLY_DO_NOT_EXPORT.txt").is_file()
    assert (output / "prompt.md").is_file()
    assert (output / "grok-review-output.schema.json").is_file()
    assert (output / "manifest.json").is_file()
    build = KnowledgeReviewPacketBuild.model_validate_json(
        (output / "build.json").read_text(encoding="utf-8")
    )
    assert build.distribution == "local_only"
    assert sum(len(packet.clauses) for packet in build.packets) == 2
