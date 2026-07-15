from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from arkts_code_reviewer.code_analysis.models import SourceSpan
from arkts_code_reviewer.retrieval_validation.lifecycle_blind_holdout import (
    LIFECYCLE_EVALUATION_HARNESS_PATHS,
    LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT,
    LIFECYCLE_OWNER_ROLE_CANDIDATE_FINGERPRINT,
    LIFECYCLE_RUNTIME_PATHS,
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
    load_lifecycle_holdout_consensus,
    load_lifecycle_holdout_review_packet,
    load_lifecycle_holdout_review_receipt,
    load_lifecycle_holdout_selection,
    review_receipt_payload_with_id,
    runtime_bundle_fingerprint,
    selection_payload_with_id,
    validate_lifecycle_holdout_review_receipt,
    verify_selection_development_exclusions,
)
from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
    load_tag_retrieval_truth,
    tag_retrieval_truth_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_TRUTH = ROOT / "tests/evaluation/tag_retrieval/manifest.json"


def _sha(value: str) -> str:
    return bytes_hash(value.encode("utf-8"))


def _span(start_line: int, end_line: int) -> dict[str, int]:
    return {
        "start_line": start_line,
        "end_line": end_line,
        "start_col": 0,
        "end_col": 0,
    }


SOURCE_TEXTS = {
    "lhs001": "alpha\nbeta\ngamma\n",
    "lhs002": "delta\nepsilon\nzeta\n",
}


def _runtime_files() -> list[dict[str, str]]:
    snapshots = tuple(
        RuntimeFileSnapshot(path=path, content_sha256=_sha(f"runtime:{path}"))
        for path in LIFECYCLE_RUNTIME_PATHS
    )
    return [item.model_dump(mode="json") for item in snapshots]


def _harness_files() -> list[dict[str, str]]:
    snapshots = tuple(
        RuntimeFileSnapshot(path=path, content_sha256=_sha(f"harness:{path}"))
        for path in LIFECYCLE_EVALUATION_HARNESS_PATHS
    )
    return [item.model_dump(mode="json") for item in snapshots]


