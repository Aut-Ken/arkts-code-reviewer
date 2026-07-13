from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from arkts_code_reviewer.knowledge.annotation import KnowledgeAnnotationBuild
from arkts_code_reviewer.knowledge.extraction import KnowledgeExtractionBuild
from arkts_code_reviewer.knowledge.models import (
    ApiSymbol,
    KnowledgeAnnotation,
    KnowledgeClause,
)
from arkts_code_reviewer.knowledge.publication import curation_content_hash
from arkts_code_reviewer.knowledge.review_campaign import (
    LoadedSelectedCampaignReview,
    PartialKnowledgeGrokCampaignAudit,
    audit_partial_knowledge_grok_campaign,
)
from arkts_code_reviewer.knowledge.review_consensus import (
    ConsensusReviewRound,
    KnowledgeReviewConsensus,
    build_knowledge_review_consensus,
)
from arkts_code_reviewer.knowledge.review_packets import (
    KnowledgeReviewClause,
    KnowledgeReviewPacketBuild,
)

EVALUATION_KNOWLEDGE_SCHEMA_VERSION = "evaluation-knowledge-build-v1"
EVALUATION_SELECTION_POLICY: Literal["dual-model-accept-unconflicted-v1"] = (
    "dual-model-accept-unconflicted-v1"
)

EvaluationExclusionReason = Literal[
    "conflict_proposal_subject",
    "consensus_correction_draft",
    "consensus_rejected",
    "consensus_unresolved",
    "duplicate_proposal_subject",
    "missing_round_receipt",
]

_BUILD_ID_PATTERN = r"^evaluation-knowledge:sha256:[0-9a-f]{64}$"
_PACKET_ID_PATTERN = r"^knowledge-review-packet:sha256:[0-9a-f]{64}$"
_CONSENSUS_ID_PATTERN = r"^knowledge-review-consensus:sha256:[0-9a-f]{64}$"
_SELECTION_VERSION_PATTERN = (
    r"^knowledge-evaluation-selection:sha256:[0-9a-f]{64}$"
)
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


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


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _tuplify_json_sequences(value: object) -> object:
    """Preserve strict tuple contracts when a nested model is loaded from JSON."""
    if isinstance(value, list | tuple):
        return tuple(_tuplify_json_sequences(item) for item in value)
    if isinstance(value, dict):
        return {
            key: _tuplify_json_sequences(item) for key, item in value.items()
        }
    return value


def _parse_utc_datetime(value: object) -> datetime:
    if isinstance(value, str):
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("evaluated_at must use ISO-8601") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError("evaluated_at must be a datetime")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("evaluated_at must be timezone-aware UTC")
    return parsed


class EvaluationPacketInventory(_FrozenModel):
    packet_id: Annotated[str, Field(pattern=_PACKET_ID_PATTERN)]
    rule_ids: tuple[str, ...]

    @field_validator("rule_ids", mode="before")
    @classmethod
    def parse_rule_ids(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Evaluation packet rule_ids must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_inventory(self) -> EvaluationPacketInventory:
        if not self.rule_ids or list(self.rule_ids) != sorted(set(self.rule_ids)):
            raise ValueError("Evaluation packet rule_ids must be non-empty and sorted")
        return self


class EvaluationExclusion(_FrozenModel):
    packet_id: Annotated[str, Field(pattern=_PACKET_ID_PATTERN)]
    rule_id: Annotated[str, Field(min_length=1)]
    reasons: tuple[EvaluationExclusionReason, ...]

    @field_validator("reasons", mode="before")
    @classmethod
    def parse_reasons(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Evaluation exclusion reasons must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_exclusion(self) -> EvaluationExclusion:
        if not self.reasons or list(self.reasons) != sorted(set(self.reasons)):
            raise ValueError("Evaluation exclusion reasons must be non-empty and sorted")
        return self


class EvaluationClause(_FrozenModel):
    packet_id: Annotated[str, Field(pattern=_PACKET_ID_PATTERN)]
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID_PATTERN)]
    review_content_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    source_clause: KnowledgeReviewClause
    clause: KnowledgeClause

    @model_validator(mode="after")
    def validate_clause(self) -> EvaluationClause:
        source = self.source_clause
        clause = self.clause
        if source.proposed_status != "Draft" or clause.status != "Draft":
            raise ValueError("Evaluation Knowledge may contain only Draft Clauses")
        if self.review_content_hash != curation_content_hash(source):
            raise ValueError("Evaluation Clause content hash does not match source Clause")
        candidate = source.candidate
        expected = (
            source.rule_id,
            candidate.native_rule_id,
            candidate.rule_type,
            candidate.source_ref.authority,
            candidate.text,
            candidate.heading_path,
            candidate.parent_context,
            candidate.applicability,
            candidate.source_ref,
            candidate.source_span,
            candidate.examples,
            candidate.source_ref.content_hash,
        )
        actual = (
            clause.rule_id,
            clause.native_rule_id,
            clause.rule_type,
            clause.authority,
            clause.text,
            clause.heading_path,
            clause.parent_context,
            clause.applicability,
            clause.source_ref,
            clause.source_span,
            clause.examples,
            clause.doc_hash,
        )
        if actual != expected:
            raise ValueError("Evaluation Clause materialization drifted from source Clause")
        return self

    @property
    def annotation(self) -> KnowledgeAnnotation:
        return self.source_clause.annotation

    @property
    def domains(self) -> tuple[str, ...]:
        return self.source_clause.domains


