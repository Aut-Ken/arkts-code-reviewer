from __future__ import annotations

import copy
import hashlib
import json

import pytest

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.annotation_config import (
    load_knowledge_annotation_config,
)
from arkts_code_reviewer.knowledge.models import (
    AnnotationProvenance,
    Applicability,
    ClauseCandidate,
    KnowledgeAnnotation,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.knowledge.review_packets import (
    KnowledgeReviewClause,
    KnowledgeReviewPacket,
    KnowledgeSourceExcerpt,
)
from arkts_code_reviewer.knowledge.review_validation import (
    load_and_validate_knowledge_model_review,
)


def _sha256(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _source_ref(body: str, line: int) -> SourceRef:
    return SourceRef(
        source_id="official-docs",
        revision="1" * 40,
        relative_path="rules/lifecycle.md",
        anchor=f"L{line}-L{line}",
        authority="official_documentation",
        content_hash=_sha256(body),
    )


def _candidate(body: str, *, line: int, text: str) -> ClauseCandidate:
    return ClauseCandidate.create(
        native_rule_id=None,
        rule_type="constraint",
        text=text,
        heading_path=("资源生命周期",),
        parent_context=None,
        neighbor_candidate_ids=(),
        applicability=Applicability(),
        source_ref=_source_ref(body, line),
        source_span=SourceSpan(start_line=line, end_line=line),
    )


def _annotation(rule_id: str, *, timer: bool) -> KnowledgeAnnotation:
    dimensions: tuple[str, ...]
    domains: tuple[str, ...]
    tags: tuple[str, ...]
    provenance: tuple[AnnotationProvenance, ...]
    if timer:
        dimensions = ("DIM-06",)
        domains = ("resource-management",)
        tags = ("has_timer",)
        provenance = (
            AnnotationProvenance(
                kind="dimension",
                value="DIM-06",
                origin="deterministic_parser",
                evidence_ref="config:dimension:DIM-06",
            ),
            AnnotationProvenance(
                kind="domain",
                value="resource-management",
                origin="deterministic_parser",
                evidence_ref="config:domain:resource-management",
            ),
            AnnotationProvenance(
                kind="tag",
                value="has_timer",
                origin="deterministic_parser",
                evidence_ref="config:tag:has_timer",
            ),
        )
    else:
        dimensions = ()
        domains = ()
        tags = ()
        provenance = ()
    return KnowledgeAnnotation(
        target_kind="clause",
        target_id=rule_id,
        index_version="review-draft-v1",
        dimension_ids=dimensions,
        tags=tags,
        domains=domains,
        provenance=provenance,
        annotation_version="annotation-v1",
    )


def _excerpt(
    body: str,
    *,
    line: int,
    exact_text: str,
    rule_id: str,
) -> KnowledgeSourceExcerpt:
    source = _source_ref(body, line)
    draft = KnowledgeSourceExcerpt.model_construct(
        excerpt_id="knowledge-source-excerpt:sha256:" + "0" * 64,
        source_id=source.source_id,
        revision=source.revision,
        relative_path=source.relative_path,
        authority=source.authority,
        content_hash=source.content_hash,
        start_line=line,
        end_line=line,
        exact_text=exact_text,
        exact_text_hash=_sha256(exact_text),
        rule_ids=(rule_id,),
    )
    payload = draft.model_dump(mode="json")
    payload["excerpt_id"] = draft.expected_excerpt_id()
    return KnowledgeSourceExcerpt.model_validate_json(json.dumps(payload))


@pytest.fixture
def review_packet() -> KnowledgeReviewPacket:
    feature_config = load_default_feature_config()
    annotation_config = load_knowledge_annotation_config(
        feature_config=feature_config,
    )
    body = "# 生命周期\n定时器不再使用时必须清理。\nWorker 结束时必须关闭。\n"
    records = (
        KnowledgeReviewClause(
            rule_id="LIFECYCLE/R-1",
            proposed_status="Draft",
            domains=("timer-subscription-lifecycle",),
            candidate=_candidate(body, line=2, text="定时器不再使用时必须清理。"),
            annotation=_annotation("LIFECYCLE/R-1", timer=True),
        ),
        KnowledgeReviewClause(
            rule_id="LIFECYCLE/R-2",
            proposed_status="Draft",
            domains=("async-taskpool-worker",),
            candidate=_candidate(body, line=3, text="Worker 结束时必须关闭。"),
            annotation=_annotation("LIFECYCLE/R-2", timer=False),
        ),
    )
    excerpts = (
        _excerpt(
            body,
            line=2,
            exact_text="定时器不再使用时必须清理。",
            rule_id="LIFECYCLE/R-1",
        ),
        _excerpt(
            body,
            line=3,
            exact_text="Worker 结束时必须关闭。",
            rule_id="LIFECYCLE/R-2",
        ),
    )
    draft = KnowledgeReviewPacket.model_construct(
        packet_id="knowledge-review-packet:sha256:" + "0" * 64,
        distribution="external_model",
        model_provider="xai",
        model_name="grok-4",
        extraction_build_id="knowledge-extraction:sha256:" + "1" * 64,
        annotation_build_id="knowledge-annotation:sha256:" + "2" * 64,
        source_bundle_id="source-bundle:sha256:" + "3" * 64,
        feature_config_fingerprint="feature-config:sha256:" + "4" * 64,
        annotation_config_fingerprint=(
            "knowledge-annotation-config:sha256:" + "5" * 64
        ),
        annotation_version="knowledge-annotation-version:sha256:" + "6" * 64,
        export_policy_fingerprint=(
            "knowledge-model-export-policy:sha256:" + "7" * 64
        ),
        prompt_version="grok-knowledge-auditor-v2",
        prompt_hash="sha256:" + "8" * 64,
        tag_registry=tuple(
            sorted(feature_config.tags_by_id.values(), key=lambda item: item.id)
        ),
        dimension_registry=tuple(
            sorted(feature_config.dimensions_by_id.values(), key=lambda item: item.id)
        ),
        source_domain_ids=annotation_config.source_domain_ids,
        domain_registry=tuple(
            sorted(annotation_config.domain_rules, key=lambda item: item.domain_id)
        ),
        api_catalog_slice=(),
        unresolved_api_names=(),
        clauses=records,
        source_excerpts=excerpts,
    )
    payload = draft.model_dump(mode="json")
    payload["packet_id"] = draft.expected_packet_id()
    return KnowledgeReviewPacket.model_validate_json(json.dumps(payload))


def _accept_payload(packet: KnowledgeReviewPacket) -> dict[str, object]:
    return {
        "schema_version": "knowledge-model-review-v1",
        "packet_id": packet.packet_id,
        "reviewer": {
            "kind": "model",
            "provider": "xai",
            "model": "grok-4",
            "prompt_version": "grok-knowledge-auditor-v2",
        },
        "packet_decision": "accept",
        "clause_reviews": [
            {
                "rule_id": clause.rule_id,
                "decision": "accept",
                "issue_codes": [],
                "evidence": [],
                "annotation_changes": [],
                "rationale": "The Clause and annotations match the packet evidence.",
            }
            for clause in packet.clauses
        ],
        "missing_clauses": [],
        "duplicate_groups": [],
        "conflicts": [],
        "summary": {
            "accepted": len(packet.clauses),
            "rejected": 0,
            "uncertain": 0,
            "with_corrections": 0,
        },
    }


def _raw(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _evidence(*, line: int, quote: str) -> dict[str, object]:
    return {
        "source_id": "official-docs",
        "relative_path": "rules/lifecycle.md",
        "start_line": line,
        "end_line": line,
        "exact_quote": quote,
    }


def _sync_summary(payload: dict[str, object]) -> None:
    reviews = payload["clause_reviews"]
    assert isinstance(reviews, list)
    decisions = [item["decision"] for item in reviews]
    payload["summary"] = {
        "accepted": decisions.count("accept"),
        "rejected": decisions.count("reject"),
        "uncertain": decisions.count("uncertain"),
        "with_corrections": decisions.count("accept_with_corrections"),
    }


def test_accept_review_is_bound_to_every_packet_clause(
    review_packet: KnowledgeReviewPacket,
) -> None:
    result = load_and_validate_knowledge_model_review(
        _raw(_accept_payload(review_packet)),
        packet=review_packet,
    )

    assert result.packet_id == review_packet.packet_id
    assert tuple(item.rule_id for item in result.clause_reviews) == (
        "LIFECYCLE/R-1",
        "LIFECYCLE/R-2",
    )


def test_duplicate_json_key_is_rejected(review_packet: KnowledgeReviewPacket) -> None:
    raw = _raw(_accept_payload(review_packet)).decode()
    forged = raw.replace(
        '"schema_version":',
        '"schema_version":"knowledge-model-review-v1","schema_version":',
        1,
    )

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_and_validate_knowledge_model_review(forged, packet=review_packet)


@pytest.mark.parametrize("coverage", ["missing", "extra"])
def test_clause_coverage_must_be_exact(
    review_packet: KnowledgeReviewPacket,
    coverage: str,
) -> None:
    payload = _accept_payload(review_packet)
    reviews = payload["clause_reviews"]
    assert isinstance(reviews, list)
    if coverage == "missing":
        reviews.pop()
    else:
        extra = copy.deepcopy(reviews[-1])
        extra["rule_id"] = "LIFECYCLE/R-9"
        reviews.append(extra)
    _sync_summary(payload)

    with pytest.raises(ValueError, match="coverage does not match packet"):
        load_and_validate_knowledge_model_review(_raw(payload), packet=review_packet)


def test_review_packet_id_and_model_must_match(review_packet: KnowledgeReviewPacket) -> None:
    packet_payload = _accept_payload(review_packet)
    packet_payload["packet_id"] = "knowledge-review-packet:sha256:" + "f" * 64
    with pytest.raises(ValueError, match="packet_id does not match"):
        load_and_validate_knowledge_model_review(_raw(packet_payload), packet=review_packet)

    model_payload = _accept_payload(review_packet)
    reviewer = model_payload["reviewer"]
    assert isinstance(reviewer, dict)
    reviewer["model"] = "grok-other"
    with pytest.raises(ValueError, match="model does not match"):
        load_and_validate_knowledge_model_review(_raw(model_payload), packet=review_packet)


@pytest.mark.parametrize(
    ("evidence", "message"),
    [
        (
            {
                "source_id": "official-docs",
                "relative_path": "../escape.md",
                "start_line": 2,
                "end_line": 2,
                "exact_quote": "定时器不再使用时必须清理。",
            },
            "safe relative path",
        ),
        (_evidence(line=99, quote="outside"), "not covered"),
        (_evidence(line=2, quote="wrong quote"), "exact_quote does not match"),
        (
            _evidence(line=3, quote="Worker 结束时必须关闭。"),
            "not covered by a related",
        ),
    ],
)
def test_evidence_must_match_related_excerpt_exactly(
    review_packet: KnowledgeReviewPacket,
    evidence: dict[str, object],
    message: str,
) -> None:
    payload = _accept_payload(review_packet)
    reviews = payload["clause_reviews"]
    assert isinstance(reviews, list)
    reviews[0].update(
        decision="reject",
        issue_codes=["unsupported_claim"],
        evidence=[evidence],
    )
    payload["packet_decision"] = "reject"
    _sync_summary(payload)

    with pytest.raises(ValueError, match=message):
        load_and_validate_knowledge_model_review(_raw(payload), packet=review_packet)


def test_registered_annotation_correction_is_accepted_as_uncertain(
    review_packet: KnowledgeReviewPacket,
) -> None:
    payload = _accept_payload(review_packet)
    reviews = payload["clause_reviews"]
    assert isinstance(reviews, list)
    reviews[0].update(
        decision="accept_with_corrections",
        issue_codes=["tag_error"],
        evidence=[_evidence(line=2, quote="定时器不再使用时必须清理。")],
        annotation_changes=[
            {
                "annotation_kind": "tag",
                "current_value": "has_timer",
                "proposed_action": "replace",
                "proposed_value": "has_worker",
                "reason_code": "tag_not_applicable",
            }
        ],
    )
    payload["packet_decision"] = "uncertain"
    _sync_summary(payload)

    result = load_and_validate_knowledge_model_review(_raw(payload), packet=review_packet)
    assert result.clause_reviews[0].decision == "accept_with_corrections"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {
                "annotation_kind": "tag",
                "current_value": "has_timer",
                "proposed_action": "replace",
                "proposed_value": "has_unregistered",
                "reason_code": "tag_not_registered",
            },
            "not registered",
        ),
        (
            {
                "annotation_kind": "tag",
                "current_value": "has_async",
                "proposed_action": "remove",
                "proposed_value": None,
                "reason_code": "tag_not_applicable",
            },
            "current_value is absent",
        ),
        (
            {
                "annotation_kind": "scenario",
                "current_value": None,
                "proposed_action": "add",
                "proposed_value": "model supplied scenario",
                "reason_code": "insufficient_source_evidence",
            },
            "only correct registered",
        ),
    ],
)
def test_annotation_changes_are_bound_to_snapshot_and_registries(
    review_packet: KnowledgeReviewPacket,
    change: dict[str, object],
    message: str,
) -> None:
    payload = _accept_payload(review_packet)
    reviews = payload["clause_reviews"]
    assert isinstance(reviews, list)
    reviews[0].update(
        decision="accept_with_corrections",
        issue_codes=["annotation_error"],
        evidence=[_evidence(line=2, quote="定时器不再使用时必须清理。")],
        annotation_changes=[change],
    )
    payload["packet_decision"] = "uncertain"
    _sync_summary(payload)

    with pytest.raises(ValueError, match=message):
        load_and_validate_knowledge_model_review(_raw(payload), packet=review_packet)


