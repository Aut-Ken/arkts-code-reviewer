from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from arkts_code_reviewer.code_analysis.models import SourceSpan
from arkts_code_reviewer.retrieval_validation import lifecycle_blind_holdout_evaluation as evaluator
from arkts_code_reviewer.retrieval_validation.lifecycle_blind_holdout import (
    LIFECYCLE_EVALUATION_HARNESS_PATHS,
    LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT,
    LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT,
    LIFECYCLE_RUNTIME_PATHS,
    LifecycleHoldoutConsensus,
    LifecycleHoldoutReviewPacket,
    LifecycleHoldoutReviewReceipt,
    LifecycleHoldoutSelection,
    ReviewPolicySnapshot,
    RuntimeFileSnapshot,
    TagContractSnapshot,
    VerifiedHoldoutCheckout,
    build_lifecycle_holdout_consensus,
    build_lifecycle_holdout_review_packet,
    bytes_hash,
    evaluation_harness_fingerprint,
    review_receipt_payload_with_id,
    runtime_bundle_fingerprint,
    selection_payload_with_id,
)

ROOT = Path(__file__).resolve().parents[1]


def _sha(value: str) -> str:
    return bytes_hash(value.encode("utf-8"))


def _span(start_line: int, end_line: int) -> dict[str, int]:
    return {
        "start_line": start_line,
        "end_line": end_line,
        "start_col": 0,
        "end_col": 0,
    }


_SOURCE_TEXT = {
    "lhs001": "alpha\nbeta\ngamma\n",
    "lhs002": "delta\nepsilon\nzeta\n",
}


