from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

KNOWLEDGE_SEED_SCHEMA_VERSION = "knowledge-seed-v1"
DEFAULT_KNOWLEDGE_SEED = Path(__file__).resolve().parents[3] / "config/knowledge_seed_v1.yaml"
PACKAGED_KNOWLEDGE_SEED = Path(__file__).resolve().parent / "defaults/knowledge_seed_v1.yaml"

_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _normalize_strings(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    result = tuple(value)
    if any(not isinstance(item, str) or not item or item.strip() != item for item in result):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if len(result) != len(set(result)):
        raise ValueError(f"{context} must not contain duplicates")
    return tuple(sorted(result))


class SeedDocument(_FrozenModel):
    source_id: str
    relative_path: str
    domains: tuple[str, ...]

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        if not _SOURCE_ID_RE.fullmatch(value):
            raise ValueError("SeedDocument.source_id is invalid")
        return value

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or value.strip() != value
            or path.is_absolute()
            or "." in path.parts
            or ".." in path.parts
            or "\\" in value
        ):
            raise ValueError("SeedDocument.relative_path must be a safe POSIX path")
        return str(path)

    @field_validator("domains", mode="before")
    @classmethod
    def normalize_domains(cls, value: object) -> tuple[str, ...]:
        return _normalize_strings(value, "SeedDocument.domains")

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not _DOMAIN_RE.fullmatch(item) for item in value):
            raise ValueError("SeedDocument.domains contains invalid IDs")
        return value


class KnowledgeSeed(_FrozenModel):
    schema_version: Literal["knowledge-seed-v1"]
    seed_id: Literal["knowledge-seed-v1"]
    description: Annotated[str, Field(min_length=1)]
    source_ids: tuple[str, ...]
    domains: tuple[str, ...]
    documents: tuple[SeedDocument, ...]

    @field_validator("source_ids", "domains", mode="before")
    @classmethod
    def normalize_root_sets(cls, value: object, info: object) -> tuple[str, ...]:
        return _normalize_strings(value, f"KnowledgeSeed.{info.field_name}")

    @field_validator("documents", mode="before")
    @classmethod
    def parse_documents(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("KnowledgeSeed.documents must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> KnowledgeSeed:
        if len(self.documents) != 24:
            raise ValueError("knowledge-seed-v1 must contain exactly 24 documents")
        keys = [(item.source_id, item.relative_path) for item in self.documents]
        if keys != sorted(set(keys)):
            raise ValueError("KnowledgeSeed.documents must be sorted and unique")
        if set(item.source_id for item in self.documents) != set(self.source_ids):
            raise ValueError("KnowledgeSeed.source_ids must exactly cover documents")
        if not all(set(item.domains).issubset(set(self.domains)) for item in self.documents):
            raise ValueError("KnowledgeSeed document contains an unknown domain")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"description"})
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"knowledge-seed:sha256:{hashlib.sha256(raw).hexdigest()}"


def _default_seed_path() -> Path:
    if DEFAULT_KNOWLEDGE_SEED.is_file():
        return DEFAULT_KNOWLEDGE_SEED
    return PACKAGED_KNOWLEDGE_SEED


def load_knowledge_seed(path: str | Path | None = None) -> KnowledgeSeed:
    seed_path = _default_seed_path() if path is None else Path(path)
    if seed_path.is_symlink() or not seed_path.is_file():
        raise ValueError("Knowledge seed must be a regular non-symlink file")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        data = yaml.load(seed_path.read_text(encoding="utf-8"))
        return KnowledgeSeed.model_validate(data)
    except (UnicodeDecodeError, ValueError, TypeError, YAMLError) as exc:
        raise ValueError(f"invalid Knowledge seed: {exc}") from exc


__all__ = [
    "DEFAULT_KNOWLEDGE_SEED",
    "KNOWLEDGE_SEED_SCHEMA_VERSION",
    "KnowledgeSeed",
    "SeedDocument",
    "load_knowledge_seed",
]
