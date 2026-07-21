"""Build the inspect-only VideoPlayer Static/DeepSeek/Grok Tag pilot.

The sample contains a pinned source file copied from ``applications_app_samples``
and a repository-authored synthetic head/Diff.  It is useful for the first real-code
semantic comparison, but it is not a real MR and is not production-distribution Truth.

This module deliberately stops before any provider call, credential lookup, database
access, embedding, or Retrieval execution.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from arkts_code_reviewer.code_analysis import (
    AnalysisResult,
    ChangedFileInput,
    ChangeSet,
    CodeAnalyzer,
    CodeSourceSnapshot,
    ContextPlanResult,
    normalize_change_set,
)
from arkts_code_reviewer.hybrid_analysis.execution import (
    AITagWireOutput,
    _validate_wire_output,
)
from arkts_code_reviewer.hybrid_analysis.models import AITagJudgment
from arkts_code_reviewer.hybrid_analysis.shadow_campaign import (
    AITagShadowCampaignBundle,
    AITagShadowCampaignUnitArtifacts,
    build_ai_tag_shadow_campaign,
    verify_ai_tag_shadow_campaign_against_upstream,
)

from . import run_e2e as fixture

PILOT_SCHEMA_VERSION = "video-player-tag-pilot-inspection-v1"
PILOT_SAMPLE_CLASSIFICATION = "pinned_real_application_base_with_synthetic_head_and_diff"
GROK_PROXY_MODEL = "grok-4.5"
GROK_PROXY_ROLE = "blind_proxy_judge_not_human_truth"
_CONTEXT_BUDGET = 32_768
_DEEPSEEK_CAMPAIGN_WALL_CLOCK_CAP_MS = 1_200_000
_GROK_PER_CALL_TIMEOUT_MS = 120_000
_GROK_CAMPAIGN_WALL_CLOCK_CAP_MS = 1_800_000
_GROK_MAX_RESPONSE_BYTES = 2_000_000


@dataclass(frozen=True)
class VideoPlayerAnalysisFixture:
    """Typed upstream roots rebuilt without running the Retrieval half of the E2E."""

    change_set: ChangeSet
    analysis_result: AnalysisResult
    context_plan: ContextPlanResult
    source_snapshots: dict[str, CodeSourceSnapshot]

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(unit.unit_id for unit in self.analysis_result.review_units)


@dataclass(frozen=True)
class VideoPlayerTagPilot:
    """In-memory pilot roots plus a metadata-only, safe-to-display inspection."""

    upstream: VideoPlayerAnalysisFixture
    campaign: AITagShadowCampaignBundle
    inspection: dict[str, object]


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _read_json_object(path: Path) -> dict[str, object]:
    value = fixture._read_optional_json(path)  # noqa: SLF001 - package-owned E2E fixture
    if value is None:
        raise ValueError(f"required VideoPlayer fixture metadata is missing: {path}")
    return value


def _review_unit_projection(analysis_result: AnalysisResult) -> list[dict[str, object]]:
    return [
        {
            "source_role": unit.source_role,
            "unit_kind": unit.unit_kind,
            "unit_symbol": unit.unit_symbol,
            "source_span": {
                "start_line": unit.source_span.start_line,
                "end_line": unit.source_span.end_line,
            },
            "context_span": {
                "start_line": unit.context_span.start_line,
                "end_line": unit.context_span.end_line,
            },
            "changed_lines": list(
                unit.changed_old_lines
                if unit.source_role == "base"
                else unit.changed_new_lines
            ),
            "selection_reason": unit.selection_reason,
        }
        for unit in analysis_result.review_units
    ]


def build_video_player_analysis_fixture() -> VideoPlayerAnalysisFixture:
    """Rebuild the fixed ChangeSet -> ContextPlan segment entirely in memory."""

    base_source = fixture._read_source(fixture.BASE_INPUT)  # noqa: SLF001
    head_source = fixture._read_source(fixture.HEAD_INPUT)  # noqa: SLF001
    if (
        fixture._sha256(base_source) != fixture.EXPECTED_BASE_HASH  # noqa: SLF001
        or fixture._line_count(base_source) != fixture.EXPECTED_BASE_LINES  # noqa: SLF001
    ):
        raise ValueError("VideoPlayer base source identity drifted")
    if (
        fixture._sha256(head_source) != fixture.EXPECTED_HEAD_HASH  # noqa: SLF001
        or fixture._line_count(head_source) != fixture.EXPECTED_HEAD_LINES  # noqa: SLF001
    ):
        raise ValueError("VideoPlayer synthetic head identity drifted")

    rendered_diff = fixture._unified_diff(base_source, head_source)  # noqa: SLF001
    if fixture._read_source(fixture.DIFF_INPUT) != rendered_diff:  # noqa: SLF001
        raise ValueError("VideoPlayer diff.patch differs from the pinned base/head pair")

    base_snapshot = fixture._source_snapshot(  # noqa: SLF001
        base_source,
        fixture.SOURCE_REVISION,
    )
    head_snapshot = fixture._source_snapshot(  # noqa: SLF001
        head_source,
        fixture.HEAD_REVISION,
    )
    atoms, opcode_contracts = fixture._sequence_matcher_atoms(  # noqa: SLF001
        base_source,
        head_source,
    )
    mutation_spec = _read_json_object(fixture.INPUTS / "mutation_spec.json")
    if mutation_spec.get("opcodes") != list(opcode_contracts):
        raise ValueError("VideoPlayer mutation opcode contract drifted")

    change_set = normalize_change_set(
        repository=fixture.SOURCE_REPOSITORY,
        base_revision=fixture.SOURCE_REVISION,
        head_revision=fixture.HEAD_REVISION,
        files=(
            ChangedFileInput(
                status="modified",
                old_path=fixture.SOURCE_PATH,
                new_path=fixture.SOURCE_PATH,
                old_snapshot=base_snapshot,
                new_snapshot=head_snapshot,
                atoms=atoms,
            ),
        ),
    )
    change_set.validate()
    snapshots = {
        base_snapshot.source_ref.source_ref_id: base_snapshot,
        head_snapshot.source_ref.source_ref_id: head_snapshot,
    }
    analyzer = CodeAnalyzer()
    analysis_result = analyzer.analyze_change_set(change_set, snapshots)
    context_plan = analyzer.plan_context(
        analysis_result,
        source_snapshots=(base_snapshot, head_snapshot),
        code_context_budget=_CONTEXT_BUDGET,
    )

    expected_review_units = _read_json_object(
        fixture.INPUTS / "expected_review_units.json"
    )
    if (
        expected_review_units.get("schema_version") != "e2e-review-unit-expected-v1"
        or expected_review_units.get("source_path") != fixture.SOURCE_PATH
        or _review_unit_projection(analysis_result) != expected_review_units.get("units")
    ):
        raise ValueError("VideoPlayer ReviewUnit projection differs from human expected")

    expected_feature_routing = _read_json_object(
        fixture.ROOT / "artifacts" / "06_feature_routing.json"
    )
    expected_context_plan = _read_json_object(
        fixture.ROOT / "artifacts" / "07_context_plan.json"
    )
    if analysis_result.feature_routing_result.to_dict() != expected_feature_routing:
        raise ValueError("VideoPlayer FeatureRouting baseline drifted")
    if context_plan.to_dict() != expected_context_plan:
        raise ValueError("VideoPlayer ContextPlan baseline drifted")

    for parsed in analysis_result.file_parse_results:
        quality = parsed.analysis.parser_quality
        if (
            quality.layer != "L1"
            or quality.error_nodes != 0
            or quality.missing_nodes != 0
            or quality.warnings
            or parsed.analysis.diagnostics
        ):
            raise ValueError("VideoPlayer pilot requires clean L1 base/head parsing")

    return VideoPlayerAnalysisFixture(
        change_set=change_set,
        analysis_result=analysis_result,
        context_plan=context_plan,
        source_snapshots=snapshots,
    )


def grok_blind_messages(
    unit: AITagShadowCampaignUnitArtifacts,
) -> tuple[str, str]:
    """Return the same semantic system/user messages without either analyzer result."""

    messages = unit.envelope.wire_payload.messages
    if tuple(message.role for message in messages) != ("system", "user"):
        raise ValueError("AI Tag envelope does not contain the expected system/user pair")
    return messages[0].content, messages[1].content


def validate_grok_blind_output(
    unit: AITagShadowCampaignUnitArtifacts,
    content: str,
) -> tuple[AITagJudgment, ...]:
    """Apply the provider-neutral 24-Tag, evidence-line, and degradation checks."""

    return _validate_wire_output(content, unit.envelope)


def grok_response_schema() -> dict[str, object]:
    """Return the CLI shape constraint; local validation remains authoritative."""

    schema = AITagWireOutput.model_json_schema()
    judgments = schema["properties"]["judgments"]
    judgments["minItems"] = 24
    judgments["maxItems"] = 24
    reason = schema["$defs"]["AITagJudgment"]["properties"]["reason"]
    for branch in reason["anyOf"]:
        if branch.get("type") == "string":
            branch["maxLength"] = 500
    return schema


def _static_summary(campaign: AITagShadowCampaignBundle) -> dict[str, object]:
    exact_counts: Counter[str] = Counter()
    routing_counts: Counter[str] = Counter()
    routing_signatures: set[tuple[str, ...]] = set()
    units_without_exact = 0
    for unit in campaign.units:
        exact = tuple(unit.card.static_tags.exact)
        routing = tuple(unit.card.static_tags.routing)
        exact_counts.update(exact)
        routing_counts.update(routing)
        routing_signatures.add(routing)
        units_without_exact += not exact
    return {
        "feature_routing_id": campaign.manifest.feature_routing_id,
        "unit_count": len(campaign.units),
        "units_with_exact_tags": len(campaign.units) - units_without_exact,
        "units_without_exact_tags": units_without_exact,
        "exact_tag_assignment_count": sum(exact_counts.values()),
        "distinct_exact_tag_count": len(exact_counts),
        "exact_tag_counts": dict(sorted(exact_counts.items())),
        "exact_scope": "unit_exact",
        "routing_tag_assignment_count": sum(routing_counts.values()),
        "distinct_routing_tag_count": len(routing_counts),
        "routing_tag_counts": dict(sorted(routing_counts.items())),
        "routing_scope": "file_hint_not_unit_positive",
        "routing_signature_count": len(routing_signatures),
        "all_units_share_one_routing_signature": len(routing_signatures) == 1,
    }


def _build_inspection(
    upstream: VideoPlayerAnalysisFixture,
    campaign: AITagShadowCampaignBundle,
) -> dict[str, object]:
    grok_schema_json = _canonical_json(grok_response_schema())
    units: list[dict[str, object]] = []
    deepseek_request_rows: list[dict[str, object]] = []
    grok_request_rows: list[dict[str, object]] = []
    for unit in campaign.units:
        grok_system_prompt, grok_user_prompt = grok_blind_messages(unit)
        grok_system_bytes = len(grok_system_prompt.encode("utf-8"))
        grok_user_bytes = len(grok_user_prompt.encode("utf-8"))
        deepseek_request_row = {
            "plan_id": unit.plan.plan_id,
            "wire_body_sha256": unit.plan.wire_body_sha256,
            "wire_body_size_bytes": len(unit.plan.wire_body_json.encode("utf-8")),
            "max_output_tokens": unit.plan.wire_payload.max_tokens,
            "wall_clock_timeout_ms": unit.plan.wall_clock_timeout_ms,
            "max_response_bytes": unit.plan.max_response_bytes,
            "max_attempts": unit.plan.max_attempts,
        }
        deepseek_request_rows.append(deepseek_request_row)
        grok_request_row = {
            "unit_id": unit.card.unit_id,
            "model_view_id": unit.model_view.model_view_id,
            "request_id": unit.request.request_id,
            "system_prompt_sha256": _sha256_text(grok_system_prompt),
            "system_prompt_size_bytes": grok_system_bytes,
            "user_prompt_sha256": _sha256_text(grok_user_prompt),
            "user_prompt_size_bytes": grok_user_bytes,
            "request_input_size_bytes": grok_system_bytes + grok_user_bytes,
            "max_response_bytes": _GROK_MAX_RESPONSE_BYTES,
            "wall_clock_timeout_ms": _GROK_PER_CALL_TIMEOUT_MS,
            "max_attempts": 1,
        }
        grok_request_rows.append(grok_request_row)
        units.append(
            {
                "unit_id": unit.card.unit_id,
                "unit_symbol": unit.card.unit_symbol,
                "unit_kind": unit.card.unit_kind,
                "source_role": unit.card.source_role,
                "line_start": unit.card.code.line_start,
                "line_end": unit.card.code.line_end,
                "changed_line_numbers": list(unit.card.code.changed_line_numbers),
                "card_truncated": unit.card.code.truncated,
                "static_exact_tags": list(unit.card.static_tags.exact),
                "static_routing_tags": list(unit.card.static_tags.routing),
                "deepseek": deepseek_request_row,
                "grok": grok_request_row,
            }
        )

    deepseek_inspection = campaign.inspection
    deepseek_execution_policy = {
        "provider": "deepseek",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-v4-pro",
        "thinking": "disabled",
        "temperature": 0,
        "response_format": "json_object",
        "retry_count": 0,
        "execution_order": "canonical_sequential",
        "campaign_wall_clock_cap_ms": _DEEPSEEK_CAMPAIGN_WALL_CLOCK_CAP_MS,
    }
    grok_execution_policy = {
        "provider": "xai_via_local_grok_build_cli",
        "endpoint": "managed_by_grok_build_cli_not_repository_pinned",
        "model": GROK_PROXY_MODEL,
        "reasoning_effort": "high",
        "system_delivery": "system_prompt_override",
        "user_delivery": "prompt_file",
        "max_turns": 1,
        "retry_count": 0,
        "execution_order": "canonical_sequential_new_process_per_unit",
        "memory": False,
        "subagents": False,
        "web_search": False,
        "tools": False,
        "plan_mode": False,
        "working_directory": "empty_temporary_directory",
        "hard_output_token_cap_available": False,
        "campaign_wall_clock_cap_ms": _GROK_CAMPAIGN_WALL_CLOCK_CAP_MS,
    }
    deepseek_request_set_digest = _sha256_text(
        _canonical_json(
            {
                "provider_policy": deepseek_execution_policy,
                "requests": deepseek_request_rows,
            }
        )
    )
    grok_request_set_digest = _sha256_text(
        _canonical_json(
            {
                "provider_policy": grok_execution_policy,
                "response_schema_sha256": _sha256_text(grok_schema_json),
                "requests": grok_request_rows,
            }
        )
    )
    payload: dict[str, object] = {
        "schema_version": PILOT_SCHEMA_VERSION,
        "mode": "inspect_only",
        "sample": {
            "classification": PILOT_SAMPLE_CLASSIFICATION,
            "is_real_mr": False,
            "repository": fixture.SOURCE_REPOSITORY,
            "source_revision": fixture.SOURCE_REVISION,
            "synthetic_head_revision": fixture.HEAD_REVISION,
            "source_path": fixture.SOURCE_PATH,
            "base_content_sha256": fixture.EXPECTED_BASE_HASH,
            "head_content_sha256": fixture.EXPECTED_HEAD_HASH,
        },
        "upstream": {
            "change_set_id": upstream.change_set.change_set_id,
            "context_plan_id": upstream.context_plan.context_plan_id,
            "review_unit_count": len(upstream.analysis_result.review_units),
        },
        "static_baseline": _static_summary(campaign),
        "outbound_model_input_scope": {
            "included": [
                "repository_ai_tag_system_prompt",
                "all_24_active_tag_contract_definitions_and_boundaries",
                "review_unit_id_path_symbol_role_and_source_identity",
                "one_complete_numbered_review_unit_code_block_per_request",
                "owner_summary",
                "unit_exact_parser_facts",
                "whole_file_file_hints",
                "parser_context_and_ownership_quality",
            ],
            "excluded": [
                "static_exact_and_routing_tag_conclusions",
                "deepseek_or_grok_conclusions",
                "dimensions_and_review_questions",
                "retrieval_candidates_and_knowledge_documents",
                "api_keys_and_database_credentials",
            ],
            "note": (
                "file_hints_are_whole_file_context_and_are_not_unit_positive_truth"
            ),
        },
        "deepseek_campaign": {
            "campaign_id": campaign.manifest.campaign_id,
            "request_set_digest": deepseek_request_set_digest,
            "execution_policy": deepseek_execution_policy,
            "planned_attempt_cap_sum": deepseek_inspection.planned_attempt_cap_sum,
            "planned_output_token_cap_sum": (
                deepseek_inspection.planned_output_token_cap_sum
            ),
            "total_wire_body_size_bytes": (
                deepseek_inspection.total_wire_body_size_bytes
            ),
            "total_response_byte_cap_sum": sum(
                row["max_response_bytes"] for row in deepseek_request_rows
            ),
        },
        "grok_blind_proxy_campaign": {
            "role": GROK_PROXY_ROLE,
            "request_set_digest": grok_request_set_digest,
            "execution_policy": grok_execution_policy,
            "planned_attempt_cap_sum": len(units),
            "total_request_input_size_bytes": sum(
                row["request_input_size_bytes"] for row in grok_request_rows
            ),
            "total_response_byte_cap_sum": sum(
                row["max_response_bytes"] for row in grok_request_rows
            ),
            "planned_output_token_cap_sum": None,
            "response_schema_sha256": _sha256_text(grok_schema_json),
            "local_validation": (
                "strict_24_tag_order_evidence_lines_and_degraded_view_checks"
            ),
            "result_use": "comparison_only_never_retrieval_input",
        },
        "units": units,
        "network_attempted": False,
        "credential_accessed": False,
        "contains_source_or_prompt_body": False,
        "quality_boundary": (
            "first_real_application_code_semantic_pilot_not_real_mr_not_human_truth_"
            "not_production_precision_recall"
        ),
    }
    payload["pilot_id"] = _sha256_text(_canonical_json(payload))
    return payload


def build_video_player_tag_pilot() -> VideoPlayerTagPilot:
    """Build and fully replay-verify the inspect-only 15-Unit pilot."""

    upstream = build_video_player_analysis_fixture()
    campaign = build_ai_tag_shadow_campaign(
        analysis_result=upstream.analysis_result,
        context_plan=upstream.context_plan,
        source_snapshots=upstream.source_snapshots,
        unit_ids=upstream.unit_ids,
    )
    verify_ai_tag_shadow_campaign_against_upstream(
        campaign,
        analysis_result=upstream.analysis_result,
        context_plan=upstream.context_plan,
        source_snapshots=upstream.source_snapshots,
        unit_ids=upstream.unit_ids,
    )
    return VideoPlayerTagPilot(
        upstream=upstream,
        campaign=campaign,
        inspection=_build_inspection(upstream, campaign),
    )


def render_video_player_tag_pilot_inspection(
    inspection: dict[str, object],
    *,
    pretty: bool = True,
) -> str:
    """Render only the source-free inspection projection."""

    expected_keys = {
        "schema_version",
        "pilot_id",
        "mode",
        "sample",
        "upstream",
        "static_baseline",
        "outbound_model_input_scope",
        "deepseek_campaign",
        "grok_blind_proxy_campaign",
        "units",
        "network_attempted",
        "credential_accessed",
        "contains_source_or_prompt_body",
        "quality_boundary",
    }
    if (
        set(inspection) != expected_keys
        or inspection.get("schema_version") != PILOT_SCHEMA_VERSION
        or inspection.get("mode") != "inspect_only"
        or inspection.get("network_attempted") is not False
        or inspection.get("credential_accessed") is not False
        or inspection.get("contains_source_or_prompt_body") is not False
    ):
        raise ValueError("unsupported VideoPlayer Tag pilot inspection")
    identity_payload = dict(inspection)
    recorded_id = identity_payload.pop("pilot_id")
    if recorded_id != _sha256_text(_canonical_json(identity_payload)):
        raise ValueError("VideoPlayer Tag pilot inspection identity does not match")
    return json.dumps(
        inspection,
        allow_nan=False,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
    )


__all__ = [
    "GROK_PROXY_MODEL",
    "PILOT_SAMPLE_CLASSIFICATION",
    "PILOT_SCHEMA_VERSION",
    "VideoPlayerAnalysisFixture",
    "VideoPlayerTagPilot",
    "build_video_player_analysis_fixture",
    "build_video_player_tag_pilot",
    "grok_blind_messages",
    "grok_response_schema",
    "render_video_player_tag_pilot_inspection",
    "validate_grok_blind_output",
]
