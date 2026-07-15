from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

import pytest

from arkts_code_reviewer.code_analysis.file_analysis_models import (
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.feature_routing.config import (
    DEFAULT_DIMENSIONS_PATH,
    DEFAULT_TAGS_PATH,
    FeatureConfig,
    load_feature_config,
)
from arkts_code_reviewer.feature_routing.engine import FeatureRouter
from arkts_code_reviewer.feature_routing.models import (
    FEATURE_ROUTING_SCHEMA_VERSION,
    FEATURE_ROUTING_V2_SCHEMA_VERSION,
    FeatureRoutingResult,
    FeatureRoutingSchemaVersion,
    FeatureSignal,
    NormalizedFeatureSignal,
    TagMatch,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
V2_TAGS_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "feature_routing"
    / "tag_config_relational_store_api_shadow_v1.yaml"
)
V3_TAGS_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "feature_routing"
    / "tag_config_lifecycle_symbol_leaf_shadow_v1.yaml"
)
HISTORICAL_PROFILE_ID = (
    "feature-profile:sha256:127dbf87aedb77846e0dd6b63fb11a937f6a3e395b924b304c3a87b21c3c413d"
)
HISTORICAL_RESULT_ID = (
    "feature-routing:sha256:eda9ce81c4ecc45560497deda04ad8a738d1e8873c001bb35727938cc0036d5b"
)


def _scope(
    unit_id: str,
    *,
    source_suffix: str = "a",
    facts: ScopedFacts | None = None,
) -> UnitFactScope:
    selected_facts = ScopedFacts() if facts is None else facts
    return UnitFactScope(
        unit_id=unit_id,
        source_ref_id=f"code-source:sha256:{source_suffix * 64}",
        unit_exact=selected_facts,
        file_hints=selected_facts,
    )


def _load_config(tags_path: Path) -> FeatureConfig:
    return load_feature_config(tags_path, DEFAULT_DIMENSIONS_PATH)


def _rebuild_result(
    result: FeatureRoutingResult,
    *,
    schema_version: FeatureRoutingSchemaVersion,
) -> FeatureRoutingResult:
    return FeatureRoutingResult.create(
        feature_config_version=result.feature_config_version,
        tags_config_version=result.tags_config_version,
        dimensions_config_version=result.dimensions_config_version,
        units=result.units,
        mr_dimensions=result.mr_dimensions,
        question_bindings=result.question_bindings,
        diagnostics=result.diagnostics,
        schema_version=schema_version,
    )


def test_legacy_feature_signal_dataclass_and_serialized_shape_are_frozen() -> None:
    signal = FeatureSignal(kind="symbols", value="Index.aboutToDisappear")

    assert tuple(field.name for field in fields(FeatureSignal)) == ("kind", "value")
    assert asdict(signal) == {
        "kind": "symbols",
        "value": "Index.aboutToDisappear",
    }
    assert signal.to_dict() == asdict(signal)


def test_default_v1_profile_and_result_identities_are_frozen() -> None:
    result = FeatureRouter().route([_scope("unit:x", facts=ScopedFacts(apis=("setInterval",)))])

    assert result.schema_version == FEATURE_ROUTING_SCHEMA_VERSION
    assert result.units[0].profile_id == HISTORICAL_PROFILE_ID
    assert result.feature_routing_id == HISTORICAL_RESULT_ID


@pytest.mark.parametrize(
    ("tags_path", "tag_schema", "routing_schema"),
    [
        (DEFAULT_TAGS_PATH, "tag-config-v1", FEATURE_ROUTING_SCHEMA_VERSION),
        (V2_TAGS_PATH, "tag-config-v2", FEATURE_ROUTING_SCHEMA_VERSION),
        (V3_TAGS_PATH, "tag-config-v3", FEATURE_ROUTING_V2_SCHEMA_VERSION),
    ],
)
def test_tag_config_schema_selects_routing_schema_for_empty_and_no_match_routes(
    tags_path: Path,
    tag_schema: str,
    routing_schema: str,
) -> None:
    config = _load_config(tags_path)
    router = FeatureRouter(config)

    empty_result = router.route([])
    no_match_result = router.route([_scope("unit:no-match", source_suffix="b")])

    assert config.tag_config.schema_version == tag_schema
    assert empty_result.schema_version == routing_schema
    assert no_match_result.schema_version == routing_schema
    assert no_match_result.units[0].tag_matches == ()


def test_v1_rejects_enriched_signal_and_v2_accepts_legacy_base_signal() -> None:
    enriched_result = FeatureRouter(_load_config(V3_TAGS_PATH)).route(
        [
            _scope(
                "unit:lifecycle",
                facts=ScopedFacts(symbols=("Index.aboutToDisappear",)),
            )
        ]
    )
    enriched_signal = enriched_result.units[0].tag_matches[0].signals[0]

    assert type(enriched_signal) is NormalizedFeatureSignal
    with pytest.raises(ValueError, match="schema does not support"):
        _rebuild_result(
            enriched_result,
            schema_version=FEATURE_ROUTING_SCHEMA_VERSION,
        )

    legacy_result = FeatureRouter().route(
        [_scope("unit:legacy", facts=ScopedFacts(apis=("setInterval",)))]
    )
    v2_legacy_result = _rebuild_result(
        legacy_result,
        schema_version=FEATURE_ROUTING_V2_SCHEMA_VERSION,
    )

    assert all(
        type(signal) is FeatureSignal
        for match in v2_legacy_result.units[0].tag_matches
        for signal in match.signals
    )
    assert v2_legacy_result.schema_version == FEATURE_ROUTING_V2_SCHEMA_VERSION
    assert v2_legacy_result.feature_routing_id != legacy_result.feature_routing_id


def test_unknown_feature_signal_subclass_is_rejected() -> None:
    @dataclass(frozen=True)
    class UnknownFeatureSignal(FeatureSignal):
        pass

    with pytest.raises(ValueError, match="unsupported FeatureSignal implementation"):
        TagMatch(
            tag_id="has_lifecycle",
            status="Active",
            scope="unit_exact",
            signals=(
                UnknownFeatureSignal(
                    kind="symbols",
                    value="Index.aboutToDisappear",
                ),
            ),
        )


def test_v2_result_identity_is_deterministic_and_replays_with_its_config() -> None:
    config = _load_config(V3_TAGS_PATH)
    scope = _scope(
        "unit:lifecycle",
        source_suffix="c",
        facts=ScopedFacts(symbols=("Index.aboutToDisappear",)),
    )
    router = FeatureRouter(config)

    result = router.route([scope])
    repeated = router.route([scope])
    payload = FeatureRoutingResult.identity_payload(
        feature_config_version=result.feature_config_version,
        tags_config_version=result.tags_config_version,
        dimensions_config_version=result.dimensions_config_version,
        units=result.units,
        mr_dimensions=result.mr_dimensions,
        question_bindings=result.question_bindings,
        diagnostics=result.diagnostics,
        schema_version=result.schema_version,
    )

    assert result.schema_version == FEATURE_ROUTING_V2_SCHEMA_VERSION
    assert result == repeated
    assert result.feature_routing_id == repeated.feature_routing_id
    assert payload["schema_version"] == FEATURE_ROUTING_V2_SCHEMA_VERSION
    result.validate_replay([scope], config)

    with pytest.raises(ValueError, match="identity does not match"):
        replace(result, feature_routing_id="feature-routing:sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="does not replay"):
        result.validate_replay([scope])
