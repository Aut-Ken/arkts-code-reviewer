from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arkts_code_reviewer.code_analysis.models import CodeFacts, CodeParser

SCHEMA_VERSION = "parser-golden-v1"
BASELINE_SCHEMA_VERSION = "parser-golden-baseline-v2"
SET_FACT_FIELDS = (
    "components",
    "apis",
    "decorators",
    "attributes",
    "symbols",
    "syntax",
)
EXPECTED_FIELDS = ("imports", *SET_FACT_FIELDS, "declarations")
DECLARATION_KINDS = {
    "struct",
    "class",
    "function",
    "method",
    "build_method",
    "builder",
    "ui_block",
}
MANIFEST_FIELDS = (
    "schema_version",
    "suite_id",
    "description",
    "coordinate_system",
    "unsupported_fields",
    "cases",
)
CASE_FIELDS = (
    "case_id",
    "description",
    "logical_path",
    "source",
    "scored_fields",
    "expected",
    "must_not_emit",
)
IMPORT_FIELDS = ("module", "default_name", "namespace_name", "named")
DECLARATION_FIELDS = ("kind", "name", "qualified_name", "parent_name", "span")
SPAN_FIELDS = ("start_line", "end_line")
REPORT_FIELDS = (
    "schema_version",
    "suite_id",
    "parser_implementation",
    "manifest_sha256",
    "case_count",
    "crashed",
    "parser_layers",
    "must_not_violation_count",
    "unsupported_fields",
    "fields",
    "cases",
)
CASE_RESULT_FIELDS = (
    "case_id",
    "source",
    "source_sha256",
    "provenance",
    "parser_layer",
    "warnings",
    "error",
    "field_scores",
    "must_not_violations",
)
FIELD_SCORE_FIELDS = (
    "tp",
    "fp",
    "fn",
    "precision",
    "recall",
    "f1",
    "false_positives",
    "false_negatives",
)
AGGREGATE_SCORE_FIELDS = ("case_count", *FIELD_SCORE_FIELDS[:6])
BASELINE_FIELDS = (
    "schema_version",
    "suite_id",
    "parser",
    "parser_metadata",
    "report",
)
L1_METADATA_FIELDS = (
    "parser_package",
    "parser_version",
    "node_version",
    "npm_version",
    "package_lock_sha256",
)


@dataclass(frozen=True)
class ParserGoldenCase:
    case_id: str
    description: str
    source_path: Path
    logical_path: str
    source_metadata: dict[str, Any]
    scored_fields: tuple[str, ...]
    expected: dict[str, list[Any] | None]
    must_not_emit: dict[str, list[str]]

    def read_source(self) -> str:
        return self.source_path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class ParserGoldenSuite:
    suite_id: str
    manifest_path: Path
    cases: tuple[ParserGoldenCase, ...]
    unsupported_fields: tuple[str, ...]


