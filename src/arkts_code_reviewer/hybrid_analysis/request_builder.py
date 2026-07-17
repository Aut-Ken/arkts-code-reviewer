from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationError, ValidationInfo, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from arkts_code_reviewer.feature_routing import FeatureConfig, load_default_feature_config
from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    identity_payload,
    seal_payload,
)
from arkts_code_reviewer.hybrid_analysis.models import (
    ACTIVE_TAG_COUNT_V1,
    AITagAnalysisRequest,
    AITagAnalysisResult,
    AITagContractView,
    AITagExecutionOutcome,
    AITagModelView,
    HybridFeatureAnalysisResult,
    ReviewUnitAnalysisCard,
    seal_ai_tag_analysis_request,
    seal_ai_tag_contract_view,
    taxonomy_fingerprint,
    verify_hybrid_chain,
    verify_model_view_against_card,
)

AI_TAG_CONTRACT_CATALOG_SCHEMA_VERSION = "ai-tag-contract-catalog-v1"
AI_TAG_PROMPT_ASSET_SCHEMA_VERSION = "ai-tag-prompt-asset-v1"
AI_TAG_MODEL_POLICY_SCHEMA_VERSION = "ai-tag-model-policy-v1"
FULL_TAXONOMY_REQUEST_BUILDER_VERSION = "full-taxonomy-request-builder-v1"

_HASH = r"[0-9a-f]{64}"
_TAG_ID = r"^has_[a-z0-9_]+$"
_ACTIVE_REGISTRY_FINGERPRINT = rf"^ai-active-tag-registry:sha256:{_HASH}$"
_CATALOG_FINGERPRINT = rf"^ai-tag-contract-catalog:sha256:{_HASH}$"
_MODEL_POLICY_FINGERPRINT = rf"^ai-tag-policy:sha256:{_HASH}$"
_SHA256 = rf"^sha256:{_HASH}$"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKAGED_DEFAULTS = Path(__file__).resolve().parent / "defaults"


def _default_asset_path(filename: str, source_relative_path: str) -> Path:
    packaged = _PACKAGED_DEFAULTS / filename
    if packaged.is_file():
        return packaged
    return _REPO_ROOT / source_relative_path


DEFAULT_AI_TAG_CONTRACTS_PATH = _default_asset_path(
    "ai_tag_contracts.yaml",
    "config/ai_tag_contracts.yaml",
)
DEFAULT_AI_TAG_PROMPT_PATH = _default_asset_path(
    "deepseek-tag-analysis-v1.md",
    "prompts/hybrid-analysis/deepseek-tag-analysis-v1.md",
)


