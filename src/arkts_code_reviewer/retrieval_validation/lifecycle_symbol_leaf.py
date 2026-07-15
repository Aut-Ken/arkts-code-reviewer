from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from arkts_code_reviewer.feature_routing.config import (
    DEFAULT_DIMENSIONS_PATH,
    FeatureConfig,
    UnitSymbolLeafOwnerRoleTrigger,
    load_default_feature_config,
    load_feature_config,
)
from arkts_code_reviewer.feature_routing.models import (
    FEATURE_ROUTING_SCHEMA_VERSION,
    FEATURE_ROUTING_V2_SCHEMA_VERSION,
    FEATURE_ROUTING_V3_SCHEMA_VERSION,
)
from arkts_code_reviewer.feature_routing.owner_context import OwnerRole
from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
    TAG_RETRIEVAL_TRUTH_OBSERVATION_V2_SCHEMA_VERSION,
    TAG_RETRIEVAL_TRUTH_OBSERVATION_V3_SCHEMA_VERSION,
    TARGET_TAGS,
    TagRetrievalCrossTargetAdjudication,
    TagRetrievalTruthSuite,
    tag_retrieval_truth_fingerprint,
)

LIFECYCLE_TAG_ID = "has_lifecycle"
LIFECYCLE_SYMBOL_LEAF_CANDIDATE_VERSION = "tags-lifecycle-symbol-leaf-shadow-v1"
LIFECYCLE_SYMBOL_LEAF_CANDIDATE_FINGERPRINT = (
    "feature-config:sha256:f3782dbb88adb17953e611bebb60b1df0de249a735f6c00149ec33b0f7bd1790"
)
LIFECYCLE_OWNER_ROLE_CANDIDATE_VERSION = "tags-lifecycle-owner-role-shadow-v1"
LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT = (
    "feature-config:sha256:844418e3d7938c816fd3b64b62cdae3d1753d286d50a6a103406838ed6db01e7"
)
LIFECYCLE_EVALUATION_TRUTH_FINGERPRINT = (
    "tag-retrieval-truth:sha256:9d07e7e59ee7823b7dff2f5355ee33c4729f6fc6a9058d2d2edfa85c34bc8a7e"
)
LIFECYCLE_SYMBOL_LEAVES = (
    "aboutToAppear",
    "aboutToDisappear",
    "onBackPress",
    "onPageHide",
    "onPageShow",
    "onReady",
)
LIFECYCLE_OWNER_AWARE_EXACT_SYMBOL_LEAVES = (
    "aboutToAppear",
    "aboutToDisappear",
    "onBackPress",
    "onPageHide",
    "onPageShow",
)
_LIFECYCLE_OWNER_ROLE_BY_LEAF: dict[str, OwnerRole] = {
    "aboutToAppear": "arkui_custom_component",
    "aboutToDisappear": "arkui_custom_component",
    "onBackPress": "arkui_router_page",
    "onPageHide": "arkui_router_page",
    "onPageShow": "arkui_router_page",
}

_EXPECTED_LIFECYCLE_METRICS: dict[str, object] = {
    "positive_case_count": 7,
    "negative_case_count": 5,
    "true_positive": 7,
    "false_positive": 0,
    "false_negative": 0,
    "true_negative": 5,
    "precision": 1.0,
    "recall": 1.0,
}
_EXPECTED_LIFECYCLE_TARGET_ADDITIONS = (
    "TR-LIFE-001",
    "TR-LIFE-003",
    "TR-LIFE-005",
    "TR-LIFE-007",
    "TR-LIFE-009",
    "TR-LIFE-010",
    "TR-LIFE-012",
)
_EXPECTED_DECLARED_CO_TAG_ADDITIONS = ("TR-TIMER-008",)
_EXPECTED_ADJUDICATED_CROSS_TARGET_ADDITIONS = (
    "TR-NET-008",
    "TR-STATE-007",
    "TR-STATE-009",
    "TR-STATE-010",
    "TR-STATE-012",
    "TR-TIMER-004",
    "TR-TIMER-010",
)
_EXPECTED_ALL_LIFECYCLE_ADDITIONS = tuple(
    sorted(
        (
            *_EXPECTED_LIFECYCLE_TARGET_ADDITIONS,
            *_EXPECTED_DECLARED_CO_TAG_ADDITIONS,
            *_EXPECTED_ADJUDICATED_CROSS_TARGET_ADDITIONS,
        )
    )
)
_EXPECTED_DEVELOPMENT_REGRESSION_METRICS: dict[str, object] = {
    "positive_case_count": 15,
    "negative_case_count": 5,
    "true_positive": 15,
    "false_positive": 0,
    "false_negative": 0,
    "true_negative": 5,
    "precision": 1.0,
    "recall": 1.0,
}
_IMMUTABLE_CASE_FIELDS = (
    "case_id",
    "source_alias",
    "changed_line",
    "target_tag",
    "split",
    "stratum",
    "review_status",
    "evidence_lines",
    "expected_exact_tag",
    "expected_routing_tag",
    "required_co_tags",
    "unit_id",
    "unit_kind",
    "unit_symbol",
    "expected_source_span",
    "actual_source_span",
    "exact_symbols",
    "file_hint_symbols",
    "parser_layer",
    "parser_error_nodes",
    "parser_missing_nodes",
    "file_diagnostics",
    "scope_diagnostics",
)


