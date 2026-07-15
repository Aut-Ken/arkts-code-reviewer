from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
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
    DEFAULT_DIMENSIONS_PATH,
    FeatureConfig,
    load_default_feature_config,
    load_feature_config,
)
from arkts_code_reviewer.feature_routing.engine import FeatureRouter

TAG_TRUTH_SCHEMA_VERSION = "tag-truth-v1"
TAG_TRUTH_REPORT_SCHEMA_VERSION = "tag-truth-report-v1"
TAG_TRUTH_BASELINE_SCHEMA_VERSION = "tag-truth-baseline-v1"

SemanticLabel = Literal[
    "positive",
    "negative",
    "needs_taxonomy_decision",
    "parser_control",
]
SourceKind = Literal["main", "docs_sample", "ohos_test", "synthetic"]
Split = Literal["calibration", "acceptance_holdout", "diagnostic"]
ReviewStatus = Literal["proposed"]


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


def _validate_relative_path(value: str, context: str) -> str:
    if value != value.strip() or "\\" in value:
        raise ValueError(f"{context} must be a trimmed POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{context} must be relative and cannot traverse parents")
    if path.as_posix() != value:
        raise ValueError(f"{context} must be normalized")
    return value


class TagTruthRepository(_FrozenModel):
    source_id: Annotated[str, Field(min_length=1)]
    repository: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class TagTruthCandidate(_FrozenModel):
    tag_id: Annotated[str, Field(pattern=r"^has_[a-z0-9_]+$")]
    status: Literal["Draft"]
    description: Annotated[str, Field(min_length=1)]
    tag_config_version: Annotated[str, Field(min_length=1)]
    config_fingerprint: Annotated[
        str,
        Field(pattern=r"^feature-config:sha256:[0-9a-f]{64}$"),
    ]
    any_import_use: tuple[str, ...]

    @field_validator("any_import_use", mode="before")
    @classmethod
    def parse_import_uses(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "candidate any_import_use")

    @field_validator("any_import_use")
    @classmethod
    def validate_import_uses(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or list(value) != sorted(set(value)):
            raise ValueError("candidate any_import_use must be sorted and unique")
        if any(identity.count("#") != 1 for identity in value):
            raise ValueError("candidate any_import_use must use module#importedName identities")
        return value


class TagTruthGates(_FrozenModel):
    min_positive_cases: Annotated[int, Field(ge=1)]
    min_negative_cases: Annotated[int, Field(ge=1)]
    min_hard_negative_cases: Annotated[int, Field(ge=1)]
    min_holdout_positive_cases: Annotated[int, Field(ge=1)]
    min_holdout_negative_cases: Annotated[int, Field(ge=1)]
    min_precision: Annotated[float, Field(ge=0.0, le=1.0)]
    min_recall: Annotated[float, Field(ge=0.0, le=1.0)]
    max_false_positives: Annotated[int, Field(ge=0)]
    max_file_hint_promotions: Annotated[int, Field(ge=0)]


class TagTruthSource(_FrozenModel):
    alias: Annotated[str, Field(pattern=r"^src[0-9]{3}$")]
    path: str
    content_sha256: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    line_count: Annotated[int, Field(ge=1)]
    source_kind: SourceKind
    app_scope: Annotated[str, Field(min_length=1)]
    source_family_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = _validate_relative_path(value, "source path")
        if not value.endswith((".ets", ".ts")):
            raise ValueError("source path must use .ets or .ts")
        return value

    @field_validator("app_scope")
    @classmethod
    def validate_app_scope(cls, value: str) -> str:
        return _validate_relative_path(value, "app_scope")

    @model_validator(mode="after")
    def validate_source_kind(self) -> TagTruthSource:
        path = f"/{self.path}"
        if self.source_kind == "main" and (
            "/src/main/" not in path or self.path.startswith("code/DocsSample/")
        ):
            raise ValueError("main source_kind requires a non-DocsSample src/main path")
        if self.source_kind == "docs_sample" and not self.path.startswith(
            "code/DocsSample/"
        ):
            raise ValueError("docs_sample source_kind requires code/DocsSample")
        if self.source_kind == "ohos_test" and "/src/ohosTest/" not in path:
            raise ValueError("ohos_test source_kind requires an src/ohosTest path")
        return self


class TagTruthCase(_FrozenModel):
    case_id: Annotated[str, Field(pattern=r"^RDB-[A-Z]+[0-9]{3}$")]
    source_alias: Annotated[str, Field(pattern=r"^src[0-9]{3}$")]
    changed_line: Annotated[int, Field(ge=1)]
    expected_unit_kind: str
    expected_unit_symbol: Annotated[str, Field(min_length=1)]
    semantic_label: SemanticLabel
    expected_shadow_match: bool
    metric_eligible: bool
    split: Split
    stratum: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    evidence_lines: tuple[int, ...]
    rationale: Annotated[str, Field(min_length=1)]
    review_status: ReviewStatus

    @field_validator("evidence_lines", mode="before")
    @classmethod
    def parse_evidence_lines(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "case evidence_lines")

    @field_validator("evidence_lines")
    @classmethod
    def validate_evidence_lines(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or any(line < 1 for line in value):
            raise ValueError("case evidence_lines must contain positive 1-based lines")
        if list(value) != sorted(set(value)):
            raise ValueError("case evidence_lines must be sorted and unique")
        return value

    @field_validator("expected_unit_kind")
    @classmethod
    def validate_unit_kind(cls, value: str) -> str:
        if value not in REVIEW_UNIT_KINDS:
            raise ValueError(f"unsupported expected_unit_kind: {value}")
        return value

    @model_validator(mode="after")
    def validate_metric_policy(self) -> TagTruthCase:
        if self.metric_eligible and self.semantic_label not in {"positive", "negative"}:
            raise ValueError("metric-eligible cases require a positive or negative label")
        if self.metric_eligible and self.split == "diagnostic":
            raise ValueError("diagnostic cases cannot enter product metrics")
        if self.semantic_label == "negative" and self.expected_shadow_match:
            raise ValueError("negative truth cannot expect a shadow match")
        if self.semantic_label == "parser_control" and self.expected_shadow_match:
            raise ValueError("parser controls cannot expect a shadow match")
        if (
            self.metric_eligible
            and self.semantic_label == "negative"
            and not self.stratum.startswith("hard-negative-")
        ):
            raise ValueError("metric-eligible negatives require a hard-negative stratum")
        return self


class TagTruthSuite(_FrozenModel):
    schema_version: Literal["tag-truth-v1"]
    suite_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    description: Annotated[str, Field(min_length=1)]
    annotation_policy_version: Annotated[str, Field(min_length=1)]
    truth_status: Literal["provisional"]
    repository: TagTruthRepository
    base_feature_config_fingerprint: Annotated[
        str,
        Field(pattern=r"^feature-config:sha256:[0-9a-f]{64}$"),
    ]
    candidate: TagTruthCandidate
    gates: TagTruthGates
    sources: tuple[TagTruthSource, ...]
    cases: tuple[TagTruthCase, ...]

    @field_validator("sources", "cases", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"suite {getattr(info, 'field_name', 'sequence')}")

    @model_validator(mode="after")
    def validate_suite(self) -> TagTruthSuite:
        if not self.sources or not self.cases:
            raise ValueError("tag truth suite must contain sources and cases")
        source_aliases = [source.alias for source in self.sources]
        source_paths = [source.path for source in self.sources]
        case_ids = [case.case_id for case in self.cases]
        if source_aliases != sorted(set(source_aliases)):
            raise ValueError("sources must be sorted by unique alias")
        if len(source_paths) != len(set(source_paths)):
            raise ValueError("source paths must be unique")
        if case_ids != sorted(set(case_ids)):
            raise ValueError("cases must be sorted by unique case_id")
        aliases = set(source_aliases)
        referenced_aliases = {case.source_alias for case in self.cases}
        unknown_aliases = sorted(referenced_aliases - aliases)
        if unknown_aliases:
            raise ValueError(f"cases reference unknown source aliases: {unknown_aliases}")
        unused_aliases = sorted(aliases - referenced_aliases)
        if unused_aliases:
            raise ValueError(f"sources are not referenced by cases: {unused_aliases}")
        sources_by_alias = {source.alias: source for source in self.sources}
        for source in self.sources:
            if not PurePosixPath(source.path).is_relative_to(
                PurePosixPath(source.app_scope)
            ):
                raise ValueError(f"source {source.alias} is outside its app_scope")
        for case in self.cases:
            source = sources_by_alias[case.source_alias]
            if case.changed_line > source.line_count:
                raise ValueError(f"case {case.case_id} changed_line exceeds source")
            if any(line > source.line_count for line in case.evidence_lines):
                raise ValueError(f"case {case.case_id} evidence line exceeds source")
            if case.metric_eligible and source.source_kind != "main":
                raise ValueError("only src/main sources may enter product metrics")
        split_by_family: dict[str, set[Split]] = {}
        for case in self.cases:
            source = sources_by_alias[case.source_alias]
            if case.split == "diagnostic":
                continue
            split_by_family.setdefault(source.source_family_id, set()).add(case.split)
        leaking = sorted(family for family, splits in split_by_family.items() if len(splits) > 1)
        if leaking:
            raise ValueError(f"source families cross calibration/holdout: {leaking}")
        return self


@dataclass(frozen=True)
class VerifiedTagTruthCheckout:
    checkout_root: Path
    source_text_by_alias: Mapping[str, str]


def load_tag_truth_suite(path: str | Path) -> TagTruthSuite:
    manifest_path = Path(path)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError(f"tag truth manifest must be a regular file: {manifest_path}")
    try:
        payload = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        return TagTruthSuite.model_validate(payload)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        _DuplicateKeyError,
    ) as exc:
        raise ValueError(f"invalid tag truth manifest {manifest_path}: {exc}") from exc


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
        raise ValueError(f"cannot inspect tag truth checkout: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValueError(f"cannot inspect tag truth checkout: {detail}")
    return completed.stdout.strip()


def verify_tag_truth_checkout(
    suite: TagTruthSuite,
    checkout_root: str | Path,
) -> VerifiedTagTruthCheckout:
    try:
        root = Path(checkout_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"tag truth checkout does not exist: {checkout_root}") from exc
    if not root.is_dir():
        raise ValueError("tag truth checkout root must be a directory")
    try:
        top_level = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    except OSError as exc:
        raise ValueError("tag truth git top level does not exist") from exc
    if top_level != root:
        raise ValueError("checkout root must be the git top level")
    actual_revision = _run_git(root, "rev-parse", "HEAD")
    if actual_revision != suite.repository.revision:
        raise ValueError(
            "tag truth checkout revision mismatch: "
            f"expected {suite.repository.revision}, got {actual_revision}"
        )
    if _run_git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("tag truth checkout must be clean")

    source_text: dict[str, str] = {}
    for source in suite.sources:
        candidate = root.joinpath(*PurePosixPath(source.path).parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"missing tag truth source: {source.path}") from exc
        if not resolved.is_relative_to(root) or candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"unsafe tag truth source: {source.path}")
        nearest_app_scope: str | None = None
        for parent in (candidate.parent, *candidate.parents):
            if parent == root:
                break
            if (parent / "AppScope/app.json5").is_file():
                nearest_app_scope = parent.relative_to(root).as_posix()
                break
        if nearest_app_scope is not None and source.app_scope != nearest_app_scope:
            raise ValueError(
                f"tag truth source app_scope mismatch for {source.path}: "
                f"expected {nearest_app_scope}, got {source.app_scope}"
            )
        try:
            raw = candidate.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read UTF-8 tag truth source: {source.path}") from exc
        actual_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if actual_hash != source.content_sha256:
            raise ValueError(
                f"tag truth source hash mismatch for {source.path}: "
                f"expected {source.content_sha256}, got {actual_hash}"
            )
        actual_line_count = len(raw.splitlines())
        if actual_line_count != source.line_count:
            raise ValueError(
                f"tag truth source line count mismatch for {source.path}: "
                f"expected {source.line_count}, got {actual_line_count}"
            )
        source_text[source.alias] = text
    return VerifiedTagTruthCheckout(root, source_text)


def load_tag_truth_feature_config(
    suite: TagTruthSuite,
    tags_path: str | Path,
) -> FeatureConfig:
    base = load_default_feature_config()
    if base.fingerprint != suite.base_feature_config_fingerprint:
        raise ValueError(
            "default Feature config drifted from tag truth base fingerprint: "
            f"expected {suite.base_feature_config_fingerprint}, got {base.fingerprint}"
        )
    shadow = load_feature_config(tags_path, DEFAULT_DIMENSIONS_PATH)
    if shadow.tag_config.schema_version != "tag-config-v2":
        raise ValueError("RDB shadow config must use frozen tag-config-v2")
    if shadow.fingerprint != suite.candidate.config_fingerprint:
        raise ValueError("shadow Feature config fingerprint does not match manifest")
    if shadow.tag_config.version != suite.candidate.tag_config_version:
        raise ValueError("shadow tag config version does not match manifest")
    expected_ids = set(base.tags_by_id) | {suite.candidate.tag_id}
    if set(shadow.tags_by_id) != expected_ids:
        raise ValueError("shadow config must contain exactly the base Tags plus the candidate")
    for tag_id, base_definition in base.tags_by_id.items():
        if shadow.tags_by_id[tag_id] != base_definition:
            raise ValueError(f"shadow config changed existing Tag {tag_id}")
    candidate = shadow.tags_by_id[suite.candidate.tag_id]
    if candidate.status != "Draft" or candidate.description != suite.candidate.description:
        raise ValueError("shadow candidate status or description does not match manifest")
    triggers = candidate.triggers
    if triggers.any_import_use != suite.candidate.any_import_use:
        raise ValueError("shadow candidate any_import_use does not match manifest")
    if any(
        (
            triggers.any_component,
            triggers.any_api,
            triggers.any_api_prefix,
            triggers.any_api_suffix,
            triggers.any_decorator,
            triggers.any_attribute,
            triggers.any_symbol,
            triggers.any_symbol_leaf,
            triggers.any_syntax,
        )
    ) or triggers.has_resource_reference:
        raise ValueError("shadow candidate may use only any_import_use")
    for dimension in shadow.dimension_config.dimensions:
        if suite.candidate.tag_id in dimension.triggers.any_tag:
            raise ValueError("shadow candidate must not bind a Dimension or Review Question")
    for question in shadow.dimension_config.review_questions:
        if suite.candidate.tag_id in question.triggers.any_tag:
            raise ValueError("shadow candidate must not bind a Dimension or Review Question")
    return shadow


def evaluate_tag_truth_suite(
    suite: TagTruthSuite,
    checkout: VerifiedTagTruthCheckout,
    feature_config: FeatureConfig,
    *,
    file_parser: FileAnalysisParser | None = None,
    unit_builder: ReviewUnitBuilder | None = None,
) -> dict[str, object]:
    parser = file_parser or ArktsFileAnalysisParser()
    builder = unit_builder or ReviewUnitBuilder()
    router = FeatureRouter(feature_config)
    sources_by_alias = {source.alias: source for source in suite.sources}
    cases_by_source: dict[str, list[TagTruthCase]] = {}
    for case in suite.cases:
        cases_by_source.setdefault(case.source_alias, []).append(case)

    rows: list[dict[str, object]] = []
    parse_count = 0
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
                    f"case {case.case_id} must resolve to exactly one ReviewUnit, "
                    f"got {len(built.units)}"
                )
            unit = built.units[0]
            if (
                unit.unit_kind != case.expected_unit_kind
                or unit.unit_symbol != case.expected_unit_symbol
            ):
                raise ValueError(
                    f"case {case.case_id} owner drift: expected "
                    f"{case.expected_unit_kind}:{case.expected_unit_symbol}, got "
                    f"{unit.unit_kind}:{unit.unit_symbol}"
                )
            if any(
                line < unit.source_span.start_line or line > unit.source_span.end_line
                for line in case.evidence_lines
            ):
                raise ValueError(
                    f"case {case.case_id} evidence_lines fall outside the ReviewUnit"
                )
            scope = project(parsed.analysis, unit)
            profile = router.route([scope]).units[0]
            exact_match = suite.candidate.tag_id in profile.shadow_exact_tags
            hint_match = suite.candidate.tag_id in profile.shadow_routing_tags
            candidate_matches = [
                match.to_dict()
                for match in profile.tag_matches
                if match.tag_id == suite.candidate.tag_id
            ]
            candidate_occurrences = [
                occurrence
                for occurrence in parsed.analysis.fact_occurrences
                if occurrence.occurrence_id in scope.exact_occurrence_ids
                and occurrence.kind == "import_use"
                and occurrence.canonical_name in suite.candidate.any_import_use
            ]
            rows.append(
                {
                    "case_id": case.case_id,
                    "source_alias": source.alias,
                    "source_kind": source.source_kind,
                    "app_scope": source.app_scope,
                    "source_family_id": source.source_family_id,
                    "split": case.split,
                    "stratum": case.stratum,
                    "semantic_label": case.semantic_label,
                    "metric_eligible": case.metric_eligible,
                    "review_status": case.review_status,
                    "expected_shadow_match": case.expected_shadow_match,
                    "actual_shadow_match": exact_match,
                    "contract_matched": (
                        exact_match == case.expected_shadow_match and not hint_match
                    ),
                    "file_hint_match": hint_match,
                    "unit_id": unit.unit_id,
                    "unit_kind": unit.unit_kind,
                    "unit_symbol": unit.unit_symbol,
                    "parser_layer": parsed.analysis.parser_quality.layer,
                    "parser_error_nodes": parsed.analysis.parser_quality.error_nodes,
                    "parser_missing_nodes": parsed.analysis.parser_quality.missing_nodes,
                    "file_diagnostics": list(parsed.analysis.diagnostics),
                    "scope_diagnostics": list(scope.diagnostics),
                    "candidate_occurrence_qualities": sorted(
                        {occurrence.quality for occurrence in candidate_occurrences}
                    ),
                    "candidate_occurrence_provenance": sorted(
                        {occurrence.provenance for occurrence in candidate_occurrences}
                    ),
                    "candidate_matches": candidate_matches,
                }
            )

    rows.sort(key=lambda row: str(row["case_id"]))
    cases_by_unit_id: dict[str, list[str]] = {}
    for row in rows:
        cases_by_unit_id.setdefault(str(row["unit_id"]), []).append(str(row["case_id"]))
    duplicate_units = {
        unit_id: case_ids
        for unit_id, case_ids in cases_by_unit_id.items()
        if len(case_ids) > 1
    }
    if duplicate_units:
        details = ", ".join(
            f"{unit_id}={case_ids}"
            for unit_id, case_ids in sorted(duplicate_units.items())
        )
        raise ValueError(f"tag truth cases must resolve to unique ReviewUnits: {details}")
    return build_tag_truth_report(suite, feature_config, rows, parse_count)