def _single_line(value: str, context: str, *, max_length: int = 500) -> str:
    if (
        not value
        or value != value.strip()
        or len(value) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(
            f"{context} must be non-empty, trimmed, single-line, and at most "
            f"{max_length} characters"
        )
    return value


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must use a sequence")
    return tuple(value)


class AITagContractCatalogEntry(FrozenModel):
    tag_id: Annotated[str, Field(pattern=_TAG_ID)]
    definition: Annotated[str, Field(min_length=1, max_length=500)]
    inclusions: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]
    exclusions: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]
    hard_negatives: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]

    @field_validator("inclusions", "exclusions", "hard_negatives", mode="before")
    @classmethod
    def parse_boundaries(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"AITagContractCatalogEntry.{info.field_name}")

    @field_validator("definition")
    @classmethod
    def validate_definition(cls, value: str) -> str:
        return _single_line(value, "AITagContractCatalogEntry.definition")

    @field_validator("inclusions", "exclusions", "hard_negatives")
    @classmethod
    def validate_boundaries(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        validated = tuple(
            _single_line(
                item,
                f"AITagContractCatalogEntry.{info.field_name}",
            )
            for item in value
        )
        if len(validated) != len(set(validated)):
            raise ValueError(
                f"AITagContractCatalogEntry.{info.field_name} must be unique"
            )
        return tuple(sorted(validated))


class AITagContractSourceTaxonomy(FrozenModel):
    schema_version: Literal["tag-config-v1"]
    version: Literal["tags-v1"]
    active_registry_fingerprint: Annotated[
        str,
        Field(pattern=_ACTIVE_REGISTRY_FINGERPRINT),
    ]


class _AITagContractCatalogPayload(FrozenModel):
    schema_version: Literal["ai-tag-contract-catalog-v1"]
    version: Literal["ai-tag-contracts-v1"]
    qualification: Literal["development_not_qualified"]
    source_taxonomy: AITagContractSourceTaxonomy
    active_tag_count: Literal[24]
    contracts: Annotated[
        tuple[AITagContractCatalogEntry, ...],
        Field(min_length=24, max_length=24),
    ]

    @field_validator("contracts", mode="before")
    @classmethod
    def parse_contracts(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AITagContractCatalog.contracts")

    @model_validator(mode="after")
    def validate_contracts(self) -> Self:
        tag_ids = tuple(contract.tag_id for contract in self.contracts)
        if tag_ids != tuple(sorted(set(tag_ids))):
            raise ValueError(
                "AITagContractCatalog contracts must use canonical unique Tag order"
            )
        return self


class AITagContractCatalog(_AITagContractCatalogPayload):
    catalog_fingerprint: Annotated[str, Field(pattern=_CATALOG_FINGERPRINT)]

    @model_validator(mode="after")
    def validate_catalog_fingerprint(self) -> Self:
        expected = canonical_hash(
            "ai-tag-contract-catalog",
            identity_payload(self, "catalog_fingerprint"),
        )
        if self.catalog_fingerprint != expected:
            raise ValueError(
                "AITagContractCatalog.catalog_fingerprint does not match its complete contents"
            )
        return self


class AITagPromptAsset(FrozenModel):
    schema_version: Literal["ai-tag-prompt-asset-v1"]
    prompt_version: Literal["deepseek-tag-analysis-v1"]
    text: Annotated[str, Field(min_length=1, max_length=50_000)]
    prompt_hash: Annotated[str, Field(pattern=_SHA256)]

    @model_validator(mode="after")
    def validate_prompt(self) -> Self:
        if not self.text.strip():
            raise ValueError("AITagPromptAsset.text must not be blank")
        if "\x00" in self.text or self.text.startswith("\ufeff"):
            raise ValueError("AITagPromptAsset.text contains a forbidden marker")
        expected = f"sha256:{hashlib.sha256(self.text.encode('utf-8')).hexdigest()}"
        if self.prompt_hash != expected:
            raise ValueError("AITagPromptAsset.prompt_hash does not match text")
        return self


class _AITagModelPolicyPayload(FrozenModel):
    schema_version: Literal["ai-tag-model-policy-v1"]
    policy_version: Annotated[str, Field(min_length=1, max_length=100)]
    provider: Literal["deepseek"]
    base_url: Literal["https://api.deepseek.com"]
    protocol: Literal["openai_chat_completions"]
    model: Literal["deepseek-v4-pro"]
    thinking: Literal["disabled"]
    reasoning_effort: None
    temperature: int
    stream: Literal[False]
    tool_choice: Literal["none"]
    tools: None
    response_format: Literal["json_object"]
    taxonomy_delivery_mode: Literal["full_single"]
    user_payload_renderer_version: Annotated[str, Field(min_length=1, max_length=100)]
    wire_output_contract_version: Literal["not_implemented-v1"]
    strict_json_validation: Literal[True]
    dispatch_mode: Literal["disabled_no_budget_no_approval"]

    @field_validator("policy_version", "user_payload_renderer_version")
    @classmethod
    def validate_versions(cls, value: str, info: ValidationInfo) -> str:
        return _single_line(value, f"AITagModelPolicy.{info.field_name}", max_length=100)

    @field_validator("temperature", mode="before")
    @classmethod
    def validate_temperature_type(cls, value: object) -> object:
        if type(value) is not int or value != 0:
            raise ValueError("AITagModelPolicy.temperature must be the integer 0")
        return value


class AITagModelPolicy(_AITagModelPolicyPayload):
    model_policy_fingerprint: Annotated[
        str,
        Field(pattern=_MODEL_POLICY_FINGERPRINT),
    ]

    @model_validator(mode="after")
    def validate_model_policy_fingerprint(self) -> Self:
        expected = canonical_hash(
            "ai-tag-policy",
            identity_payload(self, "model_policy_fingerprint"),
        )
        if self.model_policy_fingerprint != expected:
            raise ValueError(
                "AITagModelPolicy.model_policy_fingerprint does not match its contents"
            )
        return self


def active_tag_registry_fingerprint(feature_config: FeatureConfig) -> str:
    feature_config.__post_init__()
    active_tags = tuple(
        tag
        for tag in feature_config.tags_by_id.values()
        if tag.status == "Active"
    )
    payload = {
        "tag_config_schema_version": feature_config.tag_config.schema_version,
        "tag_config_version": feature_config.tag_config.version,
        "active_tags": [
            {
                "id": tag.id,
                "status": tag.status,
                "description": tag.description,
            }
            for tag in active_tags
        ],
    }
    return canonical_hash("ai-active-tag-registry", payload)


def _active_tag_ids(feature_config: FeatureConfig) -> tuple[str, ...]:
    tag_ids = tuple(
        tag_id
        for tag_id, definition in feature_config.tags_by_id.items()
        if definition.status == "Active"
    )
    if (
        len(tag_ids) != ACTIVE_TAG_COUNT_V1
        or tag_ids != tuple(sorted(set(tag_ids)))
    ):
        raise ValueError("AI Tag v1 requires exactly 24 sorted unique Active Tags")
    return tag_ids


def seal_ai_tag_contract_catalog(
    payload: Mapping[str, object],
) -> AITagContractCatalog:
    return seal_payload(
        payload,
        payload_type=_AITagContractCatalogPayload,
        sealed_type=AITagContractCatalog,
        identity_field="catalog_fingerprint",
        identity_prefix="ai-tag-contract-catalog",
        context="AI Tag Contract Catalog",
    )


def seal_ai_tag_model_policy(payload: Mapping[str, object]) -> AITagModelPolicy:
    return seal_payload(
        payload,
        payload_type=_AITagModelPolicyPayload,
        sealed_type=AITagModelPolicy,
        identity_field="model_policy_fingerprint",
        identity_prefix="ai-tag-policy",
        context="AI Tag Model Policy",
    )


def load_ai_tag_contract_catalog(
    path: str | Path,
) -> AITagContractCatalog:
    source = Path(path)
    if source.is_symlink():
        raise ValueError(f"AI Tag contract catalog path must not be a symlink: {source}")
    if not source.is_file():
        raise ValueError(f"AI Tag contract catalog path is not a file: {source}")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        raw = yaml.load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, YAMLError) as exc:
        raise ValueError(f"unable to load AI Tag contract catalog {source}: {exc}") from exc
    if raw is None:
        raise ValueError("AI Tag contract catalog must not be empty")
    if not isinstance(raw, dict):
        raise ValueError("AI Tag contract catalog must use a top-level mapping")
    try:
        return seal_ai_tag_contract_catalog(raw)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError(f"invalid AI Tag contract catalog {source}: {exc}") from exc


@lru_cache(maxsize=1)
def load_default_ai_tag_contract_catalog() -> AITagContractCatalog:
    return load_ai_tag_contract_catalog(DEFAULT_AI_TAG_CONTRACTS_PATH)


def load_ai_tag_prompt(
    path: str | Path,
    *,
    prompt_version: Literal["deepseek-tag-analysis-v1"] = "deepseek-tag-analysis-v1",
) -> AITagPromptAsset:
    source = Path(path)
    if source.is_symlink():
        raise ValueError(f"AI Tag prompt path must not be a symlink: {source}")
    if not source.is_file():
        raise ValueError(f"AI Tag prompt path is not a file: {source}")
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"unable to load AI Tag prompt {source}: {exc}") from exc
    prompt_hash = f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
    try:
        return AITagPromptAsset(
            schema_version="ai-tag-prompt-asset-v1",
            prompt_version=prompt_version,
            text=text,
            prompt_hash=prompt_hash,
        )
    except ValidationError as exc:
        raise ValueError(f"invalid AI Tag prompt {source}: {exc}") from exc


