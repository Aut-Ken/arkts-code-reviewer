from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    SemanticLabel,
    canonical_hash,
    canonical_json,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review import (
    TagTruthV2Consensus,
    TagTruthV2ReviewReceipt,
    build_tag_truth_v2_consensus,
    load_tag_truth_v2_consensus,
    load_tag_truth_v2_review_receipt,
    parse_tag_truth_v2_consensus,
    parse_tag_truth_v2_review_receipt,
    review_receipt_payload_with_id,
    seal_tag_truth_v2_review_receipt_payload,
    validate_tag_truth_v2_review_receipt,
    verify_tag_truth_v2_consensus,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    TagTruthV2ReviewPacket,
    review_policy_payload_with_id,
)

ROOT = Path(__file__).resolve().parents[1]


def _tag_contract() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "tag-contract-snapshot-v1",
        "tag_id": "has_network",
        "version": "network-blind-pilot-v1",
        "axes_relationship": "independent",
        "exact_semantics": {
            "positive": "The ReviewUnit itself performs network behavior.",
            "negative": "The ReviewUnit itself does not perform network behavior.",
            "abstain": "The exact axis cannot be decided from the ReviewUnit.",
        },
        "routing_semantics": {
            "positive": "The full source contains a conservative network routing signal.",
            "negative": "The full source contains no reliable network routing signal.",
            "abstain": "The routing axis cannot be decided from the full source.",
        },
    }
    payload["contract_fingerprint"] = canonical_hash("tag-contract-snapshot", payload)
    return payload


def _review_policy() -> dict[str, Any]:
    return review_policy_payload_with_id(
        {
            "schema_version": "tag-truth-review-policy-v1",
            "version": "dual-axis-blind-v1",
            "approval_status": "draft_not_approved",
            "owner_instruction": "Locate the ReviewUnit owning the probe line.",
            "exact_instruction": "Judge exact applicability from that ReviewUnit.",
            "routing_instruction": "Judge routing independently using the full source.",
            "abstain_instruction": "Abstain whenever either axis is undecidable.",
        }
    )


def _packet() -> TagTruthV2ReviewPacket:
    contract = _tag_contract()
    payload: dict[str, Any] = {
        "schema_version": "tag-truth-v2-review-packet-v1",
        "selection_id": f"tag-truth-selection:sha256:{'1' * 64}",
        "suite_id": "network-blind-pilot-v1",
        "target_tag_id": "has_network",
        "tag_contract": contract,
        "review_policy": _review_policy(),
        "cases": [
            {
                "case_id": "case-0000000000000001",
                "review_source_id": "review-case-0000000000000001",
                "probe_line": 3,
                "source_text": ("export struct Alpha {\n  build() {\n    fetchData()\n  }\n}\n"),
                "line_count": 5,
            },
            {
                "case_id": "case-0000000000000002",
                "review_source_id": "review-case-0000000000000002",
                "probe_line": 2,
                "source_text": "function beta() {\n  return 2\n}\n",
                "line_count": 3,
            },
        ],
    }
    payload["packet_id"] = canonical_hash("tag-truth-review-packet", payload)
    return TagTruthV2ReviewPacket.model_validate_json(canonical_json(payload))


def _axis(
    label: SemanticLabel,
    evidence_lines: list[int],
    rationale: str,
    *,
    abstain_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "evidence_lines": evidence_lines,
        "rationale": rationale,
        "abstain_reason": abstain_reason,
    }


