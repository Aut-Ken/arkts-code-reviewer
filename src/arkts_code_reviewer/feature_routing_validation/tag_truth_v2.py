from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.code_analysis.review_unit_contract import REVIEW_UNIT_V2_KINDS

TAG_TRUTH_V2_SCHEMA_VERSION = "tag-truth-v2"
TAG_TRUTH_V2_FINGERPRINT_PREFIX = "tag-truth-v2:sha256:"

DatasetRole = Literal[
    "development_regression",
    "independent_blind_challenge",
    "production_prevalence",
]
TruthStatus = Literal["proposed", "consensus"]
SemanticLabel = Literal["positive", "negative", "needs_taxonomy_decision"]
ConsensusStatus = Literal["not_applicable", "complete"]
NearDuplicateCheckStatus = Literal["not_measured", "not_qualified", "qualified"]
SourceKind = Literal["main", "docs_sample", "ohos_test"]
DataQualificationReason = Literal[
    "artifact_authenticity_not_verified",
    "external_selection_not_verified",
    "near_duplicate_verifier_unavailable",
    "production_prevalence_not_verified",
    "stage1_contract_only",
]

_SHA256 = r"^sha256:[0-9a-f]{64}$"
_TAG_CONTRACT_FINGERPRINT = r"^tag-contract-snapshot:sha256:[0-9a-f]{64}$"
_CASE_ID = r"^case-[0-9a-f]{16}$"
_RECEIPT_ID = r"^tag-truth-review-receipt:sha256:[0-9a-f]{64}$"
_CONSENSUS_ID = r"^tag-truth-consensus:sha256:[0-9a-f]{64}$"
_QUALITY_GATE_ID = r"^tag-truth-quality-gates:sha256:[0-9a-f]{64}$"
_TEMPLATE_CLUSTER_ID = r"^template-cluster:sha256:[0-9a-f]{64}$"
_MODULE_BOUNDARY_NAMES = {
    "casesfeature",
    "entry",
    "feature",
    "features",
    "product",
}

_REQUIRED_QUALIFICATION_REASONS = {
    "artifact_authenticity_not_verified",
    "near_duplicate_verifier_unavailable",
    "stage1_contract_only",
}


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


def _single_line(value: str, context: str) -> str:
    if value != value.strip() or not value or any(ord(character) < 32 for character in value):
        raise ValueError(f"{context} must be non-empty, trimmed, and single-line")
    return value


def _relative_path(value: str, context: str) -> str:
    if value != value.strip() or "\\" in value:
        raise ValueError(f"{context} must be a trimmed POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{context} must be relative and cannot traverse parents")
    if path.as_posix() != value:
        raise ValueError(f"{context} must be normalized")
    return value


