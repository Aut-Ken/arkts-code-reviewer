from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.knowledge.document_first._canonical import sha256_text
from arkts_code_reviewer.knowledge.document_first.campaign import (
    DEFAULT_DOCUMENT_CARD_CAMPAIGN_EXPORT_POLICY_PATH,
    DEFAULT_DOCUMENT_CARD_CAMPAIGN_SELECTION_PATH,
    DocumentCardCampaignBundle,
    DocumentCardCampaignPlanBundle,
    DocumentCardCampaignSelection,
    DocumentCardCampaignSelectionItem,
    assemble_document_card_campaign,
    load_document_card_campaign_inspection,
    load_document_card_campaign_selection,
    materialize_document_card_campaign,
    verify_document_card_campaign,
)
from arkts_code_reviewer.knowledge.document_first.enrichment import (
    build_document_card_dispatch_plan,
    build_document_card_request,
    load_document_card_prompt,
)
from arkts_code_reviewer.knowledge.document_first.export_policy import (
    DocumentCardExportPolicy,
    DocumentCardExportSourceRule,
    load_document_card_export_policy,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    build_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef
from arkts_code_reviewer.knowledge.registry import (
    CheckoutProfile,
    GovernanceProfile,
    IngestionProfile,
    SourceRecord,
    SourceRegistry,
    build_source_bundle,
)
from arkts_code_reviewer.knowledge.seed import KnowledgeSeed, SeedDocument

REVISION = "1" * 40
SOURCE_ID = "openharmony-docs"
PATHS = ("docs/selected-a.md", "docs/selected-b.md")


def _registry(tmp_path: Path) -> SourceRegistry:
    return SourceRegistry(
        schema_version=1,
        updated_at=date(2026, 7, 22),
        sources=(
            SourceRecord(
                id=SOURCE_ID,
                group="knowledge_source",
                kind="official_documentation",
                remote="https://example.invalid/docs.git",
                local_path=tmp_path,
                env_override="SYNTHETIC_CAMPAIGN_DOCS_PATH",
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
                    raw_prompt_use_allowed=True,
                ),
            ),
        ),
    )


def _seed() -> KnowledgeSeed:
    paths = tuple(sorted((*PATHS, *(f"docs/filler-{index:02d}.md" for index in range(22)))))
    return KnowledgeSeed(
        schema_version="knowledge-seed-v1",
        seed_id="knowledge-seed-v1",
        description="Synthetic campaign seed",
        source_ids=(SOURCE_ID,),
        domains=("campaign-test",),
        documents=tuple(
            SeedDocument(
                source_id=SOURCE_ID,
                relative_path=path,
                domains=("campaign-test",),
            )
            for path in paths
        ),
    )


def _selection() -> DocumentCardCampaignSelection:
    return DocumentCardCampaignSelection(
        schema_version="document-card-campaign-selection-v1",
        version="synthetic-campaign-v1",
        documents=tuple(
            DocumentCardCampaignSelectionItem(
                source_id=SOURCE_ID,
                revision=REVISION,
                relative_path=path,
            )
            for path in PATHS
        ),
        qualification="pilot_selection_not_export_or_execution_authorization",
    )


def _policy(*, relative_paths: tuple[str, ...] = PATHS) -> DocumentCardExportPolicy:
    return DocumentCardExportPolicy(
        schema_version="document-card-export-policy-v1",
        version="synthetic-campaign-export-v1",
        enabled=True,
        provider="deepseek",
        endpoint_url="https://api.deepseek.com/chat/completions",
        allowed_models=("deepseek-v4-pro",),
        allowed_prompt_versions=("deepseek-document-card-v1",),
        source_allowlist=(
            DocumentCardExportSourceRule(
                source_id=SOURCE_ID,
                revision=REVISION,
                relative_paths=relative_paths,
            ),
        ),
        max_document_characters=20_000,
        max_request_body_bytes=100_000,
        max_sections=32,
        max_output_tokens=4_096,
        wall_clock_timeout_ms=120_000,
        max_response_bytes=2_000_000,
        max_attempts=1,
        retry_policy="none_single_attempt_v1",
        thinking="disabled",
        temperature=0,
        response_format="json_object",
        tls_verify=True,
        follow_redirects=False,
        trust_env=False,
        qualification="development_single_document_navigation_smoke_not_production_approval",
    )


def _document(path: str) -> NormalizedDocument:
    title = Path(path).stem
    body = f"# {title}\n\n## Usage\n\nPinned campaign source.\n"
    return NormalizedDocument(
        document_id=f"{SOURCE_ID}:{path}",
        source_ref=SourceRef(
            source_id=SOURCE_ID,
            revision=REVISION,
            relative_path=path,
            anchor="document",
            authority="official_documentation",
            content_hash=sha256_text(body),
        ),
        media_type="text/markdown",
        title=title,
        heading_tree=(),
        body=body,
        language="zh-CN",
        adapter_version="synthetic-campaign-adapter-v1",
    )


