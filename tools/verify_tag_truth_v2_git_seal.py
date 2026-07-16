#!/usr/bin/env python3
"""Verify a sealed generic Tag Truth v2 review campaign without running a candidate."""

from __future__ import annotations

import sys

if not sys.flags.isolated:
    print(
        "Tag Truth v2 Git-seal verification requires Python isolated mode (-I)",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import runpy
import site
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, Protocol, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_TRUTH_RELATIVE_PATH = Path("tests/evaluation/tag_retrieval/manifest.json")


class _CapturedArtifactView(Protocol):
    role: str
    path: str
    raw_bytes: bytes
    git_blob_id: str


class _SealPreflightView(Protocol):
    seal_revision: str
    seal_tree_id: str
    candidate_commit: str
    artifacts: tuple[_CapturedArtifactView, ...]


def _load_standard_library_preflight() -> Callable[..., _SealPreflightView]:
    namespace = runpy.run_path(str(REPO_ROOT / "tools/tag_truth_v2_seal_preflight.py"))
    preflight = namespace.get("preflight_tag_truth_v2_git_seal")
    if not callable(preflight):
        raise ValueError("standard-library Tag Truth v2 Git-seal preflight is unavailable")
    return cast(Callable[..., _SealPreflightView], preflight)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the committed selection, packet, two human receipts, and consensus against "
            "an exact clean Git seal. This command never imports or executes a Tag candidate."
        )
    )
    parser.add_argument("--selection", required=True)
    parser.add_argument("--packet", required=True)
    parser.add_argument("--receipt", action="append", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--seal-revision", required=True)
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
            "Tag Truth v2 Git-seal verification requires exactly two --receipt files",
            file=sys.stderr,
        )
        return 2

    root = Path(repository_root) if repository_root is not None else REPO_ROOT
    try:
        preflight_tag_truth_v2_git_seal = _load_standard_library_preflight()
        sealed = preflight_tag_truth_v2_git_seal(
            repository_root=root,
            seal_revision=cast(str, args.seal_revision),
            artifact_paths=(
                ("selection", cast(str, args.selection)),
                ("review_packet", cast(str, args.packet)),
                ("review_receipt", receipts[0]),
                ("review_receipt", receipts[1]),
                ("consensus", cast(str, args.consensus)),
            ),
        )

        root = root.resolve(strict=True)
        sys.dont_write_bytecode = True
        _install_sealed_project_import_path(root)
        from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_provenance import (
            CapturedCommittedArtifact,
            build_tag_truth_v2_provenance_verification,
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
        report = build_tag_truth_v2_provenance_verification(
            seal_revision=sealed.seal_revision,
            seal_tree_id=sealed.seal_tree_id,
            artifacts=artifacts,
            source_root=cast(Path, args.source_root),
            development_truth=development_truth,
        )
    except Exception as exc:
        print(f"Tag Truth v2 Git-seal verification failed: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if report.consensus_status == "complete":
        return 0
    if report.consensus_status == "unresolved":
        return 1
    print("Tag Truth v2 Git-seal verification returned an invalid status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
