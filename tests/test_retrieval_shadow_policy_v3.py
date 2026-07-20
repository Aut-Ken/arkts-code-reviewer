from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.retrieval.config import load_default_retrieval_config
from arkts_code_reviewer.retrieval.shadow_policy_v3 import (
    DEFAULT_RETRIEVAL_SHADOW_POLICY_V3_PATH,
    RetrievalShadowPolicyV3,
    load_default_retrieval_shadow_policy_v3,
    load_retrieval_shadow_policy_v3,
)


def _write_policy(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "retrieval_shadow_v3.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _default_payload() -> dict[str, object]:
    return load_default_retrieval_shadow_policy_v3().model_dump(mode="json")


def _dump_yaml(payload: dict[str, object]) -> str:
    pools = payload["pools"]
    assert isinstance(pools, list)
    lines = [
        f"schema_version: {payload['schema_version']}",
        f"base_retrieval_config_fingerprint: {payload['base_retrieval_config_fingerprint']}",
        f"rrf_k: {payload['rrf_k']}",
        f"result_limit: {payload['result_limit']}",
        f"candidate_dimension_policy: {payload['candidate_dimension_policy']}",
        f"vector_query_policy: {payload['vector_query_policy']}",
        f"budget_policy: {payload['budget_policy']}",
        "pools:",
    ]
    for raw_pool in pools:
        assert isinstance(raw_pool, dict)
        lines.extend(
            (
                f"  - pool_id: {raw_pool['pool_id']}",
                f"    candidate_limit: {raw_pool['candidate_limit']}",
                f"    rrf_weight: {raw_pool['rrf_weight']}",
            )
        )
    return "\n".join(lines) + "\n"


def test_default_policy_is_bound_frozen_and_fingerprinted() -> None:
    base = load_default_retrieval_config()
    first = load_default_retrieval_shadow_policy_v3()
    second = load_default_retrieval_shadow_policy_v3()

    assert first is second
    assert first.schema_version == "retrieval-shadow-policy-v1"
    assert first.base_retrieval_config_fingerprint == base.fingerprint
    assert first.rrf_k == base.rrf_k == 60
    assert first.result_limit == base.result_limit == 8
    assert first.candidate_dimension_policy == "diagnostic_only"
    assert first.vector_query_policy == "code-exact-facts-v1"
    assert first.budget_policy == "per_unit_formal_dimensions_only"
    assert tuple(first.pool_by_id) == (
        "formal_exact",
        "file_hint",
        "text_keyword",
        "ai_inferred",
        "semantic_vector",
    )
    assert tuple(pool.candidate_limit for pool in first.pools) == (50, 50, 50, 50, 30)
    assert tuple(pool.rrf_weight for pool in first.pools) == (1.0, 0.5, 0.25, 0.25, 1.0)

    encoded = json.dumps(
        first.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert first.fingerprint == (
        "retrieval-shadow-policy:sha256:" + hashlib.sha256(encoded).hexdigest()
    )
    with pytest.raises(ValidationError, match="frozen"):
        first.rrf_k = 61


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        (
            "base_retrieval_config_fingerprint",
            "retrieval-config:sha256:" + "0" * 64,
            "base config fingerprint",
        ),
        ("rrf_k", 61, "rrf_k"),
        ("result_limit", 9, "result_limit"),
    ),
)
def test_policy_rejects_base_config_drift(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _default_payload()
    payload[field] = value
    with pytest.raises(ValueError, match=message):
        load_retrieval_shadow_policy_v3(_write_policy(tmp_path, _dump_yaml(payload)))


def test_policy_rejects_missing_and_reordered_pools(tmp_path: Path) -> None:
    missing = _default_payload()
    missing_pools = missing["pools"]
    assert isinstance(missing_pools, list)
    missing_pools.pop()

    reordered = _default_payload()
    reordered_pools = reordered["pools"]
    assert isinstance(reordered_pools, list)
    reordered_pools.reverse()

    for payload in (missing, reordered):
        with pytest.raises(ValueError, match="five canonical pools"):
            load_retrieval_shadow_policy_v3(_write_policy(tmp_path, _dump_yaml(payload)))


def test_policy_rejects_unknown_pool(tmp_path: Path) -> None:
    payload = _default_payload()
    pools = payload["pools"]
    assert isinstance(pools, list)
    first_pool = pools[0]
    assert isinstance(first_pool, dict)
    first_pool["pool_id"] = "unknown"
    with pytest.raises(ValueError, match="Input should be"):
        load_retrieval_shadow_policy_v3(_write_policy(tmp_path, _dump_yaml(payload)))


def test_policy_is_strict_and_rejects_unknown_fields(tmp_path: Path) -> None:
    payload = _default_payload()
    payload["unknown"] = "forbidden"
    text = _dump_yaml(payload) + "unknown: forbidden\n"
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_retrieval_shadow_policy_v3(_write_policy(tmp_path, text))

    payload = _default_payload()
    pools = payload["pools"]
    assert isinstance(pools, list)
    first_pool = pools[0]
    assert isinstance(first_pool, dict)
    first_pool["candidate_limit"] = True
    with pytest.raises(ValueError, match="valid integer"):
        load_retrieval_shadow_policy_v3(_write_policy(tmp_path, _dump_yaml(payload)))


@pytest.mark.parametrize("value", (".nan", ".inf", "-.inf"))
def test_policy_rejects_non_finite_weights(tmp_path: Path, value: str) -> None:
    text = DEFAULT_RETRIEVAL_SHADOW_POLICY_V3_PATH.read_text(encoding="utf-8")
    text = text.replace("rrf_weight: 0.25", f"rrf_weight: {value}", 1)
    with pytest.raises(ValueError, match="finite number"):
        load_retrieval_shadow_policy_v3(_write_policy(tmp_path, text))


def test_policy_rejects_weight_that_rounds_to_zero_at_max_rank(tmp_path: Path) -> None:
    payload = _default_payload()
    pools = payload["pools"]
    assert isinstance(pools, list)
    first_pool = pools[0]
    assert isinstance(first_pool, dict)
    first_pool["rrf_weight"] = 1e-10

    with pytest.raises(ValueError, match="too small for the frozen RRF precision"):
        load_retrieval_shadow_policy_v3(_write_policy(tmp_path, _dump_yaml(payload)))


def test_policy_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    text = DEFAULT_RETRIEVAL_SHADOW_POLICY_V3_PATH.read_text(encoding="utf-8")
    text = text.replace("rrf_k: 60", "rrf_k: 60\nrrf_k: 60", 1)
    with pytest.raises(ValueError, match="duplicate key"):
        load_retrieval_shadow_policy_v3(_write_policy(tmp_path, text))


def test_policy_rejects_symlink(tmp_path: Path) -> None:
    target = _write_policy(
        tmp_path,
        DEFAULT_RETRIEVAL_SHADOW_POLICY_V3_PATH.read_text(encoding="utf-8"),
    )
    link = tmp_path / "policy-link.yaml"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular non-symlink"):
        load_retrieval_shadow_policy_v3(link)


def test_packaged_default_path_has_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import arkts_code_reviewer.retrieval.shadow_policy_v3 as policy_module

    packaged = tmp_path / "packaged"
    repository = tmp_path / "repository"
    packaged.mkdir()
    (repository / "config").mkdir(parents=True)
    packaged_policy = packaged / "retrieval_shadow_v3.yaml"
    repository_policy = repository / "config" / "retrieval_shadow_v3.yaml"
    packaged_policy.write_text("packaged", encoding="utf-8")
    repository_policy.write_text("repository", encoding="utf-8")

    monkeypatch.setattr(policy_module, "_PACKAGED_DEFAULTS", packaged)
    monkeypatch.setattr(policy_module, "_REPO_ROOT", repository)
    assert policy_module._default_policy_path() == packaged_policy


def test_direct_model_validation_uses_active_base_config() -> None:
    payload = _default_payload()
    validated = RetrievalShadowPolicyV3.model_validate(payload)
    assert (
        validated.base_retrieval_config_fingerprint == load_default_retrieval_config().fingerprint
    )
