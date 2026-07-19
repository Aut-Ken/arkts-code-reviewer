from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from arkts_code_reviewer.code_analysis import (
    AnalysisResult,
    ChangeAtomInput,
    ChangedFileInput,
    CodeAnalyzer,
    CodeSourceRef,
    CodeSourceSnapshot,
    ContextPlanResult,
    ReviewUnitSpan,
    normalize_change_set,
)
from arkts_code_reviewer.hybrid_analysis import (
    AITagRawCompletion,
    AITagRawUsage,
    AITagResponseValidation,
    AITagTransportFailure,
    validate_ai_tag_completion,
    validate_ai_tag_transport_failure,
)
from arkts_code_reviewer.hybrid_analysis.fake_client import ScriptedFakeDeepSeekClient
from arkts_code_reviewer.hybrid_analysis.shadow_campaign import (
    AITagShadowCampaignBuilder,
    AITagShadowCampaignBundle,
    AITagShadowCampaignInspection,
    AITagShadowCampaignUnitArtifacts,
    build_ai_tag_shadow_campaign,
    load_ai_tag_shadow_campaign_inspection,
    load_ai_tag_shadow_campaign_manifest,
    render_ai_tag_shadow_campaign_inspection,
    seal_ai_tag_shadow_campaign_manifest,
    verify_ai_tag_shadow_campaign_against_upstream,
    verify_ai_tag_shadow_campaign_evaluation_report,
)
from arkts_code_reviewer.hybrid_analysis.shadow_evaluation import (
    seal_ai_tag_shadow_evaluation_report,
)

_REPOSITORY = "campaign-repo"
_BASE = "base-revision"
_HEAD = "head-revision"


@dataclass(frozen=True)
class _Scenario:
    analysis: AnalysisResult
    context_plan: ContextPlanResult
    snapshots: dict[str, CodeSourceSnapshot]


def _snapshot(path: str, content: str, revision: str) -> CodeSourceSnapshot:
    return CodeSourceSnapshot(
        source_ref=CodeSourceRef.create(
            repository=_REPOSITORY,
            revision=revision,
            path=path,
            content_hash=f"sha256:{hashlib.sha256(content.encode()).hexdigest()}",
        ),
        content=content,
    )


def _scenario() -> _Scenario:
    path = "src/Page.ets"
    base = _snapshot(
        path,
        """@Entry
@Component
struct Page {
  first() {
    console.info("old first")
  }
  second() {
    console.info("old second")
  }
}
""",
        _BASE,
    )
    head = _snapshot(
        path,
        """@Entry
@Component
struct Page {
  first() {
    console.info("new first")
  }
  second() {
    console.info("new second")
  }
}
""",
        _HEAD,
    )
    change_set = normalize_change_set(
        repository=_REPOSITORY,
        base_revision=_BASE,
        head_revision=_HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path=path,
                new_path=path,
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(5, 5),
                        new_span=ReviewUnitSpan(5, 5),
                        deleted_old_lines=(5,),
                        added_new_lines=(5,),
                    ),
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(8, 8),
                        new_span=ReviewUnitSpan(8, 8),
                        deleted_old_lines=(8,),
                        added_new_lines=(8,),
                    ),
                ),
            ),
        ),
    )
    snapshots = {
        base.source_ref.source_ref_id: base,
        head.source_ref.source_ref_id: head,
    }
    analyzer = CodeAnalyzer()
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    context_plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=20_000,
    )
    return _Scenario(analysis, context_plan, snapshots)


def _unit_ids(scenario: _Scenario) -> tuple[str, ...]:
    return tuple(item.unit_id for item in scenario.analysis.review_units)


def _bundle() -> tuple[_Scenario, AITagShadowCampaignBundle]:
    scenario = _scenario()
    bundle = build_ai_tag_shadow_campaign(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_ids=tuple(reversed(_unit_ids(scenario))),
    )
    return scenario, bundle


