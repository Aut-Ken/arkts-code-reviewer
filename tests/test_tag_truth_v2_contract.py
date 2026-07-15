from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    TAG_TRUTH_V2_FINGERPRINT_PREFIX,
    TagTruthV2ReviewChain,
    TagTruthV2Suite,
    bytes_hash,
    canonical_hash,
    load_tag_truth_v2,
    parse_tag_truth_v2,
    tag_truth_v2_fingerprint,
)


def _quality_gates(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "policy_version": "tag-quality-v1",
        "approval_status": "snapshot_only_not_approved",
        "minimum_case_count": 2,
        "minimum_exact_positive_cases": 1,
        "minimum_exact_negative_cases": 1,
        "minimum_routing_positive_cases": 1,
        "minimum_routing_negative_cases": 1,
        "minimum_source_families": 2,
        "minimum_exact_precision": 0.99,
        "minimum_exact_recall": 0.95,
        "minimum_exact_precision_wilson_95": 0.90,
        "minimum_exact_recall_wilson_95": 0.85,
        "minimum_routing_precision": 0.99,
        "minimum_routing_recall": 0.95,
        "minimum_routing_precision_wilson_95": 0.90,
        "minimum_routing_recall_wilson_95": 0.85,
        "maximum_exact_false_positives": 0,
        "maximum_exact_false_negatives": 0,
        "maximum_routing_false_positives": 0,
        "maximum_routing_false_negatives": 0,
        "maximum_exact_critical_false_positives": 0,
        "maximum_file_hint_promotions": 0,
        "maximum_parser_risk_cases": 0,
        "maximum_review_unit_risk_cases": 0,
        "maximum_scope_risk_cases": 0,
        "maximum_unresolved_taxonomy_cases": 0,
        "critical_negative_strata": ["hard_negative"],
    }
    payload.update(overrides)
    payload["quality_gate_id"] = canonical_hash("tag-truth-quality-gates", payload)
    return payload


def _source(index: int, *, family: str | None = None) -> dict[str, Any]:
    app_scope = family or f"samples/app{index}"
    return {
        "alias": f"src{index:03d}",
        "repository_source_id": "applications-samples",
        "origin": "https://example.invalid/applications_app_samples.git",
        "revision": "a" * 40,
        "path": f"samples/app{index}/entry/src/main/ets/Page.ets",
        "content_sha256": bytes_hash(f"source-{index}".encode()),
        "line_count": 40,
        "source_kind": "main",
        "app_scope": app_scope,
        "source_family_id": app_scope,
    }


def _case(index: int, *, positive: bool) -> dict[str, Any]:
    start_line = 1 if index == 1 else 11
    changed_line = start_line + 4
    label = "positive" if positive else "negative"
    return {
        "case_id": f"case-{index:016x}",
        "target_tag_id": "has_state_management",
        "source_alias": f"src{index:03d}",
        "changed_line": changed_line,
        "expected_unit_kind": "method",
        "expected_unit_symbol": f"Page{index}.update",
        "expected_unit_span": {
            "start_line": start_line,
            "end_line": start_line + 9,
        },
        "exact": {
            "label": label,
            "metric_eligible": True,
            "abstain_reason": None,
            "evidence_lines": [changed_line],
            "rationale": f"Exact label rationale {index}.",
        },
        "routing": {
            "label": label,
            "metric_eligible": True,
            "abstain_reason": None,
            "evidence_lines": [changed_line],
            "rationale": f"Routing label rationale {index}.",
        },
        "stratum_id": "direct_positive" if positive else "hard_negative",
        "critical_negative": not positive,
        "review_unit_body_sha256": bytes_hash(f"review-unit-body-{index}".encode()),
        "normalized_body_sha256": bytes_hash(f"normalized-body-{index}".encode()),
        "template_cluster_id": canonical_hash("template-cluster", {"index": index}),
    }


def _tag_contract() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "tag-contract-snapshot-v1",
        "tag_id": "has_state_management",
        "version": "state-management-v1",
        "axes_relationship": "independent",
        "exact_semantics": {
            "positive": "The ReviewUnit itself contains state-management behavior.",
            "negative": "The ReviewUnit itself contains no state-management behavior.",
            "abstain": "Exact applicability cannot be decided from the immutable ReviewUnit.",
        },
        "routing_semantics": {
            "positive": "The file contains a conservative state-management routing hint.",
            "negative": "The file contains no reliable state-management routing hint.",
            "abstain": "Routing applicability cannot be decided from the immutable source.",
        },
    }
    payload["contract_fingerprint"] = canonical_hash("tag-contract-snapshot", payload)
    return payload


