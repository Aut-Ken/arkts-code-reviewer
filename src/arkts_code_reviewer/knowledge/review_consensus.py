from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from arkts_code_reviewer.knowledge.models import (
    AnnotationChange,
    DuplicateClauseGroup,
    KnowledgeConflictReview,
    KnowledgeModelReview,
    MissingClauseReview,
    ModelReviewDecision,
)

CONSENSUS_SCHEMA_VERSION = "knowledge-review-consensus-v1"

ConsensusClauseStatus = Literal[
    "accepted",
    "rejected",
    "correction_draft",
    "unresolved",
]
ConsensusProposalStatus = Literal["confirmed_proposal", "quarantine"]
ReleaseBlockerKind = Literal[
    "clause_rejected",
    "clause_correction_draft",
    "clause_unresolved",
    "missing_clause_proposal",
    "duplicate_clause_proposal",
    "conflict_proposal",
]

_ROUND_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PACKET_ID_PATTERN = r"^knowledge-review-packet:sha256:[0-9a-f]{64}$"
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_CONSENSUS_ID_PATTERN = r"^knowledge-review-consensus:sha256:[0-9a-f]{64}$"
_CHANGE_SET_ID_PATTERN = r"^knowledge-review-change-set:sha256:[0-9a-f]{64}$"
_PROPOSAL_ID_PATTERN = r"^knowledge-review-proposal:sha256:[0-9a-f]{64}$"


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


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = _canonical_json(payload).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _model_payload(value: BaseModel) -> dict[str, object]:
    return value.model_dump(mode="json")


def _model_key(value: BaseModel) -> str:
    return _canonical_json(_model_payload(value))


def _canonical_changes(
    changes: tuple[AnnotationChange, ...],
) -> tuple[AnnotationChange, ...]:
    keyed = [(_model_key(change), change) for change in changes]
    keys = [key for key, _ in keyed]
    if len(keys) != len(set(keys)):
        raise ValueError("Consensus annotation changes must be unique")
    return tuple(change for _, change in sorted(keyed, key=lambda item: item[0]))


def _change_set_id(changes: tuple[AnnotationChange, ...]) -> str:
    canonical = _canonical_changes(changes)
    return _canonical_hash(
        "knowledge-review-change-set",
        [_model_payload(change) for change in canonical],
    )


def _proposal_id(value: BaseModel) -> str:
    return _canonical_hash("knowledge-review-proposal", _model_payload(value))


def _validate_round_name(value: str, context: str) -> str:
    if not _ROUND_NAME_RE.fullmatch(value):
        raise ValueError(f"{context} must use lowercase kebab-case")
    return value


def _validate_nonempty_trimmed(value: str, context: str) -> str:
    if not value or value.strip() != value:
        raise ValueError(f"{context} must be non-empty and trimmed")
    return value


def _validate_unique_review_findings(review: KnowledgeModelReview) -> None:
    for clause in review.clause_reviews:
        canonical = _canonical_changes(clause.annotation_changes)
        if clause.decision == "accept_with_corrections":
            if not canonical:
                raise ValueError(
                    "Consensus correction vote must contain annotation changes"
                )
        elif canonical:
            raise ValueError(
                "Consensus input only permits changes on accept_with_corrections"
            )

    missing_keys = [item.proposed_rule_id for item in review.missing_clauses]
    if len(missing_keys) != len(set(missing_keys)):
        raise ValueError("Consensus input contains duplicate missing Clause identities")

    duplicate_keys = [item.rule_ids for item in review.duplicate_groups]
    if len(duplicate_keys) != len(set(duplicate_keys)):
        raise ValueError("Consensus input contains duplicate duplicate-group identities")

    conflict_keys = [item.conflict_id for item in review.conflicts]
    if len(conflict_keys) != len(set(conflict_keys)):
        raise ValueError("Consensus input contains duplicate conflict identities")


