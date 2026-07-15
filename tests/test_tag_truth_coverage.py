from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from arkts_code_reviewer.feature_routing_validation.tag_truth_coverage import (
    TAG_TRUTH_COVERAGE_FINGERPRINT_PREFIX,
    TAG_TRUTH_COVERAGE_SCHEMA_VERSION,
    build_tag_truth_coverage_report,
    tag_truth_coverage_fingerprint,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TAGS_CONFIG = REPO_ROOT / "config/tags.yaml"
DIMENSIONS_CONFIG = REPO_ROOT / "config/dimensions.yaml"
FEATURE_GOLDEN = REPO_ROOT / "tests/golden/feature_routing/manifest.json"
DEVELOPMENT_TRUTH = REPO_ROOT / "tests/evaluation/tag_retrieval/manifest.json"
DRAFT_TRUTH = REPO_ROOT / "tests/tag_truth/relational_store_api/manifest.json"
DRAFT_BASELINE = REPO_ROOT / "tests/tag_truth/relational_store_api/baselines/current.json"
CLI = REPO_ROOT / "tools/report_tag_truth_coverage.py"


def _report(
    *,
    feature_golden: Path = FEATURE_GOLDEN,
    development_truth: Path = DEVELOPMENT_TRUTH,
    draft_baseline: Path = DRAFT_BASELINE,
) -> dict[str, object]:
    return build_tag_truth_coverage_report(
        tags_config_path=TAGS_CONFIG,
        dimensions_config_path=DIMENSIONS_CONFIG,
        feature_golden_manifest_path=feature_golden,
        development_manifest_path=development_truth,
        draft_manifest_path=DRAFT_TRUTH,
        draft_baseline_path=draft_baseline,
    )


def _formal_by_id(report: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = cast(list[dict[str, object]], report["formal_tags"])
    return {cast(str, row["tag_id"]): row for row in rows}


def test_real_coverage_report_separates_contract_development_and_missing_evidence() -> None:
    report = _report()

    assert report["schema_version"] == TAG_TRUTH_COVERAGE_SCHEMA_VERSION
    summary = cast(dict[str, object], report["summary"])
    assert summary == {
        "formal_tag_count": 24,
        "draft_candidate_count": 1,
        "synthetic_only_formal_tag_count": 20,
        "development_only_formal_tag_count": 4,
        "adjudicated_formal_tag_count": 0,
        "independent_blind_formal_tag_count": 0,
        "prevalence_measured_formal_tag_count": 0,
        "production_qualified_tag_count": 0,
        "overall_qualification_status": "not_qualified",
    }
    formal = cast(list[dict[str, object]], report["formal_tags"])
    tag_ids = [cast(str, row["tag_id"]) for row in formal]
    assert tag_ids == sorted(tag_ids)
    assert all(row["configured_status"] == "Active" for row in formal)
    assert all(row["qualification_status"] == "not_qualified" for row in formal)
    assert all(row["blind_case_count"] == 0 for row in formal)
    assert all(row["prevalence_case_count"] == 0 for row in formal)
    assert all(row["parser_risk_status"] == "not_measured" for row in formal)
    assert all(row["parser_risk_case_count"] is None for row in formal)

    tags = _formal_by_id(report)
    animation = tags["has_animation"]
    assert animation["evidence_level"] == "synthetic_only"
    animation_reasons = cast(list[str], animation["qualification_reasons"])
    assert "real_code_target_truth_missing" in animation_reasons
    animation_synthetic = cast(dict[str, object], animation["synthetic"])
    assert animation_synthetic["unit_count"] == 20
    assert animation_synthetic["exact_positive_count"] == 1
    assert animation_synthetic["routing_positive_count"] == 1

    lifecycle = tags["has_lifecycle"]
    assert lifecycle["evidence_level"] == "development_only"
    assert lifecycle["qualification_status"] == "not_qualified"
    lifecycle_reasons = cast(list[str], lifecycle["qualification_reasons"])
    assert "independent_blind_holdout_missing" in lifecycle_reasons
    lifecycle_development = cast(dict[str, object], lifecycle["development"])
    assert lifecycle_development == {
        "dataset_role": "development_regression",
        "target_case_count": 12,
        "positive_case_count": 7,
        "negative_case_count": 5,
        "source_count": 9,
        "family_count": 9,
        "truth_status": "provisional",
        "review_status_counts": {"proposed": 12},
        "legacy_acceptance_case_count": 4,
        "legacy_acceptance_is_independent": False,
        "declared_secondary_positive_count": 1,
        "recorded_product_decision_count": 7,
    }

    timer = tags["has_timer"]
    timer_synthetic = cast(dict[str, object], timer["synthetic"])
    assert timer_synthetic["exact_positive_count"] == 3
    assert timer_synthetic["routing_positive_count"] == 6
    timer_development = cast(dict[str, object], timer["development"])
    assert timer_development["target_case_count"] == 12
    assert timer_development["positive_case_count"] == 7
    assert timer_development["negative_case_count"] == 5
    assert timer_development["family_count"] == 9


def test_draft_candidate_is_separate_and_baseline_is_parser_observation_only() -> None:
    report = _report()
    formal_ids = {row["tag_id"] for row in cast(list[dict[str, object]], report["formal_tags"])}
    draft_rows = cast(list[dict[str, object]], report["draft_candidates"])

    assert len(draft_rows) == 1
    draft = draft_rows[0]
    assert draft["tag_id"] == "has_relational_store_api"
    assert draft["tag_id"] not in formal_ids
    assert draft["configured_status"] == "Draft"
    assert draft["evidence_level"] == "development_only"
    assert draft["qualification_status"] == "not_qualified"
    assert draft["parser_risk_status"] == "measured"
    assert draft["parser_risk_case_count"] == 0
    assert draft["parser_risk_source_role"] == "behavior_snapshot_not_truth"
    assert draft["blind_case_count"] == 0
    assert draft["prevalence_case_count"] == 0
    development = cast(dict[str, object], draft["development"])
    assert development["target_case_count"] == 105
    assert development["positive_case_count"] == 40
    assert development["negative_case_count"] == 65
    assert development["source_count"] == 95
    assert development["family_count"] == 44
    assert development["diagnostic_case_count"] == 9
    assert development["legacy_acceptance_case_count"] == 30
    assert development["legacy_acceptance_is_independent"] is False

    serialized = json.dumps(report, sort_keys=True)
    assert "provisional_semantic_metrics" not in serialized
    assert '"precision"' not in serialized
    assert '"recall"' not in serialized
    inputs = cast(dict[str, object], report["inputs"])
    baseline_input = cast(dict[str, object], inputs["draft_behavior_baseline"])
    assert baseline_input["role"] == "behavior_snapshot_not_truth"


def test_report_is_deterministic_and_self_fingerprinted() -> None:
    first = _report()
    second = _report()

    assert first == second
    fingerprint = cast(str, first["coverage_fingerprint"])
    assert fingerprint.startswith(TAG_TRUTH_COVERAGE_FINGERPRINT_PREFIX)
    assert fingerprint == tag_truth_coverage_fingerprint(first)
    tampered = json.loads(json.dumps(first))
    cast(dict[str, object], tampered["summary"])["formal_tag_count"] = 23
    assert tag_truth_coverage_fingerprint(tampered) != fingerprint


def test_baseline_metric_values_never_become_truth_counts(tmp_path: Path) -> None:
    baseline = json.loads(DRAFT_BASELINE.read_text(encoding="utf-8"))
    baseline["provisional_semantic_metrics"]["positive_case_count"] = 999
    baseline["provisional_semantic_metrics"]["negative_case_count"] = 0
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")

    report = _report(draft_baseline=path)
    draft = cast(list[dict[str, object]], report["draft_candidates"])[0]
    development = cast(dict[str, object], draft["development"])
    assert development["positive_case_count"] == 40
    assert development["negative_case_count"] == 65


@pytest.mark.parametrize("kind", ["golden", "development", "baseline"])
def test_coverage_rejects_unknown_input_fields(tmp_path: Path, kind: str) -> None:
    source = {
        "golden": FEATURE_GOLDEN,
        "development": DEVELOPMENT_TRUTH,
        "baseline": DRAFT_BASELINE,
    }[kind]
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["unknown_coverage_field"] = True
    path = tmp_path / f"{kind}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown|extra|fields mismatch"):
        _report(
            feature_golden=path if kind == "golden" else FEATURE_GOLDEN,
            development_truth=path if kind == "development" else DEVELOPMENT_TRUTH,
            draft_baseline=path if kind == "baseline" else DRAFT_BASELINE,
        )


def test_coverage_rejects_draft_baseline_case_identity_drift(tmp_path: Path) -> None:
    baseline = json.loads(DRAFT_BASELINE.read_text(encoding="utf-8"))
    baseline["cases"][0]["case_id"] = "RDB-N999"
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")

    with pytest.raises(ValueError, match="case identity drift"):
        _report(draft_baseline=path)


def test_cli_emits_only_deterministic_stdout_json() -> None:
    completed = subprocess.run(
        [str(REPO_ROOT / ".venv/bin/python"), str(CLI)],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": "src"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == TAG_TRUTH_COVERAGE_SCHEMA_VERSION
    assert payload["coverage_fingerprint"] == tag_truth_coverage_fingerprint(payload)
    assert payload == _report()
