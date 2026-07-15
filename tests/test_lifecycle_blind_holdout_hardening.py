from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.code_analysis.models import SourceSpan
from arkts_code_reviewer.retrieval_validation import lifecycle_blind_holdout as holdout
from arkts_code_reviewer.retrieval_validation.lifecycle_blind_holdout import (
    LIFECYCLE_CRITICAL_NEGATIVE_STRATA,
    LIFECYCLE_EVALUATION_HARNESS_PATHS,
    LIFECYCLE_REQUIRED_STRATUM_MINIMUMS,
    LIFECYCLE_RUNTIME_REQUIRED_PATHS,
    LIFECYCLE_SELECTION_POLICY_PATH,
    CandidateFreeze,
    DevelopmentExclusions,
    HoldoutQualityGates,
    HoldoutRepository,
    HoldoutSelectionCase,
    HoldoutSelectionPolicy,
    HoldoutSource,
    HoldoutStratumPolicy,
    LifecycleHoldoutSelection,
    RuntimeEnvironment,
    RuntimeFileSnapshot,
    SelectorAttestation,
    bytes_hash,
    canonical_hash,
    evaluation_harness_fingerprint,
    runtime_bundle_fingerprint,
    verify_approved_selection_policy,
    verify_candidate_corpus_independence,
    verify_lifecycle_holdout_checkout,
)
from arkts_code_reviewer.retrieval_validation.lifecycle_blind_holdout_evaluation import (
    evaluate_lifecycle_owner_role_holdout,
)


def _sha(value: str) -> str:
    return bytes_hash(value.encode("utf-8"))


def _runtime_snapshots(paths: tuple[str, ...], prefix: str) -> tuple[RuntimeFileSnapshot, ...]:
    return tuple(
        RuntimeFileSnapshot(path=path, content_sha256=_sha(f"{prefix}:{path}")) for path in paths
    )


def _candidate_freeze() -> CandidateFreeze:
    runtime_files = _runtime_snapshots(LIFECYCLE_RUNTIME_REQUIRED_PATHS, "runtime")
    harness_files = _runtime_snapshots(LIFECYCLE_EVALUATION_HARNESS_PATHS, "harness")
    return CandidateFreeze(
        candidate_commit="9b7a828449cbe760ce9374d222f75c48b6f5c852",
        tags_config_schema_version="tag-config-v4",
        tags_config_version="tags-lifecycle-owner-role-shadow-v1",
        feature_routing_schema_version="feature-routing-v3",
        feature_config_fingerprint=(
            "feature-config:sha256:844418e3d7938c816fd3b64b62cdae3d1753d286d50a6a103406838ed6db01e7"
        ),
        candidate_corpus_exposure_revision=("8255a2987f70317cc3a2a4d46044c6b55f092bb3"),
        candidate_corpus_exposure_scope="entire_tracked_repository",
        runtime_files=runtime_files,
        runtime_bundle_fingerprint=runtime_bundle_fingerprint(runtime_files),
        runtime_environment=RuntimeEnvironment(
            python_version="3.12.13",
            python_packages=(
                "pydantic==2.13.0",
                "pydantic_core==2.33.0",
                "ruamel.yaml==0.18.0",
            ),
            platform_system="Linux",
            platform_machine="x86_64",
            node_version="v26.4.0",
            node_executable_sha256=_sha("node"),
            sidecar_dependencies_fingerprint=(f"lifecycle-sidecar-dependencies:sha256:{'e' * 64}"),
        ),
        evaluation_harness_commit="b" * 40,
        evaluation_harness_files=harness_files,
        evaluation_harness_fingerprint=evaluation_harness_fingerprint(harness_files),
    )


def _minimal_quality_gates() -> HoldoutQualityGates:
    return HoldoutQualityGates(
        minimum_case_count=1,
        minimum_positive_cases=1,
        minimum_negative_cases=1,
        minimum_source_families=1,
        minimum_precision=0.9,
        minimum_recall=0.9,
        minimum_precision_wilson_95=0.8,
        minimum_recall_wilson_95=0.8,
        maximum_false_positives=0,
        maximum_false_negatives=0,
        maximum_critical_false_positives=0,
        maximum_parser_risk_cases=0,
        maximum_provenance_failure_cases=0,
        maximum_file_hint_promotions=0,
        maximum_review_unit_risk_cases=0,
        maximum_scope_risk_cases=0,
        maximum_routing_only_failures=0,
        maximum_challenge_owner_failures=0,
        critical_negative_strata=(),
    )


