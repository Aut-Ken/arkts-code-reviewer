from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from arkts_code_reviewer.knowledge.models import (
    ApiSymbol,
    CurationDecision,
    KnowledgeAnnotation,
    KnowledgeClause,
)
from arkts_code_reviewer.knowledge.review_consensus_build import (
    KnowledgeReviewConsensusBuild,
)
from arkts_code_reviewer.knowledge.review_packets import (
    KnowledgeReviewClause,
    KnowledgeReviewPacketBuild,
)

PUBLICATION_SCHEMA_VERSION = "published-knowledge-build-v1"

_BUILD_ID_PATTERN = r"^published-knowledge:sha256:[0-9a-f]{64}$"
_CONSENSUS_BUILD_ID_PATTERN = (
    r"^knowledge-review-consensus-build:sha256:[0-9a-f]{64}$"
)
_PACKET_BUILD_ID_PATTERN = r"^knowledge-review-packets:sha256:[0-9a-f]{64}$"
_EXTRACTION_BUILD_ID_PATTERN = r"^knowledge-extraction:sha256:[0-9a-f]{64}$"
_ANNOTATION_BUILD_ID_PATTERN = r"^knowledge-annotation:sha256:[0-9a-f]{64}$"
_SOURCE_BUNDLE_ID_PATTERN = r"^source-bundle:sha256:[0-9a-f]{64}$"


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


def _content_hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _parse_utc_datetime(value: object) -> datetime:
    if isinstance(value, str):
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("published_at must use ISO-8601") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError("published_at must be a datetime")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("published_at must be timezone-aware UTC")
    return parsed


