from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    bytes_hash,
    canonical_hash,
    canonical_json,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate import (
    tokenize_arkts_like,
)

PAIR_SELECTION_SCHEMA_VERSION = "tag-truth-v2-nd-pair-selection-v1"
PAIR_REVIEW_PACKET_SCHEMA_VERSION = "tag-truth-v2-nd-pair-packet-v1"
PAIR_REVIEW_RECEIPT_SCHEMA_VERSION = "tag-truth-v2-nd-pair-receipt-v1"
PAIR_CONSENSUS_SCHEMA_VERSION = "tag-truth-v2-nd-pair-consensus-v1"

PairSplit = Literal["calibration", "acceptance_holdout"]
PairAxis = Literal["file", "unit"]
PairDirection = Literal["file_file", "unit_file", "unit_unit"]
PairTruthLabel = Literal["duplicate", "independent", "ambiguous"]
PairConsensusCaseStatus = Literal[
    "agreed_resolved",
    "agreed_ambiguous",
    "unresolved",
]
PairMetricRole = Literal["binary", "ambiguous_guard", "excluded"]

_SHA256 = r"^sha256:[0-9a-f]{64}$"
_GIT_OBJECT_ID = r"^[0-9a-f]{40}$"
_MEMBER_ID = r"^tag-truth-nd-pair-member:sha256:[0-9a-f]{64}$"
_PAIR_ID = r"^tag-truth-nd-pair:sha256:[0-9a-f]{64}$"
_COMPONENT_ID = r"^tag-truth-nd-pair-component:sha256:[0-9a-f]{64}$"
_SELECTION_PROCESS_FINGERPRINT = r"^tag-truth-nd-pair-selection-process:sha256:[0-9a-f]{64}$"
_SELECTION_ID = r"^tag-truth-nd-pair-selection:sha256:[0-9a-f]{64}$"
_REVIEW_POLICY_FINGERPRINT = r"^tag-truth-nd-pair-review-policy:sha256:[0-9a-f]{64}$"
_PACKET_ID = r"^tag-truth-nd-pair-packet:sha256:[0-9a-f]{64}$"
_RECEIPT_ID = r"^tag-truth-nd-pair-receipt:sha256:[0-9a-f]{64}$"
_CONSENSUS_ID = r"^tag-truth-nd-pair-consensus:sha256:[0-9a-f]{64}$"
_TEMPLATE_CLUSTER_ID = r"^template-cluster:sha256:[0-9a-f]{64}$"
_ROUND_ID = r"^round-[a-z0-9]+(?:-[a-z0-9]+)*$"
_IDENTITY_ID = r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"
_SLUG = r"^[a-z][a-z0-9_]*$"
_TIMESTAMP = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"

_QUALIFICATION_REASONS = (
    "calibration_pending",
    "dual_review_pending",
    "external_identity_not_authenticated",
    "policy_approval_pending",
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


def _utc_timestamp(value: str, context: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(f"{context} must be a valid UTC timestamp") from exc
    return value


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={identity_field})


def _text_line_count(text: str) -> int:
    return max(1, len(text.splitlines()))


def normalized_pair_body_sha256(text: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFC", text).split())
    if not normalized:
        raise ValueError("normalized Pair member body cannot be empty")
    return bytes_hash(normalized.encode("utf-8"))


def pair_template_cluster_id(text: str) -> str:
    shape_tokens = tokenize_arkts_like(text, mode="lexical_shape")
    if not shape_tokens:
        raise ValueError("Pair member shape token stream cannot be empty")
    return canonical_hash(
        "template-cluster",
        {
            "version": "arkts-like-lexical-shape-v1",
            "tokens": shape_tokens,
        },
    )


class _PairMemberDraft(_FrozenModel):
    repository_source_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    path: str
    axis: PairAxis
    unit_start_line: Annotated[int, Field(ge=1)] | None = None
    unit_end_line: Annotated[int, Field(ge=1)] | None = None
    source_family_id: str
    media_class: Annotated[str, Field(pattern=_SLUG)]
    content_sha256: Annotated[str, Field(pattern=_SHA256)]
    manual_related_group_ids: tuple[Annotated[str, Field(pattern=_IDENTITY_ID)], ...] = ()
    line_count: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=1)]

    @field_validator("manual_related_group_ids", mode="before")
    @classmethod
    def parse_related_groups(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "pair member manual related groups")

    @field_validator("manual_related_group_ids")
    @classmethod
    def validate_related_groups(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value, "pair member manual related groups")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value, "pair member source path")

    @field_validator("source_family_id")
    @classmethod
    def validate_family(cls, value: str) -> str:
        return _relative_path(value, "pair member source family")

    @model_validator(mode="after")
    def validate_member(self) -> _PairMemberDraft:
        if bytes_hash(self.text.encode("utf-8")) != self.content_sha256:
            raise ValueError("pair member content hash does not match text")
        if self.line_count != _text_line_count(self.text):
            raise ValueError("pair member line count does not match text")
        if self.axis == "file":
            if self.unit_start_line is not None or self.unit_end_line is not None:
                raise ValueError("file pair member cannot contain a Unit span")
        elif self.unit_start_line is None or self.unit_end_line is None:
            raise ValueError("Unit pair member requires a complete source span")
        elif self.unit_start_line > self.unit_end_line:
            raise ValueError("Unit pair member span is inverted")
        return self


class _PairMemberPayload(_PairMemberDraft):
    leakage_identity_version: Literal["nfc-whitespace-shape-v1"]
    normalized_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    template_cluster_id: Annotated[str, Field(pattern=_TEMPLATE_CLUSTER_ID)]

    @model_validator(mode="after")
    def validate_derived_leakage_identities(self) -> _PairMemberPayload:
        if self.normalized_body_sha256 != normalized_pair_body_sha256(self.text):
            raise ValueError("pair member normalized-body hash does not match text")
        if self.template_cluster_id != pair_template_cluster_id(self.text):
            raise ValueError("pair member template cluster does not match text")
        return self


