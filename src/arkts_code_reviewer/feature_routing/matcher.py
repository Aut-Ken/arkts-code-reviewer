from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Protocol

from arkts_code_reviewer.feature_routing.config import (
    FeatureConfig,
    TagDefinition,
    TagTriggers,
)

SignalPair = tuple[str, str]


class FeatureFacts(Protocol):
    @property
    def components(self) -> Collection[str]: ...

    @property
    def apis(self) -> Collection[str]: ...

    @property
    def decorators(self) -> Collection[str]: ...

    @property
    def attributes(self) -> Collection[str]: ...

    @property
    def symbols(self) -> Collection[str]: ...

    @property
    def syntax(self) -> Collection[str]: ...


def match_signal_pairs(
    definition: TagDefinition,
    facts: FeatureFacts,
    *,
    include_owner_aware_import_uses: bool = False,
) -> tuple[SignalPair, ...]:
    triggers = definition.triggers
    signals: set[SignalPair] = set()
    _exact_signals(signals, "components", facts.components, triggers.any_component)
    _exact_signals(signals, "apis", facts.apis, triggers.any_api)
    _pattern_signals(signals, facts.apis, triggers, prefix=True)
    _pattern_signals(signals, facts.apis, triggers, prefix=False)
    if include_owner_aware_import_uses:
        _exact_signals(
            signals,
            "import_uses",
            getattr(facts, "import_uses", ()),
            triggers.any_import_use,
        )
    _exact_signals(signals, "decorators", facts.decorators, triggers.any_decorator)
    _exact_signals(signals, "attributes", facts.attributes, triggers.any_attribute)
    _exact_signals(signals, "symbols", facts.symbols, triggers.any_symbol)
    _exact_signals(signals, "syntax", facts.syntax, triggers.any_syntax)
    if triggers.has_resource_reference:
        signals.update(
            ("resource_references", value)
            for value in getattr(facts, "resource_references", ())
        )
    return tuple(sorted(signals))


def active_tag_ids(facts: FeatureFacts, config: FeatureConfig) -> set[str]:
    return {
        definition.id
        for definition in config.tags_by_id.values()
        if definition.status == "Active" and match_signal_pairs(definition, facts)
    }


def active_dimension_ids(tags: set[str], config: FeatureConfig) -> list[str]:
    return sorted(
        definition.id
        for definition in config.dimensions_by_id.values()
        if definition.status == "Active"
        and (
            definition.always_check
            or bool(set(definition.triggers.any_tag).intersection(tags))
        )
    )


def _exact_signals(
    target: set[SignalPair],
    kind: str,
    values: Collection[str],
    configured: tuple[str, ...],
) -> None:
    target.update((kind, value) for value in set(values).intersection(configured))


def _pattern_signals(
    target: set[SignalPair],
    values: Collection[str],
    triggers: TagTriggers,
    *,
    prefix: bool,
) -> None:
    patterns = triggers.any_api_prefix if prefix else triggers.any_api_suffix
    for value in values:
        if any(
            value.startswith(pattern) if prefix else value.endswith(pattern)
            for pattern in patterns
        ):
            target.add(("apis", value))


def registered_ids(config: FeatureConfig) -> Mapping[str, tuple[str, ...]]:
    return {
        "tags": tuple(config.tags_by_id),
        "dimensions": tuple(config.dimensions_by_id),
        "review_questions": tuple(config.review_questions_by_id),
    }


__all__ = [
    "FeatureFacts",
    "SignalPair",
    "active_dimension_ids",
    "active_tag_ids",
    "match_signal_pairs",
    "registered_ids",
]
