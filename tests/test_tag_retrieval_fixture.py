from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path
from typing import cast

import pytest

from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
    TAG_RETRIEVAL_TRUTH_OBSERVATION_SCHEMA_VERSION,
    FixtureRepository,
    TagRetrievalKnowledgeFixture,
    TagRetrievalTruthSuite,
    load_tag_retrieval_knowledge_fixture,
    load_tag_retrieval_truth,
    observe_tag_retrieval_truth,
    tag_retrieval_knowledge_fingerprint,
    tag_retrieval_truth_fingerprint,
    verify_fixture_repository,
    verify_tag_retrieval_knowledge_checkout,
    verify_tag_retrieval_truth_checkout,
)

ROOT = Path(__file__).resolve().parents[1]
TRUTH_MANIFEST = ROOT / "tests/evaluation/tag_retrieval/manifest.json"
KNOWLEDGE_FIXTURE = ROOT / "tests/evaluation/tag_retrieval/knowledge_fixture.json"
CODE_CHECKOUT = Path("/home/autken/Code/applications_app_samples")
DOCS_CHECKOUT = Path("/home/autken/Code/arkts-knowledge/sources/official-docs/openharmony-docs")

EXPECTED_BY_TAG = {
    "has_lifecycle": {
        "case_count": 12,
        "expected_exact_positive": 7,
        "actual_exact_positive": 0,
        "exact_mismatch_count": 7,
        "routing_mismatch_count": 0,
        "co_tag_mismatch_count": 0,
        "case_contract_mismatch_count": 7,
    },
    "has_network": {
        "case_count": 12,
        "expected_exact_positive": 7,
        "actual_exact_positive": 7,
        "exact_mismatch_count": 0,
        "routing_mismatch_count": 0,
        "co_tag_mismatch_count": 0,
        "case_contract_mismatch_count": 0,
    },
    "has_state_management": {
        "case_count": 12,
        "expected_exact_positive": 7,
        "actual_exact_positive": 7,
        "exact_mismatch_count": 0,
        "routing_mismatch_count": 0,
        "co_tag_mismatch_count": 0,
        "case_contract_mismatch_count": 0,
    },
    "has_timer": {
        "case_count": 12,
        "expected_exact_positive": 7,
        "actual_exact_positive": 7,
        "exact_mismatch_count": 0,
        "routing_mismatch_count": 0,
        "co_tag_mismatch_count": 1,
        "case_contract_mismatch_count": 1,
    },
}

EXPECTED_BY_TAG_AND_SPLIT = {
    "has_lifecycle": {
        "calibration": {
            "case_count": 8,
            "expected_exact_positive": 4,
            "actual_exact_positive": 0,
            "exact_mismatch_count": 4,
            "routing_mismatch_count": 0,
            "co_tag_mismatch_count": 0,
            "case_contract_mismatch_count": 4,
        },
        "acceptance_holdout": {
            "case_count": 4,
            "expected_exact_positive": 3,
            "actual_exact_positive": 0,
            "exact_mismatch_count": 3,
            "routing_mismatch_count": 0,
            "co_tag_mismatch_count": 0,
            "case_contract_mismatch_count": 3,
        },
    },
    "has_network": {
        "calibration": {
            "case_count": 8,
            "expected_exact_positive": 5,
            "actual_exact_positive": 5,
            "exact_mismatch_count": 0,
            "routing_mismatch_count": 0,
            "co_tag_mismatch_count": 0,
            "case_contract_mismatch_count": 0,
        },
        "acceptance_holdout": {
            "case_count": 4,
            "expected_exact_positive": 2,
            "actual_exact_positive": 2,
            "exact_mismatch_count": 0,
            "routing_mismatch_count": 0,
            "co_tag_mismatch_count": 0,
            "case_contract_mismatch_count": 0,
        },
    },
    "has_state_management": {
        "calibration": {
            "case_count": 8,
            "expected_exact_positive": 4,
            "actual_exact_positive": 4,
            "exact_mismatch_count": 0,
            "routing_mismatch_count": 0,
            "co_tag_mismatch_count": 0,
            "case_contract_mismatch_count": 0,
        },
        "acceptance_holdout": {
            "case_count": 4,
            "expected_exact_positive": 3,
            "actual_exact_positive": 3,
            "exact_mismatch_count": 0,
            "routing_mismatch_count": 0,
            "co_tag_mismatch_count": 0,
            "case_contract_mismatch_count": 0,
        },
    },
    "has_timer": {
        "calibration": {
            "case_count": 8,
            "expected_exact_positive": 4,
            "actual_exact_positive": 4,
            "exact_mismatch_count": 0,
            "routing_mismatch_count": 0,
            "co_tag_mismatch_count": 0,
            "case_contract_mismatch_count": 0,
        },
        "acceptance_holdout": {
            "case_count": 4,
            "expected_exact_positive": 3,
            "actual_exact_positive": 3,
            "exact_mismatch_count": 0,
            "routing_mismatch_count": 0,
            "co_tag_mismatch_count": 1,
            "case_contract_mismatch_count": 1,
        },
    },
}


