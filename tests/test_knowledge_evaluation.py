from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2

import pytest

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.annotation import KnowledgeAnnotationBuild
from arkts_code_reviewer.knowledge.annotation_config import (
    load_knowledge_annotation_config,
)
from arkts_code_reviewer.knowledge.evaluation import (
    EvaluationKnowledgeBuild,
    build_evaluation_knowledge,
    load_evaluation_annotations_file,
    load_evaluation_extraction_file,
    load_evaluation_knowledge,
)
from arkts_code_reviewer.knowledge.extraction import KnowledgeExtractionBuild
from arkts_code_reviewer.knowledge.review_campaign import (
    summarize_knowledge_grok_campaign,
)
from arkts_code_reviewer.knowledge.review_packets import (
    ExternalModelPolicy,
    KnowledgeModelExportPolicy,
    KnowledgeReviewPacketBuild,
    ModelExportSourceRule,
    build_knowledge_review_packets,
    load_knowledge_review_prompt,
)
from tests.test_knowledge_review_consensus_build import (
    _sha256_file,
    _write_response,
)
from tests.test_knowledge_review_packets import (
    REVISION,
    SOURCE_ID,
    _artifacts,
    _source_registry,
)

ROOT = Path(__file__).resolve().parents[1]
REAL_REVIEW_DATA = Path("/home/autken/Code/arkts-review-data")
REAL_EXTRACTION = (
    REAL_REVIEW_DATA / "normalized/knowledge-seed-v1/candidates.json"
)
REAL_ANNOTATIONS = (
    REAL_REVIEW_DATA / "normalized/knowledge-seed-v1/annotations.json"
)
REAL_PACKET_ROOT = (
    REAL_REVIEW_DATA
    / "reports/knowledge-review/knowledge-seed-v1-grok-4.5-auditor-v4"
)
REAL_CAMPAIGN_BASE = (
    REAL_REVIEW_DATA
    / "reports/knowledge-review-responses/knowledge-seed-v1/grok-4.5/auditor-v4"
)


def _external_artifacts() -> tuple[
    KnowledgeExtractionBuild,
    KnowledgeAnnotationBuild,
    KnowledgeReviewPacketBuild,
]:
    normalized, extraction, annotations = _artifacts(3)
    features = load_default_feature_config()
    annotation_config = load_knowledge_annotation_config(feature_config=features)
    relative_path = extraction.documents[0].clauses[0].candidate.source_ref.relative_path
    policy = KnowledgeModelExportPolicy(
        schema_version="knowledge-model-export-policy-v1",
        version="evaluation-test-v1",
        max_clauses_per_packet=1,
        max_source_ids_per_packet=1,
        context_lines_before=2,
        context_lines_after=2,
        max_excerpt_lines=120,
        max_packet_excerpt_characters=100_000,
        external_model=ExternalModelPolicy(
            enabled=True,
            provider="xai",
            allowed_models=("grok-4.5",),
            allowed_prompt_versions=("grok-knowledge-auditor-v4",),
            source_allowlist=(
                ModelExportSourceRule(
                    source_id=SOURCE_ID,
                    revision=REVISION,
                    relative_paths=(relative_path,),
                ),
            ),
        ),
    )
    packets = build_knowledge_review_packets(
        normalized,
        extraction,
        annotations,
        registry=_source_registry(raw_prompt_use_allowed=True),
        feature_config=features,
        annotation_config=annotation_config,
        policy=policy,
        prompt=load_knowledge_review_prompt(),
        distribution="external_model",
        model_provider="xai",
        model_name="grok-4.5",
    )
    assert len(packets.packets) == 3
    return extraction, annotations, packets


