from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from arkts_code_reviewer.feature_routing.config import FeatureConfig

ANNOTATION_CONFIG_SCHEMA_VERSION = "knowledge-annotation-config-v1"
DEFAULT_ANNOTATION_CONFIG = (
    Path(__file__).resolve().parents[3] / "config" / "knowledge_annotations.yaml"
)
PACKAGED_ANNOTATION_CONFIG = (
    Path(__file__).resolve().parent / "defaults" / "knowledge_annotations.yaml"
)

class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sorted_unique(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    values = tuple(value)
    if any(not isinstance(item, str) or not item or item.strip() != item for item in values):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


class KeywordTagRule(_FrozenModel):
    tag_id: Annotated[str, Field(min_length=1)]
    keywords: tuple[str, ...]

    @field_validator("keywords", mode="before")
    @classmethod
    def validate_keywords(cls, value: object) -> tuple[str, ...]:
        result = _sorted_unique(value, "KeywordTagRule.keywords")
        if not result:
            raise ValueError("KeywordTagRule.keywords must not be empty")
        return result


class ApiAlias(_FrozenModel):
    keyword: Annotated[str, Field(min_length=1)]
    canonical_name: Annotated[str, Field(min_length=1)]
    match_phrases: tuple[str, ...]

    @field_validator("match_phrases", mode="before")
    @classmethod
    def validate_match_phrases(cls, value: object) -> tuple[str, ...]:
        result = _sorted_unique(value, "ApiAlias.match_phrases")
        if not result:
            raise ValueError("ApiAlias.match_phrases must not be empty")
        return result


class KnowledgeDomainRule(_FrozenModel):
    domain_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    any_tags: tuple[str, ...] = ()
    any_apis: tuple[str, ...] = ()
    any_keywords: tuple[str, ...] = ()

    @field_validator("any_tags", "any_apis", "any_keywords", mode="before")
    @classmethod
    def validate_sets(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _sorted_unique(value, f"KnowledgeDomainRule.{info.field_name}")

    @model_validator(mode="after")
    def validate_trigger(self) -> KnowledgeDomainRule:
        if not (self.any_tags or self.any_apis or self.any_keywords):
            raise ValueError("KnowledgeDomainRule requires at least one trigger")
        return self


class KnowledgeAnnotationConfig(_FrozenModel):
    schema_version: Literal["knowledge-annotation-config-v1"]
    version: Annotated[str, Field(min_length=1)]
    source_domain_ids: tuple[str, ...]
    keyword_tag_rules: tuple[KeywordTagRule, ...]
    api_aliases: tuple[ApiAlias, ...]
    domain_rules: tuple[KnowledgeDomainRule, ...]

    @field_validator("keyword_tag_rules", "api_aliases", "domain_rules", mode="before")
    @classmethod
    def parse_records(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Knowledge annotation records must be sequences")
        return tuple(value)

    @field_validator("source_domain_ids", mode="before")
    @classmethod
    def validate_source_domains(cls, value: object) -> tuple[str, ...]:
        result = _sorted_unique(value, "KnowledgeAnnotationConfig.source_domain_ids")
        if not result:
            raise ValueError("KnowledgeAnnotationConfig.source_domain_ids must not be empty")
        return result

    @model_validator(mode="after")
    def validate_records(self) -> KnowledgeAnnotationConfig:
        tag_ids = [item.tag_id for item in self.keyword_tag_rules]
        aliases = [item.keyword for item in self.api_aliases]
        domains = [item.domain_id for item in self.domain_rules]
        for values, context in (
            (tag_ids, "keyword_tag_rules"),
            (aliases, "api_aliases"),
            (domains, "domain_rules"),
        ):
            if values != sorted(set(values)):
                raise ValueError(f"KnowledgeAnnotationConfig.{context} must be sorted and unique")
        return self

    def validate_feature_references(self, features: FeatureConfig) -> None:
        referenced_tags = {
            *(item.tag_id for item in self.keyword_tag_rules),
            *(tag for item in self.domain_rules for tag in item.any_tags),
        }
        unknown = sorted(referenced_tags - set(features.tags_by_id))
        if unknown:
            raise ValueError(f"Knowledge annotation config references unknown Tags: {unknown}")

    @property
    def registered_domain_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    *self.source_domain_ids,
                    *(item.domain_id for item in self.domain_rules),
                }
            )
        )

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"knowledge-annotation-config:sha256:{hashlib.sha256(raw).hexdigest()}"


def _default_path() -> Path:
    return (
        DEFAULT_ANNOTATION_CONFIG
        if DEFAULT_ANNOTATION_CONFIG.is_file()
        else PACKAGED_ANNOTATION_CONFIG
    )


def load_knowledge_annotation_config(
    path: str | Path | None = None,
    *,
    feature_config: FeatureConfig,
) -> KnowledgeAnnotationConfig:
    config_path = _default_path() if path is None else Path(path)
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("Knowledge annotation config must be a regular non-symlink file")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        payload = yaml.load(config_path.read_text(encoding="utf-8"))
        config = KnowledgeAnnotationConfig.model_validate(payload)
    except (OSError, UnicodeError, TypeError, ValueError, YAMLError) as exc:
        raise ValueError(f"invalid Knowledge annotation config: {exc}") from exc
    config.validate_feature_references(feature_config)
    return config


__all__ = [
    "ANNOTATION_CONFIG_SCHEMA_VERSION",
    "ApiAlias",
    "DEFAULT_ANNOTATION_CONFIG",
    "KeywordTagRule",
    "KnowledgeAnnotationConfig",
    "KnowledgeDomainRule",
    "load_knowledge_annotation_config",
]
