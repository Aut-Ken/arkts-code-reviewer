#!/usr/bin/env python3
"""Screen one sealed Tag Truth v2 campaign for near duplicates without a candidate run."""

from __future__ import annotations

import sys

if not sys.flags.isolated:
    print(
        "Tag Truth v2 near-duplicate screening requires Python isolated mode (-I)",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import os
import runpy
import site
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, Protocol, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_TRUTH_RELATIVE_PATH = Path("tests/evaluation/tag_retrieval/manifest.json")
PROJECT_REPOSITORY_SOURCE_ID = "arkts-code-reviewer"


def _remove_git_environment_overrides() -> None:
    """Prevent inherited Git routing/config variables from changing pinned reads."""

    for key in tuple(os.environ):
        if key.upper().startswith("GIT_"):
            del os.environ[key]
    os.environ.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
        }
    )


class _CapturedArtifactView(Protocol):
    role: str
    path: str
    raw_bytes: bytes
    git_blob_id: str


class _CapturedPolicyView(Protocol):
    path: str
    raw_bytes: bytes
    git_blob_id: str


class _CapturedProvenanceView(Protocol):
    path: Path
    raw_bytes: bytes


class _NearDuplicatePreflightView(Protocol):
    seal_revision: str
    seal_tree_id: str
    candidate_commit: str
    artifacts: tuple[_CapturedArtifactView, ...]
    policy: _CapturedPolicyView
    provenance: _CapturedProvenanceView