def test_packet_decision_has_frozen_semantics(
    review_packet: KnowledgeReviewPacket,
) -> None:
    payload = _accept_payload(review_packet)
    reviews = payload["clause_reviews"]
    assert isinstance(reviews, list)
    reviews[0].update(
        decision="accept_with_corrections",
        issue_codes=["tag_error"],
        evidence=[_evidence(line=2, quote="定时器不再使用时必须清理。")],
        annotation_changes=[
            {
                "annotation_kind": "tag",
                "current_value": "has_timer",
                "proposed_action": "replace",
                "proposed_value": "has_worker",
                "reason_code": "tag_not_applicable",
            }
        ],
    )
    payload["packet_decision"] = "reject"
    _sync_summary(payload)

    with pytest.raises(ValueError, match="frozen decision policy"):
        load_and_validate_knowledge_model_review(_raw(payload), packet=review_packet)


def test_duplicate_and_conflict_members_must_be_known_and_evidenced(
    review_packet: KnowledgeReviewPacket,
) -> None:
    no_evidence = _accept_payload(review_packet)
    no_evidence["packet_decision"] = "uncertain"
    no_evidence["duplicate_groups"] = [
        {"rule_ids": ["LIFECYCLE/R-1", "LIFECYCLE/R-2"], "evidence": []}
    ]
    with pytest.raises(ValueError, match="at least 1 item"):
        load_and_validate_knowledge_model_review(
            _raw(no_evidence),
            packet=review_packet,
        )

    unknown = _accept_payload(review_packet)
    unknown["packet_decision"] = "uncertain"
    unknown["duplicate_groups"] = [
        {
            "rule_ids": ["LIFECYCLE/R-1", "LIFECYCLE/R-9"],
            "evidence": [_evidence(line=2, quote="定时器不再使用时必须清理。")],
        }
    ]
    with pytest.raises(ValueError, match="unknown rule_id"):
        load_and_validate_knowledge_model_review(_raw(unknown), packet=review_packet)