class ConsensusReviewRound(_FrozenModel):
    """One upstream-validated review and its receipt provenance."""

    round_name: Annotated[str, Field(min_length=1)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID_PATTERN)]
    packet_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    prompt_version: Annotated[str, Field(min_length=1)]
    prompt_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    provider: Annotated[str, Field(min_length=1)]
    model: Annotated[str, Field(min_length=1)]
    request_id: Annotated[str, Field(min_length=1)]
    session_id: Annotated[str, Field(min_length=1)]
    review_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    receipt_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    review: KnowledgeModelReview

    @field_validator("round_name")
    @classmethod
    def validate_round_name(cls, value: str) -> str:
        return _validate_round_name(value, "Consensus round_name")

    @field_validator(
        "prompt_version",
        "provider",
        "model",
        "request_id",
        "session_id",
    )
    @classmethod
    def validate_text_fields(cls, value: str, info: ValidationInfo) -> str:
        return _validate_nonempty_trimmed(
            value,
            f"Consensus round {info.field_name}",
        )

    @model_validator(mode="after")
    def validate_review_metadata(self) -> ConsensusReviewRound:
        reviewer = self.review.reviewer
        expected = (
            self.packet_id,
            self.provider,
            self.model,
            self.prompt_version,
        )
        actual = (
            self.review.packet_id,
            reviewer.provider,
            reviewer.model,
            reviewer.prompt_version,
        )
        if actual != expected:
            raise ValueError("Consensus round metadata does not match its review")
        _validate_unique_review_findings(self.review)
        return self


class ConsensusRoundReference(_FrozenModel):
    round_name: Annotated[str, Field(min_length=1)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID_PATTERN)]
    packet_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    prompt_version: Annotated[str, Field(min_length=1)]
    prompt_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    provider: Annotated[str, Field(min_length=1)]
    model: Annotated[str, Field(min_length=1)]
    request_id: Annotated[str, Field(min_length=1)]
    session_id: Annotated[str, Field(min_length=1)]
    review_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    receipt_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]

    @field_validator("round_name")
    @classmethod
    def validate_round_name(cls, value: str) -> str:
        return _validate_round_name(value, "Consensus round reference")

    @field_validator(
        "prompt_version",
        "provider",
        "model",
        "request_id",
        "session_id",
    )
    @classmethod
    def validate_text_fields(cls, value: str, info: ValidationInfo) -> str:
        return _validate_nonempty_trimmed(
            value,
            f"Consensus round reference {info.field_name}",
        )


class ConsensusClauseVote(_FrozenModel):
    round_name: Annotated[str, Field(min_length=1)]
    decision: ModelReviewDecision
    change_set_id: Annotated[str, Field(pattern=_CHANGE_SET_ID_PATTERN)]

    @field_validator("round_name")
    @classmethod
    def validate_round_name(cls, value: str) -> str:
        return _validate_round_name(value, "Consensus Clause vote round_name")

    @model_validator(mode="after")
    def validate_empty_change_set(self) -> ConsensusClauseVote:
        if (
            self.decision != "accept_with_corrections"
            and self.change_set_id != _change_set_id(())
        ):
            raise ValueError("Non-correction consensus vote must use the empty change set")
        return self


