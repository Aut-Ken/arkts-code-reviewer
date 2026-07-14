from __future__ import annotations

import unittest

from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    DeclarationOccurrence,
    ExactRange,
    FactOccurrence,
    FileAnalysis,
    FileParserQuality,
    OwnerRef,
    ReviewRegion,
    ScopedFacts,
)
from arkts_code_reviewer.code_analysis.models import (
    ReviewUnit,
    ReviewUnitDiagnostic,
    ReviewUnitSpan,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import declaration_unit_id
from arkts_code_reviewer.code_analysis.tagger import derive_tags
from arkts_code_reviewer.code_analysis.unit_facts import project

PATH = "src/pages/Scope.ets"
SOURCE = "\n".join(f"// line {line}" for line in range(1, 13)) + "\n"


class UnitFactProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source_ref = CodeSourceRef.inline(PATH, SOURCE)
        source_ref_id = self.source_ref.source_ref_id

        self.method = DeclarationOccurrence.create(
            source_ref_id=source_ref_id,
            kind="method",
            name="load",
            qualified_name="Page.load",
            span=SourceSpan(start_line=2, end_line=8),
            exact_range=_range(2, 8, 10, 80),
        )
        self.nested_ui = DeclarationOccurrence.create(
            source_ref_id=source_ref_id,
            kind="ui_block",
            name="Column",
            qualified_name="Page.load.Column",
            span=SourceSpan(start_line=4, end_line=6),
            exact_range=_range(4, 6, 30, 60),
            parent_id=self.method.declaration_id,
        )
        self.sibling = DeclarationOccurrence.create(
            source_ref_id=source_ref_id,
            kind="method",
            name="request",
            qualified_name="Page.request",
            span=SourceSpan(start_line=10, end_line=12),
            exact_range=_range(10, 12, 90, 120),
        )

        self.direct_api = _fact(
            source_ref_id,
            kind="api",
            name="router.pushUrl",
            canonical_name="router.pushUrl",
            exact_range=_range(3, 3, 20, 25),
            owner=OwnerRef("declaration", self.method.declaration_id),
        )
        self.nested_component = _fact(
            source_ref_id,
            kind="component",
            name="Column",
            canonical_name="Column",
            exact_range=_range(4, 4, 35, 40),
            owner=OwnerRef("declaration", self.nested_ui.declaration_id),
        )
        self.recovered_attribute = _fact(
            source_ref_id,
            kind="attribute",
            name="onClick",
            canonical_name="onClick",
            exact_range=_range(5, 5, 45, 50),
            owner=OwnerRef("declaration", self.nested_ui.declaration_id),
            quality="recovered",
            provenance="recovered",
        )
        self.recovered_import_use = _fact(
            source_ref_id,
            kind="import_use",
            name="connectionAlias",
            canonical_name="@ohos.net.connection#default",
            exact_range=_range(6, 6, 55, 64),
            owner=OwnerRef("declaration", self.method.declaration_id),
            quality="recovered",
            provenance="recovered",
        )
        self.unresolved_api = _fact(
            source_ref_id,
            kind="api",
            name="sensor.on",
            canonical_name="sensor.on",
            exact_range=_range(7, 7, 65, 70),
            owner=None,
            quality="unresolved",
            provenance="L0",
        )
        self.sibling_api = _fact(
            source_ref_id,
            kind="api",
            name="http.request",
            canonical_name="http.request",
            exact_range=_range(11, 11, 100, 105),
            owner=OwnerRef("declaration", self.sibling.declaration_id),
        )
        self.sibling_import_use = _fact(
            source_ref_id,
            kind="import_use",
            name="requestClient",
            canonical_name="@ohos.net.http#default",
            exact_range=_range(11, 11, 106, 112),
            owner=OwnerRef("declaration", self.sibling.declaration_id),
        )
        self.unresolved_import_use = _fact(
            source_ref_id,
            kind="import_use",
            name="unresolvedClient",
            canonical_name="@ohos.net.socket#default",
            exact_range=_range(7, 7, 71, 79),
            owner=None,
            quality="unresolved",
            provenance="L0",
        )

        self.file_hints = ScopedFacts(
            components=("Column",),
            apis=("http.request", "router.pushUrl", "sensor.on"),
            attributes=("onClick",),
        )
        self.analysis = FileAnalysis.create(
            source_ref=self.source_ref,
            parser_version="fixture-v1",
            parser_quality=FileParserQuality(
                layer="L1",
                error_nodes=0,
                missing_nodes=0,
            ),
            declarations=(self.method, self.nested_ui, self.sibling),
            review_regions=(),
            fact_occurrences=(
                self.direct_api,
                self.nested_component,
                self.recovered_attribute,
                self.recovered_import_use,
                self.unresolved_api,
                self.unresolved_import_use,
                self.sibling_api,
                self.sibling_import_use,
            ),
            file_hints=self.file_hints,
        )

    def test_projects_direct_and_descendant_facts_without_sibling_leakage(self) -> None:
        unit = _declaration_unit(
            self.source_ref.source_ref_id,
            self.method.declaration_id,
            symbol="Page.load",
            start_line=2,
            end_line=8,
        )

        scope = project(self.analysis, unit)

        self.assertEqual(scope.unit_exact.components, ("Column",))
        self.assertEqual(scope.unit_exact.apis, ("router.pushUrl",))
        self.assertEqual(scope.unit_exact.attributes, ("onClick",))
        self.assertEqual(
            scope.unit_exact.import_uses,
            ("@ohos.net.connection#default",),
        )
        self.assertNotIn("http.request", scope.unit_exact.apis)
        self.assertNotIn("sensor.on", scope.unit_exact.apis)
        self.assertNotIn(
            "@ohos.net.http#default",
            scope.unit_exact.import_uses,
        )
        self.assertNotIn(
            "@ohos.net.socket#default",
            scope.unit_exact.import_uses,
        )
        self.assertEqual(
            scope.exact_occurrence_ids,
            tuple(
                sorted(
                    {
                        self.direct_api.occurrence_id,
                        self.nested_component.occurrence_id,
                        self.recovered_attribute.occurrence_id,
                        self.recovered_import_use.occurrence_id,
                    }
                )
            ),
        )
        self.assertEqual(scope.file_hints, self.file_hints)

        tags = derive_tags(scope.unit_exact.to_code_facts(PATH))
        self.assertIn("has_navigation", tags)
        self.assertIn("has_layout", tags)
        self.assertIn("has_interactive_component", tags)

    def test_projects_sibling_owner_independently(self) -> None:
        unit = _declaration_unit(
            self.source_ref.source_ref_id,
            self.sibling.declaration_id,
            symbol="Page.request",
            start_line=10,
            end_line=12,
        )

        scope = project(self.analysis, unit)

        self.assertEqual(scope.unit_exact.apis, ("http.request",))
        self.assertEqual(scope.unit_exact.components, ())
        self.assertEqual(
            scope.unit_exact.import_uses,
            ("@ohos.net.http#default",),
        )
        self.assertEqual(
            scope.exact_occurrence_ids,
            tuple(
                sorted(
                    (
                        self.sibling_api.occurrence_id,
                        self.sibling_import_use.occurrence_id,
                    )
                )
            ),
        )

    def test_projects_direct_review_region_facts_for_change_set_unit(self) -> None:
        region = ReviewRegion.create(
            source_ref_id=self.source_ref.source_ref_id,
            kind="field_region",
            symbol="Page.value",
            span=SourceSpan(3, 3),
            exact_range=_range(3, 3, 20, 29),
            owner_declaration_id=self.method.declaration_id,
        )
        field_write = _fact(
            self.source_ref.source_ref_id,
            kind="field_write",
            name="value",
            canonical_name="Page.value",
            exact_range=_range(3, 3, 22, 27),
            owner=OwnerRef("region", region.region_id),
        )
        analysis = FileAnalysis.create(
            source_ref=self.source_ref,
            parser_version="fixture-v1",
            parser_quality=FileParserQuality(
                layer="L1",
                error_nodes=0,
                missing_nodes=0,
            ),
            declarations=(self.method,),
            review_regions=(region,),
            fact_occurrences=(field_write,),
            file_hints=ScopedFacts(field_writes=("Page.value",)),
        )
        atom_id = "change-atom:sha256:" + "0" * 64
        unit = ReviewUnit(
            file=PATH,
            unit_symbol="Page.value",
            unit_ref=f"Page.value@{PATH}",
            full_text="// line 3",
            changed_lines=[3],
            file_changed_lines=[3],
            unit_changed_lines=[1],
            unit_id=declaration_unit_id(
                PATH,
                "field_region",
                "Page.value",
                3,
                3,
                start_offset_utf16=20,
                end_offset_utf16=29,
                source_role="head",
                source_ref_id=self.source_ref.source_ref_id,
            ),
            unit_kind="field_region",
            source_span=ReviewUnitSpan(3, 3),
            context_span=ReviewUnitSpan(3, 3),
            changed_new_lines=[3],
            selection_reason="changed_review_region",
            diagnostics=[],
            source_ref_id=self.source_ref.source_ref_id,
            source_role="head",
            change_atom_ids=[atom_id],
            owner_ref=OwnerRef("region", region.region_id),
            identity_source_ref_id=self.source_ref.source_ref_id,
            identity_start_offset_utf16=20,
            identity_end_offset_utf16=29,
        )

        scope = project(analysis, unit)

        self.assertEqual(scope.unit_exact.field_writes, ("Page.value",))
        self.assertEqual(scope.exact_occurrence_ids, (field_write.occurrence_id,))

    def test_unresolved_owner_never_becomes_exact_from_span_alone(self) -> None:
        unit = _declaration_unit(
            self.source_ref.source_ref_id,
            self.method.declaration_id,
            symbol="Page.load",
            start_line=2,
            end_line=8,
        )

        scope = project(self.analysis, unit)

        self.assertNotIn("sensor.on", scope.unit_exact.apis)
        self.assertNotIn(self.unresolved_api.occurrence_id, scope.exact_occurrence_ids)
        self.assertNotIn(
            self.unresolved_import_use.occurrence_id,
            scope.exact_occurrence_ids,
        )
        self.assertIn("sensor.on", scope.file_hints.apis)

    def test_fallback_and_ownerless_units_keep_only_file_hints(self) -> None:
        fallback = _fallback_unit(self.source_ref.source_ref_id)

        fallback_scope = project(self.analysis, fallback)

        self.assertEqual(fallback_scope.unit_exact, ScopedFacts())
        self.assertEqual(fallback_scope.file_hints, self.file_hints)
        self.assertEqual(fallback_scope.diagnostics, ())

        ownerless = _declaration_unit(
            self.source_ref.source_ref_id,
            None,
            symbol="Page.load",
            start_line=2,
            end_line=8,
        )
        ownerless_scope = project(self.analysis, ownerless)
        self.assertEqual(ownerless_scope.unit_exact, ScopedFacts())
        self.assertEqual(ownerless_scope.file_hints, self.file_hints)
        self.assertEqual(ownerless_scope.diagnostics, ("unit_owner_unresolved",))

    def test_rejects_source_revision_mismatch(self) -> None:
        unit = _declaration_unit(
            CodeSourceRef.inline(PATH, SOURCE + "// drift\n").source_ref_id,
            self.method.declaration_id,
            symbol="Page.load",
            start_line=2,
            end_line=8,
        )

        with self.assertRaisesRegex(ValueError, "source_ref_id"):
            project(self.analysis, unit)


def _range(
    start_line: int,
    end_line: int,
    start_offset: int,
    end_offset: int,
) -> ExactRange:
    return ExactRange(
        start_line=start_line,
        end_line=end_line,
        start_offset_utf16=start_offset,
        end_offset_utf16=end_offset,
    )


def _fact(
    source_ref_id: str,
    *,
    kind: str,
    name: str,
    canonical_name: str,
    exact_range: ExactRange,
    owner: OwnerRef | None,
    quality: str = "exact",
    provenance: str = "L1",
) -> FactOccurrence:
    return FactOccurrence.create(
        source_ref_id=source_ref_id,
        kind=kind,  # type: ignore[arg-type]
        name=name,
        canonical_name=canonical_name,
        span=SourceSpan(
            start_line=exact_range.start_line,
            end_line=exact_range.end_line,
        ),
        exact_range=exact_range,
        owner_ref=owner,
        quality=quality,  # type: ignore[arg-type]
        provenance=provenance,  # type: ignore[arg-type]
    )


def _declaration_unit(
    source_ref_id: str,
    declaration_id: str | None,
    *,
    symbol: str,
    start_line: int,
    end_line: int,
) -> ReviewUnit:
    unit = ReviewUnit(
        file=PATH,
        unit_symbol=symbol,
        unit_ref=f"{symbol}@{PATH}",
        full_text="\n".join(
            f"// line {line}" for line in range(start_line, end_line + 1)
        ),
        unit_id=f"{PATH}@method:{symbol}:L{start_line}-L{end_line}",
        unit_kind="method",
        source_span=ReviewUnitSpan(start_line, end_line),
        context_span=ReviewUnitSpan(start_line, end_line),
        changed_new_lines=[],
        selection_reason="innermost_changed_declaration",
        diagnostics=[],
        source_ref_id=source_ref_id,
        owner_ref=(
            None
            if declaration_id is None
            else OwnerRef("declaration", declaration_id)
        ),
    )
    return unit


def _fallback_unit(source_ref_id: str) -> ReviewUnit:
    unit = ReviewUnit(
        file=PATH,
        unit_symbol="hunk-L7-L7",
        unit_ref=f"hunk-L7-L7@{PATH}",
        full_text="\n".join(f"// line {line}" for line in range(5, 10)),
        changed_lines=[7],
        file_changed_lines=[7],
        unit_changed_lines=[3],
        context_degraded=True,
        unit_id=f"{PATH}@fallback:fallback:L7-L7:C5-L9",
        unit_kind="fallback",
        source_span=ReviewUnitSpan(7, 7),
        context_span=ReviewUnitSpan(5, 9),
        changed_new_lines=[7],
        selection_reason="fallback_window",
        diagnostics=[ReviewUnitDiagnostic(code="no_matching_declaration")],
        source_ref_id=source_ref_id,
        owner_ref=None,
    )
    return unit


if __name__ == "__main__":
    unittest.main()
