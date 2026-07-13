from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.knowledge.models import (
    AnnotationChange,
    ClauseModelReview,
    DuplicateClauseGroup,
    KnowledgeConflictReview,
    KnowledgeModelReview,
    MissingClauseReview,
    ModelReviewDecision,
    ModelReviewer,
    ModelReviewEvidence,
    ModelReviewSummary,
)
from arkts_code_reviewer.knowledge.review_consensus import (
    ConsensusReviewRound,
    KnowledgeReviewConsensus,
    build_knowledge_review_consensus,
    load_knowledge_review_consensus,
)

PACKET_ID = "knowledge-review-packet:sha256:" + "1" * 64
PACKET_HASH = "sha256:" + "2" * 64
PROMPT_HASH = "sha256:" + "3" * 64
PROMPT_VERSION = "grok-knowledge-auditor-v4"
PROVIDER = "xai"
MODEL = "grok-4.5"


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _consensus_id(payload: dict[str, object]) -> str:
    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "consensus_id"}
    }
    raw = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"knowledge-review-consensus:{_sha256(raw)}"


def _evidence(
    *,
    quote: str = "Caller must release the resource.",
    line: int = 10,
) -> ModelReviewEvidence:
    return ModelReviewEvidence(
        source_id="synthetic",
        relative_path="rules/resource.md",
        start_line=line,
        end_line=line,
        exact_quote=quote,
    )


CHANGE_ADD = AnnotationChange(
    annotation_kind="tag",
    current_value=None,
    proposed_action="add",
    proposed_value="has_timer",
    reason_code="source_explicit_metadata",
)
CHANGE_REMOVE = AnnotationChange(
    annotation_kind="domain",
    current_value="background-task",
    proposed_action="remove",
    proposed_value=None,
    reason_code="domain_not_applicable",
)
CHANGE_REMOVE_OTHER_REASON = AnnotationChange(
    annotation_kind="domain",
    current_value="background-task",
    proposed_action="remove",
    proposed_value=None,
    reason_code="keyword_only_false_positive",
)


def _clause(
    rule_id: str,
    decision: ModelReviewDecision,
    *,
    changes: tuple[AnnotationChange, ...] = (),
    issue_codes: tuple[str, ...] | None = None,
) -> ClauseModelReview:
    if decision == "accept":
        issues: tuple[str, ...] = ()
        evidence: tuple[ModelReviewEvidence, ...] = ()
    else:
        issues = issue_codes or ("annotation_error",)
        evidence = (_evidence(),)
    return ClauseModelReview(
        rule_id=rule_id,
        decision=decision,
        issue_codes=issues,
        evidence=evidence,
        annotation_changes=changes,
        rationale=f"Synthetic {decision} review for {rule_id}.",
    )


def _missing(
    *,
    rule_id: str = "MISSING/R-1",
    text: str = "Caller must close the handle.",
    rationale: str = "The packet omitted a normative requirement.",
) -> MissingClauseReview:
    return MissingClauseReview(
        proposed_rule_id=rule_id,
        rule_type="constraint",
        text=text,
        evidence=(_evidence(quote=text, line=20),),
        rationale=rationale,
    )


def _duplicate(
    *,
    quote: str = "The two Clauses state the same requirement.",
) -> DuplicateClauseGroup:
    return DuplicateClauseGroup(
        rule_ids=("RULE/R-1", "RULE/R-2"),
        evidence=(_evidence(quote=quote, line=30),),
    )


def _conflict(
    *,
    conflict_id: str = "CONFLICT-1",
    rationale: str = "The Clauses require incompatible behavior.",
) -> KnowledgeConflictReview:
    return KnowledgeConflictReview(
        conflict_id=conflict_id,
        rule_ids=("RULE/R-1", "RULE/R-2"),
        evidence=(_evidence(quote="The requirements conflict.", line=40),),
        rationale=rationale,
    )


