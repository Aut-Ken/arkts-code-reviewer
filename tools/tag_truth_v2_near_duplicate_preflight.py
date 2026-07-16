"""Standard-library-only preflight for a sealed Tag Truth v2 duplicate screen.

This module deliberately imports no project package.  It delegates the five-artifact
Git checks to the Stage-2C preflight, then captures the immutable screening policy and
the post-seal provenance report before typed project code may be imported.
"""

from __future__ import annotations

import os
import re
import runpy
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

_FULL_GIT_ID = re.compile(r"^[0-9a-f]{40}$")
_PROJECT_MODULE_PREFIX = "arkts_code_reviewer"
NEAR_DUPLICATE_POLICY_RELATIVE_PATH = (
    "tests/evaluation/tag_truth_v2/near_duplicate_shadow_policy_v1.json"
)
_NEAR_DUPLICATE_VERIFIER_CLOSURE = (
    "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_near_duplicate.py",
    "tools/tag_truth_v2_near_duplicate_preflight.py",
    "tools/screen_tag_truth_v2_near_duplicates.py",
)
_MAX_EXTERNAL_PROVENANCE_BYTES = 16 * 1024 * 1024


class NearDuplicatePreflightError(ValueError):
    """Raised when the duplicate screen cannot establish immutable inputs."""


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


@dataclass(frozen=True, slots=True)
class CapturedNearDuplicatePolicy:
    """Policy bytes proven identical at candidate freeze, seal, and current HEAD."""

    path: str
    raw_bytes: bytes
    git_blob_id: str


@dataclass(frozen=True, slots=True)
class CapturedExternalProvenance:
    """One regular non-symlink post-seal provenance report captured once."""

    path: Path
    raw_bytes: bytes


@dataclass(frozen=True, slots=True)
class TagTruthV2NearDuplicatePreflightResult:
    """All bytes and Git identities established before project imports."""

    seal_revision: str
    seal_tree_id: str
    candidate_commit: str
    artifacts: tuple[_CapturedArtifactView, ...]
    policy: CapturedNearDuplicatePolicy
    provenance: CapturedExternalProvenance


def _git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_LITERAL_PATHSPECS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def _run_git_bytes(root: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.commitGraph=false",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                *arguments,
            ],
            check=False,
            capture_output=True,
            env=_git_environment(),
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NearDuplicatePreflightError(
            f"cannot inspect Tag Truth v2 duplicate-screen inputs: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise NearDuplicatePreflightError(
            f"cannot inspect Tag Truth v2 duplicate-screen inputs: {detail or 'git command failed'}"
        )
    return completed.stdout


def _assert_project_modules_not_loaded() -> None:
    loaded = sorted(
        name
        for name in sys.modules
        if name == _PROJECT_MODULE_PREFIX or name.startswith(f"{_PROJECT_MODULE_PREFIX}.")
    )
    if loaded:
        raise NearDuplicatePreflightError(
            f"project modules loaded before standard-library duplicate-screen preflight: {loaded!r}"
        )


def _load_seal_preflight() -> Callable[..., _SealPreflightView]:
    path = Path(__file__).resolve().with_name("tag_truth_v2_seal_preflight.py")
    try:
        namespace = runpy.run_path(str(path))
    except (OSError, RuntimeError, ValueError) as exc:
        raise NearDuplicatePreflightError(
            "standard-library Tag Truth v2 Git-seal preflight is unavailable"
        ) from exc
    preflight = namespace.get("preflight_tag_truth_v2_git_seal")
    if not callable(preflight):
        raise NearDuplicatePreflightError(
            "standard-library Tag Truth v2 Git-seal preflight is unavailable"
        )
    return cast(Callable[..., _SealPreflightView], preflight)


def _normalized_repository_path(value: str | Path, context: str) -> str:
    supplied = os.fspath(value)
    if not isinstance(supplied, str):
        raise NearDuplicatePreflightError(f"{context} must be text")
    if (
        supplied != supplied.strip()
        or not supplied
        or "\\" in supplied
        or any(ord(character) < 32 or ord(character) == 127 for character in supplied)
    ):
        raise NearDuplicatePreflightError(f"{context} is not a safe normalized path")
    path = PurePosixPath(supplied)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != supplied
    ):
        raise NearDuplicatePreflightError(f"{context} must be normalized and repository-relative")
    return supplied


