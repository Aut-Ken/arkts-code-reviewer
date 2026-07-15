from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.code_analysis.models import SourceSpan
from arkts_code_reviewer.code_analysis.review_unit_contract import REVIEW_UNIT_KINDS

LIFECYCLE_HOLDOUT_SELECTION_SCHEMA_VERSION = "lifecycle-holdout-selection-v1"
LIFECYCLE_HOLDOUT_REVIEW_PACKET_SCHEMA_VERSION = "lifecycle-holdout-review-packet-v1"
LIFECYCLE_HOLDOUT_REVIEW_RECEIPT_SCHEMA_VERSION = "lifecycle-holdout-review-receipt-v1"
LIFECYCLE_HOLDOUT_CONSENSUS_SCHEMA_VERSION = "lifecycle-holdout-consensus-v1"

LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT = "9b7a828449cbe760ce9374d222f75c48b6f5c852"
LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT = (
    "feature-config:sha256:844418e3d7938c816fd3b64b62cdae3d1753d286d50a6a103406838ed6db01e7"
)
LIFECYCLE_CANDIDATE_CORPUS_EXPOSURE_REVISION = "8255a2987f70317cc3a2a4d46044c6b55f092bb3"

LIFECYCLE_RUNTIME_REQUIRED_PATHS = (
    "config/dimensions.yaml",
    "config/tags.yaml",
    "sidecars/arkts-parser/.node-version",
    "sidecars/arkts-parser/package-lock.json",
    "sidecars/arkts-parser/package.json",
    "sidecars/arkts-parser/parse_arkts.js",
    "src/arkts_code_reviewer/__init__.py",
    "src/arkts_code_reviewer/code_analysis/__init__.py",
    "src/arkts_code_reviewer/code_analysis/api_ownership.py",
    "src/arkts_code_reviewer/code_analysis/arkts_lexicon.py",
    "src/arkts_code_reviewer/code_analysis/arkts_tree_sitter_parser.py",
    "src/arkts_code_reviewer/code_analysis/file_analysis_models.py",
    "src/arkts_code_reviewer/code_analysis/file_analysis_parser.py",
    "src/arkts_code_reviewer/code_analysis/lexical.py",
    "src/arkts_code_reviewer/code_analysis/models.py",
    "src/arkts_code_reviewer/code_analysis/review_unit_contract.py",
    "src/arkts_code_reviewer/code_analysis/review_units.py",
    "src/arkts_code_reviewer/code_analysis/text_utils.py",
    "src/arkts_code_reviewer/code_analysis/unit_facts.py",
    "src/arkts_code_reviewer/feature_routing/__init__.py",
    "src/arkts_code_reviewer/feature_routing/config.py",
    "src/arkts_code_reviewer/feature_routing/engine.py",
    "src/arkts_code_reviewer/feature_routing/matcher.py",
    "src/arkts_code_reviewer/feature_routing/models.py",
    "src/arkts_code_reviewer/feature_routing/owner_context.py",
    "src/arkts_code_reviewer/retrieval_validation/lifecycle_symbol_leaf.py",
    "tests/fixtures/feature_routing/tag_config_lifecycle_owner_role_shadow_v1.yaml",
)

# Backwards-compatible import name. Official freeze construction and evaluation use the
# complete Git-derived runtime closure, not merely this minimum critical subset.
LIFECYCLE_RUNTIME_PATHS = LIFECYCLE_RUNTIME_REQUIRED_PATHS

LIFECYCLE_TAG_CONTRACT_PATH = "tests/evaluation/lifecycle_blind_holdout_v1/tag_contract.md"
LIFECYCLE_REVIEW_POLICY_PATH = "tests/evaluation/lifecycle_blind_holdout_v1/review_policy.md"
LIFECYCLE_SELECTION_POLICY_PATH = "tests/evaluation/lifecycle_blind_holdout_v1/selection_policy.md"
LIFECYCLE_TAG_CONTRACT_VERSION = "lifecycle-exact-tag-contract-v1"
LIFECYCLE_REVIEW_POLICY_VERSION = "lifecycle-blind-review-policy-v1"
LIFECYCLE_SELECTION_POLICY_VERSION = "lifecycle-holdout-v1"

LIFECYCLE_REQUIRED_STRATUM_MINIMUMS = {
    "component_v1_positive": 4,
    "component_v2_positive": 4,
    "nested_owner_negative": 4,
    "non_entry_page_negative": 4,
    "ordinary_owner_negative": 4,
    "router_page_positive": 8,
    "routing_only_negative": 4,
}
LIFECYCLE_POSITIVE_STRATA = frozenset(
    {"component_v1_positive", "component_v2_positive", "router_page_positive"}
)
LIFECYCLE_CRITICAL_NEGATIVE_STRATA = (
    "nested_owner_negative",
    "non_entry_page_negative",
    "ordinary_owner_negative",
    "routing_only_negative",
)

LIFECYCLE_EVALUATION_HARNESS_PATHS = (
    "pyproject.toml",
    "src/arkts_code_reviewer/retrieval_validation/lifecycle_blind_holdout.py",
    "src/arkts_code_reviewer/retrieval_validation/lifecycle_blind_holdout_evaluation.py",
    "src/arkts_code_reviewer/retrieval_validation/tag_retrieval_fixture.py",
    "tests/evaluation/lifecycle_blind_holdout_v1/review_policy.md",
    "tests/evaluation/lifecycle_blind_holdout_v1/selection_policy.md",
    "tests/evaluation/lifecycle_blind_holdout_v1/tag_contract.md",
    "tests/evaluation/tag_retrieval/manifest.json",
    "tools/build_lifecycle_blind_consensus.py",
    "tools/build_lifecycle_blind_review_packet.py",
    "tools/evaluate_lifecycle_owner_role_holdout.py",
    "tools/lifecycle_holdout_preflight.py",
    "tools/seal_lifecycle_blind_review_receipt.py",
    "tools/seal_lifecycle_blind_selection.py",
)

