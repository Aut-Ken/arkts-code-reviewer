from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, cast

from arkts_code_reviewer.code_analysis.change_set import CodeSourceSnapshot
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    ExactRange,
    OwnerRef,
)
from arkts_code_reviewer.code_analysis.models import (
    HostSummary,
    ReviewUnit,
    ReviewUnitDiagnostic,
    ReviewUnitSpan,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    declaration_unit_id,
    fallback_unit_id,
    normalize_review_path,
)
from arkts_code_reviewer.code_analysis.text_utils import extract_lines

SCHEMA_VERSION = "context-plan-golden-v1"
REPORT_SCHEMA_VERSION = "context-plan-golden-report-v1"
BASELINE_SCHEMA_VERSION = "context-plan-golden-baseline-v1"

_ROOT_FIELDS = {
    "schema_version",
    "suite_id",
    "description",
    "coordinate_system",
    "frozen_contract",
    "cases",
}
_COORDINATE_FIELDS = {
    "line_base",
    "line_end",
    "offset_encoding",
    "offset_base",
    "offset_end",
}
_CONTRACT_FIELDS = {
    "planner_version",
    "token_estimator_version",
    "relation_types",
    "relation_strengths",
    "relation_qualities",
    "candidate_necessities",
    "selection_reasons",
    "omission_reasons",
    "diagnostic_codes",
}
_CASE_FIELDS = {
    "case_id",
    "description",
    "change_set_id",
    "code_context_budget",
    "sources",
    "primaries",
    "candidates",
    "relation_edges",
    "expected",
}
_SOURCE_FIELDS = {
    "alias",
    "file",
    "repository",
    "revision",
    "logical_path",
    "origin_lines",
    "content_sha256",
    "source_ref_id",
}
_PRIMARY_FIELDS = {
    "alias",
    "unit_id",
    "source_alias",
    "source_role",
    "unit_kind",
    "unit_symbol",
    "source_span",
    "context_span",
    "changed_lines",
    "change_atom_ids",
    "owner_kind",
    "owner_ref_id",
    "full_text_sha256",
    "review_question_ids",
}
_CANDIDATE_FIELDS = {
    "alias",
    "candidate_id",
    "primary_alias",
    "review_question_id",
    "relation_edge_alias",
    "relation_type",
    "target_source_alias",
    "target_span",
    "target_owner_kind",
    "target_owner_unit_kind",
    "target_owner_symbol",
    "target_owner_ref_id",
    "estimated_tokens",
    "necessity",
    "provenance_ref",
}
_EDGE_FIELDS = {
    "alias",
    "edge_id",
    "source_primary_alias",
    "target_primary_alias",
    "target_candidate_alias",
    "relation_type",
    "strength",
    "quality",
    "evidence_refs",
    "provenance_ref",
}
_SPAN_FIELDS = {"start_line", "end_line"}
_EXACT_RANGE_FIELDS = {
    "start_line",
    "end_line",
    "start_offset_utf16",
    "end_offset_utf16",
}

_EXPECTED_FIELDS = {
    "schema_version",
    "context_plan_id",
    "planner_version",
    "token_estimator_version",
    "change_set_id",
    "blocking_change_ids",
    "primary_question_bindings",
    "candidates",
    "supporting_segments",
    "relation_edges",
    "change_groups",
    "bundles",
    "omitted_candidate_ids",
    "omitted_candidates",
    "budget_summary",
    "diagnostics",
}
_QUESTION_BINDING_FIELDS = {"primary_unit_id", "review_question_id"}
_EXPECTED_CANDIDATE_FIELDS = {
    "candidate_id",
    "primary_unit_id",
    "review_question_id",
    "relation_edge_id",
    "relation_type",
    "target_source_ref_id",
    "target_span",
    "estimated_tokens",
    "necessity",
    "provenance_ref",
}
_SEGMENT_FIELDS = {
    "segment_id",
    "candidate_id",
    "source_ref_id",
    "source_span",
    "source_text",
    "question_binding",
    "selection_reason",
    "estimated_tokens",
    "diagnostics",
}
_EXPECTED_EDGE_FIELDS = {
    "edge_id",
    "source_ref",
    "target_ref",
    "relation_type",
    "strength",
    "quality",
    "evidence_refs",
    "provenance_ref",
}
_GROUP_FIELDS = {"group_id", "primary_unit_ids", "strong_edge_ids", "diagnostics"}
_BUNDLE_FIELDS = {
    "bundle_id",
    "group_id",
    "primary_unit_ids",
    "primary_question_bindings",
    "supporting_segment_ids",
    "relation_edge_ids",
    "budget",
    "dispatch_allowed",
    "diagnostics",
}
_BUNDLE_BUDGET_FIELDS = {
    "limit",
    "primary_tokens",
    "supporting_tokens",
    "total_tokens",
}
_OMISSION_FIELDS = {"candidate_id", "reason"}
_BUDGET_SUMMARY_FIELDS = {
    "limit",
    "total_primary_tokens",
    "total_supporting_tokens",
    "total_omitted_tokens",
    "max_bundle_tokens",
    "dispatchable_bundles",
    "blocked_bundles",
}
_DIAGNOSTIC_FIELDS = {"code", "subject_ids"}

_RELATION_TYPES = (
    "change_correspondence",
    "direct_call",
    "direct_caller",
    "lifecycle_pair",
    "same_file",
    "same_host",
    "state_access",
)
_RELATION_STRENGTHS = ("strong", "weak")
_RELATION_QUALITIES = ("degraded", "exact")
_NECESSITIES = ("distractor", "helpful", "required")
_SELECTION_REASONS = ("helpful_context", "required_context")
_OMISSION_REASONS = (
    "budget_exceeded",
    "context_blocked",
    "distractor_rejected",
    "relation_degraded",
)
_DIAGNOSTIC_CODES = ("context_insufficient", "primary_exceeds_budget", "relation_degraded")
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_HASH_ID_RE = re.compile(r"^[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}$")
_GOLDEN_CODE_CHUNK_RE = re.compile(
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


@dataclass(frozen=True)
class _SourceFixture:
    alias: str
    source_path: Path
    source: str
    snapshot: CodeSourceSnapshot
    origin_lines: tuple[int, int]


@dataclass(frozen=True)
class _PrimaryFixture:
    alias: str
    unit: ReviewUnit
    question_ids: tuple[str, ...]


@dataclass(frozen=True)
class _CandidateFixture:
    alias: str
    value: Any
    target_text: str


@dataclass(frozen=True)
class _EdgeFixture:
    alias: str
    value: Any


@dataclass(frozen=True)
class ContextGoldenCase:
    case_id: str
    description: str
    change_set_id: str
    code_context_budget: int
    sources: tuple[_SourceFixture, ...]
    primaries: tuple[_PrimaryFixture, ...]
    candidates: tuple[_CandidateFixture, ...]
    edges: tuple[_EdgeFixture, ...]
    expected: dict[str, Any]

    def plan(self, *, permutation: str | None = None) -> Any:
        context = _context_module()
        primaries = tuple(item.unit for item in self.primaries)
        bindings = tuple(
            context.QuestionBinding(item.unit.unit_id, question_id)
            for item in self.primaries
            for question_id in item.question_ids
        )
        snapshots: Any = tuple(item.snapshot for item in self.sources)
        candidates = tuple(item.value for item in self.candidates)
        edges = tuple(item.value for item in self.edges)
        if permutation in {"primaries", "all"}:
            primaries = tuple(reversed(primaries))
        if permutation in {"bindings", "all"}:
            bindings = tuple(reversed(bindings))
        if permutation in {"sources", "all"}:
            snapshots = tuple(reversed(snapshots))
        if permutation == "source_mapping":
            snapshots = tuple(reversed(snapshots))
            snapshots = {
                item.source_ref.source_ref_id: item for item in snapshots
            }
        if permutation in {"candidates", "all"}:
            candidates = tuple(reversed(candidates))
        if permutation in {"edges", "all"}:
            edges = tuple(reversed(edges))
        return context.ContextPlanner().plan(
            change_set_id=self.change_set_id,
            primary_units=primaries,
            primary_question_bindings=bindings,
            source_snapshots=snapshots,
            candidates=candidates,
            relation_edges=edges,
            code_context_budget=self.code_context_budget,
        )


@dataclass(frozen=True)
class ContextGoldenSuite:
    suite_id: str
    manifest_path: Path
    manifest_sha256: str
    cases: tuple[ContextGoldenCase, ...]


def load_golden_suite(manifest_path: str | Path) -> ContextGoldenSuite:
    unresolved = Path(manifest_path)
    if unresolved.is_symlink():
        raise ValueError("manifest must not be a symlink")
    path = unresolved.resolve()
    raw = _read_regular_file(path, "manifest")
    data = _json_object(raw, str(path))
    _exact_fields(data, _ROOT_FIELDS, "manifest")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"manifest.schema_version must be {SCHEMA_VERSION!r}")
    suite_id = _text(data["suite_id"], "manifest.suite_id")
    _text(data["description"], "manifest.description")
    _validate_coordinate_system(data["coordinate_system"])
    _validate_frozen_contract(data["frozen_contract"])

    raw_cases = _array(data["cases"], "manifest.cases")
    if len(raw_cases) != 16:
        raise ValueError("manifest.cases must contain exactly 16 RU-5 cases")
    root = path.parent
    cases: list[ContextGoldenCase] = []
    seen_ids: set[str] = set()
    seen_semantics: set[str] = set()
    for index, value in enumerate(raw_cases):
        context = f"manifest.cases[{index}]"
        case, semantic = _load_case(_object(value, context), root, context)
        if case.case_id in seen_ids:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        if semantic in seen_semantics:
            raise ValueError(f"duplicate semantic Context Golden case: {case.case_id}")
        seen_ids.add(case.case_id)
        seen_semantics.add(semantic)
        cases.append(case)
    expected_ids = [f"CP{index:03d}" for index in range(1, 17)]
    if [case.case_id for case in cases] != expected_ids:
        raise ValueError("manifest must freeze the CP001-CP016 case matrix in order")
    return ContextGoldenSuite(
        suite_id=suite_id,
        manifest_path=path,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        cases=tuple(cases),
    )