def build_tag_truth_report(
    suite: TagTruthSuite,
    feature_config: FeatureConfig,
    rows: list[dict[str, object]],
    parse_count: int,
) -> dict[str, object]:
    eligible = [row for row in rows if row["metric_eligible"] is True]
    metrics = _confusion_metrics(eligible)
    fp = cast(int, metrics["false_positive"])
    precision = cast(float | None, metrics["precision"])
    recall = cast(float | None, metrics["recall"])
    metrics_by_split = {
        split: _confusion_metrics(
            [row for row in eligible if row["split"] == split]
        )
        for split in ("calibration", "acceptance_holdout")
    }
    metrics_by_stratum = {
        stratum: _confusion_metrics(
            [row for row in eligible if row["stratum"] == stratum]
        )
        for stratum in sorted({str(row["stratum"]) for row in eligible})
    }
    metrics_by_app_scope = {
        app_scope: _confusion_metrics(
            [row for row in eligible if row["app_scope"] == app_scope]
        )
        for app_scope in sorted({str(row["app_scope"]) for row in eligible})
    }
    contract_mismatches = [
        str(row["case_id"])
        for row in rows
        if row["contract_matched"] is not True
    ]
    file_hint_promotions = sum(row["file_hint_match"] is True for row in rows)
    taxonomy_conflicts = [
        str(row["case_id"])
        for row in rows
        if row["semantic_label"] == "needs_taxonomy_decision"
    ]
    label_counts = Counter(str(row["semantic_label"]) for row in rows)
    stratum_counts = Counter(str(row["stratum"]) for row in rows)
    review_counts = Counter(str(row["review_status"]) for row in rows)
    positive_count = sum(row["semantic_label"] == "positive" for row in eligible)
    negative_count = sum(row["semantic_label"] == "negative" for row in eligible)
    hard_negative_count = sum(
        row["semantic_label"] == "negative"
        and str(row["stratum"]).startswith("hard-negative-")
        for row in eligible
    )
    contract_perfect = not contract_mismatches and file_hint_promotions == 0
    dataset_ready_for_review = (
        positive_count >= suite.gates.min_positive_cases
        and negative_count >= suite.gates.min_negative_cases
        and contract_perfect
    )
    parser_risk_case_ids = [
        str(row["case_id"])
        for row in eligible
        if row["parser_layer"] != "L1"
        or row["parser_error_nodes"] not in {0, None}
        or row["parser_missing_nodes"] not in {0, None}
        or bool(row["scope_diagnostics"])
    ]
    recovered_signal_case_ids = [
        str(row["case_id"])
        for row in eligible
        if "recovered"
        in cast(Sequence[object], row["candidate_occurrence_qualities"])
    ]
    holdout_metrics = metrics_by_split["acceptance_holdout"]
    activation_failures: list[str] = []
    if not contract_perfect:
        activation_failures.append("contract_mismatch")
    activation_failures.append("truth_v1_is_provisional_only")
    if positive_count < suite.gates.min_positive_cases:
        activation_failures.append("insufficient_positive_cases")
    if negative_count < suite.gates.min_negative_cases:
        activation_failures.append("insufficient_negative_cases")
    if hard_negative_count < suite.gates.min_hard_negative_cases:
        activation_failures.append("insufficient_hard_negatives")
    if (
        cast(int, holdout_metrics["positive_case_count"])
        < suite.gates.min_holdout_positive_cases
    ):
        activation_failures.append("insufficient_holdout_positive_cases")
    if (
        cast(int, holdout_metrics["negative_case_count"])
        < suite.gates.min_holdout_negative_cases
    ):
        activation_failures.append("insufficient_holdout_negative_cases")
    if precision is None or precision < suite.gates.min_precision:
        activation_failures.append("precision_below_gate")
    if recall is None or recall < suite.gates.min_recall:
        activation_failures.append("recall_below_gate")
    holdout_precision = cast(float | None, holdout_metrics["precision"])
    holdout_recall = cast(float | None, holdout_metrics["recall"])
    if (
        holdout_precision is None
        or holdout_precision < suite.gates.min_precision
    ):
        activation_failures.append("holdout_precision_below_gate")
    if holdout_recall is None or holdout_recall < suite.gates.min_recall:
        activation_failures.append("holdout_recall_below_gate")
    if fp > suite.gates.max_false_positives:
        activation_failures.append("false_positives_above_gate")
    if file_hint_promotions > suite.gates.max_file_hint_promotions:
        activation_failures.append("file_hint_promotions_above_gate")
    if taxonomy_conflicts:
        activation_failures.append("unresolved_taxonomy_conflict")
    if parser_risk_case_ids:
        activation_failures.append("metric_case_parser_quality_not_qualified")
    if recovered_signal_case_ids:
        activation_failures.append("recovered_signal_not_separately_qualified")

    return {
        "schema_version": TAG_TRUTH_REPORT_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "truth_status": suite.truth_status,
        "annotation_policy_version": suite.annotation_policy_version,
        "suite_fingerprint": tag_truth_suite_fingerprint(suite),
        "repository": suite.repository.model_dump(mode="json"),
        "base_feature_config_fingerprint": suite.base_feature_config_fingerprint,
        "candidate": {
            **suite.candidate.model_dump(mode="json"),
            "observed_config_fingerprint": feature_config.fingerprint,
        },
        "source_count": len(suite.sources),
        "case_count": len(rows),
        "parse_count": parse_count,
        "contract": {
            "matched_case_count": len(rows) - len(contract_mismatches),
            "mismatched_case_count": len(contract_mismatches),
            "mismatched_case_ids": contract_mismatches,
            "file_hint_promotion_count": file_hint_promotions,
            "perfect": contract_perfect,
        },
        "provisional_semantic_metrics": {
            **metrics,
            "hard_negative_case_count": hard_negative_count,
        },
        "provisional_semantic_metrics_by_split": metrics_by_split,
        "provisional_semantic_metrics_by_stratum": metrics_by_stratum,
        "provisional_semantic_metrics_by_app_scope": metrics_by_app_scope,
        "cohorts": {
            "semantic_labels": dict(sorted(label_counts.items())),
            "strata": dict(sorted(stratum_counts.items())),
            "review_status": dict(sorted(review_counts.items())),
            "taxonomy_conflict_case_ids": taxonomy_conflicts,
            "parser_risk_case_ids": parser_risk_case_ids,
            "recovered_signal_case_ids": recovered_signal_case_ids,
        },
        "quality_decision": {
            "metrics_status": "provisional_not_activation_evidence",
            "dataset_ready_for_human_review": dataset_ready_for_review,
            "activation_ready": not activation_failures,
            "activation_failures": sorted(set(activation_failures)),
        },
        "cases": rows,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def tag_truth_suite_fingerprint(suite: TagTruthSuite) -> str:
    encoded = json.dumps(
        suite.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"tag-truth-suite:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _confusion_metrics(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    tp = sum(
        row["semantic_label"] == "positive" and row["actual_shadow_match"] is True
        for row in rows
    )
    fn = sum(
        row["semantic_label"] == "positive" and row["actual_shadow_match"] is False
        for row in rows
    )
    fp = sum(
        row["semantic_label"] == "negative" and row["actual_shadow_match"] is True
        for row in rows
    )
    tn = sum(
        row["semantic_label"] == "negative" and row["actual_shadow_match"] is False
        for row in rows
    )
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = (
        None
        if precision is None or recall is None or precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )
    return {
        "eligible_case_count": len(rows),
        "positive_case_count": tp + fn,
        "negative_case_count": fp + tn,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def baseline_payload(report: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": TAG_TRUTH_BASELINE_SCHEMA_VERSION,
        "suite_id": report["suite_id"],
        "truth_status": report["truth_status"],
        "annotation_policy_version": report["annotation_policy_version"],
        "suite_fingerprint": report["suite_fingerprint"],
        "repository": report["repository"],
        "base_feature_config_fingerprint": report["base_feature_config_fingerprint"],
        "candidate": report["candidate"],
        "source_count": report["source_count"],
        "case_count": report["case_count"],
        "contract": report["contract"],
        "provisional_semantic_metrics": report["provisional_semantic_metrics"],
        "provisional_semantic_metrics_by_split": report[
            "provisional_semantic_metrics_by_split"
        ],
        "provisional_semantic_metrics_by_stratum": report[
            "provisional_semantic_metrics_by_stratum"
        ],
        "provisional_semantic_metrics_by_app_scope": report[
            "provisional_semantic_metrics_by_app_scope"
        ],
        "cohorts": report["cohorts"],
        "quality_decision": report["quality_decision"],
        "cases": report["cases"],
    }


def assert_strict_tag_truth_baseline(
    report: Mapping[str, object],
    baseline_path: str | Path,
) -> None:
    path = Path(baseline_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"tag truth baseline must be a regular file: {path}")
    try:
        expected = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid tag truth baseline {path}: {exc}") from exc
    actual = baseline_payload(report)
    if expected != actual:
        raise ValueError("tag truth report does not match strict behavior baseline")


__all__ = [
    "TAG_TRUTH_BASELINE_SCHEMA_VERSION",
    "TAG_TRUTH_REPORT_SCHEMA_VERSION",
    "TAG_TRUTH_SCHEMA_VERSION",
    "TagTruthCase",
    "TagTruthCandidate",
    "TagTruthGates",
    "TagTruthRepository",
    "TagTruthSource",
    "TagTruthSuite",
    "VerifiedTagTruthCheckout",
    "assert_strict_tag_truth_baseline",
    "baseline_payload",
    "build_tag_truth_report",
    "evaluate_tag_truth_suite",
    "load_tag_truth_feature_config",
    "load_tag_truth_suite",
    "tag_truth_suite_fingerprint",
    "verify_tag_truth_checkout",
]