def _review(
    decisions: tuple[ModelReviewDecision, ...] = ("accept", "accept"),
    *,
    rule_ids: tuple[str, ...] | None = None,
    changes: dict[str, tuple[AnnotationChange, ...]] | None = None,
    issue_codes: dict[str, tuple[str, ...]] | None = None,
    missing: tuple[MissingClauseReview, ...] = (),
    duplicates: tuple[DuplicateClauseGroup, ...] = (),
    conflicts: tuple[KnowledgeConflictReview, ...] = (),
    packet_id: str = PACKET_ID,
    model: str = MODEL,
) -> KnowledgeModelReview:
    ids = rule_ids or tuple(f"RULE/R-{index}" for index in range(1, len(decisions) + 1))
    if len(ids) != len(decisions):
        raise AssertionError("test fixture rule IDs must match decisions")
    change_map = changes or {}
    issue_map = issue_codes or {}
    clause_reviews = tuple(
        _clause(
            rule_id,
            decision,
            changes=change_map.get(rule_id, ()),
            issue_codes=issue_map.get(rule_id),
        )
        for rule_id, decision in zip(ids, decisions, strict=True)
    )
    has_global_findings = bool(missing or duplicates or conflicts)
    if all(item == "accept" for item in decisions) and not has_global_findings:
        packet_decision = "accept"
    elif "reject" in decisions:
        packet_decision = "reject"
    else:
        packet_decision = "uncertain"
    return KnowledgeModelReview(
        packet_id=packet_id,
        reviewer=ModelReviewer(
            kind="model",
            provider="xai",
            model=model,
            prompt_version=PROMPT_VERSION,
        ),
        packet_decision=packet_decision,
        clause_reviews=clause_reviews,
        missing_clauses=missing,
        duplicate_groups=duplicates,
        conflicts=conflicts,
        summary=ModelReviewSummary(
            accepted=decisions.count("accept"),
            rejected=decisions.count("reject"),
            uncertain=decisions.count("uncertain"),
            with_corrections=decisions.count("accept_with_corrections"),
        ),
    )


def _round(
    round_name: str,
    review: KnowledgeModelReview,
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    packet_hash: str = PACKET_HASH,
    prompt_hash: str = PROMPT_HASH,
    model: str = MODEL,
    receipt_hash: str | None = None,
) -> ConsensusReviewRound:
    review_raw = json.dumps(
        review.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return ConsensusReviewRound(
        round_name=round_name,
        packet_id=review.packet_id,
        packet_hash=packet_hash,
        prompt_version=PROMPT_VERSION,
        prompt_hash=prompt_hash,
        provider=PROVIDER,
        model=model,
        request_id=request_id or f"request-{round_name}",
        session_id=session_id or f"session-{round_name}",
        review_hash=_sha256(review_raw),
        receipt_hash=receipt_hash or _sha256(f"receipt-{round_name}"),
        review=review,
    )


def _consensus(
    first: KnowledgeModelReview,
    second: KnowledgeModelReview,
) -> KnowledgeReviewConsensus:
    return build_knowledge_review_consensus(
        rounds=(
            _round("round-1", first),
            _round("round-2", second),
        )
    )


def test_identical_independent_accepts_are_release_ready_and_deterministic() -> None:
    review = _review()
    round_one = _round("round-1", review)
    round_two = _round("round-2", review)

    forward = build_knowledge_review_consensus(rounds=(round_one, round_two))
    reverse = build_knowledge_review_consensus(rounds=(round_two, round_one))

    assert forward == reverse
    assert forward.consensus_id == forward.expected_consensus_id()
    assert [item.status for item in forward.clauses] == ["accepted", "accepted"]
    assert forward.release_ready is True
    assert forward.release_blockers == ()
    assert round_one.review_hash == round_two.review_hash
    assert load_knowledge_review_consensus(forward.model_dump_json()) == forward
    with pytest.raises(ValidationError):
        forward.release_ready = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("first_decision", "second_decision", "expected"),
    [
        ("accept", "accept", "accepted"),
        ("reject", "reject", "rejected"),
        ("accept", "reject", "unresolved"),
        ("reject", "accept", "unresolved"),
        ("accept", "uncertain", "unresolved"),
        ("uncertain", "uncertain", "unresolved"),
        ("reject", "uncertain", "unresolved"),
        ("accept_with_corrections", "accept", "unresolved"),
        ("accept_with_corrections", "reject", "unresolved"),
        ("accept_with_corrections", "uncertain", "unresolved"),
    ],
)
def test_clause_decision_matrix(
    first_decision: ModelReviewDecision,
    second_decision: ModelReviewDecision,
    expected: str,
) -> None:
    first_changes = (
        {"RULE/R-1": (CHANGE_ADD,)}
        if first_decision == "accept_with_corrections"
        else {}
    )
    second_changes = (
        {"RULE/R-1": (CHANGE_ADD,)}
        if second_decision == "accept_with_corrections"
        else {}
    )
    result = _consensus(
        _review((first_decision,), changes=first_changes),
        _review((second_decision,), changes=second_changes),
    )

    assert result.clauses[0].status == expected
    assert result.release_ready is (expected == "accepted")