def load_golden_suite(manifest_path: Path) -> ParserGoldenSuite:
    manifest_path = manifest_path.resolve()
    data = _mapping(json.loads(manifest_path.read_text(encoding="utf-8")), "manifest")
    _require_exact_fields(data, MANIFEST_FIELDS, "manifest")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"manifest.schema_version must be {SCHEMA_VERSION!r}, "
            f"got {data.get('schema_version')!r}"
        )

    _string(data.get("description"), "manifest.description")
    coordinate_system = _mapping(data.get("coordinate_system"), "manifest.coordinate_system")
    _require_exact_fields(
        coordinate_system,
        ("line_base", "line_end", "column_status"),
        "manifest.coordinate_system",
    )
    if coordinate_system != {
        "line_base": 1,
        "line_end": "inclusive",
        "column_status": "not_scored_until_coordinate_contract_is_frozen",
    }:
        raise ValueError("manifest.coordinate_system must describe 1-based inclusive lines")

    suite_id = _string(data.get("suite_id"), "manifest.suite_id")
    unsupported_fields = tuple(
        _string(item, "manifest.unsupported_fields[]")
        for item in _list(data.get("unsupported_fields"), "manifest.unsupported_fields")
    )
    if len(unsupported_fields) != len(set(unsupported_fields)):
        raise ValueError("manifest.unsupported_fields must be unique")
    raw_cases = _list(data.get("cases"), "manifest.cases")
    if not raw_cases:
        raise ValueError("manifest.cases must not be empty")

    root = manifest_path.parent
    cases: list[ParserGoldenCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        case = _load_case(_mapping(raw_case, f"manifest.cases[{index}]"), root, index)
        if case.case_id in seen_ids:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        seen_ids.add(case.case_id)
        cases.append(case)

    return ParserGoldenSuite(
        suite_id=suite_id,
        manifest_path=manifest_path,
        cases=tuple(cases),
        unsupported_fields=unsupported_fields,
    )


def evaluate_golden_suite(
    suite: ParserGoldenSuite,
    parser: CodeParser,
) -> dict[str, Any]:
    case_results: list[dict[str, Any]] = []
    aggregate_counts = {field: {"tp": 0, "fp": 0, "fn": 0} for field in EXPECTED_FIELDS}
    field_case_counts: Counter[str] = Counter()
    parser_layers: Counter[str] = Counter()
    crashed = 0
    must_not_violations = 0

    for case in suite.cases:
        try:
            facts = parser.parse(case.read_source(), case.logical_path)
            actual = facts_projection(facts)
        except Exception as exc:  # pragma: no cover - retained as evaluator diagnostics.
            crashed += 1
            crashed_field_scores: dict[str, dict[str, Any]] = {}
            for field in case.scored_fields:
                expected_items = _scored_expected(case, field)
                score = score_items(expected_items, [])
                crashed_field_scores[field] = score
                field_case_counts[field] += 1
                for count_name in ("tp", "fp", "fn"):
                    aggregate_counts[field][count_name] += int(score[count_name])
            case_results.append(
                {
                    "case_id": case.case_id,
                    "source": case.logical_path,
                    "source_sha256": case.source_metadata["content_sha256"],
                    "provenance": dict(case.source_metadata),
                    "parser_layer": None,
                    "warnings": [],
                    "error": repr(exc),
                    "field_scores": crashed_field_scores,
                    "must_not_violations": {},
                }
            )
            continue

        parser_layers[facts.parser_layer] += 1
        field_scores: dict[str, dict[str, Any]] = {}
        for field in case.scored_fields:
            expected_items = _scored_expected(case, field)
            score = score_items(expected_items, actual[field])
            field_scores[field] = score
            field_case_counts[field] += 1
            for count_name in ("tp", "fp", "fn"):
                aggregate_counts[field][count_name] += int(score[count_name])

        violations = {
            field: sorted(set(actual[field]) & set(forbidden))
            for field, forbidden in case.must_not_emit.items()
            if set(actual[field]) & set(forbidden)
        }
        must_not_violations += sum(len(items) for items in violations.values())
        case_results.append(
            {
                "case_id": case.case_id,
                "source": case.logical_path,
                "source_sha256": case.source_metadata["content_sha256"],
                "provenance": dict(case.source_metadata),
                "parser_layer": facts.parser_layer,
                "warnings": list(facts.warnings),
                "error": None,
                "field_scores": field_scores,
                "must_not_violations": violations,
            }
        )

    aggregate_scores = {}
    for field, counts in aggregate_counts.items():
        aggregate_scores[field] = {
            "case_count": field_case_counts[field],
            **_score_from_counts(**counts),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "parser_implementation": type(parser).__name__,
        "manifest_sha256": hashlib.sha256(suite.manifest_path.read_bytes()).hexdigest(),
        "case_count": len(suite.cases),
        "crashed": crashed,
        "parser_layers": dict(sorted(parser_layers.items())),
        "must_not_violation_count": must_not_violations,
        "unsupported_fields": list(suite.unsupported_fields),
        "fields": aggregate_scores,
        "cases": case_results,
    }


def load_golden_baseline(
    baseline_path: Path,
    *,
    suite: ParserGoldenSuite,
    parser_id: str,
    sidecar_root: Path | None = None,
) -> dict[str, Any]:
    """Load and validate a checked-in baseline without accepting partial schemas."""

    baseline_path = baseline_path.resolve()
    data = _mapping(json.loads(baseline_path.read_text(encoding="utf-8")), "baseline")
    _require_exact_fields(data, BASELINE_FIELDS, "baseline")
    if data.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError(
            f"baseline.schema_version must be {BASELINE_SCHEMA_VERSION!r}, "
            f"got {data.get('schema_version')!r}"
        )
    if data.get("suite_id") != suite.suite_id:
        raise ValueError(
            f"baseline.suite_id must be {suite.suite_id!r}, got {data.get('suite_id')!r}"
        )
    if data.get("parser") != parser_id:
        raise ValueError(f"baseline.parser must be {parser_id!r}, got {data.get('parser')!r}")

    metadata = data.get("parser_metadata")
    if parser_id == "lexical":
        if metadata is not None:
            raise ValueError("baseline.parser_metadata must be null for lexical")
    elif parser_id == "arkts-tree-sitter-merged":
        if sidecar_root is None:
            raise ValueError("sidecar_root is required for the merged L1 baseline")
        _validate_l1_metadata(_mapping(metadata, "baseline.parser_metadata"), sidecar_root)
    else:
        raise ValueError(f"unsupported baseline parser: {parser_id}")

    _validate_baseline_report(
        _mapping(data.get("report"), "baseline.report"),
        suite,
        parser_id=parser_id,
    )
    return data


def _validate_baseline_report(
    report: dict[str, Any],
    suite: ParserGoldenSuite,
    *,
    parser_id: str,
) -> None:
    context = "baseline.report"
    _require_exact_fields(report, REPORT_FIELDS, context)
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{context}.schema_version must be {SCHEMA_VERSION!r}, "
            f"got {report.get('schema_version')!r}"
        )
    if report.get("suite_id") != suite.suite_id:
        raise ValueError(
            f"{context}.suite_id must be {suite.suite_id!r}, got {report.get('suite_id')!r}"
        )
    expected_implementation = {
        "lexical": "LexicalParser",
        "arkts-tree-sitter-merged": "ArktsTreeSitterParser",
    }[parser_id]
    if report.get("parser_implementation") != expected_implementation:
        raise ValueError(
            f"{context}.parser_implementation must be {expected_implementation!r}, "
            f"got {report.get('parser_implementation')!r}"
        )
    expected_manifest_hash = hashlib.sha256(suite.manifest_path.read_bytes()).hexdigest()
    if report.get("manifest_sha256") != expected_manifest_hash:
        raise ValueError(
            f"{context}.manifest_sha256 must be {expected_manifest_hash!r}, "
            f"got {report.get('manifest_sha256')!r}"
        )
    case_count = _nonnegative_int(report.get("case_count"), f"{context}.case_count")
    if case_count != len(suite.cases):
        raise ValueError(f"{context}.case_count must be {len(suite.cases)}, got {case_count}")
    crashed = _nonnegative_int(report.get("crashed"), f"{context}.crashed")
    if crashed:
        raise ValueError(f"{context}.crashed must be 0 for a reproducible baseline")

    unsupported_fields = [
        _string(item, f"{context}.unsupported_fields[]")
        for item in _list(report.get("unsupported_fields"), f"{context}.unsupported_fields")
    ]
    if unsupported_fields != list(suite.unsupported_fields):
        raise ValueError(f"{context}.unsupported_fields does not match the current Golden manifest")

    raw_fields = _mapping(report.get("fields"), f"{context}.fields")
    _require_exact_fields(raw_fields, EXPECTED_FIELDS, f"{context}.fields")
    raw_cases = _list(report.get("cases"), f"{context}.cases")
    if len(raw_cases) != len(suite.cases):
        raise ValueError(
            f"{context}.cases must contain {len(suite.cases)} cases, got {len(raw_cases)}"
        )

    aggregate_counts = {field: {"tp": 0, "fp": 0, "fn": 0} for field in EXPECTED_FIELDS}
    field_case_counts: Counter[str] = Counter()
    parser_layers: Counter[str] = Counter()
    must_not_violation_count = 0
    for index, (raw_case, golden_case) in enumerate(zip(raw_cases, suite.cases, strict=True)):
        case_context = f"{context}.cases[{index}]"
        case_result = _mapping(raw_case, case_context)
        _require_exact_fields(case_result, CASE_RESULT_FIELDS, case_context)
        if case_result.get("case_id") != golden_case.case_id:
            raise ValueError(
                f"{case_context}.case_id must be {golden_case.case_id!r}, "
                f"got {case_result.get('case_id')!r}"
            )
        if case_result.get("source") != golden_case.logical_path:
            raise ValueError(
                f"{case_context}.source must be {golden_case.logical_path!r}, "
                f"got {case_result.get('source')!r}"
            )
        expected_source_hash = golden_case.source_metadata["content_sha256"]
        if case_result.get("source_sha256") != expected_source_hash:
            raise ValueError(
                f"{case_context}.source_sha256 must be {expected_source_hash!r}, "
                f"got {case_result.get('source_sha256')!r}"
            )
        if case_result.get("provenance") != golden_case.source_metadata:
            raise ValueError(f"{case_context}.provenance does not match the manifest")

        parser_layer = _string(case_result.get("parser_layer"), f"{case_context}.parser_layer")
        parser_layers[parser_layer] += 1
        _string_list(case_result.get("warnings"), f"{case_context}.warnings", allow_empty=True)
        if case_result.get("error") is not None:
            raise ValueError(f"{case_context}.error must be null in a committed baseline")

        field_scores = _mapping(case_result.get("field_scores"), f"{case_context}.field_scores")
        _require_exact_fields(
            field_scores,
            golden_case.scored_fields,
            f"{case_context}.field_scores",
        )
        for field in golden_case.scored_fields:
            score = _mapping(field_scores[field], f"{case_context}.field_scores.{field}")
            counts = _validate_field_score(score, f"{case_context}.field_scores.{field}")
            field_case_counts[field] += 1
            for count_name, count in counts.items():
                aggregate_counts[field][count_name] += count

        violations = _mapping(
            case_result.get("must_not_violations"),
            f"{case_context}.must_not_violations",
        )
        invalid_violation_fields = set(violations) - set(golden_case.must_not_emit)
        if invalid_violation_fields:
            raise ValueError(
                f"{case_context}.must_not_violations contains unsupported fields: "
                f"{sorted(invalid_violation_fields)}"
            )
        for field, raw_values in violations.items():
            values = _string_list(
                raw_values,
                f"{case_context}.must_not_violations.{field}",
                allow_empty=False,
            )
            if values != sorted(set(values)):
                raise ValueError(
                    f"{case_context}.must_not_violations.{field} must be sorted and unique"
                )
            if not set(values).issubset(golden_case.must_not_emit[field]):
                raise ValueError(
                    f"{case_context}.must_not_violations.{field} contains values not "
                    "declared by must_not_emit"
                )
            must_not_violation_count += len(values)

    declared_layers = _mapping(report.get("parser_layers"), f"{context}.parser_layers")
    validated_layers = {
        _string(layer, f"{context}.parser_layers key"): _nonnegative_int(
            count, f"{context}.parser_layers.{layer}"
        )
        for layer, count in declared_layers.items()
    }
    if validated_layers != dict(sorted(parser_layers.items())):
        raise ValueError(f"{context}.parser_layers does not match per-case parser layers")

    declared_violation_count = _nonnegative_int(
        report.get("must_not_violation_count"),
        f"{context}.must_not_violation_count",
    )
    if declared_violation_count != must_not_violation_count:
        raise ValueError(f"{context}.must_not_violation_count does not match per-case violations")

    for field in EXPECTED_FIELDS:
        score_context = f"{context}.fields.{field}"
        aggregate = _mapping(raw_fields[field], score_context)
        _require_exact_fields(aggregate, AGGREGATE_SCORE_FIELDS, score_context)
        expected_aggregate = {
            "case_count": field_case_counts[field],
            **_score_from_counts(**aggregate_counts[field]),
        }
        if aggregate != expected_aggregate:
            raise ValueError(f"{score_context} does not match per-case field scores")