def _review_chain(
    case_ids: list[str],
    *,
    complete: bool,
    contract_fingerprint: str | None = None,
) -> dict[str, Any]:
    if contract_fingerprint is None:
        contract_fingerprint = str(_tag_contract()["contract_fingerprint"])
    base: dict[str, Any] = {
        "review_policy_version": "dual-review-v1",
        "review_policy_sha256": bytes_hash(b"dual-review-policy-v1"),
        "tag_contract_fingerprint": contract_fingerprint,
    }
    if not complete:
        return {
            **base,
            "consensus_status": "not_applicable",
            "receipt_references": [],
            "consensus_id": None,
            "consensus_case_ids": [],
        }
    references = []
    for index, reviewer_id in enumerate(("reviewer-a", "reviewer-b"), start=1):
        receipt_payload = {
            "reviewer_id": reviewer_id,
            "reviewed_case_ids": case_ids,
        }
        references.append(
            {
                "round_id": f"round-{index}",
                "reviewer_id": reviewer_id,
                "reviewer_kind": "human",
                "receipt_id": canonical_hash("tag-truth-review-receipt", receipt_payload),
                "tag_contract_fingerprint": contract_fingerprint,
                "candidate_design_participant": False,
                "selector_participant": False,
                "candidate_configuration_seen": False,
                "candidate_output_seen": False,
                "reviewed_case_ids": case_ids,
            }
        )
    return {
        **base,
        "consensus_status": "complete",
        "receipt_references": references,
        "consensus_id": canonical_hash("tag-truth-consensus", {"case_ids": case_ids}),
        "consensus_case_ids": case_ids,
    }


def _suite_payload(
    role: str = "development_regression",
    *,
    near_duplicate_status: str | None = None,
) -> dict[str, Any]:
    cases = [_case(1, positive=True), _case(2, positive=False)]
    case_ids = [str(item["case_id"]) for item in cases]
    complete = role != "development_regression"
    tag_contract = _tag_contract()
    if near_duplicate_status is None:
        near_duplicate_status = "not_measured"
    reasons = [
        "artifact_authenticity_not_verified",
        "near_duplicate_verifier_unavailable",
        "stage1_contract_only",
    ]
    if role != "development_regression":
        reasons.append("external_selection_not_verified")
    if role == "production_prevalence":
        reasons.append("production_prevalence_not_verified")
    quality_overrides: dict[str, object] = {}
    if role == "independent_blind_challenge":
        quality_overrides = {
            "minimum_case_count": 32,
            "minimum_exact_positive_cases": 16,
            "minimum_exact_negative_cases": 16,
            "minimum_routing_positive_cases": 16,
            "minimum_routing_negative_cases": 16,
            "minimum_source_families": 32,
            "minimum_exact_precision_wilson_95": 0.80,
            "minimum_exact_recall_wilson_95": 0.80,
            "minimum_routing_precision_wilson_95": 0.80,
            "minimum_routing_recall_wilson_95": 0.80,
        }
    elif role == "production_prevalence":
        quality_overrides = {
            "minimum_case_count": 80,
            "minimum_exact_positive_cases": 40,
            "minimum_exact_negative_cases": 40,
            "minimum_routing_positive_cases": 40,
            "minimum_routing_negative_cases": 40,
            "minimum_source_families": 80,
            "minimum_exact_precision_wilson_95": 0.90,
            "minimum_exact_recall_wilson_95": 0.90,
            "minimum_routing_precision_wilson_95": 0.90,
            "minimum_routing_recall_wilson_95": 0.90,
        }
    return {
        "schema_version": "tag-truth-v2",
        "suite_id": "state-management-contract",
        "description": "Generic Tag Truth v2 contract fixture.",
        "dataset_role": role,
        "truth_status": "consensus" if complete else "proposed",
        "data_qualification_status": "not_qualified",
        "data_qualification_reasons": sorted(reasons),
        "natural_prevalence_claimed": False,
        "near_duplicate_policy_version": "near-duplicate-policy-v1",
        "near_duplicate_check_status": near_duplicate_status,
        "tag_contract": tag_contract,
        "repository": {
            "source_id": "applications-samples",
            "repository": "applications_app_samples",
            "origin": "https://example.invalid/applications_app_samples.git",
            "revision": "a" * 40,
        },
        "sources": [_source(1), _source(2)],
        "cases": cases,
        "review_chain": _review_chain(
            case_ids,
            complete=complete,
            contract_fingerprint=str(tag_contract["contract_fingerprint"]),
        ),
        "quality_gates": _quality_gates(**quality_overrides),
    }


