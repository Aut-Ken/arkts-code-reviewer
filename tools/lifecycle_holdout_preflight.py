"""Standard-library-only preflight for the formal lifecycle holdout runner.

This module must not import ``arkts_code_reviewer``. It verifies the sealed code and
environment before Python is allowed to execute any candidate or evaluation package module.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

CANDIDATE_COMMIT = "9b7a828449cbe760ce9374d222f75c48b6f5c852"
CANDIDATE_FINGERPRINT = (
    "feature-config:sha256:844418e3d7938c816fd3b64b62cdae3d1753d286d50a6a103406838ed6db01e7"
)
CORPUS_EXPOSURE_REVISION = "8255a2987f70317cc3a2a4d46044c6b55f092bb3"

HARNESS_PATHS = (
    "pyproject.toml",
    "src/arkts_code_reviewer/retrieval_validation/lifecycle_blind_holdout.py",
    "src/arkts_code_reviewer/retrieval_validation/lifecycle_blind_holdout_evaluation.py",
    "src/arkts_code_reviewer/retrieval_validation/tag_retrieval_fixture.py",
    "tests/evaluation/lifecycle_blind_holdout_v1/review_policy.md",
    "tests/evaluation/lifecycle_blind_holdout_v1/selection_policy.md",
    "tests/evaluation/lifecycle_blind_holdout_v1/tag_contract.md",
    "tests/evaluation/tag_retrieval/manifest.json",
    "tools/build_lifecycle_blind_consensus.py",
    "tools/build_lifecycle_blind_review_packet.py",
    "tools/evaluate_lifecycle_owner_role_holdout.py",
    "tools/lifecycle_holdout_preflight.py",
    "tools/seal_lifecycle_blind_review_receipt.py",
    "tools/seal_lifecycle_blind_selection.py",
)

_STATIC_RUNTIME_PATHS = {
    "config/dimensions.yaml",
    "config/tags.yaml",
    "sidecars/arkts-parser/.node-version",
    "sidecars/arkts-parser/package-lock.json",
    "sidecars/arkts-parser/package.json",
    "sidecars/arkts-parser/parse_arkts.js",
    "tests/fixtures/feature_routing/tag_config_lifecycle_owner_role_shadow_v1.yaml",
}
_FORBIDDEN_MODULE_PREFIXES = (
    "arkts_code_reviewer.code_analysis",
    "arkts_code_reviewer.feature_routing",
    "arkts_code_reviewer.retrieval_validation",
)


class PreflightError(ValueError):
    pass


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _canonical_hash(prefix: str, payload: object) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{prefix}:sha256:{digest}"


def _bytes_hash(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _run_git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"cannot inspect formal holdout repository: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise PreflightError(f"cannot inspect formal holdout repository: {detail}")
    return completed.stdout.strip()


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"cannot inspect formal holdout ancestry: {exc}") from exc
    return completed.returncode == 0


def _git_bytes(root: Path, revision: str, relative_path: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "show", f"{revision}:{relative_path}"],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"sealed file is unavailable at {revision}: {relative_path}") from exc


def _safe_repository_file(root: Path, relative_path: str) -> Path:
    posix = PurePosixPath(relative_path)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise PreflightError(f"unsafe sealed path: {relative_path}")
    candidate = root.joinpath(*posix.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PreflightError(f"sealed file does not exist: {relative_path}") from exc
    if candidate.is_symlink() or not candidate.is_file() or not resolved.is_relative_to(root):
        raise PreflightError(f"unsafe sealed file: {relative_path}")
    return resolved


def _resolve_artifact(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PreflightError(f"formal holdout artifact does not exist: {candidate}") from exc
    if candidate.is_symlink() or not resolved.is_file() or not resolved.is_relative_to(root):
        raise PreflightError(f"formal holdout artifact is unsafe: {candidate}")
    return resolved


def _assert_candidate_modules_not_loaded() -> None:
    loaded = sorted(name for name in sys.modules if name.startswith(_FORBIDDEN_MODULE_PREFIXES))
    if loaded:
        raise PreflightError(
            f"candidate/evaluator modules loaded before standard-library preflight: {loaded!r}"
        )


def _dependency_site_packages_from_executable() -> Path:
    environment_root = Path(sys.executable).absolute().parent.parent
    configuration = environment_root / "pyvenv.cfg"
    if configuration.is_symlink() or not configuration.is_file():
        raise PreflightError("formal holdout interpreter must belong to an isolated virtualenv")
    candidates = (
        environment_root
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages",
        environment_root / "Lib/site-packages",
    )
    available = tuple(path.resolve(strict=True) for path in candidates if path.is_dir())
    if len(available) != 1:
        raise PreflightError(
            "formal holdout requires one identifiable isolated dependency site-packages"
        )
    return available[0]


def _verify_python_environment(root: Path) -> Path:
    if os.environ.get("PYTHONPATH"):
        raise PreflightError("formal holdout requires an empty PYTHONPATH before preflight")
    if not sys.flags.safe_path:
        raise PreflightError("formal holdout requires Python safe-path mode (-P)")
    if not sys.flags.no_site:
        raise PreflightError("formal holdout requires Python no-site mode (-S)")
    if not sys.flags.no_user_site:
        raise PreflightError("formal holdout requires PYTHONNOUSERSITE=1 at interpreter startup")
    if not sys.dont_write_bytecode:
        raise PreflightError("formal holdout requires bytecode writes disabled (-B)")
    for entry in sys.path:
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve(strict=True)
        except OSError:
            continue
        if resolved == root or resolved.is_relative_to(root):
            raise PreflightError(
                "formal holdout cannot expose repository paths before standard-library preflight"
            )
    dependency_site_packages = _dependency_site_packages_from_executable()
    if dependency_site_packages.is_relative_to(root):
        raise PreflightError(
            "formal holdout requires a dependency environment outside the sealed repository"
        )
    return dependency_site_packages


def _verify_project_import_closure(root: Path, seal: str) -> None:
    source_root = root / "src"
    observed: list[str] = []
    for path in source_root.rglob("*"):
        if path.is_symlink():
            raise PreflightError(f"formal holdout forbids project source symlink: {path}")
        if path.is_dir() and path.name == "__pycache__":
            raise PreflightError(f"formal holdout forbids project bytecode cache: {path}")
        if path.is_file():
            observed.append(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise PreflightError(f"formal holdout forbids irregular project source entry: {path}")

    tracked = tuple(
        _run_git(
            root,
            "ls-tree",
            "-r",
            "--name-only",
            seal,
            "--",
            "src",
        ).splitlines()
    )
    expected = tuple(
        sorted(
            {
                path
                for path in _candidate_runtime_paths(root)
                if path.startswith("src/arkts_code_reviewer/") and path.endswith(".py")
            }
            | {
                path
                for path in HARNESS_PATHS
                if path.startswith("src/arkts_code_reviewer/") and path.endswith(".py")
            }
        )
    )
    if tracked != expected:
        unexpected = sorted(set(tracked) - set(expected))
        missing = sorted(set(expected) - set(tracked))
        raise PreflightError(
            "sealed project import closure differs from the frozen runtime/harness: "
            f"unexpected={unexpected!r}, missing={missing!r}"
        )
    if tuple(sorted(observed)) != expected:
        unexpected = sorted(set(observed) - set(expected))
        missing = sorted(set(expected) - set(observed))
        raise PreflightError(
            "project worktree import closure differs from the frozen runtime/harness: "
            f"unexpected={unexpected!r}, missing={missing!r}"
        )


def _dependency_tree_fingerprint(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise PreflightError(f"runtime dependency tree is unavailable: {root}")
    payload: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            try:
                resolved = path.resolve(strict=True)
                target = os.readlink(path)
                raw = resolved.read_bytes()
            except OSError as exc:
                raise PreflightError(f"cannot read runtime dependency symlink: {path}") from exc
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise PreflightError(f"runtime dependency symlink escapes its tree: {path}")
            payload.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "symlink_target": target,
                    "target_content_sha256": _bytes_hash(raw),
                }
            )
        elif path.is_dir():
            continue
        elif path.is_file():
            payload.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "content_sha256": _bytes_hash(path.read_bytes()),
                }
            )
        else:
            raise PreflightError(f"unsafe runtime dependency entry: {path}")
    if not payload:
        raise PreflightError("runtime dependency tree cannot be empty")
    return _canonical_hash("lifecycle-sidecar-dependencies", payload)


def _runtime_environment(root: Path, dependency_site_packages: Path) -> dict[str, object]:
    forbidden = [
        name
        for name in ("ARKTS_PARSER_NODE", "ARKTS_PARSER_TIMEOUT", "NODE_OPTIONS")
        if os.environ.get(name)
    ]
    if forbidden:
        raise PreflightError(f"formal holdout forbids parser environment overrides: {forbidden!r}")
    node = shutil.which("node")
    if node is None:
        raise PreflightError("Node executable is unavailable")
    try:
        node_version = subprocess.run(
            [node, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        node_hash = _bytes_hash(Path(node).resolve(strict=True).read_bytes())
        available_packages = {
            str(distribution.metadata["Name"]).lower().replace("-", "_"): distribution.version
            for distribution in importlib.metadata.distributions(
                path=[str(dependency_site_packages)]
            )
            if distribution.metadata.get("Name")
        }
        required_packages = (
            ("annotated-types", "annotated_types"),
            ("pydantic", "pydantic"),
            ("pydantic_core", "pydantic_core"),
            ("ruamel.yaml", "ruamel.yaml"),
            ("ruamel.yaml.clib", "ruamel.yaml.clib"),
            ("typing-extensions", "typing_extensions"),
            ("typing-inspection", "typing_inspection"),
        )
        packages = tuple(
            sorted(
                f"{display_name}=={available_packages[normalized_name]}"
                for display_name, normalized_name in required_packages
            )
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise PreflightError("cannot inspect formal holdout runtime") from exc
    except KeyError as exc:
        raise PreflightError(f"required Python package is unavailable: {exc}") from exc
    return {
        "python_version": platform.python_version(),
        "python_packages": list(packages),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "node_version": node_version,
        "node_executable_sha256": node_hash,
        "sidecar_dependencies_fingerprint": _dependency_tree_fingerprint(
            root / "sidecars/arkts-parser/node_modules"
        ),
    }


def _candidate_runtime_paths(root: Path) -> tuple[str, ...]:
    tracked = _run_git(
        root,
        "ls-tree",
        "-r",
        "--name-only",
        CANDIDATE_COMMIT,
        "--",
        "src/arkts_code_reviewer",
    ).splitlines()
    return tuple(sorted({path for path in tracked if path.endswith(".py")} | _STATIC_RUNTIME_PATHS))


def _snapshot_map(value: object, context: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise PreflightError(f"{context} must be a JSON array")
    result: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "content_sha256"}:
            raise PreflightError(f"{context} contains an invalid snapshot")
        path = item.get("path")
        digest = item.get("content_sha256")
        if not isinstance(path, str) or not isinstance(digest, str):
            raise PreflightError(f"{context} contains an invalid snapshot value")
        result.append((path, digest))
    if result != sorted(set(result)):
        raise PreflightError(f"{context} paths must be sorted and unique")
    return tuple(result)


def _verify_snapshots(
    root: Path,
    revision: str,
    snapshots: tuple[tuple[str, str], ...],
    *,
    context: str,
) -> None:
    for path, digest in snapshots:
        committed = _git_bytes(root, revision, path)
        current = _safe_repository_file(root, path).read_bytes()
        if _bytes_hash(committed) != digest or _bytes_hash(current) != digest:
            raise PreflightError(f"{context} drift: {path}")


def _verify_freeze(
    root: Path,
    seal: str,
    selection: dict[str, object],
    dependency_site_packages: Path,
) -> None:
    freeze = selection.get("candidate_freeze")
    if not isinstance(freeze, dict):
        raise PreflightError("selection candidate_freeze must be an object")
    fixed = {
        "candidate_commit": CANDIDATE_COMMIT,
        "feature_config_fingerprint": CANDIDATE_FINGERPRINT,
        "candidate_corpus_exposure_revision": CORPUS_EXPOSURE_REVISION,
        "candidate_corpus_exposure_scope": "entire_tracked_repository",
    }
    if any(freeze.get(key) != value for key, value in fixed.items()):
        raise PreflightError(
            "selection candidate freeze identity differs from the approved candidate"
        )
    runtime = _snapshot_map(freeze.get("runtime_files"), "candidate runtime snapshots")
    if tuple(path for path, _digest in runtime) != _candidate_runtime_paths(root):
        raise PreflightError("candidate runtime snapshots do not cover the complete frozen tree")
    runtime_payload = [{"path": path, "content_sha256": digest} for path, digest in runtime]
    if freeze.get("runtime_bundle_fingerprint") != _canonical_hash(
        "lifecycle-candidate-runtime", runtime_payload
    ):
        raise PreflightError("candidate runtime bundle fingerprint mismatch")
    _verify_snapshots(root, CANDIDATE_COMMIT, runtime, context="candidate runtime")
    if freeze.get("runtime_environment") != _runtime_environment(
        root,
        dependency_site_packages,
    ):
        raise PreflightError("candidate runtime environment drift")

    harness_commit = freeze.get("evaluation_harness_commit")
    if not isinstance(harness_commit, str) or len(harness_commit) != 40:
        raise PreflightError("evaluation harness commit is invalid")
    if not _is_ancestor(root, CANDIDATE_COMMIT, harness_commit) or not _is_ancestor(
        root, harness_commit, seal
    ):
        raise PreflightError("evaluation harness ancestry is invalid")
    harness = _snapshot_map(freeze.get("evaluation_harness_files"), "harness snapshots")
    if tuple(path for path, _digest in harness) != HARNESS_PATHS:
        raise PreflightError("evaluation harness path set drift")
    harness_payload = [{"path": path, "content_sha256": digest} for path, digest in harness]
    if freeze.get("evaluation_harness_fingerprint") != _canonical_hash(
        "lifecycle-evaluation-harness", harness_payload
    ):
        raise PreflightError("evaluation harness fingerprint mismatch")
    _verify_snapshots(root, harness_commit, harness, context="evaluation harness")


def preflight_formal_holdout(
    *,
    repository_root: str | Path,
    seal_revision: str,
    artifact_paths: Sequence[str | Path],
) -> tuple[str, str]:
    _assert_candidate_modules_not_loaded()
    root = Path(repository_root).resolve(strict=True)
    if Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True) != root:
        raise PreflightError("formal holdout repository root must be the Git top level")
    dependency_site_packages = _verify_python_environment(root)
    if len(seal_revision) != 40 or any(
        character not in "0123456789abcdef" for character in seal_revision
    ):
        raise PreflightError("seal revision argument must be a full lowercase commit identity")
    seal = _run_git(root, "rev-parse", seal_revision)
    if seal != seal_revision:
        raise PreflightError("seal revision does not resolve to its exact commit identity")
    if not _is_ancestor(root, CANDIDATE_COMMIT, seal):
        raise PreflightError("holdout seal predates the candidate")
    if _run_git(root, "rev-parse", "HEAD") != seal:
        raise PreflightError("formal holdout requires HEAD exactly at the seal revision")
    if _run_git(root, "status", "--porcelain", "--untracked-files=all"):
        raise PreflightError("formal holdout repository must be completely clean")
    _verify_project_import_closure(root, seal)
    resolved = tuple(_resolve_artifact(root, path) for path in artifact_paths)
    relative = tuple(path.relative_to(root).as_posix() for path in resolved)
    if len(relative) != len(set(relative)) or len(relative) != 5:
        raise PreflightError("formal holdout requires five unique sealed artifacts")
    artifact_bytes = tuple(path.read_bytes() for path in resolved)
    for path, raw in zip(relative, artifact_bytes, strict=True):
        if _git_bytes(root, seal, path) != raw:
            raise PreflightError(f"sealed holdout artifact drift: {path}")
    try:
        selection = json.loads(
            artifact_bytes[0].decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise PreflightError(f"invalid sealed selection JSON: {exc}") from exc
    if not isinstance(selection, dict):
        raise PreflightError("sealed selection must be a JSON object")
    selection_id = selection.get("selection_id")
    identity = {key: value for key, value in selection.items() if key != "selection_id"}
    if selection_id != _canonical_hash("lifecycle-holdout-selection", identity):
        raise PreflightError("sealed selection self-hash mismatch")
    _verify_freeze(root, seal, selection, dependency_site_packages)
    return seal, str(dependency_site_packages)


__all__ = ["PreflightError", "preflight_formal_holdout"]
