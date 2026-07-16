from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    SemanticLabel,
    TagTruthV2LineSpan,
    canonical_hash,
    canonical_json,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    TagTruthV2ReviewPacket,
)

TAG_TRUTH_V2_REVIEW_RECEIPT_SCHEMA_VERSION = "tag-truth-v2-review-receipt-v1"
TAG_TRUTH_V2_CONSENSUS_SCHEMA_VERSION = "tag-truth-v2-consensus-v1"

_CASE_ID = r"^case-[0-9a-f]{16}$"
_SELECTION_ID = r"^tag-truth-selection:sha256:[0-9a-f]{64}$"
_PACKET_ID = r"^tag-truth-review-packet:sha256:[0-9a-f]{64}$"
_RECEIPT_ID = r"^tag-truth-review-receipt:sha256:[0-9a-f]{64}$"
_CONSENSUS_ID = r"^tag-truth-consensus:sha256:[0-9a-f]{64}$"
_TAG_CONTRACT_FINGERPRINT = r"^tag-contract-snapshot:sha256:[0-9a-f]{64}$"
_REVIEW_POLICY_FINGERPRINT = r"^tag-truth-review-policy:sha256:[0-9a-f]{64}$"
_REVIEWER_ID = r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"
_ROUND_ID = r"^round-[a-z0-9]+(?:-[a-z0-9]+)*$"
_TIMESTAMP = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"

# This is the ReviewUnit-v2 vocabulary frozen by Tag Truth v2. It is repeated
# here deliberately: loading review artifacts must not import the production
# parser, ReviewUnit builder, matcher, or routing graph.
_REVIEW_UNIT_KINDS = (
    "struct",
    "class",
    "function",
    "method",
    "build_method",
    "builder",
    "ui_block",
    "field_region",
    "import_region",
    "fallback",
)

AxisConsensusStatus = Literal["agreed_resolved", "agreed_abstain", "unresolved"]
ConsensusBlocker = Literal[
    "taxonomy_decision_required",
    "unresolved_review_disagreement",
]


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


def _utc_timestamp(value: str, context: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(f"{context} must be a valid UTC timestamp") from exc
    return value


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={identity_field})


class ReviewerIdentity(_FrozenModel):
    reviewer_id: Annotated[str, Field(pattern=_REVIEWER_ID)]
    reviewer_kind: Literal["human"]
    reviewer_role: Literal["arkts_domain_reviewer"]
    affiliation: Annotated[str, Field(min_length=1)]
    candidate_design_participant: Literal[False]
    selection_participant: Literal[False]

    @field_validator("affiliation")
    @classmethod
    def validate_affiliation(cls, value: str) -> str:
        return _single_line(value, "reviewer affiliation")


class ReviewerBlindingAttestation(_FrozenModel):
    candidate_output_seen: Literal[False]
    candidate_configuration_seen: Literal[False]
    selection_manifest_seen: Literal[False]
    review_completed_before_unblinding: Literal[True]
    attested_at: Annotated[str, Field(pattern=_TIMESTAMP)]

    @field_validator("attested_at")
    @classmethod
    def validate_attested_at(cls, value: str) -> str:
        return _utc_timestamp(value, "reviewer attested_at")