_SHA256 = r"^sha256:[0-9a-f]{64}$"
_GIT_REVISION = r"^[0-9a-f]{40}$"
_SELECTION_ID = r"^lifecycle-holdout-selection:sha256:[0-9a-f]{64}$"
_PACKET_ID = r"^lifecycle-holdout-review-packet:sha256:[0-9a-f]{64}$"
_RECEIPT_ID = r"^lifecycle-holdout-review-receipt:sha256:[0-9a-f]{64}$"
_CONSENSUS_ID = r"^lifecycle-holdout-consensus:sha256:[0-9a-f]{64}$"
_CASE_ID = r"^LH-[0-9]{4}$"
_ALLOWED_LABELS = {"positive", "negative", "needs_taxonomy_decision"}
_MODULE_BOUNDARY_NAMES = {
    "casesfeature",
    "entry",
    "feature",
    "features",
    "product",
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


def _sorted_unique(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(not value or value != value.strip() for value in values):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _single_line(value: str, context: str) -> str:
    if not value or value != value.strip() or any(ord(character) < 32 for character in value):
        raise ValueError(f"{context} must be non-empty trimmed single-line text")
    return value


def _relative_path(value: str, context: str) -> str:
    if not value or value != value.strip() or "\\" in value:
        raise ValueError(f"{context} must be a non-empty trimmed POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{context} must be relative and cannot traverse parents")
    if path.as_posix() != value:
        raise ValueError(f"{context} must be normalized")
    return value


def derive_source_family_id(source_path: str) -> str:
    normalized = _relative_path(source_path, "holdout source path")
    parts = PurePosixPath(normalized).parts
    try:
        src_index = parts.index("src")
    except ValueError as exc:
        raise ValueError("holdout source path must contain a src module boundary") from exc
    module_prefix = parts[:src_index]
    if len(module_prefix) < 2:
        raise ValueError("holdout source path has no app-family boundary")
    boundary_index: int | None = None
    for index, part in enumerate(module_prefix):
        lowered = part.lower()
        if (
            lowered in _MODULE_BOUNDARY_NAMES
            or (lowered.startswith("har") and len(lowered) <= 4)
            or (lowered.startswith("hsp") and len(lowered) <= 4)
        ):
            boundary_index = index
            break
    family_parts = (
        module_prefix[:boundary_index] if boundary_index is not None else module_prefix[:-1]
    )
    if len(family_parts) < 2:
        raise ValueError("holdout source path resolves to an unsafe app-family boundary")
    return PurePosixPath(*family_parts).as_posix()


def _path_scopes_overlap(left: str, right: str) -> bool:
    left_path = PurePosixPath(left)
    right_path = PurePosixPath(right)
    return left_path.is_relative_to(right_path) or right_path.is_relative_to(left_path)


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def canonical_hash(prefix: str, payload: object) -> str:
    return f"{prefix}:sha256:{hashlib.sha256(canonical_json(payload).encode()).hexdigest()}"


def bytes_hash(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={identity_field})


class HoldoutRepository(_FrozenModel):
    source_id: Literal["applications-app-samples"]
    repository: Literal["applications_app_samples"]
    remote: Literal["https://gitcode.com/openharmony/applications_app_samples.git"]
    revision: Annotated[str, Field(pattern=_GIT_REVISION)]


class RuntimeFileSnapshot(_FrozenModel):
    path: str
    content_sha256: Annotated[str, Field(pattern=_SHA256)]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value, "runtime snapshot path")


def runtime_bundle_fingerprint(files: Sequence[RuntimeFileSnapshot]) -> str:
    payload = [item.model_dump(mode="json") for item in files]
    return canonical_hash("lifecycle-candidate-runtime", payload)


def evaluation_harness_fingerprint(files: Sequence[RuntimeFileSnapshot]) -> str:
    payload = [item.model_dump(mode="json") for item in files]
    return canonical_hash("lifecycle-evaluation-harness", payload)


def _directory_tree_fingerprint(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"runtime dependency tree is unavailable: {root}")
    payload: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            try:
                resolved = path.resolve(strict=True)
                link_target = os.readlink(path)
                raw = resolved.read_bytes()
            except OSError as exc:
                raise ValueError(f"cannot read runtime dependency symlink: {path}") from exc
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise ValueError(f"runtime dependency symlink escapes its tree: {path}")
            payload.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "symlink_target": link_target,
                    "target_content_sha256": bytes_hash(raw),
                }
            )
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"runtime dependency tree contains an unsafe entry: {path}")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read runtime dependency: {path}") from exc
        payload.append(
            {
                "path": path.relative_to(root).as_posix(),
                "content_sha256": bytes_hash(raw),
            }
        )
    if not payload:
        raise ValueError("runtime dependency tree cannot be empty")
    return canonical_hash("lifecycle-sidecar-dependencies", payload)


class RuntimeEnvironment(_FrozenModel):
    python_version: Annotated[str, Field(pattern=r"^3\.12\.[0-9]+$")]
    python_packages: tuple[str, ...]
    platform_system: Annotated[str, Field(min_length=1)]
    platform_machine: Annotated[str, Field(min_length=1)]
    node_version: Annotated[str, Field(pattern=r"^v[0-9]+\.[0-9]+\.[0-9]+$")]
    node_executable_sha256: Annotated[str, Field(pattern=_SHA256)]
    sidecar_dependencies_fingerprint: Annotated[
        str,
        Field(pattern=r"^lifecycle-sidecar-dependencies:sha256:[0-9a-f]{64}$"),
    ]

    @field_validator("python_packages", mode="before")
    @classmethod
    def parse_packages(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "runtime Python packages")

    @field_validator("python_packages")
    @classmethod
    def validate_packages(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value, "runtime Python packages")


