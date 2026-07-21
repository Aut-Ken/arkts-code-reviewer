#!/usr/bin/env python3
"""Import the approved VideoPlayer live observations into a safe, fixed fixture.

This command is offline.  It never reads ``.env``, credentials, prompt bodies, source
blocks from provider requests, or raw provider response bodies.  It accepts an explicit
completed live-artifact directory, validates it against the rebuilt Pilot, and writes a
reviewable projection under ``inputs/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from arkts_code_reviewer.hybrid_analysis import (  # noqa: E402
    load_ai_tag_response_validation,
)
from E2E_test_example_1.video_player_tag_pilot import (  # noqa: E402
    build_video_player_tag_pilot,
    validate_grok_blind_output,
)

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "inputs"
SOURCE_INPUTS = REPOSITORY_ROOT / "E2E_test_example_1" / "inputs"
DEFAULT_LIVE_ROOT = (
    REPOSITORY_ROOT
    / ".codex"
    / "video-player-tag-pilot"
    / "28965ae3c4ec76e50aa44d26712bb7d1acbd673f4514f6d66497e98db504f067"
)

PILOT_ID = "sha256:28965ae3c4ec76e50aa44d26712bb7d1acbd673f4514f6d66497e98db504f067"
DEEPSEEK_CAMPAIGN_ID = (
    "ai-tag-shadow-campaign:sha256:85e98a9fb77d6c7b24a038616bbfe6122c13edb9bf8c7d6c8a0d328076a11b45"
)
DEEPSEEK_REQUEST_SET_DIGEST = (
    "sha256:954f53f015305769028e2fdd39d696364f1dbbafc1da614484074975741da8e6"
)
GROK_REQUEST_SET_DIGEST = "sha256:5fa7f113f815bf46a82e32e4f50e577cddad92727c0ce818f0e2c4ee33f49b35"
GROK_LIVE_DIR_NAME = "grok-rerun-1"
SOURCE_NAMES = (
    "base.ets",
    "head.ets",
    "diff.patch",
    "expected_review_units.json",
    "mutation_spec.json",
    "provenance.json",
)
OUTPUT_NAMES = (
    "deepseek_observations.json",
    "grok_observations.json",
    "live_provenance.json",
    *SOURCE_NAMES,
)


class DuplicateJsonKeyError(ValueError):
    """Raised when an imported JSON artifact is ambiguous."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required input is not a regular file: {path}")
    return path.read_bytes()


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            _read_bytes(path),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must contain one object: {path}")
    return value


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _artifact_record(path: Path, *, relative_to: Path) -> dict[str, object]:
    raw = _read_bytes(path)
    return {
        "path": path.relative_to(relative_to).as_posix(),
        "sha256": _sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def _write_bytes(path: Path, value: bytes) -> None:
    path.write_bytes(value)


def _validate_live_root(live_root: Path) -> tuple[Path, Path]:
    if live_root.is_symlink() or not live_root.is_dir():
        raise ValueError(f"live root is not a regular directory: {live_root}")
    if live_root.name != PILOT_ID.removeprefix("sha256:"):
        raise ValueError("live root does not match the approved Pilot ID")
    deepseek_dir = live_root / "deepseek"
    grok_dir = live_root / GROK_LIVE_DIR_NAME
    for path in (deepseek_dir, grok_dir):
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"provider result directory is missing or unsafe: {path}")
    return deepseek_dir, grok_dir