def _selection_payload(
    *,
    gate_overrides: dict[str, int | float] | None = None,
) -> dict[str, Any]:
    runtime_files = tuple(
        RuntimeFileSnapshot(path=path, content_sha256=_sha(f"runtime:{path}"))
        for path in LIFECYCLE_RUNTIME_PATHS
    )
    harness_files = tuple(
        RuntimeFileSnapshot(path=path, content_sha256=_sha(f"harness:{path}"))
        for path in LIFECYCLE_EVALUATION_HARNESS_PATHS
    )
    quality_gates: dict[str, object] = {
        "minimum_case_count": 2,
        "minimum_positive_cases": 1,
        "minimum_negative_cases": 1,
        "minimum_source_families": 2,
        "minimum_precision": 0.9,
        "minimum_recall": 0.9,
        "minimum_precision_wilson_95": 0.8,
        "minimum_recall_wilson_95": 0.8,
        "maximum_false_positives": 0,
        "maximum_false_negatives": 0,
        "maximum_critical_false_positives": 0,
        "maximum_parser_risk_cases": 0,
        "maximum_provenance_failure_cases": 0,
        "maximum_file_hint_promotions": 0,
        "maximum_review_unit_risk_cases": 0,
        "maximum_scope_risk_cases": 0,
        "maximum_routing_only_failures": 0,
        "maximum_challenge_owner_failures": 0,
        "critical_negative_strata": ["ordinary_owner_negative"],
    }
    quality_gates.update(gate_overrides or {})
    return {
        "schema_version": "lifecycle-holdout-selection-v1",
        "suite_id": "lifecycle-owner-role-blind-holdout-v1",
        "dataset_role": "independent_blind_holdout",
        "repository": {
            "source_id": "applications-app-samples",
            "repository": "applications_app_samples",
            "remote": "https://gitcode.com/openharmony/applications_app_samples.git",
            "revision": "a" * 40,
        },
        "candidate_freeze": {
            "candidate_commit": LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT,
            "tags_config_schema_version": "tag-config-v4",
            "tags_config_version": "tags-lifecycle-owner-role-shadow-v1",
            "feature_routing_schema_version": "feature-routing-v3",
            "feature_config_fingerprint": LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT,
            "candidate_corpus_exposure_revision": ("8255a2987f70317cc3a2a4d46044c6b55f092bb3"),
            "candidate_corpus_exposure_scope": "entire_tracked_repository",
            "runtime_files": [item.model_dump(mode="json") for item in runtime_files],
            "runtime_bundle_fingerprint": runtime_bundle_fingerprint(runtime_files),
            "runtime_environment": {
                "python_version": "3.12.13",
                "python_packages": [
                    "pydantic==2.13.0",
                    "pydantic_core==2.33.0",
                    "ruamel.yaml==0.18.0",
                ],
                "platform_system": "Linux",
                "platform_machine": "x86_64",
                "node_version": "v26.4.0",
                "node_executable_sha256": _sha("node"),
                "sidecar_dependencies_fingerprint": (
                    f"lifecycle-sidecar-dependencies:sha256:{'e' * 64}"
                ),
            },
            "evaluation_harness_commit": "b" * 40,
            "evaluation_harness_files": [item.model_dump(mode="json") for item in harness_files],
            "evaluation_harness_fingerprint": evaluation_harness_fingerprint(harness_files),
        },
        "development_exclusions": {
            "truth_suite_fingerprint": f"tag-retrieval-truth:sha256:{'d' * 64}",
            "source_family_ids": ["code/Development/App"],
            "source_paths": ["code/Development/App/entry/src/main/ets/pages/Development.ets"],
            "content_sha256": [_sha("development")],
        },
        "selection_policy": {
            "policy_version": "lifecycle-holdout-v1",
            "policy_document_sha256": _sha("selection-policy"),
            "dataset_kind": "purposive_stratified_challenge_holdout",
            "natural_prevalence_claimed": False,
            "max_cases_per_source_family": 1,
            "strata": [
                {
                    "stratum_id": "component_v1_positive",
                    "selected_case_count": 1,
                },
                {
                    "stratum_id": "ordinary_owner_negative",
                    "selected_case_count": 1,
                },
            ],
            "quality_gates": quality_gates,
        },
        "selector_attestation": {
            "selector_id": "independent-custodian",
            "selector_role": "independent_dataset_custodian",
            "candidate_design_participant": False,
            "candidate_output_seen": False,
            "candidate_configuration_seen": False,
            "attested_on": "2026-07-16",
            "process_note": "Selected without candidate access.",
        },
        "sources": [
            {
                "alias": "lhs001",
                "path": "code/FamilyOne/App/entry/src/main/ets/pages/One.ets",
                "content_sha256": _sha(_SOURCE_TEXT["lhs001"]),
                "line_count": 3,
                "app_scope": "code/FamilyOne/App",
                "source_family_id": "code/FamilyOne/App",
            },
            {
                "alias": "lhs002",
                "path": "code/FamilyTwo/App/entry/src/main/ets/pages/Two.ets",
                "content_sha256": _sha(_SOURCE_TEXT["lhs002"]),
                "line_count": 3,
                "app_scope": "code/FamilyTwo/App",
                "source_family_id": "code/FamilyTwo/App",
            },
        ],
        "cases": [
            {
                "case_id": "LH-0001",
                "source_alias": "lhs001",
                "changed_line": 1,
                "review_span": _span(1, 2),
                "review_span_sha256": _sha("alpha\nbeta"),
                "normalized_body_sha256": _sha("alpha beta"),
                "stratum_id": "component_v1_positive",
                "selection_rank": 1,
            },
            {
                "case_id": "LH-0002",
                "source_alias": "lhs002",
                "changed_line": 2,
                "review_span": _span(2, 3),
                "review_span_sha256": _sha("epsilon\nzeta"),
                "normalized_body_sha256": _sha("epsilon zeta"),
                "stratum_id": "ordinary_owner_negative",
                "selection_rank": 2,
            },
        ],
    }


def _selection(
    *,
    gate_overrides: dict[str, int | float] | None = None,
) -> LifecycleHoldoutSelection:
    payload = selection_payload_with_id(_selection_payload(gate_overrides=gate_overrides))
    for case in cast(list[dict[str, object]], payload["cases"]):
        case["review_span"] = SourceSpan(**cast(dict[str, int], case["review_span"]))
    return LifecycleHoldoutSelection.model_validate(payload)


