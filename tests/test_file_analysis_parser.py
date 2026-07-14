from __future__ import annotations

import copy
import unittest
from pathlib import Path

from arkts_code_reviewer.code_analysis import (
    FILE_ANALYSIS_SCHEMA_VERSION,
    DeclarationOccurrence,
    ExactRange,
    FactKind,
    FileAnalysisParser,
    FileParserQuality,
    ReviewRegion,
)
from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import (
    ArktsTreeSitterParser,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import CodeSourceRef
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    ArktsFileAnalysisParser,
    LegacyFileAnalysisAdapter,
)
from arkts_code_reviewer.code_analysis.lexical import LexicalParser
from arkts_code_reviewer.code_analysis.models import FileHunk
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.unit_facts import project

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_NODE_MODULE = (
    REPO_ROOT
    / "sidecars"
    / "arkts-parser"
    / "node_modules"
    / "tree-sitter-arkts"
    / "package.json"
)

SAMPLE = """import router from '@ohos.router'

@Component
struct Page {
  @State count: number = 0

  async load() {
    this.count += 1
    await router.back()
  }

  build() {
    Text(`${this.count}`).onClick(() => router.back()) Text('two')
  }
}
"""


class CountingLexicalParser(LexicalParser):
    def __init__(self) -> None:
        self.calls = 0

    def parse(self, source: str, path: str):  # type: ignore[no-untyped-def]
        self.calls += 1
        return super().parse(source, path)