class NearDuplicatePairMember(_PairMemberPayload):
    member_id: Annotated[str, Field(pattern=_MEMBER_ID)]

    @model_validator(mode="after")
    def validate_member_id(self) -> NearDuplicatePairMember:
        expected = canonical_hash(
            "tag-truth-nd-pair-member",
            _identity_payload(self, "member_id"),
        )
        if self.member_id != expected:
            raise ValueError("pair member ID does not match its contents")
        return self


def pair_member_payload_with_id(payload: Mapping[str, object]) -> dict[str, object]:
    if "member_id" in payload:
        raise ValueError("unsealed pair member cannot contain member_id")
    canonical = _PairMemberDraft.model_validate_json(canonical_json(dict(payload)))
    result = {
        **canonical.model_dump(mode="json"),
        "leakage_identity_version": "nfc-whitespace-shape-v1",
        "normalized_body_sha256": normalized_pair_body_sha256(canonical.text),
        "template_cluster_id": pair_template_cluster_id(canonical.text),
    }
    result["member_id"] = canonical_hash("tag-truth-nd-pair-member", result)
    return result


class _PairSelectionProcessPayload(_FrozenModel):
    generator_version: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")]
    selection_seed_commitment: Annotated[str, Field(pattern=_SHA256)]
    split_assignment_unit: Literal["leakage_component"]
    acceptance_holdout_visibility: Literal["custodian_sealed_until_policy_candidate_freeze"]
    selected_before_policy_candidate_freeze: Literal[True]


class NearDuplicatePairSelectionProcess(_PairSelectionProcessPayload):
    process_fingerprint: Annotated[str, Field(pattern=_SELECTION_PROCESS_FINGERPRINT)]

    @model_validator(mode="after")
    def validate_process_fingerprint(self) -> NearDuplicatePairSelectionProcess:
        expected = canonical_hash(
            "tag-truth-nd-pair-selection-process",
            _identity_payload(self, "process_fingerprint"),
        )
        if self.process_fingerprint != expected:
            raise ValueError("pair selection process fingerprint does not match its contents")
        return self


def pair_selection_process_payload_with_fingerprint(
    payload: Mapping[str, object],
) -> dict[str, object]:
    if "process_fingerprint" in payload:
        raise ValueError("unsealed pair selection process cannot contain process_fingerprint")
    canonical = _PairSelectionProcessPayload.model_validate_json(canonical_json(dict(payload)))
    result = canonical.model_dump(mode="json")
    result["process_fingerprint"] = canonical_hash(
        "tag-truth-nd-pair-selection-process",
        result,
    )
    return result


class _PairSelectionCaseDraft(_FrozenModel):
    split: PairSplit
    direction: PairDirection
    member_ids: tuple[Annotated[str, Field(pattern=_MEMBER_ID)], ...]
    selection_stratum_id: Annotated[str, Field(pattern=_SLUG)]
    selection_rank: Annotated[int, Field(ge=1)]
    coverage_strata: tuple[Annotated[str, Field(pattern=_SLUG)], ...]

    @field_validator("member_ids", "coverage_strata", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"pair selection case {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_case_draft(self) -> _PairSelectionCaseDraft:
        if len(self.member_ids) != 2 or len(set(self.member_ids)) != 2:
            raise ValueError("pair selection case requires two distinct members")
        if not self.coverage_strata:
            raise ValueError("pair selection case requires coverage strata")
        _sorted_unique(self.coverage_strata, "pair selection coverage strata")
        return self


class NearDuplicatePairSelectionCase(_PairSelectionCaseDraft):
    pair_id: Annotated[str, Field(pattern=_PAIR_ID)]
    component_id: Annotated[str, Field(pattern=_COMPONENT_ID)]

    @model_validator(mode="after")
    def validate_pair_id(self) -> NearDuplicatePairSelectionCase:
        payload = self.model_dump(mode="json", exclude={"pair_id", "component_id"})
        expected = canonical_hash("tag-truth-nd-pair", payload)
        if self.pair_id != expected:
            raise ValueError("near-duplicate pair ID does not match its contents")
        return self


def _member_leakage_keys(member: NearDuplicatePairMember) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                f"member:{member.member_id}",
                f"content:{member.content_sha256}",
                f"normalized:{member.normalized_body_sha256}",
                f"template:{member.template_cluster_id}",
                (f"family:{member.repository_source_id}:{member.source_family_id}"),
                *(f"manual:{item}" for item in member.manual_related_group_ids),
            }
        )
    )