def _judgments(
    unit: AITagShadowCampaignUnitArtifacts,
    *,
    positive: tuple[str, ...] = (),
) -> tuple[dict[str, object], ...]:
    visible_line = unit.model_view.code.line_numbers[0]
    values: list[dict[str, object]] = []
    for contract in unit.request.tag_contract_views:
        if contract.tag_id in positive:
            values.append(
                {
                    "tag_id": contract.tag_id,
                    "decision": "positive",
                    "evidence_lines": [visible_line],
                    "reason_code": "direct_unit_semantic_evidence",
                    "reason": f"Synthetic support for {contract.tag_id}.",
                }
            )
        else:
            values.append(
                {
                    "tag_id": contract.tag_id,
                    "decision": "not_supported",
                    "evidence_lines": [],
                    "reason_code": "no_support_in_complete_view",
                    "reason": None,
                }
            )
    return tuple(values)


def _valid_completion(
    unit: AITagShadowCampaignUnitArtifacts,
    *,
    positive: tuple[str, ...] = (),
    latency_ms: int = 5,
) -> AITagRawCompletion:
    content = json.dumps(
        {"judgments": _judgments(unit, positive=positive)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return AITagRawCompletion(
        source_kind="scripted_fixture",
        content=content,
        finish_reason="stop",
        model="deepseek-v4-pro",
        system_fingerprint="campaign-scripted-fixture",
        usage=AITagRawUsage(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_cache_hit_tokens=10,
        ),
        latency_ms=latency_ms,
        attempt_count=1,
    )


def _validations_from_script(
    bundle: AITagShadowCampaignBundle,
) -> dict[str, AITagResponseValidation]:
    script = (
        _valid_completion(bundle.units[0], positive=("has_logging",)),
        AITagRawCompletion(
            source_kind="scripted_fixture",
            content="{",
            finish_reason="stop",
            model="deepseek-v4-pro",
            system_fingerprint="campaign-scripted-fixture",
            usage=None,
            latency_ms=6,
            attempt_count=1,
        ),
        AITagTransportFailure(
            source_kind="scripted_fixture",
            reason_code="provider_timeout",
            attempt_count=1,
            latency_ms=7,
        ),
        _valid_completion(bundle.units[3], positive=("has_logging",), latency_ms=8),
    )
    client = ScriptedFakeDeepSeekClient(script)
    results: dict[str, AITagResponseValidation] = {}
    for item in bundle.units:
        raw = client.complete(item.envelope)
        validation = (
            validate_ai_tag_completion(item.envelope, raw)
            if isinstance(raw, AITagRawCompletion)
            else validate_ai_tag_transport_failure(item.envelope, raw)
        )
        results[item.plan.plan_id] = validation
    assert client.invocation_count == len(bundle.units)
    return results


def test_campaign_is_deterministic_order_independent_and_upstream_rebuildable() -> None:
    scenario = _scenario()
    ids = _unit_ids(scenario)
    first = build_ai_tag_shadow_campaign(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_ids=ids,
    )
    second = build_ai_tag_shadow_campaign(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=dict(reversed(tuple(scenario.snapshots.items()))),
        unit_ids=tuple(reversed(ids)),
    )

    assert len(ids) == 4
    assert first == second
    assert first.manifest.unit_count == 4
    assert tuple(item.unit_id for item in first.manifest.units) == tuple(sorted(ids))
    assert first.manifest.analysis_graph_sha256.startswith("sha256:")
    assert first.manifest.source_snapshot_bundle_sha256.startswith("sha256:")
    assert first.manifest.production_qualified is False
    verify_ai_tag_shadow_campaign_against_upstream(
        first,
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_ids=tuple(reversed(ids)),
    )


def test_manifest_and_inspection_round_trip_and_known_safe_projection() -> None:
    _, bundle = _bundle()
    manifest = load_ai_tag_shadow_campaign_manifest(bundle.manifest.model_dump_json())
    inspection_json = render_ai_tag_shadow_campaign_inspection(bundle.inspection)
    inspection = load_ai_tag_shadow_campaign_inspection(inspection_json)

    assert manifest == bundle.manifest
    assert inspection == bundle.inspection
    assert inspection.mode == "inspect_only"
    assert inspection.network_attempted is False
    assert inspection.credential_accessed is False
    assert inspection.planned_attempt_cap_sum == inspection.unit_count
    assert inspection.planned_output_token_cap_sum == inspection.unit_count * 4_096
    assert inspection.total_wire_body_size_bytes == sum(
        len(item.plan.wire_body_json.encode()) for item in bundle.units
    )
    assert "wire_body_json" not in inspection_json
    assert "console.info" not in inspection_json
    assert bundle.units[0].envelope.prompt.text not in inspection_json
    assert all(item.card.unit_id not in inspection_json for item in bundle.units)


def test_limits_change_plan_and_campaign_identity() -> None:
    scenario = _scenario()
    first = build_ai_tag_shadow_campaign(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_ids=_unit_ids(scenario),
        max_output_tokens=4_096,
    )
    second = build_ai_tag_shadow_campaign(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_ids=_unit_ids(scenario),
        max_output_tokens=8_192,
    )

    assert first.manifest.campaign_id != second.manifest.campaign_id
    assert first.manifest.units[0].plan_id != second.manifest.units[0].plan_id
    assert (
        first.inspection.units[0].wire_body_sha256
        != second.inspection.units[0].wire_body_sha256
    )
    with pytest.raises(ValueError, match="full upstream rebuild"):
        verify_ai_tag_shadow_campaign_against_upstream(
            first,
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            unit_ids=_unit_ids(scenario),
            max_output_tokens=8_192,
        )


@pytest.mark.parametrize(
    "unit_ids, message",
    [
        ((), "non-empty"),
        (("missing-unit",), "resolve to one Unit"),
    ],
)
def test_invalid_unit_selection_fails_closed(
    unit_ids: tuple[str, ...],
    message: str,
) -> None:
    scenario = _scenario()
    with pytest.raises(ValueError, match=message):
        build_ai_tag_shadow_campaign(
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            unit_ids=unit_ids,
        )


def test_duplicate_unit_selection_fails_before_building() -> None:
    scenario = _scenario()
    unit_id = _unit_ids(scenario)[0]
    with pytest.raises(ValueError, match="unique"):
        build_ai_tag_shadow_campaign(
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            unit_ids=(unit_id, unit_id),
        )


def test_manifest_self_hash_cannot_replace_full_upstream_rebuild() -> None:
    scenario, bundle = _bundle()
    payload = bundle.manifest.model_dump(mode="json", exclude={"campaign_id"})
    payload["analysis_graph_sha256"] = "sha256:" + "a" * 64
    forged = seal_ai_tag_shadow_campaign_manifest(payload)
    inspection_payload = bundle.inspection.model_dump(mode="json")
    inspection_payload["campaign_id"] = forged.campaign_id
    forged_bundle = replace(
        bundle,
        manifest=forged,
        inspection=AITagShadowCampaignInspection.model_validate(inspection_payload),
    )

    AITagShadowCampaignBuilder.default().verify_bundle_graph(forged_bundle)
    with pytest.raises(ValueError, match="full upstream rebuild"):
        verify_ai_tag_shadow_campaign_against_upstream(
            forged_bundle,
            analysis_result=scenario.analysis,
            context_plan=scenario.context_plan,
            source_snapshots=scenario.snapshots,
            unit_ids=_unit_ids(scenario),
        )


def test_manifest_context_plan_splice_is_rejected_before_evaluation() -> None:
    _, bundle = _bundle()
    payload = bundle.manifest.model_dump(mode="json", exclude={"campaign_id"})
    payload["context_plan_id"] = "context-plan:sha256:" + "a" * 64
    forged = seal_ai_tag_shadow_campaign_manifest(payload)
    inspection_payload = bundle.inspection.model_dump(mode="json")
    inspection_payload["campaign_id"] = forged.campaign_id
    forged_bundle = replace(
        bundle,
        manifest=forged,
        inspection=AITagShadowCampaignInspection.model_validate(inspection_payload),
    )
    validations = _validations_from_script(bundle)

    with pytest.raises(ValueError, match="Manifest ContextPlan"):
        AITagShadowCampaignBuilder.default().verify_bundle_graph(forged_bundle)
    with pytest.raises(ValueError, match="Manifest ContextPlan"):
        AITagShadowCampaignBuilder.default().build_evaluation_report(
            forged_bundle,
            validations,
        )


def test_cross_unit_envelope_or_plan_splice_is_rejected() -> None:
    _, bundle = _bundle()
    first, second, *rest = bundle.units
    spliced = AITagShadowCampaignUnitArtifacts(
        card=first.card,
        model_view=first.model_view,
        request=first.request,
        envelope=second.envelope,
        plan=second.plan,
    )
    forged_bundle = replace(bundle, units=(spliced, second, *rest))

    with pytest.raises(ValueError, match="Envelope differs"):
        AITagShadowCampaignBuilder.default().verify_bundle_graph(forged_bundle)


def test_inspection_model_copy_tamper_is_revalidated() -> None:
    _, bundle = _bundle()
    forged = bundle.inspection.model_copy(
        update={"planned_output_token_cap_sum": 1}
    )
    with pytest.raises(ValueError, match="campaign Inspection"):
        replace(bundle, inspection=forged)


def test_self_consistent_inspection_reference_tamper_is_rejected_by_bundle() -> None:
    _, bundle = _bundle()
    payload = bundle.inspection.model_dump(mode="json")
    payload["units"][0]["wire_body_sha256"] = "sha256:" + "a" * 64
    forged = AITagShadowCampaignInspection.model_validate(payload)

    with pytest.raises(ValueError, match="Inspection differs"):
        AITagShadowCampaignBuilder.default().verify_bundle_graph(
            replace(bundle, inspection=forged)
        )


def test_fake_multi_unit_campaign_builds_existing_evaluation_report() -> None:
    _, bundle = _bundle()
    validations = _validations_from_script(bundle)
    report = AITagShadowCampaignBuilder.default().build_evaluation_report(
        bundle,
        validations,
    )

    assert report.unit_count == 4
    assert report.valid_shape_unit_count == 2
    assert report.invalid_output_unit_count == 1
    assert report.unavailable_claim_unit_count == 1
    assert report.scripted_fixture_unit_count == 4
    assert report.collection_scope == "caller_supplied_input_set_not_campaign_bound"
    assert report.production_qualified is False
    verify_ai_tag_shadow_campaign_evaluation_report(report, bundle, validations)


def test_campaign_evaluation_requires_one_validation_for_every_plan() -> None:
    _, bundle = _bundle()
    validations = _validations_from_script(bundle)
    missing = dict(validations)
    missing.pop(bundle.units[0].plan.plan_id)
    extra = dict(validations)
    extra["ai-tag-shadow-plan:sha256:" + "f" * 64] = next(
        iter(validations.values())
    )

    with pytest.raises(ValueError, match="missing=1, extra=0"):
        AITagShadowCampaignBuilder.default().build_evaluation_report(bundle, missing)
    with pytest.raises(ValueError, match="missing=0, extra=1"):
        AITagShadowCampaignBuilder.default().build_evaluation_report(bundle, extra)


def test_cross_unit_response_validation_is_rejected() -> None:
    _, bundle = _bundle()
    validations = _validations_from_script(bundle)
    first_plan = bundle.units[0].plan.plan_id
    second_plan = bundle.units[1].plan.plan_id
    validations[first_plan], validations[second_plan] = (
        validations[second_plan],
        validations[first_plan],
    )

    with pytest.raises(ValueError, match="does not reference its envelope"):
        AITagShadowCampaignBuilder.default().build_evaluation_report(bundle, validations)


def test_validation_mapping_is_envelope_bound_not_plan_execution_proof() -> None:
    scenario, original = _bundle()
    validations = _validations_from_script(original)
    changed_limits = build_ai_tag_shadow_campaign(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_ids=_unit_ids(scenario),
        max_output_tokens=8_192,
    )
    remapped = {
        changed.plan.plan_id: validations[original_item.plan.plan_id]
        for original_item, changed in zip(original.units, changed_limits.units, strict=True)
    }

    assert all(
        original_item.envelope.envelope_id == changed.envelope.envelope_id
        and original_item.plan.plan_id != changed.plan.plan_id
        for original_item, changed in zip(
            original.units,
            changed_limits.units,
            strict=True,
        )
    )
    report = AITagShadowCampaignBuilder.default().build_evaluation_report(
        changed_limits,
        remapped,
    )
    assert report.unit_count == changed_limits.manifest.unit_count
    assert report.collection_scope == "caller_supplied_input_set_not_campaign_bound"


def test_campaign_report_tamper_is_rejected_by_campaign_aware_verifier() -> None:
    _, bundle = _bundle()
    validations = _validations_from_script(bundle)
    report = AITagShadowCampaignBuilder.default().build_evaluation_report(
        bundle,
        validations,
    )
    payload = report.model_dump(mode="json", exclude={"report_id"})
    payload["reported_latency_total_ms"] += 1

    with pytest.raises(ValueError, match="latency metrics"):
        seal_ai_tag_shadow_evaluation_report(payload)


def test_campaign_report_is_bound_to_the_supplied_validation_mapping() -> None:
    _, bundle = _bundle()
    original = _validations_from_script(bundle)
    report = AITagShadowCampaignBuilder.default().build_evaluation_report(
        bundle,
        original,
    )
    changed = dict(original)
    first = bundle.units[0]
    changed[first.plan.plan_id] = validate_ai_tag_completion(
        first.envelope,
        _valid_completion(first, positive=("has_timer",), latency_ms=11),
    )

    with pytest.raises(ValueError, match="differs from Campaign roots"):
        verify_ai_tag_shadow_campaign_evaluation_report(
            report,
            bundle,
            changed,
        )


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version":"ai-tag-shadow-campaign-inspection-v1",'
        '"schema_version":"ai-tag-shadow-campaign-inspection-v1"}',
        "NaN",
        "[]",
    ],
)
def test_campaign_inspection_loader_rejects_unsafe_json(raw: str) -> None:
    with pytest.raises(ValueError):
        load_ai_tag_shadow_campaign_inspection(raw)


