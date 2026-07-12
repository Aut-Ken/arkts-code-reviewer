from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arkts_code_reviewer.code_analysis.arkts_lexicon import (
    LIFECYCLE_SYMBOLS,
    STATE_DECORATORS,
)
from arkts_code_reviewer.code_analysis.models import (
    CodeFacts,
    Declaration,
    FileHunk,
    HostSummary,
    ParserQuality,
    ReviewUnit,
    ReviewUnitDiagnostic,
    ReviewUnitFileResult,
    ReviewUnitSpan,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    REVIEW_UNIT_DIAGNOSTIC_CODES,
    REVIEW_UNIT_KINDS,
    SELECTION_REASONS,
    declaration_unit_id,
    fallback_unit_id,
    normalize_review_path,
)
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.text_utils import extract_lines

SCHEMA_VERSION = "review-unit-golden-v1"
BASELINE_SCHEMA_VERSION = "review-unit-golden-baseline-v1"
TARGET_PHASES = ("RU-1", "RU-2", "RU-4", "RU-5")
UNSUPPORTED_INPUTS = ("deletion_only",)

MANIFEST_FIELDS = (
    "schema_version",
    "suite_id",
    "description",
    "coordinate_system",
    "frozen_contract",
    "cases",
)
CONTRACT_FIELDS = ("unit_kinds", "selection_reasons", "diagnostic_codes")
CASE_FIELDS = (
    "case_id",
    "description",
    "target_phase",
    "logical_path",
    "source",
    "input",
    "expected",
)
SOURCE_FIELDS = (
    "file",
    "kind",
    "source_id",
    "revision",
    "relative_path",
    "origin_lines",
    "content_sha256",
)
INPUT_FIELDS = (
    "mode",
    "hunks",
    "token_budget",
    "unsupported",
    "parser_fixture",
)
PARSER_FIXTURE_FIELDS = ("parser_layer", "warnings", "declarations")
DECLARATION_FIELDS = ("kind", "name", "qualified_name", "parent_name", "span")
SPAN_FIELDS = ("start_line", "end_line")
HUNK_FIELDS = ("new_start", "new_lines")
EXPECTED_FIELDS = ("units", "diagnostics")
UNIT_FIELDS = (
    "unit_id",
    "unit_kind",
    "unit_symbol",
    "source_span",
    "context_span",
    "changed_new_lines",
    "selection_reason",
    "context_degraded",
    "diagnostics",
)
DIAGNOSTIC_FIELDS = ("code", "lines")
REPORT_FIELDS = (
    "schema_version",
    "suite_id",
    "implementation",
    "manifest_sha256",
    "case_count",
    "matched_case_count",
    "mismatched_case_count",
    "phase_case_counts",
    "phase_mismatch_counts",
    "cases",
)
CASE_RESULT_FIELDS = (
    "case_id",
    "target_phase",
    "logical_path",
    "source_sha256",
    "provenance",
    "input",
    "expected",
    "actual",
    "legacy_units",
    "differences",
    "invariant_violations",
    "repeat_equal",
    "reversed_hunks_equal",
    "error",
    "matched",
)
LEGACY_UNIT_FIELDS = (
    "unit_ref",
    "changed_lines",
    "file_changed_lines",
    "unit_changed_lines",
    "full_text_sha256",
)
BASELINE_FIELDS = ("schema_version", "suite_id", "implementation", "report")


@dataclass(frozen=True)
class ReviewUnitGoldenCase:
    case_id: str
    description: str
    target_phase: str
    logical_path: str
    source_path: Path
    source_metadata: dict[str, Any]
    parser_layer: str
    warnings: tuple[str, ...]
    declarations: tuple[Declaration, ...]
    mode: str
    hunks: tuple[FileHunk, ...]
    token_budget: int | None
    unsupported: tuple[str, ...]
    expected: dict[str, Any]

    def read_source(self) -> str:
        return _read_source_snapshot(
            self.source_path,
            self.source_metadata["content_sha256"],
            f"case {self.case_id!r} source",
        )

    def input_projection(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "hunks": [
                {"new_start": hunk.new_start, "new_lines": hunk.new_lines}
                for hunk in self.hunks
            ],
            "token_budget": self.token_budget,
            "unsupported": list(self.unsupported),
            "parser_fixture": {
                "parser_layer": self.parser_layer,
                "warnings": list(self.warnings),
                "declarations": [declaration_projection(item) for item in self.declarations],
            },
        }


@dataclass(frozen=True)
class ReviewUnitGoldenSuite:
    suite_id: str
    manifest_path: Path
    manifest_sha256: str
    cases: tuple[ReviewUnitGoldenCase, ...]


def load_golden_suite(manifest_path: Path) -> ReviewUnitGoldenSuite:
    manifest_path = manifest_path.resolve()
    manifest_bytes = _read_file_bytes(manifest_path, "manifest")
    data = _parse_json_mapping(manifest_bytes, manifest_path, "manifest")
    _require_exact_fields(data, MANIFEST_FIELDS, "manifest")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"manifest.schema_version must be {SCHEMA_VERSION!r}, "
            f"got {data.get('schema_version')!r}"
        )

    suite_id = _string(data.get("suite_id"), "manifest.suite_id")
    _string(data.get("description"), "manifest.description")
    coordinate_system = _mapping(data.get("coordinate_system"), "manifest.coordinate_system")
    _require_exact_fields(
        coordinate_system,
        ("line_base", "line_end", "columns"),
        "manifest.coordinate_system",
    )
    if not reports_equal(coordinate_system, {
        "line_base": 1,
        "line_end": "inclusive",
        "columns": "unsupported",
    }):
        raise ValueError("manifest.coordinate_system must freeze 1-based inclusive lines")

    contract = _mapping(data.get("frozen_contract"), "manifest.frozen_contract")
    _require_exact_fields(contract, CONTRACT_FIELDS, "manifest.frozen_contract")
    frozen_values = {
        "unit_kinds": list(REVIEW_UNIT_KINDS),
        "selection_reasons": list(SELECTION_REASONS),
        "diagnostic_codes": list(REVIEW_UNIT_DIAGNOSTIC_CODES),
    }
    if not reports_equal(contract, frozen_values):
        raise ValueError("manifest.frozen_contract does not match the ReviewUnit v1 contract")

    raw_cases = _list(data.get("cases"), "manifest.cases")
    if not 12 <= len(raw_cases) <= 16:
        raise ValueError("manifest.cases must contain between 12 and 16 cases")

    root = manifest_path.parent
    cases: list[ReviewUnitGoldenCase] = []
    seen_ids: set[str] = set()
    seen_case_semantics: set[str] = set()
    for index, value in enumerate(raw_cases):
        case = _load_case(_mapping(value, f"manifest.cases[{index}]"), root, index)
        if case.case_id in seen_ids:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        semantic_fingerprint = _canonical(
            {
                "logical_path": case.logical_path,
                "source": case.source_metadata,
                "input": case.input_projection(),
                "expected": case.expected,
            }
        )
        if semantic_fingerprint in seen_case_semantics:
            raise ValueError(f"duplicate semantic Golden case: {case.case_id}")
        seen_ids.add(case.case_id)
        seen_case_semantics.add(semantic_fingerprint)
        cases.append(case)

    case_ids = [case.case_id for case in cases]
    if case_ids != sorted(case_ids):
        raise ValueError("manifest.cases must be sorted by case_id")
    covered_phases = {case.target_phase for case in cases}
    missing_phases = set(TARGET_PHASES) - covered_phases
    if missing_phases:
        raise ValueError(
            f"manifest.cases must cover every target phase: missing={sorted(missing_phases)}"
        )
    return ReviewUnitGoldenSuite(
        suite_id=suite_id,
        manifest_path=manifest_path,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        cases=tuple(cases),
    )


def evaluate_golden_suite(
    suite: ReviewUnitGoldenSuite,
    builder: ReviewUnitBuilder | None = None,
) -> dict[str, Any]:
    builder = builder or ReviewUnitBuilder()
    current_manifest_hash = hashlib.sha256(
        _read_file_bytes(suite.manifest_path, "manifest")
    ).hexdigest()
    if current_manifest_hash != suite.manifest_sha256:
        raise ValueError(
            "ReviewUnit Golden manifest changed after it was loaded: "
            f"expected {suite.manifest_sha256}, got {current_manifest_hash}"
        )
    case_results = [_evaluate_case(case, builder) for case in suite.cases]
    phase_case_counts = Counter(case.target_phase for case in suite.cases)
    phase_mismatch_counts = Counter(
        result["target_phase"] for result in case_results if not result["matched"]
    )
    matched_count = sum(bool(result["matched"]) for result in case_results)
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "implementation": type(builder).__name__,
        "manifest_sha256": suite.manifest_sha256,
        "case_count": len(suite.cases),
        "matched_case_count": matched_count,
        "mismatched_case_count": len(suite.cases) - matched_count,
        "phase_case_counts": {
            phase: phase_case_counts.get(phase, 0) for phase in TARGET_PHASES
        },
        "phase_mismatch_counts": {
            phase: phase_mismatch_counts.get(phase, 0) for phase in TARGET_PHASES
        },
        "cases": case_results,
    }


