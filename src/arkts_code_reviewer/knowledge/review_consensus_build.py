from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from arkts_code_reviewer.knowledge.review_campaign import (
    KnowledgeGrokCampaignSummary,
    load_selected_knowledge_grok_campaign_reviews,
    summarize_knowledge_grok_campaign,
)
from arkts_code_reviewer.knowledge.review_consensus import (
    ConsensusReviewRound,
    KnowledgeReviewConsensus,
    build_knowledge_review_consensus,
)

CONSENSUS_BUILD_SCHEMA_VERSION = "knowledge-review-consensus-build-v1"

_ROUND_PREFIX_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BUILD_ID_PATTERN = r"^knowledge-review-consensus-build:sha256:[0-9a-f]{64}$"
_PACKET_BUILD_ID_PATTERN = r"^knowledge-review-packets:sha256:[0-9a-f]{64}$"
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_CAMPAIGN_ID_PATTERN = r"^knowledge-grok-campaign:sha256:[0-9a-f]{64}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _model_payload(value: BaseModel) -> dict[str, object]:
    return value.model_dump(mode="json")


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


class ConsensusPacketStatusCounts(_FrozenModel):
    release_ready: Annotated[int, Field(ge=0)]
    release_blocked: Annotated[int, Field(ge=0)]


class ConsensusClauseStatusCounts(_FrozenModel):
    accepted: Annotated[int, Field(ge=0)]
    rejected: Annotated[int, Field(ge=0)]
    correction_draft: Annotated[int, Field(ge=0)]
    unresolved: Annotated[int, Field(ge=0)]


class ConsensusProposalStatusCounts(_FrozenModel):
    confirmed_proposal: Annotated[int, Field(ge=0)]
    quarantine: Annotated[int, Field(ge=0)]