class PublishedClause(_FrozenModel):
    clause: KnowledgeClause
    annotation: KnowledgeAnnotation
    domains: tuple[str, ...]

    @field_validator("domains", mode="before")
    @classmethod
    def parse_domains(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("PublishedClause.domains must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_record(self) -> PublishedClause:
        if self.clause.status != "Baselined":
            raise ValueError("Published Knowledge may contain only Baselined Clauses")
        if not self.domains or list(self.domains) != sorted(set(self.domains)):
            raise ValueError("PublishedClause.domains must be non-empty, sorted, and unique")
        if self.annotation.target_kind != "clause":
            raise ValueError("Published annotation must target a Clause")
        if self.annotation.target_id != self.clause.rule_id:
            raise ValueError("Published annotation target must match rule_id")
        return self


class PublishedKnowledgeBuild(_FrozenModel):
    schema_version: Literal["published-knowledge-build-v1"] = (
        "published-knowledge-build-v1"
    )
    build_id: Annotated[str, Field(pattern=_BUILD_ID_PATTERN)]
    packet_build_id: Annotated[str, Field(pattern=_PACKET_BUILD_ID_PATTERN)]
    consensus_build_id: Annotated[
        str,
        Field(pattern=_CONSENSUS_BUILD_ID_PATTERN),
    ]
    extraction_build_id: Annotated[str, Field(pattern=_EXTRACTION_BUILD_ID_PATTERN)]
    annotation_build_id: Annotated[str, Field(pattern=_ANNOTATION_BUILD_ID_PATTERN)]
    source_bundle_id: Annotated[str, Field(pattern=_SOURCE_BUNDLE_ID_PATTERN)]
    feature_config_fingerprint: Annotated[str, Field(min_length=1)]
    annotation_config_fingerprint: Annotated[str, Field(min_length=1)]
    annotation_version: Annotated[str, Field(min_length=1)]
    source_annotation_index_version: Annotated[str, Field(min_length=1)]
    curation_version: Annotated[
        str,
        Field(pattern=r"^knowledge-curation:sha256:[0-9a-f]{64}$"),
    ]
    published_at: datetime
    curation_decisions: tuple[CurationDecision, ...]
    clauses: tuple[PublishedClause, ...]
    api_symbols: tuple[ApiSymbol, ...]

    @field_validator("published_at", mode="before")
    @classmethod
    def parse_published_at(cls, value: object) -> datetime:
        return _parse_utc_datetime(value)

    @field_validator(
        "curation_decisions",
        "clauses",
        "api_symbols",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Published Knowledge collections must be sequences")
        return tuple(value)

    def identity_payload(self) -> dict[str, object]:
        return {
            "packet_build_id": self.packet_build_id,
            "consensus_build_id": self.consensus_build_id,
            "extraction_build_id": self.extraction_build_id,
            "annotation_build_id": self.annotation_build_id,
            "source_bundle_id": self.source_bundle_id,
            "feature_config_fingerprint": self.feature_config_fingerprint,
            "annotation_config_fingerprint": self.annotation_config_fingerprint,
            "annotation_version": self.annotation_version,
            "source_annotation_index_version": self.source_annotation_index_version,
            "curation_version": self.curation_version,
            "published_at": self.published_at.isoformat(),
            "curation_decisions": [
                item.model_dump(mode="json") for item in self.curation_decisions
            ],
            "clauses": [item.model_dump(mode="json") for item in self.clauses],
            "api_symbols": [
                item.model_dump(mode="json") for item in self.api_symbols
            ],
        }

    def expected_build_id(self) -> str:
        return _canonical_hash("published-knowledge", self.identity_payload())

    @model_validator(mode="after")
    def validate_build(self) -> PublishedKnowledgeBuild:
        rule_ids = [item.clause.rule_id for item in self.clauses]
        if not rule_ids or rule_ids != sorted(set(rule_ids)):
            raise ValueError("Published Clauses must be non-empty, sorted, and unique")
        decision_ids = [item.rule_id for item in self.curation_decisions]
        if decision_ids != rule_ids:
            raise ValueError("Human curation decisions must cover exactly the Clauses")
        if any(
            item.content_decision != "approved"
            or item.annotation_decision != "approved"
            or item.reviewer_kind != "human"
            or item.issue_codes
            for item in self.curation_decisions
        ):
            raise ValueError("Published Clauses require clean human curation approval")
        annotation_ids = [item.annotation.target_id for item in self.clauses]
        if annotation_ids != rule_ids:
            raise ValueError("Published annotations must align with Clauses")
        known_rule_ids = set(rule_ids)
        for item in self.clauses:
            clause = item.clause
            if clause.curation_version != self.curation_version:
                raise ValueError("Published Clause curation version does not match build")
            if (
                clause.created_at != self.published_at
                or clause.updated_at != self.published_at
            ):
                raise ValueError("Published Clause timestamps do not match published_at")
            if not set(clause.neighbor_rule_ids).issubset(known_rule_ids):
                raise ValueError("Published Clause references an unknown neighbor")
            if item.annotation.annotation_version != self.annotation_version:
                raise ValueError("Published annotation version does not match build")
            if item.annotation.index_version != self.source_annotation_index_version:
                raise ValueError("Published annotation index version does not match build")

        api_keys = [
            (item.canonical_name, item.signature, item.declaration_id)
            for item in self.api_symbols
        ]
        if api_keys != sorted(set(api_keys)):
            raise ValueError("Published API symbols must be sorted and unique")
        declaration_ids = [item.declaration_id for item in self.api_symbols]
        if len(declaration_ids) != len(set(declaration_ids)):
            raise ValueError("Published API declaration IDs must be unique")
        if self.build_id != self.expected_build_id():
            raise ValueError("PublishedKnowledgeBuild.build_id does not match content")
        return self


def _deduplicate_api_symbols(
    packets: KnowledgeReviewPacketBuild,
) -> tuple[ApiSymbol, ...]:
    by_id: dict[str, ApiSymbol] = {}
    for packet in packets.packets:
        for symbol in packet.api_catalog_slice:
            previous = by_id.setdefault(symbol.declaration_id, symbol)
            if previous != symbol:
                raise ValueError("Knowledge packets disagree about an API declaration")
    return tuple(
        sorted(
            by_id.values(),
            key=lambda item: (
                item.canonical_name,
                item.signature,
                item.declaration_id,
            ),
        )
    )


def _publication_metadata(
    packets: KnowledgeReviewPacketBuild,
) -> tuple[str, str, str, str]:
    feature_fingerprints = {
        item.feature_config_fingerprint for item in packets.packets
    }
    annotation_fingerprints = {
        item.annotation_config_fingerprint for item in packets.packets
    }
    annotation_versions = {item.annotation_version for item in packets.packets}
    source_index_versions = {
        clause.annotation.index_version
        for packet in packets.packets
        for clause in packet.clauses
    }
    if any(
        len(values) != 1
        for values in (
            feature_fingerprints,
            annotation_fingerprints,
            annotation_versions,
            source_index_versions,
        )
    ):
        raise ValueError("Knowledge packets disagree about annotation provenance")
    return (
        next(iter(feature_fingerprints)),
        next(iter(annotation_fingerprints)),
        next(iter(annotation_versions)),
        next(iter(source_index_versions)),
    )


def _materialize_clause(
    value: KnowledgeReviewClause,
    *,
    candidate_to_rule_id: dict[str, str],
    curation_version: str,
    published_at: datetime,
) -> PublishedClause:
    candidate = value.candidate
    try:
        neighbor_rule_ids = tuple(
            sorted(
                {
                    candidate_to_rule_id[candidate_id]
                    for candidate_id in candidate.neighbor_candidate_ids
                }
            )
        )
    except KeyError as exc:
        raise ValueError("Knowledge Clause references an unpublished neighbor") from exc
    clause = KnowledgeClause(
        rule_id=value.rule_id,
        native_rule_id=candidate.native_rule_id,
        rule_type=candidate.rule_type,
        status="Baselined",
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
        curation_version=curation_version,
        created_at=published_at,
        updated_at=published_at,
    )
    return PublishedClause(
        clause=clause,
        annotation=value.annotation,
        domains=value.domains,
    )


def curation_content_hash(value: KnowledgeReviewClause) -> str:
    """Bind a curator's approval to the exact Clause and annotation payload."""

    return _content_hash(
        {
            "rule_id": value.rule_id,
            "domains": value.domains,
            "candidate": value.candidate.model_dump(mode="json"),
            "annotation": value.annotation.model_dump(mode="json"),
        }
    )


def build_published_knowledge(
    *,
    packets: KnowledgeReviewPacketBuild,
    consensus: KnowledgeReviewConsensusBuild,
    curation_decisions: tuple[CurationDecision, ...],
    published_at: datetime,
) -> PublishedKnowledgeBuild:
    if not isinstance(packets, KnowledgeReviewPacketBuild):
        raise TypeError("packets must use KnowledgeReviewPacketBuild")
    if not isinstance(consensus, KnowledgeReviewConsensusBuild):
        raise TypeError("consensus must use KnowledgeReviewConsensusBuild")
    timestamp = _parse_utc_datetime(published_at)
    if consensus.packet_build_id != packets.build_id:
        raise ValueError("Knowledge consensus does not match packet build")
    if not consensus.release_ready:
        raise ValueError("Knowledge consensus is not release-ready")

    packets_by_id = {item.packet_id: item for item in packets.packets}
    consensus_by_id = {
        item.packet_id: item for item in consensus.packet_consensus
    }
    if tuple(packets_by_id) != tuple(consensus_by_id):
        raise ValueError("Knowledge consensus does not cover exactly the packet build")

    review_clauses: list[KnowledgeReviewClause] = []
    for packet_id, packet in packets_by_id.items():
        packet_consensus = consensus_by_id[packet_id]
        if not packet_consensus.release_ready or packet_consensus.release_blockers:
            raise ValueError("Knowledge packet consensus is not release-ready")
        clause_status = {
            item.rule_id: item.status for item in packet_consensus.clauses
        }
        packet_rule_ids = tuple(item.rule_id for item in packet.clauses)
        if tuple(clause_status) != packet_rule_ids:
            raise ValueError("Knowledge consensus Clause coverage does not match packet")
        if set(clause_status.values()) != {"accepted"}:
            raise ValueError("Only independently accepted Clauses may be published")
        review_clauses.extend(packet.clauses)

    candidate_to_rule_id: dict[str, str] = {}
    for value in review_clauses:
        previous = candidate_to_rule_id.setdefault(
            value.candidate.candidate_id,
            value.rule_id,
        )
        if previous != value.rule_id:
            raise ValueError("Knowledge candidates map to multiple rule IDs")

    decisions = tuple(sorted(curation_decisions, key=lambda item: item.rule_id))
    decision_ids = [item.rule_id for item in decisions]
    review_clause_by_id = {item.rule_id: item for item in review_clauses}
    if decision_ids != sorted(review_clause_by_id):
        raise ValueError("Curation decisions must cover exactly the accepted Clauses")
    for decision in decisions:
        if (
            decision.reviewer_kind != "human"
            or decision.content_decision != "approved"
            or decision.annotation_decision != "approved"
            or decision.issue_codes
        ):
            raise ValueError("Publication requires clean human curation approval")
        expected_hash = curation_content_hash(review_clause_by_id[decision.rule_id])
        if decision.content_hash != expected_hash:
            raise ValueError("Curation decision does not match Clause content")
    curation_version = _canonical_hash(
        "knowledge-curation",
        {
            "consensus_build_id": consensus.build_id,
            "decisions": [item.model_dump(mode="json") for item in decisions],
        },
    )

    feature_fingerprint, annotation_fingerprint, annotation_version, source_index = (
        _publication_metadata(packets)
    )
    published_clauses = tuple(
        sorted(
            (
                _materialize_clause(
                    item,
                    candidate_to_rule_id=candidate_to_rule_id,
                    curation_version=curation_version,
                    published_at=timestamp,
                )
                for item in review_clauses
            ),
            key=lambda item: item.clause.rule_id,
        )
    )
    api_symbols = _deduplicate_api_symbols(packets)
    draft = PublishedKnowledgeBuild.model_construct(
        build_id="published-knowledge:sha256:" + "0" * 64,
        packet_build_id=packets.build_id,
        consensus_build_id=consensus.build_id,
        extraction_build_id=packets.extraction_build_id,
        annotation_build_id=packets.annotation_build_id,
        source_bundle_id=packets.source_bundle_id,
        feature_config_fingerprint=feature_fingerprint,
        annotation_config_fingerprint=annotation_fingerprint,
        annotation_version=annotation_version,
        source_annotation_index_version=source_index,
        curation_version=curation_version,
        published_at=timestamp,
        curation_decisions=decisions,
        clauses=published_clauses,
        api_symbols=api_symbols,
    )
    return PublishedKnowledgeBuild(
        build_id=_canonical_hash("published-knowledge", draft.identity_payload()),
        packet_build_id=packets.build_id,
        consensus_build_id=consensus.build_id,
        extraction_build_id=packets.extraction_build_id,
        annotation_build_id=packets.annotation_build_id,
        source_bundle_id=packets.source_bundle_id,
        feature_config_fingerprint=feature_fingerprint,
        annotation_config_fingerprint=annotation_fingerprint,
        annotation_version=annotation_version,
        source_annotation_index_version=source_index,
        curation_version=curation_version,
        published_at=timestamp,
        curation_decisions=decisions,
        clauses=published_clauses,
        api_symbols=api_symbols,
    )


def load_published_knowledge(raw: str | bytes) -> PublishedKnowledgeBuild:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Published Knowledge must use UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("Published Knowledge input must be str or bytes")
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid Published Knowledge JSON: {exc}") from exc
    try:
        return PublishedKnowledgeBuild.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid Published Knowledge: {exc}") from exc


__all__ = [
    "PUBLICATION_SCHEMA_VERSION",
    "PublishedClause",
    "PublishedKnowledgeBuild",
    "build_published_knowledge",
    "curation_content_hash",
    "load_published_knowledge",
]
