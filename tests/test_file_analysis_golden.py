from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from arkts_code_reviewer.code_analysis.file_analysis_models import (
    FactOccurrence,
    FileAnalysis,
    FileParseResult,
    OwnerRef,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    ArktsFileAnalysisParser,
)
from arkts_code_reviewer.file_analysis_validation.golden import (
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden" / "file_analysis"
MANIFEST = GOLDEN_ROOT / "manifest.json"
BASELINE = GOLDEN_ROOT / "baselines" / "current.json"


class FileAnalysisGoldenTest(unittest.TestCase):
    def test_loads_fifteen_human_reviewable_cases(self) -> None:
        suite = load_golden_suite(MANIFEST)

        self.assertEqual(len(suite.cases), 15)
        self.assertEqual(
            [case.case_id for case in suite.cases],
            sorted(case.case_id for case in suite.cases),
        )
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        serialized_expected = json.dumps(
            [case["expected"] for case in manifest["cases"]]
        )
        self.assertNotIn("declaration_id", serialized_expected)
        self.assertNotIn("occurrence_id", serialized_expected)
        self.assertNotIn("region_id", serialized_expected)

    def test_same_line_case_freezes_utf16_identity_and_owner_aliases(self) -> None:
        suite = load_golden_suite(MANIFEST)
        same_line = next(case for case in suite.cases if case.case_id.startswith("FA005"))
        components = [
            item
            for item in same_line.expected["fact_occurrences"]
            if item["kind"] == "component"
        ]

        columns = [item for item in components if item["name"] == "Column"]
        texts = [item for item in components if item["name"] == "Text"]
        self.assertEqual({item["span"]["start_line"] for item in components}, {3})
        self.assertEqual([item["start_offset_utf16"] for item in columns], [41, 64])
        self.assertEqual([item["start_offset_utf16"] for item in texts], [52, 75])
        self.assertEqual(
            [item["owner"]["alias"] for item in columns],
            ["first_column", "second_column"],
        )

    def test_unicode_case_freezes_utf16_and_new_fact_kinds(self) -> None:
        suite = load_golden_suite(MANIFEST)
        unicode_case = next(
            case for case in suite.cases if case.case_id.startswith("FA013")
        )
        facts = unicode_case.expected["fact_occurrences"]
        emoji = next(item for item in facts if item["name"] == "'😀'")
        resource = next(item for item in facts if item["kind"] == "resource_reference")

        self.assertEqual(
            (emoji["start_offset_utf16"], emoji["end_offset_utf16"]),
            (128, 132),
        )
        self.assertEqual(resource["start_offset_utf16"], 149)
        self.assertTrue(
            {"call", "import_use", "resource_reference", "string_literal"}.issubset(
                {item["kind"] for item in facts}
            )
        )

    def test_suite_covers_every_frozen_declaration_and_fact_kind(self) -> None:
        suite = load_golden_suite(MANIFEST)

        self.assertEqual(
            {
                declaration["kind"]
                for case in suite.cases
                for declaration in case.expected["declarations"]
            },
            {
                "struct",
                "class",
                "function",
                "method",
                "build_method",
                "builder",
                "ui_block",
            },
        )
        self.assertEqual(
            {
                fact["kind"]
                for case in suite.cases
                for fact in case.expected["fact_occurrences"]
            },
            {
                "component",
                "api",
                "decorator",
                "attribute",
                "symbol",
                "syntax",
                "import_binding",
                "import_use",
                "field_read",
                "field_write",
                "call",
                "string_literal",
                "resource_reference",
            },
        )

    def test_top_level_call_freezes_honest_unresolved_owner(self) -> None:
        suite = load_golden_suite(MANIFEST)
        unresolved = next(
            case for case in suite.cases if case.case_id.startswith("FA015")
        )

        self.assertEqual(unresolved.expected["declarations"], [])
        self.assertEqual(
            unresolved.expected["diagnostics"],
            ["unresolved_fact_owner"],
        )
        for fact in unresolved.expected["fact_occurrences"]:
            if fact["kind"] in {"api", "import_use"}:
                self.assertIsNone(fact["owner"])
                self.assertEqual(fact["quality"], "unresolved")

    def test_evaluator_is_perfect_and_matches_strict_baseline(self) -> None:
        suite = load_golden_suite(MANIFEST)
        first = evaluate_golden_suite(suite)
        second = evaluate_golden_suite(suite)

        self.assertEqual(first, second)
        self.assertTrue(is_perfect(first))
        self.assertEqual(first["matched_case_count"], 15)
        assert_strict_baseline(first, suite, BASELINE)

    def test_loader_rejects_duplicate_json_keys(self) -> None:
        with self._copy_golden() as root:
            manifest = root / "manifest.json"
            text = manifest.read_text(encoding="utf-8")
            text = text.replace(
                '  "suite_id": "file-analysis-golden-v1",',
                '  "suite_id": "file-analysis-golden-v1",\n'
                '  "suite_id": "duplicate",',
                1,
            )
            manifest.write_text(text, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_golden_suite(manifest)

    def test_loader_rejects_case_schema_and_identity_drift(self) -> None:
        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "duplicate case": lambda data: data["cases"].append(
                copy.deepcopy(data["cases"][0])
            ),
            "unknown field": lambda data: data["cases"][0].__setitem__(
                "unknown", True
            ),
            "missing field": lambda data: data["cases"][0]["expected"].pop(
                "diagnostics"
            ),
            "zero based line": lambda data: data["cases"][0]["expected"][
                "fact_occurrences"
            ][0].__setitem__(3, 0),
            "reversed offset": lambda data: data["cases"][0]["expected"][
                "fact_occurrences"
            ][0].__setitem__(6, 1),
            "dangling owner": lambda data: data["cases"][0]["expected"][
                "fact_occurrences"
            ][0][7].__setitem__(1, "missing_owner"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self._copy_golden() as root:
                manifest = root / "manifest.json"
                data = json.loads(manifest.read_text(encoding="utf-8"))
                mutate(data)
                manifest.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    load_golden_suite(manifest)

    def test_loader_rejects_source_hash_and_expected_order_drift(self) -> None:
        with self._copy_golden() as root:
            source = root / "sources" / "fa001_api_alias.ets"
            source.write_text(source.read_text(encoding="utf-8") + "// drift\n")
            with self.assertRaisesRegex(ValueError, "source hash drift"):
                load_golden_suite(root / "manifest.json")

        with self._copy_golden() as root:
            manifest = root / "manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            facts = data["cases"][3]["expected"]["fact_occurrences"]
            facts.reverse()
            manifest.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "stable source order"):
                load_golden_suite(manifest)

    def test_baseline_writer_cannot_target_expected_or_history(self) -> None:
        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite)

        for path in (MANIFEST, GOLDEN_ROOT / "baselines" / "history.json"):
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError, "only update"
            ):
                write_current_baseline(report, suite, path)

    def test_complete_projection_rejects_an_unreviewed_actual_occurrence(self) -> None:
        class ExtraOccurrenceParser:
            def __init__(self) -> None:
                self.base = ArktsFileAnalysisParser()

            def parse_file(self, source_ref: object, source: str) -> FileParseResult:
                result = self.base.parse_file(source_ref, source)  # type: ignore[arg-type]
                analysis = result.analysis
                owner = analysis.declarations[0]
                occurrence = FactOccurrence.create(
                    source_ref_id=analysis.source_ref.source_ref_id,
                    kind="call",
                    name="unexpected.call",
                    canonical_name="unexpected.call",
                    span=owner.span,
                    exact_range=owner.exact_range,
                    owner_ref=OwnerRef("declaration", owner.declaration_id),
                )
                occurrences = tuple(
                    sorted(
                        (*analysis.fact_occurrences, occurrence),
                        key=lambda item: (
                            item.span.start_line,
                            item.exact_range.start_offset_utf16,
                            item.span.end_line,
                            item.exact_range.end_offset_utf16,
                            item.kind,
                            item.canonical_name or item.name,
                            item.occurrence_id,
                        ),
                    )
                )
                augmented = FileAnalysis.create(
                    source_ref=analysis.source_ref,
                    parser_version=analysis.parser_version,
                    parser_quality=analysis.parser_quality,
                    file_hints=analysis.file_hints,
                    declarations=analysis.declarations,
                    review_regions=analysis.review_regions,
                    fact_occurrences=occurrences,
                    diagnostics=analysis.diagnostics,
                )
                return FileParseResult(
                    analysis=augmented,
                    compatibility_facts=result.compatibility_facts,
                )

        report = evaluate_golden_suite(
            load_golden_suite(MANIFEST),
            ExtraOccurrenceParser(),
        )

        self.assertFalse(is_perfect(report))
        self.assertTrue(all(not case["matched"] for case in report["cases"]))
        self.assertTrue(
            all(
                any("fact_occurrences" in difference for difference in case["differences"])
                for case in report["cases"]
            )
        )

    @contextmanager
    def _copy_golden(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "file_analysis"
            shutil.copytree(GOLDEN_ROOT, destination)
            yield destination


if __name__ == "__main__":
    unittest.main()