def _rebuild_quality_gate_id(payload: dict[str, Any]) -> None:
    gates = dict(payload["quality_gates"])
    gates.pop("quality_gate_id")
    gates["quality_gate_id"] = canonical_hash("tag-truth-quality-gates", gates)
    payload["quality_gates"] = gates


def test_contract_is_strict_closed_frozen_and_canonical() -> None:
    payload = _suite_payload()
    suite = TagTruthV2Suite.model_validate(payload)

    assert suite.dataset_role == "development_regression"
    assert suite.sources[0].line_count == 40
    assert suite.model_dump(mode="json") == payload
    fingerprint = tag_truth_v2_fingerprint(suite)
    assert fingerprint.startswith(TAG_TRUTH_V2_FINGERPRINT_PREFIX)
    assert fingerprint == tag_truth_v2_fingerprint(
        parse_tag_truth_v2(json.dumps(payload, indent=2, sort_keys=False).encode())
    )

    with pytest.raises(ValidationError, match="frozen"):
        suite.__setattr__("suite_id", "changed")

    wrong_type = copy.deepcopy(payload)
    wrong_type["sources"][0]["line_count"] = "40"
    with pytest.raises(ValidationError, match="valid integer"):
        TagTruthV2Suite.model_validate(wrong_type)

    extra = copy.deepcopy(payload)
    extra["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TagTruthV2Suite.model_validate(extra)

    wrong_schema = copy.deepcopy(payload)
    wrong_schema["schema_version"] = "tag-truth-v3"
    with pytest.raises(ValidationError, match="tag-truth-v2"):
        TagTruthV2Suite.model_validate(wrong_schema)


def test_loader_rejects_duplicate_keys_and_symlinks(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":"tag-truth-v2","nested":{"key":1,"key":2}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON key: key"):
        load_tag_truth_v2(duplicate)

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_suite_payload()), encoding="utf-8")
    assert load_tag_truth_v2(manifest).suite_id == "state-management-contract"

    symlink = tmp_path / "linked.json"
    symlink.symlink_to(manifest)
    with pytest.raises(ValueError, match="regular non-symlink file"):
        load_tag_truth_v2(symlink)


def test_tag_contract_snapshot_rejects_semantic_or_identity_drift() -> None:
    payload = _suite_payload()
    payload["tag_contract"]["exact_semantics"]["positive"] = "Changed exact semantics."

    with pytest.raises(ValidationError, match="fingerprint does not match its complete snapshot"):
        TagTruthV2Suite.model_validate(payload)

    identity_drift = _suite_payload()
    identity_drift["tag_contract"]["version"] = "state-management-v2"
    with pytest.raises(ValidationError, match="fingerprint does not match its complete snapshot"):
        TagTruthV2Suite.model_validate(identity_drift)

    relationship_drift = _suite_payload()
    relationship_drift["tag_contract"]["axes_relationship"] = "implied"
    with pytest.raises(ValidationError, match="independent"):
        TagTruthV2Suite.model_validate(relationship_drift)


@pytest.mark.parametrize(
    ("axis", "semantic"),
    [
        ("exact_semantics", "positive"),
        ("exact_semantics", "negative"),
        ("exact_semantics", "abstain"),
        ("routing_semantics", "positive"),
        ("routing_semantics", "negative"),
        ("routing_semantics", "abstain"),
    ],
)
def test_each_structured_tag_semantic_is_required_trimmed_and_closed(
    axis: str,
    semantic: str,
) -> None:
    missing = _suite_payload()
    missing["tag_contract"][axis].pop(semantic)
    with pytest.raises(ValidationError, match="Field required"):
        TagTruthV2Suite.model_validate(missing)

    blank = _suite_payload()
    blank["tag_contract"][axis][semantic] = " "
    with pytest.raises(ValidationError, match="must be non-empty and trimmed"):
        TagTruthV2Suite.model_validate(blank)

    extra = _suite_payload()
    extra["tag_contract"][axis]["trigger_implementation"] = "forbidden"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TagTruthV2Suite.model_validate(extra)


