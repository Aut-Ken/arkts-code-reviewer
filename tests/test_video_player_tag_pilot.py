from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from arkts_code_reviewer.hybrid_analysis.execution import (  # noqa: E402
    AITagResponseValidationError,
)
from E2E_test_example_1.video_player_tag_pilot import (  # noqa: E402
    PILOT_SAMPLE_CLASSIFICATION,
    build_video_player_tag_pilot,
    grok_blind_messages,
    render_video_player_tag_pilot_inspection,
    validate_grok_blind_output,
)

TOOL = REPOSITORY_ROOT / "tools" / "inspect_video_player_tag_pilot.py"
EXPECTED_CAMPAIGN_ID = (
    "ai-tag-shadow-campaign:sha256:"
    "85e98a9fb77d6c7b24a038616bbfe6122c13edb9bf8c7d6c8a0d328076a11b45"
)
EXPECTED_DEEPSEEK_REQUEST_SET_DIGEST = (
    "sha256:954f53f015305769028e2fdd39d696364f1dbbafc1da614484074975741da8e6"
)
EXPECTED_GROK_REQUEST_SET_DIGEST = (
    "sha256:5fa7f113f815bf46a82e32e4f50e577cddad92727c0ce818f0e2c4ee33f49b35"
)


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_walk_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_walk_keys(item) for item in value), set())
    return set()


def test_pilot_freezes_the_real_application_static_baseline() -> None:
    pilot = build_video_player_tag_pilot()
    baseline = pilot.inspection["static_baseline"]

    assert pilot.inspection["sample"]["classification"] == PILOT_SAMPLE_CLASSIFICATION
    assert pilot.inspection["sample"]["is_real_mr"] is False
    assert pilot.campaign.manifest.campaign_id == EXPECTED_CAMPAIGN_ID
    assert len(pilot.upstream.analysis_result.review_units) == 15
    assert baseline == {
        "feature_routing_id": (
            "feature-routing:sha256:"
            "7ec55c6c2dc336ceabe291ba59beece57dca5f57300915ec8c681302b597cd16"
        ),
        "unit_count": 15,
        "units_with_exact_tags": 7,
        "units_without_exact_tags": 8,
        "exact_tag_assignment_count": 9,
        "distinct_exact_tag_count": 3,
        "exact_tag_counts": {
            "has_async": 2,
            "has_interactive_component": 2,
            "has_timer": 5,
        },
        "exact_scope": "unit_exact",
        "routing_tag_assignment_count": 165,
        "distinct_routing_tag_count": 11,
        "routing_tag_counts": {
            "has_async": 15,
            "has_file_io": 15,
            "has_image": 15,
            "has_interactive_component": 15,
            "has_layout": 15,
            "has_lifecycle": 15,
            "has_media": 15,
            "has_resource_ref": 15,
            "has_state_management": 15,
            "has_text_display": 15,
            "has_timer": 15,
        },
        "routing_scope": "file_hint_not_unit_positive",
        "routing_signature_count": 1,
        "all_units_share_one_routing_signature": True,
    }


