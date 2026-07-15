from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from arkts_code_reviewer.feature_routing.config import OwnerRole

if TYPE_CHECKING:
    from arkts_code_reviewer.code_analysis.file_analysis_models import (
        DeclarationOccurrence,
        FactOccurrence,
        FileAnalysis,
        UnitFactScope,
    )
    from arkts_code_reviewer.code_analysis.models import ReviewUnit

OwnerContextDiagnostic = Literal[
    "owner_context_unit_unresolved",
    "owner_context_symbol_unresolved",
    "owner_context_symbol_ambiguous",
    "owner_context_enclosing_owner_unresolved",
    "owner_context_role_unresolved",
    "owner_context_role_ambiguous",
    "owner_context_recovered",
]

OWNER_CONTEXT_DIAGNOSTICS = frozenset(
    {
        "owner_context_unit_unresolved",
        "owner_context_symbol_unresolved",
        "owner_context_symbol_ambiguous",
        "owner_context_enclosing_owner_unresolved",
        "owner_context_role_unresolved",
        "owner_context_role_ambiguous",
        "owner_context_recovered",
    }
)

_COMPONENT_DECORATORS = frozenset({"@Component", "@ComponentV2"})
_ENTRY_DECORATOR = "@Entry"
_CUSTOM_DIALOG_DECORATOR = "@CustomDialog"


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
class SymbolOwnerRoleEvidence:
    symbol: str
    symbol_leaf: str
    symbol_occurrence_id: str
    direct_owner_declaration_id: str
    enclosing_owner_declaration_id: str
    owner_role: OwnerRole
    role_evidence_occurrence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, context in (
            (self.symbol, "SymbolOwnerRoleEvidence.symbol"),
            (self.symbol_leaf, "SymbolOwnerRoleEvidence.symbol_leaf"),
            (
                self.symbol_occurrence_id,
                "SymbolOwnerRoleEvidence.symbol_occurrence_id",
            ),
            (
                self.direct_owner_declaration_id,
                "SymbolOwnerRoleEvidence.direct_owner_declaration_id",
            ),
            (
                self.enclosing_owner_declaration_id,
                "SymbolOwnerRoleEvidence.enclosing_owner_declaration_id",
            ),
        ):
            _non_empty(value, context)
        if self.symbol.rsplit(".", 1)[-1] != self.symbol_leaf:
            raise ValueError("SymbolOwnerRoleEvidence.symbol_leaf must match the symbol leaf")
        if self.owner_role not in {
            "arkui_custom_component",
            "arkui_router_page",
        }:
            raise ValueError("SymbolOwnerRoleEvidence.owner_role is unsupported")
        if not self.symbol_occurrence_id.startswith("occurrence:"):
            raise ValueError(
                "SymbolOwnerRoleEvidence.symbol_occurrence_id must use occurrence identity"
            )
        for declaration_id in (
            self.direct_owner_declaration_id,
            self.enclosing_owner_declaration_id,
        ):
            if not declaration_id.startswith("declaration:"):
                raise ValueError("SymbolOwnerRoleEvidence owner IDs must use declaration identity")
        if self.direct_owner_declaration_id == self.enclosing_owner_declaration_id:
            raise ValueError("SymbolOwnerRoleEvidence direct and enclosing owners must differ")
        _sorted_unique(
            self.role_evidence_occurrence_ids,
            "SymbolOwnerRoleEvidence.role_evidence_occurrence_ids",
        )
        if not self.role_evidence_occurrence_ids or any(
            not occurrence_id.startswith("occurrence:")
            for occurrence_id in self.role_evidence_occurrence_ids
        ):
            raise ValueError("SymbolOwnerRoleEvidence role evidence must use occurrence identities")


@dataclass(frozen=True)
class UnitOwnerContext:
    unit_id: str
    source_ref_id: str
    evidence: tuple[SymbolOwnerRoleEvidence, ...] = ()
    diagnostics: tuple[OwnerContextDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.unit_id, "UnitOwnerContext.unit_id")
        _non_empty(self.source_ref_id, "UnitOwnerContext.source_ref_id")
        if not self.source_ref_id.startswith("code-source:sha256:"):
            raise ValueError("UnitOwnerContext.source_ref_id must use CodeSourceRef identity")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, SymbolOwnerRoleEvidence) for item in self.evidence
        ):
            raise ValueError(
                "UnitOwnerContext.evidence must contain SymbolOwnerRoleEvidence values"
            )
        evidence_keys = [
            (
                item.symbol,
                item.owner_role,
                item.symbol_occurrence_id,
                item.enclosing_owner_declaration_id,
            )
            for item in self.evidence
        ]
        if evidence_keys != sorted(set(evidence_keys)):
            raise ValueError("UnitOwnerContext.evidence must be sorted and unique")
        _sorted_unique(self.diagnostics, "UnitOwnerContext.diagnostics")
        if not set(self.diagnostics).issubset(OWNER_CONTEXT_DIAGNOSTICS):
            raise ValueError("UnitOwnerContext.diagnostics contains unknown codes")


