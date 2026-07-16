from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.feature_routing_validation import (
    tag_truth_v2_near_duplicate as near_duplicate_core,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    canonical_hash,
    canonical_json,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate import (
    AxisDecision,
    SimilarityScores,
    SimilaritySignal,
    TagTruthV2NearDuplicatePolicy,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate_pair_truth import (
    NearDuplicatePairConsensusV1,
    NearDuplicatePairMember,
    NearDuplicatePairReviewPacketV1,
    NearDuplicatePairReviewReceiptV1,
    NearDuplicatePairSelectionV1,
    PairSplit,
    PairTruthLabel,
    validate_near_duplicate_pair_review_receipt,
    verify_near_duplicate_pair_consensus,
    verify_near_duplicate_pair_review_packet,
)

PAIR_ORACLE_SCHEMA_VERSION = "tag-truth-v2-nd-pair-oracle-v1"
CALIBRATION_GATE_SCHEMA_VERSION = "tag-truth-v2-nd-calibration-gate-v1"
CALIBRATION_REPORT_SCHEMA_VERSION = "tag-truth-v2-nd-calibration-report-v1"
POLICY_APPROVAL_RECEIPT_SCHEMA_VERSION = "tag-truth-v2-nd-policy-approval-receipt-v1"
POLICY_CANDIDATE_FREEZE_SCHEMA_VERSION = "tag-truth-v2-nd-policy-freeze-v1"
HOLDOUT_RELEASE_RECEIPT_SCHEMA_VERSION = "tag-truth-v2-nd-holdout-release-v1"

MachinePairDecision = Literal["duplicate", "gray", "clear", "abstain"]
EvidenceQualificationBlocker = Literal[
    "external_holdout_custodian_identity_not_authenticated",
    "external_pair_reviewer_identity_not_authenticated",
    "external_policy_approver_identity_not_authenticated",
    "pair_source_git_provenance_not_verified",
    "verifier_closure_git_blobs_not_verified",
]

_SHA256 = r"^sha256:[0-9a-f]{64}$"
_GIT_OBJECT_ID = r"^[0-9a-f]{40}$"
_PAIR_ID = r"^tag-truth-nd-pair:sha256:[0-9a-f]{64}$"
_MEMBER_ID = r"^tag-truth-nd-pair-member:sha256:[0-9a-f]{64}$"
_COMPONENT_ID = r"^tag-truth-nd-pair-component:sha256:[0-9a-f]{64}$"
_SELECTION_ID = r"^tag-truth-nd-pair-selection:sha256:[0-9a-f]{64}$"
_PACKET_ID = r"^tag-truth-nd-pair-packet:sha256:[0-9a-f]{64}$"
_RECEIPT_ID = r"^tag-truth-nd-pair-receipt:sha256:[0-9a-f]{64}$"
_CONSENSUS_ID = r"^tag-truth-nd-pair-consensus:sha256:[0-9a-f]{64}$"
_POLICY_FINGERPRINT = r"^tag-truth-near-duplicate-policy:sha256:[0-9a-f]{64}$"
_ORACLE_SEMANTICS_FINGERPRINT = r"^tag-truth-nd-pair-oracle-semantics:sha256:[0-9a-f]{64}$"
_ORACLE_PREDICTION_SET_ID = r"^tag-truth-nd-pair-oracle:sha256:[0-9a-f]{64}$"
_GATE_FINGERPRINT = r"^tag-truth-nd-calibration-gate:sha256:[0-9a-f]{64}$"
_REPORT_ID = r"^tag-truth-nd-calibration-report:sha256:[0-9a-f]{64}$"
_POLICY_FREEZE_ID = r"^tag-truth-nd-policy-freeze:sha256:[0-9a-f]{64}$"
_HOLDOUT_RELEASE_ID = r"^tag-truth-nd-holdout-release:sha256:[0-9a-f]{64}$"
_APPROVAL_RECEIPT_ID = r"^tag-truth-nd-policy-approval-receipt:sha256:[0-9a-f]{64}$"
_IDENTITY_ID = r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"
_SLUG = r"^[a-z][a-z0-9_]*$"
_TIMESTAMP = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"

_MACHINE_DECISIONS: tuple[MachinePairDecision, ...] = (
    "duplicate",
    "gray",
    "clear",
    "abstain",
)
_TRUTH_LABELS: tuple[PairTruthLabel, ...] = (
    "duplicate",
    "independent",
    "ambiguous",
)
_DECISION_PRIORITY: Mapping[MachinePairDecision, int] = {
    "clear": 0,
    "abstain": 1,
    "gray": 2,
    "duplicate": 3,
}
_EVIDENCE_QUALIFICATION_BLOCKERS: tuple[EvidenceQualificationBlocker, ...] = (
    "external_holdout_custodian_identity_not_authenticated",
    "external_pair_reviewer_identity_not_authenticated",
    "external_policy_approver_identity_not_authenticated",
    "pair_source_git_provenance_not_verified",
    "verifier_closure_git_blobs_not_verified",
)
_ORACLE_SEMANTICS_ID = canonical_hash(
    "tag-truth-nd-pair-oracle-semantics",
    {
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
    },
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _sorted_unique(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _single_line(value: str, context: str) -> str:
    if value != value.strip() or not value or any(ord(character) < 32 for character in value):
        raise ValueError(f"{context} must be non-empty, trimmed, and single-line")
    return value


def _utc_timestamp(value: str, context: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(f"{context} must be a valid UTC timestamp") from exc
    return value


def _relative_path(value: str, context: str) -> str:
    if (
        value != value.strip()
        or not value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{context} must be a non-empty trimmed POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{context} must be relative and cannot traverse parents")
    if path.as_posix() != value:
        raise ValueError(f"{context} must be normalized")
    return value


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={identity_field})


def _ppm(value: Fraction | float) -> int:
    return int(round(float(value) * 1_000_000))


def _wilson_lower_ppm(successes: int, total: int) -> int | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1.0 + z * z / total
    centre = proportion + z * z / (2.0 * total)
    margin = z * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total))
    return _ppm(max(0.0, (centre - margin) / denominator))


class PairOracleComparison(_FrozenModel):
    selected_member_id: Annotated[str, Field(pattern=_MEMBER_ID)]
    reference_member_id: Annotated[str, Field(pattern=_MEMBER_ID)]
    selected_axis: Literal["file", "unit"]
    reference_axis: Literal["file", "unit"]
    similarity_decision: AxisDecision
    decision: MachinePairDecision
    signals: tuple[SimilaritySignal, ...]
    scores: SimilarityScores
    tokenization_issues: tuple[str, ...]
    blockers: tuple[Literal["selected_too_short_for_policy", "tokenization_issue"], ...]

    @field_validator("signals", "tokenization_issues", "blockers", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"pair oracle {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_comparison(self) -> PairOracleComparison:
        if self.selected_member_id == self.reference_member_id:
            raise ValueError("pair oracle cannot compare a member with itself")
        if self.signals != tuple(sorted(set(self.signals))):
            raise ValueError("pair oracle signals must be sorted and unique")
        if self.tokenization_issues != tuple(sorted(set(self.tokenization_issues))):
            raise ValueError("pair oracle tokenization issues must be sorted and unique")
        if self.blockers != tuple(sorted(set(self.blockers))):
            raise ValueError("pair oracle blockers must be sorted and unique")
        expected_blockers: tuple[str, ...]
        expected_decision: MachinePairDecision
        if self.similarity_decision == "abstain":
            expected_decision = "abstain"
            expected_blockers = ("tokenization_issue",)
        elif (
            self.similarity_decision == "clear" and self.scores.selected_content_shingle_count < 32
        ):
            expected_decision = "abstain"
            expected_blockers = ("selected_too_short_for_policy",)
        else:
            expected_decision = self.similarity_decision
            expected_blockers = ()
        if self.decision != expected_decision or self.blockers != expected_blockers:
            raise ValueError("pair oracle effective decision differs from canonical semantics")
        if bool(self.tokenization_issues) != (self.similarity_decision == "abstain"):
            raise ValueError("pair oracle tokenization issues differ from similarity abstention")
        return self


class PairOraclePrediction(_FrozenModel):
    pair_id: Annotated[str, Field(pattern=_PAIR_ID)]
    decision: MachinePairDecision
    comparisons: tuple[PairOracleComparison, ...]

    @field_validator("comparisons", mode="before")
    @classmethod
    def parse_comparisons(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "pair oracle comparisons")

    @model_validator(mode="after")
    def validate_prediction(self) -> PairOraclePrediction:
        if not self.comparisons:
            raise ValueError("pair oracle prediction requires comparisons")
        keys = tuple(
            (
                item.selected_member_id,
                item.reference_member_id,
                item.selected_axis,
                item.reference_axis,
            )
            for item in self.comparisons
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("pair oracle comparisons must be sorted and unique")
        expected = max(
            (item.decision for item in self.comparisons),
            key=lambda decision: _DECISION_PRIORITY[decision],
        )
        if self.decision != expected:
            raise ValueError("pair oracle aggregate decision differs from comparisons")
        return self


class _PairOraclePredictionSetPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-nd-pair-oracle-v1"]
    oracle_semantics: Literal["near-duplicate-shadow-v1-canonical-exhaustive"]
    semantics_fingerprint: Annotated[
        str,
        Field(pattern=_ORACLE_SEMANTICS_FINGERPRINT),
    ]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    policy_fingerprint: Annotated[str, Field(pattern=_POLICY_FINGERPRINT)]
    predictions: tuple[PairOraclePrediction, ...]

    @field_validator("predictions", mode="before")
    @classmethod
    def parse_predictions(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "pair oracle predictions")

    @model_validator(mode="after")
    def validate_prediction_set(self) -> _PairOraclePredictionSetPayload:
        if self.semantics_fingerprint != _ORACLE_SEMANTICS_ID:
            raise ValueError("pair oracle semantics fingerprint is not canonical")
        pair_ids = tuple(item.pair_id for item in self.predictions)
        if not pair_ids or pair_ids != tuple(sorted(set(pair_ids))):
            raise ValueError("pair oracle predictions must be sorted and unique")
        return self


class NearDuplicatePairOraclePredictionSetV1(_PairOraclePredictionSetPayload):
    prediction_set_id: Annotated[str, Field(pattern=_ORACLE_PREDICTION_SET_ID)]

    @model_validator(mode="after")
    def validate_prediction_set_id(self) -> NearDuplicatePairOraclePredictionSetV1:
        expected = canonical_hash(
            "tag-truth-nd-pair-oracle",
            _identity_payload(self, "prediction_set_id"),
        )
        if self.prediction_set_id != expected:
            raise ValueError("pair oracle prediction-set ID does not match its contents")
        return self


def _oracle_comparison(
    *,
    pair_id: str,
    selected: NearDuplicatePairMember,
    reference: NearDuplicatePairMember,
    policy: TagTruthV2NearDuplicatePolicy,
) -> PairOracleComparison:
    probe = near_duplicate_core._probe(
        pair_id,
        selected.axis,
        selected.text,
        policy,
    )
    similarity_decision, signals, scores, issues = near_duplicate_core._similarity(
        probe,
        reference.text,
        policy,
    )
    blockers: tuple[
        Literal["selected_too_short_for_policy", "tokenization_issue"],
        ...,
    ]
    effective: MachinePairDecision
    if similarity_decision == "abstain":
        effective = "abstain"
        blockers = ("tokenization_issue",)
    elif (
        similarity_decision == "clear"
        and len(probe.content_shingles) < policy.minimum_informative_content_shingles
    ):
        effective = "abstain"
        blockers = ("selected_too_short_for_policy",)
    else:
        effective = similarity_decision
        blockers = ()
    return PairOracleComparison(
        selected_member_id=selected.member_id,
        reference_member_id=reference.member_id,
        selected_axis=selected.axis,
        reference_axis=reference.axis,
        similarity_decision=similarity_decision,
        decision=effective,
        signals=signals,
        scores=scores,
        tokenization_issues=issues,
        blockers=blockers,
    )


def build_exhaustive_pair_oracle_predictions(
    selection: NearDuplicatePairSelectionV1,
    policy: TagTruthV2NearDuplicatePolicy,
) -> NearDuplicatePairOraclePredictionSetV1:
    selection = NearDuplicatePairSelectionV1.model_validate_json(
        canonical_json(selection.model_dump(mode="json"))
    )
    policy = TagTruthV2NearDuplicatePolicy.model_validate_json(
        canonical_json(policy.model_dump(mode="json"))
    )
    members_by_id = {member.member_id: member for member in selection.members}
    predictions: list[PairOraclePrediction] = []
    for case in selection.cases:
        left = members_by_id[case.member_ids[0]]
        right = members_by_id[case.member_ids[1]]
        comparisons = [
            _oracle_comparison(
                pair_id=case.pair_id,
                selected=left,
                reference=right,
                policy=policy,
            )
        ]
        if case.direction in {"file_file", "unit_unit"}:
            comparisons.append(
                _oracle_comparison(
                    pair_id=case.pair_id,
                    selected=right,
                    reference=left,
                    policy=policy,
                )
            )
        ordered = tuple(
            sorted(
                comparisons,
                key=lambda item: (
                    item.selected_member_id,
                    item.reference_member_id,
                    item.selected_axis,
                    item.reference_axis,
                ),
            )
        )
        decision = max(
            (item.decision for item in ordered),
            key=lambda value: _DECISION_PRIORITY[value],
        )
        predictions.append(
            PairOraclePrediction(
                pair_id=case.pair_id,
                decision=decision,
                comparisons=ordered,
            )
        )
    payload: dict[str, object] = {
        "schema_version": PAIR_ORACLE_SCHEMA_VERSION,
        "oracle_semantics": "near-duplicate-shadow-v1-canonical-exhaustive",
        "semantics_fingerprint": _ORACLE_SEMANTICS_ID,
        "selection_id": selection.selection_id,
        "policy_fingerprint": policy.policy_fingerprint,
        "predictions": [
            item.model_dump(mode="json")
            for item in sorted(predictions, key=lambda item: item.pair_id)
        ],
    }
    payload["prediction_set_id"] = canonical_hash("tag-truth-nd-pair-oracle", payload)
    return NearDuplicatePairOraclePredictionSetV1.model_validate_json(canonical_json(payload))


def verify_exhaustive_pair_oracle_predictions(
    predictions: NearDuplicatePairOraclePredictionSetV1,
    selection: NearDuplicatePairSelectionV1,
    policy: TagTruthV2NearDuplicatePolicy,
) -> None:
    canonical_predictions = NearDuplicatePairOraclePredictionSetV1.model_validate_json(
        canonical_json(predictions.model_dump(mode="json"))
    )
    rebuilt = build_exhaustive_pair_oracle_predictions(selection, policy)
    if canonical_predictions != rebuilt:
        raise ValueError("pair oracle predictions do not rebuild from selection and policy")


class VerifierClosureBlob(_FrozenModel):
    path: str
    git_blob_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value, "calibration verifier closure path")


class _PolicyCandidateFreezePayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-nd-policy-freeze-v1"]
    policy_candidate_fingerprint: Annotated[str, Field(pattern=_POLICY_FINGERPRINT)]
    oracle_semantics_fingerprint: Annotated[
        str,
        Field(pattern=_ORACLE_SEMANTICS_FINGERPRINT),
    ]
    gate_fingerprint: Annotated[str, Field(pattern=_GATE_FINGERPRINT)]
    candidate_commit: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    verifier_closure: tuple[VerifierClosureBlob, ...]
    thresholds_finalized: Literal[True]
    acceptance_holdout_labels_seen: Literal[False]
    frozen_at: Annotated[str, Field(pattern=_TIMESTAMP)]

    @field_validator("verifier_closure", mode="before")
    @classmethod
    def parse_closure(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "policy candidate verifier closure")

    @field_validator("frozen_at")
    @classmethod
    def validate_frozen_at(cls, value: str) -> str:
        return _utc_timestamp(value, "policy candidate frozen_at")

    @model_validator(mode="after")
    def validate_freeze_payload(self) -> _PolicyCandidateFreezePayload:
        if not self.verifier_closure:
            raise ValueError("policy candidate freeze requires verifier closure blobs")
        keys = tuple((item.path, item.git_blob_id) for item in self.verifier_closure)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("policy candidate verifier closure must be sorted and unique")
        paths = tuple(item.path for item in self.verifier_closure)
        if len(paths) != len(set(paths)):
            raise ValueError("policy candidate verifier closure paths must be unique")
        return self


class NearDuplicatePolicyCandidateFreezeV1(_PolicyCandidateFreezePayload):
    freeze_id: Annotated[str, Field(pattern=_POLICY_FREEZE_ID)]

    @model_validator(mode="after")
    def validate_freeze_id(self) -> NearDuplicatePolicyCandidateFreezeV1:
        expected = canonical_hash(
            "tag-truth-nd-policy-freeze",
            _identity_payload(self, "freeze_id"),
        )
        if self.freeze_id != expected:
            raise ValueError("policy candidate freeze ID does not match its contents")
        return self


def seal_near_duplicate_policy_candidate_freeze_payload(
    payload: Mapping[str, object],
) -> NearDuplicatePolicyCandidateFreezeV1:
    if "freeze_id" in payload:
        raise ValueError("unsealed policy candidate freeze cannot contain freeze_id")
    canonical = _PolicyCandidateFreezePayload.model_validate_json(canonical_json(dict(payload)))
    result = canonical.model_dump(mode="json")
    result["freeze_id"] = canonical_hash("tag-truth-nd-policy-freeze", result)
    return NearDuplicatePolicyCandidateFreezeV1.model_validate_json(canonical_json(result))


def validate_near_duplicate_policy_candidate_freeze(
    freeze: NearDuplicatePolicyCandidateFreezeV1,
    *,
    policy: TagTruthV2NearDuplicatePolicy,
    predictions: NearDuplicatePairOraclePredictionSetV1,
    gate: NearDuplicateCalibrationGateV1,
) -> None:
    freeze = NearDuplicatePolicyCandidateFreezeV1.model_validate_json(
        canonical_json(freeze.model_dump(mode="json"))
    )
    policy = TagTruthV2NearDuplicatePolicy.model_validate_json(
        canonical_json(policy.model_dump(mode="json"))
    )
    predictions = NearDuplicatePairOraclePredictionSetV1.model_validate_json(
        canonical_json(predictions.model_dump(mode="json"))
    )
    gate = NearDuplicateCalibrationGateV1.model_validate_json(
        canonical_json(gate.model_dump(mode="json"))
    )
    expected_binding = (
        policy.policy_fingerprint,
        predictions.semantics_fingerprint,
        gate.gate_fingerprint,
    )
    actual_binding = (
        freeze.policy_candidate_fingerprint,
        freeze.oracle_semantics_fingerprint,
        freeze.gate_fingerprint,
    )
    if actual_binding != expected_binding:
        raise ValueError("policy candidate freeze differs from policy, Oracle, or gate")


class HoldoutCustodianIdentity(_FrozenModel):
    custodian_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    custodian_kind: Literal["human"]
    custodian_role: Literal["near_duplicate_holdout_custodian"]
    affiliation: Annotated[str, Field(min_length=1)]
    candidate_policy_author: Literal[False]
    pair_reviewer: Literal[False]
    policy_approver: Literal[False]

    @field_validator("affiliation")
    @classmethod
    def validate_affiliation(cls, value: str) -> str:
        return _single_line(value, "holdout custodian affiliation")


class _HoldoutReleaseReceiptPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-nd-holdout-release-v1"]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    freeze_id: Annotated[str, Field(pattern=_POLICY_FREEZE_ID)]
    policy_candidate_fingerprint: Annotated[str, Field(pattern=_POLICY_FINGERPRINT)]
    oracle_semantics_fingerprint: Annotated[
        str,
        Field(pattern=_ORACLE_SEMANTICS_FINGERPRINT),
    ]
    gate_fingerprint: Annotated[str, Field(pattern=_GATE_FINGERPRINT)]
    custodian: HoldoutCustodianIdentity
    labels_withheld_until_policy_freeze: Literal[True]
    calibration_actor_did_not_receive_holdout_labels_before_freeze: Literal[True]
    released_at: Annotated[str, Field(pattern=_TIMESTAMP)]
    recorded_at: Annotated[str, Field(pattern=_TIMESTAMP)]

    @field_validator("released_at", "recorded_at")
    @classmethod
    def validate_timestamps(cls, value: str, info: object) -> str:
        return _utc_timestamp(value, f"holdout release {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_release_payload(self) -> _HoldoutReleaseReceiptPayload:
        if self.released_at > self.recorded_at:
            raise ValueError("holdout release cannot be recorded before release")
        return self


class NearDuplicateHoldoutReleaseReceiptV1(_HoldoutReleaseReceiptPayload):
    release_receipt_id: Annotated[str, Field(pattern=_HOLDOUT_RELEASE_ID)]

    @model_validator(mode="after")
    def validate_release_receipt_id(self) -> NearDuplicateHoldoutReleaseReceiptV1:
        expected = canonical_hash(
            "tag-truth-nd-holdout-release",
            _identity_payload(self, "release_receipt_id"),
        )
        if self.release_receipt_id != expected:
            raise ValueError("holdout release receipt ID does not match its contents")
        return self


def validate_near_duplicate_holdout_release_receipt(
    receipt: NearDuplicateHoldoutReleaseReceiptV1,
    *,
    selection: NearDuplicatePairSelectionV1,
    freeze: NearDuplicatePolicyCandidateFreezeV1,
) -> None:
    receipt = NearDuplicateHoldoutReleaseReceiptV1.model_validate_json(
        canonical_json(receipt.model_dump(mode="json"))
    )
    selection = NearDuplicatePairSelectionV1.model_validate_json(
        canonical_json(selection.model_dump(mode="json"))
    )
    freeze = NearDuplicatePolicyCandidateFreezeV1.model_validate_json(
        canonical_json(freeze.model_dump(mode="json"))
    )
    expected_binding = (
        selection.selection_id,
        freeze.freeze_id,
        freeze.policy_candidate_fingerprint,
        freeze.oracle_semantics_fingerprint,
        freeze.gate_fingerprint,
    )
    actual_binding = (
        receipt.selection_id,
        receipt.freeze_id,
        receipt.policy_candidate_fingerprint,
        receipt.oracle_semantics_fingerprint,
        receipt.gate_fingerprint,
    )
    if actual_binding != expected_binding:
        raise ValueError("holdout release receipt differs from selection or policy freeze")
    if freeze.frozen_at >= receipt.released_at:
        raise ValueError("holdout labels must be released after policy candidate freeze")


def seal_near_duplicate_holdout_release_receipt_payload(
    payload: Mapping[str, object],
    *,
    selection: NearDuplicatePairSelectionV1,
    freeze: NearDuplicatePolicyCandidateFreezeV1,
) -> NearDuplicateHoldoutReleaseReceiptV1:
    if "release_receipt_id" in payload:
        raise ValueError("unsealed holdout release receipt cannot contain its ID")
    canonical = _HoldoutReleaseReceiptPayload.model_validate_json(canonical_json(dict(payload)))
    result = canonical.model_dump(mode="json")
    result["release_receipt_id"] = canonical_hash(
        "tag-truth-nd-holdout-release",
        result,
    )
    receipt = NearDuplicateHoldoutReleaseReceiptV1.model_validate_json(canonical_json(result))
    validate_near_duplicate_holdout_release_receipt(
        receipt,
        selection=selection,
        freeze=freeze,
    )
    return receipt


class _CalibrationGatePayload(_FrozenModel):
    gate_version: Literal["near-duplicate-calibration-gate-v1"]
    evaluation_split: Literal["acceptance_holdout"]
    confidence_method: Literal["wilson-score-95-ppm-v1"]
    minimum_duplicate_cases: Annotated[int, Field(ge=80)]
    minimum_independent_cases: Annotated[int, Field(ge=80)]
    minimum_duplicate_components: Annotated[int, Field(ge=80)]
    minimum_independent_components: Annotated[int, Field(ge=80)]
    require_one_binary_metric_pair_per_component: Literal[True]
    minimum_raw_agreement_ppm: Annotated[int, Field(ge=900_000, le=1_000_000)]
    minimum_cohen_kappa_ppm: Annotated[int, Field(ge=800_000, le=1_000_000)]
    maximum_unresolved_rate_ppm: Annotated[int, Field(ge=0, le=50_000)]
    maximum_false_clear_duplicate_count: Literal[0]
    maximum_hard_reject_independent_count: Literal[0]
    maximum_binary_abstain_count: Literal[0]
    maximum_hard_reject_ambiguous_count: Literal[0]
    maximum_auto_clear_ambiguous_count: Literal[0]
    minimum_strict_duplicate_precision_ppm: Literal[1_000_000]
    minimum_strict_duplicate_precision_wilson_ppm: Annotated[
        int,
        Field(ge=950_000, le=1_000_000),
    ]
    minimum_strict_duplicate_recall_ppm: Annotated[
        int,
        Field(ge=975_000, le=1_000_000),
    ]
    minimum_strict_duplicate_recall_wilson_ppm: Annotated[
        int,
        Field(ge=900_000, le=1_000_000),
    ]
    minimum_independent_clear_precision_ppm: Literal[1_000_000]
    minimum_independent_clear_precision_wilson_ppm: Annotated[
        int,
        Field(ge=950_000, le=1_000_000),
    ]
    minimum_independent_clear_recall_ppm: Annotated[
        int,
        Field(ge=975_000, le=1_000_000),
    ]
    minimum_independent_clear_recall_wilson_ppm: Annotated[
        int,
        Field(ge=900_000, le=1_000_000),
    ]
    minimum_duplicate_block_recall_ppm: Literal[1_000_000]
    minimum_duplicate_block_recall_wilson_ppm: Annotated[
        int,
        Field(ge=950_000, le=1_000_000),
    ]
    ambiguous_handling: Literal["guard_only_excluded_from_binary_metrics"]
    policy_approval_status: Literal["not_approved"]


class NearDuplicateCalibrationGateV1(_CalibrationGatePayload):
    gate_fingerprint: Annotated[str, Field(pattern=_GATE_FINGERPRINT)]

    @model_validator(mode="after")
    def validate_gate_fingerprint(self) -> NearDuplicateCalibrationGateV1:
        expected = canonical_hash(
            "tag-truth-nd-calibration-gate",
            _identity_payload(self, "gate_fingerprint"),
        )
        if self.gate_fingerprint != expected:
            raise ValueError("calibration gate fingerprint does not match its contents")
        return self


def calibration_gate_payload_with_fingerprint(
    payload: Mapping[str, object],
) -> dict[str, object]:
    if "gate_fingerprint" in payload:
        raise ValueError("unsealed calibration gate cannot contain gate_fingerprint")
    canonical = _CalibrationGatePayload.model_validate_json(canonical_json(dict(payload)))
    result = canonical.model_dump(mode="json")
    result["gate_fingerprint"] = canonical_hash(
        "tag-truth-nd-calibration-gate",
        result,
    )
    return result


def default_near_duplicate_calibration_gate() -> NearDuplicateCalibrationGateV1:
    return NearDuplicateCalibrationGateV1.model_validate(
        calibration_gate_payload_with_fingerprint(
            {
                "gate_version": "near-duplicate-calibration-gate-v1",
                "evaluation_split": "acceptance_holdout",
                "confidence_method": "wilson-score-95-ppm-v1",
                "minimum_duplicate_cases": 80,
                "minimum_independent_cases": 80,
                "minimum_duplicate_components": 80,
                "minimum_independent_components": 80,
                "require_one_binary_metric_pair_per_component": True,
                "minimum_raw_agreement_ppm": 900_000,
                "minimum_cohen_kappa_ppm": 800_000,
                "maximum_unresolved_rate_ppm": 50_000,
                "maximum_false_clear_duplicate_count": 0,
                "maximum_hard_reject_independent_count": 0,
                "maximum_binary_abstain_count": 0,
                "maximum_hard_reject_ambiguous_count": 0,
                "maximum_auto_clear_ambiguous_count": 0,
                "minimum_strict_duplicate_precision_ppm": 1_000_000,
                "minimum_strict_duplicate_precision_wilson_ppm": 950_000,
                "minimum_strict_duplicate_recall_ppm": 975_000,
                "minimum_strict_duplicate_recall_wilson_ppm": 900_000,
                "minimum_independent_clear_precision_ppm": 1_000_000,
                "minimum_independent_clear_precision_wilson_ppm": 950_000,
                "minimum_independent_clear_recall_ppm": 975_000,
                "minimum_independent_clear_recall_wilson_ppm": 900_000,
                "minimum_duplicate_block_recall_ppm": 1_000_000,
                "minimum_duplicate_block_recall_wilson_ppm": 950_000,
                "ambiguous_handling": "guard_only_excluded_from_binary_metrics",
                "policy_approval_status": "not_approved",
            }
        )
    )


class RateMeasurement(_FrozenModel):
    numerator: Annotated[int, Field(ge=0)]
    denominator: Annotated[int, Field(ge=0)]
    point_ppm: Annotated[int, Field(ge=0, le=1_000_000)] | None
    wilson_lower_ppm: Annotated[int, Field(ge=0, le=1_000_000)] | None
    confidence_method: Literal["wilson-score-95-ppm-v1"]

    @model_validator(mode="after")
    def validate_measurement(self) -> RateMeasurement:
        if self.numerator > self.denominator:
            raise ValueError("rate numerator cannot exceed denominator")
        expected_point = (
            _ppm(Fraction(self.numerator, self.denominator)) if self.denominator else None
        )
        expected_wilson = _wilson_lower_ppm(self.numerator, self.denominator)
        if self.point_ppm != expected_point or self.wilson_lower_ppm != expected_wilson:
            raise ValueError("rate measurement does not match numerator and denominator")
        return self


def _rate(numerator: int, denominator: int) -> RateMeasurement:
    return RateMeasurement(
        numerator=numerator,
        denominator=denominator,
        point_ppm=(_ppm(Fraction(numerator, denominator)) if denominator else None),
        wilson_lower_ppm=_wilson_lower_ppm(numerator, denominator),
        confidence_method="wilson-score-95-ppm-v1",
    )


class PairConfusionCell(_FrozenModel):
    truth_label: PairTruthLabel
    machine_decision: MachinePairDecision
    count: Annotated[int, Field(ge=0)]


class CalibrationCaseResult(_FrozenModel):
    pair_id: Annotated[str, Field(pattern=_PAIR_ID)]
    split: PairSplit
    component_id: Annotated[str, Field(pattern=_COMPONENT_ID)]
    consensus_status: Literal[
        "agreed_resolved",
        "agreed_ambiguous",
        "unresolved",
    ]
    truth_label: PairTruthLabel | None
    machine_decision: MachinePairDecision

    @model_validator(mode="after")
    def validate_case_result(self) -> CalibrationCaseResult:
        if self.consensus_status == "unresolved":
            if self.truth_label is not None:
                raise ValueError("unresolved calibration case cannot publish Truth")
        elif self.truth_label is None:
            raise ValueError("resolved calibration case requires Truth")
        return self


class CalibrationSplitMetrics(_FrozenModel):
    split: PairSplit
    case_count: Annotated[int, Field(ge=0)]
    component_count: Annotated[int, Field(ge=0)]
    resolved_case_count: Annotated[int, Field(ge=0)]
    unresolved_case_count: Annotated[int, Field(ge=0)]
    truth_duplicate_count: Annotated[int, Field(ge=0)]
    truth_independent_count: Annotated[int, Field(ge=0)]
    truth_ambiguous_count: Annotated[int, Field(ge=0)]
    duplicate_component_count: Annotated[int, Field(ge=0)]
    independent_component_count: Annotated[int, Field(ge=0)]
    ambiguous_component_count: Annotated[int, Field(ge=0)]
    outcome_duplicate_count: Annotated[int, Field(ge=0)]
    outcome_gray_count: Annotated[int, Field(ge=0)]
    outcome_clear_count: Annotated[int, Field(ge=0)]
    outcome_abstain_count: Annotated[int, Field(ge=0)]
    confusion: tuple[PairConfusionCell, ...]
    strict_duplicate_precision: RateMeasurement
    strict_duplicate_recall: RateMeasurement
    independent_clear_precision: RateMeasurement
    independent_clear_recall: RateMeasurement
    duplicate_block_recall: RateMeasurement
    false_clear_duplicate_count: Annotated[int, Field(ge=0)]
    hard_reject_independent_count: Annotated[int, Field(ge=0)]
    binary_abstain_count: Annotated[int, Field(ge=0)]
    hard_reject_ambiguous_count: Annotated[int, Field(ge=0)]
    auto_clear_ambiguous_count: Annotated[int, Field(ge=0)]

    @field_validator("confusion", mode="before")
    @classmethod
    def parse_confusion(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "calibration confusion matrix")

    @model_validator(mode="after")
    def validate_metrics(self) -> CalibrationSplitMetrics:
        expected_keys = tuple(
            (truth, decision) for truth in _TRUTH_LABELS for decision in _MACHINE_DECISIONS
        )
        keys = tuple((cell.truth_label, cell.machine_decision) for cell in self.confusion)
        if keys != expected_keys:
            raise ValueError("calibration confusion matrix must contain canonical 3x4 cells")
        counts = {(cell.truth_label, cell.machine_decision): cell.count for cell in self.confusion}
        truth_counts = {
            truth: sum(counts[(truth, decision)] for decision in _MACHINE_DECISIONS)
            for truth in _TRUTH_LABELS
        }
        outcome_counts = {
            decision: sum(counts[(truth, decision)] for truth in _TRUTH_LABELS)
            for decision in _MACHINE_DECISIONS
        }
        if self.resolved_case_count != sum(truth_counts.values()):
            raise ValueError("resolved calibration count differs from confusion matrix")
        if self.case_count != self.resolved_case_count + self.unresolved_case_count:
            raise ValueError("calibration case count differs from resolved/unresolved counts")
        if self.component_count > self.case_count:
            raise ValueError("calibration component count cannot exceed case count")
        expected_truth = (
            truth_counts["duplicate"],
            truth_counts["independent"],
            truth_counts["ambiguous"],
        )
        if expected_truth != (
            self.truth_duplicate_count,
            self.truth_independent_count,
            self.truth_ambiguous_count,
        ):
            raise ValueError("calibration Truth counts differ from confusion matrix")
        if (
            self.duplicate_component_count > self.truth_duplicate_count
            or self.independent_component_count > self.truth_independent_count
            or self.ambiguous_component_count > self.truth_ambiguous_count
        ):
            raise ValueError("calibration label component counts cannot exceed Truth counts")
        expected_outcomes = tuple(outcome_counts[decision] for decision in _MACHINE_DECISIONS)
        if expected_outcomes != (
            self.outcome_duplicate_count,
            self.outcome_gray_count,
            self.outcome_clear_count,
            self.outcome_abstain_count,
        ):
            raise ValueError("calibration machine counts differ from confusion matrix")
        duplicate_true_positive = counts[("duplicate", "duplicate")]
        duplicate_predicted = (
            counts[("duplicate", "duplicate")] + counts[("independent", "duplicate")]
        )
        independent_clear_true = counts[("independent", "clear")]
        independent_clear_predicted = (
            counts[("duplicate", "clear")] + counts[("independent", "clear")]
        )
        expected_rates = (
            _rate(duplicate_true_positive, duplicate_predicted),
            _rate(duplicate_true_positive, truth_counts["duplicate"]),
            _rate(independent_clear_true, independent_clear_predicted),
            _rate(independent_clear_true, truth_counts["independent"]),
            _rate(
                counts[("duplicate", "duplicate")] + counts[("duplicate", "gray")],
                truth_counts["duplicate"],
            ),
        )
        if expected_rates != (
            self.strict_duplicate_precision,
            self.strict_duplicate_recall,
            self.independent_clear_precision,
            self.independent_clear_recall,
            self.duplicate_block_recall,
        ):
            raise ValueError("calibration rates differ from confusion matrix")
        expected_guard_counts = (
            counts[("duplicate", "clear")],
            counts[("independent", "duplicate")],
            counts[("duplicate", "abstain")] + counts[("independent", "abstain")],
            counts[("ambiguous", "duplicate")],
            counts[("ambiguous", "clear")],
        )
        if expected_guard_counts != (
            self.false_clear_duplicate_count,
            self.hard_reject_independent_count,
            self.binary_abstain_count,
            self.hard_reject_ambiguous_count,
            self.auto_clear_ambiguous_count,
        ):
            raise ValueError("calibration guard counts differ from confusion matrix")
        return self


def _split_metrics(
    split: PairSplit,
    cases: Sequence[CalibrationCaseResult],
) -> CalibrationSplitMetrics:
    selected = tuple(case for case in cases if case.split == split)
    counts = Counter(
        (case.truth_label, case.machine_decision)
        for case in selected
        if case.truth_label is not None
    )
    confusion = tuple(
        PairConfusionCell(
            truth_label=truth,
            machine_decision=decision,
            count=counts[(truth, decision)],
        )
        for truth in _TRUTH_LABELS
        for decision in _MACHINE_DECISIONS
    )
    lookup = {(cell.truth_label, cell.machine_decision): cell.count for cell in confusion}
    duplicate_count = sum(lookup[("duplicate", decision)] for decision in _MACHINE_DECISIONS)
    independent_count = sum(lookup[("independent", decision)] for decision in _MACHINE_DECISIONS)
    ambiguous_count = sum(lookup[("ambiguous", decision)] for decision in _MACHINE_DECISIONS)
    component_ids = {case.component_id for case in selected}
    duplicate_component_ids = {
        case.component_id for case in selected if case.truth_label == "duplicate"
    }
    independent_component_ids = {
        case.component_id for case in selected if case.truth_label == "independent"
    }
    ambiguous_component_ids = {
        case.component_id for case in selected if case.truth_label == "ambiguous"
    }
    duplicate_true_positive = lookup[("duplicate", "duplicate")]
    duplicate_predicted = lookup[("duplicate", "duplicate")] + lookup[("independent", "duplicate")]
    independent_clear_true = lookup[("independent", "clear")]
    independent_clear_predicted = lookup[("duplicate", "clear")] + lookup[("independent", "clear")]
    return CalibrationSplitMetrics(
        split=split,
        case_count=len(selected),
        component_count=len(component_ids),
        resolved_case_count=duplicate_count + independent_count + ambiguous_count,
        unresolved_case_count=sum(case.truth_label is None for case in selected),
        truth_duplicate_count=duplicate_count,
        truth_independent_count=independent_count,
        truth_ambiguous_count=ambiguous_count,
        duplicate_component_count=len(duplicate_component_ids),
        independent_component_count=len(independent_component_ids),
        ambiguous_component_count=len(ambiguous_component_ids),
        outcome_duplicate_count=sum(lookup[(truth, "duplicate")] for truth in _TRUTH_LABELS),
        outcome_gray_count=sum(lookup[(truth, "gray")] for truth in _TRUTH_LABELS),
        outcome_clear_count=sum(lookup[(truth, "clear")] for truth in _TRUTH_LABELS),
        outcome_abstain_count=sum(lookup[(truth, "abstain")] for truth in _TRUTH_LABELS),
        confusion=confusion,
        strict_duplicate_precision=_rate(
            duplicate_true_positive,
            duplicate_predicted,
        ),
        strict_duplicate_recall=_rate(duplicate_true_positive, duplicate_count),
        independent_clear_precision=_rate(
            independent_clear_true,
            independent_clear_predicted,
        ),
        independent_clear_recall=_rate(
            independent_clear_true,
            independent_count,
        ),
        duplicate_block_recall=_rate(
            lookup[("duplicate", "duplicate")] + lookup[("duplicate", "gray")],
            duplicate_count,
        ),
        false_clear_duplicate_count=lookup[("duplicate", "clear")],
        hard_reject_independent_count=lookup[("independent", "duplicate")],
        binary_abstain_count=(
            lookup[("duplicate", "abstain")] + lookup[("independent", "abstain")]
        ),
        hard_reject_ambiguous_count=lookup[("ambiguous", "duplicate")],
        auto_clear_ambiguous_count=lookup[("ambiguous", "clear")],
    )


class ReviewQualityMetrics(_FrozenModel):
    split: PairSplit
    case_count: Annotated[int, Field(ge=0)]
    agreement_count: Annotated[int, Field(ge=0)]
    raw_agreement_ppm: Annotated[int, Field(ge=0, le=1_000_000)] | None
    cohen_kappa_ppm: Annotated[int, Field(ge=-1_000_000, le=1_000_000)] | None
    unresolved_count: Annotated[int, Field(ge=0)]
    unresolved_rate_ppm: Annotated[int, Field(ge=0, le=1_000_000)] | None

    @model_validator(mode="after")
    def validate_review_metrics(self) -> ReviewQualityMetrics:
        if self.agreement_count > self.case_count or self.unresolved_count > self.case_count:
            raise ValueError("review quality counts cannot exceed case count")
        expected_agreement = (
            _ppm(Fraction(self.agreement_count, self.case_count)) if self.case_count else None
        )
        expected_unresolved = (
            _ppm(Fraction(self.unresolved_count, self.case_count)) if self.case_count else None
        )
        if (
            self.raw_agreement_ppm != expected_agreement
            or self.unresolved_rate_ppm != expected_unresolved
        ):
            raise ValueError("review quality rates differ from counts")
        return self


def _review_quality(
    split: PairSplit,
    case_ids: set[str],
    consensus: NearDuplicatePairConsensusV1,
) -> ReviewQualityMetrics:
    cases = tuple(case for case in consensus.cases if case.pair_id in case_ids)
    agreement = sum(case.votes[0].label == case.votes[1].label for case in cases)
    unresolved = sum(case.consensus_status == "unresolved" for case in cases)
    labels = _TRUTH_LABELS
    left_counts = Counter(case.votes[0].label for case in cases)
    right_counts = Counter(case.votes[1].label for case in cases)
    kappa_ppm: int | None = None
    if cases:
        observed = Fraction(agreement, len(cases))
        expected = sum(
            Fraction(left_counts[label] * right_counts[label], len(cases) ** 2) for label in labels
        )
        if expected != 1:
            kappa_ppm = _ppm((observed - expected) / (1 - expected))
    return ReviewQualityMetrics(
        split=split,
        case_count=len(cases),
        agreement_count=agreement,
        raw_agreement_ppm=(_ppm(Fraction(agreement, len(cases))) if cases else None),
        cohen_kappa_ppm=kappa_ppm,
        unresolved_count=unresolved,
        unresolved_rate_ppm=(_ppm(Fraction(unresolved, len(cases))) if cases else None),
    )


CalibrationBlocker = Literal[
    "acceptance_holdout_binary_abstain",
    "acceptance_holdout_duplicate_case_count_below_gate",
    "acceptance_holdout_duplicate_component_count_below_gate",
    "acceptance_holdout_false_clear_duplicate",
    "acceptance_holdout_hard_reject_ambiguous",
    "acceptance_holdout_hard_reject_independent",
    "acceptance_holdout_independent_case_count_below_gate",
    "acceptance_holdout_independent_component_count_below_gate",
    "acceptance_holdout_metric_pairs_share_component",
    "acceptance_holdout_auto_clear_ambiguous",
    "cohen_kappa_below_gate",
    "cohen_kappa_not_computable",
    "duplicate_block_recall_below_gate",
    "duplicate_block_recall_wilson_below_gate",
    "independent_clear_precision_below_gate",
    "independent_clear_precision_wilson_below_gate",
    "independent_clear_recall_below_gate",
    "independent_clear_recall_wilson_below_gate",
    "raw_reviewer_agreement_below_gate",
    "strict_duplicate_precision_below_gate",
    "strict_duplicate_precision_wilson_below_gate",
    "strict_duplicate_recall_below_gate",
    "strict_duplicate_recall_wilson_below_gate",
    "unresolved_review_rate_above_gate",
]


class _CalibrationReportPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-nd-calibration-report-v1"]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    receipt_ids: tuple[Annotated[str, Field(pattern=_RECEIPT_ID)], ...]
    reviewer_ids: tuple[Annotated[str, Field(pattern=_IDENTITY_ID)], ...]
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID)]
    policy_candidate_fingerprint: Annotated[str, Field(pattern=_POLICY_FINGERPRINT)]
    oracle_prediction_set_id: Annotated[
        str,
        Field(pattern=_ORACLE_PREDICTION_SET_ID),
    ]
    oracle_semantics_fingerprint: Annotated[
        str,
        Field(pattern=_ORACLE_SEMANTICS_FINGERPRINT),
    ]
    gate_fingerprint: Annotated[str, Field(pattern=_GATE_FINGERPRINT)]
    policy_candidate_freeze_id: Annotated[str, Field(pattern=_POLICY_FREEZE_ID)]
    holdout_release_receipt_id: Annotated[str, Field(pattern=_HOLDOUT_RELEASE_ID)]
    holdout_custodian_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    cases: tuple[CalibrationCaseResult, ...]
    calibration_metrics: CalibrationSplitMetrics
    acceptance_holdout_metrics: CalibrationSplitMetrics
    calibration_review_quality: ReviewQualityMetrics
    acceptance_holdout_review_quality: ReviewQualityMetrics
    calibration_gate_status: Literal["passed", "failed"]
    policy_approval_readiness: Literal[
        "eligible_for_human_review",
        "not_eligible",
    ]
    policy_approval_status: Literal["not_approved"]
    qualification_blockers: tuple[CalibrationBlocker, ...]
    evidence_qualification_status: Literal["not_qualified"]
    evidence_qualification_blockers: tuple[EvidenceQualificationBlocker, ...]

    @field_validator(
        "receipt_ids",
        "reviewer_ids",
        "cases",
        "qualification_blockers",
        "evidence_qualification_blockers",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"calibration report {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_report_payload(self) -> _CalibrationReportPayload:
        if len(self.receipt_ids) != 2 or self.receipt_ids != tuple(sorted(set(self.receipt_ids))):
            raise ValueError("calibration report requires two sorted unique receipts")
        if len(self.reviewer_ids) != 2 or self.reviewer_ids != tuple(
            sorted(set(self.reviewer_ids))
        ):
            raise ValueError("calibration report requires two sorted unique reviewers")
        if self.holdout_custodian_id in self.reviewer_ids:
            raise ValueError("holdout custodian cannot be a Pair Truth reviewer")
        pair_ids = tuple(case.pair_id for case in self.cases)
        if not pair_ids or pair_ids != tuple(sorted(set(pair_ids))):
            raise ValueError("calibration report cases must be sorted and unique")
        if self.calibration_metrics.split != "calibration":
            raise ValueError("calibration metrics use the wrong split")
        if self.acceptance_holdout_metrics.split != "acceptance_holdout":
            raise ValueError("acceptance holdout metrics use the wrong split")
        if self.calibration_review_quality.split != "calibration":
            raise ValueError("calibration review metrics use the wrong split")
        if self.acceptance_holdout_review_quality.split != "acceptance_holdout":
            raise ValueError("holdout review metrics use the wrong split")
        if self.calibration_metrics != _split_metrics("calibration", self.cases):
            raise ValueError("calibration metrics do not rebuild from report cases")
        if self.acceptance_holdout_metrics != _split_metrics(
            "acceptance_holdout",
            self.cases,
        ):
            raise ValueError("holdout metrics do not rebuild from report cases")
        if self.qualification_blockers != tuple(sorted(set(self.qualification_blockers))):
            raise ValueError("calibration blockers must be sorted and unique")
        if self.evidence_qualification_blockers != _EVIDENCE_QUALIFICATION_BLOCKERS:
            raise ValueError("calibration report must preserve evidence qualification blockers")
        expected_status = "passed" if not self.qualification_blockers else "failed"
        expected_readiness = (
            "eligible_for_human_review" if expected_status == "passed" else "not_eligible"
        )
        if (
            self.calibration_gate_status != expected_status
            or self.policy_approval_readiness != expected_readiness
        ):
            raise ValueError("calibration status differs from its blockers")
        return self


class NearDuplicateCalibrationReportV1(_CalibrationReportPayload):
    report_id: Annotated[str, Field(pattern=_REPORT_ID)]

    @model_validator(mode="after")
    def validate_report_id(self) -> NearDuplicateCalibrationReportV1:
        expected = canonical_hash(
            "tag-truth-nd-calibration-report",
            _identity_payload(self, "report_id"),
        )
        if self.report_id != expected:
            raise ValueError("calibration report ID does not match its contents")
        return self


def _below(measurement: RateMeasurement, field: str, gate: int) -> bool:
    value = getattr(measurement, field)
    return value is None or value < gate


def build_near_duplicate_calibration_report(
    *,
    selection: NearDuplicatePairSelectionV1,
    packet: NearDuplicatePairReviewPacketV1,
    receipts: Sequence[NearDuplicatePairReviewReceiptV1],
    consensus: NearDuplicatePairConsensusV1,
    policy: TagTruthV2NearDuplicatePolicy,
    predictions: NearDuplicatePairOraclePredictionSetV1,
    gate: NearDuplicateCalibrationGateV1,
    freeze: NearDuplicatePolicyCandidateFreezeV1,
    holdout_release: NearDuplicateHoldoutReleaseReceiptV1,
) -> NearDuplicateCalibrationReportV1:
    selection = NearDuplicatePairSelectionV1.model_validate_json(
        canonical_json(selection.model_dump(mode="json"))
    )
    packet = NearDuplicatePairReviewPacketV1.model_validate_json(
        canonical_json(packet.model_dump(mode="json"))
    )
    consensus = NearDuplicatePairConsensusV1.model_validate_json(
        canonical_json(consensus.model_dump(mode="json"))
    )
    policy = TagTruthV2NearDuplicatePolicy.model_validate_json(
        canonical_json(policy.model_dump(mode="json"))
    )
    predictions = NearDuplicatePairOraclePredictionSetV1.model_validate_json(
        canonical_json(predictions.model_dump(mode="json"))
    )
    gate = NearDuplicateCalibrationGateV1.model_validate_json(
        canonical_json(gate.model_dump(mode="json"))
    )
    freeze = NearDuplicatePolicyCandidateFreezeV1.model_validate_json(
        canonical_json(freeze.model_dump(mode="json"))
    )
    holdout_release = NearDuplicateHoldoutReleaseReceiptV1.model_validate_json(
        canonical_json(holdout_release.model_dump(mode="json"))
    )
    verify_near_duplicate_pair_review_packet(packet, selection)
    verify_near_duplicate_pair_consensus(consensus, packet, receipts)
    canonical_receipts = tuple(
        sorted(
            (
                NearDuplicatePairReviewReceiptV1.model_validate_json(
                    canonical_json(receipt.model_dump(mode="json"))
                )
                for receipt in receipts
            ),
            key=lambda item: item.receipt_id,
        )
    )
    for receipt in canonical_receipts:
        validate_near_duplicate_pair_review_receipt(receipt, packet)
    verify_exhaustive_pair_oracle_predictions(
        predictions,
        selection,
        policy,
    )
    validate_near_duplicate_policy_candidate_freeze(
        freeze,
        policy=policy,
        predictions=predictions,
        gate=gate,
    )
    validate_near_duplicate_holdout_release_receipt(
        holdout_release,
        selection=selection,
        freeze=freeze,
    )
    if (
        consensus.selection_id != selection.selection_id
        or consensus.packet_id != packet.packet_id
        or predictions.selection_id != selection.selection_id
        or predictions.policy_fingerprint != policy.policy_fingerprint
    ):
        raise ValueError("calibration inputs do not share one sealed identity chain")
    reviewer_ids = tuple(sorted(receipt.reviewer.reviewer_id for receipt in canonical_receipts))
    if holdout_release.custodian.custodian_id in reviewer_ids:
        raise ValueError("holdout custodian cannot be a Pair Truth reviewer")

    selection_cases = {case.pair_id: case for case in selection.cases}
    consensus_cases = {case.pair_id: case for case in consensus.cases}
    prediction_cases = {item.pair_id: item for item in predictions.predictions}
    if set(selection_cases) != set(consensus_cases) or set(selection_cases) != set(
        prediction_cases
    ):
        raise ValueError("calibration inputs do not cover the same Pair set")

    case_results = tuple(
        CalibrationCaseResult(
            pair_id=pair_id,
            split=selection_cases[pair_id].split,
            component_id=selection_cases[pair_id].component_id,
            consensus_status=consensus_cases[pair_id].consensus_status,
            truth_label=consensus_cases[pair_id].label,
            machine_decision=prediction_cases[pair_id].decision,
        )
        for pair_id in sorted(selection_cases)
    )
    calibration_metrics = _split_metrics("calibration", case_results)
    holdout_metrics = _split_metrics("acceptance_holdout", case_results)
    calibration_case_ids = {case.pair_id for case in selection.cases if case.split == "calibration"}
    holdout_case_ids = {
        case.pair_id for case in selection.cases if case.split == "acceptance_holdout"
    }
    calibration_review = _review_quality(
        "calibration",
        calibration_case_ids,
        consensus,
    )
    holdout_review = _review_quality(
        "acceptance_holdout",
        holdout_case_ids,
        consensus,
    )

    blockers: list[CalibrationBlocker] = []
    if holdout_metrics.truth_duplicate_count < gate.minimum_duplicate_cases:
        blockers.append("acceptance_holdout_duplicate_case_count_below_gate")
    if holdout_metrics.truth_independent_count < gate.minimum_independent_cases:
        blockers.append("acceptance_holdout_independent_case_count_below_gate")
    if holdout_metrics.duplicate_component_count < gate.minimum_duplicate_components:
        blockers.append("acceptance_holdout_duplicate_component_count_below_gate")
    if holdout_metrics.independent_component_count < gate.minimum_independent_components:
        blockers.append("acceptance_holdout_independent_component_count_below_gate")
    binary_metric_cases = tuple(
        case
        for case in case_results
        if case.split == "acceptance_holdout" and case.truth_label in {"duplicate", "independent"}
    )
    if gate.require_one_binary_metric_pair_per_component and len(binary_metric_cases) != len(
        {case.component_id for case in binary_metric_cases}
    ):
        blockers.append("acceptance_holdout_metric_pairs_share_component")
    if holdout_metrics.false_clear_duplicate_count > gate.maximum_false_clear_duplicate_count:
        blockers.append("acceptance_holdout_false_clear_duplicate")
    if holdout_metrics.hard_reject_independent_count > gate.maximum_hard_reject_independent_count:
        blockers.append("acceptance_holdout_hard_reject_independent")
    if holdout_metrics.binary_abstain_count > gate.maximum_binary_abstain_count:
        blockers.append("acceptance_holdout_binary_abstain")
    if holdout_metrics.hard_reject_ambiguous_count > gate.maximum_hard_reject_ambiguous_count:
        blockers.append("acceptance_holdout_hard_reject_ambiguous")
    if holdout_metrics.auto_clear_ambiguous_count > gate.maximum_auto_clear_ambiguous_count:
        blockers.append("acceptance_holdout_auto_clear_ambiguous")

    if _below(
        holdout_metrics.strict_duplicate_precision,
        "point_ppm",
        gate.minimum_strict_duplicate_precision_ppm,
    ):
        blockers.append("strict_duplicate_precision_below_gate")
    if _below(
        holdout_metrics.strict_duplicate_precision,
        "wilson_lower_ppm",
        gate.minimum_strict_duplicate_precision_wilson_ppm,
    ):
        blockers.append("strict_duplicate_precision_wilson_below_gate")
    if _below(
        holdout_metrics.strict_duplicate_recall,
        "point_ppm",
        gate.minimum_strict_duplicate_recall_ppm,
    ):
        blockers.append("strict_duplicate_recall_below_gate")
    if _below(
        holdout_metrics.strict_duplicate_recall,
        "wilson_lower_ppm",
        gate.minimum_strict_duplicate_recall_wilson_ppm,
    ):
        blockers.append("strict_duplicate_recall_wilson_below_gate")
    if _below(
        holdout_metrics.independent_clear_precision,
        "point_ppm",
        gate.minimum_independent_clear_precision_ppm,
    ):
        blockers.append("independent_clear_precision_below_gate")
    if _below(
        holdout_metrics.independent_clear_precision,
        "wilson_lower_ppm",
        gate.minimum_independent_clear_precision_wilson_ppm,
    ):
        blockers.append("independent_clear_precision_wilson_below_gate")
    if _below(
        holdout_metrics.independent_clear_recall,
        "point_ppm",
        gate.minimum_independent_clear_recall_ppm,
    ):
        blockers.append("independent_clear_recall_below_gate")
    if _below(
        holdout_metrics.independent_clear_recall,
        "wilson_lower_ppm",
        gate.minimum_independent_clear_recall_wilson_ppm,
    ):
        blockers.append("independent_clear_recall_wilson_below_gate")
    if _below(
        holdout_metrics.duplicate_block_recall,
        "point_ppm",
        gate.minimum_duplicate_block_recall_ppm,
    ):
        blockers.append("duplicate_block_recall_below_gate")
    if _below(
        holdout_metrics.duplicate_block_recall,
        "wilson_lower_ppm",
        gate.minimum_duplicate_block_recall_wilson_ppm,
    ):
        blockers.append("duplicate_block_recall_wilson_below_gate")

    if (
        holdout_review.raw_agreement_ppm is None
        or holdout_review.raw_agreement_ppm < gate.minimum_raw_agreement_ppm
    ):
        blockers.append("raw_reviewer_agreement_below_gate")
    if holdout_review.cohen_kappa_ppm is None:
        blockers.append("cohen_kappa_not_computable")
    elif holdout_review.cohen_kappa_ppm < gate.minimum_cohen_kappa_ppm:
        blockers.append("cohen_kappa_below_gate")
    if (
        holdout_review.unresolved_rate_ppm is None
        or holdout_review.unresolved_rate_ppm > gate.maximum_unresolved_rate_ppm
    ):
        blockers.append("unresolved_review_rate_above_gate")

    canonical_blockers = tuple(sorted(set(blockers)))
    payload: dict[str, object] = {
        "schema_version": CALIBRATION_REPORT_SCHEMA_VERSION,
        "selection_id": selection.selection_id,
        "packet_id": packet.packet_id,
        "receipt_ids": sorted(receipt.receipt_id for receipt in canonical_receipts),
        "reviewer_ids": list(reviewer_ids),
        "consensus_id": consensus.consensus_id,
        "policy_candidate_fingerprint": policy.policy_fingerprint,
        "oracle_prediction_set_id": predictions.prediction_set_id,
        "oracle_semantics_fingerprint": predictions.semantics_fingerprint,
        "gate_fingerprint": gate.gate_fingerprint,
        "policy_candidate_freeze_id": freeze.freeze_id,
        "holdout_release_receipt_id": holdout_release.release_receipt_id,
        "holdout_custodian_id": holdout_release.custodian.custodian_id,
        "cases": [case.model_dump(mode="json") for case in case_results],
        "calibration_metrics": calibration_metrics.model_dump(mode="json"),
        "acceptance_holdout_metrics": holdout_metrics.model_dump(mode="json"),
        "calibration_review_quality": calibration_review.model_dump(mode="json"),
        "acceptance_holdout_review_quality": holdout_review.model_dump(mode="json"),
        "calibration_gate_status": "passed" if not canonical_blockers else "failed",
        "policy_approval_readiness": (
            "eligible_for_human_review" if not canonical_blockers else "not_eligible"
        ),
        "policy_approval_status": "not_approved",
        "qualification_blockers": list(canonical_blockers),
        "evidence_qualification_status": "not_qualified",
        "evidence_qualification_blockers": list(_EVIDENCE_QUALIFICATION_BLOCKERS),
    }
    payload["report_id"] = canonical_hash("tag-truth-nd-calibration-report", payload)
    return NearDuplicateCalibrationReportV1.model_validate_json(canonical_json(payload))


def verify_near_duplicate_calibration_report(
    report: NearDuplicateCalibrationReportV1,
    *,
    selection: NearDuplicatePairSelectionV1,
    packet: NearDuplicatePairReviewPacketV1,
    receipts: Sequence[NearDuplicatePairReviewReceiptV1],
    consensus: NearDuplicatePairConsensusV1,
    policy: TagTruthV2NearDuplicatePolicy,
    predictions: NearDuplicatePairOraclePredictionSetV1,
    gate: NearDuplicateCalibrationGateV1,
    freeze: NearDuplicatePolicyCandidateFreezeV1,
    holdout_release: NearDuplicateHoldoutReleaseReceiptV1,
) -> None:
    canonical_report = NearDuplicateCalibrationReportV1.model_validate_json(
        canonical_json(report.model_dump(mode="json"))
    )
    rebuilt = build_near_duplicate_calibration_report(
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
    if canonical_report != rebuilt:
        raise ValueError("calibration report does not rebuild from sealed inputs")


class PolicyApproverIdentity(_FrozenModel):
    approver_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    approver_kind: Literal["human"]
    approver_role: Literal["near_duplicate_policy_approver"]
    affiliation: Annotated[str, Field(min_length=1)]
    candidate_policy_author: Literal[False]
    pair_selector: Literal[False]
    pair_reviewer: Literal[False]
    holdout_custodian: Literal[False]

    @field_validator("affiliation")
    @classmethod
    def validate_affiliation(cls, value: str) -> str:
        return _single_line(value, "near-duplicate policy approver affiliation")


class PolicyApprovalAttestation(_FrozenModel):
    full_report_verified: Literal[True]
    acceptance_holdout_not_seen_before_policy_freeze: Literal[True]
    thresholds_unchanged_after_holdout_unseal: Literal[True]
    blind_campaign_data_not_used_for_calibration: Literal[True]
    attested_at: Annotated[str, Field(pattern=_TIMESTAMP)]

    @field_validator("attested_at")
    @classmethod
    def validate_attested_at(cls, value: str) -> str:
        return _utc_timestamp(value, "policy approval attested_at")


class _PolicyApprovalReceiptPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-nd-policy-approval-receipt-v1"]
    policy_candidate_fingerprint: Annotated[str, Field(pattern=_POLICY_FINGERPRINT)]
    oracle_semantics_fingerprint: Annotated[
        str,
        Field(pattern=_ORACLE_SEMANTICS_FINGERPRINT),
    ]
    calibration_report_id: Annotated[str, Field(pattern=_REPORT_ID)]
    gate_fingerprint: Annotated[str, Field(pattern=_GATE_FINGERPRINT)]
    policy_candidate_freeze_id: Annotated[str, Field(pattern=_POLICY_FREEZE_ID)]
    holdout_release_receipt_id: Annotated[str, Field(pattern=_HOLDOUT_RELEASE_ID)]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID)]
    approver: PolicyApproverIdentity
    attestation: PolicyApprovalAttestation
    recorded_at: Annotated[str, Field(pattern=_TIMESTAMP)]
    decision: Literal["approved", "rejected", "abstained"]
    approved_scope: Literal["future_verified_near_duplicate_screening_policy_semantics"] | None = (
        None
    )
    decision_blockers: tuple[Annotated[str, Field(pattern=_SLUG)], ...]
    rationale: Annotated[str, Field(min_length=1)]
    evidence_qualification_status: Literal["not_qualified"]
    evidence_qualification_blockers: tuple[EvidenceQualificationBlocker, ...]

    @field_validator(
        "decision_blockers",
        "evidence_qualification_blockers",
        mode="before",
    )
    @classmethod
    def parse_blockers(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"policy approval {getattr(info, 'field_name', '')}")

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str) -> str:
        return _single_line(value, "policy approval rationale")

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: str) -> str:
        return _utc_timestamp(value, "policy approval recorded_at")

    @model_validator(mode="after")
    def validate_receipt_payload(self) -> _PolicyApprovalReceiptPayload:
        if self.attestation.attested_at > self.recorded_at:
            raise ValueError("policy approval receipt predates its attestation")
        if self.decision_blockers != tuple(sorted(set(self.decision_blockers))):
            raise ValueError("policy approval blockers must be sorted and unique")
        if self.evidence_qualification_blockers != _EVIDENCE_QUALIFICATION_BLOCKERS:
            raise ValueError("policy approval must preserve evidence qualification blockers")
        if self.decision == "approved":
            if (
                self.approved_scope != "future_verified_near_duplicate_screening_policy_semantics"
                or self.decision_blockers
            ):
                raise ValueError("approved policy receipt requires scope and no blockers")
        elif self.approved_scope is not None or not self.decision_blockers:
            raise ValueError("rejected or abstained policy receipt requires blockers and no scope")
        return self


class NearDuplicatePolicyApprovalReceiptV1(_PolicyApprovalReceiptPayload):
    approval_receipt_id: Annotated[str, Field(pattern=_APPROVAL_RECEIPT_ID)]

    @model_validator(mode="after")
    def validate_approval_receipt_id(self) -> NearDuplicatePolicyApprovalReceiptV1:
        expected = canonical_hash(
            "tag-truth-nd-policy-approval-receipt",
            _identity_payload(self, "approval_receipt_id"),
        )
        if self.approval_receipt_id != expected:
            raise ValueError("policy approval receipt ID does not match its contents")
        return self


def _validate_near_duplicate_policy_approval_receipt_binding(
    receipt: NearDuplicatePolicyApprovalReceiptV1,
    *,
    report: NearDuplicateCalibrationReportV1,
    holdout_release: NearDuplicateHoldoutReleaseReceiptV1,
) -> None:
    receipt = NearDuplicatePolicyApprovalReceiptV1.model_validate_json(
        canonical_json(receipt.model_dump(mode="json"))
    )
    report = NearDuplicateCalibrationReportV1.model_validate_json(
        canonical_json(report.model_dump(mode="json"))
    )
    holdout_release = NearDuplicateHoldoutReleaseReceiptV1.model_validate_json(
        canonical_json(holdout_release.model_dump(mode="json"))
    )
    expected_binding = (
        report.policy_candidate_fingerprint,
        report.oracle_semantics_fingerprint,
        report.report_id,
        report.gate_fingerprint,
        report.policy_candidate_freeze_id,
        report.holdout_release_receipt_id,
        report.selection_id,
        report.consensus_id,
    )
    actual_binding = (
        receipt.policy_candidate_fingerprint,
        receipt.oracle_semantics_fingerprint,
        receipt.calibration_report_id,
        receipt.gate_fingerprint,
        receipt.policy_candidate_freeze_id,
        receipt.holdout_release_receipt_id,
        receipt.selection_id,
        receipt.consensus_id,
    )
    if actual_binding != expected_binding:
        raise ValueError("policy approval receipt differs from calibration report")
    if receipt.approver.approver_id in report.reviewer_ids:
        raise ValueError("policy approver cannot be a Pair Truth reviewer")
    if receipt.approver.approver_id == holdout_release.custodian.custodian_id:
        raise ValueError("policy approver cannot be the holdout custodian")
    if (
        receipt.attestation.attested_at < holdout_release.released_at
        or receipt.recorded_at < holdout_release.released_at
    ):
        raise ValueError("policy approval must occur after holdout release")
    if receipt.decision == "approved" and (
        report.calibration_gate_status != "passed"
        or report.policy_approval_readiness != "eligible_for_human_review"
        or report.qualification_blockers
    ):
        raise ValueError("failed calibration report cannot support policy approval")


def _seal_near_duplicate_policy_approval_receipt_payload(
    payload: Mapping[str, object],
) -> NearDuplicatePolicyApprovalReceiptV1:
    if "approval_receipt_id" in payload:
        raise ValueError("unsealed policy approval receipt cannot contain its ID")
    canonical = _PolicyApprovalReceiptPayload.model_validate_json(canonical_json(dict(payload)))
    result = canonical.model_dump(mode="json")
    result["approval_receipt_id"] = canonical_hash(
        "tag-truth-nd-policy-approval-receipt",
        result,
    )
    receipt = NearDuplicatePolicyApprovalReceiptV1.model_validate_json(canonical_json(result))
    return receipt


def build_verified_near_duplicate_policy_approval_receipt(
    payload: Mapping[str, object],
    *,
    report: NearDuplicateCalibrationReportV1,
    selection: NearDuplicatePairSelectionV1,
    packet: NearDuplicatePairReviewPacketV1,
    receipts: Sequence[NearDuplicatePairReviewReceiptV1],
    consensus: NearDuplicatePairConsensusV1,
    policy: TagTruthV2NearDuplicatePolicy,
    predictions: NearDuplicatePairOraclePredictionSetV1,
    gate: NearDuplicateCalibrationGateV1,
    freeze: NearDuplicatePolicyCandidateFreezeV1,
    holdout_release: NearDuplicateHoldoutReleaseReceiptV1,
) -> NearDuplicatePolicyApprovalReceiptV1:
    verify_near_duplicate_calibration_report(
        report,
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
    approval = _seal_near_duplicate_policy_approval_receipt_payload(payload)
    _validate_near_duplicate_policy_approval_receipt_binding(
        approval,
        report=report,
        holdout_release=holdout_release,
    )
    return approval


def verify_near_duplicate_policy_approval_receipt(
    receipt: NearDuplicatePolicyApprovalReceiptV1,
    *,
    report: NearDuplicateCalibrationReportV1,
    selection: NearDuplicatePairSelectionV1,
    packet: NearDuplicatePairReviewPacketV1,
    receipts: Sequence[NearDuplicatePairReviewReceiptV1],
    consensus: NearDuplicatePairConsensusV1,
    policy: TagTruthV2NearDuplicatePolicy,
    predictions: NearDuplicatePairOraclePredictionSetV1,
    gate: NearDuplicateCalibrationGateV1,
    freeze: NearDuplicatePolicyCandidateFreezeV1,
    holdout_release: NearDuplicateHoldoutReleaseReceiptV1,
) -> None:
    canonical_receipt = NearDuplicatePolicyApprovalReceiptV1.model_validate_json(
        canonical_json(receipt.model_dump(mode="json"))
    )
    verify_near_duplicate_calibration_report(
        report,
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
    _validate_near_duplicate_policy_approval_receipt_binding(
        canonical_receipt,
        report=report,
        holdout_release=holdout_release,
    )


def _parse_json_model[TModel: BaseModel](
    raw: bytes,
    model: type[TModel],
    context: str,
) -> TModel:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        return model.model_validate_json(canonical_json(payload))
    except (UnicodeError, json.JSONDecodeError, ValidationError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


def _load_json_model[TModel: BaseModel](
    path: str | Path,
    model: type[TModel],
    context: str,
) -> TModel:
    artifact = Path(path)
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError(f"{context} must be a regular non-symlink file: {artifact}")
    try:
        raw = artifact.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {context} {artifact}: {exc}") from exc
    return _parse_json_model(raw, model, context)


def parse_near_duplicate_pair_oracle_predictions(
    raw: bytes,
) -> NearDuplicatePairOraclePredictionSetV1:
    return _parse_json_model(
        raw,
        NearDuplicatePairOraclePredictionSetV1,
        "near-duplicate Pair oracle predictions",
    )


def load_near_duplicate_pair_oracle_predictions(
    path: str | Path,
) -> NearDuplicatePairOraclePredictionSetV1:
    return _load_json_model(
        path,
        NearDuplicatePairOraclePredictionSetV1,
        "near-duplicate Pair oracle predictions",
    )


def parse_near_duplicate_policy_candidate_freeze(
    raw: bytes,
) -> NearDuplicatePolicyCandidateFreezeV1:
    return _parse_json_model(
        raw,
        NearDuplicatePolicyCandidateFreezeV1,
        "near-duplicate policy candidate freeze",
    )


def load_near_duplicate_policy_candidate_freeze(
    path: str | Path,
) -> NearDuplicatePolicyCandidateFreezeV1:
    return _load_json_model(
        path,
        NearDuplicatePolicyCandidateFreezeV1,
        "near-duplicate policy candidate freeze",
    )


def parse_near_duplicate_holdout_release_receipt(
    raw: bytes,
) -> NearDuplicateHoldoutReleaseReceiptV1:
    return _parse_json_model(
        raw,
        NearDuplicateHoldoutReleaseReceiptV1,
        "near-duplicate holdout release receipt",
    )


def load_near_duplicate_holdout_release_receipt(
    path: str | Path,
) -> NearDuplicateHoldoutReleaseReceiptV1:
    return _load_json_model(
        path,
        NearDuplicateHoldoutReleaseReceiptV1,
        "near-duplicate holdout release receipt",
    )


def parse_near_duplicate_calibration_gate(
    raw: bytes,
) -> NearDuplicateCalibrationGateV1:
    return _parse_json_model(raw, NearDuplicateCalibrationGateV1, "calibration gate")


def load_near_duplicate_calibration_gate(
    path: str | Path,
) -> NearDuplicateCalibrationGateV1:
    return _load_json_model(path, NearDuplicateCalibrationGateV1, "calibration gate")


def parse_near_duplicate_calibration_report(
    raw: bytes,
) -> NearDuplicateCalibrationReportV1:
    return _parse_json_model(raw, NearDuplicateCalibrationReportV1, "calibration report")


def load_near_duplicate_calibration_report(
    path: str | Path,
) -> NearDuplicateCalibrationReportV1:
    return _load_json_model(path, NearDuplicateCalibrationReportV1, "calibration report")


def parse_near_duplicate_policy_approval_receipt(
    raw: bytes,
) -> NearDuplicatePolicyApprovalReceiptV1:
    return _parse_json_model(
        raw,
        NearDuplicatePolicyApprovalReceiptV1,
        "near-duplicate policy approval receipt",
    )


def load_near_duplicate_policy_approval_receipt(
    path: str | Path,
) -> NearDuplicatePolicyApprovalReceiptV1:
    return _load_json_model(
        path,
        NearDuplicatePolicyApprovalReceiptV1,
        "near-duplicate policy approval receipt",
    )


__all__ = [
    "CALIBRATION_GATE_SCHEMA_VERSION",
    "CALIBRATION_REPORT_SCHEMA_VERSION",
    "HOLDOUT_RELEASE_RECEIPT_SCHEMA_VERSION",
    "POLICY_CANDIDATE_FREEZE_SCHEMA_VERSION",
    "CalibrationBlocker",
    "CalibrationCaseResult",
    "CalibrationSplitMetrics",
    "EvidenceQualificationBlocker",
    "HoldoutCustodianIdentity",
    "MachinePairDecision",
    "NearDuplicateCalibrationGateV1",
    "NearDuplicateCalibrationReportV1",
    "NearDuplicateHoldoutReleaseReceiptV1",
    "NearDuplicatePairOraclePredictionSetV1",
    "NearDuplicatePolicyApprovalReceiptV1",
    "NearDuplicatePolicyCandidateFreezeV1",
    "PAIR_ORACLE_SCHEMA_VERSION",
    "POLICY_APPROVAL_RECEIPT_SCHEMA_VERSION",
    "PairConfusionCell",
    "PairOracleComparison",
    "PairOraclePrediction",
    "PolicyApprovalAttestation",
    "PolicyApproverIdentity",
    "RateMeasurement",
    "ReviewQualityMetrics",
    "VerifierClosureBlob",
    "build_exhaustive_pair_oracle_predictions",
    "build_near_duplicate_calibration_report",
    "build_verified_near_duplicate_policy_approval_receipt",
    "calibration_gate_payload_with_fingerprint",
    "default_near_duplicate_calibration_gate",
    "load_near_duplicate_calibration_gate",
    "load_near_duplicate_calibration_report",
    "load_near_duplicate_holdout_release_receipt",
    "load_near_duplicate_pair_oracle_predictions",
    "load_near_duplicate_policy_approval_receipt",
    "load_near_duplicate_policy_candidate_freeze",
    "parse_near_duplicate_calibration_gate",
    "parse_near_duplicate_calibration_report",
    "parse_near_duplicate_holdout_release_receipt",
    "parse_near_duplicate_pair_oracle_predictions",
    "parse_near_duplicate_policy_approval_receipt",
    "parse_near_duplicate_policy_candidate_freeze",
    "seal_near_duplicate_holdout_release_receipt_payload",
    "seal_near_duplicate_policy_candidate_freeze_payload",
    "verify_exhaustive_pair_oracle_predictions",
    "verify_near_duplicate_calibration_report",
    "verify_near_duplicate_policy_approval_receipt",
    "validate_near_duplicate_holdout_release_receipt",
    "validate_near_duplicate_policy_candidate_freeze",
]
