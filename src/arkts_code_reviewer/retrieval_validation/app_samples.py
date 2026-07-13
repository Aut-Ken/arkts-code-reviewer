from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

APP_SAMPLES_SCHEMA_VERSION = "applications-app-samples-v1"
APP_SAMPLES_SOURCE_ID = "applications-app-samples"
APP_SAMPLES_REVISION = "8255a2987f70317cc3a2a4d46044c6b55f092bb3"
APP_SAMPLES_CODE_COUNT = 17
APP_SAMPLES_GUIDANCE_COUNT = 9


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


class AppSampleEntry(_FrozenModel):
    path: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    line_count: Annotated[int, Field(ge=1)]
    kind: Literal["code", "sample_guidance"]
    case_role: Literal["positive", "negative", "neutral"]
    topics: tuple[str, ...]
    normative: Literal[False]

    @field_validator("topics", mode="before")
    @classmethod
    def parse_topics(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "App sample topics")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if value != value.strip() or "\\" in value:
            raise ValueError("App sample path must be a trimmed POSIX path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("App sample path must be relative and cannot traverse parents")
        if path.as_posix() != value:
            raise ValueError("App sample path must be normalized")
        return value

    @field_validator("topics")
    @classmethod
    def validate_topics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("App sample topics cannot be empty")
        if any(
            not topic
            or topic != topic.strip()
            or topic.lower() != topic
            or not all(char.isalnum() or char == "-" for char in topic)
            for topic in value
        ):
            raise ValueError("App sample topics must be lowercase kebab-case strings")
        if list(value) != sorted(set(value)):
            raise ValueError("App sample topics must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_kind_and_path(self) -> AppSampleEntry:
        if self.kind == "code" and not self.path.endswith(".ets"):
            raise ValueError("Code app samples must use an .ets path")
        if self.kind == "sample_guidance":
            name = PurePosixPath(self.path).name
            if not (name.startswith("README") and name.endswith(".md")):
                raise ValueError("Sample guidance must be a README Markdown path")
        return self


class AppSamplesManifest(_FrozenModel):
    schema_version: Literal["applications-app-samples-v1"]
    source_id: Literal["applications-app-samples"]
    revision: Literal["8255a2987f70317cc3a2a4d46044c6b55f092bb3"]
    entries: tuple[AppSampleEntry, ...]

    @field_validator("entries", mode="before")
    @classmethod
    def parse_entries(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "App sample entries")

    @model_validator(mode="after")
    def validate_manifest(self) -> AppSamplesManifest:
        paths = [entry.path for entry in self.entries]
        if paths != sorted(set(paths)):
            raise ValueError("App sample paths must be sorted and unique")
        code_count = sum(entry.kind == "code" for entry in self.entries)
        guidance_count = sum(entry.kind == "sample_guidance" for entry in self.entries)
        if code_count != APP_SAMPLES_CODE_COUNT or guidance_count != APP_SAMPLES_GUIDANCE_COUNT:
            raise ValueError(
                "App samples v1 must contain exactly "
                f"{APP_SAMPLES_CODE_COUNT} code and {APP_SAMPLES_GUIDANCE_COUNT} guidance entries"
            )
        return self


@dataclass(frozen=True)
class VerifiedAppSamplesCheckout:
    source_id: str
    revision: str
    checkout_root: Path
    file_count: int
    code_count: int
    guidance_count: int


def load_app_samples_manifest(path: Path) -> AppSamplesManifest:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        return AppSamplesManifest.model_validate(payload)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        _DuplicateKeyError,
    ) as exc:
        raise ValueError(f"invalid applications_app_samples manifest {path}: {exc}") from exc


def _run_git(checkout_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(checkout_root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot inspect applications_app_samples git checkout: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValueError(f"cannot inspect applications_app_samples git checkout: {detail}")
    return completed.stdout.strip()


def _verify_git_checkout(checkout_root: Path, manifest: AppSamplesManifest) -> None:
    top_level_text = _run_git(checkout_root, "rev-parse", "--show-toplevel")
    try:
        top_level = Path(top_level_text).resolve(strict=True)
    except OSError as exc:
        raise ValueError("applications_app_samples git top level does not exist") from exc
    if top_level != checkout_root:
        raise ValueError("checkout root must be the applications_app_samples git top level")

    head = _run_git(checkout_root, "rev-parse", "HEAD")
    if head != manifest.revision:
        raise ValueError(
            f"applications_app_samples revision mismatch: expected {manifest.revision}, got {head}"
        )
    status = _run_git(checkout_root, "status", "--porcelain", "--untracked-files=all")
    if status:
        raise ValueError("applications_app_samples checkout must be clean")


def verify_checkout(
    manifest: AppSamplesManifest,
    checkout_root: Path,
) -> VerifiedAppSamplesCheckout:
    try:
        root = checkout_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            f"applications_app_samples checkout does not exist: {checkout_root}"
        ) from exc
    if not root.is_dir():
        raise ValueError("applications_app_samples checkout root must be a directory")

    _verify_git_checkout(root, manifest)

    for entry in manifest.entries:
        candidate = root.joinpath(*PurePosixPath(entry.path).parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"missing applications_app_samples file: {entry.path}") from exc
        if not resolved.is_relative_to(root) or candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"unsafe applications_app_samples file: {entry.path}")
        try:
            raw = candidate.read_bytes()
            raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(
                f"cannot read UTF-8 applications_app_samples file: {entry.path}"
            ) from exc
        actual_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if actual_hash != entry.sha256:
            raise ValueError(
                f"applications_app_samples hash mismatch for {entry.path}: "
                f"expected {entry.sha256}, got {actual_hash}"
            )
        actual_line_count = len(raw.splitlines())
        if actual_line_count != entry.line_count:
            raise ValueError(
                f"applications_app_samples line count mismatch for {entry.path}: "
                f"expected {entry.line_count}, got {actual_line_count}"
            )

    return VerifiedAppSamplesCheckout(
        source_id=manifest.source_id,
        revision=manifest.revision,
        checkout_root=root,
        file_count=len(manifest.entries),
        code_count=APP_SAMPLES_CODE_COUNT,
        guidance_count=APP_SAMPLES_GUIDANCE_COUNT,
    )


__all__ = [
    "APP_SAMPLES_CODE_COUNT",
    "APP_SAMPLES_GUIDANCE_COUNT",
    "APP_SAMPLES_REVISION",
    "APP_SAMPLES_SCHEMA_VERSION",
    "APP_SAMPLES_SOURCE_ID",
    "AppSampleEntry",
    "AppSamplesManifest",
    "VerifiedAppSamplesCheckout",
    "load_app_samples_manifest",
    "verify_checkout",
]
