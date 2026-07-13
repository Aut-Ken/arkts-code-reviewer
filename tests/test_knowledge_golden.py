from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from arkts_code_reviewer.knowledge_validation.golden import (
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = ROOT / "tests/golden/knowledge"
MANIFEST = GOLDEN_ROOT / "manifest.json"
BASELINE = GOLDEN_ROOT / "baselines/current.json"


def _copy_suite(tmp_path: Path) -> Path:
    target = tmp_path / "knowledge"
    shutil.copytree(GOLDEN_ROOT, target)
    return target / "manifest.json"


def _mutate_manifest(tmp_path: Path, mutate: object) -> Path:
    manifest = _copy_suite(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert callable(mutate)
    mutate(payload)
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def test_knowledge_k0_records_honest_preimplementation_baseline() -> None:
    suite = load_golden_suite(MANIFEST)
    report = evaluate_golden_suite(suite)

    assert len(suite.cases) == 12
    assert report["matched_case_count"] == 1
    assert report["mismatched_case_count"] == 11
    assert report["implementation"] == "not-implemented"
    assert is_perfect(report) is False
    assert_strict_baseline(report, suite, BASELINE)


def test_knowledge_evaluator_is_repeatable_and_forged_report_is_not_perfect() -> None:
    suite = load_golden_suite(MANIFEST)
    first = evaluate_golden_suite(suite)
    second = evaluate_golden_suite(suite)
    assert first == second

    forged = copy.deepcopy(first)
    forged["matched_case_count"] = 12
    forged["mismatched_case_count"] = 0
    assert is_perfect(forged) is False


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data.__setitem__("unknown", True), "Extra inputs are not permitted"),
        (lambda data: data.pop("description"), "Field required"),
        (
            lambda data: data["cases"][1].__setitem__(
                "case_id", data["cases"][0]["case_id"]
            ),
            "consecutive and sorted",
        ),
        (
            lambda data: data["cases"][0]["source"].__setitem__(
                "content_sha256", "0" * 64
            ),
            "source hash/provenance drift",
        ),
        (
            lambda data: data["cases"][0]["source"].__setitem__("origin_lines", [0, 5]),
            "1-based inclusive",
        ),
        (
            lambda data: data["cases"][0]["expected"]["annotations"][0].__setitem__(
                "tags", ["not_registered"]
            ),
            "unregistered Tag",
        ),
        (
            lambda data: data["cases"][0]["expected"]["annotations"][0].__setitem__(
                "dimension_ids", ["DIM-99"]
            ),
            "unregistered Dimension",
        ),
        (
            lambda data: data["cases"][0]["expected"]["annotations"][0].__setitem__(
                "domains", ["resource-management", "component-lifecycle"]
            ),
            "sorted and unique",
        ),
        (
            lambda data: data["cases"][0]["expected"]["clauses"][0][
                "source_span"
            ].__setitem__("end_line", 99),
            "span out of range",
        ),
    ],
)
def test_knowledge_loader_fails_closed_on_manifest_drift(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    manifest = _mutate_manifest(tmp_path, mutate)
    with pytest.raises(ValueError, match=message):
        load_golden_suite(manifest)


def test_knowledge_loader_rejects_duplicate_json_key(tmp_path: Path) -> None:
    manifest = _copy_suite(tmp_path)
    raw = manifest.read_text(encoding="utf-8")
    raw = raw.replace(
        '"schema_version": "knowledge-golden-v1",',
        '"schema_version": "knowledge-golden-v1",\n'
        '  "schema_version": "knowledge-golden-v1",',
        1,
    )
    manifest.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_golden_suite(manifest)


def test_knowledge_loader_rejects_source_symlink(tmp_path: Path) -> None:
    manifest = _copy_suite(tmp_path)
    source = manifest.parent / "sources/KG001_numbered_spec.md"
    target = manifest.parent / "sources/KG001_real.md"
    source.rename(target)
    source.symlink_to(target.name)
    with pytest.raises(ValueError, match="must not use symlinks"):
        load_golden_suite(manifest)


def test_knowledge_baseline_writer_cannot_escape_current_path(tmp_path: Path) -> None:
    suite = load_golden_suite(MANIFEST)
    report = evaluate_golden_suite(suite)
    with pytest.raises(ValueError, match="may only update"):
        write_current_baseline(report, suite, tmp_path / "forged.json")