def _write_packet_bundle(
    packet_root: Path,
    build: KnowledgeReviewPacketBuild,
) -> dict[str, str]:
    packet_root.mkdir(parents=True)
    prompt = load_knowledge_review_prompt()
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
    for ordinal, packet in enumerate(build.packets, start=1):
        digest = packet.packet_id.rsplit(":", 1)[-1][:12]
        path = packet_root / f"packet-{ordinal:03d}-{digest}.json"
        raw = packet.model_dump_json(indent=2) + "\n"
        path.write_text(raw, encoding="utf-8")
        files.append(path)
        packet_raws[packet.packet_id] = raw
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
    return packet_raws


def _campaign_fixture(root: Path) -> tuple[
    KnowledgeExtractionBuild,
    KnowledgeAnnotationBuild,
    Path,
    Path,
]:
    extraction, annotations, packets = _external_artifacts()
    packet_root = root / "packets"
    campaign_base = root / "campaign"
    round_one = campaign_base / "round-1"
    round_two = campaign_base / "round-2"
    round_one.mkdir(parents=True)
    round_two.mkdir(parents=True)
    packet_raws = _write_packet_bundle(packet_root, packets)
    for ordinal, packet in enumerate(packets.packets, start=1):
        decision = "reject" if ordinal == 2 else "accept"
        _write_response(
            round_one,
            packet,
            packet_raws[packet.packet_id],
            identity=f"evaluation-round-1-{ordinal}",
            decision=decision,
        )
        if ordinal != 3:
            _write_response(
                round_two,
                packet,
                packet_raws[packet.packet_id],
                identity=f"evaluation-round-2-{ordinal}",
                decision=decision,
            )
    return extraction, annotations, packet_root, campaign_base


def _build_fixture(tmp_path: Path) -> EvaluationKnowledgeBuild:
    extraction, annotations, packet_root, campaign = _campaign_fixture(tmp_path)
    return build_evaluation_knowledge(
        extraction=extraction,
        annotations=annotations,
        packet_root=packet_root,
        campaign_base=campaign,
        first_round_prefix="round-1",
        second_round_prefix="round-2",
        evaluated_at=datetime(2026, 7, 13, tzinfo=UTC),
    )


def test_evaluation_build_selects_only_dual_accept_and_records_missing(
    tmp_path: Path,
) -> None:
    build = _build_fixture(tmp_path)

    assert build.production_eligible is False
    assert len(build.packet_inventory) == 3
    assert len(build.packet_consensus) == 2
    assert len(build.missing_round_packet_ids) == 1
    assert len(build.clauses) == 1
    assert len(build.exclusions) == 2
    assert sorted(item.reasons for item in build.exclusions) == [
        ("consensus_rejected",),
        ("missing_round_receipt",),
    ]
    assert build.clauses[0].clause.status == "Draft"
    assert build.clauses[0].clause.rule_id == build.clauses[0].source_clause.rule_id
    assert build.build_id == build.expected_build_id()
    assert load_evaluation_knowledge(build.model_dump_json()) == build

    with pytest.raises(ValueError, match="exactly one validated receipt.*missing"):
        summarize_knowledge_grok_campaign(
            packet_root=tmp_path / "packets",
            campaign_base=tmp_path / "campaign",
            round_prefix="round-2",
        )


def test_evaluation_build_is_deterministic_and_strict_loader_fails_closed(
    tmp_path: Path,
) -> None:
    first = _build_fixture(tmp_path / "first")
    second = _build_fixture(tmp_path / "second")

    assert first == second
    raw = first.model_dump_json()
    with pytest.raises(ValueError, match="duplicate JSON key: production_eligible"):
        load_evaluation_knowledge(raw[:-1] + ',"production_eligible":false}')
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_evaluation_knowledge(raw[:-1] + ',"unknown":true}')
    with pytest.raises(ValueError, match="must use UTF-8"):
        load_evaluation_knowledge(b"\xff")

    payload = json.loads(raw)
    payload["build_id"] = "evaluation-knowledge:sha256:" + "f" * 64
    with pytest.raises(ValueError, match="build_id does not match content"):
        load_evaluation_knowledge(json.dumps(payload))


