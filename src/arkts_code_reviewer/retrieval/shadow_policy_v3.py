from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from arkts_code_reviewer.retrieval.config import (
    RetrievalConfig,
    load_default_retrieval_config,
)

RETRIEVAL_SHADOW_POLICY_V3_SCHEMA_VERSION: Literal["retrieval-shadow-policy-v1"] = (
    "retrieval-shadow-policy-v1"
)

ShadowPoolId = Literal[
    "formal_exact",
    "file_hint",
    "text_keyword",
    "ai_inferred",
    "semantic_vector",
]

_POOL_ORDER: tuple[ShadowPoolId, ...] = (
    "formal_exact",
    "file_hint",
    "text_keyword",
    "ai_inferred",
    "semantic_vector",
)
_RETRIEVAL_CONFIG_FINGERPRINT_PATTERN = r"^retrieval-config:sha256:[0-9a-f]{64}$"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKAGED_DEFAULTS = Path(__file__).resolve().parent / "defaults"


def _default_policy_path() -> Path:
    packaged = _PACKAGED_DEFAULTS / "retrieval_shadow_v3.yaml"
    return packaged if packaged.is_file() else _REPO_ROOT / "config" / "retrieval_shadow_v3.yaml"


DEFAULT_RETRIEVAL_SHADOW_POLICY_V3_PATH = _default_policy_path()


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class ShadowPoolPolicyV3(_StrictModel):
    pool_id: ShadowPoolId
    candidate_limit: Annotated[int, Field(ge=1, le=1000)]
    rrf_weight: Annotated[float, Field(gt=0)]

    @field_validator("rrf_weight")
    @classmethod
    def validate_finite_weight(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("shadow pool RRF weight must be finite")
        return value


class RetrievalShadowPolicyV3(_StrictModel):
    schema_version: Literal["retrieval-shadow-policy-v1"]
    base_retrieval_config_fingerprint: Annotated[
        str,
        Field(pattern=_RETRIEVAL_CONFIG_FINGERPRINT_PATTERN),
    ]
    rrf_k: Annotated[int, Field(ge=1)]
    result_limit: Annotated[int, Field(ge=1, le=100)]
    candidate_dimension_policy: Literal["diagnostic_only"]
    vector_query_policy: Literal["code-exact-facts-v1"]
    budget_policy: Literal["per_unit_formal_dimensions_only"]
    pools: tuple[ShadowPoolPolicyV3, ...]

    @field_validator("pools", mode="before")
    @classmethod
    def parse_pools(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Retrieval shadow pools must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_policy(self, info: ValidationInfo) -> Self:
        pool_ids = tuple(pool.pool_id for pool in self.pools)
        if pool_ids != _POOL_ORDER:
            raise ValueError(
                "Retrieval shadow pools must contain the five canonical pools in stable order"
            )

        context = info.context
        base_config = context.get("base_retrieval_config") if isinstance(context, Mapping) else None
        if base_config is None:
            base_config = load_default_retrieval_config()
        if not isinstance(base_config, RetrievalConfig):
            raise TypeError("base_retrieval_config must use RetrievalConfig")
        if self.base_retrieval_config_fingerprint != base_config.fingerprint:
            raise ValueError("Retrieval shadow policy differs from its base config fingerprint")
        if self.rrf_k != base_config.rrf_k:
            raise ValueError("Retrieval shadow policy rrf_k differs from its base config")
        if self.result_limit != base_config.result_limit:
            raise ValueError("Retrieval shadow policy result_limit differs from its base config")
        if any(
            round(pool.rrf_weight / (self.rrf_k + pool.candidate_limit), 8) <= 0
            for pool in self.pools
        ):
            raise ValueError(
                "Retrieval shadow pool weight is too small for the frozen RRF precision"
            )
        if not math.isfinite(sum(pool.rrf_weight / (self.rrf_k + 1) for pool in self.pools)):
            raise ValueError("Retrieval shadow aggregate RRF score must remain finite")
        return self

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return f"retrieval-shadow-policy:sha256:{hashlib.sha256(encoded).hexdigest()}"

    @property
    def pool_by_id(self) -> dict[ShadowPoolId, ShadowPoolPolicyV3]:
        return {pool.pool_id: pool for pool in self.pools}


def load_retrieval_shadow_policy_v3(
    path: str | Path | None = None,
    *,
    base_config: RetrievalConfig | None = None,
) -> RetrievalShadowPolicyV3:
    if base_config is not None and not isinstance(base_config, RetrievalConfig):
        raise TypeError("base_config must use RetrievalConfig")
    resolved_base_config = base_config or load_default_retrieval_config()
    policy_path = DEFAULT_RETRIEVAL_SHADOW_POLICY_V3_PATH if path is None else Path(path)
    if policy_path.is_symlink() or not policy_path.is_file():
        raise ValueError("Retrieval shadow policy must be a regular non-symlink file")

    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        payload = yaml.load(policy_path.read_text(encoding="utf-8"))
        if payload is None:
            raise ValueError("Retrieval shadow policy must not be empty")
        return RetrievalShadowPolicyV3.model_validate(
            payload,
            context={"base_retrieval_config": resolved_base_config},
        )
    except (OSError, UnicodeError, TypeError, ValueError, YAMLError, ValidationError) as exc:
        raise ValueError(f"invalid Retrieval shadow policy {policy_path}: {exc}") from exc


@lru_cache(maxsize=1)
def load_default_retrieval_shadow_policy_v3() -> RetrievalShadowPolicyV3:
    return load_retrieval_shadow_policy_v3(DEFAULT_RETRIEVAL_SHADOW_POLICY_V3_PATH)


__all__ = [
    "DEFAULT_RETRIEVAL_SHADOW_POLICY_V3_PATH",
    "RETRIEVAL_SHADOW_POLICY_V3_SCHEMA_VERSION",
    "RetrievalShadowPolicyV3",
    "ShadowPoolId",
    "ShadowPoolPolicyV3",
    "load_default_retrieval_shadow_policy_v3",
    "load_retrieval_shadow_policy_v3",
]
