from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.retrieval.config import load_default_retrieval_config
from arkts_code_reviewer.retrieval.models import (
    KnowledgeIndex,
    RetrievalRequest,
    RetrievalUnitRequest,
    TargetPlatform,
    load_knowledge_index,
)
from arkts_code_reviewer.retrieval.service import RetrievalService
from arkts_code_reviewer.retrieval.vector import query_embedding_text

RETRIEVAL_GOLDEN_SCHEMA_VERSION = "retrieval-golden-v1"

_DIAGNOSTIC_CODES = {
    "applicability_unknown",
    "budget_exhausted",
    "context_dispatch_blocked",
    "embedding_unavailable",
    "empty_result",
    "parser_degraded",
    "vector_index_unavailable",
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _hash_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}:sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _strings(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(not value or value != value.strip() for value in values):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


class GoldenQueryEmbedding(_FrozenModel):
    query_text_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    vector: tuple[float, ...]

    @field_validator("vector", mode="before")
    @classmethod
    def parse_vector(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "Golden query vector")

    @field_validator("vector")
    @classmethod
    def validate_vector(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not value or any(not math.isfinite(item) for item in value):
            raise ValueError("Golden query vector must contain finite values")
        return value


class GoldenUnitExpected(_FrozenModel):
    unit_id: Annotated[str, Field(min_length=1)]
    ordered_rule_ids: tuple[str, ...]
    required_rule_ids: tuple[str, ...]
    forbidden_rule_ids: tuple[str, ...]
    covered_dimension_ids: tuple[str, ...]
    uncovered_dimension_ids: tuple[str, ...]
    diagnostic_codes: tuple[str, ...]

    @field_validator("unit_id")
    @classmethod
    def validate_unit_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("Golden expected Unit ID must be trimmed")
        return value

    @field_validator(
        "ordered_rule_ids",
        "required_rule_ids",
        "forbidden_rule_ids",
        "covered_dimension_ids",
        "uncovered_dimension_ids",
        "diagnostic_codes",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "Golden expected collections")

    @field_validator(
        "required_rule_ids",
        "forbidden_rule_ids",
        "covered_dimension_ids",
        "uncovered_dimension_ids",
        "diagnostic_codes",
    )
    @classmethod
    def validate_sorted(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _strings(value, "Golden expected collections")

    @model_validator(mode="after")
    def validate_expected(self) -> GoldenUnitExpected:
        if any(not value or value != value.strip() for value in self.ordered_rule_ids):
            raise ValueError("Golden ordered rules must be non-empty trimmed strings")
        if len(self.ordered_rule_ids) != len(set(self.ordered_rule_ids)):
            raise ValueError("Golden ordered rules must be unique")
        if not set(self.required_rule_ids).issubset(self.ordered_rule_ids):
            raise ValueError("Golden required rules must appear in expected order")
        if set(self.forbidden_rule_ids).intersection(self.ordered_rule_ids):
            raise ValueError("Golden forbidden rules cannot be expected")
        if set(self.covered_dimension_ids).intersection(self.uncovered_dimension_ids):
            raise ValueError("Golden Dimension coverage sets must be disjoint")
        if not set(self.diagnostic_codes).issubset(_DIAGNOSTIC_CODES):
            raise ValueError("Golden expected diagnostics contain unknown codes")
        return self


class RetrievalGoldenCase(_FrozenModel):
    case_id: Annotated[str, Field(pattern=r"^RG-[0-9]{3}$")]
    description: Annotated[str, Field(min_length=1)]
    retrieval_mode: Literal["exact", "hybrid", "embedding_failure"]
    target_platform: TargetPlatform
    units: tuple[RetrievalUnitRequest, ...]
    query_embeddings: tuple[GoldenQueryEmbedding, ...]
    expected_units: tuple[GoldenUnitExpected, ...]
    expected_degraded: bool

    @field_validator("units", "query_embeddings", "expected_units", mode="before")
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "Retrieval Golden case collections")

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError("Golden description must be trimmed single-line text")
        return value

    @model_validator(mode="after")
    def validate_case(self) -> RetrievalGoldenCase:
        unit_ids = [item.unit_id for item in self.units]
        expected_ids = [item.unit_id for item in self.expected_units]
        if not unit_ids or unit_ids != sorted(set(unit_ids)):
            raise ValueError("Golden case Units must be non-empty, sorted, and unique")
        if expected_ids != unit_ids:
            raise ValueError("Golden expected Units must align with input Units")
        embedding_hashes = [item.query_text_hash for item in self.query_embeddings]
        expected_hashes = sorted({_hash_text(query_embedding_text(item)) for item in self.units})
        if self.retrieval_mode == "exact":
            if self.query_embeddings:
                raise ValueError("Exact Golden cases must not carry query embeddings")
        elif embedding_hashes != expected_hashes:
            raise ValueError("Golden query embeddings must uniquely cover exact Unit query texts")
        dimensions_by_unit = {
            item.unit_id: set(item.retrieval_dimension_ids) for item in self.units
        }
        for expected in self.expected_units:
            if (
                set((*expected.covered_dimension_ids, *expected.uncovered_dimension_ids))
                != dimensions_by_unit[expected.unit_id]
            ):
                raise ValueError("Golden expected coverage must partition requested Dimensions")
        return self


class RetrievalGoldenManifest(_FrozenModel):
    schema_version: Literal["retrieval-golden-v1"] = "retrieval-golden-v1"
    index_file: Literal["index.json"]
    index_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    index_version: Annotated[str, Field(pattern=r"^knowledge-index:sha256:[0-9a-f]{64}$")]
    feature_config_version: Annotated[
        str,
        Field(pattern=r"^feature-config:sha256:[0-9a-f]{64}$"),
    ]
    retrieval_config_fingerprint: Annotated[
        str,
        Field(pattern=r"^retrieval-config:sha256:[0-9a-f]{64}$"),
    ]
    retrieval_version: Annotated[str, Field(min_length=1)]
    cases: tuple[RetrievalGoldenCase, ...]

    @field_validator("cases", mode="before")
    @classmethod
    def parse_cases(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "Retrieval Golden cases")

    @field_validator("retrieval_version")
    @classmethod
    def validate_retrieval_version(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("Retrieval Golden version must be trimmed")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> RetrievalGoldenManifest:
        case_ids = [item.case_id for item in self.cases]
        expected_case_ids = [f"RG-{index:03d}" for index in range(1, 37)]
        if case_ids != expected_case_ids:
            raise ValueError("Retrieval Golden v1 requires contiguous RG-001..RG-036 cases")
        if self.feature_config_version != load_default_feature_config().fingerprint:
            raise ValueError("Retrieval Golden feature config drift")
        retrieval_config = load_default_retrieval_config()
        if self.retrieval_config_fingerprint != retrieval_config.fingerprint:
            raise ValueError("Retrieval Golden config drift")
        if self.retrieval_version != retrieval_config.version:
            raise ValueError("Retrieval Golden version drift")
        return self


@dataclass(frozen=True)
class RetrievalGoldenCaseResult:
    case_id: str
    passed: bool
    differences: tuple[str, ...]
    actual_rule_ids: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class RetrievalGoldenReport:
    case_count: int
    passed_cases: int
    recall_at_5: float
    precision_at_5: float
    mrr: float
    forbidden_hits: int
    results: tuple[RetrievalGoldenCaseResult, ...]

    @property
    def perfect(self) -> bool:
        return self.passed_cases == self.case_count and self.forbidden_hits == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "case_count": self.case_count,
            "passed_cases": self.passed_cases,
            "recall_at_5": self.recall_at_5,
            "precision_at_5": self.precision_at_5,
            "mrr": self.mrr,
            "forbidden_hits": self.forbidden_hits,
            "perfect": self.perfect,
            "results": [asdict(item) for item in self.results],
        }


def render_retrieval_golden_report(report: RetrievalGoldenReport) -> str:
    if not isinstance(report, RetrievalGoldenReport):
        raise TypeError("report must use RetrievalGoldenReport")
    return (
        json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


class _FixtureEmbeddingProvider:
    def __init__(
        self,
        index: KnowledgeIndex,
        values: tuple[GoldenQueryEmbedding, ...],
        *,
        fail: bool,
    ) -> None:
        if (
            index.embedding_model is None
            or index.embedding_version is None
            or index.embedding_dimensions is None
        ):
            raise ValueError("Retrieval Golden index must contain embeddings")
        self.model_id = index.embedding_model
        self.version = index.embedding_version
        self.dimensions = index.embedding_dimensions
        self._values = {item.query_text_hash: item.vector for item in values}
        self._fail = fail
        if any(len(value) != self.dimensions for value in self._values.values()):
            raise ValueError("Retrieval Golden query vector dimensions drift")

    def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise RuntimeError("Golden provider does not build indexes")

    def embed_query(self, text: str) -> tuple[float, ...]:
        if self._fail:
            raise RuntimeError("Golden embedding failure")
        try:
            return self._values[_hash_text(text)]
        except KeyError as exc:
            raise ValueError("Golden query text has no reviewed vector") from exc


def _load_json(path: Path, context: str) -> object:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{context} must be a regular non-symlink file")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


def validate_retrieval_golden_baseline(
    path: str | Path,
    report: RetrievalGoldenReport,
) -> None:
    baseline_path = Path(path)
    payload = _load_json(baseline_path, "Retrieval Golden strict baseline")
    rendered = render_retrieval_golden_report(report)
    expected_payload = json.loads(rendered)
    if payload != expected_payload:
        raise ValueError("Retrieval Golden output differs from strict baseline")
    try:
        baseline_text = baseline_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"invalid Retrieval Golden strict baseline: {exc}") from exc
    if baseline_text != rendered:
        raise ValueError("Retrieval Golden strict baseline is not canonical JSON")


def load_retrieval_golden_manifest(
    path: str | Path,
) -> tuple[RetrievalGoldenManifest, KnowledgeIndex]:
    manifest_path = Path(path)
    payload = _load_json(manifest_path, "Retrieval Golden manifest")
    try:
        manifest = RetrievalGoldenManifest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid Retrieval Golden manifest: {exc}") from exc
    relative = PurePosixPath(manifest.index_file)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise ValueError("Retrieval Golden index path is unsafe")
    index_path = manifest_path.parent / relative
    if index_path.is_symlink() or not index_path.is_file():
        raise ValueError("Retrieval Golden index must be a regular non-symlink file")
    if _hash_file(index_path) != manifest.index_hash:
        raise ValueError("Retrieval Golden index content hash drift")
    index = load_knowledge_index(index_path.read_bytes())
    if index.index_version != manifest.index_version:
        raise ValueError("Retrieval Golden index identity drift")
    if index.origin != "golden_fixture":
        raise ValueError("Retrieval Golden requires a fixture-only index")
    if index.feature_config_version != manifest.feature_config_version:
        raise ValueError("Retrieval Golden index feature config drift")
    if index.retrieval_config_fingerprint != manifest.retrieval_config_fingerprint:
        raise ValueError("Retrieval Golden index retrieval config drift")
    if index.retrieval_version != manifest.retrieval_version:
        raise ValueError("Retrieval Golden index retrieval version drift")
    if any(case.retrieval_mode != "exact" for case in manifest.cases):
        if index.embedding_dimensions is None:
            raise ValueError("Retrieval Golden hybrid cases require an embedded index")
        if any(
            len(embedding.vector) != index.embedding_dimensions
            for case in manifest.cases
            for embedding in case.query_embeddings
        ):
            raise ValueError("Retrieval Golden query vector dimensions drift")
    known_rule_ids = {item.clause.rule_id for item in index.records}
    referenced_rule_ids = {
        rule_id
        for case in manifest.cases
        for unit in case.units
        for rule_id in unit.requested_rule_ids
    }.union(
        rule_id
        for case in manifest.cases
        for expected in case.expected_units
        for rule_id in (
            *expected.ordered_rule_ids,
            *expected.required_rule_ids,
            *expected.forbidden_rule_ids,
        )
    )
    unknown_rule_ids = sorted(referenced_rule_ids - known_rule_ids)
    if unknown_rule_ids:
        raise ValueError(f"Retrieval Golden references unknown rules: {unknown_rule_ids!r}")
    return manifest, index


def _exact_only_index(index: KnowledgeIndex) -> KnowledgeIndex:
    return KnowledgeIndex.create(
        origin=index.origin,
        published_build_id=index.published_build_id,
        source_bundle_id=index.source_bundle_id,
        feature_config_version=index.feature_config_version,
        annotation_version=index.annotation_version,
        catalog_version=index.catalog_version,
        retrieval_version=index.retrieval_version,
        retrieval_config_fingerprint=index.retrieval_config_fingerprint,
        embedding_model=None,
        embedding_version=None,
        embedding_dimensions=None,
        api_symbols=index.api_symbols,
        records=tuple(item.model_copy(update={"embedding": None}) for item in index.records),
    )


def _case_request(
    case: RetrievalGoldenCase,
    index: KnowledgeIndex,
) -> RetrievalRequest:
    return RetrievalRequest.create(
        context_plan_id=_stable_id("context-plan", case.case_id),
        feature_routing_id=_stable_id("feature-routing", case.case_id),
        feature_config_version=index.feature_config_version,
        index_version=index.index_version,
        target_platform=case.target_platform,
        total_knowledge_token_budget=sum(item.knowledge_token_budget for item in case.units),
        units=case.units,
    )


def evaluate_retrieval_golden(path: str | Path) -> RetrievalGoldenReport:
    manifest, index = load_retrieval_golden_manifest(path)
    exact_only_index = _exact_only_index(index)
    results: list[RetrievalGoldenCaseResult] = []
    required_total = 0
    required_hits = 0
    precision_sum = 0.0
    reciprocal_ranks: list[float] = []
    forbidden_hits = 0
    for case in manifest.cases:
        case_index = exact_only_index if case.retrieval_mode == "exact" else index
        provider = (
            None
            if case.retrieval_mode == "exact"
            else _FixtureEmbeddingProvider(
                case_index,
                case.query_embeddings,
                fail=case.retrieval_mode == "embedding_failure",
            )
        )
        pack = RetrievalService(
            case_index,
            embedding_provider=provider,
            allow_golden_fixture=True,
        ).retrieve(_case_request(case, case_index))
        differences: list[str] = []
        actual_by_unit = {item.unit_id: item for item in pack.units}
        actual_pairs: list[tuple[str, tuple[str, ...]]] = []
        for expected in case.expected_units:
            actual = actual_by_unit[expected.unit_id]
            actual_ids = tuple(item.rule_id for item in actual.clauses)
            actual_pairs.append((expected.unit_id, actual_ids))
            if actual_ids != expected.ordered_rule_ids:
                differences.append(
                    f"{expected.unit_id}: ordered rules {actual_ids!r} != "
                    f"{expected.ordered_rule_ids!r}"
                )
            actual_codes = tuple(sorted({item.code for item in actual.diagnostics}))
            if actual_codes != expected.diagnostic_codes:
                differences.append(
                    f"{expected.unit_id}: diagnostics {actual_codes!r} != "
                    f"{expected.diagnostic_codes!r}"
                )
            if actual.covered_dimension_ids != expected.covered_dimension_ids:
                differences.append(f"{expected.unit_id}: covered Dimensions differ")
            if actual.uncovered_dimension_ids != expected.uncovered_dimension_ids:
                differences.append(f"{expected.unit_id}: uncovered Dimensions differ")
            required = set(expected.required_rule_ids)
            first_five = set(actual_ids[:5])
            required_total += len(required)
            required_hits += len(required.intersection(first_five))
            relevant = set(expected.ordered_rule_ids)
            precision_sum += (
                len(relevant.intersection(first_five)) / min(5, len(actual_ids))
                if actual_ids
                else float(not relevant)
            )
            first_required_rank = next(
                (rank for rank, rule_id in enumerate(actual_ids, start=1) if rule_id in required),
                None,
            )
            if required:
                reciprocal_ranks.append(
                    0.0 if first_required_rank is None else 1 / first_required_rank
                )
            case_forbidden = set(expected.forbidden_rule_ids).intersection(actual_ids)
            forbidden_hits += len(case_forbidden)
            if case_forbidden:
                differences.append(
                    f"{expected.unit_id}: forbidden rules hit {sorted(case_forbidden)!r}"
                )
        if pack.degraded != case.expected_degraded:
            differences.append(f"degraded {pack.degraded!r} != {case.expected_degraded!r}")
        results.append(
            RetrievalGoldenCaseResult(
                case_id=case.case_id,
                passed=not differences,
                differences=tuple(differences),
                actual_rule_ids=tuple(actual_pairs),
            )
        )
    unit_expectation_count = sum(len(case.expected_units) for case in manifest.cases)
    return RetrievalGoldenReport(
        case_count=len(results),
        passed_cases=sum(item.passed for item in results),
        recall_at_5=round(required_hits / required_total, 6) if required_total else 1.0,
        precision_at_5=round(precision_sum / unit_expectation_count, 6),
        mrr=round(sum(reciprocal_ranks) / len(reciprocal_ranks), 6) if reciprocal_ranks else 1.0,
        forbidden_hits=forbidden_hits,
        results=tuple(results),
    )


__all__ = [
    "RETRIEVAL_GOLDEN_SCHEMA_VERSION",
    "RetrievalGoldenManifest",
    "RetrievalGoldenReport",
    "evaluate_retrieval_golden",
    "load_retrieval_golden_manifest",
    "render_retrieval_golden_report",
    "validate_retrieval_golden_baseline",
]