def _packet(selection: LifecycleHoldoutSelection) -> LifecycleHoldoutReviewPacket:
    tag_text = "has_lifecycle means an owner-qualified ArkUI lifecycle."
    policy_text = "Review only the exact source span without candidate material."
    return build_lifecycle_holdout_review_packet(
        selection,
        VerifiedHoldoutCheckout(
            root=Path("/synthetic"),
            source_text_by_alias=_SOURCE_TEXT,
        ),
        target_tag_contract=TagContractSnapshot(
            version="lifecycle-exact-tag-contract-v1",
            content_sha256=_sha(tag_text),
            text=tag_text,
        ),
        review_policy=ReviewPolicySnapshot(
            version="lifecycle-blind-review-policy-v1",
            content_sha256=_sha(policy_text),
            text=policy_text,
        ),
    )


def _receipt(
    packet: LifecycleHoldoutReviewPacket,
    *,
    round_id: str,
    reviewer_id: str,
    first_label: str = "positive",
) -> LifecycleHoldoutReviewReceipt:
    payload: dict[str, object] = {
        "schema_version": "lifecycle-holdout-review-receipt-v1",
        "round_id": round_id,
        "selection_id": packet.selection_id,
        "packet_id": packet.packet_id,
        "target_tag_contract_sha256": packet.target_tag_contract.content_sha256,
        "review_policy_sha256": packet.review_policy.content_sha256,
        "reviewer": {
            "reviewer_id": reviewer_id,
            "reviewer_kind": "human",
            "reviewer_role": "arkts_domain_reviewer",
            "affiliation": f"Independent team {reviewer_id}",
            "candidate_design_participant": False,
            "selection_participant": False,
        },
        "blinding": {
            "candidate_output_seen": False,
            "candidate_configuration_seen": False,
            "review_completed_before_unblinding": True,
            "attested_at": "2026-07-16T08:00:00Z",
        },
        "recorded_at": "2026-07-16T08:05:00Z",
        "decisions": [
            {
                "case_id": "LH-0001",
                "semantic_label": first_label,
                "expected_unit_kind": "method",
                "expected_unit_symbol": "Synthetic.aboutToAppear",
                "expected_source_span": _span(1, 2),
                "evidence_lines": [1],
                "rationale": "Independent lifecycle judgment.",
            },
            {
                "case_id": "LH-0002",
                "semantic_label": "negative",
                "expected_unit_kind": "method",
                "expected_unit_symbol": "Synthetic.aboutToAppear",
                "expected_source_span": _span(2, 3),
                "evidence_lines": [2],
                "rationale": "Independent owner-lookalike judgment.",
            },
        ],
    }
    sealed = review_receipt_payload_with_id(payload)
    for decision in cast(list[dict[str, object]], sealed["decisions"]):
        decision["expected_source_span"] = SourceSpan(
            **cast(dict[str, int], decision["expected_source_span"])
        )
    return LifecycleHoldoutReviewReceipt.model_validate(sealed)


def _consensus(
    selection: LifecycleHoldoutSelection,
    *,
    unresolved: bool = False,
) -> tuple[LifecycleHoldoutReviewPacket, LifecycleHoldoutConsensus]:
    packet = _packet(selection)
    first = _receipt(packet, round_id="round-a", reviewer_id="reviewer-a")
    second = _receipt(
        packet,
        round_id="round-b",
        reviewer_id="reviewer-b",
        first_label="negative" if unresolved else "positive",
    )
    return packet, build_lifecycle_holdout_consensus(packet, (first, second))


def _observations() -> list[dict[str, object]]:
    return [
        {
            "case_id": "LH-0001",
            "source_family_id": "code/FamilyOne/App",
            "stratum_id": "component_v1_positive",
            "expected_exact_tag": True,
            "actual_exact_tag": True,
            "parser_risk": False,
            "owner_provenance_failure": False,
            "file_hint_promoted_to_exact": False,
            "review_unit_risk": False,
            "scope_risk": False,
            "routing_only_failure": False,
            "challenge_owner_failure": False,
        },
        {
            "case_id": "LH-0002",
            "source_family_id": "code/FamilyTwo/App",
            "stratum_id": "ordinary_owner_negative",
            "expected_exact_tag": False,
            "actual_exact_tag": False,
            "parser_risk": False,
            "owner_provenance_failure": False,
            "file_hint_promoted_to_exact": False,
            "review_unit_risk": False,
            "scope_risk": False,
            "routing_only_failure": False,
            "challenge_owner_failure": False,
        },
    ]


