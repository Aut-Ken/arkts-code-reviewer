from __future__ import annotations

import pytest

from tools.run_retrieval_e2e import RetrievalE2ERun, ScenarioName, run_all_scenarios


@pytest.fixture(scope="module")
def e2e_runs() -> dict[ScenarioName, RetrievalE2ERun]:
    return run_all_scenarios()


def test_diff_flows_from_formal_analysis_to_evidence_pack(
    e2e_runs: dict[ScenarioName, RetrievalE2ERun],
) -> None:
    run = e2e_runs["diff"]

    assert run.compatibility_guard_armed is True
    assert run.formal_graph_unchanged is True
    assert run.request.context_plan_id == run.context_plan.context_plan_id
    assert (
        run.request.feature_routing_id
        == run.analysis_result.feature_routing_result.feature_routing_id
    )
    assert [unit.unit_id for unit in run.evidence_pack.units] == [
        unit.unit_id for unit in run.request.units
    ]
    assert {api for unit in run.request.units for api in unit.exact_signals.apis} == {
        "clearInterval",
        "setInterval",
    }
    assert all(
        [clause.rule_id for clause in unit.clauses] == ["E2E/TIMER"]
        for unit in run.evidence_pack.units
    )
    assert all(
        match.scope == "unit_exact"
        for unit in run.evidence_pack.units
        for clause in unit.clauses
        for match in clause.matched_by
    )


def test_full_file_addition_accounts_for_every_line(
    e2e_runs: dict[ScenarioName, RetrievalE2ERun],
) -> None:
    run = e2e_runs["full"]

    assert len(run.request.units) == 1
    assert run.analysis_result.change_set is not None
    assert run.analysis_result.change_set.files[0].status == "added"
    assert run.analysis_result.review_units[0].changed_new_lines == [1, 2, 3]
    assert run.analysis_result.review_units[0].full_text == run.snapshots[0].content.rstrip("\n")
    assert run.request.units[0].exact_tags == ("has_timer",)
    assert run.evidence_pack.units[0].clauses[0].rule_id == "E2E/TIMER"
    assert run.evidence_pack.degraded is False


def test_multi_unit_diff_preserves_owner_and_evidence_alignment(
    e2e_runs: dict[ScenarioName, RetrievalE2ERun],
) -> None:
    run = e2e_runs["multi_unit"]

    assert len(run.analysis_result.review_units) == 4
    assert sorted(unit.unit_symbol for unit in run.analysis_result.review_units) == [
        "alpha",
        "alpha",
        "beta",
        "beta",
    ]
    assert [unit.unit_id for unit in run.request.units] == sorted(
        unit.unit_id for unit in run.request.units
    )
    assert [unit.unit_id for unit in run.evidence_pack.units] == [
        unit.unit_id for unit in run.request.units
    ]
    assert all(
        tuple(clause.rule_id for clause in unit.clauses) == ("E2E/TIMER",)
        for unit in run.evidence_pack.units
    )


def test_no_recall_and_vector_degradation_are_explicit(
    e2e_runs: dict[ScenarioName, RetrievalE2ERun],
) -> None:
    run = e2e_runs["no_recall_degraded"]

    assert run.index.origin == "golden_fixture"
    assert all(record.clause.status == "Baselined" for record in run.index.records)
    assert all(not unit.clauses for unit in run.evidence_pack.units)
    assert all(
        {diagnostic.code for diagnostic in unit.diagnostics}
        >= {"embedding_unavailable", "empty_result"}
        for unit in run.evidence_pack.units
    )
    assert run.evidence_pack.degraded is True