def test_identical_canonical_corrections_form_non_applying_draft() -> None:
    first = _review(
        ("accept_with_corrections",),
        changes={"RULE/R-1": (CHANGE_ADD, CHANGE_REMOVE)},
    )
    second = _review(
        ("accept_with_corrections",),
        changes={"RULE/R-1": (CHANGE_REMOVE, CHANGE_ADD)},
    )

    result = _consensus(first, second)
    clause = result.clauses[0]

    assert clause.status == "correction_draft"
    assert clause.correction_draft is not None
    assert clause.correction_draft.auto_apply is False
    assert clause.correction_draft.annotation_changes == tuple(
        sorted((CHANGE_ADD, CHANGE_REMOVE), key=lambda item: json.dumps(
            item.model_dump(mode="json"),
            separators=(",", ":"),
            sort_keys=True,
        ))
    )
    assert result.release_ready is False
    assert [item.blocker_kind for item in result.release_blockers] == [
        "clause_correction_draft"
    ]


def test_different_canonical_corrections_remain_unresolved() -> None:
    first = _review(
        ("accept_with_corrections",),
        changes={"RULE/R-1": (CHANGE_REMOVE,)},
    )
    second = _review(
        ("accept_with_corrections",),
        changes={"RULE/R-1": (CHANGE_REMOVE_OTHER_REASON,)},
    )

    result = _consensus(first, second)

    assert result.clauses[0].status == "unresolved"
    assert result.clauses[0].correction_draft is None
    assert result.release_ready is False


def test_matching_global_findings_are_confirmed_but_block_release() -> None:
    review = _review(
        missing=(_missing(),),
        duplicates=(_duplicate(),),
        conflicts=(_conflict(),),
    )

    result = _consensus(review, review)

    assert [item.status for item in result.missing_clauses] == [
        "confirmed_proposal"
    ]
    assert [item.status for item in result.duplicate_groups] == [
        "confirmed_proposal"
    ]
    assert [item.status for item in result.conflicts] == ["confirmed_proposal"]
    assert result.release_ready is False
    assert {item.blocker_kind for item in result.release_blockers} == {
        "missing_clause_proposal",
        "duplicate_clause_proposal",
        "conflict_proposal",
    }


def test_one_sided_or_changed_global_findings_are_quarantined() -> None:
    first = _review(
        missing=(_missing(),),
        duplicates=(_duplicate(),),
        conflicts=(_conflict(),),
    )
    second = _review(
        missing=(_missing(rationale="A materially different explanation."),),
        duplicates=(_duplicate(quote="Different duplicate evidence."),),
        conflicts=(_conflict(rationale="A different conflict explanation."),),
    )

    result = _consensus(first, second)

    assert [item.status for item in result.missing_clauses] == ["quarantine"]
    assert [item.status for item in result.duplicate_groups] == ["quarantine"]
    assert [item.status for item in result.conflicts] == ["quarantine"]
    assert all(len(item.proposals) == 2 for item in result.missing_clauses)
    assert result.release_ready is False

    one_sided = _consensus(
        _review(missing=(_missing(),)),
        _review(),
    )
    assert one_sided.missing_clauses[0].status == "quarantine"
    assert len(one_sided.missing_clauses[0].proposals) == 1
    assert one_sided.release_ready is False


@pytest.mark.parametrize(
    "replayed_field",
    ["round_name", "request_id", "session_id", "receipt_hash"],
)
def test_round_replay_is_rejected(replayed_field: str) -> None:
    review = _review()
    first = _round("round-1", review)
    overrides: dict[str, str] = {}
    if replayed_field == "round_name":
        second_name = "round-1"
    else:
        second_name = "round-2"
        overrides[replayed_field] = getattr(first, replayed_field)
    second = _round(second_name, review, **overrides)

    with pytest.raises(ValueError, match="different|distinct"):
        build_knowledge_review_consensus(rounds=(first, second))


