from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import TYPE_CHECKING, cast

from arkts_code_reviewer.feature_routing.config import (
    DimensionDefinition,
    FeatureConfig,
    TagDefinition,
    load_default_feature_config,
)
from arkts_code_reviewer.feature_routing.matcher import (
    FeatureFacts,
    active_dimension_ids,
    active_tag_ids,
    match_signal_pairs,
)
from arkts_code_reviewer.feature_routing.models import (
    DimensionRoute,
    FeatureRoutingResult,
    FeatureSignal,
    FeatureSignalKind,
    ReviewQuestionBinding,
    RouteSignalScope,
    SignalScope,
    TagMatch,
    UnitFeatureProfile,
)

if TYPE_CHECKING:
    from arkts_code_reviewer.code_analysis.file_analysis_models import UnitFactScope


@lru_cache(maxsize=1)
def default_feature_config() -> FeatureConfig:
    return load_default_feature_config()


class FeatureRouter:
    def __init__(self, config: FeatureConfig | None = None) -> None:
        if config is not None and not isinstance(config, FeatureConfig):
            raise ValueError("FeatureRouter.config must use FeatureConfig or None")
        self.config = default_feature_config() if config is None else config

    def route(
        self,
        scopes: Sequence[UnitFactScope],
    ) -> FeatureRoutingResult:
        # Keep the Feature Routing package importable without initializing the
        # Code Analysis package.  The concrete runtime type is needed only when
        # a route operation is actually requested.
        from arkts_code_reviewer.code_analysis.file_analysis_models import UnitFactScope

        if not isinstance(scopes, Sequence) or isinstance(scopes, str | bytes):
            raise ValueError("FeatureRouter.scopes must be a sequence")
        normalized = tuple(scopes)
        if any(not isinstance(scope, UnitFactScope) for scope in normalized):
            raise ValueError("FeatureRouter scopes must contain UnitFactScope values")
        unit_ids = [scope.unit_id for scope in normalized]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("FeatureRouter scopes contain duplicate unit_id values")
        profiles = tuple(
            sorted(
                (self._profile(scope) for scope in normalized),
                key=lambda profile: profile.unit_id,
            )
        )
        question_bindings = tuple(
            ReviewQuestionBinding(profile.unit_id, question_id)
            for profile in profiles
            for question_id in profile.review_question_ids
        )
        mr_dimensions = tuple(
            sorted(
                {
                    dimension_id
                    for profile in profiles
                    for dimension_id in (*profile.dimensions, *profile.routing_dimensions)
                }
            )
        )
        return FeatureRoutingResult.create(
            feature_config_version=self.config.fingerprint,
            tags_config_version=self.config.tag_config.version,
            dimensions_config_version=self.config.dimension_config.version,
            units=profiles,
            mr_dimensions=mr_dimensions,
            question_bindings=question_bindings,
        )

    def _profile(self, scope: UnitFactScope) -> UnitFeatureProfile:
        tag_matches = tuple(
            sorted(
                (
                    *self._tag_matches(scope.unit_exact, "unit_exact"),
                    *self._tag_matches(scope.file_hints, "file_hint"),
                ),
                key=lambda match: (match.tag_id, match.status, match.scope),
            )
        )
        exact_tags = tuple(
            match.tag_id
            for match in tag_matches
            if match.status == "Active" and match.scope == "unit_exact"
        )
        routing_tags = tuple(
            match.tag_id
            for match in tag_matches
            if match.status == "Active" and match.scope == "file_hint"
        )
        shadow_exact_tags = tuple(
            match.tag_id
            for match in tag_matches
            if match.status == "Draft" and match.scope == "unit_exact"
        )
        shadow_routing_tags = tuple(
            match.tag_id
            for match in tag_matches
            if match.status == "Draft" and match.scope == "file_hint"
        )
        routes = tuple(
            self._dimension_route(definition, exact_tags, routing_tags)
            for definition in self.config.dimensions_by_id.values()
            if definition.status == "Active"
        )
        shadow_dimensions = tuple(
            definition.id
            for definition in self.config.dimensions_by_id.values()
            if definition.status == "Draft"
            and (
                definition.always_check
                or set(definition.triggers.any_tag).intersection(
                    (*exact_tags, *shadow_exact_tags)
                )
            )
        )
        review_question_ids, shadow_review_question_ids = self._questions(
            exact_tags,
            shadow_exact_tags,
        )
        return UnitFeatureProfile.create(
            unit_id=scope.unit_id,
            source_ref_id=scope.source_ref_id,
            feature_config_version=self.config.fingerprint,
            exact_tags=exact_tags,
            routing_tags=routing_tags,
            shadow_exact_tags=shadow_exact_tags,
            shadow_routing_tags=shadow_routing_tags,
            tag_matches=tag_matches,
            dimensions=tuple(route.dimension_id for route in routes if route.review_enabled),
            always_check_dimensions=tuple(
                route.dimension_id for route in routes if route.always_check
            ),
            retrieval_dimensions=tuple(
                route.dimension_id for route in routes if route.retrieval_enabled
            ),
            routing_dimensions=tuple(
                route.dimension_id for route in routes if route.routing_enabled
            ),
            shadow_dimensions=shadow_dimensions,
            dimension_routes=routes,
            review_question_ids=review_question_ids,
            shadow_review_question_ids=shadow_review_question_ids,
            diagnostics=tuple(scope.diagnostics),
        )

    def _tag_matches(
        self,
        facts: FeatureFacts,
        scope: SignalScope,
    ) -> tuple[TagMatch, ...]:
        matches: list[TagMatch] = []
        for definition in self.config.tags_by_id.values():
            if definition.status == "Deprecated":
                continue
            signals = _match_signals(
                definition,
                facts,
                include_owner_aware_import_uses=scope == "unit_exact",
            )
            if not signals:
                continue
            matches.append(
                TagMatch(
                    tag_id=definition.id,
                    status=definition.status,
                    scope=scope,
                    signals=signals,
                )
            )
        return tuple(matches)

    def _dimension_route(
        self,
        definition: DimensionDefinition,
        exact_tags: tuple[str, ...],
        routing_tags: tuple[str, ...],
    ) -> DimensionRoute:
        trigger_tags = set(definition.triggers.any_tag)
        exact_matches = tuple(sorted(trigger_tags.intersection(exact_tags)))
        routing_matches = tuple(
            sorted(trigger_tags.intersection(routing_tags) - set(exact_matches))
        )
        signal_scope: RouteSignalScope
        if exact_matches and routing_matches:
            signal_scope = "mixed"
        elif exact_matches:
            signal_scope = "unit_exact"
        elif routing_matches:
            signal_scope = "file_hint"
        else:
            signal_scope = "none"
        has_exact = bool(exact_matches)
        has_any = has_exact or bool(routing_matches)
        retrieval_enabled = definition.retrieval_policy == "always" or (
            definition.retrieval_policy == "signal_required" and has_exact
        )
        routing_enabled = definition.retrieval_policy == "always" or (
            definition.retrieval_policy == "signal_required" and has_any
        )
        return DimensionRoute(
            dimension_id=definition.id,
            always_check=definition.always_check,
            retrieval_policy=definition.retrieval_policy,
            review_enabled=definition.always_check or has_exact,
            retrieval_enabled=retrieval_enabled,
            routing_enabled=routing_enabled,
            signal_scope=signal_scope,
            matched_exact_tags=exact_matches,
            matched_routing_tags=routing_matches,
        )

    def _questions(
        self,
        exact_tags: tuple[str, ...],
        shadow_exact_tags: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        active: list[str] = []
        shadow: list[str] = []
        exact = set(exact_tags)
        shadow_exact = set(shadow_exact_tags)
        for definition in self.config.review_questions_by_id.values():
            triggered = definition.always_bind or bool(
                exact.intersection(definition.triggers.any_tag)
            )
            if definition.status == "Active" and triggered:
                active.append(definition.id)
            elif definition.status == "Draft" and (
                definition.always_bind
                or bool((exact | shadow_exact).intersection(definition.triggers.any_tag))
            ):
                shadow.append(definition.id)
        return tuple(active), tuple(shadow)


def derive_active_tags(
    facts: FeatureFacts,
    config: FeatureConfig | None = None,
) -> set[str]:
    active_config = default_feature_config() if config is None else config
    return active_tag_ids(facts, active_config)


def derive_active_dimensions(
    tags: set[str],
    config: FeatureConfig | None = None,
) -> list[str]:
    active_config = default_feature_config() if config is None else config
    return active_dimension_ids(tags, active_config)


def _match_signals(
    definition: TagDefinition,
    facts: FeatureFacts,
    *,
    include_owner_aware_import_uses: bool = False,
) -> tuple[FeatureSignal, ...]:
    return tuple(
        FeatureSignal(kind=cast(FeatureSignalKind, kind), value=value)
        for kind, value in match_signal_pairs(
            definition,
            facts,
            include_owner_aware_import_uses=include_owner_aware_import_uses,
        )
    )


__all__ = [
    "FeatureRouter",
    "default_feature_config",
    "derive_active_dimensions",
    "derive_active_tags",
]
