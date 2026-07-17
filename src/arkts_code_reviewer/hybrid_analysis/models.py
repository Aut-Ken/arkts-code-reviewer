from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationError, ValidationInfo, field_validator, model_validator

from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    canonical_json,
    identity_payload,
    load_json_model,
    seal_payload,
)

REVIEW_UNIT_ANALYSIS_CARD_SCHEMA_VERSION = "review-unit-analysis-card-v1"
AI_TAG_MODEL_VIEW_SCHEMA_VERSION = "ai-tag-model-view-v1"
AI_TAG_CONTRACT_VIEW_SCHEMA_VERSION = "ai-tag-contract-view-v1"
AI_TAG_ANALYSIS_REQUEST_SCHEMA_VERSION = "ai-tag-analysis-request-v1"
AI_TAG_ANALYSIS_RESULT_SCHEMA_VERSION = "ai-tag-analysis-result-v1"
AI_TAG_EXECUTION_OUTCOME_SCHEMA_VERSION = "ai-tag-execution-outcome-v1"
HYBRID_FEATURE_ANALYSIS_RESULT_SCHEMA_VERSION = "hybrid-feature-analysis-result-v1"

ACTIVE_TAG_COUNT_V1 = 24

TagDecision = Literal["positive", "not_supported", "abstain"]
StaticDecision = Literal["positive", "unknown"]
ExecutionStatus = Literal[
    "valid_result",
    "unavailable",
    "skipped_budget",
    "not_run",
    "invalid_output",
]
UnitComparisonStatus = Literal[
    "agreement_positive",
    "disagreement",
    "static_only",
    "ai_only",
    "no_positive_signal",
    "unresolved",
    "static_only_due_execution",
    "unresolved_due_execution",
]

_HASH = r"[0-9a-f]{64}"
_CARD_ID = rf"^analysis-card:sha256:{_HASH}$"
_MODEL_VIEW_ID = rf"^ai-tag-model-view:sha256:{_HASH}$"
_CONTRACT_FINGERPRINT = rf"^ai-tag-contract-view:sha256:{_HASH}$"
_REQUEST_ID = rf"^ai-tag-request:sha256:{_HASH}$"
_RESULT_ID = rf"^ai-tag-result:sha256:{_HASH}$"
_OUTCOME_ID = rf"^ai-tag-outcome:sha256:{_HASH}$"
_ANALYSIS_RUN_ID = rf"^ai-tag-run:sha256:{_HASH}$"
_HYBRID_ANALYSIS_ID = rf"^hybrid-analysis:sha256:{_HASH}$"
_SOURCE_REF_ID = rf"^code-source:sha256:{_HASH}$"
_CHANGE_ATOM_ID = rf"^change-atom:sha256:{_HASH}$"
_RELATION_EDGE_ID = rf"^relation-edge:sha256:{_HASH}$"
_FEATURE_CONFIG_FINGERPRINT = rf"^feature-config:sha256:{_HASH}$"
_FEATURE_PROFILE_ID = rf"^feature-profile:sha256:{_HASH}$"
_FEATURE_ROUTING_ID = rf"^feature-routing:sha256:{_HASH}$"
_CONTEXT_PLAN_ID = rf"^context-plan:sha256:{_HASH}$"
_OCCURRENCE_ID = rf"^occurrence:sha256:{_HASH}$"
_DECLARATION_ID = rf"^declaration:sha256:{_HASH}$"
_CONTEXT_POLICY_FINGERPRINT = rf"^analysis-context-policy:sha256:{_HASH}$"
_PROJECTION_POLICY_FINGERPRINT = rf"^ai-model-view-policy:sha256:{_HASH}$"
_TAXONOMY_FINGERPRINT = rf"^ai-tag-taxonomy:sha256:{_HASH}$"
_MODEL_POLICY_FINGERPRINT = rf"^ai-tag-policy:sha256:{_HASH}$"
_BUDGET_SNAPSHOT_ID = rf"^ai-budget-snapshot:sha256:{_HASH}$"
_SHA256 = rf"^sha256:{_HASH}$"
_TAG_ID = r"^has_[a-z0-9_]+$"
_REASON_CODE = r"^[a-z][a-z0-9_]*$"

_ABSTAIN_REASON_CODES = {
    "conflicting_evidence",
    "context_degraded",
    "insufficient_context",
    "parser_degraded",
    "unit_owner_unresolved",
    "view_truncated",
}
_NO_SUPPORT_REASON_CODES = {"no_support_in_complete_view"}

_OUTCOME_REASON_CODES: dict[ExecutionStatus, set[str]] = {
    "valid_result": {"provider_response_valid"},
    "invalid_output": {
        "evidence_out_of_range",
        "incomplete_taxonomy",
        "invalid_json",
        "non_stop_finish_reason",
        "response_empty",
        "schema_invalid",
    },
    "unavailable": {
        "provider_client_error",
        "provider_rate_limited",
        "provider_server_error",
        "provider_timeout",
    },
    "skipped_budget": {"budget_exhausted"},
    "not_run": {
        "compliance_not_approved",
        "configuration_invalid",
        "taxonomy_mismatch",
    },
}