class CandidateFreeze(_FrozenModel):
    candidate_commit: Literal["9b7a828449cbe760ce9374d222f75c48b6f5c852"]
    tags_config_schema_version: Literal["tag-config-v4"]
    tags_config_version: Literal["tags-lifecycle-owner-role-shadow-v1"]
    feature_routing_schema_version: Literal["feature-routing-v3"]
    feature_config_fingerprint: Literal[
        "feature-config:sha256:844418e3d7938c816fd3b64b62cdae3d1753d286d50a6a103406838ed6db01e7"
    ]
    candidate_corpus_exposure_revision: Literal["8255a2987f70317cc3a2a4d46044c6b55f092bb3"]
    candidate_corpus_exposure_scope: Literal["entire_tracked_repository"]
    runtime_files: tuple[RuntimeFileSnapshot, ...]
    runtime_bundle_fingerprint: Annotated[
        str,
        Field(pattern=r"^lifecycle-candidate-runtime:sha256:[0-9a-f]{64}$"),
    ]
    runtime_environment: RuntimeEnvironment
    evaluation_harness_commit: Annotated[str, Field(pattern=_GIT_REVISION)]
    evaluation_harness_files: tuple[RuntimeFileSnapshot, ...]
    evaluation_harness_fingerprint: Annotated[
        str,
        Field(pattern=r"^lifecycle-evaluation-harness:sha256:[0-9a-f]{64}$"),
    ]

    @field_validator("runtime_files", "evaluation_harness_files", mode="before")
    @classmethod
    def parse_file_snapshots(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"candidate freeze {getattr(info, 'field_name', 'files')}")

    @model_validator(mode="after")
    def validate_runtime_bundle(self) -> CandidateFreeze:
        paths = tuple(item.path for item in self.runtime_files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("candidate runtime snapshot paths must be sorted and unique")
        missing_required = sorted(set(LIFECYCLE_RUNTIME_REQUIRED_PATHS) - set(paths))
        if missing_required:
            raise ValueError(
                f"candidate runtime snapshot omits critical files: {missing_required!r}"
            )
        if self.runtime_bundle_fingerprint != runtime_bundle_fingerprint(self.runtime_files):
            raise ValueError("candidate runtime bundle fingerprint mismatch")
        harness_paths = tuple(item.path for item in self.evaluation_harness_files)
        if harness_paths != LIFECYCLE_EVALUATION_HARNESS_PATHS:
            raise ValueError("evaluation harness snapshot path set drift")
        if self.evaluation_harness_fingerprint != evaluation_harness_fingerprint(
            self.evaluation_harness_files
        ):
            raise ValueError("evaluation harness bundle fingerprint mismatch")
        return self


class DevelopmentExclusions(_FrozenModel):
    truth_suite_fingerprint: Annotated[
        str,
        Field(pattern=r"^tag-retrieval-truth:sha256:[0-9a-f]{64}$"),
    ]
    source_family_ids: tuple[str, ...]
    source_paths: tuple[str, ...]
    content_sha256: tuple[Annotated[str, Field(pattern=_SHA256)], ...]

    @field_validator(
        "source_family_ids",
        "source_paths",
        "content_sha256",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"development exclusions {getattr(info, 'field_name', '')}")

    @field_validator("source_family_ids", "source_paths", "content_sha256")
    @classmethod
    def validate_sequences(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "values")
        normalized = _sorted_unique(value, f"development exclusions {field_name}")
        if field_name in {"source_family_ids", "source_paths"}:
            for item in normalized:
                _relative_path(item, f"development exclusions {field_name}")
        return normalized


class HoldoutStratumPolicy(_FrozenModel):
    stratum_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    selected_case_count: Annotated[int, Field(ge=1)]


class HoldoutQualityGates(_FrozenModel):
    minimum_case_count: Annotated[int, Field(ge=1)]
    minimum_positive_cases: Annotated[int, Field(ge=1)]
    minimum_negative_cases: Annotated[int, Field(ge=1)]
    minimum_source_families: Annotated[int, Field(ge=1)]
    minimum_precision: Annotated[float, Field(ge=0.0, le=1.0)]
    minimum_recall: Annotated[float, Field(ge=0.0, le=1.0)]
    minimum_precision_wilson_95: Annotated[float, Field(ge=0.0, le=1.0)]
    minimum_recall_wilson_95: Annotated[float, Field(ge=0.0, le=1.0)]
    maximum_false_positives: Annotated[int, Field(ge=0)]
    maximum_false_negatives: Annotated[int, Field(ge=0)]
    maximum_critical_false_positives: Annotated[int, Field(ge=0)]
    maximum_parser_risk_cases: Literal[0]
    maximum_provenance_failure_cases: Literal[0]
    maximum_file_hint_promotions: Literal[0]
    maximum_review_unit_risk_cases: Literal[0]
    maximum_scope_risk_cases: Literal[0]
    maximum_routing_only_failures: Literal[0]
    maximum_challenge_owner_failures: Literal[0]
    critical_negative_strata: tuple[str, ...]

    @field_validator("critical_negative_strata", mode="before")
    @classmethod
    def parse_strata(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "critical negative strata")

    @field_validator("critical_negative_strata")
    @classmethod
    def validate_strata(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value, "critical negative strata")


class HoldoutSelectionPolicy(_FrozenModel):
    policy_version: Literal["lifecycle-holdout-v1"]
    policy_document_sha256: Annotated[str, Field(pattern=_SHA256)]
    dataset_kind: Literal["purposive_stratified_challenge_holdout"]
    natural_prevalence_claimed: Literal[False]
    max_cases_per_source_family: Annotated[int, Field(ge=1)]
    strata: tuple[HoldoutStratumPolicy, ...]
    quality_gates: HoldoutQualityGates

    @field_validator("strata", mode="before")
    @classmethod
    def parse_strata(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "selection policy strata")

    @model_validator(mode="after")
    def validate_strata(self) -> HoldoutSelectionPolicy:
        ids = [item.stratum_id for item in self.strata]
        if ids != sorted(set(ids)) or not ids:
            raise ValueError("selection policy strata must be sorted, unique, and non-empty")
        unknown = sorted(set(self.quality_gates.critical_negative_strata) - set(ids))
        if unknown:
            raise ValueError(f"critical negative strata are not sampled: {unknown!r}")
        return self


class SelectorAttestation(_FrozenModel):
    selector_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    selector_role: Literal["independent_dataset_custodian"]
    candidate_design_participant: Literal[False]
    candidate_output_seen: Literal[False]
    candidate_configuration_seen: Literal[False]
    attested_on: Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
    process_note: Annotated[str, Field(min_length=1)]

    @field_validator("process_note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        return _single_line(value, "selector process note")


class HoldoutSource(_FrozenModel):
    alias: Annotated[str, Field(pattern=r"^lhs[0-9]{3}$")]
    path: str
    content_sha256: Annotated[str, Field(pattern=_SHA256)]
    line_count: Annotated[int, Field(ge=1)]
    app_scope: str
    source_family_id: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = _relative_path(value, "holdout source path")
        if not value.endswith((".ets", ".ts")):
            raise ValueError("holdout source must use .ets or .ts")
        if "/src/main/ets/" not in f"/{value}/":
            raise ValueError("holdout source must be production src/main/ets code")
        if "DocsSample" in value or "/ohosTest/" in f"/{value}/":
            raise ValueError("holdout source cannot use DocsSample or ohosTest code")
        return value

    @field_validator("app_scope", "source_family_id")
    @classmethod
    def validate_scope_path(cls, value: str, info: object) -> str:
        return _relative_path(value, f"holdout source {getattr(info, 'field_name', 'scope')}")

    @model_validator(mode="after")
    def validate_scope(self) -> HoldoutSource:
        if self.source_family_id != self.app_scope:
            raise ValueError("holdout source family must equal its verified app scope")
        if self.source_family_id != derive_source_family_id(self.path):
            raise ValueError("holdout source family does not match its path-derived app scope")
        if not PurePosixPath(self.path).is_relative_to(PurePosixPath(self.app_scope)):
            raise ValueError("holdout source must stay below its app scope")
        return self


class HoldoutSelectionCase(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    source_alias: Annotated[str, Field(pattern=r"^lhs[0-9]{3}$")]
    changed_line: Annotated[int, Field(ge=1)]
    review_span: SourceSpan
    review_span_sha256: Annotated[str, Field(pattern=_SHA256)]
    normalized_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    stratum_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    selection_rank: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_line(self) -> HoldoutSelectionCase:
        if not self.review_span.start_line <= self.changed_line <= self.review_span.end_line:
            raise ValueError("holdout changed_line must be inside review_span")
        return self


class LifecycleHoldoutSelection(_FrozenModel):
    schema_version: Literal["lifecycle-holdout-selection-v1"]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    suite_id: Literal["lifecycle-owner-role-blind-holdout-v1"]
    dataset_role: Literal["independent_blind_holdout"]
    repository: HoldoutRepository
    candidate_freeze: CandidateFreeze
    development_exclusions: DevelopmentExclusions
    selection_policy: HoldoutSelectionPolicy
    selector_attestation: SelectorAttestation
    sources: tuple[HoldoutSource, ...]
    cases: tuple[HoldoutSelectionCase, ...]

    @field_validator("sources", "cases", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"holdout selection {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_selection(self) -> LifecycleHoldoutSelection:
        aliases = [item.alias for item in self.sources]
        paths = [item.path for item in self.sources]
        hashes = [item.content_sha256 for item in self.sources]
        case_ids = [item.case_id for item in self.cases]
        ranks = [item.selection_rank for item in self.cases]
        if aliases != sorted(set(aliases)) or not aliases:
            raise ValueError("holdout sources must be sorted by unique alias")
        if len(paths) != len(set(paths)) or len(hashes) != len(set(hashes)):
            raise ValueError("holdout source paths and content hashes must be unique")
        if case_ids != sorted(set(case_ids)) or not case_ids:
            raise ValueError("holdout cases must be sorted by unique case_id")
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("holdout selection ranks must be contiguous from one")
        sources = {item.alias: item for item in self.sources}
        referenced = {item.source_alias for item in self.cases}
        if referenced != set(sources):
            raise ValueError("holdout sources must be referenced exactly once or more")
        for case in self.cases:
            source = sources.get(case.source_alias)
            if source is None:
                raise ValueError(f"holdout case references unknown source: {case.source_alias}")
            if case.review_span.end_line > source.line_count:
                raise ValueError(f"holdout case span exceeds source: {case.case_id}")
        body_counts = Counter(item.normalized_body_sha256 for item in self.cases)
        duplicate_bodies = [value for value, count in body_counts.items() if count > 1]
        if duplicate_bodies:
            raise ValueError("holdout cases contain normalized-body duplicates")
        excluded = self.development_exclusions
        if set(paths).intersection(excluded.source_paths):
            raise ValueError("holdout source path overlaps development Truth")
        if set(hashes).intersection(excluded.content_sha256):
            raise ValueError("holdout content hash overlaps development Truth")
        families = [sources[item.source_alias].source_family_id for item in self.cases]
        if any(
            _path_scopes_overlap(family, excluded_family)
            for family in families
            for excluded_family in excluded.source_family_ids
        ):
            raise ValueError("holdout source family overlaps development Truth")
        unique_families = sorted(set(families))
        if any(
            _path_scopes_overlap(left, right)
            for index, left in enumerate(unique_families)
            for right in unique_families[index + 1 :]
        ):
            raise ValueError("holdout source families contain nested app scopes")
        family_counts = Counter(families)
        if max(family_counts.values()) > self.selection_policy.max_cases_per_source_family:
            raise ValueError("holdout source family exceeds the frozen per-family cap")
        strata = {item.stratum_id: item for item in self.selection_policy.strata}
        actual_strata = Counter(item.stratum_id for item in self.cases)
        expected_strata = {key: item.selected_case_count for key, item in strata.items()}
        if dict(sorted(actual_strata.items())) != expected_strata:
            raise ValueError("holdout case strata do not match the frozen selection policy")
        gates = self.selection_policy.quality_gates
        if len(self.cases) < gates.minimum_case_count:
            raise ValueError("holdout selection is smaller than its frozen minimum case count")
        if len(set(families)) < gates.minimum_source_families:
            raise ValueError("holdout selection is smaller than its frozen family minimum")
        expected_id = canonical_hash(
            "lifecycle-holdout-selection",
            _identity_payload(self, "selection_id"),
        )
        if self.selection_id != expected_id:
            raise ValueError("holdout selection_id does not match its fields")
        return self


class TagContractSnapshot(_FrozenModel):
    version: Literal["lifecycle-exact-tag-contract-v1"]
    content_sha256: Annotated[str, Field(pattern=_SHA256)]
    text: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_hash(self) -> TagContractSnapshot:
        if self.content_sha256 != bytes_hash(self.text.encode("utf-8")):
            raise ValueError("Tag contract hash does not match its text")
        return self


class ReviewPolicySnapshot(_FrozenModel):
    version: Literal["lifecycle-blind-review-policy-v1"]
    content_sha256: Annotated[str, Field(pattern=_SHA256)]
    text: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_hash(self) -> ReviewPolicySnapshot:
        if self.content_sha256 != bytes_hash(self.text.encode("utf-8")):
            raise ValueError("review policy hash does not match its text")
        return self


class BlindedReviewCase(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    source_alias: Annotated[str, Field(pattern=r"^lhs[0-9]{3}$")]
    source_path: str
    changed_line: Annotated[int, Field(ge=1)]
    review_span: SourceSpan
    exact_text: Annotated[str, Field(min_length=1)]
    exact_text_sha256: Annotated[str, Field(pattern=_SHA256)]

    @field_validator("source_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value, "blinded review source path")

    @model_validator(mode="after")
    def validate_text(self) -> BlindedReviewCase:
        if self.exact_text_sha256 != bytes_hash(self.exact_text.encode("utf-8")):
            raise ValueError("blinded review text hash does not match exact_text")
        expected_lines = self.review_span.end_line - self.review_span.start_line + 1
        if len(self.exact_text.splitlines()) != expected_lines:
            raise ValueError("blinded review text line count does not match review_span")
        return self


class LifecycleHoldoutReviewPacket(_FrozenModel):
    schema_version: Literal["lifecycle-holdout-review-packet-v1"]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    suite_id: Literal["lifecycle-owner-role-blind-holdout-v1"]
    repository_revision: Annotated[str, Field(pattern=_GIT_REVISION)]
    target_tag_id: Literal["has_lifecycle"]
    target_tag_contract: TagContractSnapshot
    review_policy: ReviewPolicySnapshot
    candidate_outputs_included: Literal[False]
    candidate_configuration_included: Literal[False]
    cases: tuple[BlindedReviewCase, ...]

    @field_validator("cases", mode="before")
    @classmethod
    def parse_cases(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "blinded review packet cases")

    @model_validator(mode="after")
    def validate_packet(self) -> LifecycleHoldoutReviewPacket:
        case_ids = [item.case_id for item in self.cases]
        if case_ids != sorted(set(case_ids)) or not case_ids:
            raise ValueError("review packet cases must be sorted and unique")
        expected_id = canonical_hash(
            "lifecycle-holdout-review-packet",
            _identity_payload(self, "packet_id"),
        )
        if self.packet_id != expected_id:
            raise ValueError("review packet_id does not match its fields")
        return self


class ReviewerIdentity(_FrozenModel):
    reviewer_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    reviewer_kind: Literal["human"]
    reviewer_role: Literal["arkts_domain_reviewer"]
    affiliation: Annotated[str, Field(min_length=1)]
    candidate_design_participant: Literal[False]
    selection_participant: Literal[False]

    @field_validator("affiliation")
    @classmethod
    def validate_affiliation(cls, value: str) -> str:
        return _single_line(value, "reviewer affiliation")


class ReviewerBlindingAttestation(_FrozenModel):
    candidate_output_seen: Literal[False]
    candidate_configuration_seen: Literal[False]
    review_completed_before_unblinding: Literal[True]
    attested_at: Annotated[
        str,
        Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"),
    ]


SemanticLabel = Literal["positive", "negative", "needs_taxonomy_decision"]


class LifecycleReviewDecision(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    semantic_label: SemanticLabel
    expected_unit_kind: str
    expected_unit_symbol: Annotated[str, Field(min_length=1)]
    expected_source_span: SourceSpan
    evidence_lines: tuple[Annotated[int, Field(ge=1)], ...]
    rationale: Annotated[str, Field(min_length=1)]

    @field_validator("evidence_lines", mode="before")
    @classmethod
    def parse_evidence_lines(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "review decision evidence_lines")

    @field_validator("expected_unit_kind")
    @classmethod
    def validate_unit_kind(cls, value: str) -> str:
        if value not in REVIEW_UNIT_KINDS:
            raise ValueError(f"unsupported expected_unit_kind: {value}")
        return value

    @field_validator("expected_unit_symbol", "rationale")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        return _single_line(value, f"review decision {getattr(info, 'field_name', 'text')}")

    @model_validator(mode="after")
    def validate_evidence(self) -> LifecycleReviewDecision:
        if list(self.evidence_lines) != sorted(set(self.evidence_lines)) or not self.evidence_lines:
            raise ValueError("review decision evidence_lines must be sorted, unique, and non-empty")
        if any(
            line < self.expected_source_span.start_line or line > self.expected_source_span.end_line
            for line in self.evidence_lines
        ):
            raise ValueError("review decision evidence must be inside expected source span")
        return self


class LifecycleHoldoutReviewReceipt(_FrozenModel):
    schema_version: Literal["lifecycle-holdout-review-receipt-v1"]
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]
    round_id: Annotated[str, Field(pattern=r"^round-[a-z0-9]+(?:-[a-z0-9]+)*$")]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    target_tag_contract_sha256: Annotated[str, Field(pattern=_SHA256)]
    review_policy_sha256: Annotated[str, Field(pattern=_SHA256)]
    reviewer: ReviewerIdentity
    blinding: ReviewerBlindingAttestation
    recorded_at: Annotated[
        str,
        Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"),
    ]
    decisions: tuple[LifecycleReviewDecision, ...]

    @field_validator("decisions", mode="before")
    @classmethod
    def parse_decisions(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "review receipt decisions")

    @model_validator(mode="after")
    def validate_receipt(self) -> LifecycleHoldoutReviewReceipt:
        case_ids = [item.case_id for item in self.decisions]
        if case_ids != sorted(set(case_ids)) or not case_ids:
            raise ValueError("review receipt decisions must be sorted and unique")
        expected_id = canonical_hash(
            "lifecycle-holdout-review-receipt",
            _identity_payload(self, "receipt_id"),
        )
        if self.receipt_id != expected_id:
            raise ValueError("review receipt_id does not match its fields")
        return self


class ConsensusVote(_FrozenModel):
    round_id: Annotated[str, Field(pattern=r"^round-[a-z0-9]+(?:-[a-z0-9]+)*$")]
    reviewer_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    semantic_label: SemanticLabel
    expected_unit_kind: str
    expected_unit_symbol: Annotated[str, Field(min_length=1)]
    expected_source_span: SourceSpan
    evidence_lines: tuple[Annotated[int, Field(ge=1)], ...]

    @field_validator("evidence_lines", mode="before")
    @classmethod
    def parse_evidence_lines(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "consensus vote evidence lines")


class LifecycleConsensusCase(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    status: Literal["agreed", "unresolved"]
    votes: tuple[ConsensusVote, ...]
    semantic_label: SemanticLabel | None = None
    expected_unit_kind: str | None = None
    expected_unit_symbol: str | None = None
    expected_source_span: SourceSpan | None = None
    evidence_lines: tuple[int, ...] = ()

    @field_validator("votes", "evidence_lines", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"consensus case {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_consensus_case(self) -> LifecycleConsensusCase:
        rounds = [item.round_id for item in self.votes]
        reviewers = [item.reviewer_id for item in self.votes]
        if len(self.votes) != 2 or rounds != sorted(set(rounds)):
            raise ValueError("consensus case requires two round-sorted unique votes")
        if len(set(reviewers)) != 2:
            raise ValueError("consensus case votes must use distinct reviewers")
        comparable = [
            (
                vote.semantic_label,
                vote.expected_unit_kind,
                vote.expected_unit_symbol,
                vote.expected_source_span,
            )
            for vote in self.votes
        ]
        agreed = comparable[0] == comparable[1]
        if self.status != ("agreed" if agreed else "unresolved"):
            raise ValueError("consensus case status does not match its votes")
        if agreed:
            vote = self.votes[0]
            expected_evidence = tuple(
                sorted(
                    set(
                        (
                            *self.votes[0].evidence_lines,
                            *self.votes[1].evidence_lines,
                        )
                    )
                )
            )
            expected = (
                vote.semantic_label,
                vote.expected_unit_kind,
                vote.expected_unit_symbol,
                vote.expected_source_span,
                expected_evidence,
            )
            actual = (
                self.semantic_label,
                self.expected_unit_kind,
                self.expected_unit_symbol,
                self.expected_source_span,
                self.evidence_lines,
            )
            if actual != expected:
                raise ValueError("agreed consensus fields do not match votes")
        elif (
            any(
                value is not None
                for value in (
                    self.semantic_label,
                    self.expected_unit_kind,
                    self.expected_unit_symbol,
                    self.expected_source_span,
                )
            )
            or self.evidence_lines
        ):
            raise ValueError("unresolved consensus case cannot publish a Truth label")
        return self


class ReceiptReference(_FrozenModel):
    round_id: Annotated[str, Field(pattern=r"^round-[a-z0-9]+(?:-[a-z0-9]+)*$")]
    reviewer_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")]
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]


class LifecycleHoldoutConsensus(_FrozenModel):
    schema_version: Literal["lifecycle-holdout-consensus-v1"]
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID)]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    receipt_references: tuple[ReceiptReference, ...]
    cases: tuple[LifecycleConsensusCase, ...]
    consensus_status: Literal["complete", "unresolved"]
    release_ready: bool
    release_blockers: tuple[str, ...]

    @field_validator("receipt_references", "cases", "release_blockers", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"consensus {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_consensus(self) -> LifecycleHoldoutConsensus:
        receipt_keys = [
            (item.round_id, item.reviewer_id, item.receipt_id) for item in self.receipt_references
        ]
        if len(receipt_keys) != 2 or receipt_keys != sorted(set(receipt_keys)):
            raise ValueError("consensus requires two sorted unique receipt references")
        if len({item.reviewer_id for item in self.receipt_references}) != 2:
            raise ValueError("consensus receipt references must use distinct reviewers")
        case_ids = [item.case_id for item in self.cases]
        if case_ids != sorted(set(case_ids)) or not case_ids:
            raise ValueError("consensus cases must be sorted and unique")
        blockers: list[str] = []
        if any(item.status == "unresolved" for item in self.cases):
            blockers.append("unresolved_review_disagreement")
        if any(item.semantic_label == "needs_taxonomy_decision" for item in self.cases):
            blockers.append("taxonomy_decision_required")
        expected_blockers = tuple(sorted(blockers))
        if self.release_blockers != expected_blockers:
            raise ValueError("consensus release blockers do not match case results")
        expected_status = "complete" if not expected_blockers else "unresolved"
        if self.consensus_status != expected_status or self.release_ready != (
            not expected_blockers
        ):
            raise ValueError("consensus readiness does not match release blockers")
        expected_id = canonical_hash(
            "lifecycle-holdout-consensus",
            _identity_payload(self, "consensus_id"),
        )
        if self.consensus_id != expected_id:
            raise ValueError("holdout consensus_id does not match its fields")
        return self


@dataclass(frozen=True)
class VerifiedHoldoutCheckout:
    root: Path
    source_text_by_alias: Mapping[str, str]


def _parse_json_model[TModel: BaseModel](
    raw: bytes,
    model: type[TModel],
    context: str,
) -> TModel:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        # JSON mode preserves strict scalar validation while allowing Pydantic to
        # materialize nested dataclasses such as SourceSpan from their JSON shape.
        return model.model_validate_json(canonical_json(payload))
    except (
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        _DuplicateKeyError,
    ) as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


def _load_json_model[TModel: BaseModel](
    path: str | Path,
    model: type[TModel],
    context: str,
) -> TModel:
    artifact = Path(path)
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError(f"{context} must be a regular non-symlink file: {artifact}")
    try:
        raw = artifact.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {context} {artifact}: {exc}") from exc
    return _parse_json_model(raw, model, f"{context} {artifact}")


def load_lifecycle_holdout_selection(path: str | Path) -> LifecycleHoldoutSelection:
    return _load_json_model(path, LifecycleHoldoutSelection, "lifecycle holdout selection")


def load_lifecycle_holdout_review_packet(path: str | Path) -> LifecycleHoldoutReviewPacket:
    return _load_json_model(path, LifecycleHoldoutReviewPacket, "lifecycle review packet")


def load_lifecycle_holdout_review_receipt(path: str | Path) -> LifecycleHoldoutReviewReceipt:
    return _load_json_model(path, LifecycleHoldoutReviewReceipt, "lifecycle review receipt")


def load_lifecycle_holdout_consensus(path: str | Path) -> LifecycleHoldoutConsensus:
    return _load_json_model(path, LifecycleHoldoutConsensus, "lifecycle holdout consensus")


def parse_lifecycle_holdout_selection(raw: bytes) -> LifecycleHoldoutSelection:
    return _parse_json_model(raw, LifecycleHoldoutSelection, "sealed lifecycle holdout selection")


def parse_lifecycle_holdout_review_packet(raw: bytes) -> LifecycleHoldoutReviewPacket:
    return _parse_json_model(raw, LifecycleHoldoutReviewPacket, "sealed lifecycle review packet")


def parse_lifecycle_holdout_review_receipt(raw: bytes) -> LifecycleHoldoutReviewReceipt:
    return _parse_json_model(raw, LifecycleHoldoutReviewReceipt, "sealed lifecycle review receipt")


def parse_lifecycle_holdout_consensus(raw: bytes) -> LifecycleHoldoutConsensus:
    return _parse_json_model(raw, LifecycleHoldoutConsensus, "sealed lifecycle holdout consensus")


def seal_lifecycle_holdout_selection_payload(
    payload: Mapping[str, object],
) -> LifecycleHoldoutSelection:
    sealed = selection_payload_with_id(payload)
    return LifecycleHoldoutSelection.model_validate_json(canonical_json(sealed))


def verify_selection_development_exclusions(
    selection: LifecycleHoldoutSelection,
    development_truth: object,
) -> None:
    from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
        TagRetrievalTruthSuite,
        tag_retrieval_truth_fingerprint,
    )

    if not isinstance(development_truth, TagRetrievalTruthSuite):
        raise ValueError("development Truth must use TagRetrievalTruthSuite")
    exclusions = selection.development_exclusions
    if exclusions.truth_suite_fingerprint != tag_retrieval_truth_fingerprint(development_truth):
        raise ValueError("development Truth fingerprint does not match selection exclusions")
    expected_families = tuple(
        sorted({source.source_family_id for source in development_truth.sources})
    )
    expected_paths = tuple(sorted(source.path for source in development_truth.sources))
    expected_hashes = tuple(sorted(source.content_sha256 for source in development_truth.sources))
    if exclusions.source_family_ids != expected_families:
        raise ValueError("development exclusion source-family set is incomplete or drifted")
    if exclusions.source_paths != expected_paths:
        raise ValueError("development exclusion source-path set is incomplete or drifted")
    if exclusions.content_sha256 != expected_hashes:
        raise ValueError("development exclusion content-hash set is incomplete or drifted")


def _run_git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot inspect holdout source checkout: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValueError(f"cannot inspect holdout source checkout: {detail}")
    return completed.stdout.strip()


def _safe_file(root: Path, relative_path: str, context: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"missing {context}: {relative_path}") from exc
    if not resolved.is_relative_to(root) or candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"unsafe {context}: {relative_path}")
    return candidate


def _read_canonical_text(root: Path, relative_path: str, context: str) -> str:
    path = _safe_file(root, relative_path, context)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read UTF-8 {context}: {relative_path}") from exc
    if not text:
        raise ValueError(f"{context} cannot be empty")
    return text


def load_canonical_lifecycle_review_material(
    repository_root: str | Path,
) -> tuple[TagContractSnapshot, ReviewPolicySnapshot]:
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"candidate repository root does not exist: {repository_root}") from exc
    contract_text = _read_canonical_text(root, LIFECYCLE_TAG_CONTRACT_PATH, "Tag contract")
    policy_text = _read_canonical_text(root, LIFECYCLE_REVIEW_POLICY_PATH, "review policy")
    return (
        TagContractSnapshot(
            version="lifecycle-exact-tag-contract-v1",
            content_sha256=bytes_hash(contract_text.encode("utf-8")),
            text=contract_text,
        ),
        ReviewPolicySnapshot(
            version="lifecycle-blind-review-policy-v1",
            content_sha256=bytes_hash(policy_text.encode("utf-8")),
            text=policy_text,
        ),
    )


def verify_approved_selection_policy(
    selection: LifecycleHoldoutSelection,
    repository_root: str | Path,
) -> None:
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"candidate repository root does not exist: {repository_root}") from exc
    policy_text = _read_canonical_text(
        root,
        LIFECYCLE_SELECTION_POLICY_PATH,
        "selection policy",
    )
    policy = selection.selection_policy
    if policy.policy_document_sha256 != bytes_hash(policy_text.encode("utf-8")):
        raise ValueError("selection policy document differs from the approved repository policy")
    gates = policy.quality_gates
    fixed_count_gates = {
        "minimum_case_count": 32,
        "minimum_positive_cases": 16,
        "minimum_negative_cases": 16,
        "minimum_source_families": 32,
    }
    actual_count_gates = {field: getattr(gates, field) for field in fixed_count_gates}
    if actual_count_gates != fixed_count_gates:
        raise ValueError("selection count gates differ from the approved fixed 32-case policy")
    weaker = (
        gates.minimum_precision < 0.95
        or gates.minimum_recall < 0.95
        or gates.minimum_precision_wilson_95 < 0.80
        or gates.minimum_recall_wilson_95 < 0.80
        or gates.maximum_false_positives != 0
        or gates.maximum_false_negatives != 0
        or gates.maximum_critical_false_positives != 0
        or policy.max_cases_per_source_family != 1
        or not gates.critical_negative_strata
    )
    if weaker:
        raise ValueError("selection quality gates are weaker than the approved policy")
    selected_by_stratum = {item.stratum_id: item.selected_case_count for item in policy.strata}
    if set(selected_by_stratum) != set(LIFECYCLE_REQUIRED_STRATUM_MINIMUMS):
        raise ValueError("selection strata differ from the approved lifecycle challenge taxonomy")
    if selected_by_stratum != LIFECYCLE_REQUIRED_STRATUM_MINIMUMS:
        raise ValueError("selection stratum counts differ from the approved fixed 32-case policy")
    if gates.critical_negative_strata != LIFECYCLE_CRITICAL_NEGATIVE_STRATA:
        raise ValueError("critical-negative strata differ from the approved lifecycle taxonomy")


def verify_lifecycle_holdout_checkout(
    selection: LifecycleHoldoutSelection,
    checkout_root: str | Path,
) -> VerifiedHoldoutCheckout:
    try:
        root = Path(checkout_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"holdout source checkout does not exist: {checkout_root}") from exc
    if not root.is_dir():
        raise ValueError("holdout source checkout root must be a directory")
    top = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != root:
        raise ValueError("holdout checkout root must be the Git top level")
    if _run_git(root, "rev-parse", "HEAD") != selection.repository.revision:
        raise ValueError("holdout source checkout revision mismatch")
    try:
        remote = _run_git(root, "remote", "get-url", "origin")
    except ValueError:
        remote = ""
    if remote != selection.repository.remote:
        raise ValueError("holdout source checkout remote mismatch")
    if _run_git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("holdout source checkout must be clean")

    texts: dict[str, str] = {}
    for source in selection.sources:
        path = _safe_file(root, source.path, "holdout source")
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read UTF-8 holdout source: {source.path}") from exc
        committed = _git_file_bytes(
            root,
            selection.repository.revision,
            source.path,
            "holdout source revision",
        )
        if raw != committed:
            raise ValueError(f"holdout source differs from its pinned Git revision: {source.path}")
        if bytes_hash(raw) != source.content_sha256:
            raise ValueError(f"holdout source hash drift: {source.path}")
        if len(raw.splitlines()) != source.line_count:
            raise ValueError(f"holdout source line-count drift: {source.path}")
        texts[source.alias] = text
    for case in selection.cases:
        exact_text = _span_text(texts[case.source_alias], case.review_span)
        if bytes_hash(exact_text.encode("utf-8")) != case.review_span_sha256:
            raise ValueError(f"holdout review-span hash drift: {case.case_id}")
        if normalized_source_body_hash(exact_text) != case.normalized_body_sha256:
            raise ValueError(f"holdout normalized-body hash drift: {case.case_id}")
    return VerifiedHoldoutCheckout(root=root, source_text_by_alias=texts)


def verify_candidate_corpus_independence(
    selection: LifecycleHoldoutSelection,
    checkout_root: str | Path,
) -> None:
    try:
        root = Path(checkout_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"holdout source checkout does not exist: {checkout_root}") from exc
    exposure_revision = selection.candidate_freeze.candidate_corpus_exposure_revision
    if _run_git(root, "rev-parse", exposure_revision) != exposure_revision:
        raise ValueError("candidate corpus exposure revision is unavailable in source checkout")
    if exposure_revision == selection.repository.revision or not _git_is_ancestor(
        root,
        exposure_revision,
        selection.repository.revision,
    ):
        raise ValueError(
            "holdout source revision must be a later descendant of the candidate corpus "
            "exposure revision"
        )
    try:
        raw_tree = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "-z", exposure_revision],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("cannot inspect candidate corpus exposure tree") from exc
    exposed_paths: set[str] = set()
    exposed_blob_ids: set[str] = set()
    exposed_families: set[str] = set()
    for entry in raw_tree.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            _mode, kind, object_id = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise ValueError("candidate corpus exposure tree contains an invalid entry") from exc
        if kind != "blob":
            continue
        exposed_paths.add(path)
        exposed_blob_ids.add(object_id)
        if path.endswith((".ets", ".ts")) and "/src/main/ets/" in f"/{path}/":
            try:
                exposed_families.add(derive_source_family_id(path))
            except ValueError:
                pass
    overlaps: list[str] = []
    for source in selection.sources:
        if source.path in exposed_paths:
            overlaps.append(f"path:{source.path}")
        if any(
            _path_scopes_overlap(source.source_family_id, exposed) for exposed in exposed_families
        ):
            overlaps.append(f"family:{source.source_family_id}")
        current_blob = _run_git(
            root,
            "rev-parse",
            f"{selection.repository.revision}:{source.path}",
        )
        if current_blob in exposed_blob_ids:
            overlaps.append(f"content:{source.alias}")
    if overlaps:
        raise ValueError(
            "holdout overlaps the corpus inspected during candidate design: "
            f"{sorted(set(overlaps))!r}"
        )


def verify_candidate_runtime_bundle(
    freeze: CandidateFreeze,
    repository_root: str | Path,
) -> None:
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"candidate repository root does not exist: {repository_root}") from exc
    top = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != root:
        raise ValueError("candidate repository root must be the Git top level")
    expected_paths = _candidate_runtime_paths(root, freeze.candidate_commit)
    actual_paths = tuple(item.path for item in freeze.runtime_files)
    if actual_paths != expected_paths:
        raise ValueError(
            "candidate runtime snapshot does not cover the complete frozen source tree"
        )
    _verify_git_snapshots(
        root,
        freeze.candidate_commit,
        freeze.runtime_files,
        context="candidate runtime",
    )
    if freeze.runtime_environment != _current_runtime_environment(root):
        raise ValueError("candidate runtime environment drift")