@dataclass(frozen=True)
class OwnerAwareRoutingInput:
    scope: UnitFactScope
    unit: ReviewUnit
    file_analysis: FileAnalysis

    def __post_init__(self) -> None:
        from arkts_code_reviewer.code_analysis.file_analysis_models import (
            FileAnalysis,
            UnitFactScope,
        )
        from arkts_code_reviewer.code_analysis.models import ReviewUnit

        if not isinstance(self.scope, UnitFactScope):
            raise ValueError("OwnerAwareRoutingInput.scope must use UnitFactScope")
        if not isinstance(self.unit, ReviewUnit):
            raise ValueError("OwnerAwareRoutingInput.unit must use ReviewUnit")
        if not isinstance(self.file_analysis, FileAnalysis):
            raise ValueError("OwnerAwareRoutingInput.file_analysis must use FileAnalysis")
        if self.scope.unit_id != self.unit.unit_id:
            raise ValueError("OwnerAwareRoutingInput scope and Unit IDs must match")
        source_ref_id = self.file_analysis.source_ref.source_ref_id
        if self.scope.source_ref_id != source_ref_id or self.unit.source_ref_id != source_ref_id:
            raise ValueError(
                "OwnerAwareRoutingInput scope, Unit, and FileAnalysis sources must match"
            )


def derive_unit_owner_context(
    file_analysis: FileAnalysis,
    unit: ReviewUnit,
) -> UnitOwnerContext:
    from arkts_code_reviewer.code_analysis.file_analysis_models import FileAnalysis
    from arkts_code_reviewer.code_analysis.models import ReviewUnit

    if not isinstance(file_analysis, FileAnalysis):
        raise ValueError("file_analysis must use FileAnalysis")
    if not isinstance(unit, ReviewUnit):
        raise ValueError("unit must use ReviewUnit")
    source_ref_id = file_analysis.source_ref.source_ref_id
    if unit.source_ref_id != source_ref_id:
        raise ValueError("ReviewUnit and FileAnalysis sources must match")

    owner_ref = unit.owner_ref
    if unit.unit_kind not in {"method", "struct", "class"}:
        return UnitOwnerContext(unit_id=unit.unit_id, source_ref_id=source_ref_id)
    if owner_ref is None or owner_ref.kind != "declaration":
        return _abstain(unit, source_ref_id, "owner_context_unit_unresolved")

    direct_candidates = tuple(
        declaration
        for declaration in file_analysis.declarations
        if declaration.declaration_id == owner_ref.ref_id
    )
    if len(direct_candidates) != 1:
        return _abstain(unit, source_ref_id, "owner_context_unit_unresolved")
    unit_owner = direct_candidates[0]
    if unit_owner.quality != "exact":
        return _abstain(unit, source_ref_id, "owner_context_recovered")
    if not _owner_matches_unit(file_analysis, unit_owner, unit):
        return _abstain(unit, source_ref_id, "owner_context_unit_unresolved")

    method_candidates: tuple[DeclarationOccurrence, ...]
    if unit_owner.kind == "method":
        if unit_owner.parent_id is None:
            return _abstain(
                unit,
                source_ref_id,
                "owner_context_enclosing_owner_unresolved",
            )
        enclosing_id = unit_owner.parent_id
        method_candidates = (unit_owner,)
    else:
        enclosing_id = unit_owner.declaration_id
        method_candidates = tuple(
            declaration
            for declaration in file_analysis.declarations
            if declaration.kind == "method" and declaration.parent_id == unit_owner.declaration_id
        )

    enclosing_candidates = tuple(
        declaration
        for declaration in file_analysis.declarations
        if declaration.declaration_id == enclosing_id
    )
    if len(enclosing_candidates) != 1:
        return _abstain(
            unit,
            source_ref_id,
            "owner_context_enclosing_owner_unresolved",
        )
    enclosing = enclosing_candidates[0]
    if enclosing.quality != "exact":
        return _abstain(unit, source_ref_id, "owner_context_recovered")
    if enclosing.kind != "struct":
        return _abstain(unit, source_ref_id, "owner_context_role_unresolved")

    decorators = tuple(
        occurrence
        for occurrence in file_analysis.fact_occurrences
        if occurrence.kind == "decorator"
        and occurrence.canonical_name
        in {
            *_COMPONENT_DECORATORS,
            _CUSTOM_DIALOG_DECORATOR,
            _ENTRY_DECORATOR,
        }
        and occurrence.owner_ref is not None
        and occurrence.owner_ref.kind == "declaration"
        and occurrence.owner_ref.ref_id == enclosing.declaration_id
    )
    if any(occurrence.quality != "exact" for occurrence in decorators):
        return _abstain(unit, source_ref_id, "owner_context_recovered")
    component_decorators = tuple(
        occurrence
        for occurrence in decorators
        if occurrence.canonical_name in _COMPONENT_DECORATORS
    )
    entry_decorators = tuple(
        occurrence for occurrence in decorators if occurrence.canonical_name == _ENTRY_DECORATOR
    )
    custom_dialog_decorators = tuple(
        occurrence
        for occurrence in decorators
        if occurrence.canonical_name == _CUSTOM_DIALOG_DECORATOR
    )
    if custom_dialog_decorators:
        diagnostic: OwnerContextDiagnostic = (
            "owner_context_role_ambiguous"
            if component_decorators or entry_decorators
            else "owner_context_role_unresolved"
        )
        return _abstain(unit, source_ref_id, diagnostic)
    if not component_decorators:
        return _abstain(unit, source_ref_id, "owner_context_role_unresolved")
    if len(component_decorators) != 1 or len(entry_decorators) > 1:
        return _abstain(unit, source_ref_id, "owner_context_role_ambiguous")

    component = component_decorators[0]
    evidence: list[SymbolOwnerRoleEvidence] = []
    diagnostics: set[OwnerContextDiagnostic] = set()
    for method in method_candidates:
        if method.quality != "exact":
            diagnostics.add("owner_context_recovered")
            continue
        symbol_candidates = tuple(
            occurrence
            for occurrence in file_analysis.fact_occurrences
            if occurrence.kind == "symbol"
            and occurrence.canonical_name == method.qualified_name
            and occurrence.owner_ref is not None
            and occurrence.owner_ref.kind == "declaration"
            and occurrence.owner_ref.ref_id == method.declaration_id
        )
        if any(occurrence.quality != "exact" for occurrence in symbol_candidates):
            diagnostics.add("owner_context_recovered")
            continue
        if not symbol_candidates:
            diagnostics.add("owner_context_symbol_unresolved")
            continue
        if len(symbol_candidates) != 1:
            diagnostics.add("owner_context_symbol_ambiguous")
            continue
        symbol = symbol_candidates[0]
        evidence.append(
            _evidence(
                symbol,
                method.declaration_id,
                enclosing.declaration_id,
                "arkui_custom_component",
                (component,),
            )
        )
        if entry_decorators:
            evidence.append(
                _evidence(
                    symbol,
                    method.declaration_id,
                    enclosing.declaration_id,
                    "arkui_router_page",
                    (component, entry_decorators[0]),
                )
            )
    evidence.sort(
        key=lambda item: (
            item.symbol,
            item.owner_role,
            item.symbol_occurrence_id,
            item.enclosing_owner_declaration_id,
        )
    )
    return UnitOwnerContext(
        unit_id=unit.unit_id,
        source_ref_id=source_ref_id,
        evidence=tuple(evidence),
        diagnostics=tuple(sorted(diagnostics)),
    )