def _component_ids(
    cases: Sequence[tuple[str, PairSplit, tuple[str, ...]]],
    members_by_id: Mapping[str, NearDuplicatePairMember],
) -> dict[str, str]:
    parent = list(range(len(cases)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    first_by_key: dict[str, int] = {}
    for index, (_, _, member_ids) in enumerate(cases):
        keys = {
            key
            for member_id in member_ids
            for key in _member_leakage_keys(members_by_id[member_id])
        }
        for key in sorted(keys):
            previous = first_by_key.setdefault(key, index)
            union(index, previous)

    grouped: dict[int, list[int]] = {}
    for index in range(len(cases)):
        grouped.setdefault(find(index), []).append(index)

    result: dict[str, str] = {}
    for indices in grouped.values():
        splits = {cases[index][1] for index in indices}
        if len(splits) != 1:
            raise ValueError("pair leakage component crosses calibration and holdout splits")
        pair_ids = tuple(sorted(cases[index][0] for index in indices))
        component_id = canonical_hash("tag-truth-nd-pair-component", pair_ids)
        for pair_id in pair_ids:
            result[pair_id] = component_id
    return result


class _NearDuplicatePairSelectionPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-nd-pair-selection-v1"]
    suite_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    dataset_role: Literal["near_duplicate_policy_calibration"]
    natural_prevalence_claimed: Literal[False]
    qualification_status: Literal["not_qualified"]
    qualification_reasons: tuple[
        Literal[
            "calibration_pending",
            "dual_review_pending",
            "external_identity_not_authenticated",
            "policy_approval_pending",
        ],
        ...,
    ]
    selection_process: NearDuplicatePairSelectionProcess
    members: tuple[NearDuplicatePairMember, ...]
    cases: tuple[NearDuplicatePairSelectionCase, ...]

    @field_validator("qualification_reasons", "members", "cases", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"pair selection {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_selection_payload(self) -> _NearDuplicatePairSelectionPayload:
        if self.qualification_reasons != _QUALIFICATION_REASONS:
            raise ValueError("pair selection must preserve every current qualification blocker")
        member_ids = tuple(member.member_id for member in self.members)
        if not member_ids or member_ids != tuple(sorted(set(member_ids))):
            raise ValueError("pair selection members must be sorted and unique")
        pair_ids = tuple(case.pair_id for case in self.cases)
        if not pair_ids or pair_ids != tuple(sorted(set(pair_ids))):
            raise ValueError("pair selection cases must be sorted and unique")
        ranks = tuple(case.selection_rank for case in self.cases)
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("pair selection ranks must be contiguous from one")
        members_by_id = {member.member_id: member for member in self.members}
        referenced: set[str] = set()
        unordered_pairs: set[frozenset[str]] = set()
        for case in self.cases:
            left_id, right_id = case.member_ids
            if left_id not in members_by_id or right_id not in members_by_id:
                raise ValueError("pair selection case references an unknown member")
            unordered = frozenset(case.member_ids)
            if unordered in unordered_pairs:
                raise ValueError("pair selection contains a duplicate unordered pair")
            unordered_pairs.add(unordered)
            left = members_by_id[left_id]
            right = members_by_id[right_id]
            expected_axes = {
                "file_file": ("file", "file"),
                "unit_file": ("unit", "file"),
                "unit_unit": ("unit", "unit"),
            }[case.direction]
            if (left.axis, right.axis) != expected_axes:
                raise ValueError("pair direction differs from member axes")
            if case.direction in {"file_file", "unit_unit"} and left_id >= right_id:
                raise ValueError("symmetric pair directions require canonical member ordering")
            referenced.update(case.member_ids)
        if referenced != set(members_by_id):
            raise ValueError("pair selection members must be referenced completely")
        if {case.split for case in self.cases} != {"calibration", "acceptance_holdout"}:
            raise ValueError("pair selection requires calibration and acceptance holdout splits")
        expected_components = _component_ids(
            tuple((case.pair_id, case.split, case.member_ids) for case in self.cases),
            members_by_id,
        )
        if any(case.component_id != expected_components[case.pair_id] for case in self.cases):
            raise ValueError("pair selection component IDs do not match leakage connectivity")
        return self


class NearDuplicatePairSelectionV1(_NearDuplicatePairSelectionPayload):
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]

    @model_validator(mode="after")
    def validate_selection_id(self) -> NearDuplicatePairSelectionV1:
        expected = canonical_hash(
            "tag-truth-nd-pair-selection",
            _identity_payload(self, "selection_id"),
        )
        if self.selection_id != expected:
            raise ValueError("pair selection ID does not match its complete contents")
        return self


class _PairSelectionDraft(_FrozenModel):
    schema_version: Literal["tag-truth-v2-nd-pair-selection-v1"]
    suite_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    dataset_role: Literal["near_duplicate_policy_calibration"]
    natural_prevalence_claimed: Literal[False]
    qualification_status: Literal["not_qualified"]
    qualification_reasons: tuple[
        Literal[
            "calibration_pending",
            "dual_review_pending",
            "external_identity_not_authenticated",
            "policy_approval_pending",
        ],
        ...,
    ]
    selection_process: _PairSelectionProcessPayload
    members: tuple[_PairMemberDraft, ...]
    cases: tuple[_PairSelectionCaseDraft, ...]

    @field_validator("qualification_reasons", "members", "cases", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"pair selection draft {getattr(info, 'field_name', '')}")


def seal_near_duplicate_pair_selection_payload(
    payload: Mapping[str, object],
) -> NearDuplicatePairSelectionV1:
    if "selection_id" in payload:
        raise ValueError("unsealed pair selection cannot contain selection_id")
    draft = _PairSelectionDraft.model_validate_json(canonical_json(dict(payload)))
    process = NearDuplicatePairSelectionProcess.model_validate(
        pair_selection_process_payload_with_fingerprint(
            draft.selection_process.model_dump(mode="json")
        )
    )
    members = tuple(
        sorted(
            (
                NearDuplicatePairMember.model_validate(
                    pair_member_payload_with_id(member.model_dump(mode="json"))
                )
                for member in draft.members
            ),
            key=lambda item: item.member_id,
        )
    )
    members_by_id = {member.member_id: member for member in members}
    case_payloads: list[dict[str, object]] = []
    for draft_case in draft.cases:
        case_payload = draft_case.model_dump(mode="json")
        member_ids = tuple(case_payload["member_ids"])
        if draft_case.direction in {"file_file", "unit_unit"}:
            member_ids = tuple(sorted(member_ids))
            case_payload["member_ids"] = list(member_ids)
        if any(member_id not in members_by_id for member_id in member_ids):
            raise ValueError("pair selection draft references an unknown sealed member")
        pair_id = canonical_hash("tag-truth-nd-pair", case_payload)
        case_payloads.append({**case_payload, "pair_id": pair_id})
    component_ids = _component_ids(
        tuple(
            (
                str(case["pair_id"]),
                cast(PairSplit, case["split"]),
                tuple(cast(list[str], case["member_ids"])),
            )
            for case in case_payloads
        ),
        members_by_id,
    )
    cases = tuple(
        sorted(
            (
                NearDuplicatePairSelectionCase.model_validate(
                    {
                        **case,
                        "component_id": component_ids[str(case["pair_id"])],
                    }
                )
                for case in case_payloads
            ),
            key=lambda item: item.pair_id,
        )
    )
    sealed_payload: dict[str, object] = {
        "schema_version": draft.schema_version,
        "suite_id": draft.suite_id,
        "dataset_role": draft.dataset_role,
        "natural_prevalence_claimed": draft.natural_prevalence_claimed,
        "qualification_status": draft.qualification_status,
        "qualification_reasons": list(draft.qualification_reasons),
        "selection_process": process.model_dump(mode="json"),
        "members": [member.model_dump(mode="json") for member in members],
        "cases": [case.model_dump(mode="json") for case in cases],
    }
    sealed_payload["selection_id"] = canonical_hash(
        "tag-truth-nd-pair-selection",
        sealed_payload,
    )
    return NearDuplicatePairSelectionV1.model_validate_json(canonical_json(sealed_payload))


class _PairReviewPolicyPayload(_FrozenModel):
    policy_version: Literal["near-duplicate-pair-review-v1"]
    duplicate_definition: Annotated[str, Field(min_length=1)]
    independent_definition: Annotated[str, Field(min_length=1)]
    ambiguous_definition: Annotated[str, Field(min_length=1)]
    bilateral_evidence_required: Literal[True]
    algorithm_output_redacted: Literal[True]
    algorithm_thresholds_redacted: Literal[True]
    explicit_repository_fields_redacted: Literal[True]
    selection_metadata_redacted: Literal[True]
    reviewers_required: Literal[2]

    @field_validator(
        "duplicate_definition",
        "independent_definition",
        "ambiguous_definition",
    )
    @classmethod
    def validate_definitions(cls, value: str, info: object) -> str:
        return _single_line(value, f"pair review {getattr(info, 'field_name', 'definition')}")


class NearDuplicatePairReviewPolicy(_PairReviewPolicyPayload):
    policy_fingerprint: Annotated[str, Field(pattern=_REVIEW_POLICY_FINGERPRINT)]

    @model_validator(mode="after")
    def validate_policy_fingerprint(self) -> NearDuplicatePairReviewPolicy:
        expected = canonical_hash(
            "tag-truth-nd-pair-review-policy",
            _identity_payload(self, "policy_fingerprint"),
        )
        if self.policy_fingerprint != expected:
            raise ValueError("pair review policy fingerprint does not match its contents")
        return self


def pair_review_policy_payload_with_fingerprint(
    payload: Mapping[str, object],
) -> dict[str, object]:
    if "policy_fingerprint" in payload:
        raise ValueError("unsealed pair review policy cannot contain policy_fingerprint")
    canonical = _PairReviewPolicyPayload.model_validate_json(canonical_json(dict(payload)))
    result = canonical.model_dump(mode="json")
    result["policy_fingerprint"] = canonical_hash(
        "tag-truth-nd-pair-review-policy",
        result,
    )
    return result


def default_near_duplicate_pair_review_policy() -> NearDuplicatePairReviewPolicy:
    return NearDuplicatePairReviewPolicy.model_validate(
        pair_review_policy_payload_with_fingerprint(
            {
                "policy_version": "near-duplicate-pair-review-v1",
                "duplicate_definition": (
                    "The two sides share substantive implementation provenance and cannot "
                    "serve as independent evidence."
                ),
                "independent_definition": (
                    "The two sides are independently implemented despite shared APIs or "
                    "ordinary framework structure."
                ),
                "ambiguous_definition": (
                    "Available content or provenance is insufficient for a reliable "
                    "duplicate-versus-independent judgement."
                ),
                "bilateral_evidence_required": True,
                "algorithm_output_redacted": True,
                "algorithm_thresholds_redacted": True,
                "explicit_repository_fields_redacted": True,
                "selection_metadata_redacted": True,
                "reviewers_required": 2,
            }
        )
    )


class PairReviewPacketSide(_FrozenModel):
    side: Literal["a", "b"]
    axis: PairAxis
    media_class: Annotated[str, Field(pattern=_SLUG)]
    content_sha256: Annotated[str, Field(pattern=_SHA256)]
    line_count: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_side(self) -> PairReviewPacketSide:
        if bytes_hash(self.text.encode("utf-8")) != self.content_sha256:
            raise ValueError("pair packet side content hash does not match text")
        if self.line_count != _text_line_count(self.text):
            raise ValueError("pair packet side line count does not match text")
        return self


class PairReviewPacketCase(_FrozenModel):
    pair_id: Annotated[str, Field(pattern=_PAIR_ID)]
    direction: PairDirection
    sides: tuple[PairReviewPacketSide, ...]

    @field_validator("sides", mode="before")
    @classmethod
    def parse_sides(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "pair review packet sides")

    @model_validator(mode="after")
    def validate_packet_case(self) -> PairReviewPacketCase:
        if tuple(side.side for side in self.sides) != ("a", "b"):
            raise ValueError("pair review packet requires canonical a/b sides")
        expected_axes = {
            "file_file": ("file", "file"),
            "unit_file": ("unit", "file"),
            "unit_unit": ("unit", "unit"),
        }[self.direction]
        if tuple(side.axis for side in self.sides) != expected_axes:
            raise ValueError("pair packet direction differs from side axes")
        return self


class _NearDuplicatePairReviewPacketPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-nd-pair-packet-v1"]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    suite_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    review_policy: NearDuplicatePairReviewPolicy
    cases: tuple[PairReviewPacketCase, ...]

    @field_validator("cases", mode="before")
    @classmethod
    def parse_cases(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "pair review packet cases")

    @model_validator(mode="after")
    def validate_packet_payload(self) -> _NearDuplicatePairReviewPacketPayload:
        pair_ids = tuple(case.pair_id for case in self.cases)
        if not pair_ids or pair_ids != tuple(sorted(set(pair_ids))):
            raise ValueError("pair review packet cases must be sorted and unique")
        return self


class NearDuplicatePairReviewPacketV1(_NearDuplicatePairReviewPacketPayload):
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]

    @model_validator(mode="after")
    def validate_packet_id(self) -> NearDuplicatePairReviewPacketV1:
        expected = canonical_hash(
            "tag-truth-nd-pair-packet",
            _identity_payload(self, "packet_id"),
        )
        if self.packet_id != expected:
            raise ValueError("pair review packet ID does not match its contents")
        return self


def build_near_duplicate_pair_review_packet(
    selection: NearDuplicatePairSelectionV1,
    *,
    review_policy: NearDuplicatePairReviewPolicy | None = None,
) -> NearDuplicatePairReviewPacketV1:
    selection = NearDuplicatePairSelectionV1.model_validate_json(
        canonical_json(selection.model_dump(mode="json"))
    )
    policy = review_policy or default_near_duplicate_pair_review_policy()
    policy = NearDuplicatePairReviewPolicy.model_validate_json(
        canonical_json(policy.model_dump(mode="json"))
    )
    members_by_id = {member.member_id: member for member in selection.members}
    cases = []
    for case in selection.cases:
        sides = []
        side_names: tuple[Literal["a", "b"], ...] = ("a", "b")
        for side_name, member_id in zip(side_names, case.member_ids, strict=True):
            member = members_by_id[member_id]
            sides.append(
                PairReviewPacketSide(
                    side=side_name,
                    axis=member.axis,
                    media_class=member.media_class,
                    content_sha256=member.content_sha256,
                    line_count=member.line_count,
                    text=member.text,
                )
            )
        cases.append(
            PairReviewPacketCase(
                pair_id=case.pair_id,
                direction=case.direction,
                sides=tuple(sides),
            )
        )
    payload: dict[str, object] = {
        "schema_version": PAIR_REVIEW_PACKET_SCHEMA_VERSION,
        "selection_id": selection.selection_id,
        "suite_id": selection.suite_id,
        "review_policy": policy.model_dump(mode="json"),
        "cases": [case.model_dump(mode="json") for case in cases],
    }
    payload["packet_id"] = canonical_hash("tag-truth-nd-pair-packet", payload)
    return NearDuplicatePairReviewPacketV1.model_validate_json(canonical_json(payload))


def verify_near_duplicate_pair_review_packet(
    packet: NearDuplicatePairReviewPacketV1,
    selection: NearDuplicatePairSelectionV1,
) -> None:
    canonical_packet = NearDuplicatePairReviewPacketV1.model_validate_json(
        canonical_json(packet.model_dump(mode="json"))
    )
    rebuilt = build_near_duplicate_pair_review_packet(
        selection,
        review_policy=canonical_packet.review_policy,
    )
    if canonical_packet != rebuilt:
        raise ValueError("pair review packet does not rebuild from selection")


class PairReviewerIdentity(_FrozenModel):
    reviewer_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    reviewer_kind: Literal["human"]
    reviewer_role: Literal["near_duplicate_truth_reviewer"]
    affiliation: Annotated[str, Field(min_length=1)]
    candidate_policy_design_participant: Literal[False]
    selection_participant: Literal[False]

    @field_validator("affiliation")
    @classmethod
    def validate_affiliation(cls, value: str) -> str:
        return _single_line(value, "pair reviewer affiliation")


class PairReviewerBlindingAttestation(_FrozenModel):
    selection_manifest_seen: Literal[False]
    split_assignment_seen: Literal[False]
    component_assignment_seen: Literal[False]
    policy_candidate_output_seen: Literal[False]
    algorithm_thresholds_seen: Literal[False]
    other_reviewer_receipt_seen: Literal[False]
    review_completed_before_unblinding: Literal[True]
    attested_at: Annotated[str, Field(pattern=_TIMESTAMP)]

    @field_validator("attested_at")
    @classmethod
    def validate_attested_at(cls, value: str) -> str:
        return _utc_timestamp(value, "pair reviewer attested_at")


class PairReviewDecision(_FrozenModel):
    pair_id: Annotated[str, Field(pattern=_PAIR_ID)]
    label: PairTruthLabel
    side_a_evidence_lines: tuple[Annotated[int, Field(ge=1)], ...]
    side_b_evidence_lines: tuple[Annotated[int, Field(ge=1)], ...]
    rationale: Annotated[str, Field(min_length=1)]
    ambiguity_reason: Annotated[str, Field(pattern=_SLUG)] | None = None

    @field_validator(
        "side_a_evidence_lines",
        "side_b_evidence_lines",
        mode="before",
    )
    @classmethod
    def parse_evidence(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"pair review {getattr(info, 'field_name', 'evidence')}")

    @field_validator("side_a_evidence_lines", "side_b_evidence_lines")
    @classmethod
    def validate_evidence(cls, value: tuple[int, ...], info: object) -> tuple[int, ...]:
        if not value:
            raise ValueError(f"{getattr(info, 'field_name', 'evidence')} cannot be empty")
        if value != tuple(sorted(set(value))):
            raise ValueError("pair review evidence lines must be sorted and unique")
        return value

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str) -> str:
        return _single_line(value, "pair review rationale")

    @model_validator(mode="after")
    def validate_label(self) -> PairReviewDecision:
        if self.label == "ambiguous":
            if self.ambiguity_reason is None:
                raise ValueError("ambiguous pair review requires ambiguity_reason")
        elif self.ambiguity_reason is not None:
            raise ValueError("resolved pair review cannot contain ambiguity_reason")
        return self


