from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, ValidationError


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def canonical_hash(prefix: str, payload: object) -> str:
    encoded = canonical_json(payload).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def identity_payload(model: BaseModel, identity_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={identity_field})


def seal_payload[PayloadT: BaseModel, SealedT: BaseModel](
    payload: Mapping[str, object],
    *,
    payload_type: type[PayloadT],
    sealed_type: type[SealedT],
    identity_field: str,
    identity_prefix: str,
    context: str,
) -> SealedT:
    if identity_field in payload:
        raise ValueError(f"unsealed {context} payload cannot contain {identity_field}")
    try:
        validated = payload_type.model_validate(payload)
        sealed = validated.model_dump(mode="json")
        sealed[identity_field] = canonical_hash(identity_prefix, sealed)
        return sealed_type.model_validate(sealed)
    except ValidationError as exc:
        raise ValueError(f"invalid {context} payload: {exc}") from exc


def load_json_object(raw: str | bytes, context: str) -> dict[str, object]:
    """Load one strict JSON object without duplicate keys or non-finite numbers."""

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{context} must use UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError(f"{context} input must be str or bytes")

    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except (json.JSONDecodeError, _DuplicateKeyError, ValueError) as exc:
        raise ValueError(f"invalid {context} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid {context} JSON: top-level value must be an object")
    return payload


def load_json_model[ModelT: BaseModel](
    raw: str | bytes,
    model_type: type[ModelT],
    context: str,
) -> ModelT:
    payload = load_json_object(raw, context)
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


__all__ = [
    "FrozenModel",
    "canonical_hash",
    "canonical_json",
    "identity_payload",
    "load_json_object",
    "load_json_model",
    "seal_payload",
]
