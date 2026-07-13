from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Never, cast

from arkts_code_reviewer.code_analysis import (
    AnalysisResult,
    ChangeAtomInput,
    ChangedFileInput,
    ChangeSet,
    CodeAnalyzer,
    CodeSourceRef,
    CodeSourceSnapshot,
    ContextPlanResult,
    ReviewUnitSpan,
    normalize_change_set,
)
from arkts_code_reviewer.code_analysis.models import RetrievalQuery
from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.models import (
    AnnotationKind,
    AnnotationProvenance,
    Applicability,
    KnowledgeAnnotation,
    KnowledgeClause,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.retrieval.config import load_default_retrieval_config
from arkts_code_reviewer.retrieval.models import (
    EvidencePack,
    KnowledgeIndex,
    KnowledgeIndexRecord,
    RetrievalRequest,
    TargetPlatform,
)
from arkts_code_reviewer.retrieval.query_planner import build_retrieval_request
from arkts_code_reviewer.retrieval.service import RetrievalService

type ScenarioName = Literal["diff", "full", "multi_unit", "no_recall_degraded"]

_REPOSITORY = "retrieval-e2e"
_BASE_REVISION = "base"
_HEAD_REVISION = "head"
_RULE_ID = "E2E/TIMER"
_NOW = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
_CURATION_VERSION = f"knowledge-curation:sha256:{'c' * 64}"
_SOURCE_INDEX_VERSION = "retrieval-e2e-annotation-v1"
_TIMER_APIS = ("clearInterval", "clearTimeout", "setInterval", "setTimeout")


@dataclass(frozen=True)
class RetrievalE2ERun:
    scenario: ScenarioName
    analysis_result: AnalysisResult
    context_plan: ContextPlanResult
    request: RetrievalRequest
    evidence_pack: EvidencePack
    index: KnowledgeIndex
    snapshots: tuple[CodeSourceSnapshot, ...]
    compatibility_guard_armed: bool
    formal_graph_unchanged: bool


class _UnreadableCompatibilityQuery:
    """Fail immediately if the compatibility RetrievalQuery is inspected."""

    def __getattribute__(self, name: str) -> Never:
        raise AssertionError(
            "formal Retrieval must not inspect AnalysisResult.retrieval_query "
            f"(attempted attribute: {name})"
        )


def _snapshot(path: str, content: str, revision: str) -> CodeSourceSnapshot:
    inline = CodeSourceRef.inline(path, content, repository=_REPOSITORY)
    source_ref = CodeSourceRef.create(
        repository=inline.repository,
        revision=revision,
        path=inline.path,
        content_hash=inline.content_hash,
    )
    return CodeSourceSnapshot(source_ref=source_ref, content=content)


def _modified_change_set(
    *,
    path: str,
    base_source: str,
    head_source: str,
    atoms: tuple[ChangeAtomInput, ...],
) -> tuple[ChangeSet, tuple[CodeSourceSnapshot, ...]]:
    base = _snapshot(path, base_source, _BASE_REVISION)
    head = _snapshot(path, head_source, _HEAD_REVISION)
    change_set = normalize_change_set(
        repository=_REPOSITORY,
        base_revision=_BASE_REVISION,
        head_revision=_HEAD_REVISION,
        files=(
            ChangedFileInput(
                status="modified",
                old_path=path,
                new_path=path,
                old_snapshot=base,
                new_snapshot=head,
                atoms=atoms,
            ),
        ),
    )
    return change_set, (base, head)


def _scenario_input(
    scenario: ScenarioName,
) -> tuple[ChangeSet, tuple[CodeSourceSnapshot, ...], bool]:
    if scenario == "diff":
        change_set, snapshots = _modified_change_set(
            path="src/DiffTimer.ets",
            base_source=("function changed() {\n  setInterval(() => {}, 10)\n}\n"),
            head_source=("function changed() {\n  clearInterval(timer)\n}\n"),
            atoms=(
                ChangeAtomInput(
                    kind="replacement",
                    old_span=ReviewUnitSpan(2, 2),
                    new_span=ReviewUnitSpan(2, 2),
                    added_new_lines=(2,),
                    deleted_old_lines=(2,),
                ),
            ),
        )
        return change_set, snapshots, False

    if scenario == "full":
        source = "function fullFile() {\n  setInterval(() => {}, 1000)\n}\n"
        head = _snapshot("src/FullFile.ets", source, _HEAD_REVISION)
        source_lines = tuple(range(1, len(source.splitlines()) + 1))
        change_set = normalize_change_set(
            repository=_REPOSITORY,
            base_revision=_BASE_REVISION,
            head_revision=_HEAD_REVISION,
            files=(
                ChangedFileInput(
                    status="added",
                    old_path=None,
                    new_path=head.source_ref.path,
                    old_snapshot=None,
                    new_snapshot=head,
                    atoms=(
                        ChangeAtomInput(
                            kind="addition",
                            old_span=None,
                            new_span=ReviewUnitSpan(1, len(source_lines)),
                            added_new_lines=source_lines,
                        ),
                    ),
                ),
            ),
        )
        return change_set, (head,), False

    if scenario == "multi_unit":
        change_set, snapshots = _modified_change_set(
            path="src/MultiTimer.ets",
            base_source=(
                "function alpha() {\n"
                "  setInterval(() => {}, 10)\n"
                "}\n"
                "\n"
                "function beta() {\n"
                "  setTimeout(() => {}, 20)\n"
                "}\n"
            ),
            head_source=(
                "function alpha() {\n"
                "  clearInterval(alphaTimer)\n"
                "}\n"
                "\n"
                "function beta() {\n"
                "  clearTimeout(betaTimer)\n"
                "}\n"
            ),
            atoms=(
                ChangeAtomInput(
                    kind="replacement",
                    old_span=ReviewUnitSpan(2, 2),
                    new_span=ReviewUnitSpan(2, 2),
                    added_new_lines=(2,),
                    deleted_old_lines=(2,),
                ),
                ChangeAtomInput(
                    kind="replacement",
                    old_span=ReviewUnitSpan(6, 6),
                    new_span=ReviewUnitSpan(6, 6),
                    added_new_lines=(6,),
                    deleted_old_lines=(6,),
                ),
            ),
        )
        return change_set, snapshots, False

    change_set, snapshots = _modified_change_set(
        path="src/NoRecall.ets",
        base_source=("function total() {\n  return 1 + 1\n}\n"),
        head_source=("function total() {\n  return 2 + 2\n}\n"),
        atoms=(
            ChangeAtomInput(
                kind="replacement",
                old_span=ReviewUnitSpan(2, 2),
                new_span=ReviewUnitSpan(2, 2),
                added_new_lines=(2,),
                deleted_old_lines=(2,),
            ),
        ),
    )
    return change_set, snapshots, True


def _source_ref() -> SourceRef:
    return SourceRef(
        source_id="source-retrieval-e2e-timer",
        revision="a" * 40,
        relative_path="rules/retrieval-e2e-timer.md",
        anchor="L1-L2",
        authority="feature_spec",
        content_hash=f"sha256:{'b' * 64}",
    )


def _annotation() -> KnowledgeAnnotation:
    values: tuple[tuple[AnnotationKind, tuple[str, ...]], ...] = (
        ("api", _TIMER_APIS),
        ("dimension", ("DIM-06",)),
        ("domain", ("resource-management",)),
        ("tag", ("has_timer",)),
    )
    provenance = tuple(
        sorted(
            (
                AnnotationProvenance(
                    kind=kind,
                    value=value,
                    origin="human_curator",
                    evidence_ref=f"test-only:{kind}:{value}",
                )
                for kind, items in values
                for value in items
            ),
            key=lambda item: (item.kind, item.value, item.origin, item.evidence_ref),
        )
    )
    return KnowledgeAnnotation(
        target_kind="clause",
        target_id=_RULE_ID,
        index_version=_SOURCE_INDEX_VERSION,
        dimension_ids=("DIM-06",),
        tags=("has_timer",),
        apis=_TIMER_APIS,
        domains=("resource-management",),
        provenance=provenance,
        annotation_version="annotation-v1",
    )


def build_test_only_baselined_index(*, embedded: bool = False) -> KnowledgeIndex:
    """Create an in-memory fixture index; it is never a production publication."""

    source_ref = _source_ref()
    clause = KnowledgeClause(
        rule_id=_RULE_ID,
        rule_type="RULE",
        status="Baselined",
        authority=source_ref.authority,
        text="Timers must be paired with an appropriate cleanup operation.",
        heading_path=("Resource management", "Timers"),
        parent_context="Retrieval E2E fixture rule.",
        applicability=Applicability(),
        source_ref=source_ref,
        source_span=SourceSpan(start_line=1, end_line=2),
        doc_hash=source_ref.content_hash,
        curation_version=_CURATION_VERSION,
        created_at=_NOW,
        updated_at=_NOW,
    )
    record = KnowledgeIndexRecord(
        clause=clause,
        annotation=_annotation(),
        domains=("resource-management",),
        retrieval_text="Timer creation and cleanup must be paired.",
        token_count=8,
        embedding=(1.0, 0.0) if embedded else None,
    )
    retrieval_config = load_default_retrieval_config()
    return KnowledgeIndex.create(
        origin="golden_fixture",
        published_build_id=f"retrieval-fixture:sha256:{'f' * 64}",
        source_bundle_id=f"source-bundle:sha256:{'d' * 64}",
        feature_config_version=load_default_feature_config().fingerprint,
        annotation_version="annotation-v1",
        catalog_version="api-catalog-v1",
        retrieval_version=retrieval_config.version,
        retrieval_config_fingerprint=retrieval_config.fingerprint,
        embedding_model="fixture-embedding" if embedded else None,
        embedding_version="fixture-embedding-v1" if embedded else None,
        embedding_dimensions=2 if embedded else None,
        api_symbols=(),
        records=(record,),
    )


def _formal_state(
    analysis_result: AnalysisResult,
    context_plan: ContextPlanResult,
) -> tuple[object, ...]:
    return (
        analysis_result.change_set,
        analysis_result.review_unit_build_result,
        analysis_result.review_units,
        analysis_result.file_parse_results,
        analysis_result.unit_fact_scopes,
        analysis_result.feature_routing_result,
        context_plan,
    )


def run_scenario(scenario: ScenarioName) -> RetrievalE2ERun:
    change_set, snapshots, embedded_index = _scenario_input(scenario)
    snapshots_by_id = {snapshot.source_ref.source_ref_id: snapshot for snapshot in snapshots}
    analyzer = CodeAnalyzer()
    analysis_result = analyzer.analyze_change_set(change_set, snapshots_by_id)
    context_plan = analyzer.plan_context(
        analysis_result,
        source_snapshots=snapshots,
        code_context_budget=4096,
    )
    formal_before = deepcopy(_formal_state(analysis_result, context_plan))

    # The planner and service must use the formal graph above. Any compatibility
    # union access now raises, so successful E2E execution proves it was not read.
    analysis_result.retrieval_query = cast(
        RetrievalQuery,
        _UnreadableCompatibilityQuery(),
    )
    index = build_test_only_baselined_index(embedded=embedded_index)
    request = build_retrieval_request(
        analysis_result,
        context_plan,
        target_platform=TargetPlatform(
            release="OpenHarmony-5.0",
            api_level=12,
            language_mode="ArkTS",
        ),
        resolved_index_version=index.index_version,
        knowledge_token_budget=1024,
    )
    evidence_pack = RetrievalService(
        index,
        allow_golden_fixture=True,
    ).retrieve(request)
    formal_graph_unchanged = formal_before == _formal_state(
        analysis_result,
        context_plan,
    )
    result = RetrievalE2ERun(
        scenario=scenario,
        analysis_result=analysis_result,
        context_plan=context_plan,
        request=request,
        evidence_pack=evidence_pack,
        index=index,
        snapshots=snapshots,
        compatibility_guard_armed=True,
        formal_graph_unchanged=formal_graph_unchanged,
    )
    validate_run(result)
    return result


def _diagnostic_codes(run: RetrievalE2ERun) -> tuple[str, ...]:
    return tuple(
        sorted(
            {diagnostic.code for unit in run.evidence_pack.units for diagnostic in unit.diagnostics}
        )
    )


def validate_run(run: RetrievalE2ERun) -> None:
    request_unit_ids = tuple(unit.unit_id for unit in run.request.units)
    evidence_unit_ids = tuple(unit.unit_id for unit in run.evidence_pack.units)
    if run.index.origin != "golden_fixture" or any(
        record.clause.status != "Baselined" for record in run.index.records
    ):
        raise AssertionError("E2E must use an explicit test-only Baselined fixture index")
    if not run.compatibility_guard_armed or not run.formal_graph_unchanged:
        raise AssertionError("formal analysis state was not protected or remained mutable")
    if run.request.context_plan_id != run.context_plan.context_plan_id:
        raise AssertionError("RetrievalRequest lost its ContextPlan identity")
    if (
        run.request.feature_routing_id
        != run.analysis_result.feature_routing_result.feature_routing_id
    ):
        raise AssertionError("RetrievalRequest lost its Feature Routing identity")
    if run.request.index_version != run.index.index_version:
        raise AssertionError("RetrievalRequest lost its resolved index identity")
    if request_unit_ids != tuple(sorted(set(request_unit_ids))):
        raise AssertionError("RetrievalRequest Unit order is not deterministic")
    if evidence_unit_ids != request_unit_ids:
        raise AssertionError("EvidencePack does not preserve the formal request Units")
    if run.evidence_pack.request_id != run.request.request_id:
        raise AssertionError("EvidencePack does not reference the RetrievalRequest")

    clauses_by_unit = tuple(
        tuple(clause.rule_id for clause in unit.clauses) for unit in run.evidence_pack.units
    )
    if run.scenario in {"diff", "full", "multi_unit"}:
        if any(rule_ids != (_RULE_ID,) for rule_ids in clauses_by_unit):
            raise AssertionError("timer scenarios must recall the Baselined timer rule")
        if run.evidence_pack.degraded:
            raise AssertionError("exact-only timer scenarios must not degrade")
    if run.scenario == "diff" and len(request_unit_ids) != 2:
        raise AssertionError("replacement diff must retain both base and head Units")
    if run.scenario == "full":
        if len(request_unit_ids) != 1:
            raise AssertionError("whole-file addition must produce one fixture Unit")
        unit = run.analysis_result.review_units[0]
        if unit.changed_new_lines != [1, 2, 3]:
            raise AssertionError("whole-file addition must account for every source line")
    if run.scenario == "multi_unit":
        symbols = tuple(sorted(unit.unit_symbol for unit in run.analysis_result.review_units))
        if len(request_unit_ids) != 4 or symbols != ("alpha", "alpha", "beta", "beta"):
            raise AssertionError("multi-Unit diff must retain both owners on both sides")
    if run.scenario == "no_recall_degraded":
        if any(rule_ids for rule_ids in clauses_by_unit):
            raise AssertionError("unmatched source must not fabricate a recall")
        expected = {"embedding_unavailable", "empty_result"}
        if not expected.issubset(_diagnostic_codes(run)) or not run.evidence_pack.degraded:
            raise AssertionError("no-recall vector fallback must be explicit and degraded")


def run_all_scenarios() -> dict[ScenarioName, RetrievalE2ERun]:
    scenarios: tuple[ScenarioName, ...] = (
        "diff",
        "full",
        "multi_unit",
        "no_recall_degraded",
    )
    return {scenario: run_scenario(scenario) for scenario in scenarios}


def summarize_run(run: RetrievalE2ERun) -> dict[str, object]:
    return {
        "scenario": run.scenario,
        "index_origin": run.index.origin,
        "baselined_rule_ids": [record.clause.rule_id for record in run.index.records],
        "parser_layers": sorted(
            {item.analysis.parser_quality.layer for item in run.analysis_result.file_parse_results}
        ),
        "review_unit_count": len(run.analysis_result.review_units),
        "retrieval_unit_count": len(run.request.units),
        "evidence_unit_count": len(run.evidence_pack.units),
        "recalled_rule_ids": sorted(
            {clause.rule_id for unit in run.evidence_pack.units for clause in unit.clauses}
        ),
        "diagnostic_codes": list(_diagnostic_codes(run)),
        "degraded": run.evidence_pack.degraded,
        "compatibility_query_unread": run.compatibility_guard_armed,
        "formal_graph_unchanged": run.formal_graph_unchanged,
        "request_id": run.request.request_id,
        "evidence_pack_id": run.evidence_pack.evidence_pack_id,
    }


def build_report() -> dict[str, object]:
    runs = run_all_scenarios()
    return {
        "schema_version": "retrieval-e2e-report-v1",
        "status": "pass",
        "cases": [summarize_run(run) for run in runs.values()],
    }


def main() -> int:
    print(json.dumps(build_report(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