def load_lifecycle_symbol_leaf_candidate_config(
    tags_path: str | Path,
) -> FeatureConfig:
    base = load_default_feature_config()
    candidate = load_feature_config(tags_path, DEFAULT_DIMENSIONS_PATH)
    if candidate.tag_config.schema_version != "tag-config-v3":
        raise ValueError("lifecycle symbol-leaf candidate must use tag-config-v3")
    if candidate.tag_config.version != LIFECYCLE_SYMBOL_LEAF_CANDIDATE_VERSION:
        raise ValueError("lifecycle symbol-leaf candidate version drift")
    if candidate.fingerprint != LIFECYCLE_SYMBOL_LEAF_CANDIDATE_FINGERPRINT:
        raise ValueError("lifecycle symbol-leaf candidate fingerprint drift")
    if candidate.dimension_config != base.dimension_config:
        raise ValueError("lifecycle symbol-leaf candidate changed Dimension config")
    if tuple(candidate.tags_by_id) != tuple(base.tags_by_id):
        raise ValueError("lifecycle symbol-leaf candidate changed the Tag ID set")

    changed_tag_ids = tuple(
        tag_id
        for tag_id in base.tags_by_id
        if candidate.tags_by_id[tag_id] != base.tags_by_id[tag_id]
    )
    if changed_tag_ids != (LIFECYCLE_TAG_ID,):
        raise ValueError("lifecycle symbol-leaf candidate may change only has_lifecycle")

    base_definition = base.tags_by_id[LIFECYCLE_TAG_ID]
    candidate_definition = candidate.tags_by_id[LIFECYCLE_TAG_ID]
    if (
        candidate_definition.status != base_definition.status
        or candidate_definition.description != base_definition.description
    ):
        raise ValueError("lifecycle symbol-leaf candidate changed Tag status or description")
    expected_triggers = base_definition.triggers.model_copy(
        update={
            "any_symbol": (),
            "any_symbol_leaf": base_definition.triggers.any_symbol,
        }
    )
    if candidate_definition.triggers != expected_triggers:
        raise ValueError("lifecycle symbol-leaf candidate changed more than the symbol operator")
    return candidate


def load_lifecycle_owner_role_candidate_config(
    tags_path: str | Path,
) -> FeatureConfig:
    base = load_default_feature_config()
    candidate = load_feature_config(tags_path, DEFAULT_DIMENSIONS_PATH)
    if candidate.tag_config.schema_version != "tag-config-v4":
        raise ValueError("lifecycle owner-role candidate must use tag-config-v4")
    if candidate.tag_config.version != LIFECYCLE_OWNER_ROLE_CANDIDATE_VERSION:
        raise ValueError("lifecycle owner-role candidate version drift")
    if candidate.fingerprint != LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT:
        raise ValueError("lifecycle owner-role candidate fingerprint drift")
    if candidate.dimension_config != base.dimension_config:
        raise ValueError("lifecycle owner-role candidate changed Dimension config")
    if tuple(candidate.tags_by_id) != tuple(base.tags_by_id):
        raise ValueError("lifecycle owner-role candidate changed the Tag ID set")

    changed_tag_ids = tuple(
        tag_id
        for tag_id in base.tags_by_id
        if candidate.tags_by_id[tag_id] != base.tags_by_id[tag_id]
    )
    if changed_tag_ids != (LIFECYCLE_TAG_ID,):
        raise ValueError("lifecycle owner-role candidate may change only has_lifecycle")

    base_definition = base.tags_by_id[LIFECYCLE_TAG_ID]
    candidate_definition = candidate.tags_by_id[LIFECYCLE_TAG_ID]
    if (
        candidate_definition.status != base_definition.status
        or candidate_definition.description
        != "代码包含具有 ArkUI owner role 的组件或页面生命周期符号"
    ):
        raise ValueError("lifecycle owner-role candidate changed Tag status or description")
    expected_triggers = base_definition.triggers.model_copy(
        update={
            "any_symbol": (),
            "any_unit_symbol_leaf_with_owner_role": tuple(
                UnitSymbolLeafOwnerRoleTrigger(
                    symbol_leaf=symbol_leaf,
                    owner_role=owner_role,
                )
                for symbol_leaf, owner_role in sorted(_LIFECYCLE_OWNER_ROLE_BY_LEAF.items())
            ),
            "any_file_symbol_leaf": base_definition.triggers.any_symbol,
        }
    )
    if candidate_definition.triggers != expected_triggers:
        raise ValueError("lifecycle owner-role candidate changed more than owner-aware routing")
    return candidate