class EvaluationKnowledgeBuild(_FrozenModel):
    schema_version: Literal["evaluation-knowledge-build-v1"] = (
        "evaluation-knowledge-build-v1"
    )
    build_id: Annotated[str, Field(pattern=_BUILD_ID_PATTERN)]
    packet_build_id: Annotated[
        str,
        Field(pattern=r"^knowledge-review-packets:sha256:[0-9a-f]{64}$"),
    ]
    packet_manifest_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    extraction_build_id: Annotated[
        str,
        Field(pattern=r"^knowledge-extraction:sha256:[0-9a-f]{64}$"),
    ]
    annotation_build_id: Annotated[
        str,
        Field(pattern=r"^knowledge-annotation:sha256:[0-9a-f]{64}$"),
    ]
    source_bundle_id: Annotated[
        str,
        Field(pattern=r"^source-bundle:sha256:[0-9a-f]{64}$"),
    ]
    feature_config_fingerprint: Annotated[
        str,
        Field(pattern=r"^feature-config:sha256:[0-9a-f]{64}$"),
    ]
    annotation_config_fingerprint: Annotated[
        str,
        Field(pattern=r"^knowledge-annotation-config:sha256:[0-9a-f]{64}$"),
    ]
    annotation_version: Annotated[
        str,
        Field(pattern=r"^knowledge-annotation-version:sha256:[0-9a-f]{64}$"),
    ]
    source_annotation_index_version: Annotated[str, Field(min_length=1)]
    selection_policy: Literal["dual-model-accept-unconflicted-v1"] = (
        "dual-model-accept-unconflicted-v1"
    )
    selection_version: Annotated[str, Field(pattern=_SELECTION_VERSION_PATTERN)]
    evaluated_at: datetime
    production_eligible: Literal[False] = False
    campaign_audits: tuple[PartialKnowledgeGrokCampaignAudit, ...]
    packet_inventory: tuple[EvaluationPacketInventory, ...]
    packet_consensus: tuple[KnowledgeReviewConsensus, ...]
    missing_round_packet_ids: tuple[str, ...]
    exclusions: tuple[EvaluationExclusion, ...]
    clauses: tuple[EvaluationClause, ...]
    api_symbols: tuple[ApiSymbol, ...]

    @field_validator("evaluated_at", mode="before")
    @classmethod
    def parse_evaluated_at(cls, value: object) -> datetime:
        return _parse_utc_datetime(value)

    @field_validator(
        "campaign_audits",
        "packet_inventory",
        "packet_consensus",
        "missing_round_packet_ids",
        "exclusions",
        "clauses",
        "api_symbols",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Evaluation Knowledge collections must be sequences")
        converted = _tuplify_json_sequences(value)
        if not isinstance(converted, tuple):
            raise AssertionError("Evaluation sequence conversion must return a tuple")
        return converted

    def identity_payload(self) -> dict[str, object]:
        return {
            "packet_build_id": self.packet_build_id,
            "packet_manifest_hash": self.packet_manifest_hash,
            "extraction_build_id": self.extraction_build_id,
            "annotation_build_id": self.annotation_build_id,
            "source_bundle_id": self.source_bundle_id,
            "feature_config_fingerprint": self.feature_config_fingerprint,
            "annotation_config_fingerprint": self.annotation_config_fingerprint,
            "annotation_version": self.annotation_version,
            "source_annotation_index_version": self.source_annotation_index_version,
            "selection_policy": self.selection_policy,
            "selection_version": self.selection_version,
            "evaluated_at": self.evaluated_at.isoformat(),
            "production_eligible": self.production_eligible,
            "campaign_audits": [
                item.model_dump(mode="json") for item in self.campaign_audits
            ],
            "packet_inventory": [
                item.model_dump(mode="json") for item in self.packet_inventory
            ],
            "packet_consensus": [
                item.model_dump(mode="json") for item in self.packet_consensus
            ],
            "missing_round_packet_ids": self.missing_round_packet_ids,
            "exclusions": [item.model_dump(mode="json") for item in self.exclusions],
            "clauses": [item.model_dump(mode="json") for item in self.clauses],
            "api_symbols": [
                item.model_dump(mode="json") for item in self.api_symbols
            ],
        }

    def expected_build_id(self) -> str:
        return _canonical_hash("evaluation-knowledge", self.identity_payload())

    @model_validator(mode="after")
    def validate_build(self) -> EvaluationKnowledgeBuild:
        _validate_evaluation_build(self)
        if self.build_id != self.expected_build_id():
            raise ValueError("EvaluationKnowledgeBuild.build_id does not match content")
        return self


def _implicated_rule_ids(
    consensus: KnowledgeReviewConsensus,
) -> tuple[set[str], set[str]]:
    duplicate_ids = {
        rule_id for finding in consensus.duplicate_groups for rule_id in finding.rule_ids
    }
    conflict_ids = {
        rule_id
        for finding in consensus.conflicts
        for proposal in finding.proposals
        for rule_id in proposal.proposal.rule_ids
    }
    return duplicate_ids, conflict_ids


def _expected_selection(
    inventory: tuple[EvaluationPacketInventory, ...],
    consensuses: tuple[KnowledgeReviewConsensus, ...],
    missing_packet_ids: tuple[str, ...],
) -> tuple[set[str], dict[str, tuple[EvaluationExclusionReason, ...]]]:
    inventory_by_packet = {item.packet_id: item.rule_ids for item in inventory}
    selected: set[str] = set()
    excluded: dict[str, tuple[EvaluationExclusionReason, ...]] = {}
    for packet_id in missing_packet_ids:
        for rule_id in inventory_by_packet[packet_id]:
            excluded[rule_id] = ("missing_round_receipt",)
    status_reason: dict[str, EvaluationExclusionReason] = {
        "rejected": "consensus_rejected",
        "correction_draft": "consensus_correction_draft",
        "unresolved": "consensus_unresolved",
    }
    for consensus in consensuses:
        duplicate_ids, conflict_ids = _implicated_rule_ids(consensus)
        known_ids = {item.rule_id for item in consensus.clauses}
        if not duplicate_ids.union(conflict_ids).issubset(known_ids):
            raise ValueError("Evaluation consensus proposal references an unknown Clause")
        for result in consensus.clauses:
            reasons: set[EvaluationExclusionReason] = set()
            if result.status != "accepted":
                reasons.add(status_reason[result.status])
            if result.rule_id in duplicate_ids:
                reasons.add("duplicate_proposal_subject")
            if result.rule_id in conflict_ids:
                reasons.add("conflict_proposal_subject")
            if reasons:
                excluded[result.rule_id] = tuple(sorted(reasons))
            else:
                selected.add(result.rule_id)
    return selected, excluded


def _validate_cross_round_audits(
    audits: tuple[PartialKnowledgeGrokCampaignAudit, ...],
) -> None:
    request_ids: list[str] = []
    session_ids: list[str] = []
    receipt_hashes: list[str] = []
    for audit in audits:
        request_ids.extend(item.request_id for item in audit.selected_receipts)
        request_ids.extend(
            item.request_id
            for item in audit.failed_attempts
            if item.request_id is not None
        )
        session_ids.extend(item.session_id for item in audit.selected_receipts)
        session_ids.extend(
            item.session_id
            for item in audit.failed_attempts
            if item.session_id is not None
        )
        receipt_hashes.extend(item.receipt_file_hash for item in audit.selected_receipts)
    if len(request_ids) != len(set(request_ids)):
        raise ValueError("Evaluation campaigns reuse request IDs across rounds")
    if len(session_ids) != len(set(session_ids)):
        raise ValueError("Evaluation campaigns reuse session IDs across rounds")
    if len(receipt_hashes) != len(set(receipt_hashes)):
        raise ValueError("Evaluation campaigns reuse receipt hashes across rounds")


def _validate_evaluation_build(build: EvaluationKnowledgeBuild) -> None:
    audit_prefixes = [item.round_prefix for item in build.campaign_audits]
    if len(build.campaign_audits) != 2 or audit_prefixes != sorted(
        set(audit_prefixes)
    ):
        raise ValueError("Evaluation Knowledge requires two prefix-sorted campaign audits")
    if any(
        item.packet_build_id != build.packet_build_id
        or item.packet_manifest_hash != build.packet_manifest_hash
        for item in build.campaign_audits
    ):
        raise ValueError("Evaluation campaign provenance does not match build")
    source_packet_ids = build.campaign_audits[0].source_packet_ids
    if any(item.source_packet_ids != source_packet_ids for item in build.campaign_audits):
        raise ValueError("Evaluation campaigns do not share a packet inventory")
    _validate_cross_round_audits(build.campaign_audits)

    inventory_ids = [item.packet_id for item in build.packet_inventory]
    if inventory_ids != list(source_packet_ids):
        raise ValueError("Evaluation packet inventory does not match campaign source")
    all_rule_ids = [rule_id for item in build.packet_inventory for rule_id in item.rule_ids]
    if len(all_rule_ids) != len(set(all_rule_ids)):
        raise ValueError("Evaluation packet inventory repeats a Clause")

    selected_by_audit = [
        {item.packet_id for item in audit.selected_receipts}
        for audit in build.campaign_audits
    ]
    paired_packet_ids = set.intersection(*selected_by_audit)
    expected_missing = tuple(sorted(set(source_packet_ids) - paired_packet_ids))
    if build.missing_round_packet_ids != expected_missing:
        raise ValueError("Evaluation missing packet set does not match campaign receipts")
    consensus_ids = [item.packet_id for item in build.packet_consensus]
    if consensus_ids != sorted(paired_packet_ids):
        raise ValueError("Evaluation consensus must cover exactly paired packets")
    inventory_by_packet = {
        item.packet_id: item.rule_ids for item in build.packet_inventory
    }
    for consensus in build.packet_consensus:
        if tuple(item.rule_id for item in consensus.clauses) != inventory_by_packet[
            consensus.packet_id
        ]:
            raise ValueError("Evaluation consensus Clause coverage does not match packet")

    receipt_by_identity = {
        (item.packet_id, item.round_name): item
        for audit in build.campaign_audits
        for item in audit.selected_receipts
    }
    for consensus in build.packet_consensus:
        for round_ref in consensus.rounds:
            selected = receipt_by_identity.get(
                (consensus.packet_id, round_ref.round_name)
            )
            if selected is None or (
                round_ref.packet_hash,
                round_ref.request_id,
                round_ref.session_id,
                round_ref.review_hash,
                round_ref.receipt_hash,
            ) != (
                selected.packet_file_hash,
                selected.request_id,
                selected.session_id,
                selected.review_file_hash,
                selected.receipt_file_hash,
            ):
                raise ValueError("Evaluation consensus round does not match audited receipt")

    expected_selected, expected_excluded = _expected_selection(
        build.packet_inventory,
        build.packet_consensus,
        build.missing_round_packet_ids,
    )
    exclusion_keys = [(item.rule_id, item.packet_id) for item in build.exclusions]
    if exclusion_keys != sorted(set(exclusion_keys)):
        raise ValueError("Evaluation exclusions must be rule-sorted and unique")
    actual_excluded = {item.rule_id: item.reasons for item in build.exclusions}
    if actual_excluded != expected_excluded:
        raise ValueError("Evaluation exclusions do not match consensus outcomes")
    packet_by_rule = {
        rule_id: item.packet_id
        for item in build.packet_inventory
        for rule_id in item.rule_ids
    }
    if any(item.packet_id != packet_by_rule[item.rule_id] for item in build.exclusions):
        raise ValueError("Evaluation exclusion references the wrong packet")

    clause_ids = [item.clause.rule_id for item in build.clauses]
    if not clause_ids or clause_ids != sorted(set(clause_ids)):
        raise ValueError("Evaluation Clauses must be non-empty, sorted, and unique")
    if set(clause_ids) != expected_selected:
        raise ValueError("Evaluation Clauses do not match accepted unconflicted consensus")
    consensus_by_packet = {item.packet_id: item for item in build.packet_consensus}
    selected_candidate_ids = {
        item.source_clause.candidate.candidate_id: item.clause.rule_id
        for item in build.clauses
    }
    if len(selected_candidate_ids) != len(build.clauses):
        raise ValueError("Evaluation selected candidates must be unique")
    for item in build.clauses:
        rule_id = item.clause.rule_id
        if item.packet_id != packet_by_rule[rule_id]:
            raise ValueError("Evaluation Clause references the wrong packet")
        consensus = consensus_by_packet[item.packet_id]
        if item.consensus_id != consensus.consensus_id:
            raise ValueError("Evaluation Clause references the wrong consensus")
        result = next(value for value in consensus.clauses if value.rule_id == rule_id)
        if result.status != "accepted" or tuple(
            vote.decision for vote in result.votes
        ) != ("accept", "accept"):
            raise ValueError("Evaluation Clause lacks two clean accept votes")
        if item.source_clause.rule_id != rule_id:
            raise ValueError("Evaluation source Clause identity mismatch")
        if item.clause.curation_version != build.selection_version:
            raise ValueError("Evaluation Clause selection version does not match build")
        if (
            item.clause.created_at != build.evaluated_at
            or item.clause.updated_at != build.evaluated_at
        ):
            raise ValueError("Evaluation Clause timestamps do not match evaluated_at")
        expected_neighbors = tuple(
            sorted(
                selected_candidate_ids[candidate_id]
                for candidate_id in item.source_clause.candidate.neighbor_candidate_ids
                if candidate_id in selected_candidate_ids
            )
        )
        if item.clause.neighbor_rule_ids != expected_neighbors:
            raise ValueError("Evaluation Clause neighbors do not match selected corpus")
        if item.source_clause.annotation.annotation_version != build.annotation_version:
            raise ValueError("Evaluation annotation version does not match build")
        if (
            item.source_clause.annotation.index_version
            != build.source_annotation_index_version
        ):
            raise ValueError("Evaluation annotation index does not match build")

    api_keys = [
        (item.canonical_name, item.signature, item.declaration_id)
        for item in build.api_symbols
    ]
    if api_keys != sorted(set(api_keys)):
        raise ValueError("Evaluation API symbols must be sorted and unique")
    declaration_ids = [item.declaration_id for item in build.api_symbols]
    if len(declaration_ids) != len(set(declaration_ids)):
        raise ValueError("Evaluation API declaration IDs must be unique")


def _validated_model[ModelT: BaseModel](value: ModelT, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate_json(value.model_dump_json())
    except ValidationError as exc:
        raise ValueError(f"invalid {model.__name__}: {exc}") from exc


def _validate_source_graph(
    *,
    packets: KnowledgeReviewPacketBuild,
    extraction: KnowledgeExtractionBuild,
    annotations: KnowledgeAnnotationBuild,
) -> tuple[
    tuple[EvaluationPacketInventory, ...],
    dict[str, KnowledgeReviewClause],
    tuple[ApiSymbol, ...],
    str,
]:
    if packets.extraction_build_id != extraction.build_id:
        raise ValueError("Evaluation packet build does not match extraction build")
    if packets.annotation_build_id != annotations.build_id:
        raise ValueError("Evaluation packet build does not match annotation build")
    if annotations.extraction_build_id != extraction.build_id:
        raise ValueError("Evaluation annotations do not match extraction build")
    if packets.source_bundle_id != extraction.source_bundle_id:
        raise ValueError("Evaluation source bundle drifted across builds")

    extracted_clauses = {
        item.rule_id: item
        for document in extraction.documents
        for item in document.clauses
    }
    extracted_domains = {
        item.rule_id: document.domains
        for document in extraction.documents
        for item in document.clauses
    }
    extracted_api_ids = {
        item.declaration_id
        for document in extraction.documents
        for item in document.api_symbols
    }
    review_clauses = {
        item.rule_id: item for packet in packets.packets for item in packet.clauses
    }
    if set(review_clauses) != set(extracted_clauses):
        raise ValueError("Evaluation packet Clause coverage does not match extraction")
    if any(
        value.proposed_status != extracted_clauses[rule_id].proposed_status
        or value.candidate != extracted_clauses[rule_id].candidate
        or value.domains != extracted_domains[rule_id]
        for rule_id, value in review_clauses.items()
    ):
        raise ValueError(
            "Evaluation packet Clause content or domains drifted from extraction"
        )
    annotation_keys = {
        (item.target_kind, item.target_id) for item in annotations.annotations
    }
    expected_annotation_keys = {
        *(("clause", rule_id) for rule_id in extracted_clauses),
        *(("api_symbol", declaration_id) for declaration_id in extracted_api_ids),
    }
    if annotation_keys != expected_annotation_keys:
        raise ValueError("Evaluation annotation coverage does not match extraction")
    annotation_by_id = {
        item.target_id: item
        for item in annotations.annotations
        if item.target_kind == "clause"
    }
    if set(annotation_by_id) != set(review_clauses):
        raise ValueError("Evaluation Clause annotation coverage is incomplete")
    if any(
        value.annotation != annotation_by_id[rule_id]
        for rule_id, value in review_clauses.items()
    ):
        raise ValueError("Evaluation packet annotation drifted from annotation build")
    if any(
        packet.feature_config_fingerprint != annotations.feature_config_fingerprint
        or packet.annotation_config_fingerprint
        != annotations.annotation_config_fingerprint
        or packet.annotation_version != annotations.annotation_version
        for packet in packets.packets
    ):
        raise ValueError("Evaluation packet annotation metadata drifted")
    source_indexes = {
        item.annotation.index_version for item in review_clauses.values()
    }
    if len(source_indexes) != 1:
        raise ValueError("Evaluation source annotations use multiple index versions")

    candidate_ids = {
        value.candidate.candidate_id for value in review_clauses.values()
    }
    if any(
        not set(value.candidate.neighbor_candidate_ids).issubset(candidate_ids)
        for value in review_clauses.values()
    ):
        raise ValueError("Evaluation Clause references an unknown neighbor candidate")
    inventory = tuple(
        EvaluationPacketInventory(
            packet_id=packet.packet_id,
            rule_ids=tuple(item.rule_id for item in packet.clauses),
        )
        for packet in packets.packets
    )
    api_symbols = tuple(
        sorted(
            (
                symbol
                for document in extraction.documents
                for symbol in document.api_symbols
            ),
            key=lambda item: (
                item.canonical_name,
                item.signature,
                item.declaration_id,
            ),
        )
    )
    return inventory, review_clauses, api_symbols, next(iter(source_indexes))


def _round_from_loaded(
    value: LoadedSelectedCampaignReview,
) -> ConsensusReviewRound:
    selected, packet, review = value
    provider = packet.model_provider
    model = packet.model_name
    if provider is None or model is None:
        raise ValueError("Evaluation packet is missing external model identity")
    return ConsensusReviewRound(
        round_name=selected.round_name,
        packet_id=selected.packet_id,
        packet_hash=selected.packet_file_hash,
        prompt_version=packet.prompt_version,
        prompt_hash=packet.prompt_hash,
        provider=provider,
        model=model,
        request_id=selected.request_id,
        session_id=selected.session_id,
        review_hash=selected.review_file_hash,
        receipt_hash=selected.receipt_file_hash,
        review=review,
    )


def build_evaluation_knowledge(
    *,
    extraction: KnowledgeExtractionBuild,
    annotations: KnowledgeAnnotationBuild,
    packet_root: str | Path,
    campaign_base: str | Path,
    first_round_prefix: str,
    second_round_prefix: str,
    evaluated_at: datetime,
) -> EvaluationKnowledgeBuild:
    if first_round_prefix == second_round_prefix:
        raise ValueError("Evaluation campaigns require two different round prefixes")
    extraction = _validated_model(extraction, KnowledgeExtractionBuild)
    annotations = _validated_model(annotations, KnowledgeAnnotationBuild)
    timestamp = _parse_utc_datetime(evaluated_at)

    first_audit, first_packets, first_loaded = audit_partial_knowledge_grok_campaign(
        packet_root=packet_root,
        campaign_base=campaign_base,
        round_prefix=first_round_prefix,
    )
    second_audit, second_packets, second_loaded = audit_partial_knowledge_grok_campaign(
        packet_root=packet_root,
        campaign_base=campaign_base,
        round_prefix=second_round_prefix,
    )
    if first_packets != second_packets:
        raise ValueError("Evaluation campaigns loaded different packet builds")
    packets = first_packets
    audits = tuple(sorted((first_audit, second_audit), key=lambda item: item.round_prefix))
    _validate_cross_round_audits(audits)

    inventory, review_clauses, api_symbols, source_index = _validate_source_graph(
        packets=packets,
        extraction=extraction,
        annotations=annotations,
    )
    loaded_maps = (
        {item[0].packet_id: item for item in first_loaded},
        {item[0].packet_id: item for item in second_loaded},
    )
    paired_packet_ids = tuple(sorted(set(loaded_maps[0]).intersection(loaded_maps[1])))
    consensuses = tuple(
        sorted(
            (
                build_knowledge_review_consensus(
                    rounds=cast(
                        tuple[ConsensusReviewRound, ConsensusReviewRound],
                        tuple(
                            sorted(
                                (
                                    _round_from_loaded(loaded_maps[0][packet_id]),
                                    _round_from_loaded(loaded_maps[1][packet_id]),
                                ),
                                key=lambda item: item.round_name,
                            )
                        ),
                    )
                )
                for packet_id in paired_packet_ids
            ),
            key=lambda item: item.packet_id,
        )
    )
    source_packet_ids = {item.packet_id for item in inventory}
    missing_packet_ids = tuple(sorted(source_packet_ids - set(paired_packet_ids)))
    selected_rule_ids, excluded_reasons = _expected_selection(
        inventory,
        consensuses,
        missing_packet_ids,
    )
    packet_by_rule = {
        rule_id: item.packet_id for item in inventory for rule_id in item.rule_ids
    }
    exclusions = tuple(
        EvaluationExclusion(
            packet_id=packet_by_rule[rule_id],
            rule_id=rule_id,
            reasons=reasons,
        )
        for rule_id, reasons in sorted(excluded_reasons.items())
    )
    selection_version = _canonical_hash(
        "knowledge-evaluation-selection",
        {
            "selection_policy": EVALUATION_SELECTION_POLICY,
            "packet_build_id": packets.build_id,
            "campaign_audit_ids": tuple(item.audit_id for item in audits),
            "consensus_ids": tuple(item.consensus_id for item in consensuses),
            "selected_rule_ids": tuple(sorted(selected_rule_ids)),
            "exclusions": [item.model_dump(mode="json") for item in exclusions],
        },
    )
    candidate_to_rule_id = {
        value.candidate.candidate_id: rule_id
        for rule_id, value in review_clauses.items()
    }
    consensus_by_packet = {item.packet_id: item for item in consensuses}
    selected_candidate_ids = {
        review_clauses[rule_id].candidate.candidate_id
        for rule_id in selected_rule_ids
    }
    clauses: list[EvaluationClause] = []
    for rule_id in sorted(selected_rule_ids):
        source = review_clauses[rule_id]
        if source.proposed_status != "Draft":
            raise ValueError("Evaluation selection accepts only source Draft Clauses")
        candidate = source.candidate
        neighbor_rule_ids = tuple(
            sorted(
                candidate_to_rule_id[candidate_id]
                for candidate_id in candidate.neighbor_candidate_ids
                if candidate_id in selected_candidate_ids
            )
        )
        clause = KnowledgeClause(
            rule_id=rule_id,
            native_rule_id=candidate.native_rule_id,
            rule_type=candidate.rule_type,
            status="Draft",
            authority=candidate.source_ref.authority,
            text=candidate.text,
            heading_path=candidate.heading_path,
            parent_context=candidate.parent_context,
            neighbor_rule_ids=neighbor_rule_ids,
            applicability=candidate.applicability,
            source_ref=candidate.source_ref,
            source_span=candidate.source_span,
            examples=candidate.examples,
            doc_hash=candidate.source_ref.content_hash,
            curation_version=selection_version,
            created_at=timestamp,
            updated_at=timestamp,
        )
        packet_id = packet_by_rule[rule_id]
        clauses.append(
            EvaluationClause(
                packet_id=packet_id,
                consensus_id=consensus_by_packet[packet_id].consensus_id,
                review_content_hash=curation_content_hash(source),
                source_clause=source,
                clause=clause,
            )
        )

    feature_fingerprints = {
        item.feature_config_fingerprint for item in packets.packets
    }
    annotation_fingerprints = {
        item.annotation_config_fingerprint for item in packets.packets
    }
    annotation_versions = {item.annotation_version for item in packets.packets}
    if any(
        len(values) != 1
        for values in (
            feature_fingerprints,
            annotation_fingerprints,
            annotation_versions,
        )
    ):
        raise ValueError("Evaluation packet metadata is inconsistent")
    draft = EvaluationKnowledgeBuild.model_construct(
        build_id="evaluation-knowledge:sha256:" + "0" * 64,
        packet_build_id=packets.build_id,
        packet_manifest_hash=audits[0].packet_manifest_hash,
        extraction_build_id=extraction.build_id,
        annotation_build_id=annotations.build_id,
        source_bundle_id=extraction.source_bundle_id,
        feature_config_fingerprint=next(iter(feature_fingerprints)),
        annotation_config_fingerprint=next(iter(annotation_fingerprints)),
        annotation_version=next(iter(annotation_versions)),
        source_annotation_index_version=source_index,
        selection_policy=EVALUATION_SELECTION_POLICY,
        selection_version=selection_version,
        evaluated_at=timestamp,
        production_eligible=False,
        campaign_audits=audits,
        packet_inventory=inventory,
        packet_consensus=consensuses,
        missing_round_packet_ids=missing_packet_ids,
        exclusions=exclusions,
        clauses=tuple(clauses),
        api_symbols=api_symbols,
    )
    return EvaluationKnowledgeBuild(
        build_id=draft.expected_build_id(),
        packet_build_id=packets.build_id,
        packet_manifest_hash=audits[0].packet_manifest_hash,
        extraction_build_id=extraction.build_id,
        annotation_build_id=annotations.build_id,
        source_bundle_id=extraction.source_bundle_id,
        feature_config_fingerprint=next(iter(feature_fingerprints)),
        annotation_config_fingerprint=next(iter(annotation_fingerprints)),
        annotation_version=next(iter(annotation_versions)),
        source_annotation_index_version=source_index,
        selection_policy=EVALUATION_SELECTION_POLICY,
        selection_version=selection_version,
        evaluated_at=timestamp,
        production_eligible=False,
        campaign_audits=audits,
        packet_inventory=inventory,
        packet_consensus=consensuses,
        missing_round_packet_ids=missing_packet_ids,
        exclusions=exclusions,
        clauses=tuple(clauses),
        api_symbols=api_symbols,
    )


def _load_strict_model_file[ModelT: BaseModel](
    path: str | Path,
    *,
    model: type[ModelT],
    context: str,
) -> ModelT:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"{context} must be a regular non-symlink file")
    try:
        raw = source.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read {context}") from exc
    try:
        json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid {context} JSON: {exc}") from exc
    try:
        return model.model_validate_json(text)
    except ValidationError as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


def load_evaluation_extraction_file(path: str | Path) -> KnowledgeExtractionBuild:
    return _load_strict_model_file(
        path,
        model=KnowledgeExtractionBuild,
        context="Evaluation Knowledge extraction",
    )


def load_evaluation_annotations_file(path: str | Path) -> KnowledgeAnnotationBuild:
    return _load_strict_model_file(
        path,
        model=KnowledgeAnnotationBuild,
        context="Evaluation Knowledge annotations",
    )


def load_evaluation_knowledge(raw: str | bytes) -> EvaluationKnowledgeBuild:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Evaluation Knowledge must use UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("Evaluation Knowledge input must be str or bytes")
    try:
        json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid Evaluation Knowledge JSON: {exc}") from exc
    try:
        return EvaluationKnowledgeBuild.model_validate_json(text)
    except ValidationError as exc:
        raise ValueError(f"invalid Evaluation Knowledge: {exc}") from exc


__all__ = [
    "EVALUATION_KNOWLEDGE_SCHEMA_VERSION",
    "EVALUATION_SELECTION_POLICY",
    "EvaluationClause",
    "EvaluationExclusion",
    "EvaluationExclusionReason",
    "EvaluationKnowledgeBuild",
    "EvaluationPacketInventory",
    "build_evaluation_knowledge",
    "load_evaluation_annotations_file",
    "load_evaluation_extraction_file",
    "load_evaluation_knowledge",
]