class _PairReviewReceiptPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-nd-pair-receipt-v1"]
    round_id: Annotated[str, Field(pattern=_ROUND_ID)]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    suite_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    review_policy_fingerprint: Annotated[str, Field(pattern=_REVIEW_POLICY_FINGERPRINT)]
    reviewer: PairReviewerIdentity
    blinding: PairReviewerBlindingAttestation
    recorded_at: Annotated[str, Field(pattern=_TIMESTAMP)]
    decisions: tuple[PairReviewDecision, ...]

    @field_validator("decisions", mode="before")
    @classmethod
    def parse_decisions(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "pair review receipt decisions")

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: str) -> str:
        return _utc_timestamp(value, "pair review receipt recorded_at")

    @model_validator(mode="after")
    def validate_receipt_payload(self) -> _PairReviewReceiptPayload:
        if self.blinding.attested_at > self.recorded_at:
            raise ValueError("pair review receipt predates its blinding attestation")
        pair_ids = tuple(decision.pair_id for decision in self.decisions)
        if not pair_ids or pair_ids != tuple(sorted(set(pair_ids))):
            raise ValueError("pair review receipt decisions must be sorted and unique")
        return self


class NearDuplicatePairReviewReceiptV1(_PairReviewReceiptPayload):
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]

    @model_validator(mode="after")
    def validate_receipt_id(self) -> NearDuplicatePairReviewReceiptV1:
        expected = canonical_hash(
            "tag-truth-nd-pair-receipt",
            _identity_payload(self, "receipt_id"),
        )
        if self.receipt_id != expected:
            raise ValueError("pair review receipt ID does not match its contents")
        return self


