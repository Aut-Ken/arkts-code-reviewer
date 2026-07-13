from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

SOURCE_REGISTRY_SCHEMA_VERSION = 1
SOURCE_BUNDLE_SCHEMA_VERSION = "source-bundle-v1"
SOURCE_REGISTRY_ENV = "ARKTS_SOURCE_REGISTRY"
DEFAULT_SOURCE_REGISTRY = Path("/home/autken/Code/arkts-knowledge/registry/sources.yaml")

SourceGroup = Literal["knowledge_source", "code_corpus", "analysis_tool"]
CheckoutMode = Literal["full", "sparse"]

_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_REMOTE_RE = re.compile(r"^https://[^\s]+$")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _normalize_setlike(value: Sequence[str], context: str) -> tuple[str, ...]:
    items = tuple(value)
    if any(not isinstance(item, str) or not item or item.strip() != item for item in items):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if len(items) != len(set(items)):
        raise ValueError(f"{context} must not contain duplicates")
    return tuple(sorted(items))


def _validate_relative_pattern(value: str, context: str) -> str:
    if not value or value.strip() != value or "\\" in value or "\0" in value:
        raise ValueError(f"{context} must be a trimmed POSIX pattern")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or "." in path.parts or ".." in path.parts:
        raise ValueError(f"{context} must stay below the source root")
    return value


