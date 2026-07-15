from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.code_analysis.file_analysis_models import CodeSourceRef
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    ArktsFileAnalysisParser,
    FileAnalysisParser,
)
from arkts_code_reviewer.code_analysis.models import FileHunk
from arkts_code_reviewer.code_analysis.review_unit_contract import REVIEW_UNIT_KINDS
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.unit_facts import project
from arkts_code_reviewer.feature_routing.config import (
    FeatureConfig,
    load_default_feature_config,
)
from arkts_code_reviewer.feature_routing.engine import FeatureRouter
from arkts_code_reviewer.knowledge.models import Applicability, SourceSpan

TAG_RETRIEVAL_TRUTH_SCHEMA_VERSION = "tag-retrieval-truth-v1"
TAG_RETRIEVAL_KNOWLEDGE_SCHEMA_VERSION = "tag-retrieval-knowledge-fixture-v1"
TAG_RETRIEVAL_TRUTH_OBSERVATION_SCHEMA_VERSION: Literal[
    "tag-retrieval-truth-observation-v1"
] = "tag-retrieval-truth-observation-v1"
TAG_RETRIEVAL_TRUTH_OBSERVATION_V2_SCHEMA_VERSION: Literal[
    "tag-retrieval-truth-observation-v2"
] = "tag-retrieval-truth-observation-v2"
TagRetrievalTruthObservationSchemaVersion = Literal[
    "tag-retrieval-truth-observation-v1",
    "tag-retrieval-truth-observation-v2",
]

_OBSERVATION_V1_CASE_FIELDS = (
    "case_id",
    "target_tag",
    "split",
    "stratum",
    "review_status",
    "expected_exact_tag",
    "actual_exact_tag",
    "expected_routing_tag",
    "actual_routing_tag",
    "exact_matches_truth",
    "routing_matches_truth",
    "missing_required_co_tags",
    "unit_id",
    "unit_kind",
    "unit_symbol",
    "expected_source_span",
    "actual_source_span",
    "parser_layer",
    "parser_error_nodes",
    "parser_missing_nodes",
    "file_diagnostics",
    "scope_diagnostics",
)

TARGET_TAGS = (
    "has_lifecycle",
    "has_network",
    "has_state_management",
    "has_timer",
)
ALLOWED_DOCUMENT_ROOTS = (
    "zh-cn/application-dev/network",
    "zh-cn/application-dev/task-management",
    "zh-cn/application-dev/ui",
)

CaseStratum = Literal[
    "direct_positive",
    "same_file_hint_only_hard_negative",
    "ownership_lookalike_negative",
    "multi_tag_positive",
]
Split = Literal["calibration", "acceptance_holdout"]