@lru_cache(maxsize=1)
def load_default_ai_tag_prompt() -> AITagPromptAsset:
    return load_ai_tag_prompt(DEFAULT_AI_TAG_PROMPT_PATH)


@lru_cache(maxsize=1)
def default_ai_tag_model_policy() -> AITagModelPolicy:
    return seal_ai_tag_model_policy(
        {
            "schema_version": AI_TAG_MODEL_POLICY_SCHEMA_VERSION,
            "policy_version": "deepseek-tag-policy-v1-disabled",
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "protocol": "openai_chat_completions",
            "model": "deepseek-v4-pro",
            "thinking": "disabled",
            "reasoning_effort": None,
            "temperature": 0,
            "stream": False,
            "tool_choice": "none",
            "tools": None,
            "response_format": "json_object",
            "taxonomy_delivery_mode": "full_single",
            "user_payload_renderer_version": "not_implemented-v1",
            "wire_output_contract_version": "not_implemented-v1",
            "strict_json_validation": True,
            "dispatch_mode": "disabled_no_budget_no_approval",
        }
    )


def validate_catalog_against_feature_config(
    catalog: AITagContractCatalog,
    feature_config: FeatureConfig,
) -> None:
    try:
        catalog = AITagContractCatalog.model_validate(catalog.model_dump(mode="json"))
    except ValidationError as exc:
        raise ValueError(f"invalid AI Tag Contract Catalog: {exc}") from exc
    feature_config.__post_init__()
    tag_config = feature_config.tag_config
    if (
        catalog.source_taxonomy.schema_version != tag_config.schema_version
        or catalog.source_taxonomy.version != tag_config.version
    ):
        raise ValueError("AI Tag contract catalog source taxonomy differs from Tag config")
    expected_registry_fingerprint = active_tag_registry_fingerprint(feature_config)
    if (
        catalog.source_taxonomy.active_registry_fingerprint
        != expected_registry_fingerprint
    ):
        raise ValueError("AI Tag contract catalog Active registry fingerprint differs")
    catalog_tag_ids = tuple(contract.tag_id for contract in catalog.contracts)
    if catalog_tag_ids != _active_tag_ids(feature_config):
        raise ValueError("AI Tag contract catalog does not exactly cover Active Tags")


