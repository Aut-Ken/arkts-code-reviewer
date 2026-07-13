from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from arkts_code_reviewer.knowledge.models import NormalizedDocument
from arkts_code_reviewer.knowledge.registry import (
    SourceRecord,
    VerifiedSource,
    ingestion_path_allowed,
)
from arkts_code_reviewer.knowledge.seed import KnowledgeSeed


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceObject(_FrozenModel):
    source_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    relative_path: Annotated[str, Field(min_length=1)]
    authority: Annotated[str, Field(min_length=1)]
    domains: tuple[str, ...]
    media_type: Annotated[str, Field(min_length=1)]

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or "." in path.parts or ".." in path.parts or "\\" in value:
            raise ValueError("SourceObject.relative_path must be a safe POSIX path")
        return str(path)


class SourceAdapter(Protocol):
    source_id: str
    adapter_version: str

    def load(self, source: SourceObject, reader: GitObjectReader) -> NormalizedDocument: ...


class GitObjectReader:
    def __init__(self, verified_sources: Mapping[str, VerifiedSource], timeout: float = 15.0):
        self._sources = dict(verified_sources)
        self._timeout = timeout

    def _source(self, source_id: str) -> VerifiedSource:
        try:
            return self._sources[source_id]
        except KeyError as exc:
            raise ValueError(f"source is not verified: {source_id}") from exc

    def list_paths(self, source_id: str) -> tuple[str, ...]:
        source = self._source(source_id)
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source.resolved_local_path),
                    "ls-tree",
                    "-r",
                    "--name-only",
                    "-z",
                    source.source.revision,
                ],
                check=True,
                capture_output=True,
                timeout=self._timeout,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"failed to list Git objects for {source_id}") from exc
        paths = tuple(item.decode("utf-8") for item in result.stdout.split(b"\0") if item)
        if paths != tuple(sorted(set(paths))):
            raise ValueError(f"Git tree paths are not deterministic for {source_id}")
        return paths

    def read_bytes(self, source: SourceObject) -> bytes:
        verified = self._source(source.source_id)
        if source.revision != verified.source.revision:
            raise ValueError("SourceObject revision does not match verified source")
        object_name = f"{source.revision}:{source.relative_path}"
        try:
            result = subprocess.run(
                ["git", "-C", str(verified.resolved_local_path), "show", object_name],
                check=True,
                capture_output=True,
                timeout=self._timeout,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"failed to read pinned Git object: {source.relative_path}") from exc
        return result.stdout


def _media_type(path: str) -> str:
    if path.endswith(".md"):
        return "text/markdown"
    if path.endswith((".d.ts", ".d.ets")):
        return "text/typescript-declaration"
    raise ValueError(f"unsupported seed media type: {path}")


def discover_seed_objects(
    seed: KnowledgeSeed,
    verified_sources: Mapping[str, VerifiedSource],
) -> tuple[SourceObject, ...]:
    if set(seed.source_ids) != set(verified_sources):
        raise ValueError("verified sources must exactly match Knowledge seed source_ids")
    reader = GitObjectReader(verified_sources)
    paths_by_source = {
        source_id: set(reader.list_paths(source_id)) for source_id in seed.source_ids
    }
    objects: list[SourceObject] = []
    for document in seed.documents:
        verified = verified_sources[document.source_id]
        record: SourceRecord = verified.source
        if document.relative_path not in paths_by_source[document.source_id]:
            raise ValueError(
                "seed document is not tracked at pinned revision: "
                f"{document.relative_path}"
            )
        if not ingestion_path_allowed(document.relative_path, record.ingestion):
            raise ValueError(
                f"seed document is outside ingestion allowlist: {document.relative_path}"
            )
        objects.append(
            SourceObject(
                source_id=document.source_id,
                revision=record.revision,
                relative_path=document.relative_path,
                authority=record.governance.authority,
                domains=document.domains,
                media_type=_media_type(document.relative_path),
            )
        )
    keys = [(item.source_id, item.relative_path) for item in objects]
    if keys != sorted(set(keys)):
        raise ValueError("discovered seed objects are not sorted and unique")
    return tuple(objects)


def content_sha256(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


__all__ = [
    "GitObjectReader",
    "SourceAdapter",
    "SourceObject",
    "content_sha256",
    "discover_seed_objects",
]