def _evaluate_case(
    case: ReviewUnitGoldenCase,
    builder: ReviewUnitBuilder,
) -> dict[str, Any]:
    source = case.read_source()
    actual: dict[str, Any] = {"units": [], "diagnostics": []}
    legacy_units: list[dict[str, Any]] = []
    invariant_violations: list[str] = []
    repeat_equal = False
    reversed_hunks_equal: bool | None = None
    error: str | None = None

    try:
        file_result = _run_builder(case, builder, source, case.hunks)
        first_snapshot = _file_result_snapshot(file_result)
        units = file_result.units
        target_units = first_snapshot["units"]
        legacy_units = first_snapshot["legacy_units"]
        actual = {
            "units": target_units,
            "diagnostics": first_snapshot["diagnostics"],
        }
        invariant_violations.extend(_file_result_invariants(case, file_result, source))
        invariant_violations.extend(_unit_invariants(case, units, source))
        repeated = _run_builder(case, builder, source, case.hunks)
        repeat_snapshot = _file_result_snapshot(repeated)
        repeat_equal = reports_equal(first_snapshot, repeat_snapshot)
        if len(case.hunks) > 1:
            reversed_result = _run_builder(
                case,
                builder,
                source,
                tuple(reversed(case.hunks)),
            )
            reversed_snapshot = _file_result_snapshot(reversed_result)
            reversed_hunks_equal = reports_equal(first_snapshot, reversed_snapshot)
        if not repeat_equal:
            invariant_violations.append("repeated execution changed ReviewUnit output")
        if reversed_hunks_equal is False:
            invariant_violations.append("reversing hunks changed ReviewUnit output")
    except Exception as exc:  # pragma: no cover - retained as evaluator diagnostics.
        error = repr(exc)
        invariant_violations.append(f"builder execution failed: {error}")

    differences = _differences(case.expected, actual)
    invariant_violations = sorted(set(invariant_violations))
    matched = not differences and not invariant_violations and error is None
    return {
        "case_id": case.case_id,
        "target_phase": case.target_phase,
        "logical_path": case.logical_path,
        "source_sha256": case.source_metadata["content_sha256"],
        "provenance": dict(case.source_metadata),
        "input": case.input_projection(),
        "expected": case.expected,
        "actual": actual,
        "legacy_units": legacy_units,
        "differences": differences,
        "invariant_violations": invariant_violations,
        "repeat_equal": repeat_equal,
        "reversed_hunks_equal": reversed_hunks_equal,
        "error": error,
        "matched": matched,
    }


def _run_builder(
    case: ReviewUnitGoldenCase,
    builder: ReviewUnitBuilder,
    source: str,
    hunks: tuple[FileHunk, ...],
) -> ReviewUnitFileResult:
    facts = CodeFacts(
        path=case.logical_path,
        declarations=[_copy_declaration(item) for item in case.declarations],
        parser_layer=case.parser_layer,  # type: ignore[arg-type]
        warnings=list(case.warnings),
    )
    return builder.build_file_result(
        case.logical_path,
        source,
        facts,
        case.mode,  # type: ignore[arg-type]
        list(hunks),
    )


def _file_result_snapshot(result: Any) -> dict[str, Any]:
    if not isinstance(result, ReviewUnitFileResult):
        raise ValueError("ReviewUnit builder must return ReviewUnitFileResult")
    result.validate()
    return {
        "path": result.path,
        "units": [_unit_projection(unit) for unit in result.units],
        "legacy_units": [_legacy_unit_projection(unit) for unit in result.units],
        "parser_quality": {
            "parser_layer": result.parser_quality.parser_layer,
            "warnings": list(result.parser_quality.warnings),
        },
        "diagnostics": _actual_diagnostics(result.diagnostics),
        "unassigned_hunk_lines": list(result.unassigned_hunk_lines),
    }


def _copy_declaration(declaration: Declaration) -> Declaration:
    return Declaration(
        kind=declaration.kind,
        name=declaration.name,
        qualified_name=declaration.qualified_name,
        span=declaration.span,
        parent_name=declaration.parent_name,
        text=declaration.text,
    )


def _unit_projection(unit: Any) -> dict[str, Any]:
    if not isinstance(unit, ReviewUnit):
        raise ValueError("ReviewUnit builder must return ReviewUnit values")
    unit.validate()
    return {
        "unit_id": unit.unit_id,
        "unit_kind": unit.unit_kind,
        "unit_symbol": unit.unit_symbol,
        "source_span": _actual_span_projection(unit.source_span),
        "context_span": _actual_span_projection(unit.context_span),
        "changed_new_lines": list(unit.changed_new_lines),
        "selection_reason": unit.selection_reason,
        "context_degraded": unit.context_degraded,
        "diagnostics": _actual_diagnostics(unit.diagnostics),
    }


def _legacy_unit_projection(unit: Any) -> dict[str, Any]:
    if not isinstance(unit, ReviewUnit):
        raise ValueError("ReviewUnit builder must return ReviewUnit values")
    unit.validate()
    return {
        "unit_ref": unit.unit_ref,
        "changed_lines": list(unit.changed_lines),
        "file_changed_lines": list(unit.file_changed_lines),
        "unit_changed_lines": list(unit.unit_changed_lines),
        "full_text_sha256": hashlib.sha256(unit.full_text.encode("utf-8")).hexdigest(),
    }


def _actual_span_projection(span: Any) -> dict[str, int]:
    if not isinstance(span, ReviewUnitSpan):
        raise ValueError("ReviewUnit spans must use ReviewUnitSpan")
    return {
        "start_line": span.start_line,
        "end_line": span.end_line,
    }


def _actual_diagnostics(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError("ReviewUnit diagnostics must be a list")
    diagnostics: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, ReviewUnitDiagnostic):
            raise ValueError("ReviewUnit diagnostics must be structured")
        diagnostics.append(
            {
                "code": value.code,
                "lines": list(value.lines),
            }
        )
    return diagnostics


def _file_result_invariants(
    case: ReviewUnitGoldenCase,
    result: ReviewUnitFileResult,
    source: str,
) -> list[str]:
    violations: list[str] = []
    source_line_count = max(1, len(source.splitlines()))
    hunk_lines = {
        line
        for hunk in case.hunks
        for line in range(hunk.new_start, hunk.new_end + 1)
    }

    if result.path != case.logical_path:
        violations.append("result.path does not match the Golden logical path")
    if not isinstance(result.units, list):
        violations.append("result.units is not a list")
        units: list[Any] = []
    else:
        units = result.units

    if not isinstance(result.parser_quality, ParserQuality):
        violations.append("result.parser_quality is not a ParserQuality")
    else:
        if result.parser_quality.parser_layer != case.parser_layer:
            violations.append("result.parser_quality.parser_layer does not match CodeFacts")
        if list(result.parser_quality.warnings) != list(case.warnings):
            violations.append("result.parser_quality.warnings do not match CodeFacts")

    diagnostics = _actual_diagnostics(result.diagnostics)
    if diagnostics != sorted(diagnostics, key=_canonical) or len(
        {_canonical(item) for item in diagnostics}
    ) != len(diagnostics):
        violations.append("result.diagnostics is not sorted and unique")
    violations.extend(
        _diagnostic_semantic_violations(
            diagnostics,
            prefix="result.diagnostics",
            source_line_count=source_line_count,
            hunk_lines=hunk_lines,
            context_span=None,
            result_level=True,
        )
    )

    unassigned = result.unassigned_hunk_lines
    if not isinstance(unassigned, list) or any(
        not isinstance(line, int) or isinstance(line, bool) or line < 1
        for line in unassigned
    ):
        violations.append("result.unassigned_hunk_lines must contain 1-based integers")
    elif unassigned != sorted(set(unassigned)):
        violations.append("result.unassigned_hunk_lines is not sorted and unique")
    else:
        assigned_lines = {
            line
            for unit in units
            if isinstance(unit, ReviewUnit)
            for line in unit.changed_new_lines
        }
        expected_unassigned = sorted(hunk_lines - assigned_lines)
        if unassigned != expected_unassigned:
            violations.append(
                "result.unassigned_hunk_lines does not equal input hunk lines "
                "without a ReviewUnit owner"
            )
    return violations