@pytest.mark.parametrize("identity_field", ["packet_hash", "prompt_hash", "model"])
def test_round_identity_drift_is_rejected(identity_field: str) -> None:
    first_review = _review()
    second_review = _review(model="grok-4.6") if identity_field == "model" else _review()
    first = _round("round-1", first_review)
    overrides: dict[str, str] = {}
    if identity_field == "packet_hash":
        overrides[identity_field] = "sha256:" + "a" * 64
    elif identity_field == "prompt_hash":
        overrides[identity_field] = "sha256:" + "b" * 64
    else:
        overrides[identity_field] = "grok-4.6"
    second = _round("round-2", second_review, **overrides)

    with pytest.raises(ValueError, match="do not share review identity"):
        build_knowledge_review_consensus(rounds=(first, second))


def test_round_metadata_must_match_embedded_review() -> None:
    with pytest.raises(ValidationError, match="metadata does not match"):
        _round("round-1", _review(), model="grok-4.6")


def test_clause_coverage_must_match_exactly() -> None:
    first = _round("round-1", _review())
    second = _round(
        "round-2",
        _review(("accept",), rule_ids=("RULE/R-1",)),
    )

    with pytest.raises(ValueError, match="same Clauses"):
        build_knowledge_review_consensus(rounds=(first, second))


def test_duplicate_global_identity_is_rejected_before_consensus() -> None:
    forged = _review(missing=(_missing(), _missing()))

    with pytest.raises(ValidationError, match="duplicate missing Clause"):
        _round("round-1", forged)


def test_consensus_output_rejects_tampered_id_order_and_aggregates() -> None:
    result = _consensus(
        _review(("reject", "accept")),
        _review(("reject", "accept")),
    )
    payload = result.model_dump(mode="json")

    wrong_id = dict(payload)
    wrong_id["consensus_id"] = "knowledge-review-consensus:sha256:" + "f" * 64
    with pytest.raises(ValidationError, match="consensus_id"):
        KnowledgeReviewConsensus.model_validate(wrong_id)

    wrong_release = dict(payload)
    wrong_release["release_ready"] = True
    with pytest.raises(ValidationError, match="release_ready"):
        KnowledgeReviewConsensus.model_validate(wrong_release)

    wrong_order = dict(payload)
    wrong_order["clauses"] = list(reversed(payload["clauses"]))
    with pytest.raises(ValidationError, match="sorted"):
        KnowledgeReviewConsensus.model_validate(wrong_order)

    extra = dict(payload)
    extra["unexpected"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        KnowledgeReviewConsensus.model_validate(extra)


def test_output_model_rejects_replayed_receipt_and_round_identity_after_rehash() -> None:
    result = _consensus(_review(), _review())

    replayed = result.model_dump(mode="json")
    rounds = replayed["rounds"]
    assert isinstance(rounds, list)
    assert isinstance(rounds[0], dict)
    assert isinstance(rounds[1], dict)
    rounds[1]["receipt_hash"] = rounds[0]["receipt_hash"]
    replayed["consensus_id"] = _consensus_id(replayed)
    with pytest.raises(ValidationError, match="receipt hashes must be distinct"):
        KnowledgeReviewConsensus.model_validate(replayed)

    wrong_identity = result.model_dump(mode="json")
    identity_rounds = wrong_identity["rounds"]
    assert isinstance(identity_rounds, list)
    assert isinstance(identity_rounds[1], dict)
    identity_rounds[1]["packet_hash"] = "sha256:" + "e" * 64
    wrong_identity["consensus_id"] = _consensus_id(wrong_identity)
    with pytest.raises(ValidationError, match="shared identity"):
        KnowledgeReviewConsensus.model_validate(wrong_identity)


def test_consensus_loader_rejects_duplicate_keys_unknown_fields_and_non_utf8() -> None:
    result = _consensus(_review(), _review())
    raw = result.model_dump_json()
    duplicate = raw.replace(
        '"schema_version":',
        '"schema_version":"knowledge-review-consensus-v1","schema_version":',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_knowledge_review_consensus(duplicate)

    payload = result.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_knowledge_review_consensus(json.dumps(payload))

    with pytest.raises(ValueError, match="UTF-8"):
        load_knowledge_review_consensus(b"\xff")