class ConsensusCorrectionDraft(_FrozenModel):
    change_set_id: Annotated[str, Field(pattern=_CHANGE_SET_ID_PATTERN)]
    annotation_changes: tuple[AnnotationChange, ...]
    auto_apply: Literal[False] = False

    @field_validator("annotation_changes", mode="before")
    @classmethod
    def parse_changes(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Consensus correction changes must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_changes(self) -> ConsensusCorrectionDraft:
        canonical = _canonical_changes(self.annotation_changes)
        if not canonical:
            raise ValueError("Consensus correction draft must not be empty")
        if canonical != self.annotation_changes:
            raise ValueError("Consensus correction changes must be canonically sorted")
        if self.change_set_id != _change_set_id(canonical):
            raise ValueError("Consensus correction change_set_id does not match changes")
        return self


class ConsensusClauseResult(_FrozenModel):
    rule_id: Annotated[str, Field(min_length=1)]
    status: ConsensusClauseStatus
    votes: tuple[ConsensusClauseVote, ...]
    correction_draft: ConsensusCorrectionDraft | None = None

    @field_validator("votes", mode="before")
    @classmethod
    def parse_votes(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Consensus Clause votes must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_result(self) -> ConsensusClauseResult:
        vote_rounds = [item.round_name for item in self.votes]
        if len(self.votes) != 2 or vote_rounds != sorted(set(vote_rounds)):
            raise ValueError(
                "Consensus Clause result requires two round-sorted unique votes"
            )
        decisions = tuple(item.decision for item in self.votes)
        if decisions == ("accept", "accept"):
            expected_status: ConsensusClauseStatus = "accepted"
        elif decisions == ("reject", "reject"):
            expected_status = "rejected"
        elif decisions == (
            "accept_with_corrections",
            "accept_with_corrections",
        ) and self.votes[0].change_set_id == self.votes[1].change_set_id:
            expected_status = "correction_draft"
        else:
            expected_status = "unresolved"
        if self.status != expected_status:
            raise ValueError("Consensus Clause status does not match round votes")
        if expected_status == "correction_draft":
            if self.correction_draft is None:
                raise ValueError("Consensus correction result requires a draft")
            if self.correction_draft.change_set_id != self.votes[0].change_set_id:
                raise ValueError("Consensus correction draft does not match round votes")
        elif self.correction_draft is not None:
            raise ValueError("Only a correction_draft result may carry a draft")
        return self


class MissingClauseRoundProposal(_FrozenModel):
    round_name: Annotated[str, Field(min_length=1)]
    proposal_id: Annotated[str, Field(pattern=_PROPOSAL_ID_PATTERN)]
    proposal: MissingClauseReview

    @field_validator("round_name")
    @classmethod
    def validate_round_name(cls, value: str) -> str:
        return _validate_round_name(value, "Missing Clause proposal round_name")

    @model_validator(mode="after")
    def validate_proposal_id(self) -> MissingClauseRoundProposal:
        if self.proposal_id != _proposal_id(self.proposal):
            raise ValueError("Missing Clause proposal_id does not match proposal")
        return self


class ConsensusMissingClauseFinding(_FrozenModel):
    proposed_rule_id: Annotated[str, Field(min_length=1)]
    status: ConsensusProposalStatus
    proposals: tuple[MissingClauseRoundProposal, ...]

    @model_validator(mode="after")
    def validate_finding(self) -> ConsensusMissingClauseFinding:
        _validate_proposal_rounds(self.proposals, "Missing Clause")
        if any(
            item.proposal.proposed_rule_id != self.proposed_rule_id
            for item in self.proposals
        ):
            raise ValueError("Missing Clause proposal identity mismatch")
        _validate_proposal_status(self.status, self.proposals, "Missing Clause")
        return self


class DuplicateGroupRoundProposal(_FrozenModel):
    round_name: Annotated[str, Field(min_length=1)]
    proposal_id: Annotated[str, Field(pattern=_PROPOSAL_ID_PATTERN)]
    proposal: DuplicateClauseGroup

    @field_validator("round_name")
    @classmethod
    def validate_round_name(cls, value: str) -> str:
        return _validate_round_name(value, "Duplicate group proposal round_name")

    @model_validator(mode="after")
    def validate_proposal_id(self) -> DuplicateGroupRoundProposal:
        if self.proposal_id != _proposal_id(self.proposal):
            raise ValueError("Duplicate group proposal_id does not match proposal")
        return self


class ConsensusDuplicateGroupFinding(_FrozenModel):
    rule_ids: tuple[str, ...]
    status: ConsensusProposalStatus
    proposals: tuple[DuplicateGroupRoundProposal, ...]

    @model_validator(mode="after")
    def validate_finding(self) -> ConsensusDuplicateGroupFinding:
        _validate_proposal_rounds(self.proposals, "Duplicate group")
        if any(item.proposal.rule_ids != self.rule_ids for item in self.proposals):
            raise ValueError("Duplicate group proposal identity mismatch")
        _validate_proposal_status(self.status, self.proposals, "Duplicate group")
        return self


class ConflictRoundProposal(_FrozenModel):
    round_name: Annotated[str, Field(min_length=1)]
    proposal_id: Annotated[str, Field(pattern=_PROPOSAL_ID_PATTERN)]
    proposal: KnowledgeConflictReview

    @field_validator("round_name")
    @classmethod
    def validate_round_name(cls, value: str) -> str:
        return _validate_round_name(value, "Conflict proposal round_name")

    @model_validator(mode="after")
    def validate_proposal_id(self) -> ConflictRoundProposal:
        if self.proposal_id != _proposal_id(self.proposal):
            raise ValueError("Conflict proposal_id does not match proposal")
        return self


class ConsensusConflictFinding(_FrozenModel):
    conflict_id: Annotated[str, Field(min_length=1)]
    status: ConsensusProposalStatus
    proposals: tuple[ConflictRoundProposal, ...]

    @model_validator(mode="after")
    def validate_finding(self) -> ConsensusConflictFinding:
        _validate_proposal_rounds(self.proposals, "Conflict")
        if any(
            item.proposal.conflict_id != self.conflict_id for item in self.proposals
        ):
            raise ValueError("Conflict proposal identity mismatch")
        _validate_proposal_status(self.status, self.proposals, "Conflict")
        return self


class _RoundProposal(Protocol):
    round_name: str
    proposal_id: str


def _validate_proposal_rounds(
    proposals: tuple[_RoundProposal, ...],
    context: str,
) -> None:
    rounds = [item.round_name for item in proposals]
    if len(proposals) not in (1, 2) or rounds != sorted(set(rounds)):
        raise ValueError(f"{context} finding requires one or two round-sorted proposals")


def _validate_proposal_status(
    status: ConsensusProposalStatus,
    proposals: tuple[_RoundProposal, ...],
    context: str,
) -> None:
    confirmed = len(proposals) == 2 and len({item.proposal_id for item in proposals}) == 1
    expected: ConsensusProposalStatus = (
        "confirmed_proposal" if confirmed else "quarantine"
    )
    if status != expected:
        raise ValueError(f"{context} consensus status does not match round proposals")


class ConsensusReleaseBlocker(_FrozenModel):
    blocker_kind: ReleaseBlockerKind
    subject_key: Annotated[str, Field(min_length=1)]


class KnowledgeReviewConsensus(_FrozenModel):
    schema_version: Literal["knowledge-review-consensus-v1"] = (
        "knowledge-review-consensus-v1"
    )
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID_PATTERN)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID_PATTERN)]
    packet_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    prompt_version: Annotated[str, Field(min_length=1)]
    prompt_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    provider: Annotated[str, Field(min_length=1)]
    model: Annotated[str, Field(min_length=1)]
    rounds: tuple[ConsensusRoundReference, ...]
    clauses: tuple[ConsensusClauseResult, ...]
    missing_clauses: tuple[ConsensusMissingClauseFinding, ...]
    duplicate_groups: tuple[ConsensusDuplicateGroupFinding, ...]
    conflicts: tuple[ConsensusConflictFinding, ...]
    release_ready: bool
    release_blockers: tuple[ConsensusReleaseBlocker, ...]

    @field_validator(
        "rounds",
        "clauses",
        "missing_clauses",
        "duplicate_groups",
        "conflicts",
        "release_blockers",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Consensus collections must be sequences")
        return tuple(value)

    @field_validator("prompt_version", "provider", "model")
    @classmethod
    def validate_text_fields(cls, value: str, info: ValidationInfo) -> str:
        return _validate_nonempty_trimmed(value, f"Consensus {info.field_name}")

    def identity_payload(self) -> dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "packet_hash": self.packet_hash,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "provider": self.provider,
            "model": self.model,
            "rounds": [_model_payload(item) for item in self.rounds],
            "clauses": [_model_payload(item) for item in self.clauses],
            "missing_clauses": [
                _model_payload(item) for item in self.missing_clauses
            ],
            "duplicate_groups": [
                _model_payload(item) for item in self.duplicate_groups
            ],
            "conflicts": [_model_payload(item) for item in self.conflicts],
            "release_ready": self.release_ready,
            "release_blockers": [
                _model_payload(item) for item in self.release_blockers
            ],
        }

    def expected_consensus_id(self) -> str:
        return _canonical_hash("knowledge-review-consensus", self.identity_payload())

    @model_validator(mode="after")
    def validate_consensus(self) -> KnowledgeReviewConsensus:
        round_names = [item.round_name for item in self.rounds]
        request_ids = [item.request_id for item in self.rounds]
        session_ids = [item.session_id for item in self.rounds]
        receipt_hashes = [item.receipt_hash for item in self.rounds]
        if len(self.rounds) != 2 or round_names != sorted(set(round_names)):
            raise ValueError("Consensus output requires two round-sorted references")
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("Consensus round request IDs must be distinct")
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("Consensus round session IDs must be distinct")
        if len(receipt_hashes) != len(set(receipt_hashes)):
            raise ValueError("Consensus round receipt hashes must be distinct")
        shared_identity = (
            self.packet_id,
            self.packet_hash,
            self.prompt_version,
            self.prompt_hash,
            self.provider,
            self.model,
        )
        if any(
            (
                item.packet_id,
                item.packet_hash,
                item.prompt_version,
                item.prompt_hash,
                item.provider,
                item.model,
            )
            != shared_identity
            for item in self.rounds
        ):
            raise ValueError("Consensus round references do not match shared identity")

        clause_ids = [item.rule_id for item in self.clauses]
        if not clause_ids or clause_ids != sorted(set(clause_ids)):
            raise ValueError("Consensus Clauses must be non-empty, sorted, and unique")
        for clause in self.clauses:
            if tuple(item.round_name for item in clause.votes) != tuple(round_names):
                raise ValueError("Consensus Clause votes do not cover both rounds")

        missing_keys = [item.proposed_rule_id for item in self.missing_clauses]
        duplicate_keys = [item.rule_ids for item in self.duplicate_groups]
        conflict_keys = [item.conflict_id for item in self.conflicts]
        if missing_keys != sorted(set(missing_keys), key=_canonical_json):
            raise ValueError("Consensus missing Clauses must be sorted and unique")
        if duplicate_keys != sorted(set(duplicate_keys), key=_canonical_json):
            raise ValueError("Consensus duplicate groups must be sorted and unique")
        if conflict_keys != sorted(set(conflict_keys), key=_canonical_json):
            raise ValueError("Consensus conflicts must be sorted and unique")
        allowed_rounds = set(round_names)
        proposal_rounds = (
            *(proposal.round_name for item in self.missing_clauses for proposal in item.proposals),
            *(proposal.round_name for item in self.duplicate_groups for proposal in item.proposals),
            *(proposal.round_name for item in self.conflicts for proposal in item.proposals),
        )
        if not set(proposal_rounds).issubset(allowed_rounds):
            raise ValueError("Consensus proposal references an unknown round")

        expected_blockers = _release_blockers(
            self.clauses,
            self.missing_clauses,
            self.duplicate_groups,
            self.conflicts,
        )
        if self.release_blockers != expected_blockers:
            raise ValueError("Consensus release blockers do not match findings")
        if self.release_ready != (not expected_blockers):
            raise ValueError("Consensus release_ready does not match blockers")
        if self.consensus_id != self.expected_consensus_id():
            raise ValueError("KnowledgeReviewConsensus.consensus_id does not match content")
        return self


