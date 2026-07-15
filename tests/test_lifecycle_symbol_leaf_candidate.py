from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import (
    ArktsTreeSitterParser,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    ArktsFileAnalysisParser,
)
from arkts_code_reviewer.code_analysis.models import FileHunk
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.unit_facts import project
from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.feature_routing.engine import FeatureRouter
from arkts_code_reviewer.feature_routing.matcher import match_signal_pairs
from arkts_code_reviewer.feature_routing.models import (
    FEATURE_ROUTING_SCHEMA_VERSION,
    FEATURE_ROUTING_V2_SCHEMA_VERSION,
    FeatureSignal,
    NormalizedFeatureSignal,
)
from arkts_code_reviewer.retrieval_validation.lifecycle_symbol_leaf import (
    LIFECYCLE_SYMBOL_LEAF_CANDIDATE_FINGERPRINT,
    LIFECYCLE_SYMBOL_LEAF_CANDIDATE_VERSION,
    build_lifecycle_symbol_leaf_comparison,
    load_lifecycle_symbol_leaf_candidate_config,
)
from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
    TAG_RETRIEVAL_TRUTH_OBSERVATION_V2_SCHEMA_VERSION,
    load_tag_retrieval_truth,
    observe_tag_retrieval_truth,
    verify_tag_retrieval_truth_checkout,
)

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_TAGS = (
    ROOT / "tests/fixtures/feature_routing/tag_config_lifecycle_symbol_leaf_shadow_v1.yaml"
)
TRUTH_MANIFEST = ROOT / "tests/evaluation/tag_retrieval/manifest.json"
CODE_CHECKOUT = Path("/home/autken/Code/applications_app_samples")
SIDECAR_NODE_MODULE = (
    ROOT / "sidecars" / "arkts-parser" / "node_modules" / "tree-sitter-arkts" / "package.json"
)


def _scope(symbols: tuple[str, ...]) -> UnitFactScope:
    facts = ScopedFacts(symbols=symbols)
    return UnitFactScope(
        unit_id="unit:lifecycle-leaf",
        source_ref_id=f"code-source:sha256:{'a' * 64}",
        unit_exact=facts,
        file_hints=facts,
        diagnostics=(),
    )


def test_candidate_config_is_an_isolated_lifecycle_operator_replacement() -> None:
    base = load_default_feature_config()
    candidate = load_lifecycle_symbol_leaf_candidate_config(CANDIDATE_TAGS)

    assert base.tag_config.schema_version == "tag-config-v1"
    assert base.tag_config.version == "tags-v1"
    assert base.fingerprint == (
        "feature-config:sha256:bb241e9bdc54a9e6418e6be03a04593b8cf854838aec4d8644faa624eff7ae9c"
    )
    assert candidate.tag_config.schema_version == "tag-config-v3"
    assert candidate.tag_config.version == LIFECYCLE_SYMBOL_LEAF_CANDIDATE_VERSION
    assert candidate.fingerprint == LIFECYCLE_SYMBOL_LEAF_CANDIDATE_FINGERPRINT
    assert candidate.dimension_config == base.dimension_config
    assert [
        tag_id
        for tag_id in base.tags_by_id
        if base.tags_by_id[tag_id] != candidate.tags_by_id[tag_id]
    ] == ["has_lifecycle"]


def test_symbol_leaf_match_preserves_qualified_raw_signal_and_operator() -> None:
    candidate = load_lifecycle_symbol_leaf_candidate_config(CANDIDATE_TAGS)
    profile = (
        FeatureRouter(candidate)
        .route([_scope(("Index.aboutToAppear", "aboutToDisappear"))])
        .units[0]
    )
    lifecycle = next(
        match
        for match in profile.tag_matches
        if match.tag_id == "has_lifecycle" and match.scope == "unit_exact"
    )

    assert profile.exact_tags == ("has_lifecycle",)
    assert profile.feature_config_version == candidate.fingerprint
    assert [signal.to_dict() for signal in lifecycle.signals] == [
        {
            "kind": "symbols",
            "normalized_value": "aboutToAppear",
            "operator": "any_symbol_leaf",
            "value": "Index.aboutToAppear",
        },
        {
            "kind": "symbols",
            "normalized_value": "aboutToDisappear",
            "operator": "any_symbol_leaf",
            "value": "aboutToDisappear",
        },
    ]
    assert match_signal_pairs(
        candidate.tags_by_id["has_lifecycle"],
        _scope(("Index.aboutToAppear",)).unit_exact,
    ) == (("symbols", "Index.aboutToAppear"),)