def _receipt_draft(
    packet: TagTruthV2ReviewPacket,
    *,
    round_id: str,
    reviewer_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": "tag-truth-v2-review-receipt-v1",
        "round_id": round_id,
        "selection_id": packet.selection_id,
        "packet_id": packet.packet_id,
        "suite_id": packet.suite_id,
        "target_tag_id": packet.target_tag_id,
        "tag_contract_fingerprint": packet.tag_contract.contract_fingerprint,
        "review_policy_fingerprint": packet.review_policy.policy_fingerprint,
        "reviewer": {
            "reviewer_id": reviewer_id,
            "reviewer_kind": "human",
            "reviewer_role": "arkts_domain_reviewer",
            "affiliation": "Independent ArkTS review group",
            "candidate_design_participant": False,
            "selection_participant": False,
        },
        "blinding": {
            "candidate_output_seen": False,
            "candidate_configuration_seen": False,
            "selection_manifest_seen": False,
            "review_completed_before_unblinding": True,
            "attested_at": "2026-07-16T09:00:00Z",
        },
        "recorded_at": "2026-07-16T09:05:00Z",
        "decisions": [
            {
                "case_id": "case-0000000000000001",
                "review_unit": {
                    "unit_kind": "build_method",
                    "qualified_symbol": "Alpha.build",
                    "source_span": {"start_line": 2, "end_line": 4},
                },
                "exact": _axis("positive", [3], "The ReviewUnit performs the call."),
                "routing": _axis(
                    "positive",
                    [1, 5],
                    "The full source provides a routing signal.",
                ),
            },
            {
                "case_id": "case-0000000000000002",
                "review_unit": {
                    "unit_kind": "function",
                    "qualified_symbol": "beta",
                    "source_span": {"start_line": 1, "end_line": 3},
                },
                "exact": _axis("negative", [2], "The function has no network behavior."),
                "routing": _axis("negative", [1], "The full source has no routing signal."),
            },
        ],
    }


def _receipt(
    packet: TagTruthV2ReviewPacket,
    *,
    round_id: str = "round-a",
    reviewer_id: str = "reviewer-a",
    draft: Mapping[str, object] | None = None,
) -> TagTruthV2ReviewReceipt:
    payload = draft or _receipt_draft(
        packet,
        round_id=round_id,
        reviewer_id=reviewer_id,
    )
    return seal_tag_truth_v2_review_receipt_payload(payload)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _run_tool(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(path), *arguments],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
    )