def test_manifest_and_inspection_loaders_reject_single_unsafe_mutations() -> None:
    _, bundle = _bundle()
    manifest_json = bundle.manifest.model_dump_json()
    duplicate_manifest = manifest_json.replace(
        '"schema_version":"ai-tag-shadow-campaign-manifest-v1"',
        '"schema_version":"ai-tag-shadow-campaign-manifest-v1",'
        '"schema_version":"ai-tag-shadow-campaign-manifest-v1"',
        1,
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_ai_tag_shadow_campaign_manifest(duplicate_manifest)

    non_finite_manifest = manifest_json.replace(
        f'"unit_count":{bundle.manifest.unit_count}',
        '"unit_count":NaN',
        1,
    )
    with pytest.raises(ValueError):
        load_ai_tag_shadow_campaign_manifest(non_finite_manifest)

    inspection_payload = bundle.inspection.model_dump(mode="json")
    inspection_payload["unexpected"] = True
    with pytest.raises(ValueError):
        load_ai_tag_shadow_campaign_inspection(
            json.dumps(inspection_payload, separators=(",", ":"))
        )


def test_inspection_cli_is_metadata_only_and_does_not_load_httpx(
    tmp_path: Path,
) -> None:
    _, bundle = _bundle()
    inspection_path = tmp_path / "inspection.json"
    inspection_path.write_text(
        render_ai_tag_shadow_campaign_inspection(bundle.inspection),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "tools/inspect_ai_tag_shadow_campaign.py",
        "--inspection",
        str(inspection_path),
    ]
    environment = dict(os.environ)
    environment["DEEPSEEK_API_KEY"] = "forbidden-secret-must-not-appear"
    (tmp_path / "sitecustomize.py").write_text(
        """import os
import socket
import sys

class _BlockHttpx:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "httpx" or fullname.startswith("httpx."):
            raise AssertionError("inspect CLI imported httpx")
        return None

def _blocked_network(*args, **kwargs):
    raise AssertionError("inspect CLI attempted network access")

_original_getitem = os._Environ.__getitem__
def _guarded_getitem(self, key):
    if key == "DEEPSEEK_API_KEY":
        raise AssertionError("inspect CLI accessed DEEPSEEK_API_KEY")
    return _original_getitem(self, key)

sys.meta_path.insert(0, _BlockHttpx())
socket.create_connection = _blocked_network
socket.socket.connect = _blocked_network
os._Environ.__getitem__ = _guarded_getitem
""",
        encoding="utf-8",
    )
    environment["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(tmp_path), environment.get("PYTHONPATH", ""))
        if part
    )
    completed = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert "forbidden-secret-must-not-appear" not in completed.stdout
    parsed = AITagShadowCampaignInspection.model_validate_json(completed.stdout)
    assert parsed == bundle.inspection

    rejected = subprocess.run(
        [*command, "--execute-live"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 2
