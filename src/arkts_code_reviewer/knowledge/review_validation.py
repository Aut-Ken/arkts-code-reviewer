from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import PurePosixPath

from pydantic import ValidationError

from arkts_code_reviewer.knowledge.models import (
    AnnotationChange,
    KnowledgeAnnotation,
    KnowledgeModelReview,
    ModelReviewEvidence,
)
from arkts_code_reviewer.knowledge.review_packets import KnowledgeReviewPacket

_REGISTERED_CHANGE_KINDS = {
    "api",
    "component",
    "decorator",
    "dimension",
    "domain",
    "tag",
}


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_review(raw: bytes | str) -> KnowledgeModelReview:
    if isinstance(raw, str):
        encoded = raw.encode("utf-8")
    elif isinstance(raw, bytes):
        encoded = raw
    else:
        raise TypeError("Knowledge model review must be UTF-8 JSON bytes or text")
    try:
        json.loads(encoded.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError as exc:
        raise ValueError("Knowledge model review must be UTF-8") from exc
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid Knowledge model review JSON: {exc}") from exc
    try:
        return KnowledgeModelReview.model_validate_json(encoded, strict=True)
    except ValidationError as exc:
        raise ValueError(f"invalid Knowledge model review: {exc}") from exc


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or "." in path.parts
        or ".." in path.parts
        or "\\" in value
    ):
        raise ValueError("Knowledge review evidence path must stay below the source root")
    return str(path)


def _evidence_key(evidence: ModelReviewEvidence) -> tuple[object, ...]:
    return (
        evidence.source_id,
        evidence.relative_path,
        evidence.start_line,
        evidence.end_line,
        evidence.exact_quote,
    )


def _validate_evidence_order(
    evidence: tuple[ModelReviewEvidence, ...],
    *,
    context: str,
) -> None:
    keys = [_evidence_key(item) for item in evidence]
    if keys != sorted(set(keys)):
        raise ValueError(f"{context} evidence must be sorted and unique")


def _validate_evidence(
    evidence: ModelReviewEvidence,
    *,
    packet: KnowledgeReviewPacket,
    related_rule_ids: frozenset[str] | None,
) -> None:
    relative_path = _safe_relative_path(evidence.relative_path)
    candidate_excerpts = [
        excerpt
        for excerpt in packet.source_excerpts
        if excerpt.source_id == evidence.source_id
        and excerpt.relative_path == relative_path
        and excerpt.start_line <= evidence.start_line
        and excerpt.end_line >= evidence.end_line
        and (
            related_rule_ids is None
            or bool(related_rule_ids.intersection(excerpt.rule_ids))
        )
    ]
    if not candidate_excerpts:
        raise ValueError("Knowledge review evidence is not covered by a related packet excerpt")
    for excerpt in candidate_excerpts:
        lines = excerpt.exact_text.split("\n")
        expected_line_count = excerpt.end_line - excerpt.start_line + 1
        if len(lines) != expected_line_count:
            raise ValueError("Knowledge review packet excerpt line count does not match its span")
        start = evidence.start_line - excerpt.start_line
        end = evidence.end_line - excerpt.start_line + 1
        expected_quote = "\n".join(lines[start:end])
        if evidence.exact_quote == expected_quote:
            return
    raise ValueError("Knowledge review evidence exact_quote does not match packet source text")


def _validate_evidence_list(
    evidence: tuple[ModelReviewEvidence, ...],
    *,
    packet: KnowledgeReviewPacket,
    context: str,
    related_rule_ids: frozenset[str] | None,
) -> None:
    _validate_evidence_order(evidence, context=context)
    for item in evidence:
        _validate_evidence(
            item,
            packet=packet,
            related_rule_ids=related_rule_ids,
        )


def _validate_packet_excerpt_lines(packet: KnowledgeReviewPacket) -> None:
    for excerpt in packet.source_excerpts:
        expected_line_count = excerpt.end_line - excerpt.start_line + 1
        if len(excerpt.exact_text.split("\n")) != expected_line_count:
            raise ValueError(
                "Knowledge review packet excerpt line count does not match its span"
            )