def test_receipt_is_closed_frozen_self_hashed_duplicate_safe_and_revalidated() -> None:
    packet = _packet()
    receipt = _receipt(packet)
    encoded = json.dumps(receipt.model_dump(mode="json"), sort_keys=False).encode()
    assert parse_tag_truth_v2_review_receipt(encoded) == receipt

    with pytest.raises(ValidationError, match="frozen"):
        receipt.__setattr__("suite_id", "changed")

    extra = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    extra["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _receipt(packet, draft=extra)

    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_tag_truth_v2_review_receipt(
            b'{"schema_version":"tag-truth-v2-review-receipt-v1",'
            b'"schema_version":"tag-truth-v2-review-receipt-v1"}'
        )

    forged_payload = receipt.model_dump(mode="json")
    forged_payload["recorded_at"] = "2026-07-16T09:06:00Z"
    with pytest.raises(ValueError, match="receipt_id"):
        parse_tag_truth_v2_review_receipt(json.dumps(forged_payload).encode())

    forged_copy = receipt.model_copy(update={"suite_id": "forged"})
    with pytest.raises(ValidationError, match="receipt_id"):
        validate_tag_truth_v2_review_receipt(forged_copy, packet)


def test_receipt_seal_canonicalizes_omitted_optional_nulls() -> None:
    packet = _packet()
    draft = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    for decision in draft["decisions"]:
        decision["exact"].pop("abstain_reason")
        decision["routing"].pop("abstain_reason")

    receipt = seal_tag_truth_v2_review_receipt_payload(draft)

    assert all(decision.exact.abstain_reason is None for decision in receipt.decisions)
    assert all(decision.routing.abstain_reason is None for decision in receipt.decisions)
    assert review_receipt_payload_with_id(draft) == receipt.model_dump(mode="json")


def test_receipt_must_bind_packet_and_cover_every_case_exactly_once() -> None:
    packet = _packet()
    validate_tag_truth_v2_review_receipt(_receipt(packet), packet)

    wrong_binding = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    wrong_binding["suite_id"] = "different-suite"
    with pytest.raises(ValueError, match="identity does not match"):
        validate_tag_truth_v2_review_receipt(_receipt(packet, draft=wrong_binding), packet)

    incomplete = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    incomplete["decisions"] = incomplete["decisions"][:1]
    with pytest.raises(ValueError, match="case coverage mismatch"):
        validate_tag_truth_v2_review_receipt(_receipt(packet, draft=incomplete), packet)

    extra = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    extra_decision = copy.deepcopy(extra["decisions"][1])
    extra_decision["case_id"] = "case-0000000000000003"
    extra["decisions"].append(extra_decision)
    with pytest.raises(ValueError, match="case coverage mismatch"):
        validate_tag_truth_v2_review_receipt(_receipt(packet, draft=extra), packet)

    duplicate = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    duplicate["decisions"] = [duplicate["decisions"][0], duplicate["decisions"][0]]
    with pytest.raises(ValidationError, match="sorted and unique"):
        _receipt(packet, draft=duplicate)


@pytest.mark.parametrize(
    ("section", "field", "forbidden_value"),
    [
        ("reviewer", "candidate_design_participant", True),
        ("reviewer", "selection_participant", True),
        ("blinding", "candidate_output_seen", True),
        ("blinding", "candidate_configuration_seen", True),
        ("blinding", "selection_manifest_seen", True),
        ("blinding", "review_completed_before_unblinding", False),
    ],
)
def test_receipt_rejects_reviewer_participation_and_false_blinding(
    section: str,
    field: str,
    forbidden_value: bool,
) -> None:
    packet = _packet()
    draft = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    draft[section][field] = forbidden_value

    with pytest.raises(ValidationError, match=field):
        _receipt(packet, draft=draft)


def test_receipt_requires_valid_and_ordered_audit_timestamps() -> None:
    packet = _packet()
    invalid = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    invalid["blinding"]["attested_at"] = "2026-13-16T09:00:00Z"
    with pytest.raises(ValidationError, match="valid UTC timestamp"):
        _receipt(packet, draft=invalid)

    reversed_times = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    reversed_times["recorded_at"] = "2026-07-16T08:59:59Z"
    with pytest.raises(ValidationError, match="recorded before"):
        _receipt(packet, draft=reversed_times)


def test_receipt_enforces_review_unit_and_axis_evidence_boundaries() -> None:
    packet = _packet()
    receipt = _receipt(packet)
    validate_tag_truth_v2_review_receipt(receipt, packet)
    assert receipt.decisions[0].routing.evidence_lines == (1, 5)

    misses_probe = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    misses_probe["decisions"][0]["review_unit"]["source_span"] = {
        "start_line": 1,
        "end_line": 2,
    }
    misses_probe["decisions"][0]["exact"]["evidence_lines"] = [2]
    with pytest.raises(ValueError, match="must contain probe_line"):
        validate_tag_truth_v2_review_receipt(_receipt(packet, draft=misses_probe), packet)

    past_source = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    past_source["decisions"][0]["review_unit"]["source_span"]["end_line"] = 6
    with pytest.raises(ValueError, match="exceeds source"):
        validate_tag_truth_v2_review_receipt(_receipt(packet, draft=past_source), packet)

    exact_outside_unit = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    exact_outside_unit["decisions"][0]["exact"]["evidence_lines"] = [1]
    with pytest.raises(ValidationError, match="exact evidence"):
        _receipt(packet, draft=exact_outside_unit)

    routing_past_source = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    routing_past_source["decisions"][0]["routing"]["evidence_lines"] = [6]
    with pytest.raises(ValueError, match="routing evidence exceeds full source"):
        validate_tag_truth_v2_review_receipt(_receipt(packet, draft=routing_past_source), packet)


@pytest.mark.parametrize(
    ("collision", "expected_message"),
    [
        ("reviewer", "distinct reviewers"),
        ("round", "distinct review rounds"),
        ("receipt", "receipt_id"),
    ],
)
def test_consensus_requires_distinct_reviewer_round_and_receipt(
    collision: str,
    expected_message: str,
) -> None:
    packet = _packet()
    first = _receipt(packet, round_id="round-a", reviewer_id="reviewer-a")
    second = _receipt(packet, round_id="round-b", reviewer_id="reviewer-b")
    if collision == "reviewer":
        second = _receipt(packet, round_id="round-b", reviewer_id="reviewer-a")
    elif collision == "round":
        second = _receipt(packet, round_id="round-a", reviewer_id="reviewer-b")
    else:
        second = second.model_copy(update={"receipt_id": first.receipt_id})

    with pytest.raises((ValueError, ValidationError), match=expected_message):
        build_tag_truth_v2_consensus(packet, (first, second))


def test_complete_consensus_is_deterministic_and_rebuild_verifiable() -> None:
    packet = _packet()
    first = _receipt(packet, round_id="round-a", reviewer_id="reviewer-a")
    second_draft = _receipt_draft(packet, round_id="round-b", reviewer_id="reviewer-b")
    second_draft["decisions"][0]["exact"]["evidence_lines"] = [2, 3]
    second_draft["decisions"][0]["routing"]["evidence_lines"] = [5]
    second_draft["decisions"][0]["exact"]["rationale"] = "Independent exact rationale."
    second = _receipt(packet, draft=second_draft)

    forward = build_tag_truth_v2_consensus(packet, (first, second))
    reverse = build_tag_truth_v2_consensus(packet, (second, first))
    assert forward == reverse
    assert canonical_json(forward.model_dump(mode="json")) == canonical_json(
        reverse.model_dump(mode="json")
    )
    assert forward.consensus_status == "complete"
    assert forward.consensus_blockers == ()
    assert forward.cases[0].review_unit_status == "agreed"
    assert forward.cases[0].exact.status == "agreed_resolved"
    assert forward.cases[0].exact.evidence_lines == (2, 3)
    assert forward.cases[0].routing.evidence_lines == (1, 5)
    assert len(forward.cases[0].exact.rationale_votes) == 2
    assert len({reference.reviewer_id for reference in forward.receipt_references}) == 2
    assert len({reference.round_id for reference in forward.receipt_references}) == 2
    assert len({reference.receipt_id for reference in forward.receipt_references}) == 2
    verify_tag_truth_v2_consensus(forward, packet, (second, first))

    parsed = parse_tag_truth_v2_consensus(json.dumps(forward.model_dump(mode="json")).encode())
    assert parsed == forward
    forged = forward.model_copy(update={"consensus_status": "unresolved"})
    with pytest.raises((ValueError, ValidationError)):
        verify_tag_truth_v2_consensus(forged, packet, (first, second))

    different_draft = _receipt_draft(packet, round_id="round-b", reviewer_id="reviewer-b")
    different_draft["decisions"][0]["exact"]["rationale"] = "A different valid vote."
    different_second = _receipt(packet, draft=different_draft)
    with pytest.raises(ValueError, match="does not rebuild"):
        verify_tag_truth_v2_consensus(forward, packet, (first, different_second))


@pytest.mark.parametrize(
    ("disputed_axis", "preserved_axis"),
    [("exact", "routing"), ("routing", "exact")],
)
def test_single_axis_disagreement_preserves_the_other_axis(
    disputed_axis: str,
    preserved_axis: str,
) -> None:
    packet = _packet()
    first = _receipt(packet, round_id="round-a", reviewer_id="reviewer-a")
    second_draft = _receipt_draft(packet, round_id="round-b", reviewer_id="reviewer-b")
    second_draft["decisions"][0][disputed_axis]["label"] = "negative"
    second_draft["decisions"][0][disputed_axis]["rationale"] = "Independent disagreement."
    second = _receipt(packet, draft=second_draft)

    consensus = build_tag_truth_v2_consensus(packet, (first, second))
    case = consensus.cases[0]
    assert getattr(case, disputed_axis).status == "unresolved"
    assert getattr(case, disputed_axis).label is None
    assert getattr(case, preserved_axis).status == "agreed_resolved"
    assert getattr(case, preserved_axis).label == "positive"
    assert consensus.consensus_status == "unresolved"
    assert consensus.consensus_blockers == ("unresolved_review_disagreement",)


def test_review_unit_disagreement_blocks_both_axes() -> None:
    packet = _packet()
    first = _receipt(packet, round_id="round-a", reviewer_id="reviewer-a")
    second_draft = _receipt_draft(packet, round_id="round-b", reviewer_id="reviewer-b")
    second_draft["decisions"][0]["review_unit"] = {
        "unit_kind": "struct",
        "qualified_symbol": "Alpha",
        "source_span": {"start_line": 1, "end_line": 5},
    }
    second = _receipt(packet, draft=second_draft)

    consensus = build_tag_truth_v2_consensus(packet, (first, second))
    case = consensus.cases[0]
    assert case.review_unit_status == "unresolved"
    assert case.review_unit is None
    assert case.exact.status == "unresolved"
    assert case.routing.status == "unresolved"
    assert case.exact.label is None
    assert case.routing.label is None


def test_agreed_taxonomy_abstain_is_preserved_as_a_blocker() -> None:
    packet = _packet()
    drafts = [
        _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a"),
        _receipt_draft(packet, round_id="round-b", reviewer_id="reviewer-b"),
    ]
    for index, draft in enumerate(drafts, start=1):
        draft["decisions"][0]["exact"] = _axis(
            "needs_taxonomy_decision",
            [3],
            f"Reviewer {index} requires a taxonomy decision.",
            abstain_reason="ambiguous_network_semantics",
        )
    consensus = build_tag_truth_v2_consensus(
        packet,
        tuple(_receipt(packet, draft=draft) for draft in drafts),
    )

    exact = consensus.cases[0].exact
    assert exact.status == "agreed_abstain"
    assert exact.label == "needs_taxonomy_decision"
    assert exact.abstain_reasons == ("ambiguous_network_semantics",)
    assert consensus.consensus_status == "unresolved"
    assert consensus.consensus_blockers == ("taxonomy_decision_required",)


def test_consensus_schema_rejects_closed_or_self_hash_drift() -> None:
    packet = _packet()
    receipts = (
        _receipt(packet, round_id="round-a", reviewer_id="reviewer-a"),
        _receipt(packet, round_id="round-b", reviewer_id="reviewer-b"),
    )
    consensus = build_tag_truth_v2_consensus(packet, receipts)
    extra = consensus.model_dump(mode="json")
    extra["unexpected"] = True
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        parse_tag_truth_v2_consensus(json.dumps(extra).encode())

    forged = consensus.model_dump(mode="json")
    forged["suite_id"] = "different-suite"
    with pytest.raises(ValueError, match="consensus_id"):
        parse_tag_truth_v2_consensus(json.dumps(forged).encode())

    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_tag_truth_v2_consensus(
            b'{"schema_version":"tag-truth-v2-consensus-v1",'
            b'"schema_version":"tag-truth-v2-consensus-v1"}'
        )

    with pytest.raises(ValidationError, match="frozen"):
        consensus.__setattr__("suite_id", "changed")

    assert (
        TagTruthV2Consensus.model_validate_json(canonical_json(consensus.model_dump(mode="json")))
        == consensus
    )


def test_receipt_and_consensus_loaders_reject_symlinks(tmp_path: Path) -> None:
    packet = _packet()
    receipts = (
        _receipt(packet, round_id="round-a", reviewer_id="reviewer-a"),
        _receipt(packet, round_id="round-b", reviewer_id="reviewer-b"),
    )
    consensus = build_tag_truth_v2_consensus(packet, receipts)
    receipt_path = tmp_path / "receipt.json"
    consensus_path = tmp_path / "consensus.json"
    _write_json(receipt_path, receipts[0].model_dump(mode="json"))
    _write_json(consensus_path, consensus.model_dump(mode="json"))
    assert load_tag_truth_v2_review_receipt(receipt_path) == receipts[0]
    assert load_tag_truth_v2_consensus(consensus_path) == consensus

    receipt_link = tmp_path / "receipt-link.json"
    consensus_link = tmp_path / "consensus-link.json"
    receipt_link.symlink_to(receipt_path)
    consensus_link.symlink_to(consensus_path)
    with pytest.raises(ValueError, match="regular non-symlink"):
        load_tag_truth_v2_review_receipt(receipt_link)
    with pytest.raises(ValueError, match="regular non-symlink"):
        load_tag_truth_v2_consensus(consensus_link)


def test_review_and_consensus_cli_exit_codes(tmp_path: Path) -> None:
    packet = _packet()
    packet_path = tmp_path / "packet.json"
    draft_path = tmp_path / "draft.json"
    _write_json(packet_path, packet.model_dump(mode="json"))
    draft = _receipt_draft(packet, round_id="round-a", reviewer_id="reviewer-a")
    for decision in draft["decisions"]:
        decision["exact"].pop("abstain_reason")
        decision["routing"].pop("abstain_reason")
    _write_json(draft_path, draft)

    seal_tool = ROOT / "tools/seal_tag_truth_v2_review_receipt.py"
    build_tool = ROOT / "tools/build_tag_truth_v2_consensus.py"
    sealed = _run_tool(seal_tool, "--packet", str(packet_path), "--draft", str(draft_path))
    assert sealed.returncode == 0, sealed.stderr
    first_payload = json.loads(sealed.stdout)
    first_path = tmp_path / "receipt-a.json"
    _write_json(first_path, first_payload)

    invalid_draft = copy.deepcopy(first_payload)
    invalid_draft.pop("receipt_id")
    invalid_draft["decisions"] = invalid_draft["decisions"][:1]
    invalid_draft_path = tmp_path / "invalid-draft.json"
    _write_json(invalid_draft_path, invalid_draft)
    invalid_seal = _run_tool(
        seal_tool,
        "--packet",
        str(packet_path),
        "--draft",
        str(invalid_draft_path),
    )
    assert invalid_seal.returncode == 2

    second = _receipt(packet, round_id="round-b", reviewer_id="reviewer-b")
    second_path = tmp_path / "receipt-b.json"
    _write_json(second_path, second.model_dump(mode="json"))
    complete = _run_tool(
        build_tool,
        "--packet",
        str(packet_path),
        "--receipt",
        str(first_path),
        "--receipt",
        str(second_path),
    )
    assert complete.returncode == 0, complete.stderr

    unresolved_draft = _receipt_draft(
        packet,
        round_id="round-b",
        reviewer_id="reviewer-b",
    )
    unresolved_draft["decisions"][0]["routing"]["label"] = "negative"
    unresolved_draft["decisions"][0]["routing"]["rationale"] = "Disputed routing label."
    unresolved = _receipt(packet, draft=unresolved_draft)
    unresolved_path = tmp_path / "receipt-unresolved.json"
    _write_json(unresolved_path, unresolved.model_dump(mode="json"))
    disputed = _run_tool(
        build_tool,
        "--packet",
        str(packet_path),
        "--receipt",
        str(first_path),
        "--receipt",
        str(unresolved_path),
    )
    assert disputed.returncode == 1, disputed.stderr

    invalid_consensus = _run_tool(
        build_tool,
        "--packet",
        str(packet_path),
        "--receipt",
        str(first_path),
    )
    assert invalid_consensus.returncode == 2


def test_stage2b_imports_do_not_load_runtime_analysis_or_routing_modules() -> None:
    module = "arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review"
    tools = tuple(
        str(path)
        for path in (
            ROOT / "tools/seal_tag_truth_v2_review_receipt.py",
            ROOT / "tools/build_tag_truth_v2_consensus.py",
        )
    )
    script = (
        "import runpy,sys; "
        f"import {module}; "
        f"[runpy.run_path(path, run_name='stage2b_import_test') for path in {tools!r}]; "
        "forbidden = ("
        "'arkts_code_reviewer.code_analysis', "
        "'arkts_code_reviewer.feature_routing.engine', "
        "'arkts_code_reviewer.feature_routing.matcher', "
        "'arkts_code_reviewer.feature_routing.config'); "
        "assert not any(name == item or name.startswith(item + '.') "
        "for name in sys.modules for item in forbidden), "
        "sorted(name for name in sys.modules "
        "if any(name == item or name.startswith(item + '.') for item in forbidden))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