def _unit_invariants(
    case: ReviewUnitGoldenCase,
    units: list[Any],
    source: str,
) -> list[str]:
    violations: list[str] = []
    unit_ids: list[str] = []
    order_keys: list[tuple[int, int, int, int, str]] = []
    source_line_count = max(1, len(source.splitlines()))
    hunk_lines = {
        line
        for hunk in case.hunks
        for line in range(hunk.new_start, hunk.new_end + 1)
    }
    for index, unit in enumerate(units):
        prefix = f"units[{index}]"
        if not isinstance(unit, ReviewUnit):
            violations.append(f"{prefix} is not a ReviewUnit")
            continue
        try:
            unit.validate()
        except ValueError as exc:
            violations.append(f"{prefix} fails ReviewUnit validation: {exc}")
        unit_id = getattr(unit, "unit_id", None)
        source_span = getattr(unit, "source_span", None)
        context_span = getattr(unit, "context_span", None)
        changed_new_lines = getattr(unit, "changed_new_lines", None)
        diagnostics = _actual_diagnostics(getattr(unit, "diagnostics", []))
        if unit.file != case.logical_path:
            violations.append(f"{prefix}.file does not match the Golden logical path")
        if not isinstance(unit.host_summary, HostSummary):
            violations.append(f"{prefix}.host_summary is not a HostSummary")
        elif unit.host_summary != _expected_host_summary(case, unit, source):
            violations.append(
                f"{prefix}.host_summary does not match its frozen host occurrence"
            )
        expected_unit_ref = f"{unit.unit_symbol}@{case.logical_path}"
        if unit.unit_ref != expected_unit_ref:
            violations.append(f"{prefix}.unit_ref does not preserve unit_symbol@logical_path")
        if diagnostics != sorted(diagnostics, key=_canonical) or len(
            {_canonical(item) for item in diagnostics}
        ) != len(diagnostics):
            violations.append(f"{prefix}.diagnostics is not sorted and unique")
        if any(item["code"] not in REVIEW_UNIT_DIAGNOSTIC_CODES for item in diagnostics):
            violations.append(f"{prefix}.diagnostics contains an unsupported code")
        if getattr(unit, "unit_kind", None) not in REVIEW_UNIT_KINDS:
            violations.append(f"{prefix}.unit_kind is unsupported")
        if getattr(unit, "selection_reason", None) not in SELECTION_REASONS:
            violations.append(f"{prefix}.selection_reason is unsupported")
        if (
            getattr(unit, "unit_kind", None) == "fallback"
        ) != (
            getattr(unit, "selection_reason", None) == "fallback_window"
        ):
            violations.append(f"{prefix}.fallback kind and reason are inconsistent")
        if not isinstance(unit_id, str) or not unit_id:
            violations.append(f"{prefix}.unit_id is unavailable")
        else:
            unit_ids.append(unit_id)
        if source_span is None:
            violations.append(f"{prefix}.source_span is unavailable")
        elif (
            source_span.start_line < 1
            or source_span.end_line < source_span.start_line
            or source_span.end_line > source_line_count
        ):
            violations.append(f"{prefix}.source_span is outside the source")
        if context_span is None:
            violations.append(f"{prefix}.context_span is unavailable")
        else:
            if (
                context_span.start_line < 1
                or context_span.end_line < context_span.start_line
                or context_span.end_line > source_line_count
            ):
                violations.append(f"{prefix}.context_span is outside the source")
            if source_span is not None and not (
                context_span.start_line <= source_span.start_line
                and source_span.end_line <= context_span.end_line
            ):
                violations.append(f"{prefix}.source_span is not inside context_span")
            expected_text = extract_lines(
                source,
                context_span.start_line,
                context_span.end_line,
            )
            if unit.full_text != expected_text:
                violations.append(f"{prefix}.full_text does not equal context_span slice")
            order_keys.append(
                (
                    context_span.start_line,
                    context_span.end_line,
                    unit.source_span.start_line,
                    unit.source_span.end_line,
                    unit_id or unit.unit_symbol,
                )
            )
        if source_span is not None and context_span is not None and unit_id:
            try:
                expected_unit_id = format_unit_id(
                    case.logical_path,
                    unit.unit_kind,
                    unit.unit_symbol,
                    {
                        "start_line": source_span.start_line,
                        "end_line": source_span.end_line,
                    },
                    {
                        "start_line": context_span.start_line,
                        "end_line": context_span.end_line,
                    },
                )
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                violations.append(f"{prefix}.identity fields are invalid: {exc}")
            else:
                if unit_id != expected_unit_id:
                    violations.append(f"{prefix}.unit_id does not match identity fields")
        if source_span is not None:
            if unit.unit_kind == "fallback":
                expected_symbol = (
                    f"hunk-L{source_span.start_line}-L{source_span.end_line}"
                )
                if unit.unit_symbol != expected_symbol:
                    violations.append(f"{prefix}.fallback symbol does not match source_span")
                if case.mode == "diff" and case.hunks and not any(
                    hunk.new_start == source_span.start_line
                    and hunk.new_end == source_span.end_line
                    for hunk in case.hunks
                ):
                    violations.append(f"{prefix}.fallback source_span does not match an input hunk")
            elif not _matches_frozen_declaration(unit, case.declarations):
                violations.append(
                    f"{prefix} owner does not match a frozen declaration occurrence"
                )
        if changed_new_lines is None:
            violations.append(f"{prefix}.changed_new_lines is unavailable")
        else:
            if list(changed_new_lines) != sorted(set(changed_new_lines)):
                violations.append(f"{prefix}.changed_new_lines is not sorted and unique")
            if context_span is not None:
                file_changed_lines = list(unit.file_changed_lines)
                if file_changed_lines != sorted(set(file_changed_lines)):
                    violations.append(f"{prefix}.file_changed_lines is not sorted and unique")
                if list(unit.changed_lines) != file_changed_lines:
                    violations.append(
                        f"{prefix}.changed_lines differs from file_changed_lines"
                    )
                expected_changed_new_lines = [
                    line
                    for line in file_changed_lines
                    if context_span.contains_line(line)
                ]
                if list(changed_new_lines) != expected_changed_new_lines:
                    violations.append(
                        f"{prefix}.changed_new_lines does not project file_changed_lines"
                    )
                assigned_hunk_lines = _assigned_hunk_lines(
                    case.hunks,
                    source_span_start=unit.source_span.start_line,
                    source_span_end=unit.source_span.end_line,
                    context_span_start=context_span.start_line,
                    context_span_end=context_span.end_line,
                    fallback=unit.unit_kind == "fallback",
                )
                if case.mode == "diff" and case.hunks and list(
                    changed_new_lines
                ) != assigned_hunk_lines:
                    violations.append(
                        f"{prefix}.changed_new_lines does not match its input hunks"
                    )
                expected_unit_changed_lines = [
                    line - context_span.start_line + 1
                    for line in expected_changed_new_lines
                ]
                if list(unit.unit_changed_lines) != expected_unit_changed_lines:
                    violations.append(
                        f"{prefix}.unit_changed_lines does not map context lines"
                    )
                outside = sorted(set(file_changed_lines) - set(expected_changed_new_lines))
                diagnosed_outside = sorted(
                    {
                        line
                        for diagnostic in diagnostics
                        if diagnostic["code"] == "changed_lines_outside_context"
                        for line in diagnostic["lines"]
                    }
                )
                if outside != diagnosed_outside:
                    violations.append(
                        f"{prefix}.outside changed lines do not match diagnostic: "
                        f"expected {outside}, actual {diagnosed_outside}"
                    )
                if case.mode == "full" and file_changed_lines:
                    violations.append(f"{prefix}.file_changed_lines must be empty in full mode")
                if case.mode == "diff" and case.hunks and any(
                    line not in hunk_lines for line in file_changed_lines
                ):
                    violations.append(
                        f"{prefix}.file_changed_lines contains a line outside all input hunks"
                    )
                if any(line > source_line_count for line in file_changed_lines):
                    violations.append(f"{prefix}.file_changed_lines exceeds the source")
        violations.extend(
            _diagnostic_semantic_violations(
                diagnostics,
                prefix=f"{prefix}.diagnostics",
                source_line_count=source_line_count,
                hunk_lines=hunk_lines,
                context_span=context_span,
                result_level=False,
            )
        )
        for field in (
            "unit_ref",
            "changed_lines",
            "file_changed_lines",
            "unit_changed_lines",
            "full_text",
        ):
            if not hasattr(unit, field):
                violations.append(f"{prefix} is missing compatibility field {field}")

    if len(unit_ids) != len(set(unit_ids)):
        violations.append("unit_id values are not unique")
    if order_keys and order_keys != sorted(order_keys):
        violations.append("ReviewUnit output order is not deterministic source order")
    return violations


def _matches_frozen_declaration(
    unit: ReviewUnit,
    declarations: tuple[Declaration, ...],
) -> bool:
    return any(
        declaration.kind == unit.unit_kind
        and declaration.qualified_name == unit.unit_symbol
        and declaration.span.start_line == unit.source_span.start_line
        and declaration.span.end_line == unit.source_span.end_line
        for declaration in declarations
    )


def _expected_host_summary(
    case: ReviewUnitGoldenCase,
    unit: ReviewUnit,
    source: str,
) -> HostSummary:
    host = _frozen_host_for_span(
        case.declarations,
        unit.source_span.start_line,
        unit.source_span.end_line,
    )
    if host is None:
        return HostSummary()

    host_text = extract_lines(source, host.span.start_line, host.span.end_line)
    lifecycle = sorted(
        {
            declaration.name
            for declaration in case.declarations
            if declaration.kind in {"method", "build_method", "builder"}
            and declaration.name in LIFECYCLE_SYMBOLS
            and _same_declaration_occurrence(
                _frozen_host_for_span(
                    case.declarations,
                    declaration.span.start_line,
                    declaration.span.end_line,
                ),
                host,
            )
        }
    )
    return HostSummary(
        struct=host.name,
        decorators=_frozen_host_decorators(host_text, host.name),
        states=_frozen_host_states(host_text),
        lifecycle=lifecycle,
        # The v1 Golden parser fixture deliberately freezes declarations and
        # quality only; it does not provide ImportInfo file hints.
        imports=[],
    )


def _frozen_host_for_span(
    declarations: tuple[Declaration, ...],
    start_line: int,
    end_line: int,
) -> Declaration | None:
    hosts = [
        declaration
        for declaration in declarations
        if declaration.kind in {"struct", "class"}
        and declaration.span.contains_line_range(start_line, end_line)
    ]
    if not hosts:
        return None
    return min(
        hosts,
        key=lambda declaration: (
            declaration.line_count,
            declaration.span.start_line,
            declaration.span.end_line,
            declaration.qualified_name,
        ),
    )


def _same_declaration_occurrence(
    first: Declaration | None,
    second: Declaration,
) -> bool:
    return first is not None and (
        first.kind,
        first.qualified_name,
        first.span.start_line,
        first.span.end_line,
    ) == (
        second.kind,
        second.qualified_name,
        second.span.start_line,
        second.span.end_line,
    )


def _frozen_host_decorators(host_text: str, host_name: str) -> list[str]:
    declaration_match = re.search(
        rf"\b(?:struct|class)\s+{re.escape(host_name)}\b",
        host_text,
    )
    leading_text = (
        host_text[: declaration_match.start()]
        if declaration_match is not None
        else ""
    )
    return sorted(
        {
            match.group(1)
            for match in re.finditer(
                r"(?m)^[ \t]*(@(?:ComponentV2|Component|Entry))\b",
                leading_text,
            )
        }
    )


def _frozen_host_states(host_text: str) -> list[str]:
    decorator_names = "|".join(
        re.escape(decorator.removeprefix("@")) for decorator in STATE_DECORATORS
    )
    pattern = re.compile(rf"@(?:{decorator_names})\s+[^\n;]+")
    return [" ".join(match.group(0).split()) for match in pattern.finditer(host_text)]


def _diagnostic_semantic_violations(
    diagnostics: list[dict[str, Any]],
    *,
    prefix: str,
    source_line_count: int,
    hunk_lines: set[int],
    context_span: Any,
    result_level: bool,
) -> list[str]:
    violations: list[str] = []
    out_of_range_hunk_lines = sorted(
        line for line in hunk_lines if line > source_line_count
    )
    for index, diagnostic in enumerate(diagnostics):
        item_prefix = f"{prefix}[{index}]"
        code = diagnostic.get("code")
        lines = diagnostic.get("lines")
        if code not in REVIEW_UNIT_DIAGNOSTIC_CODES:
            violations.append(f"{item_prefix}.code is unsupported")
            continue
        if not isinstance(lines, list) or any(
            not isinstance(line, int) or isinstance(line, bool) or line < 1
            for line in lines
        ):
            violations.append(f"{item_prefix}.lines must contain 1-based integers")
            continue
        if lines != sorted(set(lines)):
            violations.append(f"{item_prefix}.lines is not sorted and unique")
        if code == "hunk_out_of_range":
            if not result_level:
                violations.append(f"{item_prefix} is only valid at result level")
            if lines != out_of_range_hunk_lines or not lines:
                violations.append(
                    f"{item_prefix}.lines does not equal the out-of-range hunk lines"
                )
        elif code == "changed_lines_outside_context":
            if result_level or context_span is None:
                violations.append(f"{item_prefix} is only valid on a ReviewUnit")
            else:
                if any(line not in hunk_lines for line in lines):
                    violations.append(f"{item_prefix}.lines contains a non-hunk line")
                if any(context_span.start_line <= line <= context_span.end_line for line in lines):
                    violations.append(f"{item_prefix}.lines contains a context line")
        elif lines:
            violations.append(f"{item_prefix}.lines must be empty for code {code!r}")
    return violations


