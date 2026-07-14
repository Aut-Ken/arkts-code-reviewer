from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.feature_routing_validation.tag_truth import (
    TagTruthSuite,
    build_tag_truth_report,
    evaluate_tag_truth_suite,
    load_tag_truth_feature_config,
    load_tag_truth_suite,
    tag_truth_suite_fingerprint,
    verify_tag_truth_checkout,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "tests/tag_truth/relational_database/manifest.json"
TAGS_CONFIG = (
    REPO_ROOT / "tests/fixtures/feature_routing/tag_config_rdb_shadow_v1.yaml"
)
BASELINE = REPO_ROOT / "tests/tag_truth/relational_database/baselines/current.json"
SIDECAR_NODE_MODULE = (
    REPO_ROOT
    / "sidecars"
    / "arkts-parser"
    / "node_modules"
    / "tree-sitter-arkts"
    / "package.json"
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_checkout(tmp_path: Path, source: str) -> tuple[Path, str, str, int]:
    root = tmp_path / "checkout"
    path = root / "code/Sample/entry/src/main/ets/Test.ets"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.name", "Tag Truth Test")
    _git(root, "config", "user.email", "tag-truth@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")
    raw = path.read_bytes()
    return (
        root,
        _git(root, "rev-parse", "HEAD"),
        f"sha256:{hashlib.sha256(raw).hexdigest()}",
        len(raw.splitlines()),
    )


def _suite_payload(
    revision: str,
    content_hash: str,
    line_count: int,
    cases: list[dict[str, object]],
) -> dict[str, object]:
    reference = load_tag_truth_suite(MANIFEST)
    return {
        "schema_version": "tag-truth-v1",
        "suite_id": "tag-rdb-synthetic",
        "description": "Synthetic unit test for the real tag truth evaluator.",
        "annotation_policy_version": "test-policy-v1",
        "truth_status": "provisional",
        "repository": {
            "source_id": "synthetic",
            "repository": "synthetic-checkout",
            "revision": revision,
        },
        "base_feature_config_fingerprint": (
            reference.base_feature_config_fingerprint
        ),
        "candidate": reference.candidate.model_dump(mode="json"),
        "gates": {
            "min_positive_cases": 1,
            "min_negative_cases": 1,
            "min_hard_negative_cases": 1,
            "min_holdout_positive_cases": 1,
            "min_holdout_negative_cases": 1,
            "min_precision": 0.99,
            "min_recall": 0.95,
            "max_false_positives": 0,
            "max_file_hint_promotions": 0,
        },
        "sources": [
            {
                "alias": "src001",
                "path": "code/Sample/entry/src/main/ets/Test.ets",
                "content_sha256": content_hash,
                "line_count": line_count,
                "source_kind": "main",
                "app_scope": "code/Sample",
                "source_family_id": "family-sample",
            }
        ],
        "cases": cases,
    }


def test_real_tag_truth_manifest_has_approved_provisional_shape() -> None:
    suite = load_tag_truth_suite(MANIFEST)

    eligible = [case for case in suite.cases if case.metric_eligible]
    assert suite.truth_status == "provisional"
    assert len(suite.sources) == 104
    assert len(suite.cases) == 109
    assert sum(case.semantic_label == "positive" for case in eligible) == 40
    assert sum(case.semantic_label == "negative" for case in eligible) == 60
    assert sum(case.stratum == "indirect-wrapper" for case in eligible) == 5
    assert all(case.review_status == "proposed" for case in suite.cases)
    assert {
        case.case_id
        for case in suite.cases
        if case.semantic_label == "needs_taxonomy_decision"
    } == {"RDB-Q001"}


def test_behavior_baseline_is_bound_to_current_truth_suite() -> None:
    suite = load_tag_truth_suite(MANIFEST)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    assert baseline["suite_fingerprint"] == tag_truth_suite_fingerprint(suite)
    assert baseline["source_count"] == len(suite.sources)
    assert baseline["case_count"] == len(suite.cases)


def test_shadow_config_is_only_base_plus_unbound_draft_candidate() -> None:
    suite = load_tag_truth_suite(MANIFEST)
    base = load_default_feature_config()
    shadow = load_tag_truth_feature_config(suite, TAGS_CONFIG)

    assert base.fingerprint == suite.base_feature_config_fingerprint
    assert shadow.fingerprint == suite.candidate.config_fingerprint
    assert set(shadow.tags_by_id) == set(base.tags_by_id) | {
        "has_relational_database"
    }
    assert shadow.tags_by_id["has_relational_database"].status == "Draft"
    assert all(
        "has_relational_database" not in definition.triggers.any_tag
        for definition in (
            *shadow.dimension_config.dimensions,
            *shadow.dimension_config.review_questions,
        )
    )


def test_manifest_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"schema_version":"tag-truth-v1","schema_version":"tag-truth-v1"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_tag_truth_suite(path)


def test_manifest_rejects_metric_case_outside_main_source() -> None:
    suite = load_tag_truth_suite(MANIFEST)
    payload = suite.model_dump(mode="json")
    quarantined = next(
        case
        for case in payload["cases"]
        if case["semantic_label"] == "needs_taxonomy_decision"
    )
    quarantined["semantic_label"] = "positive"
    quarantined["metric_eligible"] = True
    quarantined["split"] = "calibration"

    with pytest.raises(ValueError, match="only src/main sources"):
        TagTruthSuite.model_validate(payload)


def test_manifest_rejects_unclassified_metric_negative() -> None:
    suite = load_tag_truth_suite(MANIFEST)
    payload = suite.model_dump(mode="json")
    negative = next(
        case
        for case in payload["cases"]
        if case["metric_eligible"] and case["semantic_label"] == "negative"
    )
    negative["stratum"] = "related-data-api"

    with pytest.raises(ValueError, match="hard-negative stratum"):
        TagTruthSuite.model_validate(payload)


def test_v1_manifest_cannot_claim_human_adjudication_without_receipts() -> None:
    suite = load_tag_truth_suite(MANIFEST)
    payload = suite.model_dump(mode="json")
    payload["truth_status"] = "adjudicated"

    with pytest.raises(ValueError, match="provisional"):
        TagTruthSuite.model_validate(payload)


def test_checkout_verifier_requires_clean_pinned_content(tmp_path: Path) -> None:
    source = "function plain() {\n  return 1\n}\n"
    root, revision, content_hash, line_count = _init_checkout(tmp_path, source)
    suite = TagTruthSuite.model_validate(
        _suite_payload(
            revision,
            content_hash,
            line_count,
            [
                {
                    "case_id": "RDB-N001",
                    "source_alias": "src001",
                    "changed_line": 2,
                    "expected_unit_kind": "function",
                    "expected_unit_symbol": "plain",
                    "semantic_label": "negative",
                    "expected_shadow_match": False,
                    "metric_eligible": True,
                    "split": "calibration",
                    "stratum": "hard-negative-local",
                    "evidence_lines": [2],
                    "rationale": "No RelationalStore use.",
                    "review_status": "proposed",
                }
            ],
        )
    )

    verified = verify_tag_truth_checkout(suite, root)
    assert verified.source_text_by_alias["src001"] == source

    source_path = root / "code/Sample/entry/src/main/ets/Test.ets"
    source_path.write_text(source + "// dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checkout must be clean"):
        verify_tag_truth_checkout(suite, root)


def test_report_keeps_contract_and_semantic_quality_separate() -> None:
    suite = load_tag_truth_suite(MANIFEST)
    config = load_tag_truth_feature_config(suite, TAGS_CONFIG)
    common = {
        "app_scope": "code/Synthetic",
        "parser_layer": "L1",
        "parser_error_nodes": 0,
        "parser_missing_nodes": 0,
        "scope_diagnostics": [],
        "candidate_occurrence_qualities": [],
    }
    rows = [
        {
            **common,
            "case_id": "RDB-P001",
            "candidate_occurrence_qualities": ["recovered"],
            "metric_eligible": True,
            "split": "calibration",
            "semantic_label": "positive",
            "actual_shadow_match": True,
            "contract_matched": True,
            "file_hint_match": False,
            "stratum": "direct-modern",
            "review_status": "proposed",
        },
        {
            **common,
            "case_id": "RDB-W001",
            "parser_error_nodes": 1,
            "metric_eligible": True,
            "split": "calibration",
            "semantic_label": "positive",
            "actual_shadow_match": False,
            "contract_matched": True,
            "file_hint_match": False,
            "stratum": "indirect-wrapper",
            "review_status": "proposed",
        },
        {
            **common,
            "case_id": "RDB-N001",
            "metric_eligible": True,
            "split": "acceptance_holdout",
            "semantic_label": "negative",
            "actual_shadow_match": False,
            "contract_matched": True,
            "file_hint_match": False,
            "stratum": "hard-negative-related-data-api",
            "review_status": "proposed",
        },
    ]

    report = build_tag_truth_report(suite, config, rows, parse_count=3)
    contract = report["contract"]
    metrics = report["provisional_semantic_metrics"]
    decision = report["quality_decision"]
    assert isinstance(contract, dict)
    assert isinstance(metrics, dict)
    assert isinstance(decision, dict)
    assert contract["perfect"] is True
    assert metrics["true_positive"] == 1
    assert metrics["false_negative"] == 1
    assert metrics["true_negative"] == 1
    assert metrics["recall"] == 0.5
    assert decision["activation_ready"] is False
    assert "truth_v1_is_provisional_only" in decision["activation_failures"]
    assert "metric_case_parser_quality_not_qualified" in decision["activation_failures"]
    assert "recovered_signal_not_separately_qualified" in decision["activation_failures"]


@pytest.mark.skipif(
    not SIDECAR_NODE_MODULE.is_file(),
    reason="ArkTS tree-sitter sidecar dependencies are not installed",
)
def test_synthetic_checkout_runs_real_owner_pipeline(tmp_path: Path) -> None:
    source = """import { relationalStore as rdb } from '@kit.ArkData'
function direct() {
  rdb.getRdbStore(null, { name: 'x.db', securityLevel: rdb.SecurityLevel.S1 })
}
function unrelated() {
  return 1
}
"""
    root, revision, content_hash, line_count = _init_checkout(tmp_path, source)
    suite = TagTruthSuite.model_validate(
        _suite_payload(
            revision,
            content_hash,
            line_count,
            [
                {
                    "case_id": "RDB-N001",
                    "source_alias": "src001",
                    "changed_line": 6,
                    "expected_unit_kind": "function",
                    "expected_unit_symbol": "unrelated",
                    "semantic_label": "negative",
                    "expected_shadow_match": False,
                    "metric_eligible": True,
                    "split": "calibration",
                    "stratum": "hard-negative-same-file",
                    "evidence_lines": [6],
                    "rationale": "Same file, different owner.",
                    "review_status": "proposed",
                },
                {
                    "case_id": "RDB-P001",
                    "source_alias": "src001",
                    "changed_line": 3,
                    "expected_unit_kind": "function",
                    "expected_unit_symbol": "direct",
                    "semantic_label": "positive",
                    "expected_shadow_match": True,
                    "metric_eligible": True,
                    "split": "calibration",
                    "stratum": "direct-modern",
                    "evidence_lines": [3],
                    "rationale": "Canonical alias use in the direct owner.",
                    "review_status": "proposed",
                },
            ],
        )
    )
    checkout = verify_tag_truth_checkout(suite, root)
    config = load_tag_truth_feature_config(suite, TAGS_CONFIG)

    report = evaluate_tag_truth_suite(suite, checkout, config)

    metrics = report["provisional_semantic_metrics"]
    contract = report["contract"]
    assert isinstance(metrics, dict)
    assert isinstance(contract, dict)
    assert metrics["true_positive"] == 1
    assert metrics["true_negative"] == 1
    assert metrics["false_positive"] == 0
    assert metrics["false_negative"] == 0
    assert contract["file_hint_promotion_count"] == 0
    assert report["parse_count"] == 1


@pytest.mark.skipif(
    not SIDECAR_NODE_MODULE.is_file(),
    reason="ArkTS tree-sitter sidecar dependencies are not installed",
)
def test_evaluator_rejects_two_cases_for_the_same_review_unit(tmp_path: Path) -> None:
    source = """import { relationalStore as rdb } from '@kit.ArkData'
function direct() {
  rdb.getRdbStore(null, { name: 'x.db', securityLevel: rdb.SecurityLevel.S1 })
}
"""
    root, revision, content_hash, line_count = _init_checkout(tmp_path, source)
    cases = []
    for index, line in enumerate((2, 3), 1):
        cases.append(
            {
                "case_id": f"RDB-P{index:03d}",
                "source_alias": "src001",
                "changed_line": line,
                "expected_unit_kind": "function",
                "expected_unit_symbol": "direct",
                "semantic_label": "positive",
                "expected_shadow_match": True,
                "metric_eligible": True,
                "split": "calibration",
                "stratum": "direct-modern",
                "evidence_lines": [line],
                "rationale": "Duplicate owner control.",
                "review_status": "proposed",
            }
        )
    suite = TagTruthSuite.model_validate(
        _suite_payload(revision, content_hash, line_count, cases)
    )
    checkout = verify_tag_truth_checkout(suite, root)
    config = load_tag_truth_feature_config(suite, TAGS_CONFIG)

    with pytest.raises(ValueError, match="unique ReviewUnits"):
        evaluate_tag_truth_suite(suite, checkout, config)
