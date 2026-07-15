from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from arkts_code_reviewer.feature_routing.config import FeatureConfig, load_feature_config
from arkts_code_reviewer.feature_routing_validation.golden import (
    MANIFEST_SCHEMA_VERSION as FEATURE_GOLDEN_SCHEMA_VERSION,
)
from arkts_code_reviewer.feature_routing_validation.golden import (
    FeatureGoldenCase,
    load_golden_suite,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth import (
    TAG_TRUTH_BASELINE_SCHEMA_VERSION,
    TagTruthSuite,
    load_tag_truth_suite,
    tag_truth_suite_fingerprint,
)
from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
    TAG_RETRIEVAL_TRUTH_SCHEMA_VERSION,
    TagRetrievalTruthCase,
    TagRetrievalTruthSuite,
    load_tag_retrieval_truth,
    tag_retrieval_truth_fingerprint,
)

TAG_TRUTH_COVERAGE_SCHEMA_VERSION = "tag-truth-coverage-report-v1"
TAG_TRUTH_COVERAGE_FINGERPRINT_PREFIX = "tag-truth-coverage:sha256:"

_EXPECTED_FORMAL_TAG_COUNT = 24
_EXPECTED_TAG_SCHEMA_VERSION = "tag-config-v1"
_EXPECTED_TAG_CONFIG_VERSION = "tags-v1"
_EXPECTED_DEVELOPMENT_ROLE = "development_regression"
_EXPECTED_DRAFT_SUITE_ID = "tag-rdb-01b"
_EXPECTED_DRAFT_TAG_ID = "has_relational_store_api"