def _annotation_values(
    annotation: KnowledgeAnnotation,
    kind: str,
) -> frozenset[str]:
    values: Iterable[str]
    if kind == "api":
        values = annotation.apis
    elif kind == "component":
        values = annotation.components
    elif kind == "decorator":
        values = annotation.decorators
    elif kind == "dimension":
        values = annotation.dimension_ids
    elif kind == "domain":
        values = annotation.domains
    elif kind == "tag":
        values = annotation.tags
    else:
        raise ValueError(
            "Knowledge review correction kind has no identity-bound registry in the packet"
        )
    return frozenset(values)


def _registered_values(packet: KnowledgeReviewPacket, kind: str) -> frozenset[str]:
    if kind == "api":
        return frozenset(item.canonical_name for item in packet.api_catalog_slice)
    if kind == "component":
        return frozenset(
            value
            for item in packet.tag_registry
            for value in item.triggers.any_component
        )
    if kind == "decorator":
        return frozenset(
            value
            for item in packet.tag_registry
            for value in item.triggers.any_decorator
        )
    if kind == "dimension":
        return frozenset(item.id for item in packet.dimension_registry)
    if kind == "domain":
        return frozenset(
            (*packet.source_domain_ids, *(item.domain_id for item in packet.domain_registry))
        )
    if kind == "tag":
        return frozenset(item.id for item in packet.tag_registry)
    raise ValueError("Knowledge review correction kind is not registered")


def _change_key(change: AnnotationChange) -> tuple[object, ...]:
    return (
        change.annotation_kind,
        change.proposed_action,
        change.current_value is not None,
        change.current_value or "",
        change.proposed_value is not None,
        change.proposed_value or "",
        change.reason_code,
    )


def _validate_annotation_changes(
    changes: tuple[AnnotationChange, ...],
    *,
    annotation: KnowledgeAnnotation,
    packet: KnowledgeReviewPacket,
) -> None:
    keys = [_change_key(change) for change in changes]
    if keys != sorted(set(keys)):
        raise ValueError("Knowledge review annotation changes must be sorted and unique")

    current_targets: set[tuple[str, str]] = set()
    proposed_targets: set[tuple[str, str]] = set()
    for change in changes:
        kind = change.annotation_kind
        if kind not in _REGISTERED_CHANGE_KINDS:
            raise ValueError(
                "Knowledge review correction kind is not identity-bound by the packet"
            )
        current_values = _annotation_values(annotation, kind)
        registered_values = _registered_values(packet, kind)
        if change.current_value is not None:
            target = (kind, change.current_value)
            if target in current_targets:
                raise ValueError("Knowledge review changes the same current annotation twice")
            current_targets.add(target)
            if change.current_value not in current_values:
                raise ValueError(
                    "Knowledge review annotation current_value is absent from the packet snapshot"
                )
        if change.proposed_value is not None:
            target = (kind, change.proposed_value)
            if target in proposed_targets:
                raise ValueError("Knowledge review proposes the same annotation value twice")
            proposed_targets.add(target)
            if change.proposed_value not in registered_values:
                raise ValueError(
                    "Knowledge review proposed annotation value is not registered in the packet"
                )
            if change.proposed_value in current_values:
                raise ValueError(
                    "Knowledge review proposed annotation value already exists "
                    "in the packet snapshot"
                )
        if (
            change.current_value is not None
            and change.proposed_value == change.current_value
        ):
            raise ValueError("Knowledge review annotation replacement must change the value")
    if current_targets.intersection(proposed_targets):
        raise ValueError("Knowledge review annotation changes contain conflicting operations")