def test_review_chain_binds_the_complete_tag_contract_fingerprint() -> None:
    original = TagTruthV2Suite.model_validate(_suite_payload())
    changed = _suite_payload()
    contract = changed["tag_contract"]
    contract["version"] = "state-management-v2"
    contract_without_fingerprint = dict(contract)
    contract_without_fingerprint.pop("contract_fingerprint")
    contract["contract_fingerprint"] = canonical_hash(
        "tag-contract-snapshot",
        contract_without_fingerprint,
    )

    with pytest.raises(ValidationError, match="review chain does not bind"):
        TagTruthV2Suite.model_validate(changed)

    changed["review_chain"]["tag_contract_fingerprint"] = contract["contract_fingerprint"]
    rebound = TagTruthV2Suite.model_validate(changed)
    assert original.tag_contract.contract_fingerprint != rebound.tag_contract.contract_fingerprint
    assert tag_truth_v2_fingerprint(original) != tag_truth_v2_fingerprint(rebound)


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("root", "candidate_config"),
        ("case", "predicted_exact_tags"),
        ("case", "trigger_implementation"),
    ],
)
def test_truth_contract_rejects_candidate_predictions_and_trigger_implementation(
    location: str,
    field: str,
) -> None:
    payload = _suite_payload()
    target = payload if location == "root" else payload["cases"][0]
    target[field] = ["has_state_management"]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TagTruthV2Suite.model_validate(payload)


def test_source_identity_is_complete_sorted_and_repository_bound() -> None:
    valid = _suite_payload()
    assert TagTruthV2Suite.model_validate(valid).sources[0].alias == "src001"

    unsorted = copy.deepcopy(valid)
    unsorted["sources"].reverse()
    with pytest.raises(ValidationError, match="sources must be sorted"):
        TagTruthV2Suite.model_validate(unsorted)

    unused = copy.deepcopy(valid)
    unused["sources"].append(_source(3))
    with pytest.raises(ValidationError, match="referenced completely and exclusively"):
        TagTruthV2Suite.model_validate(unused)

    drifted = copy.deepcopy(valid)
    drifted["sources"][0]["revision"] = "b" * 40
    with pytest.raises(ValidationError, match="repository binding drift"):
        TagTruthV2Suite.model_validate(drifted)

    origin_drift = copy.deepcopy(valid)
    origin_drift["sources"][0]["origin"] = "https://example.invalid/fork.git"
    with pytest.raises(ValidationError, match="repository binding drift"):
        TagTruthV2Suite.model_validate(origin_drift)


