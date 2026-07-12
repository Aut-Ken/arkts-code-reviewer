from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    FileAnalysis,
    FileParseResult,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    FileAnalysisParser,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    normalize_review_path,
)

SCHEMA_VERSION = "file-analysis-golden-v1"
BASELINE_SCHEMA_VERSION = "file-analysis-golden-baseline-v1"

_ROOT_FIELDS = {"schema_version", "suite_id", "cases"}
_CASE_FIELDS = {"case_id", "description", "source", "expected"}
_SOURCE_FIELDS = {
    "file",
    "logical_path",
    "repository",
    "revision",
    "content_sha256",
}
_EXPECTED_FIELDS = {
    "parser_version",
    "parser_quality",
    "file_hints",
    "declarations",
    "review_regions",
    "fact_occurrences",
    "diagnostics",
}
_QUALITY_FIELDS = {"layer", "error_nodes", "missing_nodes", "warnings"}
_SCOPED_FACT_FIELDS = {
    "components",
    "apis",
    "decorators",
    "attributes",
    "symbols",
    "syntax",
    "import_bindings",
    "import_uses",
    "field_reads",
    "field_writes",
    "calls",
    "string_literals",
    "resource_references",
}
_DECLARATION_FIELDS = {
    "alias",
    "kind",
    "name",
    "qualified_name",
    "span",
    "start_offset_utf16",
    "end_offset_utf16",
    "parent_alias",
    "quality",
}
_REGION_FIELDS = {
    "alias",
    "kind",
    "symbol",
    "span",
    "start_offset_utf16",
    "end_offset_utf16",
    "owner_alias",
    "quality",
    "provenance",
}
_FACT_FIELDS = {
    "kind",
    "name",
    "canonical_name",
    "span",
    "start_offset_utf16",
    "end_offset_utf16",
    "owner",
    "quality",
    "provenance",
}
_SPAN_FIELDS = {"start_line", "end_line"}
_OWNER_FIELDS = {"kind", "alias"}

_DECLARATION_KINDS = {
    "struct",
    "class",
    "function",
    "method",
    "build_method",
    "builder",
    "ui_block",
}
_REGION_KINDS = {"field_region", "import_region"}
_FACT_KINDS = {
    "component",
    "api",
    "decorator",
    "attribute",
    "symbol",
    "syntax",
    "import_binding",
    "import_use",
    "field_read",
    "field_write",
    "call",
    "string_literal",
    "resource_reference",
}
_QUALITIES = {"exact", "recovered", "degraded", "unresolved"}
_PROVENANCE = {"L0", "L1", "recovered"}
_PARSER_LAYERS = {"L0", "L1", "parse_degraded"}
_FILE_ANALYSIS_DIAGNOSTICS = {
    "file_analysis_sidecar_unavailable",
    "file_analysis_sidecar_failed",
    "file_analysis_snapshot_invalid",
    "occurrence_extraction_unavailable",
    "unresolved_fact_owner",
    "parser_error_nodes",
    "parser_missing_nodes",
    "ambiguous_binding_scope",
}
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FileAnalysisGoldenCase:
    case_id: str
    description: str
    source_path: Path
    source: str
    source_metadata: dict[str, str]
    expected: dict[str, Any]


@dataclass(frozen=True)
class FileAnalysisGoldenSuite:
    manifest_path: Path
    schema_version: str
    suite_id: str
    manifest_sha256: str
    cases: tuple[FileAnalysisGoldenCase, ...]