def _validate_graph(
    review: KnowledgeModelReview,
    *,
    packet: KnowledgeReviewPacket,
) -> None:
    expected_rule_ids = tuple(clause.rule_id for clause in packet.clauses)
    actual_rule_ids = tuple(item.rule_id for item in review.clause_reviews)
    if actual_rule_ids != expected_rule_ids:
        missing = sorted(set(expected_rule_ids) - set(actual_rule_ids))
        extra = sorted(set(actual_rule_ids) - set(expected_rule_ids))
        raise ValueError(
            "Knowledge review Clause coverage does not match packet: "
            f"missing={missing}, extra={extra}"
        )
    packet_rule_ids = frozenset(expected_rule_ids)
    clauses_by_rule = {clause.rule_id: clause for clause in packet.clauses}

    for clause_review in review.clause_reviews:
        _validate_evidence_list(
            clause_review.evidence,
            packet=packet,
            context=f"Clause review {clause_review.rule_id}",
            related_rule_ids=frozenset((clause_review.rule_id,)),
        )
        if clause_review.decision == "accept_with_corrections":
            if not clause_review.annotation_changes:
                raise ValueError("accept_with_corrections requires annotation changes")
        elif clause_review.annotation_changes:
            raise ValueError(
                "only accept_with_corrections may carry annotation changes"
            )
        _validate_annotation_changes(
            clause_review.annotation_changes,
            annotation=clauses_by_rule[clause_review.rule_id].annotation,
            packet=packet,
        )

    missing_ids = [item.proposed_rule_id for item in review.missing_clauses]
    if missing_ids != sorted(set(missing_ids)):
        raise ValueError("Knowledge review missing Clauses must be sorted and unique")
    if packet_rule_ids.intersection(missing_ids):
        raise ValueError("Knowledge review missing Clause collides with a packet rule_id")
    for missing_clause in review.missing_clauses:
        _validate_evidence_list(
            missing_clause.evidence,
            packet=packet,
            context=f"missing Clause {missing_clause.proposed_rule_id}",
            related_rule_ids=None,
        )

    duplicate_keys = [item.rule_ids for item in review.duplicate_groups]
    if duplicate_keys != sorted(set(duplicate_keys)):
        raise ValueError("Knowledge review duplicate groups must be sorted and unique")
    duplicate_members: set[str] = set()
    for duplicate_group in review.duplicate_groups:
        if not duplicate_group.evidence:
            raise ValueError("Knowledge review duplicate group requires evidence")
        if not set(duplicate_group.rule_ids).issubset(packet_rule_ids):
            raise ValueError("Knowledge review duplicate group contains an unknown rule_id")
        duplicate_members.update(duplicate_group.rule_ids)
        _validate_evidence_list(
            duplicate_group.evidence,
            packet=packet,
            context=f"duplicate group {duplicate_group.rule_ids}",
            related_rule_ids=frozenset(duplicate_group.rule_ids),
        )

    conflict_keys = [item.conflict_id for item in review.conflicts]
    if conflict_keys != sorted(set(conflict_keys)):
        raise ValueError("Knowledge review conflicts must be sorted and unique")
    conflict_members: set[str] = set()
    for conflict in review.conflicts:
        if not conflict.evidence:
            raise ValueError("Knowledge review conflict requires evidence")
        if not set(conflict.rule_ids).issubset(packet_rule_ids):
            raise ValueError("Knowledge review conflict contains an unknown rule_id")
        conflict_members.update(conflict.rule_ids)
        _validate_evidence_list(
            conflict.evidence,
            packet=packet,
            context=f"conflict {conflict.conflict_id}",
            related_rule_ids=frozenset(conflict.rule_ids),
        )
    if duplicate_members.intersection(conflict_members):
        raise ValueError("Knowledge review cannot mark the same Clause duplicate and conflicting")

    has_global_findings = bool(
        review.missing_clauses or review.duplicate_groups or review.conflicts
    )
    decisions = tuple(item.decision for item in review.clause_reviews)
    if all(item == "accept" for item in decisions) and not has_global_findings:
        expected_packet_decision = "accept"
    elif "reject" in decisions:
        expected_packet_decision = "reject"
    else:
        expected_packet_decision = "uncertain"
    if review.packet_decision != expected_packet_decision:
        raise ValueError(
            "Knowledge review packet_decision does not match the frozen decision policy"
        )


def load_and_validate_knowledge_model_review(
    raw: bytes | str,
    *,
    packet: KnowledgeReviewPacket,
) -> KnowledgeModelReview:
    """Load a Grok review and bind every claim to one immutable review packet."""
    if packet.packet_id != packet.expected_packet_id():
        raise ValueError("Knowledge review packet identity drift")
    _validate_packet_excerpt_lines(packet)
    if packet.distribution != "external_model":
        raise ValueError("Grok output requires an external-model review packet")
    review = _load_review(raw)
    if review.packet_id != packet.packet_id:
        raise ValueError("Knowledge model review packet_id does not match the packet")
    if review.reviewer.provider != packet.model_provider:
        raise ValueError("Knowledge model review provider does not match the packet")
    if review.reviewer.model != packet.model_name:
        raise ValueError("Knowledge model review model does not match the packet")
    if review.reviewer.prompt_version != packet.prompt_version:
        raise ValueError("Knowledge model review prompt version does not match the packet")
    _validate_graph(review, packet=packet)
    return review


__all__ = ["load_and_validate_knowledge_model_review"]