def test_source_family_is_path_derived_and_scopes_cannot_overlap() -> None:
    wrong_family = _suite_payload()
    wrong_family["sources"][0]["app_scope"] = "samples/claimed-family"
    wrong_family["sources"][0]["source_family_id"] = "samples/claimed-family"
    with pytest.raises(ValidationError, match="path-derived family"):
        TagTruthV2Suite.model_validate(wrong_family)

    overlapping = _suite_payload()
    overlapping["sources"][1]["path"] = "samples/app1/nested/entry/src/main/ets/Page.ets"
    overlapping["sources"][1]["app_scope"] = "samples/app1/nested"
    overlapping["sources"][1]["source_family_id"] = "samples/app1/nested"
    with pytest.raises(ValidationError, match="ancestor/descendant overlap"):
        TagTruthV2Suite.model_validate(overlapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("path", "source paths must be unique"),
        ("blob", "source blobs must be unique"),
        ("raw_body", "duplicate ReviewUnit bodies"),
        ("normalized_body", "duplicate normalized bodies"),
        ("template", "duplicate template clusters"),
        ("unit", "unique ReviewUnits"),
    ],
)
def test_suite_rejects_internal_identity_reuse(mutation: str, message: str) -> None:
    payload = _suite_payload()
    if mutation == "path":
        payload["sources"][1]["path"] = payload["sources"][0]["path"]
        payload["sources"][1]["app_scope"] = "samples/app1"
        payload["sources"][1]["source_family_id"] = "samples/app1"
    elif mutation == "blob":
        payload["sources"][1]["content_sha256"] = payload["sources"][0]["content_sha256"]
    elif mutation == "raw_body":
        payload["cases"][1]["review_unit_body_sha256"] = payload["cases"][0][
            "review_unit_body_sha256"
        ]
    elif mutation == "normalized_body":
        payload["cases"][1]["normalized_body_sha256"] = payload["cases"][0][
            "normalized_body_sha256"
        ]
    elif mutation == "template":
        payload["cases"][1]["template_cluster_id"] = payload["cases"][0]["template_cluster_id"]
    elif mutation == "unit":
        duplicate = copy.deepcopy(payload["cases"][0])
        duplicate["case_id"] = "case-0000000000000003"
        payload["cases"].append(duplicate)

    with pytest.raises(ValidationError, match=message):
        TagTruthV2Suite.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("case_order", "cases must be sorted"),
        ("unsupported_kind", "unsupported expected_unit_kind"),
        ("changed_line_outside", "changed_line must be inside"),
        ("unresolved_metric_label", "unresolved axis judgement must be metric-ineligible"),
        ("unresolved_routing_metric_label", "unresolved axis judgement must be metric-ineligible"),
        ("evidence_order", "axis judgement evidence_lines must be sorted and unique"),
        ("source_line_overflow", "span exceeds source"),
    ],
)
def test_case_identity_labels_spans_and_evidence_are_strict(
    mutation: str,
    message: str,
) -> None:
    payload = _suite_payload()
    cases = payload["cases"]
    if mutation == "case_order":
        cases.reverse()
    elif mutation == "unsupported_kind":
        cases[0]["expected_unit_kind"] = "namespace"
    elif mutation == "changed_line_outside":
        cases[0]["changed_line"] = 30
    elif mutation == "unresolved_metric_label":
        cases[0]["exact"]["label"] = "needs_taxonomy_decision"
    elif mutation == "unresolved_routing_metric_label":
        cases[0]["routing"]["label"] = "needs_taxonomy_decision"
    elif mutation == "evidence_order":
        cases[0]["exact"]["evidence_lines"] = [5, 4]
    elif mutation == "source_line_overflow":
        cases[1]["expected_unit_span"]["end_line"] = 41

    with pytest.raises(ValidationError, match=message):
        TagTruthV2Suite.model_validate(payload)


def test_unresolved_and_critical_negative_cases_fail_closed() -> None:
    unresolved = _suite_payload()
    unresolved_exact = unresolved["cases"][0]["exact"]
    unresolved_exact["label"] = "needs_taxonomy_decision"
    unresolved_exact["metric_eligible"] = False
    with pytest.raises(ValidationError, match="with an abstain_reason"):
        TagTruthV2Suite.model_validate(unresolved)

    unresolved_exact["abstain_reason"] = "taxonomy_unresolved"
    suite = TagTruthV2Suite.model_validate(unresolved)
    assert suite.cases[0].exact.metric_eligible is False
    assert suite.cases[0].routing.metric_eligible is True

    invalid_critical = _suite_payload()
    invalid_critical["cases"][0]["critical_negative"] = True
    with pytest.raises(ValidationError, match="critical-negative cases"):
        TagTruthV2Suite.model_validate(invalid_critical)


@pytest.mark.parametrize(
    ("exact_label", "routing_label"),
    [("positive", "negative"), ("negative", "positive"), ("positive", "positive")],
)
def test_exact_and_routing_labels_are_independent_judgements(
    exact_label: str,
    routing_label: str,
) -> None:
    payload = _suite_payload()
    payload["cases"][0]["exact"]["label"] = exact_label
    payload["cases"][0]["routing"]["label"] = routing_label

    suite = TagTruthV2Suite.model_validate(payload)
    assert suite.cases[0].exact.label == exact_label
    assert suite.cases[0].routing.label == routing_label


