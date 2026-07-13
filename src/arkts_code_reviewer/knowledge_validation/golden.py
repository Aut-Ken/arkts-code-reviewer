from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.models import (
    KNOWLEDGE_REVIEW_SCHEMA_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
)

MANIFEST_SCHEMA_VERSION = "knowledge-golden-v1"
REPORT_SCHEMA_VERSION = "knowledge-golden-report-v1"
BASELINE_SCHEMA_VERSION = "knowledge-golden-baseline-v1"
SUITE_ID = "knowledge-k0"

GoldenSubject = Callable[["KnowledgeGoldenCase"], dict[str, object]]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _sorted_unique(value: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{context} must contain non-empty strings")
    if list(value) != sorted(set(value)):
        raise ValueError(f"{context} must be sorted and unique")
    return value


class _Contract(_FrozenModel):
    knowledge_schema_version: Literal["knowledge-v1"]
    review_schema_version: Literal["knowledge-model-review-v1"]
    tag_ids: tuple[str, ...]
    dimension_ids: tuple[str, ...]

    @field_validator("tag_ids", "dimension_ids")
    @classmethod
    def validate_ids(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _sorted_unique(value, f"contract.{info.field_name}")


class _Source(_FrozenModel):
    file: str
    source_id: str
    repository: str
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    relative_path: str
    authority: str
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    origin_lines: tuple[int, int]

    @field_validator("file", "relative_path")
    @classmethod
    def validate_paths(cls, value: str, info: object) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or not path.parts
            or "." in path.parts
            or ".." in path.parts
            or "\\" in value
        ):
            raise ValueError(f"source.{info.field_name} must be a safe POSIX relative path")
        return str(path)

    @field_validator("source_id", "repository", "authority")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        if not value or value.strip() != value:
            raise ValueError(f"source.{info.field_name} must be non-empty and trimmed")
        return value

    @field_validator("origin_lines")
    @classmethod
    def validate_origin_lines(cls, value: tuple[int, int]) -> tuple[int, int]:
        if (
            len(value) != 2
            or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 1
                for item in value
            )
            or value[1] < value[0]
        ):
            raise ValueError("source.origin_lines must use 1-based inclusive lines")
        return value


class _Span(_FrozenModel):
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_order(self) -> _Span:
        if self.end_line < self.start_line:
            raise ValueError("span.end_line must be >= start_line")
        return self


class _Applicability(_FrozenModel):
    min_api_level: Annotated[int | None, Field(ge=1)] = None
    max_api_level: Annotated[int | None, Field(ge=1)] = None
    releases: tuple[str, ...] = ()
    language_modes: tuple[str, ...] = ()

    @field_validator("releases", "language_modes")
    @classmethod
    def validate_collections(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _sorted_unique(value, f"applicability.{info.field_name}")

    @model_validator(mode="after")
    def validate_range(self) -> _Applicability:
        if (
            self.min_api_level is not None
            and self.max_api_level is not None
            and self.max_api_level < self.min_api_level
        ):
            raise ValueError("applicability.max_api_level must be >= min_api_level")
        return self


class _Example(_FrozenModel):
    kind: Literal["positive", "negative", "neutral"]
    text: str
    source_span: _Span

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value:
            raise ValueError("example.text must not be empty")
        return value


class _ExpectedClause(_FrozenModel):
    rule_id: str
    native_rule_id: str | None
    rule_type: str
    status: Literal["Draft", "Baselined", "Deprecated"]
    text: str
    heading_path: tuple[str, ...]
    parent_context: str | None
    source_span: _Span
    applicability: _Applicability
    examples: tuple[_Example, ...]

    @field_validator("rule_id", "rule_type", "text")
    @classmethod
    def validate_text_fields(cls, value: str, info: object) -> str:
        if not value or value.strip() != value:
            raise ValueError(f"clause.{info.field_name} must be non-empty and trimmed")
        return value

    @field_validator("heading_path")
    @classmethod
    def validate_heading_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or item.strip() != item for item in value):
            raise ValueError("clause.heading_path must contain non-empty trimmed strings")
        return value