def load_golden_suite(manifest_path: str | Path) -> FileAnalysisGoldenSuite:
    path = Path(manifest_path).resolve()
    raw_bytes = path.read_bytes()
    data = _load_json_bytes(raw_bytes, str(path))
    data = _require_object(data, "manifest")
    _require_fields(data, _ROOT_FIELDS, "manifest")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"manifest.schema_version must be {SCHEMA_VERSION!r}")
    suite_id = _string(data["suite_id"], "manifest.suite_id")
    raw_cases = _list(data["cases"], "manifest.cases")
    if not 12 <= len(raw_cases) <= 16:
        raise ValueError("manifest.cases must contain 12 to 16 cases")

    cases: list[FileAnalysisGoldenCase] = []
    seen_case_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        context = f"manifest.cases[{index}]"
        raw_case = _require_object(raw_case, context)
        _require_fields(raw_case, _CASE_FIELDS, context)
        case_id = _string(raw_case["case_id"], f"{context}.case_id")
        if case_id in seen_case_ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen_case_ids.add(case_id)
        description = _string(raw_case["description"], f"{context}.description")
        source_metadata, source_path, source = _load_source(
            path.parent,
            raw_case["source"],
            f"{context}.source",
        )
        expected = _validate_expected(
            raw_case["expected"],
            source,
            f"{context}.expected",
        )
        cases.append(
            FileAnalysisGoldenCase(
                case_id=case_id,
                description=description,
                source_path=source_path,
                source=source,
                source_metadata=source_metadata,
                expected=expected,
            )
        )

    if [case.case_id for case in cases] != sorted(case.case_id for case in cases):
        raise ValueError("manifest.cases must be sorted by case_id")
    return FileAnalysisGoldenSuite(
        manifest_path=path,
        schema_version=SCHEMA_VERSION,
        suite_id=suite_id,
        manifest_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        cases=tuple(cases),
    )


def evaluate_golden_suite(
    suite: FileAnalysisGoldenSuite,
    parser: FileAnalysisParser | None = None,
) -> dict[str, Any]:
    parser = parser or _default_parser()
    case_reports = [_evaluate_case(case, parser) for case in suite.cases]
    matched_count = sum(bool(item["matched"]) for item in case_reports)
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "implementation": type(parser).__name__,
        "manifest_sha256": suite.manifest_sha256,
        "case_count": len(case_reports),
        "matched_case_count": matched_count,
        "mismatched_case_count": len(case_reports) - matched_count,
        "cases": case_reports,
    }


def is_perfect(report: dict[str, Any]) -> bool:
    try:
        cases = report["cases"]
        return (
            report["schema_version"] == SCHEMA_VERSION
            and isinstance(cases, list)
            and report["case_count"] == len(cases)
            and report["matched_case_count"] == len(cases)
            and report["mismatched_case_count"] == 0
            and len(cases) >= 12
            and all(case.get("matched") is True for case in cases)
        )
    except (KeyError, TypeError):
        return False


def write_current_baseline(
    report: dict[str, Any],
    suite: FileAnalysisGoldenSuite,
    baseline_path: str | Path,
) -> None:
    path = Path(baseline_path).resolve()
    allowed = (
        suite.manifest_path.parent / "baselines" / "current.json"
    ).resolve()
    if path != allowed:
        raise ValueError("FileAnalysis baseline writer may only update baselines/current.json")
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "manifest_sha256": suite.manifest_sha256,
        "report": report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def assert_strict_baseline(
    report: dict[str, Any],
    suite: FileAnalysisGoldenSuite,
    baseline_path: str | Path,
) -> None:
    path = Path(baseline_path).resolve()
    data = _load_json_bytes(path.read_bytes(), str(path))
    data = _require_object(data, "baseline")
    _require_fields(
        data,
        {"schema_version", "suite_id", "manifest_sha256", "report"},
        "baseline",
    )
    if data["schema_version"] != BASELINE_SCHEMA_VERSION:
        raise ValueError("unsupported FileAnalysis baseline schema")
    if data["suite_id"] != suite.suite_id:
        raise ValueError("baseline suite_id drift")
    if data["manifest_sha256"] != suite.manifest_sha256:
        raise ValueError("baseline manifest hash drift")
    if data["report"] != report:
        differences = _differences(data["report"], report)
        preview = "; ".join(differences[:5])
        raise ValueError(f"FileAnalysis strict baseline mismatch: {preview}")