def is_perfect(report: dict[str, Any], target_phase: str | None = None) -> bool:
    if not _report_is_self_consistent(report):
        return False
    if target_phase is not None and target_phase not in TARGET_PHASES:
        return False
    cases = report.get("cases")
    assert isinstance(cases, list)
    selected_phases = (
        TARGET_PHASES
        if target_phase is None
        else TARGET_PHASES[: TARGET_PHASES.index(target_phase) + 1]
    )
    selected = [
        case
        for case in cases
        if case.get("target_phase") in selected_phases
    ]
    if not selected or not all(case.get("matched") is True for case in selected):
        return False
    if target_phase is None:
        return True
    return all(
        not case.get("invariant_violations") or _is_allowed_fail_closed_error(case)
        for case in cases
    )


def _report_is_self_consistent(report: object) -> bool:
    """Reject incomplete or internally forged reports before applying a phase gate."""

    try:
        report_data = _mapping(report, "report")
        _require_exact_fields(report_data, REPORT_FIELDS, "report")
        if report_data.get("schema_version") != SCHEMA_VERSION:
            return False
        _string(report_data.get("suite_id"), "report.suite_id")
        _string(report_data.get("implementation"), "report.implementation")
        _sha256(report_data.get("manifest_sha256"), "report.manifest_sha256")

        case_count = _nonnegative_int(report_data.get("case_count"), "report.case_count")
        if not 12 <= case_count <= 16:
            return False
        cases = _list(report_data.get("cases"), "report.cases")
        if len(cases) != case_count:
            return False

        phase_case_counts = _phase_count_mapping(
            report_data.get("phase_case_counts"),
            "report.phase_case_counts",
        )
        phase_mismatch_counts = _phase_count_mapping(
            report_data.get("phase_mismatch_counts"),
            "report.phase_mismatch_counts",
        )
        if any(phase_case_counts[phase] < 1 for phase in TARGET_PHASES):
            return False
        if sum(phase_case_counts.values()) != case_count:
            return False

        observed_phase_counts: Counter[str] = Counter()
        observed_phase_mismatches: Counter[str] = Counter()
        case_ids: list[str] = []
        matched_count = 0
        for index, raw_case in enumerate(cases):
            context = f"report.cases[{index}]"
            case = _mapping(raw_case, context)
            _require_exact_fields(case, CASE_RESULT_FIELDS, context)
            case_id = _string(case.get("case_id"), f"{context}.case_id")
            target = _string(case.get("target_phase"), f"{context}.target_phase")
            if target not in TARGET_PHASES:
                return False
            _string(case.get("logical_path"), f"{context}.logical_path")
            _sha256(case.get("source_sha256"), f"{context}.source_sha256")
            _mapping(case.get("provenance"), f"{context}.provenance")
            _mapping(case.get("input"), f"{context}.input")
            expected = _mapping(case.get("expected"), f"{context}.expected")
            actual = _mapping(case.get("actual"), f"{context}.actual")
            _list(case.get("legacy_units"), f"{context}.legacy_units")
            differences = _string_list(
                case.get("differences"),
                f"{context}.differences",
                allow_empty=True,
            )
            invariants = _string_list(
                case.get("invariant_violations"),
                f"{context}.invariant_violations",
                allow_empty=True,
            )
            if differences != sorted(set(differences)):
                return False
            if differences != _differences(expected, actual):
                return False
            if invariants != sorted(set(invariants)):
                return False
            _boolean(case.get("repeat_equal"), f"{context}.repeat_equal")
            reversed_equal = case.get("reversed_hunks_equal")
            if reversed_equal is not None:
                _boolean(reversed_equal, f"{context}.reversed_hunks_equal")
            error = case.get("error")
            if error is not None:
                _string(error, f"{context}.error")
            matched = _boolean(case.get("matched"), f"{context}.matched")
            if matched != (not differences and not invariants and error is None):
                return False

            case_ids.append(case_id)
            observed_phase_counts[target] += 1
            if matched:
                matched_count += 1
            else:
                observed_phase_mismatches[target] += 1

        if case_ids != sorted(case_ids) or len(case_ids) != len(set(case_ids)):
            return False
        if phase_case_counts != {
            phase: observed_phase_counts.get(phase, 0) for phase in TARGET_PHASES
        }:
            return False
        if phase_mismatch_counts != {
            phase: observed_phase_mismatches.get(phase, 0) for phase in TARGET_PHASES
        }:
            return False
        declared_matched = _nonnegative_int(
            report_data.get("matched_case_count"),
            "report.matched_case_count",
        )
        declared_mismatched = _nonnegative_int(
            report_data.get("mismatched_case_count"),
            "report.mismatched_case_count",
        )
        return (
            declared_matched == matched_count
            and declared_mismatched == case_count - matched_count
        )
    except ValueError:
        return False


def _phase_count_mapping(value: object, context: str) -> dict[str, int]:
    mapping = _mapping(value, context)
    _require_exact_fields(mapping, TARGET_PHASES, context)
    return {
        phase: _nonnegative_int(mapping.get(phase), f"{context}.{phase}")
        for phase in TARGET_PHASES
    }


def _is_allowed_fail_closed_error(case: dict[str, Any]) -> bool:
    error = case.get("error")
    expected = case.get("expected")
    input_data = case.get("input")
    provenance = case.get("provenance")
    if (
        not isinstance(error, str)
        or not isinstance(expected, dict)
        or expected.get("units") != []
        or not isinstance(input_data, dict)
        or not isinstance(provenance, dict)
    ):
        return False
    origin_lines = provenance.get("origin_lines")
    hunks = input_data.get("hunks")
    if (
        not isinstance(origin_lines, list)
        or len(origin_lines) != 2
        or not all(isinstance(line, int) for line in origin_lines)
        or not isinstance(hunks, list)
    ):
        return False
    source_line_count = origin_lines[1] - origin_lines[0] + 1
    if len(hunks) != 1 or not isinstance(hunks[0], dict):
        return False
    hunk = hunks[0]
    new_start = hunk.get("new_start")
    new_lines = hunk.get("new_lines")
    if (
        not isinstance(new_start, int)
        or isinstance(new_start, bool)
        or not isinstance(new_lines, int)
        or isinstance(new_lines, bool)
        or new_start < 1
        or new_lines < 1
    ):
        return False
    new_end = new_start + new_lines - 1
    if new_end <= source_line_count:
        return False
    outside_lines = list(range(max(new_start, source_line_count + 1), new_end + 1))
    expected_diagnostics = expected.get("diagnostics")
    expected_error = repr(
        ValueError(
            f"hunk L{new_start}-L{new_end} exceeds source line count {source_line_count}"
        )
    )
    expected_invariant = f"builder execution failed: {expected_error}"
    return (
        error == expected_error
        and expected_diagnostics
        == [{"code": "hunk_out_of_range", "lines": outside_lines}]
        and case.get("actual") == {"units": [], "diagnostics": []}
        and case.get("legacy_units") == []
        and case.get("repeat_equal") is False
        and case.get("reversed_hunks_equal") is None
        and case.get("invariant_violations") == [expected_invariant]
    )


def format_golden_report(report: dict[str, Any]) -> str:
    lines = [
        f"ReviewUnit Golden report: {report['suite_id']}",
        f"  implementation: {report['implementation']}",
        f"  cases: {report['case_count']}",
        f"  matched: {report['matched_case_count']}",
        f"  mismatched: {report['mismatched_case_count']}",
    ]
    phase_counts = report["phase_case_counts"]
    phase_mismatches = report["phase_mismatch_counts"]
    for phase in TARGET_PHASES:
        lines.append(
            f"  {phase}: cases={phase_counts[phase]} mismatched={phase_mismatches[phase]}"
        )
    mismatched_cases = [case for case in report["cases"] if not case["matched"]]
    if mismatched_cases:
        lines.append("  differences:")
        for case in mismatched_cases:
            summary = "; ".join(
                [*case["differences"][:3], *case["invariant_violations"][:2]]
            )
            lines.append(f"    {case['case_id']} ({case['target_phase']}): {summary}")
    return "\n".join(lines)


def make_baseline(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "suite_id": report["suite_id"],
        "implementation": report["implementation"],
        "report": report,
    }


def load_golden_baseline(
    baseline_path: Path,
    *,
    suite: ReviewUnitGoldenSuite,
) -> dict[str, Any]:
    data = _load_json_mapping(baseline_path.resolve(), "baseline")
    _require_exact_fields(data, BASELINE_FIELDS, "baseline")
    if data.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError(
            f"baseline.schema_version must be {BASELINE_SCHEMA_VERSION!r}, "
            f"got {data.get('schema_version')!r}"
        )
    if data.get("suite_id") != suite.suite_id:
        raise ValueError("baseline.suite_id does not match the Golden suite")
    if data.get("implementation") != "ReviewUnitBuilder":
        raise ValueError("baseline.implementation must be 'ReviewUnitBuilder'")
    report = _mapping(data.get("report"), "baseline.report")
    _validate_baseline_report(report, suite)
    return data


