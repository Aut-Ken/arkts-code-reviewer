from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from arkts_code_reviewer.feature_routing.owner_context import (
    OWNER_CONTEXT_DIAGNOSTICS,
    OwnerAwareRoutingInput,
    OwnerRole,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from arkts_code_reviewer.code_analysis.file_analysis_models import UnitFactScope
    from arkts_code_reviewer.feature_routing.config import FeatureConfig

FEATURE_ROUTING_SCHEMA_VERSION: Literal["feature-routing-v1"] = "feature-routing-v1"
FEATURE_ROUTING_V2_SCHEMA_VERSION: Literal["feature-routing-v2"] = "feature-routing-v2"
FEATURE_ROUTING_V3_SCHEMA_VERSION: Literal["feature-routing-v3"] = "feature-routing-v3"
FeatureRoutingSchemaVersion = Literal[
    "feature-routing-v1",
    "feature-routing-v2",
    "feature-routing-v3",
]

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
_PROFILE_DIAGNOSTICS = {"unit_owner_unresolved", *OWNER_CONTEXT_DIAGNOSTICS}
_RESULT_DIAGNOSTICS: set[str] = set()


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

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "value": self.value}


@dataclass(frozen=True)
class NormalizedFeatureSignal(FeatureSignal):
    operator: Literal["any_symbol_leaf"]
    normalized_value: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.operator != "any_symbol_leaf":
            raise ValueError(f"unsupported NormalizedFeatureSignal.operator: {self.operator}")
        if self.kind != "symbols":
            raise ValueError("NormalizedFeatureSignal.any_symbol_leaf requires kind=symbols")
        _non_empty(
            self.normalized_value,
            "NormalizedFeatureSignal.normalized_value",
        )
        if "." in self.normalized_value:
            raise ValueError("NormalizedFeatureSignal.normalized_value must be unqualified")
        if self.value.rsplit(".", 1)[-1] != self.normalized_value:
            raise ValueError("NormalizedFeatureSignal.normalized_value does not match value")

    def to_dict(self) -> dict[str, object]:
        return {
            **super().to_dict(),
            "operator": self.operator,
            "normalized_value": self.normalized_value,
        }


@dataclass(frozen=True)
class FileSymbolLeafFeatureSignal(FeatureSignal):
    operator: Literal["any_file_symbol_leaf"]
    normalized_value: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.kind != "symbols":
            raise ValueError(
                "FileSymbolLeafFeatureSignal.any_file_symbol_leaf requires kind=symbols"
            )
        if self.operator != "any_file_symbol_leaf":
            raise ValueError(f"unsupported FileSymbolLeafFeatureSignal.operator: {self.operator}")
        _validate_symbol_leaf(
            self.value,
            self.normalized_value,
            "FileSymbolLeafFeatureSignal",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            **super().to_dict(),
            "operator": self.operator,
            "normalized_value": self.normalized_value,
        }


@dataclass(frozen=True)
class UnitSymbolLeafOwnerRoleFeatureSignal(FeatureSignal):
    operator: Literal["any_unit_symbol_leaf_with_owner_role"]
    normalized_value: str
    owner_role: OwnerRole
    symbol_occurrence_id: str
    direct_owner_declaration_id: str
    enclosing_owner_declaration_id: str
    role_evidence_occurrence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.kind != "symbols":
            raise ValueError(
                "UnitSymbolLeafOwnerRoleFeatureSignal."
                "any_unit_symbol_leaf_with_owner_role requires kind=symbols"
            )
        if self.operator != "any_unit_symbol_leaf_with_owner_role":
            raise ValueError(
                f"unsupported UnitSymbolLeafOwnerRoleFeatureSignal.operator: {self.operator}"
            )
        _validate_symbol_leaf(
            self.value,
            self.normalized_value,
            "UnitSymbolLeafOwnerRoleFeatureSignal",
        )
        if self.owner_role not in {
            "arkui_custom_component",
            "arkui_router_page",
        }:
            raise ValueError("UnitSymbolLeafOwnerRoleFeatureSignal.owner_role is unsupported")
        if not self.symbol_occurrence_id.startswith("occurrence:"):
            raise ValueError(
                "UnitSymbolLeafOwnerRoleFeatureSignal requires symbol occurrence identity"
            )
        for declaration_id in (
            self.direct_owner_declaration_id,
            self.enclosing_owner_declaration_id,
        ):
            if not declaration_id.startswith("declaration:"):
                raise ValueError(
                    "UnitSymbolLeafOwnerRoleFeatureSignal owner IDs must use declaration identity"
                )
        if self.direct_owner_declaration_id == self.enclosing_owner_declaration_id:
            raise ValueError(
                "UnitSymbolLeafOwnerRoleFeatureSignal direct and enclosing owners must differ"
            )
        _sorted_unique(
            self.role_evidence_occurrence_ids,
            "UnitSymbolLeafOwnerRoleFeatureSignal.role_evidence_occurrence_ids",
        )
        if not self.role_evidence_occurrence_ids or any(
            not occurrence_id.startswith("occurrence:")
            for occurrence_id in self.role_evidence_occurrence_ids
        ):
            raise ValueError(
                "UnitSymbolLeafOwnerRoleFeatureSignal role evidence must use occurrence identities"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            **super().to_dict(),
            "operator": self.operator,
            "normalized_value": self.normalized_value,
            "owner_role": self.owner_role,
            "symbol_occurrence_id": self.symbol_occurrence_id,
            "direct_owner_declaration_id": self.direct_owner_declaration_id,
            "enclosing_owner_declaration_id": self.enclosing_owner_declaration_id,
            "role_evidence_occurrence_ids": list(self.role_evidence_occurrence_ids),
        }


