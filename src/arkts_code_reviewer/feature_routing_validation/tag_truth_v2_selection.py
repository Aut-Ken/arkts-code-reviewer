from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    TagContractSnapshot,
    TagTruthV2Repository,
    TagTruthV2Source,
    bytes_hash,
    canonical_hash,
    canonical_json,
    derive_source_family_id,
)

TAG_TRUTH_V2_SELECTION_SCHEMA_VERSION = "tag-truth-v2-selection-v1"
TAG_TRUTH_V2_REVIEW_PACKET_SCHEMA_VERSION = "tag-truth-v2-review-packet-v1"
TAG_TRUTH_V2_CONSTRUCTIBILITY_SCHEMA_VERSION = "tag-truth-v2-constructibility-v1"

_GIT_REVISION = r"^[0-9a-f]{40}$"
_SHA256 = r"^sha256:[0-9a-f]{64}$"
_CASE_ID = r"^case-[0-9a-f]{16}$"
_TAG_CONTRACT_FINGERPRINT = r"^tag-contract-snapshot:sha256:[0-9a-f]{64}$"
_FEATURE_CONFIG_FINGERPRINT = r"^feature-config:sha256:[0-9a-f]{64}$"
_CANDIDATE_FREEZE_ID = r"^tag-truth-candidate-freeze:sha256:[0-9a-f]{64}$"
_EXCLUSIONS_ID = r"^tag-truth-development-exclusions:sha256:[0-9a-f]{64}$"
_REVIEW_POLICY_ID = r"^tag-truth-review-policy:sha256:[0-9a-f]{64}$"
_SELECTION_POLICY_ID = r"^tag-truth-selection-policy:sha256:[0-9a-f]{64}$"
_SELECTION_ID = r"^tag-truth-selection:sha256:[0-9a-f]{64}$"
_PACKET_ID = r"^tag-truth-review-packet:sha256:[0-9a-f]{64}$"
_CONSTRUCTIBILITY_ID = r"^tag-truth-constructibility:sha256:[0-9a-f]{64}$"

ConstructibilityReason = Literal[
    "selection_revision_equals_exposure_revision",
    "selection_revision_not_strict_descendant",
    "insufficient_verified_selectable_capacity",
]
SelectionQualificationReason = Literal[
    "candidate_runtime_verification_deferred",
    "external_selection_not_verified",
    "human_review_not_completed",
    "near_duplicate_verifier_unavailable",
    "review_policy_not_approved",
    "selection_policy_not_approved",
    "selector_identity_not_authenticated",
    "stage2a_selection_only",
]

_REQUIRED_SELECTION_REASONS: tuple[SelectionQualificationReason, ...] = (
    "candidate_runtime_verification_deferred",
    "external_selection_not_verified",
    "human_review_not_completed",
    "near_duplicate_verifier_unavailable",
    "review_policy_not_approved",
    "selection_policy_not_approved",
    "selector_identity_not_authenticated",
    "stage2a_selection_only",
)
_BANNED_PROXY_LABEL_TOKENS = ("positive", "negative", "exact", "routing", "truth")
_REGISTERED_CODE_SOURCE_ID = "applications-app-samples"
_REGISTERED_CODE_REPOSITORY = "applications_app_samples"
_REGISTERED_CODE_ORIGIN = "https://gitcode.com/openharmony/applications_app_samples.git"


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
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{context} must be a trimmed POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{context} must be relative and cannot traverse parents")
    if path.as_posix() != value:
        raise ValueError(f"{context} must be normalized")
    return value


def _path_scopes_overlap(left: str, right: str) -> bool:
    left_path = PurePosixPath(left)
    right_path = PurePosixPath(right)
    return left_path.is_relative_to(right_path) or right_path.is_relative_to(left_path)


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={identity_field})


def _is_eligible_main_source(path: str) -> bool:
    wrapped = f"/{path}/"
    return (
        path.endswith((".ets", ".ts"))
        and "/src/main/ets/" in wrapped
        and "DocsSample" not in path
        and "/ohosTest/" not in wrapped
    )


class CandidateFreezeReference(_FrozenModel):
    candidate_freeze_id: Annotated[str, Field(pattern=_CANDIDATE_FREEZE_ID)]
    candidate_commit: Annotated[str, Field(pattern=_GIT_REVISION)]
    target_tag_id: Annotated[str, Field(pattern=r"^has_[a-z0-9_]+$")]
    tag_contract_fingerprint: Annotated[str, Field(pattern=_TAG_CONTRACT_FINGERPRINT)]
    feature_config_fingerprint: Annotated[str, Field(pattern=_FEATURE_CONFIG_FINGERPRINT)]
    exposure_repository_source_id: Annotated[
        str,
        Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"),
    ]
    exposure_revision: Annotated[str, Field(pattern=_GIT_REVISION)]
    exposure_tree_id: Annotated[str, Field(pattern=_GIT_REVISION)]
    exposure_scope: Literal["entire_tracked_repository"]
    runtime_verification_status: Literal["deferred_to_candidate_runner"]

    @model_validator(mode="after")
    def validate_identity(self) -> CandidateFreezeReference:
        expected = canonical_hash(
            "tag-truth-candidate-freeze",
            _identity_payload(self, "candidate_freeze_id"),
        )
        if self.candidate_freeze_id != expected:
            raise ValueError("candidate_freeze_id does not match its complete reference")
        return self


class DevelopmentExclusions(_FrozenModel):
    exclusions_id: Annotated[str, Field(pattern=_EXCLUSIONS_ID)]
    truth_suite_fingerprint: Annotated[
        str,
        Field(pattern=r"^tag-retrieval-truth:sha256:[0-9a-f]{64}$"),
    ]
    source_family_ids: tuple[str, ...]
    source_paths: tuple[str, ...]
    content_sha256: tuple[Annotated[str, Field(pattern=_SHA256)], ...]

    @field_validator("source_family_ids", "source_paths", "content_sha256", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"development exclusions {getattr(info, 'field_name', '')}")

    @field_validator("source_family_ids", "source_paths", "content_sha256")
    @classmethod
    def validate_sequences(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "values")
        normalized = _sorted_unique(value, f"development exclusions {field_name}")
        if field_name in {"source_family_ids", "source_paths"}:
            for item in normalized:
                _relative_path(item, f"development exclusions {field_name}")
        return normalized

    @model_validator(mode="after")
    def validate_identity(self) -> DevelopmentExclusions:
        expected = canonical_hash(
            "tag-truth-development-exclusions",
            _identity_payload(self, "exclusions_id"),
        )
        if self.exclusions_id != expected:
            raise ValueError("development exclusions ID does not match its fields")
        return self