def _evaluate_case(
    case: FileAnalysisGoldenCase,
    parser: FileAnalysisParser,
) -> dict[str, Any]:
    source_ref = CodeSourceRef.create(
        repository=case.source_metadata["repository"],
        revision=case.source_metadata["revision"],
        path=case.source_metadata["logical_path"],
        content_hash=f"sha256:{case.source_metadata['content_sha256']}",
    )
    invariants: list[str] = []
    try:
        result = parser.parse_file(source_ref, case.source)
        if not isinstance(result, FileParseResult):
            raise TypeError("parse_file must return FileParseResult")
        result.analysis.validate()
        source_ref.verify_content(case.source)
        if result.analysis.source_ref != source_ref:
            raise ValueError("FileAnalysis source_ref does not match Golden input")
        repeated = parser.parse_file(source_ref, case.source)
        if result.to_dict() != repeated.to_dict():
            invariants.append("parse_file output is not deterministic")
        actual = _semantic_projection(result.analysis, case.expected)
    except Exception as exc:  # Golden reports execution failures as case failures.
        actual = {"error": f"{type(exc).__name__}: {exc}"}
        invariants.append(f"parser execution failed: {type(exc).__name__}: {exc}")

    expected_projection = _expected_projection(case.expected)
    differences = _differences(expected_projection, actual)
    return {
        "case_id": case.case_id,
        "logical_path": case.source_metadata["logical_path"],
        "source_sha256": case.source_metadata["content_sha256"],
        "matched": not differences and not invariants,
        "expected": expected_projection,
        "actual": actual,
        "differences": differences,
        "invariant_violations": invariants,
    }


def _semantic_projection(
    analysis: FileAnalysis,
    expected: dict[str, Any],
) -> dict[str, Any]:
    declaration_aliases = _match_declaration_aliases(
        analysis,
        expected["declarations"],
    )
    region_aliases = _match_region_aliases(
        analysis,
        expected["review_regions"],
    )
    id_to_alias = {**declaration_aliases, **region_aliases}
    declarations = [
        _project_declaration(item, declaration_aliases)
        for item in analysis.declarations
    ]
    regions = [
        _project_region(item, declaration_aliases, region_aliases)
        for item in analysis.review_regions
    ]
    facts = [
        _project_fact(item, id_to_alias)
        for item in analysis.fact_occurrences
    ]
    return {
        "parser_version": analysis.parser_version,
        "parser_quality": {
            "layer": analysis.parser_quality.layer,
            "error_nodes": analysis.parser_quality.error_nodes,
            "missing_nodes": analysis.parser_quality.missing_nodes,
            "warnings": list(analysis.parser_quality.warnings),
        },
        "file_hints": {
            name: list(getattr(analysis.file_hints, name))
            for name in _SCOPED_FACT_FIELDS
        },
        "declarations": declarations,
        "review_regions": regions,
        "fact_occurrences": facts,
        "diagnostics": list(analysis.diagnostics),
    }


def _expected_projection(expected: dict[str, Any]) -> dict[str, Any]:
    return expected