def test_truth_manifest_has_provisional_balanced_shape() -> None:
    suite = load_tag_retrieval_truth(TRUTH_MANIFEST)

    assert suite.truth_status == "provisional"
    assert len(suite.sources) == 36
    assert len(suite.cases) == 48
    assert all(case.review_status == "proposed" for case in suite.cases)
    assert all("/src/main/" in source.path for source in suite.sources)
    assert not any(source.path.startswith("code/DocsSample/") for source in suite.sources)
    for tag_id in (
        "has_lifecycle",
        "has_network",
        "has_state_management",
        "has_timer",
    ):
        cases = [case for case in suite.cases if case.target_tag == tag_id]
        assert Counter(case.stratum for case in cases) == {
            "direct_positive": 6,
            "same_file_hint_only_hard_negative": 3,
            "ownership_lookalike_negative": 2,
            "multi_tag_positive": 1,
        }
        assert Counter(case.split for case in cases) == {
            "calibration": 8,
            "acceptance_holdout": 4,
        }
    assert tag_retrieval_truth_fingerprint(suite).startswith("tag-retrieval-truth:sha256:")


def test_truth_stratum_rejects_inconsistent_exact_expectation() -> None:
    suite = load_tag_retrieval_truth(TRUTH_MANIFEST)
    payload = suite.model_dump(mode="json")
    lifecycle = next(case for case in payload["cases"] if case["case_id"] == "TR-LIFE-001")
    lifecycle["expected_exact_tag"] = False

    with pytest.raises(ValueError, match="contradicts its stratum"):
        TagRetrievalTruthSuite.model_validate(payload)


def test_fixture_repository_identities_are_frozen() -> None:
    suite = load_tag_retrieval_truth(TRUTH_MANIFEST)
    truth_payload = suite.model_dump(mode="json")
    truth_payload["repository"]["source_id"] = "substitute-source"

    with pytest.raises(ValueError, match="truth suite repository identity drift"):
        TagRetrievalTruthSuite.model_validate(truth_payload)

    fixture = load_tag_retrieval_knowledge_fixture(KNOWLEDGE_FIXTURE)
    knowledge_payload = fixture.model_dump(mode="json")
    knowledge_payload["repository"]["remote"] = "https://example.invalid/docs.git"

    with pytest.raises(ValueError, match="knowledge fixture repository identity drift"):
        TagRetrievalKnowledgeFixture.model_validate(knowledge_payload)


def test_truth_source_family_must_equal_verified_app_scope() -> None:
    suite = load_tag_retrieval_truth(TRUTH_MANIFEST)
    payload = suite.model_dump(mode="json")
    payload["sources"][0]["source_family_id"] = "code/substitute-family"

    with pytest.raises(ValueError, match="family must equal its verified app_scope"):
        TagRetrievalTruthSuite.model_validate(payload)


def test_truth_cannot_claim_adjudication_without_review_receipts() -> None:
    suite = load_tag_retrieval_truth(TRUTH_MANIFEST)
    payload = suite.model_dump(mode="json")
    payload["truth_status"] = "adjudicated"

    with pytest.raises(ValueError, match="provisional"):
        TagRetrievalTruthSuite.model_validate(payload)