def build_lifecycle_symbol_leaf_comparison(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    truth_suite: TagRetrievalTruthSuite,
) -> dict[str, object]:
    if tag_retrieval_truth_fingerprint(truth_suite) != LIFECYCLE_EVALUATION_TRUTH_FINGERPRINT:
        raise ValueError("lifecycle evaluation Truth suite fingerprint drift")
    base_config = load_default_feature_config()
    candidate_schema = candidate.get("tags_config_schema_version")
    candidate_kind: str
    observation_schema_version: str
    candidate_fingerprint: str
    candidate_version: str
    candidate_routing_schema: str
    if candidate_schema == "tag-config-v3":
        candidate_kind = "legacy_symbol_leaf_development_regression"
        observation_schema_version = TAG_RETRIEVAL_TRUTH_OBSERVATION_V2_SCHEMA_VERSION
        candidate_fingerprint = LIFECYCLE_SYMBOL_LEAF_CANDIDATE_FINGERPRINT
        candidate_version = LIFECYCLE_SYMBOL_LEAF_CANDIDATE_VERSION
        candidate_routing_schema = FEATURE_ROUTING_V2_SCHEMA_VERSION
    elif candidate_schema == "tag-config-v4":
        candidate_kind = "owner_aware_shadow"
        observation_schema_version = TAG_RETRIEVAL_TRUTH_OBSERVATION_V3_SCHEMA_VERSION
        candidate_fingerprint = LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT
        candidate_version = LIFECYCLE_OWNER_ROLE_CANDIDATE_VERSION
        candidate_routing_schema = FEATURE_ROUTING_V3_SCHEMA_VERSION
    else:
        raise ValueError("unsupported lifecycle candidate Tag config schema")
    _validate_observation_identity(
        baseline,
        role="baseline",
        observation_schema_version=observation_schema_version,
        feature_config_fingerprint=base_config.fingerprint,
        tags_config_schema_version="tag-config-v1",
        tags_config_version="tags-v1",
        feature_routing_schema_version=FEATURE_ROUTING_SCHEMA_VERSION,
    )
    _validate_observation_identity(
        candidate,
        role="candidate",
        observation_schema_version=observation_schema_version,
        feature_config_fingerprint=candidate_fingerprint,
        tags_config_schema_version=str(candidate_schema),
        tags_config_version=candidate_version,
        feature_routing_schema_version=candidate_routing_schema,
    )
    _validate_shared_observation_identity(baseline, candidate)

    require_profile_diagnostics = (
        observation_schema_version == TAG_RETRIEVAL_TRUTH_OBSERVATION_V3_SCHEMA_VERSION
    )
    baseline_rows = _validated_rows_by_case_id(
        baseline,
        role="baseline",
        require_profile_diagnostics=require_profile_diagnostics,
    )
    candidate_rows = _validated_rows_by_case_id(
        candidate,
        role="candidate",
        require_profile_diagnostics=require_profile_diagnostics,
    )
    if tuple(baseline_rows) != tuple(candidate_rows):
        raise ValueError("baseline and candidate observations contain different cases")
    _validate_truth_case_contract(baseline_rows, truth_suite)
    _validate_immutable_case_contract(baseline_rows, candidate_rows)

    baseline_mismatches = _string_sequence(
        baseline,
        "case_contract_mismatch_case_ids",
    )
    candidate_mismatches = _string_sequence(
        candidate,
        "case_contract_mismatch_case_ids",
    )
    resolved = tuple(sorted(set(baseline_mismatches) - set(candidate_mismatches)))
    introduced = tuple(sorted(set(candidate_mismatches) - set(baseline_mismatches)))

    case_deltas: list[dict[str, object]] = []
    changed_tag_ids: set[str] = set()
    lifecycle_target_additions: list[str] = []
    declared_co_tag_additions: list[str] = []
    adjudicated_positive_cross_target_lifecycle_additions: list[str] = []
    adjudicated_negative_cross_target_lifecycle_additions: list[str] = []
    unadjudicated_cross_target_lifecycle_additions: list[str] = []
    all_lifecycle_exact_additions: list[str] = []
    lifecycle_exact_removals: list[str] = []
    non_lifecycle_match_changed_case_ids: list[str] = []
    cross_target_adjudications: dict[tuple[str, str], TagRetrievalCrossTargetAdjudication] = {
        (adjudication.case_id, adjudication.tag_id): adjudication
        for adjudication in truth_suite.cross_target_tag_adjudications
    }
    for case_id in baseline_rows:
        before = baseline_rows[case_id]
        after = candidate_rows[case_id]
        before_exact = set(_string_sequence(before, "exact_tags"))
        after_exact = set(_string_sequence(after, "exact_tags"))
        before_routing = set(_string_sequence(before, "routing_tags"))
        after_routing = set(_string_sequence(after, "routing_tags"))
        changed_tag_ids.update(before_exact.symmetric_difference(after_exact))
        changed_tag_ids.update(before_routing.symmetric_difference(after_routing))

        lifecycle_added = LIFECYCLE_TAG_ID not in before_exact and LIFECYCLE_TAG_ID in after_exact
        if lifecycle_added:
            all_lifecycle_exact_additions.append(case_id)
            if after["target_tag"] == LIFECYCLE_TAG_ID:
                lifecycle_target_additions.append(case_id)
            elif LIFECYCLE_TAG_ID in _string_sequence(after, "required_co_tags"):
                declared_co_tag_additions.append(case_id)
            elif adjudication := cross_target_adjudications.get((case_id, LIFECYCLE_TAG_ID)):
                if adjudication.expected_exact_tag:
                    adjudicated_positive_cross_target_lifecycle_additions.append(case_id)
                else:
                    adjudicated_negative_cross_target_lifecycle_additions.append(case_id)
            else:
                unadjudicated_cross_target_lifecycle_additions.append(case_id)
        if LIFECYCLE_TAG_ID in before_exact and LIFECYCLE_TAG_ID not in after_exact:
            lifecycle_exact_removals.append(case_id)
        if _non_lifecycle_tag_matches(before) != _non_lifecycle_tag_matches(after):
            non_lifecycle_match_changed_case_ids.append(case_id)

        if (
            before_exact != after_exact
            or before_routing != after_routing
            or before["missing_required_co_tags"] != after["missing_required_co_tags"]
        ):
            case_deltas.append(
                {
                    "case_id": case_id,
                    "baseline_exact_tags": sorted(before_exact),
                    "candidate_exact_tags": sorted(after_exact),
                    "baseline_routing_tags": sorted(before_routing),
                    "candidate_routing_tags": sorted(after_routing),
                    "baseline_missing_required_co_tags": before["missing_required_co_tags"],
                    "candidate_missing_required_co_tags": after["missing_required_co_tags"],
                    "candidate_lifecycle_tag_matches": _lifecycle_tag_matches(after),
                }
            )

    lifecycle_rows = [
        row for row in candidate_rows.values() if row["target_tag"] == LIFECYCLE_TAG_ID
    ]
    metrics = _confusion_metrics(lifecycle_rows)
    metrics_by_legacy_split = {
        split: _confusion_metrics([row for row in lifecycle_rows if row["split"] == split])
        for split in ("calibration", "acceptance_holdout")
    }
    development_regression_rows = _lifecycle_development_regression_rows(
        candidate_rows,
        truth_suite,
    )
    development_regression_metrics = _confusion_metrics(development_regression_rows)
    development_regression_metrics_by_legacy_split = {
        split: _confusion_metrics(
            [row for row in development_regression_rows if row["split"] == split]
        )
        for split in ("calibration", "acceptance_holdout")
    }
    provenance_failures = tuple(
        sorted(
            case_id
            for case_id in all_lifecycle_exact_additions
            if not _has_lifecycle_unit_exact_provenance(
                candidate_rows[case_id],
                owner_aware=candidate_schema == "tag-config-v4",
            )
        )
    )
    trace_provenance_failures = tuple(
        case_id
        for case_id, row in candidate_rows.items()
        if not _has_lifecycle_trace_provenance(
            row,
            owner_aware=candidate_schema == "tag-config-v4",
        )
    )

    declared_gate_failures: list[str] = []
    if candidate_mismatches:
        declared_gate_failures.append("candidate_contract_mismatch")
    if introduced:
        declared_gate_failures.append("introduced_contract_mismatch")
    if changed_tag_ids - {LIFECYCLE_TAG_ID}:
        declared_gate_failures.append("non_lifecycle_tag_behavior_changed")
    if non_lifecycle_match_changed_case_ids:
        declared_gate_failures.append("non_lifecycle_match_provenance_changed")
    if metrics != _EXPECTED_LIFECYCLE_METRICS:
        declared_gate_failures.append("lifecycle_provisional_metrics_below_gate")
    if development_regression_metrics != _EXPECTED_DEVELOPMENT_REGRESSION_METRICS:
        declared_gate_failures.append("lifecycle_development_regression_metrics_below_gate")
    if (
        tuple(lifecycle_target_additions) != _EXPECTED_LIFECYCLE_TARGET_ADDITIONS
        or tuple(declared_co_tag_additions) != _EXPECTED_DECLARED_CO_TAG_ADDITIONS
        or tuple(adjudicated_positive_cross_target_lifecycle_additions)
        != _EXPECTED_ADJUDICATED_CROSS_TARGET_ADDITIONS
        or adjudicated_negative_cross_target_lifecycle_additions
        or unadjudicated_cross_target_lifecycle_additions
        or tuple(all_lifecycle_exact_additions) != _EXPECTED_ALL_LIFECYCLE_ADDITIONS
    ):
        declared_gate_failures.append("lifecycle_addition_classification_drift")
    if lifecycle_exact_removals:
        declared_gate_failures.append("lifecycle_exact_removal")
    if provenance_failures:
        declared_gate_failures.append("symbol_leaf_provenance_missing")
    if trace_provenance_failures:
        declared_gate_failures.append("lifecycle_trace_provenance_invalid")

    safety_blockers = [
        "truth_is_provisional",
        "development_regression_only",
        "independent_adjudicated_holdout_missing",
    ]
    if candidate_schema == "tag-config-v3":
        safety_blockers.append("ordinary_class_same_name_owner_not_distinguishable")
    if unadjudicated_cross_target_lifecycle_additions:
        safety_blockers.append("unadjudicated_cross_target_lifecycle_additions")
    evidence_gate_failures = list(dict.fromkeys((*declared_gate_failures, *safety_blockers)))

    return {
        "schema_version": (
            "lifecycle-owner-role-comparison-v1"
            if candidate_schema == "tag-config-v4"
            else "lifecycle-symbol-leaf-comparison-v3"
        ),
        "evaluation_status": "development_regression_not_activation_evidence",
        "truth_status": candidate["truth_status"],
        "candidate_kind": candidate_kind,
        "truth_suite_fingerprint": candidate["truth_suite_fingerprint"],
        "evaluation_boundary": truth_suite.evaluation_boundary.model_dump(mode="json"),
        "cross_target_tag_adjudications": [
            adjudication.model_dump(mode="json")
            for adjudication in truth_suite.cross_target_tag_adjudications
        ],
        "baseline_config": _config_identity(baseline),
        "candidate_config": _config_identity(candidate),
        "configuration_changed_tag_ids": [LIFECYCLE_TAG_ID],
        "observed_changed_tag_ids": sorted(changed_tag_ids),
        "baseline_by_tag": baseline["by_tag"],
        "candidate_by_tag": candidate["by_tag"],
        "candidate_lifecycle_target_case_metrics": metrics,
        "candidate_lifecycle_target_case_metrics_by_legacy_split": metrics_by_legacy_split,
        "development_regression_lifecycle_exact_cases": development_regression_rows,
        "development_regression_lifecycle_exact_metrics": development_regression_metrics,
        "development_regression_lifecycle_exact_metrics_by_legacy_split": (
            development_regression_metrics_by_legacy_split
        ),
        "baseline_contract_mismatch_case_ids": list(baseline_mismatches),
        "candidate_contract_mismatch_case_ids": list(candidate_mismatches),
        "resolved_contract_mismatch_case_ids": list(resolved),
        "introduced_contract_mismatch_case_ids": list(introduced),
        "lifecycle_target_addition_case_ids": lifecycle_target_additions,
        "declared_required_co_tag_lifecycle_addition_case_ids": (declared_co_tag_additions),
        "adjudicated_positive_cross_target_lifecycle_addition_case_ids": (
            adjudicated_positive_cross_target_lifecycle_additions
        ),
        "adjudicated_negative_cross_target_lifecycle_addition_case_ids": (
            adjudicated_negative_cross_target_lifecycle_additions
        ),
        "unadjudicated_cross_target_lifecycle_addition_case_ids": (
            unadjudicated_cross_target_lifecycle_additions
        ),
        "all_lifecycle_exact_addition_case_ids": all_lifecycle_exact_additions,
        "lifecycle_exact_removal_case_ids": lifecycle_exact_removals,
        "non_lifecycle_match_changed_case_ids": non_lifecycle_match_changed_case_ids,
        "symbol_leaf_provenance_failure_case_ids": list(provenance_failures),
        "lifecycle_trace_provenance_failure_case_ids": list(trace_provenance_failures),
        "declared_contract_gate": {
            "passed": not declared_gate_failures,
            "failures": declared_gate_failures,
        },
        "candidate_evidence_gate": {
            "passed": not evidence_gate_failures,
            "failures": evidence_gate_failures,
        },
        "quality_decision": {
            "activation_ready": False,
            "activation_failures": safety_blockers,
        },
        "case_deltas": case_deltas,
    }