def _selection_with_development_family(excluded_family: str) -> LifecycleHoldoutSelection:
    repository = HoldoutRepository(
        source_id="applications-app-samples",
        repository="applications_app_samples",
        remote="https://gitcode.com/openharmony/applications_app_samples.git",
        revision="a" * 40,
    )
    candidate_freeze = _candidate_freeze()
    exclusions = DevelopmentExclusions(
        truth_suite_fingerprint=f"tag-retrieval-truth:sha256:{'d' * 64}",
        source_family_ids=(excluded_family,),
        source_paths=("code/Development/App/entry/src/main/ets/pages/Index.ets",),
        content_sha256=(_sha("development-source"),),
    )
    policy = HoldoutSelectionPolicy(
        policy_version="lifecycle-holdout-v1",
        policy_document_sha256=_sha("selection-policy"),
        dataset_kind="purposive_stratified_challenge_holdout",
        natural_prevalence_claimed=False,
        max_cases_per_source_family=1,
        strata=(
            HoldoutStratumPolicy(
                stratum_id="component_v1_positive",
                selected_case_count=1,
            ),
        ),
        quality_gates=_minimal_quality_gates(),
    )
    selector = SelectorAttestation(
        selector_id="independent-custodian",
        selector_role="independent_dataset_custodian",
        candidate_design_participant=False,
        candidate_output_seen=False,
        candidate_configuration_seen=False,
        attested_on="2026-07-16",
        process_note="Synthetic hardening fixture.",
    )
    source = HoldoutSource(
        alias="lhs001",
        path="code/Foo/App/entry/src/main/ets/pages/Index.ets",
        content_sha256=_sha("source"),
        line_count=1,
        app_scope="code/Foo/App",
        source_family_id="code/Foo/App",
    )
    case = HoldoutSelectionCase(
        case_id="LH-0001",
        source_alias="lhs001",
        changed_line=1,
        review_span=SourceSpan(start_line=1, end_line=1),
        review_span_sha256=_sha("source"),
        normalized_body_sha256=_sha("normalized-source"),
        stratum_id="component_v1_positive",
        selection_rank=1,
    )
    draft = LifecycleHoldoutSelection.model_construct(
        selection_id=f"lifecycle-holdout-selection:sha256:{'0' * 64}",
        schema_version="lifecycle-holdout-selection-v1",
        suite_id="lifecycle-owner-role-blind-holdout-v1",
        dataset_role="independent_blind_holdout",
        repository=repository,
        candidate_freeze=candidate_freeze,
        development_exclusions=exclusions,
        selection_policy=policy,
        selector_attestation=selector,
        sources=(source,),
        cases=(case,),
    )
    selection_id = canonical_hash(
        "lifecycle-holdout-selection",
        draft.model_dump(mode="json", exclude={"selection_id"}),
    )
    return LifecycleHoldoutSelection(
        schema_version="lifecycle-holdout-selection-v1",
        selection_id=selection_id,
        suite_id="lifecycle-owner-role-blind-holdout-v1",
        dataset_role="independent_blind_holdout",
        repository=repository,
        candidate_freeze=candidate_freeze,
        development_exclusions=exclusions,
        selection_policy=policy,
        selector_attestation=selector,
        sources=(source,),
        cases=(case,),
    )


@pytest.mark.parametrize(
    "excluded_family",
    [
        "code/Foo",
        "code/Foo/App/Nested",
    ],
)
def test_development_family_ancestor_and_descendant_overlap_is_rejected(
    excluded_family: str,
) -> None:
    with pytest.raises(ValidationError, match="source family overlaps development Truth"):
        _selection_with_development_family(excluded_family)