def test_evaluation_source_loaders_and_partial_campaign_fail_closed(
    tmp_path: Path,
) -> None:
    extraction, annotations, packet_root, campaign = _campaign_fixture(tmp_path)
    extraction_path = tmp_path / "candidates.json"
    annotation_path = tmp_path / "annotations.json"
    extraction_path.write_text(extraction.model_dump_json(), encoding="utf-8")
    annotation_path.write_text(annotations.model_dump_json(), encoding="utf-8")

    assert load_evaluation_extraction_file(extraction_path) == extraction
    assert load_evaluation_annotations_file(annotation_path) == annotations

    extraction_raw = extraction.model_dump_json()
    extraction_path.write_text(
        extraction_raw[:-1] + f',"build_id":"{extraction.build_id}"}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON key: build_id"):
        load_evaluation_extraction_file(extraction_path)

    annotation_raw = annotations.model_dump_json()
    annotation_path.write_text(
        annotation_raw[:-1] + ',"unknown":true}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_evaluation_annotations_file(annotation_path)

    retry = campaign / "round-2-retry-1"
    retry.mkdir()
    receipt = sorted((campaign / "round-2").glob("*.receipt.json"))[0]
    copy2(receipt, retry / receipt.name)
    with pytest.raises(ValueError, match="at most one.*duplicate"):
        build_evaluation_knowledge(
            extraction=extraction,
            annotations=annotations,
            packet_root=packet_root,
            campaign_base=campaign,
            first_round_prefix="round-1",
            second_round_prefix="round-2",
            evaluated_at=datetime(2026, 7, 13, tzinfo=UTC),
        )


@pytest.mark.skipif(
    not all(
        path.is_file()
        for path in (REAL_EXTRACTION, REAL_ANNOTATIONS, REAL_PACKET_ROOT / "manifest.json")
    )
    or not REAL_CAMPAIGN_BASE.is_dir(),
    reason="local audited knowledge-seed-v1 artifacts are unavailable",
)
def test_real_evaluation_cli_builds_frozen_stage_one_counts(tmp_path: Path) -> None:
    output = tmp_path / "evaluation.json"
    command = [
        sys.executable,
        str(ROOT / "tools/build_evaluation_knowledge.py"),
        "--candidates",
        str(REAL_EXTRACTION),
        "--annotations",
        str(REAL_ANNOTATIONS),
        "--packet-root",
        str(REAL_PACKET_ROOT),
        "--campaign-base",
        str(REAL_CAMPAIGN_BASE),
        "--first-round-prefix",
        "round-1",
        "--second-round-prefix",
        "round-2",
        "--evaluated-at",
        "2026-07-13T00:00:00Z",
        "--output",
        str(output),
        "--expect-source-packets",
        "21",
        "--expect-paired-packets",
        "20",
        "--expect-missing-packets",
        "1",
        "--expect-source-clauses",
        "314",
        "--expect-selected-clauses",
        "109",
        "--expect-excluded-clauses",
        "205",
        "--expect-api-symbols",
        "644",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert (
        report["source_packets"],
        report["paired_packets"],
        report["missing_packets"],
        report["selected_clauses"],
        report["excluded_clauses"],
        report["api_symbols"],
    ) == (21, 20, 1, 109, 205, 644)
    built = load_evaluation_knowledge(output.read_bytes())
    assert len(built.clauses) == 109
    assert len(built.exclusions) == 205
    assert len(built.api_symbols) == 644
    assert Counter(
        reason for exclusion in built.exclusions for reason in exclusion.reasons
    ) == {
        "consensus_unresolved": 75,
        "consensus_correction_draft": 52,
        "consensus_rejected": 50,
        "missing_round_receipt": 25,
        "duplicate_proposal_subject": 8,
        "conflict_proposal_subject": 2,
    }
    assert sum(
        exclusion.reasons in {
            ("duplicate_proposal_subject",),
            ("conflict_proposal_subject",),
        }
        for exclusion in built.exclusions
    ) == 3

    repeated = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
    )
    assert repeated.returncode == 1
    assert "must not already exist" in repeated.stderr
