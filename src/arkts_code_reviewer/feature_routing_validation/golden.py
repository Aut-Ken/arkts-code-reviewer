from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.feature_routing.engine import FeatureRouter

MANIFEST_SCHEMA_VERSION = "feature-routing-golden-v1"
REPORT_SCHEMA_VERSION = "feature-routing-report-v1"
BASELINE_SCHEMA_VERSION = "feature-routing-baseline-v1"
SUITE_ID = "feature-routing-fr0"

_ROOT_FIELDS = {
    "schema_version",
    "suite_id",
    "description",
    "tag_ids",
    "dimension_ids",
    "review_question_ids",
    "feature_config_version",
    "tags_config_version",
    "dimensions_config_version",
    "cases",
}
_CASE_FIELDS = {"case_id", "description", "sources", "units", "expected"}
_SOURCE_FIELDS = {
    "alias",
    "file",
    "repository",
    "revision",
    "logical_path",
    "content_sha256",
    "source_ref_id",
    "origin_lines",
}
_UNIT_FIELDS = {
    "unit_id",
    "source_alias",
    "source_ref_id",
    "unit_exact",
    "file_hints",
    "scope_diagnostics",
}
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
_EXPECTED_FIELDS = {
    "schema_version",
    "feature_config_version",
    "tags_config_version",
    "dimensions_config_version",
    "units",
    "mr_dimensions",
    "question_bindings",
    "diagnostics",
}
_EXPECTED_UNIT_FIELDS = {
    "unit_id",
    "source_ref_id",
    "exact_tags",
    "routing_tags",
    "shadow_exact_tags",
    "shadow_routing_tags",
    "tag_matches",
    "dimensions",
    "always_check_dimensions",
    "retrieval_dimensions",
    "routing_dimensions",
    "shadow_dimensions",
    "review_question_ids",
    "shadow_review_question_ids",
    "diagnostics",
}
_TAG_MATCH_FIELDS = {"tag_id", "status", "scope", "signals"}
_SIGNAL_FIELDS = {"kind", "value"}
_QUESTION_BINDING_FIELDS = {"primary_unit_id", "review_question_id"}
_UNIT_FACT_DIAGNOSTICS = {"unit_owner_unresolved"}
_SHA256_HEX_LENGTH = 64