def _approved_policy(policy_text: str) -> HoldoutSelectionPolicy:
    strata = tuple(
        HoldoutStratumPolicy(
            stratum_id=stratum,
            selected_case_count=minimum,
        )
        for stratum, minimum in sorted(LIFECYCLE_REQUIRED_STRATUM_MINIMUMS.items())
    )
    return HoldoutSelectionPolicy(
        policy_version="lifecycle-holdout-v1",
        policy_document_sha256=_sha(policy_text),
        dataset_kind="purposive_stratified_challenge_holdout",
        natural_prevalence_claimed=False,
        max_cases_per_source_family=1,
        strata=strata,
        quality_gates=HoldoutQualityGates(
            minimum_case_count=32,
            minimum_positive_cases=16,
            minimum_negative_cases=16,
            minimum_source_families=32,
            minimum_precision=0.95,
            minimum_recall=0.95,
            minimum_precision_wilson_95=0.8,
            minimum_recall_wilson_95=0.8,
            maximum_false_positives=0,
            maximum_false_negatives=0,
            maximum_critical_false_positives=0,
            maximum_parser_risk_cases=0,
            maximum_provenance_failure_cases=0,
            maximum_file_hint_promotions=0,
            maximum_review_unit_risk_cases=0,
            maximum_scope_risk_cases=0,
            maximum_routing_only_failures=0,
            maximum_challenge_owner_failures=0,
            critical_negative_strata=LIFECYCLE_CRITICAL_NEGATIVE_STRATA,
        ),
    )


def _write_approved_policy(root: Path, policy_text: str) -> None:
    path = root / LIFECYCLE_SELECTION_POLICY_PATH
    path.parent.mkdir(parents=True)
    path.write_text(policy_text, encoding="utf-8")


def _selection_with_policy(policy: HoldoutSelectionPolicy) -> LifecycleHoldoutSelection:
    return LifecycleHoldoutSelection.model_construct(selection_policy=policy)


def test_approved_selection_policy_accepts_only_the_strong_baseline(tmp_path: Path) -> None:
    policy_text = "approved lifecycle holdout policy\n"
    _write_approved_policy(tmp_path, policy_text)

    verify_approved_selection_policy(
        _selection_with_policy(_approved_policy(policy_text)),
        tmp_path,
    )


@pytest.mark.parametrize(
    "weakness",
    ["quality_gate", "count_gate", "stratum_count", "taxonomy"],
)
def test_approved_selection_policy_rejects_weak_gates_and_strata(
    tmp_path: Path,
    weakness: str,
) -> None:
    policy_text = "approved lifecycle holdout policy\n"
    _write_approved_policy(tmp_path, policy_text)
    policy = _approved_policy(policy_text)
    payload = policy.model_dump(mode="json")
    if weakness == "quality_gate":
        payload["quality_gates"]["minimum_precision"] = 0.94
        expected = "quality gates are weaker"
    elif weakness == "count_gate":
        payload["quality_gates"]["minimum_case_count"] = 33
        expected = "count gates differ"
    elif weakness == "stratum_count":
        item = next(
            value for value in payload["strata"] if value["stratum_id"] == "router_page_positive"
        )
        item["selected_case_count"] = 9
        expected = "stratum counts differ"
    else:
        item = next(
            value for value in payload["strata"] if value["stratum_id"] == "router_page_positive"
        )
        item["stratum_id"] = "unexpected_positive"
        payload["strata"] = sorted(payload["strata"], key=lambda value: value["stratum_id"])
        expected = "strata differ"
    weakened = HoldoutSelectionPolicy.model_validate(payload)

    with pytest.raises(ValueError, match=expected):
        verify_approved_selection_policy(_selection_with_policy(weakened), tmp_path)