def _match_declaration_aliases(
    analysis: FileAnalysis,
    expected: list[dict[str, Any]],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for target in expected:
        candidates = [
            item
            for item in analysis.declarations
            if item.kind == target["kind"]
            and item.name == target["name"]
            and item.qualified_name == target["qualified_name"]
            and item.span.start_line == target["span"]["start_line"]
            and item.span.end_line == target["span"]["end_line"]
            and item.exact_range.start_offset_utf16
            == target["start_offset_utf16"]
            and item.exact_range.end_offset_utf16 == target["end_offset_utf16"]
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"declaration alias {target['alias']!r} resolved to {len(candidates)} items"
            )
        aliases[candidates[0].declaration_id] = target["alias"]
    return aliases


def _match_region_aliases(
    analysis: FileAnalysis,
    expected: list[dict[str, Any]],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for target in expected:
        candidates = [
            item
            for item in analysis.review_regions
            if item.kind == target["kind"]
            and item.symbol == target["symbol"]
            and item.span.start_line == target["span"]["start_line"]
            and item.span.end_line == target["span"]["end_line"]
            and item.exact_range.start_offset_utf16
            == target["start_offset_utf16"]
            and item.exact_range.end_offset_utf16 == target["end_offset_utf16"]
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"region alias {target['alias']!r} resolved to {len(candidates)} items"
            )
        aliases[candidates[0].region_id] = target["alias"]
    return aliases


def _project_declaration(item: Any, aliases: dict[str, str]) -> dict[str, Any]:
    return {
        "alias": aliases.get(item.declaration_id, f"unmapped:{item.declaration_id}"),
        "kind": item.kind,
        "name": item.name,
        "qualified_name": item.qualified_name,
        "span": {
            "start_line": item.span.start_line,
            "end_line": item.span.end_line,
        },
        "start_offset_utf16": item.exact_range.start_offset_utf16,
        "end_offset_utf16": item.exact_range.end_offset_utf16,
        "parent_alias": (
            None
            if item.parent_id is None
            else aliases.get(item.parent_id, f"unmapped:{item.parent_id}")
        ),
        "quality": item.quality,
    }


def _project_region(
    item: Any,
    declaration_aliases: dict[str, str],
    region_aliases: dict[str, str],
) -> dict[str, Any]:
    return {
        "alias": region_aliases.get(item.region_id, f"unmapped:{item.region_id}"),
        "kind": item.kind,
        "symbol": item.symbol,
        "span": {
            "start_line": item.span.start_line,
            "end_line": item.span.end_line,
        },
        "start_offset_utf16": item.exact_range.start_offset_utf16,
        "end_offset_utf16": item.exact_range.end_offset_utf16,
        "owner_alias": (
            None
            if item.owner_declaration_id is None
            else declaration_aliases.get(
                item.owner_declaration_id,
                f"unmapped:{item.owner_declaration_id}",
            )
        ),
        "quality": item.quality,
        "provenance": item.provenance,
    }


def _project_fact(item: Any, aliases: dict[str, str]) -> dict[str, Any]:
    owner = None
    if item.owner_ref is not None:
        owner = {
            "kind": item.owner_ref.kind,
            "alias": aliases.get(
                item.owner_ref.ref_id,
                f"unmapped:{item.owner_ref.ref_id}",
            ),
        }
    return {
        "kind": item.kind,
        "name": item.name,
        "canonical_name": item.canonical_name,
        "span": {
            "start_line": item.span.start_line,
            "end_line": item.span.end_line,
        },
        "start_offset_utf16": item.exact_range.start_offset_utf16,
        "end_offset_utf16": item.exact_range.end_offset_utf16,
        "owner": owner,
        "quality": item.quality,
        "provenance": item.provenance,
    }


def _load_source(
    manifest_dir: Path,
    value: object,
    context: str,
) -> tuple[dict[str, str], Path, str]:
    value = _require_object(value, context)
    _require_fields(value, _SOURCE_FIELDS, context)
    file_name = _string(value["file"], f"{context}.file")
    if Path(file_name).is_absolute() or ".." in Path(file_name).parts:
        raise ValueError(f"{context}.file must stay inside the Golden directory")
    source_path = (manifest_dir / file_name).resolve()
    sources_root = (manifest_dir / "sources").resolve()
    if source_path.parent != sources_root or source_path.suffix != ".ets":
        raise ValueError(f"{context}.file must name one direct sources/*.ets fixture")
    source = source_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    expected_digest = _string(value["content_sha256"], f"{context}.content_sha256")
    if not _SHA_RE.fullmatch(expected_digest) or digest != expected_digest:
        raise ValueError(f"{context} source hash drift")
    logical_path = _string(value["logical_path"], f"{context}.logical_path")
    if normalize_review_path(logical_path) != logical_path:
        raise ValueError(f"{context}.logical_path must be normalized")
    metadata = {
        "file": file_name,
        "logical_path": logical_path,
        "repository": _string(value["repository"], f"{context}.repository"),
        "revision": _string(value["revision"], f"{context}.revision"),
        "content_sha256": expected_digest,
    }
    return metadata, source_path, source


def _validate_expected(value: object, source: str, context: str) -> dict[str, Any]:
    value = _require_object(value, context)
    _require_fields(value, _EXPECTED_FIELDS, context)
    quality = _validate_parser_quality(value["parser_quality"], f"{context}.parser_quality")
    parser_version = _string(value["parser_version"], f"{context}.parser_version")
    file_hints = _validate_scoped_facts(value["file_hints"], f"{context}.file_hints")
    declarations = [
        _validate_declaration(item, source, f"{context}.declarations[{index}]")
        for index, item in enumerate(_list(value["declarations"], f"{context}.declarations"))
    ]
    regions = [
        _validate_region(item, source, f"{context}.review_regions[{index}]")
        for index, item in enumerate(
            _list(value["review_regions"], f"{context}.review_regions")
        )
    ]
    facts = [
        _validate_fact(item, source, f"{context}.fact_occurrences[{index}]")
        for index, item in enumerate(
            _list(value["fact_occurrences"], f"{context}.fact_occurrences")
        )
    ]
    diagnostics = _string_list(value["diagnostics"], f"{context}.diagnostics")
    if set(diagnostics) - _FILE_ANALYSIS_DIAGNOSTICS:
        raise ValueError(f"{context}.diagnostics contains unsupported codes")

    aliases: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, items in (("declaration", declarations), ("region", regions)):
        for item in items:
            key = (kind, item["alias"])
            if key in aliases:
                raise ValueError(f"{context} has duplicate {kind} alias {item['alias']!r}")
            aliases[key] = item
    for item in declarations:
        parent_alias = item["parent_alias"]
        if parent_alias is not None:
            parent = aliases.get(("declaration", parent_alias))
            if parent is None:
                raise ValueError(f"{context} declaration parent alias is unresolved")
            _require_containment(parent, item, f"{context} declaration parent")
    for item in regions:
        owner_alias = item["owner_alias"]
        if owner_alias is not None:
            owner = aliases.get(("declaration", owner_alias))
            if owner is None:
                raise ValueError(f"{context} region owner alias is unresolved")
            _require_containment(owner, item, f"{context} region owner")
    for item in facts:
        owner_ref = item["owner"]
        if owner_ref is None:
            if item["quality"] == "exact":
                raise ValueError(f"{context} exact fact requires an owner alias")
            continue
        owner = aliases.get((owner_ref["kind"], owner_ref["alias"]))
        if owner is None:
            raise ValueError(f"{context} fact owner alias is unresolved")
        _require_containment(owner, item, f"{context} fact owner")

    _require_source_order(declarations, f"{context}.declarations", "kind")
    _require_source_order(regions, f"{context}.review_regions", "kind")
    _require_source_order(facts, f"{context}.fact_occurrences", "kind")
    return {
        "parser_version": parser_version,
        "parser_quality": quality,
        "file_hints": file_hints,
        "declarations": declarations,
        "review_regions": regions,
        "fact_occurrences": facts,
        "diagnostics": diagnostics,
    }


def _validate_parser_quality(value: object, context: str) -> dict[str, Any]:
    value = _require_object(value, context)
    _require_fields(value, _QUALITY_FIELDS, context)
    layer = _string(value["layer"], f"{context}.layer")
    if layer not in _PARSER_LAYERS:
        raise ValueError(f"{context}.layer is unsupported")
    return {
        "layer": layer,
        "error_nodes": _optional_non_negative_int(
            value["error_nodes"], f"{context}.error_nodes"
        ),
        "missing_nodes": _optional_non_negative_int(
            value["missing_nodes"], f"{context}.missing_nodes"
        ),
        "warnings": _string_list(value["warnings"], f"{context}.warnings"),
    }


def _validate_scoped_facts(value: object, context: str) -> dict[str, list[str]]:
    value = _require_object(value, context)
    _require_fields(value, _SCOPED_FACT_FIELDS, context)
    return {
        name: _string_list(value[name], f"{context}.{name}")
        for name in sorted(_SCOPED_FACT_FIELDS)
    }


def _validate_declaration(value: object, source: str, context: str) -> dict[str, Any]:
    values = _fixed_row(value, 10, context)
    item = {
        "alias": values[0],
        "kind": values[1],
        "name": values[2],
        "qualified_name": values[3],
        "span": {"start_line": values[4], "end_line": values[5]},
        "start_offset_utf16": values[6],
        "end_offset_utf16": values[7],
        "parent_alias": values[8],
        "quality": values[9],
    }
    item["alias"] = _alias(item["alias"], f"{context}.alias")
    item["kind"] = _enum(item["kind"], _DECLARATION_KINDS, f"{context}.kind")
    item["name"] = _string(item["name"], f"{context}.name")
    item["qualified_name"] = _string(item["qualified_name"], f"{context}.qualified_name")
    item["parent_alias"] = _optional_alias(item["parent_alias"], f"{context}.parent_alias")
    item["quality"] = _enum(item["quality"], _QUALITIES, f"{context}.quality")
    _validate_location(item, source, context)
    return item


def _validate_region(value: object, source: str, context: str) -> dict[str, Any]:
    values = _fixed_row(value, 10, context)
    item = {
        "alias": values[0],
        "kind": values[1],
        "symbol": values[2],
        "span": {"start_line": values[3], "end_line": values[4]},
        "start_offset_utf16": values[5],
        "end_offset_utf16": values[6],
        "owner_alias": values[7],
        "quality": values[8],
        "provenance": values[9],
    }
    item["alias"] = _alias(item["alias"], f"{context}.alias")
    item["kind"] = _enum(item["kind"], _REGION_KINDS, f"{context}.kind")
    item["symbol"] = _string(item["symbol"], f"{context}.symbol")
    item["owner_alias"] = _optional_alias(item["owner_alias"], f"{context}.owner_alias")
    if (item["kind"] == "field_region") != (item["owner_alias"] is not None):
        raise ValueError(f"{context} field/import region owner contract is invalid")
    item["quality"] = _enum(item["quality"], _QUALITIES, f"{context}.quality")
    item["provenance"] = _enum(item["provenance"], _PROVENANCE, f"{context}.provenance")
    _validate_location(item, source, context)
    return item


def _validate_fact(value: object, source: str, context: str) -> dict[str, Any]:
    values = _fixed_row(value, 10, context)
    item = {
        "kind": values[0],
        "name": values[1],
        "canonical_name": values[2],
        "span": {"start_line": values[3], "end_line": values[4]},
        "start_offset_utf16": values[5],
        "end_offset_utf16": values[6],
        "owner": values[7],
        "quality": values[8],
        "provenance": values[9],
    }
    item["kind"] = _enum(item["kind"], _FACT_KINDS, f"{context}.kind")
    item["name"] = _string(item["name"], f"{context}.name")
    if item["canonical_name"] is not None:
        item["canonical_name"] = _string(
            item["canonical_name"], f"{context}.canonical_name"
        )
    if item["owner"] is not None:
        owner_values = _fixed_row(item["owner"], 2, f"{context}.owner")
        item["owner"] = {
            "kind": _enum(
                owner_values[0],
                {"declaration", "region"},
                f"{context}.owner.kind",
            ),
            "alias": _alias(owner_values[1], f"{context}.owner.alias"),
        }
    item["quality"] = _enum(item["quality"], _QUALITIES, f"{context}.quality")
    item["provenance"] = _enum(item["provenance"], _PROVENANCE, f"{context}.provenance")
    _validate_location(item, source, context)
    return item


def _validate_location(item: dict[str, Any], source: str, context: str) -> None:
    span = _require_object(item["span"], f"{context}.span")
    _require_fields(span, _SPAN_FIELDS, f"{context}.span")
    start_line = _positive_int(span["start_line"], f"{context}.span.start_line")
    end_line = _positive_int(span["end_line"], f"{context}.span.end_line")
    if end_line < start_line:
        raise ValueError(f"{context}.span is reversed")
    start_offset = _non_negative_int(
        item["start_offset_utf16"], f"{context}.start_offset_utf16"
    )
    end_offset = _non_negative_int(
        item["end_offset_utf16"], f"{context}.end_offset_utf16"
    )
    if end_offset < start_offset:
        raise ValueError(f"{context} UTF-16 offsets are reversed")
    boundaries = _utf16_boundaries(source)
    if start_offset not in boundaries or end_offset not in boundaries:
        raise ValueError(f"{context} UTF-16 offsets are outside source boundaries")
    if _line_at_utf16_offset(source, start_offset) != start_line:
        raise ValueError(f"{context} start_line does not match its UTF-16 offset")
    if _line_at_utf16_offset(source, end_offset) != end_line:
        raise ValueError(f"{context} end_line does not match its UTF-16 offset")
    item["span"] = {"start_line": start_line, "end_line": end_line}
    item["start_offset_utf16"] = start_offset
    item["end_offset_utf16"] = end_offset


def _utf16_boundaries(source: str) -> dict[int, int]:
    boundaries = {0: 0}
    offset = 0
    for index, character in enumerate(source, start=1):
        offset += 2 if ord(character) > 0xFFFF else 1
        boundaries[offset] = index
    return boundaries


def _line_at_utf16_offset(source: str, offset: int) -> int:
    index = _utf16_boundaries(source)[offset]
    return source[:index].count("\n") + 1


def _require_containment(
    owner: dict[str, Any],
    child: dict[str, Any],
    context: str,
) -> None:
    if not (
        owner["start_offset_utf16"] <= child["start_offset_utf16"]
        and child["end_offset_utf16"] <= owner["end_offset_utf16"]
    ):
        raise ValueError(f"{context} does not contain its child")


def _require_source_order(items: list[dict[str, Any]], context: str, tie: str) -> None:
    keys = [
        (
            item["start_offset_utf16"],
            item["end_offset_utf16"],
            item[tie],
            item.get("canonical_name")
            or item.get("qualified_name")
            or item.get("symbol")
            or item.get("name"),
        )
        for item in items
    ]
    if keys != sorted(keys):
        raise ValueError(f"{context} must use stable source order")


def _default_parser() -> FileAnalysisParser:
    from arkts_code_reviewer.code_analysis.file_analysis_parser import (
        ArktsFileAnalysisParser,
    )

    return ArktsFileAnalysisParser()


def _differences(expected: object, actual: object, path: str = "$") -> list[str]:
    if type(expected) is not type(actual):
        return [f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"]
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected dict, got {type(actual).__name__}"]
        differences: list[str] = []
        expected_keys = set(expected)
        actual_keys = set(actual)
        for key in sorted(expected_keys - actual_keys):
            differences.append(f"{path}.{key}: missing")
        for key in sorted(actual_keys - expected_keys):
            differences.append(f"{path}.{key}: unexpected")
        for key in sorted(expected_keys & actual_keys):
            differences.extend(_differences(expected[key], actual[key], f"{path}.{key}"))
        return differences
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: expected list, got {type(actual).__name__}"]
        differences = []
        if len(expected) != len(actual):
            differences.append(f"{path}: expected length {len(expected)}, got {len(actual)}")
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            differences.extend(_differences(left, right, f"{path}[{index}]"))
        return differences
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


def _load_json_bytes(raw: bytes, context: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{context} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON in {context}: {exc}") from exc


def _require_object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _require_fields(value: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{context} fields mismatch; missing={missing}, unknown={unknown}")


def _list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list")
    return value


def _fixed_row(value: object, length: int, context: str) -> list[Any]:
    values = _list(value, context)
    if len(values) != length:
        raise ValueError(f"{context} must contain exactly {length} columns")
    return values


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _string_list(value: object, context: str) -> list[str]:
    values = _list(value, context)
    if any(not isinstance(item, str) or not item for item in values):
        raise ValueError(f"{context} must contain non-empty strings")
    if values != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _enum(value: object, allowed: set[str], context: str) -> str:
    result = _string(value, context)
    if result not in allowed:
        raise ValueError(f"{context} has unsupported value {result!r}")
    return result


def _enum_list(value: object, allowed: set[str], context: str) -> list[str]:
    values = _string_list(value, context)
    if any(item not in allowed for item in values):
        raise ValueError(f"{context} contains unsupported values")
    return values


def _alias(value: object, context: str) -> str:
    result = _string(value, context)
    if not _ALIAS_RE.fullmatch(result):
        raise ValueError(f"{context} must use a stable lowercase alias")
    return result


def _optional_alias(value: object, context: str) -> str | None:
    return None if value is None else _alias(value, context)


def _positive_int(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{context} must be an integer >= 1")
    return value


def _non_negative_int(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def _optional_non_negative_int(value: object, context: str) -> int | None:
    return None if value is None else _non_negative_int(value, context)
