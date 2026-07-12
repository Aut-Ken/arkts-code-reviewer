from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

from arkts_code_reviewer.code_analysis.change_set import CodeSourceSnapshot
from arkts_code_reviewer.code_analysis.file_analysis_models import ExactRange
from arkts_code_reviewer.code_analysis.models import ReviewUnit
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    normalize_review_path,
)
from arkts_code_reviewer.code_analysis.text_utils import extract_lines

CONTEXT_PLAN_SCHEMA_VERSION = "context-plan-v1"
CONTEXT_PLANNER_VERSION = "context-planner-v1"
TOKEN_ESTIMATOR_VERSION = "arkts-code-token-v1"

RelationType = Literal[
    "lifecycle_pair",
    "state_access",
    "direct_call",
    "direct_caller",
    "change_correspondence",
    "same_host",
    "same_file",
]
RelationStrength = Literal["strong", "weak"]
RelationQuality = Literal["exact", "degraded"]
ContextNecessity = Literal["required", "helpful", "distractor"]
ContextSelectionReason = Literal["required_context", "helpful_context"]
CandidateOmissionReason = Literal[
    "distractor_rejected",
    "budget_exceeded",
    "context_blocked",
    "relation_degraded",
]
ContextDiagnosticCode = Literal[
    "primary_exceeds_budget",
    "context_insufficient",
    "relation_degraded",
]

_RELATION_TYPES = {
    "lifecycle_pair",
    "state_access",
    "direct_call",
    "direct_caller",
    "change_correspondence",
    "same_host",
    "same_file",
}
_RELATION_STRENGTHS = {"strong", "weak"}
_RELATION_QUALITIES = {"exact", "degraded"}
_NECESSITIES = {"required", "helpful", "distractor"}
_SELECTION_REASONS = {"required_context", "helpful_context"}
_OMISSION_REASONS = {
    "distractor_rejected",
    "budget_exceeded",
    "context_blocked",
    "relation_degraded",
}
_DIAGNOSTIC_CODES = {
    "primary_exceeds_budget",
    "context_insufficient",
    "relation_degraded",
}
_NON_EVIDENCE_RELATIONS = {"same_host", "same_file"}

# This scanner includes trivia as well as syntax. Long strings, comments, identifiers,
# and whitespace runs are charged by byte length instead of being treated as one cheap
# lexical token. It is deliberately model-independent and versioned by the contract.
_CODE_CHUNK_RE = re.compile(
    r"""
    /\*.*?\*/
    |//[^\n]*(?:\n|$)
    |`(?:\\.|[^`\\])*`
    |"(?:\\.|[^"\\])*"
    |'(?:\\.|[^'\\])*'
    |[A-Za-z_$][A-Za-z0-9_$]*
    |\d+(?:\.\d+)?(?:[eE][+-]?\d+)?
    |\s+
    |(?:===|!==|>>>|<<=|>>=|\?\?|\?\.|=>|==|!=|<=|>=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|%=|<<|>>|\*\*)
    |.
    """,
    re.DOTALL | re.VERBOSE,
)


