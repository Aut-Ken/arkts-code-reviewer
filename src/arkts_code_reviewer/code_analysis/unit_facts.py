from __future__ import annotations

from collections.abc import Iterable

from arkts_code_reviewer.code_analysis.file_analysis_models import (
    FactOccurrence,
    FileAnalysis,
    OwnerRef,
    ScopedFacts,
    UnitFactDiagnostic,
    UnitFactScope,
)
from arkts_code_reviewer.code_analysis.models import ReviewUnit

_FACT_FIELD_BY_KIND = {
    "component": "components",
    "api": "apis",
    "decorator": "decorators",
    "attribute": "attributes",
    "symbol": "symbols",
    "syntax": "syntax",
    "import": "import_bindings",
    "import_binding": "import_bindings",
    "import_use": "import_uses",
    "field_read": "field_reads",
    "field_write": "field_writes",
    "call": "calls",
    "string_literal": "string_literals",
    "resource_reference": "resource_references",
}
_EXACT_QUALITIES = {"exact", "recovered"}


def project(file_analysis: FileAnalysis, unit: ReviewUnit) -> UnitFactScope:
    """Project occurrence-backed facts into one ReviewUnit without re-parsing it.

    ``unit_exact`` is deliberately narrower than the Unit's visible context. A fact
    must belong to the Unit's owner subtree *and* fit completely inside its source
    span. File-level compatibility facts remain available through ``file_hints``,
    but are never promoted to exact evidence by this projector.
    """

    source_ref_id = file_analysis.source_ref.source_ref_id
    if unit.source_ref_id != source_ref_id:
        raise ValueError(
            "ReviewUnit.source_ref_id must match FileAnalysis.source_ref"
        )

    owner_ref = unit.owner_ref
    if unit.unit_kind == "fallback" or owner_ref is None:
        return _empty_scope(
            unit,
            file_analysis,
            diagnostics=(
                () if unit.unit_kind == "fallback" else ("unit_owner_unresolved",)
            ),
        )

    eligible_owners = _eligible_owner_keys(file_analysis, owner_ref)
    if not eligible_owners:
        return _empty_scope(
            unit,
            file_analysis,
            diagnostics=("unit_owner_unresolved",),
        )

    selected: list[FactOccurrence] = []
    for occurrence in file_analysis.fact_occurrences:
        if occurrence.quality not in _EXACT_QUALITIES:
            continue
        if occurrence.owner_ref is None:
            continue
        if _owner_key(occurrence.owner_ref) not in eligible_owners:
            continue
        if not _span_contains(unit, occurrence):
            continue
        if occurrence.kind not in _FACT_FIELD_BY_KIND:
            continue
        selected.append(occurrence)

    return UnitFactScope(
        unit_id=unit.unit_id,
        source_ref_id=source_ref_id,
        unit_exact=_scoped_facts(selected),
        file_hints=file_analysis.file_hints,
        exact_occurrence_ids=tuple(
            sorted({occurrence.occurrence_id for occurrence in selected})
        ),
        diagnostics=(),
    )


def _empty_scope(
    unit: ReviewUnit,
    file_analysis: FileAnalysis,
    *,
    diagnostics: tuple[UnitFactDiagnostic, ...],
) -> UnitFactScope:
    return UnitFactScope(
        unit_id=unit.unit_id,
        source_ref_id=file_analysis.source_ref.source_ref_id,
        unit_exact=ScopedFacts(),
        file_hints=file_analysis.file_hints,
        exact_occurrence_ids=(),
        diagnostics=diagnostics,
    )


def _eligible_owner_keys(
    file_analysis: FileAnalysis,
    direct_owner: OwnerRef,
) -> set[tuple[str, str]]:
    declarations = {
        declaration.declaration_id: declaration
        for declaration in file_analysis.declarations
    }
    regions = {region.region_id: region for region in file_analysis.review_regions}
    direct_key = _owner_key(direct_owner)

    if direct_owner.kind == "region":
        region = regions.get(direct_owner.ref_id)
        return (
            {direct_key}
            if region is not None and region.quality in _EXACT_QUALITIES
            else set()
        )
    direct_declaration = declarations.get(direct_owner.ref_id)
    if (
        direct_owner.kind != "declaration"
        or direct_declaration is None
        or direct_declaration.quality not in _EXACT_QUALITIES
    ):
        return set()

    declaration_ids = {direct_owner.ref_id}
    while True:
        descendants = {
            declaration_id
            for declaration_id, declaration in declarations.items()
            if declaration.parent_id in declaration_ids
            and declaration.quality in _EXACT_QUALITIES
        }
        new_ids = descendants - declaration_ids
        if not new_ids:
            break
        declaration_ids.update(new_ids)

    eligible = {("declaration", declaration_id) for declaration_id in declaration_ids}
    eligible.update(
        ("region", region.region_id)
        for region in regions.values()
        if region.owner_declaration_id in declaration_ids
        and region.quality in _EXACT_QUALITIES
    )
    return eligible


def _owner_key(owner_ref: OwnerRef) -> tuple[str, str]:
    return owner_ref.kind, owner_ref.ref_id


def _span_contains(unit: ReviewUnit, occurrence: FactOccurrence) -> bool:
    return (
        unit.source_span.start_line <= occurrence.span.start_line
        and occurrence.span.end_line <= unit.source_span.end_line
    )


def _scoped_facts(occurrences: Iterable[FactOccurrence]) -> ScopedFacts:
    values: dict[str, set[str]] = {
        field_name: set() for field_name in set(_FACT_FIELD_BY_KIND.values())
    }
    for occurrence in occurrences:
        field_name = _FACT_FIELD_BY_KIND[occurrence.kind]
        name = occurrence.canonical_name or occurrence.name
        if name:
            values[field_name].add(name)

    return ScopedFacts(
        components=tuple(sorted(values["components"])),
        apis=tuple(sorted(values["apis"])),
        decorators=tuple(sorted(values["decorators"])),
        attributes=tuple(sorted(values["attributes"])),
        symbols=tuple(sorted(values["symbols"])),
        syntax=tuple(sorted(values["syntax"])),
        import_bindings=tuple(sorted(values["import_bindings"])),
        import_uses=tuple(sorted(values["import_uses"])),
        field_reads=tuple(sorted(values["field_reads"])),
        field_writes=tuple(sorted(values["field_writes"])),
        calls=tuple(sorted(values["calls"])),
        string_literals=tuple(sorted(values["string_literals"])),
        resource_references=tuple(sorted(values["resource_references"])),
    )