def _validate_baseline_report(
    report: dict[str, Any],
    suite: ReviewUnitGoldenSuite,
) -> None:
    context = "baseline.report"
    _require_exact_fields(report, REPORT_FIELDS, context)
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{context}.schema_version does not match")
    if report.get("suite_id") != suite.suite_id:
        raise ValueError(f"{context}.suite_id does not match")
    if report.get("implementation") != "ReviewUnitBuilder":
        raise ValueError(f"{context}.implementation does not match")
    expected_manifest_hash = suite.manifest_sha256
    if report.get("manifest_sha256") != expected_manifest_hash:
        raise ValueError(f"{context}.manifest_sha256 does not match the manifest")
    if _nonnegative_int(report.get("case_count"), f"{context}.case_count") != len(
        suite.cases
    ):
        raise ValueError(f"{context}.case_count does not match the suite")

    raw_cases = _list(report.get("cases"), f"{context}.cases")
    if len(raw_cases) != len(suite.cases):
        raise ValueError(f"{context}.cases length does not match the suite")
    matched_count = 0
    phase_counts: Counter[str] = Counter()
    phase_mismatches: Counter[str] = Counter()
    for index, (raw_case, golden_case) in enumerate(zip(raw_cases, suite.cases, strict=True)):
        case_context = f"{context}.cases[{index}]"
        case_result = _mapping(raw_case, case_context)
        _require_exact_fields(case_result, CASE_RESULT_FIELDS, case_context)
        if case_result.get("case_id") != golden_case.case_id:
            raise ValueError(f"{case_context}.case_id does not match manifest order")
        if case_result.get("target_phase") != golden_case.target_phase:
            raise ValueError(f"{case_context}.target_phase does not match")
        if case_result.get("logical_path") != golden_case.logical_path:
            raise ValueError(f"{case_context}.logical_path does not match")
        if case_result.get("source_sha256") != golden_case.source_metadata["content_sha256"]:
            raise ValueError(f"{case_context}.source_sha256 does not match")
        if not reports_equal(case_result.get("provenance"), golden_case.source_metadata):
            raise ValueError(f"{case_context}.provenance does not match")
        if not reports_equal(case_result.get("input"), golden_case.input_projection()):
            raise ValueError(f"{case_context}.input does not match")
        if not reports_equal(case_result.get("expected"), golden_case.expected):
            raise ValueError(f"{case_context}.expected does not match")
        actual_result = _mapping(case_result.get("actual"), f"{case_context}.actual")
        _validate_actual_result(actual_result, f"{case_context}.actual")
        legacy_units = _list(
            case_result.get("legacy_units"), f"{case_context}.legacy_units"
        )
        if len(legacy_units) != len(actual_result["units"]):
            raise ValueError(f"{case_context}.legacy_units length does not match actual units")
        for unit_index, raw_unit in enumerate(legacy_units):
            unit_context = f"{case_context}.legacy_units[{unit_index}]"
            unit = _mapping(raw_unit, unit_context)
            _require_exact_fields(unit, LEGACY_UNIT_FIELDS, unit_context)
            _string(unit.get("unit_ref"), f"{unit_context}.unit_ref")
            for field in ("changed_lines", "file_changed_lines", "unit_changed_lines"):
                _validated_lines(unit.get(field), f"{unit_context}.{field}", allow_empty=True)
            _sha256(unit.get("full_text_sha256"), f"{unit_context}.full_text_sha256")
        differences = _string_list(
            case_result.get("differences"),
            f"{case_context}.differences",
            allow_empty=True,
        )
        invariants = _string_list(
            case_result.get("invariant_violations"),
            f"{case_context}.invariant_violations",
            allow_empty=True,
        )
        if differences != sorted(set(differences)):
            raise ValueError(f"{case_context}.differences must be sorted and unique")
        expected_differences = _differences(golden_case.expected, actual_result)
        if differences != expected_differences:
            raise ValueError(f"{case_context}.differences do not match expected versus actual")
        if invariants != sorted(set(invariants)):
            raise ValueError(f"{case_context}.invariant_violations must be sorted and unique")
        raw_error = case_result.get("error")
        repeat_equal = _boolean(
            case_result.get("repeat_equal"), f"{case_context}.repeat_equal"
        )
        if (
            raw_error is None
            and not repeat_equal
            and "repeated execution changed ReviewUnit output" not in invariants
        ):
            raise ValueError(f"{case_context}.repeat_equal is inconsistent")
        if repeat_equal and "repeated execution changed ReviewUnit output" in invariants:
            raise ValueError(f"{case_context}.repeat_equal is inconsistent")
        reversed_value = case_result.get("reversed_hunks_equal")
        if len(golden_case.hunks) > 1:
            if reversed_value is None:
                raise ValueError(
                    f"{case_context}.reversed_hunks_equal must record multi-hunk evidence"
                )
            reversed_equal = _boolean(
                reversed_value, f"{case_context}.reversed_hunks_equal"
            )
            if (
                not reversed_equal
                and "reversing hunks changed ReviewUnit output" not in invariants
            ):
                raise ValueError(f"{case_context}.reversed_hunks_equal is inconsistent")
            if reversed_equal and "reversing hunks changed ReviewUnit output" in invariants:
                raise ValueError(f"{case_context}.reversed_hunks_equal is inconsistent")
        elif reversed_value is not None:
            raise ValueError(
                f"{case_context}.reversed_hunks_equal must be null for fewer than two hunks"
            )
        error = raw_error
        if error is not None:
            _string(error, f"{case_context}.error")
            expected_error_invariant = f"builder execution failed: {error}"
            if expected_error_invariant not in invariants:
                raise ValueError(f"{case_context}.error is not exposed by invariant_violations")
            if repeat_equal or actual_result["units"] or legacy_units:
                raise ValueError(f"{case_context}.error payload is inconsistent")
        matched = _boolean(case_result.get("matched"), f"{case_context}.matched")
        expected_matched = not differences and not invariants and error is None
        if matched != expected_matched:
            raise ValueError(f"{case_context}.matched is inconsistent")
        source = golden_case.read_source()
        semantic_invariants = _serialized_case_invariants(
            golden_case,
            actual_result,
            legacy_units,
            source,
        )
        missing_semantic_invariants = sorted(set(semantic_invariants) - set(invariants))
        if missing_semantic_invariants:
            raise ValueError(
                f"{case_context}.invariant_violations does not expose invalid actual data: "
                f"{missing_semantic_invariants}"
            )
        if matched and semantic_invariants:
            raise ValueError(f"{case_context}.matched result violates ReviewUnit semantics")
        matched_count += int(matched)
        phase_counts[golden_case.target_phase] += 1
        if not matched:
            phase_mismatches[golden_case.target_phase] += 1

    declared_matched = _nonnegative_int(
        report.get("matched_case_count"), f"{context}.matched_case_count"
    )
    declared_mismatched = _nonnegative_int(
        report.get("mismatched_case_count"), f"{context}.mismatched_case_count"
    )
    if declared_matched != matched_count or declared_mismatched != len(suite.cases) - matched_count:
        raise ValueError(f"{context} aggregate match counts are inconsistent")
    _validate_phase_counts(report.get("phase_case_counts"), phase_counts, context)
    _validate_phase_counts(report.get("phase_mismatch_counts"), phase_mismatches, context)


def _serialized_case_invariants(
    case: ReviewUnitGoldenCase,
    actual: dict[str, Any],
    legacy_units: list[Any],
    source: str,
) -> list[str]:
    violations: list[str] = []
    source_line_count = max(1, len(source.splitlines()))
    hunk_lines = {
        line
        for hunk in case.hunks
        for line in range(hunk.new_start, hunk.new_end + 1)
    }
    violations.extend(
        _diagnostic_semantic_violations(
            actual["diagnostics"],
            prefix="result.diagnostics",
            source_line_count=source_line_count,
            hunk_lines=hunk_lines,
            context_span=None,
            result_level=True,
        )
    )
    unit_ids: list[str] = []
    order: list[tuple[int, int, int, int, str]] = []
    for index, (unit, raw_legacy) in enumerate(
        zip(actual["units"], legacy_units, strict=True)
    ):
        prefix = f"units[{index}]"
        legacy = _mapping(raw_legacy, f"legacy_units[{index}]")
        unit_id = unit["unit_id"]
        context_span = unit["context_span"]
        source_span = unit["source_span"]
        changed_new_lines = unit["changed_new_lines"]
        if unit_id is None:
            violations.append(f"{prefix}.unit_id is unavailable")
        if context_span is None:
            violations.append(f"{prefix}.context_span is unavailable")
        if changed_new_lines is None:
            violations.append(f"{prefix}.changed_new_lines is unavailable")
        if (
            unit_id is None
            or unit["unit_kind"] is None
            or source_span is None
            or context_span is None
            or changed_new_lines is None
            or unit["selection_reason"] is None
        ):
            continue

        unit_ids.append(unit_id)
        if (
            source_span["start_line"] < 1
            or source_span["end_line"] < source_span["start_line"]
            or source_span["end_line"] > source_line_count
        ):
            violations.append(f"{prefix}.source_span is outside the source")
        if (
            context_span["start_line"] < 1
            or context_span["end_line"] < context_span["start_line"]
            or context_span["end_line"] > source_line_count
        ):
            violations.append(f"{prefix}.context_span is outside the source")
        if not (
            context_span["start_line"] <= source_span["start_line"]
            and source_span["end_line"] <= context_span["end_line"]
        ):
            violations.append(f"{prefix}.source_span is not inside context_span")
        expected_id = format_unit_id(
            case.logical_path,
            unit["unit_kind"],
            unit["unit_symbol"],
            source_span,
            context_span,
        )
        if unit_id != expected_id:
            violations.append(f"{prefix}.unit_id does not match identity fields")
        if unit["unit_kind"] == "fallback":
            expected_symbol = (
                f"hunk-L{source_span['start_line']}-L{source_span['end_line']}"
            )
            if unit["unit_symbol"] != expected_symbol:
                violations.append(f"{prefix}.fallback symbol does not match source_span")
        elif not any(
            declaration.kind == unit["unit_kind"]
            and declaration.qualified_name == unit["unit_symbol"]
            and declaration.span.start_line == source_span["start_line"]
            and declaration.span.end_line == source_span["end_line"]
            for declaration in case.declarations
        ):
            violations.append(
                f"{prefix} owner does not match a frozen declaration occurrence"
            )

        expected_ref = f"{unit['unit_symbol']}@{case.logical_path}"
        if legacy["unit_ref"] != expected_ref:
            violations.append(f"{prefix}.unit_ref does not preserve unit_symbol@logical_path")
        expected_text_hash = hashlib.sha256(
            extract_lines(
                source,
                context_span["start_line"],
                context_span["end_line"],
            ).encode("utf-8")
        ).hexdigest()
        if legacy["full_text_sha256"] != expected_text_hash:
            violations.append(f"{prefix}.full_text does not equal context_span slice")
        file_changed_lines = legacy["file_changed_lines"]
        if legacy["changed_lines"] != file_changed_lines:
            violations.append(f"{prefix}.changed_lines differs from file_changed_lines")
        projected_lines = [
            line
            for line in file_changed_lines
            if context_span["start_line"] <= line <= context_span["end_line"]
        ]
        if changed_new_lines != projected_lines:
            violations.append(
                f"{prefix}.changed_new_lines does not project file_changed_lines"
            )
        assigned_hunk_lines = _assigned_hunk_lines(
            case.hunks,
            source_span_start=source_span["start_line"],
            source_span_end=source_span["end_line"],
            context_span_start=context_span["start_line"],
            context_span_end=context_span["end_line"],
            fallback=unit["unit_kind"] == "fallback",
        )
        if case.mode == "diff" and case.hunks and changed_new_lines != assigned_hunk_lines:
            violations.append(f"{prefix}.changed_new_lines does not match its input hunks")
        relative_lines = [
            line - context_span["start_line"] + 1 for line in changed_new_lines
        ]
        if legacy["unit_changed_lines"] != relative_lines:
            violations.append(f"{prefix}.unit_changed_lines does not map context lines")
        if case.mode == "full" and file_changed_lines:
            violations.append(f"{prefix}.file_changed_lines must be empty in full mode")
        if case.mode == "diff" and case.hunks and any(
            line not in hunk_lines for line in file_changed_lines
        ):
            violations.append(
                f"{prefix}.file_changed_lines contains a line outside all input hunks"
            )
        if any(line > source_line_count for line in file_changed_lines):
            violations.append(f"{prefix}.file_changed_lines exceeds the source")
        violations.extend(
            _diagnostic_semantic_violations(
                unit["diagnostics"],
                prefix=f"{prefix}.diagnostics",
                source_line_count=source_line_count,
                hunk_lines=hunk_lines,
                context_span=ReviewUnitSpan(**context_span),
                result_level=False,
            )
        )
        order.append(
            (
                context_span["start_line"],
                context_span["end_line"],
                source_span["start_line"],
                source_span["end_line"],
                unit_id,
            )
        )
    if len(unit_ids) != len(set(unit_ids)):
        violations.append("unit_id values are not unique")
    if order != sorted(order):
        violations.append("ReviewUnit output order is not deterministic source order")
    return sorted(set(violations))