def _score(
    selection: LifecycleHoldoutSelection,
    observations: list[dict[str, object]],
) -> dict[str, object]:
    return evaluator._score_lifecycle_holdout_observations(selection, observations)


def test_candidate_evaluation_rejects_unresolved_consensus_before_loading_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _selection()
    packet, consensus = _consensus(selection, unresolved=True)
    monkeypatch.setattr(
        evaluator,
        "_load_candidate_config",
        lambda _path: pytest.fail("candidate must not load before consensus is complete"),
    )

    with pytest.raises(ValueError, match="consensus must be complete"):
        evaluator._observe_lifecycle_owner_role_holdout(
            selection,
            packet,
            consensus,
            VerifiedHoldoutCheckout(
                root=Path("/synthetic"),
                source_text_by_alias=_SOURCE_TEXT,
            ),
            candidate_tags_path=Path("never-read.yaml"),
        )


def test_candidate_evaluation_rejects_missing_pre_consensus_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _selection()
    packet = _packet(selection)
    monkeypatch.setattr(
        evaluator,
        "_load_candidate_config",
        lambda _path: pytest.fail("candidate must not load before consensus is sealed"),
    )

    with pytest.raises(ValueError, match="requires sealed consensus"):
        evaluator._observe_lifecycle_owner_role_holdout(
            selection,
            packet,
            cast(Any, None),
            VerifiedHoldoutCheckout(
                root=Path("/synthetic"),
                source_text_by_alias=_SOURCE_TEXT,
            ),
            candidate_tags_path=Path("never-read.yaml"),
        )


def test_candidate_evaluation_rejects_stratum_truth_mismatch_before_loading_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _selection()
    packet = _packet(selection)
    receipts = (
        _receipt(
            packet,
            round_id="round-a",
            reviewer_id="reviewer-a",
            first_label="negative",
        ),
        _receipt(
            packet,
            round_id="round-b",
            reviewer_id="reviewer-b",
            first_label="negative",
        ),
    )
    consensus = build_lifecycle_holdout_consensus(packet, receipts)
    monkeypatch.setattr(
        evaluator,
        "_load_candidate_config",
        lambda _path: pytest.fail("candidate must not load for a mislabeled challenge stratum"),
    )

    with pytest.raises(ValueError, match="stratum disagrees"):
        evaluator._observe_lifecycle_owner_role_holdout(
            selection,
            packet,
            consensus,
            VerifiedHoldoutCheckout(
                root=Path("/synthetic"),
                source_text_by_alias=_SOURCE_TEXT,
            ),
            candidate_tags_path=Path("never-read.yaml"),
        )


def test_perfect_small_sample_fails_only_statistical_confidence_gate() -> None:
    report = _score(_selection(), _observations())
    overall = cast(dict[str, object], report["overall"])
    gate = cast(dict[str, object], report["computed_quality_gate"])

    assert overall["precision"] == 1.0
    assert overall["recall"] == 1.0
    assert cast(float, overall["precision_wilson_lower_95"]) < 0.8
    assert cast(float, overall["recall_wilson_lower_95"]) < 0.8
    assert gate == {
        "passed": False,
        "failures": [
            "precision_wilson_lower_bound_below_frozen_gate",
            "recall_wilson_lower_bound_below_frozen_gate",
        ],
    }