def _round_reference(value: ConsensusReviewRound) -> ConsensusRoundReference:
    return ConsensusRoundReference(
        round_name=value.round_name,
        packet_id=value.packet_id,
        packet_hash=value.packet_hash,
        prompt_version=value.prompt_version,
        prompt_hash=value.prompt_hash,
        provider=value.provider,
        model=value.model,
        request_id=value.request_id,
        session_id=value.session_id,
        review_hash=value.review_hash,
        receipt_hash=value.receipt_hash,
    )


def _clause_results(
    rounds: tuple[ConsensusReviewRound, ConsensusReviewRound],
) -> tuple[ConsensusClauseResult, ...]:
    reviews_by_round = {
        round_item.round_name: {
            item.rule_id: item for item in round_item.review.clause_reviews
        }
        for round_item in rounds
    }
    rule_ids = tuple(item.rule_id for item in rounds[0].review.clause_reviews)
    results: list[ConsensusClauseResult] = []
    for rule_id in rule_ids:
        clause_votes = tuple(
            reviews_by_round[round_item.round_name][rule_id]
            for round_item in rounds
        )
        canonical_changes = tuple(
            _canonical_changes(item.annotation_changes) for item in clause_votes
        )
        votes = tuple(
            ConsensusClauseVote(
                round_name=round_item.round_name,
                decision=clause_review.decision,
                change_set_id=_change_set_id(changes),
            )
            for round_item, clause_review, changes in zip(
                rounds,
                clause_votes,
                canonical_changes,
                strict=True,
            )
        )
        decisions = tuple(item.decision for item in votes)
        draft: ConsensusCorrectionDraft | None = None
        if decisions == ("accept", "accept"):
            status: ConsensusClauseStatus = "accepted"
        elif decisions == ("reject", "reject"):
            status = "rejected"
        elif (
            decisions
            == ("accept_with_corrections", "accept_with_corrections")
            and votes[0].change_set_id == votes[1].change_set_id
        ):
            status = "correction_draft"
            draft = ConsensusCorrectionDraft(
                change_set_id=votes[0].change_set_id,
                annotation_changes=canonical_changes[0],
                auto_apply=False,
            )
        else:
            status = "unresolved"
        results.append(
            ConsensusClauseResult(
                rule_id=rule_id,
                status=status,
                votes=votes,
                correction_draft=draft,
            )
        )
    return tuple(results)