def _stable_id(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _require_text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{context} must not contain control characters")
    return value


def _require_non_negative(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def _require_positive(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{context} must be an integer >= 1")
    return value


def _require_sorted_unique_strings(values: tuple[str, ...], context: str) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value for value in values
    ):
        raise ValueError(f"{context} must contain non-empty strings")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")


def _span_payload(span: ExactRange) -> dict[str, int]:
    return {
        "start_line": span.start_line,
        "end_line": span.end_line,
        "start_offset_utf16": span.start_offset_utf16,
        "end_offset_utf16": span.end_offset_utf16,
    }


def source_span_ref_id(source_ref_id: str, span: ExactRange) -> str:
    """Return the stable endpoint identity used by supporting relation edges."""

    _require_text(source_ref_id, "source_span_ref_id.source_ref_id")
    if not isinstance(span, ExactRange):
        raise ValueError("source_span_ref_id.span must use ExactRange")
    return _stable_id(
        "source-span",
        {"source_ref_id": source_ref_id, "span": _span_payload(span)},
    )


def estimate_code_tokens(source: str) -> int:
    """Estimate deterministic, model-independent ArkTS code tokens.

    Every lexical or trivia chunk costs at least one token and at least one token
    per four UTF-8 bytes. This is an executed code-context budget, not a prompt or
    model-output token count.
    """

    if not isinstance(source, str):
        raise ValueError("estimate_code_tokens.source must be a string")
    return sum(
        max(1, math.ceil(len(match.group(0).encode("utf-8")) / 4))
        for match in _CODE_CHUNK_RE.finditer(source)
    )


def _utf16_boundaries(source: str) -> dict[int, int]:
    boundaries = {0: 0}
    offset = 0
    for index, character in enumerate(source, start=1):
        offset += 2 if ord(character) > 0xFFFF else 1
        boundaries[offset] = index
    return boundaries


def _slice_exact_range(source: str, span: ExactRange, context: str) -> str:
    if not isinstance(span, ExactRange):
        raise ValueError(f"{context} must use ExactRange")
    if span.end_offset_utf16 <= span.start_offset_utf16:
        raise ValueError(f"{context} must be non-empty")
    boundaries = _utf16_boundaries(source)
    try:
        start_index = boundaries[span.start_offset_utf16]
        end_index = boundaries[span.end_offset_utf16]
    except KeyError as exc:
        raise ValueError(f"{context} offsets must be UTF-16 source boundaries") from exc
    start_line = source[:start_index].count("\n") + 1
    if start_line != span.start_line:
        raise ValueError(f"{context} start line and UTF-16 offset disagree")
    mapped_end_line = source[:end_index].count("\n") + 1
    ends_after_declared_newline = (
        end_index > 0
        and source[end_index - 1] == "\n"
        and mapped_end_line == span.end_line + 1
    )
    if mapped_end_line != span.end_line and not ends_after_declared_newline:
        raise ValueError(f"{context} end line and UTF-16 offset disagree")
    return source[start_index:end_index]


def _json_ready(value: object) -> Any:
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return cast(object, to_dict())
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


def context_plan_id(
    *,
    planner_version: str,
    token_estimator_version: str,
    change_set_id: str,
    blocking_change_ids: tuple[str, ...],
    primary_question_bindings: tuple[object, ...],
    candidates: tuple[object, ...],
    supporting_segments: tuple[object, ...],
    relation_edges: tuple[object, ...],
    change_groups: tuple[object, ...],
    bundles: tuple[object, ...],
    omitted_candidates: tuple[object, ...],
    budget_summary: object,
    diagnostics: tuple[object, ...],
) -> str:
    """Compute the public plan ID from either models or JSON-ready projections."""

    omitted_ready = [_json_ready(item) for item in omitted_candidates]
    payload = {
        "planner_version": planner_version,
        "token_estimator_version": token_estimator_version,
        "change_set_id": change_set_id,
        "blocking_change_ids": list(blocking_change_ids),
        "primary_question_bindings": [
            _json_ready(item) for item in primary_question_bindings
        ],
        "candidates": [_json_ready(item) for item in candidates],
        "supporting_segments": [
            _json_ready(item) for item in supporting_segments
        ],
        "relation_edges": [_json_ready(item) for item in relation_edges],
        "change_groups": [_json_ready(item) for item in change_groups],
        "bundles": [_json_ready(item) for item in bundles],
        "omitted_candidate_ids": [
            item["candidate_id"]
            for item in omitted_ready
            if isinstance(item, dict)
        ],
        "omitted_candidates": omitted_ready,
        "budget_summary": _json_ready(budget_summary),
        "diagnostics": [_json_ready(item) for item in diagnostics],
    }
    if len(payload["omitted_candidate_ids"]) != len(omitted_ready):
        raise ValueError("omitted_candidates must expose candidate_id")
    return _stable_id("context-plan", payload)


@dataclass(frozen=True)
class QuestionBinding:
    primary_unit_id: str
    review_question_id: str

    def __post_init__(self) -> None:
        _require_text(self.primary_unit_id, "QuestionBinding.primary_unit_id")
        _require_text(self.review_question_id, "QuestionBinding.review_question_id")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ContextDiagnostic:
    code: ContextDiagnosticCode
    subject_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.code not in _DIAGNOSTIC_CODES:
            raise ValueError(f"unsupported Context diagnostic code: {self.code}")
        _require_sorted_unique_strings(
            self.subject_ids,
            "ContextDiagnostic.subject_ids",
        )
        if not self.subject_ids:
            raise ValueError("ContextDiagnostic.subject_ids must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "subject_ids": list(self.subject_ids)}


def _validate_diagnostics(
    diagnostics: tuple[ContextDiagnostic, ...],
    context: str,
) -> None:
    if not isinstance(diagnostics, tuple) or any(
        not isinstance(item, ContextDiagnostic) for item in diagnostics
    ):
        raise ValueError(f"{context} must contain ContextDiagnostic values")
    keys = [(item.code, item.subject_ids) for item in diagnostics]
    if keys != sorted(set(keys)):
        raise ValueError(f"{context} must be sorted and unique")


@dataclass(frozen=True)
class RelationEdge:
    edge_id: str
    source_ref: str
    target_ref: str
    relation_type: RelationType
    strength: RelationStrength
    quality: RelationQuality
    evidence_refs: tuple[str, ...]
    provenance_ref: str

    @classmethod
    def create(
        cls,
        *,
        source_ref: str,
        target_ref: str,
        relation_type: RelationType,
        strength: RelationStrength,
        quality: RelationQuality,
        evidence_refs: Sequence[str],
        provenance_ref: str,
    ) -> RelationEdge:
        normalized_evidence = tuple(sorted(set(evidence_refs)))
        return cls(
            edge_id=cls.expected_id(
                source_ref=source_ref,
                target_ref=target_ref,
                relation_type=relation_type,
                strength=strength,
                quality=quality,
                evidence_refs=normalized_evidence,
                provenance_ref=provenance_ref,
            ),
            source_ref=source_ref,
            target_ref=target_ref,
            relation_type=relation_type,
            strength=strength,
            quality=quality,
            evidence_refs=normalized_evidence,
            provenance_ref=provenance_ref,
        )

    @staticmethod
    def expected_id(
        *,
        source_ref: str,
        target_ref: str,
        relation_type: RelationType,
        strength: RelationStrength,
        quality: RelationQuality,
        evidence_refs: tuple[str, ...],
        provenance_ref: str,
    ) -> str:
        return _stable_id(
            "relation-edge",
            {
                "source_ref": source_ref,
                "target_ref": target_ref,
                "relation_type": relation_type,
                "strength": strength,
                "quality": quality,
                "evidence_refs": list(evidence_refs),
                "provenance_ref": provenance_ref,
            },
        )

    def __post_init__(self) -> None:
        _require_text(self.source_ref, "RelationEdge.source_ref")
        _require_text(self.target_ref, "RelationEdge.target_ref")
        if self.source_ref == self.target_ref:
            raise ValueError("RelationEdge endpoints must differ")
        if self.relation_type not in _RELATION_TYPES:
            raise ValueError(f"unsupported relation type: {self.relation_type}")
        if self.strength not in _RELATION_STRENGTHS:
            raise ValueError(f"unsupported relation strength: {self.strength}")
        if self.quality not in _RELATION_QUALITIES:
            raise ValueError(f"unsupported relation quality: {self.quality}")
        _require_sorted_unique_strings(self.evidence_refs, "RelationEdge.evidence_refs")
        if not self.evidence_refs:
            raise ValueError("RelationEdge.evidence_refs must not be empty")
        _require_text(self.provenance_ref, "RelationEdge.provenance_ref")
        expected = self.expected_id(
            source_ref=self.source_ref,
            target_ref=self.target_ref,
            relation_type=self.relation_type,
            strength=self.strength,
            quality=self.quality,
            evidence_refs=self.evidence_refs,
            provenance_ref=self.provenance_ref,
        )
        if self.edge_id != expected:
            raise ValueError("RelationEdge.edge_id does not match its fields")

    def to_dict(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "relation_type": self.relation_type,
            "strength": self.strength,
            "quality": self.quality,
            "evidence_refs": list(self.evidence_refs),
            "provenance_ref": self.provenance_ref,
        }


@dataclass(frozen=True)
class ContextCandidate:
    candidate_id: str
    primary_unit_id: str
    review_question_id: str
    relation_edge_id: str
    relation_type: RelationType
    target_source_ref_id: str
    target_span: ExactRange
    estimated_tokens: int
    necessity: ContextNecessity
    provenance_ref: str

    @classmethod
    def create(
        cls,
        *,
        primary_unit_id: str,
        review_question_id: str,
        relation_edge_id: str,
        relation_type: RelationType,
        target_source_ref_id: str,
        target_span: ExactRange,
        estimated_tokens: int,
        necessity: ContextNecessity,
        provenance_ref: str,
    ) -> ContextCandidate:
        return cls(
            candidate_id=cls.expected_id(
                primary_unit_id=primary_unit_id,
                review_question_id=review_question_id,
                relation_edge_id=relation_edge_id,
                relation_type=relation_type,
                target_source_ref_id=target_source_ref_id,
                target_span=target_span,
                estimated_tokens=estimated_tokens,
                necessity=necessity,
                provenance_ref=provenance_ref,
            ),
            primary_unit_id=primary_unit_id,
            review_question_id=review_question_id,
            relation_edge_id=relation_edge_id,
            relation_type=relation_type,
            target_source_ref_id=target_source_ref_id,
            target_span=target_span,
            estimated_tokens=estimated_tokens,
            necessity=necessity,
            provenance_ref=provenance_ref,
        )

    @staticmethod
    def expected_id(
        *,
        primary_unit_id: str,
        review_question_id: str,
        relation_edge_id: str,
        relation_type: RelationType,
        target_source_ref_id: str,
        target_span: ExactRange,
        estimated_tokens: int,
        necessity: ContextNecessity,
        provenance_ref: str,
    ) -> str:
        return _stable_id(
            "context-candidate",
            {
                "primary_unit_id": primary_unit_id,
                "review_question_id": review_question_id,
                "relation_edge_id": relation_edge_id,
                "relation_type": relation_type,
                "target_source_ref_id": target_source_ref_id,
                "target_span": _span_payload(target_span),
                "estimated_tokens": estimated_tokens,
                "necessity": necessity,
                "provenance_ref": provenance_ref,
            },
        )

    def __post_init__(self) -> None:
        _require_text(self.primary_unit_id, "ContextCandidate.primary_unit_id")
        _require_text(self.review_question_id, "ContextCandidate.review_question_id")
        if not self.relation_edge_id.startswith("relation-edge:sha256:"):
            raise ValueError("ContextCandidate.relation_edge_id must use relation-edge ID")
        if self.relation_type not in _RELATION_TYPES:
            raise ValueError(f"unsupported relation type: {self.relation_type}")
        if self.relation_type == "change_correspondence":
            raise ValueError(
                "ContextCandidate cannot use planner-derived change_correspondence"
            )
        _require_text(
            self.target_source_ref_id,
            "ContextCandidate.target_source_ref_id",
        )
        if not isinstance(self.target_span, ExactRange):
            raise ValueError("ContextCandidate.target_span must use ExactRange")
        _require_positive(self.estimated_tokens, "ContextCandidate.estimated_tokens")
        if self.necessity not in _NECESSITIES:
            raise ValueError(f"unsupported context necessity: {self.necessity}")
        _require_text(self.provenance_ref, "ContextCandidate.provenance_ref")
        if not self.provenance_ref.startswith(
            ("declaration:sha256:", "region:sha256:")
        ):
            raise ValueError(
                "ContextCandidate.provenance_ref must identify a safe "
                "declaration or region boundary"
            )
        expected = self.expected_id(
            primary_unit_id=self.primary_unit_id,
            review_question_id=self.review_question_id,
            relation_edge_id=self.relation_edge_id,
            relation_type=self.relation_type,
            target_source_ref_id=self.target_source_ref_id,
            target_span=self.target_span,
            estimated_tokens=self.estimated_tokens,
            necessity=self.necessity,
            provenance_ref=self.provenance_ref,
        )
        if self.candidate_id != expected:
            raise ValueError("ContextCandidate.candidate_id does not match its fields")

    @property
    def target_ref(self) -> str:
        return source_span_ref_id(self.target_source_ref_id, self.target_span)

    @property
    def question_binding(self) -> QuestionBinding:
        return QuestionBinding(self.primary_unit_id, self.review_question_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "primary_unit_id": self.primary_unit_id,
            "review_question_id": self.review_question_id,
            "relation_edge_id": self.relation_edge_id,
            "relation_type": self.relation_type,
            "target_source_ref_id": self.target_source_ref_id,
            "target_span": _span_payload(self.target_span),
            "estimated_tokens": self.estimated_tokens,
            "necessity": self.necessity,
            "provenance_ref": self.provenance_ref,
        }


@dataclass(frozen=True)
class CandidateOmission:
    candidate_id: str
    reason: CandidateOmissionReason

    def __post_init__(self) -> None:
        if not self.candidate_id.startswith("context-candidate:sha256:"):
            raise ValueError("CandidateOmission.candidate_id must use candidate ID")
        if self.reason not in _OMISSION_REASONS:
            raise ValueError(f"unsupported candidate omission reason: {self.reason}")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SupportingSegment:
    segment_id: str
    candidate_id: str
    source_ref_id: str
    source_span: ExactRange
    source_text: str
    question_binding: QuestionBinding
    selection_reason: ContextSelectionReason
    estimated_tokens: int
    diagnostics: tuple[ContextDiagnostic, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        candidate: ContextCandidate,
        source_text: str,
        selection_reason: ContextSelectionReason,
        diagnostics: tuple[ContextDiagnostic, ...] = (),
    ) -> SupportingSegment:
        segment_id = cls.expected_id(
            candidate_id=candidate.candidate_id,
            source_ref_id=candidate.target_source_ref_id,
            source_span=candidate.target_span,
            source_text=source_text,
            question_binding=candidate.question_binding,
            selection_reason=selection_reason,
            estimated_tokens=candidate.estimated_tokens,
            diagnostics=diagnostics,
        )
        return cls(
            segment_id=segment_id,
            candidate_id=candidate.candidate_id,
            source_ref_id=candidate.target_source_ref_id,
            source_span=candidate.target_span,
            source_text=source_text,
            question_binding=candidate.question_binding,
            selection_reason=selection_reason,
            estimated_tokens=candidate.estimated_tokens,
            diagnostics=diagnostics,
        )

    @staticmethod
    def expected_id(
        *,
        candidate_id: str,
        source_ref_id: str,
        source_span: ExactRange,
        source_text: str,
        question_binding: QuestionBinding,
        selection_reason: ContextSelectionReason,
        estimated_tokens: int,
        diagnostics: tuple[ContextDiagnostic, ...],
    ) -> str:
        return _stable_id(
            "supporting-segment",
            {
                "candidate_id": candidate_id,
                "source_ref_id": source_ref_id,
                "source_span": _span_payload(source_span),
                "source_text_sha256": hashlib.sha256(
                    source_text.encode("utf-8")
                ).hexdigest(),
                "question_binding": question_binding.to_dict(),
                "selection_reason": selection_reason,
                "estimated_tokens": estimated_tokens,
                "diagnostics": [item.to_dict() for item in diagnostics],
            },
        )

    def __post_init__(self) -> None:
        if not self.candidate_id.startswith("context-candidate:sha256:"):
            raise ValueError("SupportingSegment.candidate_id must use candidate ID")
        _require_text(self.source_ref_id, "SupportingSegment.source_ref_id")
        if not isinstance(self.source_span, ExactRange):
            raise ValueError("SupportingSegment.source_span must use ExactRange")
        if not isinstance(self.source_text, str) or not self.source_text:
            raise ValueError("SupportingSegment.source_text must be non-empty")
        if not isinstance(self.question_binding, QuestionBinding):
            raise ValueError("SupportingSegment.question_binding must use QuestionBinding")
        if self.selection_reason not in _SELECTION_REASONS:
            raise ValueError(
                f"unsupported supporting selection reason: {self.selection_reason}"
            )
        _require_positive(self.estimated_tokens, "SupportingSegment.estimated_tokens")
        if estimate_code_tokens(self.source_text) != self.estimated_tokens:
            raise ValueError(
                "SupportingSegment.estimated_tokens must match source_text"
            )
        _validate_diagnostics(self.diagnostics, "SupportingSegment.diagnostics")
        expected = self.expected_id(
            candidate_id=self.candidate_id,
            source_ref_id=self.source_ref_id,
            source_span=self.source_span,
            source_text=self.source_text,
            question_binding=self.question_binding,
            selection_reason=self.selection_reason,
            estimated_tokens=self.estimated_tokens,
            diagnostics=self.diagnostics,
        )
        if self.segment_id != expected:
            raise ValueError("SupportingSegment.segment_id does not match its fields")

    def to_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "candidate_id": self.candidate_id,
            "source_ref_id": self.source_ref_id,
            "source_span": _span_payload(self.source_span),
            "source_text": self.source_text,
            "question_binding": self.question_binding.to_dict(),
            "selection_reason": self.selection_reason,
            "estimated_tokens": self.estimated_tokens,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class ChangeGroup:
    group_id: str
    primary_unit_ids: tuple[str, ...]
    strong_edge_ids: tuple[str, ...]
    diagnostics: tuple[ContextDiagnostic, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        primary_unit_ids: Sequence[str],
        strong_edge_ids: Sequence[str],
        diagnostics: tuple[ContextDiagnostic, ...] = (),
    ) -> ChangeGroup:
        primary_ids = tuple(primary_unit_ids)
        edge_ids = tuple(sorted(set(strong_edge_ids)))
        return cls(
            group_id=cls.expected_id(primary_ids, edge_ids, diagnostics),
            primary_unit_ids=primary_ids,
            strong_edge_ids=edge_ids,
            diagnostics=diagnostics,
        )

    @staticmethod
    def expected_id(
        primary_unit_ids: tuple[str, ...],
        strong_edge_ids: tuple[str, ...],
        diagnostics: tuple[ContextDiagnostic, ...],
    ) -> str:
        return _stable_id(
            "change-group",
            {
                "primary_unit_ids": list(primary_unit_ids),
                "strong_edge_ids": list(strong_edge_ids),
                "diagnostics": [item.to_dict() for item in diagnostics],
            },
        )

    def __post_init__(self) -> None:
        _require_sorted_unique_strings(
            self.primary_unit_ids,
            "ChangeGroup.primary_unit_ids",
        )
        if not self.primary_unit_ids:
            raise ValueError("ChangeGroup.primary_unit_ids must not be empty")
        _require_sorted_unique_strings(
            self.strong_edge_ids,
            "ChangeGroup.strong_edge_ids",
        )
        if any(
            not edge_id.startswith("relation-edge:sha256:")
            for edge_id in self.strong_edge_ids
        ):
            raise ValueError("ChangeGroup.strong_edge_ids must use relation edge IDs")
        _validate_diagnostics(self.diagnostics, "ChangeGroup.diagnostics")
        if self.group_id != self.expected_id(
            self.primary_unit_ids,
            self.strong_edge_ids,
            self.diagnostics,
        ):
            raise ValueError("ChangeGroup.group_id does not match its fields")

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "primary_unit_ids": list(self.primary_unit_ids),
            "strong_edge_ids": list(self.strong_edge_ids),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class BundleBudget:
    limit: int
    primary_tokens: int
    supporting_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        _require_positive(self.limit, "BundleBudget.limit")
        _require_non_negative(self.primary_tokens, "BundleBudget.primary_tokens")
        _require_non_negative(
            self.supporting_tokens,
            "BundleBudget.supporting_tokens",
        )
        _require_non_negative(self.total_tokens, "BundleBudget.total_tokens")
        if self.total_tokens != self.primary_tokens + self.supporting_tokens:
            raise ValueError("BundleBudget.total_tokens must equal its token parts")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewContextBundle:
    bundle_id: str
    group_id: str
    primary_unit_ids: tuple[str, ...]
    primary_question_bindings: tuple[QuestionBinding, ...]
    supporting_segment_ids: tuple[str, ...]
    relation_edge_ids: tuple[str, ...]
    budget: BundleBudget
    dispatch_allowed: bool
    diagnostics: tuple[ContextDiagnostic, ...]

    @classmethod
    def create(
        cls,
        *,
        group_id: str,
        primary_unit_ids: tuple[str, ...],
        primary_question_bindings: tuple[QuestionBinding, ...],
        supporting_segment_ids: tuple[str, ...],
        relation_edge_ids: tuple[str, ...],
        budget: BundleBudget,
        dispatch_allowed: bool,
        diagnostics: tuple[ContextDiagnostic, ...],
    ) -> ReviewContextBundle:
        return cls(
            bundle_id=cls.expected_id(
                group_id=group_id,
                primary_unit_ids=primary_unit_ids,
                primary_question_bindings=primary_question_bindings,
                supporting_segment_ids=supporting_segment_ids,
                relation_edge_ids=relation_edge_ids,
                budget=budget,
                dispatch_allowed=dispatch_allowed,
                diagnostics=diagnostics,
            ),
            group_id=group_id,
            primary_unit_ids=primary_unit_ids,
            primary_question_bindings=primary_question_bindings,
            supporting_segment_ids=supporting_segment_ids,
            relation_edge_ids=relation_edge_ids,
            budget=budget,
            dispatch_allowed=dispatch_allowed,
            diagnostics=diagnostics,
        )

    @staticmethod
    def expected_id(
        *,
        group_id: str,
        primary_unit_ids: tuple[str, ...],
        primary_question_bindings: tuple[QuestionBinding, ...],
        supporting_segment_ids: tuple[str, ...],
        relation_edge_ids: tuple[str, ...],
        budget: BundleBudget,
        dispatch_allowed: bool,
        diagnostics: tuple[ContextDiagnostic, ...],
    ) -> str:
        return _stable_id(
            "review-context-bundle",
            {
                "group_id": group_id,
                "primary_unit_ids": list(primary_unit_ids),
                "primary_question_bindings": [
                    item.to_dict() for item in primary_question_bindings
                ],
                "supporting_segment_ids": list(supporting_segment_ids),
                "relation_edge_ids": list(relation_edge_ids),
                "budget": budget.to_dict(),
                "dispatch_allowed": dispatch_allowed,
                "diagnostics": [item.to_dict() for item in diagnostics],
            },
        )

    def __post_init__(self) -> None:
        if not self.group_id.startswith("change-group:sha256:"):
            raise ValueError("ReviewContextBundle.group_id must use change group ID")
        _require_sorted_unique_strings(
            self.primary_unit_ids,
            "ReviewContextBundle.primary_unit_ids",
        )
        if not self.primary_unit_ids:
            raise ValueError("ReviewContextBundle.primary_unit_ids must not be empty")
        if not isinstance(self.primary_question_bindings, tuple) or any(
            not isinstance(item, QuestionBinding)
            for item in self.primary_question_bindings
        ):
            raise ValueError(
                "ReviewContextBundle.primary_question_bindings must use QuestionBinding"
            )
        binding_keys = [
            (item.primary_unit_id, item.review_question_id)
            for item in self.primary_question_bindings
        ]
        if binding_keys != sorted(set(binding_keys)):
            raise ValueError(
                "ReviewContextBundle.primary_question_bindings must be sorted and unique"
            )
        if not self.primary_question_bindings:
            raise ValueError(
                "ReviewContextBundle.primary_question_bindings must not be empty"
            )
        if not {
            item.primary_unit_id for item in self.primary_question_bindings
        }.issubset(self.primary_unit_ids):
            raise ValueError(
                "ReviewContextBundle question bindings must belong to bundled Primaries"
            )
        _require_sorted_unique_strings(
            self.supporting_segment_ids,
            "ReviewContextBundle.supporting_segment_ids",
        )
        _require_sorted_unique_strings(
            self.relation_edge_ids,
            "ReviewContextBundle.relation_edge_ids",
        )
        if not isinstance(self.budget, BundleBudget):
            raise ValueError("ReviewContextBundle.budget must use BundleBudget")
        if not isinstance(self.dispatch_allowed, bool):
            raise ValueError("ReviewContextBundle.dispatch_allowed must be boolean")
        _validate_diagnostics(self.diagnostics, "ReviewContextBundle.diagnostics")
        has_insufficient = any(
            item.code == "context_insufficient" for item in self.diagnostics
        )
        if self.dispatch_allowed == has_insufficient:
            raise ValueError(
                "ReviewContextBundle dispatch must be blocked exactly when context is insufficient"
            )
        expected = self.expected_id(
            group_id=self.group_id,
            primary_unit_ids=self.primary_unit_ids,
            primary_question_bindings=self.primary_question_bindings,
            supporting_segment_ids=self.supporting_segment_ids,
            relation_edge_ids=self.relation_edge_ids,
            budget=self.budget,
            dispatch_allowed=self.dispatch_allowed,
            diagnostics=self.diagnostics,
        )
        if self.bundle_id != expected:
            raise ValueError("ReviewContextBundle.bundle_id does not match its fields")

    def to_dict(self) -> dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "group_id": self.group_id,
            "primary_unit_ids": list(self.primary_unit_ids),
            "primary_question_bindings": [
                item.to_dict() for item in self.primary_question_bindings
            ],
            "supporting_segment_ids": list(self.supporting_segment_ids),
            "relation_edge_ids": list(self.relation_edge_ids),
            "budget": self.budget.to_dict(),
            "dispatch_allowed": self.dispatch_allowed,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class ContextBudgetSummary:
    limit: int
    total_primary_tokens: int
    total_supporting_tokens: int
    total_omitted_tokens: int
    max_bundle_tokens: int
    dispatchable_bundles: int
    blocked_bundles: int

    def __post_init__(self) -> None:
        _require_positive(self.limit, "ContextBudgetSummary.limit")
        for value, context in (
            (self.total_primary_tokens, "total_primary_tokens"),
            (self.total_supporting_tokens, "total_supporting_tokens"),
            (self.total_omitted_tokens, "total_omitted_tokens"),
            (self.max_bundle_tokens, "max_bundle_tokens"),
            (self.dispatchable_bundles, "dispatchable_bundles"),
            (self.blocked_bundles, "blocked_bundles"),
        ):
            _require_non_negative(value, f"ContextBudgetSummary.{context}")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ContextPlanResult:
    context_plan_id: str
    planner_version: str
    token_estimator_version: str
    change_set_id: str
    blocking_change_ids: tuple[str, ...]
    primary_question_bindings: tuple[QuestionBinding, ...]
    candidates: tuple[ContextCandidate, ...]
    supporting_segments: tuple[SupportingSegment, ...]
    relation_edges: tuple[RelationEdge, ...]
    change_groups: tuple[ChangeGroup, ...]
    bundles: tuple[ReviewContextBundle, ...]
    omitted_candidate_ids: tuple[str, ...]
    omitted_candidates: tuple[CandidateOmission, ...]
    budget_summary: ContextBudgetSummary
    diagnostics: tuple[ContextDiagnostic, ...]
    schema_version: str = CONTEXT_PLAN_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        change_set_id: str,
        blocking_change_ids: tuple[str, ...],
        primary_question_bindings: tuple[QuestionBinding, ...],
        candidates: tuple[ContextCandidate, ...],
        supporting_segments: tuple[SupportingSegment, ...],
        relation_edges: tuple[RelationEdge, ...],
        change_groups: tuple[ChangeGroup, ...],
        bundles: tuple[ReviewContextBundle, ...],
        omitted_candidates: tuple[CandidateOmission, ...],
        budget_summary: ContextBudgetSummary,
        diagnostics: tuple[ContextDiagnostic, ...],
    ) -> ContextPlanResult:
        omitted_ids = tuple(item.candidate_id for item in omitted_candidates)
        payload = cls.identity_payload(
            planner_version=CONTEXT_PLANNER_VERSION,
            token_estimator_version=TOKEN_ESTIMATOR_VERSION,
            change_set_id=change_set_id,
            blocking_change_ids=blocking_change_ids,
            primary_question_bindings=primary_question_bindings,
            candidates=candidates,
            supporting_segments=supporting_segments,
            relation_edges=relation_edges,
            change_groups=change_groups,
            bundles=bundles,
            omitted_candidate_ids=omitted_ids,
            omitted_candidates=omitted_candidates,
            budget_summary=budget_summary,
            diagnostics=diagnostics,
        )
        return cls(
            context_plan_id=_stable_id("context-plan", payload),
            planner_version=CONTEXT_PLANNER_VERSION,
            token_estimator_version=TOKEN_ESTIMATOR_VERSION,
            change_set_id=change_set_id,
            blocking_change_ids=blocking_change_ids,
            primary_question_bindings=primary_question_bindings,
            candidates=candidates,
            supporting_segments=supporting_segments,
            relation_edges=relation_edges,
            change_groups=change_groups,
            bundles=bundles,
            omitted_candidate_ids=omitted_ids,
            omitted_candidates=omitted_candidates,
            budget_summary=budget_summary,
            diagnostics=diagnostics,
        )

    @staticmethod
    def identity_payload(
        *,
        planner_version: str,
        token_estimator_version: str,
        change_set_id: str,
        blocking_change_ids: tuple[str, ...],
        primary_question_bindings: tuple[QuestionBinding, ...],
        candidates: tuple[ContextCandidate, ...],
        supporting_segments: tuple[SupportingSegment, ...],
        relation_edges: tuple[RelationEdge, ...],
        change_groups: tuple[ChangeGroup, ...],
        bundles: tuple[ReviewContextBundle, ...],
        omitted_candidate_ids: tuple[str, ...],
        omitted_candidates: tuple[CandidateOmission, ...],
        budget_summary: ContextBudgetSummary,
        diagnostics: tuple[ContextDiagnostic, ...],
    ) -> dict[str, object]:
        return {
            "planner_version": planner_version,
            "token_estimator_version": token_estimator_version,
            "change_set_id": change_set_id,
            "blocking_change_ids": list(blocking_change_ids),
            "primary_question_bindings": [
                item.to_dict() for item in primary_question_bindings
            ],
            "candidates": [item.to_dict() for item in candidates],
            "supporting_segments": [item.to_dict() for item in supporting_segments],
            "relation_edges": [item.to_dict() for item in relation_edges],
            "change_groups": [item.to_dict() for item in change_groups],
            "bundles": [item.to_dict() for item in bundles],
            "omitted_candidate_ids": list(omitted_candidate_ids),
            "omitted_candidates": [item.to_dict() for item in omitted_candidates],
            "budget_summary": budget_summary.to_dict(),
            "diagnostics": [item.to_dict() for item in diagnostics],
        }

    def __post_init__(self) -> None:
        if self.schema_version != CONTEXT_PLAN_SCHEMA_VERSION:
            raise ValueError(
                f"ContextPlanResult.schema_version must be {CONTEXT_PLAN_SCHEMA_VERSION}"
            )
        if self.planner_version != CONTEXT_PLANNER_VERSION:
            raise ValueError(
                f"ContextPlanResult.planner_version must be {CONTEXT_PLANNER_VERSION}"
            )
        if self.token_estimator_version != TOKEN_ESTIMATOR_VERSION:
            raise ValueError(
                "ContextPlanResult.token_estimator_version does not match contract"
            )
        if not self.change_set_id.startswith("change-set:sha256:"):
            raise ValueError("ContextPlanResult.change_set_id must use ChangeSet ID")
        _require_sorted_unique_strings(
            self.blocking_change_ids,
            "ContextPlanResult.blocking_change_ids",
        )
        if any(
            not (
                item.startswith("change-atom:sha256:")
                or item.startswith("changed-file:sha256:")
            )
            for item in self.blocking_change_ids
        ):
            raise ValueError(
                "ContextPlanResult.blocking_change_ids has unsupported identities"
            )
        self._validate_graph()
        expected = _stable_id(
            "context-plan",
            self.identity_payload(
                planner_version=self.planner_version,
                token_estimator_version=self.token_estimator_version,
                change_set_id=self.change_set_id,
                blocking_change_ids=self.blocking_change_ids,
                primary_question_bindings=self.primary_question_bindings,
                candidates=self.candidates,
                supporting_segments=self.supporting_segments,
                relation_edges=self.relation_edges,
                change_groups=self.change_groups,
                bundles=self.bundles,
                omitted_candidate_ids=self.omitted_candidate_ids,
                omitted_candidates=self.omitted_candidates,
                budget_summary=self.budget_summary,
                diagnostics=self.diagnostics,
            ),
        )
        if self.context_plan_id != expected:
            raise ValueError("ContextPlanResult.context_plan_id does not match its graph")

    def _validate_graph(self) -> None:
        if not isinstance(self.budget_summary, ContextBudgetSummary):
            raise ValueError("ContextPlanResult.budget_summary has invalid type")
        _validate_diagnostics(self.diagnostics, "ContextPlanResult.diagnostics")

        binding_keys = [
            (item.primary_unit_id, item.review_question_id)
            for item in self.primary_question_bindings
        ]
        if any(
            not isinstance(item, QuestionBinding)
            for item in self.primary_question_bindings
        ) or binding_keys != sorted(set(binding_keys)):
            raise ValueError(
                "ContextPlanResult.primary_question_bindings must be sorted and unique"
            )
        edge_ids = [item.edge_id for item in self.relation_edges]
        if any(not isinstance(item, RelationEdge) for item in self.relation_edges):
            raise ValueError("ContextPlanResult.relation_edges has invalid type")
        if edge_ids != sorted(set(edge_ids)):
            raise ValueError("ContextPlanResult.relation_edges must use stable ID order")
        candidate_ids = [item.candidate_id for item in self.candidates]
        if any(not isinstance(item, ContextCandidate) for item in self.candidates):
            raise ValueError("ContextPlanResult.candidates has invalid type")
        if candidate_ids != sorted(set(candidate_ids)):
            raise ValueError("ContextPlanResult.candidates must use stable ID order")
        segment_ids = [item.segment_id for item in self.supporting_segments]
        if any(
            not isinstance(item, SupportingSegment)
            for item in self.supporting_segments
        ) or segment_ids != sorted(set(segment_ids)):
            raise ValueError(
                "ContextPlanResult.supporting_segments must use stable ID order"
            )
        group_ids = [item.group_id for item in self.change_groups]
        if any(not isinstance(item, ChangeGroup) for item in self.change_groups):
            raise ValueError("ContextPlanResult.change_groups has invalid type")
        if group_ids != sorted(set(group_ids)):
            raise ValueError("ContextPlanResult.change_groups must use stable ID order")
        bundle_ids = [item.bundle_id for item in self.bundles]
        if any(
            not isinstance(item, ReviewContextBundle) for item in self.bundles
        ) or bundle_ids != sorted(set(bundle_ids)):
            raise ValueError("ContextPlanResult.bundles must use stable ID order")

        primary_ids = [
            primary_id
            for group in self.change_groups
            for primary_id in group.primary_unit_ids
        ]
        if len(primary_ids) != len(set(primary_ids)):
            raise ValueError("ChangeGroups must partition Primary ReviewUnits")
        if set(primary_ids) != {item.primary_unit_id for item in self.primary_question_bindings}:
            raise ValueError("Question bindings must exactly cover grouped Primaries")
        if {bundle.group_id for bundle in self.bundles} != set(group_ids):
            raise ValueError(
                "ContextPlanResult requires one or more bundles for every group"
            )

        edge_by_id = {item.edge_id: item for item in self.relation_edges}
        candidate_by_id = {item.candidate_id: item for item in self.candidates}
        candidate_ids_by_edge: dict[str, list[str]] = {}
        for candidate in self.candidates:
            candidate_ids_by_edge.setdefault(candidate.relation_edge_id, []).append(
                candidate.candidate_id
            )
        binding_set = set(binding_keys)
        for candidate in self.candidates:
            edge = edge_by_id.get(candidate.relation_edge_id)
            if edge is None:
                raise ValueError("ContextCandidate references a dangling RelationEdge")
            if (candidate.primary_unit_id, candidate.review_question_id) not in binding_set:
                raise ValueError("ContextCandidate has no Primary question binding")
            if (
                edge.source_ref != candidate.primary_unit_id
                or edge.target_ref != candidate.target_ref
                or edge.relation_type != candidate.relation_type
                or candidate.provenance_ref not in edge.evidence_refs
            ):
                raise ValueError("ContextCandidate does not match its RelationEdge")

        primary_id_set = set(primary_ids)
        for edge in self.relation_edges:
            if edge.source_ref not in primary_id_set:
                raise ValueError("RelationEdge must originate from a Primary ReviewUnit")
            if edge.target_ref in primary_id_set:
                if edge.edge_id in candidate_ids_by_edge:
                    raise ValueError(
                        "Primary-to-Primary RelationEdge cannot back a Supporting candidate"
                    )
            elif edge.edge_id not in candidate_ids_by_edge:
                raise ValueError(
                    "Supporting RelationEdge must be referenced by a ContextCandidate"
                )

        group_by_primary: dict[str, ChangeGroup] = {}
        for group in self.change_groups:
            for primary_id in group.primary_unit_ids:
                group_by_primary[primary_id] = group
            for edge_id in group.strong_edge_ids:
                edge = edge_by_id.get(edge_id)
                if edge is None:
                    raise ValueError("ChangeGroup references a dangling RelationEdge")
                if not (
                    edge.source_ref in group.primary_unit_ids
                    and edge.target_ref in group.primary_unit_ids
                    and edge.strength == "strong"
                    and edge.quality == "exact"
                    and edge.relation_type not in _NON_EVIDENCE_RELATIONS
                ):
                    raise ValueError(
                        "ChangeGroup may contain only strong exact typed Primary edges"
                    )

        selected_candidate_ids: list[str] = []
        for segment in self.supporting_segments:
            selected_candidate = candidate_by_id.get(segment.candidate_id)
            if selected_candidate is None:
                raise ValueError("SupportingSegment references a dangling candidate")
            if (
                segment.source_ref_id != selected_candidate.target_source_ref_id
                or segment.source_span != selected_candidate.target_span
                or segment.question_binding != selected_candidate.question_binding
                or segment.estimated_tokens != selected_candidate.estimated_tokens
            ):
                raise ValueError("SupportingSegment does not match its candidate")
            edge = edge_by_id[selected_candidate.relation_edge_id]
            if (
                selected_candidate.necessity == "distractor"
                or selected_candidate.relation_type in _NON_EVIDENCE_RELATIONS
                or edge.quality != "exact"
            ):
                raise ValueError(
                    "SupportingSegment requires an exact, non-distractor typed relation"
                )
            expected_selection = (
                "required_context"
                if selected_candidate.necessity == "required"
                else "helpful_context"
            )
            if segment.selection_reason != expected_selection:
                raise ValueError(
                    "SupportingSegment selection_reason must match candidate necessity"
                )
            selected_candidate_ids.append(segment.candidate_id)
        if len(selected_candidate_ids) != len(set(selected_candidate_ids)):
            raise ValueError("a ContextCandidate may produce at most one segment")

        omission_ids = [item.candidate_id for item in self.omitted_candidates]
        if any(
            not isinstance(item, CandidateOmission)
            for item in self.omitted_candidates
        ) or omission_ids != sorted(set(omission_ids)):
            raise ValueError("ContextPlanResult omissions must use stable candidate order")
        if self.omitted_candidate_ids != tuple(omission_ids):
            raise ValueError("omitted_candidate_ids must mirror omitted_candidates")
        if set(selected_candidate_ids).intersection(omission_ids):
            raise ValueError("a candidate cannot be both selected and omitted")
        if set(selected_candidate_ids).union(omission_ids) != set(candidate_ids):
            raise ValueError("every candidate must be selected or explicitly omitted")
        for omission in self.omitted_candidates:
            candidate = candidate_by_id[omission.candidate_id]
            edge = edge_by_id[candidate.relation_edge_id]
            if omission.reason == "distractor_rejected" and not (
                candidate.necessity == "distractor"
                or candidate.relation_type in _NON_EVIDENCE_RELATIONS
            ):
                raise ValueError("distractor omission reason does not match candidate")
            if omission.reason == "relation_degraded" and edge.quality != "degraded":
                raise ValueError("degraded omission reason requires degraded edge")
            if omission.reason == "budget_exceeded" and (
                candidate.necessity == "distractor"
                or candidate.relation_type in _NON_EVIDENCE_RELATIONS
                or edge.quality != "exact"
            ):
                raise ValueError("budget omission reason masks an ineligible relation")
            if omission.reason == "context_blocked" and (
                candidate.necessity == "distractor"
                or candidate.relation_type in _NON_EVIDENCE_RELATIONS
                or edge.quality != "exact"
            ):
                raise ValueError(
                    "context-blocked omission reason masks an ineligible relation"
                )

        segment_by_id = {
            segment.segment_id: segment for segment in self.supporting_segments
        }
        segment_by_candidate_id = {
            segment.candidate_id: segment for segment in self.supporting_segments
        }
        omission_by_candidate_id = {
            omission.candidate_id: omission for omission in self.omitted_candidates
        }
        bundles_by_group: dict[str, list[ReviewContextBundle]] = {}
        for bundle in self.bundles:
            bundles_by_group.setdefault(bundle.group_id, []).append(bundle)
        for group in self.change_groups:
            group_bundles = bundles_by_group[group.group_id]
            expected_group_bindings = {
                binding
                for binding in self.primary_question_bindings
                if binding.primary_unit_id in group.primary_unit_ids
            }
            actual_group_bindings = {
                binding
                for bundle in group_bundles
                for binding in bundle.primary_question_bindings
            }
            if actual_group_bindings != expected_group_bindings:
                raise ValueError(
                    "group bundles must collectively cover every Primary question binding"
                )
            for candidate in self.candidates:
                if candidate.primary_unit_id not in group.primary_unit_ids:
                    continue
                question_bundles = [
                    bundle
                    for bundle in group_bundles
                    if any(
                        binding.review_question_id
                        == candidate.review_question_id
                        for binding in bundle.primary_question_bindings
                    )
                ]
                if not question_bundles:
                    raise ValueError(
                        "candidate question has no ReviewContextBundle"
                    )
                candidate_omission = omission_by_candidate_id.get(
                    candidate.candidate_id
                )
                if (
                    candidate_omission is not None
                    and candidate_omission.reason == "context_blocked"
                    and any(bundle.dispatch_allowed for bundle in question_bundles)
                ):
                    raise ValueError(
                        "context-blocked candidate requires blocked question bundles"
                    )
                if candidate.necessity != "required":
                    continue
                selected_segment = segment_by_candidate_id.get(
                    candidate.candidate_id
                )
                required_omission = omission_by_candidate_id.get(
                    candidate.candidate_id
                )
                if required_omission is not None:
                    if any(bundle.dispatch_allowed for bundle in question_bundles):
                        raise ValueError(
                            "missing required context must block its question bundles"
                        )
                    if any(
                        not any(
                            diagnostic.code == "context_insufficient"
                            and candidate.candidate_id in diagnostic.subject_ids
                            for diagnostic in bundle.diagnostics
                        )
                        for bundle in question_bundles
                    ):
                        raise ValueError(
                            "missing required context needs an exact diagnostic subject"
                        )
                elif selected_segment is not None and any(
                    bundle.dispatch_allowed
                    and selected_segment.segment_id
                    not in bundle.supporting_segment_ids
                    for bundle in question_bundles
                ):
                    raise ValueError(
                        "every dispatchable question bundle must retain required context"
                    )
            group_segment_ids = {
                segment.segment_id
                for segment in self.supporting_segments
                if group_by_primary[
                    candidate_by_id[segment.candidate_id].primary_unit_id
                ].group_id
                == group.group_id
            }
            primary_token_totals = {
                bundle.budget.primary_tokens for bundle in group_bundles
            }
            if len(primary_token_totals) != 1:
                raise ValueError(
                    "all bundles in one group must count identical Primary source"
                )
            for bundle in group_bundles:
                if bundle.primary_unit_ids != group.primary_unit_ids:
                    raise ValueError(
                        "ReviewContextBundle must retain every group Primary"
                    )
                if bundle.budget.limit != self.budget_summary.limit:
                    raise ValueError("bundle budget limit must match plan budget")
                if bundle.dispatch_allowed and (
                    bundle.budget.total_tokens > bundle.budget.limit
                ):
                    raise ValueError("dispatchable bundle exceeds code context budget")
                question_ids = {
                    binding.review_question_id
                    for binding in bundle.primary_question_bindings
                }
                if len(question_ids) != 1:
                    raise ValueError(
                        "each ReviewContextBundle must bind exactly one review question"
                    )
                question_id = next(iter(question_ids))
                expected_question_bindings = tuple(
                    binding
                    for binding in self.primary_question_bindings
                    if binding.primary_unit_id in group.primary_unit_ids
                    and binding.review_question_id == question_id
                )
                if bundle.primary_question_bindings != expected_question_bindings:
                    raise ValueError(
                        "every question bundle must retain all group bindings for that question"
                    )
                if not set(group.strong_edge_ids).issubset(
                    bundle.relation_edge_ids
                ):
                    raise ValueError(
                        "bundle must retain group-forming RelationEdges"
                    )
                if not set(bundle.supporting_segment_ids).issubset(
                    group_segment_ids
                ):
                    raise ValueError(
                        "bundle SupportingSegments must belong to its Primary group"
                    )
                for segment_id in bundle.supporting_segment_ids:
                    candidate = candidate_by_id[
                        segment_by_id[segment_id].candidate_id
                    ]
                    if candidate.review_question_id != question_id:
                        raise ValueError(
                            "bundle SupportingSegment must match its bound question"
                        )
                expected_edge_ids = set(group.strong_edge_ids).union(
                    candidate_by_id[
                        segment_by_id[segment_id].candidate_id
                    ].relation_edge_id
                    for segment_id in bundle.supporting_segment_ids
                )
                if set(bundle.relation_edge_ids) != expected_edge_ids:
                    raise ValueError(
                        "bundle RelationEdges must exactly explain its group and Supporting"
                    )
                expected_supporting_tokens = sum(
                    segment_by_id[segment_id].estimated_tokens
                    for segment_id in bundle.supporting_segment_ids
                )
                if bundle.budget.supporting_tokens != expected_supporting_tokens:
                    raise ValueError(
                        "bundle supporting budget must equal selected source tokens"
                    )

            for segment_id in group_segment_ids:
                segment = segment_by_id[segment_id]
                candidate = candidate_by_id[segment.candidate_id]
                owning_bundles = [
                    bundle
                    for bundle in group_bundles
                    if segment_id in bundle.supporting_segment_ids
                ]
                if not owning_bundles:
                    raise ValueError(
                        "every SupportingSegment must belong to a bundle"
                    )
                if candidate.necessity != "required" and len(owning_bundles) != 1:
                    raise ValueError(
                        "only required Supporting may repeat across bundles"
                    )
        bundled_segment_ids = {
            segment_id
            for bundle in self.bundles
            for segment_id in bundle.supporting_segment_ids
        }
        if bundled_segment_ids != set(segment_ids):
            raise ValueError("every SupportingSegment must belong to a bundle")

        self._validate_semantic_truth()

        aggregated_diagnostics = tuple(
            sorted(
                {
                    diagnostic
                    for bundle in self.bundles
                    for diagnostic in bundle.diagnostics
                },
                key=_diagnostic_sort_key,
            )
        )
        top_only_empty_plan_diagnostics = (
            not self.bundles
            and bool(self.blocking_change_ids)
            and self.diagnostics
            == (
                ContextDiagnostic(
                    "context_insufficient",
                    self.blocking_change_ids,
                ),
            )
        )
        if (
            self.diagnostics != aggregated_diagnostics
            and not top_only_empty_plan_diagnostics
        ):
            raise ValueError(
                "ContextPlanResult diagnostics must aggregate bundle diagnostics"
            )

        expected_summary = ContextBudgetSummary(
            limit=self.budget_summary.limit,
            total_primary_tokens=sum(
                bundle.budget.primary_tokens for bundle in self.bundles
            ),
            total_supporting_tokens=sum(
                bundle.budget.supporting_tokens for bundle in self.bundles
            ),
            total_omitted_tokens=sum(
                candidate_by_id[item.candidate_id].estimated_tokens
                for item in self.omitted_candidates
            ),
            max_bundle_tokens=max(
                (bundle.budget.total_tokens for bundle in self.bundles),
                default=0,
            ),
            dispatchable_bundles=sum(
                bundle.dispatch_allowed for bundle in self.bundles
            ),
            blocked_bundles=sum(
                not bundle.dispatch_allowed for bundle in self.bundles
            ),
        )
        if self.budget_summary != expected_summary:
            raise ValueError("ContextPlanResult.budget_summary does not match bundles")

    def _validate_semantic_truth(self) -> None:
        primary_ids = tuple(
            sorted(
                {
                    binding.primary_unit_id
                    for binding in self.primary_question_bindings
                }
            )
        )
        primary_id_set = set(primary_ids)
        parent = {primary_id: primary_id for primary_id in primary_ids}

        def find(primary_id: str) -> str:
            root = parent[primary_id]
            if root != primary_id:
                parent[primary_id] = find(root)
            return parent[primary_id]

        def union(left: str, right: str) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                return
            first, second = sorted((left_root, right_root))
            parent[second] = first

        grouping_edges = [
            edge
            for edge in self.relation_edges
            if edge.source_ref in primary_id_set
            and edge.target_ref in primary_id_set
            and edge.strength == "strong"
            and edge.quality == "exact"
            and edge.relation_type not in _NON_EVIDENCE_RELATIONS
        ]
        for edge in grouping_edges:
            union(edge.source_ref, edge.target_ref)
        expected_members: dict[str, list[str]] = {}
        for primary_id in primary_ids:
            expected_members.setdefault(find(primary_id), []).append(primary_id)
        expected_groups = {
            tuple(sorted(members)): tuple(
                sorted(
                    edge.edge_id
                    for edge in grouping_edges
                    if edge.source_ref in members and edge.target_ref in members
                )
            )
            for members in expected_members.values()
        }
        actual_groups = {
            group.primary_unit_ids: group.strong_edge_ids
            for group in self.change_groups
        }
        if actual_groups != expected_groups:
            raise ValueError(
                "ChangeGroups must exactly equal strong exact relation components"
            )

        edge_by_id = {edge.edge_id: edge for edge in self.relation_edges}
        segment_by_id = {
            segment.segment_id: segment for segment in self.supporting_segments
        }
        omission_by_candidate_id = {
            omission.candidate_id: omission for omission in self.omitted_candidates
        }
        bundles_by_group: dict[str, list[ReviewContextBundle]] = {}
        for bundle in self.bundles:
            bundles_by_group.setdefault(bundle.group_id, []).append(bundle)

        for group in self.change_groups:
            group_bundles = bundles_by_group[group.group_id]
            primary_tokens = group_bundles[0].budget.primary_tokens
            group_candidates = tuple(
                sorted(
                    (
                        candidate
                        for candidate in self.candidates
                        if candidate.primary_unit_id in group.primary_unit_ids
                    ),
                    key=_candidate_sort_key,
                )
            )
            question_ids = sorted(
                {
                    binding.review_question_id
                    for binding in self.primary_question_bindings
                    if binding.primary_unit_id in group.primary_unit_ids
                }
            )
            for question_id in question_ids:
                question_candidates = tuple(
                    candidate
                    for candidate in group_candidates
                    if candidate.review_question_id == question_id
                )
                question_bundles = [
                    bundle
                    for bundle in group_bundles
                    if bundle.primary_question_bindings[0].review_question_id
                    == question_id
                ]
                self._validate_question_semantics(
                    group=group,
                    question_candidates=question_candidates,
                    question_bundles=question_bundles,
                    edge_by_id=edge_by_id,
                    segment_by_id=segment_by_id,
                    omission_by_candidate_id=omission_by_candidate_id,
                    primary_tokens=primary_tokens,
                )

    def _validate_question_semantics(
        self,
        *,
        group: ChangeGroup,
        question_candidates: tuple[ContextCandidate, ...],
        question_bundles: list[ReviewContextBundle],
        edge_by_id: Mapping[str, RelationEdge],
        segment_by_id: Mapping[str, SupportingSegment],
        omission_by_candidate_id: Mapping[str, CandidateOmission],
        primary_tokens: int,
    ) -> None:
        limit = self.budget_summary.limit
        overflow_primary_ids = (
            group.primary_unit_ids if primary_tokens > limit else ()
        )
        expected_omissions: dict[str, CandidateOmissionReason] = {}
        required_candidates: list[ContextCandidate] = []
        helpful_candidates: list[ContextCandidate] = []
        required_missing: list[str] = []
        degraded_edge_ids: list[str] = []

        for candidate in question_candidates:
            edge = edge_by_id[candidate.relation_edge_id]
            if candidate.necessity == "distractor" or (
                candidate.relation_type in _NON_EVIDENCE_RELATIONS
            ):
                expected_omissions[candidate.candidate_id] = (
                    "distractor_rejected"
                )
                if candidate.necessity == "required":
                    required_missing.append(candidate.candidate_id)
                continue
            if edge.quality != "exact":
                expected_omissions[candidate.candidate_id] = "relation_degraded"
                degraded_edge_ids.append(edge.edge_id)
                if candidate.necessity == "required":
                    required_missing.append(candidate.candidate_id)
                continue
            if self.blocking_change_ids:
                expected_omissions[candidate.candidate_id] = "context_blocked"
                if candidate.necessity == "required":
                    required_missing.append(candidate.candidate_id)
                continue
            if overflow_primary_ids:
                expected_omissions[candidate.candidate_id] = "budget_exceeded"
                if candidate.necessity == "required":
                    required_missing.append(candidate.candidate_id)
                continue
            if candidate.necessity == "required":
                required_candidates.append(candidate)
            else:
                helpful_candidates.append(candidate)

        selected_required: list[ContextCandidate] = []
        required_tokens = 0
        for candidate in required_candidates:
            if primary_tokens + required_tokens + candidate.estimated_tokens <= limit:
                selected_required.append(candidate)
                required_tokens += candidate.estimated_tokens
            else:
                expected_omissions[candidate.candidate_id] = "budget_exceeded"
                required_missing.append(candidate.candidate_id)

        helpful_bins: list[list[ContextCandidate]] = [[]]
        if required_missing:
            for candidate in helpful_candidates:
                expected_omissions[candidate.candidate_id] = "context_blocked"
        else:
            helpful_capacity = limit - primary_tokens - required_tokens
            helpful_bin_tokens = [0]
            for candidate in helpful_candidates:
                if candidate.estimated_tokens > helpful_capacity:
                    expected_omissions[candidate.candidate_id] = "budget_exceeded"
                    continue
                placed = False
                for index, used_tokens in enumerate(helpful_bin_tokens):
                    if used_tokens + candidate.estimated_tokens <= helpful_capacity:
                        helpful_bins[index].append(candidate)
                        helpful_bin_tokens[index] += candidate.estimated_tokens
                        placed = True
                        break
                if not placed:
                    helpful_bins.append([candidate])
                    helpful_bin_tokens.append(candidate.estimated_tokens)

        expected_selected_ids = {
            candidate.candidate_id for candidate in selected_required
        }.union(
            candidate.candidate_id
            for helpful_bin in helpful_bins
            for candidate in helpful_bin
        )
        actual_selected_ids = {
            segment.candidate_id
            for segment in self.supporting_segments
            if segment.question_binding.review_question_id
            == question_bundles[0]
            .primary_question_bindings[0]
            .review_question_id
            and segment.question_binding.primary_unit_id
            in group.primary_unit_ids
        }
        if actual_selected_ids != expected_selected_ids:
            raise ValueError(
                "Supporting selection does not match necessity and budget truth"
            )
        actual_omissions = {
            candidate.candidate_id: omission_by_candidate_id[
                candidate.candidate_id
            ].reason
            for candidate in question_candidates
            if candidate.candidate_id in omission_by_candidate_id
        }
        if actual_omissions != expected_omissions:
            raise ValueError(
                "candidate omission reasons do not match relation and budget truth"
            )

        if required_missing:
            helpful_bins = [[]]
        expected_bundle_candidates = Counter(
            tuple(
                sorted(
                    {
                        candidate.candidate_id
                        for candidate in selected_required
                    }.union(
                        candidate.candidate_id for candidate in helpful_bin
                    )
                )
            )
            for helpful_bin in helpful_bins
        )
        actual_bundle_candidates = Counter(
            tuple(
                sorted(
                    segment_by_id[segment_id].candidate_id
                    for segment_id in bundle.supporting_segment_ids
                )
            )
            for bundle in question_bundles
        )
        if actual_bundle_candidates != expected_bundle_candidates:
            raise ValueError(
                "ReviewContextBundles do not match required repetition/helpful first-fit"
            )

        expected_diagnostics: list[ContextDiagnostic] = []
        if overflow_primary_ids:
            expected_diagnostics.append(
                ContextDiagnostic("primary_exceeds_budget", overflow_primary_ids)
            )
        if degraded_edge_ids:
            expected_diagnostics.append(
                ContextDiagnostic(
                    "relation_degraded",
                    tuple(sorted(set(degraded_edge_ids))),
                )
            )
        insufficient_subjects = tuple(
            sorted(
                set(
                    (
                        *self.blocking_change_ids,
                        *overflow_primary_ids,
                        *required_missing,
                    )
                )
            )
        )
        if insufficient_subjects:
            expected_diagnostics.append(
                ContextDiagnostic(
                    "context_insufficient",
                    insufficient_subjects,
                )
            )
        normalized_diagnostics = tuple(
            sorted(set(expected_diagnostics), key=_diagnostic_sort_key)
        )
        expected_dispatch = not insufficient_subjects
        if any(
            bundle.diagnostics != normalized_diagnostics
            or bundle.dispatch_allowed != expected_dispatch
            for bundle in question_bundles
        ):
            raise ValueError(
                "bundle diagnostics/dispatch do not match semantic truth"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "context_plan_id": self.context_plan_id,
            **self.identity_payload(
                planner_version=self.planner_version,
                token_estimator_version=self.token_estimator_version,
                change_set_id=self.change_set_id,
                blocking_change_ids=self.blocking_change_ids,
                primary_question_bindings=self.primary_question_bindings,
                candidates=self.candidates,
                supporting_segments=self.supporting_segments,
                relation_edges=self.relation_edges,
                change_groups=self.change_groups,
                bundles=self.bundles,
                omitted_candidate_ids=self.omitted_candidate_ids,
                omitted_candidates=self.omitted_candidates,
                budget_summary=self.budget_summary,
                diagnostics=self.diagnostics,
            ),
        }


class _UnionFind:
    def __init__(self, values: Sequence[str]) -> None:
        self._parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self._parent[value]
        if parent != value:
            self._parent[value] = self.find(parent)
        return self._parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        self._parent[second] = first


class ContextPlanner:
    """Deterministic planning engine used by CodeAnalyzer and the Golden harness.

    Application code must enter through ``CodeAnalyzer.plan_context`` so the
    Primary sequence comes from one complete, validated RU-4 AnalysisResult.
    ``plan`` remains visible only as the fixture seam for the independent Golden.
    """

    def plan(
        self,
        *,
        change_set_id: str,
        primary_units: Sequence[ReviewUnit],
        primary_question_bindings: Sequence[QuestionBinding],
        source_snapshots: Mapping[str, CodeSourceSnapshot]
        | Sequence[CodeSourceSnapshot],
        candidates: Sequence[ContextCandidate] = (),
        relation_edges: Sequence[RelationEdge] = (),
        blocking_change_ids: Sequence[str] = (),
        code_context_budget: int,
    ) -> ContextPlanResult:
        if not isinstance(change_set_id, str) or not change_set_id.startswith(
            "change-set:sha256:"
        ):
            raise ValueError("change_set_id must use ChangeSet identity")
        budget_limit = _require_positive(
            code_context_budget,
            "code_context_budget",
        )
        units = self._validate_primaries(primary_units)
        bindings = self._validate_bindings(primary_question_bindings, units)
        external_edges = self._validate_edges(relation_edges)
        if any(
            edge.relation_type == "change_correspondence"
            for edge in external_edges
        ):
            raise ValueError(
                "change_correspondence RelationEdges are planner-derived only"
            )
        edges = self._validate_edges(
            (
                *external_edges,
                *self._change_correspondence_edges(units, change_set_id),
            )
        )
        normalized_candidates = self._validate_candidates(candidates)
        blockers = tuple(sorted(set(blocking_change_ids)))
        if any(
            not isinstance(item, str)
            or not (
                item.startswith("change-atom:sha256:")
                or item.startswith("changed-file:sha256:")
            )
            for item in blockers
        ):
            raise ValueError(
                "blocking_change_ids must contain ChangeAtom or ChangedFile identities"
            )
        if len(blockers) != len(tuple(blocking_change_ids)):
            raise ValueError("blocking_change_ids must be unique")
        snapshots = self._validate_snapshots(
            source_snapshots,
            units,
            normalized_candidates,
        )
        candidate_text = self._validate_candidate_graph(
            normalized_candidates,
            edges,
            bindings,
            units,
            snapshots,
        )
        self._validate_primary_sources(units, snapshots)

        groups = self._build_groups(units, edges)
        segments: list[SupportingSegment] = []
        omissions: list[CandidateOmission] = []
        bundles: list[ReviewContextBundle] = []
        plan_diagnostics: list[ContextDiagnostic] = []

        for group in groups:
            (
                group_segments,
                group_omissions,
                group_bundles,
                group_diagnostics,
            ) = self._plan_group(
                group=group,
                units=units,
                bindings=bindings,
                candidates=normalized_candidates,
                edges=edges,
                candidate_text=candidate_text,
                blockers=blockers,
                budget_limit=budget_limit,
            )
            segments.extend(group_segments)
            omissions.extend(group_omissions)
            bundles.extend(group_bundles)
            plan_diagnostics.extend(group_diagnostics)

        if blockers and not groups:
            plan_diagnostics.append(
                ContextDiagnostic("context_insufficient", blockers)
            )

        sorted_segments = tuple(sorted(segments, key=lambda item: item.segment_id))
        sorted_omissions = tuple(
            sorted(omissions, key=lambda item: item.candidate_id)
        )
        sorted_bundles = tuple(sorted(bundles, key=lambda item: item.bundle_id))
        summary = ContextBudgetSummary(
            limit=budget_limit,
            total_primary_tokens=sum(
                bundle.budget.primary_tokens for bundle in sorted_bundles
            ),
            total_supporting_tokens=sum(
                bundle.budget.supporting_tokens for bundle in sorted_bundles
            ),
            total_omitted_tokens=sum(
                next(
                    candidate.estimated_tokens
                    for candidate in normalized_candidates
                    if candidate.candidate_id == omission.candidate_id
                )
                for omission in sorted_omissions
            ),
            max_bundle_tokens=max(
                (bundle.budget.total_tokens for bundle in sorted_bundles),
                default=0,
            ),
            dispatchable_bundles=sum(
                bundle.dispatch_allowed for bundle in sorted_bundles
            ),
            blocked_bundles=sum(
                not bundle.dispatch_allowed for bundle in sorted_bundles
            ),
        )
        return ContextPlanResult.create(
            change_set_id=change_set_id,
            blocking_change_ids=blockers,
            primary_question_bindings=bindings,
            candidates=normalized_candidates,
            supporting_segments=sorted_segments,
            relation_edges=edges,
            change_groups=groups,
            bundles=sorted_bundles,
            omitted_candidates=sorted_omissions,
            budget_summary=summary,
            diagnostics=tuple(
                sorted(set(plan_diagnostics), key=_diagnostic_sort_key)
            ),
        )

    def _change_correspondence_edges(
        self,
        units: tuple[ReviewUnit, ...],
        change_set_id: str,
    ) -> tuple[RelationEdge, ...]:
        base_units = [unit for unit in units if unit.source_role == "base"]
        head_units = [unit for unit in units if unit.source_role == "head"]
        edges: list[RelationEdge] = []
        for base_unit in base_units:
            base_atom_ids = set(base_unit.change_atom_ids)
            for head_unit in head_units:
                shared_atom_ids = tuple(
                    sorted(base_atom_ids.intersection(head_unit.change_atom_ids))
                )
                if not shared_atom_ids:
                    continue
                edges.append(
                    RelationEdge.create(
                        source_ref=base_unit.unit_id,
                        target_ref=head_unit.unit_id,
                        relation_type="change_correspondence",
                        strength="strong",
                        quality="exact",
                        evidence_refs=shared_atom_ids,
                        provenance_ref=change_set_id,
                    )
                )
        return tuple(sorted(edges, key=lambda edge: edge.edge_id))

    def _plan_group(
        self,
        *,
        group: ChangeGroup,
        units: tuple[ReviewUnit, ...],
        bindings: tuple[QuestionBinding, ...],
        candidates: tuple[ContextCandidate, ...],
        edges: tuple[RelationEdge, ...],
        candidate_text: Mapping[str, str],
        blockers: tuple[str, ...],
        budget_limit: int,
    ) -> tuple[
        list[SupportingSegment],
        list[CandidateOmission],
        list[ReviewContextBundle],
        list[ContextDiagnostic],
    ]:
        group_primary_ids = set(group.primary_unit_ids)
        group_units = [
            unit for unit in units if unit.unit_id in group_primary_ids
        ]
        primary_tokens = sum(
            estimate_code_tokens(unit.full_text) for unit in group_units
        )
        group_bindings = tuple(
            binding
            for binding in bindings
            if binding.primary_unit_id in group_primary_ids
        )
        group_candidates = tuple(
            sorted(
                (
                    candidate
                    for candidate in candidates
                    if candidate.primary_unit_id in group_primary_ids
                ),
                key=_candidate_sort_key,
            )
        )
        edge_by_id = {edge.edge_id: edge for edge in edges}
        candidate_by_id = {
            candidate.candidate_id: candidate for candidate in group_candidates
        }
        segments: list[SupportingSegment] = []
        omissions: list[CandidateOmission] = []
        bundles: list[ReviewContextBundle] = []
        diagnostics: list[ContextDiagnostic] = []
        segment_by_candidate_id: dict[str, SupportingSegment] = {}

        def omit(
            candidate: ContextCandidate,
            reason: CandidateOmissionReason,
        ) -> None:
            omissions.append(CandidateOmission(candidate.candidate_id, reason))

        def select(candidate: ContextCandidate) -> SupportingSegment:
            existing = segment_by_candidate_id.get(candidate.candidate_id)
            if existing is not None:
                return existing
            selection_reason: ContextSelectionReason = (
                "required_context"
                if candidate.necessity == "required"
                else "helpful_context"
            )
            segment = SupportingSegment.create(
                candidate=candidate,
                source_text=candidate_text[candidate.candidate_id],
                selection_reason=selection_reason,
            )
            segment_by_candidate_id[candidate.candidate_id] = segment
            segments.append(segment)
            return segment

        overflow_primary_ids = (
            group.primary_unit_ids if primary_tokens > budget_limit else ()
        )
        question_ids = sorted(
            {binding.review_question_id for binding in group_bindings}
        )
        for question_id in question_ids:
            question_bindings = tuple(
                binding
                for binding in group_bindings
                if binding.review_question_id == question_id
            )
            question_candidates = tuple(
                candidate
                for candidate in group_candidates
                if candidate.review_question_id == question_id
            )
            required_candidates: list[ContextCandidate] = []
            helpful_candidates: list[ContextCandidate] = []
            required_missing: list[str] = []
            degraded_edge_ids: list[str] = []

            for candidate in question_candidates:
                edge = edge_by_id[candidate.relation_edge_id]
                if candidate.necessity == "distractor" or (
                    candidate.relation_type in _NON_EVIDENCE_RELATIONS
                ):
                    omit(candidate, "distractor_rejected")
                    if candidate.necessity == "required":
                        required_missing.append(candidate.candidate_id)
                    continue
                if edge.quality != "exact":
                    omit(candidate, "relation_degraded")
                    degraded_edge_ids.append(edge.edge_id)
                    if candidate.necessity == "required":
                        required_missing.append(candidate.candidate_id)
                    continue
                if blockers:
                    omit(candidate, "context_blocked")
                    if candidate.necessity == "required":
                        required_missing.append(candidate.candidate_id)
                    continue
                if overflow_primary_ids:
                    omit(candidate, "budget_exceeded")
                    if candidate.necessity == "required":
                        required_missing.append(candidate.candidate_id)
                    continue
                if candidate.necessity == "required":
                    required_candidates.append(candidate)
                else:
                    helpful_candidates.append(candidate)

            selected_required: list[ContextCandidate] = []
            required_tokens = 0
            for candidate in required_candidates:
                if (
                    primary_tokens
                    + required_tokens
                    + candidate.estimated_tokens
                    <= budget_limit
                ):
                    selected_required.append(candidate)
                    required_tokens += candidate.estimated_tokens
                else:
                    omit(candidate, "budget_exceeded")
                    required_missing.append(candidate.candidate_id)

            helpful_bins: list[list[ContextCandidate]] = [[]]
            if required_missing:
                for candidate in helpful_candidates:
                    omit(candidate, "context_blocked")
            else:
                helpful_capacity = budget_limit - primary_tokens - required_tokens
                helpful_bin_tokens = [0]
                for candidate in helpful_candidates:
                    if candidate.estimated_tokens > helpful_capacity:
                        omit(candidate, "budget_exceeded")
                        continue
                    placed = False
                    for index, used_tokens in enumerate(helpful_bin_tokens):
                        if used_tokens + candidate.estimated_tokens <= helpful_capacity:
                            helpful_bins[index].append(candidate)
                            helpful_bin_tokens[index] += candidate.estimated_tokens
                            placed = True
                            break
                    if not placed:
                        helpful_bins.append([candidate])
                        helpful_bin_tokens.append(candidate.estimated_tokens)

            question_diagnostics: list[ContextDiagnostic] = []
            if overflow_primary_ids:
                question_diagnostics.append(
                    ContextDiagnostic(
                        "primary_exceeds_budget",
                        overflow_primary_ids,
                    )
                )
            if degraded_edge_ids:
                question_diagnostics.append(
                    ContextDiagnostic(
                        "relation_degraded",
                        tuple(sorted(set(degraded_edge_ids))),
                    )
                )
            insufficient_subjects = tuple(
                sorted(
                    set((*blockers, *overflow_primary_ids, *required_missing))
                )
            )
            if insufficient_subjects:
                question_diagnostics.append(
                    ContextDiagnostic(
                        "context_insufficient",
                        insufficient_subjects,
                    )
                )
            normalized_diagnostics = tuple(
                sorted(set(question_diagnostics), key=_diagnostic_sort_key)
            )
            diagnostics.extend(normalized_diagnostics)

            required_segments = [
                select(candidate) for candidate in selected_required
            ]
            if required_missing:
                helpful_bins = [[]]
            for helpful_bin in helpful_bins:
                selected_segments = [
                    *required_segments,
                    *(select(candidate) for candidate in helpful_bin),
                ]
                segment_ids = tuple(
                    sorted(segment.segment_id for segment in selected_segments)
                )
                relation_edge_ids = tuple(
                    sorted(
                        set(group.strong_edge_ids).union(
                            candidate_by_id[
                                segment.candidate_id
                            ].relation_edge_id
                            for segment in selected_segments
                        )
                    )
                )
                supporting_tokens = sum(
                    segment.estimated_tokens for segment in selected_segments
                )
                bundle_budget = BundleBudget(
                    limit=budget_limit,
                    primary_tokens=primary_tokens,
                    supporting_tokens=supporting_tokens,
                    total_tokens=primary_tokens + supporting_tokens,
                )
                bundles.append(
                    ReviewContextBundle.create(
                        group_id=group.group_id,
                        primary_unit_ids=group.primary_unit_ids,
                        primary_question_bindings=question_bindings,
                        supporting_segment_ids=segment_ids,
                        relation_edge_ids=relation_edge_ids,
                        budget=bundle_budget,
                        dispatch_allowed=not insufficient_subjects,
                        diagnostics=normalized_diagnostics,
                    )
                )

        return segments, omissions, bundles, diagnostics

    def _validate_primaries(
        self,
        primary_units: Sequence[ReviewUnit],
    ) -> tuple[ReviewUnit, ...]:
        if isinstance(primary_units, str | bytes) or not isinstance(
            primary_units,
            Sequence,
        ):
            raise ValueError("primary_units must be a ReviewUnit sequence")
        units = tuple(primary_units)
        if any(not isinstance(unit, ReviewUnit) for unit in units):
            raise ValueError("primary_units must contain ReviewUnit values")
        for unit in units:
            unit.validate()
            if (
                unit.source_ref_id is None
                or unit.source_role not in {"base", "head"}
                or not unit.change_atom_ids
            ):
                raise ValueError(
                    "Context Planner Primary values must come from review-unit-build-v3"
                )
        unit_ids = [unit.unit_id for unit in units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("primary_units contains duplicate unit_id values")
        return tuple(sorted(units, key=lambda unit: unit.unit_id))

    def _validate_bindings(
        self,
        values: Sequence[QuestionBinding],
        units: tuple[ReviewUnit, ...],
    ) -> tuple[QuestionBinding, ...]:
        bindings = tuple(values)
        if any(not isinstance(item, QuestionBinding) for item in bindings):
            raise ValueError(
                "primary_question_bindings must contain QuestionBinding values"
            )
        bindings = tuple(
            sorted(
                bindings,
                key=lambda item: (item.primary_unit_id, item.review_question_id),
            )
        )
        keys = [
            (item.primary_unit_id, item.review_question_id) for item in bindings
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("primary_question_bindings contains duplicates")
        unit_ids = {unit.unit_id for unit in units}
        bound_ids = {item.primary_unit_id for item in bindings}
        if bound_ids != unit_ids:
            raise ValueError(
                "primary_question_bindings must exactly cover all Primary ReviewUnits"
            )
        return bindings

    def _validate_edges(
        self,
        values: Sequence[RelationEdge],
    ) -> tuple[RelationEdge, ...]:
        edges = tuple(values)
        if any(not isinstance(item, RelationEdge) for item in edges):
            raise ValueError("relation_edges must contain RelationEdge values")
        edges = tuple(sorted(edges, key=lambda item: item.edge_id))
        ids = [item.edge_id for item in edges]
        if len(ids) != len(set(ids)):
            raise ValueError("relation_edges contains duplicate identities")
        return edges

    def _validate_candidates(
        self,
        values: Sequence[ContextCandidate],
    ) -> tuple[ContextCandidate, ...]:
        candidates = tuple(values)
        if any(not isinstance(item, ContextCandidate) for item in candidates):
            raise ValueError("candidates must contain ContextCandidate values")
        candidates = tuple(sorted(candidates, key=lambda item: item.candidate_id))
        ids = [item.candidate_id for item in candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidates contains duplicate identities")
        semantic_keys = [
            (
                item.primary_unit_id,
                item.review_question_id,
                item.target_source_ref_id,
                item.target_span,
            )
            for item in candidates
        ]
        if len(semantic_keys) != len(set(semantic_keys)):
            raise ValueError(
                "candidates contains duplicate Primary/question/target contexts"
            )
        return candidates

    def _validate_snapshots(
        self,
        values: Mapping[str, CodeSourceSnapshot] | Sequence[CodeSourceSnapshot],
        units: tuple[ReviewUnit, ...],
        candidates: tuple[ContextCandidate, ...],
    ) -> dict[str, CodeSourceSnapshot]:
        if isinstance(values, Mapping):
            snapshots = dict(values)
            if any(key != snapshot.source_ref.source_ref_id for key, snapshot in snapshots.items()):
                raise ValueError("source_snapshots mapping keys must match source identities")
        elif isinstance(values, Sequence) and not isinstance(values, str | bytes):
            sequence = tuple(values)
            snapshots = {
                snapshot.source_ref.source_ref_id: snapshot for snapshot in sequence
            }
            if len(snapshots) != len(sequence):
                raise ValueError("source_snapshots contains duplicate source identities")
        else:
            raise ValueError("source_snapshots must be a mapping or sequence")
        if any(
            not isinstance(snapshot, CodeSourceSnapshot)
            for snapshot in snapshots.values()
        ):
            raise ValueError(
                "source_snapshots must contain CodeSourceSnapshot values"
            )
        required_source_ids = {
            unit.source_ref_id for unit in units if unit.source_ref_id is not None
        }.union(candidate.target_source_ref_id for candidate in candidates)
        if set(snapshots) != required_source_ids:
            raise ValueError(
                "source_snapshots must exactly cover Primary and candidate sources"
            )
        return snapshots

    def _validate_primary_sources(
        self,
        units: tuple[ReviewUnit, ...],
        snapshots: Mapping[str, CodeSourceSnapshot],
    ) -> None:
        for unit in units:
            assert unit.source_ref_id is not None
            snapshot = snapshots[unit.source_ref_id]
            if snapshot.source_ref.path != normalize_review_path(unit.file):
                raise ValueError("Primary source path does not match ReviewUnit.file")
            expected = extract_lines(
                snapshot.content,
                unit.context_span.start_line,
                unit.context_span.end_line,
            )
            if unit.full_text != expected:
                raise ValueError(
                    "Primary ReviewUnit.full_text must equal its immutable source slice"
                )

    def _validate_candidate_graph(
        self,
        candidates: tuple[ContextCandidate, ...],
        edges: tuple[RelationEdge, ...],
        bindings: tuple[QuestionBinding, ...],
        units: tuple[ReviewUnit, ...],
        snapshots: Mapping[str, CodeSourceSnapshot],
    ) -> dict[str, str]:
        edge_by_id = {edge.edge_id: edge for edge in edges}
        binding_set = {
            (binding.primary_unit_id, binding.review_question_id)
            for binding in bindings
        }
        unit_ids = {unit.unit_id for unit in units}
        candidate_ids_by_edge: dict[str, list[str]] = {}
        for candidate in candidates:
            candidate_ids_by_edge.setdefault(candidate.relation_edge_id, []).append(
                candidate.candidate_id
            )
        for edge in edges:
            if edge.source_ref not in unit_ids:
                raise ValueError("RelationEdge must originate from a Primary ReviewUnit")
            if edge.target_ref in unit_ids:
                if edge.edge_id in candidate_ids_by_edge:
                    raise ValueError(
                        "Primary-to-Primary RelationEdge cannot back a Supporting candidate"
                    )
            elif edge.edge_id not in candidate_ids_by_edge:
                raise ValueError(
                    "Supporting RelationEdge must be referenced by a ContextCandidate"
                )
        candidate_text: dict[str, str] = {}
        for candidate in candidates:
            if candidate.primary_unit_id not in unit_ids:
                raise ValueError("ContextCandidate references an unknown Primary")
            if (
                candidate.primary_unit_id,
                candidate.review_question_id,
            ) not in binding_set:
                raise ValueError("ContextCandidate has no matching question binding")
            candidate_edge = edge_by_id.get(candidate.relation_edge_id)
            if candidate_edge is None:
                raise ValueError("ContextCandidate references a dangling RelationEdge")
            if (
                candidate_edge.source_ref != candidate.primary_unit_id
                or candidate_edge.target_ref != candidate.target_ref
                or candidate_edge.relation_type != candidate.relation_type
                or candidate.provenance_ref not in candidate_edge.evidence_refs
            ):
                raise ValueError("ContextCandidate relation provenance is inconsistent")
            snapshot = snapshots[candidate.target_source_ref_id]
            text = _slice_exact_range(
                snapshot.content,
                candidate.target_span,
                "ContextCandidate.target_span",
            )
            if not text:
                raise ValueError("ContextCandidate target source must not be empty")
            actual_tokens = estimate_code_tokens(text)
            if candidate.estimated_tokens != actual_tokens:
                raise ValueError(
                    "ContextCandidate.estimated_tokens does not match source span"
                )
            candidate_text[candidate.candidate_id] = text
        return candidate_text

    def _build_groups(
        self,
        units: tuple[ReviewUnit, ...],
        edges: tuple[RelationEdge, ...],
    ) -> tuple[ChangeGroup, ...]:
        unit_ids = tuple(unit.unit_id for unit in units)
        unit_id_set = set(unit_ids)
        union_find = _UnionFind(unit_ids)
        grouping_edges: list[RelationEdge] = []
        for edge in edges:
            source_is_primary = edge.source_ref in unit_id_set
            target_is_primary = edge.target_ref in unit_id_set
            if source_is_primary != target_is_primary:
                if edge.target_ref in unit_id_set:
                    raise ValueError(
                        "Primary-target RelationEdge must start from a Primary"
                    )
                continue
            if not source_is_primary:
                continue
            if (
                edge.strength == "strong"
                and edge.quality == "exact"
                and edge.relation_type not in _NON_EVIDENCE_RELATIONS
            ):
                grouping_edges.append(edge)
                union_find.union(edge.source_ref, edge.target_ref)

        members: dict[str, list[str]] = {}
        for unit_id in unit_ids:
            members.setdefault(union_find.find(unit_id), []).append(unit_id)
        groups: list[ChangeGroup] = []
        for primary_ids in members.values():
            normalized_primary_ids = tuple(sorted(primary_ids))
            strong_edge_ids = tuple(
                sorted(
                    edge.edge_id
                    for edge in grouping_edges
                    if edge.source_ref in normalized_primary_ids
                    and edge.target_ref in normalized_primary_ids
                )
            )
            groups.append(
                ChangeGroup.create(
                    primary_unit_ids=normalized_primary_ids,
                    strong_edge_ids=strong_edge_ids,
                )
            )
        return tuple(sorted(groups, key=lambda group: group.group_id))


def _candidate_sort_key(candidate: ContextCandidate) -> tuple[object, ...]:
    necessity_rank = {"required": 0, "helpful": 1, "distractor": 2}
    return (
        necessity_rank[candidate.necessity],
        candidate.primary_unit_id,
        candidate.review_question_id,
        candidate.target_source_ref_id,
        candidate.target_span.start_line,
        candidate.target_span.start_offset_utf16,
        candidate.target_span.end_line,
        candidate.target_span.end_offset_utf16,
        candidate.candidate_id,
    )


def _diagnostic_sort_key(
    diagnostic: ContextDiagnostic,
) -> tuple[str, tuple[str, ...]]:
    return diagnostic.code, diagnostic.subject_ids


__all__ = [
    "CONTEXT_PLAN_SCHEMA_VERSION",
    "CONTEXT_PLANNER_VERSION",
    "TOKEN_ESTIMATOR_VERSION",
    "BundleBudget",
    "CandidateOmission",
    "ChangeGroup",
    "ContextBudgetSummary",
    "ContextCandidate",
    "ContextDiagnostic",
    "ContextPlanResult",
    "ContextPlanner",
    "QuestionBinding",
    "RelationEdge",
    "ReviewContextBundle",
    "SupportingSegment",
    "context_plan_id",
    "estimate_code_tokens",
    "source_span_ref_id",
]