def _validate_phase_counts(value: object, counts: Counter[str], context: str) -> None:
    mapping = _mapping(value, f"{context}.phase_counts")
    _require_exact_fields(mapping, TARGET_PHASES, f"{context}.phase_counts")
    expected = {phase: counts.get(phase, 0) for phase in TARGET_PHASES}
    actual = {
        phase: _nonnegative_int(mapping.get(phase), f"{context}.phase_counts.{phase}")
        for phase in TARGET_PHASES
    }
    if actual != expected:
        raise ValueError(f"{context}.phase_counts are inconsistent")


def _validate_actual_result(value: dict[str, Any], context: str) -> None:
    _require_exact_fields(value, EXPECTED_FIELDS, context)
    _validate_diagnostics(value.get("diagnostics"), f"{context}.diagnostics")
    for index, raw_unit in enumerate(_list(value.get("units"), f"{context}.units")):
        unit_context = f"{context}.units[{index}]"
        unit = _mapping(raw_unit, unit_context)
        _require_exact_fields(unit, UNIT_FIELDS, unit_context)
        for nullable_string in ("unit_id", "unit_kind", "selection_reason"):
            field_value = unit.get(nullable_string)
            if field_value is not None:
                _string(field_value, f"{unit_context}.{nullable_string}")
        unit_kind = unit.get("unit_kind")
        if unit_kind is not None and unit_kind not in REVIEW_UNIT_KINDS:
            raise ValueError(f"{unit_context}.unit_kind is unsupported")
        selection_reason = unit.get("selection_reason")
        if selection_reason is not None and selection_reason not in SELECTION_REASONS:
            raise ValueError(f"{unit_context}.selection_reason is unsupported")
        _string(unit.get("unit_symbol"), f"{unit_context}.unit_symbol")
        for span_name in ("source_span", "context_span"):
            span = unit.get(span_name)
            if span is not None:
                _validate_span(span, f"{unit_context}.{span_name}", source_line_count=None)
        changed = unit.get("changed_new_lines")
        if changed is not None:
            _validated_lines(changed, f"{unit_context}.changed_new_lines", allow_empty=True)
        _boolean(unit.get("context_degraded"), f"{unit_context}.context_degraded")
        _validate_diagnostics(unit.get("diagnostics"), f"{unit_context}.diagnostics")


def _load_case(
    raw: dict[str, Any],
    root: Path,
    index: int,
) -> ReviewUnitGoldenCase:
    context = f"manifest.cases[{index}]"
    _require_exact_fields(raw, CASE_FIELDS, context)
    case_id = _string(raw.get("case_id"), f"{context}.case_id")
    description = _string(raw.get("description"), f"{context}.description")
    target_phase = _string(raw.get("target_phase"), f"{context}.target_phase")
    if target_phase not in TARGET_PHASES:
        raise ValueError(f"{context}.target_phase is unsupported: {target_phase}")
    logical_path = _portable_path(raw.get("logical_path"), f"{context}.logical_path")
    if not logical_path.endswith(".ets"):
        raise ValueError(f"{context}.logical_path must end in .ets")

    source = _mapping(raw.get("source"), f"{context}.source")
    _require_exact_fields(source, SOURCE_FIELDS, f"{context}.source")
    source_file = _portable_path(source.get("file"), f"{context}.source.file")
    source_candidate = root / source_file
    if _path_contains_symlink(root, source_candidate):
        raise ValueError(f"{context}.source.file must not traverse a symlink")
    source_path = source_candidate.resolve()
    if not source_path.is_relative_to(root.resolve()) or not source_path.is_file():
        raise ValueError(f"{context}.source.file does not identify a self-contained source")
    expected_hash = _sha256(source.get("content_sha256"), f"{context}.source.content_sha256")
    source_text = _read_source_snapshot(source_path, expected_hash, f"{context}.source.file")
    source_kind = _string(source.get("kind"), f"{context}.source.kind")
    if source_kind != "synthetic":
        raise ValueError(f"{context}.source.kind is unsupported: {source_kind}")
    source_id = _string(source.get("source_id"), f"{context}.source.source_id")
    revision = _string(source.get("revision"), f"{context}.source.revision")
    relative_path = _portable_path(
        source.get("relative_path"), f"{context}.source.relative_path"
    )
    if (
        source_id != "review-unit-golden"
        or revision != "review-unit-golden-v1"
        or not relative_path.startswith("synthetic/")
    ):
        raise ValueError(f"{context}.source synthetic provenance drift")
    if (
        not source_file.startswith("sources/")
        or not relative_path.startswith("synthetic/")
        or not source_file.endswith(".ets")
        or not relative_path.endswith(".ets")
    ):
        raise ValueError(f"{context}.source paths must end in .ets")
    if source_file.removeprefix("sources/") != relative_path.removeprefix("synthetic/"):
        raise ValueError(f"{context}.source file and relative_path identify different snapshots")
    line_count = max(1, len(source_text.splitlines()))
    raw_origin_lines = _list(
        source.get("origin_lines"), f"{context}.source.origin_lines"
    )
    origin_lines = [
        _positive_int(item, f"{context}.source.origin_lines[]")
        for item in raw_origin_lines
    ]
    if len(origin_lines) != 2 or origin_lines[1] < origin_lines[0]:
        raise ValueError(f"{context}.source.origin_lines must be [start_line, end_line]")
    if origin_lines[1] - origin_lines[0] + 1 != line_count:
        raise ValueError(f"{context}.source.origin_lines length does not match the snapshot")
    if origin_lines != [1, line_count]:
        raise ValueError(f"{context}.source synthetic origin_lines must be [1, line_count]")

    input_data = _mapping(raw.get("input"), f"{context}.input")
    _require_exact_fields(input_data, INPUT_FIELDS, f"{context}.input")
    mode = _string(input_data.get("mode"), f"{context}.input.mode")
    if mode not in {"full", "diff"}:
        raise ValueError(f"{context}.input.mode must be 'full' or 'diff'")
    raw_hunks = _list(input_data.get("hunks"), f"{context}.input.hunks")
    hunks: list[FileHunk] = []
    seen_hunks: set[tuple[int, int]] = set()
    for hunk_index, raw_hunk in enumerate(raw_hunks):
        hunk_context = f"{context}.input.hunks[{hunk_index}]"
        hunk = _mapping(raw_hunk, hunk_context)
        _require_exact_fields(hunk, HUNK_FIELDS, hunk_context)
        new_start = _positive_int(hunk.get("new_start"), f"{hunk_context}.new_start")
        new_lines = _positive_int(hunk.get("new_lines"), f"{hunk_context}.new_lines")
        identity = (new_start, new_lines)
        if identity in seen_hunks:
            raise ValueError(f"{context}.input.hunks must not contain duplicates")
        seen_hunks.add(identity)
        hunks.append(FileHunk(new_start=new_start, new_lines=new_lines))
    if mode == "full" and hunks:
        raise ValueError(f"{context}.input.hunks must be empty in full mode")
    token_budget = input_data.get("token_budget")
    if token_budget is not None:
        token_budget = _positive_int(token_budget, f"{context}.input.token_budget")
    unsupported = tuple(
        _string(item, f"{context}.input.unsupported[]")
        for item in _list(input_data.get("unsupported"), f"{context}.input.unsupported")
    )
    if list(unsupported) != sorted(set(unsupported)):
        raise ValueError(f"{context}.input.unsupported must be sorted and unique")
    if not set(unsupported).issubset(UNSUPPORTED_INPUTS):
        raise ValueError(f"{context}.input.unsupported contains an unknown value")

    parser_fixture = _mapping(
        input_data.get("parser_fixture"),
        f"{context}.input.parser_fixture",
    )
    _require_exact_fields(
        parser_fixture,
        PARSER_FIXTURE_FIELDS,
        f"{context}.input.parser_fixture",
    )
    parser_layer = _string(
        parser_fixture.get("parser_layer"),
        f"{context}.input.parser_fixture.parser_layer",
    )
    if parser_layer not in {"L0", "L1", "parse_degraded"}:
        raise ValueError(f"{context}.input.parser_fixture.parser_layer is unsupported")
    warnings = tuple(
        _string_list(
            parser_fixture.get("warnings"),
            f"{context}.input.parser_fixture.warnings",
            allow_empty=True,
        )
    )
    if list(warnings) != sorted(set(warnings)):
        raise ValueError(f"{context}.input.parser_fixture.warnings must be sorted and unique")
    declarations = tuple(
        _load_declaration(value, context, declaration_index, source_text, line_count)
        for declaration_index, value in enumerate(
            _list(
                parser_fixture.get("declarations"),
                f"{context}.input.parser_fixture.declarations",
            )
        )
    )
    _validate_declaration_order_and_parents(declarations, context)

    expected = _load_expected(
        raw.get("expected"),
        context,
        logical_path,
        line_count,
        mode=mode,
        hunks=tuple(hunks),
        declarations=declarations,
        parser_layer=parser_layer,
        warnings=warnings,
        token_budget=token_budget,
        unsupported=unsupported,
    )
    return ReviewUnitGoldenCase(
        case_id=case_id,
        description=description,
        target_phase=target_phase,
        logical_path=logical_path,
        source_path=source_path,
        source_metadata=dict(source),
        parser_layer=parser_layer,
        warnings=warnings,
        declarations=declarations,
        mode=mode,
        hunks=tuple(hunks),
        token_budget=token_budget,
        unsupported=unsupported,
        expected=expected,
    )


