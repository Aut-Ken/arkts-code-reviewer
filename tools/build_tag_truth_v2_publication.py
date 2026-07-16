#!/usr/bin/env python3
"""Publish one sealed Tag Truth v2 consensus without running a candidate."""

from __future__ import annotations

import sys

if not sys.flags.isolated or not sys.dont_write_bytecode:
    print(
        "Tag Truth v2 publication requires Python isolated no-bytecode mode (-I -B)",
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
    raw_bytes: bytes


class _CapturedExternalView(Protocol):
    raw_bytes: bytes


class _PublicationPreflightView(Protocol):
    seal_revision: str
    seal_tree_id: str
    candidate_commit: str
    artifacts: tuple[_CapturedArtifactView, ...]
    near_duplicate_policy: _CapturedPolicyView
    provenance: _CapturedExternalView
    screening: _CapturedExternalView


def _load_standard_library_preflight() -> Callable[..., _PublicationPreflightView]:
    namespace = runpy.run_path(str(REPO_ROOT / "tools/tag_truth_v2_publication_preflight.py"))
    preflight = namespace.get("preflight_tag_truth_v2_publication")
    if not callable(preflight):
        raise ValueError("standard-library Tag Truth v2 publication preflight is unavailable")
    return cast(Callable[..., _PublicationPreflightView], preflight)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild a sealed Tag Truth v2 review and near-duplicate chain, then publish "
            "only its human consensus. This command never imports or executes a Tag candidate."
        )
    )
    parser.add_argument("--selection", required=True)
    parser.add_argument("--packet", required=True)
    parser.add_argument("--receipt", action="append", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--seal-revision", required=True)
    parser.add_argument("--provenance-verification", type=Path, required=True)
    parser.add_argument("--near-duplicate-policy", required=True)
    parser.add_argument("--near-duplicate-verification", type=Path, required=True)
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
            "Tag Truth v2 publication requires exactly two --receipt files",
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
            near_duplicate_policy_path=cast(str, args.near_duplicate_policy),
            near_duplicate_verification_path=cast(
                Path,
                args.near_duplicate_verification,
            ),
        )

        root = root.resolve(strict=True)
        source_root = cast(Path, args.source_root).resolve(strict=True)
        sys.dont_write_bytecode = True
        _install_sealed_project_import_path(root)

        from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate import (
            parse_tag_truth_v2_near_duplicate_policy,
            parse_tag_truth_v2_near_duplicate_verification,
        )
        from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_provenance import (
            CapturedCommittedArtifact,
            parse_tag_truth_v2_provenance_verification,
        )
        from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_publication import (
            build_verified_tag_truth_v2_publication,
        )
        from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
            load_tag_truth_v2_development_exclusion_snapshot,
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
        near_duplicate_policy = parse_tag_truth_v2_near_duplicate_policy(
            sealed.near_duplicate_policy.raw_bytes
        )
        provenance = parse_tag_truth_v2_provenance_verification(sealed.provenance.raw_bytes)
        screening = parse_tag_truth_v2_near_duplicate_verification(sealed.screening.raw_bytes)

        publication = build_verified_tag_truth_v2_publication(
            repository_root=root,
            seal_revision=sealed.seal_revision,
            seal_tree_id=sealed.seal_tree_id,
            artifacts=artifacts,
            source_root=source_root,
            development_truth=development_truth,
            provenance=provenance,
            near_duplicate_policy=near_duplicate_policy,
            screening=screening,
        )
    except Exception as exc:
        print(f"Tag Truth v2 publication failed: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            publication.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if publication.publication_status == "published_consensus_not_qualified":
        return 0
    if publication.publication_status == "blocked_no_suite":
        return 1
    print("Tag Truth v2 publication returned an invalid status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