@pytest.mark.parametrize("ineligible_axis", ["exact", "routing"])
def test_exact_and_routing_metric_eligibility_and_abstain_are_independent(
    ineligible_axis: str,
) -> None:
    payload = _suite_payload()
    payload["cases"][0][ineligible_axis]["metric_eligible"] = False
    payload["cases"][0][ineligible_axis]["abstain_reason"] = "scope_not_measured"

    suite = TagTruthV2Suite.model_validate(payload)
    assert getattr(suite.cases[0], ineligible_axis).metric_eligible is False
    other_axis = "routing" if ineligible_axis == "exact" else "exact"
    assert getattr(suite.cases[0], other_axis).metric_eligible is True


def test_stage1_cannot_self_declare_a_qualified_near_duplicate_check() -> None:
    payload = _suite_payload(near_duplicate_status="qualified")
    with pytest.raises(ValidationError, match="has no near-duplicate verifier"):
        TagTruthV2Suite.model_validate(payload)

    development = TagTruthV2Suite.model_validate(_suite_payload())
    assert development.data_qualification_status == "not_qualified"
    assert development.near_duplicate_check_status == "not_measured"
    assert "near_duplicate_verifier_unavailable" in development.data_qualification_reasons

    qualification_claim = _suite_payload()
    qualification_claim["data_qualification_status"] = "qualified"
    with pytest.raises(ValidationError, match="not_qualified"):
        TagTruthV2Suite.model_validate(qualification_claim)

    missing_reason = _suite_payload()
    missing_reason["data_qualification_reasons"].remove("near_duplicate_verifier_unavailable")
    with pytest.raises(ValidationError, match="missing required Stage-1 reasons"):
        TagTruthV2Suite.model_validate(missing_reason)


def test_complete_review_chain_is_a_closed_reference_contract_not_authentication() -> None:
    case_ids = ["case-0000000000000001", "case-0000000000000002"]
    chain = TagTruthV2ReviewChain.model_validate(_review_chain(case_ids, complete=True))

    assert chain.consensus_status == "complete"
    assert chain.consensus_case_ids == tuple(case_ids)
    assert {item.reviewer_id for item in chain.receipt_references} == {
        "reviewer-a",
        "reviewer-b",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("family", "at most one case per source family"),
        ("body", "duplicate normalized bodies"),
        ("template", "duplicate template clusters"),
    ],
)
def test_blind_challenge_rejects_declared_duplicate_relationships(
    mutation: str,
    message: str,
) -> None:
    payload = _suite_payload("independent_blind_challenge")
    if mutation == "family":
        payload["sources"][1]["path"] = "samples/app1/feature/src/main/ets/Page.ets"
        payload["sources"][1]["app_scope"] = "samples/app1"
        payload["sources"][1]["source_family_id"] = "samples/app1"
    elif mutation == "body":
        payload["cases"][1]["normalized_body_sha256"] = payload["cases"][0][
            "normalized_body_sha256"
        ]
    elif mutation == "template":
        payload["cases"][1]["template_cluster_id"] = payload["cases"][0]["template_cluster_id"]

    with pytest.raises(ValidationError, match=message):
        TagTruthV2Suite.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("one_receipt", "exactly two review receipts"),
        ("same_reviewer", "reviewer IDs must each be unique"),
        ("same_round", "round IDs must each be unique"),
        ("same_receipt", "receipt IDs must each be unique"),
        ("partial_receipt", "each review receipt must cover"),
        ("partial_consensus", "review consensus must cover"),
        ("contract_binding", "review receipt Tag contract fingerprint"),
        ("candidate_seen", "must satisfy all blinding attestations"),
        ("receipt_order", "review receipt references must be sorted"),
    ],
)
def test_blind_challenge_requires_complete_blinded_dual_review(
    mutation: str,
    message: str,
) -> None:
    payload = _suite_payload("independent_blind_challenge")
    chain = payload["review_chain"]
    references = chain["receipt_references"]
    if mutation == "one_receipt":
        references.pop()
    elif mutation == "same_reviewer":
        references[1]["reviewer_id"] = "reviewer-a"
    elif mutation == "same_round":
        references[1]["round_id"] = "round-1"
    elif mutation == "same_receipt":
        references[1]["receipt_id"] = references[0]["receipt_id"]
    elif mutation == "partial_receipt":
        references[0]["reviewed_case_ids"] = ["case-0000000000000001"]
    elif mutation == "partial_consensus":
        chain["consensus_case_ids"] = ["case-0000000000000001"]
    elif mutation == "contract_binding":
        references[0]["tag_contract_fingerprint"] = canonical_hash(
            "tag-contract-snapshot",
            {"different": True},
        )
    elif mutation == "candidate_seen":
        references[0]["candidate_output_seen"] = True
    elif mutation == "receipt_order":
        references.reverse()

    with pytest.raises(ValidationError, match=message):
        TagTruthV2Suite.model_validate(payload)