def _group_proposals[
    ProposalT: (MissingClauseReview, DuplicateClauseGroup, KnowledgeConflictReview),
    FindingT: (
        ConsensusMissingClauseFinding,
        ConsensusDuplicateGroupFinding,
        ConsensusConflictFinding,
    ),
](
    *,
    rounds: tuple[ConsensusReviewRound, ConsensusReviewRound],
    values: Callable[[KnowledgeModelReview], tuple[ProposalT, ...]],
    key: Callable[[ProposalT], object],
    build: Callable[[object, list[tuple[str, ProposalT]]], FindingT],
) -> tuple[FindingT, ...]:
    grouped: dict[object, list[tuple[str, ProposalT]]] = {}
    for round_item in rounds:
        for proposal in values(round_item.review):
            grouped.setdefault(key(proposal), []).append(
                (round_item.round_name, proposal)
            )
    return tuple(
        build(group_key, sorted(grouped[group_key], key=lambda item: item[0]))
        for group_key in sorted(grouped, key=_canonical_json)
    )


def _proposal_status(
    proposals: Sequence[tuple[str, BaseModel]],
) -> ConsensusProposalStatus:
    proposal_ids = {_proposal_id(item) for _, item in proposals}
    return "confirmed_proposal" if len(proposals) == 2 and len(proposal_ids) == 1 else "quarantine"


