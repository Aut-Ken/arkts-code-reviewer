"""Standard-library-only preflight for Tag Truth v2 consensus publication.

The preflight reuses the complete Stage-2D1 pre-import checks, then freezes the
publication verifier closure and captures the external near-duplicate report
before any project package can be imported.
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
_PUBLICATION_VERIFIER_CLOSURE = (
    "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_publication.py",
    "tools/tag_truth_v2_publication_preflight.py",
    "tools/build_tag_truth_v2_publication.py",
)
_MAX_EXTERNAL_SCREENING_BYTES = 64 * 1024 * 1024


class PublicationPreflightError(ValueError):
    """Raised when immutable publication inputs cannot be established."""


class _CapturedArtifactView(Protocol):
    role: str
    path: str
    raw_bytes: bytes
    git_blob_id: str


class _CapturedPolicyView(Protocol):
    path: str
    raw_bytes: bytes
    git_blob_id: str


class _CapturedExternalView(Protocol):
    path: Path
    raw_bytes: bytes


class _NearDuplicatePreflightView(Protocol):
    seal_revision: str
    seal_tree_id: str
    candidate_commit: str
    artifacts: tuple[_CapturedArtifactView, ...]
    policy: _CapturedPolicyView
    provenance: _CapturedExternalView


@dataclass(frozen=True, slots=True)
class CapturedExternalScreening:
    """One regular non-symlink post-seal screening report captured once."""

    path: Path
    raw_bytes: bytes


@dataclass(frozen=True, slots=True)
class TagTruthV2PublicationPreflightResult:
    """All bytes and Git identities established before project imports."""

    seal_revision: str
    seal_tree_id: str
    candidate_commit: str
    artifacts: tuple[_CapturedArtifactView, ...]
    near_duplicate_policy: _CapturedPolicyView
    provenance: _CapturedExternalView
    screening: CapturedExternalScreening


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
        raise PublicationPreflightError(
            f"cannot inspect Tag Truth v2 publication inputs: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise PublicationPreflightError(
            f"cannot inspect Tag Truth v2 publication inputs: {detail or 'git command failed'}"
        )
    return completed.stdout


def _assert_project_modules_not_loaded() -> None:
    loaded = sorted(
        name
        for name in sys.modules
        if name == _PROJECT_MODULE_PREFIX or name.startswith(f"{_PROJECT_MODULE_PREFIX}.")
    )
    if loaded:
        raise PublicationPreflightError(
            f"project modules loaded before standard-library publication preflight: {loaded!r}"
        )


def _load_near_duplicate_preflight() -> Callable[..., _NearDuplicatePreflightView]:
    path = Path(__file__).resolve().with_name("tag_truth_v2_near_duplicate_preflight.py")
    try:
        namespace = runpy.run_path(str(path))
    except (OSError, RuntimeError, ValueError) as exc:
        raise PublicationPreflightError(
            "standard-library near-duplicate preflight is unavailable"
        ) from exc
    preflight = namespace.get("preflight_tag_truth_v2_near_duplicate_screen")
    if not callable(preflight):
        raise PublicationPreflightError("standard-library near-duplicate preflight is unavailable")
    return cast(Callable[..., _NearDuplicatePreflightView], preflight)


def _normalized_repository_path(value: str, context: str) -> str:
    if (
        value != value.strip()
        or not value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise PublicationPreflightError(f"{context} is not a safe normalized path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise PublicationPreflightError(f"{context} must be normalized and repository-relative")
    return value


def _regular_repository_file(root: Path, relative_path: str, context: str) -> Path:
    lexical = root.joinpath(*PurePosixPath(relative_path).parts)
    current = root
    try:
        for part in PurePosixPath(relative_path).parts:
            current /= part
            if current.is_symlink():
                raise PublicationPreflightError(f"{context} cannot use symlinks")
        metadata = lexical.stat(follow_symlinks=False)
    except OSError as exc:
        raise PublicationPreflightError(f"{context} is unavailable: {relative_path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise PublicationPreflightError(f"{context} must be a regular file")
    try:
        if lexical.resolve(strict=True) != lexical:
            raise PublicationPreflightError(f"{context} path is not canonical")
    except OSError as exc:
        raise PublicationPreflightError(f"{context} is unavailable: {relative_path}") from exc
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
        raise PublicationPreflightError(
            f"{context} must be tracked exactly once at {revision}: {relative_path}"
        )
    try:
        metadata, raw_path = records[0].split(b"\t", maxsplit=1)
        mode, object_type, object_id = metadata.split(b" ", maxsplit=2)
        listed_path = raw_path.decode("utf-8")
        blob_id = object_id.decode("ascii")
    except (UnicodeError, ValueError) as exc:
        raise PublicationPreflightError(f"invalid Git tree entry for {context}") from exc
    if listed_path != relative_path:
        raise PublicationPreflightError(f"Git tree path differs from {context}")
    if mode not in {b"100644", b"100755"} or object_type != b"blob":
        raise PublicationPreflightError(f"{context} must be a tracked regular blob")
    if _FULL_GIT_ID.fullmatch(blob_id) is None:
        raise PublicationPreflightError(f"{context} Git blob identity is invalid")
    return blob_id


def _verify_publication_verifier_closure(
    root: Path,
    *,
    candidate_commit: str,
    seal_revision: str,
) -> None:
    for relative_path in _PUBLICATION_VERIFIER_CLOSURE:
        normalized = _normalized_repository_path(
            relative_path,
            "publication verifier closure path",
        )
        current_path = _regular_repository_file(
            root,
            normalized,
            "publication verifier closure file",
        )
        candidate_blob = _git_blob_id(
            root,
            candidate_commit,
            normalized,
            "publication verifier closure file at candidate freeze",
        )
        seal_blob = _git_blob_id(
            root,
            seal_revision,
            normalized,
            "publication verifier closure file at seal",
        )
        if candidate_blob != seal_blob:
            raise PublicationPreflightError(
                f"publication verifier closure changed after candidate freeze: {normalized}"
            )
        try:
            current = current_path.read_bytes()
        except OSError as exc:
            raise PublicationPreflightError(
                f"cannot capture publication verifier closure file: {normalized}"
            ) from exc
        committed = _run_git_bytes(root, "show", f"{seal_revision}:{normalized}")
        if current_path.is_symlink() or current != committed:
            raise PublicationPreflightError(f"publication verifier closure drifted: {normalized}")


def _capture_external_screening(
    path: str | Path,
    *,
    repository_root: Path,
    provenance_path: Path,
) -> CapturedExternalScreening:
    supplied = Path(path)
    lexical = supplied if supplied.is_absolute() else Path.cwd() / supplied
    current = Path(lexical.anchor) if lexical.is_absolute() else Path.cwd()
    try:
        parts = lexical.parts[1:] if lexical.is_absolute() else lexical.parts
        for part in parts:
            current /= part
            if current.is_symlink():
                raise PublicationPreflightError("near-duplicate report cannot use symlinks")
        metadata = lexical.stat(follow_symlinks=False)
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise PublicationPreflightError(f"near-duplicate report is unavailable: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise PublicationPreflightError("near-duplicate report must be a regular file")
    if metadata.st_size > _MAX_EXTERNAL_SCREENING_BYTES:
        raise PublicationPreflightError("near-duplicate report exceeds 64 MiB")
    if resolved == repository_root or resolved.is_relative_to(repository_root):
        raise PublicationPreflightError(
            "near-duplicate report must remain external to the sealed project checkout"
        )
    if resolved == provenance_path:
        raise PublicationPreflightError(
            "provenance and near-duplicate reports must use distinct files"
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
            raise PublicationPreflightError("near-duplicate report must remain a regular file")
        if (opened_metadata.st_dev, opened_metadata.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            raise PublicationPreflightError("near-duplicate report changed during capture")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw_bytes = stream.read(_MAX_EXTERNAL_SCREENING_BYTES + 1)
    except OSError as exc:
        raise PublicationPreflightError("cannot capture near-duplicate report bytes") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw_bytes) > _MAX_EXTERNAL_SCREENING_BYTES:
        raise PublicationPreflightError("near-duplicate report exceeds 64 MiB")
    if lexical.is_symlink():
        raise PublicationPreflightError("near-duplicate report cannot be a symlink")
    return CapturedExternalScreening(path=resolved, raw_bytes=raw_bytes)


def preflight_tag_truth_v2_publication(
    *,
    repository_root: str | Path,
    seal_revision: str,
    artifact_paths: Sequence[tuple[str, str | Path]],
    provenance_verification_path: str | Path,
    near_duplicate_policy_path: str | Path,
    near_duplicate_verification_path: str | Path,
) -> TagTruthV2PublicationPreflightResult:
    """Capture every publication input before importing project code."""

    _assert_project_modules_not_loaded()
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise PublicationPreflightError(
            f"project repository root is unavailable: {repository_root}"
        ) from exc
    near_duplicate_preflight = _load_near_duplicate_preflight()
    sealed = near_duplicate_preflight(
        repository_root=root,
        seal_revision=seal_revision,
        artifact_paths=artifact_paths,
        provenance_verification_path=provenance_verification_path,
        policy_path=near_duplicate_policy_path,
    )
    _verify_publication_verifier_closure(
        root,
        candidate_commit=sealed.candidate_commit,
        seal_revision=sealed.seal_revision,
    )
    screening = _capture_external_screening(
        near_duplicate_verification_path,
        repository_root=root,
        provenance_path=sealed.provenance.path,
    )
    return TagTruthV2PublicationPreflightResult(
        seal_revision=sealed.seal_revision,
        seal_tree_id=sealed.seal_tree_id,
        candidate_commit=sealed.candidate_commit,
        artifacts=sealed.artifacts,
        near_duplicate_policy=sealed.policy,
        provenance=sealed.provenance,
        screening=screening,
    )


__all__ = [
    "CapturedExternalScreening",
    "PublicationPreflightError",
    "TagTruthV2PublicationPreflightResult",
    "preflight_tag_truth_v2_publication",
]