def _load_standard_library_preflight() -> Callable[..., _NearDuplicatePreflightView]:
    namespace = runpy.run_path(str(REPO_ROOT / "tools/tag_truth_v2_near_duplicate_preflight.py"))
    preflight = namespace.get("preflight_tag_truth_v2_near_duplicate_screen")
    if not callable(preflight):
        raise ValueError("standard-library near-duplicate preflight is unavailable")
    return cast(Callable[..., _NearDuplicatePreflightView], preflight)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify one sealed Tag Truth v2 review campaign and run the versioned shadow "
            "near-duplicate screen. This command never imports or executes a Tag candidate."
        )
    )
    parser.add_argument("--selection", required=True)
    parser.add_argument("--packet", required=True)
    parser.add_argument("--receipt", action="append", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--seal-revision", required=True)
    parser.add_argument("--provenance-verification", type=Path, required=True)
    parser.add_argument("--policy", required=True)
    return parser


def _trusted_site_packages() -> set[Path]:
    candidates = [*site.getsitepackages(), site.getusersitepackages()]
    trusted: set[Path] = set()
    for candidate in candidates:
        try:
            trusted.add(Path(candidate).resolve(strict=True))
        except OSError:
            continue
    return trusted


def _install_sealed_project_import_path(root: Path) -> None:
    source_root = (root / "src").resolve(strict=True)
    trusted_site_packages = _trusted_site_packages()
    retained: list[str] = []
    seen = {source_root}
    for entry in sys.path:
        candidate = Path(entry) if entry else Path.cwd()
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen:
            continue
        if (resolved == root or resolved.is_relative_to(root)) and (
            resolved not in trusted_site_packages
        ):
            continue
        seen.add(resolved)
        retained.append(str(resolved))
    sys.path[:] = [str(source_root), *retained]


def main(
    argv: Sequence[str] | None = None,
    *,
    repository_root: str | Path | None = None,
) -> int:
    args = _parser().parse_args(argv)
    receipts = cast(list[str], args.receipt)
    if len(receipts) != 2:
        print(
            "Tag Truth v2 near-duplicate screening requires exactly two --receipt files",
            file=sys.stderr,
        )
        return 2

    root = Path(repository_root) if repository_root is not None else REPO_ROOT
    try:
        _remove_git_environment_overrides()
        preflight = _load_standard_library_preflight()
        sealed = preflight(
            repository_root=root,
            seal_revision=cast(str, args.seal_revision),
            artifact_paths=(
                ("selection", cast(str, args.selection)),
                ("review_packet", cast(str, args.packet)),
                ("review_receipt", receipts[0]),
                ("review_receipt", receipts[1]),
                ("consensus", cast(str, args.consensus)),
            ),
            provenance_verification_path=cast(Path, args.provenance_verification),
            policy_path=cast(str, args.policy),
        )

        root = root.resolve(strict=True)
        source_root = cast(Path, args.source_root).resolve(strict=True)
        sys.dont_write_bytecode = True
        _install_sealed_project_import_path(root)

        from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate import (
            build_tag_truth_v2_near_duplicate_verification,
            parse_tag_truth_v2_near_duplicate_policy,
            scan_pinned_git_reference_inventory,
        )
        from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_provenance import (
            CapturedCommittedArtifact,
            build_tag_truth_v2_provenance_verification,
            parse_tag_truth_v2_provenance_verification,
            verify_tag_truth_v2_provenance_verification,
        )
        from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review import (
            parse_tag_truth_v2_consensus,
        )
        from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
            load_tag_truth_v2_development_exclusion_snapshot,
            parse_tag_truth_v2_review_packet,
            parse_tag_truth_v2_selection,
        )

        core_roles: dict[str, Literal["selection", "packet", "receipt", "consensus"]] = {
            "selection": "selection",
            "review_packet": "packet",
            "review_receipt": "receipt",
            "consensus": "consensus",
        }
        artifacts = tuple(
            CapturedCommittedArtifact(
                role=core_roles[artifact.role],
                path=artifact.path,
                raw_bytes=artifact.raw_bytes,
                git_blob_id=artifact.git_blob_id,
            )
            for artifact in sealed.artifacts
        )
        development_truth = load_tag_truth_v2_development_exclusion_snapshot(
            root / DEVELOPMENT_TRUTH_RELATIVE_PATH
        )
        rebuilt_provenance = build_tag_truth_v2_provenance_verification(
            seal_revision=sealed.seal_revision,
            seal_tree_id=sealed.seal_tree_id,
            artifacts=artifacts,
            source_root=source_root,
            development_truth=development_truth,
        )
        supplied_provenance = parse_tag_truth_v2_provenance_verification(
            sealed.provenance.raw_bytes
        )
        verify_tag_truth_v2_provenance_verification(
            supplied_provenance,
            seal_revision=sealed.seal_revision,
            seal_tree_id=sealed.seal_tree_id,
            artifacts=artifacts,
            source_root=source_root,
            development_truth=development_truth,
        )
        if supplied_provenance != rebuilt_provenance:
            raise ValueError(
                "supplied provenance verification differs from the rebuilt sealed campaign"
            )

        by_role: dict[str, list[_CapturedArtifactView]] = {}
        for artifact in sealed.artifacts:
            by_role.setdefault(artifact.role, []).append(artifact)
        selection = parse_tag_truth_v2_selection(by_role["selection"][0].raw_bytes)
        packet = parse_tag_truth_v2_review_packet(by_role["review_packet"][0].raw_bytes)
        consensus = parse_tag_truth_v2_consensus(by_role["consensus"][0].raw_bytes)
        policy = parse_tag_truth_v2_near_duplicate_policy(sealed.policy.raw_bytes)

        maximum_blob_bytes = policy.maximum_blob_bytes
        maximum_total_reference_bytes = policy.maximum_total_reference_bytes
        maximum_inventory_entries = policy.maximum_inventory_entries
        reference_inventories = (
            scan_pinned_git_reference_inventory(
                root,
                role="candidate_project",
                repository_source_id=PROJECT_REPOSITORY_SOURCE_ID,
                revision=selection.candidate_freeze.candidate_commit,
                expected_tree_id=None,
                included_paths=None,
                maximum_blob_bytes=maximum_blob_bytes,
                maximum_total_reference_bytes=maximum_total_reference_bytes,
                maximum_inventory_entries=maximum_inventory_entries,
            ),
            scan_pinned_git_reference_inventory(
                source_root,
                role="exposure",
                repository_source_id=selection.repository.source_id,
                revision=selection.candidate_freeze.exposure_revision,
                expected_tree_id=selection.candidate_freeze.exposure_tree_id,
                included_paths=None,
                maximum_blob_bytes=maximum_blob_bytes,
                maximum_total_reference_bytes=maximum_total_reference_bytes,
                maximum_inventory_entries=maximum_inventory_entries,
            ),
            scan_pinned_git_reference_inventory(
                source_root,
                role="development_truth",
                repository_source_id=development_truth.repository_source_id,
                revision=development_truth.repository_revision,
                expected_tree_id=None,
                included_paths=tuple(item.path for item in development_truth.sources),
                maximum_blob_bytes=maximum_blob_bytes,
                maximum_total_reference_bytes=maximum_total_reference_bytes,
                maximum_inventory_entries=maximum_inventory_entries,
            ),
        )
        report = build_tag_truth_v2_near_duplicate_verification(
            policy=policy,
            provenance=supplied_provenance,
            selection=selection,
            packet=packet,
            consensus=consensus,
            reference_inventories=reference_inventories,
        )
    except Exception as exc:
        print(f"Tag Truth v2 near-duplicate screening failed: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if (
        report.screening_outcome == "clean"
        and report.near_duplicate_qualification_status == "qualified"
    ):
        return 0
    if report.screening_outcome in {"clean", "potential_duplicate", "review_required"}:
        return 1
    print("Tag Truth v2 near-duplicate screen returned an invalid outcome", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