def _missing_findings(
    rounds: tuple[ConsensusReviewRound, ConsensusReviewRound],
) -> tuple[ConsensusMissingClauseFinding, ...]:
    def build(
        group_key: object,
        proposals: list[tuple[str, MissingClauseReview]],
    ) -> ConsensusMissingClauseFinding:
        return ConsensusMissingClauseFinding(
            proposed_rule_id=str(group_key),
            status=_proposal_status(proposals),
            proposals=tuple(
                MissingClauseRoundProposal(
                    round_name=round_name,
                    proposal_id=_proposal_id(proposal),
                    proposal=proposal,
                )
                for round_name, proposal in proposals
            ),
        )

    return _group_proposals(
        rounds=rounds,
        values=lambda review: review.missing_clauses,
        key=lambda proposal: proposal.proposed_rule_id,
        build=build,
    )


def _duplicate_findings(
    rounds: tuple[ConsensusReviewRound, ConsensusReviewRound],
) -> tuple[ConsensusDuplicateGroupFinding, ...]:
    def build(
        group_key: object,
        proposals: list[tuple[str, DuplicateClauseGroup]],
    ) -> ConsensusDuplicateGroupFinding:
        if not isinstance(group_key, tuple):
            raise ValueError("Duplicate group identity must be a tuple")
        return ConsensusDuplicateGroupFinding(
            rule_ids=group_key,
            status=_proposal_status(proposals),
            proposals=tuple(
                DuplicateGroupRoundProposal(
                    round_name=round_name,
                    proposal_id=_proposal_id(proposal),
                    proposal=proposal,
                )
                for round_name, proposal in proposals
            ),
        )

    return _group_proposals(
        rounds=rounds,
        values=lambda review: review.duplicate_groups,
        key=lambda proposal: proposal.rule_ids,
        build=build,
    )