@pytest.mark.parametrize(
    "symbol",
    [
        "Index.notaboutToAppear",
        "Index.aboutToAppearExtra",
        "Index.AboutToAppear",
    ],
)
def test_symbol_leaf_match_is_case_sensitive_and_segment_bounded(symbol: str) -> None:
    candidate = load_lifecycle_symbol_leaf_candidate_config(CANDIDATE_TAGS)
    profile = FeatureRouter(candidate).route([_scope((symbol,))]).units[0]

    assert "has_lifecycle" not in profile.exact_tags


def test_symbol_leaf_candidate_records_ordinary_class_same_name_limitation() -> None:
    candidate = load_lifecycle_symbol_leaf_candidate_config(CANDIDATE_TAGS)
    profile = FeatureRouter(candidate).route([_scope(("Helper.aboutToAppear",))]).units[0]

    assert "has_lifecycle" in profile.exact_tags


@pytest.mark.skipif(
    not SIDECAR_NODE_MODULE.is_file(),
    reason="ArkTS tree-sitter sidecar dependencies are not installed",
)
def test_parser_pipeline_keeps_sibling_comment_string_and_attribute_out_of_exact() -> None:
    source = """@Component
struct Index {
  aboutToAppear(): void {}
  build() {
    // aboutToDisappear
    Text('onPageShow')
      .onReady(() => {})
  }
}
"""
    source_ref = CodeSourceRef.inline("src/Index.ets", source)
    parsed = ArktsFileAnalysisParser(ArktsTreeSitterParser()).parse_file(
        source_ref,
        source,
    )
    router = FeatureRouter(load_lifecycle_symbol_leaf_candidate_config(CANDIDATE_TAGS))

    profiles = []
    scopes = []
    for changed_line in (3, 6):
        built = ReviewUnitBuilder().build_file_result(
            source_ref.path,
            source,
            parsed.compatibility_facts,
            "diff",
            [FileHunk(new_start=changed_line, new_lines=1)],
            source_ref_id=source_ref.source_ref_id,
        )
        assert len(built.units) == 1
        scope = project(parsed.analysis, built.units[0])
        scopes.append(scope)
        profiles.append(router.route([scope]).units[0])

    lifecycle_scope, build_scope = scopes
    lifecycle_profile, build_profile = profiles
    assert lifecycle_scope.unit_exact.symbols == ("Index.aboutToAppear",)
    assert lifecycle_profile.exact_tags == ("has_lifecycle",)
    assert "RQ-lifecycle" in lifecycle_profile.review_question_ids
    assert "onReady" in build_scope.unit_exact.attributes
    assert "'onPageShow'" in build_scope.unit_exact.string_literals
    assert "has_lifecycle" not in build_profile.exact_tags
    assert "has_lifecycle" in build_profile.routing_tags
    assert "RQ-lifecycle" not in build_profile.review_question_ids


def test_legacy_signal_shape_is_unchanged_and_leaf_provenance_fails_closed() -> None:
    assert FeatureSignal(kind="symbols", value="aboutToAppear").to_dict() == {
        "kind": "symbols",
        "value": "aboutToAppear",
    }
    with pytest.raises(TypeError):
        NormalizedFeatureSignal(  # type: ignore[call-arg]
            kind="symbols",
            value="Index.aboutToAppear",
        )
    with pytest.raises(ValueError, match="does not match value"):
        NormalizedFeatureSignal(
            kind="symbols",
            value="Index.aboutToAppear",
            operator="any_symbol_leaf",
            normalized_value="aboutToDisappear",
        )
    with pytest.raises(ValueError, match="requires kind=symbols"):
        NormalizedFeatureSignal(
            kind="apis",
            value="Index.aboutToAppear",
            operator="any_symbol_leaf",
            normalized_value="aboutToAppear",
        )