class CheckoutProfile(_FrozenModel):
    mode: CheckoutMode
    include: tuple[str, ...] = ()
    profile: str | None = None

    @field_validator("include", mode="before")
    @classmethod
    def normalize_include(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("CheckoutProfile.include must be a sequence")
        return _normalize_setlike(value, "CheckoutProfile.include")

    @field_validator("include")
    @classmethod
    def validate_include(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _validate_relative_pattern(item, "CheckoutProfile.include")
        return value

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str | None) -> str | None:
        if value is not None and (not value or value.strip() != value):
            raise ValueError("CheckoutProfile.profile must be non-empty and trimmed")
        return value

    @model_validator(mode="after")
    def validate_mode(self) -> CheckoutProfile:
        if self.mode == "full" and (self.include or self.profile is not None):
            raise ValueError("full checkout must not declare sparse include/profile")
        if self.mode == "sparse" and not (self.include or self.profile):
            raise ValueError("sparse checkout requires include paths or a profile")
        return self


class IngestionProfile(_FrozenModel):
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    execute_repository_scripts: Literal[False]
    index_as_normative_knowledge: bool

    @field_validator("include", "exclude", mode="before")
    @classmethod
    def normalize_patterns(cls, value: object, info: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError(f"IngestionProfile.{info.field_name} must be a sequence")
        return _normalize_setlike(value, f"IngestionProfile.{info.field_name}")

    @field_validator("include", "exclude")
    @classmethod
    def validate_patterns(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        for item in value:
            _validate_relative_pattern(item, f"IngestionProfile.{info.field_name}")
        return value

    @property
    def profile_hash(self) -> str:
        return _canonical_hash(
            "ingestion-profile",
            {
                "include": self.include,
                "exclude": self.exclude,
                "execute_repository_scripts": self.execute_repository_scripts,
                "index_as_normative_knowledge": self.index_as_normative_knowledge,
            },
        )


class GovernanceProfile(_FrozenModel):
    authority: Annotated[str, Field(min_length=1)]
    curation_required: Literal[True]
    raw_prompt_use_allowed: Literal[False]
    compiler_or_doc_cross_check_required: bool = False
    positive_example_assumption_allowed: bool = False
    prompt_pattern_review_required: bool = False
    version_and_language_mode_gating_required: bool = False

    @field_validator("authority")
    @classmethod
    def validate_authority(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("GovernanceProfile.authority must be trimmed")
        return value


class SourceRecord(_FrozenModel):
    id: str
    group: SourceGroup
    kind: Annotated[str, Field(min_length=1)]
    remote: str
    local_path: Path
    env_override: str
    branch: Annotated[str, Field(min_length=1)]
    revision: str
    shallow_clone: bool
    checkout: CheckoutProfile
    use_for: tuple[str, ...]
    ingestion: IngestionProfile
    governance: GovernanceProfile

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _SOURCE_ID_RE.fullmatch(value):
            raise ValueError("SourceRecord.id must use lowercase kebab-case")
        return value

    @field_validator("remote")
    @classmethod
    def validate_remote(cls, value: str) -> str:
        if not _REMOTE_RE.fullmatch(value):
            raise ValueError("SourceRecord.remote must be an HTTPS URL")
        return value

    @field_validator("local_path")
    @classmethod
    def validate_local_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("SourceRecord.local_path must be absolute")
        return value

    @field_validator("local_path", mode="before")
    @classmethod
    def parse_local_path(cls, value: object) -> Path:
        if not isinstance(value, str | Path):
            raise ValueError("SourceRecord.local_path must be a filesystem path")
        return Path(value)

    @field_validator("env_override")
    @classmethod
    def validate_env_override(cls, value: str) -> str:
        if not _ENV_NAME_RE.fullmatch(value):
            raise ValueError("SourceRecord.env_override must be an uppercase environment name")
        return value

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if not _REVISION_RE.fullmatch(value):
            raise ValueError("SourceRecord.revision must be a full lowercase Git revision")
        return value

    @field_validator("use_for", mode="before")
    @classmethod
    def normalize_use_for(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("SourceRecord.use_for must be a sequence")
        result = _normalize_setlike(value, "SourceRecord.use_for")
        if not result:
            raise ValueError("SourceRecord.use_for must not be empty")
        return result


class SourceRegistry(_FrozenModel):
    schema_version: Literal[1]
    updated_at: date
    sources: tuple[SourceRecord, ...]

    @field_validator("sources", mode="before")
    @classmethod
    def parse_sources(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("SourceRegistry.sources must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_sources(self) -> SourceRegistry:
        if not self.sources:
            raise ValueError("SourceRegistry.sources must not be empty")
        ids = [source.id for source in self.sources]
        envs = [source.env_override for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("SourceRegistry contains duplicate source ids")
        if len(envs) != len(set(envs)):
            raise ValueError("SourceRegistry contains duplicate env_override values")
        return self

    @property
    def sources_by_id(self) -> Mapping[str, SourceRecord]:
        return {source.id: source for source in self.sources}


class VerifiedSource(_FrozenModel):
    source: SourceRecord
    resolved_local_path: Path
    git_toplevel: Path
    remote: str
    branch: str
    head_revision: str

    @model_validator(mode="after")
    def validate_checkout(self) -> VerifiedSource:
        if self.resolved_local_path != self.git_toplevel:
            raise ValueError("VerifiedSource path must equal the Git toplevel")
        if _normalize_remote(self.remote) != _normalize_remote(self.source.remote):
            raise ValueError("VerifiedSource remote does not match SourceRecord")
        if self.branch != self.source.branch:
            raise ValueError("VerifiedSource branch does not match SourceRecord")
        if self.head_revision != self.source.revision:
            raise ValueError("VerifiedSource HEAD does not match SourceRecord revision")
        return self


class SourceBundleEntry(_FrozenModel):
    source_id: str
    revision: str
    ingestion_profile_hash: str

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        if not _SOURCE_ID_RE.fullmatch(value):
            raise ValueError("SourceBundleEntry.source_id is invalid")
        return value

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if not _REVISION_RE.fullmatch(value):
            raise ValueError("SourceBundleEntry.revision is invalid")
        return value

    @field_validator("ingestion_profile_hash")
    @classmethod
    def validate_profile_hash(cls, value: str) -> str:
        if not re.fullmatch(r"^ingestion-profile:sha256:[0-9a-f]{64}$", value):
            raise ValueError("SourceBundleEntry.ingestion_profile_hash is invalid")
        return value


class SourceBundle(_FrozenModel):
    schema_version: Literal["source-bundle-v1"] = SOURCE_BUNDLE_SCHEMA_VERSION
    source_bundle_id: Annotated[str, Field(pattern=r"^source-bundle:sha256:[0-9a-f]{64}$")]
    entries: tuple[SourceBundleEntry, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> SourceBundle:
        ids = [entry.source_id for entry in self.entries]
        if not ids or ids != sorted(set(ids)):
            raise ValueError("SourceBundle.entries must be non-empty, sorted, and unique")
        expected = _source_bundle_id(self.entries)
        if self.source_bundle_id != expected:
            raise ValueError("SourceBundle.source_bundle_id does not match entries")
        return self


def _source_bundle_id(entries: Sequence[SourceBundleEntry]) -> str:
    return _canonical_hash(
        "source-bundle",
        [entry.model_dump(mode="json") for entry in entries],
    )


def _read_registry_file(path: Path) -> bytes:
    current = path
    while True:
        if current.is_symlink():
            raise ValueError("source registry path must not use symlinks")
        if current.parent == current:
            break
        current = current.parent
    if not path.is_file():
        raise ValueError("source registry must be a regular file")
    return path.read_bytes()


def load_source_registry(path: str | Path | None = None) -> SourceRegistry:
    configured = path
    if configured is None:
        configured = os.environ.get(SOURCE_REGISTRY_ENV, str(DEFAULT_SOURCE_REGISTRY))
    registry_path = Path(configured)
    if not registry_path.is_absolute():
        raise ValueError("source registry path must be absolute")
    raw = _read_registry_file(registry_path)
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        data = yaml.load(raw.decode("utf-8"))
        return SourceRegistry.model_validate(data)
    except (UnicodeDecodeError, ValueError, TypeError, YAMLError) as exc:
        raise ValueError(f"invalid source registry: {exc}") from exc


def resolve_source_path(
    source: SourceRecord,
    environment: Mapping[str, str] | None = None,
) -> Path:
    active_environment = os.environ if environment is None else environment
    override = active_environment.get(source.env_override)
    path = source.local_path if override is None else Path(override)
    if not path.is_absolute():
        raise ValueError(f"{source.env_override} must contain an absolute path")
    return path.resolve()


def _run_git(path: Path, *args: str, timeout_seconds: float = 10.0) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Git verification failed for {path}: {' '.join(args)}") from exc
    return result.stdout.strip()


def _normalize_remote(remote: str) -> str:
    return remote.rstrip("/").removesuffix(".git")


def verify_source_checkout(
    source: SourceRecord,
    *,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = 10.0,
) -> VerifiedSource:
    path = resolve_source_path(source, environment)
    if not path.is_dir():
        raise ValueError(f"registered source path does not exist: {source.id}")
    toplevel = Path(
        _run_git(path, "rev-parse", "--show-toplevel", timeout_seconds=timeout_seconds)
    ).resolve()
    remote = _run_git(path, "remote", "get-url", "origin", timeout_seconds=timeout_seconds)
    branch = _run_git(path, "branch", "--show-current", timeout_seconds=timeout_seconds)
    head = _run_git(path, "rev-parse", "HEAD", timeout_seconds=timeout_seconds)
    return VerifiedSource(
        source=source,
        resolved_local_path=path,
        git_toplevel=toplevel,
        remote=remote,
        branch=branch,
        head_revision=head,
    )


def build_source_bundle(
    registry: SourceRegistry,
    source_ids: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    verify: bool = True,
) -> tuple[SourceBundle, tuple[VerifiedSource, ...]]:
    requested = tuple(source_ids)
    if not requested or len(requested) != len(set(requested)):
        raise ValueError("source_ids must be non-empty and unique")
    sources_by_id = registry.sources_by_id
    unknown = sorted(set(requested) - set(sources_by_id))
    if unknown:
        raise ValueError(f"unknown source ids: {unknown}")
    selected = tuple(sources_by_id[source_id] for source_id in sorted(requested))
    verified = (
        tuple(verify_source_checkout(source, environment=environment) for source in selected)
        if verify
        else ()
    )
    entries = tuple(
        SourceBundleEntry(
            source_id=source.id,
            revision=source.revision,
            ingestion_profile_hash=source.ingestion.profile_hash,
        )
        for source in selected
    )
    bundle = SourceBundle(source_bundle_id=_source_bundle_id(entries), entries=entries)
    return bundle, verified


def _glob_regex(pattern: str) -> re.Pattern[str]:
    _validate_relative_pattern(pattern, "ingestion pattern")
    output: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    output.append("(?:.*/)?")
                    index += 1
                else:
                    output.append(".*")
                continue
            output.append("[^/]*")
        elif char == "?":
            output.append("[^/]")
        else:
            output.append(re.escape(char))
        index += 1
    output.append("$")
    return re.compile("".join(output))


def ingestion_path_allowed(relative_path: str, profile: IngestionProfile) -> bool:
    normalized = _validate_relative_pattern(relative_path, "ingestion relative_path")
    included = any(_glob_regex(pattern).fullmatch(normalized) for pattern in profile.include)
    if not included:
        return False
    excluded = any(_glob_regex(pattern).fullmatch(normalized) for pattern in profile.exclude)
    return not excluded


__all__ = [
    "DEFAULT_SOURCE_REGISTRY",
    "SOURCE_BUNDLE_SCHEMA_VERSION",
    "SOURCE_REGISTRY_ENV",
    "CheckoutProfile",
    "GovernanceProfile",
    "IngestionProfile",
    "SourceBundle",
    "SourceBundleEntry",
    "SourceRecord",
    "SourceRegistry",
    "VerifiedSource",
    "build_source_bundle",
    "ingestion_path_allowed",
    "load_source_registry",
    "resolve_source_path",
    "verify_source_checkout",
]