def pair_review_receipt_payload_with_id(
    payload: Mapping[str, object],
) -> dict[str, object]:
    if "receipt_id" in payload:
        raise ValueError("unsealed pair review receipt cannot contain receipt_id")
    canonical = _PairReviewReceiptPayload.model_validate_json(canonical_json(dict(payload)))
    result = canonical.model_dump(mode="json")
    result["receipt_id"] = canonical_hash("tag-truth-nd-pair-receipt", result)
    return result


def seal_near_duplicate_pair_review_receipt_payload(
    payload: Mapping[str, object],
) -> NearDuplicatePairReviewReceiptV1:
    return NearDuplicatePairReviewReceiptV1.model_validate_json(
        canonical_json(pair_review_receipt_payload_with_id(payload))
    )


def validate_near_duplicate_pair_review_receipt(
    receipt: NearDuplicatePairReviewReceiptV1,
    packet: NearDuplicatePairReviewPacketV1,
) -> None:
    packet = NearDuplicatePairReviewPacketV1.model_validate_json(
        canonical_json(packet.model_dump(mode="json"))
    )
    receipt = NearDuplicatePairReviewReceiptV1.model_validate_json(
        canonical_json(receipt.model_dump(mode="json"))
    )
    expected_binding = (
        packet.selection_id,
        packet.packet_id,
        packet.suite_id,
        packet.review_policy.policy_fingerprint,
    )
    actual_binding = (
        receipt.selection_id,
        receipt.packet_id,
        receipt.suite_id,
        receipt.review_policy_fingerprint,
    )
    if actual_binding != expected_binding:
        raise ValueError("pair review receipt differs from the review packet")
    cases_by_id = {case.pair_id: case for case in packet.cases}
    decision_ids = {decision.pair_id for decision in receipt.decisions}
    if decision_ids != set(cases_by_id):
        missing = sorted(set(cases_by_id) - decision_ids)
        extra = sorted(decision_ids - set(cases_by_id))
        raise ValueError(f"pair review receipt coverage mismatch: missing={missing}, extra={extra}")
    for decision in receipt.decisions:
        case = cases_by_id[decision.pair_id]
        side_a, side_b = case.sides
        if any(line > side_a.line_count for line in decision.side_a_evidence_lines):
            raise ValueError("pair review side-a evidence exceeds the review text")
        if any(line > side_b.line_count for line in decision.side_b_evidence_lines):
            raise ValueError("pair review side-b evidence exceeds the review text")