def _selection_payload() -> dict[str, Any]:
    runtime_files = _runtime_files()
    runtime_snapshots = tuple(RuntimeFileSnapshot.model_validate(item) for item in runtime_files)
    harness_files = _harness_files()
    harness_snapshots = tuple(RuntimeFileSnapshot.model_validate(item) for item in harness_files)
    source_one = "code/FamilyOne/App/entry/src/main/ets/pages/One.ets"
    source_two = "code/FamilyTwo/App/entry/src/main/ets/pages/Two.ets"
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
            "runtime_files": runtime_files,
            "runtime_bundle_fingerprint": runtime_bundle_fingerprint(runtime_snapshots),
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
            "evaluation_harness_files": harness_files,
            "evaluation_harness_fingerprint": evaluation_harness_fingerprint(harness_snapshots),
        },
        "development_exclusions": {
            "truth_suite_fingerprint": f"tag-retrieval-truth:sha256:{'d' * 64}",
            "source_family_ids": ["code/Development/App"],
            "source_paths": ["code/Development/App/entry/src/main/ets/pages/Development.ets"],
            "content_sha256": [_sha("development-source")],
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
            "quality_gates": {
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
            },
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
                "path": source_one,
                "content_sha256": _sha(SOURCE_TEXTS["lhs001"]),
                "line_count": 3,
                "app_scope": "code/FamilyOne/App",
                "source_family_id": "code/FamilyOne/App",
            },
            {
                "alias": "lhs002",
                "path": source_two,
                "content_sha256": _sha(SOURCE_TEXTS["lhs002"]),
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


def _materialize_selection_spans(payload: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(payload)
    for case in result["cases"]:
        case["review_span"] = SourceSpan(**case["review_span"])
    return result


def _selection_from_payload(payload: dict[str, Any]) -> LifecycleHoldoutSelection:
    sealed = selection_payload_with_id(payload)
    return LifecycleHoldoutSelection.model_validate(_materialize_selection_spans(sealed))


def _selection() -> LifecycleHoldoutSelection:
    return _selection_from_payload(_selection_payload())


def _write_json_artifact(tmp_path: Path, filename: str, artifact: BaseModel) -> Path:
    artifact_path = tmp_path / filename
    artifact_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    return artifact_path


def _snapshots() -> tuple[TagContractSnapshot, ReviewPolicySnapshot]:
    tag_text = "has_lifecycle means an owner-qualified ArkUI lifecycle."
    policy_text = "Review only the exact source span without candidate material."
    return (
        TagContractSnapshot(
            version="lifecycle-exact-tag-contract-v1",
            content_sha256=_sha(tag_text),
            text=tag_text,
        ),
        ReviewPolicySnapshot(
            version="lifecycle-blind-review-policy-v1",
            content_sha256=_sha(policy_text),
            text=policy_text,
        ),
    )


def _packet() -> LifecycleHoldoutReviewPacket:
    selection = _selection()
    tag_contract, review_policy = _snapshots()
    return build_lifecycle_holdout_review_packet(
        selection,
        VerifiedHoldoutCheckout(root=Path("/synthetic"), source_text_by_alias=SOURCE_TEXTS),
        target_tag_contract=tag_contract,
        review_policy=review_policy,
    )


def _decision_payload(
    case_id: str,
    *,
    label: str,
    symbol_suffix: str = "lifecycle",
    evidence_line: int | None = None,
) -> dict[str, Any]:
    spans = {
        "LH-0001": _span(1, 2),
        "LH-0002": _span(2, 3),
        "LH-0003": _span(1, 2),
    }
    default_evidence = {"LH-0001": 1, "LH-0002": 2, "LH-0003": 1}
    return {
        "case_id": case_id,
        "semantic_label": label,
        "expected_unit_kind": "method",
        "expected_unit_symbol": f"Synthetic.{symbol_suffix}",
        "expected_source_span": spans[case_id],
        "evidence_lines": [evidence_line or default_evidence[case_id]],
        "rationale": f"Human judgment for {case_id}.",
    }


def _receipt_payload(
    packet: LifecycleHoldoutReviewPacket,
    *,
    round_id: str,
    reviewer_id: str,
    case_ids: tuple[str, ...] = ("LH-0001", "LH-0002"),
    labels: dict[str, str] | None = None,
    symbol_suffixes: dict[str, str] | None = None,
    evidence_lines: dict[str, int] | None = None,
) -> dict[str, Any]:
    labels = labels or {"LH-0001": "positive", "LH-0002": "negative"}
    symbol_suffixes = symbol_suffixes or {}
    evidence_lines = evidence_lines or {}
    return {
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
            _decision_payload(
                case_id,
                label=labels.get(case_id, "negative"),
                symbol_suffix=symbol_suffixes.get(case_id, "lifecycle"),
                evidence_line=evidence_lines.get(case_id),
            )
            for case_id in sorted(case_ids)
        ],
    }


def _materialize_receipt_spans(payload: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(payload)
    for decision in result["decisions"]:
        decision["expected_source_span"] = SourceSpan(**decision["expected_source_span"])
    return result


def _receipt_from_payload(payload: dict[str, Any]) -> LifecycleHoldoutReviewReceipt:
    sealed = review_receipt_payload_with_id(payload)
    return LifecycleHoldoutReviewReceipt.model_validate(_materialize_receipt_spans(sealed))


def _receipt(
    packet: LifecycleHoldoutReviewPacket,
    *,
    round_id: str,
    reviewer_id: str,
    labels: dict[str, str] | None = None,
    symbol_suffixes: dict[str, str] | None = None,
    evidence_lines: dict[str, int] | None = None,
) -> LifecycleHoldoutReviewReceipt:
    return _receipt_from_payload(
        _receipt_payload(
            packet,
            round_id=round_id,
            reviewer_id=reviewer_id,
            labels=labels,
            symbol_suffixes=symbol_suffixes,
            evidence_lines=evidence_lines,
        )
    )


def test_selection_self_hash_rejects_tampering() -> None:
    payload = _selection().model_dump(mode="json")
    payload["selection_id"] = f"lifecycle-holdout-selection:sha256:{'0' * 64}"

    with pytest.raises(ValidationError, match="selection_id does not match"):
        LifecycleHoldoutSelection.model_validate(_materialize_selection_spans(payload))


def test_json_loaders_materialize_source_spans(tmp_path: Path) -> None:
    selection = _selection()
    packet = _packet()
    first = _receipt(packet, round_id="round-a", reviewer_id="reviewer-a")
    second = _receipt(packet, round_id="round-b", reviewer_id="reviewer-b")
    consensus = build_lifecycle_holdout_consensus(packet, (first, second))
    loaded_selection = load_lifecycle_holdout_selection(
        _write_json_artifact(tmp_path, "selection.json", selection)
    )
    loaded_packet = load_lifecycle_holdout_review_packet(
        _write_json_artifact(tmp_path, "packet.json", packet)
    )
    loaded_receipt = load_lifecycle_holdout_review_receipt(
        _write_json_artifact(tmp_path, "receipt.json", first)
    )
    loaded_consensus = load_lifecycle_holdout_consensus(
        _write_json_artifact(tmp_path, "consensus.json", consensus)
    )

    assert loaded_selection == selection
    assert isinstance(loaded_selection.cases[0].review_span, SourceSpan)
    assert loaded_packet == packet
    assert isinstance(loaded_packet.cases[0].review_span, SourceSpan)
    assert loaded_receipt == first
    assert isinstance(loaded_receipt.decisions[0].expected_source_span, SourceSpan)
    assert loaded_consensus == consensus
    assert isinstance(
        loaded_consensus.cases[0].votes[0].expected_source_span,
        SourceSpan,
    )


def test_json_loader_rejects_duplicate_keys_before_model_validation(tmp_path: Path) -> None:
    artifact = tmp_path / "duplicate-selection.json"
    artifact.write_text(
        '{"schema_version":"first","schema_version":"second"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate JSON key: schema_version"):
        load_lifecycle_holdout_selection(artifact)


@pytest.mark.parametrize("forbidden_field", ["semantic_label", "actual_exact_tag"])
def test_selection_case_rejects_truth_and_candidate_output_fields(
    forbidden_field: str,
) -> None:
    payload = _selection_payload()
    payload["cases"][0][forbidden_field] = "forbidden"

    with pytest.raises(ValidationError, match=forbidden_field):
        _selection_from_payload(payload)


@pytest.mark.parametrize(
    ("exclusion_field", "overlap_value", "expected_message"),
    [
        ("source_family_ids", "code/FamilyOne/App", "source family overlaps"),
        (
            "source_paths",
            "code/FamilyOne/App/entry/src/main/ets/pages/One.ets",
            "source path overlaps",
        ),
        ("content_sha256", _sha(SOURCE_TEXTS["lhs001"]), "content hash overlaps"),
    ],
)
def test_selection_rejects_development_overlap(
    exclusion_field: str,
    overlap_value: str,
    expected_message: str,
) -> None:
    payload = _selection_payload()
    payload["development_exclusions"][exclusion_field] = [overlap_value]

    with pytest.raises(ValidationError, match=expected_message):
        _selection_from_payload(payload)


def test_selection_rejects_duplicate_normalized_bodies() -> None:
    payload = _selection_payload()
    payload["cases"][1]["normalized_body_sha256"] = payload["cases"][0]["normalized_body_sha256"]

    with pytest.raises(ValidationError, match="normalized-body duplicates"):
        _selection_from_payload(payload)


def test_selection_exclusion_snapshot_must_cover_complete_development_truth() -> None:
    truth = load_tag_retrieval_truth(DEVELOPMENT_TRUTH)
    payload = _selection_payload()
    payload["development_exclusions"] = {
        "truth_suite_fingerprint": tag_retrieval_truth_fingerprint(truth),
        "source_family_ids": sorted({source.source_family_id for source in truth.sources}),
        "source_paths": sorted(source.path for source in truth.sources),
        "content_sha256": sorted(source.content_sha256 for source in truth.sources),
    }
    selection = _selection_from_payload(payload)

    verify_selection_development_exclusions(selection, truth)

    incomplete = deepcopy(payload)
    incomplete["development_exclusions"]["source_family_ids"] = incomplete[
        "development_exclusions"
    ]["source_family_ids"][1:]
    incomplete_selection = _selection_from_payload(incomplete)
    with pytest.raises(ValueError, match="source-family set is incomplete"):
        verify_selection_development_exclusions(incomplete_selection, truth)


def test_selection_enforces_source_family_cap() -> None:
    payload = _selection_payload()
    payload["sources"][1].update(
        {
            "path": "code/FamilyOne/App/entry/src/main/ets/pages/Two.ets",
            "app_scope": "code/FamilyOne/App",
            "source_family_id": "code/FamilyOne/App",
        }
    )

    with pytest.raises(ValidationError, match="per-family cap"):
        _selection_from_payload(payload)


def test_selection_enforces_frozen_stratum_counts() -> None:
    payload = _selection_payload()
    payload["cases"][1]["stratum_id"] = "component_v1_positive"

    with pytest.raises(ValidationError, match="strata do not match"):
        _selection_from_payload(payload)


@pytest.mark.parametrize(
    ("gate", "value", "expected_message"),
    [
        ("minimum_case_count", 3, "minimum case count"),
        ("minimum_source_families", 3, "family minimum"),
    ],
)
def test_selection_enforces_count_gates(
    gate: str,
    value: int,
    expected_message: str,
) -> None:
    payload = _selection_payload()
    payload["selection_policy"]["quality_gates"][gate] = value

    with pytest.raises(ValidationError, match=expected_message):
        _selection_from_payload(payload)


def test_packet_builder_exposes_only_blinded_span_and_verifies_its_hash() -> None:
    packet = _packet()
    payload = packet.model_dump(mode="json")

    assert packet.candidate_outputs_included is False
    assert packet.candidate_configuration_included is False
    assert packet.cases[0].exact_text == "alpha\nbeta"
    assert packet.cases[0].exact_text_sha256 == _sha("alpha\nbeta")
    assert "candidate_freeze" not in payload
    assert "feature_config_fingerprint" not in payload
    assert "runtime_files" not in payload
    assert all("normalized_body_sha256" not in case for case in payload["cases"])
    assert all("selection_rank" not in case for case in payload["cases"])
    assert all("stratum_id" not in case for case in payload["cases"])


def test_packet_loader_rejects_extra_candidate_outputs(tmp_path: Path) -> None:
    payload = _packet().model_dump(mode="json")
    payload["candidate_outputs"] = {"LH-0001": {"exact_tag": True}}
    artifact = tmp_path / "packet-with-candidate-output.json"
    artifact.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="candidate_outputs"):
        load_lifecycle_holdout_review_packet(artifact)


def test_packet_builder_rejects_review_span_hash_drift() -> None:
    payload = _selection_payload()
    payload["cases"][0]["review_span_sha256"] = _sha("tampered span")
    selection = _selection_from_payload(payload)
    tag_contract, review_policy = _snapshots()

    with pytest.raises(ValueError, match="review-span hash drift"):
        build_lifecycle_holdout_review_packet(
            selection,
            VerifiedHoldoutCheckout(
                root=Path("/synthetic"),
                source_text_by_alias=SOURCE_TEXTS,
            ),
            target_tag_contract=tag_contract,
            review_policy=review_policy,
        )


@pytest.mark.parametrize(
    ("case_ids", "expected_message"),
    [
        (("LH-0001",), "missing=\\['LH-0002'\\], extra=\\[\\]"),
        (
            ("LH-0001", "LH-0002", "LH-0003"),
            "missing=\\[\\], extra=\\['LH-0003'\\]",
        ),
    ],
)
def test_review_receipt_rejects_missing_or_extra_case_coverage(
    case_ids: tuple[str, ...],
    expected_message: str,
) -> None:
    packet = _packet()
    receipt = _receipt_from_payload(
        _receipt_payload(
            packet,
            round_id="round-a",
            reviewer_id="reviewer-a",
            case_ids=case_ids,
        )
    )

    with pytest.raises(ValueError, match=expected_message):
        validate_lifecycle_holdout_review_receipt(receipt, packet)


def test_review_receipt_accepts_full_packet_coverage() -> None:
    packet = _packet()
    receipt = _receipt(packet, round_id="round-a", reviewer_id="reviewer-a")

    validate_lifecycle_holdout_review_receipt(receipt, packet)


@pytest.mark.parametrize("collision", ["reviewer", "round", "receipt"])
def test_consensus_rejects_reviewer_round_and_receipt_reuse(collision: str) -> None:
    packet = _packet()
    first = _receipt(packet, round_id="round-a", reviewer_id="reviewer-a")
    second = _receipt(packet, round_id="round-b", reviewer_id="reviewer-b")
    if collision == "reviewer":
        second = _receipt(packet, round_id="round-b", reviewer_id="reviewer-a")
    elif collision == "round":
        second = _receipt(packet, round_id="round-a", reviewer_id="reviewer-b")
    else:
        second = second.model_copy(update={"receipt_id": first.receipt_id})

    with pytest.raises(ValueError, match=f"distinct {collision}"):
        build_lifecycle_holdout_consensus(packet, (first, second))


@pytest.mark.parametrize(
    ("artifact", "field"),
    [
        ("selection", "candidate_design_participant"),
        ("selection", "candidate_output_seen"),
        ("selection", "candidate_configuration_seen"),
        ("receipt_reviewer", "candidate_design_participant"),
        ("receipt_reviewer", "selection_participant"),
        ("receipt_blinding", "candidate_output_seen"),
        ("receipt_blinding", "candidate_configuration_seen"),
    ],
)
def test_schema_rejects_candidate_participation_and_false_blinding(
    artifact: str,
    field: str,
) -> None:
    if artifact == "selection":
        payload = _selection_payload()
        payload["selector_attestation"][field] = True
        with pytest.raises(ValidationError, match=field):
            _selection_from_payload(payload)
        return

    packet = _packet()
    payload = _receipt_payload(packet, round_id="round-a", reviewer_id="reviewer-a")
    section = "reviewer" if artifact == "receipt_reviewer" else "blinding"
    payload[section][field] = True
    with pytest.raises(ValidationError, match=field):
        _receipt_from_payload(payload)


def test_schema_rejects_review_completed_after_unblinding() -> None:
    packet = _packet()
    payload = _receipt_payload(packet, round_id="round-a", reviewer_id="reviewer-a")
    payload["blinding"]["review_completed_before_unblinding"] = False

    with pytest.raises(ValidationError, match="review_completed_before_unblinding"):
        _receipt_from_payload(payload)


def test_consensus_preserves_disagreement_as_unresolved_without_truth_label() -> None:
    packet = _packet()
    first = _receipt(packet, round_id="round-a", reviewer_id="reviewer-a")
    second = _receipt(
        packet,
        round_id="round-b",
        reviewer_id="reviewer-b",
        labels={"LH-0001": "negative", "LH-0002": "negative"},
    )

    consensus = build_lifecycle_holdout_consensus(packet, (first, second))
    disputed = consensus.cases[0]

    assert disputed.status == "unresolved"
    assert disputed.semantic_label is None
    assert disputed.expected_unit_kind is None
    assert disputed.expected_unit_symbol is None
    assert disputed.expected_source_span is None
    assert disputed.evidence_lines == ()
    assert consensus.consensus_status == "unresolved"
    assert consensus.release_ready is False
    assert consensus.release_blockers == ("unresolved_review_disagreement",)


def test_consensus_blocks_release_for_agreed_taxonomy_decision() -> None:
    packet = _packet()
    labels = {"LH-0001": "needs_taxonomy_decision", "LH-0002": "negative"}
    first = _receipt(
        packet,
        round_id="round-a",
        reviewer_id="reviewer-a",
        labels=labels,
    )
    second = _receipt(
        packet,
        round_id="round-b",
        reviewer_id="reviewer-b",
        labels=labels,
    )

    consensus = build_lifecycle_holdout_consensus(packet, (first, second))

    assert consensus.cases[0].status == "agreed"
    assert consensus.cases[0].semantic_label == "needs_taxonomy_decision"
    assert consensus.consensus_status == "unresolved"
    assert consensus.release_ready is False
    assert consensus.release_blockers == ("taxonomy_decision_required",)


def test_two_consistent_blinded_votes_produce_complete_consensus() -> None:
    packet = _packet()
    first = _receipt(
        packet,
        round_id="round-a",
        reviewer_id="reviewer-a",
        evidence_lines={"LH-0001": 1, "LH-0002": 2},
    )
    second = _receipt(
        packet,
        round_id="round-b",
        reviewer_id="reviewer-b",
        evidence_lines={"LH-0001": 2, "LH-0002": 3},
    )

    consensus = build_lifecycle_holdout_consensus(packet, (second, first))

    assert [item.status for item in consensus.cases] == ["agreed", "agreed"]
    assert consensus.cases[0].evidence_lines == (1, 2)
    assert consensus.cases[1].evidence_lines == (2, 3)
    assert consensus.consensus_status == "complete"
    assert consensus.release_ready is True
    assert consensus.release_blockers == ()
    assert [item.round_id for item in consensus.receipt_references] == [
        "round-a",
        "round-b",
    ]
