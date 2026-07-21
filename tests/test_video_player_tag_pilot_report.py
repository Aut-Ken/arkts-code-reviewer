from __future__ import annotations

import json
from pathlib import Path

import pytest

from E2E_test_example_1.video_player_tag_pilot import build_video_player_tag_pilot
from E2E_test_example_2_ai_tag_pilot import run_e2e


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            nested_key
            for nested in value.values()
            for nested_key in _walk_keys(nested)
        }
    if isinstance(value, list):
        return {nested_key for nested in value for nested_key in _walk_keys(nested)}
    return set()


def test_offline_tag_pilot_rebuild_matches_published_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "artifacts"
    report_path = tmp_path / "REPORT.md"

    summary = run_e2e.run(output_dir, report_path)

    assert summary["execution_status"] == "partial"
    assert summary["deepseek_shape_status"] == "pass"
    assert summary["grok_status"] == "provider_error"
    assert summary["tag_quality_status"] == "not_qualified"
    assert not (output_dir / "RUN_INCOMPLETE").exists()
    for name in run_e2e.OUTPUT_NAMES:
        assert (output_dir / name).read_bytes() == (
            run_e2e.DEFAULT_OUTPUT_DIR / name
        ).read_bytes()
    assert report_path.read_bytes() == run_e2e.DEFAULT_REPORT_PATH.read_bytes()


def test_provider_observation_inputs_exclude_sensitive_request_fields() -> None:
    deepseek = json.loads(
        (run_e2e.INPUTS / "deepseek_observations.json").read_text(encoding="utf-8")
    )
    grok = json.loads(
        (run_e2e.INPUTS / "grok_observations.json").read_text(encoding="utf-8")
    )
    forbidden_keys = {
        "api_key",
        "authorization",
        "messages",
        "prompt",
        "raw_provider_response_body",
        "system_fingerprint",
        "provider_response_id",
        "credential_scope_id",
        "wire_body_json",
    }

    assert not (_walk_keys(deepseek) | _walk_keys(grok)) & forbidden_keys
    assert len(deepseek["observations"]) == 15
    assert all(len(row["judgments"]) == 24 for row in deepseek["observations"])
    assert len(grok["observations"]) == 15
    assert all(row["positive_tags"] is None for row in grok["observations"])
    assert all(
        row["positive_tags_note"] == "not_available_due_to_execution_failure"
        for row in grok["observations"]
    )


def test_offline_rebuild_rejects_judgment_and_provenance_corruption() -> None:
    pilot = build_video_player_tag_pilot()
    deepseek = json.loads(
        (run_e2e.INPUTS / "deepseek_observations.json").read_text(encoding="utf-8")
    )
    grok = json.loads(
        (run_e2e.INPUTS / "grok_observations.json").read_text(encoding="utf-8")
    )
    provenance = json.loads(
        (run_e2e.INPUTS / "live_provenance.json").read_text(encoding="utf-8")
    )
    positive = next(
        judgment
        for row in deepseek["observations"]
        for judgment in row["judgments"]
        if judgment["decision"] == "positive"
    )
    positive["evidence_lines"] = [999_999]

    with pytest.raises(ValueError):
        run_e2e._validate_observations(pilot, deepseek, grok, provenance)

    deepseek = json.loads(
        (run_e2e.INPUTS / "deepseek_observations.json").read_text(encoding="utf-8")
    )
    provenance["source_artifacts"][0]["sha256"] = f"sha256:{'0' * 64}"
    with pytest.raises(ValueError, match="live provenance"):
        run_e2e._validate_observations(pilot, deepseek, grok, provenance)
