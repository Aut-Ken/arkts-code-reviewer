from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

import pytest

from arkts_code_reviewer.feature_routing.config import (
    DimensionDefinition,
    DimensionTriggers,
    TagDefinition,
    TagTriggers,
)
from arkts_code_reviewer.knowledge.models import (
    AnnotationProvenance,
    Applicability,
    ClauseCandidate,
    ClauseModelReview,
    CurationDecision,
    KnowledgeAnnotation,
    KnowledgeModelReview,
    ModelReviewer,
    ModelReviewEvidence,
    ModelReviewSummary,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.knowledge.publication import (
    build_published_knowledge,
    curation_content_hash,
    load_published_knowledge,
)
from arkts_code_reviewer.knowledge.review_consensus import (
    ConsensusReviewRound,
    build_knowledge_review_consensus,
)
from arkts_code_reviewer.knowledge.review_consensus_build import (
    KnowledgeReviewConsensusBuild,
)
from arkts_code_reviewer.knowledge.review_packets import (
    KnowledgeReviewClause,
    KnowledgeReviewPacket,
    KnowledgeReviewPacketBuild,
    KnowledgeSourceExcerpt,
)

PUBLISHED_AT = datetime(2026, 7, 13, 8, 30, tzinfo=UTC)


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _candidate(
    index: int,
    *,
    neighbor_candidate_ids: tuple[str, ...] = (),
) -> ClauseCandidate:
    text = f"Caller must validate state {index}."
    source_ref = SourceRef(
        source_id="synthetic",
        revision=f"{index}" * 40,
        relative_path=f"docs/rule-{index}.md",
        anchor="L1-L1",
        authority="test",
        content_hash=_sha256(text),
    )
    return ClauseCandidate.create(
        native_rule_id=f"R-{index}",
        rule_type="constraint",
        text=text,
        heading_path=("Rules",),
        parent_context="Synthetic publication fixture.",
        neighbor_candidate_ids=neighbor_candidate_ids,
        applicability=Applicability(min_api_level=12),
        source_ref=source_ref,
        source_span=SourceSpan(start_line=1, end_line=1),
    )


def _review_clause(candidate: ClauseCandidate, index: int) -> KnowledgeReviewClause:
    rule_id = f"TEST/R-{index}"
    return KnowledgeReviewClause(
        rule_id=rule_id,
        proposed_status="Draft",
        domains=("test-domain",),
        candidate=candidate,
        annotation=KnowledgeAnnotation(
            target_kind="clause",
            target_id=rule_id,
            index_version="candidate:publication-fixture",
            tags=("has_test_signal",),
            dimension_ids=("DIM-99",),
            domains=("test-domain",),
            provenance=(
                AnnotationProvenance(
                    kind="dimension",
                    value="DIM-99",
                    origin="deterministic_parser",
                    evidence_ref="fixture:dimension",
                ),
                AnnotationProvenance(
                    kind="domain",
                    value="test-domain",
                    origin="source_metadata",
                    evidence_ref="fixture:domain",
                ),
                AnnotationProvenance(
                    kind="tag",
                    value="has_test_signal",
                    origin="deterministic_parser",
                    evidence_ref="fixture:tag",
                ),
            ),
            annotation_version="knowledge-annotation-version:sha256:" + "7" * 64,
        ),
    )


def _excerpt(clause: KnowledgeReviewClause) -> KnowledgeSourceExcerpt:
    source = clause.candidate.source_ref
    text = clause.candidate.text
    fields = {
        "source_id": source.source_id,
        "revision": source.revision,
        "relative_path": source.relative_path,
        "authority": source.authority,
        "content_hash": source.content_hash,
        "start_line": 1,
        "end_line": 1,
        "exact_text": text,
        "exact_text_hash": _sha256(text),
        "rule_ids": (clause.rule_id,),
    }
    return KnowledgeSourceExcerpt(
        excerpt_id=_canonical_hash("knowledge-source-excerpt", fields),
        source_id=source.source_id,
        revision=source.revision,
        relative_path=source.relative_path,
        authority=source.authority,
        content_hash=source.content_hash,
        start_line=1,
        end_line=1,
        exact_text=text,
        exact_text_hash=_sha256(text),
        rule_ids=(clause.rule_id,),
    )


def _packet(
    *,
    unknown_neighbor: bool = False,
) -> KnowledgeReviewPacket:
    first = _candidate(1)
    second = _candidate(2)
    if unknown_neighbor:
        first = _candidate(
            1,
            neighbor_candidate_ids=("clause-candidate:sha256:" + "f" * 64,),
        )
    else:
        first = _candidate(1, neighbor_candidate_ids=(second.candidate_id,))
        second = _candidate(2, neighbor_candidate_ids=(first.candidate_id,))
    clauses = tuple(
        sorted(
            (_review_clause(first, 1), _review_clause(second, 2)),
            key=lambda item: item.rule_id,
        )
    )
    prompt_hash = _sha256("Audit the attached publication fixture.")
    distribution: Literal["external_model"] = "external_model"
    extraction_build_id = "knowledge-extraction:sha256:" + "2" * 64
    annotation_build_id = "knowledge-annotation:sha256:" + "3" * 64
    source_bundle_id = "source-bundle:sha256:" + "4" * 64
    feature_config_fingerprint = "feature-config:sha256:" + "5" * 64
    annotation_config_fingerprint = "knowledge-annotation-config:sha256:" + "6" * 64
    annotation_version = "knowledge-annotation-version:sha256:" + "7" * 64
    export_policy_fingerprint = "knowledge-model-export-policy:sha256:" + "8" * 64
    tag_registry = (
        TagDefinition(
            id="has_test_signal",
            status="Active",
            description="Synthetic test signal.",
            triggers=TagTriggers(any_api=("test.api",)),
        ),
    )
    dimension_registry = (
        DimensionDefinition(
            id="DIM-99",
            title="Synthetic",
            status="Active",
            always_check=True,
            retrieval_policy="always",
            triggers=DimensionTriggers(),
        ),
    )
    source_excerpts = tuple(
        sorted(
            (_excerpt(clause) for clause in clauses),
            key=lambda item: (
                item.source_id,
                item.relative_path,
                item.start_line,
                item.end_line,
                item.excerpt_id,
            ),
        )
    )
    draft = KnowledgeReviewPacket.model_construct(
        packet_id="knowledge-review-packet:sha256:" + "0" * 64,
        distribution=distribution,
        model_provider="xai",
        model_name="grok-4.5",
        extraction_build_id=extraction_build_id,
        annotation_build_id=annotation_build_id,
        source_bundle_id=source_bundle_id,
        feature_config_fingerprint=feature_config_fingerprint,
        annotation_config_fingerprint=annotation_config_fingerprint,
        annotation_version=annotation_version,
        export_policy_fingerprint=export_policy_fingerprint,
        prompt_version="grok-knowledge-auditor-v4",
        prompt_hash=prompt_hash,
        tag_registry=tag_registry,
        dimension_registry=dimension_registry,
        source_domain_ids=("test-domain",),
        domain_registry=(),
        api_catalog_slice=(),
        unresolved_api_names=(),
        clauses=clauses,
        source_excerpts=source_excerpts,
    )
    return KnowledgeReviewPacket(
        packet_id=_canonical_hash("knowledge-review-packet", draft.identity_payload()),
        distribution=distribution,
        model_provider="xai",
        model_name="grok-4.5",
        extraction_build_id=extraction_build_id,
        annotation_build_id=annotation_build_id,
        source_bundle_id=source_bundle_id,
        feature_config_fingerprint=feature_config_fingerprint,
        annotation_config_fingerprint=annotation_config_fingerprint,
        annotation_version=annotation_version,
        export_policy_fingerprint=export_policy_fingerprint,
        prompt_version="grok-knowledge-auditor-v4",
        prompt_hash=prompt_hash,
        tag_registry=tag_registry,
        dimension_registry=dimension_registry,
        source_domain_ids=("test-domain",),
        domain_registry=(),
        api_catalog_slice=(),
        unresolved_api_names=(),
        clauses=clauses,
        source_excerpts=source_excerpts,
    )


def _packet_build(packet: KnowledgeReviewPacket) -> KnowledgeReviewPacketBuild:
    draft = KnowledgeReviewPacketBuild.model_construct(
        build_id="knowledge-review-packets:sha256:" + "0" * 64,
        distribution=packet.distribution,
        extraction_build_id=packet.extraction_build_id,
        annotation_build_id=packet.annotation_build_id,
        source_bundle_id=packet.source_bundle_id,
        export_policy_fingerprint=packet.export_policy_fingerprint,
        prompt_version=packet.prompt_version,
        prompt_hash=packet.prompt_hash,
        packets=(packet,),
    )
    return KnowledgeReviewPacketBuild(
        build_id=_canonical_hash("knowledge-review-packets", draft.identity_payload()),
        distribution=packet.distribution,
        extraction_build_id=packet.extraction_build_id,
        annotation_build_id=packet.annotation_build_id,
        source_bundle_id=packet.source_bundle_id,
        export_policy_fingerprint=packet.export_policy_fingerprint,
        prompt_version=packet.prompt_version,
        prompt_hash=packet.prompt_hash,
        packets=(packet,),
    )


def _model_review(
    packet: KnowledgeReviewPacket,
    *,
    decision: Literal["accept", "reject"],
) -> KnowledgeModelReview:
    reviews: list[ClauseModelReview] = []
    for clause in packet.clauses:
        if decision == "accept":
            issue_codes: tuple[str, ...] = ()
            evidence: tuple[ModelReviewEvidence, ...] = ()
        else:
            issue_codes = ("unsupported_claim",)
            evidence = (
                ModelReviewEvidence(
                    source_id=clause.candidate.source_ref.source_id,
                    relative_path=clause.candidate.source_ref.relative_path,
                    start_line=clause.candidate.source_span.start_line,
                    end_line=clause.candidate.source_span.end_line,
                    exact_quote=clause.candidate.text,
                ),
            )
        reviews.append(
            ClauseModelReview(
                rule_id=clause.rule_id,
                decision=decision,
                issue_codes=issue_codes,
                evidence=evidence,
                annotation_changes=(),
                rationale=f"Synthetic {decision} review.",
            )
        )
    return KnowledgeModelReview(
        packet_id=packet.packet_id,
        reviewer=ModelReviewer(
            kind="model",
            provider="xai",
            model="grok-4.5",
            prompt_version="grok-knowledge-auditor-v4",
        ),
        packet_decision=decision,
        clause_reviews=tuple(reviews),
        missing_clauses=(),
        duplicate_groups=(),
        conflicts=(),
        summary=ModelReviewSummary(
            accepted=len(reviews) if decision == "accept" else 0,
            rejected=len(reviews) if decision == "reject" else 0,
            uncertain=0,
            with_corrections=0,
        ),
    )


def _round(
    packet: KnowledgeReviewPacket,
    review: KnowledgeModelReview,
    round_name: str,
) -> ConsensusReviewRound:
    review_raw = json.dumps(
        review.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return ConsensusReviewRound(
        round_name=round_name,
        packet_id=packet.packet_id,
        packet_hash=_sha256(packet.model_dump_json()),
        prompt_version=packet.prompt_version,
        prompt_hash=packet.prompt_hash,
        provider="xai",
        model="grok-4.5",
        request_id=f"request-{round_name}",
        session_id=f"session-{round_name}",
        review_hash=_sha256(review_raw),
        receipt_hash=_sha256(f"receipt-{round_name}"),
        review=review,
    )


def _consensus_build(
    packets: KnowledgeReviewPacketBuild,
    *,
    decision: Literal["accept", "reject"] = "accept",
) -> KnowledgeReviewConsensusBuild:
    packet = packets.packets[0]
    review = _model_review(packet, decision=decision)
    packet_consensus = build_knowledge_review_consensus(
        rounds=(
            _round(packet, review, "round-1"),
            _round(packet, review, "round-2"),
        )
    )
    build_id = _canonical_hash(
        "knowledge-review-consensus-build",
        {
            "packet_build_id": packets.build_id,
            "packet_consensus_ids": (packet_consensus.consensus_id,),
        },
    )
    # Publication consumes an already validated consensus-build artifact. The focused
    # fixture constructs that outer artifact without recreating campaign receipts; the
    # packet-level value above still comes from two real, independent consensus rounds.
    return KnowledgeReviewConsensusBuild.model_construct(
        build_id=build_id,
        packet_build_id=packets.build_id,
        packet_consensus=(packet_consensus,),
        release_ready=packet_consensus.release_ready,
    )


def _decisions(
    packets: KnowledgeReviewPacketBuild,
) -> tuple[CurationDecision, ...]:
    clauses = packets.packets[0].clauses
    return tuple(
        CurationDecision(
            rule_id=clause.rule_id,
            content_hash=curation_content_hash(clause),
            content_decision="approved",
            annotation_decision="approved",
            reviewer_kind="human",
            reviewer_id="curator-1",
            review_version="human-curation-v1",
        )
        for clause in clauses
    )


def _artifacts(
    *,
    unknown_neighbor: bool = False,
    consensus_decision: Literal["accept", "reject"] = "accept",
) -> tuple[
    KnowledgeReviewPacketBuild,
    KnowledgeReviewConsensusBuild,
    tuple[CurationDecision, ...],
]:
    packets = _packet_build(_packet(unknown_neighbor=unknown_neighbor))
    return packets, _consensus_build(packets, decision=consensus_decision), _decisions(
        packets
    )


def test_publication_requires_human_curation_after_two_accepted_model_rounds() -> None:
    packets, consensus, decisions = _artifacts()
    assert consensus.packet_consensus[0].release_ready is True
    assert {item.status for item in consensus.packet_consensus[0].clauses} == {"accepted"}

    with pytest.raises(ValueError, match="cover exactly"):
        build_published_knowledge(
            packets=packets,
            consensus=consensus,
            curation_decisions=(),
            published_at=PUBLISHED_AT,
        )

    result = build_published_knowledge(
        packets=packets,
        consensus=consensus,
        curation_decisions=decisions,
        published_at=PUBLISHED_AT,
    )
    assert [item.clause.rule_id for item in result.clauses] == ["TEST/R-1", "TEST/R-2"]
    assert {item.clause.status for item in result.clauses} == {"Baselined"}
    assert result.curation_decisions == decisions
    assert result.clauses[0].clause.neighbor_rule_ids == ("TEST/R-2",)
    assert result.clauses[1].clause.neighbor_rule_ids == ("TEST/R-1",)
    assert result.build_id == result.expected_build_id()


def test_publication_is_deterministic_and_normalizes_decision_order() -> None:
    packets, consensus, decisions = _artifacts()

    forward = build_published_knowledge(
        packets=packets,
        consensus=consensus,
        curation_decisions=decisions,
        published_at=PUBLISHED_AT,
    )
    reverse = build_published_knowledge(
        packets=packets,
        consensus=consensus,
        curation_decisions=tuple(reversed(decisions)),
        published_at=PUBLISHED_AT,
    )

    assert forward == reverse
    assert forward.model_dump_json() == reverse.model_dump_json()


def test_publication_rejects_non_release_consensus() -> None:
    packets, consensus, decisions = _artifacts(consensus_decision="reject")
    assert consensus.release_ready is False

    with pytest.raises(ValueError, match="not release-ready"):
        build_published_knowledge(
            packets=packets,
            consensus=consensus,
            curation_decisions=decisions,
            published_at=PUBLISHED_AT,
        )


def test_publication_rejects_missing_and_extra_human_decisions() -> None:
    packets, consensus, decisions = _artifacts()
    extra = CurationDecision(
        rule_id="TEST/R-999",
        content_hash="sha256:" + "9" * 64,
        content_decision="approved",
        annotation_decision="approved",
        reviewer_kind="human",
        reviewer_id="curator-1",
        review_version="human-curation-v1",
    )

    for invalid in (
        decisions[:-1],
        (*decisions, extra),
        (*decisions, decisions[0]),
    ):
        with pytest.raises(ValueError, match="cover exactly"):
            build_published_knowledge(
                packets=packets,
                consensus=consensus,
                curation_decisions=invalid,
                published_at=PUBLISHED_AT,
            )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"reviewer_kind": "model"}, "clean human"),
        ({"content_decision": "rejected"}, "clean human"),
        ({"annotation_decision": "rejected"}, "clean human"),
        ({"issue_codes": ("unresolved",)}, "clean human"),
        ({"content_hash": "sha256:" + "f" * 64}, "does not match Clause content"),
    ],
)
def test_publication_rejects_unclean_or_content_drifted_decision(
    changes: dict[str, object],
    message: str,
) -> None:
    packets, consensus, decisions = _artifacts()
    invalid = (decisions[0].model_copy(update=changes), *decisions[1:])

    with pytest.raises(ValueError, match=message):
        build_published_knowledge(
            packets=packets,
            consensus=consensus,
            curation_decisions=invalid,
            published_at=PUBLISHED_AT,
        )