def _validate_observation_identity(
    observation: Mapping[str, object],
    *,
    role: str,
    observation_schema_version: str,
    feature_config_fingerprint: str,
    tags_config_schema_version: str,
    tags_config_version: str,
    feature_routing_schema_version: str,
) -> None:
    expected = {
        "schema_version": observation_schema_version,
        "suite_id": "active-tag-retrieval-pilot-v1",
        "truth_status": "provisional",
        "feature_config_fingerprint": feature_config_fingerprint,
        "tags_config_schema_version": tags_config_schema_version,
        "tags_config_version": tags_config_version,
        "feature_routing_schema_version": feature_routing_schema_version,
        "source_count": 36,
        "case_count": 48,
        "parse_count": 36,
    }
    for key, expected_value in expected.items():
        if observation.get(key) != expected_value:
            raise ValueError(f"{role} observation {key} identity mismatch")
    truth_fingerprint = observation.get("truth_suite_fingerprint")
    if truth_fingerprint != LIFECYCLE_EVALUATION_TRUTH_FINGERPRINT:
        raise ValueError(f"{role} observation truth_suite_fingerprint identity mismatch")


def _validate_shared_observation_identity(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> None:
    shared_fields = (
        "suite_id",
        "truth_status",
        "truth_suite_fingerprint",
        "source_count",
        "case_count",
        "parse_count",
        "file_diagnostic_case_counts",
        "scope_diagnostic_case_counts",
        "parser_risk_case_ids",
    )
    for key in shared_fields:
        if key not in baseline or key not in candidate:
            raise ValueError(f"baseline/candidate observations require {key}")
        if baseline[key] != candidate[key]:
            raise ValueError(f"baseline/candidate observation {key} mismatch")


def _validate_truth_case_contract(
    rows: Mapping[str, Mapping[str, object]],
    truth_suite: TagRetrievalTruthSuite,
) -> None:
    truth_cases = {case.case_id: case for case in truth_suite.cases}
    if tuple(rows) != tuple(truth_cases):
        raise ValueError("observation cases do not match the lifecycle evaluation Truth suite")
    for case_id, case in truth_cases.items():
        expected = {
            "case_id": case.case_id,
            "source_alias": case.source_alias,
            "changed_line": case.changed_line,
            "target_tag": case.target_tag,
            "split": case.split,
            "stratum": case.stratum,
            "review_status": case.review_status,
            "evidence_lines": list(case.evidence_lines),
            "expected_exact_tag": case.expected_exact_tag,
            "expected_routing_tag": case.expected_routing_tag,
            "required_co_tags": list(case.required_co_tags),
            "unit_kind": case.expected_unit_kind,
            "unit_symbol": case.expected_unit_symbol,
            "expected_source_span": case.expected_source_span.model_dump(mode="json"),
        }
        row = rows[case_id]
        for key, expected_value in expected.items():
            if row[key] != expected_value:
                raise ValueError(f"observation row does not match Truth suite: {case_id}:{key}")


def _validated_rows_by_case_id(
    observation: Mapping[str, object],
    *,
    role: str,
    require_profile_diagnostics: bool,
) -> dict[str, Mapping[str, object]]:
    rows = _rows_by_case_id(observation)
    if observation.get("case_count") != len(rows):
        raise ValueError(f"{role} observation case_count does not match rows")
    for row in rows.values():
        _validate_observation_row(
            row,
            role=role,
            require_profile_diagnostics=require_profile_diagnostics,
        )
    if require_profile_diagnostics:
        expected_profile_diagnostic_counts = dict(
            sorted(
                Counter(
                    diagnostic
                    for row in rows.values()
                    for diagnostic in _string_sequence(row, "profile_diagnostics")
                ).items()
            )
        )
        if observation.get("profile_diagnostic_case_counts") != (
            expected_profile_diagnostic_counts
        ):
            raise ValueError(
                f"{role} observation profile_diagnostic_case_counts does not match rows"
            )
        expected_owner_context_abstains = [
            case_id
            for case_id, row in rows.items()
            if any(
                diagnostic.startswith("owner_context_")
                for diagnostic in _string_sequence(row, "profile_diagnostics")
            )
        ]
        if observation.get("owner_context_abstain_case_ids") != (expected_owner_context_abstains):
            raise ValueError(
                f"{role} observation owner_context_abstain_case_ids does not match rows"
            )

    expected_mismatches = {
        "exact_mismatch_case_ids": [
            case_id for case_id, row in rows.items() if row["exact_matches_truth"] is False
        ],
        "routing_mismatch_case_ids": [
            case_id for case_id, row in rows.items() if row["routing_matches_truth"] is False
        ],
        "co_tag_mismatch_case_ids": [
            case_id for case_id, row in rows.items() if bool(row["missing_required_co_tags"])
        ],
        "case_contract_mismatch_case_ids": [
            case_id
            for case_id, row in rows.items()
            if row["exact_matches_truth"] is False
            or row["routing_matches_truth"] is False
            or bool(row["missing_required_co_tags"])
        ],
    }
    for key, expected in expected_mismatches.items():
        if list(_string_sequence(observation, key)) != expected:
            raise ValueError(f"{role} observation {key} does not match rows")

    expected_by_tag, expected_by_tag_and_split = _summaries(rows)
    if observation.get("by_tag") != expected_by_tag:
        raise ValueError(f"{role} observation by_tag does not match rows")
    if observation.get("by_tag_and_split") != expected_by_tag_and_split:
        raise ValueError(f"{role} observation by_tag_and_split does not match rows")
    return rows


def _validate_observation_row(
    row: Mapping[str, object],
    *,
    role: str,
    require_profile_diagnostics: bool,
) -> None:
    required_fields = {
        *_IMMUTABLE_CASE_FIELDS,
        "actual_exact_tag",
        "actual_routing_tag",
        "exact_matches_truth",
        "routing_matches_truth",
        "missing_required_co_tags",
        "exact_tags",
        "routing_tags",
        "tag_matches",
    }
    missing = sorted(required_fields - set(row))
    if missing:
        raise ValueError(f"{role} observation row missing fields: {missing!r}")
    if require_profile_diagnostics:
        _sorted_string_sequence(row, "profile_diagnostics")

    for key in (
        "expected_exact_tag",
        "actual_exact_tag",
        "expected_routing_tag",
        "actual_routing_tag",
        "exact_matches_truth",
        "routing_matches_truth",
    ):
        if not isinstance(row[key], bool):
            raise ValueError(f"{role} observation row {key} must be bool")

    target_tag = row["target_tag"]
    if not isinstance(target_tag, str) or not target_tag:
        raise ValueError(f"{role} observation row target_tag must be a string")
    required_co_tags = set(_sorted_string_sequence(row, "required_co_tags"))
    exact_tags = set(_sorted_string_sequence(row, "exact_tags"))
    routing_tags = set(_sorted_string_sequence(row, "routing_tags"))
    missing_co_tags = set(_sorted_string_sequence(row, "missing_required_co_tags"))
    _sorted_string_sequence(row, "exact_symbols")
    _sorted_string_sequence(row, "file_hint_symbols")

    if (target_tag in exact_tags) != row["actual_exact_tag"]:
        raise ValueError(f"{role} observation row actual_exact_tag is inconsistent")
    if (target_tag in routing_tags) != row["actual_routing_tag"]:
        raise ValueError(f"{role} observation row actual_routing_tag is inconsistent")
    if missing_co_tags != required_co_tags - exact_tags:
        raise ValueError(f"{role} observation row missing_required_co_tags is inconsistent")
    if row["exact_matches_truth"] != (row["actual_exact_tag"] == row["expected_exact_tag"]):
        raise ValueError(f"{role} observation row exact_matches_truth is inconsistent")
    if row["routing_matches_truth"] != (row["actual_routing_tag"] == row["expected_routing_tag"]):
        raise ValueError(f"{role} observation row routing_matches_truth is inconsistent")

    matches = _tag_matches(row)
    exact_from_matches = {
        str(match["tag_id"])
        for match in matches
        if match.get("status") == "Active" and match.get("scope") == "unit_exact"
    }
    routing_from_matches = {
        str(match["tag_id"])
        for match in matches
        if match.get("status") == "Active" and match.get("scope") == "file_hint"
    }
    if exact_from_matches != exact_tags:
        raise ValueError(f"{role} observation row exact_tags do not match tag_matches")
    if routing_from_matches != routing_tags:
        raise ValueError(f"{role} observation row routing_tags do not match tag_matches")


def _validate_immutable_case_contract(
    baseline_rows: Mapping[str, Mapping[str, object]],
    candidate_rows: Mapping[str, Mapping[str, object]],
) -> None:
    for case_id, before in baseline_rows.items():
        after = candidate_rows[case_id]
        for key in _IMMUTABLE_CASE_FIELDS:
            if before[key] != after[key]:
                raise ValueError(f"baseline/candidate immutable case field drift: {case_id}:{key}")


def _rows_by_case_id(
    observation: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    raw_rows = observation.get("cases")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, str | bytes):
        raise ValueError("observation must include per-case rows")
    rows: dict[str, Mapping[str, object]] = {}
    case_ids: list[str] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, Mapping):
            raise ValueError("observation case rows must be mappings")
        case_id = raw_row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("observation case row requires case_id")
        if case_id in rows:
            raise ValueError("observation contains duplicate case_id")
        case_ids.append(case_id)
        rows[case_id] = cast(Mapping[str, object], raw_row)
    if case_ids != sorted(case_ids):
        raise ValueError("observation cases must use stable case_id order")
    return rows


