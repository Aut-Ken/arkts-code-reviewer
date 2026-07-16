"""Standard-library-only Git-seal preflight for Tag Truth v2 provenance.

This module intentionally imports no project package.  It captures the five review
artifacts from a clean, exact Git seal before the caller is allowed to import the
typed provenance verifier.  The returned bytes are the only artifact bytes that the
typed layer should consume.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

_FULL_GIT_ID = re.compile(r"^[0-9a-f]{40}$")
_EXPECTED_ROLES = (
    "selection",
    "review_packet",
    "review_receipt",
    "review_receipt",
    "consensus",
)
_PROJECT_MODULE_PREFIX = "arkts_code_reviewer"
_TYPED_VERIFIER_CLOSURE = (
    "src/arkts_code_reviewer/__init__.py",
    "src/arkts_code_reviewer/feature_routing_validation/__init__.py",
    "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2.py",
    "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_selection.py",
    "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_review.py",
    "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_provenance.py",
    "tests/evaluation/tag_retrieval/manifest.json",
    "tools/tag_truth_v2_seal_preflight.py",
    "tools/verify_tag_truth_v2_git_seal.py",
)

ArtifactRole = Literal[
    "selection",
    "review_packet",
    "review_receipt",
    "consensus",
]


class SealPreflightError(ValueError):
    """Raised when the worktree cannot prove the requested immutable seal."""


class _DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CapturedSealedArtifact:
    """One regular artifact captured once from the verified sealed worktree."""

    role: ArtifactRole
    path: str
    raw_bytes: bytes
    git_blob_id: str


@dataclass(frozen=True, slots=True)
class TagTruthV2SealPreflightResult:
    """Git identities and artifact bytes established before project imports."""

    seal_revision: str
    seal_tree_id: str
    candidate_commit: str
    artifacts: tuple[CapturedSealedArtifact, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


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
        raise SealPreflightError(f"cannot inspect Tag Truth v2 seal: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise SealPreflightError(
            f"cannot inspect Tag Truth v2 seal: {detail or 'git command failed'}"
        )
    return completed.stdout


def _run_git_text(root: Path, *arguments: str) -> str:
    raw = _run_git_bytes(root, *arguments)
    try:
        return raw.decode("utf-8").strip()
    except UnicodeError as exc:
        raise SealPreflightError("Git returned non-UTF-8 seal metadata") from exc


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
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
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            check=False,
            capture_output=True,
            env=_git_environment(),
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SealPreflightError(f"cannot inspect Tag Truth v2 seal ancestry: {exc}") from exc
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    raise SealPreflightError(
        f"cannot inspect Tag Truth v2 seal ancestry: {detail or 'git command failed'}"
    )


def _assert_project_modules_not_loaded() -> None:
    loaded = sorted(
        name
        for name in sys.modules
        if name == _PROJECT_MODULE_PREFIX or name.startswith(f"{_PROJECT_MODULE_PREFIX}.")
    )
    if loaded:
        raise SealPreflightError(
            f"project modules loaded before standard-library Git-seal preflight: {loaded!r}"
        )


def _repository_root(repository_root: str | Path) -> Path:
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise SealPreflightError(
            f"project repository root is unavailable: {repository_root}"
        ) from exc
    if not root.is_dir():
        raise SealPreflightError("project repository root must be a directory")
    top_level = _run_git_text(root, "rev-parse", "--show-toplevel")
    try:
        resolved_top_level = Path(top_level).resolve(strict=True)
    except OSError as exc:
        raise SealPreflightError("Git top-level path is unavailable") from exc
    if resolved_top_level != root:
        raise SealPreflightError("project repository root must be the Git top level")
    return root


def _reject_git_grafts(root: Path) -> None:
    common_dir_value = _run_git_text(root, "rev-parse", "--git-common-dir")
    common_dir = Path(common_dir_value)
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    try:
        common_dir = common_dir.resolve(strict=True)
    except OSError as exc:
        raise SealPreflightError("Git common directory is unavailable") from exc
    grafts = common_dir / "info" / "grafts"
    try:
        grafts.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SealPreflightError("cannot inspect legacy Git grafts") from exc
    raise SealPreflightError("legacy Git grafts are forbidden for Tag Truth v2 seals")


def _verify_seal(root: Path, seal_revision: str) -> tuple[str, str]:
    if _FULL_GIT_ID.fullmatch(seal_revision) is None:
        raise SealPreflightError("seal revision must be an exact full lowercase 40-character ID")
    seal = _run_git_text(root, "rev-parse", "--verify", f"{seal_revision}^{{commit}}")
    if seal != seal_revision:
        raise SealPreflightError("seal revision does not resolve to its exact commit identity")
    head = _run_git_text(root, "rev-parse", "--verify", "HEAD^{commit}")
    if head != seal:
        raise SealPreflightError("Tag Truth v2 verification requires HEAD exactly at the seal")
    if _run_git_bytes(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise SealPreflightError(
            "Tag Truth v2 seal requires a clean tracked and untracked worktree"
        )
    seal_tree_id = _run_git_text(root, "rev-parse", "--verify", f"{seal}^{{tree}}")
    if _FULL_GIT_ID.fullmatch(seal_tree_id) is None:
        raise SealPreflightError("seal tree identity is not a full lowercase Git object ID")
    return seal, seal_tree_id


def _resolve_regular_artifact(root: Path, supplied: str | Path) -> tuple[Path, str]:
    supplied_path = os.fspath(supplied)
    if not isinstance(supplied_path, str):
        raise SealPreflightError("sealed artifact path must be text")
    if (
        supplied_path != supplied_path.strip()
        or "\\" in supplied_path
        or any(ord(character) < 32 or ord(character) == 127 for character in supplied_path)
    ):
        raise SealPreflightError(f"sealed artifact path is not a safe normalized path: {supplied}")
    relative = PurePosixPath(supplied_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != supplied_path
    ):
        raise SealPreflightError(
            f"sealed artifact path must be normalized and repository-relative: {supplied}"
        )
    relative_path = relative.as_posix()
    lexical = root.joinpath(*relative.parts)

    current = root
    try:
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise SealPreflightError(f"sealed artifact cannot use symlinks: {relative_path}")
        metadata = lexical.stat(follow_symlinks=False)
    except OSError as exc:
        raise SealPreflightError(f"sealed artifact is unavailable: {relative_path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SealPreflightError(f"sealed artifact must be a regular file: {relative_path}")
    try:
        if lexical.resolve(strict=True) != lexical:
            raise SealPreflightError(f"sealed artifact path is not canonical: {relative_path}")
    except OSError as exc:
        raise SealPreflightError(f"sealed artifact is unavailable: {relative_path}") from exc
    return lexical, relative_path


def _sealed_blob_id(root: Path, seal: str, relative_path: str) -> str:
    listing = _run_git_bytes(
        root,
        "ls-tree",
        "-z",
        "--full-tree",
        seal,
        "--",
        relative_path,
    )
    records = tuple(record for record in listing.split(b"\0") if record)
    if len(records) != 1:
        raise SealPreflightError(
            f"sealed artifact must be tracked exactly once at the seal: {relative_path}"
        )
    try:
        metadata, raw_path = records[0].split(b"\t", maxsplit=1)
        mode, object_type, object_id = metadata.split(b" ", maxsplit=2)
        listed_path = raw_path.decode("utf-8")
        blob_id = object_id.decode("ascii")
    except (UnicodeError, ValueError) as exc:
        raise SealPreflightError(
            f"invalid Git tree entry for sealed artifact: {relative_path}"
        ) from exc
    if listed_path != relative_path:
        raise SealPreflightError(f"Git tree path differs from sealed artifact: {relative_path}")
    if mode not in {b"100644", b"100755"} or object_type != b"blob":
        raise SealPreflightError(f"sealed artifact must be a tracked regular blob: {relative_path}")
    if _FULL_GIT_ID.fullmatch(blob_id) is None:
        raise SealPreflightError(f"sealed artifact blob identity is invalid: {relative_path}")
    return blob_id


def _verify_import_candidate_safety(root: Path) -> None:
    source_root = root / "src"
    package_root = source_root / "arkts_code_reviewer"
    validation_root = package_root / "feature_routing_validation"
    scanned_directories = (
        (source_root, (("arkts_code_reviewer", "arkts_code_reviewer"),)),
        (
            package_root,
            (
                ("__init__", "__init__.py"),
                ("feature_routing_validation", "feature_routing_validation"),
            ),
        ),
        (
            validation_root,
            (
                ("__init__", "__init__.py"),
                ("tag_truth_v2", "tag_truth_v2.py"),
                ("tag_truth_v2_selection", "tag_truth_v2_selection.py"),
                ("tag_truth_v2_review", "tag_truth_v2_review.py"),
                ("tag_truth_v2_provenance", "tag_truth_v2_provenance.py"),
                ("tag_truth_v2_near_duplicate", "tag_truth_v2_near_duplicate.py"),
            ),
        ),
    )
    for directory, protected_stems in scanned_directories:
        if directory.is_symlink() or not directory.is_dir():
            raise SealPreflightError(f"typed verifier package directory is unsafe: {directory}")
        for entry in directory.iterdir():
            if entry.is_symlink():
                raise SealPreflightError(f"typed verifier import candidate is a symlink: {entry}")
            if directory == source_root and entry.name != "arkts_code_reviewer":
                raise SealPreflightError(
                    f"unsealed top-level import candidate is forbidden: {entry}"
                )
            if entry.name == "__pycache__":
                raise SealPreflightError(
                    f"typed verifier import closure forbids bytecode cache: {entry}"
                )
            if any(
                entry.name != allowed_name
                and (entry.name == stem or entry.name.startswith(f"{stem}."))
                for stem, allowed_name in protected_stems
            ):
                raise SealPreflightError(
                    f"unsealed typed verifier import candidate is forbidden: {entry}"
                )


def _verify_typed_verifier_closure(root: Path, seal: str) -> None:
    _verify_import_candidate_safety(root)
    for relative_path in _TYPED_VERIFIER_CLOSURE:
        current_path, normalized_path = _resolve_regular_artifact(root, relative_path)
        _sealed_blob_id(root, seal, normalized_path)
        try:
            current = current_path.read_bytes()
        except OSError as exc:
            raise SealPreflightError(
                f"cannot read typed verifier closure file: {normalized_path}"
            ) from exc
        committed = _run_git_bytes(root, "show", f"{seal}:{normalized_path}")
        if current_path.is_symlink() or committed != current:
            raise SealPreflightError(f"typed verifier closure drifted: {normalized_path}")


def _capture_artifacts(
    root: Path,
    seal: str,
    artifact_paths: Sequence[tuple[str, str | Path]],
) -> tuple[CapturedSealedArtifact, ...]:
    roles = tuple(role for role, _path in artifact_paths)
    if roles != _EXPECTED_ROLES:
        raise SealPreflightError(
            "Git seal requires ordered artifacts: selection, review_packet, "
            "two review_receipts, consensus"
        )
    resolved = tuple(
        (role, *_resolve_regular_artifact(root, path)) for role, path in artifact_paths
    )
    relative_paths = tuple(relative_path for _role, _path, relative_path in resolved)
    if len(relative_paths) != 5 or len(set(relative_paths)) != 5:
        raise SealPreflightError("Git seal requires exactly five unique artifact paths")

    captured: list[CapturedSealedArtifact] = []
    for role, current_path, relative_path in resolved:
        blob_id = _sealed_blob_id(root, seal, relative_path)
        try:
            raw_bytes = current_path.read_bytes()
        except OSError as exc:
            raise SealPreflightError(f"cannot capture sealed artifact: {relative_path}") from exc
        committed = _run_git_bytes(root, "show", f"{seal}:{relative_path}")
        if current_path.is_symlink() or committed != raw_bytes:
            raise SealPreflightError(f"sealed artifact worktree bytes drifted: {relative_path}")
        captured.append(
            CapturedSealedArtifact(
                role=cast(ArtifactRole, role),
                path=relative_path,
                raw_bytes=raw_bytes,
                git_blob_id=blob_id,
            )
        )
    return tuple(captured)


def _candidate_commit(selection_raw: bytes) -> str:
    try:
        payload = json.loads(
            selection_raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise SealPreflightError(f"committed selection is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SealPreflightError("committed selection must be a JSON object")
    freeze = payload.get("candidate_freeze")
    if not isinstance(freeze, dict):
        raise SealPreflightError("committed selection candidate_freeze must be an object")
    candidate_commit = freeze.get("candidate_commit")
    if not isinstance(candidate_commit, str) or _FULL_GIT_ID.fullmatch(candidate_commit) is None:
        raise SealPreflightError(
            "committed selection candidate_commit must be a full lowercase Git identity"
        )
    return candidate_commit


def _verify_candidate_ancestry(root: Path, candidate_commit: str, seal: str) -> None:
    resolved = _run_git_text(
        root,
        "rev-parse",
        "--verify",
        f"{candidate_commit}^{{commit}}",
    )
    if resolved != candidate_commit:
        raise SealPreflightError("selection candidate_commit is not its exact commit identity")
    if candidate_commit == seal or not _is_ancestor(root, candidate_commit, seal):
        raise SealPreflightError("selection candidate_commit must be a strict ancestor of the seal")


def preflight_tag_truth_v2_git_seal(
    *,
    repository_root: str | Path,
    seal_revision: str,
    artifact_paths: Sequence[tuple[str, str | Path]],
) -> TagTruthV2SealPreflightResult:
    """Verify and capture the five committed review artifacts without project imports."""

    _assert_project_modules_not_loaded()
    root = _repository_root(repository_root)
    _reject_git_grafts(root)
    seal, seal_tree_id = _verify_seal(root, seal_revision)
    artifacts = _capture_artifacts(root, seal, artifact_paths)
    selection = next(artifact for artifact in artifacts if artifact.role == "selection")
    candidate_commit = _candidate_commit(selection.raw_bytes)
    _verify_candidate_ancestry(root, candidate_commit, seal)
    _verify_typed_verifier_closure(root, seal)
    return TagTruthV2SealPreflightResult(
        seal_revision=seal,
        seal_tree_id=seal_tree_id,
        candidate_commit=candidate_commit,
        artifacts=artifacts,
    )


__all__ = [
    "CapturedSealedArtifact",
    "SealPreflightError",
    "TagTruthV2SealPreflightResult",
    "preflight_tag_truth_v2_git_seal",
]