class _ExpectedApiSymbol(_FrozenModel):
    canonical_name: str
    kind: str
    signature: str
    since: Annotated[int | None, Field(ge=1)]
    deprecated_since: Annotated[int | None, Field(ge=1)]
    source_span: _Span

    @field_validator("canonical_name", "kind", "signature")
    @classmethod
    def validate_text_fields(cls, value: str, info: object) -> str:
        if not value or value.strip() != value:
            raise ValueError(f"api_symbol.{info.field_name} must be non-empty and trimmed")
        return value

    @model_validator(mode="after")
    def validate_versions(self) -> _ExpectedApiSymbol:
        if (
            self.since is not None
            and self.deprecated_since is not None
            and self.deprecated_since < self.since
        ):
            raise ValueError("api_symbol.deprecated_since must be >= since")
        return self


class _ExpectedAnnotation(_FrozenModel):
    target_id: str
    tags: tuple[str, ...]
    dimension_ids: tuple[str, ...]
    apis: tuple[str, ...]
    domains: tuple[str, ...]
    forbidden_tags: tuple[str, ...]
    forbidden_dimension_ids: tuple[str, ...]

    @field_validator(
        "tags",
        "dimension_ids",
        "apis",
        "domains",
        "forbidden_tags",
        "forbidden_dimension_ids",
    )
    @classmethod
    def validate_collections(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _sorted_unique(value, f"annotation.{info.field_name}")

    @model_validator(mode="after")
    def validate_positive_negative_disjoint(self) -> _ExpectedAnnotation:
        if set(self.tags) & set(self.forbidden_tags):
            raise ValueError("annotation tags and forbidden_tags must be disjoint")
        if set(self.dimension_ids) & set(self.forbidden_dimension_ids):
            raise ValueError(
                "annotation dimension_ids and forbidden_dimension_ids must be disjoint"
            )
        return self


class _Expected(_FrozenModel):
    clauses: tuple[_ExpectedClause, ...]
    api_symbols: tuple[_ExpectedApiSymbol, ...]
    annotations: tuple[_ExpectedAnnotation, ...]

    @model_validator(mode="after")
    def validate_order_and_links(self) -> _Expected:
        clause_ids = [item.rule_id for item in self.clauses]
        api_ids = [item.canonical_name for item in self.api_symbols]
        annotation_ids = [item.target_id for item in self.annotations]
        if clause_ids != sorted(set(clause_ids)):
            raise ValueError("expected.clauses must be sorted and unique by rule_id")
        if api_ids != sorted(set(api_ids)):
            raise ValueError("expected.api_symbols must be sorted and unique")
        if annotation_ids != sorted(set(annotation_ids)):
            raise ValueError("expected.annotations must be sorted and unique")
        if not set(annotation_ids).issubset(set(clause_ids) | set(api_ids)):
            raise ValueError("expected.annotations must target a Clause or API symbol")
        return self


class _Case(_FrozenModel):
    case_id: Annotated[str, Field(pattern=r"^KG[0-9]{3}$")]
    description: str
    source: _Source
    expected: _Expected

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("case.description must be non-empty and trimmed")
        return value


class _Manifest(_FrozenModel):
    schema_version: Literal["knowledge-golden-v1"]
    suite_id: Literal["knowledge-k0"]
    description: str
    contract: _Contract
    cases: tuple[_Case, ...]

    @model_validator(mode="after")
    def validate_cases(self) -> _Manifest:
        if not 12 <= len(self.cases) <= 30:
            raise ValueError("manifest.cases must contain between 12 and 30 cases")
        ids = [case.case_id for case in self.cases]
        if ids != [f"KG{index:03d}" for index in range(1, len(ids) + 1)]:
            raise ValueError("manifest case IDs must be consecutive and sorted")
        return self


@dataclass(frozen=True)
class KnowledgeGoldenSource:
    file_path: Path
    source_id: str
    repository: str
    revision: str
    relative_path: str
    authority: str
    content_sha256: str
    origin_lines: tuple[int, int]
    content: str


@dataclass(frozen=True)
class KnowledgeGoldenCase:
    case_id: str
    description: str
    source: KnowledgeGoldenSource
    expected: dict[str, object]


@dataclass(frozen=True)
class KnowledgeGoldenSuite:
    suite_id: str
    manifest_path: Path
    manifest_sha256: str
    tag_ids: tuple[str, ...]
    dimension_ids: tuple[str, ...]
    cases: tuple[KnowledgeGoldenCase, ...]


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(raw: bytes, context: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError as exc:
        raise ValueError(f"{context} must be UTF-8") from exc
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


def _read_regular_file(path: Path, context: str) -> bytes:
    current = path
    while True:
        if current.is_symlink():
            raise ValueError(f"{context} must not use symlinks")
        if current.parent == current:
            break
        current = current.parent
    if not path.is_file():
        raise ValueError(f"{context} must be a regular file")
    return path.read_bytes()


def _resolve_source(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("Golden source path escapes suite root")
    current = candidate
    while current != root.parent:
        if current.is_symlink():
            raise ValueError("Golden source path must not use symlinks")
        if current == root:
            break
        current = current.parent
    return resolved


def load_golden_suite(manifest_path: str | Path) -> KnowledgeGoldenSuite:
    path = Path(manifest_path)
    raw = _read_regular_file(path, "Knowledge Golden manifest")
    try:
        manifest = _Manifest.model_validate(_load_json(raw, "Knowledge Golden manifest"))
    except ValueError as exc:
        raise ValueError(f"invalid Knowledge Golden manifest: {exc}") from exc

    if manifest.contract.knowledge_schema_version != KNOWLEDGE_SCHEMA_VERSION:
        raise ValueError("Knowledge Golden contract knowledge schema version drift")
    if manifest.contract.review_schema_version != KNOWLEDGE_REVIEW_SCHEMA_VERSION:
        raise ValueError("Knowledge Golden contract review schema version drift")
    feature_config = load_default_feature_config()
    expected_tags = tuple(feature_config.tags_by_id)
    expected_dimensions = tuple(feature_config.dimensions_by_id)
    if manifest.contract.tag_ids != expected_tags:
        raise ValueError("Knowledge Golden Tag registry drift")
    if manifest.contract.dimension_ids != expected_dimensions:
        raise ValueError("Knowledge Golden Dimension registry drift")

    root = path.resolve().parent
    cases: list[KnowledgeGoldenCase] = []
    for case in manifest.cases:
        source_path = _resolve_source(root, case.source.file)
        source_raw = _read_regular_file(source_path, f"Knowledge Golden source {case.case_id}")
        digest = hashlib.sha256(source_raw).hexdigest()
        if digest != case.source.content_sha256:
            raise ValueError(f"Knowledge Golden source hash/provenance drift: {case.case_id}")
        try:
            content = source_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Knowledge Golden source must be UTF-8: {case.case_id}") from exc
        line_count = len(content.splitlines())
        if case.source.origin_lines != (1, line_count):
            raise ValueError(f"Knowledge Golden origin line drift: {case.case_id}")
        for clause in case.expected.clauses:
            if clause.source_span.end_line > line_count:
                raise ValueError(f"Knowledge Golden Clause span out of range: {case.case_id}")
        for symbol in case.expected.api_symbols:
            if symbol.source_span.end_line > line_count:
                raise ValueError(f"Knowledge Golden API span out of range: {case.case_id}")
        annotation_tags = {
            tag
            for annotation in case.expected.annotations
            for tag in (*annotation.tags, *annotation.forbidden_tags)
        }
        annotation_dimensions = {
            dimension
            for annotation in case.expected.annotations
            for dimension in (
                *annotation.dimension_ids,
                *annotation.forbidden_dimension_ids,
            )
        }
        if not annotation_tags.issubset(set(expected_tags)):
            raise ValueError(
                f"Knowledge Golden annotation contains unregistered Tag: {case.case_id}"
            )
        if not annotation_dimensions.issubset(set(expected_dimensions)):
            raise ValueError(
                f"Knowledge Golden annotation contains unregistered Dimension: {case.case_id}"
            )
        cases.append(
            KnowledgeGoldenCase(
                case_id=case.case_id,
                description=case.description,
                source=KnowledgeGoldenSource(
                    file_path=source_path,
                    source_id=case.source.source_id,
                    repository=case.source.repository,
                    revision=case.source.revision,
                    relative_path=case.source.relative_path,
                    authority=case.source.authority,
                    content_sha256=digest,
                    origin_lines=case.source.origin_lines,
                    content=content,
                ),
                expected=case.expected.model_dump(mode="json"),
            )
        )
    return KnowledgeGoldenSuite(
        suite_id=manifest.suite_id,
        manifest_path=path.resolve(),
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        tag_ids=expected_tags,
        dimension_ids=expected_dimensions,
        cases=tuple(cases),
    )


def _empty_subject(_: KnowledgeGoldenCase) -> dict[str, object]:
    return {"clauses": [], "api_symbols": [], "annotations": []}


def _diff(expected: object, actual: object, path: str = "expected") -> list[str]:
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        differences: list[str] = []
        if set(expected) != set(actual):
            differences.append(
                f"{path} fields differ: expected={sorted(expected)} actual={sorted(actual)}"
            )
        for key in sorted(set(expected) & set(actual)):
            differences.extend(_diff(expected[key], actual[key], f"{path}.{key}"))
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        if expected == actual:
            return []
        return [f"{path} differs"]
    if expected != actual:
        return [f"{path}: expected={expected!r} actual={actual!r}"]
    return []


def evaluate_golden_suite(
    suite: KnowledgeGoldenSuite,
    subject: GoldenSubject | None = None,
    *,
    implementation: str = "not-implemented",
) -> dict[str, object]:
    active_subject = _empty_subject if subject is None else subject
    reports: list[dict[str, object]] = []
    for case in suite.cases:
        actual = active_subject(case)
        differences = _diff(case.expected, actual)
        reports.append(
            {
                "case_id": case.case_id,
                "matched": not differences,
                "differences": differences,
                "expected": case.expected,
                "actual": actual,
            }
        )
    matched = sum(bool(report["matched"]) for report in reports)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "implementation": implementation,
        "manifest_sha256": suite.manifest_sha256,
        "case_count": len(reports),
        "matched_case_count": matched,
        "mismatched_case_count": len(reports) - matched,
        "cases": reports,
    }


def is_perfect(report: Mapping[str, object]) -> bool:
    cases = report.get("cases")
    return bool(
        report.get("schema_version") == REPORT_SCHEMA_VERSION
        and isinstance(cases, list)
        and report.get("case_count") == len(cases)
        and report.get("matched_case_count") == len(cases)
        and report.get("mismatched_case_count") == 0
        and all(isinstance(case, dict) and case.get("matched") is True for case in cases)
    )


def write_current_baseline(
    report: Mapping[str, object],
    suite: KnowledgeGoldenSuite,
    baseline_path: str | Path,
) -> None:
    path = Path(baseline_path)
    allowed = suite.manifest_path.parent / "baselines" / "current.json"
    if path.resolve() != allowed.resolve():
        raise ValueError("Knowledge baseline writer may only update baselines/current.json")
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "manifest_sha256": suite.manifest_sha256,
        "report": report,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def assert_strict_baseline(
    report: Mapping[str, object],
    suite: KnowledgeGoldenSuite,
    baseline_path: str | Path,
) -> None:
    path = Path(baseline_path)
    raw = _read_regular_file(path, "Knowledge Golden baseline")
    data = _load_json(raw, "Knowledge Golden baseline")
    if not isinstance(data, dict) or set(data) != {
        "schema_version",
        "suite_id",
        "manifest_sha256",
        "report",
    }:
        raise ValueError("Knowledge baseline fields mismatch")
    if data["schema_version"] != BASELINE_SCHEMA_VERSION:
        raise ValueError("Knowledge baseline schema version drift")
    if data["suite_id"] != suite.suite_id:
        raise ValueError("Knowledge baseline suite drift")
    if data["manifest_sha256"] != suite.manifest_sha256:
        raise ValueError("Knowledge baseline manifest drift")
    if data["report"] != report:
        raise AssertionError("Knowledge strict baseline differs from current report")


__all__ = [
    "KnowledgeGoldenCase",
    "KnowledgeGoldenSuite",
    "assert_strict_baseline",
    "evaluate_golden_suite",
    "is_perfect",
    "load_golden_suite",
    "write_current_baseline",
]