def _conflict_findings(
    rounds: tuple[ConsensusReviewRound, ConsensusReviewRound],
) -> tuple[ConsensusConflictFinding, ...]:
    def build(
        group_key: object,
        proposals: list[tuple[str, KnowledgeConflictReview]],
    ) -> ConsensusConflictFinding:
        return ConsensusConflictFinding(
            conflict_id=str(group_key),
            status=_proposal_status(proposals),
            proposals=tuple(
                ConflictRoundProposal(
                    round_name=round_name,
                    proposal_id=_proposal_id(proposal),
                    proposal=proposal,
                )
                for round_name, proposal in proposals
            ),
        )

    return _group_proposals(
        rounds=rounds,
        values=lambda review: review.conflicts,
        key=lambda proposal: proposal.conflict_id,
        build=build,
    )


def _release_blockers(
    clauses: tuple[ConsensusClauseResult, ...],
    missing_clauses: tuple[ConsensusMissingClauseFinding, ...],
    duplicate_groups: tuple[ConsensusDuplicateGroupFinding, ...],
    conflicts: tuple[ConsensusConflictFinding, ...],
) -> tuple[ConsensusReleaseBlocker, ...]:
    clause_kinds: dict[ConsensusClauseStatus, ReleaseBlockerKind | None] = {
        "accepted": None,
        "rejected": "clause_rejected",
        "correction_draft": "clause_correction_draft",
        "unresolved": "clause_unresolved",
    }
    blockers = [
        ConsensusReleaseBlocker(
            blocker_kind=kind,
            subject_key=clause.rule_id,
        )
        for clause in clauses
        if (kind := clause_kinds[clause.status]) is not None
    ]
    blockers.extend(
        ConsensusReleaseBlocker(
            blocker_kind="missing_clause_proposal",
            subject_key=item.proposed_rule_id,
        )
        for item in missing_clauses
    )
    blockers.extend(
        ConsensusReleaseBlocker(
            blocker_kind="duplicate_clause_proposal",
            subject_key=_canonical_json(item.rule_ids),
        )
        for item in duplicate_groups
    )
    blockers.extend(
        ConsensusReleaseBlocker(
            blocker_kind="conflict_proposal",
            subject_key=item.conflict_id,
        )
        for item in conflicts
    )
    return tuple(
        sorted(
            blockers,
            key=lambda item: (item.blocker_kind, item.subject_key),
        )
    )


