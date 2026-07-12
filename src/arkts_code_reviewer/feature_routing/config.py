from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

type FeatureStatus = Literal["Active", "Draft", "Deprecated"]
type RetrievalPolicy = Literal["signal_required", "always", "disabled"]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKAGED_DEFAULTS = Path(__file__).resolve().parent / "defaults"


def _default_config_path(filename: str) -> Path:
    packaged = _PACKAGED_DEFAULTS / filename
    if packaged.is_file():
        return packaged
    return _REPO_ROOT / "config" / filename


DEFAULT_TAGS_PATH = _default_config_path("tags.yaml")
DEFAULT_DIMENSIONS_PATH = _default_config_path("dimensions.yaml")
_TAG_ID_RE = re.compile(r"has_[a-z0-9_]+\Z")
_DIMENSION_ID_RE = re.compile(r"DIM-[0-9]{2}\Z")
_QUESTION_ID_RE = re.compile(r"RQ-[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _Identified(Protocol):
    id: str


def _require_text(value: str, context: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{context} must be a non-empty trimmed string")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{context} must not contain control characters")
    return value


def _string_tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("trigger values must use a YAML sequence")
    return tuple(value)


def _require_sorted_unique(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(not value or value != value.strip() for value in values):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


class TagTriggers(_StrictModel):
    any_component: tuple[str, ...] = ()
    any_api: tuple[str, ...] = ()
    any_api_prefix: tuple[str, ...] = ()
    any_api_suffix: tuple[str, ...] = ()
    any_decorator: tuple[str, ...] = ()
    any_attribute: tuple[str, ...] = ()
    any_symbol: tuple[str, ...] = ()
    any_syntax: tuple[str, ...] = ()
    has_resource_reference: bool = False

    @field_validator(
        "any_component",
        "any_api",
        "any_api_prefix",
        "any_api_suffix",
        "any_decorator",
        "any_attribute",
        "any_symbol",
        "any_syntax",
        mode="before",
    )
    @classmethod
    def _coerce_trigger_sequence(cls, value: object) -> tuple[object, ...]:
        return _string_tuple(value)

    @field_validator(
        "any_component",
        "any_api",
        "any_api_prefix",
        "any_api_suffix",
        "any_decorator",
        "any_attribute",
        "any_symbol",
        "any_syntax",
    )
    @classmethod
    def _validate_trigger_sequence(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "trigger")
        return _require_sorted_unique(value, f"TagTriggers.{field_name}")

    @model_validator(mode="after")
    def _require_trigger(self) -> Self:
        sequences = (
            self.any_component,
            self.any_api,
            self.any_api_prefix,
            self.any_api_suffix,
            self.any_decorator,
            self.any_attribute,
            self.any_symbol,
            self.any_syntax,
        )
        if not self.has_resource_reference and not any(sequences):
            raise ValueError("TagTriggers must contain at least one trigger")
        return self


class TagDefinition(_StrictModel):
    id: str
    status: FeatureStatus
    description: str
    triggers: TagTriggers

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        _require_text(value, "TagDefinition.id")
        if _TAG_ID_RE.fullmatch(value) is None:
            raise ValueError("TagDefinition.id must use has_<snake_case>")
        return value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _require_text(value, "TagDefinition.description")


class TagConfig(_StrictModel):
    schema_version: Literal["tag-config-v1"]
    version: str
    tags: tuple[TagDefinition, ...]

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: object) -> tuple[object, ...]:
        return _string_tuple(value)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _require_text(value, "TagConfig.version")

    @model_validator(mode="after")
    def _validate_tags(self) -> Self:
        if not self.tags:
            raise ValueError("TagConfig.tags must not be empty")
        ids = [tag.id for tag in self.tags]
        if len(ids) != len(set(ids)):
            raise ValueError("TagConfig.tags contains duplicate IDs")
        return self


class DimensionTriggers(_StrictModel):
    any_tag: tuple[str, ...] = ()

    @field_validator("any_tag", mode="before")
    @classmethod
    def _coerce_tags(cls, value: object) -> tuple[object, ...]:
        return _string_tuple(value)

    @field_validator("any_tag")
    @classmethod
    def _validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_sorted_unique(value, "DimensionTriggers.any_tag")


class DimensionDefinition(_StrictModel):
    id: str
    title: str
    status: FeatureStatus
    always_check: bool
    retrieval_policy: RetrievalPolicy
    triggers: DimensionTriggers

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        _require_text(value, "DimensionDefinition.id")
        if _DIMENSION_ID_RE.fullmatch(value) is None:
            raise ValueError("DimensionDefinition.id must use DIM-NN")
        return value

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _require_text(value, "DimensionDefinition.title")


class ReviewQuestionDefinition(_StrictModel):
    id: str
    title: str
    status: FeatureStatus
    always_bind: bool
    triggers: DimensionTriggers

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        _require_text(value, "ReviewQuestionDefinition.id")
        if _QUESTION_ID_RE.fullmatch(value) is None:
            raise ValueError("ReviewQuestionDefinition.id must use RQ-kebab-case")
        return value

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _require_text(value, "ReviewQuestionDefinition.title")

    @model_validator(mode="after")
    def _validate_binding_policy(self) -> Self:
        if self.always_bind and self.triggers.any_tag:
            raise ValueError("always-bound review questions must not declare tag triggers")
        if not self.always_bind and not self.triggers.any_tag:
            raise ValueError("conditional review questions require at least one tag trigger")
        return self


class DimensionConfig(_StrictModel):
    schema_version: Literal["dimension-config-v1"]
    version: str
    review_questions: tuple[ReviewQuestionDefinition, ...]
    dimensions: tuple[DimensionDefinition, ...]

    @field_validator("review_questions", "dimensions", mode="before")
    @classmethod
    def _coerce_definitions(cls, value: object) -> tuple[object, ...]:
        return _string_tuple(value)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _require_text(value, "DimensionConfig.version")

    @model_validator(mode="after")
    def _validate_definitions(self) -> Self:
        if not self.review_questions:
            raise ValueError("DimensionConfig.review_questions must not be empty")
        if not self.dimensions:
            raise ValueError("DimensionConfig.dimensions must not be empty")
        question_ids = [question.id for question in self.review_questions]
        dimension_ids = [dimension.id for dimension in self.dimensions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("DimensionConfig.review_questions contains duplicate IDs")
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError("DimensionConfig.dimensions contains duplicate IDs")
        return self


@dataclass(frozen=True)
class FeatureConfig:
    tag_config: TagConfig
    dimension_config: DimensionConfig
    tags_by_id: MappingProxyType[str, TagDefinition]
    dimensions_by_id: MappingProxyType[str, DimensionDefinition]
    review_questions_by_id: MappingProxyType[str, ReviewQuestionDefinition]
    fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.tag_config, TagConfig):
            raise ValueError("FeatureConfig.tag_config must use TagConfig")
        if not isinstance(self.dimension_config, DimensionConfig):
            raise ValueError("FeatureConfig.dimension_config must use DimensionConfig")
        expected_tags = _sorted_mapping(self.tag_config.tags)
        expected_dimensions = _sorted_mapping(self.dimension_config.dimensions)
        expected_questions = _sorted_mapping(self.dimension_config.review_questions)
        for actual, expected, context in (
            (self.tags_by_id, expected_tags, "tags_by_id"),
            (self.dimensions_by_id, expected_dimensions, "dimensions_by_id"),
            (self.review_questions_by_id, expected_questions, "review_questions_by_id"),
        ):
            if not isinstance(actual, MappingProxyType) or dict(actual) != dict(expected):
                raise ValueError(f"FeatureConfig.{context} does not match definitions")
        _validate_references(expected_tags, expected_dimensions, expected_questions)
        expected_fingerprint = _feature_fingerprint(
            self.tag_config,
            self.dimension_config,
        )
        if self.fingerprint != expected_fingerprint:
            raise ValueError("FeatureConfig.fingerprint does not match definitions")


def load_feature_config(
    tags_path: str | Path | None = None,
    dimensions_path: str | Path | None = None,
) -> FeatureConfig:
    tag_config = _load_model(
        DEFAULT_TAGS_PATH if tags_path is None else Path(tags_path),
        TagConfig,
        "tag config",
    )
    dimension_config = _load_model(
        DEFAULT_DIMENSIONS_PATH if dimensions_path is None else Path(dimensions_path),
        DimensionConfig,
        "dimension config",
    )
    tags_by_id = _sorted_mapping(tag_config.tags)
    dimensions_by_id = _sorted_mapping(dimension_config.dimensions)
    questions_by_id = _sorted_mapping(dimension_config.review_questions)
    _validate_references(tags_by_id, dimensions_by_id, questions_by_id)
    fingerprint = _feature_fingerprint(tag_config, dimension_config)
    return FeatureConfig(
        tag_config=tag_config,
        dimension_config=dimension_config,
        tags_by_id=tags_by_id,
        dimensions_by_id=dimensions_by_id,
        review_questions_by_id=questions_by_id,
        fingerprint=fingerprint,
    )


@lru_cache(maxsize=1)
def load_default_feature_config() -> FeatureConfig:
    return load_feature_config(DEFAULT_TAGS_PATH, DEFAULT_DIMENSIONS_PATH)


def _load_model[ModelT: BaseModel](
    path: Path,
    model_type: type[ModelT],
    context: str,
) -> ModelT:
    if path.is_symlink():
        raise ValueError(f"{context} path must not be a symlink: {path}")
    if not path.is_file():
        raise ValueError(f"{context} path is not a file: {path}")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, YAMLError) as exc:
        raise ValueError(f"unable to load {context} {path}: {exc}") from exc
    if raw is None:
        raise ValueError(f"{context} must not be empty")
    try:
        return model_type.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid {context} {path}: {exc}") from exc


