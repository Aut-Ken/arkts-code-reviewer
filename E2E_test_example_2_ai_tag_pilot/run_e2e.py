#!/usr/bin/env python3
"""Rebuild and publish the offline VideoPlayer Static/AI Tag Pilot report.

The provider observations are fixed inputs prepared from the approved 2026-07-21
Campaign.  This command performs no provider request, credential lookup, Retrieval,
or knowledge access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from E2E_test_example_1.video_player_tag_pilot import (  # noqa: E402
    build_video_player_tag_pilot,
    render_video_player_tag_pilot_inspection,
    validate_grok_blind_output,
)

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "inputs"
DEFAULT_OUTPUT_DIR = ROOT / "artifacts"
DEFAULT_REPORT_PATH = ROOT / "REPORT.md"
SOURCE_FIXTURE_INPUTS = REPOSITORY_ROOT / "E2E_test_example_1" / "inputs"

PILOT_ID = "sha256:28965ae3c4ec76e50aa44d26712bb7d1acbd673f4514f6d66497e98db504f067"
CAMPAIGN_ID = (
    "ai-tag-shadow-campaign:sha256:85e98a9fb77d6c7b24a038616bbfe6122c13edb9bf8c7d6c8a0d328076a11b45"
)
DEEPSEEK_REQUEST_SET_DIGEST = (
    "sha256:954f53f015305769028e2fdd39d696364f1dbbafc1da614484074975741da8e6"
)
GROK_REQUEST_SET_DIGEST = "sha256:5fa7f113f815bf46a82e32e4f50e577cddad92727c0ce818f0e2c4ee33f49b35"
GROK_SOURCE_RUN = "grok-rerun-1"
SOURCE_NAMES = (
    "base.ets",
    "head.ets",
    "diff.patch",
    "expected_review_units.json",
    "mutation_spec.json",
    "provenance.json",
)
OUTPUT_NAMES = (
    "00_run_manifest.json",
    "01_change_set.json",
    "02_parser_base.json",
    "03_parser_head.json",
    "04_review_unit_build.json",
    "05_unit_fact_scopes.json",
    "06_static_feature_routing.json",
    "07_context_plan.json",
    "08_campaign_inspection.json",
    "09_deepseek_results.json",
    "10_grok_results.json",
    "11_static_deepseek_comparison.json",
    "12_live_provenance.json",
    "13_assertions.json",
    "14_summary.json",
)


class DuplicateJsonKeyError(ValueError):
    """Raised when a fixed Pilot input is ambiguous JSON."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required Pilot input is missing or unsafe: {path}")
    return path.read_bytes()


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            _read_bytes(path),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must contain one object: {path}")
    return value


def _json_text(value: object) -> str:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _strings(value: object, context: str) -> list[str]:
    items = _list(value, context)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{context} must contain only strings")
    return items


def _validate_source_fixture() -> dict[str, object]:
    files: list[dict[str, object]] = []
    for name in SOURCE_NAMES:
        imported = _read_bytes(INPUTS / name)
        canonical = _read_bytes(SOURCE_FIXTURE_INPUTS / name)
        if imported != canonical:
            raise ValueError(f"Pilot source fixture copy drifted: inputs/{name}")
        files.append(
            {
                "path": f"inputs/{name}",
                "sha256": _sha256_bytes(imported),
                "size_bytes": len(imported),
            }
        )
    return {
        "schema_version": "video-player-tag-source-fixture-v1",
        "source_example": "E2E_test_example_1",
        "files": files,
    }