def _validate_field_score(score: dict[str, Any], context: str) -> dict[str, int]:
    _require_exact_fields(score, FIELD_SCORE_FIELDS, context)
    counts = {
        count_name: _nonnegative_int(score.get(count_name), f"{context}.{count_name}")
        for count_name in ("tp", "fp", "fn")
    }
    false_positives = _list(score.get("false_positives"), f"{context}.false_positives")
    false_negatives = _list(score.get("false_negatives"), f"{context}.false_negatives")
    if len(false_positives) != counts["fp"]:
        raise ValueError(f"{context}.false_positives length must equal fp")
    if len(false_negatives) != counts["fn"]:
        raise ValueError(f"{context}.false_negatives length must equal fn")
    expected_metrics = _score_from_counts(**counts)
    for metric in ("precision", "recall", "f1"):
        value = score.get(metric)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{context}.{metric} must be a number")
        if value != expected_metrics[metric]:
            raise ValueError(f"{context}.{metric} does not match tp/fp/fn")
    return counts


def _validate_l1_metadata(metadata: dict[str, Any], sidecar_root: Path) -> None:
    context = "baseline.parser_metadata"
    _require_exact_fields(metadata, L1_METADATA_FIELDS, context)
    package_name = _string(metadata.get("parser_package"), f"{context}.parser_package")
    if package_name != "tree-sitter-arkts":
        raise ValueError(f"{context}.parser_package must be 'tree-sitter-arkts'")
    parser_version = _string(metadata.get("parser_version"), f"{context}.parser_version")
    node_version = _string(metadata.get("node_version"), f"{context}.node_version")
    npm_version = _string(metadata.get("npm_version"), f"{context}.npm_version")
    expected_lock_hash = _string(
        metadata.get("package_lock_sha256"), f"{context}.package_lock_sha256"
    )

    sidecar_root = sidecar_root.resolve()
    version_file = sidecar_root / ".node-version"
    if not version_file.is_file():
        raise ValueError(f"missing L1 runtime pin: {version_file}")
    pinned_node_version = version_file.read_text(encoding="utf-8").strip().removeprefix("v")
    if pinned_node_version != node_version:
        raise ValueError(
            f"{context}.node_version must match .node-version "
            f"({pinned_node_version!r}), got {node_version!r}"
        )

    lock_path = sidecar_root / "package-lock.json"
    if not lock_path.is_file():
        raise ValueError(f"missing L1 package lock: {lock_path}")
    actual_lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    if actual_lock_hash != expected_lock_hash:
        raise ValueError(
            f"{context}.package_lock_sha256 must be {actual_lock_hash!r}, "
            f"got {expected_lock_hash!r}"
        )
    lock_data = _mapping(json.loads(lock_path.read_text(encoding="utf-8")), "package-lock")
    lock_packages = _mapping(lock_data.get("packages"), "package-lock.packages")
    locked_package = _mapping(
        lock_packages.get(f"node_modules/{package_name}"),
        f"package-lock.packages.node_modules/{package_name}",
    )
    if locked_package.get("version") != parser_version:
        raise ValueError(f"{context}.parser_version does not match package-lock.json")

    package_data = _mapping(
        json.loads((sidecar_root / "package.json").read_text(encoding="utf-8")),
        "sidecar package",
    )
    dependencies = _mapping(package_data.get("dependencies"), "sidecar package.dependencies")
    if dependencies.get(package_name) != parser_version:
        raise ValueError(f"{context}.parser_version does not match package.json")

    installed_package_path = sidecar_root / "node_modules" / package_name / "package.json"
    if not installed_package_path.is_file():
        raise ValueError(
            "L1 sidecar dependencies are not installed; run npm ci before strict comparison"
        )
    installed_package = _mapping(
        json.loads(installed_package_path.read_text(encoding="utf-8")),
        f"installed {package_name}",
    )
    if installed_package.get("version") != parser_version:
        raise ValueError(f"{context}.parser_version does not match the installed package")

    actual_node_version = _command_version(("node", "--version")).removeprefix("v")
    if actual_node_version != node_version:
        raise ValueError(
            f"active Node version must be {node_version!r}, got {actual_node_version!r}"
        )
    actual_npm_version = _command_version(("npm", "--version"))
    if actual_npm_version != npm_version:
        raise ValueError(f"active npm version must be {npm_version!r}, got {actual_npm_version!r}")