def _string_sequence(row: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = row.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError(f"{key} must be a sequence")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must contain strings")
    return tuple(cast(Sequence[str], value))


def _sorted_string_sequence(row: Mapping[str, object], key: str) -> tuple[str, ...]:
    values = _string_sequence(row, key)
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{key} must be sorted and unique")
    return values


def _tag_matches(row: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw_matches = row.get("tag_matches")
    if not isinstance(raw_matches, Sequence) or isinstance(raw_matches, str | bytes):
        raise ValueError("tag_matches must be a sequence")
    matches: list[Mapping[str, object]] = []
    for raw_match in raw_matches:
        if not isinstance(raw_match, Mapping):
            raise ValueError("tag_matches must contain mappings")
        if not isinstance(raw_match.get("tag_id"), str):
            raise ValueError("tag match requires tag_id")
        if raw_match.get("status") not in {"Active", "Draft"}:
            raise ValueError("tag match has invalid status")
        if raw_match.get("scope") not in {"unit_exact", "file_hint"}:
            raise ValueError("tag match has invalid scope")
        raw_signals = raw_match.get("signals")
        if not isinstance(raw_signals, Sequence) or isinstance(
            raw_signals,
            str | bytes,
        ):
            raise ValueError("tag match signals must be a sequence")
        if not raw_signals or any(not isinstance(signal, Mapping) for signal in raw_signals):
            raise ValueError("tag match signals must contain mappings")
        matches.append(cast(Mapping[str, object], raw_match))
    return tuple(matches)


def _non_lifecycle_tag_matches(
    row: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    return tuple(match for match in _tag_matches(row) if match["tag_id"] != LIFECYCLE_TAG_ID)


def _lifecycle_tag_matches(row: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [match for match in _tag_matches(row) if match["tag_id"] == LIFECYCLE_TAG_ID]


def _summaries(
    rows: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, dict[str, int]]]]:
    def summarize(selected: list[Mapping[str, object]]) -> dict[str, int]:
        return {
            "case_count": len(selected),
            "expected_exact_positive": sum(row["expected_exact_tag"] is True for row in selected),
            "actual_exact_positive": sum(row["actual_exact_tag"] is True for row in selected),
            "exact_mismatch_count": sum(row["exact_matches_truth"] is False for row in selected),
            "routing_mismatch_count": sum(
                row["routing_matches_truth"] is False for row in selected
            ),
            "co_tag_mismatch_count": sum(bool(row["missing_required_co_tags"]) for row in selected),
            "case_contract_mismatch_count": sum(
                row["exact_matches_truth"] is False
                or row["routing_matches_truth"] is False
                or bool(row["missing_required_co_tags"])
                for row in selected
            ),
        }

    by_tag: dict[str, dict[str, int]] = {}
    by_tag_and_split: dict[str, dict[str, dict[str, int]]] = {}
    for tag_id in TARGET_TAGS:
        tagged = [row for row in rows.values() if row["target_tag"] == tag_id]
        by_tag[tag_id] = summarize(tagged)
        by_tag_and_split[tag_id] = {
            split: summarize([row for row in tagged if row["split"] == split])
            for split in ("calibration", "acceptance_holdout")
        }
    return by_tag, by_tag_and_split


def _lifecycle_development_regression_rows(
    rows: Mapping[str, Mapping[str, object]],
    truth_suite: TagRetrievalTruthSuite,
) -> list[dict[str, object]]:
    cross_target_expectations = {
        adjudication.case_id: adjudication.expected_exact_tag
        for adjudication in truth_suite.cross_target_tag_adjudications
        if adjudication.tag_id == LIFECYCLE_TAG_ID
    }
    selected: list[dict[str, object]] = []
    for case in truth_suite.cases:
        expected_exact: bool | None = None
        label_source: str | None = None
        if case.target_tag == LIFECYCLE_TAG_ID:
            expected_exact = case.expected_exact_tag
            label_source = "primary_target_truth"
        elif LIFECYCLE_TAG_ID in case.required_co_tags:
            expected_exact = True
            label_source = "required_co_tag_truth"
        elif case.case_id in cross_target_expectations:
            expected_exact = cross_target_expectations[case.case_id]
            label_source = "cross_target_product_decision"
        if expected_exact is None or label_source is None:
            continue
        row = rows[case.case_id]
        selected.append(
            {
                "case_id": case.case_id,
                "split": case.split,
                "label_source": label_source,
                "expected_exact_tag": expected_exact,
                "actual_exact_tag": LIFECYCLE_TAG_ID in _sorted_string_sequence(row, "exact_tags"),
            }
        )
    return selected


def _confusion_metrics(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    tp = sum(row["expected_exact_tag"] is True and row["actual_exact_tag"] is True for row in rows)
    fn = sum(row["expected_exact_tag"] is True and row["actual_exact_tag"] is False for row in rows)
    fp = sum(row["expected_exact_tag"] is False and row["actual_exact_tag"] is True for row in rows)
    tn = sum(
        row["expected_exact_tag"] is False and row["actual_exact_tag"] is False for row in rows
    )
    return {
        "positive_case_count": tp + fn,
        "negative_case_count": fp + tn,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": None if tp + fp == 0 else tp / (tp + fp),
        "recall": None if tp + fn == 0 else tp / (tp + fn),
    }


def _has_lifecycle_unit_exact_provenance(
    row: Mapping[str, object],
    *,
    owner_aware: bool,
) -> bool:
    return _has_lifecycle_scope_provenance(
        row,
        "unit_exact",
        owner_aware=owner_aware,
    )


def _has_lifecycle_trace_provenance(
    row: Mapping[str, object],
    *,
    owner_aware: bool,
) -> bool:
    matches = _lifecycle_tag_matches(row)
    if not matches:
        return True
    scopes = [match.get("scope") for match in matches]
    if len(scopes) != len(set(scopes)):
        return False
    return all(
        scope in {"unit_exact", "file_hint"}
        and _has_lifecycle_scope_provenance(
            row,
            str(scope),
            owner_aware=owner_aware,
        )
        for scope in scopes
    )


def _has_lifecycle_scope_provenance(
    row: Mapping[str, object],
    scope: str,
    *,
    owner_aware: bool,
) -> bool:
    fact_key = "exact_symbols" if scope == "unit_exact" else "file_hint_symbols"
    scoped_symbols = set(_string_sequence(row, fact_key))
    matches = [
        match
        for match in _tag_matches(row)
        if match.get("tag_id") == LIFECYCLE_TAG_ID
        and match.get("status") == "Active"
        and match.get("scope") == scope
    ]
    if len(matches) != 1:
        return False
    raw_signals = matches[0]["signals"]
    assert isinstance(raw_signals, Sequence)
    if not raw_signals:
        return False
    for signal in raw_signals:
        if not isinstance(signal, Mapping):
            return False
        value = signal.get("value")
        normalized = signal.get("normalized_value")
        if (
            signal.get("kind") != "symbols"
            or not isinstance(value, str)
            or not isinstance(normalized, str)
            or value not in scoped_symbols
            or value.rsplit(".", 1)[-1] != normalized
        ):
            return False
        if not owner_aware:
            if (
                set(signal) != {"kind", "value", "operator", "normalized_value"}
                or signal.get("operator") != "any_symbol_leaf"
                or normalized not in LIFECYCLE_SYMBOL_LEAVES
            ):
                return False
            continue
        if scope == "file_hint":
            if (
                set(signal) != {"kind", "value", "operator", "normalized_value"}
                or signal.get("operator") != "any_file_symbol_leaf"
                or normalized not in LIFECYCLE_SYMBOL_LEAVES
            ):
                return False
            continue
        if not _has_owner_role_signal_provenance(signal, normalized):
            return False
    return True


def _has_owner_role_signal_provenance(
    signal: Mapping[str, object],
    normalized: str,
) -> bool:
    required_fields = {
        "kind",
        "value",
        "operator",
        "normalized_value",
        "owner_role",
        "symbol_occurrence_id",
        "direct_owner_declaration_id",
        "enclosing_owner_declaration_id",
        "role_evidence_occurrence_ids",
    }
    if (
        set(signal) != required_fields
        or signal.get("operator") != "any_unit_symbol_leaf_with_owner_role"
        or normalized not in LIFECYCLE_OWNER_AWARE_EXACT_SYMBOL_LEAVES
        or signal.get("owner_role") != _LIFECYCLE_OWNER_ROLE_BY_LEAF[normalized]
    ):
        return False
    symbol_occurrence_id = signal.get("symbol_occurrence_id")
    direct_owner_id = signal.get("direct_owner_declaration_id")
    enclosing_owner_id = signal.get("enclosing_owner_declaration_id")
    if (
        not isinstance(symbol_occurrence_id, str)
        or not symbol_occurrence_id.startswith("occurrence:")
        or not isinstance(direct_owner_id, str)
        or not direct_owner_id.startswith("declaration:")
        or not isinstance(enclosing_owner_id, str)
        or not enclosing_owner_id.startswith("declaration:")
        or direct_owner_id == enclosing_owner_id
    ):
        return False
    role_evidence_ids = signal.get("role_evidence_occurrence_ids")
    if (
        not isinstance(role_evidence_ids, Sequence)
        or isinstance(role_evidence_ids, str | bytes)
        or not role_evidence_ids
        or any(
            not isinstance(item, str) or not item.startswith("occurrence:")
            for item in role_evidence_ids
        )
        or list(role_evidence_ids) != sorted(set(role_evidence_ids))
    ):
        return False
    return True


def _config_identity(observation: Mapping[str, object]) -> dict[str, object]:
    return {
        "feature_config_fingerprint": observation["feature_config_fingerprint"],
        "tags_config_schema_version": observation["tags_config_schema_version"],
        "tags_config_version": observation["tags_config_version"],
        "feature_routing_schema_version": observation["feature_routing_schema_version"],
    }


__all__ = [
    "LIFECYCLE_EVALUATION_TRUTH_FINGERPRINT",
    "LIFECYCLE_OWNER_AWARE_EXACT_SYMBOL_LEAVES",
    "LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT",
    "LIFECYCLE_OWNER_ROLE_CANDIDATE_VERSION",
    "LIFECYCLE_SYMBOL_LEAF_CANDIDATE_FINGERPRINT",
    "LIFECYCLE_SYMBOL_LEAF_CANDIDATE_VERSION",
    "LIFECYCLE_SYMBOL_LEAVES",
    "LIFECYCLE_TAG_ID",
    "build_lifecycle_symbol_leaf_comparison",
    "load_lifecycle_owner_role_candidate_config",
    "load_lifecycle_symbol_leaf_candidate_config",
]