def _make_dependency_tree(root: Path) -> Path:
    package = root / "package"
    package.mkdir(parents=True)
    (package / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    (package / "alias.js").symlink_to("index.js")
    return root


def test_runtime_dependency_relative_symlink_has_stable_fingerprint(tmp_path: Path) -> None:
    first = _make_dependency_tree(tmp_path / "first" / "node_modules")
    second = _make_dependency_tree(tmp_path / "second" / "node_modules")

    assert holdout._directory_tree_fingerprint(first) == holdout._directory_tree_fingerprint(first)
    assert holdout._directory_tree_fingerprint(first) == holdout._directory_tree_fingerprint(second)


def test_runtime_dependency_symlink_cannot_escape_node_modules(tmp_path: Path) -> None:
    dependencies = tmp_path / "node_modules"
    dependencies.mkdir()
    outside = tmp_path / "outside.js"
    outside.write_text("outside\n", encoding="utf-8")
    (dependencies / "escape.js").symlink_to("../outside.js")

    with pytest.raises(ValueError, match="symlink escapes its tree"):
        holdout._directory_tree_fingerprint(dependencies)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write_source(root: Path, relative_path: str, text: str) -> None:
    path = root.joinpath(*relative_path.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _candidate_corpus_repository(root: Path) -> tuple[str, str, dict[str, str]]:
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Holdout Test")
    _git(root, "config", "user.email", "holdout@example.invalid")
    paths = {
        "path": "code/PathFamily/App/entry/src/main/ets/pages/SamePath.ets",
        "family": "code/FamilyOverlap/App/entry/src/main/ets/pages/New.ets",
        "content": "code/BrandNew/App/entry/src/main/ets/pages/Copied.ets",
        "clean": "code/FutureOnly/App/entry/src/main/ets/pages/Clean.ets",
    }
    _write_source(root, paths["path"], "exposed path v1\n")
    _write_source(
        root,
        "code/FamilyOverlap/App/entry/src/main/ets/pages/Old.ets",
        "exposed family\n",
    )
    _write_source(
        root,
        "code/BlobOrigin/App/entry/src/main/ets/pages/Blob.ets",
        "shared exposed blob\n",
    )
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "candidate exposure")
    exposure_revision = _git(root, "rev-parse", "HEAD")

    _write_source(root, paths["path"], "same path with future content\n")
    _write_source(root, paths["family"], "new path in exposed family\n")
    _write_source(root, paths["content"], "shared exposed blob\n")
    _write_source(root, paths["clean"], "entirely new future source\n")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "future holdout candidates")
    holdout_revision = _git(root, "rev-parse", "HEAD")
    return exposure_revision, holdout_revision, paths


def _corpus_selection(
    root: Path,
    exposure_revision: str,
    holdout_revision: str,
    source_path: str,
) -> LifecycleHoldoutSelection:
    raw = (root / source_path).read_bytes()
    candidate_freeze = CandidateFreeze.model_construct(
        candidate_corpus_exposure_revision=exposure_revision,
    )
    repository = HoldoutRepository(
        source_id="applications-app-samples",
        repository="applications_app_samples",
        remote="https://gitcode.com/openharmony/applications_app_samples.git",
        revision=holdout_revision,
    )
    source = HoldoutSource(
        alias="lhs001",
        path=source_path,
        content_sha256=bytes_hash(raw),
        line_count=len(raw.splitlines()),
        app_scope=holdout.derive_source_family_id(source_path),
        source_family_id=holdout.derive_source_family_id(source_path),
    )
    return LifecycleHoldoutSelection.model_construct(
        repository=repository,
        candidate_freeze=candidate_freeze,
        sources=(source,),
        cases=(),
    )


def test_candidate_corpus_exposure_rejects_all_overlap_axes_and_allows_new_source(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "corpus"
    exposure_revision, holdout_revision, paths = _candidate_corpus_repository(repository)
    expectations = {
        "path": "path:",
        "family": "family:",
        "content": "content:lhs001",
    }
    for case, expected in expectations.items():
        selection = _corpus_selection(
            repository,
            exposure_revision,
            holdout_revision,
            paths[case],
        )
        with pytest.raises(ValueError, match=expected):
            verify_candidate_corpus_independence(selection, repository)

    clean = _corpus_selection(
        repository,
        exposure_revision,
        holdout_revision,
        paths["clean"],
    )
    verify_candidate_corpus_independence(clean, repository)


def test_source_checkout_bytes_must_match_pinned_revision_even_when_git_hides_drift(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "corpus"
    exposure_revision, holdout_revision, paths = _candidate_corpus_repository(repository)
    _git(
        repository,
        "remote",
        "add",
        "origin",
        "https://gitcode.com/openharmony/applications_app_samples.git",
    )
    source_path = paths["clean"]
    _write_source(repository, source_path, "hidden worktree replacement\n")
    _git(repository, "update-index", "--assume-unchanged", source_path)
    selection = _corpus_selection(
        repository,
        exposure_revision,
        holdout_revision,
        source_path,
    )

    with pytest.raises(ValueError, match="differs from its pinned Git revision"):
        verify_lifecycle_holdout_checkout(selection, repository)


def test_public_evaluator_does_not_expose_parser_or_unit_builder_injection() -> None:
    parameters = inspect.signature(evaluate_lifecycle_owner_role_holdout).parameters

    assert "file_parser" not in parameters
    assert "unit_builder" not in parameters