@pytest.mark.parametrize(
    ("case_index", "field", "value", "expected_blocker"),
    [
        (1, "actual_exact_tag", True, "false_positives_above_frozen_gate"),
        (0, "actual_exact_tag", False, "false_negatives_above_frozen_gate"),
        (1, "actual_exact_tag", True, "critical_false_positives_above_frozen_gate"),
        (0, "parser_risk", True, "parser_risk_above_frozen_gate"),
        (
            0,
            "owner_provenance_failure",
            True,
            "owner_provenance_failure_above_frozen_gate",
        ),
        (
            0,
            "file_hint_promoted_to_exact",
            True,
            "file_hint_promotion_above_frozen_gate",
        ),
        (0, "review_unit_risk", True, "review_unit_risk_above_frozen_gate"),
        (0, "scope_risk", True, "unit_fact_scope_risk_above_frozen_gate"),
        (
            0,
            "routing_only_failure",
            True,
            "routing_only_contract_failure_above_frozen_gate",
        ),
        (
            0,
            "challenge_owner_failure",
            True,
            "challenge_owner_evidence_failure_above_frozen_gate",
        ),
    ],
)
def test_safety_and_error_blockers_fail_closed(
    case_index: int,
    field: str,
    value: object,
    expected_blocker: str,
) -> None:
    selection = _selection(
        gate_overrides={
            "minimum_precision_wilson_95": 0.0,
            "minimum_recall_wilson_95": 0.0,
        }
    )
    rows = _observations()
    rows[case_index][field] = value

    report = _score(selection, rows)
    gate = cast(dict[str, object], report["computed_quality_gate"])

    assert gate["passed"] is False
    assert expected_blocker in cast(list[str], gate["failures"])


def test_observed_source_family_collapse_fails_frozen_family_gate() -> None:
    selection = _selection(
        gate_overrides={
            "minimum_precision_wilson_95": 0.0,
            "minimum_recall_wilson_95": 0.0,
        }
    )
    rows = _observations()
    rows[1]["source_family_id"] = rows[0]["source_family_id"]

    report = _score(selection, rows)

    assert "insufficient_source_families" in cast(
        list[str],
        cast(dict[str, object], report["computed_quality_gate"])["failures"],
    )


def test_unsealed_scoring_cannot_claim_official_evidence_or_activation() -> None:
    selection = _selection(
        gate_overrides={
            "minimum_precision_wilson_95": 0.0,
            "minimum_recall_wilson_95": 0.0,
        }
    )

    report = _score(selection, _observations())

    assert report["computed_quality_gate"] == {"passed": True, "failures": []}
    assert "schema_version" not in report
    assert "evaluation_role" not in report
    assert "evidence_gate" not in report
    assert "production_activation" not in report


def test_component_v2_contract_coverage_requires_all_four_owner_checks() -> None:
    rows: list[dict[str, object]] = [
        {
            "stratum_id": "component_v2_positive",
            "challenge_owner_failure": index == 3,
        }
        for index in range(4)
    ]

    coverage = evaluator._component_v2_contract_coverage(rows)

    assert coverage == {
        "full_candidate_contract_qualified": False,
        "component_v2_selected_case_count": 4,
        "component_v2_owner_verified_case_count": 3,
        "known_unqualified_slices": ["component_v2_owner_role"],
        "reason": "1 of 4 selected ComponentV2 cases failed owner-role evidence checks",
    }


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _sealed_repository(tmp_path: Path) -> tuple[Path, str, str, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "holdout-tests@example.invalid")
    _git(root, "config", "user.name", "Holdout Tests")
    marker = root / "candidate.txt"
    marker.write_text("candidate freeze\n", encoding="utf-8")
    _git(root, "add", "candidate.txt")
    _git(root, "commit", "-m", "candidate freeze")
    candidate_commit = _git(root, "rev-parse", "HEAD")
    artifact = root / "artifacts/selection.json"
    artifact.parent.mkdir()
    artifact.write_text('{"sealed":true}\n', encoding="utf-8")
    _git(root, "add", "artifacts/selection.json")
    _git(root, "commit", "-m", "seal holdout")
    seal_commit = _git(root, "rev-parse", "HEAD")
    return root, candidate_commit, seal_commit, artifact


def test_verify_holdout_artifacts_sealed_accepts_clean_exact_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, candidate_commit, seal_commit, artifact = _sealed_repository(tmp_path)
    monkeypatch.setattr(
        evaluator,
        "LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT",
        candidate_commit,
    )

    verified = evaluator.verify_holdout_artifacts_sealed(
        root,
        seal_commit,
        [artifact],
    )

    assert verified.seal_revision == seal_commit
    assert verified.paths == (artifact.resolve(),)
    assert verified.artifact_bytes == (b'{"sealed":true}\n',)


