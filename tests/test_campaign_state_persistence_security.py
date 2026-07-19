from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import arkts_code_reviewer.hybrid_analysis.campaign_live_smoke as campaign_live_smoke
from arkts_code_reviewer.hybrid_analysis.campaign_live_smoke import (
    REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT,
    AtomicCampaignPlanBudgetLedger,
    CampaignSmokePreflightError,
    build_local_campaign_egress_approval,
    build_local_campaign_plan_reservations,
    build_repository_synthetic_campaign_bundle,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationError,
)


def _ledger(state_dir: Path) -> AtomicCampaignPlanBudgetLedger:
    bundle = build_repository_synthetic_campaign_bundle()
    caps = bundle.caps
    approval = build_local_campaign_egress_approval(
        bundle,
        approved_campaign_id=bundle.case.campaign_id,
        approved_plan_set_digest=bundle.case.plan_set_digest,
        cap_units=caps.max_units,
        cap_total_attempts=caps.max_total_attempts,
        cap_total_wire_body_bytes=caps.max_total_wire_body_bytes,
        cap_total_output_tokens=caps.max_total_output_tokens,
        cap_total_response_bytes=caps.max_total_response_bytes,
        cap_campaign_wall_clock_ms=caps.campaign_wall_clock_cap_ms,
        acknowledgement=REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT,
    )
    reservation = build_local_campaign_plan_reservations(
        bundle,
        approval=approval,
    )[0]
    plan_by_id = {unit.plan.plan_id: unit.plan for unit in bundle.campaign.units}
    return AtomicCampaignPlanBudgetLedger(
        bundle=bundle,
        reservation=reservation,
        expected_plan=plan_by_id[reservation.plan_id],
        state_dir=state_dir,
    )


def _consume(ledger: AtomicCampaignPlanBudgetLedger) -> None:
    plan = ledger._expected_plan  # noqa: SLF001 - focused security contract test
    reservation = ledger._reservation  # noqa: SLF001 - focused security contract test
    ledger.consume_one_attempt_reservation(
        plan=plan,
        reservation_id=reservation.reservation_id,
    )


def test_preflight_rejects_symlink_and_non_private_state_directories(
    tmp_path: Path,
) -> None:
    private_target = tmp_path / "private-target"
    private_target.mkdir(mode=0o700)
    symlink_state = tmp_path / "symlink-state"
    symlink_state.symlink_to(private_target, target_is_directory=True)

    with pytest.raises(CampaignSmokePreflightError) as symlink_rejected:
        campaign_live_smoke._validate_state_preflight(symlink_state, ())  # noqa: SLF001
    assert symlink_rejected.value.code == "unsafe_state_directory"

    public_state = tmp_path / "public-state"
    public_state.mkdir(mode=0o755)
    os.chmod(public_state, 0o755)
    with pytest.raises(CampaignSmokePreflightError) as public_rejected:
        campaign_live_smoke._validate_state_preflight(public_state, ())  # noqa: SLF001
    assert public_rejected.value.code == "unsafe_state_directory"


def test_preflight_rejects_a_symlink_parent_without_following_it(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(CampaignSmokePreflightError) as rejected:
        campaign_live_smoke._validate_state_preflight(  # noqa: SLF001
            parent_link / "state",
            (),
        )
    assert rejected.value.code == "unsafe_state_directory"
    assert not (real_parent / "state").exists()


def test_existing_marker_symlink_is_treated_as_consumed_without_following_it(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("unchanged", encoding="utf-8")
    ledger = _ledger(state_dir)
    ledger.marker_path.symlink_to(victim)

    with pytest.raises(CampaignSmokePreflightError) as rejected:
        campaign_live_smoke._validate_state_preflight(  # noqa: SLF001
            state_dir,
            (ledger._reservation,),  # noqa: SLF001 - security contract fixture
        )

    assert rejected.value.code == "campaign_plan_already_reserved"
    assert victim.read_text(encoding="utf-8") == "unchanged"


def test_marker_open_stays_bound_to_open_state_directory_during_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    moved_state = tmp_path / "state-opened-before-swap"
    attacker_state = tmp_path / "attacker-state"
    attacker_state.mkdir(mode=0o700)
    ledger = _ledger(state_dir)
    marker_name = ledger.marker_path.name
    real_open = os.open
    swapped = False

    def swapping_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and dir_fd is not None and os.fspath(path) == marker_name:
            os.rename(state_dir, moved_state)
            state_dir.symlink_to(attacker_state, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(campaign_live_smoke.os, "open", swapping_open)
    _consume(ledger)

    assert swapped is True
    assert (moved_state / marker_name).is_file()
    assert not (attacker_state / marker_name).exists()


def test_marker_and_containing_directory_are_fsynced_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path / "state")
    real_fsync = os.fsync
    synced_types: list[str] = []

    def recording_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        synced_types.append("directory" if stat.S_ISDIR(metadata.st_mode) else "file")
        real_fsync(descriptor)

    monkeypatch.setattr(campaign_live_smoke.os, "fsync", recording_fsync)
    _consume(ledger)

    # Creating the state directory may first sync its parent. The durable marker
    # contract itself is the final file sync followed by its containing directory.
    assert synced_types[-2:] == ["file", "directory"]


def test_directory_fsync_failure_fails_closed_after_marker_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    ledger = _ledger(state_dir)
    real_fsync = os.fsync

    def fail_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("synthetic directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(campaign_live_smoke.os, "fsync", fail_directory_fsync)
    with pytest.raises(AITagShadowAuthorizationError) as rejected:
        _consume(ledger)

    assert rejected.value.reason_code == "budget_not_reserved"
    assert ledger.marker_path.is_file()


def test_result_artifact_publish_does_not_follow_or_replace_existing_symlink(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    execution_result_id = "ai-tag-shadow-campaign-execution-result:sha256:" + "a" * 64
    artifact_name = campaign_live_smoke._campaign_result_artifact_name(  # noqa: SLF001
        execution_result_id
    )
    victim = tmp_path / "victim"
    victim.write_text("unchanged", encoding="utf-8")
    (state_dir / artifact_name).symlink_to(victim)
    summary = campaign_live_smoke._seal_campaign_run_summary(  # noqa: SLF001
        {
            "schema_version": (
                campaign_live_smoke.AI_TAG_CAMPAIGN_LIVE_SMOKE_SUMMARY_SCHEMA_VERSION
            ),
            "mode": "live_shadow_campaign_result",
            "execution_result_id": execution_result_id,
            "result_artifact_name": artifact_name,
        }
    )

    with pytest.raises(FileExistsError):
        campaign_live_smoke._persist_campaign_run_summary(  # noqa: SLF001
            state_dir=state_dir,
            summary=summary,
            execution_result_id=execution_result_id,
        )

    assert victim.read_text(encoding="utf-8") == "unchanged"
    assert not tuple(state_dir.glob("*.tmp"))