_BASELINE_ROOT_FIELDS = {
    "schema_version",
    "suite_id",
    "truth_status",
    "annotation_policy_version",
    "suite_fingerprint",
    "repository",
    "base_feature_config_fingerprint",
    "candidate",
    "source_count",
    "case_count",
    "contract",
    "provisional_semantic_metrics",
    "provisional_semantic_metrics_by_split",
    "provisional_semantic_metrics_by_stratum",
    "provisional_semantic_metrics_by_app_scope",
    "cohorts",
    "quality_decision",
    "cases",
}
_BASELINE_COHORT_FIELDS = {
    "semantic_labels",
    "strata",
    "review_status",
    "taxonomy_conflict_case_ids",
    "parser_risk_case_ids",
    "recovered_signal_case_ids",
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


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be an object")
    return cast(dict[str, object], value)


def _sequence(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return cast(list[object], value)


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def _exact_fields(value: Mapping[str, object], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{context} fields mismatch: "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )


def _sorted_unique_strings(value: object, context: str) -> tuple[str, ...]:
    items = _sequence(value, context)
    if any(not isinstance(item, str) or not item for item in items):
        raise ValueError(f"{context} must contain non-empty strings")
    strings = cast(list[str], items)
    if strings != sorted(set(strings)):
        raise ValueError(f"{context} must be sorted and unique")
    return tuple(strings)


def _canonical_hash(prefix: str, payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def tag_truth_coverage_fingerprint(report: Mapping[str, object]) -> str:
    payload = dict(report)
    payload.pop("coverage_fingerprint", None)
    return _canonical_hash("tag-truth-coverage", payload)


def _load_draft_behavior_baseline(
    path: str | Path,
    suite: TagTruthSuite,
) -> tuple[dict[str, object], str, tuple[str, ...]]:
    baseline_path = Path(path)
    if baseline_path.is_symlink() or not baseline_path.is_file():
        raise ValueError(f"draft behavior baseline must be a regular file: {baseline_path}")
    try:
        raw = baseline_path.read_bytes()
        root = _mapping(
            json.loads(raw, object_pairs_hook=_reject_duplicate_keys),
            "draft behavior baseline",
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid draft behavior baseline {baseline_path}: {exc}") from exc

    _exact_fields(root, _BASELINE_ROOT_FIELDS, "draft behavior baseline")
    if root["schema_version"] != TAG_TRUTH_BASELINE_SCHEMA_VERSION:
        raise ValueError("unsupported draft behavior baseline schema")
    expected_identity: dict[str, object] = {
        "suite_id": suite.suite_id,
        "truth_status": suite.truth_status,
        "annotation_policy_version": suite.annotation_policy_version,
        "suite_fingerprint": tag_truth_suite_fingerprint(suite),
        "repository": suite.repository.model_dump(mode="json"),
        "base_feature_config_fingerprint": suite.base_feature_config_fingerprint,
        "source_count": len(suite.sources),
        "case_count": len(suite.cases),
    }
    for field, expected in expected_identity.items():
        if root[field] != expected:
            raise ValueError(f"draft behavior baseline {field} drift")

    candidate = _mapping(root["candidate"], "draft behavior baseline candidate")
    expected_candidate = suite.candidate.model_dump(mode="json")
    expected_candidate["observed_config_fingerprint"] = suite.candidate.config_fingerprint
    if candidate != expected_candidate:
        raise ValueError("draft behavior baseline candidate identity drift")

    baseline_cases = _sequence(root["cases"], "draft behavior baseline cases")
    if len(baseline_cases) != len(suite.cases):
        raise ValueError("draft behavior baseline case count drift")
    baseline_case_ids = tuple(
        _text(_mapping(item, "draft behavior baseline case").get("case_id"), "baseline case_id")
        for item in baseline_cases
    )
    expected_case_ids = tuple(case.case_id for case in suite.cases)
    if baseline_case_ids != expected_case_ids:
        raise ValueError("draft behavior baseline case identity drift")

    cohorts = _mapping(root["cohorts"], "draft behavior baseline cohorts")
    _exact_fields(cohorts, _BASELINE_COHORT_FIELDS, "draft behavior baseline cohorts")
    parser_risk_case_ids = _sorted_unique_strings(
        cohorts["parser_risk_case_ids"],
        "draft behavior baseline parser_risk_case_ids",
    )
    eligible_case_ids = {case.case_id for case in suite.cases if case.metric_eligible}
    if not set(parser_risk_case_ids).issubset(eligible_case_ids):
        raise ValueError("draft behavior baseline parser risk references ineligible cases")

    baseline_fingerprint = f"tag-truth-baseline:sha256:{hashlib.sha256(raw).hexdigest()}"
    return root, baseline_fingerprint, parser_risk_case_ids


def _validate_input_identity(
    feature_config: FeatureConfig,
    golden_tag_ids: Sequence[str],
    golden_feature_config_version: str,
    development: TagRetrievalTruthSuite,
    draft: TagTruthSuite,
) -> tuple[str, ...]:
    formal_tag_ids = tuple(sorted(feature_config.tags_by_id))
    if (
        feature_config.tag_config.schema_version != _EXPECTED_TAG_SCHEMA_VERSION
        or feature_config.tag_config.version != _EXPECTED_TAG_CONFIG_VERSION
        or len(formal_tag_ids) != _EXPECTED_FORMAL_TAG_COUNT
    ):
        raise ValueError("formal Tag configuration identity drift")
    if any(definition.status != "Active" for definition in feature_config.tags_by_id.values()):
        raise ValueError("formal tags-v1 must contain only Active Tags")
    if tuple(golden_tag_ids) != formal_tag_ids:
        raise ValueError("Feature Routing Golden Tag IDs differ from formal Tags")
    if golden_feature_config_version != feature_config.fingerprint:
        raise ValueError("Feature Routing Golden Feature config fingerprint drift")
    if development.schema_version != TAG_RETRIEVAL_TRUTH_SCHEMA_VERSION:
        raise ValueError("unsupported development Truth schema")
    if development.feature_config_fingerprint != feature_config.fingerprint:
        raise ValueError("development Truth Feature config fingerprint drift")
    if development.evaluation_boundary.dataset_role != _EXPECTED_DEVELOPMENT_ROLE:
        raise ValueError("development Truth dataset role drift")
    if set(case.target_tag for case in development.cases) - set(formal_tag_ids):
        raise ValueError("development Truth references non-formal target Tags")
    if draft.suite_id != _EXPECTED_DRAFT_SUITE_ID:
        raise ValueError("unsupported Draft Tag Truth suite")
    if draft.candidate.tag_id != _EXPECTED_DRAFT_TAG_ID:
        raise ValueError("unsupported Draft Tag candidate")
    if draft.candidate.tag_id in feature_config.tags_by_id:
        raise ValueError("Draft candidate must remain separate from formal Tags")
    if draft.base_feature_config_fingerprint != feature_config.fingerprint:
        raise ValueError("Draft Tag Truth base Feature config fingerprint drift")
    development_repository = development.repository
    if (
        draft.repository.source_id,
        draft.repository.repository,
        draft.repository.revision,
    ) != (
        development_repository.source_id,
        development_repository.repository,
        development_repository.revision,
    ):
        raise ValueError("development and Draft Truth repository identities differ")
    return formal_tag_ids


def _synthetic_counts(
    cases: Sequence[FeatureGoldenCase],
    formal_tag_ids: Sequence[str],
) -> tuple[int, dict[str, Counter[str]]]:
    counts = {tag_id: Counter[str]() for tag_id in formal_tag_ids}
    unit_count = 0
    for case_object in cases:
        expected = cast(Mapping[str, object], case_object.expected)
        units = _sequence(expected["units"], "Feature Routing Golden expected units")
        unit_count += len(units)
        for unit_object in units:
            unit = _mapping(unit_object, "Feature Routing Golden expected unit")
            exact_tags = _sorted_unique_strings(unit["exact_tags"], "Golden exact_tags")
            routing_tags = _sorted_unique_strings(unit["routing_tags"], "Golden routing_tags")
            for tag_id in exact_tags:
                if tag_id not in counts:
                    raise ValueError("Golden exact_tags contain a non-formal Tag")
                counts[tag_id]["exact_positive"] += 1
            for tag_id in routing_tags:
                if tag_id not in counts:
                    raise ValueError("Golden routing_tags contain a non-formal Tag")
                counts[tag_id]["routing_positive"] += 1
    return unit_count, counts


def _formal_tag_rows(
    feature_config: FeatureConfig,
    formal_tag_ids: Sequence[str],
    golden_case_count: int,
    synthetic_unit_count: int,
    synthetic_counts: Mapping[str, Counter[str]],
    development: TagRetrievalTruthSuite,
) -> list[dict[str, object]]:
    sources_by_alias = {source.alias: source for source in development.sources}
    target_cases_by_tag: dict[str, list[TagRetrievalTruthCase]] = {
        tag_id: [] for tag_id in formal_tag_ids
    }
    secondary_counts = Counter[str]()
    for case in development.cases:
        target_cases_by_tag[case.target_tag].append(case)
        secondary_counts.update(case.required_co_tags)
    product_decisions: Counter[str] = Counter(
        item.tag_id for item in development.cross_target_tag_adjudications
    )

    rows: list[dict[str, object]] = []
    for tag_id in formal_tag_ids:
        target_cases = target_cases_by_tag[tag_id]
        positive_count = sum(case.expected_exact_tag for case in target_cases)
        negative_count = len(target_cases) - positive_count
        source_aliases = {case.source_alias for case in target_cases}
        families = {sources_by_alias[alias].source_family_id for alias in source_aliases}
        legacy_acceptance_count = sum(case.split == "acceptance_holdout" for case in target_cases)
        review_status_counts = Counter(case.review_status for case in target_cases)
        synthetic = synthetic_counts[tag_id]
        exact_positive = synthetic["exact_positive"]
        routing_positive = synthetic["routing_positive"]
        evidence_level = "development_only" if target_cases else "synthetic_only"
        qualification_reasons = [
            "adjudicated_truth_missing",
            "independent_blind_holdout_missing",
            "parser_risk_not_measured",
            "prevalence_sample_missing",
        ]
        if target_cases:
            qualification_reasons.extend(
                ["independent_adjudication_missing", "truth_is_provisional"]
            )
        else:
            qualification_reasons.append("real_code_target_truth_missing")
        rows.append(
            {
                "tag_id": tag_id,
                "configured_status": feature_config.tags_by_id[tag_id].status,
                "synthetic": {
                    "dataset_role": "synthetic_contract_golden",
                    "case_count": golden_case_count,
                    "unit_count": synthetic_unit_count,
                    "exact_positive_count": exact_positive,
                    "exact_expected_absent_count": synthetic_unit_count - exact_positive,
                    "routing_positive_count": routing_positive,
                    "routing_expected_absent_count": synthetic_unit_count - routing_positive,
                    "truth_status": "human_reviewed_expected",
                    "review_status": "human_reviewed",
                },
                "development": {
                    "dataset_role": (
                        development.evaluation_boundary.dataset_role
                        if target_cases
                        else "not_measured"
                    ),
                    "target_case_count": len(target_cases),
                    "positive_case_count": positive_count,
                    "negative_case_count": negative_count,
                    "source_count": len(source_aliases),
                    "family_count": len(families),
                    "truth_status": development.truth_status if target_cases else "not_measured",
                    "review_status_counts": dict(sorted(review_status_counts.items())),
                    "legacy_acceptance_case_count": legacy_acceptance_count,
                    "legacy_acceptance_is_independent": False,
                    "declared_secondary_positive_count": secondary_counts[tag_id],
                    "recorded_product_decision_count": product_decisions[tag_id],
                },
                "adjudicated_case_count": 0,
                "adjudicated_status": "not_measured",
                "blind_case_count": 0,
                "blind_status": "not_measured",
                "prevalence_case_count": 0,
                "prevalence_status": "not_measured",
                "parser_risk_status": "not_measured",
                "parser_risk_case_count": None,
                "parser_risk_reason": "input_manifests_contain_no_runtime_parser_observation",
                "evidence_level": evidence_level,
                "qualification_status": "not_qualified",
                "qualification_reasons": sorted(qualification_reasons),
                "production_qualified": False,
            }
        )
    return rows


def _draft_candidate_row(
    suite: TagTruthSuite,
    parser_risk_case_ids: Sequence[str],
) -> dict[str, object]:
    sources_by_alias = {source.alias: source for source in suite.sources}
    eligible = [case for case in suite.cases if case.metric_eligible]
    positive_count = sum(case.semantic_label == "positive" for case in eligible)
    negative_count = sum(case.semantic_label == "negative" for case in eligible)
    source_aliases = {case.source_alias for case in eligible}
    families = {sources_by_alias[alias].source_family_id for alias in source_aliases}
    review_status_counts = Counter(case.review_status for case in eligible)
    legacy_acceptance_count = sum(case.split == "acceptance_holdout" for case in eligible)
    return {
        "tag_id": suite.candidate.tag_id,
        "configured_status": suite.candidate.status,
        "synthetic": {
            "dataset_role": "not_measured",
            "case_count": 0,
            "unit_count": 0,
            "exact_positive_count": 0,
            "exact_expected_absent_count": 0,
            "routing_positive_count": 0,
            "routing_expected_absent_count": 0,
            "truth_status": "not_measured",
            "review_status": "not_measured",
        },
        "development": {
            "dataset_role": "development_regression",
            "target_case_count": len(eligible),
            "positive_case_count": positive_count,
            "negative_case_count": negative_count,
            "source_count": len(source_aliases),
            "family_count": len(families),
            "diagnostic_case_count": len(suite.cases) - len(eligible),
            "truth_status": suite.truth_status,
            "review_status_counts": dict(sorted(review_status_counts.items())),
            "legacy_acceptance_case_count": legacy_acceptance_count,
            "legacy_acceptance_is_independent": False,
            "declared_secondary_positive_count": 0,
            "recorded_product_decision_count": 0,
        },
        "adjudicated_case_count": 0,
        "adjudicated_status": "not_measured",
        "blind_case_count": 0,
        "blind_status": "not_measured",
        "prevalence_case_count": 0,
        "prevalence_status": "not_measured",
        "parser_risk_status": "measured",
        "parser_risk_case_count": len(parser_risk_case_ids),
        "parser_risk_case_ids": list(parser_risk_case_ids),
        "parser_risk_source_role": "behavior_snapshot_not_truth",
        "evidence_level": "development_only",
        "qualification_status": "not_qualified",
        "qualification_reasons": [
            "adjudicated_truth_missing",
            "independent_blind_holdout_missing",
            "prevalence_sample_missing",
            "truth_is_provisional",
        ],
        "production_qualified": False,
    }


def build_tag_truth_coverage_report(
    *,
    tags_config_path: str | Path,
    dimensions_config_path: str | Path,
    feature_golden_manifest_path: str | Path,
    development_manifest_path: str | Path,
    draft_manifest_path: str | Path,
    draft_baseline_path: str | Path,
) -> dict[str, object]:
    feature_config = load_feature_config(tags_config_path, dimensions_config_path)
    golden = load_golden_suite(feature_golden_manifest_path)
    development = load_tag_retrieval_truth(development_manifest_path)
    draft = load_tag_truth_suite(draft_manifest_path)
    _, draft_baseline_fingerprint, parser_risk_case_ids = _load_draft_behavior_baseline(
        draft_baseline_path,
        draft,
    )
    formal_tag_ids = _validate_input_identity(
        feature_config,
        golden.tag_ids,
        golden.feature_config_version,
        development,
        draft,
    )
    synthetic_unit_count, synthetic_counts = _synthetic_counts(
        golden.cases,
        formal_tag_ids,
    )
    formal_rows = _formal_tag_rows(
        feature_config,
        formal_tag_ids,
        len(golden.cases),
        synthetic_unit_count,
        synthetic_counts,
        development,
    )
    draft_rows = [_draft_candidate_row(draft, parser_risk_case_ids)]

    report: dict[str, object] = {
        "schema_version": TAG_TRUTH_COVERAGE_SCHEMA_VERSION,
        "inputs": {
            "formal_feature_config": {
                "tag_schema_version": feature_config.tag_config.schema_version,
                "tag_config_version": feature_config.tag_config.version,
                "dimension_config_version": feature_config.dimension_config.version,
                "feature_config_fingerprint": feature_config.fingerprint,
            },
            "feature_routing_golden": {
                "schema_version": FEATURE_GOLDEN_SCHEMA_VERSION,
                "suite_id": golden.suite_id,
                "manifest_fingerprint": (f"feature-routing-golden:sha256:{golden.manifest_sha256}"),
            },
            "development_truth": {
                "schema_version": development.schema_version,
                "suite_id": development.suite_id,
                "truth_status": development.truth_status,
                "dataset_role": development.evaluation_boundary.dataset_role,
                "truth_fingerprint": tag_retrieval_truth_fingerprint(development),
            },
            "draft_truth": {
                "schema_version": draft.schema_version,
                "suite_id": draft.suite_id,
                "truth_status": draft.truth_status,
                "truth_fingerprint": tag_truth_suite_fingerprint(draft),
            },
            "draft_behavior_baseline": {
                "schema_version": TAG_TRUTH_BASELINE_SCHEMA_VERSION,
                "role": "behavior_snapshot_not_truth",
                "baseline_fingerprint": draft_baseline_fingerprint,
            },
        },
        "summary": {
            "formal_tag_count": len(formal_rows),
            "draft_candidate_count": len(draft_rows),
            "synthetic_only_formal_tag_count": sum(
                row["evidence_level"] == "synthetic_only" for row in formal_rows
            ),
            "development_only_formal_tag_count": sum(
                row["evidence_level"] == "development_only" for row in formal_rows
            ),
            "adjudicated_formal_tag_count": 0,
            "independent_blind_formal_tag_count": 0,
            "prevalence_measured_formal_tag_count": 0,
            "production_qualified_tag_count": 0,
            "overall_qualification_status": "not_qualified",
        },
        "formal_tags": formal_rows,
        "draft_candidates": draft_rows,
        "interpretation_constraints": [
            "synthetic Golden coverage proves a frozen routing contract, not real-code quality",
            "development labels are provisional and were exposed during candidate iteration",
            "legacy acceptance_holdout split names do not provide independent blind evidence",
            (
                "the Draft baseline is used only for bound runtime parser-risk observation, "
                "never Truth"
            ),
            "zero independent blind or prevalence cases means no Tag is production qualified",
        ],
    }
    report["coverage_fingerprint"] = tag_truth_coverage_fingerprint(report)
    return report


__all__ = [
    "TAG_TRUTH_COVERAGE_FINGERPRINT_PREFIX",
    "TAG_TRUTH_COVERAGE_SCHEMA_VERSION",
    "build_tag_truth_coverage_report",
    "tag_truth_coverage_fingerprint",
]