def test_verify_holdout_artifacts_sealed_rejects_non_ancestor_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, candidate_commit, seal_commit, artifact = _sealed_repository(tmp_path)
    monkeypatch.setattr(
        evaluator,
        "LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT",
        candidate_commit,
    )
    original = evaluator._is_ancestor
    monkeypatch.setattr(
        evaluator,
        "_is_ancestor",
        lambda git_root, ancestor, descendant: (
            False
            if ancestor == seal_commit and descendant == "HEAD"
            else original(git_root, ancestor, descendant)
        ),
    )

    with pytest.raises(ValueError, match="seal revision is not an ancestor"):
        evaluator.verify_holdout_artifacts_sealed(root, seal_commit, [artifact])


def test_verify_holdout_artifacts_sealed_rejects_clean_descendant_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, candidate_commit, seal_commit, artifact = _sealed_repository(tmp_path)
    monkeypatch.setattr(evaluator, "LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT", candidate_commit)
    descendant = root / "after-seal.txt"
    descendant.write_text("later code\n", encoding="utf-8")
    _git(root, "add", "after-seal.txt")
    _git(root, "commit", "-m", "post seal change")

    with pytest.raises(ValueError, match="HEAD exactly at the seal revision"):
        evaluator.verify_holdout_artifacts_sealed(root, seal_commit, [artifact])