_VALID_COMPARISON_BY_AXES: dict[
    tuple[StaticDecision, TagDecision],
    UnitComparisonStatus,
] = {
    ("positive", "positive"): "agreement_positive",
    ("positive", "not_supported"): "disagreement",
    ("positive", "abstain"): "static_only",
    ("unknown", "positive"): "ai_only",
    ("unknown", "not_supported"): "no_positive_signal",
    ("unknown", "abstain"): "unresolved",
}


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _sorted_unique_strings(
    values: tuple[str, ...],
    context: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not allow_empty and not values:
        raise ValueError(f"{context} must not be empty")
    if any(not value or value != value.strip() for value in values):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _single_line(value: str, context: str) -> str:
    if (
        not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{context} must be non-empty, trimmed, and single-line")
    return value


def _validate_identity(model: FrozenModel, field: str, prefix: str, context: str) -> None:
    expected = canonical_hash(prefix, identity_payload(model, field))
    if getattr(model, field) != expected:
        raise ValueError(f"{context}.{field} does not match its complete contents")


def _revalidate[ModelT: FrozenModel](
    model: ModelT,
    model_type: type[ModelT],
    context: str,
) -> ModelT:
    try:
        return model_type.model_validate(model.model_dump(mode="json"))
    except ValidationError as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


class AnalysisCode(FrozenModel):
    mode: Literal["full_unit", "changed_window", "deletion_base"]
    text: Annotated[str, Field(min_length=1)]
    line_start: Annotated[int, Field(ge=1)]
    line_end: Annotated[int, Field(ge=1)]
    changed_line_numbers: tuple[Annotated[int, Field(ge=1)], ...]
    truncated: bool

    @field_validator("changed_line_numbers", mode="before")
    @classmethod
    def parse_changed_lines(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AnalysisCode.changed_line_numbers")

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("AnalysisCode.text must not contain NUL")
        return value

    @model_validator(mode="after")
    def validate_lines(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("AnalysisCode line range is inverted")
        if len(self.text.splitlines()) != self.line_end - self.line_start + 1:
            raise ValueError("AnalysisCode text line count must match its declared range")
        if not self.changed_line_numbers:
            raise ValueError("AnalysisCode.changed_line_numbers must not be empty")
        if self.changed_line_numbers != tuple(sorted(set(self.changed_line_numbers))):
            raise ValueError("AnalysisCode.changed_line_numbers must be sorted and unique")
        if any(
            line < self.line_start or line > self.line_end
            for line in self.changed_line_numbers
        ):
            raise ValueError("AnalysisCode changed lines must stay inside the code range")
        return self


class CodeFactSet(FrozenModel):
    apis: tuple[str, ...]
    components: tuple[str, ...]
    decorators: tuple[str, ...]
    attributes: tuple[str, ...]
    symbols: tuple[str, ...]
    syntax: tuple[str, ...]
    calls: tuple[str, ...]
    import_bindings: tuple[str, ...]
    import_uses: tuple[str, ...]
    field_reads: tuple[str, ...]
    field_writes: tuple[str, ...]
    string_literals: tuple[str, ...]
    resource_references: tuple[str, ...]

    @field_validator(
        "apis",
        "components",
        "decorators",
        "attributes",
        "symbols",
        "syntax",
        "calls",
        "import_bindings",
        "import_uses",
        "field_reads",
        "field_writes",
        "string_literals",
        "resource_references",
        mode="before",
    )
    @classmethod
    def parse_facts(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"CodeFactSet.{info.field_name}")

    @field_validator(
        "apis",
        "components",
        "decorators",
        "attributes",
        "symbols",
        "syntax",
        "calls",
        "import_bindings",
        "import_uses",
        "field_reads",
        "field_writes",
        "string_literals",
        "resource_references",
    )
    @classmethod
    def validate_facts(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _sorted_unique_strings(value, f"CodeFactSet.{info.field_name}")


class ScopedCodeFacts(FrozenModel):
    unit_exact: CodeFactSet
    file_hints: CodeFactSet


class BasicStaticFeatureSignal(FrozenModel):
    signal_type: Literal["basic"]
    kind: Literal[
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
    value: Annotated[str, Field(min_length=1)]

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        return _single_line(value, "BasicStaticFeatureSignal.value")


class NormalizedSymbolLeafSignal(FrozenModel):
    signal_type: Literal["normalized_symbol_leaf"]
    kind: Literal["symbols"]
    value: Annotated[str, Field(min_length=1)]
    operator: Literal["any_symbol_leaf"]
    normalized_value: Annotated[str, Field(min_length=1)]

    @field_validator("value", "normalized_value")
    @classmethod
    def validate_values(cls, value: str, info: ValidationInfo) -> str:
        value = _single_line(value, f"NormalizedSymbolLeafSignal.{info.field_name}")
        if info.field_name == "normalized_value" and "." in value:
            raise ValueError("normalized symbol leaf must be unqualified")
        return value

    @model_validator(mode="after")
    def validate_leaf(self) -> Self:
        if self.value.rsplit(".", 1)[-1] != self.normalized_value:
            raise ValueError("normalized symbol leaf does not match value")
        return self


class FileSymbolLeafSignal(FrozenModel):
    signal_type: Literal["file_symbol_leaf"]
    kind: Literal["symbols"]
    value: Annotated[str, Field(min_length=1)]
    operator: Literal["any_file_symbol_leaf"]
    normalized_value: Annotated[str, Field(min_length=1)]

    @field_validator("value", "normalized_value")
    @classmethod
    def validate_values(cls, value: str, info: ValidationInfo) -> str:
        value = _single_line(value, f"FileSymbolLeafSignal.{info.field_name}")
        if info.field_name == "normalized_value" and "." in value:
            raise ValueError("file symbol leaf must be unqualified")
        return value

    @model_validator(mode="after")
    def validate_leaf(self) -> Self:
        if self.value.rsplit(".", 1)[-1] != self.normalized_value:
            raise ValueError("file symbol leaf does not match value")
        return self


class UnitOwnerRoleSymbolSignal(FrozenModel):
    signal_type: Literal["unit_owner_role_symbol"]
    kind: Literal["symbols"]
    value: Annotated[str, Field(min_length=1)]
    operator: Literal["any_unit_symbol_leaf_with_owner_role"]
    normalized_value: Annotated[str, Field(min_length=1)]
    owner_role: Literal["arkui_custom_component", "arkui_router_page"]
    symbol_occurrence_id: Annotated[str, Field(pattern=_OCCURRENCE_ID)]
    direct_owner_declaration_id: Annotated[str, Field(pattern=_DECLARATION_ID)]
    enclosing_owner_declaration_id: Annotated[str, Field(pattern=_DECLARATION_ID)]
    role_evidence_occurrence_ids: tuple[
        Annotated[str, Field(pattern=_OCCURRENCE_ID)],
        ...,
    ]

    @field_validator("role_evidence_occurrence_ids", mode="before")
    @classmethod
    def parse_role_evidence(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "UnitOwnerRoleSymbolSignal.role_evidence_occurrence_ids")

    @field_validator("value", "normalized_value")
    @classmethod
    def validate_values(cls, value: str, info: ValidationInfo) -> str:
        value = _single_line(value, f"UnitOwnerRoleSymbolSignal.{info.field_name}")
        if info.field_name == "normalized_value" and "." in value:
            raise ValueError("owner-role symbol leaf must be unqualified")
        return value

    @model_validator(mode="after")
    def validate_signal(self) -> Self:
        if self.value.rsplit(".", 1)[-1] != self.normalized_value:
            raise ValueError("owner-role symbol leaf does not match value")
        if self.direct_owner_declaration_id == self.enclosing_owner_declaration_id:
            raise ValueError("owner-role signal direct and enclosing owners must differ")
        _sorted_unique_strings(
            self.role_evidence_occurrence_ids,
            "UnitOwnerRoleSymbolSignal.role_evidence_occurrence_ids",
            allow_empty=False,
        )
        return self


StaticFeatureSignal = Annotated[
    BasicStaticFeatureSignal
    | NormalizedSymbolLeafSignal
    | FileSymbolLeafSignal
    | UnitOwnerRoleSymbolSignal,
    Field(discriminator="signal_type"),
]


class StaticTagMatch(FrozenModel):
    tag_id: Annotated[str, Field(pattern=_TAG_ID)]
    status: Literal["Active"]
    scope: Literal["unit_exact", "file_hint"]
    signals: tuple[StaticFeatureSignal, ...]

    @field_validator("signals", mode="before")
    @classmethod
    def parse_signals(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "StaticTagMatch.signals")

    @model_validator(mode="after")
    def validate_signals(self) -> Self:
        if not self.signals:
            raise ValueError("StaticTagMatch.signals must not be empty")
        keys = tuple(canonical_json(item.model_dump(mode="json")) for item in self.signals)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("StaticTagMatch.signals must be sorted and unique")
        if any(
            isinstance(signal, FileSymbolLeafSignal) for signal in self.signals
        ) and self.scope != "file_hint":
            raise ValueError("file symbol-leaf signal requires file_hint scope")
        if any(
            isinstance(signal, UnitOwnerRoleSymbolSignal) for signal in self.signals
        ) and self.scope != "unit_exact":
            raise ValueError("owner-role symbol signal requires unit_exact scope")
        return self


class StaticTagSignals(FrozenModel):
    exact: tuple[Annotated[str, Field(pattern=_TAG_ID)], ...]
    routing: tuple[Annotated[str, Field(pattern=_TAG_ID)], ...]
    matches: tuple[StaticTagMatch, ...]

    @field_validator("exact", "routing", "matches", mode="before")
    @classmethod
    def parse_tags(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"StaticTagSignals.{info.field_name}")

    @field_validator("exact", "routing")
    @classmethod
    def validate_tags(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _sorted_unique_strings(value, f"StaticTagSignals.{info.field_name}")

    @model_validator(mode="after")
    def validate_matches(self) -> Self:
        match_keys = tuple((item.scope, item.tag_id) for item in self.matches)
        if match_keys != tuple(sorted(set(match_keys))):
            raise ValueError("StaticTagSignals.matches must be sorted and unique")
        expected_exact = tuple(
            sorted(item.tag_id for item in self.matches if item.scope == "unit_exact")
        )
        expected_routing = tuple(
            sorted(item.tag_id for item in self.matches if item.scope == "file_hint")
        )
        if self.exact != expected_exact or self.routing != expected_routing:
            raise ValueError("Static Tag lists must exactly match typed TagMatch provenance")
        return self


class AnalysisQuality(FrozenModel):
    parser_layer: Literal["L0", "L1", "parse_degraded"]
    error_nodes: Annotated[int | None, Field(ge=0)]
    missing_nodes: Annotated[int | None, Field(ge=0)]
    context_degraded: bool
    unit_owner_unresolved: bool

    @model_validator(mode="after")
    def validate_node_counts(self) -> Self:
        if (self.error_nodes is None) != (self.missing_nodes is None):
            raise ValueError("AnalysisQuality node counts must be provided together")
        if self.parser_layer == "L1" and self.error_nodes is None:
            raise ValueError("L1 quality must carry explicit AST node counts")
        if self.parser_layer != "L1" and self.error_nodes is not None:
            raise ValueError("Only L1 quality may carry AST node counts")
        return self


class OwnerIdentity(FrozenModel):
    kind: Literal["declaration", "region"]
    ref_id: Annotated[
        str,
        Field(pattern=rf"^(?:declaration|region):sha256:{_HASH}$"),
    ]
    owner_kind: Literal[
        "struct",
        "class",
        "function",
        "method",
        "build_method",
        "builder",
        "ui_block",
        "field_region",
        "import_region",
    ]
    qualified_name: Annotated[str, Field(min_length=1)]
    quality: Literal["exact", "recovered"]

    @field_validator("qualified_name")
    @classmethod
    def validate_qualified_name(cls, value: str) -> str:
        return _single_line(value, "OwnerIdentity.qualified_name")

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        expected_prefix = f"{self.kind}:"
        if not self.ref_id.startswith(expected_prefix):
            raise ValueError("OwnerIdentity.ref_id prefix must match kind")
        region_kind = self.owner_kind in {"field_region", "import_region"}
        if region_kind != (self.kind == "region"):
            raise ValueError("OwnerIdentity owner_kind must match declaration/region kind")
        return self


class OwnerSummary(FrozenModel):
    resolution: Literal["resolved", "partial", "unresolved", "not_applicable"]
    unit_owner: OwnerIdentity | None
    enclosing_owner: OwnerIdentity | None
    owner_roles: tuple[Literal["arkui_custom_component", "arkui_router_page"], ...]
    diagnostics: tuple[
        Literal[
            "owner_context_unit_unresolved",
            "owner_context_symbol_unresolved",
            "owner_context_symbol_ambiguous",
            "owner_context_enclosing_owner_unresolved",
            "owner_context_role_unresolved",
            "owner_context_role_ambiguous",
            "owner_context_recovered",
        ],
        ...,
    ]

    @field_validator("owner_roles", "diagnostics", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"OwnerSummary.{info.field_name}")

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        if self.owner_roles != tuple(sorted(set(self.owner_roles))):
            raise ValueError("OwnerSummary.owner_roles must be sorted and unique")
        if self.diagnostics != tuple(sorted(set(self.diagnostics))):
            raise ValueError("OwnerSummary.diagnostics must be sorted and unique")
        if (
            self.unit_owner is not None
            and self.enclosing_owner is not None
            and self.unit_owner.ref_id == self.enclosing_owner.ref_id
        ):
            if self.unit_owner.owner_kind != "struct":
                raise ValueError(
                    "Only a struct Unit may also be its own owner-role container"
                )
            if self.unit_owner != self.enclosing_owner:
                raise ValueError(
                    "A repeated immutable owner identity must have identical contents"
                )
        if self.resolution == "resolved":
            if self.unit_owner is None or self.diagnostics:
                raise ValueError("resolved OwnerSummary requires owner and no diagnostics")
            if self.unit_owner.quality != "exact" or (
                self.enclosing_owner is not None
                and self.enclosing_owner.quality != "exact"
            ):
                raise ValueError("resolved OwnerSummary requires exact owner identities")
        elif self.resolution == "partial":
            if self.unit_owner is None or not self.owner_roles or not self.diagnostics:
                raise ValueError(
                    "partial OwnerSummary requires owner, roles, and diagnostics"
                )
            if self.unit_owner.quality != "exact" or (
                self.enclosing_owner is not None
                and self.enclosing_owner.quality != "exact"
            ):
                raise ValueError("partial OwnerSummary roles require exact owner identities")
        elif self.resolution == "unresolved":
            if not self.diagnostics or self.owner_roles:
                raise ValueError(
                    "unresolved OwnerSummary requires diagnostics and no owner roles"
                )
        elif self.owner_roles or self.diagnostics or self.enclosing_owner is not None:
            raise ValueError(
                "not_applicable OwnerSummary cannot carry roles, diagnostics, or enclosing owner"
            )
        if self.owner_roles:
            if (
                self.resolution not in {"resolved", "partial"}
                or self.enclosing_owner is None
                or self.enclosing_owner.owner_kind != "struct"
            ):
                raise ValueError("owner roles require a resolved enclosing struct")
        return self


class AIModelOwnerSummary(FrozenModel):
    resolution: Literal["resolved", "partial", "unresolved", "not_applicable"]
    unit_owner_kind: Literal[
        "struct",
        "class",
        "function",
        "method",
        "build_method",
        "builder",
        "ui_block",
        "field_region",
        "import_region",
    ] | None
    unit_owner_qualified_name: str | None
    enclosing_owner_kind: Literal[
        "struct",
        "class",
        "function",
        "method",
        "build_method",
        "builder",
        "ui_block",
        "field_region",
        "import_region",
    ] | None
    enclosing_owner_qualified_name: str | None
    owner_roles: tuple[Literal["arkui_custom_component", "arkui_router_page"], ...]
    diagnostics: tuple[
        Literal[
            "owner_context_unit_unresolved",
            "owner_context_symbol_unresolved",
            "owner_context_symbol_ambiguous",
            "owner_context_enclosing_owner_unresolved",
            "owner_context_role_unresolved",
            "owner_context_role_ambiguous",
            "owner_context_recovered",
        ],
        ...,
    ]

    @field_validator("owner_roles", "diagnostics", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"AIModelOwnerSummary.{info.field_name}")

    @field_validator("unit_owner_qualified_name", "enclosing_owner_qualified_name")
    @classmethod
    def validate_names(cls, value: str | None, info: ValidationInfo) -> str | None:
        return (
            None
            if value is None
            else _single_line(value, f"AIModelOwnerSummary.{info.field_name}")
        )

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if (self.unit_owner_kind is None) != (self.unit_owner_qualified_name is None):
            raise ValueError("AI Model owner identity fields must be provided together")
        if (self.enclosing_owner_kind is None) != (
            self.enclosing_owner_qualified_name is None
        ):
            raise ValueError("AI Model enclosing owner fields must be provided together")
        if self.owner_roles != tuple(sorted(set(self.owner_roles))):
            raise ValueError("AIModelOwnerSummary.owner_roles must be sorted and unique")
        if self.diagnostics != tuple(sorted(set(self.diagnostics))):
            raise ValueError("AIModelOwnerSummary.diagnostics must be sorted and unique")
        if self.resolution == "resolved":
            if self.unit_owner_kind is None or self.diagnostics:
                raise ValueError("resolved AI Model owner summary is inconsistent")
        elif self.resolution == "partial":
            if (
                self.unit_owner_kind is None
                or not self.owner_roles
                or not self.diagnostics
            ):
                raise ValueError("partial AI Model owner summary is inconsistent")
        elif self.resolution == "unresolved":
            if not self.diagnostics or self.owner_roles:
                raise ValueError("unresolved AI Model owner summary is inconsistent")
        elif self.owner_roles or self.diagnostics or self.enclosing_owner_kind is not None:
            raise ValueError("not_applicable AI Model owner summary is inconsistent")
        if self.resolution == "not_applicable" and self.unit_owner_kind in {
            "method",
            "struct",
            "class",
        }:
            raise ValueError(
                "owner-aware AI Model Unit kind cannot use not_applicable resolution"
            )
        if self.owner_roles and self.enclosing_owner_kind != "struct":
            raise ValueError("AI Model owner roles require an enclosing struct")
        return self


class AvailableContextRef(FrozenModel):
    relation_edge_id: Annotated[str, Field(pattern=_RELATION_EDGE_ID)]
    target_unit_id: Annotated[str, Field(min_length=1)]
    relation_type: Literal[
        "lifecycle_pair",
        "state_access",
        "direct_call",
        "direct_caller",
        "change_correspondence",
        "same_host",
        "same_file",
    ]

    @field_validator("target_unit_id")
    @classmethod
    def validate_target_unit_id(cls, value: str) -> str:
        return _single_line(value, "AvailableContextRef.target_unit_id")


class _ReviewUnitAnalysisCardPayload(FrozenModel):
    schema_version: Literal["review-unit-analysis-card-v1"]
    unit_id: Annotated[str, Field(min_length=1)]
    source_ref_id: Annotated[str, Field(pattern=_SOURCE_REF_ID)]
    feature_profile_id: Annotated[str, Field(pattern=_FEATURE_PROFILE_ID)]
    feature_routing_id: Annotated[str, Field(pattern=_FEATURE_ROUTING_ID)]
    context_plan_id: Annotated[str, Field(pattern=_CONTEXT_PLAN_ID)]
    source_role: Literal["base", "head"]
    unit_kind: Literal[
        "struct",
        "class",
        "function",
        "method",
        "build_method",
        "builder",
        "ui_block",
        "field_region",
        "import_region",
        "fallback",
    ]
    unit_symbol: Annotated[str, Field(min_length=1)]
    owner_summary: OwnerSummary
    code: AnalysisCode
    change_atom_ids: tuple[Annotated[str, Field(pattern=_CHANGE_ATOM_ID)], ...]
    exact_occurrence_ids: tuple[Annotated[str, Field(pattern=_OCCURRENCE_ID)], ...]
    owner_context_occurrence_ids: tuple[
        Annotated[str, Field(pattern=_OCCURRENCE_ID)],
        ...,
    ]
    owner_context_declaration_ids: tuple[
        Annotated[str, Field(pattern=_DECLARATION_ID)],
        ...,
    ]
    unit_fact_diagnostics: tuple[Literal["unit_owner_unresolved"], ...]
    facts: ScopedCodeFacts
    static_tags: StaticTagSignals
    quality: AnalysisQuality
    available_context_refs: tuple[AvailableContextRef, ...]
    code_token_budget: Annotated[int, Field(ge=1)]
    feature_config_fingerprint: Annotated[
        str,
        Field(pattern=_FEATURE_CONFIG_FINGERPRINT),
    ]
    context_policy_fingerprint: Annotated[
        str,
        Field(pattern=_CONTEXT_POLICY_FINGERPRINT),
    ]

    @field_validator(
        "change_atom_ids",
        "exact_occurrence_ids",
        "owner_context_occurrence_ids",
        "owner_context_declaration_ids",
        "unit_fact_diagnostics",
        "available_context_refs",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"ReviewUnitAnalysisCard.{info.field_name}")

    @field_validator("unit_id", "unit_symbol")
    @classmethod
    def validate_text_identity(cls, value: str, info: ValidationInfo) -> str:
        return _single_line(value, f"ReviewUnitAnalysisCard.{info.field_name}")

    @model_validator(mode="after")
    def validate_card_payload(self) -> Self:
        _sorted_unique_strings(
            self.change_atom_ids,
            "ReviewUnitAnalysisCard.change_atom_ids",
            allow_empty=False,
        )
        _sorted_unique_strings(
            self.exact_occurrence_ids,
            "ReviewUnitAnalysisCard.exact_occurrence_ids",
        )
        _sorted_unique_strings(
            self.owner_context_occurrence_ids,
            "ReviewUnitAnalysisCard.owner_context_occurrence_ids",
        )
        _sorted_unique_strings(
            self.owner_context_declaration_ids,
            "ReviewUnitAnalysisCard.owner_context_declaration_ids",
        )
        _sorted_unique_strings(
            self.unit_fact_diagnostics,
            "ReviewUnitAnalysisCard.unit_fact_diagnostics",
        )
        context_keys = tuple(
            (item.relation_type, item.target_unit_id, item.relation_edge_id)
            for item in self.available_context_refs
        )
        if context_keys != tuple(sorted(set(context_keys))):
            raise ValueError(
                "ReviewUnitAnalysisCard.available_context_refs must be sorted and unique"
            )
        facts_by_scope = {
            "unit_exact": self.facts.unit_exact,
            "file_hint": self.facts.file_hints,
        }
        for match in self.static_tags.matches:
            scoped_facts = facts_by_scope[match.scope]
            for signal in match.signals:
                if signal.value not in getattr(scoped_facts, signal.kind):
                    raise ValueError(
                        "Static TagMatch signal is absent from its declared fact scope"
                    )
                if (
                    isinstance(signal, UnitOwnerRoleSymbolSignal)
                    and signal.owner_role not in self.owner_summary.owner_roles
                ):
                    raise ValueError(
                        "owner-role TagMatch signal differs from the owner summary"
                    )
                if isinstance(signal, UnitOwnerRoleSymbolSignal):
                    if signal.symbol_occurrence_id not in self.exact_occurrence_ids:
                        raise ValueError(
                            "owner-role TagMatch symbol occurrence is absent from Unit facts"
                        )
                    if not set(signal.role_evidence_occurrence_ids).issubset(
                        self.owner_context_occurrence_ids
                    ):
                        raise ValueError(
                            "owner-role TagMatch role evidence is absent from owner context"
                        )
                    if self.unit_kind == "method" and set(
                        signal.role_evidence_occurrence_ids
                    ).intersection(self.exact_occurrence_ids):
                        raise ValueError(
                            "method owner-role evidence cannot be a Unit exact occurrence"
                        )
                    if not {
                        signal.direct_owner_declaration_id,
                        signal.enclosing_owner_declaration_id,
                    }.issubset(self.owner_context_declaration_ids):
                        raise ValueError(
                            "owner-role TagMatch declarations are absent from owner context"
                        )
                    unit_owner = self.owner_summary.unit_owner
                    enclosing_owner = self.owner_summary.enclosing_owner
                    method_unit_mismatch = self.unit_kind == "method" and (
                        unit_owner is None
                        or unit_owner.ref_id != signal.direct_owner_declaration_id
                        or enclosing_owner is None
                        or enclosing_owner.ref_id
                        != signal.enclosing_owner_declaration_id
                    )
                    struct_unit_mismatch = self.unit_kind == "struct" and (
                        unit_owner is None
                        or unit_owner.ref_id != signal.enclosing_owner_declaration_id
                        or enclosing_owner is None
                        or enclosing_owner.ref_id
                        != signal.enclosing_owner_declaration_id
                    )
                    if self.unit_kind not in {"method", "struct"} or (
                        method_unit_mismatch or struct_unit_mismatch
                    ):
                        raise ValueError(
                            "owner-role TagMatch declarations differ from owner summary"
                        )
        if self.code.mode == "deletion_base" and self.source_role != "base":
            raise ValueError("deletion_base code must use the base source role")
        owner = self.owner_summary.unit_owner
        if self.unit_kind == "fallback":
            if (
                self.owner_summary.resolution != "not_applicable"
                or owner is not None
                or self.owner_summary.enclosing_owner is not None
                or self.quality.unit_owner_unresolved
                or self.unit_fact_diagnostics
            ):
                raise ValueError("fallback Analysis Card cannot carry owner context")
            if not self.quality.context_degraded:
                raise ValueError("fallback Analysis Card must be context degraded")
        elif self.quality.unit_owner_unresolved:
            if (
                owner is not None
                or self.owner_summary.resolution != "unresolved"
                or self.unit_fact_diagnostics != ("unit_owner_unresolved",)
            ):
                raise ValueError(
                    "owner-unresolved Analysis Card cannot carry a resolved Unit owner"
                )
        else:
            if self.unit_fact_diagnostics:
                raise ValueError(
                    "resolved Analysis Card cannot carry Unit fact diagnostics"
                )
            if (
                owner is None
                or owner.owner_kind != self.unit_kind
                or owner.qualified_name != self.unit_symbol
            ):
                raise ValueError(
                    "Analysis Card Unit owner must match its ReviewUnit kind and symbol"
                )
            if self.unit_kind in {"method", "struct", "class"} and (
                self.owner_summary.resolution == "not_applicable"
            ):
                raise ValueError(
                    "owner-aware Analysis Card Unit kind cannot use not_applicable resolution"
                )
        return self


class ReviewUnitAnalysisCard(_ReviewUnitAnalysisCardPayload):
    card_id: Annotated[str, Field(pattern=_CARD_ID)]

    @model_validator(mode="after")
    def validate_card_id(self) -> Self:
        _validate_identity(self, "card_id", "analysis-card", "ReviewUnitAnalysisCard")
        return self


class AIModelCode(FrozenModel):
    mode: Literal["full_unit", "changed_window", "deletion_base"]
    numbered_text: Annotated[str, Field(min_length=1)]
    line_numbers: tuple[Annotated[int, Field(ge=1)], ...]
    truncated: bool

    @field_validator("line_numbers", mode="before")
    @classmethod
    def parse_line_numbers(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AIModelCode.line_numbers")

    @field_validator("numbered_text")
    @classmethod
    def validate_numbered_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("AIModelCode.numbered_text must not contain NUL")
        return value

    @model_validator(mode="after")
    def validate_lines(self) -> Self:
        if not self.line_numbers:
            raise ValueError("AIModelCode.line_numbers must not be empty")
        if self.line_numbers != tuple(sorted(set(self.line_numbers))):
            raise ValueError("AIModelCode.line_numbers must be sorted and unique")
        rendered_lines = self.numbered_text.splitlines()
        prefixes = tuple(f"{line}:" for line in self.line_numbers)
        if len(rendered_lines) != len(prefixes) or any(
            not rendered.startswith(prefix)
            or (len(rendered) > len(prefix) and rendered[len(prefix)] != " ")
            for rendered, prefix in zip(rendered_lines, prefixes, strict=True)
        ):
            raise ValueError(
                "AIModelCode.numbered_text must exactly follow declared line-number order"
            )
        return self


class _AITagModelViewPayload(FrozenModel):
    schema_version: Literal["ai-tag-model-view-v1"]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    unit_id: Annotated[str, Field(min_length=1)]
    source_ref_id: Annotated[str, Field(pattern=_SOURCE_REF_ID)]
    code: AIModelCode
    owner_summary: AIModelOwnerSummary
    scoped_facts: ScopedCodeFacts
    quality: AnalysisQuality
    projection_policy_fingerprint: Annotated[
        str,
        Field(pattern=_PROJECTION_POLICY_FINGERPRINT),
    ]

    @field_validator("unit_id")
    @classmethod
    def validate_unit_id(cls, value: str) -> str:
        return _single_line(value, "AITagModelView.unit_id")


class AITagModelView(_AITagModelViewPayload):
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]

    @model_validator(mode="after")
    def validate_model_view_id(self) -> Self:
        _validate_identity(self, "model_view_id", "ai-tag-model-view", "AITagModelView")
        return self


class _AITagContractViewPayload(FrozenModel):
    schema_version: Literal["ai-tag-contract-view-v1"]
    tag_id: Annotated[str, Field(pattern=_TAG_ID)]
    definition: Annotated[str, Field(min_length=1)]
    inclusions: tuple[str, ...]
    exclusions: tuple[str, ...]
    hard_negatives: tuple[str, ...]

    @field_validator("inclusions", "exclusions", "hard_negatives", mode="before")
    @classmethod
    def parse_boundaries(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"AITagContractView.{info.field_name}")

    @field_validator("definition")
    @classmethod
    def validate_definition(cls, value: str) -> str:
        return _single_line(value, "AITagContractView.definition")

    @field_validator("inclusions", "exclusions", "hard_negatives")
    @classmethod
    def validate_boundaries(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _sorted_unique_strings(value, f"AITagContractView.{info.field_name}")


class AITagContractView(_AITagContractViewPayload):
    contract_fingerprint: Annotated[str, Field(pattern=_CONTRACT_FINGERPRINT)]

    @model_validator(mode="after")
    def validate_contract_fingerprint(self) -> Self:
        _validate_identity(
            self,
            "contract_fingerprint",
            "ai-tag-contract-view",
            "AITagContractView",
        )
        return self


class _AITagAnalysisRequestPayload(FrozenModel):
    schema_version: Literal["ai-tag-analysis-request-v1"]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    taxonomy_delivery_mode: Literal["full_single"]
    active_taxonomy_fingerprint: Annotated[
        str,
        Field(pattern=_TAXONOMY_FINGERPRINT),
    ]
    tag_contract_views: tuple[AITagContractView, ...]
    required_tag_count: Literal[24]
    prompt_version: Annotated[str, Field(min_length=1)]
    prompt_hash: Annotated[str, Field(pattern=_SHA256)]
    model_policy_fingerprint: Annotated[str, Field(pattern=_MODEL_POLICY_FINGERPRINT)]

    @field_validator("tag_contract_views", mode="before")
    @classmethod
    def parse_contracts(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AITagAnalysisRequest.tag_contract_views")

    @field_validator("prompt_version")
    @classmethod
    def validate_prompt_version(cls, value: str) -> str:
        return _single_line(value, "AITagAnalysisRequest.prompt_version")

    @model_validator(mode="after")
    def validate_taxonomy(self) -> Self:
        tag_ids = tuple(item.tag_id for item in self.tag_contract_views)
        if len(tag_ids) != ACTIVE_TAG_COUNT_V1:
            raise ValueError("AITagAnalysisRequest must contain exactly 24 Tag contracts")
        if tag_ids != tuple(sorted(set(tag_ids))):
            raise ValueError(
                "AITagAnalysisRequest Tag contracts must use canonical unique Tag order"
            )
        expected_fingerprint = taxonomy_fingerprint(self.tag_contract_views)
        if self.active_taxonomy_fingerprint != expected_fingerprint:
            raise ValueError(
                "AITagAnalysisRequest.active_taxonomy_fingerprint does not match contracts"
            )
        return self


class AITagAnalysisRequest(_AITagAnalysisRequestPayload):
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]

    @model_validator(mode="after")
    def validate_request_id(self) -> Self:
        _validate_identity(self, "request_id", "ai-tag-request", "AITagAnalysisRequest")
        return self


class AITagJudgment(FrozenModel):
    tag_id: Annotated[str, Field(pattern=_TAG_ID)]
    decision: TagDecision
    evidence_lines: tuple[Annotated[int, Field(ge=1)], ...]
    reason_code: Annotated[str, Field(pattern=_REASON_CODE)]
    reason: str | None

    @field_validator("evidence_lines", mode="before")
    @classmethod
    def parse_evidence_lines(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AITagJudgment.evidence_lines")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        return None if value is None else _single_line(value, "AITagJudgment.reason")

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.evidence_lines != tuple(sorted(set(self.evidence_lines))):
            raise ValueError("AITagJudgment.evidence_lines must be sorted and unique")
        if self.decision == "positive":
            if not self.evidence_lines or self.reason is None:
                raise ValueError("positive judgment requires evidence lines and reason")
            if self.reason_code in _ABSTAIN_REASON_CODES | _NO_SUPPORT_REASON_CODES:
                raise ValueError("positive judgment uses an incompatible reason code")
        elif self.decision == "not_supported":
            if self.evidence_lines or self.reason is not None:
                raise ValueError(
                    "not_supported judgment cannot carry evidence lines or free-text reason"
                )
            if self.reason_code not in _NO_SUPPORT_REASON_CODES:
                raise ValueError("not_supported judgment uses an unsupported reason code")
        else:
            if self.evidence_lines or self.reason is None:
                raise ValueError("abstain judgment requires reason and no evidence lines")
            if self.reason_code not in _ABSTAIN_REASON_CODES:
                raise ValueError("abstain judgment uses an unsupported reason code")
        return self


class AITagUsage(FrozenModel):
    input_tokens: Annotated[int | None, Field(ge=0)]
    output_tokens: Annotated[int | None, Field(ge=0)]
    cache_read_input_tokens: Annotated[int | None, Field(ge=0)]

    @model_validator(mode="after")
    def validate_reporting(self) -> Self:
        values = (
            self.input_tokens,
            self.output_tokens,
            self.cache_read_input_tokens,
        )
        if any(value is None for value in values) and any(value is not None for value in values):
            raise ValueError("AITagUsage token counts must be reported together or all null")
        return self


class _AITagAnalysisResultPayload(FrozenModel):
    schema_version: Literal["ai-tag-analysis-result-v1"]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    provider: Literal["deepseek"]
    model: Literal["deepseek-v4-pro"]
    system_fingerprint: Annotated[str, Field(min_length=1)]
    thinking: Literal["disabled"]
    reasoning_effort: None
    response_format: Literal["json_object"]
    finish_reason: Literal["stop"]
    judgments: tuple[AITagJudgment, ...]
    usage: AITagUsage
    latency_ms: Annotated[int, Field(ge=0)]
    attempt_count: Annotated[int, Field(ge=1)]
    output_status: Literal["valid"]

    @field_validator("judgments", mode="before")
    @classmethod
    def parse_judgments(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AITagAnalysisResult.judgments")

    @field_validator("system_fingerprint")
    @classmethod
    def validate_system_fingerprint(cls, value: str) -> str:
        return _single_line(value, "AITagAnalysisResult.system_fingerprint")

    @model_validator(mode="after")
    def validate_judgments(self) -> Self:
        tag_ids = tuple(item.tag_id for item in self.judgments)
        if len(tag_ids) != ACTIVE_TAG_COUNT_V1:
            raise ValueError("AITagAnalysisResult must contain exactly 24 judgments")
        if tag_ids != tuple(sorted(set(tag_ids))):
            raise ValueError(
                "AITagAnalysisResult judgments must use canonical unique Tag order"
            )
        return self


class AITagAnalysisResult(_AITagAnalysisResultPayload):
    result_id: Annotated[str, Field(pattern=_RESULT_ID)]

    @model_validator(mode="after")
    def validate_result_id(self) -> Self:
        _validate_identity(self, "result_id", "ai-tag-result", "AITagAnalysisResult")
        return self


class _AITagExecutionOutcomePayload(FrozenModel):
    schema_version: Literal["ai-tag-execution-outcome-v1"]
    analysis_run_id: Annotated[str, Field(pattern=_ANALYSIS_RUN_ID)]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    request_id: Annotated[str | None, Field(pattern=_REQUEST_ID)]
    status: ExecutionStatus
    result_id: Annotated[str | None, Field(pattern=_RESULT_ID)]
    reason_code: Annotated[str, Field(pattern=_REASON_CODE)]
    attempt_count: Annotated[int, Field(ge=0)]
    budget_snapshot_id: Annotated[str, Field(pattern=_BUDGET_SNAPSHOT_ID)]

    @model_validator(mode="after")
    def validate_status_matrix(self) -> Self:
        if self.reason_code not in _OUTCOME_REASON_CODES[self.status]:
            raise ValueError("AITagExecutionOutcome reason_code does not match status")
        if self.status == "valid_result":
            if self.request_id is None or self.result_id is None or self.attempt_count < 1:
                raise ValueError(
                    "valid_result requires request, result, and at least one attempt"
                )
        elif self.status in {"invalid_output", "unavailable"}:
            if self.request_id is None or self.result_id is not None or self.attempt_count < 1:
                raise ValueError(
                    f"{self.status} requires request, no result, and at least one attempt"
                )
        elif self.status == "skipped_budget":
            if self.request_id is None or self.result_id is not None or self.attempt_count != 0:
                raise ValueError(
                    "skipped_budget requires request, no result, and zero attempts"
                )
        elif (
            self.request_id is not None
            or self.result_id is not None
            or self.attempt_count != 0
        ):
            raise ValueError("not_run requires no request, no result, and zero attempts")
        return self


class AITagExecutionOutcome(_AITagExecutionOutcomePayload):
    outcome_id: Annotated[str, Field(pattern=_OUTCOME_ID)]

    @model_validator(mode="after")
    def validate_outcome_id(self) -> Self:
        _validate_identity(self, "outcome_id", "ai-tag-outcome", "AITagExecutionOutcome")
        return self


class HybridTagState(FrozenModel):
    tag_id: Annotated[str, Field(pattern=_TAG_ID)]
    static_exact_decision: StaticDecision
    static_routing_decision: StaticDecision
    ai_unit_decision: TagDecision | None
    unit_comparison_status: UnitComparisonStatus

    @model_validator(mode="after")
    def validate_comparison_status(self) -> Self:
        expected = reduce_unit_comparison(
            self.static_exact_decision,
            self.ai_unit_decision,
        )
        if self.unit_comparison_status != expected:
            raise ValueError(
                "HybridTagState.unit_comparison_status does not match exact/AI axes"
            )
        return self


class _HybridFeatureAnalysisResultPayload(FrozenModel):
    schema_version: Literal["hybrid-feature-analysis-result-v1"]
    unit_id: Annotated[str, Field(min_length=1)]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    ai_execution_outcome_id: Annotated[str, Field(pattern=_OUTCOME_ID)]
    ai_result_id: Annotated[str | None, Field(pattern=_RESULT_ID)]
    tag_states: tuple[HybridTagState, ...]
    diagnostics: tuple[str, ...]

    @field_validator("tag_states", "diagnostics", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"HybridFeatureAnalysisResult.{info.field_name}")

    @field_validator("unit_id")
    @classmethod
    def validate_unit_id(cls, value: str) -> str:
        return _single_line(value, "HybridFeatureAnalysisResult.unit_id")

    @field_validator("diagnostics")
    @classmethod
    def validate_diagnostics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique_strings(value, "HybridFeatureAnalysisResult.diagnostics")

    @model_validator(mode="after")
    def validate_tag_states(self) -> Self:
        tag_ids = tuple(item.tag_id for item in self.tag_states)
        if len(tag_ids) != ACTIVE_TAG_COUNT_V1:
            raise ValueError("HybridFeatureAnalysisResult must contain exactly 24 Tag states")
        if tag_ids != tuple(sorted(set(tag_ids))):
            raise ValueError(
                "HybridFeatureAnalysisResult Tag states must use canonical unique Tag order"
            )
        decisions_present = tuple(item.ai_unit_decision is not None for item in self.tag_states)
        if self.ai_result_id is None and any(decisions_present):
            raise ValueError("Hybrid result without AI result cannot carry AI decisions")
        if self.ai_result_id is not None and not all(decisions_present):
            raise ValueError("Hybrid result with AI result requires all AI decisions")
        return self


class HybridFeatureAnalysisResult(_HybridFeatureAnalysisResultPayload):
    analysis_id: Annotated[str, Field(pattern=_HYBRID_ANALYSIS_ID)]

    @model_validator(mode="after")
    def validate_analysis_id(self) -> Self:
        _validate_identity(
            self,
            "analysis_id",
            "hybrid-analysis",
            "HybridFeatureAnalysisResult",
        )
        return self


def taxonomy_fingerprint(contracts: Sequence[AITagContractView]) -> str:
    validated = tuple(
        _revalidate(item, AITagContractView, "AI Tag Contract View")
        for item in contracts
    )
    return canonical_hash(
        "ai-tag-taxonomy",
        {"tag_contract_views": [item.model_dump(mode="json") for item in validated]},
    )


def project_owner_summary(summary: OwnerSummary) -> AIModelOwnerSummary:
    summary = _revalidate(summary, OwnerSummary, "Owner Summary")
    return AIModelOwnerSummary(
        resolution=summary.resolution,
        unit_owner_kind=(
            None if summary.unit_owner is None else summary.unit_owner.owner_kind
        ),
        unit_owner_qualified_name=(
            None if summary.unit_owner is None else summary.unit_owner.qualified_name
        ),
        enclosing_owner_kind=(
            None
            if summary.enclosing_owner is None
            else summary.enclosing_owner.owner_kind
        ),
        enclosing_owner_qualified_name=(
            None
            if summary.enclosing_owner is None
            else summary.enclosing_owner.qualified_name
        ),
        owner_roles=summary.owner_roles,
        diagnostics=summary.diagnostics,
    )


def reduce_unit_comparison(
    static_exact_decision: StaticDecision,
    ai_unit_decision: TagDecision | None,
) -> UnitComparisonStatus:
    if ai_unit_decision is None:
        return (
            "static_only_due_execution"
            if static_exact_decision == "positive"
            else "unresolved_due_execution"
        )
    return _VALID_COMPARISON_BY_AXES[(static_exact_decision, ai_unit_decision)]


def seal_review_unit_analysis_card(
    payload: Mapping[str, object],
) -> ReviewUnitAnalysisCard:
    return seal_payload(
        payload,
        payload_type=_ReviewUnitAnalysisCardPayload,
        sealed_type=ReviewUnitAnalysisCard,
        identity_field="card_id",
        identity_prefix="analysis-card",
        context="ReviewUnit Analysis Card",
    )


def seal_ai_tag_model_view(payload: Mapping[str, object]) -> AITagModelView:
    return seal_payload(
        payload,
        payload_type=_AITagModelViewPayload,
        sealed_type=AITagModelView,
        identity_field="model_view_id",
        identity_prefix="ai-tag-model-view",
        context="AI Tag Model View",
    )


def seal_ai_tag_contract_view(payload: Mapping[str, object]) -> AITagContractView:
    return seal_payload(
        payload,
        payload_type=_AITagContractViewPayload,
        sealed_type=AITagContractView,
        identity_field="contract_fingerprint",
        identity_prefix="ai-tag-contract-view",
        context="AI Tag Contract View",
    )


def seal_ai_tag_analysis_request(
    payload: Mapping[str, object],
) -> AITagAnalysisRequest:
    return seal_payload(
        payload,
        payload_type=_AITagAnalysisRequestPayload,
        sealed_type=AITagAnalysisRequest,
        identity_field="request_id",
        identity_prefix="ai-tag-request",
        context="AI Tag Analysis Request",
    )


def seal_ai_tag_analysis_result(
    payload: Mapping[str, object],
) -> AITagAnalysisResult:
    return seal_payload(
        payload,
        payload_type=_AITagAnalysisResultPayload,
        sealed_type=AITagAnalysisResult,
        identity_field="result_id",
        identity_prefix="ai-tag-result",
        context="AI Tag Analysis Result",
    )


def seal_ai_tag_execution_outcome(
    payload: Mapping[str, object],
) -> AITagExecutionOutcome:
    return seal_payload(
        payload,
        payload_type=_AITagExecutionOutcomePayload,
        sealed_type=AITagExecutionOutcome,
        identity_field="outcome_id",
        identity_prefix="ai-tag-outcome",
        context="AI Tag Execution Outcome",
    )


def seal_hybrid_feature_analysis_result(
    payload: Mapping[str, object],
) -> HybridFeatureAnalysisResult:
    return seal_payload(
        payload,
        payload_type=_HybridFeatureAnalysisResultPayload,
        sealed_type=HybridFeatureAnalysisResult,
        identity_field="analysis_id",
        identity_prefix="hybrid-analysis",
        context="Hybrid Feature Analysis Result",
    )


def load_review_unit_analysis_card(raw: str | bytes) -> ReviewUnitAnalysisCard:
    return load_json_model(raw, ReviewUnitAnalysisCard, "ReviewUnit Analysis Card")


def load_ai_tag_model_view(raw: str | bytes) -> AITagModelView:
    return load_json_model(raw, AITagModelView, "AI Tag Model View")


def load_ai_tag_contract_view(raw: str | bytes) -> AITagContractView:
    return load_json_model(raw, AITagContractView, "AI Tag Contract View")


def load_ai_tag_analysis_request(raw: str | bytes) -> AITagAnalysisRequest:
    return load_json_model(raw, AITagAnalysisRequest, "AI Tag Analysis Request")


def load_ai_tag_analysis_result(raw: str | bytes) -> AITagAnalysisResult:
    return load_json_model(raw, AITagAnalysisResult, "AI Tag Analysis Result")


def load_ai_tag_execution_outcome(raw: str | bytes) -> AITagExecutionOutcome:
    return load_json_model(raw, AITagExecutionOutcome, "AI Tag Execution Outcome")


def load_hybrid_feature_analysis_result(
    raw: str | bytes,
) -> HybridFeatureAnalysisResult:
    return load_json_model(raw, HybridFeatureAnalysisResult, "Hybrid Feature Analysis Result")


def verify_model_view_against_card(
    model_view: AITagModelView,
    card: ReviewUnitAnalysisCard,
) -> None:
    model_view = _revalidate(model_view, AITagModelView, "AI Tag Model View")
    card = _revalidate(card, ReviewUnitAnalysisCard, "ReviewUnit Analysis Card")
    if (
        model_view.card_id != card.card_id
        or model_view.unit_id != card.unit_id
        or model_view.source_ref_id != card.source_ref_id
    ):
        raise ValueError("AI Tag Model View does not reference the supplied Analysis Card")
    if model_view.code.mode != card.code.mode or model_view.code.truncated != card.code.truncated:
        raise ValueError("AI Tag Model View code policy differs from its Analysis Card")
    expected_lines = tuple(range(card.code.line_start, card.code.line_end + 1))
    if model_view.code.line_numbers != expected_lines:
        raise ValueError("AI Tag Model View lines differ from its Analysis Card range")
    expected_numbered_text = "\n".join(
        f"{line}: {text}"
        for line, text in zip(
            expected_lines,
            card.code.text.splitlines(),
            strict=True,
        )
    )
    if model_view.code.numbered_text != expected_numbered_text:
        raise ValueError("AI Tag Model View text differs from its Analysis Card code")
    if model_view.owner_summary != project_owner_summary(card.owner_summary):
        raise ValueError("AI Tag Model View owner summary differs from its Analysis Card")
    if model_view.scoped_facts != card.facts or model_view.quality != card.quality:
        raise ValueError("AI Tag Model View facts or quality differ from its Analysis Card")


def verify_request_against_model_view(
    request: AITagAnalysisRequest,
    model_view: AITagModelView,
    *,
    active_tag_ids: Sequence[str],
    active_taxonomy_fingerprint: str,
) -> None:
    request = _revalidate(request, AITagAnalysisRequest, "AI Tag Analysis Request")
    model_view = _revalidate(model_view, AITagModelView, "AI Tag Model View")
    if request.card_id != model_view.card_id or request.model_view_id != model_view.model_view_id:
        raise ValueError("AI Tag request does not reference the supplied Model View")
    expected_tag_ids = tuple(active_tag_ids)
    if (
        len(expected_tag_ids) != ACTIVE_TAG_COUNT_V1
        or expected_tag_ids != tuple(sorted(set(expected_tag_ids)))
    ):
        raise ValueError("active Tag registry snapshot must contain 24 sorted unique IDs")
    request_tag_ids = tuple(item.tag_id for item in request.tag_contract_views)
    if request_tag_ids != expected_tag_ids:
        raise ValueError("AI Tag request does not exactly cover the active Tag registry")
    if request.active_taxonomy_fingerprint != active_taxonomy_fingerprint:
        raise ValueError("AI Tag request taxonomy fingerprint differs from registry snapshot")


def verify_result_against_request(
    result: AITagAnalysisResult,
    request: AITagAnalysisRequest,
    model_view: AITagModelView,
) -> None:
    result = _revalidate(result, AITagAnalysisResult, "AI Tag Analysis Result")
    request = _revalidate(request, AITagAnalysisRequest, "AI Tag Analysis Request")
    model_view = _revalidate(model_view, AITagModelView, "AI Tag Model View")
    if result.request_id != request.request_id:
        raise ValueError("AI Tag result does not reference the supplied request")
    if (
        request.model_view_id != model_view.model_view_id
        or request.card_id != model_view.card_id
    ):
        raise ValueError("AI Tag request does not reference the supplied Model View")
    request_tag_ids = tuple(item.tag_id for item in request.tag_contract_views)
    result_tag_ids = tuple(item.tag_id for item in result.judgments)
    if result_tag_ids != request_tag_ids:
        raise ValueError("AI Tag result does not exactly cover the requested Tags")
    visible_lines = set(model_view.code.line_numbers)
    for judgment in result.judgments:
        if not set(judgment.evidence_lines).issubset(visible_lines):
            raise ValueError("AI Tag result cites evidence outside the Model View")
    degraded = (
        model_view.code.truncated
        or model_view.quality.parser_layer != "L1"
        or model_view.quality.context_degraded
        or model_view.quality.unit_owner_unresolved
        or model_view.owner_summary.resolution in {"partial", "unresolved"}
        or bool(model_view.quality.error_nodes)
        or bool(model_view.quality.missing_nodes)
    )
    if degraded and any(
        judgment.decision == "not_supported" for judgment in result.judgments
    ):
        raise ValueError("degraded Model View cannot support not_supported judgments")


def verify_outcome_against_result(
    outcome: AITagExecutionOutcome,
    result: AITagAnalysisResult | None,
) -> None:
    outcome = _revalidate(outcome, AITagExecutionOutcome, "AI Tag Execution Outcome")
    if result is not None:
        result = _revalidate(result, AITagAnalysisResult, "AI Tag Analysis Result")
    if outcome.status == "valid_result":
        if result is None:
            raise ValueError("valid_result outcome requires the referenced AI result")
        if outcome.result_id != result.result_id or outcome.request_id != result.request_id:
            raise ValueError("AI outcome does not reference the supplied AI result")
        if outcome.attempt_count != result.attempt_count:
            raise ValueError("AI outcome and result attempt counts differ")
    elif result is not None:
        raise ValueError("non-valid AI outcome cannot be paired with an AI result")


def verify_hybrid_chain(
    hybrid: HybridFeatureAnalysisResult,
    card: ReviewUnitAnalysisCard,
    model_view: AITagModelView,
    request: AITagAnalysisRequest | None,
    outcome: AITagExecutionOutcome,
    result: AITagAnalysisResult | None,
    *,
    active_tag_ids: Sequence[str],
    active_taxonomy_fingerprint: str,
) -> None:
    """Verify the supplied artifact graph, starting at a caller-supplied sealed Card.

    This closure does not rebuild the Card from UnitFactScope, FeatureProfile,
    FeatureRoutingResult, or Tag configuration.
    """
    hybrid = _revalidate(
        hybrid,
        HybridFeatureAnalysisResult,
        "Hybrid Feature Analysis Result",
    )
    card = _revalidate(card, ReviewUnitAnalysisCard, "ReviewUnit Analysis Card")
    model_view = _revalidate(model_view, AITagModelView, "AI Tag Model View")
    if request is not None:
        request = _revalidate(request, AITagAnalysisRequest, "AI Tag Analysis Request")
    outcome = _revalidate(outcome, AITagExecutionOutcome, "AI Tag Execution Outcome")
    if result is not None:
        result = _revalidate(result, AITagAnalysisResult, "AI Tag Analysis Result")
    verify_model_view_against_card(model_view, card)
    if hybrid.card_id != card.card_id or hybrid.unit_id != card.unit_id:
        raise ValueError("Hybrid analysis does not reference the supplied Analysis Card")
    if hybrid.ai_execution_outcome_id != outcome.outcome_id:
        raise ValueError("Hybrid analysis does not reference the supplied AI outcome")
    if outcome.card_id != card.card_id or outcome.model_view_id != model_view.model_view_id:
        raise ValueError("AI outcome does not reference the supplied Card and Model View")

    expected_tag_ids = tuple(active_tag_ids)
    if (
        len(expected_tag_ids) != ACTIVE_TAG_COUNT_V1
        or expected_tag_ids != tuple(sorted(set(expected_tag_ids)))
    ):
        raise ValueError("active Tag registry snapshot must contain 24 sorted unique IDs")
    state_tag_ids = tuple(state.tag_id for state in hybrid.tag_states)
    if state_tag_ids != expected_tag_ids:
        raise ValueError("Hybrid Tag states do not exactly cover the active Tag registry")
    if not set((*card.static_tags.exact, *card.static_tags.routing)).issubset(
        expected_tag_ids
    ):
        raise ValueError("Analysis Card static Tags are outside the active Tag registry")
    for state in hybrid.tag_states:
        expected_exact: StaticDecision = (
            "positive" if state.tag_id in card.static_tags.exact else "unknown"
        )
        expected_routing: StaticDecision = (
            "positive" if state.tag_id in card.static_tags.routing else "unknown"
        )
        if (
            state.static_exact_decision != expected_exact
            or state.static_routing_decision != expected_routing
        ):
            raise ValueError("Hybrid static Tag axes differ from the Analysis Card")

    if outcome.status == "not_run":
        if request is not None:
            raise ValueError("not_run Hybrid chain cannot carry an AI request")
    else:
        if request is None:
            raise ValueError("executed Hybrid chain requires the referenced AI request")
        verify_request_against_model_view(
            request,
            model_view,
            active_tag_ids=expected_tag_ids,
            active_taxonomy_fingerprint=active_taxonomy_fingerprint,
        )
        if request.card_id != card.card_id or outcome.request_id != request.request_id:
            raise ValueError("AI request/outcome do not belong to the supplied Analysis Card")
        request_tag_ids = tuple(item.tag_id for item in request.tag_contract_views)
        if request_tag_ids != expected_tag_ids:
            raise ValueError("AI request does not exactly cover the active Tag registry")

    if result is not None:
        if request is None:
            raise ValueError("AI result cannot exist without an AI request")
        verify_result_against_request(result, request, model_view)
    verify_outcome_against_result(outcome, result)
    if outcome.status == "valid_result":
        if result is None or hybrid.ai_result_id != result.result_id:
            raise ValueError("valid Hybrid analysis does not reference the supplied AI result")
        decisions = {item.tag_id: item.decision for item in result.judgments}
        if any(
            state.ai_unit_decision != decisions.get(state.tag_id)
            for state in hybrid.tag_states
        ):
            raise ValueError("Hybrid AI decisions differ from the supplied AI result")
    elif hybrid.ai_result_id is not None or any(
        state.ai_unit_decision is not None for state in hybrid.tag_states
    ):
        raise ValueError("non-valid Hybrid analysis must not carry AI decisions")


__all__ = [
    "ACTIVE_TAG_COUNT_V1",
    "AI_TAG_ANALYSIS_REQUEST_SCHEMA_VERSION",
    "AI_TAG_ANALYSIS_RESULT_SCHEMA_VERSION",
    "AI_TAG_CONTRACT_VIEW_SCHEMA_VERSION",
    "AI_TAG_EXECUTION_OUTCOME_SCHEMA_VERSION",
    "AI_TAG_MODEL_VIEW_SCHEMA_VERSION",
    "HYBRID_FEATURE_ANALYSIS_RESULT_SCHEMA_VERSION",
    "REVIEW_UNIT_ANALYSIS_CARD_SCHEMA_VERSION",
    "AITagAnalysisRequest",
    "AITagAnalysisResult",
    "AITagContractView",
    "AITagExecutionOutcome",
    "AITagJudgment",
    "AITagModelView",
    "AITagUsage",
    "AIModelCode",
    "AnalysisCode",
    "AnalysisQuality",
    "AvailableContextRef",
    "BasicStaticFeatureSignal",
    "CodeFactSet",
    "ExecutionStatus",
    "FileSymbolLeafSignal",
    "HybridFeatureAnalysisResult",
    "HybridTagState",
    "AIModelOwnerSummary",
    "OwnerIdentity",
    "OwnerSummary",
    "ReviewUnitAnalysisCard",
    "ScopedCodeFacts",
    "NormalizedSymbolLeafSignal",
    "StaticFeatureSignal",
    "StaticDecision",
    "StaticTagMatch",
    "StaticTagSignals",
    "TagDecision",
    "UnitComparisonStatus",
    "UnitOwnerRoleSymbolSignal",
    "load_ai_tag_analysis_request",
    "load_ai_tag_analysis_result",
    "load_ai_tag_contract_view",
    "load_ai_tag_execution_outcome",
    "load_ai_tag_model_view",
    "load_hybrid_feature_analysis_result",
    "load_review_unit_analysis_card",
    "project_owner_summary",
    "reduce_unit_comparison",
    "seal_ai_tag_analysis_request",
    "seal_ai_tag_analysis_result",
    "seal_ai_tag_contract_view",
    "seal_ai_tag_execution_outcome",
    "seal_ai_tag_model_view",
    "seal_hybrid_feature_analysis_result",
    "seal_review_unit_analysis_card",
    "taxonomy_fingerprint",
    "verify_hybrid_chain",
    "verify_model_view_against_card",
    "verify_outcome_against_result",
    "verify_request_against_model_view",
    "verify_result_against_request",
]