def build_knowledge_review_consensus(
    *,
    rounds: tuple[ConsensusReviewRound, ConsensusReviewRound],
) -> KnowledgeReviewConsensus:
    if not isinstance(rounds, tuple) or len(rounds) != 2:
        raise ValueError("Knowledge review consensus requires exactly two rounds")
    ordered_values = sorted(rounds, key=lambda item: item.round_name)
    ordered_rounds = (ordered_values[0], ordered_values[1])
    first, second = ordered_rounds
    if first.round_name == second.round_name:
        raise ValueError("Knowledge review consensus requires different rounds")
    shared_metadata = (
        "packet_id",
        "packet_hash",
        "prompt_version",
        "prompt_hash",
        "provider",
        "model",
    )
    mismatches = [
        field
        for field in shared_metadata
        if getattr(first, field) != getattr(second, field)
    ]
    if mismatches:
        raise ValueError(f"Consensus rounds do not share review identity: {mismatches}")
    if first.request_id == second.request_id:
        raise ValueError("Consensus rounds must use different request IDs")
    if first.session_id == second.session_id:
        raise ValueError("Consensus rounds must use different session IDs")
    if first.receipt_hash == second.receipt_hash:
        raise ValueError("Consensus rounds must use different receipt hashes")
    first_rule_ids = tuple(item.rule_id for item in first.review.clause_reviews)
    second_rule_ids = tuple(item.rule_id for item in second.review.clause_reviews)
    if first_rule_ids != second_rule_ids:
        raise ValueError("Consensus rounds must cover exactly the same Clauses")

    clauses = _clause_results(ordered_rounds)
    missing_clauses = _missing_findings(ordered_rounds)
    duplicate_groups = _duplicate_findings(ordered_rounds)
    conflicts = _conflict_findings(ordered_rounds)
    blockers = _release_blockers(
        clauses,
        missing_clauses,
        duplicate_groups,
        conflicts,
    )
    round_refs = tuple(_round_reference(item) for item in ordered_rounds)
    payload = {
        "packet_id": first.packet_id,
        "packet_hash": first.packet_hash,
        "prompt_version": first.prompt_version,
        "prompt_hash": first.prompt_hash,
        "provider": first.provider,
        "model": first.model,
        "rounds": [_model_payload(item) for item in round_refs],
        "clauses": [_model_payload(item) for item in clauses],
        "missing_clauses": [_model_payload(item) for item in missing_clauses],
        "duplicate_groups": [_model_payload(item) for item in duplicate_groups],
        "conflicts": [_model_payload(item) for item in conflicts],
        "release_ready": not blockers,
        "release_blockers": [_model_payload(item) for item in blockers],
    }
    return KnowledgeReviewConsensus(
        consensus_id=_canonical_hash("knowledge-review-consensus", payload),
        packet_id=first.packet_id,
        packet_hash=first.packet_hash,
        prompt_version=first.prompt_version,
        prompt_hash=first.prompt_hash,
        provider=first.provider,
        model=first.model,
        rounds=round_refs,
        clauses=clauses,
        missing_clauses=missing_clauses,
        duplicate_groups=duplicate_groups,
        conflicts=conflicts,
        release_ready=not blockers,
        release_blockers=blockers,
    )


def load_knowledge_review_consensus(
    raw: str | bytes,
) -> KnowledgeReviewConsensus:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Knowledge review consensus must use UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("Knowledge review consensus input must be str or bytes")
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid Knowledge review consensus JSON: {exc}") from exc
    try:
        return KnowledgeReviewConsensus.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid Knowledge review consensus: {exc}") from exc


__all__ = [
    "CONSENSUS_SCHEMA_VERSION",
    "ConsensusClauseResult",
    "ConsensusClauseStatus",
    "ConsensusConflictFinding",
    "ConsensusCorrectionDraft",
    "ConsensusDuplicateGroupFinding",
    "ConsensusMissingClauseFinding",
    "ConsensusProposalStatus",
    "ConsensusReleaseBlocker",
    "ConsensusReviewRound",
    "ConsensusRoundReference",
    "KnowledgeReviewConsensus",
    "build_knowledge_review_consensus",
    "load_knowledge_review_consensus",
]
