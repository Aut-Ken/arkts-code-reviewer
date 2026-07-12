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

from arkts_code_reviewer.review_unit_v2_validation.golden import (
    _load_exact_range,
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden" / "review_unit_v2"
MANIFEST = GOLDEN_ROOT / "manifest.json"
BASELINE = GOLDEN_ROOT / "baselines" / "current.json"


class ReviewUnitV2GoldenTest(unittest.TestCase):
    def test_loads_the_frozen_rv201_to_rv216_matrix(self) -> None:
        suite = load_golden_suite(MANIFEST)

        self.assertEqual(
            [case.case_id for case in suite.cases],
            [f"RV2{index:02d}" for index in range(1, 17)],
        )
        self.assertEqual(len(suite.cases), 16)
        for case in suite.cases:
            for source in case.sources:
                self.assertTrue(
                    source.parse_result.analysis.parser_version.startswith("fixture-file-analysis-")
                )
                self.assertTrue(
                    source.snapshot.source_ref.source_ref_id.startswith("code-source:sha256:")
                )

    def test_matrix_freezes_change_roles_regions_fallback_and_quality(self) -> None:
        cases = {case.case_id: case.expected for case in load_golden_suite(MANIFEST).cases}

        rv202 = cases["RV202"]["file_results"]
        self.assertEqual([item["source_role"] for item in rv202], ["base", "head"])
        self.assertNotEqual(
            rv202[0]["units"][0]["unit_id"],
            rv202[1]["units"][0]["unit_id"],
        )
        self.assertEqual(
            rv202[0]["units"][0]["change_atom_ids"],
            rv202[1]["units"][0]["change_atom_ids"],
        )

        self.assertEqual(
            cases["RV203"]["file_results"][0]["units"][0]["changed_old_lines"],
            [1, 2, 3],
        )
        self.assertTrue(all(not item["units"] for item in cases["RV204"]["file_results"]))
        self.assertEqual(
            [item["code"] for item in cases["RV207"]["diagnostics"]],
            ["binary_change_unsupported"],
        )
        self.assertEqual(
            len(cases["RV208"]["file_results"][0]["units"][0]["change_atom_ids"]),
            2,
        )
        self.assertEqual(len(cases["RV209"]["file_results"][0]["units"]), 2)

        unit_kinds = {
            case_id: {
                unit["unit_kind"]
                for result in cases[case_id]["file_results"]
                for unit in result["units"]
            }
            for case_id in ("RV210", "RV211", "RV212")
        }
        self.assertIn("field_region", unit_kinds["RV210"])
        self.assertIn("import_region", unit_kinds["RV211"])
        self.assertEqual(unit_kinds["RV212"], {"fallback"})
        self.assertTrue(
            all(
                unit["context_degraded"]
                and {item["code"] for item in unit["diagnostics"]}
                >= {"parser_degraded", "no_matching_declaration"}
                for unit in cases["RV214"]["file_results"][0]["units"]
            )
        )

    def test_context_span_does_not_turn_unchanged_lines_into_changed_lines(self) -> None:
        case = next(case for case in load_golden_suite(MANIFEST).cases if case.case_id == "RV213")

        for result in case.expected["file_results"]:
            unit = result["units"][0]
            self.assertEqual(unit["context_span"], {"start_line": 1, "end_line": 5})
            effective = (
                unit["changed_old_lines"]
                if result["source_role"] == "base"
                else unit["changed_new_lines"]
            )
            self.assertEqual(effective, [3])

    def test_atom_role_coverage_is_complete_and_exact(self) -> None:
        suite = load_golden_suite(MANIFEST)

        for case in suite.cases:
            expected = case.expected
            coverage_keys = [
                (item["atom_id"], item["source_role"]) for item in expected["coverage"]
            ]
            self.assertEqual(coverage_keys, sorted(set(coverage_keys)))
            for item in expected["coverage"]:
                self.assertEqual(item["lines"], sorted(set(item["lines"])))

    def test_evaluator_is_perfect_repeatable_permutation_safe_and_strict(self) -> None:
        suite = load_golden_suite(MANIFEST)
        first = evaluate_golden_suite(suite)
        second = evaluate_golden_suite(suite)

        self.assertEqual(first, second)
        self.assertTrue(is_perfect(first))
        self.assertEqual(first["matched_case_count"], 16)
        self.assertTrue(all(item["repeat_equal"] for item in first["cases"]))
        self.assertTrue(all(item["permutation_equal"] for item in first["cases"]))
        assert_strict_baseline(first, suite, BASELINE)

    def test_perfect_gate_rejects_forged_counts_without_case_rows(self) -> None:
        self.assertFalse(
            is_perfect(
                {
                    "schema_version": "review-unit-v2-golden-report-v1",
                    "case_count": 16,
                    "matched_case_count": 16,
                    "mismatched_case_count": 0,
                    "cases": [],
                }
            )
        )

    def test_loader_rejects_duplicate_json_keys(self) -> None:
        with self._copy_golden() as root:
            manifest = root / "manifest.json"
            text = manifest.read_text(encoding="utf-8").replace(
                '  "suite_id": "review-unit-v2-ru4",',
                '  "suite_id": "review-unit-v2-ru4",\n  "suite_id": "duplicate",',
                1,
            )
            manifest.write_text(text, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_golden_suite(manifest)

    def test_loader_rejects_unknown_missing_duplicate_case_and_alias_fields(self) -> None:
        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "unknown field": lambda data: data["cases"][0].__setitem__("unknown", True),
            "missing field": lambda data: data["cases"][0]["expected"].pop("coverage"),
            "duplicate case": lambda data: data["cases"].__setitem__(
                1, copy.deepcopy(data["cases"][0])
            ),
            "duplicate source alias": lambda data: data["cases"][1]["sources"][1].__setitem__(
                "alias", "base"
            ),
        }
        self._assert_manifest_mutations_rejected(mutations)

    def test_loader_rejects_hash_provenance_span_line_owner_and_id_drift(self) -> None:
        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "source hash": lambda data: data["cases"][0]["sources"][0].__setitem__(
                "content_sha256", "0" * 64
            ),
            "revision": lambda data: data["cases"][0]["sources"][0].__setitem__(
                "revision", "drift"
            ),
            "zero line": lambda data: data["cases"][0]["sources"][0]["analysis"]["declarations"][0][
                "span"
            ].__setitem__("start_line", 0),
            "utf16": lambda data: data["cases"][0]["sources"][0]["analysis"]["declarations"][
                0
            ].__setitem__("end_offset_utf16", 1),
            "unsorted changed lines": lambda data: data["cases"][0]["files"][0]["atoms"][
                0
            ].__setitem__("added_new_lines", [2, 1, 3]),
            "dangling owner": lambda data: data["cases"][0]["expected"]["file_results"][0]["units"][
                0
            ]["owner"].__setitem__("alias", "missing"),
            "stable id": lambda data: data["cases"][0]["expected"]["file_results"][0]["units"][
                0
            ].__setitem__("unit_id", "not-an-id"),
        }
        self._assert_manifest_mutations_rejected(mutations)

    def test_loader_rejects_unit_atom_assignment_drift(self) -> None:
        with self._copy_golden() as root:
            manifest = root / "manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            unit = data["cases"][7]["expected"]["file_results"][0]["units"][0]
            unit["change_atom_aliases"] = unit["change_atom_aliases"][:1]
            unit["change_atom_ids"] = unit["change_atom_ids"][:1]
            manifest.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "changed lines and ChangeAtom IDs"):
                load_golden_suite(manifest)

    def test_exact_range_loader_accepts_distinct_intra_line_occurrences(self) -> None:
        source = "const first = 1; const second = 2;\n"

        span, exact_range = _load_exact_range(
            {
                "span": {"start_line": 1, "end_line": 1},
                "start_offset_utf16": 17,
                "end_offset_utf16": 33,
            },
            source,
            "fixture.same_line",
        )

        self.assertEqual((span.start_line, span.end_line), (1, 1))
        self.assertEqual(
            (exact_range.start_offset_utf16, exact_range.end_offset_utf16),
            (17, 33),
        )

    def test_loader_rejects_output_order_and_symlink_sources(self) -> None:
        with self._copy_golden() as root:
            manifest = root / "manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["cases"][1]["expected"]["file_results"].reverse()
            manifest.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "stable output order"):
                load_golden_suite(manifest)

        with self._copy_golden() as root:
            source = root / "sources" / "RV201_head.ets"
            target = root / "source-target.ets"
            source.rename(target)
            source.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                load_golden_suite(root / "manifest.json")

    def test_baseline_writer_cannot_overwrite_expected_truth(self) -> None:
        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite)

        for path in (MANIFEST, GOLDEN_ROOT / "baselines" / "history.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "only update"):
                write_current_baseline(report, suite, path)

    def _assert_manifest_mutations_rejected(
        self,
        mutations: dict[str, Callable[[dict[str, Any]], object]],
    ) -> None:
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

    @contextmanager
    def _copy_golden(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "review_unit_v2"
            shutil.copytree(GOLDEN_ROOT, root)
            yield root


if __name__ == "__main__":
    unittest.main()