def verify_evaluation_harness_bundle(
    freeze: CandidateFreeze,
    repository_root: str | Path,
) -> None:
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"candidate repository root does not exist: {repository_root}") from exc
    top = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != root:
        raise ValueError("candidate repository root must be the Git top level")
    if not _git_is_ancestor(root, freeze.candidate_commit, freeze.evaluation_harness_commit):
        raise ValueError("evaluation harness was frozen before the candidate")
    if not _git_is_ancestor(root, freeze.evaluation_harness_commit, "HEAD"):
        raise ValueError("evaluation harness commit is not an ancestor of HEAD")
    _verify_git_snapshots(
        root,
        freeze.evaluation_harness_commit,
        freeze.evaluation_harness_files,
        context="evaluation harness",
    )


def _git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot inspect holdout Git ancestry: {exc}") from exc
    return completed.returncode == 0


def _git_file_bytes(root: Path, revision: str, path: str, context: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "show", f"{revision}:{path}"],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"{context} file is unavailable at commit: {path}") from exc


def _candidate_runtime_paths(root: Path, commit: str) -> tuple[str, ...]:
    tracked = _run_git(
        root,
        "ls-tree",
        "-r",
        "--name-only",
        commit,
        "--",
        "src/arkts_code_reviewer",
    ).splitlines()
    paths = {path for path in tracked if path.endswith(".py")}
    paths.update(
        {
            "config/dimensions.yaml",
            "config/tags.yaml",
            "sidecars/arkts-parser/.node-version",
            "sidecars/arkts-parser/package-lock.json",
            "sidecars/arkts-parser/package.json",
            "sidecars/arkts-parser/parse_arkts.js",
            "tests/fixtures/feature_routing/tag_config_lifecycle_owner_role_shadow_v1.yaml",
        }
    )
    result = tuple(sorted(paths))
    missing_required = sorted(set(LIFECYCLE_RUNTIME_REQUIRED_PATHS) - set(result))
    if missing_required:
        raise ValueError(f"candidate commit omits critical runtime files: {missing_required!r}")
    return result


