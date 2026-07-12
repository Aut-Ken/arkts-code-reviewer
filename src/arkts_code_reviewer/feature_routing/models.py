from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from arkts_code_reviewer.code_analysis.file_analysis_models import UnitFactScope
    from arkts_code_reviewer.feature_routing.config import FeatureConfig

FEATURE_ROUTING_SCHEMA_VERSION = "feature-routing-v1"

SignalScope = Literal["unit_exact", "file_hint"]
RouteSignalScope = Literal["unit_exact", "file_hint", "mixed", "none"]
FeatureStatus = Literal["Active", "Draft"]
RetrievalPolicy = Literal["signal_required", "always", "disabled"]
FeatureSignalKind = Literal[
    "components",
    "apis",
    "decorators",
    "attributes",
    "symbols",
    "syntax",
    "import_bindings",
    "import_uses",
    "field_reads",
    "field_writes",
    "calls",
    "string_literals",
    "resource_references",
]

_SIGNAL_KINDS = {
    "components",
    "apis",
    "decorators",
    "attributes",
    "symbols",
    "syntax",
    "import_bindings",
    "import_uses",
    "field_reads",
    "field_writes",
    "calls",
    "string_literals",
    "resource_references",
}
_ROUTE_SCOPES = {"unit_exact", "file_hint", "mixed", "none"}
_RETRIEVAL_POLICIES = {"signal_required", "always", "disabled"}
_STATUSES = {"Active", "Draft"}
_PROFILE_DIAGNOSTICS = {"unit_owner_unresolved"}