def _regular_repository_file(root: Path, relative_path: str, context: str) -> Path:
    lexical = root.joinpath(*PurePosixPath(relative_path).parts)
    current = root
    try:
        for part in PurePosixPath(relative_path).parts:
            current /= part
            if current.is_symlink():
                raise NearDuplicatePreflightError(f"{context} cannot use symlinks")
        metadata = lexical.stat(follow_symlinks=False)
    except OSError as exc:
        raise NearDuplicatePreflightError(f"{context} is unavailable: {relative_path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise NearDuplicatePreflightError(f"{context} must be a regular file")
    try:
        if lexical.resolve(strict=True) != lexical:
            raise NearDuplicatePreflightError(f"{context} path is not canonical")
    except OSError as exc:
        raise NearDuplicatePreflightError(f"{context} is unavailable: {relative_path}") from exc
    return lexical


def _git_blob_id(root: Path, revision: str, relative_path: str, context: str) -> str:
    listing = _run_git_bytes(
        root,
        "ls-tree",
        "-z",
        "--full-tree",
        revision,
        "--",
        relative_path,
    )
    records = tuple(record for record in listing.split(b"\0") if record)
    if len(records) != 1:
        raise NearDuplicatePreflightError(
            f"{context} must be tracked exactly once at {revision}: {relative_path}"
        )
    try:
        metadata, raw_path = records[0].split(b"\t", maxsplit=1)
        mode, object_type, object_id = metadata.split(b" ", maxsplit=2)
        listed_path = raw_path.decode("utf-8")
        blob_id = object_id.decode("ascii")
    except (UnicodeError, ValueError) as exc:
        raise NearDuplicatePreflightError(f"invalid Git tree entry for {context}") from exc
    if listed_path != relative_path:
        raise NearDuplicatePreflightError(f"Git tree path differs from {context}")
    if mode not in {b"100644", b"100755"} or object_type != b"blob":
        raise NearDuplicatePreflightError(f"{context} must be a tracked regular blob")
    if _FULL_GIT_ID.fullmatch(blob_id) is None:
        raise NearDuplicatePreflightError(f"{context} Git blob identity is invalid")
    return blob_id


def _capture_policy(
    root: Path,
    *,
    candidate_commit: str,
    seal_revision: str,
    policy_path: str | Path,
) -> CapturedNearDuplicatePolicy:
    relative_path = _normalized_repository_path(policy_path, "near-duplicate policy path")
    if relative_path != NEAR_DUPLICATE_POLICY_RELATIVE_PATH:
        raise NearDuplicatePreflightError(
            "near-duplicate policy must use the frozen canonical repository path"
        )
    current_path = _regular_repository_file(root, relative_path, "near-duplicate policy")
    candidate_blob = _git_blob_id(
        root,
        candidate_commit,
        relative_path,
        "near-duplicate policy at candidate freeze",
    )
    seal_blob = _git_blob_id(
        root,
        seal_revision,
        relative_path,
        "near-duplicate policy at seal",
    )
    if candidate_blob != seal_blob:
        raise NearDuplicatePreflightError(
            "near-duplicate policy Git blob changed after candidate freeze"
        )
    try:
        raw_bytes = current_path.read_bytes()
    except OSError as exc:
        raise NearDuplicatePreflightError("cannot capture near-duplicate policy bytes") from exc
    committed = _run_git_bytes(root, "show", f"{seal_revision}:{relative_path}")
    if current_path.is_symlink() or raw_bytes != committed:
        raise NearDuplicatePreflightError("near-duplicate policy worktree bytes drifted from seal")
    return CapturedNearDuplicatePolicy(
        path=relative_path,
        raw_bytes=raw_bytes,
        git_blob_id=seal_blob,
    )


def _verify_near_duplicate_verifier_closure(
    root: Path,
    *,
    candidate_commit: str,
    seal_revision: str,
) -> None:
    for relative_path in _NEAR_DUPLICATE_VERIFIER_CLOSURE:
        current_path = _regular_repository_file(
            root,
            relative_path,
            "near-duplicate verifier closure file",
        )
        candidate_blob = _git_blob_id(
            root,
            candidate_commit,
            relative_path,
            "near-duplicate verifier closure file at candidate freeze",
        )
        seal_blob = _git_blob_id(
            root,
            seal_revision,
            relative_path,
            "near-duplicate verifier closure file at seal",
        )
        if candidate_blob != seal_blob:
            raise NearDuplicatePreflightError(
                f"near-duplicate verifier closure changed after candidate freeze: {relative_path}"
            )
        try:
            current = current_path.read_bytes()
        except OSError as exc:
            raise NearDuplicatePreflightError(
                f"cannot capture near-duplicate verifier closure file: {relative_path}"
            ) from exc
        committed = _run_git_bytes(root, "show", f"{seal_revision}:{relative_path}")
        if current_path.is_symlink() or current != committed:
            raise NearDuplicatePreflightError(
                f"near-duplicate verifier closure drifted: {relative_path}"
            )


def _capture_external_provenance(
    path: str | Path,
    *,
    repository_root: Path,
) -> CapturedExternalProvenance:
    supplied = Path(path)
    lexical = supplied if supplied.is_absolute() else Path.cwd() / supplied
    current = Path(lexical.anchor) if lexical.is_absolute() else Path.cwd()
    try:
        parts = lexical.parts[1:] if lexical.is_absolute() else lexical.parts
        for part in parts:
            current /= part
            if current.is_symlink():
                raise NearDuplicatePreflightError(
                    "provenance verification report cannot use symlinks"
                )
        metadata = lexical.stat(follow_symlinks=False)
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise NearDuplicatePreflightError(
            f"provenance verification report is unavailable: {path}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise NearDuplicatePreflightError("provenance verification report must be a regular file")
    if metadata.st_size > _MAX_EXTERNAL_PROVENANCE_BYTES:
        raise NearDuplicatePreflightError("provenance verification report exceeds 16 MiB")
    if resolved == repository_root or resolved.is_relative_to(repository_root):
        raise NearDuplicatePreflightError(
            "provenance verification report must remain external to the sealed project checkout"
        )
    descriptor: int | None = None
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(lexical, flags)
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            raise NearDuplicatePreflightError(
                "provenance verification report must remain a regular file"
            )
        if (opened_metadata.st_dev, opened_metadata.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            raise NearDuplicatePreflightError(
                "provenance verification report changed during capture"
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw_bytes = stream.read(_MAX_EXTERNAL_PROVENANCE_BYTES + 1)
    except OSError as exc:
        raise NearDuplicatePreflightError(
            "cannot capture provenance verification report bytes"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw_bytes) > _MAX_EXTERNAL_PROVENANCE_BYTES:
        raise NearDuplicatePreflightError("provenance verification report exceeds 16 MiB")
    if lexical.is_symlink():
        raise NearDuplicatePreflightError("provenance verification report cannot be a symlink")
    return CapturedExternalProvenance(path=resolved, raw_bytes=raw_bytes)


def preflight_tag_truth_v2_near_duplicate_screen(
    *,
    repository_root: str | Path,
    seal_revision: str,
    artifact_paths: Sequence[tuple[str, str | Path]],
    provenance_verification_path: str | Path,
    policy_path: str | Path,
) -> TagTruthV2NearDuplicatePreflightResult:
    """Capture every duplicate-screen input before importing project code."""

    _assert_project_modules_not_loaded()
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise NearDuplicatePreflightError(
            f"project repository root is unavailable: {repository_root}"
        ) from exc
    seal_preflight = _load_seal_preflight()
    sealed = seal_preflight(
        repository_root=root,
        seal_revision=seal_revision,
        artifact_paths=artifact_paths,
    )
    _verify_near_duplicate_verifier_closure(
        root,
        candidate_commit=sealed.candidate_commit,
        seal_revision=sealed.seal_revision,
    )
    policy = _capture_policy(
        root,
        candidate_commit=sealed.candidate_commit,
        seal_revision=sealed.seal_revision,
        policy_path=policy_path,
    )
    provenance = _capture_external_provenance(
        provenance_verification_path,
        repository_root=root,
    )
    return TagTruthV2NearDuplicatePreflightResult(
        seal_revision=sealed.seal_revision,
        seal_tree_id=sealed.seal_tree_id,
        candidate_commit=sealed.candidate_commit,
        artifacts=sealed.artifacts,
        policy=policy,
        provenance=provenance,
    )


__all__ = [
    "CapturedExternalProvenance",
    "CapturedNearDuplicatePolicy",
    "NearDuplicatePreflightError",
    "NEAR_DUPLICATE_POLICY_RELATIVE_PATH",
    "TagTruthV2NearDuplicatePreflightResult",
    "preflight_tag_truth_v2_near_duplicate_screen",
]