def _load_case(
    data: dict[str, Any],
    root: Path,
    context: str,
) -> tuple[ContextGoldenCase, str]:
    _exact_fields(data, _CASE_FIELDS, context)
    case_id = _text(data["case_id"], f"{context}.case_id")
    description = _text(data["description"], f"{context}.description")
    change_set_id = _hash_id(data["change_set_id"], "change-set", f"{context}.change_set_id")
    budget = _positive(data["code_context_budget"], f"{context}.code_context_budget")

    sources: list[_SourceFixture] = []
    source_by_alias: dict[str, _SourceFixture] = {}
    for index, value in enumerate(_array(data["sources"], f"{context}.sources")):
        item_context = f"{context}.sources[{index}]"
        source = _load_source(_object(value, item_context), root, item_context)
        if source.alias in source_by_alias:
            raise ValueError(f"duplicate source alias: {source.alias}")
        source_by_alias[source.alias] = source
        sources.append(source)
    _sorted_aliases([item.alias for item in sources], f"{context}.sources")
    source_ref_ids = [item.snapshot.source_ref.source_ref_id for item in sources]
    if len(source_ref_ids) != len(set(source_ref_ids)):
        raise ValueError(f"{context}.sources contain duplicate source identities")

    primaries: list[_PrimaryFixture] = []
    primary_by_alias: dict[str, _PrimaryFixture] = {}
    for index, value in enumerate(_array(data["primaries"], f"{context}.primaries")):
        item_context = f"{context}.primaries[{index}]"
        primary = _load_primary(
            _object(value, item_context), source_by_alias, item_context
        )
        if primary.alias in primary_by_alias:
            raise ValueError(f"duplicate Primary alias: {primary.alias}")
        primary_by_alias[primary.alias] = primary
        primaries.append(primary)
    if not primaries:
        raise ValueError(f"{context}.primaries must not be empty")
    _sorted_aliases([item.alias for item in primaries], f"{context}.primaries")
    primary_ids = [item.unit.unit_id for item in primaries]
    if len(primary_ids) != len(set(primary_ids)):
        raise ValueError(f"{context}.primaries contain duplicate Unit identities")

    raw_candidates = [
        _object(value, f"{context}.candidates[{index}]")
        for index, value in enumerate(_array(data["candidates"], f"{context}.candidates"))
    ]
    raw_edges = [
        _object(value, f"{context}.relation_edges[{index}]")
        for index, value in enumerate(
            _array(data["relation_edges"], f"{context}.relation_edges")
        )
    ]
    candidates, candidate_by_alias = _load_candidates(
        raw_candidates, raw_edges, source_by_alias, primary_by_alias, context
    )
    explicit_edges = _load_edges(
        raw_edges,
        primary_by_alias,
        candidate_by_alias,
        context,
    )
    edge_aliases = {item.alias for item in explicit_edges}
    for candidate in candidates:
        edge_id = candidate.value.relation_edge_id
        if edge_id not in {item.value.edge_id for item in explicit_edges}:
            raise ValueError(f"{context} candidate has dangling relation_edge_id")
    if len(edge_aliases) != len(explicit_edges):
        raise ValueError(f"{context}.relation_edges aliases must be unique")
    derived_edges = _derive_change_correspondence_edges(
        primaries, change_set_id, context
    )
    edges = sorted(
        (*explicit_edges, *derived_edges), key=lambda item: item.value.edge_id
    )
    if len({item.value.edge_id for item in edges}) != len(edges):
        raise ValueError(f"{context} explicit edges collide with derived correspondence")
    used_source_ids = {
        item.unit.source_ref_id for item in primaries if item.unit.source_ref_id is not None
    }.union(item.value.target_source_ref_id for item in candidates)
    if used_source_ids != set(source_ref_ids):
        raise ValueError(f"{context}.sources must exactly cover Primary and candidate sources")

    expected = _load_expected(
        _object(data["expected"], f"{context}.expected"),
        change_set_id=change_set_id,
        budget=budget,
        sources=sources,
        primaries=primaries,
        candidates=candidates,
        edges=edges,
        context=f"{context}.expected",
    )
    semantic = _canonical(
        {
            "change_set_id": change_set_id,
            "budget": budget,
            "source_refs": [item.snapshot.source_ref.source_ref_id for item in sources],
            "primary_ids": [item.unit.unit_id for item in primaries],
            "candidate_ids": [item.value.candidate_id for item in candidates],
            "edge_ids": [item.value.edge_id for item in edges],
            "expected": expected,
        }
    )
    return (
        ContextGoldenCase(
            case_id=case_id,
            description=description,
            change_set_id=change_set_id,
            code_context_budget=budget,
            sources=tuple(sources),
            primaries=tuple(primaries),
            candidates=tuple(candidates),
            edges=tuple(explicit_edges),
            expected=expected,
        ),
        semantic,
    )


def _load_source(data: dict[str, Any], root: Path, context: str) -> _SourceFixture:
    _exact_fields(data, _SOURCE_FIELDS, context)
    alias = _alias(data["alias"], f"{context}.alias")
    source_path = _safe_child(root, _text(data["file"], f"{context}.file"), context)
    raw = _read_regular_file(source_path, f"{context}.file")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{context}.file must be UTF-8") from exc
    digest = _sha(data["content_sha256"], f"{context}.content_sha256")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"{context} source hash/provenance drift")
    repository = _text(data["repository"], f"{context}.repository")
    revision = _text(data["revision"], f"{context}.revision")
    logical_path = _logical_path(data["logical_path"], f"{context}.logical_path")
    source_ref = CodeSourceRef.create(
        repository=repository,
        revision=revision,
        path=logical_path,
        content_hash=f"sha256:{digest}",
    )
    frozen_id = _hash_id(
        data["source_ref_id"], "code-source", f"{context}.source_ref_id"
    )
    expected_source_id = _golden_stable_id(
        "code-source",
        {
            "repository": repository,
            "revision": revision,
            "path": logical_path,
            "content_hash": f"sha256:{digest}",
        },
    )
    if frozen_id != expected_source_id or source_ref.source_ref_id != expected_source_id:
        raise ValueError(f"{context}.source_ref_id provenance drift")
    line_count = len(source.splitlines(keepends=True))
    origin_lines = _origin_lines(data["origin_lines"], line_count, context)
    return _SourceFixture(
        alias=alias,
        source_path=source_path,
        source=source,
        snapshot=CodeSourceSnapshot(source_ref, source),
        origin_lines=origin_lines,
    )


def _load_primary(
    data: dict[str, Any],
    sources: dict[str, _SourceFixture],
    context: str,
) -> _PrimaryFixture:
    _exact_fields(data, _PRIMARY_FIELDS, context)
    alias = _alias(data["alias"], f"{context}.alias")
    source_alias = _alias(data["source_alias"], f"{context}.source_alias")
    if source_alias not in sources:
        raise ValueError(f"{context}.source_alias is dangling")
    fixture = sources[source_alias]
    source_role = _enum(data["source_role"], {"base", "head"}, f"{context}.source_role")
    unit_kind = _enum(
        data["unit_kind"],
        {
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
        },
        f"{context}.unit_kind",
    )
    unit_symbol = _text(data["unit_symbol"], f"{context}.unit_symbol")
    source_span = _line_span(data["source_span"], fixture.source, f"{context}.source_span")
    context_span = _line_span(
        data["context_span"], fixture.source, f"{context}.context_span"
    )
    if not (
        context_span.start_line <= source_span.start_line
        and source_span.end_line <= context_span.end_line
    ):
        raise ValueError(f"{context}.source_span must be inside context_span")
    changed_lines = _lines(data["changed_lines"], f"{context}.changed_lines")
    if not changed_lines or any(not context_span.contains_line(line) for line in changed_lines):
        raise ValueError(f"{context}.changed_lines must be non-empty and inside context_span")
    atom_ids = tuple(
        _hash_id(value, "change-atom", f"{context}.change_atom_ids[{index}]")
        for index, value in enumerate(
            _array(data["change_atom_ids"], f"{context}.change_atom_ids")
        )
    )
    if not atom_ids or list(atom_ids) != sorted(set(atom_ids)):
        raise ValueError(f"{context}.change_atom_ids must be sorted and unique")
    owner_kind = _nullable_text(data["owner_kind"], f"{context}.owner_kind")
    raw_owner_ref_id = _nullable_text(
        data["owner_ref_id"], f"{context}.owner_ref_id"
    )
    if unit_kind == "fallback":
        if owner_kind is not None or raw_owner_ref_id is not None:
            raise ValueError(f"{context} fallback must not invent an owner")
        expected_unit_id = fallback_unit_id(
            fixture.snapshot.source_ref.path,
            source_span.start_line,
            source_span.end_line,
            context_span.start_line,
            context_span.end_line,
            source_role=cast(Any, source_role),
            source_ref_id=fixture.snapshot.source_ref.source_ref_id,
        )
        owner_ref = None
        selection_reason = "fallback_window"
        context_degraded = True
        diagnostics = [ReviewUnitDiagnostic("no_matching_declaration")]
    else:
        expected_owner_kind = (
            "region" if unit_kind in {"field_region", "import_region"} else "declaration"
        )
        if owner_kind != expected_owner_kind or raw_owner_ref_id is None:
            raise ValueError(f"{context}.owner_kind must match Unit kind")
        owner_ref_id = _hash_id(
            raw_owner_ref_id, expected_owner_kind, f"{context}.owner_ref_id"
        )
        owner_ref = OwnerRef(cast(Any, expected_owner_kind), owner_ref_id)
        expected_unit_id = declaration_unit_id(
            fixture.snapshot.source_ref.path,
            cast(Any, unit_kind),
            unit_symbol,
            source_span.start_line,
            source_span.end_line,
            source_role=cast(Any, source_role),
            source_ref_id=fixture.snapshot.source_ref.source_ref_id,
        )
        selection_reason = "innermost_changed_declaration"
        context_degraded = False
        diagnostics = []
    unit_id = _text(data["unit_id"], f"{context}.unit_id")
    if unit_id != expected_unit_id:
        raise ValueError(f"{context}.unit_id identity drift")
    full_text = extract_lines(
        fixture.source, context_span.start_line, context_span.end_line
    )
    digest = _sha(data["full_text_sha256"], f"{context}.full_text_sha256")
    if hashlib.sha256(full_text.encode("utf-8")).hexdigest() != digest:
        raise ValueError(f"{context}.full_text hash/source-span drift")
    question_ids = tuple(
        _text(value, f"{context}.review_question_ids[{index}]")
        for index, value in enumerate(
            _array(data["review_question_ids"], f"{context}.review_question_ids")
        )
    )
    if not question_ids or list(question_ids) != sorted(set(question_ids)):
        raise ValueError(f"{context}.review_question_ids must be sorted and unique")
    relative_lines = [line - context_span.start_line + 1 for line in changed_lines]
    unit = ReviewUnit(
        file=fixture.snapshot.source_ref.path,
        unit_symbol=unit_symbol,
        unit_ref=f"{unit_symbol}@{fixture.snapshot.source_ref.path}",
        full_text=full_text,
        changed_lines=list(changed_lines),
        file_changed_lines=list(changed_lines),
        unit_changed_lines=relative_lines,
        host_summary=HostSummary(),
        context_degraded=context_degraded,
        unit_id=unit_id,
        unit_kind=cast(Any, unit_kind),
        source_span=source_span,
        context_span=context_span,
        changed_new_lines=list(changed_lines) if source_role == "head" else [],
        changed_old_lines=list(changed_lines) if source_role == "base" else [],
        selection_reason=cast(Any, selection_reason),
        diagnostics=diagnostics,
        source_ref_id=fixture.snapshot.source_ref.source_ref_id,
        source_role=cast(Any, source_role),
        change_atom_ids=list(atom_ids),
        owner_ref=owner_ref,
        identity_source_ref_id=fixture.snapshot.source_ref.source_ref_id,
    )
    return _PrimaryFixture(alias=alias, unit=unit, question_ids=question_ids)


