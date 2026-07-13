from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKAGED_DEFAULTS = Path(__file__).resolve().parent / "defaults"


def _default_config_path() -> Path:
    packaged = _PACKAGED_DEFAULTS / "retrieval.yaml"
    return packaged if packaged.is_file() else _REPO_ROOT / "config" / "retrieval.yaml"


DEFAULT_RETRIEVAL_CONFIG_PATH = _default_config_path()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExactWeights(_StrictModel):
    rule_id: Annotated[int, Field(ge=1)]
    api: Annotated[int, Field(ge=1)]
    component: Annotated[int, Field(ge=1)]
    decorator: Annotated[int, Field(ge=1)]
    exact_tag: Annotated[int, Field(ge=1)]
    routing_tag: Annotated[int, Field(ge=1)]
    keyword: Annotated[int, Field(ge=1)]
    dimension_overlap: Annotated[int, Field(ge=0)]
    applicability_exact: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_priority(self) -> ExactWeights:
        if not (
            self.rule_id
            > self.api
            > self.component
            == self.decorator
            > self.exact_tag
            > self.routing_tag
            > self.keyword
        ):
            raise ValueError("Retrieval exact weights violate the frozen priority order")
        return self


class AuthorityPriority(_StrictModel):
    authority: Annotated[str, Field(min_length=1)]
    priority: Annotated[int, Field(ge=0)]

    @field_validator("authority")
    @classmethod
    def validate_authority(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("authority must be trimmed text")
        return value


class RetrievalConfig(_StrictModel):
    schema_version: Literal["retrieval-config-v1"]
    version: Annotated[str, Field(min_length=1)]
    max_units: Annotated[int, Field(ge=1, le=50)]
    exact_candidate_limit: Annotated[int, Field(ge=1, le=1000)]
    vector_candidate_limit: Annotated[int, Field(ge=1, le=1000)]
    result_limit: Annotated[int, Field(ge=1, le=100)]
    rrf_k: Annotated[int, Field(ge=1)]
    minimum_vector_similarity: Annotated[float, Field(ge=-1, le=1)]
    weights: ExactWeights
    authority_priorities: tuple[AuthorityPriority, ...]

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("Retrieval config version must be trimmed")
        return value

    @field_validator("authority_priorities", mode="before")
    @classmethod
    def parse_priorities(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("authority_priorities must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_priorities(self) -> RetrievalConfig:
        keys = [item.authority for item in self.authority_priorities]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("authority priorities must be non-empty and unique")
        expected = sorted(
            self.authority_priorities,
            key=lambda item: (-item.priority, item.authority),
        )
        if list(self.authority_priorities) != expected:
            raise ValueError("authority priorities must use stable priority order")
        return self

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"retrieval-config:sha256:{hashlib.sha256(encoded).hexdigest()}"

    @property
    def authority_priority_by_id(self) -> dict[str, int]:
        return {item.authority: item.priority for item in self.authority_priorities}


def load_retrieval_config(path: str | Path | None = None) -> RetrievalConfig:
    config_path = DEFAULT_RETRIEVAL_CONFIG_PATH if path is None else Path(path)
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("Retrieval config must be a regular non-symlink file")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        payload = yaml.load(config_path.read_text(encoding="utf-8"))
        if payload is None:
            raise ValueError("Retrieval config must not be empty")
        return RetrievalConfig.model_validate(payload)
    except (OSError, UnicodeError, TypeError, ValueError, YAMLError, ValidationError) as exc:
        raise ValueError(f"invalid Retrieval config {config_path}: {exc}") from exc


@lru_cache(maxsize=1)
def load_default_retrieval_config() -> RetrievalConfig:
    return load_retrieval_config(DEFAULT_RETRIEVAL_CONFIG_PATH)


__all__ = [
    "DEFAULT_RETRIEVAL_CONFIG_PATH",
    "AuthorityPriority",
    "ExactWeights",
    "RetrievalConfig",
    "load_default_retrieval_config",
    "load_retrieval_config",
]