@dataclass(frozen=True)
class DevelopmentTruthExclusionSource:
    source_family_id: str
    path: str
    content_sha256: str


@dataclass(frozen=True)
class DevelopmentTruthExclusionSnapshot:
    truth_suite_fingerprint: str
    repository_source_id: str
    repository_name: str
    repository_origin: str
    repository_revision: str
    sources: tuple[DevelopmentTruthExclusionSource, ...]


def _validate_registered_code_source(snapshot: DevelopmentTruthExclusionSnapshot) -> None:
    identity = (
        snapshot.repository_source_id,
        snapshot.repository_name,
        snapshot.repository_origin,
    )
    expected = (
        _REGISTERED_CODE_SOURCE_ID,
        _REGISTERED_CODE_REPOSITORY,
        _REGISTERED_CODE_ORIGIN,
    )
    if identity != expected:
        raise ValueError("development Truth does not use the frozen registered code source")


class ReviewPolicySnapshot(_FrozenModel):
    schema_version: Literal["tag-truth-review-policy-v1"]
    policy_fingerprint: Annotated[str, Field(pattern=_REVIEW_POLICY_ID)]
    version: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")]
    approval_status: Literal["draft_not_approved"]
    owner_instruction: Annotated[str, Field(min_length=1)]
    exact_instruction: Annotated[str, Field(min_length=1)]
    routing_instruction: Annotated[str, Field(min_length=1)]
    abstain_instruction: Annotated[str, Field(min_length=1)]

    @field_validator(
        "owner_instruction",
        "exact_instruction",
        "routing_instruction",
        "abstain_instruction",
    )
    @classmethod
    def validate_instructions(cls, value: str, info: object) -> str:
        if value != value.strip() or not value:
            raise ValueError(
                f"review policy {getattr(info, 'field_name', 'instruction')} must be trimmed"
            )
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> ReviewPolicySnapshot:
        expected = canonical_hash(
            "tag-truth-review-policy",
            _identity_payload(self, "policy_fingerprint"),
        )
        if self.policy_fingerprint != expected:
            raise ValueError("review policy fingerprint does not match its fields")
        return self


class ProxyStratumPolicy(_FrozenModel):
    stratum_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    selected_case_count: Annotated[int, Field(ge=1)]

    @field_validator("stratum_id")
    @classmethod
    def reject_truth_like_name(cls, value: str) -> str:
        if any(token in value for token in _BANNED_PROXY_LABEL_TOKENS):
            raise ValueError("proxy stratum IDs cannot encode Truth or routing labels")
        return value


class PostReviewClassMinimums(_FrozenModel):
    minimum_exact_positive_cases: Annotated[int, Field(ge=1)]
    minimum_exact_negative_cases: Annotated[int, Field(ge=1)]
    minimum_routing_positive_cases: Annotated[int, Field(ge=1)]
    minimum_routing_negative_cases: Annotated[int, Field(ge=1)]


class SelectionPolicy(_FrozenModel):
    schema_version: Literal["tag-truth-selection-policy-v1"]
    policy_fingerprint: Annotated[str, Field(pattern=_SELECTION_POLICY_ID)]
    policy_version: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")]
    approval_status: Literal["draft_not_approved"]
    dataset_kind: Literal["purposive_proxy_stratified_challenge"]
    natural_prevalence_claimed: Literal[False]
    near_duplicate_check_status: Literal["not_qualified"]
    max_cases_per_source_family: Literal[1]
    selected_case_count: Annotated[int, Field(ge=1)]
    strata: tuple[ProxyStratumPolicy, ...]
    post_review_minimums: PostReviewClassMinimums

    @field_validator("strata", mode="before")
    @classmethod
    def parse_strata(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "selection policy strata")

    @model_validator(mode="after")
    def validate_policy(self) -> SelectionPolicy:
        ids = tuple(item.stratum_id for item in self.strata)
        if not ids or ids != tuple(sorted(set(ids))):
            raise ValueError("selection policy strata must be sorted, unique, and non-empty")
        if sum(item.selected_case_count for item in self.strata) != self.selected_case_count:
            raise ValueError("selection policy stratum counts must equal selected_case_count")
        minimums = self.post_review_minimums
        if (
            minimums.minimum_exact_positive_cases + minimums.minimum_exact_negative_cases
            > self.selected_case_count
            or minimums.minimum_routing_positive_cases + minimums.minimum_routing_negative_cases
            > self.selected_case_count
        ):
            raise ValueError("post-review class minimums exceed the selected case count")
        expected = canonical_hash(
            "tag-truth-selection-policy",
            _identity_payload(self, "policy_fingerprint"),
        )
        if self.policy_fingerprint != expected:
            raise ValueError("selection policy fingerprint does not match its fields")
        return self