class ReviewUnitSelection(_FrozenModel):
    unit_kind: str
    qualified_symbol: Annotated[str, Field(min_length=1)]
    source_span: TagTruthV2LineSpan

    @field_validator("unit_kind")
    @classmethod
    def validate_unit_kind(cls, value: str) -> str:
        if value not in _REVIEW_UNIT_KINDS:
            raise ValueError(f"unsupported ReviewUnit kind: {value}")
        return value

    @field_validator("qualified_symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return _single_line(value, "reviewer-selected qualified symbol")


class AxisReviewDecision(_FrozenModel):
    label: SemanticLabel
    evidence_lines: tuple[Annotated[int, Field(ge=1)], ...]
    rationale: Annotated[str, Field(min_length=1)]
    abstain_reason: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")] | None = None

    @field_validator("evidence_lines", mode="before")
    @classmethod
    def parse_evidence_lines(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "review decision evidence_lines")

    @field_validator("evidence_lines")
    @classmethod
    def validate_evidence_lines(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("review decision evidence_lines must be non-empty, sorted, and unique")
        return value

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str) -> str:
        return _single_line(value, "review decision rationale")

    @model_validator(mode="after")
    def validate_resolution(self) -> AxisReviewDecision:
        if self.label == "needs_taxonomy_decision":
            if self.abstain_reason is None:
                raise ValueError("needs_taxonomy_decision requires an abstain_reason")
        elif self.abstain_reason is not None:
            raise ValueError("resolved positive/negative decisions cannot declare abstain_reason")
        return self


class TagTruthV2ReviewDecision(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    review_unit: ReviewUnitSelection
    exact: AxisReviewDecision
    routing: AxisReviewDecision

    @model_validator(mode="after")
    def validate_exact_evidence_scope(self) -> TagTruthV2ReviewDecision:
        span = self.review_unit.source_span
        if any(
            line < span.start_line or line > span.end_line for line in self.exact.evidence_lines
        ):
            raise ValueError(
                "exact evidence must stay inside the reviewer-selected ReviewUnit span"
            )
        return self


class _TagTruthV2ReviewReceiptPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-review-receipt-v1"]
    round_id: Annotated[str, Field(pattern=_ROUND_ID)]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    suite_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    target_tag_id: Annotated[str, Field(pattern=r"^has_[a-z0-9_]+$")]
    tag_contract_fingerprint: Annotated[str, Field(pattern=_TAG_CONTRACT_FINGERPRINT)]
    review_policy_fingerprint: Annotated[str, Field(pattern=_REVIEW_POLICY_FINGERPRINT)]
    reviewer: ReviewerIdentity
    blinding: ReviewerBlindingAttestation
    recorded_at: Annotated[str, Field(pattern=_TIMESTAMP)]
    decisions: tuple[TagTruthV2ReviewDecision, ...]

    @field_validator("decisions", mode="before")
    @classmethod
    def parse_decisions(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "Tag Truth v2 review receipt decisions")

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: str) -> str:
        return _utc_timestamp(value, "review receipt recorded_at")

    @model_validator(mode="after")
    def validate_receipt_payload(self) -> _TagTruthV2ReviewReceiptPayload:
        if self.blinding.attested_at > self.recorded_at:
            raise ValueError("review receipt cannot be recorded before its blinding attestation")
        case_ids = tuple(item.case_id for item in self.decisions)
        if not case_ids or case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("review receipt decisions must be sorted and unique")
        return self


class TagTruthV2ReviewReceipt(_TagTruthV2ReviewReceiptPayload):
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]

    @model_validator(mode="after")
    def validate_receipt_id(self) -> TagTruthV2ReviewReceipt:
        expected = canonical_hash(
            "tag-truth-review-receipt",
            _identity_payload(self, "receipt_id"),
        )
        if self.receipt_id != expected:
            raise ValueError("review receipt_id does not match its complete contents")
        return self


class ReceiptReference(_FrozenModel):
    round_id: Annotated[str, Field(pattern=_ROUND_ID)]
    reviewer_id: Annotated[str, Field(pattern=_REVIEWER_ID)]
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]


class AxisRationaleVote(_FrozenModel):
    round_id: Annotated[str, Field(pattern=_ROUND_ID)]
    reviewer_id: Annotated[str, Field(pattern=_REVIEWER_ID)]
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]
    rationale: Annotated[str, Field(min_length=1)]

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str) -> str:
        return _single_line(value, "axis consensus rationale vote")


class ConsensusVote(_FrozenModel):
    round_id: Annotated[str, Field(pattern=_ROUND_ID)]
    reviewer_id: Annotated[str, Field(pattern=_REVIEWER_ID)]
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]
    review_unit: ReviewUnitSelection
    exact: AxisReviewDecision
    routing: AxisReviewDecision

    @model_validator(mode="after")
    def validate_exact_evidence_scope(self) -> ConsensusVote:
        span = self.review_unit.source_span
        if any(
            line < span.start_line or line > span.end_line for line in self.exact.evidence_lines
        ):
            raise ValueError("consensus exact vote evidence must stay inside its ReviewUnit")
        return self