class PairReceiptReference(_FrozenModel):
    round_id: Annotated[str, Field(pattern=_ROUND_ID)]
    reviewer_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]


class PairConsensusVote(_FrozenModel):
    round_id: Annotated[str, Field(pattern=_ROUND_ID)]
    reviewer_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]
    label: PairTruthLabel
    side_a_evidence_lines: tuple[Annotated[int, Field(ge=1)], ...]
    side_b_evidence_lines: tuple[Annotated[int, Field(ge=1)], ...]
    rationale: Annotated[str, Field(min_length=1)]
    ambiguity_reason: Annotated[str, Field(pattern=_SLUG)] | None = None

    @field_validator(
        "side_a_evidence_lines",
        "side_b_evidence_lines",
        mode="before",
    )
    @classmethod
    def parse_evidence(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"pair consensus vote {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_vote(self) -> PairConsensusVote:
        PairReviewDecision(
            pair_id="tag-truth-nd-pair:sha256:" + "0" * 64,
            label=self.label,
            side_a_evidence_lines=self.side_a_evidence_lines,
            side_b_evidence_lines=self.side_b_evidence_lines,
            rationale=self.rationale,
            ambiguity_reason=self.ambiguity_reason,
        )
        return self


class PairConsensusCase(_FrozenModel):
    pair_id: Annotated[str, Field(pattern=_PAIR_ID)]
    votes: tuple[PairConsensusVote, ...]
    consensus_status: PairConsensusCaseStatus
    label: PairTruthLabel | None = None
    metric_role: PairMetricRole
    side_a_evidence_lines: tuple[Annotated[int, Field(ge=1)], ...] = ()
    side_b_evidence_lines: tuple[Annotated[int, Field(ge=1)], ...] = ()
    ambiguity_reasons: tuple[Annotated[str, Field(pattern=_SLUG)], ...] = ()

    @field_validator(
        "votes",
        "side_a_evidence_lines",
        "side_b_evidence_lines",
        "ambiguity_reasons",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"pair consensus case {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_consensus_case(self) -> PairConsensusCase:
        vote_keys = tuple((vote.round_id, vote.reviewer_id, vote.receipt_id) for vote in self.votes)
        if len(vote_keys) != 2 or vote_keys != tuple(sorted(set(vote_keys))):
            raise ValueError("pair consensus case requires two sorted unique votes")
        labels = tuple(vote.label for vote in self.votes)
        if labels[0] != labels[1]:
            expected_status: PairConsensusCaseStatus = "unresolved"
            expected_label = None
            expected_role: PairMetricRole = "excluded"
            expected_a: tuple[int, ...] = ()
            expected_b: tuple[int, ...] = ()
            expected_reasons: tuple[str, ...] = ()
        else:
            expected_label = labels[0]
            expected_status = (
                "agreed_ambiguous" if expected_label == "ambiguous" else "agreed_resolved"
            )
            expected_role = "ambiguous_guard" if expected_label == "ambiguous" else "binary"
            expected_a = tuple(
                sorted({line for vote in self.votes for line in vote.side_a_evidence_lines})
            )
            expected_b = tuple(
                sorted({line for vote in self.votes for line in vote.side_b_evidence_lines})
            )
            expected_reasons = tuple(
                sorted(
                    {reason for vote in self.votes if (reason := vote.ambiguity_reason) is not None}
                )
            )
        if (
            self.consensus_status != expected_status
            or self.label != expected_label
            or self.metric_role != expected_role
            or self.side_a_evidence_lines != expected_a
            or self.side_b_evidence_lines != expected_b
            or self.ambiguity_reasons != expected_reasons
        ):
            raise ValueError("pair consensus case does not match its preserved votes")
        return self


class _NearDuplicatePairConsensusPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-nd-pair-consensus-v1"]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    suite_id: Annotated[str, Field(pattern=_IDENTITY_ID)]
    review_policy_fingerprint: Annotated[str, Field(pattern=_REVIEW_POLICY_FINGERPRINT)]
    receipt_references: tuple[PairReceiptReference, ...]
    cases: tuple[PairConsensusCase, ...]
    consensus_status: Literal["complete", "unresolved"]
    consensus_blockers: tuple[Literal["unresolved_pair_review_disagreement"], ...]

    @field_validator(
        "receipt_references",
        "cases",
        "consensus_blockers",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"pair consensus {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_consensus_payload(self) -> _NearDuplicatePairConsensusPayload:
        reference_keys = tuple(
            (item.round_id, item.reviewer_id, item.receipt_id) for item in self.receipt_references
        )
        if len(reference_keys) != 2 or reference_keys != tuple(sorted(set(reference_keys))):
            raise ValueError("pair consensus requires two sorted unique receipt references")
        for attribute, context in (
            ("round_id", "rounds"),
            ("reviewer_id", "reviewers"),
            ("receipt_id", "receipts"),
        ):
            values = tuple(getattr(item, attribute) for item in self.receipt_references)
            if len(values) != len(set(values)):
                raise ValueError(f"pair consensus requires distinct {context}")
        pair_ids = tuple(case.pair_id for case in self.cases)
        if not pair_ids or pair_ids != tuple(sorted(set(pair_ids))):
            raise ValueError("pair consensus cases must be sorted and unique")
        for case in self.cases:
            case_keys = tuple(
                (vote.round_id, vote.reviewer_id, vote.receipt_id) for vote in case.votes
            )
            if case_keys != reference_keys:
                raise ValueError("pair consensus votes differ from receipt references")
        unresolved = any(case.consensus_status == "unresolved" for case in self.cases)
        expected_blockers = ("unresolved_pair_review_disagreement",) if unresolved else ()
        expected_status = "unresolved" if unresolved else "complete"
        if self.consensus_blockers != expected_blockers or self.consensus_status != expected_status:
            raise ValueError("pair consensus aggregate status does not match cases")
        return self


class NearDuplicatePairConsensusV1(_NearDuplicatePairConsensusPayload):
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID)]

    @model_validator(mode="after")
    def validate_consensus_id(self) -> NearDuplicatePairConsensusV1:
        expected = canonical_hash(
            "tag-truth-nd-pair-consensus",
            _identity_payload(self, "consensus_id"),
        )
        if self.consensus_id != expected:
            raise ValueError("pair consensus ID does not match its complete contents")
        return self


def _consensus_vote(
    receipt: NearDuplicatePairReviewReceiptV1,
    decision: PairReviewDecision,
) -> PairConsensusVote:
    return PairConsensusVote(
        round_id=receipt.round_id,
        reviewer_id=receipt.reviewer.reviewer_id,
        receipt_id=receipt.receipt_id,
        label=decision.label,
        side_a_evidence_lines=decision.side_a_evidence_lines,
        side_b_evidence_lines=decision.side_b_evidence_lines,
        rationale=decision.rationale,
        ambiguity_reason=decision.ambiguity_reason,
    )


def build_near_duplicate_pair_consensus(
    packet: NearDuplicatePairReviewPacketV1,
    receipts: Sequence[NearDuplicatePairReviewReceiptV1],
) -> NearDuplicatePairConsensusV1:
    packet = NearDuplicatePairReviewPacketV1.model_validate_json(
        canonical_json(packet.model_dump(mode="json"))
    )
    if len(receipts) != 2:
        raise ValueError("pair consensus requires exactly two review receipts")
    ordered = tuple(
        sorted(
            (
                NearDuplicatePairReviewReceiptV1.model_validate_json(
                    canonical_json(receipt.model_dump(mode="json"))
                )
                for receipt in receipts
            ),
            key=lambda item: (
                item.round_id,
                item.reviewer.reviewer_id,
                item.receipt_id,
            ),
        )
    )
    if len({receipt.round_id for receipt in ordered}) != 2:
        raise ValueError("pair consensus requires distinct review rounds")
    if len({receipt.reviewer.reviewer_id for receipt in ordered}) != 2:
        raise ValueError("pair consensus requires distinct reviewers")
    if len({receipt.receipt_id for receipt in ordered}) != 2:
        raise ValueError("pair consensus requires distinct receipt IDs")
    for receipt in ordered:
        validate_near_duplicate_pair_review_receipt(receipt, packet)
    decisions = tuple(
        {decision.pair_id: decision for decision in receipt.decisions} for receipt in ordered
    )
    cases: list[PairConsensusCase] = []
    for packet_case in packet.cases:
        votes = tuple(
            _consensus_vote(receipt, by_pair[packet_case.pair_id])
            for receipt, by_pair in zip(ordered, decisions, strict=True)
        )
        labels = tuple(vote.label for vote in votes)
        if labels[0] == labels[1]:
            label: PairTruthLabel | None = labels[0]
            status: PairConsensusCaseStatus = (
                "agreed_ambiguous" if label == "ambiguous" else "agreed_resolved"
            )
            role: PairMetricRole = "ambiguous_guard" if label == "ambiguous" else "binary"
            evidence_a = tuple(
                sorted({line for vote in votes for line in vote.side_a_evidence_lines})
            )
            evidence_b = tuple(
                sorted({line for vote in votes for line in vote.side_b_evidence_lines})
            )
            reasons = tuple(
                sorted({reason for vote in votes if (reason := vote.ambiguity_reason) is not None})
            )
        else:
            label = None
            status = "unresolved"
            role = "excluded"
            evidence_a = ()
            evidence_b = ()
            reasons = ()
        cases.append(
            PairConsensusCase(
                pair_id=packet_case.pair_id,
                votes=votes,
                consensus_status=status,
                label=label,
                metric_role=role,
                side_a_evidence_lines=evidence_a,
                side_b_evidence_lines=evidence_b,
                ambiguity_reasons=reasons,
            )
        )
    unresolved = any(case.consensus_status == "unresolved" for case in cases)
    payload: dict[str, object] = {
        "schema_version": PAIR_CONSENSUS_SCHEMA_VERSION,
        "selection_id": packet.selection_id,
        "packet_id": packet.packet_id,
        "suite_id": packet.suite_id,
        "review_policy_fingerprint": packet.review_policy.policy_fingerprint,
        "receipt_references": [
            {
                "round_id": receipt.round_id,
                "reviewer_id": receipt.reviewer.reviewer_id,
                "receipt_id": receipt.receipt_id,
            }
            for receipt in ordered
        ],
        "cases": [case.model_dump(mode="json") for case in cases],
        "consensus_status": "unresolved" if unresolved else "complete",
        "consensus_blockers": (["unresolved_pair_review_disagreement"] if unresolved else []),
    }
    payload["consensus_id"] = canonical_hash("tag-truth-nd-pair-consensus", payload)
    return NearDuplicatePairConsensusV1.model_validate_json(canonical_json(payload))


def verify_near_duplicate_pair_consensus(
    consensus: NearDuplicatePairConsensusV1,
    packet: NearDuplicatePairReviewPacketV1,
    receipts: Sequence[NearDuplicatePairReviewReceiptV1],
) -> None:
    canonical_consensus = NearDuplicatePairConsensusV1.model_validate_json(
        canonical_json(consensus.model_dump(mode="json"))
    )
    rebuilt = build_near_duplicate_pair_consensus(packet, receipts)
    if canonical_consensus != rebuilt:
        raise ValueError("pair consensus does not rebuild from packet and receipts")


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


def parse_near_duplicate_pair_selection(raw: bytes) -> NearDuplicatePairSelectionV1:
    return _parse_json_model(raw, NearDuplicatePairSelectionV1, "near-duplicate pair selection")


def load_near_duplicate_pair_selection(path: str | Path) -> NearDuplicatePairSelectionV1:
    return _load_json_model(
        path,
        NearDuplicatePairSelectionV1,
        "near-duplicate pair selection",
    )


def parse_near_duplicate_pair_review_packet(
    raw: bytes,
) -> NearDuplicatePairReviewPacketV1:
    return _parse_json_model(raw, NearDuplicatePairReviewPacketV1, "pair review packet")


def load_near_duplicate_pair_review_packet(
    path: str | Path,
) -> NearDuplicatePairReviewPacketV1:
    return _load_json_model(path, NearDuplicatePairReviewPacketV1, "pair review packet")


def parse_near_duplicate_pair_review_receipt(
    raw: bytes,
) -> NearDuplicatePairReviewReceiptV1:
    return _parse_json_model(raw, NearDuplicatePairReviewReceiptV1, "pair review receipt")


def load_near_duplicate_pair_review_receipt(
    path: str | Path,
) -> NearDuplicatePairReviewReceiptV1:
    return _load_json_model(path, NearDuplicatePairReviewReceiptV1, "pair review receipt")


def parse_near_duplicate_pair_consensus(raw: bytes) -> NearDuplicatePairConsensusV1:
    return _parse_json_model(raw, NearDuplicatePairConsensusV1, "pair consensus")


def load_near_duplicate_pair_consensus(path: str | Path) -> NearDuplicatePairConsensusV1:
    return _load_json_model(path, NearDuplicatePairConsensusV1, "pair consensus")


__all__ = [
    "NearDuplicatePairConsensusV1",
    "NearDuplicatePairMember",
    "NearDuplicatePairReviewPacketV1",
    "NearDuplicatePairReviewPolicy",
    "NearDuplicatePairReviewReceiptV1",
    "NearDuplicatePairSelectionCase",
    "NearDuplicatePairSelectionProcess",
    "NearDuplicatePairSelectionV1",
    "PAIR_CONSENSUS_SCHEMA_VERSION",
    "PAIR_REVIEW_PACKET_SCHEMA_VERSION",
    "PAIR_REVIEW_RECEIPT_SCHEMA_VERSION",
    "PAIR_SELECTION_SCHEMA_VERSION",
    "PairAxis",
    "PairConsensusCase",
    "PairConsensusCaseStatus",
    "PairConsensusVote",
    "PairDirection",
    "PairMetricRole",
    "PairReceiptReference",
    "PairReviewDecision",
    "PairReviewPacketCase",
    "PairReviewPacketSide",
    "PairReviewerBlindingAttestation",
    "PairReviewerIdentity",
    "PairSplit",
    "PairTruthLabel",
    "build_near_duplicate_pair_consensus",
    "build_near_duplicate_pair_review_packet",
    "default_near_duplicate_pair_review_policy",
    "load_near_duplicate_pair_consensus",
    "load_near_duplicate_pair_review_packet",
    "load_near_duplicate_pair_review_receipt",
    "load_near_duplicate_pair_selection",
    "normalized_pair_body_sha256",
    "pair_member_payload_with_id",
    "pair_review_policy_payload_with_fingerprint",
    "pair_review_receipt_payload_with_id",
    "pair_selection_process_payload_with_fingerprint",
    "pair_template_cluster_id",
    "parse_near_duplicate_pair_consensus",
    "parse_near_duplicate_pair_review_packet",
    "parse_near_duplicate_pair_review_receipt",
    "parse_near_duplicate_pair_selection",
    "seal_near_duplicate_pair_review_receipt_payload",
    "seal_near_duplicate_pair_selection_payload",
    "validate_near_duplicate_pair_review_receipt",
    "verify_near_duplicate_pair_consensus",
    "verify_near_duplicate_pair_review_packet",
]