def _load_declaration(
    value: object,
    case_context: str,
    index: int,
    source: str,
    line_count: int,
) -> Declaration:
    context = f"{case_context}.input.parser_fixture.declarations[{index}]"
    declaration = _mapping(value, context)
    _require_exact_fields(declaration, DECLARATION_FIELDS, context)
    kind = _string(declaration.get("kind"), f"{context}.kind")
    if kind not in REVIEW_UNIT_KINDS or kind == "fallback":
        raise ValueError(f"{context}.kind is not a declaration kind: {kind}")
    span_data = _validate_span(declaration.get("span"), f"{context}.span", line_count)
    span = SourceSpan(**span_data)
    parent_name = declaration.get("parent_name")
    if parent_name is not None:
        parent_name = _string(parent_name, f"{context}.parent_name")
    return Declaration(
        kind=kind,
        name=_string(declaration.get("name"), f"{context}.name"),
        qualified_name=_string(
            declaration.get("qualified_name"), f"{context}.qualified_name"
        ),
        parent_name=parent_name,
        span=span,
        text=extract_lines(source, span.start_line, span.end_line),
    )


def _validate_declaration_order_and_parents(
    declarations: tuple[Declaration, ...],
    case_context: str,
) -> None:
    order = [
        (item.span.start_line, item.span.end_line, item.kind, item.qualified_name)
        for item in declarations
    ]
    if order != sorted(order):
        raise ValueError(f"{case_context}.input.parser_fixture.declarations must be sorted")
    seen_occurrences: set[tuple[str, str, int, int]] = set()
    for declaration in declarations:
        identity = (
            declaration.kind,
            declaration.qualified_name,
            declaration.span.start_line,
            declaration.span.end_line,
        )
        if identity in seen_occurrences:
            raise ValueError(f"{case_context}.input.parser_fixture.declarations has a duplicate")
        seen_occurrences.add(identity)
        if declaration.parent_name is None:
            continue
        if not any(
            parent.qualified_name == declaration.parent_name
            and parent.span.contains_line_range(
                declaration.span.start_line, declaration.span.end_line
            )
            for parent in declarations
        ):
            raise ValueError(
                f"{case_context}.input.parser_fixture declaration parent does not contain child"
            )


def _load_expected(
    value: object,
    case_context: str,
    logical_path: str,
    source_line_count: int,
    *,
    mode: str,
    hunks: tuple[FileHunk, ...],
    declarations: tuple[Declaration, ...],
    parser_layer: str,
    warnings: tuple[str, ...],
    token_budget: int | None,
    unsupported: tuple[str, ...],
) -> dict[str, Any]:
    context = f"{case_context}.expected"
    expected = _mapping(value, context)
    _require_exact_fields(expected, EXPECTED_FIELDS, context)
    diagnostics = _validate_diagnostics(expected.get("diagnostics"), f"{context}.diagnostics")
    units = [
        _load_expected_unit(raw_unit, context, index, logical_path, source_line_count)
        for index, raw_unit in enumerate(_list(expected.get("units"), f"{context}.units"))
    ]
    unit_ids = [unit["unit_id"] for unit in units]
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError(f"{context}.units contains duplicate unit_id values")
    order = [
        (
            unit["context_span"]["start_line"],
            unit["context_span"]["end_line"],
            unit["source_span"]["start_line"],
            unit["source_span"]["end_line"],
            unit["unit_id"],
        )
        for unit in units
    ]
    if order != sorted(order):
        raise ValueError(f"{context}.units must be in deterministic source order")
    result = {"units": units, "diagnostics": diagnostics}
    _validate_expected_semantics(
        result,
        context=context,
        mode=mode,
        hunks=hunks,
        declarations=declarations,
        parser_layer=parser_layer,
        warnings=warnings,
        token_budget=token_budget,
        unsupported=unsupported,
        source_line_count=source_line_count,
    )
    return result


def _validate_expected_semantics(
    expected: dict[str, Any],
    *,
    context: str,
    mode: str,
    hunks: tuple[FileHunk, ...],
    declarations: tuple[Declaration, ...],
    parser_layer: str,
    warnings: tuple[str, ...],
    token_budget: int | None,
    unsupported: tuple[str, ...],
    source_line_count: int,
) -> None:
    hunk_lines = {
        line
        for hunk in hunks
        for line in range(hunk.new_start, hunk.new_end + 1)
    }
    diagnostic_violations = _diagnostic_semantic_violations(
        expected["diagnostics"],
        prefix=f"{context}.diagnostics",
        source_line_count=source_line_count,
        hunk_lines=hunk_lines,
        context_span=None,
        result_level=True,
    )
    if diagnostic_violations:
        raise ValueError("; ".join(diagnostic_violations))

    required_result_codes: set[str] = set()
    if mode == "diff" and not hunks:
        required_result_codes.add("diff_file_without_hunks")
    if any(line > source_line_count for line in hunk_lines):
        required_result_codes.add("hunk_out_of_range")
    if "deletion_only" in unsupported:
        required_result_codes.add("unsupported_deletion_only")
    if token_budget is not None:
        required_result_codes.add("budget_not_enforced")
    actual_result_codes = {item["code"] for item in expected["diagnostics"]}
    if actual_result_codes != required_result_codes:
        raise ValueError(
            f"{context}.diagnostics codes must reflect the frozen case input: "
            f"expected={sorted(required_result_codes)}, actual={sorted(actual_result_codes)}"
        )

    parser_codes: set[str] = set()
    if parser_layer == "parse_degraded":
        parser_codes.add("parser_degraded")
    if any("error_nodes" in warning for warning in warnings):
        parser_codes.add("parser_error_nodes")
    if any("missing_nodes" in warning for warning in warnings):
        parser_codes.add("parser_missing_nodes")

    for index, unit in enumerate(expected["units"]):
        unit_context = f"{context}.units[{index}]"
        source_span = unit["source_span"]
        context_span = unit["context_span"]
        unit_hunk_lines = {
            line
            for hunk in hunks
            if _ranges_overlap(
                source_span["start_line"],
                source_span["end_line"],
                hunk.new_start,
                hunk.new_end,
            )
            for line in range(hunk.new_start, hunk.new_end + 1)
        }
        if mode == "full":
            expected_changed_lines: list[int] = []
        elif unit["unit_kind"] == "fallback":
            expected_changed_lines = sorted(
                line
                for line in unit_hunk_lines
                if context_span["start_line"] <= line <= context_span["end_line"]
            )
        else:
            expected_changed_lines = sorted(
                line
                for line in unit_hunk_lines
                if source_span["start_line"] <= line <= source_span["end_line"]
            )
        if unit["changed_new_lines"] != expected_changed_lines:
            raise ValueError(
                f"{unit_context}.changed_new_lines does not equal input hunk lines "
                "assigned to its source occurrence"
            )

        if unit["unit_kind"] == "fallback":
            expected_symbol = (
                f"hunk-L{source_span['start_line']}-L{source_span['end_line']}"
            )
            if unit["unit_symbol"] != expected_symbol:
                raise ValueError(f"{unit_context}.unit_symbol does not match fallback span")
            if mode == "diff" and hunks and not any(
                hunk.new_start == source_span["start_line"]
                and hunk.new_end == source_span["end_line"]
                for hunk in hunks
            ):
                raise ValueError(f"{unit_context}.source_span does not match an input hunk")
            if unit["context_degraded"] is not True:
                raise ValueError(f"{unit_context}.context_degraded must be true for fallback")
        elif not any(
            declaration.kind == unit["unit_kind"]
            and declaration.qualified_name == unit["unit_symbol"]
            and declaration.span.start_line == source_span["start_line"]
            and declaration.span.end_line == source_span["end_line"]
            for declaration in declarations
        ):
            raise ValueError(
                f"{unit_context} owner does not match a frozen declaration occurrence"
            )

        reason = unit["selection_reason"]
        if reason == "large_build_ui_block" and unit["unit_kind"] != "ui_block":
            raise ValueError(f"{unit_context}.large_build_ui_block requires ui_block")
        if reason == "full_top_level_declaration" and (
            mode != "full" or unit["unit_kind"] not in {"struct", "class"}
        ):
            raise ValueError(
                f"{unit_context}.full_top_level_declaration requires full struct/class"
            )
        if mode == "diff" and hunks and reason == "full_top_level_declaration":
            raise ValueError(f"{unit_context} uses a full-mode selection reason in diff mode")

        unit_diagnostic_violations = _diagnostic_semantic_violations(
            unit["diagnostics"],
            prefix=f"{unit_context}.diagnostics",
            source_line_count=source_line_count,
            hunk_lines=hunk_lines,
            context_span=ReviewUnitSpan(**context_span),
            result_level=False,
        )
        if unit_diagnostic_violations:
            raise ValueError("; ".join(unit_diagnostic_violations))
        unit_codes = {item["code"] for item in unit["diagnostics"]}
        actual_parser_codes = unit_codes.intersection(
            {"parser_degraded", "parser_error_nodes", "parser_missing_nodes"}
        )
        if actual_parser_codes != parser_codes:
            raise ValueError(
                f"{unit_context}.diagnostics parser quality codes do not match input: "
                f"expected={sorted(parser_codes)}, actual={sorted(actual_parser_codes)}"
            )
        expected_context_degraded = unit["unit_kind"] == "fallback" or bool(parser_codes)
        if unit["context_degraded"] is not expected_context_degraded:
            raise ValueError(
                f"{unit_context}.context_degraded must reflect fallback/parser degradation"
            )

    if mode == "diff" and not hunks and expected["units"]:
        raise ValueError(f"{context}.units must be empty for diff mode without hunks")