def test_knowledge_fixture_is_provisional_and_non_production() -> None:
    fixture = load_tag_retrieval_knowledge_fixture(KNOWLEDGE_FIXTURE)

    assert fixture.fixture_role == "golden_fixture"
    assert fixture.truth_status == "provisional"
    assert fixture.source_authority == "official_documentation"
    assert len(fixture.documents) == 18
    assert len(fixture.clauses) == 24
    assert Counter(clause.target_tag for clause in fixture.clauses) == {
        "has_lifecycle": 7,
        "has_network": 6,
        "has_state_management": 8,
        "has_timer": 3,
    }
    assert all(clause.review_status == "proposed" for clause in fixture.clauses)
    timer_clauses = [clause for clause in fixture.clauses if clause.target_tag == "has_timer"]
    assert all(clause.conditional_only for clause in timer_clauses)
    assert sum(bool(clause.supporting_source_spans) for clause in timer_clauses) == 2
    assert tag_retrieval_knowledge_fingerprint(fixture).startswith(
        "tag-retrieval-knowledge:sha256:"
    )


def test_knowledge_fixture_cannot_be_promoted_by_changing_one_field() -> None:
    fixture = load_tag_retrieval_knowledge_fixture(KNOWLEDGE_FIXTURE)
    payload = fixture.model_dump(mode="json")
    payload["fixture_role"] = "publication"

    with pytest.raises(ValueError, match="golden_fixture"):
        TagRetrievalKnowledgeFixture.model_validate(payload)