def test_pilot_plans_blind_same_semantic_input_without_egress() -> None:
    pilot = build_video_player_tag_pilot()
    inspection = pilot.inspection

    assert inspection["mode"] == "inspect_only"
    assert inspection["network_attempted"] is False
    assert inspection["credential_accessed"] is False
    assert inspection["contains_source_or_prompt_body"] is False
    deepseek = inspection["deepseek_campaign"]
    assert deepseek["campaign_id"] == EXPECTED_CAMPAIGN_ID
    assert deepseek["request_set_digest"] == EXPECTED_DEEPSEEK_REQUEST_SET_DIGEST
    assert deepseek["planned_attempt_cap_sum"] == 15
    assert deepseek["planned_output_token_cap_sum"] == 61_440
    assert deepseek["total_wire_body_size_bytes"] == 461_106
    assert deepseek["total_response_byte_cap_sum"] == 30_000_000
    assert deepseek["execution_policy"]["retry_count"] == 0
    grok = inspection["grok_blind_proxy_campaign"]
    assert grok["planned_attempt_cap_sum"] == 15
    assert grok["request_set_digest"] == EXPECTED_GROK_REQUEST_SET_DIGEST
    assert grok["planned_output_token_cap_sum"] is None
    assert grok["total_response_byte_cap_sum"] == 30_000_000
    assert grok["role"] == "blind_proxy_judge_not_human_truth"
    assert grok["result_use"] == "comparison_only_never_retrieval_input"
    assert grok["execution_policy"]["system_delivery"] == "system_prompt_override"
    assert grok["execution_policy"]["user_delivery"] == "prompt_file"
    assert grok["execution_policy"]["retry_count"] == 0
    assert len(inspection["units"]) == 15
    assert all(unit["card_truncated"] is False for unit in inspection["units"])

    for unit in pilot.campaign.units:
        model_payload = unit.envelope.user_payload.model_dump(mode="json")
        forbidden_keys = {
            "static_tags",
            "exact_tags",
            "routing_tags",
            "dimensions",
            "review_question_ids",
            "retrieval",
            "deepseek_result",
        }
        assert not (_walk_keys(model_payload) & forbidden_keys)
        grok_system, grok_user = grok_blind_messages(unit)
        assert grok_system == unit.envelope.wire_payload.messages[0].content
        assert grok_user == unit.envelope.wire_payload.messages[1].content
        assert "proxy judge" not in grok_system.lower()
        assert "proxy judge" not in grok_user.lower()
        assert '"static_tags"' not in grok_user
        assert '"exact_tags"' not in grok_user
        assert '"routing_tags"' not in grok_user


def test_grok_proxy_output_uses_the_same_strict_local_semantic_validation() -> None:
    unit = build_video_player_tag_pilot().campaign.units[0]
    valid = {
        "judgments": [
            {
                "tag_id": contract.tag_id,
                "decision": "not_supported",
                "evidence_lines": [],
                "reason_code": "no_support_in_complete_view",
                "reason": None,
            }
            for contract in unit.request.tag_contract_views
        ]
    }
    content = json.dumps(valid, ensure_ascii=False, separators=(",", ":"))
    assert len(validate_grok_blind_output(unit, content)) == 24

    missing = {"judgments": valid["judgments"][:-1]}
    with pytest.raises(AITagResponseValidationError, match="complete requested taxonomy"):
        validate_grok_blind_output(unit, json.dumps(missing))

    out_of_range = json.loads(content)
    out_of_range["judgments"][0] = {
        "tag_id": out_of_range["judgments"][0]["tag_id"],
        "decision": "positive",
        "evidence_lines": [999_999],
        "reason_code": "direct_unit_semantic_evidence",
        "reason": "Visible semantics support this Tag.",
    }
    with pytest.raises(AITagResponseValidationError, match="outside the Model View"):
        validate_grok_blind_output(unit, json.dumps(out_of_range))


def test_pilot_is_deterministic_and_cli_does_not_need_secrets_or_database() -> None:
    first = build_video_player_tag_pilot().inspection
    second = build_video_player_tag_pilot().inspection
    assert first == second

    tampered = dict(first)
    tampered["mode"] = "live"
    with pytest.raises(ValueError, match="unsupported"):
        render_video_player_tag_pilot_inspection(tampered)

    env = dict(os.environ)
    env.pop("ARKTS_RETRIEVAL_DATABASE_URL", None)
    env.pop("DATABASE_URL", None)
    env["DEEPSEEK_API_KEY"] = "must-not-be-read-by-inspection"
    completed = subprocess.run(
        [sys.executable, str(TOOL), "--compact"],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == first
    rendered = completed.stdout
    assert "DEEPSEEK_API_KEY" not in rendered
    assert "must-not-be-read-by-inspection" not in rendered
    assert "wire_body_json" not in rendered
    assert "AI Tag analysis system contract" not in rendered
    assert "connection.createNetConnection" not in rendered