class SelectorAttestation(_FrozenModel):
    selector_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    selector_role: Literal["independent_dataset_custodian"]
    candidate_design_participant: Literal[False]
    candidate_configuration_seen: Literal[False]
    candidate_output_seen: Literal[False]
    selected_after_candidate_freeze: Literal[True]
    attested_on: Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
    process_note: Annotated[str, Field(min_length=1)]

    @field_validator("process_note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        return _single_line(value, "selector process note")


class SelectionCase(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    source_alias: Annotated[str, Field(pattern=r"^src[0-9]{3,6}$")]
    probe_line: Annotated[int, Field(ge=1)]
    proxy_stratum_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    selection_rank: Annotated[int, Field(ge=1)]


class TagTruthV2Selection(_FrozenModel):
    schema_version: Literal["tag-truth-v2-selection-v1"]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    suite_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    dataset_role: Literal["independent_blind_challenge"]
    data_qualification_status: Literal["not_qualified"]
    data_qualification_reasons: tuple[SelectionQualificationReason, ...]
    repository: TagTruthV2Repository
    candidate_freeze: CandidateFreezeReference
    development_exclusions: DevelopmentExclusions
    selection_policy: SelectionPolicy
    selector_attestation: SelectorAttestation
    tag_contract: TagContractSnapshot
    review_policy: ReviewPolicySnapshot
    sources: tuple[TagTruthV2Source, ...]
    cases: tuple[SelectionCase, ...]

    @field_validator("data_qualification_reasons", "sources", "cases", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"selection {getattr(info, 'field_name', 'sequence')}")

    @field_validator("data_qualification_reasons")
    @classmethod
    def validate_reasons(
        cls,
        value: tuple[SelectionQualificationReason, ...],
    ) -> tuple[SelectionQualificationReason, ...]:
        if value != tuple(sorted(_REQUIRED_SELECTION_REASONS)):
            raise ValueError("selection must retain every Stage-2A not-qualified reason")
        return value

    @model_validator(mode="after")
    def validate_selection(self) -> TagTruthV2Selection:
        self._validate_bindings()
        self._validate_sources_and_cases()
        expected = canonical_hash(
            "tag-truth-selection",
            _identity_payload(self, "selection_id"),
        )
        if self.selection_id != expected:
            raise ValueError("selection_id does not match the complete selection")
        return self

    def _validate_bindings(self) -> None:
        freeze = self.candidate_freeze
        if freeze.target_tag_id != self.tag_contract.tag_id:
            raise ValueError("candidate freeze target Tag differs from selection contract")
        if freeze.tag_contract_fingerprint != self.tag_contract.contract_fingerprint:
            raise ValueError("candidate freeze does not bind the selection Tag contract")
        if freeze.exposure_repository_source_id != self.repository.source_id:
            raise ValueError("candidate exposure source differs from selection repository")

    def _validate_sources_and_cases(self) -> None:
        aliases = tuple(source.alias for source in self.sources)
        paths = tuple(source.path for source in self.sources)
        hashes = tuple(source.content_sha256 for source in self.sources)
        families = tuple(source.source_family_id for source in self.sources)
        case_ids = tuple(case.case_id for case in self.cases)
        if not aliases or aliases != tuple(sorted(set(aliases))):
            raise ValueError("selection sources must be sorted by unique alias")
        if len(paths) != len(set(paths)) or len(hashes) != len(set(hashes)):
            raise ValueError("selection source paths and content hashes must be unique")
        if len(families) != len(set(families)):
            raise ValueError("selection requires exactly one source family per case")
        for index, family in enumerate(sorted(families)):
            for other in sorted(families)[index + 1 :]:
                if _path_scopes_overlap(family, other):
                    raise ValueError("selection source families cannot overlap by ancestry")
        if not case_ids or case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("selection cases must be sorted by unique opaque case ID")
        ranks = tuple(case.selection_rank for case in self.cases)
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("selection ranks must be contiguous from one")
        if len(self.cases) != self.selection_policy.selected_case_count:
            raise ValueError("selection case count differs from its policy")

        sources_by_alias = {source.alias: source for source in self.sources}
        reference_counts = Counter(case.source_alias for case in self.cases)
        if set(reference_counts) != set(sources_by_alias) or any(
            count != 1 for count in reference_counts.values()
        ):
            raise ValueError("selection requires exactly one case for every source")
        allowed_strata = {item.stratum_id for item in self.selection_policy.strata}
        actual_strata = Counter(case.proxy_stratum_id for case in self.cases)
        expected_strata = {
            item.stratum_id: item.selected_case_count for item in self.selection_policy.strata
        }
        if (
            set(actual_strata) - allowed_strata
            or dict(sorted(actual_strata.items())) != expected_strata
        ):
            raise ValueError("selection proxy strata differ from the frozen policy")
        for source in self.sources:
            _relative_path(source.path, "selection source path")
            _relative_path(source.source_family_id, "selection source family")
            if (
                source.repository_source_id != self.repository.source_id
                or source.origin != self.repository.origin
                or source.revision != self.repository.revision
            ):
                raise ValueError("selection source repository binding drift")
            if source.source_kind != "main" or not _is_eligible_main_source(source.path):
                raise ValueError("selection sources must be non-DocsSample src/main code")
        for case in self.cases:
            source = sources_by_alias[case.source_alias]
            if case.probe_line > source.line_count:
                raise ValueError(f"selection probe line exceeds source: {case.case_id}")

        excluded = self.development_exclusions
        if set(paths).intersection(excluded.source_paths):
            raise ValueError("selection source path overlaps development Truth")
        if set(hashes).intersection(excluded.content_sha256):
            raise ValueError("selection source content overlaps development Truth")
        if any(
            _path_scopes_overlap(family, excluded_family)
            for family in families
            for excluded_family in excluded.source_family_ids
        ):
            raise ValueError("selection source family overlaps development Truth")


class TagTruthV2ConstructibilityReport(_FrozenModel):
    schema_version: Literal["tag-truth-v2-constructibility-v1"]
    report_id: Annotated[str, Field(pattern=_CONSTRUCTIBILITY_ID)]
    repository_source_id: Annotated[
        str,
        Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"),
    ]
    repository: Annotated[str, Field(min_length=1)]
    origin: Annotated[str, Field(min_length=1)]
    exposure_revision: Annotated[str, Field(pattern=_GIT_REVISION)]
    exposure_tree_id: Annotated[str, Field(pattern=_GIT_REVISION)]
    selection_revision: Annotated[str, Field(pattern=_GIT_REVISION)]
    selection_tree_id: Annotated[str, Field(pattern=_GIT_REVISION)]
    candidate_freeze_id: Annotated[str, Field(pattern=_CANDIDATE_FREEZE_ID)]
    selection_policy_fingerprint: Annotated[str, Field(pattern=_SELECTION_POLICY_ID)]
    required_case_count: Annotated[int, Field(ge=1)]
    strict_descendant: bool
    new_eligible_source_count: Annotated[int, Field(ge=0)]
    new_eligible_family_count: Annotated[int, Field(ge=0)]
    verified_selectable_case_lower_bound: Annotated[int, Field(ge=0)]
    verified_selectable_capacity_satisfied: bool
    selection_constructibility_status: Literal["not_constructible", "inventory_capacity_only"]
    proxy_strata_capacity_status: Literal["not_measured"]
    reasons: tuple[ConstructibilityReason, ...]
    interpretation: Literal["verified_structural_lower_bound_not_stratum_or_truth"]

    @field_validator("repository", "origin")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        return _single_line(value, f"constructibility {getattr(info, 'field_name', 'identity')}")

    @field_validator("reasons", mode="before")
    @classmethod
    def parse_reasons(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "constructibility reasons")

    @field_validator("reasons")
    @classmethod
    def validate_reasons(
        cls,
        value: tuple[ConstructibilityReason, ...],
    ) -> tuple[ConstructibilityReason, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("constructibility reasons must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_report(self) -> TagTruthV2ConstructibilityReport:
        if self.exposure_revision == self.selection_revision and self.strict_descendant:
            raise ValueError("a revision cannot be its own strict descendant")
        if not self.strict_descendant and (
            self.new_eligible_source_count != 0
            or self.new_eligible_family_count != 0
            or self.verified_selectable_case_lower_bound != 0
        ):
            raise ValueError("non-descendant reports cannot claim new eligible inventory")
        if self.verified_selectable_case_lower_bound > min(
            self.new_eligible_source_count,
            self.new_eligible_family_count,
        ):
            raise ValueError("selectable lower bound cannot exceed raw eligible inventory")
        expected_reasons: tuple[ConstructibilityReason, ...]
        if self.exposure_revision == self.selection_revision:
            expected_reasons = ("selection_revision_equals_exposure_revision",)
        elif not self.strict_descendant:
            expected_reasons = ("selection_revision_not_strict_descendant",)
        else:
            capacity_reasons: list[ConstructibilityReason] = []
            if self.verified_selectable_case_lower_bound < self.required_case_count:
                capacity_reasons.append("insufficient_verified_selectable_capacity")
            expected_reasons = tuple(sorted(capacity_reasons))
        if self.reasons != expected_reasons:
            raise ValueError("constructibility reasons do not match report evidence")
        expected_capacity = (
            self.strict_descendant
            and self.verified_selectable_case_lower_bound >= self.required_case_count
            and not self.reasons
        )
        if self.verified_selectable_capacity_satisfied != expected_capacity:
            raise ValueError("verified selectable capacity status does not match its evidence")
        expected_status = "inventory_capacity_only" if expected_capacity else "not_constructible"
        if self.selection_constructibility_status != expected_status:
            raise ValueError("selection constructibility status does not match its evidence")
        expected = canonical_hash(
            "tag-truth-constructibility",
            _identity_payload(self, "report_id"),
        )
        if self.report_id != expected:
            raise ValueError("constructibility report ID does not match its fields")
        return self


class BlindReviewCase(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    review_source_id: Annotated[str, Field(pattern=r"^review-case-[0-9a-f]{16}$")]
    probe_line: Annotated[int, Field(ge=1)]
    source_text: Annotated[str, Field(min_length=1)]
    line_count: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_source(self) -> BlindReviewCase:
        if self.review_source_id != f"review-{self.case_id}":
            raise ValueError("review_source_id must derive only from the opaque case ID")
        raw = self.source_text.encode("utf-8")
        if len(raw.splitlines()) != self.line_count:
            raise ValueError("blind review source line count does not match source_text")
        if self.probe_line > self.line_count:
            raise ValueError("blind review probe line exceeds source_text")
        return self


class TagTruthV2ReviewPacket(_FrozenModel):
    schema_version: Literal["tag-truth-v2-review-packet-v1"]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    suite_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    target_tag_id: Annotated[str, Field(pattern=r"^has_[a-z0-9_]+$")]
    tag_contract: TagContractSnapshot
    review_policy: ReviewPolicySnapshot
    cases: tuple[BlindReviewCase, ...]

    @field_validator("cases", mode="before")
    @classmethod
    def parse_cases(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "blind review packet cases")

    @model_validator(mode="after")
    def validate_packet(self) -> TagTruthV2ReviewPacket:
        case_ids = tuple(case.case_id for case in self.cases)
        if not case_ids or case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("blind review packet cases must be sorted and unique")
        if self.target_tag_id != self.tag_contract.tag_id:
            raise ValueError("blind review packet target Tag differs from its contract")
        expected = canonical_hash(
            "tag-truth-review-packet",
            _identity_payload(self, "packet_id"),
        )
        if self.packet_id != expected:
            raise ValueError("blind review packet ID does not match its fields")
        return self


@dataclass(frozen=True)
class VerifiedTagTruthV2Checkout:
    selection_id: str
    repository_revision: str
    root: Path
    source_text_by_alias: Mapping[str, str]


@dataclass(frozen=True)
class _GitTreeEntry:
    mode: str
    path: str
    object_id: str

    @property
    def is_regular_file(self) -> bool:
        return self.mode in {"100644", "100755"}


@dataclass(frozen=True)
class _StructurallySelectableSource:
    path: str
    source_family_id: str
    content_sha256: str


def _verified_selectable_capacity_lower_bound(
    candidates: tuple[_StructurallySelectableSource, ...],
) -> int:
    family_hashes: dict[str, set[str]] = {}
    for candidate in candidates:
        family_hashes.setdefault(candidate.source_family_id, set()).add(candidate.content_sha256)
    leaf_families = tuple(
        family
        for family in family_hashes
        if not any(
            other != family and PurePosixPath(other).is_relative_to(PurePosixPath(family))
            for other in family_hashes
        )
    )
    used_hashes: set[str] = set()
    selected_count = 0
    for family in sorted(leaf_families, key=lambda item: (len(family_hashes[item]), item)):
        available_hashes = sorted(family_hashes[family] - used_hashes)
        if not available_hashes:
            continue
        used_hashes.add(available_hashes[0])
        selected_count += 1
    return selected_count


def candidate_freeze_payload_with_id(payload: Mapping[str, object]) -> dict[str, object]:
    return _payload_with_identity(
        payload,
        "candidate_freeze_id",
        "tag-truth-candidate-freeze",
    )


def development_exclusions_payload_with_id(payload: Mapping[str, object]) -> dict[str, object]:
    return _payload_with_identity(
        payload,
        "exclusions_id",
        "tag-truth-development-exclusions",
    )


def review_policy_payload_with_id(payload: Mapping[str, object]) -> dict[str, object]:
    return _payload_with_identity(payload, "policy_fingerprint", "tag-truth-review-policy")


def selection_policy_payload_with_id(payload: Mapping[str, object]) -> dict[str, object]:
    return _payload_with_identity(payload, "policy_fingerprint", "tag-truth-selection-policy")


def parse_tag_truth_v2_candidate_freeze(raw: bytes) -> CandidateFreezeReference:
    return _parse_json_model(raw, CandidateFreezeReference, "Tag Truth v2 candidate freeze")


def load_tag_truth_v2_candidate_freeze(path: str | Path) -> CandidateFreezeReference:
    return _load_json_model(path, CandidateFreezeReference, "Tag Truth v2 candidate freeze")


def parse_tag_truth_v2_selection_policy(raw: bytes) -> SelectionPolicy:
    return _parse_json_model(raw, SelectionPolicy, "Tag Truth v2 selection policy")


def load_tag_truth_v2_selection_policy(path: str | Path) -> SelectionPolicy:
    return _load_json_model(path, SelectionPolicy, "Tag Truth v2 selection policy")


def selection_payload_with_id(payload: Mapping[str, object]) -> dict[str, object]:
    return _payload_with_identity(payload, "selection_id", "tag-truth-selection")


def _payload_with_identity(
    payload: Mapping[str, object],
    identity_field: str,
    prefix: str,
) -> dict[str, object]:
    if identity_field in payload:
        raise ValueError(f"draft payload cannot provide {identity_field}")
    result = dict(payload)
    result[identity_field] = canonical_hash(prefix, result)
    return result


def seal_tag_truth_v2_selection_payload(payload: Mapping[str, object]) -> TagTruthV2Selection:
    sealed = selection_payload_with_id(payload)
    return TagTruthV2Selection.model_validate_json(canonical_json(sealed))


def _canonical_selection(selection: TagTruthV2Selection) -> TagTruthV2Selection:
    return TagTruthV2Selection.model_validate_json(
        canonical_json(selection.model_dump(mode="json"))
    )


def load_tag_truth_v2_development_exclusion_snapshot(
    path: str | Path,
) -> DevelopmentTruthExclusionSnapshot:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(
            f"development Truth manifest must be a regular non-symlink file: {candidate}"
        )
    try:
        raw = candidate.read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid development Truth manifest {candidate}: {exc}") from exc
    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
        raise ValueError("development Truth manifest must be an object with string keys")
    if payload.get("schema_version") != "tag-retrieval-truth-v2":
        raise ValueError("unsupported development Truth schema_version")
    evaluation_boundary = payload.get("evaluation_boundary")
    if (
        not isinstance(evaluation_boundary, dict)
        or evaluation_boundary.get("dataset_role") != "development_regression"
    ):
        raise ValueError("development Truth manifest must declare development_regression")
    repository = payload.get("repository")
    if not isinstance(repository, dict) or any(not isinstance(key, str) for key in repository):
        raise ValueError("development Truth repository must be an object")
    repository_fields: dict[str, str] = {}
    for field_name in ("source_id", "repository", "remote", "revision"):
        value = repository.get(field_name)
        if not isinstance(value, str):
            raise ValueError(f"development Truth repository requires string {field_name}")
        repository_fields[field_name] = _single_line(
            value,
            f"development Truth repository {field_name}",
        )
    if re.fullmatch(_GIT_REVISION, repository_fields["revision"]) is None:
        raise ValueError("development Truth repository revision must be a full Git revision")
    raw_sources = _sequence(payload.get("sources"), "development Truth sources")
    sources: list[DevelopmentTruthExclusionSource] = []
    for index, raw_source in enumerate(raw_sources):
        if not isinstance(raw_source, dict) or any(not isinstance(key, str) for key in raw_source):
            raise ValueError(f"development Truth source {index} must be an object")
        values: dict[str, str] = {}
        for field_name in ("source_family_id", "path", "content_sha256"):
            value = raw_source.get(field_name)
            if not isinstance(value, str):
                raise ValueError(f"development Truth source {index} requires string {field_name}")
            values[field_name] = value
        source_path = _relative_path(values["path"], "development Truth source path")
        source_family_id = _relative_path(
            values["source_family_id"],
            "development Truth source family",
        )
        if derive_source_family_id(source_path) != source_family_id:
            raise ValueError("development Truth source family differs from its path")
        content_sha256 = values["content_sha256"]
        if re.fullmatch(_SHA256, content_sha256) is None:
            raise ValueError("development Truth source content_sha256 is invalid")
        sources.append(
            DevelopmentTruthExclusionSource(
                source_family_id=source_family_id,
                path=source_path,
                content_sha256=content_sha256,
            )
        )
    if not sources:
        raise ValueError("development Truth manifest must contain sources")
    paths = tuple(source.path for source in sources)
    if len(paths) != len(set(paths)):
        raise ValueError("development Truth source paths must be unique")
    snapshot = DevelopmentTruthExclusionSnapshot(
        truth_suite_fingerprint=canonical_hash("tag-retrieval-truth", payload),
        repository_source_id=repository_fields["source_id"],
        repository_name=repository_fields["repository"],
        repository_origin=repository_fields["remote"],
        repository_revision=repository_fields["revision"],
        sources=tuple(sources),
    )
    _validate_registered_code_source(snapshot)
    return snapshot


def verify_tag_truth_v2_development_exclusions(
    selection: TagTruthV2Selection,
    development_truth: DevelopmentTruthExclusionSnapshot,
    source_root: str | Path,
) -> None:
    selection = _canonical_selection(selection)
    _validate_registered_code_source(development_truth)
    exclusions = selection.development_exclusions
    if (
        selection.repository.source_id != development_truth.repository_source_id
        or selection.repository.repository != development_truth.repository_name
        or selection.repository.origin != development_truth.repository_origin
    ):
        raise ValueError("development Truth repository differs from selection repository")
    if exclusions.truth_suite_fingerprint != development_truth.truth_suite_fingerprint:
        raise ValueError("development Truth fingerprint does not match selection exclusions")
    expected_families = tuple(
        sorted({source.source_family_id for source in development_truth.sources})
    )
    expected_paths = tuple(sorted(source.path for source in development_truth.sources))
    expected_hashes = tuple(sorted({source.content_sha256 for source in development_truth.sources}))
    if exclusions.source_family_ids != expected_families:
        raise ValueError("development exclusion source-family set is incomplete or drifted")
    if exclusions.source_paths != expected_paths:
        raise ValueError("development exclusion source-path set is incomplete or drifted")
    if exclusions.content_sha256 != expected_hashes:
        raise ValueError("development exclusion content-hash set is incomplete or drifted")
    root = _resolve_git_root(source_root)
    if _run_git_text(root, "remote", "get-url", "origin") != development_truth.repository_origin:
        raise ValueError("development Truth source checkout remote mismatch")
    development_revision = development_truth.repository_revision
    exposure_revision = selection.candidate_freeze.exposure_revision
    if _run_git_text(root, "rev-parse", development_revision) != development_revision:
        raise ValueError("development Truth revision is unavailable in source checkout")
    if development_revision != exposure_revision and not _git_is_ancestor(
        root,
        development_revision,
        exposure_revision,
    ):
        raise ValueError("development Truth revision must be within the candidate exposure history")


def _run_git_text(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-c", "core.commitGraph=false", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot inspect Git checkout: {' '.join(arguments)}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"Git inspection failed ({' '.join(arguments)}): {detail}")
    return completed.stdout.strip()


def _git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.commitGraph=false",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            check=False,
            capture_output=True,
            timeout=30,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("cannot inspect Git ancestry") from exc
    if completed.returncode not in {0, 1}:
        raise ValueError("cannot inspect Git ancestry")
    return completed.returncode == 0


def _git_tree_entries(root: Path, revision: str) -> tuple[_GitTreeEntry, ...]:
    try:
        raw = subprocess.run(
            [
                "git",
                "-c",
                "core.commitGraph=false",
                "-C",
                str(root),
                "ls-tree",
                "-r",
                "-z",
                revision,
            ],
            check=True,
            capture_output=True,
            timeout=60,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot inspect Git tree: {revision}") from exc
    entries: list[_GitTreeEntry] = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Git tree contains an invalid entry") from exc
        if kind == "blob":
            entries.append(_GitTreeEntry(mode=mode, path=path, object_id=object_id))
    return tuple(entries)


def _resolve_git_root(source_root: str | Path) -> Path:
    try:
        root = Path(source_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"source checkout does not exist: {source_root}") from exc
    if not root.is_dir():
        raise ValueError("source checkout root must be a directory")
    top = Path(_run_git_text(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != root:
        raise ValueError("source checkout root must be the Git top level")
    return root


def assess_tag_truth_v2_constructibility(
    source_root: str | Path,
    *,
    candidate_freeze: CandidateFreezeReference,
    development_truth: DevelopmentTruthExclusionSnapshot,
    selection_policy: SelectionPolicy,
    selection_revision: str,
) -> TagTruthV2ConstructibilityReport:
    _validate_registered_code_source(development_truth)
    candidate_freeze = CandidateFreezeReference.model_validate_json(
        canonical_json(candidate_freeze.model_dump(mode="json"))
    )
    selection_policy = SelectionPolicy.model_validate_json(
        canonical_json(selection_policy.model_dump(mode="json"))
    )
    if candidate_freeze.exposure_repository_source_id != development_truth.repository_source_id:
        raise ValueError("candidate exposure source differs from development Truth repository")
    root = _resolve_git_root(source_root)
    remote = _run_git_text(root, "remote", "get-url", "origin")
    if remote != development_truth.repository_origin:
        raise ValueError("constructibility source remote mismatch")
    exposure_revision = candidate_freeze.exposure_revision
    for revision in (
        development_truth.repository_revision,
        exposure_revision,
        selection_revision,
    ):
        if _run_git_text(root, "rev-parse", revision) != revision:
            raise ValueError("constructibility requires full pinned Git revisions")
    if development_truth.repository_revision != exposure_revision and not _git_is_ancestor(
        root,
        development_truth.repository_revision,
        exposure_revision,
    ):
        raise ValueError("development Truth revision must be within the candidate exposure history")
    exposure_tree_id = _run_git_text(root, "rev-parse", f"{exposure_revision}^{{tree}}")
    if exposure_tree_id != candidate_freeze.exposure_tree_id:
        raise ValueError("candidate exposure tree identity drift")
    selection_tree_id = _run_git_text(root, "rev-parse", f"{selection_revision}^{{tree}}")
    strict_descendant = exposure_revision != selection_revision and _git_is_ancestor(
        root,
        exposure_revision,
        selection_revision,
    )
    reasons: list[ConstructibilityReason] = []
    new_paths: list[str] = []
    new_families: set[str] = set()
    selectable_candidates: list[_StructurallySelectableSource] = []
    if exposure_revision == selection_revision:
        reasons.append("selection_revision_equals_exposure_revision")
    elif not strict_descendant:
        reasons.append("selection_revision_not_strict_descendant")
    else:
        exposure_entries = _git_tree_entries(root, exposure_revision)
        exposure_paths = {entry.path for entry in exposure_entries}
        exposure_blobs = {entry.object_id for entry in exposure_entries}
        exposure_paths.update(source.path for source in development_truth.sources)
        development_hashes = {source.content_sha256 for source in development_truth.sources}
        exposure_families = {
            derive_source_family_id(entry.path)
            for entry in exposure_entries
            if _is_eligible_main_source(entry.path)
        }
        exposure_families.update(source.source_family_id for source in development_truth.sources)
        for entry in _git_tree_entries(root, selection_revision):
            path = entry.path
            if (
                not entry.is_regular_file
                or not _is_eligible_main_source(path)
                or path in exposure_paths
                or entry.object_id in exposure_blobs
            ):
                continue
            try:
                _relative_path(path, "constructibility source path")
                family = derive_source_family_id(path)
                raw = _git_file_bytes(root, selection_revision, path)
                raw.decode("utf-8")
            except (UnicodeError, ValueError):
                continue
            if not raw.splitlines():
                continue
            content_sha256 = bytes_hash(raw)
            if content_sha256 in development_hashes:
                continue
            if any(_path_scopes_overlap(family, exposed) for exposed in exposure_families):
                continue
            new_paths.append(path)
            new_families.add(family)
            selectable_candidates.append(
                _StructurallySelectableSource(
                    path=path,
                    source_family_id=family,
                    content_sha256=content_sha256,
                )
            )
        selectable_lower_bound = _verified_selectable_capacity_lower_bound(
            tuple(selectable_candidates)
        )
        if selectable_lower_bound < selection_policy.selected_case_count:
            reasons.append("insufficient_verified_selectable_capacity")
    if not strict_descendant:
        selectable_lower_bound = 0
    payload: dict[str, object] = {
        "schema_version": TAG_TRUTH_V2_CONSTRUCTIBILITY_SCHEMA_VERSION,
        "repository_source_id": development_truth.repository_source_id,
        "repository": development_truth.repository_name,
        "origin": development_truth.repository_origin,
        "exposure_revision": exposure_revision,
        "exposure_tree_id": exposure_tree_id,
        "selection_revision": selection_revision,
        "selection_tree_id": selection_tree_id,
        "candidate_freeze_id": candidate_freeze.candidate_freeze_id,
        "selection_policy_fingerprint": selection_policy.policy_fingerprint,
        "required_case_count": selection_policy.selected_case_count,
        "strict_descendant": strict_descendant,
        "new_eligible_source_count": len(new_paths),
        "new_eligible_family_count": len(new_families),
        "verified_selectable_case_lower_bound": selectable_lower_bound,
        "verified_selectable_capacity_satisfied": (
            strict_descendant and selectable_lower_bound >= selection_policy.selected_case_count
        ),
        "selection_constructibility_status": (
            "inventory_capacity_only"
            if strict_descendant and selectable_lower_bound >= selection_policy.selected_case_count
            else "not_constructible"
        ),
        "proxy_strata_capacity_status": "not_measured",
        "reasons": sorted(reasons),
        "interpretation": "verified_structural_lower_bound_not_stratum_or_truth",
    }
    payload["report_id"] = canonical_hash("tag-truth-constructibility", payload)
    return TagTruthV2ConstructibilityReport.model_validate(payload)


def _safe_file(root: Path, relative_path: str, context: str) -> Path:
    normalized = _relative_path(relative_path, context)
    candidate = root.joinpath(*PurePosixPath(normalized).parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{context} does not exist: {relative_path}") from exc
    if not resolved.is_relative_to(root) or candidate.is_symlink() or not resolved.is_file():
        raise ValueError(f"{context} must be a regular in-tree non-symlink file")
    return resolved


def _git_file_bytes(root: Path, revision: str, path: str) -> bytes:
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.commitGraph=false",
                "-C",
                str(root),
                "show",
                f"{revision}:{path}",
            ],
            check=True,
            capture_output=True,
            timeout=30,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot read pinned Git source: {path}") from exc
    return completed.stdout


def verify_tag_truth_v2_selection_checkout(
    selection: TagTruthV2Selection,
    source_root: str | Path,
) -> VerifiedTagTruthV2Checkout:
    selection = _canonical_selection(selection)
    root = _resolve_git_root(source_root)
    if _run_git_text(root, "rev-parse", "HEAD") != selection.repository.revision:
        raise ValueError("selection source checkout revision mismatch")
    remote = _run_git_text(root, "remote", "get-url", "origin")
    if remote != selection.repository.origin:
        raise ValueError("selection source checkout remote mismatch")
    if _run_git_text(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("selection source checkout must be clean")
    selected_entries = {
        entry.path: entry for entry in _git_tree_entries(root, selection.repository.revision)
    }
    texts: dict[str, str] = {}
    for source in selection.sources:
        entry = selected_entries.get(source.path)
        if entry is None or not entry.is_regular_file:
            raise ValueError(f"selection source must be a regular Git file: {source.path}")
        path = _safe_file(root, source.path, "selection source")
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read UTF-8 selection source: {source.path}") from exc
        if raw != _git_file_bytes(root, selection.repository.revision, source.path):
            raise ValueError(f"selection source differs from pinned Git bytes: {source.path}")
        if bytes_hash(raw) != source.content_sha256:
            raise ValueError(f"selection source hash drift: {source.path}")
        if len(raw.splitlines()) != source.line_count:
            raise ValueError(f"selection source line-count drift: {source.path}")
        texts[source.alias] = text
    return VerifiedTagTruthV2Checkout(
        selection_id=selection.selection_id,
        repository_revision=selection.repository.revision,
        root=root,
        source_text_by_alias=texts,
    )


def verify_tag_truth_v2_selection_exposure(
    selection: TagTruthV2Selection,
    source_root: str | Path,
) -> None:
    selection = _canonical_selection(selection)
    root = _resolve_git_root(source_root)
    freeze = selection.candidate_freeze
    exposure = freeze.exposure_revision
    selected = selection.repository.revision
    if _run_git_text(root, "rev-parse", exposure) != exposure:
        raise ValueError("candidate exposure revision is unavailable in source checkout")
    tree_id = _run_git_text(root, "rev-parse", f"{exposure}^{{tree}}")
    if tree_id != freeze.exposure_tree_id:
        raise ValueError("candidate exposure tree identity drift")
    if exposure == selected or not _git_is_ancestor(root, exposure, selected):
        raise ValueError("selection revision must be a strict descendant of exposure revision")
    exposure_entries = _git_tree_entries(root, exposure)
    exposed_paths = {entry.path for entry in exposure_entries}
    exposed_blobs = {entry.object_id for entry in exposure_entries}
    exposed_families = {
        derive_source_family_id(entry.path)
        for entry in exposure_entries
        if _is_eligible_main_source(entry.path)
    }
    selected_entries = {entry.path: entry for entry in _git_tree_entries(root, selected)}
    overlaps: list[str] = []
    for source in selection.sources:
        selected_entry = selected_entries.get(source.path)
        if selected_entry is None or not selected_entry.is_regular_file:
            raise ValueError(f"selection source must be a regular Git file: {source.path}")
        if source.path in exposed_paths:
            overlaps.append(f"path:{source.path}")
        if any(
            _path_scopes_overlap(source.source_family_id, family) for family in exposed_families
        ):
            overlaps.append(f"family:{source.source_family_id}")
        if selected_entry.object_id in exposed_blobs:
            overlaps.append(f"content:{source.alias}")
    if overlaps:
        raise ValueError(
            f"selection overlaps the candidate exposure tree: {sorted(set(overlaps))!r}"
        )


def build_tag_truth_v2_review_packet(
    selection: TagTruthV2Selection,
    checkout: VerifiedTagTruthV2Checkout,
) -> TagTruthV2ReviewPacket:
    selection = _canonical_selection(selection)
    if checkout.selection_id != selection.selection_id:
        raise ValueError("verified checkout does not bind this selection")
    if checkout.repository_revision != selection.repository.revision:
        raise ValueError("verified checkout repository revision differs from selection")
    expected_aliases = {source.alias for source in selection.sources}
    if set(checkout.source_text_by_alias) != expected_aliases:
        raise ValueError("verified checkout source aliases differ from selection")
    sources_by_alias = {source.alias: source for source in selection.sources}
    cases: list[BlindReviewCase] = []
    for selection_case in selection.cases:
        source_text = checkout.source_text_by_alias.get(selection_case.source_alias)
        if source_text is None:
            raise ValueError(
                f"verified checkout is missing source alias: {selection_case.source_alias}"
            )
        raw = source_text.encode("utf-8")
        source = sources_by_alias[selection_case.source_alias]
        if bytes_hash(raw) != source.content_sha256:
            raise ValueError("verified checkout source hash differs from selection")
        if len(raw.splitlines()) != source.line_count:
            raise ValueError("verified checkout source line count differs from selection")
        cases.append(
            BlindReviewCase(
                case_id=selection_case.case_id,
                review_source_id=f"review-{selection_case.case_id}",
                probe_line=selection_case.probe_line,
                source_text=source_text,
                line_count=len(raw.splitlines()),
            )
        )
    payload: dict[str, object] = {
        "schema_version": TAG_TRUTH_V2_REVIEW_PACKET_SCHEMA_VERSION,
        "selection_id": selection.selection_id,
        "suite_id": selection.suite_id,
        "target_tag_id": selection.tag_contract.tag_id,
        "tag_contract": selection.tag_contract.model_dump(mode="json"),
        "review_policy": selection.review_policy.model_dump(mode="json"),
        "cases": [case.model_dump(mode="json") for case in cases],
    }
    payload["packet_id"] = canonical_hash("tag-truth-review-packet", payload)
    return TagTruthV2ReviewPacket.model_validate(payload)


def verify_tag_truth_v2_review_packet(
    packet: TagTruthV2ReviewPacket,
    selection: TagTruthV2Selection,
) -> None:
    packet = TagTruthV2ReviewPacket.model_validate_json(
        canonical_json(packet.model_dump(mode="json"))
    )
    selection = _canonical_selection(selection)
    packet_by_case = {case.case_id: case for case in packet.cases}
    if set(packet_by_case) != {case.case_id for case in selection.cases}:
        raise ValueError("review packet case set differs from selection")
    source_text_by_alias = {
        case.source_alias: packet_by_case[case.case_id].source_text for case in selection.cases
    }
    rebuilt = build_tag_truth_v2_review_packet(
        selection,
        VerifiedTagTruthV2Checkout(
            selection_id=selection.selection_id,
            repository_revision=selection.repository.revision,
            root=Path("."),
            source_text_by_alias=source_text_by_alias,
        ),
    )
    if packet != rebuilt:
        raise ValueError("review packet does not match the sealed selection")


def _parse_json_model[TModel: BaseModel](
    raw: bytes,
    model: type[TModel],
    context: str,
) -> TModel:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        return model.model_validate(payload)
    except (UnicodeError, json.JSONDecodeError, ValidationError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


def _load_json_model[TModel: BaseModel](
    path: str | Path,
    model: type[TModel],
    context: str,
) -> TModel:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"{context} must be a regular non-symlink file: {candidate}")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {context} {candidate}: {exc}") from exc
    return _parse_json_model(raw, model, context)


def parse_tag_truth_v2_selection(raw: bytes) -> TagTruthV2Selection:
    return _parse_json_model(raw, TagTruthV2Selection, "Tag Truth v2 selection")


def load_tag_truth_v2_selection(path: str | Path) -> TagTruthV2Selection:
    return _load_json_model(path, TagTruthV2Selection, "Tag Truth v2 selection")


def parse_tag_truth_v2_review_packet(raw: bytes) -> TagTruthV2ReviewPacket:
    return _parse_json_model(raw, TagTruthV2ReviewPacket, "Tag Truth v2 review packet")


def load_tag_truth_v2_review_packet(path: str | Path) -> TagTruthV2ReviewPacket:
    return _load_json_model(path, TagTruthV2ReviewPacket, "Tag Truth v2 review packet")


__all__ = [
    "BlindReviewCase",
    "CandidateFreezeReference",
    "ConstructibilityReason",
    "DevelopmentExclusions",
    "DevelopmentTruthExclusionSnapshot",
    "DevelopmentTruthExclusionSource",
    "PostReviewClassMinimums",
    "ProxyStratumPolicy",
    "ReviewPolicySnapshot",
    "SelectionCase",
    "SelectionPolicy",
    "SelectionQualificationReason",
    "SelectorAttestation",
    "TAG_TRUTH_V2_CONSTRUCTIBILITY_SCHEMA_VERSION",
    "TAG_TRUTH_V2_REVIEW_PACKET_SCHEMA_VERSION",
    "TAG_TRUTH_V2_SELECTION_SCHEMA_VERSION",
    "TagTruthV2ConstructibilityReport",
    "TagTruthV2ReviewPacket",
    "TagTruthV2Selection",
    "VerifiedTagTruthV2Checkout",
    "assess_tag_truth_v2_constructibility",
    "build_tag_truth_v2_review_packet",
    "candidate_freeze_payload_with_id",
    "development_exclusions_payload_with_id",
    "load_tag_truth_v2_candidate_freeze",
    "load_tag_truth_v2_development_exclusion_snapshot",
    "load_tag_truth_v2_review_packet",
    "load_tag_truth_v2_selection",
    "load_tag_truth_v2_selection_policy",
    "parse_tag_truth_v2_candidate_freeze",
    "parse_tag_truth_v2_review_packet",
    "parse_tag_truth_v2_selection",
    "parse_tag_truth_v2_selection_policy",
    "review_policy_payload_with_id",
    "seal_tag_truth_v2_selection_payload",
    "selection_payload_with_id",
    "selection_policy_payload_with_id",
    "verify_tag_truth_v2_development_exclusions",
    "verify_tag_truth_v2_selection_checkout",
    "verify_tag_truth_v2_selection_exposure",
    "verify_tag_truth_v2_review_packet",
]