def _ranges_overlap(first_start: int, first_end: int, second_start: int, second_end: int) -> bool:
    return first_start <= second_end and second_start <= first_end


def _assigned_hunk_lines(
    hunks: tuple[FileHunk, ...],
    *,
    source_span_start: int,
    source_span_end: int,
    context_span_start: int,
    context_span_end: int,
    fallback: bool,
) -> list[int]:
    assigned_start = context_span_start if fallback else source_span_start
    assigned_end = context_span_end if fallback else source_span_end
    return sorted(
        {
            line
            for hunk in hunks
            if _ranges_overlap(
                source_span_start,
                source_span_end,
                hunk.new_start,
                hunk.new_end,
            )
            for line in range(hunk.new_start, hunk.new_end + 1)
            if assigned_start <= line <= assigned_end
        }
    )


def _load_expected_unit(
    value: object,
    expected_context: str,
    index: int,
    logical_path: str,
    source_line_count: int,
) -> dict[str, Any]:
    context = f"{expected_context}.units[{index}]"
    unit = _mapping(value, context)
    _require_exact_fields(unit, UNIT_FIELDS, context)
    unit_kind = _string(unit.get("unit_kind"), f"{context}.unit_kind")
    if unit_kind not in REVIEW_UNIT_KINDS:
        raise ValueError(f"{context}.unit_kind is unsupported: {unit_kind}")
    unit_symbol = _string(unit.get("unit_symbol"), f"{context}.unit_symbol")
    source_span = _validate_span(
        unit.get("source_span"), f"{context}.source_span", source_line_count
    )
    context_span = _validate_span(
        unit.get("context_span"), f"{context}.context_span", source_line_count
    )
    if not (
        context_span["start_line"] <= source_span["start_line"]
        and source_span["end_line"] <= context_span["end_line"]
    ):
        raise ValueError(f"{context}.source_span must be contained by context_span")
    expected_id = format_unit_id(
        logical_path,
        unit_kind,
        unit_symbol,
        source_span,
        context_span,
    )
    unit_id = _string(unit.get("unit_id"), f"{context}.unit_id")
    if unit_id != expected_id:
        raise ValueError(f"{context}.unit_id must be {expected_id!r}, got {unit_id!r}")
    changed_lines = _validated_lines(
        unit.get("changed_new_lines"),
        f"{context}.changed_new_lines",
        allow_empty=True,
    )
    if any(
        line < context_span["start_line"] or line > context_span["end_line"]
        for line in changed_lines
    ):
        raise ValueError(f"{context}.changed_new_lines must be inside context_span")
    selection_reason = _string(
        unit.get("selection_reason"), f"{context}.selection_reason"
    )
    if selection_reason not in SELECTION_REASONS:
        raise ValueError(f"{context}.selection_reason is unsupported")
    if (unit_kind == "fallback") != (selection_reason == "fallback_window"):
        raise ValueError(f"{context}.fallback kind and reason must be used together")
    diagnostics = _validate_diagnostics(unit.get("diagnostics"), f"{context}.diagnostics")
    return {
        "unit_id": unit_id,
        "unit_kind": unit_kind,
        "unit_symbol": unit_symbol,
        "source_span": source_span,
        "context_span": context_span,
        "changed_new_lines": changed_lines,
        "selection_reason": selection_reason,
        "context_degraded": _boolean(
            unit.get("context_degraded"), f"{context}.context_degraded"
        ),
        "diagnostics": diagnostics,
    }


def format_unit_id(
    logical_path: str,
    unit_kind: str,
    unit_symbol: str,
    source_span: dict[str, int],
    context_span: dict[str, int],
) -> str:
    if unit_kind == "fallback":
        return fallback_unit_id(
            logical_path,
            source_span["start_line"],
            source_span["end_line"],
            context_span["start_line"],
            context_span["end_line"],
        )
    return declaration_unit_id(
        logical_path,
        unit_kind,  # type: ignore[arg-type]
        unit_symbol,
        source_span["start_line"],
        source_span["end_line"],
    )


def normalize_path(path: str) -> str:
    return normalize_review_path(path)


def declaration_projection(declaration: Declaration) -> dict[str, Any]:
    return {
        "kind": declaration.kind,
        "name": declaration.name,
        "qualified_name": declaration.qualified_name,
        "parent_name": declaration.parent_name,
        "span": {
            "start_line": declaration.span.start_line,
            "end_line": declaration.span.end_line,
        },
    }


def _validate_span(
    value: object,
    context: str,
    source_line_count: int | None,
) -> dict[str, int]:
    span = _mapping(value, context)
    _require_exact_fields(span, SPAN_FIELDS, context)
    start_line = _positive_int(span.get("start_line"), f"{context}.start_line")
    end_line = _positive_int(span.get("end_line"), f"{context}.end_line")
    if end_line < start_line:
        raise ValueError(f"{context}.end_line must be >= start_line")
    if source_line_count is not None and end_line > source_line_count:
        raise ValueError(f"{context}.end_line exceeds the source line count")
    return {"start_line": start_line, "end_line": end_line}


def _validate_diagnostics(value: object, context: str) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for index, raw_diagnostic in enumerate(_list(value, context)):
        item_context = f"{context}[{index}]"
        diagnostic = _mapping(raw_diagnostic, item_context)
        _require_exact_fields(diagnostic, DIAGNOSTIC_FIELDS, item_context)
        code = _string(diagnostic.get("code"), f"{item_context}.code")
        if code not in REVIEW_UNIT_DIAGNOSTIC_CODES:
            raise ValueError(f"{item_context}.code is unsupported: {code}")
        lines = _validated_lines(
            diagnostic.get("lines"), f"{item_context}.lines", allow_empty=True
        )
        diagnostics.append({"code": code, "lines": lines})
    if diagnostics != sorted(diagnostics, key=_canonical):
        raise ValueError(f"{context} must be sorted and unique")
    if len({_canonical(item) for item in diagnostics}) != len(diagnostics):
        raise ValueError(f"{context} must be sorted and unique")
    return diagnostics


def _differences(expected: object, actual: object, path: str = "result") -> list[str]:
    differences: list[str] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            child_path = f"{path}.{key}"
            if key not in expected:
                differences.append(f"{child_path}: unexpected actual field")
            elif key not in actual:
                differences.append(f"{child_path}: missing actual field")
            else:
                differences.extend(_differences(expected[key], actual[key], child_path))
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            differences.append(
                f"{path}.length: expected {len(expected)!r}, actual {len(actual)!r}"
            )
        for index, (expected_item, actual_item) in enumerate(
            zip(expected, actual, strict=False)
        ):
            differences.extend(
                _differences(expected_item, actual_item, f"{path}[{index}]")
            )
    elif type(expected) is not type(actual) or expected != actual:
        differences.append(f"{path}: expected {expected!r}, actual {actual!r}")
    return sorted(set(differences))


def reports_equal(first: object, second: object) -> bool:
    """Compare JSON reports without Python's bool/int equality coercion."""

    return not _differences(first, second, path="report")


def _load_json_mapping(path: Path, context: str) -> dict[str, Any]:
    payload = _read_file_bytes(path, f"{context} JSON")
    return _parse_json_mapping(payload, path, context)


def _read_file_bytes(path: Path, context: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {context} {path}: {exc}") from exc


def _parse_json_mapping(payload: bytes, path: Path, context: str) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
        return _mapping(
            json.loads(text, object_pairs_hook=_reject_duplicate_json_keys),
            context,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {context} JSON {path}: {exc}") from exc


def _read_source_snapshot(path: Path, expected_hash: str, context: str) -> str:
    payload = _read_file_bytes(path, context)
    actual_hash = hashlib.sha256(payload).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(
            f"{context} content_sha256 drift: expected {expected_hash}, got {actual_hash}"
        )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"cannot decode {context} as UTF-8: {exc}") from exc


def _path_contains_symlink(root: Path, candidate: Path) -> bool:
    resolved_root = root.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    current = resolved_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _portable_path(value: object, context: str) -> str:
    path = _string(value, context)
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in path:
        raise ValueError(f"{context} must be a portable relative path")
    normalized = normalize_path(path)
    if normalized in {"", "."} or normalized != path:
        raise ValueError(f"{context} must already be normalized")
    return path


def _validated_lines(value: object, context: str, *, allow_empty: bool) -> list[int]:
    values = [
        _positive_int(item, f"{context}[]") for item in _list(value, context)
    ]
    if not allow_empty and not values:
        raise ValueError(f"{context} must not be empty")
    if values != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _mapping(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _require_exact_fields(
    value: dict[str, Any],
    expected_fields: tuple[str, ...],
    context: str,
) -> None:
    expected = set(expected_fields)
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{context} fields mismatch: missing={missing}, extra={extra}")


def _list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _string_list(value: object, context: str, *, allow_empty: bool) -> list[str]:
    values = [_string(item, f"{context}[]") for item in _list(value, context)]
    if not allow_empty and not values:
        raise ValueError(f"{context} must not be empty")
    return values


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")
    return value


def _positive_int(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{context} must be an integer >= 1")
    return value


def _nonnegative_int(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{context} must be an integer >= 0")
    return value


def _sha256(value: object, context: str) -> str:
    return _lower_hex(_string(value, context), context, length=64)


def _lower_hex(value: str, context: str, *, length: int) -> str:
    if len(value) != length or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{context} must be {length} lowercase hexadecimal characters")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
