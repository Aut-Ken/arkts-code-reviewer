from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from arkts_code_reviewer.feature_routing_validation.golden import (
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = ROOT / "tests/golden/feature_routing"
MANIFEST = GOLDEN_ROOT / "manifest.json"
BASELINE = GOLDEN_ROOT / "baselines/current.json"


def _copy_suite(tmp_path: Path) -> Path:
    target = tmp_path / "feature_routing"
    shutil.copytree(GOLDEN_ROOT, target)
    return target / "manifest.json"


def _mutate_manifest(tmp_path: Path, mutate: object) -> Path:
    manifest = _copy_suite(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert callable(mutate)
    mutate(payload)
    manifest.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def test_feature_routing_matches_complete_reviewed_formal_contract() -> None:
    suite = load_golden_suite(MANIFEST)
    report = evaluate_golden_suite(suite)

    assert len(suite.cases) == 16
    assert len(suite.review_question_ids) == 12
    assert report["matched_case_count"] == 16
    assert report["mismatched_case_count"] == 0
    assert report["metrics"]["input_order_stability"] == 1.0
    assert is_perfect(report, suite) is True
    assert_strict_baseline(report, suite, BASELINE)

    expected_units = [
        unit
        for row in report["cases"]
        for unit in row["expected"]["units"]
    ]
    assert len(expected_units) == 20
    assert {
        tag
        for unit in expected_units
        for field in ("exact_tags", "routing_tags")
        for tag in unit[field]
    } == set(suite.tag_ids)
    assert {
        dimension
        for unit in expected_units
        for field in ("dimensions", "routing_dimensions")
        for dimension in unit[field]
    } == set(suite.dimension_ids)
    assert {
        question
        for unit in expected_units
        for question in unit["review_question_ids"]
    } == set(suite.review_question_ids)
    activation_signals = {
        (
            match["tag_id"],
            match["scope"],
            signal["kind"],
            signal["value"],
        )
        for unit in expected_units
        for match in unit["tag_matches"]
        for signal in match["signals"]
    }
    assert len(activation_signals) == 69
    assert sum(
        len(match["signals"])
        for unit in expected_units
        for match in unit["tag_matches"]
    ) == 86
    assert sum(
        len(row["expected"]["question_bindings"])
        for row in report["cases"]
    ) == 44

    expected = report["cases"][0]["expected"]
    assert set(expected) == {
        "schema_version",
        "feature_config_version",
        "tags_config_version",
        "dimensions_config_version",
        "units",
        "mr_dimensions",
        "question_bindings",
        "diagnostics",
    }
    assert set(expected["units"][0]) == {
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


def test_evaluator_is_repeatable_and_rejects_forged_perfect_report() -> None:
    suite = load_golden_suite(MANIFEST)
    first = evaluate_golden_suite(suite)
    second = evaluate_golden_suite(suite)
    assert first == second

    forged = copy.deepcopy(first)
    forged["cases"][0]["metric_counts"]["exact_tag_true"] += 1
    assert is_perfect(forged, suite) is False


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data.__setitem__("unknown", True), "fields mismatch"),
        (lambda data: data.pop("description"), "fields mismatch"),
        (
            lambda data: data["cases"][1].__setitem__("case_id", data["cases"][0]["case_id"]),
            "FR001 through FR016",
        ),
        (
            lambda data: data["cases"][0]["sources"][0].__setitem__("content_sha256", "0" * 64),
            "source hash/provenance drift",
        ),
        (
            lambda data: data["cases"][0]["sources"][0].__setitem__(
                "source_ref_id", "code-source:sha256:" + "0" * 64
            ),
            "source_ref_id provenance drift",
        ),
        (
            lambda data: data["cases"][0]["sources"][0].__setitem__("origin_lines", [0, 1]),
            "1-based integers",
        ),
        (
            lambda data: data["cases"][0]["units"][0]["unit_exact"].__setitem__(
                "apis", ["setTimeout", "setInterval"]
            ),
            "sorted and unique",
        ),
        (
            lambda data: data["cases"][0]["expected"]["units"][0]["exact_tags"].append(
                "not_registered"
            ),
            "sorted and unique|unregistered",
        ),
        (
            lambda data: data["review_question_ids"].append("RQ-unknown"),
            "freeze the 12 review questions",
        ),
        (
            lambda data: data.__setitem__(
                "feature_config_version", "feature-config:sha256:" + "0" * 64
            ),
            "feature_config_version drift",
        ),
        (
            lambda data: data["cases"][0]["expected"].__setitem__(
                "schema_version", "feature-routing-v999"
            ),
            "schema_version disagrees with frozen truth",
        ),
        (
            lambda data: data["cases"][0]["expected"]["units"][0].pop("tag_matches"),
            "fields mismatch",
        ),
        (
            lambda data: data["cases"][0]["expected"]["units"][0]["tag_matches"].reverse(),
            "tag_matches must be sorted and unique",
        ),
        (
            lambda data: data["cases"][0]["expected"]["units"][0]["tag_matches"][0][
                "signals"
            ].append({"kind": "unknown_kind", "value": "forged"}),
            "kind is unsupported",
        ),
        (
            lambda data: data["cases"][0]["expected"]["units"][0]["retrieval_dimensions"].append(
                "DIM-07"
            ),
            "retrieval_dimensions disagree with frozen truth",
        ),
        (
            lambda data: data["cases"][0]["expected"]["units"][0]["diagnostics"].append(
                "unit_owner_unresolved"
            ),
            "diagnostics disagree with frozen truth",
        ),
        (
            lambda data: data["cases"][0]["expected"]["question_bindings"][0].__setitem__(
                "review_question_id", "RQ-accessibility"
            ),
            "question_bindings disagree with frozen truth",
        ),
    ],
)
def test_loader_fails_closed_on_manifest_drift(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    manifest = _mutate_manifest(tmp_path, mutate)
    with pytest.raises(ValueError, match=message):
        load_golden_suite(manifest)


def test_loader_rejects_duplicate_json_key(tmp_path: Path) -> None:
    manifest = _copy_suite(tmp_path)
    raw = manifest.read_text(encoding="utf-8")
    raw = raw.replace(
        '"schema_version": "feature-routing-golden-v1",',
        '"schema_version": "feature-routing-golden-v1",\n'
        '  "schema_version": "feature-routing-golden-v1",',
        1,
    )
    manifest.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_golden_suite(manifest)


def test_loader_and_baseline_reject_symlinks(tmp_path: Path) -> None:
    manifest_link = tmp_path / "manifest.json"
    manifest_link.symlink_to(MANIFEST)
    with pytest.raises(ValueError, match="must not be a symlink"):
        load_golden_suite(manifest_link)

    suite = load_golden_suite(MANIFEST)
    report = evaluate_golden_suite(suite)
    baseline_link = tmp_path / "current.json"
    baseline_link.symlink_to(BASELINE)
    with pytest.raises(ValueError, match="must not be a symlink"):
        assert_strict_baseline(report, suite, baseline_link)


def test_baseline_writer_cannot_overwrite_expected(tmp_path: Path) -> None:
    suite = load_golden_suite(MANIFEST)
    report = evaluate_golden_suite(suite)
    with pytest.raises(ValueError, match="may only update"):
        write_current_baseline(report, suite, tmp_path / "manifest.json")