def _load_candidates(
    values: list[dict[str, Any]],
    raw_edges: list[dict[str, Any]],
    sources: dict[str, _SourceFixture],
    primaries: dict[str, _PrimaryFixture],
    context: str,
) -> tuple[list[_CandidateFixture], dict[str, _CandidateFixture]]:
    edge_ids: dict[str, str] = {}
    for index, edge in enumerate(raw_edges):
        item_context = f"{context}.relation_edges[{index}]"
        _exact_fields(edge, _EDGE_FIELDS, item_context)
        alias = _alias(edge["alias"], f"{item_context}.alias")
        if alias in edge_ids:
            raise ValueError(f"duplicate RelationEdge alias: {alias}")
        edge_ids[alias] = _hash_id(
            edge["edge_id"], "relation-edge", f"{item_context}.edge_id"
        )

    context_module = _context_module()
    fixtures: list[_CandidateFixture] = []
    by_alias: dict[str, _CandidateFixture] = {}
    for index, data in enumerate(values):
        item_context = f"{context}.candidates[{index}]"
        _exact_fields(data, _CANDIDATE_FIELDS, item_context)
        alias = _alias(data["alias"], f"{item_context}.alias")
        if alias in by_alias:
            raise ValueError(f"duplicate ContextCandidate alias: {alias}")
        primary_alias = _alias(data["primary_alias"], f"{item_context}.primary_alias")
        if primary_alias not in primaries:
            raise ValueError(f"{item_context}.primary_alias is dangling")
        primary = primaries[primary_alias]
        question_id = _text(
            data["review_question_id"], f"{item_context}.review_question_id"
        )
        if question_id not in primary.question_ids:
            raise ValueError(f"{item_context}.review_question_id is not bound to Primary")
        edge_alias = _alias(
            data["relation_edge_alias"], f"{item_context}.relation_edge_alias"
        )
        if edge_alias not in edge_ids:
            raise ValueError(f"{item_context}.relation_edge_alias is dangling")
        relation_type = _enum(
            data["relation_type"], set(_RELATION_TYPES), f"{item_context}.relation_type"
        )
        source_alias = _alias(
            data["target_source_alias"], f"{item_context}.target_source_alias"
        )
        if source_alias not in sources:
            raise ValueError(f"{item_context}.target_source_alias is dangling")
        source = sources[source_alias]
        target_span = _exact_range(
            data["target_span"], source.source, f"{item_context}.target_span"
        )
        target_owner_kind = _enum(
            data["target_owner_kind"],
            {"declaration", "region"},
            f"{item_context}.target_owner_kind",
        )
        if target_owner_kind != "declaration":
            raise ValueError(
                f"{item_context} first Context Golden only supports declaration boundaries"
            )
        target_owner_unit_kind = _enum(
            data["target_owner_unit_kind"],
            {"function", "method", "build_method", "builder", "struct", "class", "ui_block"},
            f"{item_context}.target_owner_unit_kind",
        )
        target_owner_symbol = _text(
            data["target_owner_symbol"], f"{item_context}.target_owner_symbol"
        )
        target_owner_ref_id = _hash_id(
            data["target_owner_ref_id"],
            "declaration",
            f"{item_context}.target_owner_ref_id",
        )
        expected_owner_id = _golden_stable_id(
            "declaration",
            {
                "source_ref_id": source.snapshot.source_ref.source_ref_id,
                "kind": target_owner_unit_kind,
                "qualified_name": target_owner_symbol,
                "start_line": target_span.start_line,
                "end_line": target_span.end_line,
                "start_offset_utf16": target_span.start_offset_utf16,
                "end_offset_utf16": target_span.end_offset_utf16,
            },
        )
        if target_owner_ref_id != expected_owner_id:
            raise ValueError(f"{item_context}.target_owner_ref_id identity drift")
        target_text = _slice_exact(source.source, target_span)
        estimated_tokens = _positive(
            data["estimated_tokens"], f"{item_context}.estimated_tokens"
        )
        actual_tokens = _golden_estimate_code_tokens(target_text)
        if estimated_tokens != actual_tokens:
            raise ValueError(f"{item_context}.estimated_tokens drift from source/span")
        necessity = _enum(
            data["necessity"], set(_NECESSITIES), f"{item_context}.necessity"
        )
        candidate_id = _hash_id(
            data["candidate_id"], "context-candidate", f"{item_context}.candidate_id"
        )
        candidate_payload = {
            "primary_unit_id": primary.unit.unit_id,
            "review_question_id": question_id,
            "relation_edge_id": edge_ids[edge_alias],
            "relation_type": relation_type,
            "target_source_ref_id": source.snapshot.source_ref.source_ref_id,
            "target_span": _range_dict(target_span),
            "estimated_tokens": estimated_tokens,
            "necessity": necessity,
            "provenance_ref": target_owner_ref_id,
        }
        if data["provenance_ref"] != target_owner_ref_id:
            raise ValueError(f"{item_context}.provenance_ref must identify target owner")
        if candidate_id != _golden_stable_id("context-candidate", candidate_payload):
            raise ValueError(f"{item_context}.candidate_id identity drift")
        candidate = context_module.ContextCandidate(
            candidate_id=candidate_id,
            primary_unit_id=primary.unit.unit_id,
            review_question_id=question_id,
            relation_edge_id=edge_ids[edge_alias],
            relation_type=relation_type,
            target_source_ref_id=source.snapshot.source_ref.source_ref_id,
            target_span=target_span,
            estimated_tokens=estimated_tokens,
            necessity=necessity,
            provenance_ref=candidate_payload["provenance_ref"],
        )
        fixture = _CandidateFixture(alias=alias, value=candidate, target_text=target_text)
        fixtures.append(fixture)
        by_alias[alias] = fixture
    _sorted_aliases([item.alias for item in fixtures], f"{context}.candidates")
    candidate_ids = [item.value.candidate_id for item in fixtures]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError(f"{context}.candidates contain duplicate identities")
    return fixtures, by_alias


def _load_edges(
    values: list[dict[str, Any]],
    primaries: dict[str, _PrimaryFixture],
    candidates: dict[str, _CandidateFixture],
    context: str,
) -> list[_EdgeFixture]:
    context_module = _context_module()
    result: list[_EdgeFixture] = []
    aliases: set[str] = set()
    for index, data in enumerate(values):
        item_context = f"{context}.relation_edges[{index}]"
        _exact_fields(data, _EDGE_FIELDS, item_context)
        alias = _alias(data["alias"], f"{item_context}.alias")
        if alias in aliases:
            raise ValueError(f"duplicate RelationEdge alias: {alias}")
        aliases.add(alias)
        source_alias = _alias(
            data["source_primary_alias"], f"{item_context}.source_primary_alias"
        )
        if source_alias not in primaries:
            raise ValueError(f"{item_context}.source_primary_alias is dangling")
        target_primary = _nullable_alias(
            data["target_primary_alias"], f"{item_context}.target_primary_alias"
        )
        target_candidate = _nullable_alias(
            data["target_candidate_alias"], f"{item_context}.target_candidate_alias"
        )
        if (target_primary is None) == (target_candidate is None):
            raise ValueError(f"{item_context} requires exactly one target alias")
        if target_primary is not None:
            if target_primary not in primaries:
                raise ValueError(f"{item_context}.target_primary_alias is dangling")
            target_ref = primaries[target_primary].unit.unit_id
        else:
            assert target_candidate is not None
            if target_candidate not in candidates:
                raise ValueError(f"{item_context}.target_candidate_alias is dangling")
            candidate = candidates[target_candidate].value
            target_ref = _golden_source_span_ref_id(
                candidate.target_source_ref_id, candidate.target_span
            )
        edge_id = _hash_id(data["edge_id"], "relation-edge", f"{item_context}.edge_id")
        relation_type = _enum(
            data["relation_type"],
            set(_RELATION_TYPES),
            f"{item_context}.relation_type",
        )
        strength = _enum(
            data["strength"],
            set(_RELATION_STRENGTHS),
            f"{item_context}.strength",
        )
        quality = _enum(
            data["quality"],
            set(_RELATION_QUALITIES),
            f"{item_context}.quality",
        )
        evidence_refs = _strings(
            data["evidence_refs"], f"{item_context}.evidence_refs", allow_empty=False
        )
        provenance_ref = _text(
            data["provenance_ref"], f"{item_context}.provenance_ref"
        )
        edge_payload = {
            "source_ref": primaries[source_alias].unit.unit_id,
            "target_ref": target_ref,
            "relation_type": relation_type,
            "strength": strength,
            "quality": quality,
            "evidence_refs": list(evidence_refs),
            "provenance_ref": provenance_ref,
        }
        if edge_id != _golden_stable_id("relation-edge", edge_payload):
            raise ValueError(f"{item_context}.edge_id identity drift")
        edge = context_module.RelationEdge(
            edge_id=edge_id,
            source_ref=primaries[source_alias].unit.unit_id,
            target_ref=target_ref,
            relation_type=relation_type,
            strength=strength,
            quality=quality,
            evidence_refs=evidence_refs,
            provenance_ref=provenance_ref,
        )
        result.append(_EdgeFixture(alias=alias, value=edge))
    _sorted_aliases([item.alias for item in result], f"{context}.relation_edges")
    edge_ids = [item.value.edge_id for item in result]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError(f"{context}.relation_edges contain duplicate identities")

    edge_by_id = {item.value.edge_id: item.value for item in result}
    for candidate in candidates.values():
        edge = edge_by_id.get(candidate.value.relation_edge_id)
        if edge is None:
            raise ValueError(f"{context} candidate has dangling RelationEdge")
        expected_target = _golden_source_span_ref_id(
            candidate.value.target_source_ref_id,
            candidate.value.target_span,
        )
        if (
            edge.source_ref != candidate.value.primary_unit_id
            or edge.target_ref != expected_target
            or edge.relation_type != candidate.value.relation_type
            or candidate.value.provenance_ref not in edge.evidence_refs
        ):
            raise ValueError(f"{context} candidate RelationEdge provenance drift")
    return result


def _derive_change_correspondence_edges(
    primaries: list[_PrimaryFixture],
    change_set_id: str,
    context: str,
) -> list[_EdgeFixture]:
    context_module = _context_module()
    base_units = sorted(
        (item.unit for item in primaries if item.unit.source_role == "base"),
        key=lambda item: item.unit_id,
    )
    head_units = sorted(
        (item.unit for item in primaries if item.unit.source_role == "head"),
        key=lambda item: item.unit_id,
    )
    result: list[_EdgeFixture] = []
    for base_unit in base_units:
        for head_unit in head_units:
            shared_atom_ids = tuple(
                sorted(set(base_unit.change_atom_ids).intersection(head_unit.change_atom_ids))
            )
            if not shared_atom_ids:
                continue
            payload = {
                "source_ref": base_unit.unit_id,
                "target_ref": head_unit.unit_id,
                "relation_type": "change_correspondence",
                "strength": "strong",
                "quality": "exact",
                "evidence_refs": list(shared_atom_ids),
                "provenance_ref": change_set_id,
            }
            edge_id = _golden_stable_id("relation-edge", payload)
            edge = context_module.RelationEdge(
                edge_id=edge_id,
                source_ref=base_unit.unit_id,
                target_ref=head_unit.unit_id,
                relation_type="change_correspondence",
                strength="strong",
                quality="exact",
                evidence_refs=shared_atom_ids,
                provenance_ref=change_set_id,
            )
            result.append(
                _EdgeFixture(
                    alias=f"derived_{len(result):03d}",
                    value=edge,
                )
            )
    edge_ids = [item.value.edge_id for item in result]
    if edge_ids != sorted(set(edge_ids)):
        result.sort(key=lambda item: item.value.edge_id)
        edge_ids = [item.value.edge_id for item in result]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError(f"{context} duplicate derived change correspondence")
    return result