def _stable_id(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        _json_ready(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _json_ready(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_ready(to_dict())
    return value


def _non_empty(value: str, context: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")


def _sorted_unique(values: tuple[str, ...], context: str) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{context} must be a tuple")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{context} must contain non-empty strings")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")


@dataclass(frozen=True)
class FeatureSignal:
    kind: FeatureSignalKind
    value: str

    def __post_init__(self) -> None:
        if self.kind not in _SIGNAL_KINDS:
            raise ValueError(f"unsupported FeatureSignal.kind: {self.kind}")
        _non_empty(self.value, "FeatureSignal.value")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value}


@dataclass(frozen=True)
class TagMatch:
    tag_id: str
    status: FeatureStatus
    scope: SignalScope
    signals: tuple[FeatureSignal, ...]

    def __post_init__(self) -> None:
        _non_empty(self.tag_id, "TagMatch.tag_id")
        if self.status not in _STATUSES:
            raise ValueError(f"unsupported TagMatch.status: {self.status}")
        if self.scope not in {"unit_exact", "file_hint"}:
            raise ValueError(f"unsupported TagMatch.scope: {self.scope}")
        if not isinstance(self.signals, tuple) or not self.signals:
            raise ValueError("TagMatch.signals must not be empty")
        if any(not isinstance(signal, FeatureSignal) for signal in self.signals):
            raise ValueError("TagMatch.signals must contain FeatureSignal values")
        keys = [(signal.kind, signal.value) for signal in self.signals]
        if keys != sorted(set(keys)):
            raise ValueError("TagMatch.signals must be sorted and unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "tag_id": self.tag_id,
            "status": self.status,
            "scope": self.scope,
            "signals": [signal.to_dict() for signal in self.signals],
        }


@dataclass(frozen=True)
class DimensionRoute:
    dimension_id: str
    always_check: bool
    retrieval_policy: RetrievalPolicy
    review_enabled: bool
    retrieval_enabled: bool
    routing_enabled: bool
    signal_scope: RouteSignalScope
    matched_exact_tags: tuple[str, ...]
    matched_routing_tags: tuple[str, ...]

    def __post_init__(self) -> None:
        _non_empty(self.dimension_id, "DimensionRoute.dimension_id")
        if self.retrieval_policy not in _RETRIEVAL_POLICIES:
            raise ValueError(
                f"unsupported DimensionRoute.retrieval_policy: {self.retrieval_policy}"
            )
        for value, context in (
            (self.always_check, "always_check"),
            (self.review_enabled, "review_enabled"),
            (self.retrieval_enabled, "retrieval_enabled"),
            (self.routing_enabled, "routing_enabled"),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"DimensionRoute.{context} must be boolean")
        if self.signal_scope not in _ROUTE_SCOPES:
            raise ValueError(f"unsupported DimensionRoute.signal_scope: {self.signal_scope}")
        _sorted_unique(self.matched_exact_tags, "DimensionRoute.matched_exact_tags")
        _sorted_unique(self.matched_routing_tags, "DimensionRoute.matched_routing_tags")
        has_exact = bool(self.matched_exact_tags)
        has_routing = bool(self.matched_routing_tags)
        expected_scope: RouteSignalScope
        if has_exact and has_routing:
            expected_scope = "mixed"
        elif has_exact:
            expected_scope = "unit_exact"
        elif has_routing:
            expected_scope = "file_hint"
        else:
            expected_scope = "none"
        if self.signal_scope != expected_scope:
            raise ValueError("DimensionRoute.signal_scope does not match its signals")
        if self.review_enabled != (self.always_check or has_exact):
            raise ValueError("DimensionRoute.review_enabled does not match policy")
        expected_retrieval = self.retrieval_policy == "always" or (
            self.retrieval_policy == "signal_required" and has_exact
        )
        if self.retrieval_enabled != expected_retrieval:
            raise ValueError("DimensionRoute.retrieval_enabled does not match policy")
        expected_routing = self.retrieval_policy == "always" or (
            self.retrieval_policy == "signal_required" and (has_exact or has_routing)
        )
        if self.routing_enabled != expected_routing:
            raise ValueError("DimensionRoute.routing_enabled does not match policy")

    def to_dict(self) -> dict[str, object]:
        return {
            "dimension_id": self.dimension_id,
            "always_check": self.always_check,
            "retrieval_policy": self.retrieval_policy,
            "review_enabled": self.review_enabled,
            "retrieval_enabled": self.retrieval_enabled,
            "routing_enabled": self.routing_enabled,
            "signal_scope": self.signal_scope,
            "matched_exact_tags": list(self.matched_exact_tags),
            "matched_routing_tags": list(self.matched_routing_tags),
        }


@dataclass(frozen=True)
class ReviewQuestionBinding:
    primary_unit_id: str
    review_question_id: str

    def __post_init__(self) -> None:
        _non_empty(
            self.primary_unit_id,
            "ReviewQuestionBinding.primary_unit_id",
        )
        _non_empty(
            self.review_question_id,
            "ReviewQuestionBinding.review_question_id",
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "primary_unit_id": self.primary_unit_id,
            "review_question_id": self.review_question_id,
        }


@dataclass(frozen=True)
class UnitFeatureProfile:
    profile_id: str
    unit_id: str
    source_ref_id: str
    feature_config_version: str
    exact_tags: tuple[str, ...]
    routing_tags: tuple[str, ...]
    shadow_exact_tags: tuple[str, ...]
    shadow_routing_tags: tuple[str, ...]
    tag_matches: tuple[TagMatch, ...]
    dimensions: tuple[str, ...]
    always_check_dimensions: tuple[str, ...]
    retrieval_dimensions: tuple[str, ...]
    routing_dimensions: tuple[str, ...]
    shadow_dimensions: tuple[str, ...]
    dimension_routes: tuple[DimensionRoute, ...]
    review_question_ids: tuple[str, ...]
    shadow_review_question_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        unit_id: str,
        source_ref_id: str,
        feature_config_version: str,
        exact_tags: tuple[str, ...],
        routing_tags: tuple[str, ...],
        shadow_exact_tags: tuple[str, ...],
        shadow_routing_tags: tuple[str, ...],
        tag_matches: tuple[TagMatch, ...],
        dimensions: tuple[str, ...],
        always_check_dimensions: tuple[str, ...],
        retrieval_dimensions: tuple[str, ...],
        routing_dimensions: tuple[str, ...],
        shadow_dimensions: tuple[str, ...],
        dimension_routes: tuple[DimensionRoute, ...],
        review_question_ids: tuple[str, ...],
        shadow_review_question_ids: tuple[str, ...],
        diagnostics: tuple[str, ...],
    ) -> UnitFeatureProfile:
        payload = cls.identity_payload(
            unit_id=unit_id,
            source_ref_id=source_ref_id,
            feature_config_version=feature_config_version,
            exact_tags=exact_tags,
            routing_tags=routing_tags,
            shadow_exact_tags=shadow_exact_tags,
            shadow_routing_tags=shadow_routing_tags,
            tag_matches=tag_matches,
            dimensions=dimensions,
            always_check_dimensions=always_check_dimensions,
            retrieval_dimensions=retrieval_dimensions,
            routing_dimensions=routing_dimensions,
            shadow_dimensions=shadow_dimensions,
            dimension_routes=dimension_routes,
            review_question_ids=review_question_ids,
            shadow_review_question_ids=shadow_review_question_ids,
            diagnostics=diagnostics,
        )
        return cls(
            profile_id=_stable_id("feature-profile", payload),
            unit_id=unit_id,
            source_ref_id=source_ref_id,
            feature_config_version=feature_config_version,
            exact_tags=exact_tags,
            routing_tags=routing_tags,
            shadow_exact_tags=shadow_exact_tags,
            shadow_routing_tags=shadow_routing_tags,
            tag_matches=tag_matches,
            dimensions=dimensions,
            always_check_dimensions=always_check_dimensions,
            retrieval_dimensions=retrieval_dimensions,
            routing_dimensions=routing_dimensions,
            shadow_dimensions=shadow_dimensions,
            dimension_routes=dimension_routes,
            review_question_ids=review_question_ids,
            shadow_review_question_ids=shadow_review_question_ids,
            diagnostics=diagnostics,
        )

    @staticmethod
    def identity_payload(
        *,
        unit_id: str,
        source_ref_id: str,
        feature_config_version: str,
        exact_tags: tuple[str, ...],
        routing_tags: tuple[str, ...],
        shadow_exact_tags: tuple[str, ...],
        shadow_routing_tags: tuple[str, ...],
        tag_matches: tuple[TagMatch, ...],
        dimensions: tuple[str, ...],
        always_check_dimensions: tuple[str, ...],
        retrieval_dimensions: tuple[str, ...],
        routing_dimensions: tuple[str, ...],
        shadow_dimensions: tuple[str, ...],
        dimension_routes: tuple[DimensionRoute, ...],
        review_question_ids: tuple[str, ...],
        shadow_review_question_ids: tuple[str, ...],
        diagnostics: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "unit_id": unit_id,
            "source_ref_id": source_ref_id,
            "feature_config_version": feature_config_version,
            "exact_tags": exact_tags,
            "routing_tags": routing_tags,
            "shadow_exact_tags": shadow_exact_tags,
            "shadow_routing_tags": shadow_routing_tags,
            "tag_matches": tag_matches,
            "dimensions": dimensions,
            "always_check_dimensions": always_check_dimensions,
            "retrieval_dimensions": retrieval_dimensions,
            "routing_dimensions": routing_dimensions,
            "shadow_dimensions": shadow_dimensions,
            "dimension_routes": dimension_routes,
            "review_question_ids": review_question_ids,
            "shadow_review_question_ids": shadow_review_question_ids,
            "diagnostics": diagnostics,
        }

    def __post_init__(self) -> None:
        _non_empty(self.unit_id, "UnitFeatureProfile.unit_id")
        _non_empty(self.source_ref_id, "UnitFeatureProfile.source_ref_id")
        if not self.source_ref_id.startswith("code-source:sha256:"):
            raise ValueError("UnitFeatureProfile.source_ref_id must use CodeSourceRef identity")
        if not self.feature_config_version.startswith("feature-config:sha256:"):
            raise ValueError("UnitFeatureProfile requires a feature config fingerprint")
        for values, context in (
            (self.exact_tags, "exact_tags"),
            (self.routing_tags, "routing_tags"),
            (self.shadow_exact_tags, "shadow_exact_tags"),
            (self.shadow_routing_tags, "shadow_routing_tags"),
            (self.dimensions, "dimensions"),
            (self.always_check_dimensions, "always_check_dimensions"),
            (self.retrieval_dimensions, "retrieval_dimensions"),
            (self.routing_dimensions, "routing_dimensions"),
            (self.shadow_dimensions, "shadow_dimensions"),
            (self.review_question_ids, "review_question_ids"),
            (self.shadow_review_question_ids, "shadow_review_question_ids"),
            (self.diagnostics, "diagnostics"),
        ):
            _sorted_unique(values, f"UnitFeatureProfile.{context}")
        if not set(self.diagnostics).issubset(_PROFILE_DIAGNOSTICS):
            raise ValueError("UnitFeatureProfile.diagnostics contains unknown codes")
        if not isinstance(self.tag_matches, tuple) or any(
            not isinstance(match, TagMatch) for match in self.tag_matches
        ):
            raise ValueError(
                "UnitFeatureProfile.tag_matches must contain TagMatch values"
            )
        match_keys = [(match.tag_id, match.status, match.scope) for match in self.tag_matches]
        if match_keys != sorted(set(match_keys)):
            raise ValueError("UnitFeatureProfile.tag_matches must be sorted and unique")
        exact_from_matches = tuple(
            match.tag_id
            for match in self.tag_matches
            if match.status == "Active" and match.scope == "unit_exact"
        )
        routing_from_matches = tuple(
            match.tag_id
            for match in self.tag_matches
            if match.status == "Active" and match.scope == "file_hint"
        )
        shadow_exact_from_matches = tuple(
            match.tag_id
            for match in self.tag_matches
            if match.status == "Draft" and match.scope == "unit_exact"
        )
        shadow_routing_from_matches = tuple(
            match.tag_id
            for match in self.tag_matches
            if match.status == "Draft" and match.scope == "file_hint"
        )
        if self.exact_tags != exact_from_matches:
            raise ValueError("UnitFeatureProfile.exact_tags do not match activation trace")
        if self.routing_tags != routing_from_matches:
            raise ValueError("UnitFeatureProfile.routing_tags do not match activation trace")
        if self.shadow_exact_tags != shadow_exact_from_matches:
            raise ValueError("UnitFeatureProfile.shadow_exact_tags do not match activation trace")
        if self.shadow_routing_tags != shadow_routing_from_matches:
            raise ValueError("UnitFeatureProfile.shadow_routing_tags do not match activation trace")
        if not isinstance(self.dimension_routes, tuple) or any(
            not isinstance(route, DimensionRoute) for route in self.dimension_routes
        ):
            raise ValueError(
                "UnitFeatureProfile.dimension_routes must contain DimensionRoute values"
            )
        route_ids = [route.dimension_id for route in self.dimension_routes]
        if route_ids != sorted(set(route_ids)):
            raise ValueError("UnitFeatureProfile.dimension_routes must use stable Dimension order")
        if self.dimensions != tuple(
            route.dimension_id for route in self.dimension_routes if route.review_enabled
        ):
            raise ValueError("UnitFeatureProfile.dimensions do not match Dimension routes")
        if self.always_check_dimensions != tuple(
            route.dimension_id for route in self.dimension_routes if route.always_check
        ):
            raise ValueError("always_check_dimensions do not match Dimension routes")
        if self.retrieval_dimensions != tuple(
            route.dimension_id for route in self.dimension_routes if route.retrieval_enabled
        ):
            raise ValueError("retrieval_dimensions do not match Dimension routes")
        if self.routing_dimensions != tuple(
            route.dimension_id for route in self.dimension_routes if route.routing_enabled
        ):
            raise ValueError("routing_dimensions do not match Dimension routes")
        expected_id = _stable_id(
            "feature-profile",
            self.identity_payload(
                unit_id=self.unit_id,
                source_ref_id=self.source_ref_id,
                feature_config_version=self.feature_config_version,
                exact_tags=self.exact_tags,
                routing_tags=self.routing_tags,
                shadow_exact_tags=self.shadow_exact_tags,
                shadow_routing_tags=self.shadow_routing_tags,
                tag_matches=self.tag_matches,
                dimensions=self.dimensions,
                always_check_dimensions=self.always_check_dimensions,
                retrieval_dimensions=self.retrieval_dimensions,
                routing_dimensions=self.routing_dimensions,
                shadow_dimensions=self.shadow_dimensions,
                dimension_routes=self.dimension_routes,
                review_question_ids=self.review_question_ids,
                shadow_review_question_ids=self.shadow_review_question_ids,
                diagnostics=self.diagnostics,
            ),
        )
        if self.profile_id != expected_id:
            raise ValueError("UnitFeatureProfile.profile_id does not match its fields")

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "unit_id": self.unit_id,
            "source_ref_id": self.source_ref_id,
            "feature_config_version": self.feature_config_version,
            "exact_tags": list(self.exact_tags),
            "routing_tags": list(self.routing_tags),
            "shadow_exact_tags": list(self.shadow_exact_tags),
            "shadow_routing_tags": list(self.shadow_routing_tags),
            "tag_matches": [match.to_dict() for match in self.tag_matches],
            "dimensions": list(self.dimensions),
            "always_check_dimensions": list(self.always_check_dimensions),
            "retrieval_dimensions": list(self.retrieval_dimensions),
            "routing_dimensions": list(self.routing_dimensions),
            "shadow_dimensions": list(self.shadow_dimensions),
            "dimension_routes": [route.to_dict() for route in self.dimension_routes],
            "review_question_ids": list(self.review_question_ids),
            "shadow_review_question_ids": list(self.shadow_review_question_ids),
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class FeatureRoutingResult:
    feature_routing_id: str
    feature_config_version: str
    tags_config_version: str
    dimensions_config_version: str
    units: tuple[UnitFeatureProfile, ...]
    mr_dimensions: tuple[str, ...]
    question_bindings: tuple[ReviewQuestionBinding, ...]
    diagnostics: tuple[str, ...] = ()
    schema_version: str = FEATURE_ROUTING_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        feature_config_version: str,
        tags_config_version: str,
        dimensions_config_version: str,
        units: tuple[UnitFeatureProfile, ...],
        mr_dimensions: tuple[str, ...],
        question_bindings: tuple[ReviewQuestionBinding, ...],
        diagnostics: tuple[str, ...] = (),
    ) -> FeatureRoutingResult:
        payload = cls.identity_payload(
            feature_config_version=feature_config_version,
            tags_config_version=tags_config_version,
            dimensions_config_version=dimensions_config_version,
            units=units,
            mr_dimensions=mr_dimensions,
            question_bindings=question_bindings,
            diagnostics=diagnostics,
        )
        return cls(
            feature_routing_id=_stable_id("feature-routing", payload),
            feature_config_version=feature_config_version,
            tags_config_version=tags_config_version,
            dimensions_config_version=dimensions_config_version,
            units=units,
            mr_dimensions=mr_dimensions,
            question_bindings=question_bindings,
            diagnostics=diagnostics,
        )

    @staticmethod
    def identity_payload(
        *,
        feature_config_version: str,
        tags_config_version: str,
        dimensions_config_version: str,
        units: tuple[UnitFeatureProfile, ...],
        mr_dimensions: tuple[str, ...],
        question_bindings: tuple[ReviewQuestionBinding, ...],
        diagnostics: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "feature_config_version": feature_config_version,
            "tags_config_version": tags_config_version,
            "dimensions_config_version": dimensions_config_version,
            "units": units,
            "mr_dimensions": mr_dimensions,
            "question_bindings": question_bindings,
            "diagnostics": diagnostics,
        }

    def __post_init__(self) -> None:
        if self.schema_version != FEATURE_ROUTING_SCHEMA_VERSION:
            raise ValueError("FeatureRoutingResult.schema_version is unsupported")
        if not self.feature_config_version.startswith("feature-config:sha256:"):
            raise ValueError("FeatureRoutingResult requires a feature config fingerprint")
        _non_empty(self.tags_config_version, "FeatureRoutingResult.tags_config_version")
        _non_empty(
            self.dimensions_config_version,
            "FeatureRoutingResult.dimensions_config_version",
        )
        if not isinstance(self.units, tuple) or any(
            not isinstance(unit, UnitFeatureProfile) for unit in self.units
        ):
            raise ValueError("FeatureRoutingResult.units contains invalid profiles")
        unit_ids = [unit.unit_id for unit in self.units]
        if unit_ids != sorted(set(unit_ids)):
            raise ValueError("FeatureRoutingResult.units must use stable unit_id order")
        if any(
            unit.feature_config_version != self.feature_config_version
            for unit in self.units
        ):
            raise ValueError("FeatureRoutingResult profile config versions disagree")
        _sorted_unique(self.mr_dimensions, "FeatureRoutingResult.mr_dimensions")
        _sorted_unique(self.diagnostics, "FeatureRoutingResult.diagnostics")
        if not isinstance(self.question_bindings, tuple) or any(
            not isinstance(binding, ReviewQuestionBinding)
            for binding in self.question_bindings
        ):
            raise ValueError("FeatureRoutingResult.question_bindings has invalid type")
        binding_keys = [
            (binding.primary_unit_id, binding.review_question_id)
            for binding in self.question_bindings
        ]
        if binding_keys != sorted(set(binding_keys)):
            raise ValueError("FeatureRoutingResult.question_bindings must be sorted and unique")
        expected_bindings = [
            (unit.unit_id, question_id)
            for unit in self.units
            for question_id in unit.review_question_ids
        ]
        if binding_keys != sorted(expected_bindings):
            raise ValueError("FeatureRoutingResult.question_bindings do not match profiles")
        expected_mr = tuple(
            sorted(
                {
                    dimension_id
                    for unit in self.units
                    for dimension_id in (*unit.dimensions, *unit.routing_dimensions)
                }
            )
        )
        if self.mr_dimensions != expected_mr:
            raise ValueError("FeatureRoutingResult.mr_dimensions do not match Unit routes")
        expected_id = _stable_id(
            "feature-routing",
            self.identity_payload(
                feature_config_version=self.feature_config_version,
                tags_config_version=self.tags_config_version,
                dimensions_config_version=self.dimensions_config_version,
                units=self.units,
                mr_dimensions=self.mr_dimensions,
                question_bindings=self.question_bindings,
                diagnostics=self.diagnostics,
            ),
        )
        if self.feature_routing_id != expected_id:
            raise ValueError("FeatureRoutingResult identity does not match its graph")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "feature_routing_id": self.feature_routing_id,
            "feature_config_version": self.feature_config_version,
            "tags_config_version": self.tags_config_version,
            "dimensions_config_version": self.dimensions_config_version,
            "units": [unit.to_dict() for unit in self.units],
            "mr_dimensions": list(self.mr_dimensions),
            "question_bindings": [
                binding.to_dict() for binding in self.question_bindings
            ],
            "diagnostics": list(self.diagnostics),
        }

    def validate_replay(
        self,
        scopes: Sequence[UnitFactScope],
        config: FeatureConfig | None = None,
    ) -> None:
        from arkts_code_reviewer.feature_routing.engine import FeatureRouter

        expected = FeatureRouter(config).route(scopes)
        if self != expected:
            raise ValueError(
                "FeatureRoutingResult does not replay from its UnitFactScopes"
            )


__all__ = [
    "FEATURE_ROUTING_SCHEMA_VERSION",
    "DimensionRoute",
    "FeatureRoutingResult",
    "FeatureSignal",
    "FeatureStatus",
    "RouteSignalScope",
    "ReviewQuestionBinding",
    "SignalScope",
    "TagMatch",
    "UnitFeatureProfile",
]
