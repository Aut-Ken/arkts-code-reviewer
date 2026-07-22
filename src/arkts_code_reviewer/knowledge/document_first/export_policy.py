from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
)

DOCUMENT_CARD_EXPORT_POLICY_SCHEMA_VERSION = "document-card-export-policy-v1"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PACKAGED_DEFAULTS = Path(__file__).resolve().parent / "defaults"


def _default_asset_path(filename: str, source_relative_path: str) -> Path:
    packaged = _PACKAGED_DEFAULTS / filename
    if packaged.is_file():
        return packaged
    return _REPO_ROOT / source_relative_path


DEFAULT_DOCUMENT_CARD_EXPORT_POLICY_PATH = _default_asset_path(
    "knowledge_document_card_export.yaml",
    "config/knowledge_document_card_export.yaml",
)


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _sorted_unique_strings(value: object, context: str) -> tuple[str, ...]:
    items = _sequence(value, context)
    if any(not isinstance(item, str) or not item or item != item.strip() for item in items):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    strings = tuple(item for item in items if isinstance(item, str))
    if strings != tuple(sorted(set(strings))):
        raise ValueError(f"{context} must be sorted and unique")
    return strings


def _relative_path(value: str, context: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or value != value.strip()
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or "\\" in value
    ):
        raise ValueError(f"{context} must stay below its source root")
    return str(path)


class DocumentCardExportSourceRule(FrozenModel):
    source_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    relative_paths: tuple[str, ...]

    @field_validator("relative_paths", mode="before")
    @classmethod
    def parse_paths(cls, value: object) -> tuple[str, ...]:
        paths = _sorted_unique_strings(value, "DocumentCardExportSourceRule.relative_paths")
        if not paths:
            raise ValueError("DocumentCardExportSourceRule.relative_paths must not be empty")
        return tuple(
            _relative_path(path, "DocumentCardExportSourceRule.relative_paths") for path in paths
        )


class DocumentCardExportPolicy(FrozenModel):
    schema_version: Literal["document-card-export-policy-v1"]
    version: Annotated[str, Field(min_length=1)]
    enabled: bool
    provider: Literal["deepseek"]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    allowed_models: tuple[str, ...]
    allowed_prompt_versions: tuple[str, ...]
    source_allowlist: tuple[DocumentCardExportSourceRule, ...]
    max_document_characters: Annotated[int, Field(ge=1, le=500_000)]
    max_request_body_bytes: Annotated[int, Field(ge=1_024, le=2_000_000)]
    max_sections: Annotated[int, Field(ge=1, le=200)]
    max_output_tokens: Annotated[int, Field(ge=256, le=16_384)]
    wall_clock_timeout_ms: Annotated[int, Field(ge=1_000, le=300_000)]
    max_response_bytes: Annotated[int, Field(ge=1_024, le=8_000_000)]
    max_attempts: Literal[1]
    retry_policy: Literal["none_single_attempt_v1"]
    thinking: Literal["disabled"]
    temperature: Literal[0]
    response_format: Literal["json_object"]
    tls_verify: Literal[True]
    follow_redirects: Literal[False]
    trust_env: Literal[False]
    qualification: Literal["development_single_document_navigation_smoke_not_production_approval"]

    @field_validator("allowed_models", "allowed_prompt_versions", mode="before")
    @classmethod
    def parse_string_sets(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _sorted_unique_strings(value, f"DocumentCardExportPolicy.{info.field_name}")

    @field_validator("source_allowlist", mode="before")
    @classmethod
    def parse_source_allowlist(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "DocumentCardExportPolicy.source_allowlist")

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("DocumentCardExportPolicy.version must be trimmed")
        return value

    @model_validator(mode="after")
    def validate_complete_policy(self) -> Self:
        keys = tuple((item.source_id, item.revision) for item in self.source_allowlist)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("DocumentCardExportPolicy source rules must be sorted and unique")
        configured = bool(
            self.allowed_models or self.allowed_prompt_versions or self.source_allowlist
        )
        if self.enabled and not (
            self.allowed_models and self.allowed_prompt_versions and self.source_allowlist
        ):
            raise ValueError("enabled Document Card export policy requires complete allowlists")
        if not self.enabled and configured:
            raise ValueError("disabled Document Card export policy cannot carry allowlists")
        return self

    @property
    def fingerprint(self) -> str:
        return canonical_hash("document-card-export-policy", self.model_dump(mode="json"))

    def permits_source(self, *, source_id: str, revision: str, relative_path: str) -> bool:
        return any(
            rule.source_id == source_id
            and rule.revision == revision
            and relative_path in rule.relative_paths
            for rule in self.source_allowlist
        )


def load_document_card_export_policy(
    path: str | Path | None = None,
) -> DocumentCardExportPolicy:
    policy_path = DEFAULT_DOCUMENT_CARD_EXPORT_POLICY_PATH if path is None else Path(path)
    if policy_path.is_symlink() or not policy_path.is_file():
        raise ValueError("Document Card export policy must be a regular non-symlink file")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        payload = yaml.load(policy_path.read_text(encoding="utf-8"))
        return DocumentCardExportPolicy.model_validate(payload)
    except (OSError, UnicodeError, TypeError, ValueError, YAMLError) as exc:
        raise ValueError(f"invalid Document Card export policy: {exc}") from exc


__all__ = [
    "DEFAULT_DOCUMENT_CARD_EXPORT_POLICY_PATH",
    "DOCUMENT_CARD_EXPORT_POLICY_SCHEMA_VERSION",
    "DocumentCardExportPolicy",
    "DocumentCardExportSourceRule",
    "load_document_card_export_policy",
]