def _load_expected(
    data: dict[str, Any],
    *,
    change_set_id: str,
    budget: int,
    sources: list[_SourceFixture],
    primaries: list[_PrimaryFixture],
    candidates: list[_CandidateFixture],
    edges: list[_EdgeFixture],
    context: str,
) -> dict[str, Any]:
    _exact_fields(data, _EXPECTED_FIELDS, context)
    context_plan_id = _hash_id(
        data["context_plan_id"], "context-plan", f"{context}.context_plan_id"
    )
    if data["schema_version"] != "context-plan-v1":
        raise ValueError(f"{context}.schema_version drift")
    if data["planner_version"] != "context-planner-v1":
        raise ValueError(f"{context}.planner_version drift")
    if data["token_estimator_version"] != "arkts-code-token-v1":
        raise ValueError(f"{context}.token_estimator_version drift")
    if data["change_set_id"] != change_set_id:
        raise ValueError(f"{context}.change_set_id drift")
    blocking_context = f"{context}.blocking_change_ids"
    blocking_change_ids = list(_strings(data["blocking_change_ids"], blocking_context))
    for index, blocker in enumerate(blocking_change_ids):
        item_context = f"{blocking_context}[{index}]"
        if blocker.startswith("change-atom:sha256:"):
            _hash_id(blocker, "change-atom", item_context)
        elif blocker.startswith("changed-file:sha256:"):
            _hash_id(blocker, "changed-file", item_context)
        else:
            raise ValueError(
                f"{item_context} must use a ChangeAtom or ChangedFile identity"
            )

    bindings = _question_bindings(
        data["primary_question_bindings"], f"{context}.primary_question_bindings"
    )
    expected_bindings = sorted(
        {
            (item.unit.unit_id, question_id)
            for item in primaries
            for question_id in item.question_ids
        }
    )
    if [(item["primary_unit_id"], item["review_question_id"]) for item in bindings] != (
        expected_bindings
    ):
        raise ValueError(f"{context}.primary_question_bindings do not cover all Primaries")

    expected_candidates = _expected_candidates(
        data["candidates"], f"{context}.candidates"
    )
    input_candidates = sorted(
        (_public_dict(item.value) for item in candidates),
        key=lambda item: item["candidate_id"],
    )
    if expected_candidates != input_candidates:
        raise ValueError(f"{context}.candidates input/provenance drift")

    expected_edges = _expected_edges(data["relation_edges"], f"{context}.relation_edges")
    input_edges = sorted(
        (_public_dict(item.value) for item in edges), key=lambda item: item["edge_id"]
    )
    if expected_edges != input_edges:
        raise ValueError(f"{context}.relation_edges input/provenance drift")

    candidate_by_id = {item.value.candidate_id: item for item in candidates}
    source_by_id = {item.snapshot.source_ref.source_ref_id: item for item in sources}
    segment_values = _array(data["supporting_segments"], f"{context}.supporting_segments")
    segments: list[dict[str, Any]] = []
    selected_candidate_ids: set[str] = set()
    for index, value in enumerate(segment_values):
        item_context = f"{context}.supporting_segments[{index}]"
        item = _object(value, item_context)
        _exact_fields(item, _SEGMENT_FIELDS, item_context)
        segment_id = _hash_id(
            item["segment_id"], "supporting-segment", f"{item_context}.segment_id"
        )
        candidate_id = _hash_id(
            item["candidate_id"], "context-candidate", f"{item_context}.candidate_id"
        )
        if candidate_id not in candidate_by_id or candidate_id in selected_candidate_ids:
            raise ValueError(f"{item_context}.candidate_id is dangling or duplicated")
        selected_candidate_ids.add(candidate_id)
        fixture = candidate_by_id[candidate_id]
        candidate = fixture.value
        source_ref_id = _hash_id(
            item["source_ref_id"], "code-source", f"{item_context}.source_ref_id"
        )
        if source_ref_id != candidate.target_source_ref_id or source_ref_id not in source_by_id:
            raise ValueError(f"{item_context}.source_ref_id candidate provenance drift")
        span = _expected_exact_range(item["source_span"], f"{item_context}.source_span")
        if span != _public_dict(candidate.target_span):
            raise ValueError(f"{item_context}.source_span candidate provenance drift")
        source_text = item["source_text"]
        if not isinstance(source_text, str) or source_text != fixture.target_text:
            raise ValueError(f"{item_context}.source_text is not the exact source/span slice")
        binding = _question_binding(
            item["question_binding"], f"{item_context}.question_binding"
        )
        if binding != {
            "primary_unit_id": candidate.primary_unit_id,
            "review_question_id": candidate.review_question_id,
        }:
            raise ValueError(f"{item_context}.question_binding candidate drift")
        selection_reason = _enum(
            item["selection_reason"],
            set(_SELECTION_REASONS),
            f"{item_context}.selection_reason",
        )
        expected_reason = (
            "required_context" if candidate.necessity == "required" else "helpful_context"
        )
        if candidate.necessity == "distractor" or selection_reason != expected_reason:
            raise ValueError(f"{item_context}.selection_reason contradicts candidate necessity")
        estimated = _positive(item["estimated_tokens"], f"{item_context}.estimated_tokens")
        if estimated != candidate.estimated_tokens:
            raise ValueError(f"{item_context}.estimated_tokens candidate drift")
        diagnostics = _diagnostics(item["diagnostics"], f"{item_context}.diagnostics")
        if diagnostics:
            raise ValueError(f"{item_context}.diagnostics are not part of v1 selection")
        expected_segment_id = _golden_stable_id(
            "supporting-segment",
            {
                "candidate_id": candidate_id,
                "source_ref_id": source_ref_id,
                "source_span": span,
                "source_text_sha256": hashlib.sha256(
                    source_text.encode("utf-8")
                ).hexdigest(),
                "question_binding": binding,
                "selection_reason": selection_reason,
                "estimated_tokens": estimated,
                "diagnostics": diagnostics,
            },
        )
        if segment_id != expected_segment_id:
            raise ValueError(f"{item_context}.segment_id identity drift")
        segments.append(
            {
                "segment_id": segment_id,
                "candidate_id": candidate_id,
                "source_ref_id": source_ref_id,
                "source_span": span,
                "source_text": source_text,
                "question_binding": binding,
                "selection_reason": selection_reason,
                "estimated_tokens": estimated,
                "diagnostics": diagnostics,
            }
        )
    _require_unique_stable_order(
        [item["segment_id"] for item in segments], f"{context}.supporting_segments"
    )

    omitted_ids = list(
        _strings(data["omitted_candidate_ids"], f"{context}.omitted_candidate_ids")
    )
    all_candidate_ids = {item.value.candidate_id for item in candidates}
    if set(omitted_ids) != all_candidate_ids - selected_candidate_ids:
        raise ValueError(f"{context}.omitted_candidate_ids do not complement selected support")
    omissions = _omissions(data["omitted_candidates"], f"{context}.omitted_candidates")
    if [item["candidate_id"] for item in omissions] != omitted_ids:
        raise ValueError(f"{context}.omitted_candidates IDs/reasons drift")
    for omission in omissions:
        candidate = candidate_by_id[omission["candidate_id"]].value
        if candidate.necessity == "distractor" and omission["reason"] != "distractor_rejected":
            raise ValueError(f"{context} distractor must use distractor_rejected")

    groups = _groups(data["change_groups"], f"{context}.change_groups")
    _validate_group_truth(groups, primaries, edges, context)
    _validate_candidate_selection_truth(
        groups=groups,
        candidates=candidate_by_id,
        edges=edges,
        primaries=primaries,
        selected_candidate_ids=selected_candidate_ids,
        omissions=omissions,
        budget=budget,
        context=context,
    )
    bundles = _bundles(data["bundles"], f"{context}.bundles")
    _validate_bundle_truth(
        bundles,
        groups=groups,
        bindings=bindings,
        segments=segments,
        edges=expected_edges,
        candidates=candidate_by_id,
        omitted=omissions,
        primaries=primaries,
        budget=budget,
        context=context,
    )
    summary = _budget_summary(data["budget_summary"], f"{context}.budget_summary")
    _validate_budget_summary(summary, bundles, omissions, candidate_by_id, budget, context)
    diagnostics = _diagnostics(data["diagnostics"], f"{context}.diagnostics")
    expected_plan_diagnostics = sorted(
        {
            (item["code"], tuple(item["subject_ids"]))
            for bundle in bundles
            for item in bundle["diagnostics"]
        }
    )
    if diagnostics != [
        {"code": code, "subject_ids": list(subject_ids)}
        for code, subject_ids in expected_plan_diagnostics
    ]:
        raise ValueError(f"{context}.diagnostics must equal bundle diagnostic union")

    projection = {
        "schema_version": data["schema_version"],
        "context_plan_id": context_plan_id,
        "planner_version": data["planner_version"],
        "token_estimator_version": data["token_estimator_version"],
        "change_set_id": change_set_id,
        "blocking_change_ids": blocking_change_ids,
        "primary_question_bindings": bindings,
        "candidates": expected_candidates,
        "supporting_segments": segments,
        "relation_edges": expected_edges,
        "change_groups": groups,
        "bundles": bundles,
        "omitted_candidate_ids": omitted_ids,
        "omitted_candidates": omissions,
        "budget_summary": summary,
        "diagnostics": diagnostics,
    }
    _validate_plan_id(projection, context)
    return projection


def _question_binding(value: Any, context: str) -> dict[str, str]:
    data = _object(value, context)
    _exact_fields(data, _QUESTION_BINDING_FIELDS, context)
    return {
        "primary_unit_id": _text(data["primary_unit_id"], f"{context}.primary_unit_id"),
        "review_question_id": _text(
            data["review_question_id"], f"{context}.review_question_id"
        ),
    }


def _question_bindings(value: Any, context: str) -> list[dict[str, str]]:
    result = [
        _question_binding(item, f"{context}[{index}]")
        for index, item in enumerate(_array(value, context))
    ]
    keys = [(item["primary_unit_id"], item["review_question_id"]) for item in result]
    if keys != sorted(set(keys)):
        raise ValueError(f"{context} must use unique stable order")
    return result


def _expected_exact_range(value: Any, context: str) -> dict[str, int]:
    data = _object(value, context)
    _exact_fields(data, _EXACT_RANGE_FIELDS, context)
    result = {
        "start_line": _positive(data["start_line"], f"{context}.start_line"),
        "end_line": _positive(data["end_line"], f"{context}.end_line"),
        "start_offset_utf16": _count(
            data["start_offset_utf16"], f"{context}.start_offset_utf16"
        ),
        "end_offset_utf16": _count(
            data["end_offset_utf16"], f"{context}.end_offset_utf16"
        ),
    }
    if (
        result["end_line"] < result["start_line"]
        or result["end_offset_utf16"] <= result["start_offset_utf16"]
    ):
        raise ValueError(f"{context} is invalid")
    return result