def _bundle(tmp_path: Path) -> DocumentCardCampaignBundle:
    registry = _registry(tmp_path)
    seed = _seed()
    selection = _selection()
    policy = _policy()
    prompt = load_document_card_prompt()
    source_bundle, _verified = build_source_bundle(registry, (SOURCE_ID,), verify=False)
    plans: list[DocumentCardCampaignPlanBundle] = []
    for path in PATHS:
        document = _document(path)
        document_map = build_markdown_document_map(document)
        request = build_document_card_request(
            document=document,
            document_map=document_map,
            registry=registry,
            policy=policy,
            prompt=prompt,
        )
        plan = build_document_card_dispatch_plan(
            document=document,
            document_map=document_map,
            request=request,
            registry=registry,
            policy=policy,
            prompt=prompt,
        )
        plans.append(
            DocumentCardCampaignPlanBundle(
                document=document,
                document_map=document_map,
                request=request,
                plan=plan,
            )
        )
    return assemble_document_card_campaign(
        registry=registry,
        seed=seed,
        selection=selection,
        source_bundle=source_bundle,
        policy=policy,
        prompt=prompt,
        plans=tuple(plans),
    )


def test_campaign_is_deterministic_content_addressed_and_offline(tmp_path: Path) -> None:
    first = _bundle(tmp_path)
    second = _bundle(tmp_path)

    assert first.inspection == second.inspection
    assert first.inspection.campaign_id == second.inspection.campaign_id
    assert first.inspection.document_count == 2
    assert first.inspection.total_attempt_cap == 2
    assert first.inspection.total_output_token_cap == 8_192
    assert first.inspection.total_response_body_bytes == 4_000_000
    assert first.inspection.total_wall_clock_timeout_ms == 240_000
    assert first.inspection.total_request_body_bytes == sum(
        item.plan.wire_body_size_bytes for item in first.plans
    )
    assert first.inspection.network_attempted is False
    assert first.inspection.credential_accessed is False
    assert first.inspection.execution_authorized is False
    assert first.inspection.evidence_eligible is False
    assert first.inspection.production_qualified is False


def test_selection_rejects_duplicates_and_noncanonical_order() -> None:
    item = DocumentCardCampaignSelectionItem(
        source_id=SOURCE_ID,
        revision=REVISION,
        relative_path=PATHS[0],
    )
    with pytest.raises(ValidationError, match="sorted, and unique"):
        DocumentCardCampaignSelection(
            schema_version="document-card-campaign-selection-v1",
            version="duplicate-v1",
            documents=(item, item),
            qualification="pilot_selection_not_export_or_execution_authorization",
        )
    with pytest.raises(ValidationError, match="sorted, and unique"):
        DocumentCardCampaignSelection(
            schema_version="document-card-campaign-selection-v1",
            version="reversed-v1",
            documents=tuple(reversed(_selection().documents)),
            qualification="pilot_selection_not_export_or_execution_authorization",
        )


def test_campaign_rebuild_rejects_policy_ineligible_document(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    ineligible = replace(bundle, policy=_policy(relative_paths=(PATHS[0],)))

    with pytest.raises(ValueError, match="outside export policy"):
        verify_document_card_campaign(ineligible)


def test_campaign_loader_rejects_tampered_totals_and_duplicate_json_key(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    payload = bundle.inspection.model_dump(mode="json")
    payload["total_output_token_cap"] += 1

    with pytest.raises(ValueError, match="aggregate budgets"):
        load_document_card_campaign_inspection(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_document_card_campaign_inspection(
            '{"schema_version":"document-card-campaign-inspection-v1",'
            '"schema_version":"document-card-campaign-inspection-v1"}'
        )


def test_materialization_writes_exact_per_plan_zero_through_five(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    output = materialize_document_card_campaign(bundle, output_root=tmp_path / "artifacts")

    assert materialize_document_card_campaign(
        bundle,
        output_root=tmp_path / "artifacts",
    ) == output
    assert (output / "00_campaign-selection.json").is_file()
    assert (output / "01_campaign-inspection.json").is_file()
    plan_directories = tuple(sorted((output / "plans").iterdir()))
    assert len(plan_directories) == 2
    for plan_directory in plan_directories:
        assert tuple(path.name for path in sorted(plan_directory.iterdir())) == (
            "00_source-manifest.json",
            "01_source.md",
            "02_document-map.json",
            "03_request.json",
            "04_dispatch-plan.json",
            "05_inspection.json",
        )


def test_checked_in_pilot_selection_contains_exact_seven_pending_documents() -> None:
    selection = load_document_card_campaign_selection(
        DEFAULT_DOCUMENT_CARD_CAMPAIGN_SELECTION_PATH
    )

    assert len(selection.documents) == 7
    assert {
        (item.source_id, item.relative_path) for item in selection.documents
    } == {
        (
            "arkui-specs",
            "05-ui-components/03-scroll-container-components/09-tabs-tab-content/"
            "Feat-05-tabs-events-spec.md",
        ),
        (
            "arkui-specs",
            "05-ui-components/08-image-components/01-image/"
            "Feat-05-image-base-memory-opt-spec.md",
        ),
        ("openharmony-docs", "zh-cn/application-dev/arkts-utils/sendable-constraints.md"),
        ("openharmony-docs", "zh-cn/application-dev/arkts-utils/taskpool-introduction.md"),
        ("openharmony-docs", "zh-cn/application-dev/reference/common/js-apis-timer.md"),
        (
            "openharmony-docs",
            "zh-cn/application-dev/ui/state-management/"
            "arkts-page-custom-components-lifecycle.md",
        ),
        (
            "openharmony-docs",
            "zh-cn/application-dev/ui/state-management/arkts-state-management-overview.md",
        ),
    }
    policy = load_document_card_export_policy(
        DEFAULT_DOCUMENT_CARD_CAMPAIGN_EXPORT_POLICY_PATH
    )
    assert all(
        policy.permits_source(
            source_id=item.source_id,
            revision=item.revision,
            relative_path=item.relative_path,
        )
        for item in selection.documents
    )
