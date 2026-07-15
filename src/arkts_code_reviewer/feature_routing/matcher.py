from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from arkts_code_reviewer.feature_routing.config import (
    FeatureConfig,
    TagDefinition,
    TagTriggers,
)

SignalPair = tuple[str, str]


@dataclass(frozen=True)
class SignalMatch:
    kind: str
    value: str
    operator: Literal["any_symbol_leaf"] | None = None
    normalized_value: str | None = None


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
    return tuple(
        sorted(
            {
                (signal.kind, signal.value)
                for signal in match_signals(
                    definition,
                    facts,
                    include_owner_aware_import_uses=include_owner_aware_import_uses,
                )
            }
        )
    )


def match_signals(
    definition: TagDefinition,
    facts: FeatureFacts,
    *,
    include_owner_aware_import_uses: bool = False,
) -> tuple[SignalMatch, ...]:
    triggers = definition.triggers
    signals: set[SignalMatch] = set()
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
    _symbol_leaf_signals(signals, facts.symbols, triggers.any_symbol_leaf)
    _exact_signals(signals, "syntax", facts.syntax, triggers.any_syntax)
    if triggers.has_resource_reference:
        signals.update(
            SignalMatch("resource_references", value)
            for value in getattr(facts, "resource_references", ())
        )
    return tuple(
        sorted(
            signals,
            key=lambda signal: (
                signal.kind,
                signal.value,
                signal.operator or "",
                signal.normalized_value or "",
            ),
        )
    )


def active_tag_ids(facts: FeatureFacts, config: FeatureConfig) -> set[str]:
    return {
        definition.id
        for definition in config.tags_by_id.values()
        if definition.status == "Active" and match_signals(definition, facts)
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
    target: set[SignalMatch],
    kind: str,
    values: Collection[str],
    configured: tuple[str, ...],
) -> None:
    target.update(
        SignalMatch(kind, value)
        for value in set(values).intersection(configured)
    )


def _symbol_leaf_signals(
    target: set[SignalMatch],
    values: Collection[str],
    configured: tuple[str, ...],
) -> None:
    configured_leaves = set(configured)
    for value in values:
        leaf = value.rsplit(".", 1)[-1]
        if leaf in configured_leaves:
            target.add(
                SignalMatch(
                    kind="symbols",
                    value=value,
                    operator="any_symbol_leaf",
                    normalized_value=leaf,
                )
            )


def _pattern_signals(
    target: set[SignalMatch],
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
            target.add(SignalMatch("apis", value))


def registered_ids(config: FeatureConfig) -> Mapping[str, tuple[str, ...]]:
    return {
        "tags": tuple(config.tags_by_id),
        "dimensions": tuple(config.dimensions_by_id),
        "review_questions": tuple(config.review_questions_by_id),
    }


__all__ = [
    "FeatureFacts",
    "SignalMatch",
    "SignalPair",
    "active_dimension_ids",
    "active_tag_ids",
    "match_signal_pairs",
    "match_signals",
    "registered_ids",
]
