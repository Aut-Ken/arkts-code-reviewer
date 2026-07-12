from __future__ import annotations

import dataclasses
import unittest

from arkts_code_reviewer.code_analysis.file_analysis_models import (
    FILE_ANALYSIS_SCHEMA_VERSION,
    CodeSourceRef,
    DeclarationOccurrence,
    ExactRange,
    FactOccurrence,
    FileAnalysis,
    FileParseResult,
    FileParserQuality,
    OwnerRef,
    ReviewRegion,
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.code_analysis.models import CodeFacts, SourceSpan

PATH = "src/Model.ets"
SOURCE = "struct Host {\n  run() {}\n}\n"


class FileAnalysisModelValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source_ref = CodeSourceRef.inline(PATH, SOURCE)
        self.quality = FileParserQuality(layer="L1", error_nodes=0, missing_nodes=0)

    def test_file_analysis_rejects_wrong_nested_model_types(self) -> None:
        valid = {
            "schema_version": FILE_ANALYSIS_SCHEMA_VERSION,
            "analysis_id": FileAnalysis.expected_id(
                self.source_ref.source_ref_id,
                "fixture-v1",
            ),
            "source_ref": self.source_ref,
            "parser_version": "fixture-v1",
            "parser_quality": self.quality,
            "file_hints": ScopedFacts(),
        }
        mutations = {
            "source_ref": "not-a-source-ref",
            "parser_quality": "not-parser-quality",
            "file_hints": "not-scoped-facts",
            "declarations": [],
            "review_regions": [],
            "fact_occurrences": [],
        }
        for field, wrong_value in mutations.items():
            with self.subTest(field=field):
                values = {**valid, field: wrong_value}
                with self.assertRaises(ValueError):
                    FileAnalysis(**values)  # type: ignore[arg-type]

    def test_scope_and_analysis_diagnostics_are_closed_enums(self) -> None:
        with self.assertRaisesRegex(ValueError, "unit_exact"):
            UnitFactScope(
                unit_id="unit",
                source_ref_id=self.source_ref.source_ref_id,
                unit_exact="wrong",  # type: ignore[arg-type]
                file_hints=ScopedFacts(),
            )
        with self.assertRaisesRegex(ValueError, "unsupported codes"):
            UnitFactScope(
                unit_id="unit",
                source_ref_id=self.source_ref.source_ref_id,
                unit_exact=ScopedFacts(),
                file_hints=ScopedFacts(),
                diagnostics=("free_text",),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "unsupported codes"):
            FileAnalysis.create(
                source_ref=self.source_ref,
                parser_version="fixture-v1",
                parser_quality=self.quality,
                file_hints=ScopedFacts(),
                diagnostics=("free_text",),  # type: ignore[arg-type]
            )

    def test_file_parse_result_rejects_untyped_members(self) -> None:
        analysis = FileAnalysis.create(
            source_ref=self.source_ref,
            parser_version="fixture-v1",
            parser_quality=self.quality,
            file_hints=ScopedFacts(),
        )
        with self.assertRaisesRegex(ValueError, "analysis"):
            FileParseResult(
                analysis="wrong",  # type: ignore[arg-type]
                compatibility_facts=CodeFacts(path=PATH),
            )
        with self.assertRaisesRegex(ValueError, "compatibility_facts"):
            FileParseResult(
                analysis=analysis,
                compatibility_facts="wrong",  # type: ignore[arg-type]
            )

    def test_file_parse_result_rejects_hint_and_warning_drift(self) -> None:
        mismatched_hints = FileAnalysis.create(
            source_ref=self.source_ref,
            parser_version="fixture-v1",
            parser_quality=self.quality,
            file_hints=ScopedFacts(apis=("router.back",)),
        )
        with self.assertRaisesRegex(ValueError, "file_hints"):
            FileParseResult(
                analysis=mismatched_hints,
                compatibility_facts=CodeFacts(path=PATH, parser_layer="L1"),
            )

        warning_quality = FileParserQuality(
            layer="L1",
            error_nodes=0,
            missing_nodes=0,
            warnings=("warning: one",),
        )
        mismatched_warnings = FileAnalysis.create(
            source_ref=self.source_ref,
            parser_version="fixture-v1",
            parser_quality=warning_quality,
            file_hints=ScopedFacts(),
        )
        with self.assertRaisesRegex(ValueError, "warnings"):
            FileParseResult(
                analysis=mismatched_warnings,
                compatibility_facts=CodeFacts(path=PATH, parser_layer="L1"),
            )

    def test_declaration_parent_rejects_self_equal_range_and_cycles(self) -> None:
        host = _declaration(
            self.source_ref,
            kind="struct",
            name="Host",
            qualified_name="Host",
            start_line=1,
            end_line=3,
            start_offset=0,
            end_offset=29,
        )
        self_parent = dataclasses.replace(host, parent_id=host.declaration_id)
        with self.assertRaisesRegex(ValueError, "strictly containing"):
            _analysis(self.source_ref, self.quality, (self_parent,))

        same_range_child = _declaration(
            self.source_ref,
            kind="method",
            name="run",
            qualified_name="Host.run",
            start_line=1,
            end_line=3,
            start_offset=0,
            end_offset=29,
            parent_id=host.declaration_id,
        )
        with self.assertRaisesRegex(ValueError, "strictly containing"):
            _analysis(self.source_ref, self.quality, (same_range_child, host))

        child = _declaration(
            self.source_ref,
            kind="method",
            name="run",
            qualified_name="Host.run",
            start_line=2,
            end_line=2,
            start_offset=16,
            end_offset=24,
            parent_id=host.declaration_id,
        )
        cyclic_host = dataclasses.replace(host, parent_id=child.declaration_id)
        with self.assertRaises(ValueError):
            _analysis(self.source_ref, self.quality, (cyclic_host, child))

    def test_scoped_facts_preserve_exact_api_projection_without_resource_proxy(self) -> None:
        resource_only = ScopedFacts(resource_references=("app.string.title",))
        rawfile = ScopedFacts(
            apis=("$rawfile",),
            resource_references=("assets/icon.png",),
        )
        ordinary = ScopedFacts(
            calls=("helper",),
            string_literals=("'text'",),
        )

        self.assertEqual(resource_only.to_code_facts(PATH).apis, set())
        self.assertEqual(rawfile.to_code_facts(PATH).apis, {"$rawfile"})
        self.assertNotIn("$r", rawfile.to_code_facts(PATH).apis)
        self.assertEqual(ordinary.to_code_facts(PATH).apis, set())

    def test_parser_quality_and_non_l1_structure_invariants(self) -> None:
        self.assertEqual(FileParserQuality(layer="L1").layer, "L1")
        with self.assertRaisesRegex(ValueError, "provided together"):
            FileParserQuality(layer="L1", error_nodes=0)
        with self.assertRaisesRegex(ValueError, "require null"):
            FileParserQuality(layer="L0", error_nodes=0, missing_nodes=0)

        declaration = _declaration(
            self.source_ref,
            kind="struct",
            name="Host",
            qualified_name="Host",
            start_line=1,
            end_line=3,
            start_offset=0,
            end_offset=29,
        )
        with self.assertRaisesRegex(ValueError, "non-L1"):
            FileAnalysis.create(
                source_ref=self.source_ref,
                parser_version="fixture-v1",
                parser_quality=FileParserQuality(layer="L0"),
                file_hints=ScopedFacts(),
                declarations=(declaration,),
            )

    def test_occurrence_and_region_quality_requires_consistent_provenance(self) -> None:
        declaration = _declaration(
            self.source_ref,
            kind="struct",
            name="Host",
            qualified_name="Host",
            start_line=1,
            end_line=3,
            start_offset=0,
            end_offset=29,
        )
        owner = OwnerRef("declaration", declaration.declaration_id)
        exact_range = ExactRange(2, 2, 16, 19)
        span = SourceSpan(2, 2)

        with self.assertRaisesRegex(ValueError, "requires owner_ref"):
            FactOccurrence.create(
                source_ref_id=self.source_ref.source_ref_id,
                kind="call",
                name="run",
                canonical_name="run",
                span=span,
                exact_range=exact_range,
                owner_ref=None,
                quality="recovered",
                provenance="recovered",
            )
        with self.assertRaisesRegex(ValueError, "must not have owner_ref"):
            FactOccurrence.create(
                source_ref_id=self.source_ref.source_ref_id,
                kind="call",
                name="run",
                canonical_name="run",
                span=span,
                exact_range=exact_range,
                owner_ref=owner,
                quality="unresolved",
                provenance="L1",
            )
        with self.assertRaisesRegex(ValueError, "requires L1 provenance"):
            FactOccurrence.create(
                source_ref_id=self.source_ref.source_ref_id,
                kind="call",
                name="run",
                canonical_name="run",
                span=span,
                exact_range=exact_range,
                owner_ref=owner,
                quality="exact",
                provenance="recovered",
            )
        with self.assertRaisesRegex(ValueError, "recovered provenance"):
            ReviewRegion.create(
                source_ref_id=self.source_ref.source_ref_id,
                kind="field_region",
                symbol="Host.value",
                span=span,
                exact_range=exact_range,
                owner_declaration_id=declaration.declaration_id,
                quality="recovered",
                provenance="L1",
            )
        with self.assertRaisesRegex(ValueError, "recovered provenance"):
            ReviewRegion.create(
                source_ref_id=self.source_ref.source_ref_id,
                kind="field_region",
                symbol="Host.value",
                span=span,
                exact_range=exact_range,
                owner_declaration_id=declaration.declaration_id,
                quality="recovered",
                provenance="L0",
            )
        recovered_region = ReviewRegion.create(
            source_ref_id=self.source_ref.source_ref_id,
            kind="field_region",
            symbol="Host.value",
            span=span,
            exact_range=exact_range,
            owner_declaration_id=declaration.declaration_id,
            quality="recovered",
            provenance="recovered",
        )
        self.assertEqual(recovered_region.provenance, "recovered")


def _analysis(
    source_ref: CodeSourceRef,
    quality: FileParserQuality,
    declarations: tuple[DeclarationOccurrence, ...],
) -> FileAnalysis:
    return FileAnalysis.create(
        source_ref=source_ref,
        parser_version="fixture-v1",
        parser_quality=quality,
        file_hints=ScopedFacts(),
        declarations=declarations,
    )


def _declaration(
    source_ref: CodeSourceRef,
    *,
    kind: str,
    name: str,
    qualified_name: str,
    start_line: int,
    end_line: int,
    start_offset: int,
    end_offset: int,
    parent_id: str | None = None,
) -> DeclarationOccurrence:
    return DeclarationOccurrence.create(
        source_ref_id=source_ref.source_ref_id,
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        span=SourceSpan(start_line=start_line, end_line=end_line),
        exact_range=ExactRange(
            start_line=start_line,
            end_line=end_line,
            start_offset_utf16=start_offset,
            end_offset_utf16=end_offset,
        ),
        parent_id=parent_id,
    )


if __name__ == "__main__":
    unittest.main()