_FEATURE_OUTPUT_SCHEMA_VERSION = "feature-routing-v1"
_FEATURE_CONFIG_VERSION = (
    "feature-config:sha256:bb241e9bdc54a9e6418e6be03a04593b8cf854838aec4d8644faa624eff7ae9c"
)
_TAGS_CONFIG_VERSION = "tags-v1"
_DIMENSIONS_CONFIG_VERSION = "dimensions-v1"
_TAG_IDS = (
    "has_animation",
    "has_async",
    "has_builder",
    "has_file_io",
    "has_image",
    "has_interactive_component",
    "has_layout",
    "has_lifecycle",
    "has_list_render",
    "has_logging",
    "has_media",
    "has_navigation",
    "has_network",
    "has_permission_request",
    "has_resource_ref",
    "has_responsive_api",
    "has_state_management",
    "has_storage",
    "has_subscription",
    "has_taskpool",
    "has_text_display",
    "has_timer",
    "has_user_input",
    "has_worker",
)
_DIMENSION_IDS = tuple(f"DIM-{index:02d}" for index in range(1, 13))
_REVIEW_QUESTION_IDS = (
    "RQ-accessibility",
    "RQ-adaptability",
    "RQ-concurrency",
    "RQ-correctness",
    "RQ-dfx",
    "RQ-internationalization",
    "RQ-lifecycle",
    "RQ-navigation",
    "RQ-network",
    "RQ-resource",
    "RQ-security",
    "RQ-state",
)
_SIGNAL_KINDS = {
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

_IMAGE_COMPONENTS = {"Image", "ImageAnimator", "ImageSpan"}
_INTERACTIVE_COMPONENTS = {
    "Button",
    "Checkbox",
    "Radio",
    "Search",
    "Slider",
    "TextArea",
    "TextInput",
    "Toggle",
}
_INTERACTIVE_ATTRIBUTES = {"onBlur", "onChange", "onClick", "onFocus", "onTouch"}
_LAYOUT_COMPONENTS = {
    "Column",
    "Flex",
    "Grid",
    "GridCol",
    "GridRow",
    "RelativeContainer",
    "Row",
    "Stack",
}
_STATE_DECORATORS = {
    "@BuilderParam",
    "@Consume",
    "@Link",
    "@Local",
    "@ObjectLink",
    "@Observed",
    "@ObservedV2",
    "@Once",
    "@Param",
    "@Prop",
    "@Provide",
    "@Require",
    "@State",
    "@StorageLink",
    "@StorageProp",
    "@Trace",
    "@Watch",
}
_LIFECYCLE_SYMBOLS = {
    "aboutToAppear",
    "aboutToDisappear",
    "onBackPress",
    "onPageHide",
    "onPageShow",
    "onReady",
}
_CORE_DIMENSIONS = {"DIM-01", "DIM-02", "DIM-03", "DIM-04", "DIM-05", "DIM-12"}
_DIMENSION_TAGS = {
    "DIM-06": {"has_file_io", "has_image", "has_media", "has_subscription", "has_timer"},
    "DIM-07": {"has_async", "has_taskpool", "has_worker"},
    "DIM-08": {"has_interactive_component"},
    "DIM-09": {"has_layout", "has_responsive_api"},
    "DIM-10": {"has_resource_ref", "has_text_display"},
    "DIM-11": {"has_network", "has_permission_request", "has_storage", "has_user_input"},
}
_QUESTION_TAGS = {
    "RQ-accessibility": {"has_interactive_component"},
    "RQ-adaptability": {"has_layout", "has_responsive_api"},
    "RQ-concurrency": {"has_async", "has_taskpool", "has_worker"},
    "RQ-dfx": {"has_logging"},
    "RQ-internationalization": {"has_resource_ref", "has_text_display"},
    "RQ-lifecycle": {"has_lifecycle"},
    "RQ-navigation": {"has_navigation"},
    "RQ-network": {"has_network"},
    "RQ-resource": {
        "has_file_io",
        "has_image",
        "has_media",
        "has_subscription",
        "has_timer",
    },
    "RQ-security": {
        "has_network",
        "has_permission_request",
        "has_storage",
        "has_user_input",
    },
    "RQ-state": {"has_state_management"},
}


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class GoldenSource:
    alias: str
    file: Path
    repository: str
    revision: str
    logical_path: str
    content_sha256: str
    source_ref_id: str
    origin_lines: tuple[int, int]
    content: str

    def provenance(self) -> dict[str, object]:
        return {
            "alias": self.alias,
            "repository": self.repository,
            "revision": self.revision,
            "logical_path": self.logical_path,
            "content_sha256": self.content_sha256,
            "source_ref_id": self.source_ref_id,
            "origin_lines": list(self.origin_lines),
        }


@dataclass(frozen=True)
class GoldenUnit:
    unit_id: str
    source_alias: str
    source_ref_id: str
    unit_exact: ScopedFacts
    file_hints: ScopedFacts
    scope_diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class FeatureGoldenCase:
    case_id: str
    description: str
    sources: tuple[GoldenSource, ...]
    units: tuple[GoldenUnit, ...]
    expected: dict[str, Any]

    def evaluate(self, *, reverse_units: bool = False) -> dict[str, Any]:
        units: Sequence[GoldenUnit] = self.units[::-1] if reverse_units else self.units
        result = FeatureRouter().route(
            [
                UnitFactScope(
                    unit_id=unit.unit_id,
                    source_ref_id=unit.source_ref_id,
                    unit_exact=unit.unit_exact,
                    file_hints=unit.file_hints,
                    diagnostics=unit.scope_diagnostics,  # type: ignore[arg-type]
                )
                for unit in units
            ]
        )
        return {
            "schema_version": result.schema_version,
            "feature_config_version": result.feature_config_version,
            "tags_config_version": result.tags_config_version,
            "dimensions_config_version": result.dimensions_config_version,
            "units": [
                {
                    "unit_id": profile.unit_id,
                    "source_ref_id": profile.source_ref_id,
                    "exact_tags": list(profile.exact_tags),
                    "routing_tags": list(profile.routing_tags),
                    "shadow_exact_tags": list(profile.shadow_exact_tags),
                    "shadow_routing_tags": list(profile.shadow_routing_tags),
                    "tag_matches": [match.to_dict() for match in profile.tag_matches],
                    "dimensions": list(profile.dimensions),
                    "always_check_dimensions": list(profile.always_check_dimensions),
                    "retrieval_dimensions": list(profile.retrieval_dimensions),
                    "routing_dimensions": list(profile.routing_dimensions),
                    "shadow_dimensions": list(profile.shadow_dimensions),
                    "review_question_ids": list(profile.review_question_ids),
                    "shadow_review_question_ids": list(profile.shadow_review_question_ids),
                    "diagnostics": list(profile.diagnostics),
                }
                for profile in result.units
            ],
            "mr_dimensions": list(result.mr_dimensions),
            "question_bindings": [binding.to_dict() for binding in result.question_bindings],
            "diagnostics": list(result.diagnostics),
        }


@dataclass(frozen=True)
class FeatureGoldenSuite:
    suite_id: str
    description: str
    tag_ids: tuple[str, ...]
    dimension_ids: tuple[str, ...]
    review_question_ids: tuple[str, ...]
    feature_config_version: str
    tags_config_version: str
    dimensions_config_version: str
    cases: tuple[FeatureGoldenCase, ...]
    manifest_path: Path
    manifest_sha256: str


def load_golden_suite(manifest_path: str | Path) -> FeatureGoldenSuite:
    path = Path(manifest_path)
    if path.is_symlink():
        raise ValueError("Feature Routing manifest must not be a symlink")
    raw = path.read_bytes()
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (_DuplicateKeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Feature Routing manifest: {exc}") from exc
    root = _object(value, "manifest")
    _exact_fields(root, _ROOT_FIELDS, "manifest")
    if root["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported Feature Routing manifest schema")
    if root["suite_id"] != SUITE_ID:
        raise ValueError("unsupported Feature Routing suite_id")
    description = _text(root["description"], "manifest.description")
    tag_ids = _sorted_strings(root["tag_ids"], "manifest.tag_ids", non_empty=True)
    dimension_ids = _sorted_strings(root["dimension_ids"], "manifest.dimension_ids", non_empty=True)
    review_question_ids = _sorted_strings(
        root["review_question_ids"],
        "manifest.review_question_ids",
        non_empty=True,
    )
    feature_config_version = _text(
        root["feature_config_version"], "manifest.feature_config_version"
    )
    tags_config_version = _text(root["tags_config_version"], "manifest.tags_config_version")
    dimensions_config_version = _text(
        root["dimensions_config_version"], "manifest.dimensions_config_version"
    )
    if tag_ids != _TAG_IDS:
        raise ValueError("Feature Routing contract must freeze the 24 registered Tags")
    if dimension_ids != _DIMENSION_IDS:
        raise ValueError("Feature Routing contract must freeze DIM-01 through DIM-12")
    if review_question_ids != _REVIEW_QUESTION_IDS:
        raise ValueError("Feature Routing contract must freeze the 12 review questions")
    if feature_config_version != _FEATURE_CONFIG_VERSION:
        raise ValueError("Feature Routing feature_config_version drift")
    if tags_config_version != _TAGS_CONFIG_VERSION:
        raise ValueError("Feature Routing tags_config_version drift")
    if dimensions_config_version != _DIMENSIONS_CONFIG_VERSION:
        raise ValueError("Feature Routing dimensions_config_version drift")

    case_values = _array(root["cases"], "manifest.cases")
    if len(case_values) != 16:
        raise ValueError("Feature Routing Golden requires exactly 16 cases")
    cases = tuple(
        _load_case(
            item,
            index,
            path.parent,
            set(tag_ids),
            set(dimension_ids),
            set(review_question_ids),
        )
        for index, item in enumerate(case_values)
    )
    case_ids = [case.case_id for case in cases]
    if case_ids != [f"FR{index:03d}" for index in range(1, 17)]:
        raise ValueError("Feature Routing cases must be ordered FR001 through FR016")
    return FeatureGoldenSuite(
        suite_id=SUITE_ID,
        description=description,
        tag_ids=tag_ids,
        dimension_ids=dimension_ids,
        review_question_ids=review_question_ids,
        feature_config_version=feature_config_version,
        tags_config_version=tags_config_version,
        dimensions_config_version=dimensions_config_version,
        cases=cases,
        manifest_path=path,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _load_case(
    value: object,
    index: int,
    root: Path,
    tag_ids: set[str],
    dimension_ids: set[str],
    review_question_ids: set[str],
) -> FeatureGoldenCase:
    context = f"manifest.cases[{index}]"
    data = _object(value, context)
    _exact_fields(data, _CASE_FIELDS, context)
    case_id = _text(data["case_id"], f"{context}.case_id")
    description = _text(data["description"], f"{context}.description")
    sources = tuple(
        _load_source(item, source_index, root, f"{context}.sources")
        for source_index, item in enumerate(_array(data["sources"], f"{context}.sources"))
    )
    if not sources:
        raise ValueError(f"{context}.sources must not be empty")
    aliases = [source.alias for source in sources]
    if aliases != sorted(set(aliases)):
        raise ValueError(f"{context}.sources must use unique stable alias order")
    source_by_alias = {source.alias: source for source in sources}
    units = tuple(
        _load_unit(item, unit_index, source_by_alias, f"{context}.units")
        for unit_index, item in enumerate(_array(data["units"], f"{context}.units"))
    )
    if not units:
        raise ValueError(f"{context}.units must not be empty")
    unit_ids = [unit.unit_id for unit in units]
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError(f"{context}.units must use unique unit_id values")
    expected = _load_expected(
        data["expected"],
        units,
        tag_ids,
        dimension_ids,
        review_question_ids,
        f"{context}.expected",
    )
    return FeatureGoldenCase(case_id, description, sources, units, expected)


def _load_source(value: object, index: int, root: Path, parent: str) -> GoldenSource:
    context = f"{parent}[{index}]"
    data = _object(value, context)
    _exact_fields(data, _SOURCE_FIELDS, context)
    alias = _text(data["alias"], f"{context}.alias")
    relative_file = _text(data["file"], f"{context}.file")
    candidate = Path(relative_file)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{context}.file escapes fixture root")
    fixture = root / candidate
    if fixture.is_symlink() or not fixture.is_file():
        raise ValueError(f"{context}.file must be a regular non-symlink fixture")
    resolved_root = root.resolve()
    if not fixture.resolve().is_relative_to(resolved_root):
        raise ValueError(f"{context}.file escapes fixture root")
    content = fixture.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    expected_digest = _sha256(data["content_sha256"], f"{context}.content_sha256")
    if digest != expected_digest:
        raise ValueError(f"{context} source hash/provenance drift")
    repository = _text(data["repository"], f"{context}.repository")
    revision = _text(data["revision"], f"{context}.revision")
    logical_path = _text(data["logical_path"], f"{context}.logical_path")
    source_ref = CodeSourceRef.create(
        repository=repository,
        revision=revision,
        path=logical_path,
        content_hash=f"sha256:{digest}",
    )
    source_ref_id = _text(data["source_ref_id"], f"{context}.source_ref_id")
    if source_ref_id != source_ref.source_ref_id:
        raise ValueError(f"{context}.source_ref_id provenance drift")
    origin = _array(data["origin_lines"], f"{context}.origin_lines")
    if len(origin) != 2 or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in origin
    ):
        raise ValueError(f"{context}.origin_lines must be two 1-based integers")
    start, end = origin
    line_count = max(1, len(content.splitlines()))
    if end < start or end > line_count:
        raise ValueError(f"{context}.origin_lines are invalid or out of range")
    return GoldenSource(
        alias,
        fixture,
        repository,
        revision,
        logical_path,
        digest,
        source_ref_id,
        (start, end),
        content,
    )


def _load_unit(
    value: object,
    index: int,
    sources: Mapping[str, GoldenSource],
    parent: str,
) -> GoldenUnit:
    context = f"{parent}[{index}]"
    data = _object(value, context)
    _exact_fields(data, _UNIT_FIELDS, context)
    unit_id = _text(data["unit_id"], f"{context}.unit_id")
    source_alias = _text(data["source_alias"], f"{context}.source_alias")
    source = sources.get(source_alias)
    if source is None:
        raise ValueError(f"{context}.source_alias is unknown")
    source_ref_id = _text(data["source_ref_id"], f"{context}.source_ref_id")
    if source_ref_id != source.source_ref_id:
        raise ValueError(f"{context}.source_ref_id does not match source_alias")
    exact = _scoped_facts(data["unit_exact"], f"{context}.unit_exact")
    hints = _scoped_facts(data["file_hints"], f"{context}.file_hints")
    diagnostics = _sorted_strings(data["scope_diagnostics"], f"{context}.scope_diagnostics")
    if not set(diagnostics).issubset(_UNIT_FACT_DIAGNOSTICS):
        raise ValueError(f"{context}.scope_diagnostics contains unknown values")
    return GoldenUnit(unit_id, source_alias, source_ref_id, exact, hints, diagnostics)


def _load_expected(
    value: object,
    units: tuple[GoldenUnit, ...],
    tag_ids: set[str],
    dimension_ids: set[str],
    review_question_ids: set[str],
    context: str,
) -> dict[str, Any]:
    data = _object(value, context)
    _exact_fields(data, _EXPECTED_FIELDS, context)
    schema_version = _text(data["schema_version"], f"{context}.schema_version")
    feature_config_version = _text(
        data["feature_config_version"], f"{context}.feature_config_version"
    )
    tags_config_version = _text(data["tags_config_version"], f"{context}.tags_config_version")
    dimensions_config_version = _text(
        data["dimensions_config_version"],
        f"{context}.dimensions_config_version",
    )
    for actual, expected, field in (
        (schema_version, _FEATURE_OUTPUT_SCHEMA_VERSION, "schema_version"),
        (feature_config_version, _FEATURE_CONFIG_VERSION, "feature_config_version"),
        (tags_config_version, _TAGS_CONFIG_VERSION, "tags_config_version"),
        (
            dimensions_config_version,
            _DIMENSIONS_CONFIG_VERSION,
            "dimensions_config_version",
        ),
    ):
        if actual != expected:
            raise ValueError(f"{context}.{field} disagrees with frozen truth")

    expected_units: list[dict[str, Any]] = []
    for index, item in enumerate(_array(data["units"], f"{context}.units")):
        item_context = f"{context}.units[{index}]"
        unit = _object(item, item_context)
        _exact_fields(unit, _EXPECTED_UNIT_FIELDS, item_context)
        expected_units.append(
            {
                "unit_id": _text(unit["unit_id"], f"{item_context}.unit_id"),
                "source_ref_id": _text(unit["source_ref_id"], f"{item_context}.source_ref_id"),
                "exact_tags": list(
                    _registered_strings(unit["exact_tags"], tag_ids, f"{item_context}.exact_tags")
                ),
                "routing_tags": list(
                    _registered_strings(
                        unit["routing_tags"], tag_ids, f"{item_context}.routing_tags"
                    )
                ),
                "shadow_exact_tags": list(
                    _registered_strings(
                        unit["shadow_exact_tags"],
                        tag_ids,
                        f"{item_context}.shadow_exact_tags",
                    )
                ),
                "shadow_routing_tags": list(
                    _registered_strings(
                        unit["shadow_routing_tags"],
                        tag_ids,
                        f"{item_context}.shadow_routing_tags",
                    )
                ),
                "tag_matches": _load_tag_matches(
                    unit["tag_matches"], tag_ids, f"{item_context}.tag_matches"
                ),
                "dimensions": list(
                    _registered_strings(
                        unit["dimensions"], dimension_ids, f"{item_context}.dimensions"
                    )
                ),
                "always_check_dimensions": list(
                    _registered_strings(
                        unit["always_check_dimensions"],
                        dimension_ids,
                        f"{item_context}.always_check_dimensions",
                    )
                ),
                "retrieval_dimensions": list(
                    _registered_strings(
                        unit["retrieval_dimensions"],
                        dimension_ids,
                        f"{item_context}.retrieval_dimensions",
                    )
                ),
                "routing_dimensions": list(
                    _registered_strings(
                        unit["routing_dimensions"],
                        dimension_ids,
                        f"{item_context}.routing_dimensions",
                    )
                ),
                "shadow_dimensions": list(
                    _registered_strings(
                        unit["shadow_dimensions"],
                        dimension_ids,
                        f"{item_context}.shadow_dimensions",
                    )
                ),
                "review_question_ids": list(
                    _registered_strings(
                        unit["review_question_ids"],
                        review_question_ids,
                        f"{item_context}.review_question_ids",
                    )
                ),
                "shadow_review_question_ids": list(
                    _registered_strings(
                        unit["shadow_review_question_ids"],
                        review_question_ids,
                        f"{item_context}.shadow_review_question_ids",
                    )
                ),
                "diagnostics": list(
                    _sorted_strings(unit["diagnostics"], f"{item_context}.diagnostics")
                ),
            }
        )
    expected_ids = [str(item["unit_id"]) for item in expected_units]
    if expected_ids != sorted(expected_ids):
        raise ValueError(f"{context}.units must use stable unit_id order")
    input_by_id = {unit.unit_id: unit for unit in units}
    if set(expected_ids) != set(input_by_id):
        raise ValueError(f"{context}.units must exactly cover input units")
    for item in expected_units:
        input_unit = input_by_id[str(item["unit_id"])]
        if item["source_ref_id"] != input_unit.source_ref_id:
            raise ValueError(f"{context} expected source_ref_id drift")
        exact_matches = _target_tag_matches(input_unit.unit_exact, "unit_exact")
        routing_matches = _target_tag_matches(input_unit.file_hints, "file_hint")
        tag_match_truth = sorted(
            (*exact_matches, *routing_matches),
            key=lambda match: (match["tag_id"], match["status"], match["scope"]),
        )
        exact_truth = sorted(match["tag_id"] for match in exact_matches)
        routing_truth = sorted(match["tag_id"] for match in routing_matches)
        dimension_truth = sorted(_target_dimensions(set(exact_truth)))
        retrieval_truth = sorted(_target_conditional_dimensions(set(exact_truth)))
        routing_dimension_truth = sorted(
            _target_conditional_dimensions(set(exact_truth) | set(routing_truth))
        )
        question_truth = sorted(_target_review_questions(set(exact_truth)))
        frozen_fields: dict[str, object] = {
            "exact_tags": exact_truth,
            "routing_tags": routing_truth,
            "shadow_exact_tags": [],
            "shadow_routing_tags": [],
            "tag_matches": tag_match_truth,
            "dimensions": dimension_truth,
            "always_check_dimensions": sorted(_CORE_DIMENSIONS),
            "retrieval_dimensions": retrieval_truth,
            "routing_dimensions": routing_dimension_truth,
            "shadow_dimensions": [],
            "review_question_ids": question_truth,
            "shadow_review_question_ids": [],
            "diagnostics": list(input_unit.scope_diagnostics),
        }
        for field, truth in frozen_fields.items():
            if item[field] != truth:
                raise ValueError(f"{context} {field} disagree with frozen truth")

    mr_dimensions = list(
        _registered_strings(data["mr_dimensions"], dimension_ids, f"{context}.mr_dimensions")
    )
    mr_truth = sorted(
        {
            dimension
            for item in expected_units
            for field in ("dimensions", "routing_dimensions")
            for dimension in item[field]
        }
    )
    if mr_dimensions != mr_truth:
        raise ValueError(f"{context}.mr_dimensions disagree with frozen truth")

    question_bindings = _load_question_bindings(
        data["question_bindings"],
        set(expected_ids),
        review_question_ids,
        f"{context}.question_bindings",
    )
    binding_truth = [
        {
            "primary_unit_id": str(item["unit_id"]),
            "review_question_id": question_id,
        }
        for item in expected_units
        for question_id in item["review_question_ids"]
    ]
    if question_bindings != binding_truth:
        raise ValueError(f"{context}.question_bindings disagree with frozen truth")
    diagnostics = list(_sorted_strings(data["diagnostics"], f"{context}.diagnostics"))
    if diagnostics:
        raise ValueError(f"{context}.diagnostics disagree with frozen truth")
    return {
        "schema_version": schema_version,
        "feature_config_version": feature_config_version,
        "tags_config_version": tags_config_version,
        "dimensions_config_version": dimensions_config_version,
        "units": expected_units,
        "mr_dimensions": mr_dimensions,
        "question_bindings": question_bindings,
        "diagnostics": diagnostics,
    }


def _load_tag_matches(
    value: object,
    tag_ids: set[str],
    context: str,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for index, item in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        data = _object(item, item_context)
        _exact_fields(data, _TAG_MATCH_FIELDS, item_context)
        tag_id = _text(data["tag_id"], f"{item_context}.tag_id")
        if tag_id not in tag_ids:
            raise ValueError(f"{item_context}.tag_id is unregistered")
        status = _text(data["status"], f"{item_context}.status")
        if status not in {"Active", "Draft"}:
            raise ValueError(f"{item_context}.status is unsupported")
        scope = _text(data["scope"], f"{item_context}.scope")
        if scope not in {"unit_exact", "file_hint"}:
            raise ValueError(f"{item_context}.scope is unsupported")
        matches.append(
            {
                "tag_id": tag_id,
                "status": status,
                "scope": scope,
                "signals": _load_signals(data["signals"], f"{item_context}.signals"),
            }
        )
    keys = [(match["tag_id"], match["status"], match["scope"]) for match in matches]
    if keys != sorted(set(keys)):
        raise ValueError(f"{context} must be sorted and unique")
    return matches


def _load_signals(value: object, context: str) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    for index, item in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        data = _object(item, item_context)
        _exact_fields(data, _SIGNAL_FIELDS, item_context)
        kind = _text(data["kind"], f"{item_context}.kind")
        if kind not in _SIGNAL_KINDS:
            raise ValueError(f"{item_context}.kind is unsupported")
        signals.append(
            {
                "kind": kind,
                "value": _text(data["value"], f"{item_context}.value"),
            }
        )
    keys = [(signal["kind"], signal["value"]) for signal in signals]
    if not signals or keys != sorted(set(keys)):
        raise ValueError(f"{context} must be non-empty, sorted and unique")
    return signals


def _load_question_bindings(
    value: object,
    unit_ids: set[str],
    review_question_ids: set[str],
    context: str,
) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    for index, item in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        data = _object(item, item_context)
        _exact_fields(data, _QUESTION_BINDING_FIELDS, item_context)
        unit_id = _text(data["primary_unit_id"], f"{item_context}.primary_unit_id")
        question_id = _text(data["review_question_id"], f"{item_context}.review_question_id")
        if unit_id not in unit_ids:
            raise ValueError(f"{item_context}.primary_unit_id is unknown")
        if question_id not in review_question_ids:
            raise ValueError(f"{item_context}.review_question_id is unregistered")
        bindings.append(
            {
                "primary_unit_id": unit_id,
                "review_question_id": question_id,
            }
        )
    keys = [(binding["primary_unit_id"], binding["review_question_id"]) for binding in bindings]
    if keys != sorted(set(keys)):
        raise ValueError(f"{context} must be sorted and unique")
    return bindings


def _scoped_facts(value: object, context: str) -> ScopedFacts:
    data = _object(value, context)
    _exact_fields(data, _SCOPED_FACT_FIELDS, context)
    payload = {
        field: _sorted_strings(data[field], f"{context}.{field}")
        for field in sorted(_SCOPED_FACT_FIELDS)
    }
    return ScopedFacts(**payload)


def _target_tag_matches(facts: ScopedFacts, scope: str) -> list[dict[str, Any]]:
    signals_by_tag = _target_tag_signals(facts)
    return [
        {
            "tag_id": tag_id,
            "status": "Active",
            "scope": scope,
            "signals": [
                {"kind": kind, "value": value} for kind, value in sorted(signals_by_tag[tag_id])
            ],
        }
        for tag_id in sorted(signals_by_tag)
    ]


def _target_tag_signals(
    facts: ScopedFacts,
) -> dict[str, set[tuple[str, str]]]:
    components = set(facts.components)
    apis = set(facts.apis)
    decorators = set(facts.decorators)
    attributes = set(facts.attributes)
    symbols = set(facts.symbols)
    syntax = set(facts.syntax)
    signals: dict[str, set[tuple[str, str]]] = {}

    def exact(
        tag_id: str,
        kind: str,
        actual: set[str],
        configured: set[str],
    ) -> None:
        signals.setdefault(tag_id, set()).update(
            (kind, value) for value in actual.intersection(configured)
        )
        if not signals[tag_id]:
            signals.pop(tag_id)

    def prefixes(tag_id: str, actual: set[str], configured: tuple[str, ...]) -> None:
        matches = {
            ("apis", value)
            for value in actual
            if any(value.startswith(prefix) for prefix in configured)
        }
        if matches:
            signals.setdefault(tag_id, set()).update(matches)

    exact("has_animation", "apis", apis, {"animateTo"})
    exact("has_animation", "attributes", attributes, {"transition"})
    exact("has_async", "syntax", syntax, {"async_fn", "await_expr", "promise"})
    exact("has_builder", "decorators", decorators, {"@Builder", "@BuilderParam"})
    prefixes("has_file_io", apis, ("fileIo.", "fs."))
    exact("has_image", "components", components, _IMAGE_COMPONENTS)
    prefixes("has_image", apis, ("image.",))
    exact(
        "has_interactive_component",
        "components",
        components,
        _INTERACTIVE_COMPONENTS,
    )
    exact(
        "has_interactive_component",
        "attributes",
        attributes,
        _INTERACTIVE_ATTRIBUTES,
    )
    exact("has_layout", "components", components, _LAYOUT_COMPONENTS)
    exact("has_lifecycle", "symbols", symbols, _LIFECYCLE_SYMBOLS)
    exact("has_list_render", "components", components, {"Grid", "List", "WaterFlow"})
    exact(
        "has_list_render",
        "symbols",
        symbols,
        {"ForEach", "LazyForEach", "Repeat"},
    )
    prefixes("has_logging", apis, ("hilog.",))
    exact("has_media", "components", components, {"Video", "XComponent"})
    prefixes("has_media", apis, ("audio.", "camera.", "media."))
    exact(
        "has_navigation",
        "components",
        components,
        {"NavDestination", "Navigation"},
    )
    prefixes("has_navigation", apis, ("router.",))
    prefixes("has_network", apis, ("http.", "rcp.", "socket."))
    exact(
        "has_permission_request",
        "apis",
        apis,
        {"requestPermissionsFromUser"},
    )
    prefixes("has_permission_request", apis, ("abilityAccessCtrl.",))
    exact("has_resource_ref", "apis", apis, {"$r", "$rawfile"})
    if facts.resource_references:
        signals.setdefault("has_resource_ref", set()).update(
            ("resource_references", value) for value in facts.resource_references
        )
    exact(
        "has_responsive_api",
        "components",
        components,
        {"GridCol", "GridRow"},
    )
    prefixes("has_responsive_api", apis, ("display.", "mediaquery."))
    exact("has_state_management", "decorators", decorators, _STATE_DECORATORS)
    prefixes("has_storage", apis, ("preferences.", "relationalStore."))
    exact(
        "has_subscription",
        "apis",
        apis,
        {
            "emitter.off",
            "emitter.on",
            "emitter.once",
            "sensor.off",
            "sensor.on",
            "sensor.once",
        },
    )
    prefixes("has_taskpool", apis, ("taskpool.",))
    exact(
        "has_text_display",
        "components",
        components,
        {"Search", "Text", "TextArea", "TextInput"},
    )
    exact("has_text_display", "attributes", attributes, {"placeholder"})
    exact(
        "has_timer",
        "apis",
        apis,
        {
            "clearInterval",
            "clearTimeout",
            "setInterval",
            "setTimeout",
            "systemTimer.setInterval",
        },
    )
    exact(
        "has_user_input",
        "components",
        components,
        {"Search", "TextArea", "TextInput"},
    )
    prefixes("has_worker", apis, ("worker.",))
    exact("has_worker", "symbols", symbols, {"ThreadWorker"})
    return signals


def _target_dimensions(tags: set[str]) -> set[str]:
    return set(_CORE_DIMENSIONS) | _target_conditional_dimensions(tags)


def _target_conditional_dimensions(tags: set[str]) -> set[str]:
    dimensions: set[str] = set()
    for dimension_id, trigger_tags in _DIMENSION_TAGS.items():
        if tags & trigger_tags:
            dimensions.add(dimension_id)
    return dimensions


def _target_review_questions(tags: set[str]) -> set[str]:
    questions = {"RQ-correctness"}
    for question_id, trigger_tags in _QUESTION_TAGS.items():
        if tags & trigger_tags:
            questions.add(question_id)
    return questions


def evaluate_golden_suite(suite: FeatureGoldenSuite) -> dict[str, Any]:
    rows = [_evaluate_case(case) for case in suite.cases]
    matched = sum(row["matched"] is True for row in rows)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "implementation": "formal-feature-router-v1",
        "manifest_sha256": suite.manifest_sha256,
        "case_count": len(rows),
        "matched_case_count": matched,
        "mismatched_case_count": len(rows) - matched,
        "metrics": _metrics(rows),
        "cases": rows,
    }


def _evaluate_case(case: FeatureGoldenCase) -> dict[str, Any]:
    error: str | None = None
    try:
        first = case.evaluate()
        repeat_equal = first == case.evaluate()
        permutation_equal = first == case.evaluate(reverse_units=True)
        differences = _differences(case.expected, first)
    except Exception as exc:  # Golden failures must remain visible.
        first = {"error": f"{type(exc).__name__}: {exc}"}
        repeat_equal = False
        permutation_equal = False
        differences = _differences(case.expected, first)
        error = f"{type(exc).__name__}: {exc}"
    matched = not differences and repeat_equal and permutation_equal and error is None
    return {
        "case_id": case.case_id,
        "matched": matched,
        "repeat_equal": repeat_equal,
        "permutation_equal": permutation_equal,
        "source_provenance": [source.provenance() for source in case.sources],
        "expected": case.expected,
        "actual": first,
        "differences": differences,
        "error": error,
        "metric_counts": _metric_counts(case.expected, first),
    }


def _metric_counts(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, int]:
    expected_units = {
        str(item["unit_id"]): item
        for item in expected.get("units", [])
        if isinstance(item, dict) and "unit_id" in item
    }
    actual_units = {
        str(item["unit_id"]): item
        for item in actual.get("units", [])
        if isinstance(item, dict) and "unit_id" in item
    }
    counts: dict[str, int] = {}
    for field, prefix in (
        ("exact_tags", "exact_tag"),
        ("routing_tags", "routing_tag"),
        ("dimensions", "dimension"),
        ("retrieval_dimensions", "retrieval_dimension"),
        ("routing_dimensions", "routing_dimension"),
        ("review_question_ids", "review_question"),
    ):
        truth = {
            (unit_id, value)
            for unit_id, item in expected_units.items()
            for value in item.get(field, [])
        }
        predicted = {
            (unit_id, value)
            for unit_id, item in actual_units.items()
            for value in item.get(field, [])
        }
        counts[f"{prefix}_true"] = len(truth)
        counts[f"{prefix}_actual"] = len(predicted)
        counts[f"{prefix}_hit"] = len(truth & predicted)
    expected_signals = _activation_signal_keys(expected_units)
    actual_signals = _activation_signal_keys(actual_units)
    expected_bindings = _binding_keys(expected.get("question_bindings", []))
    actual_bindings = _binding_keys(actual.get("question_bindings", []))
    expected_mr = set(expected.get("mr_dimensions", []))
    actual_mr = set(actual.get("mr_dimensions", []))
    counts.update(
        {
            "mr_dimension_true": len(expected_mr),
            "mr_dimension_actual": len(actual_mr),
            "mr_dimension_hit": len(expected_mr & actual_mr),
            "activation_signal_true": len(expected_signals),
            "activation_signal_actual": len(actual_signals),
            "activation_signal_hit": len(expected_signals & actual_signals),
            "question_binding_true": len(expected_bindings),
            "question_binding_actual": len(actual_bindings),
            "question_binding_hit": len(expected_bindings & actual_bindings),
        }
    )
    return counts


def _activation_signal_keys(
    units: Mapping[str, Mapping[str, Any]],
) -> set[tuple[str, str, str, str, str, str]]:
    return {
        (
            unit_id,
            str(match["tag_id"]),
            str(match["status"]),
            str(match["scope"]),
            str(signal["kind"]),
            str(signal["value"]),
        )
        for unit_id, unit in units.items()
        for match in unit.get("tag_matches", [])
        if isinstance(match, dict)
        for signal in match.get("signals", [])
        if isinstance(signal, dict)
        and {"tag_id", "status", "scope"}.issubset(match)
        and {"kind", "value"}.issubset(signal)
    }


def _binding_keys(value: object) -> set[tuple[str, str]]:
    if not isinstance(value, list):
        return set()
    return {
        (str(binding["primary_unit_id"]), str(binding["review_question_id"]))
        for binding in value
        if isinstance(binding, dict)
        and {"primary_unit_id", "review_question_id"}.issubset(binding)
    }


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    counts: dict[str, int] = {}
    for row in rows:
        for key, value in row["metric_counts"].items():
            counts[key] = counts.get(key, 0) + int(value)
    result: dict[str, float] = {}
    for prefix in (
        "exact_tag",
        "routing_tag",
        "dimension",
        "retrieval_dimension",
        "routing_dimension",
        "review_question",
        "activation_signal",
        "question_binding",
        "mr_dimension",
    ):
        result[f"{prefix}_precision"] = _ratio(counts[f"{prefix}_hit"], counts[f"{prefix}_actual"])
        result[f"{prefix}_recall"] = _ratio(counts[f"{prefix}_hit"], counts[f"{prefix}_true"])
    result["case_exact_accuracy"] = _ratio(sum(row["matched"] is True for row in rows), len(rows))
    result["input_order_stability"] = _ratio(
        sum(row["permutation_equal"] is True for row in rows), len(rows)
    )
    return result


def is_perfect(report: Mapping[str, Any], suite: FeatureGoldenSuite) -> bool:
    try:
        rows = report["cases"]
        if (
            report["schema_version"] != REPORT_SCHEMA_VERSION
            or report["suite_id"] != suite.suite_id
            or report["manifest_sha256"] != suite.manifest_sha256
            or not isinstance(rows, list)
            or len(rows) != 16
        ):
            return False
        if [row.get("case_id") for row in rows] != [case.case_id for case in suite.cases]:
            return False
        for row, case in zip(rows, suite.cases, strict=True):
            if (
                set(row)
                != {
                    "case_id",
                    "matched",
                    "repeat_equal",
                    "permutation_equal",
                    "source_provenance",
                    "expected",
                    "actual",
                    "differences",
                    "error",
                    "metric_counts",
                }
                or row["matched"] is not True
                or row["repeat_equal"] is not True
                or row["permutation_equal"] is not True
                or row["expected"] != case.expected
                or row["actual"] != case.expected
                or row["differences"] != []
                or row["error"] is not None
                or row["source_provenance"] != [source.provenance() for source in case.sources]
                or row["metric_counts"] != _metric_counts(case.expected, case.expected)
            ):
                return False
        metrics = report["metrics"]
        if not isinstance(metrics, dict) or metrics != _metrics(rows):
            return False
        required_one = {
            "exact_tag_precision",
            "exact_tag_recall",
            "routing_tag_precision",
            "routing_tag_recall",
            "dimension_precision",
            "dimension_recall",
            "retrieval_dimension_precision",
            "retrieval_dimension_recall",
            "routing_dimension_precision",
            "routing_dimension_recall",
            "review_question_precision",
            "review_question_recall",
            "activation_signal_precision",
            "activation_signal_recall",
            "question_binding_precision",
            "question_binding_recall",
            "mr_dimension_precision",
            "mr_dimension_recall",
            "case_exact_accuracy",
            "input_order_stability",
        }
        return (
            all(metrics.get(key) == 1.0 for key in required_one)
            and report["case_count"] == 16
            and report["matched_case_count"] == 16
            and report["mismatched_case_count"] == 0
        )
    except (KeyError, TypeError, ValueError):
        return False


def write_current_baseline(
    report: Mapping[str, Any], suite: FeatureGoldenSuite, baseline_path: str | Path
) -> None:
    path = Path(baseline_path)
    allowed = suite.manifest_path.parent / "baselines" / "current.json"
    if path.resolve() != allowed.resolve():
        raise ValueError("Feature Routing baseline writer may only update baselines/current.json")
    if allowed.parent.is_symlink() or (path.exists() and path.is_symlink()):
        raise ValueError("Feature Routing baseline must not be a symlink")
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "manifest_sha256": suite.manifest_sha256,
        "report": report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def assert_strict_baseline(
    report: Mapping[str, Any], suite: FeatureGoldenSuite, baseline_path: str | Path
) -> None:
    path = Path(baseline_path)
    if path.is_symlink():
        raise ValueError("Feature Routing baseline must not be a symlink")
    value = json.loads(path.read_bytes(), object_pairs_hook=_reject_duplicate_keys)
    data = _object(value, "baseline")
    _exact_fields(data, {"schema_version", "suite_id", "manifest_sha256", "report"}, "baseline")
    if (
        data["schema_version"] != BASELINE_SCHEMA_VERSION
        or data["suite_id"] != suite.suite_id
        or data["manifest_sha256"] != suite.manifest_sha256
        or data["report"] != report
    ):
        raise ValueError("Feature Routing strict baseline drift")


def _differences(expected: object, actual: object, path: str = "result") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            next_path = f"{path}.{key}"
            if key not in expected:
                differences.append(f"{next_path}: unexpected field")
            elif key not in actual:
                differences.append(f"{next_path}: missing field")
            else:
                differences.extend(_differences(expected[key], actual[key], next_path))
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        differences = []
        if len(expected) != len(actual):
            differences.append(f"{path}.length: expected {len(expected)}, actual {len(actual)}")
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            differences.extend(_differences(left, right, f"{path}[{index}]"))
        return differences
    if expected != actual:
        return [f"{path}: expected {expected!r}, actual {actual!r}"]
    return []


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be an object")
    return value


def _array(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _exact_fields(value: Mapping[str, object], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{context} fields mismatch: missing={missing}, unknown={unknown}")


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _sha256(value: object, context: str) -> str:
    text = _text(value, context)
    if len(text) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValueError(f"{context} must be lowercase SHA-256 hex")
    return text


def _sorted_strings(value: object, context: str, *, non_empty: bool = False) -> tuple[str, ...]:
    values = _array(value, context)
    if any(not isinstance(item, str) or not item for item in values):
        raise ValueError(f"{context} must contain non-empty strings")
    if values != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    if non_empty and not values:
        raise ValueError(f"{context} must not be empty")
    return tuple(values)


def _registered_strings(value: object, allowed: set[str], context: str) -> tuple[str, ...]:
    values = _sorted_strings(value, context)
    if not set(values).issubset(allowed):
        raise ValueError(f"{context} contains unregistered values")
    return values


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "FeatureGoldenCase",
    "FeatureGoldenSuite",
    "assert_strict_baseline",
    "evaluate_golden_suite",
    "is_perfect",
    "load_golden_suite",
    "write_current_baseline",
]