def test_dataset_roles_freeze_truth_review_and_prevalence_semantics() -> None:
    development = TagTruthV2Suite.model_validate(_suite_payload())
    assert development.dataset_role == "development_regression"
    assert development.truth_status == "proposed"
    assert development.natural_prevalence_claimed is False

    for role in ("independent_blind_challenge", "production_prevalence"):
        with pytest.raises(
            ValidationError,
            match="Stage 1 only loads development_regression data",
        ):
            TagTruthV2Suite.model_validate(_suite_payload(role))

    proposed_blind = _suite_payload("independent_blind_challenge")
    proposed_blind["truth_status"] = "proposed"
    proposed_blind["review_chain"] = _review_chain(
        ["case-0000000000000001", "case-0000000000000002"],
        complete=False,
    )
    with pytest.raises(ValidationError, match="require complete consensus"):
        TagTruthV2Suite.model_validate(proposed_blind)

    prevalence_claim = _suite_payload("production_prevalence")
    prevalence_claim["natural_prevalence_claimed"] = True
    with pytest.raises(ValidationError, match="Input should be False"):
        TagTruthV2Suite.model_validate(prevalence_claim)


@pytest.mark.parametrize("role", ["independent_blind_challenge", "production_prevalence"])
def test_stage1_loader_rejects_non_development_roles(tmp_path: Path, role: str) -> None:
    manifest = tmp_path / f"{role}.json"
    manifest.write_text(json.dumps(_suite_payload(role)), encoding="utf-8")

    with pytest.raises(ValueError, match="Stage 1 only loads development_regression data"):
        load_tag_truth_v2(manifest)


def test_quality_gate_snapshot_is_self_bound_and_references_real_truth() -> None:
    tampered = _suite_payload()
    tampered["quality_gates"]["minimum_exact_precision"] = 0.5
    with pytest.raises(ValidationError, match="quality_gate_id does not match"):
        TagTruthV2Suite.model_validate(tampered)

    extra_stratum = _suite_payload()
    extra_stratum["quality_gates"]["critical_negative_strata"] = [
        "hard_negative",
        "unseen_negative",
    ]
    _rebuild_quality_gate_id(extra_stratum)
    with pytest.raises(ValidationError, match=r"extra=\['unseen_negative'\], missing=\[\]"):
        TagTruthV2Suite.model_validate(extra_stratum)

    missing_stratum = _suite_payload()
    missing_stratum["quality_gates"]["critical_negative_strata"] = []
    _rebuild_quality_gate_id(missing_stratum)
    with pytest.raises(ValidationError, match=r"extra=\[\], missing=\['hard_negative'\]"):
        TagTruthV2Suite.model_validate(missing_stratum)

    for field, value in (
        ("minimum_exact_precision", 0.98),
        ("minimum_routing_recall", 0.94),
        ("minimum_exact_precision_wilson_95", 0.79),
        ("minimum_routing_recall_wilson_95", 0.79),
        ("minimum_source_families", 1),
        ("maximum_exact_false_positives", 1),
        ("maximum_routing_false_negatives", 1),
    ):
        weakened = _suite_payload()
        weakened["quality_gates"][field] = value
        _rebuild_quality_gate_id(weakened)
        with pytest.raises(ValidationError, match="weakens the Stage-1 policy floor"):
            TagTruthV2Suite.model_validate(weakened)


def test_fingerprint_changes_when_bound_truth_changes() -> None:
    original = TagTruthV2Suite.model_validate(_suite_payload())
    changed_payload = _suite_payload()
    changed_payload["cases"][0]["exact"]["rationale"] = "Different human rationale."
    changed = TagTruthV2Suite.model_validate(changed_payload)

    assert tag_truth_v2_fingerprint(original) != tag_truth_v2_fingerprint(changed)