def test_missing_clause_cannot_collide_with_packet_rule(
    review_packet: KnowledgeReviewPacket,
) -> None:
    payload = _accept_payload(review_packet)
    payload["packet_decision"] = "uncertain"
    payload["missing_clauses"] = [
        {
            "proposed_rule_id": "LIFECYCLE/R-1",
            "rule_type": "constraint",
            "text": "Duplicated proposal.",
            "evidence": [_evidence(line=2, quote="定时器不再使用时必须清理。")],
            "rationale": "The proposal uses an existing identity.",
        }
    ]

    with pytest.raises(ValueError, match="collides with a packet rule_id"):
        load_and_validate_knowledge_model_review(_raw(payload), packet=review_packet)


def test_packet_identity_is_rechecked_before_review(
    review_packet: KnowledgeReviewPacket,
) -> None:
    forged = review_packet.model_copy(
        update={"packet_id": "knowledge-review-packet:sha256:" + "f" * 64}
    )

    with pytest.raises(ValueError, match="packet identity drift"):
        load_and_validate_knowledge_model_review(
            _raw(_accept_payload(review_packet)),
            packet=forged,
        )


def test_packet_excerpt_line_count_is_checked_before_acceptance(
    review_packet: KnowledgeReviewPacket,
) -> None:
    original = review_packet.source_excerpts[0]
    bad_excerpt_payload = original.model_dump()
    bad_excerpt_payload.update(
        excerpt_id="knowledge-source-excerpt:sha256:" + "0" * 64,
        end_line=original.end_line + 1,
    )
    bad_excerpt = KnowledgeSourceExcerpt.model_construct(**bad_excerpt_payload)
    bad_excerpt = bad_excerpt.model_copy(
        update={"excerpt_id": bad_excerpt.expected_excerpt_id()}
    )
    draft = review_packet.model_copy(
        update={
            "packet_id": "knowledge-review-packet:sha256:" + "0" * 64,
            "source_excerpts": (bad_excerpt, *review_packet.source_excerpts[1:]),
        }
    )
    forged = draft.model_copy(update={"packet_id": draft.expected_packet_id()})

    with pytest.raises(ValueError, match="line count does not match"):
        load_and_validate_knowledge_model_review(
            _raw(_accept_payload(forged)),
            packet=forged,
        )
