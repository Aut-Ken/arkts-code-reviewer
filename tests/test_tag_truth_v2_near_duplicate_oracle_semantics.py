from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    bytes_hash,
    canonical_hash,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate import (
    SimilarityScores,
    TagTruthV2NearDuplicatePolicy,
    load_tag_truth_v2_near_duplicate_policy,
    near_duplicate_policy_payload_with_fingerprint,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate_calibration import (
    MachinePairDecision,
    NearDuplicatePairOraclePredictionSetV1,
    PairOracleComparison,
    PairOraclePrediction,
    build_exhaustive_pair_oracle_predictions,
    verify_exhaustive_pair_oracle_predictions,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate_pair_truth import (
    NearDuplicatePairSelectionV1,
    PairAxis,
    PairDirection,
    PairSplit,
    pair_member_payload_with_id,
    seal_near_duplicate_pair_selection_payload,
)

POLICY_PATH = "tests/evaluation/tag_truth_v2/near_duplicate_shadow_policy_v1.json"

_QUALIFICATION_REASONS = [
    "calibration_pending",
    "dual_review_pending",
    "external_identity_not_authenticated",
    "policy_approval_pending",
]
_DIRECTION_AXES: dict[PairDirection, tuple[PairAxis, PairAxis]] = {
    "file_file": ("file", "file"),
    "unit_file": ("unit", "file"),
    "unit_unit": ("unit", "unit"),
}
_SEMANTICS_PAYLOAD: dict[str, object] = {
    "oracle_semantics": "near-duplicate-shadow-v1-canonical-exhaustive",
    "canonical_similarity": "tag-truth-v2-near-duplicate-screening-v1",
    "direction_policy": {
        "file_file": "bidirectional",
        "unit_file": "left-unit-to-right-file-only",
        "unit_unit": "bidirectional",
    },
    "pair_reducer": ("duplicate", "gray", "abstain", "clear"),
    "selected_short_probe_action": "abstain_after_exact_similarity",
    "comparison_scope": "manifest_pairs_without_prefilter",
}


@dataclass(frozen=True)
class _CaseSpec:
    name: str
    split: PairSplit
    direction: PairDirection
    left_text: str
    right_text: str


def _member_draft(
    *,
    case_name: str,
    side: str,
    axis: PairAxis,
    text: str,
) -> dict[str, object]:
    line_count = max(1, len(text.splitlines()))
    return {
        "repository_source_id": "oracle_fixture",
        "revision": "a" * 40,
        "path": f"fixtures/{case_name}-{side}.ets",
        "axis": axis,
        "unit_start_line": 1 if axis == "unit" else None,
        "unit_end_line": line_count if axis == "unit" else None,
        "source_family_id": f"families/{case_name}-{side}",
        "media_class": "arkts",
        "content_sha256": bytes_hash(text.encode("utf-8")),
        "manual_related_group_ids": [],
        "line_count": line_count,
        "text": text,
    }


def _selection(
    specs: tuple[_CaseSpec, ...],
    *,
    suite_id: str = "oracle_semantics_fixture",
) -> NearDuplicatePairSelectionV1:
    members: list[dict[str, object]] = []
    cases: list[dict[str, object]] = []
    for rank, spec in enumerate(specs, start=1):
        left_axis, right_axis = _DIRECTION_AXES[spec.direction]
        left = _member_draft(
            case_name=spec.name,
            side="left",
            axis=left_axis,
            text=spec.left_text,
        )
        right = _member_draft(
            case_name=spec.name,
            side="right",
            axis=right_axis,
            text=spec.right_text,
        )
        left_id = cast(str, pair_member_payload_with_id(left)["member_id"])
        right_id = cast(str, pair_member_payload_with_id(right)["member_id"])
        members.extend((left, right))
        cases.append(
            {
                "split": spec.split,
                "direction": spec.direction,
                "member_ids": [left_id, right_id],
                "selection_stratum_id": spec.name,
                "selection_rank": rank,
                "coverage_strata": [f"{spec.direction}_coverage"],
            }
        )
    return seal_near_duplicate_pair_selection_payload(
        {
            "schema_version": "tag-truth-v2-nd-pair-selection-v1",
            "suite_id": suite_id,
            "dataset_role": "near_duplicate_policy_calibration",
            "natural_prevalence_claimed": False,
            "qualification_status": "not_qualified",
            "qualification_reasons": _QUALIFICATION_REASONS,
            "selection_process": {
                "generator_version": "oracle-test-v1",
                "selection_seed_commitment": bytes_hash(b"oracle-semantics-test-seed"),
                "split_assignment_unit": "leakage_component",
                "acceptance_holdout_visibility": ("custodian_sealed_until_policy_candidate_freeze"),
                "selected_before_policy_candidate_freeze": True,
            },
            "members": members,
            "cases": cases,
        }
    )


def _policy() -> TagTruthV2NearDuplicatePolicy:
    return load_tag_truth_v2_near_duplicate_policy(POLICY_PATH)


def _predictions_by_stratum(
    selection: NearDuplicatePairSelectionV1,
    policy: TagTruthV2NearDuplicatePolicy,
) -> dict[str, PairOraclePrediction]:
    predictions = build_exhaustive_pair_oracle_predictions(selection, policy)
    by_pair_id = {prediction.pair_id: prediction for prediction in predictions.predictions}
    return {case.selection_stratum_id: by_pair_id[case.pair_id] for case in selection.cases}


def test_short_exact_remains_duplicate_but_short_unrelated_clear_becomes_abstain() -> None:
    selection = _selection(
        (
            _CaseSpec(
                name="short_exact",
                split="calibration",
                direction="unit_file",
                left_text="alpha beta gamma",
                right_text="alpha beta gamma",
            ),
            _CaseSpec(
                name="short_unrelated",
                split="acceptance_holdout",
                direction="unit_file",
                left_text="left + middle + right",
                right_text="if (ready) { return value; }",
            ),
        )
    )

    predictions = _predictions_by_stratum(selection, _policy())
    exact = predictions["short_exact"]
    unrelated = predictions["short_unrelated"]

    assert exact.decision == "duplicate"
    assert len(exact.comparisons) == 1
    assert exact.comparisons[0].similarity_decision == "duplicate"
    assert exact.comparisons[0].decision == "duplicate"
    assert exact.comparisons[0].blockers == ()

    assert unrelated.decision == "abstain"
    assert len(unrelated.comparisons) == 1
    assert unrelated.comparisons[0].similarity_decision == "clear"
    assert unrelated.comparisons[0].decision == "abstain"
    assert unrelated.comparisons[0].blockers == ("selected_too_short_for_policy",)


def test_direction_contract_is_one_way_for_unit_file_and_bidirectional_otherwise() -> None:
    selection = _selection(
        (
            _CaseSpec(
                name="unit_file_direction",
                split="calibration",
                direction="unit_file",
                left_text=" ".join(f"unitLeft{index}" for index in range(50)),
                right_text=" ".join(f"fileRight{index}" for index in range(50)),
            ),
            _CaseSpec(
                name="file_file_direction",
                split="acceptance_holdout",
                direction="file_file",
                left_text=" ".join(f"fileA{index} +" for index in range(60)),
                right_text=" ".join(f"fileB{index} -" for index in range(60)),
            ),
            _CaseSpec(
                name="unit_unit_direction",
                split="calibration",
                direction="unit_unit",
                left_text=" ".join(f"unitA{index} *" for index in range(80)),
                right_text=" ".join(f"unitB{index} /" for index in range(80)),
            ),
        )
    )

    predictions = _predictions_by_stratum(selection, _policy())
    unit_file = predictions["unit_file_direction"].comparisons
    file_file = predictions["file_file_direction"].comparisons
    unit_unit = predictions["unit_unit_direction"].comparisons

    assert len(unit_file) == 1
    assert (unit_file[0].selected_axis, unit_file[0].reference_axis) == ("unit", "file")

    assert len(file_file) == 2
    assert {
        (comparison.selected_member_id, comparison.reference_member_id) for comparison in file_file
    } == {
        (file_file[0].selected_member_id, file_file[0].reference_member_id),
        (file_file[0].reference_member_id, file_file[0].selected_member_id),
    }
    assert all(
        (comparison.selected_axis, comparison.reference_axis) == ("file", "file")
        for comparison in file_file
    )

    assert len(unit_unit) == 2
    assert {
        (comparison.selected_member_id, comparison.reference_member_id) for comparison in unit_unit
    } == {
        (unit_unit[0].selected_member_id, unit_unit[0].reference_member_id),
        (unit_unit[0].reference_member_id, unit_unit[0].selected_member_id),
    }
    assert all(
        (comparison.selected_axis, comparison.reference_axis) == ("unit", "unit")
        for comparison in unit_unit
    )


def _scores(
    *,
    selected_shingles: int = 40,
    reference_shingles: int = 40,
    shared_shingles: int = 0,
    exact: bool = False,
) -> SimilarityScores:
    return SimilarityScores(
        selected_content_token_count=50,
        reference_content_token_count=50,
        selected_content_shingle_count=selected_shingles,
        reference_content_shingle_count=reference_shingles,
        shared_content_shingle_count=shared_shingles,
        content_union_shingle_count=selected_shingles + reference_shingles - shared_shingles,
        selected_shape_shingle_count=40,
        reference_shape_shingle_count=40,
        shared_shape_shingle_count=0,
        longest_contiguous_token_run=0,
        normalized_token_stream_equal=exact,
        normalized_shape_token_stream_equal=False,
    )


def _comparison(label: str, decision: MachinePairDecision) -> PairOracleComparison:
    selected_member_id = canonical_hash("tag-truth-nd-pair-member", {"selected": label})
    reference_member_id = canonical_hash("tag-truth-nd-pair-member", {"reference": label})
    if decision == "duplicate":
        return PairOracleComparison(
            selected_member_id=selected_member_id,
            reference_member_id=reference_member_id,
            selected_axis="file",
            reference_axis="file",
            similarity_decision="duplicate",
            decision="duplicate",
            signals=("normalized_token_stream_equal",),
            scores=_scores(shared_shingles=40, exact=True),
            tokenization_issues=(),
            blockers=(),
        )
    if decision == "gray":
        return PairOracleComparison(
            selected_member_id=selected_member_id,
            reference_member_id=reference_member_id,
            selected_axis="file",
            reference_axis="file",
            similarity_decision="gray",
            decision="gray",
            signals=("content_jaccard",),
            scores=_scores(
                selected_shingles=100,
                reference_shingles=100,
                shared_shingles=70,
            ),
            tokenization_issues=(),
            blockers=(),
        )
    if decision == "abstain":
        return PairOracleComparison(
            selected_member_id=selected_member_id,
            reference_member_id=reference_member_id,
            selected_axis="file",
            reference_axis="file",
            similarity_decision="abstain",
            decision="abstain",
            signals=(),
            scores=_scores(),
            tokenization_issues=("unterminated_string_literal",),
            blockers=("tokenization_issue",),
        )
    if decision == "clear":
        return PairOracleComparison(
            selected_member_id=selected_member_id,
            reference_member_id=reference_member_id,
            selected_axis="file",
            reference_axis="file",
            similarity_decision="clear",
            decision="clear",
            signals=(),
            scores=_scores(),
            tokenization_issues=(),
            blockers=(),
        )
    raise AssertionError(f"unsupported fixture decision: {decision}")


@pytest.mark.parametrize(
    ("decisions", "expected"),
    [
        (("clear",), "clear"),
        (("clear", "abstain"), "abstain"),
        (("clear", "abstain", "gray"), "gray"),
        (("clear", "abstain", "gray", "duplicate"), "duplicate"),
    ],
)
def test_pair_reducer_uses_duplicate_gray_abstain_clear_priority(
    decisions: tuple[MachinePairDecision, ...],
    expected: MachinePairDecision,
) -> None:
    comparisons = tuple(
        sorted(
            (
                _comparison(f"comparison-{index}", decision)
                for index, decision in enumerate(decisions)
            ),
            key=lambda item: (
                item.selected_member_id,
                item.reference_member_id,
                item.selected_axis,
                item.reference_axis,
            ),
        )
    )

    prediction = PairOraclePrediction(
        pair_id=canonical_hash("tag-truth-nd-pair", {"fixture": decisions}),
        decision=expected,
        comparisons=comparisons,
    )

    assert prediction.decision == expected


def _changed_policy(
    policy: TagTruthV2NearDuplicatePolicy,
) -> TagTruthV2NearDuplicatePolicy:
    payload = policy.model_dump(mode="json", exclude={"policy_fingerprint"})
    payload["gray_content_jaccard"] = {"numerator": 2, "denominator": 5}
    return TagTruthV2NearDuplicatePolicy.model_validate(
        near_duplicate_policy_payload_with_fingerprint(payload)
    )


def test_full_verifier_rejects_cross_selection_cross_policy_and_rehashed_tampering() -> None:
    specs = (
        _CaseSpec(
            name="verification_calibration",
            split="calibration",
            direction="unit_file",
            left_text=" ".join(f"calibrationUnit{index}" for index in range(50)),
            right_text=" ".join(f"calibrationFile{index}" for index in range(50)),
        ),
        _CaseSpec(
            name="verification_holdout",
            split="acceptance_holdout",
            direction="file_file",
            left_text=" ".join(f"holdoutLeft{index} +" for index in range(60)),
            right_text=" ".join(f"holdoutRight{index} -" for index in range(60)),
        ),
    )
    selection = _selection(specs)
    other_selection = _selection(specs, suite_id="other_oracle_semantics_fixture")
    policy = _policy()
    predictions = build_exhaustive_pair_oracle_predictions(selection, policy)

    with pytest.raises(ValueError, match="do not rebuild"):
        verify_exhaustive_pair_oracle_predictions(predictions, other_selection, policy)
    with pytest.raises(ValueError, match="do not rebuild"):
        verify_exhaustive_pair_oracle_predictions(
            predictions,
            selection,
            _changed_policy(policy),
        )

    tampered_payload = predictions.model_dump(mode="json", exclude={"prediction_set_id"})
    prediction_payloads = cast(list[dict[str, object]], tampered_payload["predictions"])
    comparison_payloads = cast(
        list[dict[str, object]],
        prediction_payloads[0]["comparisons"],
    )
    score_payload = cast(dict[str, object], comparison_payloads[0]["scores"])
    score_payload["reference_content_token_count"] = (
        cast(int, score_payload["reference_content_token_count"]) + 1
    )
    tampered_payload["prediction_set_id"] = canonical_hash(
        "tag-truth-nd-pair-oracle",
        tampered_payload,
    )
    tampered = NearDuplicatePairOraclePredictionSetV1.model_validate(tampered_payload)

    with pytest.raises(ValueError, match="do not rebuild"):
        verify_exhaustive_pair_oracle_predictions(tampered, selection, policy)


def test_semantics_fingerprint_is_stable_and_does_not_claim_code_identity() -> None:
    selection = _selection(
        (
            _CaseSpec(
                name="semantics_calibration",
                split="calibration",
                direction="unit_file",
                left_text=" ".join(f"semanticsUnit{index}" for index in range(50)),
                right_text=" ".join(f"semanticsFile{index}" for index in range(50)),
            ),
            _CaseSpec(
                name="semantics_holdout",
                split="acceptance_holdout",
                direction="file_file",
                left_text=" ".join(f"semanticsLeft{index} +" for index in range(60)),
                right_text=" ".join(f"semanticsRight{index} -" for index in range(60)),
            ),
        )
    )
    policy = _policy()

    first = build_exhaustive_pair_oracle_predictions(selection, policy)
    second = build_exhaustive_pair_oracle_predictions(selection, policy)
    expected = canonical_hash("tag-truth-nd-pair-oracle-semantics", _SEMANTICS_PAYLOAD)

    assert first == second
    assert first.semantics_fingerprint == expected
    assert first.semantics_fingerprint.startswith("tag-truth-nd-pair-oracle-semantics:sha256:")
    assert "implementation_fingerprint" not in first.model_dump(mode="json")
