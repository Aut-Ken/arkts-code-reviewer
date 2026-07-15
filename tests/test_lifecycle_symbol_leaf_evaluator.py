from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.feature_routing.models import (
    FEATURE_ROUTING_SCHEMA_VERSION,
    FEATURE_ROUTING_V3_SCHEMA_VERSION,
)
from arkts_code_reviewer.retrieval_validation.lifecycle_symbol_leaf import (
    LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT,
    LIFECYCLE_OWNER_ROLE_CANDIDATE_VERSION,
    build_lifecycle_symbol_leaf_comparison,
)
from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
    TAG_RETRIEVAL_TRUTH_OBSERVATION_V3_SCHEMA_VERSION,
    TARGET_TAGS,
    TagRetrievalTruthSuite,
    load_tag_retrieval_truth,
    tag_retrieval_truth_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
TRUTH_MANIFEST = ROOT / "tests/evaluation/tag_retrieval/manifest.json"


def _load_evaluator_cli() -> ModuleType:
    path = ROOT / "tools/evaluate_lifecycle_symbol_leaf_candidate.py"
    spec = importlib.util.spec_from_file_location(
        "test_evaluate_lifecycle_symbol_leaf_candidate",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluator_cli = _load_evaluator_cli()

LIFECYCLE_TARGET_ADDITIONS = (
    "TR-LIFE-001",
    "TR-LIFE-003",
    "TR-LIFE-005",
    "TR-LIFE-007",
    "TR-LIFE-009",
    "TR-LIFE-010",
    "TR-LIFE-012",
)
DECLARED_CO_TAG_ADDITIONS = ("TR-TIMER-008",)
ADJUDICATED_CROSS_TARGET_ADDITIONS = (
    "TR-NET-008",
    "TR-STATE-007",
    "TR-STATE-009",
    "TR-STATE-010",
    "TR-STATE-012",
    "TR-TIMER-004",
    "TR-TIMER-010",
)
ALL_LIFECYCLE_ADDITIONS = tuple(
    sorted(
        (
            *LIFECYCLE_TARGET_ADDITIONS,
            *DECLARED_CO_TAG_ADDITIONS,
            *ADJUDICATED_CROSS_TARGET_ADDITIONS,
        )
    )
)


def _truth_suite() -> TagRetrievalTruthSuite:
    return load_tag_retrieval_truth(TRUTH_MANIFEST)


def _legacy_match(tag_id: str, scope: str) -> dict[str, object]:
    return {
        "tag_id": tag_id,
        "status": "Active",
        "scope": scope,
        "signals": [{"kind": "syntax", "value": f"fixture_{tag_id}"}],
    }


def _lifecycle_match(scope: str, symbol: str) -> dict[str, object]:
    normalized = symbol.rsplit(".", 1)[-1]
    signal: dict[str, object]
    if scope == "file_hint":
        signal = {
            "kind": "symbols",
            "value": symbol,
            "operator": "any_file_symbol_leaf",
            "normalized_value": normalized,
        }
    else:
        signal = {
            "kind": "symbols",
            "value": symbol,
            "operator": "any_unit_symbol_leaf_with_owner_role",
            "normalized_value": normalized,
            "owner_role": "arkui_custom_component",
            "symbol_occurrence_id": f"occurrence:sha256:{'a' * 64}",
            "direct_owner_declaration_id": f"declaration:sha256:{'b' * 64}",
            "enclosing_owner_declaration_id": f"declaration:sha256:{'c' * 64}",
            "role_evidence_occurrence_ids": [f"occurrence:sha256:{'d' * 64}"],
        }
    return {
        "tag_id": "has_lifecycle",
        "status": "Active",
        "scope": scope,
        "signals": [signal],
    }


def _matches(
    exact_tags: set[str],
    routing_tags: set[str],
    *,
    candidate: bool,
    lifecycle_symbol: str,
) -> list[dict[str, object]]:
    matches = []
    for tag_id in exact_tags:
        if candidate and tag_id == "has_lifecycle":
            matches.append(_lifecycle_match("unit_exact", lifecycle_symbol))
        else:
            matches.append(_legacy_match(tag_id, "unit_exact"))
    for tag_id in routing_tags:
        if candidate and tag_id == "has_lifecycle":
            matches.append(_lifecycle_match("file_hint", lifecycle_symbol))
        else:
            matches.append(_legacy_match(tag_id, "file_hint"))
    return sorted(
        matches,
        key=lambda match: (str(match["tag_id"]), str(match["scope"])),
    )


def _row(case: Any, *, candidate: bool) -> dict[str, object]:
    lifecycle_symbol = f"Owner{case.case_id.replace('-', '')}.aboutToAppear"
    exact_tags = set(case.required_co_tags)
    if not candidate:
        exact_tags.discard("has_lifecycle")
    if case.expected_exact_tag and not (
        not candidate and case.case_id in LIFECYCLE_TARGET_ADDITIONS
    ):
        exact_tags.add(case.target_tag)
    routing_tags = {case.target_tag} if case.expected_routing_tag else set()
    if candidate and case.case_id in ALL_LIFECYCLE_ADDITIONS:
        exact_tags.add("has_lifecycle")

    actual_exact = case.target_tag in exact_tags
    actual_routing = case.target_tag in routing_tags
    missing_co_tags = sorted(set(case.required_co_tags) - exact_tags)
    exact_symbols = [lifecycle_symbol] if case.case_id in ALL_LIFECYCLE_ADDITIONS else []
    file_hint_symbols = (
        [lifecycle_symbol]
        if case.case_id in ALL_LIFECYCLE_ADDITIONS or "has_lifecycle" in routing_tags
        else []
    )
    return {
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
        "exact_tags": sorted(exact_tags),
        "routing_tags": sorted(routing_tags),
        "exact_symbols": exact_symbols,
        "file_hint_symbols": file_hint_symbols,
        "tag_matches": _matches(
            exact_tags,
            routing_tags,
            candidate=candidate,
            lifecycle_symbol=lifecycle_symbol,
        ),
        "unit_id": f"unit:{case.case_id}",
        "unit_kind": case.expected_unit_kind,
        "unit_symbol": case.expected_unit_symbol,
        "expected_source_span": case.expected_source_span.model_dump(mode="json"),
        "actual_source_span": case.expected_source_span.model_dump(mode="json"),
        "parser_layer": "L1",
        "parser_error_nodes": 0,
        "parser_missing_nodes": 0,
        "file_diagnostics": [],
        "scope_diagnostics": [],
        "profile_diagnostics": [],
    }


def _summarize(rows: list[dict[str, object]]) -> dict[str, int]:
    return {
        "case_count": len(rows),
        "expected_exact_positive": sum(row["expected_exact_tag"] is True for row in rows),
        "actual_exact_positive": sum(row["actual_exact_tag"] is True for row in rows),
        "exact_mismatch_count": sum(row["exact_matches_truth"] is False for row in rows),
        "routing_mismatch_count": sum(row["routing_matches_truth"] is False for row in rows),
        "co_tag_mismatch_count": sum(bool(row["missing_required_co_tags"]) for row in rows),
        "case_contract_mismatch_count": sum(
            row["exact_matches_truth"] is False
            or row["routing_matches_truth"] is False
            or bool(row["missing_required_co_tags"])
            for row in rows
        ),
    }


def _observation(*, candidate: bool) -> dict[str, object]:
    truth = _truth_suite()
    rows = [_row(case, candidate=candidate) for case in truth.cases]
    rows.sort(key=lambda row: str(row["case_id"]))
    by_tag: dict[str, dict[str, int]] = {}
    by_tag_and_split: dict[str, dict[str, dict[str, int]]] = {}
    for tag_id in TARGET_TAGS:
        tagged = [row for row in rows if row["target_tag"] == tag_id]
        by_tag[tag_id] = _summarize(tagged)
        by_tag_and_split[tag_id] = {
            split: _summarize([row for row in tagged if row["split"] == split])
            for split in ("calibration", "acceptance_holdout")
        }
    exact_mismatches = [str(row["case_id"]) for row in rows if row["exact_matches_truth"] is False]
    routing_mismatches = [
        str(row["case_id"]) for row in rows if row["routing_matches_truth"] is False
    ]
    co_tag_mismatches = [str(row["case_id"]) for row in rows if row["missing_required_co_tags"]]
    contract_mismatches = [
        str(row["case_id"])
        for row in rows
        if row["exact_matches_truth"] is False
        or row["routing_matches_truth"] is False
        or bool(row["missing_required_co_tags"])
    ]

    base = load_default_feature_config()
    return {
        "schema_version": TAG_RETRIEVAL_TRUTH_OBSERVATION_V3_SCHEMA_VERSION,
        "suite_id": truth.suite_id,
        "truth_status": truth.truth_status,
        "truth_suite_fingerprint": tag_retrieval_truth_fingerprint(truth),
        "feature_config_fingerprint": (
            LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT if candidate else base.fingerprint
        ),
        "tags_config_schema_version": "tag-config-v4" if candidate else "tag-config-v1",
        "tags_config_version": (LIFECYCLE_OWNER_ROLE_CANDIDATE_VERSION if candidate else "tags-v1"),
        "feature_routing_schema_version": (
            FEATURE_ROUTING_V3_SCHEMA_VERSION if candidate else FEATURE_ROUTING_SCHEMA_VERSION
        ),
        "source_count": len(truth.sources),
        "case_count": len(rows),
        "parse_count": len(truth.sources),
        "by_tag": by_tag,
        "by_tag_and_split": by_tag_and_split,
        "file_diagnostic_case_counts": {},
        "scope_diagnostic_case_counts": {},
        "exact_mismatch_case_ids": exact_mismatches,
        "routing_mismatch_case_ids": routing_mismatches,
        "co_tag_mismatch_case_ids": co_tag_mismatches,
        "case_contract_mismatch_case_ids": contract_mismatches,
        "parser_risk_case_ids": [],
        "profile_diagnostic_case_counts": {},
        "owner_context_abstain_case_ids": [],
        "cases": rows,
    }


def _comparison() -> dict[str, object]:
    return build_lifecycle_symbol_leaf_comparison(
        _observation(candidate=False),
        _observation(candidate=True),
        truth_suite=_truth_suite(),
    )


def _case(observation: dict[str, object], case_id: str) -> dict[str, object]:
    rows = cast(list[dict[str, object]], observation["cases"])
    return next(row for row in rows if row["case_id"] == case_id)


def test_hermetic_comparison_uses_explicit_cross_target_adjudications() -> None:
    comparison = _comparison()

    assert comparison["schema_version"] == "lifecycle-owner-role-comparison-v1"
    assert comparison["candidate_kind"] == "owner_aware_shadow"
    assert comparison["lifecycle_target_addition_case_ids"] == list(LIFECYCLE_TARGET_ADDITIONS)
    assert comparison["declared_required_co_tag_lifecycle_addition_case_ids"] == list(
        DECLARED_CO_TAG_ADDITIONS
    )
    assert comparison["adjudicated_positive_cross_target_lifecycle_addition_case_ids"] == list(
        ADJUDICATED_CROSS_TARGET_ADDITIONS
    )
    assert comparison["adjudicated_negative_cross_target_lifecycle_addition_case_ids"] == []
    assert comparison["unadjudicated_cross_target_lifecycle_addition_case_ids"] == []
    assert comparison["evaluation_boundary"] == {
        "dataset_role": "development_regression",
        "legacy_acceptance_holdout_is_independent": False,
        "independent_blind_holdout_available": False,
        "rationale": (
            "All 48 cases and both legacy split labels were visible during candidate "
            "development, so they can freeze behavior but cannot provide independent "
            "activation evidence."
        ),
    }
    assert [
        item["case_id"]
        for item in cast(
            list[dict[str, object]],
            comparison["cross_target_tag_adjudications"],
        )
    ] == list(ADJUDICATED_CROSS_TARGET_ADDITIONS)
    assert all(
        cast(dict[str, object], item["receipt"])["independently_adjudicated"] is False
        for item in cast(
            list[dict[str, object]],
            comparison["cross_target_tag_adjudications"],
        )
    )
    assert comparison["all_lifecycle_exact_addition_case_ids"] == list(ALL_LIFECYCLE_ADDITIONS)
    assert comparison["symbol_leaf_provenance_failure_case_ids"] == []
    assert comparison["lifecycle_trace_provenance_failure_case_ids"] == []
    assert "TR-TIMER-008" in cast(
        list[str],
        comparison["resolved_contract_mismatch_case_ids"],
    )
    assert comparison["candidate_lifecycle_target_case_metrics"] == {
        "positive_case_count": 7,
        "negative_case_count": 5,
        "true_positive": 7,
        "false_positive": 0,
        "false_negative": 0,
        "true_negative": 5,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert comparison["development_regression_lifecycle_exact_metrics"] == {
        "positive_case_count": 15,
        "negative_case_count": 5,
        "true_positive": 15,
        "false_positive": 0,
        "false_negative": 0,
        "true_negative": 5,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert comparison["declared_contract_gate"] == {"passed": True, "failures": []}
    evidence_gate = cast(dict[str, object], comparison["candidate_evidence_gate"])
    assert evidence_gate["passed"] is False
    assert "development_regression_only" in cast(
        list[str],
        evidence_gate["failures"],
    )
    assert "independent_adjudicated_holdout_missing" in cast(
        list[str],
        evidence_gate["failures"],
    )
    assert "unadjudicated_cross_target_lifecycle_additions" not in cast(
        list[str], evidence_gate["failures"]
    )
    assert "ordinary_class_same_name_owner_not_distinguishable" not in cast(
        list[str], evidence_gate["failures"]
    )


@pytest.mark.parametrize(
    ("case_id", "signal_key", "bad_value"),
    [
        ("TR-LIFE-001", "operator", "any_symbol"),
        ("TR-TIMER-008", "normalized_value", "aboutToDisappear"),
        ("TR-NET-008", "value", "OtherOwner.aboutToAppear"),
    ],
)
def test_all_lifecycle_addition_classes_require_exact_leaf_provenance(
    case_id: str,
    signal_key: str,
    bad_value: str,
) -> None:
    baseline = _observation(candidate=False)
    candidate = _observation(candidate=True)
    row = _case(candidate, case_id)
    matches = cast(list[dict[str, object]], row["tag_matches"])
    lifecycle = next(
        match
        for match in matches
        if match["tag_id"] == "has_lifecycle" and match["scope"] == "unit_exact"
    )
    signal = cast(list[dict[str, object]], lifecycle["signals"])[0]
    signal[signal_key] = bad_value

    comparison = build_lifecycle_symbol_leaf_comparison(
        baseline,
        candidate,
        truth_suite=_truth_suite(),
    )

    assert comparison["symbol_leaf_provenance_failure_case_ids"] == [case_id]
    declared_gate = cast(dict[str, object], comparison["declared_contract_gate"])
    assert declared_gate["passed"] is False
    assert "symbol_leaf_provenance_missing" in cast(
        list[str],
        declared_gate["failures"],
    )


def test_declared_gate_rejects_internally_consistent_classification_drift() -> None:
    baseline = _observation(candidate=False)
    candidate = _observation(candidate=True)
    row = _case(candidate, "TR-NET-008")
    row["exact_tags"] = [
        tag_id for tag_id in cast(list[str], row["exact_tags"]) if tag_id != "has_lifecycle"
    ]
    row["tag_matches"] = [
        match
        for match in cast(list[dict[str, object]], row["tag_matches"])
        if not (match["tag_id"] == "has_lifecycle" and match["scope"] == "unit_exact")
    ]

    comparison = build_lifecycle_symbol_leaf_comparison(
        baseline,
        candidate,
        truth_suite=_truth_suite(),
    )

    declared_gate = cast(dict[str, object], comparison["declared_contract_gate"])
    assert declared_gate["passed"] is False
    assert "lifecycle_addition_classification_drift" in cast(
        list[str],
        declared_gate["failures"],
    )


def test_declared_gate_rejects_invalid_file_hint_leaf_provenance() -> None:
    baseline = _observation(candidate=False)
    candidate = _observation(candidate=True)
    row = _case(candidate, "TR-LIFE-001")
    matches = cast(list[dict[str, object]], row["tag_matches"])
    lifecycle_hint = next(
        match
        for match in matches
        if match["tag_id"] == "has_lifecycle" and match["scope"] == "file_hint"
    )
    signal = cast(list[dict[str, object]], lifecycle_hint["signals"])[0]
    signal["kind"] = "syntax"

    comparison = build_lifecycle_symbol_leaf_comparison(
        baseline,
        candidate,
        truth_suite=_truth_suite(),
    )

    assert comparison["lifecycle_trace_provenance_failure_case_ids"] == ["TR-LIFE-001"]
    declared_gate = cast(dict[str, object], comparison["declared_contract_gate"])
    assert declared_gate["passed"] is False
    assert "lifecycle_trace_provenance_invalid" in cast(
        list[str],
        declared_gate["failures"],
    )


@pytest.mark.parametrize(
    ("candidate_side", "key", "bad_value"),
    [
        (False, "feature_config_fingerprint", "feature-config:sha256:" + "0" * 64),
        (False, "feature_routing_schema_version", FEATURE_ROUTING_V3_SCHEMA_VERSION),
        (True, "tags_config_version", "wrong-candidate-version"),
    ],
)
def test_comparison_rejects_wrong_config_identity(
    candidate_side: bool,
    key: str,
    bad_value: object,
) -> None:
    baseline = _observation(candidate=False)
    candidate = _observation(candidate=True)
    selected = candidate if candidate_side else baseline
    selected[key] = bad_value

    with pytest.raises(ValueError, match=f"{key} identity mismatch"):
        build_lifecycle_symbol_leaf_comparison(
            baseline,
            candidate,
            truth_suite=_truth_suite(),
        )


def test_comparison_rejects_truth_and_immutable_case_identity_drift() -> None:
    baseline = _observation(candidate=False)
    candidate = _observation(candidate=True)
    candidate["truth_suite_fingerprint"] = "tag-retrieval-truth:sha256:" + "f" * 64

    with pytest.raises(ValueError, match="truth_suite_fingerprint identity mismatch"):
        build_lifecycle_symbol_leaf_comparison(
            baseline,
            candidate,
            truth_suite=_truth_suite(),
        )

    candidate = _observation(candidate=True)
    _case(candidate, "TR-TIMER-008")["review_status"] = "drifted"
    with pytest.raises(ValueError, match="immutable case field drift"):
        build_lifecycle_symbol_leaf_comparison(
            baseline,
            candidate,
            truth_suite=_truth_suite(),
        )


def test_comparison_binds_both_observations_to_truth_case_rows() -> None:
    baseline = _observation(candidate=False)
    candidate = _observation(candidate=True)
    _case(baseline, "TR-NET-001")["source_alias"] = "src999"
    _case(candidate, "TR-NET-001")["source_alias"] = "src999"

    with pytest.raises(ValueError, match="does not match Truth suite"):
        build_lifecycle_symbol_leaf_comparison(
            baseline,
            candidate,
            truth_suite=_truth_suite(),
        )


def test_comparison_rejects_internally_inconsistent_summary() -> None:
    baseline = _observation(candidate=False)
    candidate = _observation(candidate=True)
    candidate["case_contract_mismatch_case_ids"] = ["TR-NET-001"]

    with pytest.raises(ValueError, match="does not match rows"):
        build_lifecycle_symbol_leaf_comparison(
            baseline,
            candidate,
            truth_suite=_truth_suite(),
        )


def _cli_report(*, declared_passed: bool, evidence_passed: bool) -> dict[str, object]:
    return {
        "schema_version": "lifecycle-owner-role-evaluation-v1",
        "comparison": {
            "declared_contract_gate": {
                "passed": declared_passed,
                "failures": [] if declared_passed else ["declared_failure"],
            },
            "candidate_evidence_gate": {
                "passed": evidence_passed,
                "failures": [] if evidence_passed else ["safety_blocker"],
            },
        },
    }


def test_cli_exit_zero_for_declared_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _cli_report(declared_passed=True, evidence_passed=False)
    monkeypatch.setattr(evaluator_cli, "_evaluate", lambda _args: report)

    assert evaluator_cli.main(["--source-root", "/unused", "--require-declared-contract"]) == 0
    assert json.loads(capsys.readouterr().out) == report


def test_cli_exit_one_for_required_candidate_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _cli_report(declared_passed=True, evidence_passed=False)
    monkeypatch.setattr(evaluator_cli, "_evaluate", lambda _args: report)

    assert evaluator_cli.main(["--source-root", "/unused", "--require-candidate-evidence"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == report
    assert captured.err == ""


def test_cli_exit_two_for_identity_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_args: object) -> dict[str, object]:
        raise ValueError("candidate observation config identity mismatch")

    monkeypatch.setattr(evaluator_cli, "_evaluate", fail)

    assert evaluator_cli.main(["--source-root", "/unused"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "candidate observation config identity mismatch" in captured.err