def _sorted_unique(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def derive_source_family_id(source_path: str) -> str:
    """Derive the app-family boundary from a normalized ArkTS source path."""
    normalized = _relative_path(source_path, "Tag Truth v2 source path")
    parts = PurePosixPath(normalized).parts
    try:
        src_index = parts.index("src")
    except ValueError as exc:
        raise ValueError("Tag Truth v2 source path must contain a src module boundary") from exc
    module_prefix = parts[:src_index]
    if len(module_prefix) < 2:
        raise ValueError("Tag Truth v2 source path has no app-family boundary")
    boundary_index: int | None = None
    for index, part in enumerate(module_prefix):
        lowered = part.lower()
        if (
            lowered in _MODULE_BOUNDARY_NAMES
            or (lowered.startswith("har") and len(lowered) <= 4)
            or (lowered.startswith("hsp") and len(lowered) <= 4)
        ):
            boundary_index = index
            break
    family_parts = (
        module_prefix[:boundary_index] if boundary_index is not None else module_prefix[:-1]
    )
    if len(family_parts) < 2:
        raise ValueError("Tag Truth v2 source path resolves to an unsafe app-family boundary")
    return PurePosixPath(*family_parts).as_posix()


def _path_scopes_overlap(left: str, right: str) -> bool:
    left_path = PurePosixPath(left)
    right_path = PurePosixPath(right)
    return left_path.is_relative_to(right_path) or right_path.is_relative_to(left_path)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def canonical_hash(prefix: str, payload: object) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{prefix}:sha256:{digest}"


def bytes_hash(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


class TagAxisSemantics(_FrozenModel):
    positive: Annotated[str, Field(min_length=1)]
    negative: Annotated[str, Field(min_length=1)]
    abstain: Annotated[str, Field(min_length=1)]

    @field_validator("positive", "negative", "abstain")
    @classmethod
    def validate_semantics(cls, value: str, info: object) -> str:
        if value != value.strip() or not value:
            raise ValueError(
                f"Tag axis {getattr(info, 'field_name', 'semantics')} must be non-empty and trimmed"
            )
        return value


class TagContractSnapshot(_FrozenModel):
    schema_version: Literal["tag-contract-snapshot-v1"]
    tag_id: Annotated[str, Field(pattern=r"^has_[a-z0-9_]+$")]
    version: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")]
    axes_relationship: Literal["independent"]
    exact_semantics: TagAxisSemantics
    routing_semantics: TagAxisSemantics
    contract_fingerprint: Annotated[str, Field(pattern=_TAG_CONTRACT_FINGERPRINT)]

    @model_validator(mode="after")
    def validate_fingerprint(self) -> TagContractSnapshot:
        payload = self.model_dump(mode="json", exclude={"contract_fingerprint"})
        expected = canonical_hash("tag-contract-snapshot", payload)
        if self.contract_fingerprint != expected:
            raise ValueError("Tag contract fingerprint does not match its complete snapshot")
        return self


class TagTruthV2Repository(_FrozenModel):
    source_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    repository: Annotated[str, Field(min_length=1)]
    origin: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]

    @field_validator("repository", "origin")
    @classmethod
    def validate_identity(cls, value: str, info: object) -> str:
        return _single_line(value, f"repository {getattr(info, 'field_name', 'identity')}")


class TagTruthV2Source(_FrozenModel):
    alias: Annotated[str, Field(pattern=r"^src[0-9]{3,6}$")]
    repository_source_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    origin: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    path: str
    content_sha256: Annotated[str, Field(pattern=_SHA256)]
    line_count: Annotated[int, Field(ge=1)]
    source_kind: SourceKind
    app_scope: str
    source_family_id: str

    @field_validator("origin")
    @classmethod
    def validate_single_line_fields(cls, value: str, info: object) -> str:
        return _single_line(value, f"source {getattr(info, 'field_name', 'identity')}")

    @field_validator("app_scope", "source_family_id")
    @classmethod
    def validate_scope_fields(cls, value: str, info: object) -> str:
        return _relative_path(value, f"source {getattr(info, 'field_name', 'scope')}")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = _relative_path(value, "source path")
        if not value.endswith((".ets", ".ts")):
            raise ValueError("Tag Truth source must use .ets or .ts")
        return value

    @model_validator(mode="after")
    def validate_scope_and_kind(self) -> TagTruthV2Source:
        expected_family = derive_source_family_id(self.path)
        if self.app_scope != expected_family or self.source_family_id != expected_family:
            raise ValueError(
                "Tag Truth v2 source app_scope and family must equal its path-derived family"
            )
        if not PurePosixPath(self.path).is_relative_to(PurePosixPath(self.app_scope)):
            raise ValueError("Tag Truth v2 source must stay below its app_scope")
        path = f"/{self.path}/"
        if self.source_kind == "main" and ("/src/main/" not in path or "DocsSample" in self.path):
            raise ValueError("main source_kind requires non-DocsSample src/main code")
        if self.source_kind == "ohos_test" and "/src/ohosTest/" not in path:
            raise ValueError("ohos_test source_kind requires src/ohosTest code")
        if self.source_kind == "docs_sample" and "DocsSample" not in self.path:
            raise ValueError("docs_sample source_kind requires a DocsSample path")
        return self


class TagTruthV2LineSpan(_FrozenModel):
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_order(self) -> TagTruthV2LineSpan:
        if self.end_line < self.start_line:
            raise ValueError("line span end_line must be >= start_line")
        return self


class TagTruthV2AxisJudgement(_FrozenModel):
    label: SemanticLabel
    metric_eligible: bool
    abstain_reason: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")] | None = None
    evidence_lines: tuple[Annotated[int, Field(ge=1)], ...]
    rationale: Annotated[str, Field(min_length=1)]

    @field_validator("evidence_lines", mode="before")
    @classmethod
    def parse_evidence_lines(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "axis judgement evidence_lines")

    @field_validator("evidence_lines")
    @classmethod
    def validate_evidence_lines(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("axis judgement evidence_lines cannot be empty")
        if value != tuple(sorted(set(value))):
            raise ValueError("axis judgement evidence_lines must be sorted and unique")
        return value

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str) -> str:
        return _single_line(value, "axis judgement rationale")

    @model_validator(mode="after")
    def validate_eligibility(self) -> TagTruthV2AxisJudgement:
        if self.label == "needs_taxonomy_decision":
            if self.metric_eligible or self.abstain_reason is None:
                raise ValueError(
                    "unresolved axis judgement must be metric-ineligible with an abstain_reason"
                )
            return self
        if self.metric_eligible and self.abstain_reason is not None:
            raise ValueError("metric-eligible axis judgement cannot declare an abstain_reason")
        if not self.metric_eligible and self.abstain_reason is None:
            raise ValueError("metric-ineligible axis judgement requires an abstain_reason")
        return self


class TagTruthV2Case(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    target_tag_id: Annotated[str, Field(pattern=r"^has_[a-z0-9_]+$")]
    source_alias: Annotated[str, Field(pattern=r"^src[0-9]{3,6}$")]
    changed_line: Annotated[int, Field(ge=1)]
    expected_unit_kind: str
    expected_unit_symbol: Annotated[str, Field(min_length=1)]
    expected_unit_span: TagTruthV2LineSpan
    exact: TagTruthV2AxisJudgement
    routing: TagTruthV2AxisJudgement
    stratum_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    critical_negative: bool
    review_unit_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    normalized_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    template_cluster_id: Annotated[str, Field(pattern=_TEMPLATE_CLUSTER_ID)]

    @field_validator("expected_unit_kind")
    @classmethod
    def validate_unit_kind(cls, value: str) -> str:
        if value not in REVIEW_UNIT_V2_KINDS:
            raise ValueError(f"unsupported expected_unit_kind: {value}")
        return value

    @field_validator("expected_unit_symbol")
    @classmethod
    def validate_text_fields(cls, value: str, info: object) -> str:
        return _single_line(value, f"case {getattr(info, 'field_name', 'text')}")

    @model_validator(mode="after")
    def validate_semantics(self) -> TagTruthV2Case:
        span = self.expected_unit_span
        if not span.start_line <= self.changed_line <= span.end_line:
            raise ValueError("case changed_line must be inside expected_unit_span")
        if any(
            line < span.start_line or line > span.end_line for line in self.exact.evidence_lines
        ):
            raise ValueError("exact evidence lines must be inside expected_unit_span")
        if self.critical_negative and not (
            self.exact.metric_eligible and self.exact.label == "negative"
        ):
            raise ValueError(
                "critical-negative cases must be metric-eligible resolved exact negatives"
            )
        return self


class TagTruthV2ReviewReceiptReference(_FrozenModel):
    round_id: Annotated[str, Field(pattern=r"^round-[a-z0-9]+(?:-[a-z0-9]+)*$")]
    reviewer_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    reviewer_kind: Literal["human"]
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]
    tag_contract_fingerprint: Annotated[str, Field(pattern=_TAG_CONTRACT_FINGERPRINT)]
    candidate_design_participant: bool
    selector_participant: bool
    candidate_configuration_seen: bool
    candidate_output_seen: bool
    reviewed_case_ids: tuple[Annotated[str, Field(pattern=_CASE_ID)], ...]

    @field_validator("reviewed_case_ids", mode="before")
    @classmethod
    def parse_case_ids(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "review receipt reviewed_case_ids")

    @field_validator("reviewed_case_ids")
    @classmethod
    def validate_case_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("review receipt must cover at least one case")
        return _sorted_unique(value, "review receipt case IDs")


class TagTruthV2ReviewChain(_FrozenModel):
    review_policy_version: Annotated[
        str,
        Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"),
    ]
    review_policy_sha256: Annotated[str, Field(pattern=_SHA256)]
    tag_contract_fingerprint: Annotated[str, Field(pattern=_TAG_CONTRACT_FINGERPRINT)]
    consensus_status: ConsensusStatus
    receipt_references: tuple[TagTruthV2ReviewReceiptReference, ...]
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID)] | None = None
    consensus_case_ids: tuple[Annotated[str, Field(pattern=_CASE_ID)], ...] = ()

    @field_validator("receipt_references", "consensus_case_ids", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"review chain {getattr(info, 'field_name', 'sequence')}")

    @field_validator("consensus_case_ids")
    @classmethod
    def validate_consensus_case_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value, "review consensus case IDs")

    @model_validator(mode="after")
    def validate_chain_shape(self) -> TagTruthV2ReviewChain:
        if any(
            reference.tag_contract_fingerprint != self.tag_contract_fingerprint
            for reference in self.receipt_references
        ):
            raise ValueError("review receipt Tag contract fingerprint must match the review chain")
        receipt_keys = tuple(
            (item.round_id, item.reviewer_id, item.receipt_id) for item in self.receipt_references
        )
        if receipt_keys != tuple(sorted(set(receipt_keys))):
            raise ValueError("review receipt references must be sorted and unique")
        for attribute, label in (
            ("round_id", "round IDs"),
            ("reviewer_id", "reviewer IDs"),
            ("receipt_id", "receipt IDs"),
        ):
            values = tuple(getattr(item, attribute) for item in self.receipt_references)
            if len(values) != len(set(values)):
                raise ValueError(f"review receipt {label} must each be unique")
        if self.consensus_status == "not_applicable":
            if self.receipt_references or self.consensus_id is not None or self.consensus_case_ids:
                raise ValueError("not-applicable review chain cannot contain consensus artifacts")
            return self
        if len(self.receipt_references) != 2:
            raise ValueError("complete consensus requires exactly two review receipts")
        if self.consensus_id is None or not self.consensus_case_ids:
            raise ValueError("complete consensus requires an ID and covered case set")
        return self