class FileAnalysisParserTest(unittest.TestCase):
    def test_public_file_analysis_contract_is_exported(self) -> None:
        self.assertEqual(FILE_ANALYSIS_SCHEMA_VERSION, "file-analysis-v1")
        self.assertTrue(hasattr(FileAnalysisParser, "parse_file"))
        self.assertTrue(DeclarationOccurrence.__dataclass_fields__)
        self.assertTrue(ExactRange.__dataclass_fields__)
        self.assertTrue(FileParserQuality.__dataclass_fields__)
        self.assertTrue(ReviewRegion.__dataclass_fields__)
        self.assertIsNotNone(FactKind)

    @unittest.skipUnless(
        SIDECAR_NODE_MODULE.is_file(),
        "ArkTS tree-sitter sidecar dependencies are not installed",
    )
    def test_parses_once_and_formalizes_owned_occurrences(self) -> None:
        fallback = CountingLexicalParser()
        parser = ArktsFileAnalysisParser(
            ArktsTreeSitterParser(fallback=fallback)
        )
        source_ref = CodeSourceRef.inline("src/pages/Page.ets", SAMPLE)

        result = parser.parse_file(source_ref, SAMPLE)

        self.assertEqual(fallback.calls, 1)
        self.assertEqual(result.analysis.parser_quality.layer, "L1")
        self.assertEqual(result.compatibility_facts.parser_layer, "L1")
        self.assertEqual(result.compatibility_facts.apis, {"router.back"})
        self.assertTrue(result.analysis.declarations)
        self.assertEqual(
            {region.kind for region in result.analysis.review_regions},
            {"field_region", "import_region"},
        )
        kinds = {item.kind for item in result.analysis.fact_occurrences}
        self.assertTrue(
            {
                "api",
                "attribute",
                "component",
                "decorator",
                "field_read",
                "field_write",
                "import_binding",
                "symbol",
                "syntax",
            }.issubset(kinds)
        )
        exact = [
            item
            for item in result.analysis.fact_occurrences
            if item.quality == "exact"
        ]
        self.assertTrue(exact)
        self.assertTrue(all(item.owner_ref is not None for item in exact))

        method_result = ReviewUnitBuilder().build_file_result(
            source_ref.path,
            SAMPLE,
            result.compatibility_facts,
            "diff",
            [FileHunk(new_start=9, new_lines=1)],
            source_ref_id=source_ref.source_ref_id,
        )
        method_unit = method_result.units[0]
        self.assertIsNotNone(method_unit.owner_ref)
        method_scope = project(result.analysis, method_unit)
        self.assertIn("router.back", method_scope.unit_exact.apis)

        build_result = ReviewUnitBuilder().build_file_result(
            source_ref.path,
            SAMPLE,
            result.compatibility_facts,
            "diff",
            [FileHunk(new_start=13, new_lines=1)],
            source_ref_id=source_ref.source_ref_id,
        )
        build_scope = project(result.analysis, build_result.units[0])
        self.assertIn("Text", build_scope.unit_exact.components)
        self.assertIn("onClick", build_scope.unit_exact.attributes)

        text_occurrences = [
            item
            for item in result.analysis.declarations
            if item.kind == "ui_block" and item.name == "Text"
        ]
        self.assertEqual(len(text_occurrences), 2)
        self.assertEqual(
            len({item.declaration_id for item in text_occurrences}),
            2,
        )
        compatibility_texts = [
            item
            for item in result.compatibility_facts.declarations
            if item.kind == "ui_block" and item.name == "Text"
        ]
        self.assertEqual(
            {item.declaration_id for item in compatibility_texts},
            {item.declaration_id for item in text_occurrences},
        )
        self.assertEqual(
            len({item.start_offset_utf16 for item in compatibility_texts}),
            2,
        )

    def test_unavailable_sidecar_keeps_hints_without_exact_occurrences(self) -> None:
        parser = ArktsFileAnalysisParser(
            ArktsTreeSitterParser(
                fallback=LexicalParser(),
                sidecar_path=REPO_ROOT / "does-not-exist.js",
            )
        )
        source_ref = CodeSourceRef.inline("src/pages/Page.ets", SAMPLE)

        result = parser.parse_file(source_ref, SAMPLE)

        self.assertEqual(result.analysis.parser_quality.layer, "L0")
        self.assertEqual(result.analysis.fact_occurrences, ())
        self.assertEqual(result.analysis.declarations, ())
        self.assertIn("router.back", result.analysis.file_hints.apis)
        self.assertEqual(
            result.analysis.diagnostics,
            ("file_analysis_sidecar_unavailable",),
        )

    def test_invalid_snapshot_fails_closed_to_compatibility_hints(self) -> None:
        class InvalidSnapshotParser(ArktsTreeSitterParser):
            def __init__(self) -> None:
                super().__init__(sidecar_path=Path(__file__))

            def _run_sidecar(
                self,
                source: str,
                path: str,
                output_schema: str | None = None,
            ) -> dict[str, object]:
                return {
                    "output_schema": "file-analysis-v1",
                    "unexpected": True,
                }

        source_ref = CodeSourceRef.inline("src/pages/Page.ets", SAMPLE)
        result = ArktsFileAnalysisParser(InvalidSnapshotParser()).parse_file(
            source_ref, SAMPLE
        )

        self.assertEqual(result.analysis.parser_quality.layer, "parse_degraded")
        self.assertEqual(result.analysis.fact_occurrences, ())
        self.assertEqual(
            result.analysis.diagnostics,
            ("file_analysis_snapshot_invalid",),
        )
        self.assertTrue(
            any(
                warning.startswith("arkts_file_analysis_invalid:")
                for warning in result.compatibility_facts.warnings
            )
        )

    def test_legacy_adapter_calls_parser_once_and_never_invents_exact_facts(self) -> None:
        parser = CountingLexicalParser()
        source_ref = CodeSourceRef.inline("src/pages/Page.ets", SAMPLE)

        result = LegacyFileAnalysisAdapter(parser).parse_file(source_ref, SAMPLE)

        self.assertEqual(parser.calls, 1)
        self.assertEqual(result.analysis.fact_occurrences, ())
        self.assertEqual(result.analysis.review_regions, ())
        self.assertIn("router.back", result.analysis.file_hints.apis)
        self.assertEqual(
            result.analysis.diagnostics,
            ("occurrence_extraction_unavailable",),
        )

    @unittest.skipUnless(
        SIDECAR_NODE_MODULE.is_file(),
        "ArkTS tree-sitter sidecar dependencies are not installed",
    )
    def test_error_and_missing_ranges_mark_affected_declarations_recovered(self) -> None:
        source = "@Component\nstruct Broken {\n  build() { Text( }\n"
        source_ref = CodeSourceRef.inline("src/pages/Broken.ets", source)

        result = ArktsFileAnalysisParser().parse_file(source_ref, source)

        quality = result.analysis.parser_quality
        self.assertEqual(quality.layer, "L1")
        self.assertGreater((quality.error_nodes or 0) + (quality.missing_nodes or 0), 0)
        self.assertTrue(
            any(item.quality == "recovered" for item in result.analysis.declarations)
        )
        self.assertTrue(
            {"parser_error_nodes", "parser_missing_nodes"}
            & set(result.analysis.diagnostics)
        )

    @unittest.skipUnless(
        SIDECAR_NODE_MODULE.is_file(),
        "ArkTS tree-sitter sidecar dependencies are not installed",
    )
    def test_scope_aware_bindings_keep_good_uses_and_demote_shadowed_calls(self) -> None:
        source = """import router from '@ohos.router'
function good() { router.back() }
function destructured({router}) { router.back() }
function caught() { try {} catch (router) { router.back() } }
const arrow = (router) => router.back()
function loop(items: object[]) {
  for (let router of items) { router.back() }
  router.back()
}
function goodAgain() { router.pushUrl({ url: 'x' }) }
"""
        source_ref = CodeSourceRef.inline("src/pages/Scoped.ets", source)

        analysis = ArktsFileAnalysisParser().parse_file(source_ref, source).analysis

        apis = [
            (item.name, item.span.start_line)
            for item in analysis.fact_occurrences
            if item.kind == "api"
        ]
        calls = [
            (item.name, item.span.start_line)
            for item in analysis.fact_occurrences
            if item.kind == "call" and item.name == "router.back"
        ]
        import_uses = [
            item.span.start_line
            for item in analysis.fact_occurrences
            if item.kind == "import_use"
        ]
        self.assertEqual(
            apis,
            [("router.back", 2), ("router.back", 8), ("router.pushUrl", 10)],
        )
        self.assertEqual(
            calls,
            [
                ("router.back", 3),
                ("router.back", 4),
                ("router.back", 5),
                ("router.back", 7),
            ],
        )
        self.assertEqual(import_uses, [2, 8, 10])

    @unittest.skipUnless(
        SIDECAR_NODE_MODULE.is_file(),
        "ArkTS tree-sitter sidecar dependencies are not installed",
    )
    def test_comment_separated_decorator_keeps_v2_owner_and_bridge_identity(self) -> None:
        source = """@Component
struct Decorated {
  // before decorator
  @Builder
  // between decorator and method
  content() { Text('x') }
}
"""
        source_ref = CodeSourceRef.inline("src/pages/Decorated.ets", source)

        result = ArktsFileAnalysisParser().parse_file(source_ref, source)

        builder = next(
            item for item in result.analysis.declarations if item.name == "content"
        )
        compatibility_builder = next(
            item
            for item in result.compatibility_facts.declarations
            if item.name == "content"
        )
        decorator = next(
            item
            for item in result.analysis.fact_occurrences
            if item.kind == "decorator" and item.canonical_name == "@Builder"
        )
        self.assertEqual(builder.span.start_line, 4)
        self.assertEqual(compatibility_builder.span.start_line, 6)
        self.assertEqual(compatibility_builder.declaration_id, builder.declaration_id)
        self.assertIsNotNone(decorator.owner_ref)
        owner_ref = decorator.owner_ref
        assert owner_ref is not None
        self.assertEqual(owner_ref.ref_id, builder.declaration_id)

    @unittest.skipUnless(
        SIDECAR_NODE_MODULE.is_file(),
        "ArkTS tree-sitter sidecar dependencies are not installed",
    )
    def test_exact_calls_literals_resources_import_uses_and_symbol_name_spans(self) -> None:
        source = """import router from '@ohos.router'
function exercise(): string {
  const fake = 'router.back()'
  const message = `line one
line two`
  ;[1].map((value) => value)
  router.back()
  return $r('app.string.title')
}
"""
        source_ref = CodeSourceRef.inline("src/pages/Facts.ets", source)

        analysis = ArktsFileAnalysisParser().parse_file(source_ref, source).analysis

        self.assertIn("arkts-file-analysis-python-v1.0.0", analysis.parser_version)
        self.assertIn("arkts-parser-sidecar-v2.0.0", analysis.parser_version)
        calls = [item for item in analysis.fact_occurrences if item.kind == "call"]
        self.assertEqual([item.name for item in calls], ["[1].map"])
        self.assertFalse(any(item.span.start_line == 3 for item in calls))
        self.assertTrue(
            any(
                item.kind == "api" and item.canonical_name == "router.back"
                for item in analysis.fact_occurrences
            )
        )
        self.assertTrue(
            any(item.kind == "import_use" for item in analysis.fact_occurrences)
        )
        literals = [
            item for item in analysis.fact_occurrences if item.kind == "string_literal"
        ]
        self.assertTrue(any("line one\\nline two" in item.name for item in literals))
        self.assertFalse(any(item.name == "string" for item in literals))
        resource = next(
            item
            for item in analysis.fact_occurrences
            if item.kind == "resource_reference"
        )
        self.assertEqual(resource.canonical_name, "app.string.title")
        self.assertIsNotNone(resource.owner_ref)

        symbol = next(
            item
            for item in analysis.fact_occurrences
            if item.kind == "symbol" and item.name == "exercise"
        )
        declaration = next(
            item for item in analysis.declarations if item.name == "exercise"
        )
        self.assertLess(symbol.exact_range.start_offset_utf16, symbol.exact_range.end_offset_utf16)
        self.assertLess(
            symbol.exact_range.end_offset_utf16 - symbol.exact_range.start_offset_utf16,
            declaration.exact_range.end_offset_utf16
            - declaration.exact_range.start_offset_utf16,
        )

    @unittest.skipUnless(
        SIDECAR_NODE_MODULE.is_file(),
        "ArkTS tree-sitter sidecar dependencies are not installed",
    )
    def test_snapshot_line_and_legacy_value_drift_fail_closed(self) -> None:
        source_ref = CodeSourceRef.inline("src/pages/Page.ets", SAMPLE)
        raw = ArktsTreeSitterParser()._run_sidecar(
            SAMPLE,
            source_ref.path,
            output_schema="file-analysis-v1",
        )

        class FixedSnapshotParser(ArktsTreeSitterParser):
            def __init__(self, snapshot: dict[str, object]) -> None:
                super().__init__(sidecar_path=Path(__file__))
                self.snapshot = snapshot

            def _run_sidecar(
                self,
                source: str,
                path: str,
                output_schema: str | None = None,
            ) -> dict[str, object]:
                return self.snapshot

        mutations = []
        wrong_line = copy.deepcopy(raw)
        wrong_line["declarations_v2"][0]["span"]["start_line"] += 1
        mutations.append(wrong_line)
        zero_based_line = copy.deepcopy(raw)
        zero_based_line["raw_occurrences"][0]["span"]["start_line"] = 0
        mutations.append(zero_based_line)
        repeated_component = copy.deepcopy(raw)
        repeated_component["components"].append(repeated_component["components"][0])
        mutations.append(repeated_component)
        malformed_declaration = copy.deepcopy(raw)
        malformed_declaration["declarations"][0]["unknown"] = True
        mutations.append(malformed_declaration)

        for snapshot in mutations:
            with self.subTest(snapshot=snapshot):
                result = ArktsFileAnalysisParser(
                    FixedSnapshotParser(snapshot)
                ).parse_file(source_ref, SAMPLE)
                self.assertEqual(
                    result.analysis.parser_quality.layer,
                    "parse_degraded",
                )
                self.assertEqual(result.analysis.fact_occurrences, ())
                self.assertEqual(
                    result.analysis.diagnostics,
                    ("file_analysis_snapshot_invalid",),
                )

        ambiguous = copy.deepcopy(raw)
        raw_call = next(
            item
            for item in ambiguous["raw_occurrences"]
            if item["kind"] == "raw_call" and item["name"] == "router.back"
        )
        raw_call["binding_status"] = "ambiguous"
        ambiguous_line = raw_call["span"]["start_line"]
        ambiguous_result = ArktsFileAnalysisParser(
            FixedSnapshotParser(ambiguous)
        ).parse_file(source_ref, SAMPLE)
        self.assertEqual(ambiguous_result.analysis.parser_quality.layer, "L1")
        self.assertIn(
            "ambiguous_binding_scope",
            ambiguous_result.analysis.diagnostics,
        )
        self.assertFalse(
            any(
                item.kind == "api" and item.name == "router.back"
                and item.span.start_line == ambiguous_line
                for item in ambiguous_result.analysis.fact_occurrences
            )
        )
        self.assertTrue(
            any(
                item.kind == "call" and item.name == "router.back"
                and item.span.start_line == ambiguous_line
                for item in ambiguous_result.analysis.fact_occurrences
            )
        )

        ambiguous_import = copy.deepcopy(raw)
        raw_import_use = next(
            item
            for item in ambiguous_import["raw_occurrences"]
            if item["kind"] == "import_use"
        )
        raw_import_use["binding_status"] = "ambiguous"
        ambiguous_import_result = ArktsFileAnalysisParser(
            FixedSnapshotParser(ambiguous_import)
        ).parse_file(source_ref, SAMPLE)
        self.assertIn(
            "ambiguous_binding_scope",
            ambiguous_import_result.analysis.diagnostics,
        )
        self.assertFalse(
            any(
                item.kind == "import_use"
                and item.canonical_name == raw_import_use["canonical_name"]
                and item.span.start_line == raw_import_use["span"]["start_line"]
                for item in ambiguous_import_result.analysis.fact_occurrences
            )
        )

    @unittest.skipUnless(
        SIDECAR_NODE_MODULE.is_file(),
        "ArkTS tree-sitter sidecar dependencies are not installed",
    )
    def test_component_modifier_call_spine_does_not_invalidate_snapshot(self) -> None:
        source = """@Component
struct DialogPage {
  build() {
    Button('点我关闭弹窗').onClick(() => this.close())
  }

  close() {}
}
"""
        source_ref = CodeSourceRef.inline("src/pages/DialogPage.ets", source)

        result = ArktsFileAnalysisParser().parse_file(source_ref, source)

        self.assertEqual(result.analysis.parser_quality.layer, "L1")
        self.assertNotIn("file_analysis_snapshot_invalid", result.analysis.diagnostics)
        self.assertTrue(result.analysis.declarations)
        self.assertTrue(
            any(
                item.kind == "component" and item.canonical_name == "Button"
                for item in result.analysis.fact_occurrences
            )
        )
        self.assertTrue(
            any(
                item.kind == "attribute" and item.canonical_name == "onClick"
                for item in result.analysis.fact_occurrences
            )
        )

if __name__ == "__main__":
    unittest.main()
