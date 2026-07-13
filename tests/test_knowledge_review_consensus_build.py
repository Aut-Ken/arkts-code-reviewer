from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.feature_routing.config import (
    DimensionDefinition,
    DimensionTriggers,
    TagDefinition,
    TagTriggers,
)
from arkts_code_reviewer.knowledge.models import (
    Applicability,
    ClauseCandidate,
    KnowledgeAnnotation,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.knowledge.review_consensus_build import (
    KnowledgeReviewConsensusBuild,
    build_knowledge_review_consensus_campaign,
    load_knowledge_review_consensus_build,
)
from arkts_code_reviewer.knowledge.review_packets import (
    KnowledgeReviewClause,
    KnowledgeReviewPacket,
    KnowledgeReviewPacketBuild,
    KnowledgeSourceExcerpt,
)


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _packet(index: int, prompt_hash: str) -> KnowledgeReviewPacket:
    text = f"Caller must validate state {index}."
    source_hash = _sha256_text(text)
    source_ref = SourceRef(
        source_id="synthetic",
        revision="1" * 40,
        relative_path=f"docs/rule-{index}.md",
        anchor="L1-L1",
        authority="test",
        content_hash=source_hash,
    )
    span = SourceSpan(start_line=1, end_line=1)
    candidate = ClauseCandidate.create(
        native_rule_id=f"R-{index}",
        rule_type="constraint",
        text=text,
        heading_path=("Rules",),
        parent_context=None,
        neighbor_candidate_ids=(),
        applicability=Applicability(),
        source_ref=source_ref,
        source_span=span,
    )
    rule_id = f"TEST/R-{index}"
    annotation = KnowledgeAnnotation(
        target_kind="clause",
        target_id=rule_id,
        index_version="test-index-v1",
        provenance=(),
        annotation_version="test-annotation-v1",
    )
    clause = KnowledgeReviewClause(
        rule_id=rule_id,
        proposed_status="Draft",
        domains=("test-domain",),
        candidate=candidate,
        annotation=annotation,
    )
    excerpt_fields = {
        "source_id": source_ref.source_id,
        "revision": source_ref.revision,
        "relative_path": source_ref.relative_path,
        "authority": source_ref.authority,
        "content_hash": source_ref.content_hash,
        "start_line": 1,
        "end_line": 1,
        "exact_text": text,
        "exact_text_hash": _sha256_text(text),
        "rule_ids": (rule_id,),
    }
    excerpt = KnowledgeSourceExcerpt(
        excerpt_id=_canonical_hash("knowledge-source-excerpt", excerpt_fields),
        **excerpt_fields,
    )
    fields = {
        "distribution": "external_model",
        "model_provider": "xai",
        "model_name": "grok-4.5",
        "extraction_build_id": "knowledge-extraction:sha256:" + "2" * 64,
        "annotation_build_id": "knowledge-annotation:sha256:" + "3" * 64,
        "source_bundle_id": "source-bundle:sha256:" + "4" * 64,
        "feature_config_fingerprint": "feature-config:sha256:" + "5" * 64,
        "annotation_config_fingerprint": (
            "knowledge-annotation-config:sha256:" + "6" * 64
        ),
        "annotation_version": "knowledge-annotation-version:sha256:" + "7" * 64,
        "export_policy_fingerprint": (
            "knowledge-model-export-policy:sha256:" + "8" * 64
        ),
        "prompt_version": "grok-knowledge-auditor-v4",
        "prompt_hash": prompt_hash,
        "tag_registry": (
            TagDefinition(
                id="has_test_signal",
                status="Active",
                description="Synthetic test signal.",
                triggers=TagTriggers(any_api=("test.api",)),
            ),
        ),
        "dimension_registry": (
            DimensionDefinition(
                id="DIM-99",
                title="Synthetic",
                status="Active",
                always_check=True,
                retrieval_policy="always",
                triggers=DimensionTriggers(),
            ),
        ),
        "source_domain_ids": ("test-domain",),
        "domain_registry": (),
        "api_catalog_slice": (),
        "unresolved_api_names": (),
        "clauses": (clause,),
        "source_excerpts": (excerpt,),
    }
    draft = KnowledgeReviewPacket.model_construct(
        packet_id="knowledge-review-packet:sha256:" + "0" * 64,
        **fields,
    )
    return KnowledgeReviewPacket(
        packet_id=_canonical_hash(
            "knowledge-review-packet",
            draft.identity_payload(),
        ),
        **fields,
    )


def _packet_build(
    packets: tuple[KnowledgeReviewPacket, ...],
) -> KnowledgeReviewPacketBuild:
    fields = {
        "distribution": "external_model",
        "extraction_build_id": packets[0].extraction_build_id,
        "annotation_build_id": packets[0].annotation_build_id,
        "source_bundle_id": packets[0].source_bundle_id,
        "export_policy_fingerprint": packets[0].export_policy_fingerprint,
        "prompt_version": packets[0].prompt_version,
        "prompt_hash": packets[0].prompt_hash,
        "packets": packets,
    }
    draft = KnowledgeReviewPacketBuild.model_construct(
        build_id="knowledge-review-packets:sha256:" + "0" * 64,
        **fields,
    )
    return KnowledgeReviewPacketBuild(
        build_id=_canonical_hash(
            "knowledge-review-packets",
            draft.identity_payload(),
        ),
        **fields,
    )


def _review_payload(
    packet: KnowledgeReviewPacket,
    *,
    decision: str,
) -> dict[str, object]:
    clause = packet.clauses[0]
    if decision == "accept":
        issue_codes: list[str] = []
        evidence: list[dict[str, object]] = []
        packet_decision = "accept"
    else:
        issue_codes = ["unsupported_claim"]
        source = clause.candidate.source_ref
        span = clause.candidate.source_span
        evidence = [
            {
                "source_id": source.source_id,
                "relative_path": source.relative_path,
                "start_line": span.start_line,
                "end_line": span.end_line,
                "exact_quote": clause.candidate.text,
            }
        ]
        packet_decision = "reject"
    return {
        "schema_version": "knowledge-model-review-v1",
        "packet_id": packet.packet_id,
        "reviewer": {
            "kind": "model",
            "provider": "xai",
            "model": "grok-4.5",
            "prompt_version": "grok-knowledge-auditor-v4",
        },
        "packet_decision": packet_decision,
        "clause_reviews": [
            {
                "rule_id": clause.rule_id,
                "decision": decision,
                "issue_codes": issue_codes,
                "evidence": evidence,
                "annotation_changes": [],
                "rationale": f"Synthetic {decision} review.",
            }
        ],
        "missing_clauses": [],
        "duplicate_groups": [],
        "conflicts": [],
        "summary": {
            "accepted": int(decision == "accept"),
            "rejected": int(decision == "reject"),
            "uncertain": 0,
            "with_corrections": 0,
        },
    }


def _write_response(
    round_path: Path,
    packet: KnowledgeReviewPacket,
    packet_raw: str,
    *,
    identity: str,
    decision: str,
) -> str:
    digest = packet.packet_id.rsplit(":", 1)[-1]
    stem = f"review-{digest}"
    review = _review_payload(packet, decision=decision)
    review_raw = json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    usage = {
        "input_tokens": 100,
        "cache_read_input_tokens": 10,
        "output_tokens": 20,
        "reasoning_tokens": 5,
        "total_tokens": 130,
    }
    wrapper = {
        "text": json.dumps(review, ensure_ascii=False, sort_keys=True),
        "stopReason": "EndTurn",
        "sessionId": f"session-{identity}",
        "requestId": f"request-{identity}",
        "thought": "",
        "usage": usage,
        "num_turns": 1,
        "modelUsage": {},
        "structuredOutput": review,
    }
    raw_response = json.dumps(wrapper, ensure_ascii=False, indent=2, sort_keys=True)
    receipt = {
        "schema_version": "knowledge-grok-review-receipt-v1",
        "packet_id": packet.packet_id,
        "packet_hash": _sha256_text(packet_raw),
        "review_hash": _sha256_text(review_raw),
        "raw_response_hash": _sha256_text(raw_response),
        "provider": "xai",
        "model": "grok-4.5",
        "prompt_version": "grok-knowledge-auditor-v4",
        "prompt_hash": packet.prompt_hash,
        "request_id": f"request-{identity}",
        "session_id": f"session-{identity}",
        "stop_reason": "EndTurn",
        "usage": usage,
        "packet_decision": review["packet_decision"],
        "summary": review["summary"],
        "validated": True,
    }
    round_path.joinpath(f"{stem}.review.json").write_text(
        review_raw,
        encoding="utf-8",
    )
    round_path.joinpath(f"{stem}.raw.json").write_text(
        raw_response + "\n",
        encoding="utf-8",
    )
    round_path.joinpath(f"{stem}.receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return stem


def _fixture(
    root: Path,
    *,
    reverse: bool = False,
) -> tuple[Path, Path, str]:
    packet_root = root / "packets"
    campaign_base = root / "campaign"
    round_one = campaign_base / "round-1"
    round_two = campaign_base / "round-2"
    packet_root.mkdir(parents=True)
    round_one.mkdir(parents=True)
    round_two.mkdir(parents=True)
    prompt = "Audit the attached packet."
    prompt_hash = _sha256_text(prompt)
    packets = tuple(
        sorted(
            (_packet(1, prompt_hash), _packet(2, prompt_hash)),
            key=lambda item: item.packet_id,
        )
    )
    build = _packet_build(packets)
    files: list[Path] = []
    build_path = packet_root / "build.json"
    build_path.write_text(build.model_dump_json(indent=2) + "\n", encoding="utf-8")
    files.append(build_path)
    prompt_path = packet_root / "prompt.md"
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    files.append(prompt_path)
    schema_path = packet_root / "grok-review-output.schema.json"
    schema_path.write_text("{}\n", encoding="utf-8")
    files.append(schema_path)
    packet_raws: dict[str, str] = {}
    for ordinal, packet in enumerate(packets, start=1):
        digest = packet.packet_id.rsplit(":", 1)[-1][:12]
        path = packet_root / f"packet-{ordinal:03d}-{digest}.json"
        raw = packet.model_dump_json(indent=2) + "\n"
        path.write_text(raw, encoding="utf-8")
        packet_raws[packet.packet_id] = raw
        files.append(path)
    manifest = {
        "schema_version": "knowledge-review-packet-manifest-v1",
        "build_id": build.build_id,
        "distribution": "external_model",
        "files": [
            {
                "relative_path": path.relative_to(packet_root).as_posix(),
                "content_hash": _sha256_file(path),
            }
            for path in sorted(files)
        ],
    }
    packet_root.joinpath("manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    indexed = list(enumerate(packets, start=1))
    if reverse:
        indexed.reverse()
    accepted_stem = ""
    for ordinal, packet in indexed:
        decision = "accept" if ordinal == 1 else "reject"
        first_stem = _write_response(
            round_one,
            packet,
            packet_raws[packet.packet_id],
            identity=f"round-1-{ordinal}",
            decision=decision,
        )
        _write_response(
            round_two,
            packet,
            packet_raws[packet.packet_id],
            identity=f"round-2-{ordinal}",
            decision=decision,
        )
        if decision == "accept":
            accepted_stem = first_stem
    return packet_root, campaign_base, accepted_stem


def _build(packet_root: Path, campaign_base: Path) -> KnowledgeReviewConsensusBuild:
    return build_knowledge_review_consensus_campaign(
        packet_root=packet_root,
        campaign_base=campaign_base,
        first_round_prefix="round-1",
        second_round_prefix="round-2",
    )


def _reuse_one_wrapper_identity(
    campaign: Path,
    stem: str,
    field: str,
) -> None:
    wrapper_keys = {"request_id": "requestId", "session_id": "sessionId"}
    wrapper_key = wrapper_keys[field]
    source_raw = campaign / "round-1" / f"{stem}.raw.json"
    target_raw = campaign / "round-2" / f"{stem}.raw.json"
    source_wrapper = json.loads(source_raw.read_text(encoding="utf-8"))
    target_wrapper = json.loads(target_raw.read_text(encoding="utf-8"))
    target_wrapper[wrapper_key] = source_wrapper[wrapper_key]
    raw_response = json.dumps(
        target_wrapper,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    target_raw.write_text(raw_response + "\n", encoding="utf-8")
    receipt_path = campaign / "round-2" / f"{stem}.receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = source_wrapper[wrapper_key]
    receipt["raw_response_hash"] = _sha256_text(raw_response)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_campaign_bridge_replays_receipts_and_builds_deterministic_consensus(
    tmp_path: Path,
) -> None:
    first_root, first_campaign, _ = _fixture(tmp_path / "first")
    second_root, second_campaign, _ = _fixture(tmp_path / "second", reverse=True)

    first = _build(first_root, first_campaign)
    second = build_knowledge_review_consensus_campaign(
        packet_root=second_root,
        campaign_base=second_campaign,
        first_round_prefix="round-2",
        second_round_prefix="round-1",
    )

    assert first == second
    assert first.build_id == first.expected_build_id()
    assert [item.round_prefix for item in first.campaign_summaries] == [
        "round-1",
        "round-2",
    ]
    assert first.campaign_summary_ids == tuple(
        item.summary_id for item in first.campaign_summaries
    )
    assert first.packet_count == 2
    assert [item.packet_id for item in first.packet_consensus] == sorted(
        item.packet_id for item in first.packet_consensus
    )
    assert first.packet_status_counts.release_ready == 1
    assert first.packet_status_counts.release_blocked == 1
    assert first.clause_status_counts.accepted == 1
    assert first.clause_status_counts.rejected == 1
    assert first.proposal_status_counts.confirmed_proposal == 0
    assert first.proposal_status_counts.quarantine == 0
    assert first.release_ready is False


def test_campaign_bridge_fails_closed_on_missing_or_duplicate_packet_receipt(
    tmp_path: Path,
) -> None:
    packet_root, campaign, _ = _fixture(tmp_path / "missing")
    sorted((campaign / "round-2").glob("*.receipt.json"))[0].unlink()
    with pytest.raises(ValueError, match="exactly one validated receipt.*missing"):
        _build(packet_root, campaign)

    packet_root, campaign, _ = _fixture(tmp_path / "duplicate")
    retry = campaign / "round-2-retry-1"
    retry.mkdir()
    receipt = sorted((campaign / "round-2").glob("*.receipt.json"))[0]
    shutil.copy2(receipt, retry / receipt.name)
    with pytest.raises(ValueError, match="exactly one validated receipt.*duplicate"):
        _build(packet_root, campaign)


def test_campaign_bridge_rejects_hash_drift_during_replay(tmp_path: Path) -> None:
    packet_root, campaign, _ = _fixture(tmp_path)
    review = sorted((campaign / "round-2").glob("*.review.json"))[0]
    review.write_text(review.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="receipt review_hash mismatch"):
        _build(packet_root, campaign)


def test_campaign_bridge_rejects_cross_round_attempt_and_receipt_replay(
    tmp_path: Path,
) -> None:
    packet_root, campaign, accepted_stem = _fixture(tmp_path)
    for suffix in ("review.json", "raw.json", "receipt.json"):
        shutil.copy2(
            campaign / "round-1" / f"{accepted_stem}.{suffix}",
            campaign / "round-2" / f"{accepted_stem}.{suffix}",
        )

    with pytest.raises(
        ValueError,
        match="request_id.*session_id.*receipt_file_hash",
    ):
        _build(packet_root, campaign)


@pytest.mark.parametrize("field", ["request_id", "session_id"])
def test_campaign_bridge_independently_rejects_cross_round_id_reuse(
    tmp_path: Path,
    field: str,
) -> None:
    packet_root, campaign, accepted_stem = _fixture(tmp_path)
    _reuse_one_wrapper_identity(campaign, accepted_stem, field)

    with pytest.raises(ValueError, match=field):
        _build(packet_root, campaign)


def test_campaign_bridge_checks_failed_attempt_ids_across_campaigns(
    tmp_path: Path,
) -> None:
    packet_root, campaign, accepted_stem = _fixture(tmp_path)
    retry = campaign / "round-2-retry-1"
    retry.mkdir()
    shutil.copy2(
        campaign / "round-1" / f"{accepted_stem}.raw.json",
        retry / f"{accepted_stem}.raw.json",
    )

    with pytest.raises(ValueError, match="request_id.*session_id"):
        _build(packet_root, campaign)


def test_campaign_bridge_rejects_same_campaign_prefix(tmp_path: Path) -> None:
    packet_root, campaign, _ = _fixture(tmp_path)

    with pytest.raises(ValueError, match="different round prefixes"):
        build_knowledge_review_consensus_campaign(
            packet_root=packet_root,
            campaign_base=campaign,
            first_round_prefix="round-1",
            second_round_prefix="round-1",
        )


def test_consensus_build_model_rejects_count_and_id_tampering(tmp_path: Path) -> None:
    packet_root, campaign, _ = _fixture(tmp_path)
    result = _build(packet_root, campaign)
    payload = result.model_dump(mode="json")

    wrong_count = dict(payload)
    wrong_count["packet_count"] = 3
    with pytest.raises(ValidationError, match="packet count"):
        KnowledgeReviewConsensusBuild.model_validate(wrong_count)

    wrong_id = dict(payload)
    wrong_id["build_id"] = "knowledge-review-consensus-build:sha256:" + "f" * 64
    with pytest.raises(ValidationError, match="build_id"):
        KnowledgeReviewConsensusBuild.model_validate(wrong_id)


def test_consensus_build_binds_campaign_provenance_and_selected_receipts(
    tmp_path: Path,
) -> None:
    packet_root, campaign, _ = _fixture(tmp_path)
    result = _build(packet_root, campaign)
    payload = result.model_dump(mode="json")

    changed_provenance = json.loads(json.dumps(payload))
    replacement_build_id = "knowledge-review-packets:sha256:" + "9" * 64
    changed_provenance["packet_build_id"] = replacement_build_id
    changed_provenance["build_id"] = _canonical_hash(
        "knowledge-review-consensus-build",
        {
            key: value
            for key, value in changed_provenance.items()
            if key not in {"schema_version", "build_id"}
        },
    )
    with pytest.raises(ValidationError, match="packet provenance"):
        KnowledgeReviewConsensusBuild.model_validate(changed_provenance)

    changed_mapping = json.loads(json.dumps(payload))
    summary = changed_mapping["campaign_summaries"][0]
    summary["selected_receipts"][0]["request_id"] = "request-substituted"
    summary["summary_id"] = _canonical_hash(
        "knowledge-grok-campaign",
        {
            key: value
            for key, value in summary.items()
            if key not in {"schema_version", "summary_id"}
        },
    )
    changed_mapping["campaign_summary_ids"][0] = summary["summary_id"]
    changed_mapping["build_id"] = _canonical_hash(
        "knowledge-review-consensus-build",
        {
            key: value
            for key, value in changed_mapping.items()
            if key not in {"schema_version", "build_id"}
        },
    )
    with pytest.raises(ValidationError, match="selected receipt"):
        KnowledgeReviewConsensusBuild.model_validate(changed_mapping)


def test_consensus_build_strict_loader_rejects_invalid_json_boundaries(
    tmp_path: Path,
) -> None:
    packet_root, campaign, _ = _fixture(tmp_path)
    result = _build(packet_root, campaign)
    raw = result.model_dump_json()

    assert load_knowledge_review_consensus_build(raw) == result
    with pytest.raises(ValueError, match="duplicate JSON key: packet_count"):
        load_knowledge_review_consensus_build(
            raw[:-1] + ',"packet_count":2}'
        )
    with pytest.raises(ValueError, match="must use UTF-8"):
        load_knowledge_review_consensus_build(b"\xff")
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_knowledge_review_consensus_build(raw[:-1] + ',"unknown":true}')


def test_consensus_build_cli_uses_exclusive_output_creation(tmp_path: Path) -> None:
    packet_root, campaign, _ = _fixture(tmp_path)
    output = tmp_path / "result" / "consensus.json"
    command = [
        sys.executable,
        "tools/build_knowledge_review_consensus.py",
        "--packet-root",
        str(packet_root),
        "--campaign-base",
        str(campaign),
        "--first-round-prefix",
        "round-1",
        "--second-round-prefix",
        "round-2",
        "--output",
        str(output),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = "src"

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    built = KnowledgeReviewConsensusBuild.model_validate_json(
        output.read_text(encoding="utf-8")
    )
    assert built.packet_count == 2
    original = output.read_bytes()

    repeated = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert repeated.returncode != 0
    assert "must not already exist" in repeated.stderr
    assert output.read_bytes() == original
