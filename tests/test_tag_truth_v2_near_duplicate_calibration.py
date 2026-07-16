from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    bytes_hash,
    canonical_hash,
    canonical_json,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate import (
    TagTruthV2NearDuplicatePolicy,
    load_tag_truth_v2_near_duplicate_policy,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate_calibration import (
    NearDuplicateCalibrationGateV1,
    NearDuplicateCalibrationReportV1,
    NearDuplicateHoldoutReleaseReceiptV1,
    NearDuplicatePairOraclePredictionSetV1,
    NearDuplicatePolicyApprovalReceiptV1,
    NearDuplicatePolicyCandidateFreezeV1,
    build_exhaustive_pair_oracle_predictions,
    build_near_duplicate_calibration_report,
    build_verified_near_duplicate_policy_approval_receipt,
    default_near_duplicate_calibration_gate,
    parse_near_duplicate_calibration_report,
    parse_near_duplicate_pair_oracle_predictions,
    parse_near_duplicate_policy_approval_receipt,
    seal_near_duplicate_holdout_release_receipt_payload,
    seal_near_duplicate_policy_candidate_freeze_payload,
    verify_exhaustive_pair_oracle_predictions,
    verify_near_duplicate_calibration_report,
    verify_near_duplicate_policy_approval_receipt,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate_pair_truth import (
    NearDuplicatePairConsensusV1,
    NearDuplicatePairReviewPacketV1,
    NearDuplicatePairReviewReceiptV1,
    NearDuplicatePairSelectionV1,
    PairTruthLabel,
    build_near_duplicate_pair_consensus,
    build_near_duplicate_pair_review_packet,
    pair_member_payload_with_id,
    parse_near_duplicate_pair_consensus,
    parse_near_duplicate_pair_selection,
    seal_near_duplicate_pair_review_receipt_payload,
    seal_near_duplicate_pair_selection_payload,
    validate_near_duplicate_pair_review_receipt,
    verify_near_duplicate_pair_consensus,
    verify_near_duplicate_pair_review_packet,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "tests/evaluation/tag_truth_v2/near_duplicate_shadow_policy_v1.json"

_REVISION = "a" * 40
_SELECTION_REASONS = [
    "calibration_pending",
    "dual_review_pending",
    "external_identity_not_authenticated",
    "policy_approval_pending",
]
_EVIDENCE_BLOCKERS = [
    "external_holdout_custodian_identity_not_authenticated",
    "external_pair_reviewer_identity_not_authenticated",
    "external_policy_approver_identity_not_authenticated",
    "pair_source_git_provenance_not_verified",
    "verifier_closure_git_blobs_not_verified",
]

_TextPattern = Literal["duplicate", "independent", "gray", "abstain"]
_HoldoutComponentMode = Literal[
    "distinct",
    "shared_independent",
    "shared_across_binary_labels",
]


@dataclass(frozen=True)
class _PairSpec:
    name: str
    split: Literal["calibration", "acceptance_holdout"]
    truth_label: PairTruthLabel
    text_pattern: _TextPattern


@dataclass(frozen=True)
class _CalibrationFixture:
    selection: NearDuplicatePairSelectionV1
    packet: NearDuplicatePairReviewPacketV1
    receipts: tuple[NearDuplicatePairReviewReceiptV1, NearDuplicatePairReviewReceiptV1]
    consensus: NearDuplicatePairConsensusV1
    policy: TagTruthV2NearDuplicatePolicy
    predictions: NearDuplicatePairOraclePredictionSetV1
    gate: NearDuplicateCalibrationGateV1
    freeze: NearDuplicatePolicyCandidateFreezeV1
    holdout_release: NearDuplicateHoldoutReleaseReceiptV1
    report: NearDuplicateCalibrationReportV1
    labels: Mapping[str, PairTruthLabel]
    duplicate_pair_ids: tuple[str, ...]
    independent_pair_ids: tuple[str, ...]
    gray_ambiguous_pair_ids: tuple[str, ...]
    abstain_ambiguous_pair_id: str


_OPERATORS = ("+", "-", "*", "/", "%", "&&", "||", "??", "==", "===", "<=", ">=")


def _operator_stream(prefix: str, seed: str, count: int = 96) -> str:
    state = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")
    fragments: list[str] = []
    for index in range(count):
        state = (1_103_515_245 * state + 12_345) % (2**31)
        fragments.append(f"{prefix}_{index} {_OPERATORS[state % len(_OPERATORS)]}")
    return " ".join(fragments)


def _pair_texts(name: str, pattern: _TextPattern) -> tuple[str, str]:
    identifier_name = name.replace("-", "_")
    if pattern == "duplicate":
        text = (
            f"function duplicate_{identifier_name}() "
            f"{{ {_operator_stream(identifier_name, name)} }}"
        )
        return text, text
    if pattern == "independent":
        return (
            f"function left_{identifier_name}() "
            f"{{ {_operator_stream(f'left_{identifier_name}', f'{name}:left')} }}",
            f"function right_{identifier_name}() "
            f"{{ {_operator_stream(f'right_{identifier_name}', f'{name}:right')} }}",
        )
    if pattern == "gray":
        return (
            _operator_stream(f"left_{identifier_name}", name, count=100),
            _operator_stream(f"right_{identifier_name}", name, count=100),
        )
    return f"left_{identifier_name}", f"right_{identifier_name}"


def _member_payload(
    *,
    name: str,
    side: Literal["a", "b"],
    text: str,
) -> dict[str, object]:
    raw = text.encode("utf-8")
    return {
        "repository_source_id": "pair-fixture",
        "revision": _REVISION,
        "path": f"pairs/{name}/{side}.ets",
        "axis": "file",
        "unit_start_line": None,
        "unit_end_line": None,
        "source_family_id": f"families/{name}",
        "media_class": "arkts",
        "content_sha256": bytes_hash(raw),
        "manual_related_group_ids": [],
        "line_count": 1,
        "text": text,
    }


def _specs() -> tuple[_PairSpec, ...]:
    specs: list[_PairSpec] = [
        _PairSpec("calibration-duplicate", "calibration", "duplicate", "duplicate"),
        _PairSpec("calibration-independent", "calibration", "independent", "independent"),
    ]
    specs.extend(
        _PairSpec(
            f"holdout-duplicate-{index:03d}",
            "acceptance_holdout",
            "duplicate",
            "duplicate",
        )
        for index in range(80)
    )
    specs.extend(
        _PairSpec(
            f"holdout-independent-{index:03d}",
            "acceptance_holdout",
            "independent",
            "independent",
        )
        for index in range(80)
    )
    specs.extend(
        _PairSpec(
            f"holdout-gray-ambiguous-{index:02d}",
            "acceptance_holdout",
            "ambiguous",
            "gray",
        )
        for index in range(3)
    )
    specs.append(
        _PairSpec(
            "holdout-short-ambiguous",
            "acceptance_holdout",
            "ambiguous",
            "abstain",
        )
    )
    return tuple(specs)


def _build_selection(
    specs: Sequence[_PairSpec],
    *,
    shared_manual_group_across_splits: bool = False,
    holdout_component_mode: _HoldoutComponentMode = "distinct",
) -> tuple[NearDuplicatePairSelectionV1, Mapping[str, PairTruthLabel]]:
    members: list[dict[str, object]] = []
    cases: list[dict[str, object]] = []
    labels_by_name = {spec.name: spec.truth_label for spec in specs}
    for rank, spec in enumerate(specs, start=1):
        left_text, right_text = _pair_texts(spec.name, spec.text_pattern)
        left = _member_payload(name=spec.name, side="a", text=left_text)
        right = _member_payload(name=spec.name, side="b", text=right_text)
        if shared_manual_group_across_splits and spec.name in {
            "calibration-duplicate",
            "holdout-duplicate-000",
        }:
            left["manual_related_group_ids"] = ["cross-split-group"]
        if holdout_component_mode == "shared_independent" and spec.name in {
            "holdout-independent-000",
            "holdout-independent-001",
        }:
            left["manual_related_group_ids"] = ["shared-independent-component"]
        if holdout_component_mode == "shared_across_binary_labels" and spec.name in {
            "holdout-duplicate-000",
            "holdout-independent-000",
        }:
            left["manual_related_group_ids"] = ["shared-binary-component"]
        left_id = str(pair_member_payload_with_id(left)["member_id"])
        right_id = str(pair_member_payload_with_id(right)["member_id"])
        members.extend((left, right))
        cases.append(
            {
                "split": spec.split,
                "direction": "file_file",
                "member_ids": [left_id, right_id],
                "selection_stratum_id": "synthetic_challenge",
                "selection_rank": rank,
                "coverage_strata": ["arkts_file_pair"],
            }
        )
    selection = seal_near_duplicate_pair_selection_payload(
        {
            "schema_version": "tag-truth-v2-nd-pair-selection-v1",
            "suite_id": "near-duplicate-calibration-fixture",
            "dataset_role": "near_duplicate_policy_calibration",
            "natural_prevalence_claimed": False,
            "qualification_status": "not_qualified",
            "qualification_reasons": _SELECTION_REASONS,
            "selection_process": {
                "generator_version": "synthetic-pair-fixture-v1",
                "selection_seed_commitment": bytes_hash(b"synthetic-pair-seed"),
                "split_assignment_unit": "leakage_component",
                "acceptance_holdout_visibility": ("custodian_sealed_until_policy_candidate_freeze"),
                "selected_before_policy_candidate_freeze": True,
            },
            "members": members,
            "cases": cases,
        }
    )
    labels = {
        case.pair_id: labels_by_name[specs[case.selection_rank - 1].name]
        for case in selection.cases
    }
    return selection, labels


def _review_receipt(
    packet: NearDuplicatePairReviewPacketV1,
    *,
    reviewer_id: str,
    round_id: str,
    labels: Mapping[str, PairTruthLabel],
) -> NearDuplicatePairReviewReceiptV1:
    decisions = []
    for case in packet.cases:
        label = labels[case.pair_id]
        decisions.append(
            {
                "pair_id": case.pair_id,
                "label": label,
                "side_a_evidence_lines": [1],
                "side_b_evidence_lines": [1],
                "rationale": f"{reviewer_id} reviewed both complete synthetic sides.",
                "ambiguity_reason": ("insufficient_context" if label == "ambiguous" else None),
            }
        )
    receipt = seal_near_duplicate_pair_review_receipt_payload(
        {
            "schema_version": "tag-truth-v2-nd-pair-receipt-v1",
            "round_id": round_id,
            "selection_id": packet.selection_id,
            "packet_id": packet.packet_id,
            "suite_id": packet.suite_id,
            "review_policy_fingerprint": packet.review_policy.policy_fingerprint,
            "reviewer": {
                "reviewer_id": reviewer_id,
                "reviewer_kind": "human",
                "reviewer_role": "near_duplicate_truth_reviewer",
                "affiliation": "Independent synthetic review fixture",
                "candidate_policy_design_participant": False,
                "selection_participant": False,
            },
            "blinding": {
                "selection_manifest_seen": False,
                "split_assignment_seen": False,
                "component_assignment_seen": False,
                "policy_candidate_output_seen": False,
                "algorithm_thresholds_seen": False,
                "other_reviewer_receipt_seen": False,
                "review_completed_before_unblinding": True,
                "attested_at": "2026-07-16T09:00:00Z",
            },
            "recorded_at": "2026-07-16T09:01:00Z",
            "decisions": decisions,
        }
    )
    validate_near_duplicate_pair_review_receipt(receipt, packet)
    return receipt


def _policy_freeze_payload(
    policy: TagTruthV2NearDuplicatePolicy,
    predictions: NearDuplicatePairOraclePredictionSetV1,
    gate: NearDuplicateCalibrationGateV1,
) -> dict[str, object]:
    return {
        "schema_version": "tag-truth-v2-nd-policy-freeze-v1",
        "policy_candidate_fingerprint": policy.policy_fingerprint,
        "oracle_semantics_fingerprint": predictions.semantics_fingerprint,
        "gate_fingerprint": gate.gate_fingerprint,
        "candidate_commit": "b" * 40,
        "verifier_closure": [
            {
                "path": (
                    "src/arkts_code_reviewer/feature_routing_validation/"
                    "tag_truth_v2_near_duplicate_calibration.py"
                ),
                "git_blob_id": "c" * 40,
            },
            {
                "path": (
                    "src/arkts_code_reviewer/feature_routing_validation/"
                    "tag_truth_v2_near_duplicate_pair_truth.py"
                ),
                "git_blob_id": "d" * 40,
            },
        ],
        "thresholds_finalized": True,
        "acceptance_holdout_labels_seen": False,
        "frozen_at": "2026-07-16T10:00:00Z",
    }


def _policy_freeze(
    policy: TagTruthV2NearDuplicatePolicy,
    predictions: NearDuplicatePairOraclePredictionSetV1,
    gate: NearDuplicateCalibrationGateV1,
) -> NearDuplicatePolicyCandidateFreezeV1:
    return seal_near_duplicate_policy_candidate_freeze_payload(
        _policy_freeze_payload(policy, predictions, gate)
    )


def _holdout_release_payload(
    selection: NearDuplicatePairSelectionV1,
    freeze: NearDuplicatePolicyCandidateFreezeV1,
    *,
    custodian_id: str = "holdout-custodian",
    released_at: str = "2026-07-16T11:00:00Z",
    recorded_at: str = "2026-07-16T11:01:00Z",
) -> dict[str, object]:
    return {
        "schema_version": "tag-truth-v2-nd-holdout-release-v1",
        "selection_id": selection.selection_id,
        "freeze_id": freeze.freeze_id,
        "policy_candidate_fingerprint": freeze.policy_candidate_fingerprint,
        "oracle_semantics_fingerprint": freeze.oracle_semantics_fingerprint,
        "gate_fingerprint": freeze.gate_fingerprint,
        "custodian": {
            "custodian_id": custodian_id,
            "custodian_kind": "human",
            "custodian_role": "near_duplicate_holdout_custodian",
            "affiliation": "Independent synthetic holdout custodian fixture",
            "candidate_policy_author": False,
            "pair_reviewer": False,
            "policy_approver": False,
        },
        "labels_withheld_until_policy_freeze": True,
        "calibration_actor_did_not_receive_holdout_labels_before_freeze": True,
        "released_at": released_at,
        "recorded_at": recorded_at,
    }


def _holdout_release(
    selection: NearDuplicatePairSelectionV1,
    freeze: NearDuplicatePolicyCandidateFreezeV1,
) -> NearDuplicateHoldoutReleaseReceiptV1:
    return seal_near_duplicate_holdout_release_receipt_payload(
        _holdout_release_payload(selection, freeze),
        selection=selection,
        freeze=freeze,
    )


def _build_fixture(
    selection: NearDuplicatePairSelectionV1,
    labels: Mapping[str, PairTruthLabel],
) -> _CalibrationFixture:
    packet = build_near_duplicate_pair_review_packet(selection)
    receipts = (
        _review_receipt(
            packet,
            reviewer_id="pair-reviewer-a",
            round_id="round-a",
            labels=labels,
        ),
        _review_receipt(
            packet,
            reviewer_id="pair-reviewer-b",
            round_id="round-b",
            labels=labels,
        ),
    )
    consensus = build_near_duplicate_pair_consensus(packet, receipts)
    policy = load_tag_truth_v2_near_duplicate_policy(POLICY_PATH)
    predictions = build_exhaustive_pair_oracle_predictions(selection, policy)
    gate = default_near_duplicate_calibration_gate()
    freeze = _policy_freeze(policy, predictions, gate)
    holdout_release = _holdout_release(selection, freeze)
    report = build_near_duplicate_calibration_report(
        selection=selection,
        packet=packet,
        receipts=receipts,
        consensus=consensus,
        policy=policy,
        predictions=predictions,
        gate=gate,
        freeze=freeze,
        holdout_release=holdout_release,
    )
    by_pair = {case.pair_id: case for case in selection.cases}
    duplicate_ids = tuple(
        sorted(
            pair_id
            for pair_id, label in labels.items()
            if label == "duplicate" and by_pair[pair_id].split == "acceptance_holdout"
        )
    )
    independent_ids = tuple(
        sorted(
            pair_id
            for pair_id, label in labels.items()
            if label == "independent" and by_pair[pair_id].split == "acceptance_holdout"
        )
    )
    gray_ids = tuple(
        sorted(
            prediction.pair_id
            for prediction in predictions.predictions
            if labels[prediction.pair_id] == "ambiguous" and prediction.decision == "gray"
        )
    )
    abstain_ids = tuple(
        prediction.pair_id
        for prediction in predictions.predictions
        if labels[prediction.pair_id] == "ambiguous" and prediction.decision == "abstain"
    )
    assert len(duplicate_ids) == 80
    assert len(independent_ids) == 80
    assert len(gray_ids) == 3
    assert len(abstain_ids) == 1
    return _CalibrationFixture(
        selection=selection,
        packet=packet,
        receipts=receipts,
        consensus=consensus,
        policy=policy,
        predictions=predictions,
        gate=gate,
        freeze=freeze,
        holdout_release=holdout_release,
        report=report,
        labels=labels,
        duplicate_pair_ids=duplicate_ids,
        independent_pair_ids=independent_ids,
        gray_ambiguous_pair_ids=gray_ids,
        abstain_ambiguous_pair_id=abstain_ids[0],
    )


def _report_for_labels(
    fixture: _CalibrationFixture,
    labels_a: Mapping[str, PairTruthLabel],
    labels_b: Mapping[str, PairTruthLabel] | None = None,
) -> tuple[
    tuple[NearDuplicatePairReviewReceiptV1, NearDuplicatePairReviewReceiptV1],
    NearDuplicatePairConsensusV1,
    NearDuplicateCalibrationReportV1,
]:
    second_labels = labels_b or labels_a
    receipts = (
        _review_receipt(
            fixture.packet,
            reviewer_id="pair-reviewer-a",
            round_id="round-a",
            labels=labels_a,
        ),
        _review_receipt(
            fixture.packet,
            reviewer_id="pair-reviewer-b",
            round_id="round-b",
            labels=second_labels,
        ),
    )
    consensus = build_near_duplicate_pair_consensus(fixture.packet, receipts)
    report = build_near_duplicate_calibration_report(
        selection=fixture.selection,
        packet=fixture.packet,
        receipts=receipts,
        consensus=consensus,
        policy=fixture.policy,
        predictions=fixture.predictions,
        gate=fixture.gate,
        freeze=fixture.freeze,
        holdout_release=fixture.holdout_release,
    )
    return receipts, consensus, report


@pytest.fixture(scope="module")
def passing_fixture() -> _CalibrationFixture:
    selection, labels = _build_selection(_specs())
    return _build_fixture(selection, labels)


def _approval_payload(
    fixture: _CalibrationFixture,
    *,
    report: NearDuplicateCalibrationReportV1 | None = None,
    approver_id: str = "policy-approver",
    decision: Literal["approved", "rejected", "abstained"] = "approved",
) -> dict[str, object]:
    bound_report = report or fixture.report
    approved = decision == "approved"
    return {
        "schema_version": "tag-truth-v2-nd-policy-approval-receipt-v1",
        "policy_candidate_fingerprint": bound_report.policy_candidate_fingerprint,
        "oracle_semantics_fingerprint": bound_report.oracle_semantics_fingerprint,
        "calibration_report_id": bound_report.report_id,
        "gate_fingerprint": bound_report.gate_fingerprint,
        "policy_candidate_freeze_id": bound_report.policy_candidate_freeze_id,
        "holdout_release_receipt_id": bound_report.holdout_release_receipt_id,
        "selection_id": bound_report.selection_id,
        "consensus_id": bound_report.consensus_id,
        "approver": {
            "approver_id": approver_id,
            "approver_kind": "human",
            "approver_role": "near_duplicate_policy_approver",
            "affiliation": "Independent policy approval fixture",
            "candidate_policy_author": False,
            "pair_selector": False,
            "pair_reviewer": False,
            "holdout_custodian": False,
        },
        "attestation": {
            "full_report_verified": True,
            "acceptance_holdout_not_seen_before_policy_freeze": True,
            "thresholds_unchanged_after_holdout_unseal": True,
            "blind_campaign_data_not_used_for_calibration": True,
            "attested_at": "2026-07-16T12:00:00Z",
        },
        "recorded_at": "2026-07-16T12:01:00Z",
        "decision": decision,
        "approved_scope": (
            "future_verified_near_duplicate_screening_policy_semantics" if approved else None
        ),
        "decision_blockers": [] if approved else ["calibration_not_accepted"],
        "rationale": "The complete sealed calibration report was independently reviewed.",
        "evidence_qualification_status": "not_qualified",
        "evidence_qualification_blockers": _EVIDENCE_BLOCKERS,
    }


def _build_verified_approval(
    fixture: _CalibrationFixture,
    payload: Mapping[str, object],
    *,
    report: NearDuplicateCalibrationReportV1 | None = None,
    receipts: Sequence[NearDuplicatePairReviewReceiptV1] | None = None,
    consensus: NearDuplicatePairConsensusV1 | None = None,
) -> NearDuplicatePolicyApprovalReceiptV1:
    return build_verified_near_duplicate_policy_approval_receipt(
        payload,
        report=report or fixture.report,
        selection=fixture.selection,
        packet=fixture.packet,
        receipts=receipts or fixture.receipts,
        consensus=consensus or fixture.consensus,
        policy=fixture.policy,
        predictions=fixture.predictions,
        gate=fixture.gate,
        freeze=fixture.freeze,
        holdout_release=fixture.holdout_release,
    )


def test_complete_pair_truth_to_oracle_report_chain_passes_but_remains_not_approved(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    verify_near_duplicate_pair_review_packet(fixture.packet, fixture.selection)
    for receipt in fixture.receipts:
        validate_near_duplicate_pair_review_receipt(receipt, fixture.packet)
    verify_near_duplicate_pair_consensus(
        fixture.consensus,
        fixture.packet,
        tuple(reversed(fixture.receipts)),
    )
    verify_exhaustive_pair_oracle_predictions(
        fixture.predictions,
        fixture.selection,
        fixture.policy,
    )
    verify_near_duplicate_calibration_report(
        fixture.report,
        selection=fixture.selection,
        packet=fixture.packet,
        receipts=tuple(reversed(fixture.receipts)),
        consensus=fixture.consensus,
        policy=fixture.policy,
        predictions=fixture.predictions,
        gate=fixture.gate,
        freeze=fixture.freeze,
        holdout_release=fixture.holdout_release,
    )

    metrics = fixture.report.acceptance_holdout_metrics
    assert metrics.truth_duplicate_count == 80
    assert metrics.truth_independent_count == 80
    assert metrics.truth_ambiguous_count == 4
    assert metrics.strict_duplicate_precision.point_ppm == 1_000_000
    assert metrics.strict_duplicate_precision.wilson_lower_ppm == 954_182
    assert metrics.strict_duplicate_recall.point_ppm == 1_000_000
    assert metrics.independent_clear_precision.point_ppm == 1_000_000
    assert metrics.independent_clear_recall.point_ppm == 1_000_000
    assert metrics.duplicate_block_recall.point_ppm == 1_000_000
    assert fixture.report.acceptance_holdout_review_quality.raw_agreement_ppm == 1_000_000
    assert fixture.report.acceptance_holdout_review_quality.cohen_kappa_ppm == 1_000_000
    assert fixture.report.calibration_gate_status == "passed"
    assert fixture.report.policy_approval_readiness == "eligible_for_human_review"
    assert fixture.report.policy_approval_status == "not_approved"
    assert fixture.report.qualification_blockers == ()
    assert fixture.report.evidence_qualification_status == "not_qualified"
    assert list(fixture.report.evidence_qualification_blockers) == _EVIDENCE_BLOCKERS


def test_ambiguous_and_unresolved_cases_are_preserved_but_excluded_from_binary_metrics(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    ambiguous_cases = tuple(case for case in fixture.consensus.cases if case.label == "ambiguous")
    assert len(ambiguous_cases) == 4
    assert all(case.consensus_status == "agreed_ambiguous" for case in ambiguous_cases)
    assert all(case.metric_role == "ambiguous_guard" for case in ambiguous_cases)

    labels_b = dict(fixture.labels)
    labels_b[fixture.gray_ambiguous_pair_ids[0]] = "independent"
    _, consensus, report = _report_for_labels(fixture, fixture.labels, labels_b)
    unresolved = next(
        case for case in consensus.cases if case.pair_id == fixture.gray_ambiguous_pair_ids[0]
    )
    assert unresolved.consensus_status == "unresolved"
    assert unresolved.label is None
    assert unresolved.metric_role == "excluded"
    assert consensus.consensus_status == "unresolved"
    assert report.acceptance_holdout_metrics.truth_duplicate_count == 80
    assert report.acceptance_holdout_metrics.truth_independent_count == 80
    assert report.acceptance_holdout_metrics.truth_ambiguous_count == 3
    assert report.acceptance_holdout_metrics.unresolved_case_count == 1
    assert report.acceptance_holdout_review_quality.unresolved_count == 1
    assert report.calibration_gate_status == "passed"


@pytest.mark.parametrize(
    ("pair_group", "new_label", "expected_blocker"),
    [
        ("independent", "duplicate", "acceptance_holdout_false_clear_duplicate"),
        ("duplicate", "independent", "acceptance_holdout_hard_reject_independent"),
        ("abstain", "duplicate", "acceptance_holdout_binary_abstain"),
        ("duplicate", "ambiguous", "acceptance_holdout_hard_reject_ambiguous"),
        ("independent", "ambiguous", "acceptance_holdout_auto_clear_ambiguous"),
    ],
)
def test_calibration_fails_closed_for_every_fatal_truth_machine_transition(
    passing_fixture: _CalibrationFixture,
    pair_group: str,
    new_label: PairTruthLabel,
    expected_blocker: str,
) -> None:
    fixture = passing_fixture
    pair_id = {
        "duplicate": fixture.duplicate_pair_ids[0],
        "independent": fixture.independent_pair_ids[0],
        "abstain": fixture.abstain_ambiguous_pair_id,
    }[pair_group]
    labels = dict(fixture.labels)
    labels[pair_id] = new_label

    _, _, report = _report_for_labels(fixture, labels)

    assert report.calibration_gate_status == "failed"
    assert report.policy_approval_readiness == "not_eligible"
    assert expected_blocker in report.qualification_blockers
    assert report.policy_approval_status == "not_approved"


def test_three_gray_duplicate_truth_pairs_fail_strict_recall_and_wilson_gates(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    labels = dict(fixture.labels)
    for pair_id in fixture.gray_ambiguous_pair_ids:
        labels[pair_id] = "duplicate"

    _, _, report = _report_for_labels(fixture, labels)

    metrics = report.acceptance_holdout_metrics
    assert metrics.truth_duplicate_count == 83
    assert metrics.false_clear_duplicate_count == 0
    assert metrics.binary_abstain_count == 0
    assert metrics.duplicate_block_recall.point_ppm == 1_000_000
    assert metrics.strict_duplicate_recall.point_ppm == 963_855
    assert "strict_duplicate_recall_below_gate" in report.qualification_blockers
    assert "strict_duplicate_recall_wilson_below_gate" in report.qualification_blockers


def test_review_disagreement_above_five_percent_and_missing_truth_fail_closed(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    labels_b = dict(fixture.labels)
    for pair_id in fixture.duplicate_pair_ids[:9]:
        labels_b[pair_id] = "independent"

    _, _, report = _report_for_labels(fixture, fixture.labels, labels_b)

    quality = report.acceptance_holdout_review_quality
    assert quality.unresolved_count == 9
    assert quality.unresolved_rate_ppm is not None
    assert quality.unresolved_rate_ppm > 50_000
    assert report.acceptance_holdout_metrics.truth_duplicate_count == 71
    assert "unresolved_review_rate_above_gate" in report.qualification_blockers
    assert "acceptance_holdout_duplicate_case_count_below_gate" in report.qualification_blockers


def test_selection_rejects_leakage_components_that_cross_splits() -> None:
    with pytest.raises(ValueError, match="leakage component crosses"):
        _build_selection(_specs(), shared_manual_group_across_splits=True)


@pytest.mark.parametrize(
    ("component_mode", "expected_independent_components", "expected_component_blockers"),
    [
        (
            "shared_independent",
            79,
            {
                "acceptance_holdout_independent_component_count_below_gate",
                "acceptance_holdout_metric_pairs_share_component",
            },
        ),
        (
            "shared_across_binary_labels",
            80,
            {"acceptance_holdout_metric_pairs_share_component"},
        ),
    ],
)
def test_case_count_cannot_substitute_for_independent_component_coverage(
    component_mode: _HoldoutComponentMode,
    expected_independent_components: int,
    expected_component_blockers: set[str],
) -> None:
    selection, labels = _build_selection(
        _specs(),
        holdout_component_mode=component_mode,
    )
    report = _build_fixture(selection, labels).report
    metrics = report.acceptance_holdout_metrics

    assert metrics.truth_duplicate_count == 80
    assert metrics.truth_independent_count == 80
    assert metrics.duplicate_component_count == 80
    assert metrics.independent_component_count == expected_independent_components
    assert report.calibration_gate_status == "failed"
    component_blockers = {
        blocker for blocker in report.qualification_blockers if "component" in blocker
    }
    assert component_blockers == expected_component_blockers


def test_receipt_validation_rejects_incomplete_coverage_and_false_blinding(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    incomplete_payload = fixture.receipts[0].model_dump(
        mode="json",
        exclude={"receipt_id"},
    )
    incomplete_payload["decisions"] = incomplete_payload["decisions"][:-1]
    incomplete = seal_near_duplicate_pair_review_receipt_payload(incomplete_payload)
    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_near_duplicate_pair_review_receipt(incomplete, fixture.packet)

    false_blinding = fixture.receipts[0].model_dump(
        mode="json",
        exclude={"receipt_id"},
    )
    false_blinding["blinding"]["policy_candidate_output_seen"] = True
    with pytest.raises(ValidationError):
        seal_near_duplicate_pair_review_receipt_payload(false_blinding)

    missing_ambiguity_reason = fixture.receipts[0].model_dump(
        mode="json",
        exclude={"receipt_id"},
    )
    decision = next(
        item for item in missing_ambiguity_reason["decisions"] if item["label"] == "ambiguous"
    )
    decision["ambiguity_reason"] = None
    with pytest.raises(ValidationError, match="ambiguity_reason"):
        seal_near_duplicate_pair_review_receipt_payload(missing_ambiguity_reason)


def test_pair_and_calibration_parsers_reject_duplicate_keys_unknown_fields_and_hash_drift(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    selection_raw = canonical_json(fixture.selection.model_dump(mode="json")).encode()
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_near_duplicate_pair_selection(
            selection_raw.replace(b"{", b'{"schema_version":"forged",', 1)
        )

    report_payload = fixture.report.model_dump(mode="json")
    report_payload["unknown"] = True
    with pytest.raises(ValueError, match="extra"):
        parse_near_duplicate_calibration_report(json.dumps(report_payload).encode())

    forged_report = fixture.report.model_dump(mode="json")
    forged_report["policy_approval_status"] = "approved"
    with pytest.raises(ValueError, match="not_approved|literal"):
        parse_near_duplicate_calibration_report(json.dumps(forged_report).encode())

    oracle_payload = fixture.predictions.model_dump(mode="json")
    oracle_payload["policy_fingerprint"] = "tag-truth-near-duplicate-policy:sha256:" + "0" * 64
    with pytest.raises(ValueError, match="prediction-set ID"):
        parse_near_duplicate_pair_oracle_predictions(json.dumps(oracle_payload).encode())

    consensus_payload = fixture.consensus.model_dump(mode="json")
    consensus_payload["consensus_status"] = "unresolved"
    with pytest.raises(ValueError, match="aggregate status"):
        parse_near_duplicate_pair_consensus(json.dumps(consensus_payload).encode())


def test_policy_freeze_and_holdout_release_bindings_are_enforced(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    freeze_payload = _policy_freeze_payload(
        fixture.policy,
        fixture.predictions,
        fixture.gate,
    )
    freeze_payload["gate_fingerprint"] = fixture.gate.gate_fingerprint[:-1] + (
        "0" if fixture.gate.gate_fingerprint[-1] != "0" else "1"
    )
    mismatched_freeze = seal_near_duplicate_policy_candidate_freeze_payload(freeze_payload)
    mismatched_release = seal_near_duplicate_holdout_release_receipt_payload(
        _holdout_release_payload(fixture.selection, mismatched_freeze),
        selection=fixture.selection,
        freeze=mismatched_freeze,
    )
    with pytest.raises(ValueError, match="freeze differs from policy"):
        build_near_duplicate_calibration_report(
            selection=fixture.selection,
            packet=fixture.packet,
            receipts=fixture.receipts,
            consensus=fixture.consensus,
            policy=fixture.policy,
            predictions=fixture.predictions,
            gate=fixture.gate,
            freeze=mismatched_freeze,
            holdout_release=mismatched_release,
        )

    release_payload = _holdout_release_payload(fixture.selection, fixture.freeze)
    release_payload["freeze_id"] = fixture.freeze.freeze_id[:-1] + (
        "0" if fixture.freeze.freeze_id[-1] != "0" else "1"
    )
    with pytest.raises(ValueError, match="differs from selection or policy freeze"):
        seal_near_duplicate_holdout_release_receipt_payload(
            release_payload,
            selection=fixture.selection,
            freeze=fixture.freeze,
        )


def test_holdout_release_and_policy_approval_times_are_strictly_ordered(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    with pytest.raises(ValueError, match="released after policy candidate freeze"):
        seal_near_duplicate_holdout_release_receipt_payload(
            _holdout_release_payload(
                fixture.selection,
                fixture.freeze,
                released_at=fixture.freeze.frozen_at,
                recorded_at="2026-07-16T10:01:00Z",
            ),
            selection=fixture.selection,
            freeze=fixture.freeze,
        )

    with pytest.raises(ValidationError, match="recorded before release"):
        seal_near_duplicate_holdout_release_receipt_payload(
            _holdout_release_payload(
                fixture.selection,
                fixture.freeze,
                released_at="2026-07-16T11:00:00Z",
                recorded_at="2026-07-16T10:59:59Z",
            ),
            selection=fixture.selection,
            freeze=fixture.freeze,
        )

    approval_payload = _approval_payload(fixture)
    attestation = approval_payload["attestation"]
    assert isinstance(attestation, dict)
    attestation["attested_at"] = "2026-07-16T10:30:00Z"
    approval_payload["recorded_at"] = "2026-07-16T10:31:00Z"
    with pytest.raises(ValueError, match="must occur after holdout release"):
        _build_verified_approval(fixture, approval_payload)


def test_self_hashed_forged_report_cannot_reach_verified_policy_approval(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    forged_payload = fixture.report.model_dump(
        mode="json",
        exclude={"report_id"},
    )
    forged_payload["policy_candidate_freeze_id"] = fixture.freeze.freeze_id[:-1] + (
        "0" if fixture.freeze.freeze_id[-1] != "0" else "1"
    )
    forged_payload["report_id"] = canonical_hash(
        "tag-truth-nd-calibration-report",
        forged_payload,
    )
    forged_report = NearDuplicateCalibrationReportV1.model_validate(forged_payload)

    with pytest.raises(ValueError, match="does not rebuild from sealed inputs"):
        _build_verified_approval(
            fixture,
            _approval_payload(fixture, report=forged_report),
            report=forged_report,
        )


def test_policy_approval_receipt_requires_a_distinct_human_and_exact_report_binding(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    receipt = _build_verified_approval(
        fixture,
        _approval_payload(fixture),
    )
    verify_near_duplicate_policy_approval_receipt(
        receipt,
        report=fixture.report,
        selection=fixture.selection,
        packet=fixture.packet,
        receipts=fixture.receipts,
        consensus=fixture.consensus,
        policy=fixture.policy,
        predictions=fixture.predictions,
        gate=fixture.gate,
        freeze=fixture.freeze,
        holdout_release=fixture.holdout_release,
    )
    raw = canonical_json(receipt.model_dump(mode="json")).encode()
    assert parse_near_duplicate_policy_approval_receipt(raw) == receipt
    assert receipt.evidence_qualification_status == "not_qualified"
    assert list(receipt.evidence_qualification_blockers) == _EVIDENCE_BLOCKERS

    same_as_reviewer = _approval_payload(
        fixture,
        approver_id=fixture.report.reviewer_ids[0],
    )
    with pytest.raises(ValueError, match="cannot be a Pair Truth reviewer"):
        _build_verified_approval(
            fixture,
            same_as_reviewer,
        )

    same_as_custodian = _approval_payload(
        fixture,
        approver_id=fixture.holdout_release.custodian.custodian_id,
    )
    with pytest.raises(ValueError, match="cannot be the holdout custodian"):
        _build_verified_approval(
            fixture,
            same_as_custodian,
        )

    wrong_binding = _approval_payload(fixture)
    wrong_binding["calibration_report_id"] = "tag-truth-nd-calibration-report:sha256:" + "0" * 64
    with pytest.raises(ValueError, match="differs from calibration report"):
        _build_verified_approval(
            fixture,
            wrong_binding,
        )

    approved_with_blocker = _approval_payload(fixture)
    approved_with_blocker["decision_blockers"] = ["manual_exception"]
    with pytest.raises(ValidationError, match="scope and no blockers"):
        _build_verified_approval(
            fixture,
            approved_with_blocker,
        )

    rejected_without_blocker = _approval_payload(fixture, decision="rejected")
    rejected_without_blocker["decision_blockers"] = []
    with pytest.raises(ValidationError, match="requires blockers"):
        _build_verified_approval(
            fixture,
            rejected_without_blocker,
        )


def test_failed_calibration_report_cannot_support_an_approved_receipt(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    labels = dict(fixture.labels)
    labels[fixture.independent_pair_ids[0]] = "duplicate"
    receipts, consensus, failed_report = _report_for_labels(fixture, labels)
    assert failed_report.calibration_gate_status == "failed"

    with pytest.raises(ValueError, match="failed calibration report"):
        _build_verified_approval(
            fixture,
            _approval_payload(fixture, report=failed_report),
            report=failed_report,
            receipts=receipts,
            consensus=consensus,
        )


def test_gate_and_artifacts_are_strictly_frozen_models(
    passing_fixture: _CalibrationFixture,
) -> None:
    fixture = passing_fixture
    assert fixture.selection.model_config.get("frozen") is True
    assert fixture.report.model_config.get("frozen") is True
    assert fixture.gate.policy_approval_status == "not_approved"

    gate_payload = fixture.gate.model_dump(mode="json")
    gate_payload["minimum_duplicate_cases"] = 79
    with pytest.raises(ValidationError):
        NearDuplicateCalibrationGateV1.model_validate(gate_payload)

    with pytest.raises(ValidationError, match="frozen"):
        fixture.report.__setattr__("policy_approval_status", "approved")

    approval_payload = _approval_payload(fixture)
    approver = approval_payload["approver"]
    assert isinstance(approver, dict)
    approver["candidate_policy_author"] = True
    with pytest.raises(ValidationError):
        _build_verified_approval(
            fixture,
            approval_payload,
        )