class AxisConsensus(_FrozenModel):
    status: AxisConsensusStatus
    label: SemanticLabel | None = None
    evidence_lines: tuple[Annotated[int, Field(ge=1)], ...] = ()
    abstain_reasons: tuple[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")], ...] = ()
    rationale_votes: tuple[AxisRationaleVote, ...]

    @field_validator("evidence_lines", "abstain_reasons", "rationale_votes", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"axis consensus {getattr(info, 'field_name', 'sequence')}")

    @model_validator(mode="after")
    def validate_consensus(self) -> AxisConsensus:
        rationale_keys = tuple(
            (item.round_id, item.reviewer_id, item.receipt_id) for item in self.rationale_votes
        )
        if len(rationale_keys) != 2 or rationale_keys != tuple(sorted(set(rationale_keys))):
            raise ValueError("axis consensus requires two sorted unique rationale votes")
        if self.evidence_lines != tuple(sorted(set(self.evidence_lines))):
            raise ValueError("axis consensus evidence_lines must be sorted and unique")
        if self.abstain_reasons != tuple(sorted(set(self.abstain_reasons))):
            raise ValueError("axis consensus abstain_reasons must be sorted and unique")
        if self.status == "agreed_resolved":
            if self.label not in {"positive", "negative"}:
                raise ValueError("agreed_resolved consensus requires a positive/negative label")
            if not self.evidence_lines or self.abstain_reasons:
                raise ValueError(
                    "agreed_resolved consensus requires evidence and no abstain reasons"
                )
        elif self.status == "agreed_abstain":
            if self.label != "needs_taxonomy_decision":
                raise ValueError("agreed_abstain consensus requires needs_taxonomy_decision")
            if not self.evidence_lines or not self.abstain_reasons:
                raise ValueError("agreed_abstain consensus requires evidence and abstain reasons")
        elif self.label is not None or self.evidence_lines or self.abstain_reasons:
            raise ValueError("unresolved axis consensus cannot publish a label or merged evidence")
        return self


class TagTruthV2ConsensusCase(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    review_unit_status: Literal["agreed", "unresolved"]
    review_unit: ReviewUnitSelection | None = None
    votes: tuple[ConsensusVote, ...]
    exact: AxisConsensus
    routing: AxisConsensus

    @field_validator("votes", mode="before")
    @classmethod
    def parse_votes(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "Tag Truth v2 consensus case votes")

    @model_validator(mode="after")
    def validate_case_consensus(self) -> TagTruthV2ConsensusCase:
        vote_keys = tuple((item.round_id, item.reviewer_id, item.receipt_id) for item in self.votes)
        if len(vote_keys) != 2 or vote_keys != tuple(sorted(set(vote_keys))):
            raise ValueError("consensus case requires two sorted unique complete votes")
        if len({item.round_id for item in self.votes}) != 2:
            raise ValueError("consensus case votes must use distinct review rounds")
        if len({item.reviewer_id for item in self.votes}) != 2:
            raise ValueError("consensus case votes must use distinct reviewers")
        if len({item.receipt_id for item in self.votes}) != 2:
            raise ValueError("consensus case votes must use distinct receipts")

        unit_agreed = self.votes[0].review_unit == self.votes[1].review_unit
        expected_unit_status = "agreed" if unit_agreed else "unresolved"
        expected_unit = self.votes[0].review_unit if unit_agreed else None
        if self.review_unit_status != expected_unit_status or self.review_unit != expected_unit:
            raise ValueError("consensus ReviewUnit fields do not match the preserved votes")

        for axis_name in ("exact", "routing"):
            actual = getattr(self, axis_name)
            expected = _axis_consensus_from_votes(self.votes, axis_name, unit_agreed)
            if actual != expected:
                raise ValueError(f"{axis_name} consensus does not match the preserved votes")
        return self


class TagTruthV2Consensus(_FrozenModel):
    schema_version: Literal["tag-truth-v2-consensus-v1"]
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID)]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    suite_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    target_tag_id: Annotated[str, Field(pattern=r"^has_[a-z0-9_]+$")]
    tag_contract_fingerprint: Annotated[str, Field(pattern=_TAG_CONTRACT_FINGERPRINT)]
    review_policy_fingerprint: Annotated[str, Field(pattern=_REVIEW_POLICY_FINGERPRINT)]
    receipt_references: tuple[ReceiptReference, ...]
    cases: tuple[TagTruthV2ConsensusCase, ...]
    consensus_status: Literal["complete", "unresolved"]
    consensus_blockers: tuple[ConsensusBlocker, ...]

    @field_validator("receipt_references", "cases", "consensus_blockers", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"Tag Truth v2 consensus {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_consensus(self) -> TagTruthV2Consensus:
        reference_keys = tuple(
            (item.round_id, item.reviewer_id, item.receipt_id) for item in self.receipt_references
        )
        if len(reference_keys) != 2 or reference_keys != tuple(sorted(set(reference_keys))):
            raise ValueError("consensus requires two sorted unique receipt references")
        if len({item.round_id for item in self.receipt_references}) != 2:
            raise ValueError("consensus receipts must use distinct review rounds")
        if len({item.reviewer_id for item in self.receipt_references}) != 2:
            raise ValueError("consensus receipts must use distinct reviewers")
        if len({item.receipt_id for item in self.receipt_references}) != 2:
            raise ValueError("consensus receipts must use distinct receipt IDs")

        case_ids = tuple(item.case_id for item in self.cases)
        if not case_ids or case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("consensus cases must be sorted and unique")
        for case in self.cases:
            case_references = tuple(
                (item.round_id, item.reviewer_id, item.receipt_id) for item in case.votes
            )
            if case_references != reference_keys:
                raise ValueError("consensus case votes differ from receipt references")

        blockers: list[ConsensusBlocker] = []
        if any(
            case.review_unit_status == "unresolved"
            or case.exact.status == "unresolved"
            or case.routing.status == "unresolved"
            for case in self.cases
        ):
            blockers.append("unresolved_review_disagreement")
        if any(
            case.exact.status == "agreed_abstain" or case.routing.status == "agreed_abstain"
            for case in self.cases
        ):
            blockers.append("taxonomy_decision_required")
        expected_blockers = tuple(sorted(blockers))
        if self.consensus_blockers != expected_blockers:
            raise ValueError("consensus blockers do not match case outcomes")
        expected_status = "complete" if not expected_blockers else "unresolved"
        if self.consensus_status != expected_status:
            raise ValueError("consensus_status does not match case outcomes")

        expected_id = canonical_hash(
            "tag-truth-consensus",
            _identity_payload(self, "consensus_id"),
        )
        if self.consensus_id != expected_id:
            raise ValueError("consensus_id does not match its complete contents")
        return self


def _rationale_votes(
    votes: tuple[ConsensusVote, ...],
    axis_name: Literal["exact", "routing"],
) -> tuple[AxisRationaleVote, ...]:
    return tuple(
        AxisRationaleVote(
            round_id=vote.round_id,
            reviewer_id=vote.reviewer_id,
            receipt_id=vote.receipt_id,
            rationale=getattr(vote, axis_name).rationale,
        )
        for vote in votes
    )


def _axis_consensus_from_votes(
    votes: tuple[ConsensusVote, ...],
    axis_name: Literal["exact", "routing"],
    unit_agreed: bool,
) -> AxisConsensus:
    decisions = tuple(getattr(vote, axis_name) for vote in votes)
    rationale_votes = _rationale_votes(votes, axis_name)
    if not unit_agreed or decisions[0].label != decisions[1].label:
        return AxisConsensus(status="unresolved", rationale_votes=rationale_votes)

    label = decisions[0].label
    evidence_lines = tuple(
        sorted(set((*decisions[0].evidence_lines, *decisions[1].evidence_lines)))
    )
    if label == "needs_taxonomy_decision":
        abstain_reasons = tuple(
            sorted(
                {
                    reason
                    for decision in decisions
                    if (reason := decision.abstain_reason) is not None
                }
            )
        )
        return AxisConsensus(
            status="agreed_abstain",
            label=label,
            evidence_lines=evidence_lines,
            abstain_reasons=abstain_reasons,
            rationale_votes=rationale_votes,
        )
    return AxisConsensus(
        status="agreed_resolved",
        label=label,
        evidence_lines=evidence_lines,
        rationale_votes=rationale_votes,
    )


def review_receipt_payload_with_id(payload: Mapping[str, object]) -> dict[str, object]:
    if "receipt_id" in payload:
        raise ValueError("unsealed review receipt payload cannot contain receipt_id")
    canonical_payload = _TagTruthV2ReviewReceiptPayload.model_validate_json(
        canonical_json(dict(payload))
    )
    result = canonical_payload.model_dump(mode="json")
    result["receipt_id"] = canonical_hash("tag-truth-review-receipt", result)
    return result


def seal_tag_truth_v2_review_receipt_payload(
    payload: Mapping[str, object],
) -> TagTruthV2ReviewReceipt:
    sealed = review_receipt_payload_with_id(payload)
    return TagTruthV2ReviewReceipt.model_validate_json(canonical_json(sealed))


def _canonical_packet(packet: TagTruthV2ReviewPacket) -> TagTruthV2ReviewPacket:
    return TagTruthV2ReviewPacket.model_validate_json(
        canonical_json(packet.model_dump(mode="json"))
    )


def _canonical_receipt(receipt: TagTruthV2ReviewReceipt) -> TagTruthV2ReviewReceipt:
    return TagTruthV2ReviewReceipt.model_validate_json(
        canonical_json(receipt.model_dump(mode="json"))
    )


def validate_tag_truth_v2_review_receipt(
    receipt: TagTruthV2ReviewReceipt,
    packet: TagTruthV2ReviewPacket,
) -> None:
    packet = _canonical_packet(packet)
    receipt = _canonical_receipt(receipt)
    expected_binding = (
        packet.selection_id,
        packet.packet_id,
        packet.suite_id,
        packet.target_tag_id,
        packet.tag_contract.contract_fingerprint,
        packet.review_policy.policy_fingerprint,
    )
    actual_binding = (
        receipt.selection_id,
        receipt.packet_id,
        receipt.suite_id,
        receipt.target_tag_id,
        receipt.tag_contract_fingerprint,
        receipt.review_policy_fingerprint,
    )
    if actual_binding != expected_binding:
        raise ValueError("review receipt identity does not match the sealed review packet")

    packet_cases = {item.case_id: item for item in packet.cases}
    decision_ids = {item.case_id for item in receipt.decisions}
    if decision_ids != set(packet_cases):
        missing = sorted(set(packet_cases) - decision_ids)
        extra = sorted(decision_ids - set(packet_cases))
        raise ValueError(f"review receipt case coverage mismatch: missing={missing}, extra={extra}")

    for decision in receipt.decisions:
        packet_case = packet_cases[decision.case_id]
        span = decision.review_unit.source_span
        if not span.start_line <= packet_case.probe_line <= span.end_line:
            raise ValueError(
                f"reviewer-selected ReviewUnit must contain probe_line: {decision.case_id}"
            )
        if span.end_line > packet_case.line_count:
            raise ValueError(f"reviewer-selected ReviewUnit exceeds source: {decision.case_id}")
        if any(line > packet_case.line_count for line in decision.routing.evidence_lines):
            raise ValueError(f"routing evidence exceeds full source: {decision.case_id}")


def _consensus_vote(
    receipt: TagTruthV2ReviewReceipt,
    decision: TagTruthV2ReviewDecision,
) -> ConsensusVote:
    return ConsensusVote(
        round_id=receipt.round_id,
        reviewer_id=receipt.reviewer.reviewer_id,
        receipt_id=receipt.receipt_id,
        review_unit=decision.review_unit,
        exact=decision.exact,
        routing=decision.routing,
    )


def build_tag_truth_v2_consensus(
    packet: TagTruthV2ReviewPacket,
    receipts: Sequence[TagTruthV2ReviewReceipt],
) -> TagTruthV2Consensus:
    packet = _canonical_packet(packet)
    if len(receipts) != 2:
        raise ValueError("Tag Truth v2 consensus requires exactly two review receipts")
    ordered = tuple(
        sorted(
            (_canonical_receipt(receipt) for receipt in receipts),
            key=lambda item: (item.round_id, item.reviewer.reviewer_id, item.receipt_id),
        )
    )
    if len({item.round_id for item in ordered}) != 2:
        raise ValueError("consensus receipts must use distinct review rounds")
    if len({item.reviewer.reviewer_id for item in ordered}) != 2:
        raise ValueError("consensus receipts must use distinct reviewers")
    if len({item.receipt_id for item in ordered}) != 2:
        raise ValueError("consensus receipts must use distinct receipt IDs")
    for receipt in ordered:
        validate_tag_truth_v2_review_receipt(receipt, packet)

    decisions = tuple(
        {decision.case_id: decision for decision in receipt.decisions} for receipt in ordered
    )
    cases: list[TagTruthV2ConsensusCase] = []
    for packet_case in packet.cases:
        votes = tuple(
            _consensus_vote(receipt, by_case[packet_case.case_id])
            for receipt, by_case in zip(ordered, decisions, strict=True)
        )
        unit_agreed = votes[0].review_unit == votes[1].review_unit
        cases.append(
            TagTruthV2ConsensusCase(
                case_id=packet_case.case_id,
                review_unit_status="agreed" if unit_agreed else "unresolved",
                review_unit=votes[0].review_unit if unit_agreed else None,
                votes=votes,
                exact=_axis_consensus_from_votes(votes, "exact", unit_agreed),
                routing=_axis_consensus_from_votes(votes, "routing", unit_agreed),
            )
        )

    blockers: list[ConsensusBlocker] = []
    if any(
        case.review_unit_status == "unresolved"
        or case.exact.status == "unresolved"
        or case.routing.status == "unresolved"
        for case in cases
    ):
        blockers.append("unresolved_review_disagreement")
    if any(
        case.exact.status == "agreed_abstain" or case.routing.status == "agreed_abstain"
        for case in cases
    ):
        blockers.append("taxonomy_decision_required")
    blockers = sorted(blockers)
    payload: dict[str, object] = {
        "schema_version": TAG_TRUTH_V2_CONSENSUS_SCHEMA_VERSION,
        "selection_id": packet.selection_id,
        "packet_id": packet.packet_id,
        "suite_id": packet.suite_id,
        "target_tag_id": packet.target_tag_id,
        "tag_contract_fingerprint": packet.tag_contract.contract_fingerprint,
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
        "consensus_status": "complete" if not blockers else "unresolved",
        "consensus_blockers": blockers,
    }
    payload["consensus_id"] = canonical_hash("tag-truth-consensus", payload)
    return TagTruthV2Consensus.model_validate_json(canonical_json(payload))


def verify_tag_truth_v2_consensus(
    consensus: TagTruthV2Consensus,
    packet: TagTruthV2ReviewPacket,
    receipts: Sequence[TagTruthV2ReviewReceipt],
) -> None:
    canonical_consensus = TagTruthV2Consensus.model_validate_json(
        canonical_json(consensus.model_dump(mode="json"))
    )
    rebuilt = build_tag_truth_v2_consensus(packet, receipts)
    if canonical_consensus != rebuilt:
        raise ValueError("Tag Truth v2 consensus does not rebuild from packet and receipts")


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
    return _parse_json_model(raw, model, f"{context} {artifact}")


def parse_tag_truth_v2_review_receipt(raw: bytes) -> TagTruthV2ReviewReceipt:
    return _parse_json_model(raw, TagTruthV2ReviewReceipt, "Tag Truth v2 review receipt")


def load_tag_truth_v2_review_receipt(path: str | Path) -> TagTruthV2ReviewReceipt:
    return _load_json_model(path, TagTruthV2ReviewReceipt, "Tag Truth v2 review receipt")


def parse_tag_truth_v2_consensus(raw: bytes) -> TagTruthV2Consensus:
    return _parse_json_model(raw, TagTruthV2Consensus, "Tag Truth v2 consensus")


def load_tag_truth_v2_consensus(path: str | Path) -> TagTruthV2Consensus:
    return _load_json_model(path, TagTruthV2Consensus, "Tag Truth v2 consensus")


__all__ = [
    "AxisConsensus",
    "AxisConsensusStatus",
    "AxisRationaleVote",
    "AxisReviewDecision",
    "ConsensusBlocker",
    "ConsensusVote",
    "ReceiptReference",
    "ReviewUnitSelection",
    "ReviewerBlindingAttestation",
    "ReviewerIdentity",
    "TAG_TRUTH_V2_CONSENSUS_SCHEMA_VERSION",
    "TAG_TRUTH_V2_REVIEW_RECEIPT_SCHEMA_VERSION",
    "TagTruthV2Consensus",
    "TagTruthV2ConsensusCase",
    "TagTruthV2ReviewDecision",
    "TagTruthV2ReviewReceipt",
    "build_tag_truth_v2_consensus",
    "load_tag_truth_v2_consensus",
    "load_tag_truth_v2_review_receipt",
    "parse_tag_truth_v2_consensus",
    "parse_tag_truth_v2_review_receipt",
    "review_receipt_payload_with_id",
    "seal_tag_truth_v2_review_receipt_payload",
    "validate_tag_truth_v2_review_receipt",
    "verify_tag_truth_v2_consensus",
]
