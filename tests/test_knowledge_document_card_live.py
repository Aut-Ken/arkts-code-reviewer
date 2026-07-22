from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import pytest

from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekCredentialUnavailableError,
    DeepSeekHttpResponse,
)
from arkts_code_reviewer.knowledge.document_first import (
    DocumentCardDraft,
    DocumentSectionSummary,
    build_document_card,
    build_document_card_dispatch_plan,
    build_document_card_request,
    build_markdown_document_map,
    load_document_card_export_policy,
    load_document_card_prompt,
    verify_document_card_dispatch_plan,
)
from arkts_code_reviewer.knowledge.document_first._canonical import (
    canonical_json,
    sha256_text,
)
from arkts_code_reviewer.knowledge.document_first.export_policy import (
    DocumentCardExportPolicy,
    DocumentCardExportSourceRule,
)
from arkts_code_reviewer.knowledge.document_first.live_smoke import (
    DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT,
    DocumentCardRunArtifacts,
    DocumentCardSmokeBundle,
    DocumentCardSmokeError,
    _seal_receipt,
    materialize_document_card_inspection,
    materialize_document_card_run,
    run_document_card_live_once,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef
from arkts_code_reviewer.knowledge.registry import (
    CheckoutProfile,
    GovernanceProfile,
    IngestionProfile,
    SourceRecord,
    SourceRegistry,
)

REVISION = "1" * 40
SOURCE_ID = "synthetic-docs"
RELATIVE_PATH = "docs/example.md"
BODY = "# TaskPool 与 Worker\n\n## 选择建议\n\n根据任务特点选择并发模型。\n"


class _FakeCredentialProvider:
    @property
    def credential_scope_id(self) -> str:
        return "deepseek-credential-scope:sha256:" + "2" * 64

    def is_configured(self) -> bool:
        return True

    def get_api_key(self) -> str:
        return "test-only-key"


class _FakeTransport:
    def __init__(self, response: DeepSeekHttpResponse) -> None:
        self.response = response
        self.send_count = 0

    def send(self, plan: object, *, api_key: str) -> DeepSeekHttpResponse:
        del plan
        assert api_key == "test-only-key"
        self.send_count += 1
        return self.response


class _SlowTransport:
    def __init__(self) -> None:
        self.send_count = 0

    def send(self, plan: object, *, api_key: str) -> DeepSeekHttpResponse:
        del plan, api_key
        self.send_count += 1
        time.sleep(5)
        raise AssertionError("absolute wall-clock deadline did not interrupt transport")


class _UnavailableCredentialProvider(_FakeCredentialProvider):
    def get_api_key(self) -> str:
        raise DeepSeekCredentialUnavailableError("test credential unavailable")


def _registry(*, raw_prompt_use_allowed: bool = True) -> SourceRegistry:
    return SourceRegistry(
        schema_version=1,
        updated_at=date(2026, 7, 22),
        sources=(
            SourceRecord(
                id=SOURCE_ID,
                group="knowledge_source",
                kind="official_documentation",
                remote="https://example.invalid/synthetic-docs.git",
                local_path=Path("/tmp/synthetic-docs"),
                env_override="SYNTHETIC_DOCS_PATH",
                branch="main",
                revision=REVISION,
                shallow_clone=True,
                checkout=CheckoutProfile(mode="full"),
                use_for=("retrieval_knowledge",),
                ingestion=IngestionProfile(
                    include=("docs/**/*.md",),
                    exclude=(),
                    execute_repository_scripts=False,
                    index_as_normative_knowledge=True,
                ),
                governance=GovernanceProfile(
                    authority="official_documentation",
                    curation_required=True,
                    raw_prompt_use_allowed=raw_prompt_use_allowed,
                ),
            ),
        ),
    )


def _policy(
    *,
    wall_clock_timeout_ms: int = 120_000,
    allowed_relative_path: str = RELATIVE_PATH,
) -> DocumentCardExportPolicy:
    return DocumentCardExportPolicy(
        schema_version="document-card-export-policy-v1",
        version="document-card-test-v1",
        enabled=True,
        provider="deepseek",
        endpoint_url="https://api.deepseek.com/chat/completions",
        allowed_models=("deepseek-v4-pro",),
        allowed_prompt_versions=("deepseek-document-card-v1",),
        source_allowlist=(
            DocumentCardExportSourceRule(
                source_id=SOURCE_ID,
                revision=REVISION,
                relative_paths=(allowed_relative_path,),
            ),
        ),
        max_document_characters=20_000,
        max_request_body_bytes=100_000,
        max_sections=32,
        max_output_tokens=4_096,
        wall_clock_timeout_ms=wall_clock_timeout_ms,
        max_response_bytes=2_000_000,
        max_attempts=1,
        retry_policy="none_single_attempt_v1",
        thinking="disabled",
        temperature=0,
        response_format="json_object",
        tls_verify=True,
        follow_redirects=False,
        trust_env=False,
        qualification=("development_single_document_navigation_smoke_not_production_approval"),
    )


def _document(*, body: str = BODY) -> NormalizedDocument:
    return NormalizedDocument(
        document_id=f"{SOURCE_ID}:{RELATIVE_PATH}",
        source_ref=SourceRef(
            source_id=SOURCE_ID,
            revision=REVISION,
            relative_path=RELATIVE_PATH,
            anchor="document",
            authority="official_documentation",
            content_hash=sha256_text(body),
        ),
        media_type="text/markdown",
        title="TaskPool 与 Worker",
        heading_tree=(),
        body=body,
        language="zh-CN",
        release="HarmonyOS NEXT",
        api_level=18,
        language_mode="static",
        adapter_version="test-adapter-v1",
    )


def _bundle(
    *,
    registry: SourceRegistry | None = None,
    body: str = BODY,
    policy: DocumentCardExportPolicy | None = None,
) -> DocumentCardSmokeBundle:
    document = _document(body=body)
    document_map = build_markdown_document_map(document)
    prompt = load_document_card_prompt()
    export_policy = _policy() if policy is None else policy
    source_registry = _registry() if registry is None else registry
    request = build_document_card_request(
        document=document,
        document_map=document_map,
        registry=source_registry,
        policy=export_policy,
        prompt=prompt,
    )
    plan = build_document_card_dispatch_plan(
        document=document,
        document_map=document_map,
        request=request,
        registry=source_registry,
        policy=export_policy,
        prompt=prompt,
    )
    return DocumentCardSmokeBundle(
        registry=source_registry,
        policy=export_policy,
        prompt=prompt,
        document=document,
        document_map=document_map,
        request=request,
        plan=plan,
    )


def _draft(bundle: DocumentCardSmokeBundle, *, reverse: bool = False) -> DocumentCardDraft:
    summaries = tuple(
        DocumentSectionSummary(
            section_id=section.section_id,
            summary=f"导航摘要：{section.title}",
        )
        for section in bundle.document_map.sections
    )
    if reverse:
        summaries = tuple(reversed(summaries))
    return DocumentCardDraft(
        document_id=bundle.document.document_id,
        summary="比较 TaskPool 与 Worker 的适用场景。",
        primary_topics=("并发模型", "任务调度"),
        important_apis=("TaskPool", "Worker"),
        section_summaries=summaries,
    )


def _provider_response(
    draft: DocumentCardDraft,
    *,
    finish_reason: str = "stop",
    completion_tokens: int = 100,
) -> DeepSeekHttpResponse:
    content = json.dumps(draft.model_dump(mode="json"), ensure_ascii=False)
    body = json.dumps(
        {
            "id": "response-test-1",
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "index": 0,
                    "message": {"content": content, "role": "assistant"},
                    "logprobs": None,
                }
            ],
            "created": 1,
            "model": "deepseek-v4-pro",
            "object": "chat.completion",
            "system_fingerprint": "test-fingerprint",
            "usage": {
                "completion_tokens": completion_tokens,
                "prompt_tokens": 200,
                "total_tokens": 200 + completion_tokens,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 200,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    return DeepSeekHttpResponse(
        status_code=200,
        body=body,
        retry_after_ms=None,
        latency_ms=25,
    )


def _run(
    bundle: DocumentCardSmokeBundle,
    *,
    state_dir: Path,
    transport: _FakeTransport,
) -> DocumentCardRunArtifacts:
    return run_document_card_live_once(
        bundle=bundle,
        approved_plan_id=bundle.plan.plan_id,
        approved_wire_body_sha256=bundle.plan.wire_body_sha256,
        reserved_max_output_tokens=bundle.plan.wire_payload.max_tokens,
        acknowledgement=DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT,
        state_dir=state_dir,
        credential_provider=_FakeCredentialProvider(),
        transport=transport,
    )


def test_default_policy_and_prompt_bind_one_real_document() -> None:
    policy = load_document_card_export_policy()
    prompt = load_document_card_prompt()

    assert policy.permits_source(
        source_id="openharmony-docs",
        revision="c8f5fb6c2fe03cf66b8a41c196ad7fc5e7891c47",
        relative_path="zh-cn/application-dev/arkts-utils/taskpool-vs-worker.md",
    )
    assert prompt.prompt_version == "deepseek-document-card-v1"
    assert "navigation_only_not_evidence" in prompt.text


def test_request_and_plan_rebuild_deterministically() -> None:
    bundle = _bundle()
    second = _bundle()

    assert bundle.request == second.request
    assert bundle.plan == second.plan
    assert bundle.plan.wire_body_sha256 == sha256_text(bundle.plan.wire_body_json)
    assert bundle.plan.qualification == "plan_not_authorization"
    assert bundle.plan.wire_input.markdown_body == BODY
    verify_document_card_dispatch_plan(
        bundle.plan,
        document=bundle.document,
        document_map=bundle.document_map,
        request=bundle.request,
        registry=bundle.registry,
        policy=bundle.policy,
        prompt=bundle.prompt,
    )


def test_registry_raw_prompt_lock_is_mandatory() -> None:
    document = _document()
    document_map = build_markdown_document_map(document)

    with pytest.raises(ValueError, match="registry policy does not permit"):
        build_document_card_request(
            document=document,
            document_map=document_map,
            registry=_registry(raw_prompt_use_allowed=False),
            policy=_policy(),
            prompt=load_document_card_prompt(),
        )


def test_independent_export_allowlist_is_mandatory() -> None:
    document = _document()
    document_map = build_markdown_document_map(document)

    with pytest.raises(ValueError, match="exact export allowlist"):
        build_document_card_request(
            document=document,
            document_map=document_map,
            registry=_registry(),
            policy=_policy(allowed_relative_path="docs/another.md"),
            prompt=load_document_card_prompt(),
        )


def test_inspection_materializes_exact_inputs_without_live_result(tmp_path: Path) -> None:
    bundle = _bundle()
    output_root = tmp_path / "artifacts"

    inspection = materialize_document_card_inspection(bundle, output_root=output_root)
    output_dir = Path(str(inspection["artifact_directory"]))

    assert inspection["network_attempted"] is False
    assert (output_dir / "01_source.md").read_text(encoding="utf-8") == BODY
    assert (output_dir / "04_dispatch-plan.json").is_file()
    assert not (output_dir / "08_document-card.json").exists()
    assert stat_mode(output_dir / "01_source.md") == 0o600


def test_atomic_writer_handles_short_os_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle()
    real_write = os.write

    def short_write(descriptor: int, content: bytes | memoryview) -> int:
        return real_write(descriptor, content[:7])

    monkeypatch.setattr(os, "write", short_write)
    inspection = materialize_document_card_inspection(
        bundle,
        output_root=tmp_path / "artifacts",
    )
    output_dir = Path(str(inspection["artifact_directory"]))

    assert (output_dir / "01_source.md").read_text(encoding="utf-8") == BODY
    assert (
        json.loads((output_dir / "05_inspection.json").read_text(encoding="utf-8"))["plan_id"]
        == bundle.plan.plan_id
    )


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_wrong_exact_body_approval_never_dispatches(tmp_path: Path) -> None:
    bundle = _bundle()
    transport = _FakeTransport(_provider_response(_draft(bundle)))

    with pytest.raises(DocumentCardSmokeError, match="approved_body_sha256_mismatch"):
        run_document_card_live_once(
            bundle=bundle,
            approved_plan_id=bundle.plan.plan_id,
            approved_wire_body_sha256="sha256:" + "0" * 64,
            reserved_max_output_tokens=bundle.plan.wire_payload.max_tokens,
            acknowledgement=DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT,
            state_dir=tmp_path / "state",
            credential_provider=_FakeCredentialProvider(),
            transport=transport,
        )

    assert transport.send_count == 0
    assert not (tmp_path / "state").exists()


def test_unavailable_credential_does_not_consume_attempt_or_dispatch(tmp_path: Path) -> None:
    bundle = _bundle()
    transport = _FakeTransport(_provider_response(_draft(bundle)))

    with pytest.raises(DocumentCardSmokeError, match="credential_unavailable"):
        run_document_card_live_once(
            bundle=bundle,
            approved_plan_id=bundle.plan.plan_id,
            approved_wire_body_sha256=bundle.plan.wire_body_sha256,
            reserved_max_output_tokens=bundle.plan.wire_payload.max_tokens,
            acknowledgement=DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT,
            state_dir=tmp_path / "state",
            credential_provider=_UnavailableCredentialProvider(),
            transport=transport,
        )

    assert transport.send_count == 0
    assert not (tmp_path / "state").exists()


def test_valid_provider_result_builds_navigation_only_card(tmp_path: Path) -> None:
    bundle = _bundle()
    transport = _FakeTransport(_provider_response(_draft(bundle)))

    artifacts = _run(bundle, state_dir=tmp_path / "state", transport=transport)

    assert transport.send_count == 1
    assert artifacts.receipt.status == "valid_card"
    assert artifacts.card is not None
    assert artifacts.card.evidence_eligible is False
    assert artifacts.card.use_scope == "navigation_only_not_evidence"

    inspection = materialize_document_card_inspection(bundle, output_root=tmp_path / "out")
    output_dir = materialize_document_card_run(
        bundle,
        artifacts,
        output_root=tmp_path / "out",
    )
    assert output_dir == Path(str(inspection["artifact_directory"]))
    assert (output_dir / "07_document-card-draft.json").is_file()
    assert (output_dir / "08_document-card.json").is_file()
    assert (output_dir / "09_receipt.json").is_file()


def test_invalid_section_order_is_retained_as_failed_receipt(tmp_path: Path) -> None:
    bundle = _bundle()
    transport = _FakeTransport(_provider_response(_draft(bundle, reverse=True)))

    artifacts = _run(bundle, state_dir=tmp_path / "state", transport=transport)

    assert artifacts.receipt.status == "document_card_invalid"
    assert artifacts.receipt.failure_code == "document_card_draft_invalid"
    assert artifacts.raw_response_body is not None
    assert artifacts.draft is None
    assert artifacts.card is None


def test_non_stop_finish_reason_never_builds_card(tmp_path: Path) -> None:
    bundle = _bundle()
    transport = _FakeTransport(_provider_response(_draft(bundle), finish_reason="length"))

    artifacts = _run(bundle, state_dir=tmp_path / "state", transport=transport)

    assert artifacts.receipt.status == "provider_response_invalid"
    assert artifacts.receipt.failure_code == "finish_reason_not_stop"
    assert artifacts.card is None


def test_reported_completion_tokens_cannot_exceed_reserved_budget(tmp_path: Path) -> None:
    bundle = _bundle()
    transport = _FakeTransport(
        _provider_response(
            _draft(bundle),
            completion_tokens=bundle.plan.wire_payload.max_tokens + 1,
        )
    )

    artifacts = _run(bundle, state_dir=tmp_path / "state", transport=transport)

    assert artifacts.receipt.status == "provider_response_invalid"
    assert artifacts.receipt.failure_code == "usage_exceeds_reserved_output"
    assert artifacts.card is None


def test_absolute_wall_clock_deadline_returns_typed_transport_receipt(tmp_path: Path) -> None:
    bundle = _bundle(policy=_policy(wall_clock_timeout_ms=1_000))
    transport = _SlowTransport()

    artifacts = run_document_card_live_once(
        bundle=bundle,
        approved_plan_id=bundle.plan.plan_id,
        approved_wire_body_sha256=bundle.plan.wire_body_sha256,
        reserved_max_output_tokens=bundle.plan.wire_payload.max_tokens,
        acknowledgement=DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT,
        state_dir=tmp_path / "state",
        credential_provider=_FakeCredentialProvider(),
        transport=transport,
    )

    assert transport.send_count == 1
    assert artifacts.receipt.status == "transport_error"
    assert artifacts.receipt.failure_code == "transport_failed"


def test_run_artifacts_cannot_be_materialized_under_another_bundle(tmp_path: Path) -> None:
    first = _bundle()
    second = _bundle(body=BODY + "\n补充说明。\n")
    transport = _FakeTransport(_provider_response(_draft(first)))
    artifacts = _run(first, state_dir=tmp_path / "state", transport=transport)

    with pytest.raises(DocumentCardSmokeError, match="run_artifacts_bundle_mismatch"):
        materialize_document_card_run(
            second,
            artifacts,
            output_root=tmp_path / "out",
        )


def test_raw_provider_response_is_bound_to_draft_card_and_receipt(tmp_path: Path) -> None:
    bundle = _bundle()
    original = _run(
        bundle,
        state_dir=tmp_path / "state",
        transport=_FakeTransport(_provider_response(_draft(bundle))),
    )
    forged_draft = DocumentCardDraft(
        document_id=bundle.document.document_id,
        summary="与原始模型回答不同的伪造摘要。",
        primary_topics=("并发模型", "伪造主题"),
        important_apis=("TaskPool", "Worker"),
        section_summaries=_draft(bundle).section_summaries,
    )
    forged_card = build_document_card(
        bundle.document,
        bundle.document_map,
        forged_draft,
    )
    receipt_payload = original.receipt.model_dump(
        mode="json",
        exclude={"receipt_id"},
    )
    receipt_payload["draft_sha256"] = sha256_text(
        canonical_json(forged_draft.model_dump(mode="json"))
    )
    receipt_payload["card_id"] = forged_card.card_id
    forged = DocumentCardRunArtifacts(
        receipt=_seal_receipt(receipt_payload),
        raw_response_body=original.raw_response_body,
        draft=forged_draft,
        card=forged_card,
    )

    with pytest.raises(
        DocumentCardSmokeError,
        match="run_artifacts_provider_chain_mismatch",
    ):
        materialize_document_card_run(
            bundle,
            forged,
            output_root=tmp_path / "out",
        )


def test_live_prompt_summary_limit_does_not_change_v1_artifact_schema(tmp_path: Path) -> None:
    bundle = _bundle()
    long_draft = DocumentCardDraft(
        document_id=bundle.document.document_id,
        summary="长" * 501,
        primary_topics=("并发模型",),
        important_apis=("TaskPool",),
        section_summaries=_draft(bundle).section_summaries,
    )
    transport = _FakeTransport(_provider_response(long_draft))

    artifacts = _run(bundle, state_dir=tmp_path / "state", transport=transport)

    assert len(long_draft.summary) == 501
    assert artifacts.receipt.status == "document_card_invalid"
    assert artifacts.receipt.failure_code == "document_card_draft_invalid"
    assert artifacts.card is None


def test_attempt_marker_prevents_replay(tmp_path: Path) -> None:
    bundle = _bundle()
    transport = _FakeTransport(_provider_response(_draft(bundle)))
    state_dir = tmp_path / "state"
    first = _run(bundle, state_dir=state_dir, transport=transport)
    assert first.receipt.status == "valid_card"

    with pytest.raises(DocumentCardSmokeError, match="attempt_already_consumed"):
        _run(bundle, state_dir=state_dir, transport=transport)

    assert transport.send_count == 1