class KnowledgeReviewConsensusBuild(_FrozenModel):
    schema_version: Literal["knowledge-review-consensus-build-v1"] = (
        "knowledge-review-consensus-build-v1"
    )
    build_id: Annotated[str, Field(pattern=_BUILD_ID_PATTERN)]
    packet_build_id: Annotated[str, Field(pattern=_PACKET_BUILD_ID_PATTERN)]
    packet_manifest_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    campaign_summary_ids: tuple[
        Annotated[str, Field(pattern=_CAMPAIGN_ID_PATTERN)], ...
    ]
    campaign_summaries: tuple[KnowledgeGrokCampaignSummary, ...]
    packet_count: Annotated[int, Field(ge=1)]
    packet_consensus: tuple[KnowledgeReviewConsensus, ...]
    packet_status_counts: ConsensusPacketStatusCounts
    clause_status_counts: ConsensusClauseStatusCounts
    proposal_status_counts: ConsensusProposalStatusCounts
    release_ready: bool

    @field_validator(
        "campaign_summary_ids",
        "campaign_summaries",
        "packet_consensus",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Consensus build collections must be sequences")
        return tuple(value)

    def identity_payload(self) -> dict[str, object]:
        return {
            "packet_build_id": self.packet_build_id,
            "packet_manifest_hash": self.packet_manifest_hash,
            "campaign_summary_ids": self.campaign_summary_ids,
            "campaign_summaries": [
                _model_payload(item) for item in self.campaign_summaries
            ],
            "packet_count": self.packet_count,
            "packet_consensus": [
                _model_payload(item) for item in self.packet_consensus
            ],
            "packet_status_counts": _model_payload(self.packet_status_counts),
            "clause_status_counts": _model_payload(self.clause_status_counts),
            "proposal_status_counts": _model_payload(self.proposal_status_counts),
            "release_ready": self.release_ready,
        }

    def expected_build_id(self) -> str:
        return _canonical_hash(
            "knowledge-review-consensus-build",
            self.identity_payload(),
        )

    @model_validator(mode="after")
    def validate_build(self) -> KnowledgeReviewConsensusBuild:
        prefixes = [item.round_prefix for item in self.campaign_summaries]
        if len(self.campaign_summaries) != 2 or prefixes != sorted(set(prefixes)):
            raise ValueError("Consensus build requires two prefix-sorted campaigns")
        expected_summary_ids = tuple(
            item.summary_id for item in self.campaign_summaries
        )
        if self.campaign_summary_ids != expected_summary_ids:
            raise ValueError(
                "Consensus campaign_summary_ids do not match campaign summaries"
            )
        if any(
            item.packet_build_id != self.packet_build_id
            or item.packet_manifest_hash != self.packet_manifest_hash
            for item in self.campaign_summaries
        ):
            raise ValueError("Consensus campaign packet provenance does not match build")
        first_summary, second_summary = self.campaign_summaries
        first_rounds = set(first_summary.round_names)
        second_rounds = set(second_summary.round_names)
        if first_rounds.intersection(second_rounds):
            raise ValueError("Consensus campaign round sets must be disjoint")
        _validate_cross_campaign_independence(first_summary, second_summary)

        packet_ids = [item.packet_id for item in self.packet_consensus]
        if len(packet_ids) != self.packet_count:
            raise ValueError("Consensus packet count does not match packet_consensus")
        if packet_ids != sorted(set(packet_ids)):
            raise ValueError("Consensus packets must be packet-sorted and unique")
        first_selected = {
            item.packet_id: item for item in first_summary.selected_receipts
        }
        second_selected = {
            item.packet_id: item for item in second_summary.selected_receipts
        }
        if tuple(first_selected) != tuple(second_selected) or tuple(
            first_selected
        ) != tuple(packet_ids):
            raise ValueError(
                "Consensus campaigns and packets do not cover exactly the same packets"
            )
        for item in self.packet_consensus:
            round_by_name = {
                round_item.round_name: round_item for round_item in item.rounds
            }
            first_matches = first_rounds.intersection(round_by_name)
            second_matches = second_rounds.intersection(round_by_name)
            if len(first_matches) != 1 or len(second_matches) != 1:
                raise ValueError(
                    "Consensus packet must reference one selected round per campaign"
                )
            for selected, matches in (
                (first_selected[item.packet_id], first_matches),
                (second_selected[item.packet_id], second_matches),
            ):
                round_item = round_by_name[next(iter(matches))]
                selected_identity = (
                    selected.round_name,
                    selected.packet_id,
                    selected.packet_file_hash,
                    selected.request_id,
                    selected.session_id,
                    selected.review_file_hash,
                    selected.receipt_file_hash,
                )
                round_identity = (
                    round_item.round_name,
                    round_item.packet_id,
                    round_item.packet_hash,
                    round_item.request_id,
                    round_item.session_id,
                    round_item.review_hash,
                    round_item.receipt_hash,
                )
                if round_identity != selected_identity:
                    raise ValueError(
                        "Consensus round reference does not match selected receipt"
                    )

        request_ids = [
            round_item.request_id
            for item in self.packet_consensus
            for round_item in item.rounds
        ]
        session_ids = [
            round_item.session_id
            for item in self.packet_consensus
            for round_item in item.rounds
        ]
        receipt_hashes = [
            round_item.receipt_hash
            for item in self.packet_consensus
            for round_item in item.rounds
        ]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("Consensus build request IDs must be globally unique")
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("Consensus build session IDs must be globally unique")
        if len(receipt_hashes) != len(set(receipt_hashes)):
            raise ValueError("Consensus build receipt hashes must be globally unique")

        expected_packet_counts = _packet_status_counts(self.packet_consensus)
        expected_clause_counts = _clause_status_counts(self.packet_consensus)
        expected_proposal_counts = _proposal_status_counts(self.packet_consensus)
        if self.packet_status_counts != expected_packet_counts:
            raise ValueError("Consensus packet status counts do not match packets")
        if self.clause_status_counts != expected_clause_counts:
            raise ValueError("Consensus Clause status counts do not match packets")
        if self.proposal_status_counts != expected_proposal_counts:
            raise ValueError("Consensus proposal status counts do not match packets")
        if self.release_ready != (expected_packet_counts.release_blocked == 0):
            raise ValueError("Consensus build release_ready does not match packet statuses")
        if self.build_id != self.expected_build_id():
            raise ValueError("KnowledgeReviewConsensusBuild.build_id does not match content")
        return self


def _packet_status_counts(
    values: tuple[KnowledgeReviewConsensus, ...],
) -> ConsensusPacketStatusCounts:
    ready = sum(item.release_ready for item in values)
    return ConsensusPacketStatusCounts(
        release_ready=ready,
        release_blocked=len(values) - ready,
    )


def _clause_status_counts(
    values: tuple[KnowledgeReviewConsensus, ...],
) -> ConsensusClauseStatusCounts:
    counts = Counter(
        clause.status for item in values for clause in item.clauses
    )
    return ConsensusClauseStatusCounts(
        accepted=counts["accepted"],
        rejected=counts["rejected"],
        correction_draft=counts["correction_draft"],
        unresolved=counts["unresolved"],
    )


def _proposal_status_counts(
    values: tuple[KnowledgeReviewConsensus, ...],
) -> ConsensusProposalStatusCounts:
    counts: Counter[str] = Counter()
    for item in values:
        counts.update(finding.status for finding in item.missing_clauses)
        counts.update(finding.status for finding in item.duplicate_groups)
        counts.update(finding.status for finding in item.conflicts)
    return ConsensusProposalStatusCounts(
        confirmed_proposal=counts["confirmed_proposal"],
        quarantine=counts["quarantine"],
    )


def _attempt_ids(
    summary: KnowledgeGrokCampaignSummary,
    field: Literal["request_id", "session_id"],
) -> set[str]:
    selected = {getattr(item, field) for item in summary.selected_receipts}
    failed = {
        value
        for item in summary.failed_attempts
        if (value := getattr(item, field)) is not None
    }
    return selected.union(failed)


def _validate_cross_campaign_independence(
    first: KnowledgeGrokCampaignSummary,
    second: KnowledgeGrokCampaignSummary,
) -> None:
    duplicate_fields: list[str] = []
    if _attempt_ids(first, "request_id").intersection(
        _attempt_ids(second, "request_id")
    ):
        duplicate_fields.append("request_id")
    if _attempt_ids(first, "session_id").intersection(
        _attempt_ids(second, "session_id")
    ):
        duplicate_fields.append("session_id")
    first_receipts = {item.receipt_file_hash for item in first.selected_receipts}
    second_receipts = {item.receipt_file_hash for item in second.selected_receipts}
    if first_receipts.intersection(second_receipts):
        duplicate_fields.append("receipt_file_hash")
    if duplicate_fields:
        raise ValueError(
            "Consensus campaigns reuse cross-round identities: "
            f"{duplicate_fields}"
        )


def build_knowledge_review_consensus_campaign(
    *,
    packet_root: str | Path,
    campaign_base: str | Path,
    first_round_prefix: str,
    second_round_prefix: str,
) -> KnowledgeReviewConsensusBuild:
    if first_round_prefix == second_round_prefix:
        raise ValueError("Consensus campaigns require two different round prefixes")
    packet_root_path = Path(packet_root)
    campaign_base_path = Path(campaign_base)
    first_summary = summarize_knowledge_grok_campaign(
        packet_root=packet_root_path,
        campaign_base=campaign_base_path,
        round_prefix=first_round_prefix,
    )
    second_summary = summarize_knowledge_grok_campaign(
        packet_root=packet_root_path,
        campaign_base=campaign_base_path,
        round_prefix=second_round_prefix,
    )
    summaries = tuple(
        sorted((first_summary, second_summary), key=lambda item: item.round_prefix)
    )
    first_summary, second_summary = summaries
    if (
        first_summary.packet_build_id != second_summary.packet_build_id
        or first_summary.packet_manifest_hash != second_summary.packet_manifest_hash
    ):
        raise ValueError("Consensus campaigns do not share packet provenance")
    if set(first_summary.round_names).intersection(second_summary.round_names):
        raise ValueError("Consensus campaign round sets overlap")
    first_ids = tuple(item.packet_id for item in first_summary.selected_receipts)
    second_ids = tuple(item.packet_id for item in second_summary.selected_receipts)
    if first_ids != second_ids:
        raise ValueError("Consensus campaigns do not cover exactly the same packets")
    _validate_cross_campaign_independence(first_summary, second_summary)

    loaded_by_prefix = {
        summary.round_prefix: load_selected_knowledge_grok_campaign_reviews(
            packet_root=packet_root_path,
            campaign_base=campaign_base_path,
            summary=summary,
        )
        for summary in summaries
    }
    loaded_maps = {
        prefix: {
            selected.packet_id: (selected, packet, review)
            for selected, packet, review in loaded
        }
        for prefix, loaded in loaded_by_prefix.items()
    }
    packet_consensus: list[KnowledgeReviewConsensus] = []
    for packet_id in first_ids:
        first_selected, first_packet, first_review = loaded_maps[
            first_summary.round_prefix
        ][packet_id]
        second_selected, second_packet, second_review = loaded_maps[
            second_summary.round_prefix
        ][packet_id]
        if first_packet != second_packet:
            raise ValueError("Consensus campaigns loaded different packet content")
        if first_selected.packet_file_hash != second_selected.packet_file_hash:
            raise ValueError("Consensus campaigns loaded different packet file hashes")
        provider = first_packet.model_provider
        model = first_packet.model_name
        if provider is None or model is None:
            raise ValueError("Consensus packet is missing external model identity")
        rounds = (
            ConsensusReviewRound(
                round_name=first_selected.round_name,
                packet_id=packet_id,
                packet_hash=first_selected.packet_file_hash,
                prompt_version=first_packet.prompt_version,
                prompt_hash=first_packet.prompt_hash,
                provider=provider,
                model=model,
                request_id=first_selected.request_id,
                session_id=first_selected.session_id,
                review_hash=first_selected.review_file_hash,
                receipt_hash=first_selected.receipt_file_hash,
                review=first_review,
            ),
            ConsensusReviewRound(
                round_name=second_selected.round_name,
                packet_id=packet_id,
                packet_hash=second_selected.packet_file_hash,
                prompt_version=second_packet.prompt_version,
                prompt_hash=second_packet.prompt_hash,
                provider=provider,
                model=model,
                request_id=second_selected.request_id,
                session_id=second_selected.session_id,
                review_hash=second_selected.review_file_hash,
                receipt_hash=second_selected.receipt_file_hash,
                review=second_review,
            ),
        )
        packet_consensus.append(build_knowledge_review_consensus(rounds=rounds))

    ordered_consensus = tuple(
        sorted(packet_consensus, key=lambda item: item.packet_id)
    )
    campaign_summary_ids = tuple(item.summary_id for item in summaries)
    packet_counts = _packet_status_counts(ordered_consensus)
    clause_counts = _clause_status_counts(ordered_consensus)
    proposal_counts = _proposal_status_counts(ordered_consensus)
    payload = {
        "packet_build_id": first_summary.packet_build_id,
        "packet_manifest_hash": first_summary.packet_manifest_hash,
        "campaign_summary_ids": campaign_summary_ids,
        "campaign_summaries": [_model_payload(item) for item in summaries],
        "packet_count": len(ordered_consensus),
        "packet_consensus": [
            _model_payload(item) for item in ordered_consensus
        ],
        "packet_status_counts": _model_payload(packet_counts),
        "clause_status_counts": _model_payload(clause_counts),
        "proposal_status_counts": _model_payload(proposal_counts),
        "release_ready": packet_counts.release_blocked == 0,
    }
    return KnowledgeReviewConsensusBuild(
        build_id=_canonical_hash("knowledge-review-consensus-build", payload),
        packet_build_id=first_summary.packet_build_id,
        packet_manifest_hash=first_summary.packet_manifest_hash,
        campaign_summary_ids=campaign_summary_ids,
        campaign_summaries=summaries,
        packet_count=len(ordered_consensus),
        packet_consensus=ordered_consensus,
        packet_status_counts=packet_counts,
        clause_status_counts=clause_counts,
        proposal_status_counts=proposal_counts,
        release_ready=packet_counts.release_blocked == 0,
    )


def load_knowledge_review_consensus_build(
    raw: str | bytes,
) -> KnowledgeReviewConsensusBuild:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Knowledge review consensus build must use UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("Knowledge review consensus build input must be str or bytes")
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid Knowledge review consensus build JSON: {exc}") from exc
    try:
        return KnowledgeReviewConsensusBuild.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid Knowledge review consensus build: {exc}") from exc


__all__ = [
    "CONSENSUS_BUILD_SCHEMA_VERSION",
    "ConsensusClauseStatusCounts",
    "ConsensusPacketStatusCounts",
    "ConsensusProposalStatusCounts",
    "KnowledgeReviewConsensusBuild",
    "build_knowledge_review_consensus_campaign",
    "load_knowledge_review_consensus_build",
]