@pytest.mark.integration
@pytest.mark.skipif(
    not CODE_CHECKOUT.is_dir(),
    reason="pinned local applications_app_samples checkout is not available",
)
def test_candidate_against_provisional_tag_retrieval_truth() -> None:
    truth = load_tag_retrieval_truth(TRUTH_MANIFEST)
    checkout = verify_tag_retrieval_truth_checkout(truth, CODE_CHECKOUT)
    baseline = observe_tag_retrieval_truth(
        truth,
        checkout,
        feature_config=load_default_feature_config(),
        observation_schema_version=TAG_RETRIEVAL_TRUTH_OBSERVATION_V2_SCHEMA_VERSION,
    )
    candidate = observe_tag_retrieval_truth(
        truth,
        checkout,
        feature_config=load_lifecycle_symbol_leaf_candidate_config(CANDIDATE_TAGS),
        observation_schema_version=TAG_RETRIEVAL_TRUTH_OBSERVATION_V2_SCHEMA_VERSION,
    )
    comparison = build_lifecycle_symbol_leaf_comparison(
        baseline,
        candidate,
        truth_suite=truth,
    )

    assert baseline["schema_version"] == "tag-retrieval-truth-observation-v2"
    assert candidate["schema_version"] == "tag-retrieval-truth-observation-v2"
    assert baseline["feature_routing_schema_version"] == FEATURE_ROUTING_SCHEMA_VERSION
    assert candidate["feature_routing_schema_version"] == FEATURE_ROUTING_V2_SCHEMA_VERSION
    assert comparison["candidate_lifecycle_target_case_metrics"] == {
        "positive_case_count": 7,
        "negative_case_count": 5,
        "true_positive": 7,
        "false_positive": 0,
        "false_negative": 0,
        "true_negative": 5,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert comparison["candidate_lifecycle_target_case_metrics_by_legacy_split"] == {
        "calibration": {
            "positive_case_count": 4,
            "negative_case_count": 4,
            "true_positive": 4,
            "false_positive": 0,
            "false_negative": 0,
            "true_negative": 4,
            "precision": 1.0,
            "recall": 1.0,
        },
        "acceptance_holdout": {
            "positive_case_count": 3,
            "negative_case_count": 1,
            "true_positive": 3,
            "false_positive": 0,
            "false_negative": 0,
            "true_negative": 1,
            "precision": 1.0,
            "recall": 1.0,
        },
    }
    assert comparison["observed_changed_tag_ids"] == ["has_lifecycle"]
    assert comparison["candidate_contract_mismatch_case_ids"] == []
    assert comparison["introduced_contract_mismatch_case_ids"] == []
    assert comparison["lifecycle_target_addition_case_ids"] == [
        "TR-LIFE-001",
        "TR-LIFE-003",
        "TR-LIFE-005",
        "TR-LIFE-007",
        "TR-LIFE-009",
        "TR-LIFE-010",
        "TR-LIFE-012",
    ]
    assert comparison["declared_required_co_tag_lifecycle_addition_case_ids"] == ["TR-TIMER-008"]
    assert comparison["adjudicated_positive_cross_target_lifecycle_addition_case_ids"] == [
        "TR-NET-008",
        "TR-STATE-007",
        "TR-STATE-009",
        "TR-STATE-010",
        "TR-STATE-012",
        "TR-TIMER-004",
        "TR-TIMER-010",
    ]
    assert comparison["unadjudicated_cross_target_lifecycle_addition_case_ids"] == []
    assert comparison["all_lifecycle_exact_addition_case_ids"] == [
        "TR-LIFE-001",
        "TR-LIFE-003",
        "TR-LIFE-005",
        "TR-LIFE-007",
        "TR-LIFE-009",
        "TR-LIFE-010",
        "TR-LIFE-012",
        "TR-NET-008",
        "TR-STATE-007",
        "TR-STATE-009",
        "TR-STATE-010",
        "TR-STATE-012",
        "TR-TIMER-004",
        "TR-TIMER-008",
        "TR-TIMER-010",
    ]
    assert comparison["symbol_leaf_provenance_failure_case_ids"] == []
    assert comparison["lifecycle_trace_provenance_failure_case_ids"] == []
    assert comparison["resolved_contract_mismatch_case_ids"] == [
        "TR-LIFE-001",
        "TR-LIFE-003",
        "TR-LIFE-005",
        "TR-LIFE-007",
        "TR-LIFE-009",
        "TR-LIFE-010",
        "TR-LIFE-012",
        "TR-TIMER-008",
    ]
    declared_gate = cast(dict[str, object], comparison["declared_contract_gate"])
    assert declared_gate == {"passed": True, "failures": []}
    evidence_gate = cast(dict[str, object], comparison["candidate_evidence_gate"])
    assert evidence_gate["passed"] is False
    assert "development_regression_only" in cast(
        list[str],
        evidence_gate["failures"],
    )
    quality = cast(dict[str, object], comparison["quality_decision"])
    assert quality["activation_ready"] is False
    assert "ordinary_class_same_name_owner_not_distinguishable" in cast(
        list[str],
        quality["activation_failures"],
    )
    assert "independent_adjudicated_holdout_missing" in cast(
        list[str],
        quality["activation_failures"],
    )
