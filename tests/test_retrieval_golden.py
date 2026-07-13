from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from arkts_code_reviewer.retrieval.models import KnowledgeIndex
from arkts_code_reviewer.retrieval_validation.golden import (
    evaluate_retrieval_golden,
    load_retrieval_golden_manifest,
    render_retrieval_golden_report,
    validate_retrieval_golden_baseline,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = ROOT / "tests/golden/retrieval"
MANIFEST = GOLDEN_ROOT / "manifest.json"
BASELINE = GOLDEN_ROOT / "baseline.json"
EVALUATOR = ROOT / "tools/evaluate_retrieval_golden.py"


def _load_object(path: Path) -> dict[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _write_object(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _copy_suite(tmp_path: Path) -> Path:
    target = tmp_path / "retrieval"
    shutil.copytree(GOLDEN_ROOT, target)
    return target / "manifest.json"


def _mutate_manifest(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> Path:
    manifest = _copy_suite(tmp_path)
    payload = _load_object(manifest)
    mutate(payload)
    _write_object(manifest, payload)
    return manifest


def _run_evaluator(*args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, str(EVALUATOR), *args],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _rebuild_index(
    index: KnowledgeIndex,
    *,
    retrieval_config_fingerprint: str,
) -> KnowledgeIndex:
    return KnowledgeIndex.create(
        origin=index.origin,
        published_build_id=index.published_build_id,
        source_bundle_id=index.source_bundle_id,
        feature_config_version=index.feature_config_version,
        annotation_version=index.annotation_version,
        catalog_version=index.catalog_version,
        retrieval_version=index.retrieval_version,
        retrieval_config_fingerprint=retrieval_config_fingerprint,
        embedding_model=index.embedding_model,
        embedding_version=index.embedding_version,
        embedding_dimensions=index.embedding_dimensions,
        api_symbols=index.api_symbols,
        records=index.records,
    )


def test_reviewed_retrieval_golden_is_complete_perfect_and_strict() -> None:
    manifest, index = load_retrieval_golden_manifest(MANIFEST)
    report = evaluate_retrieval_golden(MANIFEST)

    assert index.origin == "golden_fixture"
    assert [case.case_id for case in manifest.cases] == [
        f"RG-{number:03d}" for number in range(1, 37)
    ]
    assert Counter(case.retrieval_mode for case in manifest.cases) == {
        "exact": 18,
        "hybrid": 12,
        "embedding_failure": 6,
    }
    assert sum(len(case.units) for case in manifest.cases) == 39
    assert all(
        unit.semantic_code_excerpt is None
        for case in manifest.cases
        if case.retrieval_mode == "exact"
        for unit in case.units
    )
    assert all(
        unit.semantic_code_excerpt is not None
        for case in manifest.cases
        if case.retrieval_mode != "exact"
        for unit in case.units
    )
    assert (
        sum(
            not expected.ordered_rule_ids
            for case in manifest.cases
            for expected in case.expected_units
        )
        == 8
    )
    assert {
        code
        for case in manifest.cases
        for expected in case.expected_units
        for code in expected.diagnostic_codes
    } == {
        "applicability_unknown",
        "budget_exhausted",
        "context_dispatch_blocked",
        "embedding_unavailable",
        "empty_result",
        "parser_degraded",
    }
    assert {case.case_id for case in manifest.cases if len(case.units) > 1} == {
        "RG-018",
        "RG-030",
        "RG-036",
    }
    shared_query_case = manifest.cases[29]
    assert len(shared_query_case.units) == 2
    assert len(shared_query_case.query_embeddings) == 1

    assert report.case_count == 36
    assert report.passed_cases == 36
    assert report.recall_at_5 == 1.0
    assert report.precision_at_5 == 1.0
    assert report.mrr == 1.0
    assert report.forbidden_hits == 0
    assert report.perfect is True
    assert all(result.passed and not result.differences for result in report.results)
    validate_retrieval_golden_baseline(BASELINE, report)
    assert BASELINE.read_text(encoding="utf-8") == render_retrieval_golden_report(report)


def test_cli_require_perfect_and_strict_baseline_succeed(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    completed = _run_evaluator(
        "--manifest",
        str(MANIFEST),
        "--strict-baseline",
        str(BASELINE),
        "--require-perfect",
        "--output",
        str(output),
    )

    assert completed.returncode == 0, completed.stderr
    payload: object = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    assert payload["perfect"] is True
    assert output.read_text(encoding="utf-8") == completed.stdout


def test_cli_require_perfect_rejects_a_valid_but_wrong_expectation(
    tmp_path: Path,
) -> None:
    def reverse_expected_order(payload: dict[str, Any]) -> None:
        ordered = payload["cases"][0]["expected_units"][0]["ordered_rule_ids"]
        ordered.reverse()

    manifest = _mutate_manifest(tmp_path, reverse_expected_order)
    completed = _run_evaluator(
        "--manifest",
        str(manifest),
        "--require-perfect",
    )

    assert completed.returncode == 1
    assert not completed.stderr
    payload: object = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    assert payload["passed_cases"] == 35
    assert payload["perfect"] is False


def test_cli_strict_baseline_rejects_report_drift(tmp_path: Path) -> None:
    def reverse_expected_order(payload: dict[str, Any]) -> None:
        payload["cases"][0]["expected_units"][0]["ordered_rule_ids"].reverse()

    manifest = _mutate_manifest(tmp_path, reverse_expected_order)
    completed = _run_evaluator(
        "--manifest",
        str(manifest),
        "--strict-baseline",
        str(BASELINE),
    )

    assert completed.returncode == 2
    assert not completed.stdout
    assert "differs from strict baseline" in completed.stderr


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("index_hash", "sha256:" + "0" * 64, "index content hash drift"),
        (
            "index_version",
            "knowledge-index:sha256:" + "0" * 64,
            "index identity drift",
        ),
        (
            "feature_config_version",
            "feature-config:sha256:" + "0" * 64,
            "feature config drift",
        ),
        (
            "retrieval_config_fingerprint",
            "retrieval-config:sha256:" + "0" * 64,
            "Golden config drift",
        ),
        ("retrieval_version", "retrieval-v999", "Golden version drift"),
    ],
)
def test_loader_rejects_manifest_hash_and_config_provenance_drift(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    manifest = _mutate_manifest(
        tmp_path,
        lambda payload: payload.__setitem__(field, value),
    )

    with pytest.raises(ValueError, match=message):
        load_retrieval_golden_manifest(manifest)


def test_loader_rejects_raw_index_hash_drift(tmp_path: Path) -> None:
    manifest = _copy_suite(tmp_path)
    index_path = manifest.parent / "index.json"
    index_path.write_bytes(index_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="index content hash drift"):
        load_retrieval_golden_manifest(manifest)


def test_loader_rejects_valid_index_with_embedded_config_provenance_drift(
    tmp_path: Path,
) -> None:
    manifest = _copy_suite(tmp_path)
    payload = _load_object(manifest)
    _, index = load_retrieval_golden_manifest(MANIFEST)
    drifted = _rebuild_index(
        index,
        retrieval_config_fingerprint="retrieval-config:sha256:" + "0" * 64,
    )
    index_path = manifest.parent / "index.json"
    raw = (drifted.model_dump_json(indent=2) + "\n").encode("utf-8")
    index_path.write_bytes(raw)
    payload["index_hash"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    payload["index_version"] = drifted.index_version
    _write_object(manifest, payload)

    with pytest.raises(ValueError, match="index retrieval config drift"):
        load_retrieval_golden_manifest(manifest)


@pytest.mark.parametrize("location", ["manifest", "case", "unit", "expected"])
def test_loader_rejects_unknown_fields(tmp_path: Path, location: str) -> None:
    def add_unknown(payload: dict[str, Any]) -> None:
        if location == "manifest":
            target = payload
        elif location == "case":
            target = payload["cases"][0]
        elif location == "unit":
            target = payload["cases"][0]["units"][0]
        else:
            target = payload["cases"][0]["expected_units"][0]
        target["unknown_contract_field"] = True

    manifest = _mutate_manifest(tmp_path, add_unknown)

    with pytest.raises(ValueError, match="invalid Retrieval Golden manifest"):
        load_retrieval_golden_manifest(manifest)


def test_loader_rejects_duplicate_manifest_json_key(tmp_path: Path) -> None:
    manifest = _copy_suite(tmp_path)
    raw = manifest.read_text(encoding="utf-8")
    raw = raw.replace(
        '"schema_version": "retrieval-golden-v1"',
        '"schema_version": "retrieval-golden-v1",\n  "schema_version": "retrieval-golden-v1"',
        1,
    )
    manifest.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_retrieval_golden_manifest(manifest)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra"])
def test_loader_requires_exact_contiguous_case_ids(
    tmp_path: Path,
    mutation: str,
) -> None:
    def mutate_cases(payload: dict[str, Any]) -> None:
        if mutation == "missing":
            payload["cases"].pop()
        elif mutation == "duplicate":
            payload["cases"][1]["case_id"] = "RG-001"
        else:
            extra = payload["cases"][-1].copy()
            extra["case_id"] = "RG-037"
            payload["cases"].append(extra)

    manifest = _mutate_manifest(tmp_path, mutate_cases)

    with pytest.raises(ValueError, match="contiguous RG-001..RG-036"):
        load_retrieval_golden_manifest(manifest)


@pytest.mark.parametrize("reference", ["requested", "expected"])
def test_loader_rejects_unknown_rule_references(
    tmp_path: Path,
    reference: str,
) -> None:
    def add_unknown_rule(payload: dict[str, Any]) -> None:
        case = payload["cases"][7]
        if reference == "requested":
            case["units"][0]["requested_rule_ids"] = ["UNKNOWN/RULE"]
        else:
            expected = case["expected_units"][0]
            expected["ordered_rule_ids"] = ["UNKNOWN/RULE"]
            expected["required_rule_ids"] = ["UNKNOWN/RULE"]

    manifest = _mutate_manifest(tmp_path, add_unknown_rule)

    with pytest.raises(ValueError, match="references unknown rules"):
        load_retrieval_golden_manifest(manifest)


def test_loader_rejects_unknown_diagnostic_code(tmp_path: Path) -> None:
    manifest = _mutate_manifest(
        tmp_path,
        lambda payload: payload["cases"][0]["expected_units"][0].__setitem__(
            "diagnostic_codes", ["forged_diagnostic"]
        ),
    )

    with pytest.raises(ValueError, match="unknown codes"):
        load_retrieval_golden_manifest(manifest)


def test_loader_rejects_query_hash_and_vector_dimension_drift(tmp_path: Path) -> None:
    hash_manifest = _mutate_manifest(
        tmp_path / "hash",
        lambda payload: payload["cases"][18]["query_embeddings"][0].__setitem__(
            "query_text_hash", "sha256:" + "0" * 64
        ),
    )
    with pytest.raises(ValueError, match="uniquely cover exact Unit query texts"):
        load_retrieval_golden_manifest(hash_manifest)

    def shorten_vector(payload: dict[str, Any]) -> None:
        payload["cases"][18]["query_embeddings"][0]["vector"].pop()

    vector_manifest = _mutate_manifest(tmp_path / "vector", shorten_vector)
    with pytest.raises(ValueError, match="query vector dimensions drift"):
        load_retrieval_golden_manifest(vector_manifest)


def test_loader_rejects_query_embeddings_on_exact_cases(tmp_path: Path) -> None:
    def add_embedding(payload: dict[str, Any]) -> None:
        payload["cases"][0]["query_embeddings"] = [
            {
                "query_text_hash": "sha256:" + "0" * 64,
                "vector": [0.0] * 8,
            }
        ]

    manifest = _mutate_manifest(tmp_path, add_embedding)

    with pytest.raises(ValueError, match="Exact Golden cases must not carry"):
        load_retrieval_golden_manifest(manifest)


def test_loader_rejects_manifest_and_index_symlinks(tmp_path: Path) -> None:
    manifest_link = tmp_path / "manifest.json"
    manifest_link.symlink_to(MANIFEST)
    with pytest.raises(ValueError, match="regular non-symlink file"):
        load_retrieval_golden_manifest(manifest_link)

    manifest = _copy_suite(tmp_path / "index-link")
    index_path = manifest.parent / "index.json"
    index_path.unlink()
    index_path.symlink_to(GOLDEN_ROOT / "index.json")
    with pytest.raises(ValueError, match="regular non-symlink file"):
        load_retrieval_golden_manifest(manifest)


def test_strict_baseline_rejects_semantic_format_and_duplicate_key_drift(
    tmp_path: Path,
) -> None:
    report = evaluate_retrieval_golden(MANIFEST)

    semantic = tmp_path / "semantic.json"
    semantic_payload = _load_object(BASELINE)
    semantic_payload["passed_cases"] = 35
    _write_object(semantic, semantic_payload)
    with pytest.raises(ValueError, match="differs from strict baseline"):
        validate_retrieval_golden_baseline(semantic, report)

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(
        json.dumps(_load_object(BASELINE), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not canonical JSON"):
        validate_retrieval_golden_baseline(noncanonical, report)

    duplicate = tmp_path / "duplicate.json"
    raw = BASELINE.read_text(encoding="utf-8").replace(
        '"case_count": 36,',
        '"case_count": 36,\n  "case_count": 36,',
        1,
    )
    duplicate.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        validate_retrieval_golden_baseline(duplicate, report)


def test_strict_baseline_rejects_symlink(tmp_path: Path) -> None:
    report = evaluate_retrieval_golden(MANIFEST)
    baseline_link = tmp_path / "baseline.json"
    baseline_link.symlink_to(BASELINE)

    with pytest.raises(ValueError, match="regular non-symlink file"):
        validate_retrieval_golden_baseline(baseline_link, report)