def _verify_git_snapshots(
    root: Path,
    revision: str,
    snapshots: Sequence[RuntimeFileSnapshot],
    *,
    context: str,
) -> None:
    for snapshot in snapshots:
        committed = _git_file_bytes(root, revision, snapshot.path, context)
        if bytes_hash(committed) != snapshot.content_sha256:
            raise ValueError(f"{context} snapshot differs from its frozen commit: {snapshot.path}")
        path = _safe_file(root, snapshot.path, "candidate runtime file")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read {context} file: {snapshot.path}") from exc
        if bytes_hash(raw) != snapshot.content_sha256:
            raise ValueError(f"{context} file drift: {snapshot.path}")


def _current_runtime_environment(root: Path) -> RuntimeEnvironment:
    forbidden_environment = [
        name
        for name in ("ARKTS_PARSER_NODE", "ARKTS_PARSER_TIMEOUT", "NODE_OPTIONS")
        if os.environ.get(name)
    ]
    if forbidden_environment:
        raise ValueError(
            "frozen holdout runtime forbids parser environment overrides: "
            f"{forbidden_environment!r}"
        )
    node = shutil.which("node")
    if node is None:
        raise ValueError("Node executable is unavailable for the frozen Parser runtime")
    try:
        node_version = subprocess.run(
            [node, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("cannot inspect frozen Node runtime") from exc
    distributions = (
        "annotated-types",
        "pydantic",
        "pydantic_core",
        "ruamel.yaml",
        "ruamel.yaml.clib",
        "typing-extensions",
        "typing-inspection",
    )
    try:
        packages = tuple(
            sorted(f"{name}=={importlib.metadata.version(name)}" for name in distributions)
        )
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError(f"required Python runtime package is unavailable: {exc}") from exc
    dependencies = root / "sidecars/arkts-parser/node_modules"
    try:
        node_bytes = Path(node).resolve(strict=True).read_bytes()
    except OSError as exc:
        raise ValueError("cannot hash frozen Node executable") from exc
    return RuntimeEnvironment(
        python_version=platform.python_version(),
        python_packages=packages,
        platform_system=platform.system(),
        platform_machine=platform.machine(),
        node_version=node_version,
        node_executable_sha256=bytes_hash(node_bytes),
        sidecar_dependencies_fingerprint=_directory_tree_fingerprint(dependencies),
    )


def build_lifecycle_owner_role_candidate_freeze(
    repository_root: str | Path,
) -> CandidateFreeze:
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"candidate repository root does not exist: {repository_root}") from exc
    top = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != root:
        raise ValueError("candidate repository root must be the Git top level")
    commit = _run_git(root, "rev-parse", LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT)
    if commit != LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT:
        raise ValueError("candidate freeze commit identity mismatch")
    if not _git_is_ancestor(root, commit, "HEAD"):
        raise ValueError("candidate freeze commit is not an ancestor of HEAD")
    files = tuple(
        RuntimeFileSnapshot(
            path=path,
            content_sha256=bytes_hash(_git_file_bytes(root, commit, path, "candidate freeze")),
        )
        for path in _candidate_runtime_paths(root, commit)
    )
    harness_commit = _run_git(root, "rev-parse", "HEAD")
    harness_files = tuple(
        RuntimeFileSnapshot(
            path=path,
            content_sha256=bytes_hash(
                _git_file_bytes(root, harness_commit, path, "evaluation harness freeze")
            ),
        )
        for path in LIFECYCLE_EVALUATION_HARNESS_PATHS
    )
    freeze = CandidateFreeze(
        candidate_commit="9b7a828449cbe760ce9374d222f75c48b6f5c852",
        tags_config_schema_version="tag-config-v4",
        tags_config_version="tags-lifecycle-owner-role-shadow-v1",
        feature_routing_schema_version="feature-routing-v3",
        feature_config_fingerprint=(
            "feature-config:sha256:844418e3d7938c816fd3b64b62cdae3d1753d286d50a6a103406838ed6db01e7"
        ),
        candidate_corpus_exposure_revision=("8255a2987f70317cc3a2a4d46044c6b55f092bb3"),
        candidate_corpus_exposure_scope="entire_tracked_repository",
        runtime_files=files,
        runtime_bundle_fingerprint=runtime_bundle_fingerprint(files),
        runtime_environment=_current_runtime_environment(root),
        evaluation_harness_commit=harness_commit,
        evaluation_harness_files=harness_files,
        evaluation_harness_fingerprint=evaluation_harness_fingerprint(harness_files),
    )
    verify_candidate_runtime_bundle(freeze, root)
    verify_evaluation_harness_bundle(freeze, root)
    return freeze


def _span_text(text: str, span: SourceSpan) -> str:
    lines = text.splitlines()
    selected = lines[span.start_line - 1 : span.end_line]
    if len(selected) != span.end_line - span.start_line + 1:
        raise ValueError("review span exceeds source text")
    return "\n".join(selected)


def normalized_source_body_hash(text: str) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        raise ValueError("normalized holdout source body cannot be empty")
    return bytes_hash(normalized.encode("utf-8"))


def build_lifecycle_holdout_review_packet(
    selection: LifecycleHoldoutSelection,
    checkout: VerifiedHoldoutCheckout,
    *,
    target_tag_contract: TagContractSnapshot,
    review_policy: ReviewPolicySnapshot,
) -> LifecycleHoldoutReviewPacket:
    sources = {item.alias: item for item in selection.sources}
    cases: list[BlindedReviewCase] = []
    for item in selection.cases:
        source = sources[item.source_alias]
        text = checkout.source_text_by_alias.get(item.source_alias)
        if text is None:
            raise ValueError(f"verified checkout is missing source alias: {item.source_alias}")
        exact_text = _span_text(text, item.review_span)
        exact_hash = bytes_hash(exact_text.encode("utf-8"))
        if exact_hash != item.review_span_sha256:
            raise ValueError(f"holdout review-span hash drift: {item.case_id}")
        cases.append(
            BlindedReviewCase(
                case_id=item.case_id,
                source_alias=item.source_alias,
                source_path=source.path,
                changed_line=item.changed_line,
                review_span=item.review_span,
                exact_text=exact_text,
                exact_text_sha256=exact_hash,
            )
        )
    payload: dict[str, object] = {
        "schema_version": LIFECYCLE_HOLDOUT_REVIEW_PACKET_SCHEMA_VERSION,
        "packet_id": "lifecycle-holdout-review-packet:sha256:" + "0" * 64,
        "selection_id": selection.selection_id,
        "suite_id": selection.suite_id,
        "repository_revision": selection.repository.revision,
        "target_tag_id": "has_lifecycle",
        "target_tag_contract": target_tag_contract.model_dump(mode="json"),
        "review_policy": review_policy.model_dump(mode="json"),
        "candidate_outputs_included": False,
        "candidate_configuration_included": False,
        "cases": [case.model_dump(mode="json") for case in cases],
    }
    payload["packet_id"] = canonical_hash(
        "lifecycle-holdout-review-packet",
        {key: value for key, value in payload.items() if key != "packet_id"},
    )
    return LifecycleHoldoutReviewPacket.model_validate_json(canonical_json(payload))


def validate_lifecycle_holdout_review_receipt(
    receipt: LifecycleHoldoutReviewReceipt,
    packet: LifecycleHoldoutReviewPacket,
) -> None:
    # Re-validate even model instances so unchecked model_copy(update=...) values
    # cannot bypass the artifact's self-hash or closed schema at API boundaries.
    LifecycleHoldoutReviewPacket.model_validate_json(canonical_json(packet.model_dump(mode="json")))
    LifecycleHoldoutReviewReceipt.model_validate_json(
        canonical_json(receipt.model_dump(mode="json"))
    )
    expected_identity = (
        packet.selection_id,
        packet.packet_id,
        packet.target_tag_contract.content_sha256,
        packet.review_policy.content_sha256,
    )
    actual_identity = (
        receipt.selection_id,
        receipt.packet_id,
        receipt.target_tag_contract_sha256,
        receipt.review_policy_sha256,
    )
    if actual_identity != expected_identity:
        raise ValueError("review receipt identity does not match the blinded packet")
    packet_cases = {item.case_id: item for item in packet.cases}
    decision_ids = {item.case_id for item in receipt.decisions}
    if decision_ids != set(packet_cases):
        missing = sorted(set(packet_cases) - decision_ids)
        extra = sorted(decision_ids - set(packet_cases))
        raise ValueError(f"review receipt case coverage mismatch: missing={missing}, extra={extra}")
    for decision in receipt.decisions:
        packet_case = packet_cases[decision.case_id]
        if (
            decision.expected_source_span.start_line < packet_case.review_span.start_line
            or decision.expected_source_span.end_line > packet_case.review_span.end_line
        ):
            raise ValueError(f"review decision span escapes blinded packet: {decision.case_id}")


def _decision_vote(
    receipt: LifecycleHoldoutReviewReceipt,
    decision: LifecycleReviewDecision,
) -> ConsensusVote:
    return ConsensusVote(
        round_id=receipt.round_id,
        reviewer_id=receipt.reviewer.reviewer_id,
        semantic_label=decision.semantic_label,
        expected_unit_kind=decision.expected_unit_kind,
        expected_unit_symbol=decision.expected_unit_symbol,
        expected_source_span=decision.expected_source_span,
        evidence_lines=decision.evidence_lines,
    )


def build_lifecycle_holdout_consensus(
    packet: LifecycleHoldoutReviewPacket,
    receipts: Sequence[LifecycleHoldoutReviewReceipt],
) -> LifecycleHoldoutConsensus:
    if len(receipts) != 2:
        raise ValueError("lifecycle holdout consensus requires exactly two receipts")
    ordered = tuple(sorted(receipts, key=lambda item: item.round_id))
    if len({item.round_id for item in ordered}) != 2:
        raise ValueError("lifecycle holdout receipts must use distinct round IDs")
    if len({item.reviewer.reviewer_id for item in ordered}) != 2:
        raise ValueError("lifecycle holdout receipts must use distinct reviewers")
    if len({item.receipt_id for item in ordered}) != 2:
        raise ValueError("lifecycle holdout receipts must use distinct receipt IDs")
    for receipt in ordered:
        validate_lifecycle_holdout_review_receipt(receipt, packet)
    decisions = [{item.case_id: item for item in receipt.decisions} for receipt in ordered]
    cases: list[LifecycleConsensusCase] = []
    for packet_case in packet.cases:
        votes = tuple(
            _decision_vote(receipt, by_case[packet_case.case_id])
            for receipt, by_case in zip(ordered, decisions, strict=True)
        )
        comparable = [
            (
                vote.semantic_label,
                vote.expected_unit_kind,
                vote.expected_unit_symbol,
                vote.expected_source_span,
            )
            for vote in votes
        ]
        if comparable[0] == comparable[1]:
            vote = votes[0]
            cases.append(
                LifecycleConsensusCase(
                    case_id=packet_case.case_id,
                    status="agreed",
                    votes=votes,
                    semantic_label=vote.semantic_label,
                    expected_unit_kind=vote.expected_unit_kind,
                    expected_unit_symbol=vote.expected_unit_symbol,
                    expected_source_span=vote.expected_source_span,
                    evidence_lines=tuple(
                        sorted(set((*votes[0].evidence_lines, *votes[1].evidence_lines)))
                    ),
                )
            )
        else:
            cases.append(
                LifecycleConsensusCase(
                    case_id=packet_case.case_id,
                    status="unresolved",
                    votes=votes,
                )
            )
    blockers: list[str] = []
    if any(item.status == "unresolved" for item in cases):
        blockers.append("unresolved_review_disagreement")
    if any(item.semantic_label == "needs_taxonomy_decision" for item in cases):
        blockers.append("taxonomy_decision_required")
    blockers = sorted(blockers)
    payload: dict[str, object] = {
        "schema_version": LIFECYCLE_HOLDOUT_CONSENSUS_SCHEMA_VERSION,
        "consensus_id": "lifecycle-holdout-consensus:sha256:" + "0" * 64,
        "selection_id": packet.selection_id,
        "packet_id": packet.packet_id,
        "receipt_references": [
            {
                "round_id": receipt.round_id,
                "reviewer_id": receipt.reviewer.reviewer_id,
                "receipt_id": receipt.receipt_id,
            }
            for receipt in ordered
        ],
        "cases": [item.model_dump(mode="json") for item in cases],
        "consensus_status": "complete" if not blockers else "unresolved",
        "release_ready": not blockers,
        "release_blockers": blockers,
    }
    payload["consensus_id"] = canonical_hash(
        "lifecycle-holdout-consensus",
        {key: value for key, value in payload.items() if key != "consensus_id"},
    )
    return LifecycleHoldoutConsensus.model_validate_json(canonical_json(payload))


def selection_payload_with_id(payload: Mapping[str, object]) -> dict[str, object]:
    if "selection_id" in payload:
        raise ValueError("unsealed selection payload cannot contain selection_id")
    result = dict(payload)
    result["selection_id"] = canonical_hash("lifecycle-holdout-selection", result)
    return result


def review_receipt_payload_with_id(payload: Mapping[str, object]) -> dict[str, object]:
    if "receipt_id" in payload:
        raise ValueError("unsealed review receipt payload cannot contain receipt_id")
    result = dict(payload)
    result["receipt_id"] = canonical_hash("lifecycle-holdout-review-receipt", result)
    return result


__all__ = [
    "CandidateFreeze",
    "DevelopmentExclusions",
    "HoldoutQualityGates",
    "HoldoutRepository",
    "HoldoutSelectionCase",
    "HoldoutSelectionPolicy",
    "HoldoutSource",
    "HoldoutStratumPolicy",
    "LIFECYCLE_HOLDOUT_CONSENSUS_SCHEMA_VERSION",
    "LIFECYCLE_HOLDOUT_REVIEW_PACKET_SCHEMA_VERSION",
    "LIFECYCLE_HOLDOUT_REVIEW_RECEIPT_SCHEMA_VERSION",
    "LIFECYCLE_HOLDOUT_SELECTION_SCHEMA_VERSION",
    "LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT",
    "LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT",
    "LIFECYCLE_EVALUATION_HARNESS_PATHS",
    "LIFECYCLE_RUNTIME_REQUIRED_PATHS",
    "LIFECYCLE_RUNTIME_PATHS",
    "LifecycleConsensusCase",
    "LifecycleHoldoutConsensus",
    "LifecycleHoldoutReviewPacket",
    "LifecycleHoldoutReviewReceipt",
    "LifecycleHoldoutSelection",
    "LifecycleReviewDecision",
    "ReviewPolicySnapshot",
    "ReviewerBlindingAttestation",
    "ReviewerIdentity",
    "RuntimeFileSnapshot",
    "RuntimeEnvironment",
    "SelectorAttestation",
    "TagContractSnapshot",
    "VerifiedHoldoutCheckout",
    "build_lifecycle_holdout_consensus",
    "build_lifecycle_holdout_review_packet",
    "build_lifecycle_owner_role_candidate_freeze",
    "bytes_hash",
    "canonical_hash",
    "canonical_json",
    "derive_source_family_id",
    "evaluation_harness_fingerprint",
    "load_lifecycle_holdout_consensus",
    "load_lifecycle_holdout_review_packet",
    "load_lifecycle_holdout_review_receipt",
    "load_lifecycle_holdout_selection",
    "load_canonical_lifecycle_review_material",
    "normalized_source_body_hash",
    "parse_lifecycle_holdout_consensus",
    "parse_lifecycle_holdout_review_packet",
    "parse_lifecycle_holdout_review_receipt",
    "parse_lifecycle_holdout_selection",
    "review_receipt_payload_with_id",
    "runtime_bundle_fingerprint",
    "seal_lifecycle_holdout_selection_payload",
    "selection_payload_with_id",
    "validate_lifecycle_holdout_review_receipt",
    "verify_candidate_runtime_bundle",
    "verify_candidate_corpus_independence",
    "verify_evaluation_harness_bundle",
    "verify_approved_selection_policy",
    "verify_lifecycle_holdout_checkout",
    "verify_selection_development_exclusions",
]
