from __future__ import annotations

import json
import re
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "E2E_test_example_1"
ARTIFACTS = EXAMPLE_ROOT / "artifacts"
EXPECTED_INDEX_VERSION = (
    "knowledge-index:sha256:aa792a335c07f03f740f8f0f790f16782c7dba93bc4c94af6666214b985e291a"
)
ARTIFACT_NAMES = (
    "00_run_manifest.json",
    "01_change_set.json",
    "02_parser_base.json",
    "03_parser_head.json",
    "04_review_unit_build.json",
    "05_unit_fact_scopes.json",
    "06_feature_routing.json",
    "07_context_plan.json",
    "08_retrieval_request.json",
    "09_knowledge_index_summary.json",
    "10_evidence_pack.json",
    "11_assertions.json",
    "12_summary.json",
)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    assert isinstance(value, dict)
    return value


def test_e2e_example_has_complete_parseable_artifact_set() -> None:
    assert not (ARTIFACTS / "RUN_INCOMPLETE").exists()
    assert [path.name for path in sorted(ARTIFACTS.glob("*.json"))] == list(ARTIFACT_NAMES)
    for name in ARTIFACT_NAMES:
        _load_json(ARTIFACTS / name)


def test_e2e_example_summary_freezes_reported_chain_and_quality() -> None:
    summary = _load_json(ARTIFACTS / "12_summary.json")
    assert summary["chain_integrity_status"] == "pass"
    assert summary["semantic_retrieval_quality_status"] == "not_qualified"
    assert summary["review_unit_count"] == 15
    assert summary["unassigned_change_atom_count"] == 0
    assert summary["evidence_clause_relation_count"] == 67
    assert summary["unit_exact_clause_relation_count"] == 1
    assert summary["file_hint_clause_relation_count"] == 60
    assert summary["covered_dimension_instance_count"] == 0
    assert summary["uncovered_dimension_instance_count"] == 9
    assert summary["budget_exhausted_unit_count"] == 15

    assertions = _load_json(ARTIFACTS / "11_assertions.json")
    assert assertions["status"] == "pass"
    assert assertions["passed"] == 15
    assert assertions["failed"] == 0


def test_e2e_review_units_match_the_reviewed_expected_contract() -> None:
    expected = _load_json(EXAMPLE_ROOT / "inputs" / "expected_review_units.json")
    build = _load_json(ARTIFACTS / "04_review_unit_build.json")
    actual: list[dict[str, object]] = []
    file_results = build["file_results"]
    assert isinstance(file_results, list)
    for file_result in file_results:
        assert isinstance(file_result, dict)
        source_role = file_result["source_role"]
        units = file_result["units"]
        assert isinstance(units, list)
        for unit in units:
            assert isinstance(unit, dict)
            actual.append(
                {
                    "source_role": source_role,
                    "unit_kind": unit["unit_kind"],
                    "unit_symbol": unit["unit_symbol"],
                    "source_span": unit["source_span"],
                    "context_span": unit["context_span"],
                    "changed_lines": (
                        unit["changed_old_lines"]
                        if source_role == "base"
                        else unit["changed_new_lines"]
                    ),
                    "selection_reason": unit["selection_reason"],
                }
            )
    assert actual == expected["units"]


def test_e2e_report_links_and_status_are_presentation_safe() -> None:
    report = (EXAMPLE_ROOT / "REPORT.md").read_text(encoding="utf-8")
    assert "链路完整性：**PASS**" in report
    assert "语义检索质量：**NOT QUALIFIED" in report
    for relative_path in re.findall(r"\]\(((?:inputs|artifacts)/[^)]+)\)", report):
        assert (EXAMPLE_ROOT / relative_path).is_file(), relative_path


def test_e2e_artifacts_pin_index_and_do_not_persist_database_url() -> None:
    manifest = _load_json(ARTIFACTS / "00_run_manifest.json")
    index_summary = _load_json(ARTIFACTS / "09_knowledge_index_summary.json")
    assert index_summary["index_version"] == EXPECTED_INDEX_VERSION
    runtime = manifest["runtime"]
    assert isinstance(runtime, dict)
    assert runtime["database_url_persisted"] is False
    artifact_text = "".join(
        (ARTIFACTS / name).read_text(encoding="utf-8") for name in ARTIFACT_NAMES
    )
    assert "postgresql://" not in artifact_text