def _result_files(provider_dir: Path, suffix: str) -> tuple[Path, ...]:
    files = tuple(sorted(provider_dir.glob(f"*.{suffix}.json")))
    if len(files) != 15:
        raise ValueError(f"expected 15 {suffix} artifacts under {provider_dir}, got {len(files)}")
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise ValueError(f"unsafe {suffix} artifact under {provider_dir}")
    return files


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _prepare_deepseek(
    deepseek_dir: Path,
    pilot: object,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    inspection = _load_json(deepseek_dir / "inspection.json")
    if inspection != pilot.inspection:
        raise ValueError("DeepSeek live inspection differs from rebuilt Pilot")
    summary = _load_json(deepseek_dir / "summary.json")
    if (
        summary.get("campaign_id") != DEEPSEEK_CAMPAIGN_ID
        or summary.get("request_set_digest") != DEEPSEEK_REQUEST_SET_DIGEST
        or summary.get("attempted_count") != 15
        or summary.get("status_counts") != {"valid_shape": 15}
    ):
        raise ValueError("DeepSeek summary does not match the approved successful run")

    markers = _result_files(deepseek_dir, "attempted")
    results = _result_files(deepseek_dir, "result")
    marker_by_plan: dict[str, tuple[dict[str, object], Path]] = {}
    for path in markers:
        marker = _load_json(path)
        plan_id = marker.get("plan_id")
        if (
            not isinstance(plan_id, str)
            or marker.get("request_set_digest") != DEEPSEEK_REQUEST_SET_DIGEST
            or marker.get("attempt_cap") != 1
            or marker.get("retry_count") != 0
            or plan_id in marker_by_plan
        ):
            raise ValueError(f"invalid DeepSeek attempt marker: {path}")
        marker_by_plan[plan_id] = (marker, path)

    unit_by_id = {unit.card.unit_id: unit for unit in pilot.campaign.units}
    projections_by_unit: dict[str, dict[str, object]] = {}
    for path in results:
        result = _load_json(path)
        unit_id = result.get("unit_id")
        plan_id = result.get("plan_id")
        if not isinstance(unit_id, str) or unit_id not in unit_by_id:
            raise ValueError(f"unknown DeepSeek Unit in {path}")
        if not isinstance(plan_id, str) or plan_id not in marker_by_plan:
            raise ValueError(f"DeepSeek result lacks its attempt marker: {path}")
        if unit_id in projections_by_unit:
            raise ValueError(f"duplicate DeepSeek Unit result: {unit_id}")
        unit = unit_by_id[unit_id]
        marker, marker_path = marker_by_plan[plan_id]
        validation_raw = _mapping(result.get("response_validation"), "response_validation")
        validation = load_ai_tag_response_validation(_json_bytes(validation_raw))
        receipt = _mapping(result.get("provider_response_receipt"), "provider receipt")
        attempt_receipt = _mapping(result.get("attempt_receipt"), "attempt receipt")
        positive_tags = [
            item.tag_id for item in validation.judgments if item.decision == "positive"
        ]
        if (
            result.get("campaign_id") != DEEPSEEK_CAMPAIGN_ID
            or result.get("request_set_digest") != DEEPSEEK_REQUEST_SET_DIGEST
            or result.get("provider") != "deepseek"
            or result.get("qualification") != "real_provider_shadow_observation_not_tag_truth"
            or result.get("positive_tags") != positive_tags
            or validation.status != "valid_shape"
            or validation.attempt_count != 1
            or validation.request_id != unit.request.request_id
            or validation.envelope_id != unit.envelope.envelope_id
            or plan_id != unit.plan.plan_id
            or marker.get("wire_body_sha256") != unit.plan.wire_body_sha256
            or attempt_receipt.get("http_status") != 200
            or receipt.get("finish_reason") != "stop"
            or receipt.get("model") != "deepseek-v4-pro"
        ):
            raise ValueError(f"DeepSeek result failed Pilot binding: {path}")

        projections_by_unit[unit_id] = {
            "schema_version": "deepseek-tag-live-observation-projection-v1",
            "unit_id": unit_id,
            "unit_symbol": result.get("unit_symbol"),
            "source_role": result.get("source_role"),
            "plan_id": plan_id,
            "request_id": validation.request_id,
            "envelope_id": validation.envelope_id,
            "wire_body_sha256": validation.wire_body_sha256,
            "status": validation.status,
            "reason_code": validation.reason_code,
            "provider": "deepseek",
            "model": validation.model,
            "finish_reason": validation.finish_reason,
            "http_status": attempt_receipt.get("http_status"),
            "attempt_count": validation.attempt_count,
            "attempt_cap": marker.get("attempt_cap"),
            "retry_count": marker.get("retry_count"),
            "latency_ms": validation.latency_ms,
            "usage": validation.usage.model_dump(mode="json"),
            "positive_tags": positive_tags,
            "judgments": [item.model_dump(mode="json") for item in validation.judgments],
            "judgment_reason_role": "model_diagnostic_text_not_tag_truth",
            "qualification": "real_provider_shadow_observation_not_tag_truth",
            "transport_evidence": {
                "attempt_receipt_id": attempt_receipt.get("receipt_id"),
                "provider_response_receipt_id": receipt.get("receipt_id"),
                "response_body_sha256": receipt.get("response_body_sha256"),
                "response_body_size_bytes": receipt.get("response_body_size_bytes"),
                "transport_status": attempt_receipt.get("transport_status"),
                "provider_signed": False,
            },
            "source_artifacts": {
                "attempt_marker": _artifact_record(marker_path, relative_to=deepseek_dir),
                "result": _artifact_record(path, relative_to=deepseek_dir),
            },
        }

    ordered = [projections_by_unit[unit.card.unit_id] for unit in pilot.campaign.units]
    if len(ordered) != 15:
        raise ValueError("DeepSeek projection does not cover all 15 Pilot Units")
    return summary, ordered


def _prepare_grok(
    grok_dir: Path,
    pilot: object,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    inspection = _load_json(grok_dir / "inspection.json")
    if inspection != pilot.inspection:
        raise ValueError("Grok live inspection differs from rebuilt Pilot")
    summary = _load_json(grok_dir / "summary.json")
    if (
        summary.get("request_set_digest") != GROK_REQUEST_SET_DIGEST
        or summary.get("attempted_count") != 15
        or summary.get("status_counts") != {"valid_shape": 15}
    ):
        raise ValueError("Grok summary does not match the approved successful rerun")

    markers = _result_files(grok_dir, "attempted")
    results = _result_files(grok_dir, "result")
    planned_by_unit = {
        row["unit_id"]: row["grok"] for row in _list(pilot.inspection["units"], "Pilot Units")
    }
    marker_by_model_view: dict[str, tuple[dict[str, object], Path]] = {}
    for path in markers:
        marker = _load_json(path)
        model_view_id = marker.get("model_view_id")
        if (
            not isinstance(model_view_id, str)
            or marker.get("request_set_digest") != GROK_REQUEST_SET_DIGEST
            or marker.get("attempt_cap") != 1
            or marker.get("retry_count") != 0
            or model_view_id in marker_by_model_view
        ):
            raise ValueError(f"invalid Grok attempt marker: {path}")
        marker_by_model_view[model_view_id] = (marker, path)

    unit_by_id = {unit.card.unit_id: unit for unit in pilot.campaign.units}
    projections_by_unit: dict[str, dict[str, object]] = {}
    for path in results:
        result = _load_json(path)
        unit_id = result.get("unit_id")
        model_view_id = result.get("model_view_id")
        if not isinstance(unit_id, str) or unit_id not in unit_by_id:
            raise ValueError(f"unknown Grok Unit in {path}")
        if not isinstance(model_view_id, str) or model_view_id not in marker_by_model_view:
            raise ValueError(f"Grok result lacks its attempt marker: {path}")
        if unit_id in projections_by_unit:
            raise ValueError(f"duplicate Grok Unit result: {unit_id}")
        unit = unit_by_id[unit_id]
        marker, marker_path = marker_by_model_view[model_view_id]
        planned = _mapping(planned_by_unit.get(unit_id), "planned Grok request")
        judgments_raw = _list(result.get("judgments"), "Grok judgments")
        judgments = [dict(_mapping(item, "Grok judgment")) for item in judgments_raw]
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
        positive_tags = [
            item.tag_id for item in strict_judgments if item.decision == "positive"
        ]
        usage = dict(_mapping(result.get("usage"), "Grok usage"))
        expected_usage_keys = {
            "input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
        }
        if (
            result.get("request_set_digest") != GROK_REQUEST_SET_DIGEST
            or result.get("provider") != "grok"
            or result.get("status") != "valid_shape"
            or result.get("reason_code") != "response_shape_valid"
            or result.get("qualification") != "blind_proxy_judge_not_human_truth"
            or result.get("stopReason") != "EndTurn"
            or result.get("stderr_size_bytes") != 0
            or result.get("stderr_sha256")
            != "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            or not isinstance(result.get("stdout_size_bytes"), int)
            or result.get("stdout_size_bytes", 0) <= 0
            or marker.get("unit_id") != unit_id
            or marker.get("request_id") != unit.request.request_id
            or marker.get("system_prompt_sha256") != planned.get("system_prompt_sha256")
            or marker.get("user_prompt_sha256") != planned.get("user_prompt_sha256")
            or model_view_id != unit.model_view.model_view_id
            or result.get("request_id") != unit.request.request_id
            or judgments != strict_judgment_payloads
            or result.get("positive_tags") != positive_tags
            or set(usage) != expected_usage_keys
            or any(not isinstance(value, int) or value < 0 for value in usage.values())
        ):
            raise ValueError(f"Grok successful result failed Pilot binding: {path}")

        projections_by_unit[unit_id] = {
            "schema_version": "grok-tag-live-observation-projection-v1",
            "unit_id": unit_id,
            "unit_symbol": result.get("unit_symbol"),
            "source_role": result.get("source_role"),
            "model_view_id": model_view_id,
            "request_id": result.get("request_id"),
            "prompt_identity": {
                "system_prompt_sha256": marker.get("system_prompt_sha256"),
                "user_prompt_sha256": marker.get("user_prompt_sha256"),
            },
            "provider": "grok",
            "model": "grok-4.5",
            "status": "valid_shape",
            "reason_code": "response_shape_valid",
            "stop_reason": result.get("stopReason"),
            "latency_ms": result.get("latency_ms"),
            "attempt_count": 1,
            "attempt_cap": marker.get("attempt_cap"),
            "retry_count": marker.get("retry_count"),
            "validated_judgment_available": True,
            "positive_tags": positive_tags,
            "judgments": judgments,
            "usage": usage,
            "judgment_reason_role": "model_diagnostic_text_not_tag_truth",
            "transport_evidence": {
                "stdout_sha256": result.get("stdout_sha256"),
                "stdout_size_bytes": result.get("stdout_size_bytes"),
                "stderr_sha256": result.get("stderr_sha256"),
                "stderr_size_bytes": result.get("stderr_size_bytes"),
                "provider_signed": False,
            },
            "qualification": "blind_proxy_judge_not_human_truth",
            "source_artifacts": {
                "attempt_marker": _artifact_record(marker_path, relative_to=grok_dir),
                "result": _artifact_record(path, relative_to=grok_dir),
            },
        }

    ordered = [projections_by_unit[unit.card.unit_id] for unit in pilot.campaign.units]
    if len(ordered) != 15:
        raise ValueError("Grok projection does not cover all 15 Pilot Units")
    return summary, ordered


def _source_manifest(live_root: Path, pilot: object) -> dict[str, object]:
    records: list[dict[str, object]] = []
    provider_dirs = {
        "deepseek": live_root / "deepseek",
        "grok": live_root / GROK_LIVE_DIR_NAME,
    }
    for provider, provider_dir in provider_dirs.items():
        for path in sorted(provider_dir.glob("*.json")):
            raw = _read_bytes(path)
            source_dir_name = "deepseek" if provider == "deepseek" else GROK_LIVE_DIR_NAME
            records.append(
                {
                    "provider": provider,
                    "path": f"{source_dir_name}/{path.name}",
                    "sha256": _sha256_bytes(raw),
                    "size_bytes": len(raw),
                }
            )
    return {
        "schema_version": "video-player-tag-live-provenance-v2",
        "pilot_id": pilot.inspection["pilot_id"],
        "campaign_id": DEEPSEEK_CAMPAIGN_ID,
        "deepseek_request_set_digest": DEEPSEEK_REQUEST_SET_DIGEST,
        "grok_request_set_digest": GROK_REQUEST_SET_DIGEST,
        "grok_source_run": GROK_LIVE_DIR_NAME,
        "source_artifact_count": len(records),
        "source_artifacts": records,
        "projection_policy": {
            "included": [
                "validated_24_tag_judgments_and_model_reasons",
                "usage_latency_and_attempt_limits",
                "transport_and_source_artifact_hashes",
                "grok_validated_blind_proxy_judgments",
            ],
            "excluded": [
                "api_keys_and_authorization_headers",
                "prompt_and_source_code_bodies",
                "wire_request_bodies",
                "raw_provider_response_bodies",
                "credential_scope_and_internal_provider_identifiers",
                "provider_request_and_session_identifiers",
            ],
            "deepseek_reason_role": "model_diagnostic_text_not_tag_truth",
            "grok_reason_role": "model_diagnostic_text_not_tag_truth",
            "grok_judgment_role": "blind_proxy_not_human_truth",
        },
        "contains_api_key": False,
        "contains_prompt_or_source_body": False,
        "contains_raw_provider_response_body": False,
        "quality_boundary": (
            "real_provider_observation_not_human_truth_not_real_mr_not_production_precision_recall"
        ),
    }


def prepare(live_root: Path) -> None:
    deepseek_dir, grok_dir = _validate_live_root(live_root)
    pilot = build_video_player_tag_pilot()
    if (
        pilot.inspection.get("pilot_id") != PILOT_ID
        or pilot.campaign.manifest.campaign_id != DEEPSEEK_CAMPAIGN_ID
        or pilot.inspection["deepseek_campaign"]["request_set_digest"]
        != DEEPSEEK_REQUEST_SET_DIGEST
        or pilot.inspection["grok_blind_proxy_campaign"]["request_set_digest"]
        != GROK_REQUEST_SET_DIGEST
    ):
        raise ValueError("rebuilt Pilot identity differs from the approved live run")

    deepseek_summary, deepseek_rows = _prepare_deepseek(deepseek_dir, pilot)
    grok_summary, grok_rows = _prepare_grok(grok_dir, pilot)
    deepseek_payload = {
        "schema_version": "deepseek-tag-live-observation-set-v1",
        "pilot_id": PILOT_ID,
        "campaign_id": DEEPSEEK_CAMPAIGN_ID,
        "request_set_digest": DEEPSEEK_REQUEST_SET_DIGEST,
        "summary": deepseek_summary,
        "observations": deepseek_rows,
    }
    grok_payload = {
        "schema_version": "grok-tag-live-observation-set-v1",
        "pilot_id": PILOT_ID,
        "request_set_digest": GROK_REQUEST_SET_DIGEST,
        "source_run": GROK_LIVE_DIR_NAME,
        "summary": grok_summary,
        "validated_judgment_available": True,
        "observations": grok_rows,
    }
    provenance = _source_manifest(live_root, pilot)

    INPUTS.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".tag-pilot-inputs-", dir=ROOT) as temp_name:
        staging = Path(temp_name)
        _write_bytes(staging / "deepseek_observations.json", _json_bytes(deepseek_payload))
        _write_bytes(staging / "grok_observations.json", _json_bytes(grok_payload))
        _write_bytes(staging / "live_provenance.json", _json_bytes(provenance))
        for name in SOURCE_NAMES:
            source = SOURCE_INPUTS / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"source fixture file is missing or unsafe: {source}")
            shutil.copyfile(source, staging / name)
        for name in OUTPUT_NAMES:
            os.replace(staging / name, INPUTS / name)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and import the approved VideoPlayer DeepSeek/Grok live "
            "observations without any provider call or credential access."
        )
    )
    parser.add_argument(
        "--live-root",
        type=Path,
        default=DEFAULT_LIVE_ROOT,
        help="Explicit completed .codex Pilot directory.",
    )
    args = parser.parse_args(argv)
    try:
        prepare(args.live_root.resolve())
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": "prepared",
                "pilot_id": PILOT_ID,
                "input_dir": str(INPUTS),
                "network_attempted": False,
                "credential_accessed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