_CASE_PREFIX_BY_TAG = {
    "has_lifecycle": "TR-LIFE-",
    "has_network": "TR-NET-",
    "has_state_management": "TR-STATE-",
    "has_timer": "TR-TIMER-",
}
_CLAUSE_PREFIX_BY_TAG = {
    "has_lifecycle": "OHDOC-E2E/LIFE-",
    "has_network": "OHDOC-E2E/NET-",
    "has_state_management": "OHDOC-E2E/STATE-",
    "has_timer": "OHDOC-E2E/TIMER-",
}
_EXPECTED_STRATA = {
    "direct_positive": 6,
    "same_file_hint_only_hard_negative": 3,
    "ownership_lookalike_negative": 2,
    "multi_tag_positive": 1,
}
_EXPECTED_KNOWLEDGE_COUNTS = {
    "has_lifecycle": 7,
    "has_network": 6,
    "has_state_management": 8,
    "has_timer": 3,
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


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _validate_sorted_unique(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(not value or value != value.strip() for value in values):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _validate_relative_path(value: str, context: str) -> str:
    if not value or value != value.strip() or "\\" in value:
        raise ValueError(f"{context} must be a non-empty trimmed POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{context} must be relative and cannot traverse parents")
    if path.as_posix() != value:
        raise ValueError(f"{context} must be normalized")
    return value


def _load_json(path: str | Path, context: str) -> object:
    fixture_path = Path(path)
    if fixture_path.is_symlink() or not fixture_path.is_file():
        raise ValueError(f"{context} must be a regular non-symlink file: {fixture_path}")
    try:
        return json.loads(
            fixture_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid {context} {fixture_path}: {exc}") from exc


class FixtureRepository(_FrozenModel):
    source_id: Annotated[str, Field(min_length=1)]
    repository: Annotated[str, Field(min_length=1)]
    remote: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]

    @field_validator("source_id", "repository", "remote")
    @classmethod
    def validate_identity_field(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("fixture repository identity fields must be trimmed")
        return value


class TagRetrievalTruthSource(_FrozenModel):
    alias: Annotated[str, Field(pattern=r"^src[0-9]{3}$")]
    path: str
    content_sha256: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    line_count: Annotated[int, Field(ge=1)]
    app_scope: str
    source_family_id: Annotated[str, Field(min_length=1)]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = _validate_relative_path(value, "truth source path")
        if not value.endswith((".ets", ".ts")):
            raise ValueError("truth source must use .ets or .ts")
        if "/src/main/" not in f"/{value}" or value.startswith("code/DocsSample/"):
            raise ValueError("metric truth sources must be non-DocsSample src/main code")
        return value

    @field_validator("app_scope")
    @classmethod
    def validate_app_scope(cls, value: str) -> str:
        return _validate_relative_path(value, "truth source app_scope")

    @field_validator("source_family_id")
    @classmethod
    def validate_family(cls, value: str) -> str:
        return _validate_relative_path(value, "truth source family")

    @model_validator(mode="after")
    def validate_scope(self) -> TagRetrievalTruthSource:
        if not PurePosixPath(self.path).is_relative_to(PurePosixPath(self.app_scope)):
            raise ValueError("truth source must stay below app_scope")
        if self.source_family_id != self.app_scope:
            raise ValueError("truth source family must equal its verified app_scope")
        return self


class TagRetrievalTruthCase(_FrozenModel):
    case_id: Annotated[str, Field(pattern=r"^TR-(?:LIFE|NET|STATE|TIMER)-[0-9]{3}$")]
    target_tag: Annotated[str, Field(pattern=r"^has_[a-z0-9_]+$")]
    source_alias: Annotated[str, Field(pattern=r"^src[0-9]{3}$")]
    changed_line: Annotated[int, Field(ge=1)]
    expected_unit_kind: str
    expected_unit_symbol: Annotated[str, Field(min_length=1)]
    expected_source_span: SourceSpan
    stratum: CaseStratum
    expected_exact_tag: bool
    expected_routing_tag: bool
    required_co_tags: tuple[str, ...] = ()
    split: Split
    evidence_lines: tuple[int, ...]
    rationale: Annotated[str, Field(min_length=1)]
    review_status: Literal["proposed"]

    @field_validator("required_co_tags", "evidence_lines", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"truth case {getattr(info, 'field_name', 'sequence')}")

    @field_validator("required_co_tags")
    @classmethod
    def validate_co_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sorted_unique(value, "truth case required_co_tags")

    @field_validator("evidence_lines")
    @classmethod
    def validate_evidence_lines(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or any(line < 1 for line in value):
            raise ValueError("truth case evidence_lines must contain positive 1-based lines")
        if list(value) != sorted(set(value)):
            raise ValueError("truth case evidence_lines must be sorted and unique")
        return value

    @field_validator("expected_unit_kind")
    @classmethod
    def validate_unit_kind(cls, value: str) -> str:
        if value not in REVIEW_UNIT_KINDS:
            raise ValueError(f"unsupported expected_unit_kind: {value}")
        return value

    @model_validator(mode="after")
    def validate_semantics(self) -> TagRetrievalTruthCase:
        if self.target_tag not in TARGET_TAGS:
            raise ValueError(f"unsupported target Tag: {self.target_tag}")
        if not self.case_id.startswith(_CASE_PREFIX_BY_TAG[self.target_tag]):
            raise ValueError("truth case ID does not match target Tag")
        expected = {
            "direct_positive": (True, True),
            "same_file_hint_only_hard_negative": (False, True),
            "ownership_lookalike_negative": (False, False),
            "multi_tag_positive": (True, True),
        }[self.stratum]
        if (self.expected_exact_tag, self.expected_routing_tag) != expected:
            raise ValueError("truth case exact/routing expectation contradicts its stratum")
        if self.stratum == "multi_tag_positive":
            if not self.required_co_tags or self.target_tag in self.required_co_tags:
                raise ValueError("multi-tag truth requires at least one different co-Tag")
        elif self.required_co_tags:
            raise ValueError("only multi-tag truth may declare required_co_tags")
        if not (
            self.expected_source_span.start_line
            <= self.changed_line
            <= self.expected_source_span.end_line
        ):
            raise ValueError("truth case changed_line must be inside expected_source_span")
        if any(
            line < self.expected_source_span.start_line or line > self.expected_source_span.end_line
            for line in self.evidence_lines
        ):
            raise ValueError("truth case evidence_lines must be inside expected_source_span")
        return self


class TagRetrievalTruthSuite(_FrozenModel):
    schema_version: Literal["tag-retrieval-truth-v1"]
    suite_id: Literal["active-tag-retrieval-pilot-v1"]
    description: Annotated[str, Field(min_length=1)]
    annotation_policy_version: Annotated[str, Field(min_length=1)]
    truth_status: Literal["provisional"]
    repository: FixtureRepository
    feature_config_fingerprint: Annotated[
        str,
        Field(pattern=r"^feature-config:sha256:[0-9a-f]{64}$"),
    ]
    sources: tuple[TagRetrievalTruthSource, ...]
    cases: tuple[TagRetrievalTruthCase, ...]

    @field_validator("sources", "cases", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"truth suite {getattr(info, 'field_name', 'sequence')}")

    @model_validator(mode="after")
    def validate_suite(self) -> TagRetrievalTruthSuite:
        config = load_default_feature_config()
        expected_repository = (
            "applications-app-samples",
            "applications_app_samples",
            "https://gitcode.com/openharmony/applications_app_samples.git",
        )
        actual_repository = (
            self.repository.source_id,
            self.repository.repository,
            self.repository.remote,
        )
        if actual_repository != expected_repository:
            raise ValueError("truth suite repository identity drift")
        if self.feature_config_fingerprint != config.fingerprint:
            raise ValueError("truth suite Feature config fingerprint drift")
        if set(TARGET_TAGS) - set(config.tags_by_id):
            raise ValueError("truth suite target Tags are not registered")
        aliases = [source.alias for source in self.sources]
        paths = [source.path for source in self.sources]
        case_ids = [case.case_id for case in self.cases]
        if aliases != sorted(set(aliases)):
            raise ValueError("truth sources must be sorted by unique alias")
        if len(paths) != len(set(paths)):
            raise ValueError("truth source paths must be unique")
        if case_ids != sorted(set(case_ids)):
            raise ValueError("truth cases must be sorted by unique case_id")
        sources_by_alias = {source.alias: source for source in self.sources}
        referenced = {case.source_alias for case in self.cases}
        unknown = sorted(referenced - set(sources_by_alias))
        if unknown:
            raise ValueError(f"truth cases reference unknown sources: {unknown!r}")
        unused = sorted(set(sources_by_alias) - referenced)
        if unused:
            raise ValueError(f"truth sources are unused: {unused!r}")
        locations: set[tuple[str, int, str]] = set()
        for case in self.cases:
            source = sources_by_alias[case.source_alias]
            if case.changed_line > source.line_count or any(
                line > source.line_count for line in case.evidence_lines
            ):
                raise ValueError(f"truth case {case.case_id} line exceeds source")
            if case.expected_source_span.end_line > source.line_count:
                raise ValueError(f"truth case {case.case_id} expected span exceeds source")
            location = (case.source_alias, case.changed_line, case.target_tag)
            if location in locations:
                raise ValueError(
                    "truth cases for one target Tag must use unique source-line locations"
                )
            locations.add(location)
            unknown_co_tags = sorted(set(case.required_co_tags) - set(config.tags_by_id))
            if unknown_co_tags:
                raise ValueError(
                    f"truth case {case.case_id} has unregistered co-Tags: {unknown_co_tags!r}"
                )
        for tag_id in TARGET_TAGS:
            tagged = [case for case in self.cases if case.target_tag == tag_id]
            if len(tagged) != 12:
                raise ValueError(f"truth suite requires 12 cases for {tag_id}")
            if Counter(case.stratum for case in tagged) != _EXPECTED_STRATA:
                raise ValueError(f"truth suite strata drift for {tag_id}")
            if Counter(case.split for case in tagged) != {
                "calibration": 8,
                "acceptance_holdout": 4,
            }:
                raise ValueError(f"truth suite split drift for {tag_id}")
        splits_by_family: dict[str, set[Split]] = {}
        for case in self.cases:
            family = sources_by_alias[case.source_alias].source_family_id
            splits_by_family.setdefault(family, set()).add(case.split)
        leaking = sorted(family for family, splits in splits_by_family.items() if len(splits) > 1)
        if leaking:
            raise ValueError(f"source families cross calibration/holdout: {leaking!r}")
        return self


class KnowledgeFixtureDocument(_FrozenModel):
    alias: Annotated[str, Field(pattern=r"^doc[0-9]{3}$")]
    path: str
    content_sha256: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    line_count: Annotated[int, Field(ge=1)]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = _validate_relative_path(value, "knowledge document path")
        if not value.endswith(".md"):
            raise ValueError("knowledge documents must use Markdown")
        path = PurePosixPath(value)
        if not any(path.is_relative_to(PurePosixPath(root)) for root in ALLOWED_DOCUMENT_ROOTS):
            raise ValueError("knowledge document is outside the approved roots")
        return value


class HashedSourceSpan(_FrozenModel):
    span: SourceSpan
    content_sha256: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class ProposedKnowledgeClause(_FrozenModel):
    rule_id: Annotated[str, Field(pattern=r"^OHDOC-E2E/(?:LIFE|NET|STATE|TIMER)-[0-9]{2}$")]
    target_tag: Annotated[str, Field(pattern=r"^has_[a-z0-9_]+$")]
    document_alias: Annotated[str, Field(pattern=r"^doc[0-9]{3}$")]
    source_span: SourceSpan
    source_span_sha256: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    supporting_source_spans: tuple[HashedSourceSpan, ...] = ()
    rule_type: Literal["constraint", "behavior", "guidance", "applicability"]
    text: Annotated[str, Field(min_length=1)]
    heading_path: tuple[str, ...]
    applicability: Applicability = Applicability()
    dimension_ids: tuple[str, ...]
    review_question_ids: tuple[str, ...]
    tags: tuple[str, ...]
    apis: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    domains: tuple[str, ...]
    raw_keywords: tuple[str, ...] = ()
    scenario: Annotated[str, Field(min_length=1)]
    conditional_only: bool
    applicability_note: Annotated[str, Field(min_length=1)]
    risk_note: Annotated[str, Field(min_length=1)]
    review_status: Literal["proposed"]

    @field_validator(
        "heading_path",
        "dimension_ids",
        "review_question_ids",
        "tags",
        "apis",
        "components",
        "decorators",
        "domains",
        "raw_keywords",
        "supporting_source_spans",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"knowledge clause {getattr(info, 'field_name', 'sequence')}")

    @field_validator(
        "dimension_ids",
        "review_question_ids",
        "tags",
        "apis",
        "components",
        "decorators",
        "domains",
        "raw_keywords",
    )
    @classmethod
    def validate_sorted_fields(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _validate_sorted_unique(
            value,
            f"knowledge clause {getattr(info, 'field_name', 'sequence')}",
        )

    @field_validator("heading_path")
    @classmethod
    def validate_heading_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item or item != item.strip() for item in value):
            raise ValueError("knowledge clause heading_path must contain trimmed headings")
        return value

    @field_validator("text", "scenario", "applicability_note", "risk_note")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("knowledge fixture text must be trimmed single-line text")
        return value

    @model_validator(mode="after")
    def validate_clause(self) -> ProposedKnowledgeClause:
        if self.target_tag not in TARGET_TAGS:
            raise ValueError(f"unsupported target Tag: {self.target_tag}")
        if not self.rule_id.startswith(_CLAUSE_PREFIX_BY_TAG[self.target_tag]):
            raise ValueError("knowledge rule ID does not match target Tag")
        if self.target_tag not in self.tags:
            raise ValueError("knowledge clause tags must contain target_tag")
        if not self.domains:
            raise ValueError("knowledge clause requires at least one Domain")
        if self.target_tag == "has_timer" and not self.conditional_only:
            raise ValueError("timer pilot knowledge must remain conditional-only")
        supporting_keys = [
            (item.span.start_line, item.span.end_line) for item in self.supporting_source_spans
        ]
        if supporting_keys != sorted(set(supporting_keys)):
            raise ValueError("supporting source spans must be sorted and unique")
        return self


class TagRetrievalKnowledgeFixture(_FrozenModel):
    schema_version: Literal["tag-retrieval-knowledge-fixture-v1"]
    fixture_id: Literal["active-tag-official-docs-pilot-v1"]
    description: Annotated[str, Field(min_length=1)]
    fixture_role: Literal["golden_fixture"]
    truth_status: Literal["provisional"]
    source_authority: Literal["official_documentation"]
    repository: FixtureRepository
    allowed_document_roots: tuple[str, ...]
    feature_config_fingerprint: Annotated[
        str,
        Field(pattern=r"^feature-config:sha256:[0-9a-f]{64}$"),
    ]
    documents: tuple[KnowledgeFixtureDocument, ...]
    clauses: tuple[ProposedKnowledgeClause, ...]

    @field_validator("allowed_document_roots", "documents", "clauses", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"knowledge fixture {getattr(info, 'field_name', 'sequence')}")

    @field_validator("allowed_document_roots")
    @classmethod
    def validate_roots(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        value = _validate_sorted_unique(value, "knowledge fixture allowed roots")
        if value != ALLOWED_DOCUMENT_ROOTS:
            raise ValueError("knowledge fixture allowed roots differ from the approved scope")
        return value

    @model_validator(mode="after")
    def validate_fixture(self) -> TagRetrievalKnowledgeFixture:
        config = load_default_feature_config()
        expected_repository = (
            "openharmony-docs",
            "openharmony/docs",
            "https://gitcode.com/openharmony/docs.git",
        )
        actual_repository = (
            self.repository.source_id,
            self.repository.repository,
            self.repository.remote,
        )
        if actual_repository != expected_repository:
            raise ValueError("knowledge fixture repository identity drift")
        if self.feature_config_fingerprint != config.fingerprint:
            raise ValueError("knowledge fixture Feature config fingerprint drift")
        aliases = [document.alias for document in self.documents]
        paths = [document.path for document in self.documents]
        rule_ids = [clause.rule_id for clause in self.clauses]
        if aliases != sorted(set(aliases)):
            raise ValueError("knowledge documents must be sorted by unique alias")
        if len(paths) != len(set(paths)):
            raise ValueError("knowledge document paths must be unique")
        if rule_ids != sorted(set(rule_ids)):
            raise ValueError("knowledge clauses must be sorted by unique rule_id")
        documents_by_alias = {document.alias: document for document in self.documents}
        referenced = {clause.document_alias for clause in self.clauses}
        unknown = sorted(referenced - set(documents_by_alias))
        if unknown:
            raise ValueError(f"knowledge clauses reference unknown documents: {unknown!r}")
        unused = sorted(set(documents_by_alias) - referenced)
        if unused:
            raise ValueError(f"knowledge documents are unused: {unused!r}")
        counts = Counter(clause.target_tag for clause in self.clauses)
        if counts != _EXPECTED_KNOWLEDGE_COUNTS:
            raise ValueError("knowledge fixture target-Tag coverage drift")
        for clause in self.clauses:
            document = documents_by_alias[clause.document_alias]
            if clause.source_span.end_line > document.line_count:
                raise ValueError(f"knowledge clause {clause.rule_id} span exceeds document")
            if any(
                item.span.end_line > document.line_count for item in clause.supporting_source_spans
            ):
                raise ValueError(
                    f"knowledge clause {clause.rule_id} supporting span exceeds document"
                )
            unknown_tags = sorted(set(clause.tags) - set(config.tags_by_id))
            unknown_dimensions = sorted(set(clause.dimension_ids) - set(config.dimensions_by_id))
            unknown_questions = sorted(
                set(clause.review_question_ids) - set(config.review_questions_by_id)
            )
            if unknown_tags or unknown_dimensions or unknown_questions:
                raise ValueError(
                    f"knowledge clause {clause.rule_id} contains unregistered routing IDs"
                )
        return self


@dataclass(frozen=True)
class VerifiedTruthCheckout:
    checkout_root: Path
    source_text_by_alias: Mapping[str, str]


@dataclass(frozen=True)
class VerifiedKnowledgeCheckout:
    checkout_root: Path
    document_bytes_by_alias: Mapping[str, bytes]


def load_tag_retrieval_truth(path: str | Path) -> TagRetrievalTruthSuite:
    payload = _load_json(path, "Tag Retrieval truth manifest")
    try:
        return TagRetrievalTruthSuite.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid Tag Retrieval truth manifest {path}: {exc}") from exc


def load_tag_retrieval_knowledge_fixture(
    path: str | Path,
) -> TagRetrievalKnowledgeFixture:
    payload = _load_json(path, "Tag Retrieval knowledge fixture")
    try:
        return TagRetrievalKnowledgeFixture.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid Tag Retrieval knowledge fixture {path}: {exc}") from exc


def _run_git(checkout_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(checkout_root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot inspect fixture checkout: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValueError(f"cannot inspect fixture checkout: {detail}")
    return completed.stdout.strip()


def _verify_repository(repository: FixtureRepository, checkout_root: str | Path) -> Path:
    try:
        root = Path(checkout_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"fixture checkout does not exist: {checkout_root}") from exc
    if not root.is_dir():
        raise ValueError("fixture checkout root must be a directory")
    try:
        top_level = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    except OSError as exc:
        raise ValueError("fixture git top level does not exist") from exc
    if top_level != root:
        raise ValueError("fixture checkout root must be the git top level")
    actual_remote = _run_git(root, "remote", "get-url", "origin")
    if actual_remote != repository.remote:
        raise ValueError(
            f"fixture checkout remote mismatch: expected {repository.remote}, got {actual_remote}"
        )
    actual_revision = _run_git(root, "rev-parse", "HEAD")
    if actual_revision != repository.revision:
        raise ValueError(
            f"fixture checkout revision mismatch: expected {repository.revision}, "
            f"got {actual_revision}"
        )
    if _run_git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("fixture checkout must be clean")
    return root


def verify_fixture_repository(
    repository: FixtureRepository,
    checkout_root: str | Path,
) -> Path:
    """Verify a fixture checkout's root, origin, revision, and clean state."""
    return _verify_repository(repository, checkout_root)


def _safe_file(root: Path, relative_path: str, context: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"missing {context}: {relative_path}") from exc
    if not resolved.is_relative_to(root) or candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"unsafe {context}: {relative_path}")
    return candidate


def verify_tag_retrieval_truth_checkout(
    suite: TagRetrievalTruthSuite,
    checkout_root: str | Path,
) -> VerifiedTruthCheckout:
    root = verify_fixture_repository(suite.repository, checkout_root)
    source_text: dict[str, str] = {}
    for source in suite.sources:
        candidate = _safe_file(root, source.path, "Tag Retrieval truth source")
        nearest_app_scope: str | None = None
        for parent in (candidate.parent, *candidate.parents):
            if parent == root:
                break
            if (parent / "AppScope/app.json5").is_file():
                nearest_app_scope = parent.relative_to(root).as_posix()
                break
        if source.app_scope != nearest_app_scope:
            raise ValueError(
                f"truth source app_scope mismatch for {source.path}: "
                f"expected {nearest_app_scope}, got {source.app_scope}"
            )
        try:
            raw = candidate.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read UTF-8 truth source: {source.path}") from exc
        actual_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if actual_hash != source.content_sha256:
            raise ValueError(f"truth source hash drift: {source.path}")
        if len(raw.splitlines()) != source.line_count:
            raise ValueError(f"truth source line-count drift: {source.path}")
        source_text[source.alias] = text
    return VerifiedTruthCheckout(root, source_text)


def verify_tag_retrieval_knowledge_checkout(
    fixture: TagRetrievalKnowledgeFixture,
    checkout_root: str | Path,
) -> VerifiedKnowledgeCheckout:
    root = verify_fixture_repository(fixture.repository, checkout_root)
    raw_by_alias: dict[str, bytes] = {}
    documents_by_alias = {document.alias: document for document in fixture.documents}
    for document in fixture.documents:
        candidate = _safe_file(root, document.path, "Tag Retrieval knowledge document")
        try:
            raw = candidate.read_bytes()
            raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read UTF-8 knowledge document: {document.path}") from exc
        actual_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if actual_hash != document.content_sha256:
            raise ValueError(f"knowledge document hash drift: {document.path}")
        if len(raw.splitlines()) != document.line_count:
            raise ValueError(f"knowledge document line-count drift: {document.path}")
        raw_by_alias[document.alias] = raw
    for clause in fixture.clauses:
        document = documents_by_alias[clause.document_alias]
        raw = raw_by_alias[clause.document_alias]
        lines = raw.splitlines(keepends=True)
        span_bytes = b"".join(
            lines[clause.source_span.start_line - 1 : clause.source_span.end_line]
        )
        if not span_bytes.strip():
            raise ValueError(f"knowledge clause {clause.rule_id} source span is empty")
        actual_span_hash = f"sha256:{hashlib.sha256(span_bytes).hexdigest()}"
        if actual_span_hash != clause.source_span_sha256:
            raise ValueError(
                f"knowledge clause source-span hash drift: {clause.rule_id} in {document.path}"
            )
        for supporting in clause.supporting_source_spans:
            supporting_bytes = b"".join(
                lines[supporting.span.start_line - 1 : supporting.span.end_line]
            )
            if not supporting_bytes.strip():
                raise ValueError(
                    f"knowledge clause {clause.rule_id} supporting source span is empty"
                )
            actual_supporting_hash = f"sha256:{hashlib.sha256(supporting_bytes).hexdigest()}"
            if actual_supporting_hash != supporting.content_sha256:
                raise ValueError(
                    f"knowledge clause supporting-span hash drift: {clause.rule_id} "
                    f"in {document.path}"
                )
    return VerifiedKnowledgeCheckout(root, raw_by_alias)


def observe_tag_retrieval_truth(
    suite: TagRetrievalTruthSuite,
    checkout: VerifiedTruthCheckout,
    *,
    feature_config: FeatureConfig | None = None,
    observation_schema_version: TagRetrievalTruthObservationSchemaVersion = (
        TAG_RETRIEVAL_TRUTH_OBSERVATION_SCHEMA_VERSION
    ),
    file_parser: FileAnalysisParser | None = None,
    unit_builder: ReviewUnitBuilder | None = None,
) -> dict[str, object]:
    parser = file_parser or ArktsFileAnalysisParser()
    builder = unit_builder or ReviewUnitBuilder()
    default_config = load_default_feature_config()
    active_config = default_config if feature_config is None else feature_config
    if observation_schema_version not in {
        TAG_RETRIEVAL_TRUTH_OBSERVATION_SCHEMA_VERSION,
        TAG_RETRIEVAL_TRUTH_OBSERVATION_V2_SCHEMA_VERSION,
    }:
        raise ValueError("unsupported Tag Retrieval observation schema")
    if (
        observation_schema_version == TAG_RETRIEVAL_TRUTH_OBSERVATION_SCHEMA_VERSION
        and active_config != default_config
    ):
        raise ValueError("observation-v1 supports only the frozen default Feature config")
    router = FeatureRouter(active_config)
    routing_schema_version = router.route([]).schema_version
    sources_by_alias = {source.alias: source for source in suite.sources}
    cases_by_source: dict[str, list[TagRetrievalTruthCase]] = {}
    for case in suite.cases:
        cases_by_source.setdefault(case.source_alias, []).append(case)

    rows: list[dict[str, object]] = []
    parse_count = 0
    observed_units: set[tuple[str, str]] = set()
    for source_alias in sorted(cases_by_source):
        source = sources_by_alias[source_alias]
        text = checkout.source_text_by_alias[source_alias]
        source_ref = CodeSourceRef.create(
            repository=suite.repository.repository,
            revision=suite.repository.revision,
            path=source.path,
            content_hash=source.content_sha256,
        )
        parsed = parser.parse_file(source_ref, text)
        parse_count += 1
        for case in sorted(cases_by_source[source_alias], key=lambda item: item.case_id):
            built = builder.build_file_result(
                source.path,
                text,
                parsed.compatibility_facts,
                "diff",
                [FileHunk(new_start=case.changed_line, new_lines=1)],
                source_ref_id=source_ref.source_ref_id,
            )
            if len(built.units) != 1:
                raise ValueError(
                    f"truth case {case.case_id} must resolve to exactly one ReviewUnit, "
                    f"got {len(built.units)}"
                )
            unit = built.units[0]
            actual_source_span = {
                "start_line": unit.source_span.start_line,
                "end_line": unit.source_span.end_line,
            }
            expected_source_span = case.expected_source_span.model_dump(mode="json")
            if (
                unit.unit_kind != case.expected_unit_kind
                or unit.unit_symbol != case.expected_unit_symbol
                or actual_source_span != expected_source_span
            ):
                raise ValueError(
                    f"truth case {case.case_id} owner drift: expected "
                    f"{case.expected_unit_kind}:{case.expected_unit_symbol}:"
                    f"{expected_source_span}, got "
                    f"{unit.unit_kind}:{unit.unit_symbol}:{actual_source_span}"
                )
            if any(
                line < unit.source_span.start_line or line > unit.source_span.end_line
                for line in case.evidence_lines
            ):
                raise ValueError(
                    f"truth case {case.case_id} evidence_lines fall outside the ReviewUnit"
                )
            unit_target = (unit.unit_id, case.target_tag)
            if unit_target in observed_units:
                raise ValueError(
                    "Tag Retrieval truth cases for one target Tag must resolve to "
                    "unique ReviewUnits"
                )
            observed_units.add(unit_target)
            scope = project(parsed.analysis, unit)
            routing = router.route([scope])
            if routing.schema_version != routing_schema_version:
                raise ValueError("Feature Routing schema changed within one observation")
            profile = routing.units[0]
            actual_exact = case.target_tag in profile.exact_tags
            actual_routing = case.target_tag in profile.routing_tags
            missing_co_tags = sorted(set(case.required_co_tags) - set(profile.exact_tags))
            rows.append(
                {
                    "case_id": case.case_id,
                    "source_alias": case.source_alias,
                    "changed_line": case.changed_line,
                    "target_tag": case.target_tag,
                    "split": case.split,
                    "stratum": case.stratum,
                    "review_status": case.review_status,
                    "evidence_lines": list(case.evidence_lines),
                    "expected_exact_tag": case.expected_exact_tag,
                    "actual_exact_tag": actual_exact,
                    "expected_routing_tag": case.expected_routing_tag,
                    "actual_routing_tag": actual_routing,
                    "required_co_tags": list(case.required_co_tags),
                    "exact_matches_truth": actual_exact == case.expected_exact_tag,
                    "routing_matches_truth": actual_routing == case.expected_routing_tag,
                    "missing_required_co_tags": missing_co_tags,
                    "exact_tags": list(profile.exact_tags),
                    "routing_tags": list(profile.routing_tags),
                    "exact_symbols": list(scope.unit_exact.symbols),
                    "file_hint_symbols": list(scope.file_hints.symbols),
                    "tag_matches": [match.to_dict() for match in profile.tag_matches],
                    "unit_id": unit.unit_id,
                    "unit_kind": unit.unit_kind,
                    "unit_symbol": unit.unit_symbol,
                    "expected_source_span": expected_source_span,
                    "actual_source_span": actual_source_span,
                    "parser_layer": parsed.analysis.parser_quality.layer,
                    "parser_error_nodes": parsed.analysis.parser_quality.error_nodes,
                    "parser_missing_nodes": parsed.analysis.parser_quality.missing_nodes,
                    "file_diagnostics": list(parsed.analysis.diagnostics),
                    "scope_diagnostics": list(scope.diagnostics),
                }
            )
    rows.sort(key=lambda row: str(row["case_id"]))

    def summarize(selected: list[dict[str, object]]) -> dict[str, int]:
        exact_mismatches = sum(row["exact_matches_truth"] is False for row in selected)
        routing_mismatches = sum(row["routing_matches_truth"] is False for row in selected)
        co_tag_mismatches = sum(bool(row["missing_required_co_tags"]) for row in selected)
        contract_mismatches = sum(
            row["exact_matches_truth"] is False
            or row["routing_matches_truth"] is False
            or bool(row["missing_required_co_tags"])
            for row in selected
        )
        return {
            "case_count": len(selected),
            "expected_exact_positive": sum(row["expected_exact_tag"] is True for row in selected),
            "actual_exact_positive": sum(row["actual_exact_tag"] is True for row in selected),
            "exact_mismatch_count": exact_mismatches,
            "routing_mismatch_count": routing_mismatches,
            "co_tag_mismatch_count": co_tag_mismatches,
            "case_contract_mismatch_count": contract_mismatches,
        }

    by_tag: dict[str, dict[str, int]] = {}
    by_tag_and_split: dict[str, dict[str, dict[str, int]]] = {}
    for tag_id in TARGET_TAGS:
        tagged = [row for row in rows if row["target_tag"] == tag_id]
        by_tag[tag_id] = summarize(tagged)
        by_tag_and_split[tag_id] = {
            split: summarize([row for row in tagged if row["split"] == split])
            for split in ("calibration", "acceptance_holdout")
        }
    serialized_rows = (
        [
            {key: row[key] for key in _OBSERVATION_V1_CASE_FIELDS}
            for row in rows
        ]
        if observation_schema_version
        == TAG_RETRIEVAL_TRUTH_OBSERVATION_SCHEMA_VERSION
        else rows
    )
    observation: dict[str, object] = {
        "schema_version": observation_schema_version,
        "suite_id": suite.suite_id,
        "truth_status": suite.truth_status,
        "source_count": len(suite.sources),
        "case_count": len(rows),
        "parse_count": parse_count,
        "by_tag": by_tag,
        "by_tag_and_split": by_tag_and_split,
        "file_diagnostic_case_counts": dict(
            sorted(
                Counter(
                    str(diagnostic)
                    for row in rows
                    for diagnostic in cast(list[str], row["file_diagnostics"])
                ).items()
            )
        ),
        "scope_diagnostic_case_counts": dict(
            sorted(
                Counter(
                    str(diagnostic)
                    for row in rows
                    for diagnostic in cast(list[str], row["scope_diagnostics"])
                ).items()
            )
        ),
        "exact_mismatch_case_ids": [
            str(row["case_id"]) for row in rows if row["exact_matches_truth"] is False
        ],
        "routing_mismatch_case_ids": [
            str(row["case_id"]) for row in rows if row["routing_matches_truth"] is False
        ],
        "co_tag_mismatch_case_ids": [
            str(row["case_id"]) for row in rows if bool(row["missing_required_co_tags"])
        ],
        "case_contract_mismatch_case_ids": [
            str(row["case_id"])
            for row in rows
            if row["exact_matches_truth"] is False
            or row["routing_matches_truth"] is False
            or bool(row["missing_required_co_tags"])
        ],
        "parser_risk_case_ids": [
            str(row["case_id"])
            for row in rows
            if row["parser_layer"] != "L1"
            or row["parser_error_nodes"] not in {0, None}
            or row["parser_missing_nodes"] not in {0, None}
            or bool(row["file_diagnostics"])
            or bool(row["scope_diagnostics"])
        ],
        "cases": serialized_rows,
    }
    if (
        observation_schema_version
        == TAG_RETRIEVAL_TRUTH_OBSERVATION_V2_SCHEMA_VERSION
    ):
        observation.update(
            {
                "truth_suite_fingerprint": tag_retrieval_truth_fingerprint(suite),
                "feature_config_fingerprint": active_config.fingerprint,
                "tags_config_schema_version": active_config.tag_config.schema_version,
                "tags_config_version": active_config.tag_config.version,
                "feature_routing_schema_version": routing_schema_version,
            }
        )
    return observation


def tag_retrieval_truth_fingerprint(suite: TagRetrievalTruthSuite) -> str:
    encoded = json.dumps(
        suite.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"tag-retrieval-truth:sha256:{hashlib.sha256(encoded).hexdigest()}"


def tag_retrieval_knowledge_fingerprint(
    fixture: TagRetrievalKnowledgeFixture,
) -> str:
    encoded = json.dumps(
        fixture.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"tag-retrieval-knowledge:sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "ALLOWED_DOCUMENT_ROOTS",
    "TAG_RETRIEVAL_KNOWLEDGE_SCHEMA_VERSION",
    "TAG_RETRIEVAL_TRUTH_OBSERVATION_SCHEMA_VERSION",
    "TAG_RETRIEVAL_TRUTH_OBSERVATION_V2_SCHEMA_VERSION",
    "TAG_RETRIEVAL_TRUTH_SCHEMA_VERSION",
    "TagRetrievalTruthObservationSchemaVersion",
    "TARGET_TAGS",
    "KnowledgeFixtureDocument",
    "HashedSourceSpan",
    "FixtureRepository",
    "ProposedKnowledgeClause",
    "TagRetrievalKnowledgeFixture",
    "TagRetrievalTruthCase",
    "TagRetrievalTruthSource",
    "TagRetrievalTruthSuite",
    "VerifiedKnowledgeCheckout",
    "VerifiedTruthCheckout",
    "load_tag_retrieval_knowledge_fixture",
    "load_tag_retrieval_truth",
    "observe_tag_retrieval_truth",
    "tag_retrieval_knowledge_fingerprint",
    "tag_retrieval_truth_fingerprint",
    "verify_tag_retrieval_knowledge_checkout",
    "verify_tag_retrieval_truth_checkout",
    "verify_fixture_repository",
]