def _validate_symbol_leaf(value: str, leaf: str, context: str) -> None:
    _non_empty(leaf, f"{context}.normalized_value")
    if "." in leaf:
        raise ValueError(f"{context}.normalized_value must be unqualified")
    if value.rsplit(".", 1)[-1] != leaf:
        raise ValueError(f"{context}.normalized_value does not match value")


def _feature_signal_key(signal: FeatureSignal) -> tuple[str, ...]:
    if type(signal) is FeatureSignal:
        return (signal.kind, signal.value, "", "")
    if type(signal) is NormalizedFeatureSignal:
        normalized = signal
        return (
            normalized.kind,
            normalized.value,
            normalized.operator,
            normalized.normalized_value,
        )
    if type(signal) is FileSymbolLeafFeatureSignal:
        file_hint = signal
        return (
            file_hint.kind,
            file_hint.value,
            file_hint.operator,
            file_hint.normalized_value,
        )
    if type(signal) is UnitSymbolLeafOwnerRoleFeatureSignal:
        owner_aware = signal
        return (
            owner_aware.kind,
            owner_aware.value,
            owner_aware.operator,
            owner_aware.normalized_value,
            owner_aware.owner_role,
            owner_aware.symbol_occurrence_id,
            owner_aware.direct_owner_declaration_id,
            owner_aware.enclosing_owner_declaration_id,
            *owner_aware.role_evidence_occurrence_ids,
        )
    raise ValueError("unsupported FeatureSignal implementation")


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
        if (
            any(type(signal) is FileSymbolLeafFeatureSignal for signal in self.signals)
            and self.scope != "file_hint"
        ):
            raise ValueError("FileSymbolLeafFeatureSignal is restricted to file_hint scope")
        if (
            any(type(signal) is UnitSymbolLeafOwnerRoleFeatureSignal for signal in self.signals)
            and self.scope != "unit_exact"
        ):
            raise ValueError(
                "UnitSymbolLeafOwnerRoleFeatureSignal is restricted to unit_exact scope"
            )
        keys = [_feature_signal_key(signal) for signal in self.signals]
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
            raise ValueError("UnitFeatureProfile.tag_matches must contain TagMatch values")
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
    schema_version: FeatureRoutingSchemaVersion = FEATURE_ROUTING_SCHEMA_VERSION

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
        schema_version: FeatureRoutingSchemaVersion = FEATURE_ROUTING_SCHEMA_VERSION,
    ) -> FeatureRoutingResult:
        payload = cls.identity_payload(
            feature_config_version=feature_config_version,
            tags_config_version=tags_config_version,
            dimensions_config_version=dimensions_config_version,
            units=units,
            mr_dimensions=mr_dimensions,
            question_bindings=question_bindings,
            diagnostics=diagnostics,
            schema_version=schema_version,
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
            schema_version=schema_version,
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
        schema_version: FeatureRoutingSchemaVersion = FEATURE_ROUTING_SCHEMA_VERSION,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "feature_config_version": feature_config_version,
            "tags_config_version": tags_config_version,
            "dimensions_config_version": dimensions_config_version,
            "units": units,
            "mr_dimensions": mr_dimensions,
            "question_bindings": question_bindings,
            "diagnostics": diagnostics,
        }
        if schema_version in {
            FEATURE_ROUTING_V2_SCHEMA_VERSION,
            FEATURE_ROUTING_V3_SCHEMA_VERSION,
        }:
            payload["schema_version"] = schema_version
        return payload

    def __post_init__(self) -> None:
        if self.schema_version not in {
            FEATURE_ROUTING_SCHEMA_VERSION,
            FEATURE_ROUTING_V2_SCHEMA_VERSION,
            FEATURE_ROUTING_V3_SCHEMA_VERSION,
        }:
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
        if any(unit.feature_config_version != self.feature_config_version for unit in self.units):
            raise ValueError("FeatureRoutingResult profile config versions disagree")
        allowed_signal_types_by_schema: dict[
            FeatureRoutingSchemaVersion,
            set[type[FeatureSignal]],
        ] = {
            FEATURE_ROUTING_SCHEMA_VERSION: {FeatureSignal},
            FEATURE_ROUTING_V2_SCHEMA_VERSION: {
                FeatureSignal,
                NormalizedFeatureSignal,
            },
            FEATURE_ROUTING_V3_SCHEMA_VERSION: {
                FeatureSignal,
                NormalizedFeatureSignal,
                FileSymbolLeafFeatureSignal,
                UnitSymbolLeafOwnerRoleFeatureSignal,
            },
        }
        allowed_signal_types = allowed_signal_types_by_schema[self.schema_version]
        if any(
            type(signal) not in allowed_signal_types
            for unit in self.units
            for match in unit.tag_matches
            for signal in match.signals
        ):
            raise ValueError(
                "FeatureRoutingResult schema does not support its FeatureSignal values"
            )
        _sorted_unique(self.mr_dimensions, "FeatureRoutingResult.mr_dimensions")
        _sorted_unique(self.diagnostics, "FeatureRoutingResult.diagnostics")
        if not set(self.diagnostics).issubset(_RESULT_DIAGNOSTICS):
            raise ValueError("FeatureRoutingResult.diagnostics contains unknown codes")
        if not isinstance(self.question_bindings, tuple) or any(
            not isinstance(binding, ReviewQuestionBinding) for binding in self.question_bindings
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
                schema_version=self.schema_version,
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
            "question_bindings": [binding.to_dict() for binding in self.question_bindings],
            "diagnostics": list(self.diagnostics),
        }

    def validate_replay(
        self,
        scopes: Sequence[UnitFactScope],
        config: FeatureConfig | None = None,
    ) -> None:
        from arkts_code_reviewer.feature_routing.engine import FeatureRouter

        if self.schema_version == FEATURE_ROUTING_V3_SCHEMA_VERSION:
            raise ValueError("feature-routing-v3 requires validate_owner_aware_replay")

        expected = FeatureRouter(config).route(scopes)
        if self != expected:
            raise ValueError("FeatureRoutingResult does not replay from its UnitFactScopes")

    def validate_owner_aware_replay(
        self,
        inputs: Sequence[OwnerAwareRoutingInput],
        config: FeatureConfig | None = None,
    ) -> None:
        from arkts_code_reviewer.feature_routing.engine import FeatureRouter

        expected = FeatureRouter(config).route_owner_aware_shadow(inputs)
        if self != expected:
            raise ValueError("FeatureRoutingResult does not replay from owner-aware inputs")


__all__ = [
    "FEATURE_ROUTING_SCHEMA_VERSION",
    "FEATURE_ROUTING_V2_SCHEMA_VERSION",
    "FEATURE_ROUTING_V3_SCHEMA_VERSION",
    "DimensionRoute",
    "FeatureRoutingResult",
    "FeatureSignal",
    "FileSymbolLeafFeatureSignal",
    "FeatureRoutingSchemaVersion",
    "FeatureStatus",
    "NormalizedFeatureSignal",
    "RouteSignalScope",
    "ReviewQuestionBinding",
    "SignalScope",
    "TagMatch",
    "UnitSymbolLeafOwnerRoleFeatureSignal",
    "UnitFeatureProfile",
]