class TagTruthV2QualityGateSnapshot(_FrozenModel):
    quality_gate_id: Annotated[str, Field(pattern=_QUALITY_GATE_ID)]
    policy_version: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")]
    approval_status: Literal["snapshot_only_not_approved"]
    minimum_case_count: Annotated[int, Field(ge=1)]
    minimum_exact_positive_cases: Annotated[int, Field(ge=1)]
    minimum_exact_negative_cases: Annotated[int, Field(ge=1)]
    minimum_routing_positive_cases: Annotated[int, Field(ge=1)]
    minimum_routing_negative_cases: Annotated[int, Field(ge=1)]
    minimum_source_families: Annotated[int, Field(ge=1)]
    minimum_exact_precision: Annotated[float, Field(ge=0.0, le=1.0)]
    minimum_exact_recall: Annotated[float, Field(ge=0.0, le=1.0)]
    minimum_exact_precision_wilson_95: Annotated[float, Field(ge=0.0, le=1.0)]
    minimum_exact_recall_wilson_95: Annotated[float, Field(ge=0.0, le=1.0)]
    minimum_routing_precision: Annotated[float, Field(ge=0.0, le=1.0)]
    minimum_routing_recall: Annotated[float, Field(ge=0.0, le=1.0)]
    minimum_routing_precision_wilson_95: Annotated[float, Field(ge=0.0, le=1.0)]
    minimum_routing_recall_wilson_95: Annotated[float, Field(ge=0.0, le=1.0)]
    maximum_exact_false_positives: Annotated[int, Field(ge=0)]
    maximum_exact_false_negatives: Annotated[int, Field(ge=0)]
    maximum_routing_false_positives: Annotated[int, Field(ge=0)]
    maximum_routing_false_negatives: Annotated[int, Field(ge=0)]
    maximum_exact_critical_false_positives: Annotated[int, Field(ge=0)]
    maximum_file_hint_promotions: Annotated[int, Field(ge=0)]
    maximum_parser_risk_cases: Annotated[int, Field(ge=0)]
    maximum_review_unit_risk_cases: Annotated[int, Field(ge=0)]
    maximum_scope_risk_cases: Annotated[int, Field(ge=0)]
    maximum_unresolved_taxonomy_cases: Annotated[int, Field(ge=0)]
    critical_negative_strata: tuple[str, ...]

    @field_validator("critical_negative_strata", mode="before")
    @classmethod
    def parse_strata(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "quality gate critical_negative_strata")

    @field_validator("critical_negative_strata")
    @classmethod
    def validate_strata(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not item or item != item.strip() or not item.replace("_", "a").isalnum():
                raise ValueError("critical negative strata must use lowercase slug IDs")
            if item.lower() != item or not item[0].isalpha():
                raise ValueError("critical negative strata must use lowercase slug IDs")
        return _sorted_unique(value, "quality gate critical-negative strata")

    @model_validator(mode="after")
    def validate_snapshot(self) -> TagTruthV2QualityGateSnapshot:
        if self.minimum_case_count < (
            self.minimum_exact_positive_cases + self.minimum_exact_negative_cases
        ):
            raise ValueError("minimum_case_count cannot be below exact positive/negative minima")
        if self.minimum_case_count < (
            self.minimum_routing_positive_cases + self.minimum_routing_negative_cases
        ):
            raise ValueError("minimum_case_count cannot be below routing positive/negative minima")
        if self.minimum_source_families > self.minimum_case_count:
            raise ValueError("minimum_source_families cannot exceed minimum_case_count")
        payload = self.model_dump(mode="json", exclude={"quality_gate_id"})
        expected = canonical_hash("tag-truth-quality-gates", payload)
        if self.quality_gate_id != expected:
            raise ValueError("quality_gate_id does not match the structured gate snapshot")
        return self


class TagTruthV2Suite(_FrozenModel):
    schema_version: Literal["tag-truth-v2"]
    suite_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    description: Annotated[str, Field(min_length=1)]
    dataset_role: DatasetRole
    truth_status: TruthStatus
    data_qualification_status: Literal["not_qualified"]
    data_qualification_reasons: tuple[DataQualificationReason, ...]
    natural_prevalence_claimed: Literal[False]
    near_duplicate_policy_version: Annotated[
        str,
        Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"),
    ]
    near_duplicate_check_status: NearDuplicateCheckStatus
    tag_contract: TagContractSnapshot
    repository: TagTruthV2Repository
    sources: tuple[TagTruthV2Source, ...]
    cases: tuple[TagTruthV2Case, ...]
    review_chain: TagTruthV2ReviewChain
    quality_gates: TagTruthV2QualityGateSnapshot

    @field_validator("sources", "cases", "data_qualification_reasons", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"Tag Truth v2 {getattr(info, 'field_name', 'sequence')}")

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return _single_line(value, "Tag Truth v2 description")

    @field_validator("data_qualification_reasons")
    @classmethod
    def validate_qualification_reasons(
        cls,
        value: tuple[DataQualificationReason, ...],
    ) -> tuple[DataQualificationReason, ...]:
        if not value:
            raise ValueError("data qualification requires machine-readable reasons")
        if value != tuple(sorted(set(value))):
            raise ValueError("data qualification reasons must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_suite(self) -> TagTruthV2Suite:
        required_reasons = set(_REQUIRED_QUALIFICATION_REASONS)
        if self.dataset_role != "development_regression":
            required_reasons.add("external_selection_not_verified")
        if self.dataset_role == "production_prevalence":
            required_reasons.add("production_prevalence_not_verified")
        missing_reasons = sorted(required_reasons - set(self.data_qualification_reasons))
        if missing_reasons:
            raise ValueError(
                f"data qualification is missing required Stage-1 reasons: {missing_reasons!r}"
            )
        if self.near_duplicate_check_status == "qualified":
            raise ValueError(
                "Tag Truth v2 Stage 1 has no near-duplicate verifier and cannot claim qualified"
            )
        if self.truth_status == "consensus":
            if self.review_chain.consensus_status != "complete":
                raise ValueError("consensus Truth requires a complete review chain")
        elif self.review_chain.consensus_status != "not_applicable":
            raise ValueError("proposed Truth cannot publish a consensus review chain")
        if self.dataset_role != "development_regression" and self.truth_status != "consensus":
            raise ValueError("independent and production Truth require complete consensus")
        if self.dataset_role == "development_regression" and self.truth_status != "proposed":
            raise ValueError("development regression Truth must remain proposed")

        aliases = tuple(item.alias for item in self.sources)
        paths = tuple(item.path for item in self.sources)
        blob_hashes = tuple(item.content_sha256 for item in self.sources)
        case_ids = tuple(item.case_id for item in self.cases)
        if not aliases or aliases != tuple(sorted(set(aliases))):
            raise ValueError("Tag Truth v2 sources must be sorted by unique alias")
        if len(paths) != len(set(paths)):
            raise ValueError("Tag Truth v2 source paths must be unique")
        if len(blob_hashes) != len(set(blob_hashes)):
            raise ValueError("Tag Truth v2 source blobs must be unique")
        if not case_ids or case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("Tag Truth v2 cases must be sorted by unique opaque case ID")

        sources_by_alias = {item.alias: item for item in self.sources}
        referenced = {item.source_alias for item in self.cases}
        if referenced != set(sources_by_alias):
            raise ValueError("Tag Truth v2 sources must be referenced completely and exclusively")
        unit_keys: set[tuple[str, str, str, int, int]] = set()
        for source in self.sources:
            if (
                source.repository_source_id != self.repository.source_id
                or source.origin != self.repository.origin
                or source.revision != self.repository.revision
            ):
                raise ValueError("Tag Truth v2 source repository binding drift")
        for case in self.cases:
            if case.target_tag_id != self.tag_contract.tag_id:
                raise ValueError("Tag Truth v2 case target Tag differs from its contract")
            case_source = sources_by_alias.get(case.source_alias)
            if case_source is None:
                raise ValueError(
                    f"Tag Truth v2 case references unknown source: {case.source_alias}"
                )
            if case.expected_unit_span.end_line > case_source.line_count:
                raise ValueError(f"Tag Truth v2 case span exceeds source: {case.case_id}")
            if any(line > case_source.line_count for line in case.routing.evidence_lines):
                raise ValueError(f"Tag Truth v2 routing evidence exceeds source: {case.case_id}")
            span = case.expected_unit_span
            unit_key = (
                case.source_alias,
                case.expected_unit_kind,
                case.expected_unit_symbol,
                span.start_line,
                span.end_line,
            )
            if unit_key in unit_keys:
                raise ValueError("Tag Truth v2 cases must identify unique ReviewUnits")
            unit_keys.add(unit_key)

        family_ids = tuple(sorted({item.source_family_id for item in self.sources}))
        for index, family in enumerate(family_ids):
            for other in family_ids[index + 1 :]:
                if _path_scopes_overlap(family, other):
                    raise ValueError(
                        "Tag Truth v2 source families cannot have ancestor/descendant overlap"
                    )
        raw_bodies = tuple(item.review_unit_body_sha256 for item in self.cases)
        normalized_bodies = tuple(item.normalized_body_sha256 for item in self.cases)
        template_clusters = tuple(item.template_cluster_id for item in self.cases)
        for identities, label in (
            (raw_bodies, "ReviewUnit bodies"),
            (normalized_bodies, "normalized bodies"),
            (template_clusters, "template clusters"),
        ):
            if len(identities) != len(set(identities)):
                raise ValueError(f"Tag Truth v2 cases contain duplicate {label}")

        self._validate_review_coverage(case_ids)
        self._validate_gate_references()
        self._validate_gate_policy()
        if self.dataset_role == "independent_blind_challenge":
            self._validate_blind_challenge(sources_by_alias)
        if self.dataset_role != "development_regression":
            raise ValueError(
                "Tag Truth v2 Stage 1 only loads development_regression data; "
                "independent and production roles require a later verifier"
            )
        return self

    def _validate_review_coverage(self, case_ids: tuple[str, ...]) -> None:
        chain = self.review_chain
        contract_fingerprint = self.tag_contract.contract_fingerprint
        if chain.tag_contract_fingerprint != contract_fingerprint:
            raise ValueError("review chain does not bind the suite Tag contract fingerprint")
        if any(
            reference.tag_contract_fingerprint != contract_fingerprint
            for reference in chain.receipt_references
        ):
            raise ValueError("review receipt does not bind the suite Tag contract fingerprint")
        if chain.consensus_status != "complete":
            return
        if chain.consensus_case_ids != case_ids:
            raise ValueError("review consensus must cover the complete ordered case set")
        for reference in chain.receipt_references:
            if reference.reviewed_case_ids != case_ids:
                raise ValueError("each review receipt must cover the complete ordered case set")

    def _validate_gate_references(self) -> None:
        declared = set(self.quality_gates.critical_negative_strata)
        actual = {case.stratum_id for case in self.cases if case.critical_negative}
        extra = sorted(declared - actual)
        missing = sorted(actual - declared)
        if extra or missing:
            raise ValueError(
                "quality gate critical-negative strata must equal actual exact "
                f"critical-negative case strata; extra={extra!r}, missing={missing!r}"
            )

    def _validate_gate_policy(self) -> None:
        gates = self.quality_gates
        count_floors = {
            "development_regression": (2, 1, 1, 1, 1, 2),
            "independent_blind_challenge": (32, 16, 16, 16, 16, 32),
            "production_prevalence": (80, 40, 40, 40, 40, 80),
        }[self.dataset_role]
        count_values = (
            gates.minimum_case_count,
            gates.minimum_exact_positive_cases,
            gates.minimum_exact_negative_cases,
            gates.minimum_routing_positive_cases,
            gates.minimum_routing_negative_cases,
            gates.minimum_source_families,
        )
        weakened: list[str] = []
        count_names = (
            "minimum_case_count",
            "minimum_exact_positive_cases",
            "minimum_exact_negative_cases",
            "minimum_routing_positive_cases",
            "minimum_routing_negative_cases",
            "minimum_source_families",
        )
        weakened.extend(
            name
            for name, value, floor in zip(count_names, count_values, count_floors, strict=True)
            if value < floor
        )
        wilson_floor = 0.90 if self.dataset_role == "production_prevalence" else 0.80
        lower_bound_floors = {
            "minimum_exact_precision": 0.99,
            "minimum_exact_recall": 0.95,
            "minimum_exact_precision_wilson_95": wilson_floor,
            "minimum_exact_recall_wilson_95": wilson_floor,
            "minimum_routing_precision": 0.99,
            "minimum_routing_recall": 0.95,
            "minimum_routing_precision_wilson_95": wilson_floor,
            "minimum_routing_recall_wilson_95": wilson_floor,
        }
        weakened.extend(
            name for name, floor in lower_bound_floors.items() if getattr(gates, name) < floor
        )
        zero_maxima = (
            "maximum_exact_false_positives",
            "maximum_exact_false_negatives",
            "maximum_routing_false_positives",
            "maximum_routing_false_negatives",
            "maximum_exact_critical_false_positives",
            "maximum_file_hint_promotions",
            "maximum_parser_risk_cases",
            "maximum_review_unit_risk_cases",
            "maximum_scope_risk_cases",
            "maximum_unresolved_taxonomy_cases",
        )
        weakened.extend(name for name in zero_maxima if getattr(gates, name) != 0)
        if weakened:
            raise ValueError(
                f"quality gate snapshot weakens the Stage-1 policy floor: {sorted(weakened)!r}"
            )

    def _validate_blind_challenge(
        self,
        sources_by_alias: Mapping[str, TagTruthV2Source],
    ) -> None:
        families = [sources_by_alias[item.source_alias].source_family_id for item in self.cases]
        duplicate_families = sorted(
            family for family, count in Counter(families).items() if count > 1
        )
        if duplicate_families:
            raise ValueError(
                "independent blind challenge permits at most one case per source family: "
                f"{duplicate_families!r}"
            )
        normalized_bodies = [item.normalized_body_sha256 for item in self.cases]
        template_clusters = [item.template_cluster_id for item in self.cases]
        if len(normalized_bodies) != len(set(normalized_bodies)):
            raise ValueError("independent blind challenge contains duplicate normalized bodies")
        if len(template_clusters) != len(set(template_clusters)):
            raise ValueError("independent blind challenge contains duplicate template clusters")
        for reference in self.review_chain.receipt_references:
            if any(
                (
                    reference.candidate_design_participant,
                    reference.selector_participant,
                    reference.candidate_configuration_seen,
                    reference.candidate_output_seen,
                )
            ):
                raise ValueError(
                    "independent blind reviewers must satisfy all blinding attestations"
                )


def parse_tag_truth_v2(raw: bytes) -> TagTruthV2Suite:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        return TagTruthV2Suite.model_validate(payload)
    except (
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        _DuplicateKeyError,
    ) as exc:
        raise ValueError(f"invalid Tag Truth v2 payload: {exc}") from exc


def load_tag_truth_v2(path: str | Path) -> TagTruthV2Suite:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"Tag Truth v2 manifest must be a regular non-symlink file: {candidate}")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read Tag Truth v2 manifest {candidate}: {exc}") from exc
    return parse_tag_truth_v2(raw)


def tag_truth_v2_fingerprint(suite: TagTruthV2Suite) -> str:
    payload = suite.model_dump(mode="json")
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{TAG_TRUTH_V2_FINGERPRINT_PREFIX}{digest}"


__all__ = [
    "ConsensusStatus",
    "DataQualificationReason",
    "DatasetRole",
    "NearDuplicateCheckStatus",
    "SemanticLabel",
    "SourceKind",
    "TAG_TRUTH_V2_FINGERPRINT_PREFIX",
    "TAG_TRUTH_V2_SCHEMA_VERSION",
    "TagAxisSemantics",
    "TagContractSnapshot",
    "TagTruthV2AxisJudgement",
    "TagTruthV2Case",
    "TagTruthV2LineSpan",
    "TagTruthV2QualityGateSnapshot",
    "TagTruthV2Repository",
    "TagTruthV2ReviewChain",
    "TagTruthV2ReviewReceiptReference",
    "TagTruthV2Source",
    "TagTruthV2Suite",
    "TruthStatus",
    "bytes_hash",
    "canonical_hash",
    "canonical_json",
    "derive_source_family_id",
    "load_tag_truth_v2",
    "parse_tag_truth_v2",
    "tag_truth_v2_fingerprint",
]
