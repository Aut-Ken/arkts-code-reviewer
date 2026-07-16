from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
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
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate import (
    ReferenceInventorySummary,
    ScannedReferenceInventory,
    TagTruthV2NearDuplicatePolicy,
    TagTruthV2NearDuplicateVerification,
    scan_pinned_git_reference_inventory,
    verify_tag_truth_v2_near_duplicate_verification,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_provenance import (
    CapturedCommittedArtifact,
    TagTruthV2ProvenanceVerification,
    TagTruthV2SealedArtifact,
    verify_tag_truth_v2_provenance_verification,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review import (
    AxisConsensus,
    AxisRationaleVote,
    ConsensusVote,
    ReviewerBlindingAttestation,
    ReviewerIdentity,
    ReviewUnitSelection,
    TagTruthV2Consensus,
    TagTruthV2ReviewReceipt,
    parse_tag_truth_v2_consensus,
    parse_tag_truth_v2_review_receipt,
    validate_tag_truth_v2_review_receipt,
    verify_tag_truth_v2_consensus,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    DevelopmentTruthExclusionSnapshot,
    TagTruthV2ReviewPacket,
    TagTruthV2Selection,
    parse_tag_truth_v2_review_packet,
    parse_tag_truth_v2_selection,
    verify_tag_truth_v2_review_packet,
)

TAG_TRUTH_V2_PUBLICATION_SCHEMA_VERSION = "tag-truth-v2-publication-v1"
TAG_TRUTH_V2_PUBLISHED_CONSENSUS_SCHEMA_VERSION = "tag-truth-v2-published-consensus-v1"

PublicationStatus = Literal[
    "blocked_no_suite",
    "published_consensus_not_qualified",
]
PublicationBlocker = Literal[
    "near_duplicate_potential_duplicate",
    "near_duplicate_review_required",
    "taxonomy_decision_required",
    "unresolved_review_disagreement",
]
EvidenceQualificationBlocker = Literal[
    "external_reviewer_identity_not_authenticated",
    "external_selector_identity_not_authenticated",
    "git_host_and_remote_not_authenticated",
    "near_duplicate_calibration_truth_unavailable",
    "near_duplicate_policy_not_approved",
    "review_policy_not_approved",
    "selection_policy_not_approved",
]
NearDuplicateQualificationBlocker = Literal[
    "calibration_truth_unavailable",
    "policy_not_approved",
]

_SHA256 = r"^sha256:[0-9a-f]{64}$"
_GIT_OBJECT_ID = r"^[0-9a-f]{40}$"
_CASE_ID = r"^case-[0-9a-f]{16}$"
_SELECTION_ID = r"^tag-truth-selection:sha256:[0-9a-f]{64}$"
_PACKET_ID = r"^tag-truth-review-packet:sha256:[0-9a-f]{64}$"
_RECEIPT_ID = r"^tag-truth-review-receipt:sha256:[0-9a-f]{64}$"
_CONSENSUS_ID = r"^tag-truth-consensus:sha256:[0-9a-f]{64}$"
_PROVENANCE_ID = r"^tag-truth-provenance-verification:sha256:[0-9a-f]{64}$"
_SCREENING_ID = r"^tag-truth-near-duplicate-screening:sha256:[0-9a-f]{64}$"
_CANDIDATE_FREEZE_ID = r"^tag-truth-candidate-freeze:sha256:[0-9a-f]{64}$"
_FEATURE_CONFIG_FINGERPRINT = r"^feature-config:sha256:[0-9a-f]{64}$"
_TAG_CONTRACT_FINGERPRINT = r"^tag-contract-snapshot:sha256:[0-9a-f]{64}$"
_SELECTION_POLICY_FINGERPRINT = r"^tag-truth-selection-policy:sha256:[0-9a-f]{64}$"
_REVIEW_POLICY_FINGERPRINT = r"^tag-truth-review-policy:sha256:[0-9a-f]{64}$"
_NEAR_DUPLICATE_POLICY_FINGERPRINT = r"^tag-truth-near-duplicate-policy:sha256:[0-9a-f]{64}$"
_INVENTORY_FINGERPRINT = r"^tag-truth-reference-inventory:sha256:[0-9a-f]{64}$"
_PUBLISHED_SUITE_ID = r"^tag-truth-published-consensus:sha256:[0-9a-f]{64}$"
_PUBLICATION_ID = r"^tag-truth-publication:sha256:[0-9a-f]{64}$"
_CHAIN_BINDING_ID = r"^tag-truth-publication-chain:sha256:[0-9a-f]{64}$"

_EXPECTED_EVIDENCE_BLOCKERS: tuple[EvidenceQualificationBlocker, ...] = (
    "external_reviewer_identity_not_authenticated",
    "external_selector_identity_not_authenticated",
    "git_host_and_remote_not_authenticated",
    "near_duplicate_calibration_truth_unavailable",
    "near_duplicate_policy_not_approved",
    "review_policy_not_approved",
    "selection_policy_not_approved",
)
_EXPECTED_NEAR_DUPLICATE_BLOCKERS: tuple[
    NearDuplicateQualificationBlocker,
    ...,
] = (
    "calibration_truth_unavailable",
    "policy_not_approved",
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


def _path_scopes_overlap(left: str, right: str) -> bool:
    left_path = PurePosixPath(left)
    right_path = PurePosixPath(right)
    return left_path.is_relative_to(right_path) or right_path.is_relative_to(left_path)


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={identity_field})


class PublishedReceiptReference(_FrozenModel):
    round_id: Annotated[str, Field(pattern=r"^round-[a-z0-9]+(?:-[a-z0-9]+)*$")]
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]
    reviewer: ReviewerIdentity
    blinding: ReviewerBlindingAttestation
    recorded_at: Annotated[
        str,
        Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"),
    ]
    reviewed_case_ids: tuple[Annotated[str, Field(pattern=_CASE_ID)], ...]

    @field_validator("reviewed_case_ids", mode="before")
    @classmethod
    def parse_case_ids(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "published receipt reviewed case IDs")

    @field_validator("reviewed_case_ids")
    @classmethod
    def validate_case_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("published receipt must cover at least one case")
        return _sorted_unique(value, "published receipt reviewed case IDs")


class PublishedConsensusAxis(_FrozenModel):
    status: Literal["agreed_resolved"]
    label: Literal["positive", "negative"]
    metric_eligible: Literal[True]
    evidence_lines: tuple[Annotated[int, Field(ge=1)], ...]
    rationale_votes: tuple[AxisRationaleVote, ...]

    @field_validator("evidence_lines", "rationale_votes", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"published axis {getattr(info, 'field_name', 'sequence')}")

    @model_validator(mode="after")
    def validate_axis(self) -> PublishedConsensusAxis:
        if not self.evidence_lines:
            raise ValueError("published consensus axis requires evidence lines")
        if self.evidence_lines != tuple(sorted(set(self.evidence_lines))):
            raise ValueError("published consensus axis evidence lines must be sorted and unique")
        vote_keys = tuple(
            (vote.round_id, vote.reviewer_id, vote.receipt_id) for vote in self.rationale_votes
        )
        if len(vote_keys) != 2 or vote_keys != tuple(sorted(set(vote_keys))):
            raise ValueError("published consensus axis requires two sorted unique rationale votes")
        for attribute, label in (
            ("round_id", "round IDs"),
            ("reviewer_id", "reviewer IDs"),
            ("receipt_id", "receipt IDs"),
        ):
            values = tuple(getattr(vote, attribute) for vote in self.rationale_votes)
            if len(values) != len(set(values)):
                raise ValueError(f"published consensus axis {label} must be distinct")
        return self


class PublishedNearDuplicateCaseBinding(_FrozenModel):
    file_content_sha256: Annotated[str, Field(pattern=_SHA256)]
    unit_content_sha256: Annotated[str, Field(pattern=_SHA256)]
    unit_start_line: Annotated[int, Field(ge=1)]
    unit_end_line: Annotated[int, Field(ge=1)]
    file_decision: Literal["clear"]
    unit_decision: Literal["clear"]
    overall_decision: Literal["clear"]

    @model_validator(mode="after")
    def validate_span(self) -> PublishedNearDuplicateCaseBinding:
        if self.unit_start_line > self.unit_end_line:
            raise ValueError("published near-duplicate Unit span is inverted")
        return self


class PublishedConsensusCase(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    source_alias: Annotated[str, Field(pattern=r"^src[0-9]{3,6}$")]
    probe_line: Annotated[int, Field(ge=1)]
    selection_stratum_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    selection_rank: Annotated[int, Field(ge=1)]
    review_unit: ReviewUnitSelection
    votes: tuple[ConsensusVote, ...]
    exact: PublishedConsensusAxis
    routing: PublishedConsensusAxis
    near_duplicate: PublishedNearDuplicateCaseBinding

    @field_validator("votes", mode="before")
    @classmethod
    def parse_votes(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "published consensus votes")

    @model_validator(mode="after")
    def validate_case(self) -> PublishedConsensusCase:
        span = self.review_unit.source_span
        if not span.start_line <= self.probe_line <= span.end_line:
            raise ValueError("published probe line must stay inside the agreed ReviewUnit")
        if (
            self.near_duplicate.unit_start_line != span.start_line
            or self.near_duplicate.unit_end_line != span.end_line
        ):
            raise ValueError("published near-duplicate span differs from the agreed ReviewUnit")
        if any(
            line < span.start_line or line > span.end_line for line in self.exact.evidence_lines
        ):
            raise ValueError("published exact evidence must stay inside the agreed ReviewUnit")
        vote_keys = tuple((vote.round_id, vote.reviewer_id, vote.receipt_id) for vote in self.votes)
        if len(vote_keys) != 2 or vote_keys != tuple(sorted(set(vote_keys))):
            raise ValueError("published case requires two sorted unique complete votes")
        if any(vote.review_unit != self.review_unit for vote in self.votes):
            raise ValueError("published complete votes differ from the agreed ReviewUnit")
        self._validate_axis_votes("exact", self.exact)
        self._validate_axis_votes("routing", self.routing)
        return self

    def _validate_axis_votes(
        self,
        axis_name: Literal["exact", "routing"],
        published: PublishedConsensusAxis,
    ) -> None:
        decisions = tuple(getattr(vote, axis_name) for vote in self.votes)
        if any(
            decision.label != published.label or decision.abstain_reason is not None
            for decision in decisions
        ):
            raise ValueError(f"published {axis_name} label differs from preserved votes")
        expected_evidence = tuple(
            sorted({line for decision in decisions for line in decision.evidence_lines})
        )
        if published.evidence_lines != expected_evidence:
            raise ValueError(f"published {axis_name} evidence differs from preserved votes")
        expected_rationales = tuple(
            AxisRationaleVote(
                round_id=vote.round_id,
                reviewer_id=vote.reviewer_id,
                receipt_id=vote.receipt_id,
                rationale=getattr(vote, axis_name).rationale,
            )
            for vote in self.votes
        )
        if published.rationale_votes != expected_rationales:
            raise ValueError(f"published {axis_name} rationales differ from preserved votes")


class PublishedReviewChain(_FrozenModel):
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    receipt_references: tuple[PublishedReceiptReference, ...]
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID)]
    review_policy_fingerprint: Annotated[str, Field(pattern=_REVIEW_POLICY_FINGERPRINT)]
    consensus_status: Literal["complete"]
    consensus_case_ids: tuple[Annotated[str, Field(pattern=_CASE_ID)], ...]

    @field_validator("receipt_references", "consensus_case_ids", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(
            value,
            f"published review chain {getattr(info, 'field_name', 'sequence')}",
        )

    @model_validator(mode="after")
    def validate_review_chain(self) -> PublishedReviewChain:
        keys = tuple(
            (
                item.round_id,
                item.reviewer.reviewer_id,
                item.receipt_id,
            )
            for item in self.receipt_references
        )
        if len(keys) != 2 or keys != tuple(sorted(set(keys))):
            raise ValueError("published review chain requires two sorted unique receipts")
        for attribute, label in (
            ("round_id", "round IDs"),
            ("receipt_id", "receipt IDs"),
        ):
            values = tuple(getattr(item, attribute) for item in self.receipt_references)
            if len(values) != len(set(values)):
                raise ValueError(f"published review chain {label} must be distinct")
        reviewer_ids = tuple(item.reviewer.reviewer_id for item in self.receipt_references)
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("published review chain reviewer IDs must be distinct")
        if not self.consensus_case_ids:
            raise ValueError("published review chain requires consensus case coverage")
        _sorted_unique(self.consensus_case_ids, "published consensus case IDs")
        for reference in self.receipt_references:
            if reference.reviewed_case_ids != self.consensus_case_ids:
                raise ValueError("published receipt coverage differs from consensus")
        return self


class PublicationReadiness(_FrozenModel):
    evidence_qualification_status: Literal["not_qualified"]
    evidence_qualification_blockers: tuple[EvidenceQualificationBlocker, ...]
    near_duplicate_qualification_status: Literal["not_qualified_policy_unapproved"]
    near_duplicate_qualification_blockers: tuple[
        NearDuplicateQualificationBlocker,
        ...,
    ]
    candidate_execution_status: Literal["not_run"]
    quality_gate_status: Literal["not_evaluated"]
    activation_status: Literal["not_evaluated"]

    @field_validator(
        "evidence_qualification_blockers",
        "near_duplicate_qualification_blockers",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"readiness {getattr(info, 'field_name', 'sequence')}")

    @model_validator(mode="after")
    def validate_readiness(self) -> PublicationReadiness:
        if self.evidence_qualification_blockers != _EXPECTED_EVIDENCE_BLOCKERS:
            raise ValueError("publication must retain every current evidence blocker")
        if self.near_duplicate_qualification_blockers != (_EXPECTED_NEAR_DUPLICATE_BLOCKERS):
            raise ValueError(
                "publication must retain the frozen near-duplicate qualification blockers"
            )
        return self


class PublicationChainBinding(_FrozenModel):
    chain_binding_id: Annotated[str, Field(pattern=_CHAIN_BINDING_ID)]
    tag_contract_fingerprint: Annotated[str, Field(pattern=_TAG_CONTRACT_FINGERPRINT)]
    feature_config_fingerprint: Annotated[str, Field(pattern=_FEATURE_CONFIG_FINGERPRINT)]
    selection_policy_fingerprint: Annotated[
        str,
        Field(pattern=_SELECTION_POLICY_FINGERPRINT),
    ]
    review_policy_fingerprint: Annotated[str, Field(pattern=_REVIEW_POLICY_FINGERPRINT)]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    receipt_ids: tuple[Annotated[str, Field(pattern=_RECEIPT_ID)], ...]
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID)]
    provenance_verification_id: Annotated[str, Field(pattern=_PROVENANCE_ID)]
    near_duplicate_screening_id: Annotated[str, Field(pattern=_SCREENING_ID)]
    near_duplicate_policy_fingerprint: Annotated[
        str,
        Field(pattern=_NEAR_DUPLICATE_POLICY_FINGERPRINT),
    ]
    seal_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    seal_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    sealed_artifacts: tuple[TagTruthV2SealedArtifact, ...]
    candidate_freeze_id: Annotated[str, Field(pattern=_CANDIDATE_FREEZE_ID)]
    candidate_commit: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    candidate_project_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    source_repository_source_id: Annotated[
        str,
        Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"),
    ]
    source_repository_origin: Annotated[str, Field(min_length=1)]
    source_repository_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    source_repository_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    exposure_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    exposure_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    development_truth_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    development_truth_suite_fingerprint: Annotated[
        str,
        Field(pattern=r"^tag-retrieval-truth:sha256:[0-9a-f]{64}$"),
    ]
    reference_inventories: tuple[ReferenceInventorySummary, ...]

    @field_validator(
        "receipt_ids",
        "sealed_artifacts",
        "reference_inventories",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"publication chain {getattr(info, 'field_name', 'sequence')}")

    @field_validator("source_repository_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return _single_line(value, "publication source repository origin")

    @model_validator(mode="after")
    def validate_chain(self) -> PublicationChainBinding:
        if len(self.receipt_ids) != 2 or self.receipt_ids != tuple(sorted(set(self.receipt_ids))):
            raise ValueError("publication chain requires two sorted unique receipt IDs")
        artifact_counts = Counter(item.role for item in self.sealed_artifacts)
        if artifact_counts != Counter({"selection": 1, "packet": 1, "receipt": 2, "consensus": 1}):
            raise ValueError("publication chain requires the complete five-artifact seal")
        role_order = {"selection": 0, "packet": 1, "receipt": 2, "consensus": 3}
        artifact_keys = tuple(
            (role_order[item.role], item.logical_id, item.path) for item in self.sealed_artifacts
        )
        if artifact_keys != tuple(sorted(artifact_keys)):
            raise ValueError("publication sealed artifacts must use canonical ordering")
        artifact_paths = tuple(item.path for item in self.sealed_artifacts)
        logical_ids = tuple(item.logical_id for item in self.sealed_artifacts)
        if len(artifact_paths) != len(set(artifact_paths)) or len(logical_ids) != len(
            set(logical_ids)
        ):
            raise ValueError("publication sealed artifact paths and IDs must be unique")
        by_role = {
            role: tuple(item.logical_id for item in self.sealed_artifacts if item.role == role)
            for role in role_order
        }
        if (
            by_role["selection"] != (self.selection_id,)
            or by_role["packet"] != (self.packet_id,)
            or tuple(sorted(by_role["receipt"])) != self.receipt_ids
            or by_role["consensus"] != (self.consensus_id,)
        ):
            raise ValueError("publication sealed artifacts differ from chain IDs")
        expected_roles = ("candidate_project", "exposure", "development_truth")
        if tuple(item.role for item in self.reference_inventories) != expected_roles:
            raise ValueError("publication chain requires the three canonical inventories")
        fingerprints = tuple(item.inventory_fingerprint for item in self.reference_inventories)
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("publication chain inventory fingerprints must be unique")
        candidate, exposure, development = self.reference_inventories
        if (
            candidate.scope != "entire_tracked_tree"
            or exposure.scope != "entire_tracked_tree"
            or development.scope != "registered_paths"
        ):
            raise ValueError("publication inventories use an invalid scope")
        if (
            candidate.repository_source_id != "arkts-code-reviewer"
            or candidate.revision != self.candidate_commit
            or candidate.tree_id != self.candidate_project_tree_id
        ):
            raise ValueError("candidate-project inventory differs from publication chain")
        if (
            exposure.repository_source_id != self.source_repository_source_id
            or exposure.revision != self.exposure_revision
            or exposure.tree_id != self.exposure_tree_id
        ):
            raise ValueError("exposure inventory differs from publication chain")
        if (
            development.repository_source_id != self.source_repository_source_id
            or development.revision != self.development_truth_revision
        ):
            raise ValueError("development inventory differs from publication chain")
        expected = canonical_hash(
            "tag-truth-publication-chain",
            _identity_payload(self, "chain_binding_id"),
        )
        if self.chain_binding_id != expected:
            raise ValueError("publication chain binding ID does not match its contents")
        return self


class PublishedTagTruthV2ConsensusSuiteV1(_FrozenModel):
    schema_version: Literal["tag-truth-v2-published-consensus-v1"]
    published_suite_id: Annotated[str, Field(pattern=_PUBLISHED_SUITE_ID)]
    suite_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    dataset_role: Literal["independent_blind_challenge"]
    truth_status: Literal["consensus"]
    natural_prevalence_claimed: Literal[False]
    chain_binding_id: Annotated[str, Field(pattern=_CHAIN_BINDING_ID)]
    readiness: PublicationReadiness
    tag_contract: TagContractSnapshot
    repository: TagTruthV2Repository
    sources: tuple[TagTruthV2Source, ...]
    review_chain: PublishedReviewChain
    cases: tuple[PublishedConsensusCase, ...]

    @field_validator(
        "sources",
        "cases",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"published suite {getattr(info, 'field_name', 'sequence')}")

    @model_validator(mode="after")
    def validate_suite(self) -> PublishedTagTruthV2ConsensusSuiteV1:
        aliases = tuple(source.alias for source in self.sources)
        if not aliases or aliases != tuple(sorted(set(aliases))):
            raise ValueError("published sources must be sorted by unique alias")
        paths = tuple(source.path for source in self.sources)
        content_hashes = tuple(source.content_sha256 for source in self.sources)
        family_ids = tuple(source.source_family_id for source in self.sources)
        if len(paths) != len(set(paths)) or len(content_hashes) != len(set(content_hashes)):
            raise ValueError("published source paths and content hashes must be unique")
        if len(family_ids) != len(set(family_ids)):
            raise ValueError("published source families must be unique")
        for index, family in enumerate(sorted(family_ids)):
            for other in sorted(family_ids)[index + 1 :]:
                if _path_scopes_overlap(family, other):
                    raise ValueError("published source families cannot overlap by ancestry")
        case_ids = tuple(case.case_id for case in self.cases)
        if not case_ids or case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("published cases must be sorted by unique case ID")
        case_aliases = tuple(case.source_alias for case in self.cases)
        if tuple(sorted(case_aliases)) != aliases or len(case_aliases) != len(set(case_aliases)):
            raise ValueError("published suite requires exactly one case per source")
        ranks = tuple(case.selection_rank for case in self.cases)
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("published selection ranks must be contiguous from one")
        if self.review_chain.consensus_case_ids != case_ids:
            raise ValueError("published review chain must cover the complete case set")
        receipt_keys = tuple(
            (
                item.round_id,
                item.reviewer.reviewer_id,
                item.receipt_id,
            )
            for item in self.review_chain.receipt_references
        )
        for case in self.cases:
            vote_keys = tuple(
                (vote.round_id, vote.reviewer_id, vote.receipt_id) for vote in case.votes
            )
            if vote_keys != receipt_keys:
                raise ValueError("published case votes differ from suite review chain")
        unit_hashes = tuple(case.near_duplicate.unit_content_sha256 for case in self.cases)
        if len(unit_hashes) != len(set(unit_hashes)):
            raise ValueError("published ReviewUnit content hashes must be unique")
        sources_by_alias = {source.alias: source for source in self.sources}
        for source in self.sources:
            if (
                source.repository_source_id != self.repository.source_id
                or source.origin != self.repository.origin
                or source.revision != self.repository.revision
            ):
                raise ValueError("published source repository binding drift")
        for case in self.cases:
            source = sources_by_alias[case.source_alias]
            if case.near_duplicate.file_content_sha256 != source.content_sha256:
                raise ValueError("published file hash differs from its source")
            if case.review_unit.source_span.end_line > source.line_count or any(
                line > source.line_count for line in case.routing.evidence_lines
            ):
                raise ValueError("published case evidence exceeds its source")
        expected = canonical_hash(
            "tag-truth-published-consensus",
            _identity_payload(self, "published_suite_id"),
        )
        if self.published_suite_id != expected:
            raise ValueError("published suite ID does not match its complete contents")
        return self


class _TagTruthV2PublicationPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-publication-v1"]
    publication_status: PublicationStatus
    publication_blockers: tuple[PublicationBlocker, ...]
    chain_binding: PublicationChainBinding
    published_suite_fingerprint: (
        Annotated[
            str,
            Field(pattern=_PUBLISHED_SUITE_ID),
        ]
        | None
    )
    published_suite: PublishedTagTruthV2ConsensusSuiteV1 | None
    readiness: PublicationReadiness

    @field_validator("publication_blockers", mode="before")
    @classmethod
    def parse_blockers(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "publication blockers")

    @model_validator(mode="after")
    def validate_payload(self) -> _TagTruthV2PublicationPayload:
        if self.publication_blockers != tuple(sorted(set(self.publication_blockers))):
            raise ValueError("publication blockers must be sorted and unique")
        if self.publication_status == "blocked_no_suite":
            if (
                not self.publication_blockers
                or self.published_suite is not None
                or self.published_suite_fingerprint is not None
            ):
                raise ValueError("blocked publication cannot contain a published suite")
            return self
        if (
            self.publication_blockers
            or self.published_suite is None
            or self.published_suite_fingerprint != self.published_suite.published_suite_id
        ):
            raise ValueError("published consensus requires one matching unblocked suite")
        suite = self.published_suite
        chain = self.chain_binding
        review_chain = suite.review_chain
        if suite.chain_binding_id != chain.chain_binding_id:
            raise ValueError("published suite differs from publication chain binding")
        if suite.readiness != self.readiness:
            raise ValueError("published suite readiness differs from publication readiness")
        if suite.tag_contract.contract_fingerprint != chain.tag_contract_fingerprint:
            raise ValueError("published suite Tag contract differs from publication chain")
        if (
            suite.repository.source_id != chain.source_repository_source_id
            or suite.repository.origin != chain.source_repository_origin
            or suite.repository.revision != chain.source_repository_revision
        ):
            raise ValueError("published suite repository differs from publication chain")
        if (
            review_chain.selection_id != chain.selection_id
            or review_chain.packet_id != chain.packet_id
            or tuple(sorted(item.receipt_id for item in review_chain.receipt_references))
            != chain.receipt_ids
            or review_chain.consensus_id != chain.consensus_id
            or review_chain.review_policy_fingerprint != chain.review_policy_fingerprint
        ):
            raise ValueError("published review chain differs from publication chain")
        receipt_keys = tuple(
            (
                item.round_id,
                item.reviewer.reviewer_id,
                item.receipt_id,
            )
            for item in review_chain.receipt_references
        )
        for case in suite.cases:
            case_keys = tuple(
                (vote.round_id, vote.reviewer_id, vote.receipt_id) for vote in case.votes
            )
            if case_keys != receipt_keys:
                raise ValueError("published case votes differ from the review chain")
        return self


class TagTruthV2PublicationV1(_TagTruthV2PublicationPayload):
    publication_id: Annotated[str, Field(pattern=_PUBLICATION_ID)]

    @model_validator(mode="after")
    def validate_publication_id(self) -> TagTruthV2PublicationV1:
        expected = canonical_hash(
            "tag-truth-publication",
            _identity_payload(self, "publication_id"),
        )
        if self.publication_id != expected:
            raise ValueError("publication ID does not match its complete contents")
        return self


def publication_payload_with_id(payload: Mapping[str, object]) -> dict[str, object]:
    if "publication_id" in payload:
        raise ValueError("unsealed publication payload cannot contain publication_id")
    canonical = _TagTruthV2PublicationPayload.model_validate_json(canonical_json(dict(payload)))
    result = canonical.model_dump(mode="json")
    result["publication_id"] = canonical_hash("tag-truth-publication", result)
    return result


def _canonical_model[TModel: BaseModel](value: TModel, model: type[TModel]) -> TModel:
    return model.model_validate_json(canonical_json(value.model_dump(mode="json")))


def _ordered_receipts(
    receipts: Sequence[TagTruthV2ReviewReceipt],
) -> tuple[TagTruthV2ReviewReceipt, TagTruthV2ReviewReceipt]:
    if len(receipts) != 2:
        raise ValueError("publication requires exactly two review receipts")
    ordered = tuple(
        sorted(
            (_canonical_model(receipt, TagTruthV2ReviewReceipt) for receipt in receipts),
            key=lambda item: (
                item.round_id,
                item.reviewer.reviewer_id,
                item.receipt_id,
            ),
        )
    )
    if len(ordered) != 2:
        raise ValueError("publication requires exactly two review receipts")
    return ordered


def _validate_chain_bindings(
    *,
    selection: TagTruthV2Selection,
    packet: TagTruthV2ReviewPacket,
    receipts: tuple[TagTruthV2ReviewReceipt, TagTruthV2ReviewReceipt],
    consensus: TagTruthV2Consensus,
    provenance: TagTruthV2ProvenanceVerification,
    near_duplicate_policy: TagTruthV2NearDuplicatePolicy,
    screening: TagTruthV2NearDuplicateVerification,
) -> None:
    verify_tag_truth_v2_review_packet(packet, selection)
    for receipt in receipts:
        validate_tag_truth_v2_review_receipt(receipt, packet)
    verify_tag_truth_v2_consensus(consensus, packet, receipts)
    expected_receipt_ids = tuple(sorted(receipt.receipt_id for receipt in receipts))
    if provenance.selection_id != selection.selection_id:
        raise ValueError("publication provenance differs from selection")
    if provenance.packet_id != packet.packet_id:
        raise ValueError("publication provenance differs from review packet")
    if provenance.receipt_ids != expected_receipt_ids:
        raise ValueError("publication provenance differs from review receipts")
    if provenance.consensus_id != consensus.consensus_id:
        raise ValueError("publication provenance differs from consensus")
    if screening.policy_fingerprint != near_duplicate_policy.policy_fingerprint:
        raise ValueError("publication screening differs from near-duplicate policy")
    if screening.provenance_verification_id != provenance.verification_id:
        raise ValueError("publication screening differs from provenance verification")
    for field_name in (
        "seal_revision",
        "seal_tree_id",
        "candidate_commit",
        "source_repository_revision",
        "source_repository_tree_id",
        "exposure_revision",
        "exposure_tree_id",
        "selection_id",
        "packet_id",
        "receipt_ids",
        "consensus_id",
    ):
        left = getattr(screening, field_name)
        right = getattr(provenance, field_name)
        if left != right:
            raise ValueError(f"publication screening/provenance {field_name} drift")


def _publication_blockers(
    consensus: TagTruthV2Consensus,
    screening: TagTruthV2NearDuplicateVerification,
) -> tuple[PublicationBlocker, ...]:
    blockers: list[PublicationBlocker] = list(consensus.consensus_blockers)
    if screening.screening_outcome == "potential_duplicate":
        blockers.append("near_duplicate_potential_duplicate")
    elif screening.screening_outcome == "review_required":
        blockers.append("near_duplicate_review_required")
    return tuple(sorted(set(blockers)))


def _chain_binding(
    *,
    selection: TagTruthV2Selection,
    packet: TagTruthV2ReviewPacket,
    receipts: tuple[TagTruthV2ReviewReceipt, TagTruthV2ReviewReceipt],
    consensus: TagTruthV2Consensus,
    provenance: TagTruthV2ProvenanceVerification,
    near_duplicate_policy: TagTruthV2NearDuplicatePolicy,
    screening: TagTruthV2NearDuplicateVerification,
) -> PublicationChainBinding:
    payload: dict[str, object] = {
        "tag_contract_fingerprint": selection.tag_contract.contract_fingerprint,
        "feature_config_fingerprint": (selection.candidate_freeze.feature_config_fingerprint),
        "selection_policy_fingerprint": (selection.selection_policy.policy_fingerprint),
        "review_policy_fingerprint": packet.review_policy.policy_fingerprint,
        "selection_id": selection.selection_id,
        "packet_id": packet.packet_id,
        "receipt_ids": sorted(receipt.receipt_id for receipt in receipts),
        "consensus_id": consensus.consensus_id,
        "provenance_verification_id": provenance.verification_id,
        "near_duplicate_screening_id": screening.screening_id,
        "near_duplicate_policy_fingerprint": near_duplicate_policy.policy_fingerprint,
        "seal_revision": provenance.seal_revision,
        "seal_tree_id": provenance.seal_tree_id,
        "sealed_artifacts": [
            artifact.model_dump(mode="json") for artifact in provenance.sealed_artifacts
        ],
        "candidate_freeze_id": selection.candidate_freeze.candidate_freeze_id,
        "candidate_commit": selection.candidate_freeze.candidate_commit,
        "candidate_project_tree_id": screening.candidate_project_tree_id,
        "source_repository_source_id": selection.repository.source_id,
        "source_repository_origin": selection.repository.origin,
        "source_repository_revision": provenance.source_repository_revision,
        "source_repository_tree_id": provenance.source_repository_tree_id,
        "exposure_revision": provenance.exposure_revision,
        "exposure_tree_id": provenance.exposure_tree_id,
        "development_truth_revision": screening.development_truth_revision,
        "development_truth_suite_fingerprint": (screening.development_truth_suite_fingerprint),
        "reference_inventories": [
            inventory.model_dump(mode="json") for inventory in screening.reference_inventories
        ],
    }
    payload["chain_binding_id"] = canonical_hash(
        "tag-truth-publication-chain",
        payload,
    )
    return PublicationChainBinding.model_validate_json(canonical_json(payload))


def _readiness(
    screening: TagTruthV2NearDuplicateVerification,
) -> PublicationReadiness:
    if screening.near_duplicate_qualification_status != "not_qualified_policy_unapproved":
        raise ValueError(
            "publication v1 cannot consume a different near-duplicate qualification contract"
        )
    return PublicationReadiness(
        evidence_qualification_status="not_qualified",
        evidence_qualification_blockers=_EXPECTED_EVIDENCE_BLOCKERS,
        near_duplicate_qualification_status="not_qualified_policy_unapproved",
        near_duplicate_qualification_blockers=(_EXPECTED_NEAR_DUPLICATE_BLOCKERS),
        candidate_execution_status="not_run",
        quality_gate_status="not_evaluated",
        activation_status="not_evaluated",
    )


def _published_axis(axis: AxisConsensus) -> PublishedConsensusAxis:
    if axis.status != "agreed_resolved" or axis.label not in {"positive", "negative"}:
        raise ValueError("publication cannot project an unresolved or abstained axis")
    label: Literal["positive", "negative"] = "positive" if axis.label == "positive" else "negative"
    return PublishedConsensusAxis(
        status="agreed_resolved",
        label=label,
        metric_eligible=True,
        evidence_lines=axis.evidence_lines,
        rationale_votes=axis.rationale_votes,
    )


def _unit_text(source_text: str, start_line: int, end_line: int) -> str:
    lines = source_text.splitlines(keepends=True)
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise ValueError("published ReviewUnit span exceeds packet source")
    return "".join(lines[start_line - 1 : end_line])


def _published_suite(
    *,
    selection: TagTruthV2Selection,
    packet: TagTruthV2ReviewPacket,
    receipts: tuple[TagTruthV2ReviewReceipt, TagTruthV2ReviewReceipt],
    consensus: TagTruthV2Consensus,
    screening: TagTruthV2NearDuplicateVerification,
    chain_binding: PublicationChainBinding,
    readiness: PublicationReadiness,
) -> PublishedTagTruthV2ConsensusSuiteV1:
    selection_cases = {case.case_id: case for case in selection.cases}
    packet_cases = {case.case_id: case for case in packet.cases}
    consensus_cases = {case.case_id: case for case in consensus.cases}
    screening_cases = {case.case_id: case for case in screening.cases}
    expected_case_ids = tuple(sorted(selection_cases))
    if not (
        tuple(sorted(packet_cases))
        == tuple(sorted(consensus_cases))
        == tuple(sorted(screening_cases))
        == expected_case_ids
    ):
        raise ValueError("publication inputs do not share one complete case set")
    sources_by_alias = {source.alias: source for source in selection.sources}
    published_cases: list[PublishedConsensusCase] = []
    for case_id in expected_case_ids:
        selected = selection_cases[case_id]
        packet_case = packet_cases[case_id]
        agreed = consensus_cases[case_id]
        screened = screening_cases[case_id]
        if agreed.review_unit_status != "agreed" or agreed.review_unit is None:
            raise ValueError("publication cannot project an unresolved ReviewUnit")
        if (
            screened.file_decision != "clear"
            or screened.unit_decision != "clear"
            or screened.overall_decision != "clear"
            or screened.unit_content_sha256 is None
            or screened.unit_start_line is None
            or screened.unit_end_line is None
        ):
            raise ValueError("publication cannot project a non-clear near-duplicate case")
        source = sources_by_alias.get(selected.source_alias)
        if source is None:
            raise ValueError("publication case references an unknown source")
        source_bytes = packet_case.source_text.encode("utf-8")
        if (
            bytes_hash(source_bytes) != source.content_sha256
            or bytes_hash(source_bytes) != screened.file_content_sha256
        ):
            raise ValueError("publication source bytes differ from sealed source identities")
        span = agreed.review_unit.source_span
        unit_text = _unit_text(packet_case.source_text, span.start_line, span.end_line)
        if bytes_hash(unit_text.encode("utf-8")) != screened.unit_content_sha256:
            raise ValueError("publication ReviewUnit bytes differ from screening identity")
        published_cases.append(
            PublishedConsensusCase(
                case_id=case_id,
                source_alias=selected.source_alias,
                probe_line=selected.probe_line,
                selection_stratum_id=selected.proxy_stratum_id,
                selection_rank=selected.selection_rank,
                review_unit=agreed.review_unit,
                votes=agreed.votes,
                exact=_published_axis(agreed.exact),
                routing=_published_axis(agreed.routing),
                near_duplicate=PublishedNearDuplicateCaseBinding(
                    file_content_sha256=screened.file_content_sha256,
                    unit_content_sha256=screened.unit_content_sha256,
                    unit_start_line=screened.unit_start_line,
                    unit_end_line=screened.unit_end_line,
                    file_decision="clear",
                    unit_decision="clear",
                    overall_decision="clear",
                ),
            )
        )
    review_chain = PublishedReviewChain(
        selection_id=selection.selection_id,
        packet_id=packet.packet_id,
        receipt_references=tuple(
            PublishedReceiptReference(
                round_id=receipt.round_id,
                receipt_id=receipt.receipt_id,
                reviewer=receipt.reviewer,
                blinding=receipt.blinding,
                recorded_at=receipt.recorded_at,
                reviewed_case_ids=tuple(decision.case_id for decision in receipt.decisions),
            )
            for receipt in receipts
        ),
        consensus_id=consensus.consensus_id,
        review_policy_fingerprint=packet.review_policy.policy_fingerprint,
        consensus_status="complete",
        consensus_case_ids=tuple(case.case_id for case in consensus.cases),
    )
    payload: dict[str, object] = {
        "schema_version": TAG_TRUTH_V2_PUBLISHED_CONSENSUS_SCHEMA_VERSION,
        "suite_id": selection.suite_id,
        "dataset_role": "independent_blind_challenge",
        "truth_status": "consensus",
        "natural_prevalence_claimed": False,
        "chain_binding_id": chain_binding.chain_binding_id,
        "readiness": readiness.model_dump(mode="json"),
        "tag_contract": selection.tag_contract.model_dump(mode="json"),
        "repository": selection.repository.model_dump(mode="json"),
        "sources": [source.model_dump(mode="json") for source in selection.sources],
        "review_chain": review_chain.model_dump(mode="json"),
        "cases": [case.model_dump(mode="json") for case in published_cases],
    }
    payload["published_suite_id"] = canonical_hash(
        "tag-truth-published-consensus",
        payload,
    )
    return PublishedTagTruthV2ConsensusSuiteV1.model_validate_json(canonical_json(payload))


def _build_tag_truth_v2_publication(
    *,
    selection: TagTruthV2Selection,
    packet: TagTruthV2ReviewPacket,
    receipts: Sequence[TagTruthV2ReviewReceipt],
    consensus: TagTruthV2Consensus,
    provenance: TagTruthV2ProvenanceVerification,
    near_duplicate_policy: TagTruthV2NearDuplicatePolicy,
    screening: TagTruthV2NearDuplicateVerification,
) -> TagTruthV2PublicationV1:
    selection = _canonical_model(selection, TagTruthV2Selection)
    packet = _canonical_model(packet, TagTruthV2ReviewPacket)
    ordered_receipts = _ordered_receipts(receipts)
    consensus = _canonical_model(consensus, TagTruthV2Consensus)
    provenance = _canonical_model(
        provenance,
        TagTruthV2ProvenanceVerification,
    )
    near_duplicate_policy = _canonical_model(
        near_duplicate_policy,
        TagTruthV2NearDuplicatePolicy,
    )
    screening = _canonical_model(
        screening,
        TagTruthV2NearDuplicateVerification,
    )
    _validate_chain_bindings(
        selection=selection,
        packet=packet,
        receipts=ordered_receipts,
        consensus=consensus,
        provenance=provenance,
        near_duplicate_policy=near_duplicate_policy,
        screening=screening,
    )
    blockers = _publication_blockers(consensus, screening)
    chain_binding = _chain_binding(
        selection=selection,
        packet=packet,
        receipts=ordered_receipts,
        consensus=consensus,
        provenance=provenance,
        near_duplicate_policy=near_duplicate_policy,
        screening=screening,
    )
    readiness = _readiness(screening)
    published_suite = (
        None
        if blockers
        else _published_suite(
            selection=selection,
            packet=packet,
            receipts=ordered_receipts,
            consensus=consensus,
            screening=screening,
            chain_binding=chain_binding,
            readiness=readiness,
        )
    )
    payload: dict[str, object] = {
        "schema_version": TAG_TRUTH_V2_PUBLICATION_SCHEMA_VERSION,
        "publication_status": (
            "blocked_no_suite" if blockers else "published_consensus_not_qualified"
        ),
        "publication_blockers": list(blockers),
        "chain_binding": chain_binding.model_dump(mode="json"),
        "published_suite_fingerprint": (
            published_suite.published_suite_id if published_suite is not None else None
        ),
        "published_suite": (
            published_suite.model_dump(mode="json") if published_suite is not None else None
        ),
        "readiness": readiness.model_dump(mode="json"),
    }
    sealed = publication_payload_with_id(payload)
    return TagTruthV2PublicationV1.model_validate_json(canonical_json(sealed))


def _parse_campaign_artifacts(
    artifacts: Sequence[CapturedCommittedArtifact],
) -> tuple[
    TagTruthV2Selection,
    TagTruthV2ReviewPacket,
    tuple[TagTruthV2ReviewReceipt, TagTruthV2ReviewReceipt],
    TagTruthV2Consensus,
]:
    captured = tuple(artifacts)
    counts = Counter(item.role for item in captured)
    if counts != Counter({"selection": 1, "packet": 1, "receipt": 2, "consensus": 1}):
        raise ValueError("publication requires the complete five-artifact seal")
    by_role: dict[str, list[CapturedCommittedArtifact]] = {}
    for artifact in captured:
        by_role.setdefault(artifact.role, []).append(artifact)
    selection = parse_tag_truth_v2_selection(by_role["selection"][0].raw_bytes)
    packet = parse_tag_truth_v2_review_packet(by_role["packet"][0].raw_bytes)
    receipts = _ordered_receipts(
        tuple(parse_tag_truth_v2_review_receipt(item.raw_bytes) for item in by_role["receipt"])
    )
    consensus = parse_tag_truth_v2_consensus(by_role["consensus"][0].raw_bytes)
    return selection, packet, receipts, consensus


def _scan_reference_inventories(
    *,
    repository_root: str | Path,
    source_root: str | Path,
    selection: TagTruthV2Selection,
    development_truth: DevelopmentTruthExclusionSnapshot,
    policy: TagTruthV2NearDuplicatePolicy,
) -> tuple[
    ScannedReferenceInventory,
    ScannedReferenceInventory,
    ScannedReferenceInventory,
]:
    scan_kwargs = {
        "maximum_blob_bytes": policy.maximum_blob_bytes,
        "maximum_total_reference_bytes": policy.maximum_total_reference_bytes,
        "maximum_inventory_entries": policy.maximum_inventory_entries,
    }
    return (
        scan_pinned_git_reference_inventory(
            repository_root,
            role="candidate_project",
            repository_source_id="arkts-code-reviewer",
            revision=selection.candidate_freeze.candidate_commit,
            expected_tree_id=None,
            included_paths=None,
            **scan_kwargs,
        ),
        scan_pinned_git_reference_inventory(
            source_root,
            role="exposure",
            repository_source_id=selection.repository.source_id,
            revision=selection.candidate_freeze.exposure_revision,
            expected_tree_id=selection.candidate_freeze.exposure_tree_id,
            included_paths=None,
            **scan_kwargs,
        ),
        scan_pinned_git_reference_inventory(
            source_root,
            role="development_truth",
            repository_source_id=development_truth.repository_source_id,
            revision=development_truth.repository_revision,
            expected_tree_id=None,
            included_paths=tuple(item.path for item in development_truth.sources),
            **scan_kwargs,
        ),
    )


def build_verified_tag_truth_v2_publication(
    *,
    repository_root: str | Path,
    seal_revision: str,
    seal_tree_id: str,
    artifacts: Sequence[CapturedCommittedArtifact],
    source_root: str | Path,
    development_truth: DevelopmentTruthExclusionSnapshot,
    provenance: TagTruthV2ProvenanceVerification,
    near_duplicate_policy: TagTruthV2NearDuplicatePolicy,
    screening: TagTruthV2NearDuplicateVerification,
) -> TagTruthV2PublicationV1:
    verify_tag_truth_v2_provenance_verification(
        provenance,
        seal_revision=seal_revision,
        seal_tree_id=seal_tree_id,
        artifacts=artifacts,
        source_root=source_root,
        development_truth=development_truth,
    )
    selection, packet, receipts, consensus = _parse_campaign_artifacts(artifacts)
    reference_inventories = _scan_reference_inventories(
        repository_root=repository_root,
        source_root=source_root,
        selection=selection,
        development_truth=development_truth,
        policy=near_duplicate_policy,
    )
    verify_tag_truth_v2_near_duplicate_verification(
        screening,
        policy=near_duplicate_policy,
        provenance=provenance,
        selection=selection,
        packet=packet,
        consensus=consensus,
        reference_inventories=reference_inventories,
    )
    return _build_tag_truth_v2_publication(
        selection=selection,
        packet=packet,
        receipts=receipts,
        consensus=consensus,
        provenance=provenance,
        near_duplicate_policy=near_duplicate_policy,
        screening=screening,
    )


def verify_tag_truth_v2_publication(
    publication: TagTruthV2PublicationV1,
    *,
    repository_root: str | Path,
    seal_revision: str,
    seal_tree_id: str,
    artifacts: Sequence[CapturedCommittedArtifact],
    source_root: str | Path,
    development_truth: DevelopmentTruthExclusionSnapshot,
    provenance: TagTruthV2ProvenanceVerification,
    near_duplicate_policy: TagTruthV2NearDuplicatePolicy,
    screening: TagTruthV2NearDuplicateVerification,
) -> None:
    canonical_publication = TagTruthV2PublicationV1.model_validate_json(
        canonical_json(publication.model_dump(mode="json"))
    )
    rebuilt = build_verified_tag_truth_v2_publication(
        repository_root=repository_root,
        seal_revision=seal_revision,
        seal_tree_id=seal_tree_id,
        artifacts=artifacts,
        source_root=source_root,
        development_truth=development_truth,
        provenance=provenance,
        near_duplicate_policy=near_duplicate_policy,
        screening=screening,
    )
    if canonical_publication != rebuilt:
        raise ValueError("Tag Truth v2 publication does not rebuild from sealed inputs")


def parse_tag_truth_v2_publication(raw: bytes) -> TagTruthV2PublicationV1:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        return TagTruthV2PublicationV1.model_validate_json(canonical_json(payload))
    except (
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        _DuplicateKeyError,
    ) as exc:
        raise ValueError(f"invalid Tag Truth v2 publication: {exc}") from exc


def load_tag_truth_v2_publication(path: str | Path) -> TagTruthV2PublicationV1:
    artifact = Path(path)
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError(f"Tag Truth v2 publication must be a regular non-symlink file: {artifact}")
    try:
        raw = artifact.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read Tag Truth v2 publication {artifact}: {exc}") from exc
    return parse_tag_truth_v2_publication(raw)


def published_suite_fingerprint(
    suite: PublishedTagTruthV2ConsensusSuiteV1,
) -> str:
    canonical_suite = PublishedTagTruthV2ConsensusSuiteV1.model_validate_json(
        canonical_json(suite.model_dump(mode="json"))
    )
    return canonical_suite.published_suite_id


__all__ = [
    "EvidenceQualificationBlocker",
    "NearDuplicateQualificationBlocker",
    "PublicationBlocker",
    "PublicationChainBinding",
    "PublicationReadiness",
    "PublicationStatus",
    "PublishedConsensusAxis",
    "PublishedConsensusCase",
    "PublishedNearDuplicateCaseBinding",
    "PublishedReceiptReference",
    "PublishedReviewChain",
    "PublishedTagTruthV2ConsensusSuiteV1",
    "TAG_TRUTH_V2_PUBLICATION_SCHEMA_VERSION",
    "TAG_TRUTH_V2_PUBLISHED_CONSENSUS_SCHEMA_VERSION",
    "TagTruthV2PublicationV1",
    "build_verified_tag_truth_v2_publication",
    "load_tag_truth_v2_publication",
    "parse_tag_truth_v2_publication",
    "publication_payload_with_id",
    "published_suite_fingerprint",
    "verify_tag_truth_v2_publication",
]
