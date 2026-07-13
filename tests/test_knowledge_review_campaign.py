from __future__ import annotations

import hashlib
import json
import shutil
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
from arkts_code_reviewer.knowledge.review_campaign import (
    KnowledgeGrokCampaignSummary,
    summarize_knowledge_grok_campaign,
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
    excerpt_payload = {
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
        excerpt_id=_canonical_hash("knowledge-source-excerpt", excerpt_payload),
        **excerpt_payload,
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
    incomplete = KnowledgeReviewPacket.model_construct(
        packet_id="knowledge-review-packet:sha256:" + "0" * 64,
        **fields,
    )
    return KnowledgeReviewPacket(
        packet_id=_canonical_hash(
            "knowledge-review-packet",
            incomplete.identity_payload(),
        ),
        **fields,
    )


def _packet_build(packets: tuple[KnowledgeReviewPacket, ...]) -> KnowledgeReviewPacketBuild:
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
    incomplete = KnowledgeReviewPacketBuild.model_construct(
        build_id="knowledge-review-packets:sha256:" + "0" * 64,
        **fields,
    )
    return KnowledgeReviewPacketBuild(
        build_id=_canonical_hash(
            "knowledge-review-packets",
            incomplete.identity_payload(),
        ),
        **fields,
    )


def _accepted_review(packet: KnowledgeReviewPacket) -> dict[str, object]:
    return {
        "schema_version": "knowledge-model-review-v1",
        "packet_id": packet.packet_id,
        "reviewer": {
            "kind": "model",
            "provider": "xai",
            "model": "grok-4.5",
            "prompt_version": "grok-knowledge-auditor-v4",
        },
        "packet_decision": "accept",
        "clause_reviews": [
            {
                "rule_id": packet.clauses[0].rule_id,
                "decision": "accept",
                "issue_codes": [],
                "evidence": [],
                "annotation_changes": [],
                "rationale": "The candidate is fully supported by the packet.",
            }
        ],
        "missing_clauses": [],
        "duplicate_groups": [],
        "conflicts": [],
        "summary": {
            "accepted": 1,
            "rejected": 0,
            "uncertain": 0,
            "with_corrections": 0,
        },
    }


def _write_response(
    round_path: Path,
    packet: KnowledgeReviewPacket,
    packet_raw: str,
    index: int,
) -> None:
    digest = packet.packet_id.rsplit(":", 1)[-1]
    stem = f"review-{digest}"
    review = _accepted_review(packet)
    review_raw = json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    usage = {
        "input_tokens": 100 + index,
        "cache_read_input_tokens": 10,
        "output_tokens": 20,
        "reasoning_tokens": 5,
        "total_tokens": 130 + index,
    }
    wrapper = {
        "text": json.dumps(review, ensure_ascii=False, sort_keys=True),
        "stopReason": "EndTurn",
        "sessionId": f"session-{index}",
        "requestId": f"request-{index}",
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
        "request_id": f"request-{index}",
        "session_id": f"session-{index}",
        "stop_reason": "EndTurn",
        "usage": usage,
        "packet_decision": "accept",
        "summary": review["summary"],
        "validated": True,
    }
    round_path.joinpath(f"{stem}.review.json").write_text(review_raw, encoding="utf-8")
    round_path.joinpath(f"{stem}.raw.json").write_text(
        raw_response + "\n",
        encoding="utf-8",
    )
    round_path.joinpath(f"{stem}.receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fixture(root: Path, *, reverse: bool = False) -> tuple[Path, Path]:
    packet_root = root / "packets"
    campaign_base = root / "campaign"
    round_path = campaign_base / "round-1"
    packet_root.mkdir(parents=True)
    round_path.mkdir(parents=True)
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
    ordered = list(enumerate(packets, start=1))
    if reverse:
        ordered.reverse()
    for index, packet in ordered:
        _write_response(round_path, packet, packet_raws[packet.packet_id], index)
    first = packets[0]
    digest = first.packet_id.rsplit(":", 1)[-1]
    round_path.joinpath(f"review-{digest}.failed.stderr.txt").write_text(
        "synthetic transport failure\n",
        encoding="utf-8",
    )
    return packet_root, campaign_base


def _summary(packet_root: Path, campaign_base: Path):
    return summarize_knowledge_grok_campaign(
        packet_root=packet_root,
        campaign_base=campaign_base,
        round_prefix="round-1",
    )


def test_campaign_summary_is_deterministic_and_input_order_independent(
    tmp_path: Path,
) -> None:
    first_root, first_campaign = _fixture(tmp_path / "first")
    second_root, second_campaign = _fixture(tmp_path / "second", reverse=True)

    first = _summary(first_root, first_campaign)
    second = _summary(second_root, second_campaign)

    assert first == second
    assert first.packet_count == 2
    assert first.packet_decisions.accept == 2
    assert first.clause_decisions.accepted == 2
    assert first.valid_usage.total_tokens == 263
    assert first.structured_attempt_count == 2
    assert first.transport_failure_count == 1
    assert [item.packet_id for item in first.selected_receipts] == sorted(
        item.packet_id for item in first.selected_receipts
    )
    for item in first.selected_receipts:
        receipt_path = first_campaign / item.receipt_file
        stem = receipt_path.name.removesuffix(".receipt.json")
        assert item.receipt_file_hash == _sha256_file(receipt_path)
        assert item.review_file_hash == _sha256_file(
            receipt_path.with_name(f"{stem}.review.json")
        )
        assert item.raw_file_hash == _sha256_file(
            receipt_path.with_name(f"{stem}.raw.json")
        )
    failure = first.failed_attempts[0]
    assert failure.failure_kind == "transport_error"
    assert failure.artifacts
    assert all(
        artifact.content_hash
        == _sha256_file(first_campaign / artifact.relative_path)
        for artifact in failure.artifacts
    )


def test_campaign_rejects_missing_and_duplicate_receipts(tmp_path: Path) -> None:
    packet_root, campaign = _fixture(tmp_path)
    receipt = sorted((campaign / "round-1").glob("*.receipt.json"))[0]
    receipt.unlink()
    with pytest.raises(ValueError, match="exactly one validated receipt.*missing"):
        _summary(packet_root, campaign)

    packet_root, campaign = _fixture(tmp_path / "duplicate")
    retry = campaign / "round-1-retry-1"
    retry.mkdir()
    stem = sorted((campaign / "round-1").glob("*.receipt.json"))[0].name.removesuffix(
        ".receipt.json"
    )
    for suffix in ("receipt.json", "review.json", "raw.json"):
        shutil.copy2(
            campaign / "round-1" / f"{stem}.{suffix}",
            retry / f"{stem}.{suffix}",
        )
    with pytest.raises(ValueError, match="exactly one validated receipt.*duplicate"):
        _summary(packet_root, campaign)


def test_campaign_rejects_packet_review_and_raw_hash_drift(tmp_path: Path) -> None:
    packet_root, campaign = _fixture(tmp_path / "packet")
    packet_path = sorted(packet_root.glob("packet-*.json"))[0]
    packet_path.write_text(packet_path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="packet manifest hash mismatch"):
        _summary(packet_root, campaign)

    packet_root, campaign = _fixture(tmp_path / "review")
    review_path = sorted((campaign / "round-1").glob("*.review.json"))[0]
    review_path.write_text(review_path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="receipt review_hash mismatch"):
        _summary(packet_root, campaign)

    packet_root, campaign = _fixture(tmp_path / "raw")
    raw_path = sorted((campaign / "round-1").glob("*.raw.json"))[0]
    wrapper = json.loads(raw_path.read_text())
    raw_path.write_text(
        json.dumps(wrapper, ensure_ascii=False, indent=4, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="receipt raw_response_hash mismatch"):
        _summary(packet_root, campaign)


def test_campaign_rejects_raw_wrapper_disagreement(tmp_path: Path) -> None:
    packet_root, campaign = _fixture(tmp_path)
    raw_path = sorted((campaign / "round-1").glob("*.raw.json"))[0]
    receipt_path = raw_path.with_name(raw_path.name.replace(".raw.json", ".receipt.json"))
    wrapper = json.loads(raw_path.read_text())
    wrapper["text"] = "{}"
    raw_response = json.dumps(wrapper, ensure_ascii=False, indent=2, sort_keys=True)
    raw_path.write_text(raw_response + "\n", encoding="utf-8")
    receipt = json.loads(receipt_path.read_text())
    receipt["raw_response_hash"] = _sha256_text(raw_response)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="structuredOutput and text do not match"):
        _summary(packet_root, campaign)


def test_campaign_rejects_globally_reused_request_or_session_id(tmp_path: Path) -> None:
    packet_root, campaign = _fixture(tmp_path)
    round_path = campaign / "round-1"
    raw_paths = sorted(round_path.glob("*.raw.json"))
    first = json.loads(raw_paths[0].read_text())
    second = json.loads(raw_paths[1].read_text())
    second["requestId"] = first["requestId"]
    raw_response = json.dumps(second, ensure_ascii=False, indent=2, sort_keys=True)
    raw_paths[1].write_text(raw_response + "\n", encoding="utf-8")
    receipt_path = raw_paths[1].with_name(
        raw_paths[1].name.replace(".raw.json", ".receipt.json")
    )
    receipt = json.loads(receipt_path.read_text())
    receipt["request_id"] = first["requestId"]
    receipt["raw_response_hash"] = _sha256_text(raw_response)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="request_id values must be globally unique"):
        _summary(packet_root, campaign)


def test_raw_only_failed_attempt_contributes_ids_usage_and_structured_count(
    tmp_path: Path,
) -> None:
    packet_root, campaign = _fixture(tmp_path)
    source = sorted((campaign / "round-1").glob("*.raw.json"))[0]
    retry = campaign / "round-1-retry-1"
    retry.mkdir()
    wrapper = json.loads(source.read_text())
    wrapper["requestId"] = "raw-only-request"
    wrapper["sessionId"] = "raw-only-session"
    target = retry / source.name
    target.write_text(
        json.dumps(wrapper, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = _summary(packet_root, campaign)

    raw_only = [
        item
        for item in summary.failed_attempts
        if item.failure_kind == "incomplete_artifact"
    ]
    assert len(raw_only) == 1
    assert raw_only[0].structured_response is True
    assert raw_only[0].request_id == "raw-only-request"
    assert raw_only[0].session_id == "raw-only-session"
    assert raw_only[0].usage is not None
    assert summary.structured_attempt_count == 3
    assert summary.failed_structured_usage == raw_only[0].usage
    assert len(raw_only[0].artifacts) == 1
    assert raw_only[0].artifacts[0].content_hash == _sha256_file(target)


def test_raw_only_retry_reusing_selected_ids_is_rejected(tmp_path: Path) -> None:
    packet_root, campaign = _fixture(tmp_path)
    source = sorted((campaign / "round-1").glob("*.raw.json"))[0]
    retry = campaign / "round-1-retry-1"
    retry.mkdir()
    shutil.copy2(source, retry / source.name)

    with pytest.raises(ValueError, match="request_id values must be globally unique"):
        _summary(packet_root, campaign)


def test_wrapper_error_attempt_still_participates_in_global_id_checks(
    tmp_path: Path,
) -> None:
    packet_root, campaign = _fixture(tmp_path)
    source_raw = sorted((campaign / "round-1").glob("*.raw.json"))[0]
    source_review = source_raw.with_name(
        source_raw.name.replace(".raw.json", ".review.json")
    )
    retry = campaign / "round-1-retry-1"
    retry.mkdir()
    wrapper = json.loads(source_raw.read_text())
    wrapper["text"] = "{}"
    retry.joinpath(source_raw.name).write_text(
        json.dumps(wrapper, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shutil.copy2(source_review, retry / source_review.name)

    with pytest.raises(ValueError, match="request_id values must be globally unique"):
        _summary(packet_root, campaign)


def test_selected_receipt_and_wrapper_require_end_turn(tmp_path: Path) -> None:
    packet_root, campaign = _fixture(tmp_path / "receipt")
    receipt_path = sorted((campaign / "round-1").glob("*.receipt.json"))[0]
    receipt = json.loads(receipt_path.read_text())
    receipt["stop_reason"] = "MaxTurns"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid campaign receipt"):
        _summary(packet_root, campaign)

    packet_root, campaign = _fixture(tmp_path / "wrapper")
    raw_path = sorted((campaign / "round-1").glob("*.raw.json"))[0]
    receipt_path = raw_path.with_name(raw_path.name.replace(".raw.json", ".receipt.json"))
    wrapper = json.loads(raw_path.read_text())
    wrapper["stopReason"] = "MaxTurns"
    raw_response = json.dumps(wrapper, ensure_ascii=False, indent=2, sort_keys=True)
    raw_path.write_text(raw_response + "\n", encoding="utf-8")
    receipt = json.loads(receipt_path.read_text())
    receipt["raw_response_hash"] = _sha256_text(raw_response)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="stopReason must be EndTurn"):
        _summary(packet_root, campaign)


def test_raw_exact_byte_hash_detects_outer_whitespace_drift(tmp_path: Path) -> None:
    packet_root, campaign = _fixture(tmp_path)
    before = _summary(packet_root, campaign)
    raw_path = sorted((campaign / "round-1").glob("*.raw.json"))[0]
    logical_response = raw_path.read_text().strip()
    raw_path.write_text(f"\n{logical_response}\n\n", encoding="utf-8")

    after = _summary(packet_root, campaign)

    packet_id = "knowledge-review-packet:sha256:" + raw_path.name.removeprefix(
        "review-"
    ).removesuffix(".raw.json")
    before_selected = {item.packet_id: item for item in before.selected_receipts}
    after_selected = {item.packet_id: item for item in after.selected_receipts}
    assert before_selected[packet_id].raw_file_hash != after_selected[packet_id].raw_file_hash
    assert before.summary_id != after.summary_id


def test_campaign_summary_recomputes_aggregates_in_model_validator(
    tmp_path: Path,
) -> None:
    packet_root, campaign = _fixture(tmp_path)
    summary = _summary(packet_root, campaign)
    forged = summary.model_dump()
    forged["packet_decisions"]["accept"] = 0
    forged["packet_decisions"]["reject"] = 2

    with pytest.raises(ValidationError, match="decision totals do not match"):
        KnowledgeGrokCampaignSummary.model_validate(forged)