def _sorted_mapping[DefinitionT: _Identified](
    values: tuple[DefinitionT, ...],
) -> MappingProxyType[str, DefinitionT]:
    return MappingProxyType(
        {
            str(value.id): value
            for value in sorted(values, key=lambda item: str(item.id))
        }
    )


def _validate_references(
    tags: MappingProxyType[str, TagDefinition],
    dimensions: MappingProxyType[str, DimensionDefinition],
    questions: MappingProxyType[str, ReviewQuestionDefinition],
) -> None:
    for kind, definitions in (("dimension", dimensions), ("review question", questions)):
        for definition in definitions.values():
            referenced_ids = definition.triggers.any_tag
            unknown = sorted(set(referenced_ids) - set(tags))
            if unknown:
                raise ValueError(
                    f"{kind} {definition.id} references unknown tags: {unknown}"
                )
            if definition.status == "Active":
                non_active = sorted(
                    tag_id
                    for tag_id in referenced_ids
                    if tags[tag_id].status != "Active"
                )
                if non_active:
                    raise ValueError(
                        f"Active {kind} {definition.id} depends on non-Active tags: "
                        f"{non_active}"
                    )


def _feature_fingerprint(
    tag_config: TagConfig,
    dimension_config: DimensionConfig,
) -> str:
    payload = {
        "tag_config": {
            **tag_config.model_dump(mode="json", exclude={"tags"}),
            "tags": [
                item.model_dump(mode="json")
                for item in sorted(tag_config.tags, key=lambda value: value.id)
            ],
        },
        "dimension_config": {
            **dimension_config.model_dump(
                mode="json",
                exclude={"dimensions", "review_questions"},
            ),
            "dimensions": [
                item.model_dump(mode="json")
                for item in sorted(
                    dimension_config.dimensions,
                    key=lambda value: value.id,
                )
            ],
            "review_questions": [
                item.model_dump(mode="json")
                for item in sorted(
                    dimension_config.review_questions,
                    key=lambda value: value.id,
                )
            ],
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"feature-config:sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "DEFAULT_DIMENSIONS_PATH",
    "DEFAULT_TAGS_PATH",
    "DimensionConfig",
    "DimensionDefinition",
    "DimensionTriggers",
    "FeatureConfig",
    "FeatureStatus",
    "RetrievalPolicy",
    "ReviewQuestionDefinition",
    "TagConfig",
    "TagDefinition",
    "TagTriggers",
    "load_default_feature_config",
    "load_feature_config",
]