def test_loaders_reject_duplicate_json_keys(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":"x","schema_version":"x"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_tag_retrieval_truth(duplicate)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_tag_retrieval_knowledge_fixture(duplicate)


def _git(checkout: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_repository_verifier_checks_origin_revision_and_cleanliness(tmp_path: Path) -> None:
    checkout = tmp_path / "fixture"
    checkout.mkdir()
    _git(checkout, "init", "--quiet")
    _git(checkout, "config", "user.email", "fixture@example.invalid")
    _git(checkout, "config", "user.name", "Fixture Test")
    remote = "https://example.invalid/fixture.git"
    _git(checkout, "remote", "add", "origin", remote)
    (checkout / "tracked.txt").write_text("pinned\n", encoding="utf-8")
    _git(checkout, "add", "tracked.txt")
    _git(checkout, "commit", "--quiet", "-m", "fixture")
    revision = _git(checkout, "rev-parse", "HEAD")
    repository = FixtureRepository(
        source_id="fixture-source",
        repository="fixture/repository",
        remote=remote,
        revision=revision,
    )

    assert verify_fixture_repository(repository, checkout) == checkout.resolve()

    wrong_remote = FixtureRepository(
        source_id=repository.source_id,
        repository=repository.repository,
        remote="https://example.invalid/substitute.git",
        revision=revision,
    )
    with pytest.raises(ValueError, match="remote mismatch"):
        verify_fixture_repository(wrong_remote, checkout)

    wrong_revision = FixtureRepository(
        source_id=repository.source_id,
        repository=repository.repository,
        remote=remote,
        revision="0" * 40,
    )
    with pytest.raises(ValueError, match="revision mismatch"):
        verify_fixture_repository(wrong_revision, checkout)

    (checkout / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be clean"):
        verify_fixture_repository(repository, checkout)


@pytest.mark.integration
@pytest.mark.skipif(
    not CODE_CHECKOUT.is_dir() or not DOCS_CHECKOUT.is_dir(),
    reason="pinned local code and official-docs checkouts are not available",
)
def test_pinned_external_sources_and_current_tag_observation() -> None:
    suite = load_tag_retrieval_truth(TRUTH_MANIFEST)
    code = verify_tag_retrieval_truth_checkout(suite, CODE_CHECKOUT)
    fixture = load_tag_retrieval_knowledge_fixture(KNOWLEDGE_FIXTURE)
    docs = verify_tag_retrieval_knowledge_checkout(fixture, DOCS_CHECKOUT)

    assert len(code.source_text_by_alias) == 36
    assert len(docs.document_bytes_by_alias) == 18
    observation = observe_tag_retrieval_truth(suite, code)
    assert observation["schema_version"] == TAG_RETRIEVAL_TRUTH_OBSERVATION_SCHEMA_VERSION
    assert set(observation) == {
        "schema_version",
        "suite_id",
        "truth_status",
        "source_count",
        "case_count",
        "parse_count",
        "by_tag",
        "by_tag_and_split",
        "file_diagnostic_case_counts",
        "scope_diagnostic_case_counts",
        "exact_mismatch_case_ids",
        "routing_mismatch_case_ids",
        "co_tag_mismatch_case_ids",
        "case_contract_mismatch_case_ids",
        "parser_risk_case_ids",
        "cases",
    }
    legacy_rows = cast(list[dict[str, object]], observation["cases"])
    assert set(legacy_rows[0]) == {
        "case_id",
        "target_tag",
        "split",
        "stratum",
        "review_status",
        "expected_exact_tag",
        "actual_exact_tag",
        "expected_routing_tag",
        "actual_routing_tag",
        "exact_matches_truth",
        "routing_matches_truth",
        "missing_required_co_tags",
        "unit_id",
        "unit_kind",
        "unit_symbol",
        "expected_source_span",
        "actual_source_span",
        "parser_layer",
        "parser_error_nodes",
        "parser_missing_nodes",
        "file_diagnostics",
        "scope_diagnostics",
    }
    assert observation["case_count"] == 48
    assert observation["parse_count"] == 36
    assert observation["file_diagnostic_case_counts"] == {"unresolved_fact_owner": 34}
    assert observation["scope_diagnostic_case_counts"] == {}
    assert observation["parser_risk_case_ids"] == [
        "TR-LIFE-001",
        "TR-LIFE-002",
        "TR-LIFE-003",
        "TR-LIFE-004",
        "TR-LIFE-007",
        "TR-LIFE-008",
        "TR-LIFE-009",
        "TR-LIFE-010",
        "TR-LIFE-012",
        "TR-NET-001",
        "TR-NET-003",
        "TR-NET-004",
        "TR-NET-005",
        "TR-NET-007",
        "TR-NET-009",
        "TR-NET-010",
        "TR-NET-011",
        "TR-NET-012",
        "TR-STATE-003",
        "TR-STATE-004",
        "TR-STATE-005",
        "TR-STATE-006",
        "TR-STATE-007",
        "TR-STATE-008",
        "TR-STATE-010",
        "TR-STATE-011",
        "TR-STATE-012",
        "TR-TIMER-002",
        "TR-TIMER-003",
        "TR-TIMER-004",
        "TR-TIMER-007",
        "TR-TIMER-009",
        "TR-TIMER-011",
        "TR-TIMER-012",
    ]
    by_tag = cast(dict[str, dict[str, int]], observation["by_tag"])
    assert by_tag == EXPECTED_BY_TAG
    by_tag_and_split = cast(
        dict[str, dict[str, dict[str, int]]],
        observation["by_tag_and_split"],
    )
    assert by_tag_and_split == EXPECTED_BY_TAG_AND_SPLIT
    assert observation["exact_mismatch_case_ids"] == [
        "TR-LIFE-001",
        "TR-LIFE-003",
        "TR-LIFE-005",
        "TR-LIFE-007",
        "TR-LIFE-009",
        "TR-LIFE-010",
        "TR-LIFE-012",
    ]
    assert observation["routing_mismatch_case_ids"] == []
    assert observation["co_tag_mismatch_case_ids"] == ["TR-TIMER-008"]
    assert observation["case_contract_mismatch_case_ids"] == [
        "TR-LIFE-001",
        "TR-LIFE-003",
        "TR-LIFE-005",
        "TR-LIFE-007",
        "TR-LIFE-009",
        "TR-LIFE-010",
        "TR-LIFE-012",
        "TR-TIMER-008",
    ]