def _command_version(command: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cannot run {' '.join(command)}: {exc}") from exc
    return _string(completed.stdout.strip(), f"{' '.join(command)} output")


def facts_projection(facts: CodeFacts) -> dict[str, list[Any]]:
    return {
        "imports": _flatten_imports(
            [
                {
                    "module": item.module,
                    "default_name": item.default_name,
                    "namespace_name": item.namespace_name,
                    "named": item.named,
                }
                for item in facts.imports
            ]
        ),
        "components": sorted(facts.components),
        "apis": sorted(facts.apis),
        "decorators": sorted(facts.decorators),
        "attributes": sorted(facts.attributes),
        "symbols": sorted(facts.symbols),
        "syntax": sorted(facts.syntax),
        "declarations": [
            {
                "kind": item.kind,
                "name": item.name,
                "qualified_name": item.qualified_name,
                "parent_name": item.parent_name,
                "span": {
                    "start_line": item.span.start_line,
                    "end_line": item.span.end_line,
                },
            }
            for item in facts.declarations
        ],
    }


def _scored_expected(case: ParserGoldenCase, field: str) -> list[Any]:
    expected_items = case.expected[field]
    if expected_items is None:  # Guarded by manifest validation.
        raise AssertionError(f"scored field {field} has no expected values")
    return expected_items


def score_items(expected: list[Any], actual: list[Any]) -> dict[str, Any]:
    expected_values = Counter(_canonical(item) for item in expected)
    actual_values = Counter(_canonical(item) for item in actual)
    shared = expected_values & actual_values
    false_positives = actual_values - expected_values
    false_negatives = expected_values - actual_values
    return {
        **_score_from_counts(
            tp=sum(shared.values()),
            fp=sum(false_positives.values()),
            fn=sum(false_negatives.values()),
        ),
        "false_positives": _expand_counter(false_positives),
        "false_negatives": _expand_counter(false_negatives),
    }


def format_golden_report(report: dict[str, Any]) -> str:
    lines = [
        f"Parser Golden report: {report['suite_id']}",
        f"  parser: {report['parser_implementation']}",
        f"  cases: {report['case_count']}",
        f"  crashed: {report['crashed']}",
        f"  parser_layers: {report['parser_layers']}",
        f"  must_not_violations: {report['must_not_violation_count']}",
        f"  unsupported_fields: {report['unsupported_fields']}",
        "  field metrics:",
    ]
    for field, score in report["fields"].items():
        lines.append(
            f"    {field}: cases={score['case_count']} "
            f"P={score['precision']:.3f} R={score['recall']:.3f} "
            f"F1={score['f1']:.3f} TP={score['tp']} FP={score['fp']} FN={score['fn']}"
        )
    return "\n".join(lines)


def _load_case(raw: dict[str, Any], root: Path, index: int) -> ParserGoldenCase:
    context = f"manifest.cases[{index}]"
    _require_exact_fields(raw, CASE_FIELDS, context)
    case_id = _string(raw.get("case_id"), f"{context}.case_id")
    description = _string(raw.get("description"), f"{context}.description")
    logical_path = _string(raw.get("logical_path"), f"{context}.logical_path")
    if "\\" in logical_path:
        raise ValueError(f"{context}.logical_path must use '/' separators")
    logical_candidate = Path(logical_path)
    if logical_candidate.is_absolute() or ".." in logical_candidate.parts:
        raise ValueError(f"{context}.logical_path must be relative and stay inside the suite")
    if logical_candidate.suffix != ".ets":
        raise ValueError(f"{context}.logical_path must end in .ets")

    source = _mapping(raw.get("source"), f"{context}.source")
    _validate_source_metadata(source, context)
    source_file = _string(source.get("file"), f"{context}.source.file")
    source_path = (root / source_file).resolve()
    if not source_path.is_relative_to(root.resolve()):
        raise ValueError(f"{context}.source.file escapes the Golden directory")
    if not source_path.is_file():
        raise ValueError(f"{context}.source.file does not exist: {source_file}")
    expected_hash = _sha256(source.get("content_sha256"), f"{context}.source.content_sha256")
    actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(
            f"{context}.source hash mismatch for {source_file}: "
            f"expected {expected_hash}, got {actual_hash}"
        )

    source_kind = _string(source.get("kind"), f"{context}.source.kind")
    if source_kind == "external_snapshot":
        origin_lines = _list(source.get("origin_lines"), f"{context}.source.origin_lines")
        origin_start = _positive_int(origin_lines[0], f"{context}.source.origin_lines[0]")
        origin_end = _positive_int(origin_lines[1], f"{context}.source.origin_lines[1]")
        source_line_count = len(source_path.read_text(encoding="utf-8").splitlines())
        if origin_end < origin_start or origin_end - origin_start + 1 != source_line_count:
            raise ValueError(f"{context}.source.origin_lines must match the snapshot line count")

    scored_fields = tuple(
        _string(item, f"{context}.scored_fields[]")
        for item in _list(raw.get("scored_fields"), f"{context}.scored_fields")
    )
    if not scored_fields:
        raise ValueError(f"{context}.scored_fields must not be empty")
    if len(scored_fields) != len(set(scored_fields)):
        raise ValueError(f"{context}.scored_fields must be unique")
    invalid_scored_fields = set(scored_fields) - set(EXPECTED_FIELDS)
    if invalid_scored_fields:
        raise ValueError(
            f"{context}.scored_fields contains unsupported fields: {sorted(invalid_scored_fields)}"
        )

    raw_expected = _mapping(raw.get("expected"), f"{context}.expected")
    if set(raw_expected) != set(EXPECTED_FIELDS):
        raise ValueError(f"{context}.expected fields must be exactly {sorted(EXPECTED_FIELDS)}")
    expected: dict[str, list[Any] | None] = {}
    for field in EXPECTED_FIELDS:
        value = raw_expected.get(field)
        if field not in scored_fields:
            if value is not None:
                raise ValueError(f"{context}.expected.{field} must be null when unscored")
            expected[field] = None
            continue
        expected[field] = _list(value, f"{context}.expected.{field}")

    imports = expected["imports"]
    if imports is not None:
        expected["imports"] = _flatten_imports(imports)
    for field in SET_FACT_FIELDS:
        raw_values = expected[field]
        if raw_values is None:
            continue
        values = [_string(item, f"{context}.expected.{field}[]") for item in raw_values]
        if values != sorted(set(values)):
            raise ValueError(f"{context}.expected.{field} must be sorted and unique")
        expected[field] = values
    declarations = expected["declarations"]
    if declarations is not None:
        validated_declarations = [
            _validate_declaration(
                item,
                f"{context}.expected.declarations[{item_index}]",
                source_line_count=len(source_path.read_text(encoding="utf-8").splitlines()),
            )
            for item_index, item in enumerate(declarations)
        ]
        _validate_declaration_relationships(validated_declarations, context)
        expected["declarations"] = validated_declarations

    raw_must_not = _mapping(raw.get("must_not_emit", {}), f"{context}.must_not_emit")
    invalid_fields = set(raw_must_not) - set(SET_FACT_FIELDS)
    if invalid_fields:
        raise ValueError(
            f"{context}.must_not_emit contains unsupported fields: {sorted(invalid_fields)}"
        )
    must_not_emit: dict[str, list[str]] = {}
    for field, value in raw_must_not.items():
        forbidden = [
            _string(item, f"{context}.must_not_emit.{field}[]")
            for item in _list(value, f"{context}.must_not_emit.{field}")
        ]
        if forbidden != sorted(set(forbidden)):
            raise ValueError(f"{context}.must_not_emit.{field} must be sorted and unique")
        expected_values = expected[field]
        if expected_values is not None and set(forbidden) & set(expected_values):
            raise ValueError(f"{context}.must_not_emit.{field} overlaps the scored expected truth")
        must_not_emit[field] = forbidden

    return ParserGoldenCase(
        case_id=case_id,
        description=description,
        source_path=source_path,
        logical_path=logical_path,
        source_metadata=dict(source),
        scored_fields=scored_fields,
        expected=expected,
        must_not_emit=must_not_emit,
    )


def _validate_source_metadata(source: dict[str, Any], case_context: str) -> None:
    context = f"{case_context}.source"
    source_kind = _string(source.get("kind"), f"{context}.kind")
    common_fields = {"file", "kind", "content_sha256"}
    provenance_fields = {"source_id", "revision", "relative_path", "license"}
    if source_kind == "synthetic":
        required_fields = common_fields
        allowed_fields = common_fields
    elif source_kind == "grammar_derived":
        required_fields = common_fields | provenance_fields
        allowed_fields = required_fields
    elif source_kind == "external_snapshot":
        required_fields = common_fields | provenance_fields | {"origin_lines"}
        allowed_fields = required_fields | {
            "normalizations",
            "sample_role",
            "upstream_content_sha256",
        }
    else:
        raise ValueError(f"{context}.kind is unsupported: {source_kind}")

    actual_fields = set(source)
    missing = sorted(required_fields - actual_fields)
    extra = sorted(actual_fields - allowed_fields)
    if missing or extra:
        raise ValueError(f"{context} fields mismatch: missing={missing}, extra={extra}")

    source_file = _string(source.get("file"), f"{context}.file")
    source_candidate = Path(source_file)
    if source_candidate.is_absolute() or ".." in source_candidate.parts or "\\" in source_file:
        raise ValueError(f"{context}.file must be a portable relative path")
    if source_candidate.suffix != ".ets":
        raise ValueError(f"{context}.file must end in .ets")
    _sha256(source.get("content_sha256"), f"{context}.content_sha256")

    if source_kind != "synthetic":
        _string(source.get("source_id"), f"{context}.source_id")
        revision = _string(source.get("revision"), f"{context}.revision")
        if source_kind == "external_snapshot":
            _lower_hex(revision, f"{context}.revision", length=40)
        relative_path = _string(source.get("relative_path"), f"{context}.relative_path")
        relative_candidate = Path(relative_path.split("#", 1)[0])
        if (
            relative_candidate.is_absolute()
            or ".." in relative_candidate.parts
            or "\\" in relative_path
        ):
            raise ValueError(f"{context}.relative_path must be portable and relative")
        _string(source.get("license"), f"{context}.license")

    if source_kind != "external_snapshot":
        return

    origin_lines = _list(source.get("origin_lines"), f"{context}.origin_lines")
    if len(origin_lines) != 2:
        raise ValueError(f"{context}.origin_lines must contain [start_line, end_line]")
    if "sample_role" in source:
        _string(source.get("sample_role"), f"{context}.sample_role")
    has_upstream_hash = "upstream_content_sha256" in source
    has_normalizations = "normalizations" in source
    if has_upstream_hash != has_normalizations:
        raise ValueError(
            f"{context}.upstream_content_sha256 and normalizations must be provided together"
        )
    if has_upstream_hash:
        _sha256(
            source.get("upstream_content_sha256"),
            f"{context}.upstream_content_sha256",
        )
        normalizations = _string_list(
            source.get("normalizations"),
            f"{context}.normalizations",
            allow_empty=False,
        )
        if normalizations != sorted(set(normalizations)):
            raise ValueError(f"{context}.normalizations must be sorted and unique")


def _validate_declaration(
    value: object,
    context: str,
    *,
    source_line_count: int,
) -> dict[str, Any]:
    declaration = _mapping(value, context)
    _require_exact_fields(declaration, DECLARATION_FIELDS, context)
    kind = _string(declaration.get("kind"), f"{context}.kind")
    if kind not in DECLARATION_KINDS:
        raise ValueError(f"{context}.kind is unsupported: {kind}")
    span = _mapping(declaration.get("span"), f"{context}.span")
    _require_exact_fields(span, SPAN_FIELDS, f"{context}.span")
    start_line = _positive_int(span.get("start_line"), f"{context}.span.start_line")
    end_line = _positive_int(span.get("end_line"), f"{context}.span.end_line")
    if end_line < start_line:
        raise ValueError(f"{context}.span.end_line must be >= start_line")
    if end_line > source_line_count:
        raise ValueError(f"{context}.span.end_line exceeds the source line count")
    parent_name = declaration.get("parent_name")
    if parent_name is not None:
        parent_name = _string(parent_name, f"{context}.parent_name")
    return {
        "kind": kind,
        "name": _string(declaration.get("name"), f"{context}.name"),
        "qualified_name": _string(declaration.get("qualified_name"), f"{context}.qualified_name"),
        "parent_name": parent_name,
        "span": {"start_line": start_line, "end_line": end_line},
    }


def _validate_declaration_relationships(
    declarations: list[dict[str, Any]],
    case_context: str,
) -> None:
    by_qualified_name: dict[str, list[dict[str, Any]]] = {}
    for declaration in declarations:
        parent_name = declaration["parent_name"]
        expected_qualified_name = (
            declaration["name"] if parent_name is None else f"{parent_name}.{declaration['name']}"
        )
        if declaration["qualified_name"] != expected_qualified_name:
            raise ValueError(
                f"{case_context}.expected.declarations has an inconsistent qualified_name: "
                f"{declaration['qualified_name']!r}"
            )
        by_qualified_name.setdefault(declaration["qualified_name"], []).append(declaration)

    for declaration in declarations:
        parent_name = declaration["parent_name"]
        if parent_name is None:
            continue
        possible_parents = by_qualified_name.get(parent_name, [])
        child_span = declaration["span"]
        if not any(
            parent["span"]["start_line"] <= child_span["start_line"]
            and parent["span"]["end_line"] >= child_span["end_line"]
            for parent in possible_parents
        ):
            raise ValueError(
                f"{case_context}.expected.declarations parent {parent_name!r} "
                f"does not contain {declaration['qualified_name']!r}"
            )


def _flatten_imports(imports: list[Any]) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    for index, value in enumerate(imports):
        item = _mapping(value, f"imports[{index}]")
        _require_exact_fields(item, IMPORT_FIELDS, f"imports[{index}]")
        module = _string(item.get("module"), f"imports[{index}].module")
        default_name = item.get("default_name")
        namespace_name = item.get("namespace_name")
        named = _mapping(item.get("named", {}), f"imports[{index}].named")
        if default_name is not None:
            local = _string(default_name, f"imports[{index}].default_name")
            bindings.append(
                {"module": module, "kind": "default", "imported": "default", "local": local}
            )
        if namespace_name is not None:
            local = _string(namespace_name, f"imports[{index}].namespace_name")
            bindings.append(
                {"module": module, "kind": "namespace", "imported": "*", "local": local}
            )
        for local, imported in sorted(named.items()):
            bindings.append(
                {
                    "module": module,
                    "kind": "named",
                    "imported": _string(imported, f"imports[{index}].named[{local!r}]"),
                    "local": _string(local, f"imports[{index}].named key"),
                }
            )
        if default_name is None and namespace_name is None and not named:
            bindings.append({"module": module, "kind": "side_effect", "imported": "", "local": ""})
    sorted_bindings = sorted(bindings, key=_canonical)
    if len({_canonical(item) for item in sorted_bindings}) != len(sorted_bindings):
        raise ValueError("imports must not contain duplicate bindings")
    return sorted_bindings


def _score_from_counts(tp: int, fp: int, fn: int) -> dict[str, int | float]:
    precision = tp / (tp + fp) if tp + fp else (1.0 if fn == 0 else 0.0)
    recall = tp / (tp + fn) if tp + fn else (1.0 if fp == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _expand_counter(counter: Counter[str]) -> list[Any]:
    values: list[Any] = []
    for encoded, count in sorted(counter.items()):
        values.extend(json.loads(encoded) for _ in range(count))
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
    actual_fields = set(value)
    expected = set(expected_fields)
    if actual_fields != expected:
        missing = sorted(expected - actual_fields)
        extra = sorted(actual_fields - expected)
        raise ValueError(f"{context} fields mismatch: missing={missing}, extra={extra}")


def _list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _string_list(value: object, context: str, *, allow_empty: bool) -> list[str]:
    items = [_string(item, f"{context}[]") for item in _list(value, context)]
    if not allow_empty and not items:
        raise ValueError(f"{context} must not be empty")
    return items


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _sha256(value: object, context: str) -> str:
    return _lower_hex(_string(value, context), context, length=64)


def _lower_hex(value: str, context: str, *, length: int) -> str:
    if len(value) != length or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{context} must be {length} lowercase hexadecimal characters")
    return value


def _positive_int(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{context} must be an integer >= 1")
    return value


def _nonnegative_int(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{context} must be an integer >= 0")
    return value