def _expected_candidates(value: Any, context: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        item = _object(raw, item_context)
        _exact_fields(item, _EXPECTED_CANDIDATE_FIELDS, item_context)
        result.append(
            {
                "candidate_id": _hash_id(
                    item["candidate_id"], "context-candidate", f"{item_context}.candidate_id"
                ),
                "primary_unit_id": _text(
                    item["primary_unit_id"], f"{item_context}.primary_unit_id"
                ),
                "review_question_id": _text(
                    item["review_question_id"], f"{item_context}.review_question_id"
                ),
                "relation_edge_id": _hash_id(
                    item["relation_edge_id"],
                    "relation-edge",
                    f"{item_context}.relation_edge_id",
                ),
                "relation_type": _enum(
                    item["relation_type"],
                    set(_RELATION_TYPES),
                    f"{item_context}.relation_type",
                ),
                "target_source_ref_id": _hash_id(
                    item["target_source_ref_id"],
                    "code-source",
                    f"{item_context}.target_source_ref_id",
                ),
                "target_span": _expected_exact_range(
                    item["target_span"], f"{item_context}.target_span"
                ),
                "estimated_tokens": _positive(
                    item["estimated_tokens"], f"{item_context}.estimated_tokens"
                ),
                "necessity": _enum(
                    item["necessity"], set(_NECESSITIES), f"{item_context}.necessity"
                ),
                "provenance_ref": _text(
                    item["provenance_ref"], f"{item_context}.provenance_ref"
                ),
            }
        )
    _require_unique_stable_order(
        [item["candidate_id"] for item in result], context
    )
    return result


def _expected_edges(value: Any, context: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        item = _object(raw, item_context)
        _exact_fields(item, _EXPECTED_EDGE_FIELDS, item_context)
        result.append(
            {
                "edge_id": _hash_id(
                    item["edge_id"], "relation-edge", f"{item_context}.edge_id"
                ),
                "source_ref": _text(item["source_ref"], f"{item_context}.source_ref"),
                "target_ref": _text(item["target_ref"], f"{item_context}.target_ref"),
                "relation_type": _enum(
                    item["relation_type"],
                    set(_RELATION_TYPES),
                    f"{item_context}.relation_type",
                ),
                "strength": _enum(
                    item["strength"],
                    set(_RELATION_STRENGTHS),
                    f"{item_context}.strength",
                ),
                "quality": _enum(
                    item["quality"],
                    set(_RELATION_QUALITIES),
                    f"{item_context}.quality",
                ),
                "evidence_refs": list(
                    _strings(
                        item["evidence_refs"],
                        f"{item_context}.evidence_refs",
                        allow_empty=False,
                    )
                ),
                "provenance_ref": _text(
                    item["provenance_ref"], f"{item_context}.provenance_ref"
                ),
            }
        )
    _require_unique_stable_order([item["edge_id"] for item in result], context)
    return result


def _diagnostics(value: Any, context: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        item = _object(raw, item_context)
        _exact_fields(item, _DIAGNOSTIC_FIELDS, item_context)
        result.append(
            {
                "code": _enum(
                    item["code"], set(_DIAGNOSTIC_CODES), f"{item_context}.code"
                ),
                "subject_ids": list(
                    _strings(
                        item["subject_ids"],
                        f"{item_context}.subject_ids",
                        allow_empty=False,
                    )
                ),
            }
        )
    keys = [(item["code"], item["subject_ids"]) for item in result]
    comparable = [(code, tuple(subjects)) for code, subjects in keys]
    if comparable != sorted(set(comparable)):
        raise ValueError(f"{context} must use unique stable order")
    return result


def _omissions(value: Any, context: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index, raw in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        item = _object(raw, item_context)
        _exact_fields(item, _OMISSION_FIELDS, item_context)
        result.append(
            {
                "candidate_id": _hash_id(
                    item["candidate_id"],
                    "context-candidate",
                    f"{item_context}.candidate_id",
                ),
                "reason": _enum(
                    item["reason"], set(_OMISSION_REASONS), f"{item_context}.reason"
                ),
            }
        )
    _require_unique_stable_order([item["candidate_id"] for item in result], context)
    return result


def _groups(value: Any, context: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        item = _object(raw, item_context)
        _exact_fields(item, _GROUP_FIELDS, item_context)
        group_id = _hash_id(
            item["group_id"], "change-group", f"{item_context}.group_id"
        )
        primary_ids = list(
                    _strings(
                        item["primary_unit_ids"],
                        f"{item_context}.primary_unit_ids",
                        allow_empty=False,
                    )
                )
        strong_edge_ids = list(
                    _strings(item["strong_edge_ids"], f"{item_context}.strong_edge_ids")
                )
        diagnostics = _diagnostics(item["diagnostics"], f"{item_context}.diagnostics")
        if group_id != _golden_stable_id(
            "change-group",
            {
                "primary_unit_ids": primary_ids,
                "strong_edge_ids": strong_edge_ids,
                "diagnostics": diagnostics,
            },
        ):
            raise ValueError(f"{item_context}.group_id identity drift")
        result.append(
            {
                "group_id": group_id,
                "primary_unit_ids": primary_ids,
                "strong_edge_ids": strong_edge_ids,
                "diagnostics": diagnostics,
            }
        )
    _require_unique_stable_order([item["group_id"] for item in result], context)
    return result


def _bundles(value: Any, context: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        item = _object(raw, item_context)
        _exact_fields(item, _BUNDLE_FIELDS, item_context)
        budget = _object(item["budget"], f"{item_context}.budget")
        _exact_fields(budget, _BUNDLE_BUDGET_FIELDS, f"{item_context}.budget")
        bundle_id = _hash_id(
            item["bundle_id"],
            "review-context-bundle",
            f"{item_context}.bundle_id",
        )
        result.append(
            {
                "bundle_id": bundle_id,
                "group_id": _hash_id(
                    item["group_id"], "change-group", f"{item_context}.group_id"
                ),
                "primary_unit_ids": list(
                    _strings(
                        item["primary_unit_ids"],
                        f"{item_context}.primary_unit_ids",
                        allow_empty=False,
                    )
                ),
                "primary_question_bindings": _question_bindings(
                    item["primary_question_bindings"],
                    f"{item_context}.primary_question_bindings",
                ),
                "supporting_segment_ids": list(
                    _strings(
                        item["supporting_segment_ids"],
                        f"{item_context}.supporting_segment_ids",
                    )
                ),
                "relation_edge_ids": list(
                    _strings(
                        item["relation_edge_ids"], f"{item_context}.relation_edge_ids"
                    )
                ),
                "budget": {
                    "limit": _positive(budget["limit"], f"{item_context}.budget.limit"),
                    "primary_tokens": _count(
                        budget["primary_tokens"], f"{item_context}.budget.primary_tokens"
                    ),
                    "supporting_tokens": _count(
                        budget["supporting_tokens"],
                        f"{item_context}.budget.supporting_tokens",
                    ),
                    "total_tokens": _count(
                        budget["total_tokens"], f"{item_context}.budget.total_tokens"
                    ),
                },
                "dispatch_allowed": _boolean(
                    item["dispatch_allowed"], f"{item_context}.dispatch_allowed"
                ),
                "diagnostics": _diagnostics(
                    item["diagnostics"], f"{item_context}.diagnostics"
                ),
            }
        )
        bundle_payload = {key: value for key, value in result[-1].items() if key != "bundle_id"}
        if bundle_id != _golden_stable_id("review-context-bundle", bundle_payload):
            raise ValueError(f"{item_context}.bundle_id identity drift")
    _require_unique_stable_order([item["bundle_id"] for item in result], context)
    return result


def _budget_summary(value: Any, context: str) -> dict[str, int]:
    data = _object(value, context)
    _exact_fields(data, _BUDGET_SUMMARY_FIELDS, context)
    return {
        "limit": _positive(data["limit"], f"{context}.limit"),
        "total_primary_tokens": _count(
            data["total_primary_tokens"], f"{context}.total_primary_tokens"
        ),
        "total_supporting_tokens": _count(
            data["total_supporting_tokens"], f"{context}.total_supporting_tokens"
        ),
        "total_omitted_tokens": _count(
            data["total_omitted_tokens"], f"{context}.total_omitted_tokens"
        ),
        "max_bundle_tokens": _count(
            data["max_bundle_tokens"], f"{context}.max_bundle_tokens"
        ),
        "dispatchable_bundles": _count(
            data["dispatchable_bundles"], f"{context}.dispatchable_bundles"
        ),
        "blocked_bundles": _count(
            data["blocked_bundles"], f"{context}.blocked_bundles"
        ),
    }


def _validate_candidate_selection_truth(
    *,
    groups: list[dict[str, Any]],
    candidates: dict[str, _CandidateFixture],
    edges: list[_EdgeFixture],
    primaries: list[_PrimaryFixture],
    selected_candidate_ids: set[str],
    omissions: list[dict[str, str]],
    budget: int,
    context: str,
) -> None:
    edge_by_id = {item.value.edge_id: item.value for item in edges}
    primary_by_id = {item.unit.unit_id: item for item in primaries}
    actual_omissions = {item["candidate_id"]: item["reason"] for item in omissions}
    expected_selected: set[str] = set()
    expected_omissions: dict[str, str] = {}
    necessity_rank = {"required": 0, "helpful": 1, "distractor": 2}
    for group in groups:
        primary_tokens = sum(
            _golden_estimate_code_tokens(primary_by_id[item].unit.full_text)
            for item in group["primary_unit_ids"]
        )
        group_candidates = sorted(
            (
                item.value
                for item in candidates.values()
                if item.value.primary_unit_id in group["primary_unit_ids"]
            ),
            key=lambda item: (
                necessity_rank[item.necessity],
                item.primary_unit_id,
                item.review_question_id,
                item.target_source_ref_id,
                item.target_span.start_line,
                item.target_span.start_offset_utf16,
                item.target_span.end_line,
                item.target_span.end_offset_utf16,
                item.candidate_id,
            ),
        )
        question_ids = sorted({item.review_question_id for item in group_candidates})
        for question_id in question_ids:
            question_candidates = [
                item for item in group_candidates if item.review_question_id == question_id
            ]
            required_candidates: list[Any] = []
            helpful_candidates: list[Any] = []
            required_missing = False
            for candidate in question_candidates:
                edge = edge_by_id[candidate.relation_edge_id]
                if candidate.necessity == "distractor" or candidate.relation_type in {
                    "same_file",
                    "same_host",
                }:
                    expected_omissions[candidate.candidate_id] = "distractor_rejected"
                    required_missing = required_missing or candidate.necessity == "required"
                elif edge.quality != "exact":
                    expected_omissions[candidate.candidate_id] = "relation_degraded"
                    required_missing = required_missing or candidate.necessity == "required"
                elif primary_tokens > budget:
                    expected_omissions[candidate.candidate_id] = "budget_exceeded"
                    required_missing = required_missing or candidate.necessity == "required"
                elif candidate.necessity == "required":
                    required_candidates.append(candidate)
                else:
                    helpful_candidates.append(candidate)

            required_tokens = 0
            for candidate in required_candidates:
                if primary_tokens + required_tokens + candidate.estimated_tokens <= budget:
                    expected_selected.add(candidate.candidate_id)
                    required_tokens += candidate.estimated_tokens
                else:
                    expected_omissions[candidate.candidate_id] = "budget_exceeded"
                    required_missing = True
            if required_missing:
                for candidate in helpful_candidates:
                    expected_omissions[candidate.candidate_id] = "context_blocked"
                continue
            helpful_capacity = budget - primary_tokens - required_tokens
            for candidate in helpful_candidates:
                if candidate.estimated_tokens > helpful_capacity:
                    expected_omissions[candidate.candidate_id] = "budget_exceeded"
                else:
                    expected_selected.add(candidate.candidate_id)
    if selected_candidate_ids != expected_selected or actual_omissions != expected_omissions:
        raise ValueError(f"{context} candidate selection/omission policy drift")


def _validate_group_truth(
    groups: list[dict[str, Any]],
    primaries: list[_PrimaryFixture],
    edges: list[_EdgeFixture],
    context: str,
) -> None:
    primary_ids = {item.unit.unit_id for item in primaries}
    parent = {item: item for item in primary_ids}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        parent[second] = first

    eligible: list[Any] = []
    for fixture in edges:
        edge = fixture.value
        if (
            edge.source_ref in primary_ids
            and edge.target_ref in primary_ids
            and edge.strength == "strong"
            and edge.quality == "exact"
            and edge.relation_type not in {"same_file", "same_host"}
        ):
            eligible.append(edge)
            union(edge.source_ref, edge.target_ref)
    components: dict[str, set[str]] = {}
    for primary_id in primary_ids:
        components.setdefault(find(primary_id), set()).add(primary_id)
    expected_components = {tuple(sorted(items)) for items in components.values()}
    actual_components = {tuple(item["primary_unit_ids"]) for item in groups}
    if actual_components != expected_components or sum(
        len(item["primary_unit_ids"]) for item in groups
    ) != len(primary_ids):
        raise ValueError(f"{context}.change_groups do not partition every Primary exactly once")
    for group in groups:
        if group["diagnostics"]:
            raise ValueError(f"{context} ChangeGroup diagnostics are not part of v1")
        members = set(group["primary_unit_ids"])
        expected_edges = sorted(
            edge.edge_id
            for edge in eligible
            if edge.source_ref in members and edge.target_ref in members
        )
        if group["strong_edge_ids"] != expected_edges:
            raise ValueError(f"{context} ChangeGroup strong edge truth drift")


def _validate_bundle_truth(
    bundles: list[dict[str, Any]],
    *,
    groups: list[dict[str, Any]],
    bindings: list[dict[str, str]],
    segments: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    candidates: dict[str, _CandidateFixture],
    omitted: list[dict[str, str]],
    primaries: list[_PrimaryFixture],
    budget: int,
    context: str,
) -> None:
    primary_by_id = {item.unit.unit_id: item for item in primaries}
    segment_by_candidate_id = {
        item["candidate_id"]: item for item in segments
    }
    omission_by_candidate_id = {
        item["candidate_id"]: item["reason"] for item in omitted
    }
    necessity_rank = {"required": 0, "helpful": 1, "distractor": 2}

    def candidate_key(item: Any) -> tuple[Any, ...]:
        return (
            necessity_rank[item.necessity],
            item.primary_unit_id,
            item.review_question_id,
            item.target_source_ref_id,
            item.target_span.start_line,
            item.target_span.start_offset_utf16,
            item.target_span.end_line,
            item.target_span.end_offset_utf16,
            item.candidate_id,
        )

    expected_bundles: list[dict[str, Any]] = []
    referenced_segment_ids: set[str] = set()
    for group in groups:
        members = set(group["primary_unit_ids"])
        primary_tokens = sum(
            _golden_estimate_code_tokens(primary_by_id[item].unit.full_text)
            for item in group["primary_unit_ids"]
        )
        group_bindings = [
            item for item in bindings if item["primary_unit_id"] in members
        ]
        question_ids = sorted(
            {item["review_question_id"] for item in group_bindings}
        )
        group_candidates = sorted(
            (
                item.value
                for item in candidates.values()
                if item.value.primary_unit_id in members
            ),
            key=candidate_key,
        )
        for question_id in question_ids:
            question_bindings = [
                item
                for item in group_bindings
                if item["review_question_id"] == question_id
            ]
            if {item["primary_unit_id"] for item in question_bindings} != members:
                raise ValueError(
                    f"{context} every grouped Primary must bind question {question_id!r}"
                )
            question_candidates = [
                item
                for item in group_candidates
                if item.review_question_id == question_id
            ]
            selected_required = [
                item
                for item in question_candidates
                if item.necessity == "required"
                and item.candidate_id in segment_by_candidate_id
            ]
            selected_helpful = [
                item
                for item in question_candidates
                if item.necessity == "helpful"
                and item.candidate_id in segment_by_candidate_id
            ]
            required_missing_ids = tuple(
                sorted(
                    item.candidate_id
                    for item in question_candidates
                    if item.necessity == "required"
                    and item.candidate_id in omission_by_candidate_id
                )
            )
            degraded_edge_ids = tuple(
                sorted(
                    item.relation_edge_id
                    for item in question_candidates
                    if omission_by_candidate_id.get(item.candidate_id)
                    == "relation_degraded"
                )
            )
            overflow_ids = tuple(
                group["primary_unit_ids"] if primary_tokens > budget else ()
            )
            diagnostics: list[dict[str, Any]] = []
            if overflow_ids:
                diagnostics.append(
                    {
                        "code": "primary_exceeds_budget",
                        "subject_ids": list(overflow_ids),
                    }
                )
            if degraded_edge_ids:
                diagnostics.append(
                    {
                        "code": "relation_degraded",
                        "subject_ids": list(degraded_edge_ids),
                    }
                )
            insufficient_ids = sorted(
                set((*overflow_ids, *required_missing_ids))
            )
            if insufficient_ids:
                diagnostics.append(
                    {
                        "code": "context_insufficient",
                        "subject_ids": insufficient_ids,
                    }
                )
            diagnostics.sort(
                key=lambda item: (item["code"], item["subject_ids"])
            )

            required_tokens = sum(
                item.estimated_tokens for item in selected_required
            )
            helpful_capacity = budget - primary_tokens - required_tokens
            helpful_bins: list[list[Any]] = [[]]
            helpful_bin_tokens = [0]
            for candidate in selected_helpful:
                if candidate.estimated_tokens > helpful_capacity:
                    raise ValueError(
                        f"{context} selected helpful candidate exceeds atomic capacity"
                    )
                for index, used_tokens in enumerate(helpful_bin_tokens):
                    if (
                        used_tokens + candidate.estimated_tokens
                        <= helpful_capacity
                    ):
                        helpful_bins[index].append(candidate)
                        helpful_bin_tokens[index] += candidate.estimated_tokens
                        break
                else:
                    helpful_bins.append([candidate])
                    helpful_bin_tokens.append(candidate.estimated_tokens)

            if required_missing_ids:
                helpful_bins = [[]]
            for helpful_bin in helpful_bins:
                candidate_ids = [
                    *(item.candidate_id for item in selected_required),
                    *(item.candidate_id for item in helpful_bin),
                ]
                segment_ids = sorted(
                    segment_by_candidate_id[item]["segment_id"]
                    for item in candidate_ids
                )
                referenced_segment_ids.update(segment_ids)
                relation_edge_ids = sorted(
                    set(group["strong_edge_ids"]).union(
                        candidates[item].value.relation_edge_id
                        for item in candidate_ids
                    )
                )
                supporting_tokens = sum(
                    segment_by_candidate_id[item]["estimated_tokens"]
                    for item in candidate_ids
                )
                bundle_without_id = {
                    "group_id": group["group_id"],
                    "primary_unit_ids": group["primary_unit_ids"],
                    "primary_question_bindings": question_bindings,
                    "supporting_segment_ids": segment_ids,
                    "relation_edge_ids": relation_edge_ids,
                    "budget": {
                        "limit": budget,
                        "primary_tokens": primary_tokens,
                        "supporting_tokens": supporting_tokens,
                        "total_tokens": primary_tokens + supporting_tokens,
                    },
                    "dispatch_allowed": not insufficient_ids,
                    "diagnostics": diagnostics,
                }
                expected_bundles.append(
                    {
                        "bundle_id": _golden_stable_id(
                            "review-context-bundle", bundle_without_id
                        ),
                        **bundle_without_id,
                    }
                )

    expected_bundles.sort(key=lambda item: item["bundle_id"])
    if bundles != expected_bundles:
        raise ValueError(
            f"{context}.bundles drift from question/required/first-fit truth"
        )
    if referenced_segment_ids != {
        item["segment_id"] for item in segments
    }:
        raise ValueError(
            f"{context} selected SupportingSegments are not referenced by bundles"
        )
    for bundle in bundles:
        if bundle["dispatch_allowed"] and (
            bundle["budget"]["total_tokens"] > bundle["budget"]["limit"]
        ):
            raise ValueError(
                f"{context} dispatchable bundle exceeds code-context budget"
            )


def _validate_budget_summary(
    summary: dict[str, int],
    bundles: list[dict[str, Any]],
    omissions: list[dict[str, str]],
    candidates: dict[str, _CandidateFixture],
    budget: int,
    context: str,
) -> None:
    expected = {
        "limit": budget,
        "total_primary_tokens": sum(
            item["budget"]["primary_tokens"] for item in bundles
        ),
        "total_supporting_tokens": sum(
            item["budget"]["supporting_tokens"] for item in bundles
        ),
        "total_omitted_tokens": sum(
            candidates[item["candidate_id"]].value.estimated_tokens for item in omissions
        ),
        "max_bundle_tokens": max(
            (item["budget"]["total_tokens"] for item in bundles), default=0
        ),
        "dispatchable_bundles": sum(item["dispatch_allowed"] for item in bundles),
        "blocked_bundles": sum(not item["dispatch_allowed"] for item in bundles),
    }
    if summary != expected:
        raise ValueError(f"{context}.budget_summary aggregate drift")


def _validate_plan_id(projection: dict[str, Any], context: str) -> None:
    expected_id = _golden_stable_id(
        "context-plan",
        {
            "planner_version": projection["planner_version"],
            "token_estimator_version": projection["token_estimator_version"],
            "change_set_id": projection["change_set_id"],
            "blocking_change_ids": projection["blocking_change_ids"],
            "primary_question_bindings": projection["primary_question_bindings"],
            "candidates": projection["candidates"],
            "supporting_segments": projection["supporting_segments"],
            "relation_edges": projection["relation_edges"],
            "change_groups": projection["change_groups"],
            "bundles": projection["bundles"],
            "omitted_candidate_ids": projection["omitted_candidate_ids"],
            "omitted_candidates": projection["omitted_candidates"],
            "budget_summary": projection["budget_summary"],
            "diagnostics": projection["diagnostics"],
        },
    )
    if projection["context_plan_id"] != expected_id:
        raise ValueError(f"{context}.context_plan_id identity drift")


def evaluate_golden_suite(suite: ContextGoldenSuite) -> dict[str, Any]:
    case_reports = [_evaluate_case(case) for case in suite.cases]
    matched = sum(item["matched"] is True for item in case_reports)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "implementation": "ContextPlanner.plan",
        "manifest_sha256": suite.manifest_sha256,
        "case_count": len(case_reports),
        "matched_case_count": matched,
        "mismatched_case_count": len(case_reports) - matched,
        "metrics": _suite_metrics(case_reports),
        "cases": case_reports,
    }


def _evaluate_case(case: ContextGoldenCase) -> dict[str, Any]:
    invariants: list[str] = []
    error: str | None = None
    try:
        first = case.plan()
        second = case.plan()
        actual = _public_dict(first)
        repeat_equal = actual == _public_dict(second)
        permutation_checks = {
            name: actual == _public_dict(case.plan(permutation=name))
            for name in (
                "primaries",
                "bindings",
                "sources",
                "source_mapping",
                "candidates",
                "edges",
                "all",
            )
        }
        permutation_equal = all(permutation_checks.values())
        if not repeat_equal:
            invariants.append("planner output is not repeatable")
        if not permutation_equal:
            invariants.append("input order changes planner output")
        invariants.extend(_actual_invariants(case, actual))
    except Exception as exc:  # Golden execution errors are visible mismatches.
        actual = {"error": f"{type(exc).__name__}: {exc}"}
        repeat_equal = False
        permutation_equal = False
        permutation_checks = {}
        error = f"{type(exc).__name__}: {exc}"
        invariants.append("context planning failed")
    differences = _differences(case.expected, actual)
    return {
        "case_id": case.case_id,
        "matched": not differences and not invariants,
        "repeat_equal": repeat_equal,
        "permutation_equal": permutation_equal,
        "permutation_checks": permutation_checks,
        "source_provenance": [
            {
                "alias": item.alias,
                "repository": item.snapshot.source_ref.repository,
                "revision": item.snapshot.source_ref.revision,
                "logical_path": item.snapshot.source_ref.path,
                "content_sha256": item.snapshot.source_ref.content_hash.removeprefix("sha256:"),
                "origin_lines": list(item.origin_lines),
            }
            for item in case.sources
        ],
        "expected": case.expected,
        "actual": actual,
        "differences": differences,
        "invariant_violations": invariants,
        "error": error,
        "metric_counts": _case_metric_counts(case, actual),
    }


def _actual_invariants(case: ContextGoldenCase, actual: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    expected_primary_ids = {
        primary_id
        for group in case.expected["change_groups"]
        for primary_id in group["primary_unit_ids"]
    }
    actual_group_primary_list = [
        primary_id
        for group in actual.get("change_groups", [])
        for primary_id in group.get("primary_unit_ids", [])
    ]
    actual_bundle_primary_ids = {
        primary_id
        for bundle in actual.get("bundles", [])
        for primary_id in bundle.get("primary_unit_ids", [])
    }
    if (
        set(actual_group_primary_list) != expected_primary_ids
        or len(actual_group_primary_list) != len(expected_primary_ids)
        or actual_bundle_primary_ids != expected_primary_ids
    ):
        violations.append("Primary grouping or bundle coverage is incomplete")
    for bundle in actual.get("bundles", []):
        budget = bundle.get("budget", {})
        if bundle.get("dispatch_allowed") and budget.get("total_tokens", 0) > budget.get(
            "limit", 0
        ):
            violations.append("dispatchable bundle exceeds its code-context budget")
    return violations


def _case_metric_counts(case: ContextGoldenCase, actual: dict[str, Any]) -> dict[str, int]:
    primary_truth = {
        primary_id
        for group in case.expected["change_groups"]
        for primary_id in group["primary_unit_ids"]
    }
    primary_actual = {
        value
        for bundle in actual.get("bundles", [])
        for value in bundle.get("primary_unit_ids", [])
    }
    relation_truth = {
        edge_id
        for bundle in case.expected["bundles"]
        for edge_id in bundle["relation_edge_ids"]
    }
    relation_actual = {
        edge_id
        for bundle in actual.get("bundles", [])
        for edge_id in bundle.get("relation_edge_ids", [])
    }
    necessity_by_id = {
        item.value.candidate_id: item.value.necessity for item in case.candidates
    }
    feasible_required = {
        item["candidate_id"]
        for item in case.expected["supporting_segments"]
        if necessity_by_id[item["candidate_id"]] == "required"
    }
    selected = {
        item.get("candidate_id") for item in actual.get("supporting_segments", [])
    }
    distractors = {
        item.value.candidate_id
        for item in case.candidates
        if item.value.necessity == "distractor"
    }
    omitted = set(actual.get("omitted_candidate_ids", []))
    insufficient_required = {
        item["candidate_id"]
        for item in case.expected["omitted_candidates"]
        if necessity_by_id[item["candidate_id"]] == "required"
    }
    utilized = sum(
        item.get("budget", {}).get("total_tokens", 0)
        for item in actual.get("bundles", [])
        if item.get("dispatch_allowed") is True
    )
    capacity = case.code_context_budget * sum(
        item.get("dispatch_allowed") is True for item in actual.get("bundles", [])
    )
    return {
        "primary_true": len(primary_truth),
        "primary_hit": len(primary_truth & primary_actual),
        "relation_true": len(relation_truth),
        "relation_actual": len(relation_actual),
        "relation_hit": len(relation_truth & relation_actual),
        "required_true": len(feasible_required),
        "required_hit": len(feasible_required & selected),
        "required_insufficient": len(insufficient_required),
        "distractor_true": len(distractors),
        "distractor_rejected": len(distractors & omitted),
        "utilized_tokens": utilized,
        "budget_capacity": capacity,
    }


def _suite_metrics(case_reports: list[dict[str, Any]]) -> dict[str, float]:
    counts = {
        key: sum(item["metric_counts"][key] for item in case_reports)
        for key in (
            "primary_true",
            "primary_hit",
            "relation_true",
            "relation_actual",
            "relation_hit",
            "required_true",
            "required_hit",
            "required_insufficient",
            "distractor_true",
            "distractor_rejected",
            "utilized_tokens",
            "budget_capacity",
        )
    }
    return {
        "primary_coverage": _ratio(counts["primary_hit"], counts["primary_true"]),
        "relation_precision": _ratio(counts["relation_hit"], counts["relation_actual"]),
        "relation_recall": _ratio(counts["relation_hit"], counts["relation_true"]),
        "required_context_recall_at_budget": _ratio(
            counts["required_hit"], counts["required_true"]
        ),
        "required_context_insufficient_count": float(
            counts["required_insufficient"]
        ),
        "distractor_rejection": _ratio(
            counts["distractor_rejected"], counts["distractor_true"]
        ),
        "budget_utilization": _ratio(
            counts["utilized_tokens"], counts["budget_capacity"]
        ),
        "input_order_stability": _ratio(
            sum(item["permutation_equal"] is True for item in case_reports),
            len(case_reports),
        ),
    }


def is_perfect(
    report: dict[str, Any],
    suite: ContextGoldenSuite,
) -> bool:
    report_fields = {
        "schema_version",
        "suite_id",
        "implementation",
        "manifest_sha256",
        "case_count",
        "matched_case_count",
        "mismatched_case_count",
        "metrics",
        "cases",
    }
    expected_metric_keys = {
        "primary_coverage",
        "relation_precision",
        "relation_recall",
        "required_context_recall_at_budget",
        "required_context_insufficient_count",
        "distractor_rejection",
        "budget_utilization",
        "input_order_stability",
    }
    expected_case_fields = {
        "case_id",
        "matched",
        "repeat_equal",
        "permutation_equal",
        "permutation_checks",
        "source_provenance",
        "expected",
        "actual",
        "differences",
        "invariant_violations",
        "error",
        "metric_counts",
    }
    metric_count_fields = {
        "primary_true",
        "primary_hit",
        "relation_true",
        "relation_actual",
        "relation_hit",
        "required_true",
        "required_hit",
        "required_insufficient",
        "distractor_true",
        "distractor_rejected",
        "utilized_tokens",
        "budget_capacity",
    }
    permutation_fields = {
        "primaries",
        "bindings",
        "sources",
        "source_mapping",
        "candidates",
        "edges",
        "all",
    }
    try:
        if (
            set(report) != report_fields
            or report["schema_version"] != REPORT_SCHEMA_VERSION
            or report["implementation"] != "ContextPlanner.plan"
            or not isinstance(report["suite_id"], str)
            or not report["suite_id"]
            or not isinstance(report["manifest_sha256"], str)
            or not _SHA_RE.fullmatch(report["manifest_sha256"])
        ):
            return False
        rows = report["cases"]
        if (
            not isinstance(rows, list)
            or len(rows) != 16
            or any(not isinstance(row, dict) for row in rows)
        ):
            return False
        expected_case_ids = [f"CP{index:03d}" for index in range(1, 17)]
        if [row.get("case_id") for row in rows] != expected_case_ids:
            return False

        if (
            report["suite_id"] != suite.suite_id
            or report["manifest_sha256"] != suite.manifest_sha256
            or [case.case_id for case in suite.cases] != expected_case_ids
        ):
            return False
        suite_by_id = {case.case_id: case for case in suite.cases}

        for row in rows:
            if set(row) != expected_case_fields:
                return False
            if (
                row["matched"] is not True
                or row["repeat_equal"] is not True
                or row["permutation_equal"] is not True
                or row["differences"] != []
                or row["invariant_violations"] != []
                or row["error"] is not None
                or not isinstance(row["expected"], dict)
                or not row["expected"]
                or row["expected"] != row["actual"]
                or row["expected"].get("schema_version") != "context-plan-v1"
            ):
                return False
            checks = row["permutation_checks"]
            if (
                not isinstance(checks, dict)
                or set(checks) != permutation_fields
                or any(value is not True for value in checks.values())
            ):
                return False
            counts = row["metric_counts"]
            if (
                not isinstance(counts, dict)
                or set(counts) != metric_count_fields
                or any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                    for value in counts.values()
                )
                or counts["primary_true"] < 1
                or counts["primary_hit"] > counts["primary_true"]
                or counts["relation_hit"]
                > min(counts["relation_true"], counts["relation_actual"])
                or counts["required_hit"] > counts["required_true"]
                or counts["distractor_rejected"] > counts["distractor_true"]
                or counts["utilized_tokens"] > counts["budget_capacity"]
            ):
                return False
            provenance = row["source_provenance"]
            if not isinstance(provenance, list) or not provenance:
                return False
            provenance_aliases: list[str] = []
            for source in provenance:
                if (
                    not isinstance(source, dict)
                    or set(source)
                    != {
                        "alias",
                        "repository",
                        "revision",
                        "logical_path",
                        "content_sha256",
                        "origin_lines",
                    }
                    or not isinstance(source["alias"], str)
                    or not source["alias"]
                    or not isinstance(source["repository"], str)
                    or not source["repository"]
                    or not isinstance(source["revision"], str)
                    or not source["revision"]
                    or not isinstance(source["logical_path"], str)
                    or not source["logical_path"]
                    or not isinstance(source["content_sha256"], str)
                    or not _SHA_RE.fullmatch(source["content_sha256"])
                    or not isinstance(source["origin_lines"], list)
                    or len(source["origin_lines"]) != 2
                    or any(
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        or value < 1
                        for value in source["origin_lines"]
                    )
                    or source["origin_lines"][1] < source["origin_lines"][0]
                ):
                    return False
                provenance_aliases.append(source["alias"])
            if provenance_aliases != sorted(set(provenance_aliases)):
                return False

            golden_case = suite_by_id[row["case_id"]]
            expected_provenance = [
                {
                    "alias": item.alias,
                    "repository": item.snapshot.source_ref.repository,
                    "revision": item.snapshot.source_ref.revision,
                    "logical_path": item.snapshot.source_ref.path,
                    "content_sha256": item.snapshot.source_ref.content_hash.removeprefix(
                        "sha256:"
                    ),
                    "origin_lines": list(item.origin_lines),
                }
                for item in golden_case.sources
            ]
            if (
                row["expected"] != golden_case.expected
                or provenance != expected_provenance
                or counts != _case_metric_counts(golden_case, row["actual"])
            ):
                return False

        recomputed_metrics = _suite_metrics(rows)
        metrics = report["metrics"]
        if (
            not isinstance(metrics, dict)
            or set(metrics) != expected_metric_keys
            or metrics != recomputed_metrics
            or metrics["primary_coverage"] != 1.0
            or metrics["relation_precision"] != 1.0
            or metrics["relation_recall"] != 1.0
            or metrics["required_context_recall_at_budget"] != 1.0
            or metrics["required_context_insufficient_count"] < 0
            or metrics["distractor_rejection"] != 1.0
            or not 0.0 <= metrics["budget_utilization"] <= 1.0
            or metrics["input_order_stability"] != 1.0
        ):
            return False
        case_count = _count(report["case_count"], "report.case_count")
        matched_count = _count(
            report["matched_case_count"], "report.matched_case_count"
        )
        mismatched_count = _count(
            report["mismatched_case_count"], "report.mismatched_case_count"
        )
        return (
            case_count == len(rows)
            and matched_count == sum(row["matched"] is True for row in rows) == 16
            and mismatched_count == 0
            and case_count == matched_count + mismatched_count
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def write_current_baseline(
    report: dict[str, Any],
    suite: ContextGoldenSuite,
    baseline_path: str | Path,
) -> None:
    path = Path(baseline_path)
    allowed = suite.manifest_path.parent / "baselines" / "current.json"
    if path.resolve() != allowed.resolve():
        raise ValueError("ContextPlan baseline writer may only update baselines/current.json")
    if allowed.parent.is_symlink() or (path.exists() and path.is_symlink()):
        raise ValueError("ContextPlan baseline must not be a symlink")
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "manifest_sha256": suite.manifest_sha256,
        "report": report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def assert_strict_baseline(
    report: dict[str, Any],
    suite: ContextGoldenSuite,
    baseline_path: str | Path,
) -> None:
    path = Path(baseline_path)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("ContextPlan baseline must not be a symlink")
    data = _json_object(_read_regular_file(path, "baseline"), str(path))
    _exact_fields(
        data,
        {"schema_version", "suite_id", "manifest_sha256", "report"},
        "baseline",
    )
    if data["schema_version"] != BASELINE_SCHEMA_VERSION:
        raise ValueError("unsupported ContextPlan baseline schema")
    if data["suite_id"] != suite.suite_id:
        raise ValueError("baseline suite_id drift")
    if data["manifest_sha256"] != suite.manifest_sha256:
        raise ValueError("baseline manifest hash drift")
    if data["report"] != report:
        preview = "; ".join(_differences(data["report"], report)[:5])
        raise ValueError(f"ContextPlan strict baseline mismatch: {preview}")


def _validate_coordinate_system(value: Any) -> None:
    data = _object(value, "manifest.coordinate_system")
    _exact_fields(data, _COORDINATE_FIELDS, "manifest.coordinate_system")
    if data != {
        "line_base": 1,
        "line_end": "inclusive",
        "offset_encoding": "UTF-16",
        "offset_base": 0,
        "offset_end": "exclusive",
    }:
        raise ValueError("manifest.coordinate_system does not match the frozen contract")


def _validate_frozen_contract(value: Any) -> None:
    data = _object(value, "manifest.frozen_contract")
    _exact_fields(data, _CONTRACT_FIELDS, "manifest.frozen_contract")
    expected = {
        "planner_version": "context-planner-v1",
        "token_estimator_version": "arkts-code-token-v1",
        "relation_types": list(_RELATION_TYPES),
        "relation_strengths": list(_RELATION_STRENGTHS),
        "relation_qualities": list(_RELATION_QUALITIES),
        "candidate_necessities": list(_NECESSITIES),
        "selection_reasons": list(_SELECTION_REASONS),
        "omission_reasons": list(_OMISSION_REASONS),
        "diagnostic_codes": list(_DIAGNOSTIC_CODES),
    }
    if data != expected:
        raise ValueError("manifest.frozen_contract drift")


def _line_span(value: Any, source: str, context: str) -> ReviewUnitSpan:
    data = _object(value, context)
    _exact_fields(data, _SPAN_FIELDS, context)
    span = ReviewUnitSpan(
        _positive(data["start_line"], f"{context}.start_line"),
        _positive(data["end_line"], f"{context}.end_line"),
    )
    if span.end_line > len(source.splitlines(keepends=True)):
        raise ValueError(f"{context} exceeds source line count")
    return span


def _exact_range(value: Any, source: str, context: str) -> ExactRange:
    data = _object(value, context)
    _exact_fields(data, _EXACT_RANGE_FIELDS, context)
    exact = ExactRange(
        start_line=_positive(data["start_line"], f"{context}.start_line"),
        end_line=_positive(data["end_line"], f"{context}.end_line"),
        start_offset_utf16=_count(
            data["start_offset_utf16"], f"{context}.start_offset_utf16"
        ),
        end_offset_utf16=_count(
            data["end_offset_utf16"], f"{context}.end_offset_utf16"
        ),
    )
    if exact.end_offset_utf16 <= exact.start_offset_utf16:
        raise ValueError(f"{context} must be a non-empty exact range")
    boundaries = _utf16_boundaries(source)
    if (
        exact.start_offset_utf16 not in boundaries
        or exact.end_offset_utf16 not in boundaries
    ):
        raise ValueError(f"{context} is not on a UTF-16 source boundary")
    start_index = boundaries[exact.start_offset_utf16]
    end_index = boundaries[exact.end_offset_utf16]
    start_line = source[:start_index].count("\n") + 1
    mapped_end = source[:end_index].count("\n") + 1
    ends_after_newline = (
        end_index > 0
        and source[end_index - 1] == "\n"
        and mapped_end == exact.end_line + 1
    )
    if start_line != exact.start_line or (
        mapped_end != exact.end_line and not ends_after_newline
    ):
        raise ValueError(f"{context} line and UTF-16 coordinates disagree")
    if exact.end_line > len(source.splitlines(keepends=True)):
        raise ValueError(f"{context} exceeds source line count")
    return exact


def _slice_exact(source: str, exact: ExactRange) -> str:
    boundaries = _utf16_boundaries(source)
    return source[
        boundaries[exact.start_offset_utf16] : boundaries[exact.end_offset_utf16]
    ]


def _utf16_boundaries(source: str) -> dict[int, int]:
    result = {0: 0}
    offset = 0
    for index, character in enumerate(source, start=1):
        offset += 2 if ord(character) > 0xFFFF else 1
        result[offset] = index
    return result


def _safe_child(root: Path, value: str, context: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ValueError(f"{context}.file must be a safe relative path")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{context}.file must not traverse a symlink")
    resolved = current.resolve(strict=True)
    sources_root = (root / "sources").resolve(strict=True)
    if not resolved.is_relative_to(sources_root):
        raise ValueError(f"{context}.file must stay under Golden sources/")
    return resolved


def _read_regular_file(path: Path, context: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{context} must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _json_object(raw: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{context} is not valid UTF-8 JSON") from exc
    return _object(value, context)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _exact_fields(data: dict[str, Any], fields: set[str], context: str) -> None:
    missing = sorted(fields - set(data))
    unknown = sorted(set(data) - fields)
    if missing or unknown:
        raise ValueError(f"{context} fields invalid: missing={missing}, unknown={unknown}")


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be an object")
    return cast(dict[str, Any], value)


def _array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ValueError(f"{context} must be non-empty text")
    return value


def _nullable_text(value: Any, context: str) -> str | None:
    return None if value is None else _text(value, context)


def _alias(value: Any, context: str) -> str:
    result = _text(value, context)
    if not _ALIAS_RE.fullmatch(result):
        raise ValueError(f"{context} must be a lowercase semantic alias")
    return result


def _nullable_alias(value: Any, context: str) -> str | None:
    return None if value is None else _alias(value, context)


def _logical_path(value: Any, context: str) -> str:
    result = _text(value, context)
    if (
        result.startswith("/")
        or "\\" in result
        or not Path(result).parts
        or any(part in {"", ".", ".."} for part in Path(result).parts)
    ):
        raise ValueError(f"{context} must be a normalized repository-relative path")
    if normalize_review_path(result) != result:
        raise ValueError(f"{context} must already be normalized")
    return result


def _sha(value: Any, context: str) -> str:
    result = _text(value, context)
    if not _SHA_RE.fullmatch(result):
        raise ValueError(f"{context} must be 64 lowercase hexadecimal characters")
    return result


def _hash_id(value: Any, prefix: str, context: str) -> str:
    result = _text(value, context)
    if not _HASH_ID_RE.fullmatch(result) or not result.startswith(f"{prefix}:sha256:"):
        raise ValueError(f"{context} must be a stable {prefix} identity")
    return result


def _enum(value: Any, allowed: set[str], context: str) -> str:
    result = _text(value, context)
    if result not in allowed:
        raise ValueError(f"{context} has unsupported value {result!r}")
    return result


def _positive(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{context} must be an integer >= 1")
    return value


def _count(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{context} must be an integer >= 0")
    return value


def _boolean(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")
    return value


def _lines(value: Any, context: str) -> tuple[int, ...]:
    result = tuple(
        _positive(item, f"{context}[{index}]")
        for index, item in enumerate(_array(value, context))
    )
    if list(result) != sorted(set(result)):
        raise ValueError(f"{context} must be sorted and unique")
    return result


def _strings(value: Any, context: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    result = tuple(
        _text(item, f"{context}[{index}]")
        for index, item in enumerate(_array(value, context))
    )
    if (not allow_empty and not result) or list(result) != sorted(set(result)):
        raise ValueError(f"{context} must be sorted and unique")
    return result


def _origin_lines(value: Any, line_count: int, context: str) -> tuple[int, int]:
    values = _array(value, f"{context}.origin_lines")
    if len(values) != 2:
        raise ValueError(f"{context}.origin_lines must contain [start, end]")
    start = _positive(values[0], f"{context}.origin_lines[0]")
    end = _positive(values[1], f"{context}.origin_lines[1]")
    if end < start or end - start + 1 != line_count:
        raise ValueError(f"{context}.origin_lines do not match the source line count")
    return (start, end)


def _sorted_aliases(values: list[str], context: str) -> None:
    if values != sorted(set(values)):
        raise ValueError(f"{context} aliases must be sorted and unique")


def _require_unique_stable_order(values: list[str], context: str) -> None:
    if values != sorted(set(values)):
        raise ValueError(f"{context} must use unique stable ID order")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _golden_stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}:sha256:{hashlib.sha256(_canonical(payload).encode('utf-8')).hexdigest()}"


def _golden_estimate_code_tokens(source: str) -> int:
    if not isinstance(source, str):
        raise ValueError("Golden token source must be text")
    return sum(
        max(1, math.ceil(len(match.group(0).encode("utf-8")) / 4))
        for match in _GOLDEN_CODE_CHUNK_RE.finditer(source)
    )


def _range_dict(span: ExactRange) -> dict[str, int]:
    return {
        "start_line": span.start_line,
        "end_line": span.end_line,
        "start_offset_utf16": span.start_offset_utf16,
        "end_offset_utf16": span.end_offset_utf16,
    }


def _golden_source_span_ref_id(source_ref_id: str, span: ExactRange) -> str:
    return _golden_stable_id(
        "source-span", {"source_ref_id": source_ref_id, "span": _range_dict(span)}
    )


def _public_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        result = value.to_dict()
    elif is_dataclass(value):
        result = asdict(cast(Any, value))
    else:
        raise ValueError(f"{type(value).__name__} has no public serialization")
    if not isinstance(result, dict):
        raise ValueError(f"{type(value).__name__} serialization must be an object")
    return cast(dict[str, Any], json.loads(json.dumps(result, ensure_ascii=False)))


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def _differences(expected: Any, actual: Any, path: str = "$") -> list[str]:
    if type(expected) is not type(actual):
        return [f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"]
    if isinstance(expected, dict):
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}"
            if key not in expected:
                differences.append(f"{child}: unexpected")
            elif key not in actual:
                differences.append(f"{child}: missing")
            else:
                differences.extend(_differences(expected[key], actual[key], child))
        return differences
    if isinstance(expected, list):
        differences = []
        if len(expected) != len(actual):
            differences.append(
                f"{path}: expected {len(expected)} items, got {len(actual)}"
            )
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            differences.extend(_differences(left, right, f"{path}[{index}]"))
        return differences
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


def _context_module() -> Any:
    from arkts_code_reviewer.code_analysis import context_planning

    return context_planning
