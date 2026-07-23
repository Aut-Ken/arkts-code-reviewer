from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import DeepSeekHttpResponse
from arkts_code_reviewer.knowledge.document_first._canonical import sha256_text
from arkts_code_reviewer.knowledge.document_first.campaign import (
    DocumentCardCampaignBundle,
    DocumentCardCampaignPlanBundle,
    DocumentCardCampaignSelection,
    DocumentCardCampaignSelectionItem,
    assemble_document_card_campaign,
    materialize_document_card_campaign,
)
from arkts_code_reviewer.knowledge.document_first.campaign_live import (
    DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT,
    DocumentCardCampaignLiveError,
    DocumentCardCampaignRunArtifacts,
    load_document_card_campaign_live_receipt,
    materialize_document_card_campaign_run,
    run_document_card_campaign_live_once,
)
from arkts_code_reviewer.knowledge.document_first.enrichment import (
    DocumentCardDispatchPlan,
    build_document_card_dispatch_plan,
    build_document_card_request,
    load_document_card_prompt,
)
from arkts_code_reviewer.knowledge.document_first.export_policy import (
    DocumentCardExportPolicy,
    DocumentCardExportSourceRule,
)
from arkts_code_reviewer.knowledge.document_first.models import (
    DocumentCardDraft,
    DocumentSectionSummary,
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
SOURCE_ID = "synthetic-docs"
PATHS = ("docs/a.md", "docs/b.md")
SECRET = "campaign-test-secret"


class _CredentialProvider:
    credential_scope_id = "deepseek-credential-scope:sha256:" + "2" * 64

    def __init__(self) -> None:
        self.configured_calls = 0
        self.key_calls = 0

    def is_configured(self) -> bool:
        self.configured_calls += 1
        return True

    def get_api_key(self) -> str:
        self.key_calls += 1
        return SECRET


class _OrderedTransport:
    def __init__(
        self,
        outcomes: dict[str, DeepSeekHttpResponse | RuntimeError],
    ) -> None:
        self.outcomes = outcomes
        self.plan_ids: list[str] = []

    def send(
        self,
        plan: DocumentCardDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        assert api_key == SECRET
        self.plan_ids.append(plan.plan_id)
        outcome = self.outcomes[plan.plan_id]
        if isinstance(outcome, RuntimeError):
            raise outcome
        return outcome


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
                env_override="SYNTHETIC_CAMPAIGN_LIVE_DOCS_PATH",
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


def _policy() -> DocumentCardExportPolicy:
    return DocumentCardExportPolicy(
        schema_version="document-card-export-policy-v1",
        version="synthetic-campaign-live-v1",
        enabled=True,
        provider="deepseek",
        endpoint_url="https://api.deepseek.com/chat/completions",
        allowed_models=("deepseek-v4-pro",),
        allowed_prompt_versions=("deepseek-document-card-v1",),
        source_allowlist=(
            DocumentCardExportSourceRule(
                source_id=SOURCE_ID,
                revision=REVISION,
                relative_paths=PATHS,
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
    title = Path(path).stem.upper()
    body = f"# {title}\n\n## Usage\n\nPinned campaign document {title}.\n"
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
        language="en",
        adapter_version="synthetic-campaign-live-adapter-v1",
    )


def _campaign(tmp_path: Path) -> DocumentCardCampaignBundle:
    registry = _registry(tmp_path)
    seed_paths = tuple(sorted((*PATHS, *(f"docs/filler-{index:02d}.md" for index in range(22)))))
    seed = KnowledgeSeed(
        schema_version="knowledge-seed-v1",
        seed_id="knowledge-seed-v1",
        description="Synthetic live campaign seed",
        source_ids=(SOURCE_ID,),
        domains=("campaign-live-test",),
        documents=tuple(
            SeedDocument(
                source_id=SOURCE_ID,
                relative_path=path,
                domains=("campaign-live-test",),
            )
            for path in seed_paths
        ),
    )
    selection = DocumentCardCampaignSelection(
        schema_version="document-card-campaign-selection-v1",
        version="synthetic-live-v1",
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


def _draft(planned: DocumentCardCampaignPlanBundle) -> DocumentCardDraft:
    return DocumentCardDraft(
        document_id=planned.document.document_id,
        summary=f"Navigation summary for {planned.document.title}.",
        primary_topics=("navigation", planned.document.title),
        important_apis=(planned.document.title,),
        section_summaries=tuple(
            DocumentSectionSummary(
                section_id=section.section_id,
                summary=f"Summary for {section.title}.",
            )
            for section in planned.document_map.sections
        ),
    )


def _response(
    planned: DocumentCardCampaignPlanBundle,
    *,
    latency_ms: int,
) -> DeepSeekHttpResponse:
    content = json.dumps(_draft(planned).model_dump(mode="json"), ensure_ascii=False)
    body = json.dumps(
        {
            "id": f"response-{planned.plan.plan_id[-8:]}",
            "choices": [
                {
                    "finish_reason": "stop",
                    "index": 0,
                    "message": {"content": content, "role": "assistant"},
                    "logprobs": None,
                }
            ],
            "created": 1,
            "model": "deepseek-v4-pro",
            "object": "chat.completion",
            "usage": {
                "completion_tokens": 5,
                "prompt_tokens": 10,
                "total_tokens": 15,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    return DeepSeekHttpResponse(
        status_code=200,
        body=body,
        retry_after_ms=None,
        latency_ms=latency_ms,
    )


def _valid_outcomes(
    campaign: DocumentCardCampaignBundle,
) -> dict[str, DeepSeekHttpResponse | RuntimeError]:
    return {
        planned.plan.plan_id: _response(planned, latency_ms=11 + index)
        for index, planned in enumerate(campaign.plans)
    }


def _run(
    campaign: DocumentCardCampaignBundle,
    *,
    state_dir: Path,
    credential_provider: _CredentialProvider,
    transport: _OrderedTransport,
) -> DocumentCardCampaignRunArtifacts:
    inspection = campaign.inspection
    output_root = state_dir.parent / "artifacts"
    materialize_document_card_campaign(campaign, output_root=output_root)
    return run_document_card_campaign_live_once(
        campaign=campaign,
        approved_campaign_id=inspection.campaign_id,
        approved_plan_set_digest=inspection.plan_set_digest,
        approved_document_count=inspection.document_count,
        approved_total_attempt_cap=inspection.total_attempt_cap,
        approved_total_request_body_bytes=inspection.total_request_body_bytes,
        approved_total_output_token_cap=inspection.total_output_token_cap,
        approved_total_response_body_bytes=inspection.total_response_body_bytes,
        approved_total_wall_clock_timeout_ms=inspection.total_wall_clock_timeout_ms,
        acknowledgement=DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT,
        state_dir=state_dir,
        output_root=output_root,
        credential_provider=credential_provider,
        transport=transport,
    )


def test_all_valid_runs_in_canonical_order_and_seals_aggregates(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    transport = _OrderedTransport(_valid_outcomes(campaign))
    artifacts = _run(
        campaign,
        state_dir=tmp_path / "state",
        credential_provider=_CredentialProvider(),
        transport=transport,
    )

    assert transport.plan_ids == [planned.plan.plan_id for planned in campaign.plans]
    assert artifacts.receipt.outcome == "all_valid"
    assert artifacts.receipt.valid_card_count == 2
    assert artifacts.receipt.attempt_count == 2
    assert artifacts.receipt.retry_count == 0
    assert artifacts.receipt.total_latency_ms == 23
    assert artifacts.receipt.total_prompt_tokens == 20
    assert artifacts.receipt.total_completion_tokens == 10
    assert artifacts.receipt.total_tokens == 30
    assert artifacts.receipt.approved_document_count == 2
    assert artifacts.receipt.observed_total_response_body_bytes == sum(
        len(outcome.body)
        for outcome in transport.outcomes.values()
        if isinstance(outcome, DeepSeekHttpResponse)
    )
    assert artifacts.receipt.evidence_eligible is False
    assert artifacts.receipt.production_qualified is False

    tampered = artifacts.receipt.model_dump(mode="json")
    tampered["total_latency_ms"] += 1
    with pytest.raises(ValueError, match="aggregates"):
        load_document_card_campaign_live_receipt(json.dumps(tampered))


def test_typed_transport_failure_does_not_stop_later_plan(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    outcomes = _valid_outcomes(campaign)
    outcomes[campaign.plans[0].plan.plan_id] = RuntimeError("synthetic transport failure")
    transport = _OrderedTransport(outcomes)

    artifacts = _run(
        campaign,
        state_dir=tmp_path / "state",
        credential_provider=_CredentialProvider(),
        transport=transport,
    )

    assert transport.plan_ids == [planned.plan.plan_id for planned in campaign.plans]
    assert [run.receipt.status for run in artifacts.plan_runs] == [
        "transport_error",
        "valid_card",
    ]
    assert artifacts.receipt.outcome == "completed_with_failures"
    assert artifacts.receipt.valid_card_count == 1
    assert artifacts.receipt.failed_count == 1
    campaign_directory = tmp_path / "artifacts" / campaign.inspection.campaign_id.rsplit(":", 1)[-1]
    plan_directories = tuple(sorted((campaign_directory / "plans").iterdir()))
    assert (plan_directories[0] / "09_receipt.json").is_file()
    assert not (plan_directories[0] / "06_provider-response.raw.json").exists()
    assert (plan_directories[1] / "09_receipt.json").is_file()
    assert (plan_directories[1] / "08_document-card.json").is_file()


@pytest.mark.parametrize(
    ("control", "error_code"),
    (
        ("campaign_id", "approved_campaign_id_mismatch"),
        ("plan_set_digest", "approved_plan_set_digest_mismatch"),
        ("document_count", "approved_document_count_mismatch"),
        ("attempt_cap", "approved_total_attempt_cap_mismatch"),
        ("request_bytes", "approved_total_request_body_bytes_mismatch"),
        ("output_tokens", "approved_total_output_token_cap_mismatch"),
        ("response_bytes", "approved_total_response_body_bytes_mismatch"),
        ("wall_clock", "approved_total_wall_clock_timeout_ms_mismatch"),
        ("acknowledgement", "campaign_export_acknowledgement_missing"),
    ),
)
def test_every_exact_approval_mismatch_precedes_credential_state_and_network(
    tmp_path: Path,
    control: str,
    error_code: str,
) -> None:
    campaign = _campaign(tmp_path)
    credential = _CredentialProvider()
    transport = _OrderedTransport(_valid_outcomes(campaign))
    inspection = campaign.inspection

    with pytest.raises(DocumentCardCampaignLiveError, match=error_code):
        run_document_card_campaign_live_once(
            campaign=campaign,
            approved_campaign_id=(
                inspection.campaign_id + "-wrong"
                if control == "campaign_id"
                else inspection.campaign_id
            ),
            approved_plan_set_digest=(
                inspection.plan_set_digest + "-wrong"
                if control == "plan_set_digest"
                else inspection.plan_set_digest
            ),
            approved_document_count=inspection.document_count
            + (1 if control == "document_count" else 0),
            approved_total_attempt_cap=inspection.total_attempt_cap
            + (1 if control == "attempt_cap" else 0),
            approved_total_request_body_bytes=inspection.total_request_body_bytes
            + (1 if control == "request_bytes" else 0),
            approved_total_output_token_cap=inspection.total_output_token_cap
            + (1 if control == "output_tokens" else 0),
            approved_total_response_body_bytes=inspection.total_response_body_bytes
            + (1 if control == "response_bytes" else 0),
            approved_total_wall_clock_timeout_ms=inspection.total_wall_clock_timeout_ms
            + (1 if control == "wall_clock" else 0),
            acknowledgement=(
                "WRONG_ACKNOWLEDGEMENT"
                if control == "acknowledgement"
                else DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT
            ),
            state_dir=tmp_path / "state",
            output_root=tmp_path / "artifacts",
            credential_provider=credential,
            transport=transport,
        )

    assert credential.configured_calls == 0
    assert credential.key_calls == 0
    assert transport.plan_ids == []
    assert not (tmp_path / "state").exists()


def test_tampered_offline_plan_blocks_credential_state_and_network(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    output_root = tmp_path / "artifacts"
    campaign_directory = materialize_document_card_campaign(
        campaign,
        output_root=output_root,
    )
    first_plan_directory = tuple(sorted((campaign_directory / "plans").iterdir()))[0]
    (first_plan_directory / "04_dispatch-plan.json").write_text(
        '{"tampered":true}\n',
        encoding="utf-8",
    )
    credential = _CredentialProvider()
    transport = _OrderedTransport(_valid_outcomes(campaign))
    inspection = campaign.inspection

    with pytest.raises(
        DocumentCardCampaignLiveError,
        match="campaign_offline_artifact_mismatch",
    ):
        run_document_card_campaign_live_once(
            campaign=campaign,
            approved_campaign_id=inspection.campaign_id,
            approved_plan_set_digest=inspection.plan_set_digest,
            approved_document_count=inspection.document_count,
            approved_total_attempt_cap=inspection.total_attempt_cap,
            approved_total_request_body_bytes=inspection.total_request_body_bytes,
            approved_total_output_token_cap=inspection.total_output_token_cap,
            approved_total_response_body_bytes=inspection.total_response_body_bytes,
            approved_total_wall_clock_timeout_ms=inspection.total_wall_clock_timeout_ms,
            acknowledgement=DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT,
            state_dir=tmp_path / "state",
            output_root=output_root,
            credential_provider=credential,
            transport=transport,
        )

    assert credential.configured_calls == 0
    assert credential.key_calls == 0
    assert transport.plan_ids == []
    assert not (tmp_path / "state").exists()


def test_any_existing_plan_marker_blocks_entire_replay_before_new_request(
    tmp_path: Path,
) -> None:
    campaign = _campaign(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    later_plan_digest = campaign.plans[1].plan.plan_id.rsplit(":", 1)[-1]
    (state_dir / f"{later_plan_digest}.consumed.json").write_text(
        "already consumed\n",
        encoding="utf-8",
    )
    replay_credential = _CredentialProvider()
    replay_transport = _OrderedTransport(_valid_outcomes(campaign))

    with pytest.raises(DocumentCardCampaignLiveError, match="attempt_already_consumed"):
        _run(
            campaign,
            state_dir=state_dir,
            credential_provider=replay_credential,
            transport=replay_transport,
        )

    assert replay_credential.configured_calls == 0
    assert replay_credential.key_calls == 0
    assert replay_transport.plan_ids == []


def test_materialization_uses_ordinal_dirs_rejects_collision_and_never_writes_key(
    tmp_path: Path,
) -> None:
    campaign = _campaign(tmp_path)
    output_root = tmp_path / "artifacts"
    campaign_directory = materialize_document_card_campaign(
        campaign,
        output_root=output_root,
    )
    artifacts = _run(
        campaign,
        state_dir=tmp_path / "state",
        credential_provider=_CredentialProvider(),
        transport=_OrderedTransport(_valid_outcomes(campaign)),
    )

    result_directory = materialize_document_card_campaign_run(
        campaign,
        artifacts,
        output_root=output_root,
    )

    assert result_directory == campaign_directory
    assert (campaign_directory / "02_campaign-live-receipt.json").is_file()
    plan_directories = tuple(sorted((campaign_directory / "plans").iterdir()))
    assert len(plan_directories) == 2
    assert all((directory / "09_receipt.json").is_file() for directory in plan_directories)
    assert all((directory / "08_document-card.json").is_file() for directory in plan_directories)
    assert all(
        SECRET.encode("utf-8") not in path.read_bytes()
        for path in output_root.rglob("*")
        if path.is_file()
    )

    collided = plan_directories[0] / "09_receipt.json"
    collided.write_bytes(b"do-not-overwrite\n")
    with pytest.raises(DocumentCardCampaignLiveError, match="artifact_collision"):
        materialize_document_card_campaign_run(
            campaign,
            artifacts,
            output_root=output_root,
        )
    assert collided.read_bytes() == b"do-not-overwrite\n"
