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


def test_current_baseline_exposes_reviewed_feature_routing_defects() -> None:
    suite = load_golden_suite(MANIFEST)
    report = evaluate_golden_suite(suite)

    assert len(suite.cases) == 16
    assert report["matched_case_count"] == 14
    assert report["mismatched_case_count"] == 2
    assert [
        row["case_id"] for row in report["cases"] if row["matched"] is not True
    ] == ["FR013", "FR014"]
    assert report["metrics"]["input_order_stability"] == 1.0
    assert is_perfect(report, suite) is False
    assert_strict_baseline(report, suite, BASELINE)


def test_evaluator_is_repeatable_and_rejects_forged_perfect_report() -> None:
    suite = load_golden_suite(MANIFEST)
    first = evaluate_golden_suite(suite)
    second = evaluate_golden_suite(suite)
    assert first == second

    forged = copy.deepcopy(first)
    forged["matched_case_count"] = 16
    forged["mismatched_case_count"] = 0
    forged["metrics"] = {
        key: 1.0 for key in forged["metrics"]
    }
    for row in forged["cases"]:
        row["matched"] = True
        row["actual"] = copy.deepcopy(row["expected"])
        row["differences"] = []
    assert is_perfect(forged, suite) is False


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data.__setitem__("unknown", True), "fields mismatch"),
        (lambda data: data.pop("description"), "fields mismatch"),
        (
            lambda data: data["cases"][1].__setitem__(
                "case_id", data["cases"][0]["case_id"]
            ),
            "FR001 through FR016",
        ),
        (
            lambda data: data["cases"][0]["sources"][0].__setitem__(
                "content_sha256", "0" * 64
            ),
            "source hash/provenance drift",
        ),
        (
            lambda data: data["cases"][0]["sources"][0].__setitem__(
                "source_ref_id", "code-source:sha256:" + "0" * 64
            ),
            "source_ref_id provenance drift",
        ),
        (
            lambda data: data["cases"][0]["sources"][0].__setitem__(
                "origin_lines", [0, 1]
            ),
            "1-based integers",
        ),
        (
            lambda data: data["cases"][0]["units"][0]["unit_exact"].__setitem__(
                "apis", ["setTimeout", "setInterval"]
            ),
            "sorted and unique",
        ),
        (
            lambda data: data["cases"][0]["expected"]["units"][0][
                "exact_tags"
            ].append("not_registered"),
            "sorted and unique|unregistered",
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
