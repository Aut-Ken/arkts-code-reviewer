from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CORPUS_SCHEMA_VERSION = "parser-corpus-v1"


@dataclass(frozen=True)
class SampleEntry:
    category: str
    path: str

    @property
    def sample_id(self) -> str:
        return f"{self.category}/{Path(self.path).name}"


@dataclass(frozen=True)
class CorpusManifest:
    schema_version: str
    suite_id: str
    suite_role: str
    source_id: str
    revision: str
    samples: tuple[SampleEntry, ...]


def load_manifest(manifest_path: Path) -> list[SampleEntry]:
    """Load sample entries for the GLM validation tools.

    The compatibility wrapper deliberately returns a list; deterministic corpus
    runners should use :func:`load_corpus_manifest` so they cannot discard source
    identity and revision metadata.
    """

    return list(load_corpus_manifest(manifest_path).samples)


def load_corpus_manifest(manifest_path: Path) -> CorpusManifest:
    value: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = _mapping(value, "manifest")
    expected_fields = {
        "schema_version",
        "suite_id",
        "suite_role",
        "engine",
        "source_id",
        "revision",
        "description",
        "samples",
    }
    if set(data) != expected_fields:
        missing = sorted(expected_fields - set(data))
        extra = sorted(set(data) - expected_fields)
        raise ValueError(f"manifest fields mismatch: missing={missing}, extra={extra}")
    schema_version = _string(data.get("schema_version"), "manifest.schema_version")
    if schema_version != CORPUS_SCHEMA_VERSION:
        raise ValueError(
            f"manifest.schema_version must be {CORPUS_SCHEMA_VERSION!r}, got {schema_version!r}"
        )

    raw_samples = data.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValueError("manifest.samples must be a non-empty array")
    _string(data.get("engine"), "manifest.engine")
    _string(data.get("description"), "manifest.description")
    samples: list[SampleEntry] = []
    seen_paths: set[str] = set()
    for index, value in enumerate(raw_samples):
        item = _mapping(value, f"manifest.samples[{index}]")
        if set(item) != {"category", "path"}:
            raise ValueError(
                f"manifest.samples[{index}] fields must be exactly ['category', 'path']"
            )
        category = _string(item.get("category"), f"manifest.samples[{index}].category")
        path = _string(item.get("path"), f"manifest.samples[{index}].path").replace("\\", "/")
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"manifest.samples[{index}].path must stay inside source root")
        if candidate.suffix != ".ets":
            raise ValueError(f"manifest.samples[{index}].path must end in .ets")
        if path in seen_paths:
            raise ValueError(f"duplicate manifest sample path: {path}")
        seen_paths.add(path)
        samples.append(SampleEntry(category=category, path=path))

    return CorpusManifest(
        schema_version=schema_version,
        suite_id=_string(data.get("suite_id"), "manifest.suite_id"),
        suite_role=_string(data.get("suite_role"), "manifest.suite_role"),
        source_id=_string(data.get("source_id"), "manifest.source_id"),
        revision=_git_revision(data.get("revision"), "manifest.revision"),
        samples=tuple(samples),
    )


def verify_corpus_checkout(engine_root: Path, manifest: CorpusManifest) -> str:
    """Verify only the manifest-selected paths against the pinned Git revision."""

    engine_root = engine_root.resolve()
    if not engine_root.is_dir():
        raise ValueError(f"corpus source root does not exist: {engine_root}")
    head = _run_git(engine_root, "rev-parse", "HEAD")
    if head != manifest.revision:
        raise ValueError(f"corpus revision mismatch: expected {manifest.revision}, got {head}")

    paths = [sample.path for sample in manifest.samples]
    tracked_paths = set(
        _run_git(
            engine_root,
            "ls-tree",
            "-r",
            "--name-only",
            manifest.revision,
            "--",
            *paths,
        ).splitlines()
    )
    missing_from_revision = sorted(set(paths) - tracked_paths)
    if missing_from_revision:
        raise ValueError(
            "manifest-selected corpus files are absent from the pinned revision: "
            + ", ".join(missing_from_revision[:5])
        )
    changed = _run_git(
        engine_root,
        "diff",
        "--name-only",
        manifest.revision,
        "--",
        *paths,
    )
    if changed:
        changed_paths = changed.splitlines()
        raise ValueError(
            "manifest-selected corpus files differ from the pinned revision: "
            + ", ".join(changed_paths[:5])
        )
    return head


def _run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"git {' '.join(args[:2])} failed: {detail}")
    return completed.stdout.strip()


def _mapping(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _git_revision(value: object, context: str) -> str:
    revision = _string(value, context)
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise ValueError(f"{context} must be a full lowercase Git commit")
    return revision


def select_samples(
    samples: list[SampleEntry],
    *,
    category: str | None = None,
    sample_id: str | None = None,
    limit: int | None = None,
) -> list[SampleEntry]:
    selected = samples
    if category:
        selected = [sample for sample in selected if sample.category == category]
    if sample_id:
        selected = [
            sample
            for sample in selected
            if sample.sample_id == sample_id or sample.path == sample_id or sample_id in sample.path
        ]
    if limit is not None:
        selected = selected[:limit]
    return selected
