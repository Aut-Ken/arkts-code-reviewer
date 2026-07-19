from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

import arkts_code_reviewer.hybrid_analysis.campaign_live_smoke as campaign_live_smoke
import arkts_code_reviewer.hybrid_analysis.repository_campaign_parser as campaign_parser
from arkts_code_reviewer.code_analysis import CodeSourceRef
from arkts_code_reviewer.hybrid_analysis.campaign_live_smoke import (
    build_repository_synthetic_campaign_bundle,
)
from arkts_code_reviewer.hybrid_analysis.repository_campaign_parser import (
    RepositorySyntheticCampaignFileParser,
    RepositorySyntheticCampaignParserError,
)


def _campaign_identity() -> tuple[str, str, str, int, tuple[str, ...]]:
    bundle = build_repository_synthetic_campaign_bundle()
    return (
        bundle.case.campaign_id,
        bundle.case.plan_set_digest,
        bundle.caps.caps_id,
        bundle.caps.max_total_wire_body_bytes,
        tuple(unit.plan.plan_id for unit in bundle.campaign.units),
    )


def test_fixed_campaign_ignores_parser_environment_and_never_spawns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _campaign_identity()
    monkeypatch.setenv("ARKTS_PARSER_NODE", "/hostile/node")
    monkeypatch.setenv("ARKTS_PARSER_SIDECAR", "/hostile/sidecar")
    monkeypatch.setenv("ARKTS_PARSER_TIMEOUT", "not-a-number")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-reach-a-parser-child")

    def forbidden_subprocess(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("the frozen Campaign must not execute a subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden_subprocess)
    assert _campaign_identity() == expected


def test_fixed_campaign_preserves_method_owners_and_exact_timer_scope() -> None:
    bundle = build_repository_synthetic_campaign_bundle()
    units = tuple(
        (
            unit.source_role,
            unit.unit_symbol,
            unit.unit_kind,
            unit.source_span.start_line,
            unit.source_span.end_line,
            unit.context_degraded,
        )
        for unit in bundle.trusted_upstream.analysis_result.review_units
    )
    assert units == (
        ("base", "CampaignProbe.first", "method", 4, 6, False),
        ("base", "CampaignProbe.second", "method", 7, 11, False),
        ("head", "CampaignProbe.first", "method", 4, 6, False),
        ("head", "CampaignProbe.second", "method", 7, 11, False),
    )
    cards = tuple(item.card for item in bundle.campaign.units)
    first_cards = tuple(
        card
        for card in cards
        if card.owner_summary.unit_owner is not None
        and card.owner_summary.unit_owner.qualified_name == "CampaignProbe.first"
    )
    second_cards = tuple(
        card
        for card in cards
        if card.owner_summary.unit_owner is not None
        and card.owner_summary.unit_owner.qualified_name == "CampaignProbe.second"
    )
    assert len(first_cards) == len(second_cards) == 2
    assert all(card.static_tags.exact == () for card in first_cards)
    assert all(card.static_tags.routing == ("has_timer",) for card in first_cards)
    assert all(card.facts.unit_exact.calls == ("console.info",) for card in first_cards)
    assert all(card.static_tags.exact == ("has_timer",) for card in second_cards)
    assert all(card.static_tags.routing == ("has_timer",) for card in second_cards)
    assert all(card.facts.unit_exact.apis == ("setTimeout",) for card in second_cards)


def test_frozen_parser_rejects_same_content_under_an_unapproved_source_ref() -> None:
    parser = RepositorySyntheticCampaignFileParser()
    source = campaign_live_smoke._FIXED_BASE_CODE  # noqa: SLF001
    source_hash = "sha256:" + hashlib.sha256(source.encode()).hexdigest()
    unapproved = CodeSourceRef.create(
        repository="different-repository",
        revision=campaign_parser.REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_REVISION,
        path=campaign_parser.REPOSITORY_SYNTHETIC_CAMPAIGN_PATH,
        content_hash=source_hash,
    )
    with pytest.raises(
        RepositorySyntheticCampaignParserError,
        match="source_ref_mismatch",
    ):
        parser.parse_file(unapproved, source)


def test_frozen_parser_fails_closed_on_asset_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tampered = tmp_path / "tampered.json"
    raw = campaign_parser._ASSET_PATH.read_bytes()  # noqa: SLF001
    tampered.write_bytes(raw[:-1] + (b" " if raw[-1:] != b" " else b"\n"))
    monkeypatch.setattr(campaign_parser, "_ASSET_PATH", tampered)
    with pytest.raises(
        RepositorySyntheticCampaignParserError,
        match="asset_identity_mismatch",
    ):
        RepositorySyntheticCampaignFileParser()


def test_frozen_parser_rejects_duplicate_asset_keys_after_identity_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    raw = b'{"base":{},"base":{},"head":{}}'
    duplicate.write_bytes(raw)
    monkeypatch.setattr(campaign_parser, "_ASSET_PATH", duplicate)
    monkeypatch.setattr(campaign_parser, "_ASSET_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(
        RepositorySyntheticCampaignParserError,
        match="asset_invalid",
    ):
        RepositorySyntheticCampaignFileParser()