def test_publication_rejects_unknown_neighbor() -> None:
    packets, consensus, decisions = _artifacts(unknown_neighbor=True)

    with pytest.raises(ValueError, match="unpublished neighbor"):
        build_published_knowledge(
            packets=packets,
            consensus=consensus,
            curation_decisions=decisions,
            published_at=PUBLISHED_AT,
        )


def test_publication_rejects_cross_build_consensus_identity() -> None:
    packets, consensus, decisions = _artifacts()
    unrelated = consensus.model_copy(
        update={"packet_build_id": "knowledge-review-packets:sha256:" + "f" * 64}
    )

    with pytest.raises(ValueError, match="does not match packet build"):
        build_published_knowledge(
            packets=packets,
            consensus=unrelated,
            curation_decisions=decisions,
            published_at=PUBLISHED_AT,
        )


def test_publication_strict_loader_round_trip_and_boundaries() -> None:
    packets, consensus, decisions = _artifacts()
    result = build_published_knowledge(
        packets=packets,
        consensus=consensus,
        curation_decisions=decisions,
        published_at=PUBLISHED_AT,
    )
    raw = result.model_dump_json()

    assert load_published_knowledge(raw) == result
    with pytest.raises(ValueError, match="duplicate JSON key: build_id"):
        load_published_knowledge('{"build_id":"one","build_id":"two"}')
    with pytest.raises(ValueError, match="UTF-8"):
        load_published_knowledge(b"\xff")

    unknown = result.model_dump(mode="json")
    unknown["unknown"] = True
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_published_knowledge(json.dumps(unknown))

    tampered = result.model_dump(mode="json")
    tampered["build_id"] = "published-knowledge:sha256:" + "f" * 64
    with pytest.raises(ValueError, match="build_id does not match content"):
        load_published_knowledge(json.dumps(tampered))


def test_publication_loader_rejects_noncanonical_collection_order() -> None:
    packets, consensus, decisions = _artifacts()
    result = build_published_knowledge(
        packets=packets,
        consensus=consensus,
        curation_decisions=decisions,
        published_at=PUBLISHED_AT,
    )
    payload = result.model_dump(mode="json")
    payload["clauses"] = list(reversed(payload["clauses"]))
    payload["curation_decisions"] = list(reversed(payload["curation_decisions"]))

    with pytest.raises(ValueError, match="sorted|align|cover exactly"):
        load_published_knowledge(json.dumps(payload))
