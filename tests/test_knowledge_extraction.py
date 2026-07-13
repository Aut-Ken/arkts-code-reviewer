from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from arkts_code_reviewer.knowledge.build import (
    NormalizedKnowledgeBuild,
    NormalizedSeedDocument,
)
from arkts_code_reviewer.knowledge.extraction import (
    KnowledgeExtractionBuild,
    build_knowledge_extraction,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef

ROOT = Path(__file__).resolve().parents[1]
SIDECAR_GRAMMAR = ROOT / "sidecars/knowledge-api-parser/node_modules/tree-sitter-arkts/package.json"


def _source_ref(relative_path: str, body: str) -> SourceRef:
    return SourceRef(
        source_id="synthetic",
        revision="0" * 40,
        relative_path=relative_path,
        anchor="document",
        authority="test",
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
    )


def _normalized_build() -> NormalizedKnowledgeBuild:
    documents: list[NormalizedSeedDocument] = []
    for index in range(16):
        body = f"# 规则 {index:02d}\n\n调用方必须执行检查 {index:02d}。\n"
        path = f"docs/rule-{index:02d}.md"
        documents.append(
            NormalizedSeedDocument(
                domains=("test-domain",),
                document=NormalizedDocument(
                    document_id=f"synthetic:{path}",
                    source_ref=_source_ref(path, body),
                    media_type="text/markdown",
                    title=f"规则 {index:02d}",
                    heading_tree=(),
                    body=body,
                    language="zh-CN",
                    adapter_version="test-markdown-v1",
                ),
            )
        )
    for index in range(8):
        body = (
            f"/** @since 9 */\ndeclare namespace api{index} {{\n"
            "  function run(): void;\n}\n"
        )
        path = f"api/api-{index:02d}.d.ts"
        documents.append(
            NormalizedSeedDocument(
                domains=("test-domain",),
                document=NormalizedDocument(
                    document_id=f"synthetic:{path}",
                    source_ref=_source_ref(path, body),
                    media_type="text/typescript-declaration",
                    title=f"api-{index:02d}.d.ts",
                    heading_tree=(),
                    body=body,
                    language="en",
                    adapter_version="test-api-v1",
                ),
            )
        )
    ordered = tuple(sorted(documents, key=lambda item: item.document.document_id))
    adapter_versions = ("test-api-v1", "test-markdown-v1")
    payload = {
        "seed_id": "knowledge-seed-v1",
        "seed_fingerprint": "knowledge-seed:sha256:" + "1" * 64,
        "source_bundle_id": "source-bundle:sha256:" + "2" * 64,
        "adapter_versions": adapter_versions,
        "documents": [
            {
                "document_id": item.document.document_id,
                "content_hash": item.document.source_ref.content_hash,
                "domains": item.domains,
                "adapter_version": item.document.adapter_version,
            }
            for item in ordered
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return NormalizedKnowledgeBuild(
        build_id="knowledge-build:sha256:" + hashlib.sha256(raw.encode()).hexdigest(),
        seed_id="knowledge-seed-v1",
        seed_fingerprint="knowledge-seed:sha256:" + "1" * 64,
        source_bundle_id="source-bundle:sha256:" + "2" * 64,
        adapter_versions=adapter_versions,
        documents=ordered,
    )


def test_extraction_build_is_complete_repeatable_and_json_round_trippable() -> None:
    if not SIDECAR_GRAMMAR.is_file():
        pytest.skip("Knowledge API sidecar dependencies are not installed")
    normalized = _normalized_build()

    first = build_knowledge_extraction(normalized)
    second = build_knowledge_extraction(normalized)

    assert first == second
    assert sum(len(item.clauses) for item in first.documents) == 16
    assert sum(len(item.api_symbols) for item in first.documents) == 8
    assert all(
        clause.proposed_status == "Draft"
        for item in first.documents
        for clause in item.clauses
    )
    assert KnowledgeExtractionBuild.model_validate_json(first.model_dump_json()) == first