def _evidence(
    symbol: FactOccurrence,
    direct_owner_declaration_id: str,
    enclosing_owner_declaration_id: str,
    owner_role: OwnerRole,
    role_occurrences: tuple[FactOccurrence, ...],
) -> SymbolOwnerRoleEvidence:
    canonical_symbol = symbol.canonical_name or symbol.name
    return SymbolOwnerRoleEvidence(
        symbol=canonical_symbol,
        symbol_leaf=canonical_symbol.rsplit(".", 1)[-1],
        symbol_occurrence_id=symbol.occurrence_id,
        direct_owner_declaration_id=direct_owner_declaration_id,
        enclosing_owner_declaration_id=enclosing_owner_declaration_id,
        owner_role=owner_role,
        role_evidence_occurrence_ids=tuple(
            sorted(occurrence.occurrence_id for occurrence in role_occurrences)
        ),
    )


def _owner_matches_unit(
    file_analysis: FileAnalysis,
    declaration: DeclarationOccurrence,
    unit: ReviewUnit,
) -> bool:
    if not (
        declaration.kind == unit.unit_kind
        and declaration.qualified_name == unit.unit_symbol
        and declaration.span.start_line == unit.source_span.start_line
        and declaration.span.end_line == unit.source_span.end_line
    ):
        return False
    collisions = tuple(
        item
        for item in file_analysis.declarations
        if item.kind == declaration.kind
        and item.qualified_name == declaration.qualified_name
        and item.span.start_line == declaration.span.start_line
        and item.span.end_line == declaration.span.end_line
    )
    if unit.identity_start_offset_utf16 is None:
        return len(collisions) == 1
    return (
        unit.identity_start_offset_utf16 == declaration.exact_range.start_offset_utf16
        and unit.identity_end_offset_utf16 == declaration.exact_range.end_offset_utf16
    )


def _abstain(
    unit: ReviewUnit,
    source_ref_id: str,
    diagnostic: OwnerContextDiagnostic,
) -> UnitOwnerContext:
    return UnitOwnerContext(
        unit_id=unit.unit_id,
        source_ref_id=source_ref_id,
        diagnostics=(diagnostic,),
    )


__all__ = [
    "OWNER_CONTEXT_DIAGNOSTICS",
    "OwnerAwareRoutingInput",
    "OwnerContextDiagnostic",
    "OwnerRole",
    "SymbolOwnerRoleEvidence",
    "UnitOwnerContext",
    "derive_unit_owner_context",
]
