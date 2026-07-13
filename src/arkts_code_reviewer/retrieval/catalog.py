from __future__ import annotations

import hashlib
import json

from arkts_code_reviewer.knowledge.models import ApiSymbol


def aggregate_api_catalog_version(api_symbols: tuple[ApiSymbol, ...]) -> str:
    """Return one deterministic identity for zero, one, or many catalog fragments."""

    if not isinstance(api_symbols, tuple) or any(
        not isinstance(item, ApiSymbol) for item in api_symbols
    ):
        raise TypeError("api_symbols must be a tuple of ApiSymbol values")
    if not api_symbols:
        return "api-catalog:none"

    fragment_versions = {item.catalog_version for item in api_symbols}
    if len(fragment_versions) == 1:
        return next(iter(fragment_versions))

    ordered = sorted(
        api_symbols,
        key=lambda item: (
            item.canonical_name,
            item.signature,
            item.declaration_id,
        ),
    )
    payload = [item.model_dump(mode="json") for item in ordered]
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"api-catalog:sha256:{hashlib.sha256(raw).hexdigest()}"


__all__ = ["aggregate_api_catalog_version"]