def test_verify_holdout_artifacts_sealed_rejects_dirty_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, candidate_commit, seal_commit, artifact = _sealed_repository(tmp_path)
    monkeypatch.setattr(
        evaluator,
        "LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT",
        candidate_commit,
    )
    untracked = root / "artifacts/untracked.json"
    untracked.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="repository must be completely clean"):
        evaluator.verify_holdout_artifacts_sealed(root, seal_commit, [untracked])

    untracked.unlink()
    artifact.write_text('{"sealed":false}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="repository must be completely clean"):
        evaluator.verify_holdout_artifacts_sealed(root, seal_commit, [artifact])


def _formal_chain_bytes() -> tuple[
    LifecycleHoldoutSelection,
    LifecycleHoldoutReviewPacket,
    tuple[LifecycleHoldoutReviewReceipt, LifecycleHoldoutReviewReceipt],
    LifecycleHoldoutConsensus,
    tuple[bytes, ...],
]:
    selection = _selection()
    packet = _packet(selection)
    receipts = (
        _receipt(packet, round_id="round-a", reviewer_id="reviewer-a"),
        _receipt(packet, round_id="round-b", reviewer_id="reviewer-b"),
    )
    consensus = build_lifecycle_holdout_consensus(packet, receipts)
    artifacts = (selection, packet, *receipts, consensus)
    raw = tuple(
        json.dumps(item.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        for item in artifacts
    )
    return selection, packet, receipts, consensus, raw


def _prepare_formal_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[LifecycleHoldoutSelection, LifecycleHoldoutReviewPacket]:
    selection, packet, _receipts, _consensus_artifact, raw = _formal_chain_bytes()
    monkeypatch.setattr(evaluator, "verify_holdout_execution_environment", lambda *_args: None)
    monkeypatch.setattr(
        evaluator,
        "verify_holdout_artifacts_sealed",
        lambda *_args, **_kwargs: evaluator.VerifiedHoldoutArtifacts(
            seal_revision="c" * 40,
            paths=tuple(Path(f"/sealed/{index}.json") for index in range(5)),
            artifact_bytes=raw,
        ),
    )
    monkeypatch.setattr(evaluator, "_is_ancestor", lambda *_args: True)
    monkeypatch.setattr(evaluator, "verify_evaluation_harness_bundle", lambda *_args: None)
    monkeypatch.setattr(evaluator, "verify_candidate_runtime_bundle", lambda *_args: None)
    monkeypatch.setattr(evaluator, "verify_approved_selection_policy", lambda *_args: None)
    monkeypatch.setattr(
        evaluator,
        "verify_selection_development_exclusions",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        evaluator,
        "verify_lifecycle_holdout_checkout",
        lambda *_args: VerifiedHoldoutCheckout(
            root=Path("/synthetic"),
            source_text_by_alias=_SOURCE_TEXT,
        ),
    )
    monkeypatch.setattr(evaluator, "verify_candidate_corpus_independence", lambda *_args: None)
    monkeypatch.setattr(
        evaluator,
        "load_canonical_lifecycle_review_material",
        lambda *_args: (packet.target_tag_contract, packet.review_policy),
    )
    return selection, packet


@pytest.mark.parametrize(
    ("preflight", "message"),
    [
        ("verify_evaluation_harness_bundle", "harness drift"),
        ("verify_candidate_runtime_bundle", "runtime drift"),
        ("verify_approved_selection_policy", "weak policy"),
        ("verify_selection_development_exclusions", "development leakage"),
        ("verify_candidate_corpus_independence", "seen corpus"),
    ],
)
def test_formal_evaluation_fails_preflight_before_candidate_load(
    preflight: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_formal_evaluation(monkeypatch)
    monkeypatch.setattr(
        evaluator,
        preflight,
        lambda *_args: (_ for _ in ()).throw(ValueError(message)),
    )
    monkeypatch.setattr(
        evaluator,
        "_load_candidate_config",
        lambda _path: pytest.fail("candidate must not load before every preflight passes"),
    )

    with pytest.raises(ValueError, match=message):
        evaluator.evaluate_lifecycle_owner_role_holdout(
            selection_path="selection.json",
            packet_path="packet.json",
            receipt_paths=("reviewer-a.json", "reviewer-b.json"),
            consensus_path="consensus.json",
            source_root="/synthetic",
            repository_root=ROOT,
            candidate_tags_path=(
                ROOT / "tests/fixtures/feature_routing/"
                "tag_config_lifecycle_owner_role_shadow_v1.yaml"
            ),
            seal_revision="c" * 40,
        )


def test_formal_evaluation_rebuilds_packet_before_candidate_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _selection_artifact, packet = _prepare_formal_evaluation(monkeypatch)
    altered_text = packet.target_tag_contract.text + "\nAltered after review."
    monkeypatch.setattr(
        evaluator,
        "load_canonical_lifecycle_review_material",
        lambda *_args: (
            TagContractSnapshot(
                version="lifecycle-exact-tag-contract-v1",
                content_sha256=_sha(altered_text),
                text=altered_text,
            ),
            packet.review_policy,
        ),
    )
    monkeypatch.setattr(
        evaluator,
        "_load_candidate_config",
        lambda _path: pytest.fail("candidate must not load before packet reconstruction"),
    )

    with pytest.raises(ValueError, match="review packet does not match canonical"):
        evaluator.evaluate_lifecycle_owner_role_holdout(
            selection_path="selection.json",
            packet_path="packet.json",
            receipt_paths=("reviewer-a.json", "reviewer-b.json"),
            consensus_path="consensus.json",
            source_root="/synthetic",
            repository_root=ROOT,
            candidate_tags_path=(
                ROOT / "tests/fixtures/feature_routing/"
                "tag_config_lifecycle_owner_role_shadow_v1.yaml"
            ),
            seal_revision="c" * 40,
        )


def test_formal_evaluation_has_no_parser_or_unit_builder_injection() -> None:
    parameters = inspect.signature(evaluator.evaluate_lifecycle_owner_role_holdout).parameters

    assert "file_parser" not in parameters
    assert "unit_builder" not in parameters


def test_seal_verifier_returns_repository_bytes_when_cwd_has_same_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, candidate_commit, seal_commit, artifact = _sealed_repository(tmp_path)
    monkeypatch.setattr(evaluator, "LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT", candidate_commit)
    outside = tmp_path / "outside"
    collision = outside / "artifacts/selection.json"
    collision.parent.mkdir(parents=True)
    collision.write_text('{"sealed":false}\n', encoding="utf-8")
    monkeypatch.chdir(outside)

    verified = evaluator.verify_holdout_artifacts_sealed(
        root,
        seal_commit,
        [Path("artifacts/selection.json")],
    )

    assert verified.paths == (artifact.resolve(),)
    assert verified.artifact_bytes == (b'{"sealed":true}\n',)