def _validate_observations(
    pilot: object,
    deepseek: dict[str, object],
    grok: dict[str, object],
    provenance: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if (
        deepseek.get("schema_version") != "deepseek-tag-live-observation-set-v1"
        or deepseek.get("pilot_id") != PILOT_ID
        or deepseek.get("campaign_id") != CAMPAIGN_ID
        or deepseek.get("request_set_digest") != DEEPSEEK_REQUEST_SET_DIGEST
    ):
        raise ValueError("DeepSeek observation fixture identity does not match")
    if (
        grok.get("schema_version") != "grok-tag-live-observation-set-v1"
        or grok.get("pilot_id") != PILOT_ID
        or grok.get("request_set_digest") != GROK_REQUEST_SET_DIGEST
        or grok.get("source_run") != GROK_SOURCE_RUN
        or grok.get("validated_judgment_available") is not True
    ):
        raise ValueError("Grok observation fixture identity does not match")
    if (
        provenance.get("schema_version") != "video-player-tag-live-provenance-v2"
        or provenance.get("pilot_id") != PILOT_ID
        or provenance.get("campaign_id") != CAMPAIGN_ID
        or provenance.get("deepseek_request_set_digest") != DEEPSEEK_REQUEST_SET_DIGEST
        or provenance.get("grok_request_set_digest") != GROK_REQUEST_SET_DIGEST
        or provenance.get("grok_source_run") != GROK_SOURCE_RUN
        or provenance.get("contains_api_key") is not False
        or provenance.get("contains_prompt_or_source_body") is not False
        or provenance.get("contains_raw_provider_response_body") is not False
    ):
        raise ValueError("live provenance fixture identity or safety boundary differs")

    deepseek_rows_raw = _list(deepseek.get("observations"), "DeepSeek observations")
    grok_rows_raw = _list(grok.get("observations"), "Grok observations")
    if len(deepseek_rows_raw) != 15 or len(grok_rows_raw) != 15:
        raise ValueError("provider fixtures must each cover exactly 15 Units")
    deepseek_rows = [dict(_mapping(row, "DeepSeek observation")) for row in deepseek_rows_raw]
    grok_rows = [dict(_mapping(row, "Grok observation")) for row in grok_rows_raw]

    expected_units = list(pilot.campaign.units)
    expected_tag_ids = [
        contract.tag_id for contract in expected_units[0].request.tag_contract_views
    ]
    planned_grok_by_unit = {
        row["unit_id"]: row["grok"]
        for row in _list(pilot.inspection["units"], "Pilot inspection Units")
    }
    for index, (unit, deepseek_row, grok_row) in enumerate(
        zip(expected_units, deepseek_rows, grok_rows, strict=True)
    ):
        judgments = _list(deepseek_row.get("judgments"), "DeepSeek judgments")
        judgment_tag_ids = [_mapping(item, "DeepSeek judgment").get("tag_id") for item in judgments]
        positive_tags = [
            item["tag_id"]
            for item in judgments
            if _mapping(item, "DeepSeek judgment").get("decision") == "positive"
        ]
        strict_judgments = validate_grok_blind_output(
            unit,
            json.dumps(
                {"judgments": judgments},
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        strict_judgment_payloads = [item.model_dump(mode="json") for item in strict_judgments]
        if (
            deepseek_row.get("unit_id") != unit.card.unit_id
            or deepseek_row.get("plan_id") != unit.plan.plan_id
            or deepseek_row.get("request_id") != unit.request.request_id
            or deepseek_row.get("envelope_id") != unit.envelope.envelope_id
            or deepseek_row.get("status") != "valid_shape"
            or deepseek_row.get("model") != "deepseek-v4-pro"
            or deepseek_row.get("attempt_count") != 1
            or deepseek_row.get("attempt_cap") != 1
            or deepseek_row.get("retry_count") != 0
            or judgment_tag_ids != expected_tag_ids
            or judgments != strict_judgment_payloads
            or deepseek_row.get("positive_tags") != positive_tags
        ):
            raise ValueError(f"DeepSeek observation {index + 1} failed replay binding")
        grok_judgments = _list(grok_row.get("judgments"), "Grok judgments")
        grok_judgment_tag_ids = [
            _mapping(item, "Grok judgment").get("tag_id") for item in grok_judgments
        ]
        grok_positive_tags = [
            item["tag_id"]
            for item in grok_judgments
            if _mapping(item, "Grok judgment").get("decision") == "positive"
        ]
        strict_grok_judgments = validate_grok_blind_output(
            unit,
            json.dumps(
                {"judgments": grok_judgments},
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        strict_grok_payloads = [
            item.model_dump(mode="json") for item in strict_grok_judgments
        ]
        planned_grok = _mapping(
            planned_grok_by_unit.get(unit.card.unit_id),
            "planned Grok request",
        )
        prompt_identity = _mapping(
            grok_row.get("prompt_identity"),
            "Grok prompt identity",
        )
        if (
            grok_row.get("unit_id") != unit.card.unit_id
            or grok_row.get("model_view_id") != unit.model_view.model_view_id
            or grok_row.get("request_id") != unit.request.request_id
            or grok_row.get("status") != "valid_shape"
            or grok_row.get("model") != "grok-4.5"
            or grok_row.get("validated_judgment_available") is not True
            or grok_row.get("attempt_count") != 1
            or grok_row.get("attempt_cap") != 1
            or grok_row.get("retry_count") != 0
            or grok_judgment_tag_ids != expected_tag_ids
            or grok_judgments != strict_grok_payloads
            or grok_row.get("positive_tags") != grok_positive_tags
            or prompt_identity.get("system_prompt_sha256")
            != planned_grok.get("system_prompt_sha256")
            or prompt_identity.get("user_prompt_sha256")
            != planned_grok.get("user_prompt_sha256")
        ):
            raise ValueError(f"Grok observation {index + 1} failed replay binding")

    provenance_records = _list(
        provenance.get("source_artifacts"),
        "live provenance source_artifacts",
    )
    if provenance.get("source_artifact_count") != 64 or len(provenance_records) != 64:
        raise ValueError("live provenance must contain exactly 64 source artifacts")
    provenance_by_path: dict[str, Mapping[str, object]] = {}
    for value in provenance_records:
        record = _mapping(value, "live provenance artifact")
        path = record.get("path")
        if not isinstance(path, str) or path in provenance_by_path:
            raise ValueError("live provenance artifact paths must be unique strings")
        provenance_by_path[path] = record

    referenced_paths: set[str] = set()
    provider_prefixes = {"deepseek": "deepseek", "grok": GROK_SOURCE_RUN}
    for provider, rows in (("deepseek", deepseek_rows), ("grok", grok_rows)):
        for row in rows:
            source_artifacts = _mapping(
                row.get("source_artifacts"),
                f"{provider} source_artifacts",
            )
            if set(source_artifacts) != {"attempt_marker", "result"}:
                raise ValueError(f"{provider} observation source artifacts are incomplete")
            for artifact in source_artifacts.values():
                projection = _mapping(artifact, f"{provider} source artifact")
                path = projection.get("path")
                if not isinstance(path, str):
                    raise ValueError(f"{provider} source artifact path is invalid")
                provenance_path = f"{provider_prefixes[provider]}/{path}"
                provenance_record = provenance_by_path.get(provenance_path)
                if provenance_record is None or (
                    provenance_record.get("provider") != provider
                    or provenance_record.get("sha256") != projection.get("sha256")
                    or provenance_record.get("size_bytes") != projection.get("size_bytes")
                ):
                    raise ValueError(f"{provider} source artifact does not match live provenance")
                referenced_paths.add(provenance_path)

    summary_and_inspection = {
        "deepseek/inspection.json": pilot.inspection,
        "deepseek/summary.json": deepseek["summary"],
        f"{GROK_SOURCE_RUN}/inspection.json": pilot.inspection,
        f"{GROK_SOURCE_RUN}/summary.json": grok["summary"],
    }
    for path, payload in summary_and_inspection.items():
        record = provenance_by_path.get(path)
        raw = _json_text(payload).encode("utf-8")
        if record is None or (
            record.get("sha256") != _sha256_bytes(raw) or record.get("size_bytes") != len(raw)
        ):
            raise ValueError(f"{path} does not match live provenance")
        referenced_paths.add(path)
    if referenced_paths != set(provenance_by_path):
        raise ValueError("live provenance contains unreferenced source artifacts")
    return deepseek_rows, grok_rows


def _build_comparison(
    pilot: object,
    deepseek_rows: list[dict[str, object]],
    grok_rows: list[dict[str, object]],
) -> dict[str, object]:
    static_counts: Counter[str] = Counter()
    deepseek_counts: Counter[str] = Counter()
    grok_counts: Counter[str] = Counter()
    overlap_counts: Counter[str] = Counter()
    deepseek_only_counts: Counter[str] = Counter()
    static_only_counts: Counter[str] = Counter()
    deepseek_grok_overlap_counts: Counter[str] = Counter()
    deepseek_only_vs_grok_counts: Counter[str] = Counter()
    grok_only_vs_deepseek_counts: Counter[str] = Counter()
    both_not_positive = 0
    exact_positive_set_units = 0
    units: list[dict[str, object]] = []
    for unit, deepseek, grok in zip(
        pilot.campaign.units,
        deepseek_rows,
        grok_rows,
        strict=True,
    ):
        static_exact = list(unit.card.static_tags.exact)
        static_routing = list(unit.card.static_tags.routing)
        deepseek_positive = _strings(
            deepseek.get("positive_tags"),
            "DeepSeek positive_tags",
        )
        grok_positive = _strings(grok.get("positive_tags"), "Grok positive_tags")
        static_set = set(static_exact)
        deepseek_set = set(deepseek_positive)
        grok_set = set(grok_positive)
        overlap = sorted(static_set & deepseek_set)
        deepseek_only = sorted(deepseek_set - static_set)
        static_only = sorted(static_set - deepseek_set)
        deepseek_grok_overlap = sorted(deepseek_set & grok_set)
        deepseek_only_vs_grok = sorted(deepseek_set - grok_set)
        grok_only_vs_deepseek = sorted(grok_set - deepseek_set)
        static_counts.update(static_exact)
        deepseek_counts.update(deepseek_positive)
        grok_counts.update(grok_positive)
        overlap_counts.update(overlap)
        deepseek_only_counts.update(deepseek_only)
        static_only_counts.update(static_only)
        deepseek_grok_overlap_counts.update(deepseek_grok_overlap)
        deepseek_only_vs_grok_counts.update(deepseek_only_vs_grok)
        grok_only_vs_deepseek_counts.update(grok_only_vs_deepseek)
        exact_positive_set_units += deepseek_set == grok_set
        deepseek_decisions = {
            item["tag_id"]: item["decision"]
            for value in _list(deepseek["judgments"], "DeepSeek judgments")
            for item in [_mapping(value, "DeepSeek judgment")]
        }
        grok_decisions = {
            item["tag_id"]: item["decision"]
            for value in _list(grok["judgments"], "Grok judgments")
            for item in [_mapping(value, "Grok judgment")]
        }
        if set(deepseek_decisions) != set(grok_decisions):
            raise ValueError("DeepSeek/Grok judgment taxonomies differ")
        both_not_positive += sum(
            deepseek_decisions[tag] != "positive" and grok_decisions[tag] != "positive"
            for tag in deepseek_decisions
        )
        units.append(
            {
                "unit_id": unit.card.unit_id,
                "unit_symbol": unit.card.unit_symbol,
                "unit_kind": unit.card.unit_kind,
                "source_role": unit.card.source_role,
                "line_start": unit.card.code.line_start,
                "line_end": unit.card.code.line_end,
                "static_exact_tags": static_exact,
                "static_routing_tags": static_routing,
                "static_routing_scope": "whole_file_hint_not_unit_positive",
                "deepseek_positive_tags": deepseek_positive,
                "overlap_tags": overlap,
                "deepseek_only_candidate_tags": deepseek_only,
                "static_only_tags": static_only,
                "grok_status": grok["status"],
                "grok_validated_judgment_available": True,
                "grok_positive_tags": grok_positive,
                "deepseek_grok_overlap_tags": deepseek_grok_overlap,
                "deepseek_only_vs_grok_tags": deepseek_only_vs_grok,
                "grok_only_vs_deepseek_tags": grok_only_vs_deepseek,
                "deepseek_grok_exact_positive_set": deepseek_set == grok_set,
            }
        )

    deepseek_positive_count = sum(deepseek_counts.values())
    grok_positive_count = sum(grok_counts.values())
    both_positive = sum(deepseek_grok_overlap_counts.values())
    deepseek_only_vs_grok = sum(deepseek_only_vs_grok_counts.values())
    grok_only_vs_deepseek = sum(grok_only_vs_deepseek_counts.values())
    decision_total = len(units) * 24
    raw_agreement = (both_positive + both_not_positive) / decision_total
    deepseek_positive_rate = deepseek_positive_count / decision_total
    grok_positive_rate = grok_positive_count / decision_total
    expected_agreement = (
        deepseek_positive_rate * grok_positive_rate
        + (1 - deepseek_positive_rate) * (1 - grok_positive_rate)
    )
    cohen_kappa = (raw_agreement - expected_agreement) / (1 - expected_agreement)

    return {
        "schema_version": "video-player-static-deepseek-grok-tag-comparison-v1",
        "pilot_id": PILOT_ID,
        "campaign_id": CAMPAIGN_ID,
        "comparison_status": "complete_proxy_comparison_without_human_truth",
        "unit_count": len(units),
        "static_exact": {
            "assignment_count": sum(static_counts.values()),
            "distinct_tag_count": len(static_counts),
            "tag_counts": dict(sorted(static_counts.items())),
            "empty_unit_count": sum(not unit["static_exact_tags"] for unit in units),
        },
        "deepseek": {
            "candidate_positive_assignment_count": deepseek_positive_count,
            "distinct_candidate_tag_count": len(deepseek_counts),
            "tag_counts": dict(sorted(deepseek_counts.items())),
            "empty_unit_count": sum(not unit["deepseek_positive_tags"] for unit in units),
        },
        "agreement": {
            "overlap_assignment_count": sum(overlap_counts.values()),
            "overlap_tag_counts": dict(sorted(overlap_counts.items())),
            "deepseek_only_candidate_assignment_count": sum(deepseek_only_counts.values()),
            "deepseek_only_candidate_tag_counts": dict(sorted(deepseek_only_counts.items())),
            "static_only_assignment_count": sum(static_only_counts.values()),
            "static_only_tag_counts": dict(sorted(static_only_counts.items())),
        },
        "grok": {
            "attempted_count": 15,
            "valid_judgment_count": 15,
            "provider_error_count": 0,
            "positive_assignment_count": grok_positive_count,
            "distinct_positive_tag_count": len(grok_counts),
            "tag_counts": dict(sorted(grok_counts.items())),
            "empty_unit_count": sum(not unit["grok_positive_tags"] for unit in units),
        },
        "deepseek_grok_agreement": {
            "exact_positive_set_unit_count": exact_positive_set_units,
            "both_positive_assignment_count": both_positive,
            "deepseek_only_assignment_count": deepseek_only_vs_grok,
            "grok_only_assignment_count": grok_only_vs_deepseek,
            "both_not_positive_assignment_count": both_not_positive,
            "positive_jaccard": both_positive
            / (both_positive + deepseek_only_vs_grok + grok_only_vs_deepseek),
            "raw_binary_decision_agreement": raw_agreement,
            "cohen_kappa_binary_positive": cohen_kappa,
            "both_positive_tag_counts": dict(sorted(deepseek_grok_overlap_counts.items())),
            "deepseek_only_tag_counts": dict(sorted(deepseek_only_vs_grok_counts.items())),
            "grok_only_tag_counts": dict(sorted(grok_only_vs_deepseek_counts.items())),
        },
        "units": units,
        "quality_boundary": ("candidate_comparison_not_human_truth_not_tag_precision_recall"),
    }


def _assertion(
    assertions: list[dict[str, object]],
    assertion_id: str,
    description: str,
    passed: bool,
    evidence: object,
) -> None:
    if not passed:
        raise AssertionError(f"{assertion_id} failed: {description}")
    assertions.append(
        {
            "id": assertion_id,
            "description": description,
            "passed": True,
            "evidence": evidence,
        }
    )


def _build_assertions(
    pilot: object,
    comparison: Mapping[str, object],
    deepseek_rows: list[dict[str, object]],
    grok_rows: list[dict[str, object]],
    source_fixture: Mapping[str, object],
) -> dict[str, object]:
    assertions: list[dict[str, object]] = []
    static = _mapping(comparison["static_exact"], "static comparison")
    deepseek = _mapping(comparison["deepseek"], "DeepSeek comparison")
    agreement = _mapping(comparison["agreement"], "agreement comparison")
    grok = _mapping(comparison["grok"], "Grok comparison")
    proxy_agreement = _mapping(
        comparison["deepseek_grok_agreement"],
        "DeepSeek/Grok agreement",
    )
    _assertion(
        assertions,
        "A01",
        "新目录中的源码 fixture 与首次 E2E 字节一致",
        len(_list(source_fixture["files"], "source files")) == len(SOURCE_NAMES),
        source_fixture,
    )
    _assertion(
        assertions,
        "A02",
        "离线重建得到批准的 Pilot 与 Campaign identity",
        pilot.inspection["pilot_id"] == PILOT_ID
        and pilot.campaign.manifest.campaign_id == CAMPAIGN_ID,
        {"pilot_id": PILOT_ID, "campaign_id": CAMPAIGN_ID},
    )
    _assertion(
        assertions,
        "A03",
        "ChangeSet 到 ContextPlan 重建出 15 个 ReviewUnit",
        len(pilot.upstream.analysis_result.review_units) == 15,
        {"review_unit_count": 15},
    )
    parser_quality = [
        result.analysis.parser_quality
        for result in pilot.upstream.analysis_result.file_parse_results
    ]
    _assertion(
        assertions,
        "A04",
        "base/head Parser 均为无 ERROR、missing、warning 的 L1",
        all(
            item.layer == "L1"
            and item.error_nodes == 0
            and item.missing_nodes == 0
            and not item.warnings
            for item in parser_quality
        ),
        [asdict(item) for item in parser_quality],
    )
    _assertion(
        assertions,
        "A05",
        "静态 Unit exact 与 whole-file routing hints 分开保存",
        static["assignment_count"] == 9
        and pilot.inspection["static_baseline"]["routing_tag_assignment_count"] == 165,
        {
            "unit_exact_assignments": static["assignment_count"],
            "file_hint_assignments": 165,
        },
    )
    _assertion(
        assertions,
        "A06",
        "DeepSeek 15 个结果均通过 24 Tag 结构校验",
        len(deepseek_rows) == 15
        and all(
            row["status"] == "valid_shape" and len(row["judgments"]) == 24 for row in deepseek_rows
        ),
        {"valid_unit_count": 15, "judgment_count": 360},
    )
    _assertion(
        assertions,
        "A07",
        "DeepSeek 每 Unit 仅尝试一次且没有重试",
        all(
            row["attempt_count"] == 1 and row["attempt_cap"] == 1 and row["retry_count"] == 0
            for row in deepseek_rows
        ),
        {"attempted_count": 15, "retry_count": 0},
    )
    _assertion(
        assertions,
        "A08",
        "静态 9 个 exact 均被 DeepSeek 覆盖，另有 40 个候选 positive",
        static["assignment_count"] == 9
        and deepseek["candidate_positive_assignment_count"] == 49
        and agreement["overlap_assignment_count"] == 9
        and agreement["deepseek_only_candidate_assignment_count"] == 40
        and agreement["static_only_assignment_count"] == 0,
        {
            "static_exact": 9,
            "deepseek_candidate_positive": 49,
            "overlap": 9,
            "deepseek_only_candidate": 40,
        },
    )
    _assertion(
        assertions,
        "A09",
        "Grok 15 个盲判结果均通过相同的 24 Tag 结构校验",
        len(grok_rows) == 15
        and all(
            row["status"] == "valid_shape"
            and row["validated_judgment_available"] is True
            and len(row["judgments"]) == 24
            for row in grok_rows
        ),
        {
            "attempted_count": 15,
            "valid_judgment_count": grok["valid_judgment_count"],
            "judgment_count": 360,
        },
    )
    _assertion(
        assertions,
        "A10",
        "DeepSeek 与 Grok 的 positive 分歧被完整保留且没有在线裁决",
        proxy_agreement["both_positive_assignment_count"] == 41
        and proxy_agreement["deepseek_only_assignment_count"] == 8
        and proxy_agreement["grok_only_assignment_count"] == 1
        and proxy_agreement["exact_positive_set_unit_count"] == 9,
        {
            "both_positive": 41,
            "deepseek_only": 8,
            "grok_only": 1,
            "exact_positive_set_units": 9,
        },
    )
    _assertion(
        assertions,
        "A11",
        "样本与结果明确不是真实 MR、人工 Truth 或生产 P/R",
        pilot.inspection["sample"]["is_real_mr"] is False
        and "not_production_precision_recall" in pilot.inspection["quality_boundary"],
        {
            "is_real_mr": False,
            "human_truth": False,
            "production_precision_recall": False,
        },
    )
    return {
        "schema_version": "video-player-tag-pilot-assertions-v2",
        "status": "pass",
        "passed": len(assertions),
        "failed": 0,
        "assertions": assertions,
    }


def _join_tags(value: object) -> str:
    tags = _strings(value, "tag list")
    return ", ".join(f"`{tag}`" for tag in tags) if tags else "—"


def _report(
    manifest: Mapping[str, object],
    comparison: Mapping[str, object],
    summary: Mapping[str, object],
    assertions: Mapping[str, object],
) -> str:
    static = _mapping(comparison["static_exact"], "static comparison")
    deepseek = _mapping(comparison["deepseek"], "DeepSeek comparison")
    grok = _mapping(comparison["grok"], "Grok comparison")
    proxy_agreement = _mapping(
        comparison["deepseek_grok_agreement"],
        "DeepSeek/Grok agreement",
    )
    units = _list(comparison["units"], "comparison Units")
    unit_rows = []
    for value in units:
        unit = _mapping(value, "comparison Unit")
        model_difference = []
        if unit["deepseek_only_vs_grok_tags"]:
            model_difference.append(
                f"DS-only: {_join_tags(unit['deepseek_only_vs_grok_tags'])}"
            )
        if unit["grok_only_vs_deepseek_tags"]:
            model_difference.append(
                f"Grok-only: {_join_tags(unit['grok_only_vs_deepseek_tags'])}"
            )
        unit_rows.append(
            "| {side} | `{symbol}` | {static} | {deepseek} | {grok} | {difference} |".format(
                side=unit["source_role"],
                symbol=unit["unit_symbol"],
                static=_join_tags(unit["static_exact_tags"]),
                deepseek=_join_tags(unit["deepseek_positive_tags"]),
                grok=_join_tags(unit["grok_positive_tags"]),
                difference="；".join(model_difference) if model_difference else "一致",
            )
        )

    static_counts = _mapping(static["tag_counts"], "static Tag counts")
    deepseek_counts = _mapping(deepseek["tag_counts"], "DeepSeek Tag counts")
    grok_counts = _mapping(grok["tag_counts"], "Grok Tag counts")
    tag_rows = []
    for tag in sorted(set(static_counts) | set(deepseek_counts) | set(grok_counts)):
        tag_rows.append(
            f"| `{tag}` | {static_counts.get(tag, 0)} | {deepseek_counts.get(tag, 0)} | "
            f"{grok_counts.get(tag, 0)} |"
        )

    artifact_rows = [
        f"| {name.removesuffix('.json')} | [artifacts/{name}](artifacts/{name}) |"
        for name in OUTPUT_NAMES
    ]
    return "\n".join(
        [
            "# VideoPlayer Static / DeepSeek / Grok Tag Pilot",
            "",
            "> 固定的真实应用 VideoPlayer base 与仓库人工构造的 head/Diff，离线重建 "
            "ChangeSet → Parser → ReviewUnit → UnitFactScope → Feature Routing → "
            "AI Tag Campaign，并整理 2026-07-21 已批准的真实 Provider 观察。",
            "",
            "## 1. 汇报结论",
            "",
            "- Pilot 执行状态：**PARTIAL**。",
            "- DeepSeek 结构执行：**PASS，15/15 valid_shape**。",
            "- Grok 盲判执行：**PASS，15/15 valid_shape**。",
            "- Tag 真实质量：**NOT QUALIFIED**。",
            "- 静态 `unit_exact`：9 次分配、3 种 Tag、8/15 Unit 为空。",
            "- DeepSeek：49 次候选 positive、10 种 Tag、3/15 Unit 为空。",
            "- Grok：42 次代理 positive、8 种 Tag、3/15 Unit 为空。",
            "- 静态的 9 次 exact 全部被 DeepSeek 覆盖；DeepSeek 另给出 40 次候选 "
            "positive。这里的“候选”不等于“正确补漏”。",
            "- DeepSeek 与 Grok 重合 41 次 positive；DeepSeek-only 8 次，Grok-only "
            "1 次；9/15 Unit 的 positive 集合完全一致。模型一致不等于判断正确。",
            "- 本样本不是真实 MR，没有人工 Tag Truth，不能计算真实 Precision/Recall。",
            "- 本次没有执行知识检索、文档质量评估、Final LLM 或 Finding。",
            "",
            "## 2. 输入与来源",
            "",
            "- 原仓库：`applications_app_samples`。",
            "- 固定 revision：`8255a2987f70317cc3a2a4d46044c6b55f092bb3`。",
            "- base 为固定真实应用源码；head 与 Diff 是仓库人工构造。",
            "- ReviewUnit：15 个；base/head Parser 均为干净 L1。",
            "- Provider 运行日期：2026-07-21。",
            f"- Pilot ID：`{PILOT_ID}`。",
            f"- DeepSeek Campaign：`{CAMPAIGN_ID}`。",
            "- 输入文件：[base.ets](inputs/base.ets) · [head.ets](inputs/head.ets) · "
            "[diff.patch](inputs/diff.patch) · "
            "[来源](inputs/provenance.json) · "
            "[变更说明](inputs/mutation_spec.json)。",
            "- 脱敏 Provider 观察：[DeepSeek](inputs/deepseek_observations.json) · "
            "[Grok](inputs/grok_observations.json) · "
            "[原始文件哈希清单](inputs/live_provenance.json)。",
            "",
            "## 3. 实际链路",
            "",
            "```text",
            "base/head + Diff",
            "  → ChangeSet",
            "  → Parser L1",
            "  → 15 ReviewUnits",
            "  → UnitFactScope (unit_exact / whole-file file_hints)",
            "  → Static Feature Routing",
            "  → 15 个完整 24-Tag DeepSeek 判断",
            "  → 15 个完整 24-Tag Grok blind proxy 判断",
            "  → Static / DeepSeek / Grok 对照",
            "```",
            "",
            "这条 Pilot 在 Tag 对照处停止。它没有进入 Retrieval，也没有生成评审意见。",
            "",
            "## 4. 静态、DeepSeek 与 Grok 总体对照",
            "",
            "| 指标 | Static unit_exact | DeepSeek candidate | Grok proxy |",
            "|---|---:|---:|---:|",
            f"| Tag 分配数 | {static['assignment_count']} | "
            f"{deepseek['candidate_positive_assignment_count']} | "
            f"{grok['positive_assignment_count']} |",
            f"| 不同 Tag 数 | {static['distinct_tag_count']} | "
            f"{deepseek['distinct_candidate_tag_count']} | "
            f"{grok['distinct_positive_tag_count']} |",
            f"| 空 Unit 数 | {static['empty_unit_count']} | {deepseek['empty_unit_count']} | "
            f"{grok['empty_unit_count']} |",
            "",
            "`routing_tags` 在本样本中是同一个整文件提示签名：11 种 Tag × 15 Unit = "
            "165 次分配。它们不是 Unit positive，不能加入上表的静态 exact 数量。",
            "",
            "### Tag 分布",
            "",
            "| Tag | Static exact | DeepSeek candidate positive | Grok proxy positive |",
            "|---|---:|---:|---:|",
            *tag_rows,
            "",
            "## 5. 逐 ReviewUnit 对照",
            "",
            "| side | ReviewUnit | Static exact | DeepSeek positive | Grok positive | 模型分歧 |",
            "|---|---|---|---|---|---|",
            *unit_rows,
            "",
            "Grok 是独立盲判代理：它没有读取 DeepSeek 的结论。分歧被原样保留，没有在线裁决。",
            "",
            "## 6. DeepSeek 运行证据",
            "",
            f"- 有效 Unit：`{summary['deepseek_valid_unit_count']}/15`。",
            f"- 24-Tag 判断总数：`{summary['deepseek_judgment_count']}`。",
            f"- positive / not_supported / abstain："
            f"`{summary['deepseek_positive_count']}` / "
            f"`{summary['deepseek_not_supported_count']}` / "
            f"`{summary['deepseek_abstain_count']}`。",
            f"- 输入 / 输出 / cache-read tokens："
            f"`{summary['deepseek_input_tokens']}` / "
            f"`{summary['deepseek_output_tokens']}` / "
            f"`{summary['deepseek_cache_read_input_tokens']}`。",
            f"- 单 Unit 延迟范围：`{summary['deepseek_latency_min_ms']}`～"
            f"`{summary['deepseek_latency_max_ms']}` ms。",
            "- 每个 Unit 只尝试一次，不重试。",
            "- `valid_shape` 只证明输出满足合同，不证明判断正确。",
            "",
            "## 7. DeepSeek / Grok 一致性",
            "",
            f"- 360 个二元 positive/not-positive 判断中有 "
            f"`{proxy_agreement['both_positive_assignment_count']}` 个共同 positive、"
            f"`{proxy_agreement['both_not_positive_assignment_count']}` 个共同 not-positive。",
            f"- DeepSeek-only：`{proxy_agreement['deepseek_only_assignment_count']}`；"
            f"Grok-only：`{proxy_agreement['grok_only_assignment_count']}`。",
            f"- positive Jaccard：`{proxy_agreement['positive_jaccard']:.3f}`；"
            f"raw binary agreement：`{proxy_agreement['raw_binary_decision_agreement']:.3f}`；"
            f"Cohen kappa：`{proxy_agreement['cohen_kappa_binary_positive']:.3f}`。",
            "- raw agreement 会被大量共同 negative 抬高；这些指标只描述两个模型的一致性，"
            "不是 Precision、Recall 或准确率。",
            "",
            "## 8. 已观察到的过度推断与漏判风险",
            "",
            "这次真实运行没有人工 Truth，但模型理由已经暴露出需要重点复核的候选：",
            "",
            "1. `has_file_io`：根据 `readLRCFile` helper 名称推断文件 I/O，当前 Unit "
            "没有直接出现 `fileIo.` 或 `fs.`。",
            "2. `has_network`：根据 `addNetworkListener` helper 调用推断网络行为，属于 间接语义。",
            "3. `has_timer`：根据 `clearRuntimeTimers` helper 调用推断计时器行为，属于 间接语义。",
            "4. `has_subscription`：将 AVPlayer 的 `.on/.off` 解释成当前只为 "
            "emitter/sensor 定义的订阅 Tag。",
            "5. `has_logging`：将自定义 `Log.info` 解释成当前配置定义的 `hilog.`。",
            "",
            "6 个 Unit 的模型 positive 集合不一致。DeepSeek-only 主要是 "
            "`has_state_management`（5 次），另有 `has_file_io`、`has_network`、"
            "`has_timer` 各 1 次；Grok-only 是 `aboutToDisappear` 的 `has_network`。",
            "这些只是合同对照下的风险观察，不是正式人工裁决。双方共同判断也可能共同偏离 "
            "Tag 合同，因此不能把一致结果直接提升为 `unit_exact` 或 Tag Truth。",
            "",
            "## 9. Grok 运行结果",
            "",
            "- 按批准范围执行 15 次，每个 Unit 一次，无重试。",
            f"- 有效 Unit：`{summary['grok_valid_judgment_count']}/15`；24-Tag 判断总数："
            f"`{summary['grok_judgment_count']}`。",
            f"- positive / not_supported / abstain：`{summary['grok_positive_count']}` / "
            f"`{summary['grok_not_supported_count']}` / `{summary['grok_abstain_count']}`。",
            f"- 输入 / 输出 / reasoning / cache-read tokens："
            f"`{summary['grok_input_tokens']}` / `{summary['grok_output_tokens']}` / "
            f"`{summary['grok_reasoning_tokens']}` / "
            f"`{summary['grok_cache_read_input_tokens']}`。",
            f"- 单 Unit 延迟范围：`{summary['grok_latency_min_ms']}`～"
            f"`{summary['grok_latency_max_ms']}` ms；Campaign 合计约 "
            f"`{summary['grok_campaign_elapsed_ms']}` ms。",
            "- `valid_shape` 证明输出完整且通过本地语义校验，不证明 Grok 是正确裁判。",
            "",
            "## 10. 机器产物",
            "",
            "| 阶段 | 文件 |",
            "|---|---|",
            *artifact_rows,
            "",
            "## 11. 断言",
            "",
            f"离线重建与结果整理共通过 `{assertions['passed']}` 项断言；完整证据见 "
            "[13_assertions.json](artifacts/13_assertions.json)。断言 PASS 证明的是身份、"
            "结构和统计可重放，不代表 Tag 真实质量 PASS。",
            "",
            "## 12. 如何离线重建",
            "",
            "```bash",
            "cd /home/autken/Code/arkts-code-reviewer",
            "PYTHONPATH=src .venv/bin/python E2E_test_example_2_ai_tag_pilot/run_e2e.py",
            "```",
            "",
            "命令只读取仓库内固定输入并重建报告，不读取 `.env`，也不会调用 DeepSeek "
            "或 Grok。`prepare_observations.py` 仅用于从原始 `.codex` 证据重新生成脱敏 "
            "inputs，正常查看或重建报告不需要运行它。",
            "",
            "## 13. 准确结论边界",
            "",
            "本 Pilot 已证明：静态 exact 在该样本上的输出很少；DeepSeek 能给出更多候选 "
            "Tag；DeepSeek 与 Grok 都完成 15×24 结构化判断；双方一致性、分歧、运行身份和"
            "用量可以复核。",
            "",
            "本 Pilot 没有证明：AI 候选都正确、模型一致即真实、真实 Tag Precision/Recall、"
            "Grok 裁判质量、知识检索提升、相关文档质量、最终模型答案质量或生产可用性。",
            "",
        ]
    )


def _build_payloads() -> tuple[dict[str, object], str]:
    source_fixture = _validate_source_fixture()
    pilot = build_video_player_tag_pilot()
    if pilot.inspection.get("pilot_id") != PILOT_ID:
        raise ValueError("rebuilt Pilot identity drifted")
    # Re-run the source-free inspection validator and identity check.
    render_video_player_tag_pilot_inspection(pilot.inspection)

    deepseek_input = _load_json(INPUTS / "deepseek_observations.json")
    grok_input = _load_json(INPUTS / "grok_observations.json")
    provenance = _load_json(INPUTS / "live_provenance.json")
    deepseek_rows, grok_rows = _validate_observations(
        pilot,
        deepseek_input,
        grok_input,
        provenance,
    )
    comparison = _build_comparison(pilot, deepseek_rows, grok_rows)
    assertions = _build_assertions(
        pilot,
        comparison,
        deepseek_rows,
        grok_rows,
        source_fixture,
    )

    parse_results = list(pilot.upstream.analysis_result.file_parse_results)
    parse_by_revision = {item.analysis.source_ref.revision: item for item in parse_results}
    base_revision = pilot.inspection["sample"]["source_revision"]
    head_revision = pilot.inspection["sample"]["synthetic_head_revision"]
    base_parse = parse_by_revision[base_revision]
    head_parse = parse_by_revision[head_revision]

    decision_counts: Counter[str] = Counter()
    input_tokens = 0
    output_tokens = 0
    cache_tokens = 0
    latencies: list[int] = []
    for row in deepseek_rows:
        for judgment in row["judgments"]:
            decision_counts[_mapping(judgment, "judgment")["decision"]] += 1
        usage = _mapping(row["usage"], "DeepSeek usage")
        input_tokens += int(usage["input_tokens"])
        output_tokens += int(usage["output_tokens"])
        cache_tokens += int(usage["cache_read_input_tokens"])
        latencies.append(int(row["latency_ms"]))

    grok_decision_counts: Counter[str] = Counter()
    grok_input_tokens = 0
    grok_output_tokens = 0
    grok_reasoning_tokens = 0
    grok_cache_tokens = 0
    grok_latencies: list[int] = []
    for row in grok_rows:
        for judgment in row["judgments"]:
            grok_decision_counts[_mapping(judgment, "Grok judgment")["decision"]] += 1
        usage = _mapping(row["usage"], "Grok usage")
        grok_input_tokens += int(usage["input_tokens"])
        grok_output_tokens += int(usage["output_tokens"])
        grok_reasoning_tokens += int(usage["reasoning_tokens"])
        grok_cache_tokens += int(usage["cache_read_input_tokens"])
        grok_latencies.append(int(row["latency_ms"]))

    deepseek_summary = _mapping(deepseek_input["summary"], "DeepSeek summary")
    grok_summary = _mapping(grok_input["summary"], "Grok summary")
    summary = {
        "schema_version": "video-player-tag-pilot-summary-v2",
        "execution_status": "partial",
        "deepseek_shape_status": "pass",
        "grok_status": "pass",
        "tag_quality_status": "not_qualified",
        "production_eligible": False,
        "pilot_id": PILOT_ID,
        "campaign_id": CAMPAIGN_ID,
        "is_real_mr": False,
        "review_unit_count": 15,
        "static_exact_assignment_count": comparison["static_exact"]["assignment_count"],
        "static_exact_distinct_tag_count": comparison["static_exact"]["distinct_tag_count"],
        "static_empty_unit_count": comparison["static_exact"]["empty_unit_count"],
        "file_hint_assignment_count": pilot.inspection["static_baseline"][
            "routing_tag_assignment_count"
        ],
        "file_hint_scope": "whole_file_context_not_unit_positive",
        "deepseek_valid_unit_count": 15,
        "deepseek_judgment_count": sum(decision_counts.values()),
        "deepseek_positive_count": decision_counts["positive"],
        "deepseek_not_supported_count": decision_counts["not_supported"],
        "deepseek_abstain_count": decision_counts["abstain"],
        "deepseek_candidate_positive_assignment_count": comparison["deepseek"][
            "candidate_positive_assignment_count"
        ],
        "deepseek_candidate_distinct_tag_count": comparison["deepseek"][
            "distinct_candidate_tag_count"
        ],
        "deepseek_empty_unit_count": comparison["deepseek"]["empty_unit_count"],
        "static_deepseek_overlap_assignment_count": comparison["agreement"][
            "overlap_assignment_count"
        ],
        "deepseek_only_candidate_assignment_count": comparison["agreement"][
            "deepseek_only_candidate_assignment_count"
        ],
        "static_only_assignment_count": comparison["agreement"]["static_only_assignment_count"],
        "deepseek_input_tokens": input_tokens,
        "deepseek_output_tokens": output_tokens,
        "deepseek_cache_read_input_tokens": cache_tokens,
        "deepseek_model_latency_sum_ms": sum(latencies),
        "deepseek_latency_min_ms": min(latencies),
        "deepseek_latency_max_ms": max(latencies),
        "deepseek_campaign_elapsed_ms": deepseek_summary["elapsed_ms"],
        "grok_attempted_count": grok_summary["attempted_count"],
        "grok_valid_judgment_count": 15,
        "grok_provider_error_count": 0,
        "grok_judgment_count": sum(grok_decision_counts.values()),
        "grok_positive_count": grok_decision_counts["positive"],
        "grok_not_supported_count": grok_decision_counts["not_supported"],
        "grok_abstain_count": grok_decision_counts["abstain"],
        "grok_positive_assignment_count": comparison["grok"]["positive_assignment_count"],
        "grok_distinct_positive_tag_count": comparison["grok"]["distinct_positive_tag_count"],
        "grok_empty_unit_count": comparison["grok"]["empty_unit_count"],
        "grok_input_tokens": grok_input_tokens,
        "grok_output_tokens": grok_output_tokens,
        "grok_reasoning_tokens": grok_reasoning_tokens,
        "grok_cache_read_input_tokens": grok_cache_tokens,
        "grok_model_latency_sum_ms": sum(grok_latencies),
        "grok_latency_min_ms": min(grok_latencies),
        "grok_latency_max_ms": max(grok_latencies),
        "grok_campaign_elapsed_ms": grok_summary["elapsed_ms"],
        "deepseek_grok_both_positive_count": comparison["deepseek_grok_agreement"][
            "both_positive_assignment_count"
        ],
        "deepseek_grok_deepseek_only_count": comparison["deepseek_grok_agreement"][
            "deepseek_only_assignment_count"
        ],
        "deepseek_grok_grok_only_count": comparison["deepseek_grok_agreement"][
            "grok_only_assignment_count"
        ],
        "deepseek_grok_exact_positive_set_unit_count": comparison[
            "deepseek_grok_agreement"
        ]["exact_positive_set_unit_count"],
        "human_truth_available": False,
        "real_tag_precision_recall_available": False,
        "retrieval_executed": False,
        "document_quality_evaluated": False,
        "finding_generated": False,
        "report": "REPORT.md",
    }
    manifest = {
        "schema_version": "video-player-tag-pilot-run-manifest-v2",
        "execution_status": "partial",
        "tag_quality_status": "not_qualified",
        "scope": "ChangeSet through Static/DeepSeek/Grok Tag comparison",
        "excluded_stages": [
            "Retrieval",
            "Rules",
            "Review Prompt",
            "Final LLM",
            "Finding",
            "GitCode publication",
        ],
        "sample": pilot.inspection["sample"],
        "source_fixture": source_fixture,
        "pilot_id": PILOT_ID,
        "campaign_id": CAMPAIGN_ID,
        "deepseek_request_set_digest": DEEPSEEK_REQUEST_SET_DIGEST,
        "grok_request_set_digest": GROK_REQUEST_SET_DIGEST,
        "grok_source_run": GROK_SOURCE_RUN,
        "provider_observation_date": "2026-07-21",
        "runtime": {
            "python": sys.version.split()[0],
            "network_attempted": False,
            "credential_accessed": False,
            "provider_observations_are_fixed_inputs": True,
        },
        "output_files": list(OUTPUT_NAMES),
        "quality_boundary": ("not_real_mr_not_human_truth_not_tag_precision_recall_not_production"),
    }

    analysis = pilot.upstream.analysis_result
    build_result = analysis.review_unit_build_result
    if build_result is None:
        raise ValueError("Pilot AnalysisResult lacks ReviewUnitBuildResult")
    payloads: dict[str, object] = {
        "00_run_manifest.json": manifest,
        "01_change_set.json": pilot.upstream.change_set.to_dict(),
        "02_parser_base.json": base_parse.to_dict(),
        "03_parser_head.json": head_parse.to_dict(),
        "04_review_unit_build.json": asdict(build_result),
        "05_unit_fact_scopes.json": {
            "schema_version": "unit-fact-scopes-v1",
            "scopes": [scope.to_dict() for scope in analysis.unit_fact_scopes],
        },
        "06_static_feature_routing.json": analysis.feature_routing_result.to_dict(),
        "07_context_plan.json": pilot.upstream.context_plan.to_dict(),
        "08_campaign_inspection.json": pilot.inspection,
        "09_deepseek_results.json": deepseek_input,
        "10_grok_results.json": grok_input,
        "11_static_deepseek_comparison.json": comparison,
        "12_live_provenance.json": provenance,
        "13_assertions.json": assertions,
        "14_summary.json": summary,
    }
    report = _report(manifest, comparison, summary, assertions)
    return payloads, report


def run(output_dir: Path, report_path: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    marker = output_dir / "RUN_INCOMPLETE"
    marker.write_text(
        "The latest offline Tag Pilot rebuild did not complete.\n",
        encoding="utf-8",
    )
    payloads, report = _build_payloads()
    if set(payloads) != set(OUTPUT_NAMES):
        raise ValueError("Tag Pilot output set differs from the fixed manifest")
    with TemporaryDirectory(prefix=".tag-pilot-staging-", dir=output_dir.parent) as name:
        staging = Path(name)
        for output_name in OUTPUT_NAMES:
            (staging / output_name).write_text(
                _json_text(payloads[output_name]),
                encoding="utf-8",
            )
            # Strictly parse the exact bytes before publication.
            _load_json(staging / output_name)
        staged_report = staging / "REPORT.md"
        staged_report.write_text(report, encoding="utf-8")
        for output_name in OUTPUT_NAMES:
            os.replace(staging / output_name, output_dir / output_name)
        os.replace(staged_report, report_path)
    marker.unlink()
    return dict(_mapping(payloads["14_summary.json"], "summary"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Offline-rebuild the fixed VideoPlayer Static/DeepSeek/Grok Tag Pilot "
            "artifacts and report."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args(argv)
    try:
        summary = run(args.output_dir.resolve(), args.report_path.resolve())
    except (AssertionError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