def project_ai_tag_contract_views(
    catalog: AITagContractCatalog,
    feature_config: FeatureConfig,
) -> tuple[AITagContractView, ...]:
    validate_catalog_against_feature_config(catalog, feature_config)
    return tuple(
        seal_ai_tag_contract_view(
            {
                "schema_version": "ai-tag-contract-view-v1",
                "tag_id": contract.tag_id,
                "definition": contract.definition,
                "inclusions": contract.inclusions,
                "exclusions": contract.exclusions,
                "hard_negatives": contract.hard_negatives,
            }
        )
        for contract in catalog.contracts
    )


@dataclass(frozen=True)
class FullTaxonomyRequestBuilder:
    feature_config: FeatureConfig
    catalog: AITagContractCatalog
    prompt: AITagPromptAsset
    model_policy: AITagModelPolicy

    @classmethod
    def default(cls) -> FullTaxonomyRequestBuilder:
        return cls(
            feature_config=load_default_feature_config(),
            catalog=load_default_ai_tag_contract_catalog(),
            prompt=load_default_ai_tag_prompt(),
            model_policy=default_ai_tag_model_policy(),
        )

    def build(
        self,
        *,
        card: ReviewUnitAnalysisCard,
        model_view: AITagModelView,
    ) -> AITagAnalysisRequest:
        verify_model_view_against_card(model_view, card)
        if card.feature_config_fingerprint != self.feature_config.fingerprint:
            raise ValueError("Analysis Card feature config differs from request FeatureConfig")
        try:
            prompt = AITagPromptAsset.model_validate(self.prompt.model_dump(mode="json"))
            policy = AITagModelPolicy.model_validate(
                self.model_policy.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise ValueError(f"invalid AI Tag request input: {exc}") from exc
        contracts = project_ai_tag_contract_views(self.catalog, self.feature_config)
        return seal_ai_tag_analysis_request(
            {
                "schema_version": "ai-tag-analysis-request-v1",
                "card_id": card.card_id,
                "model_view_id": model_view.model_view_id,
                "taxonomy_delivery_mode": "full_single",
                "active_taxonomy_fingerprint": taxonomy_fingerprint(contracts),
                "tag_contract_views": contracts,
                "required_tag_count": ACTIVE_TAG_COUNT_V1,
                "prompt_version": prompt.prompt_version,
                "prompt_hash": prompt.prompt_hash,
                "model_policy_fingerprint": policy.model_policy_fingerprint,
            }
        )


def build_full_taxonomy_request(
    *,
    card: ReviewUnitAnalysisCard,
    model_view: AITagModelView,
) -> AITagAnalysisRequest:
    return FullTaxonomyRequestBuilder.default().build(card=card, model_view=model_view)


def verify_full_taxonomy_request(
    request: AITagAnalysisRequest,
    *,
    card: ReviewUnitAnalysisCard,
    model_view: AITagModelView,
    feature_config: FeatureConfig,
    catalog: AITagContractCatalog,
    prompt: AITagPromptAsset,
    model_policy: AITagModelPolicy,
) -> None:
    try:
        request = AITagAnalysisRequest.model_validate(request.model_dump(mode="json"))
    except ValidationError as exc:
        raise ValueError(f"invalid AI Tag Analysis Request: {exc}") from exc
    expected = FullTaxonomyRequestBuilder(
        feature_config=feature_config,
        catalog=catalog,
        prompt=prompt,
        model_policy=model_policy,
    ).build(card=card, model_view=model_view)
    if request != expected:
        raise ValueError("AI Tag request differs from trusted-input deterministic rebuild")


def verify_hybrid_chain_with_trusted_request(
    hybrid: HybridFeatureAnalysisResult,
    *,
    card: ReviewUnitAnalysisCard,
    model_view: AITagModelView,
    request: AITagAnalysisRequest,
    outcome: AITagExecutionOutcome,
    result: AITagAnalysisResult | None,
    feature_config: FeatureConfig,
    catalog: AITagContractCatalog,
    prompt: AITagPromptAsset,
    model_policy: AITagModelPolicy,
) -> None:
    """Close a Hybrid graph over a trusted-input rebuilt full-taxonomy request.

    The supplied Analysis Card remains this verifier's trust root. Callers that need
    Card-to-Parser provenance must separately run the upstream Analysis Card verifier.
    """
    verify_full_taxonomy_request(
        request,
        card=card,
        model_view=model_view,
        feature_config=feature_config,
        catalog=catalog,
        prompt=prompt,
        model_policy=model_policy,
    )
    verify_hybrid_chain(
        hybrid,
        card,
        model_view,
        request,
        outcome,
        result,
        active_tag_ids=_active_tag_ids(feature_config),
        active_taxonomy_fingerprint=request.active_taxonomy_fingerprint,
    )


__all__ = [
    "AI_TAG_CONTRACT_CATALOG_SCHEMA_VERSION",
    "AI_TAG_MODEL_POLICY_SCHEMA_VERSION",
    "AI_TAG_PROMPT_ASSET_SCHEMA_VERSION",
    "DEFAULT_AI_TAG_CONTRACTS_PATH",
    "DEFAULT_AI_TAG_PROMPT_PATH",
    "FULL_TAXONOMY_REQUEST_BUILDER_VERSION",
    "AITagContractCatalog",
    "AITagContractCatalogEntry",
    "AITagContractSourceTaxonomy",
    "AITagModelPolicy",
    "AITagPromptAsset",
    "FullTaxonomyRequestBuilder",
    "active_tag_registry_fingerprint",
    "build_full_taxonomy_request",
    "default_ai_tag_model_policy",
    "load_ai_tag_contract_catalog",
    "load_ai_tag_prompt",
    "load_default_ai_tag_contract_catalog",
    "load_default_ai_tag_prompt",
    "project_ai_tag_contract_views",
    "seal_ai_tag_contract_catalog",
    "seal_ai_tag_model_policy",
    "validate_catalog_against_feature_config",
    "verify_full_taxonomy_request",
    "verify_hybrid_chain_with_trusted_request",
]
