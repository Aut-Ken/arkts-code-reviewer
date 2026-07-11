from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from arkts_code_reviewer.code_analysis.models import (
    CodeFacts,
    Declaration,
    FileHunk,
    ReviewUnit,
    ReviewUnitDiagnostic,
    ReviewUnitSpan,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    REVIEW_UNIT_DIAGNOSTIC_CODES,
    REVIEW_UNIT_KINDS,
    SELECTION_REASONS,
    declaration_unit_id,
    normalize_review_path,
)
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.review_unit_validation.golden import (
    evaluate_golden_suite,
    is_perfect,
    load_golden_baseline,
    load_golden_suite,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden" / "review_unit"
MANIFEST = GOLDEN_ROOT / "manifest.json"
BEFORE_RU1_BASELINE = GOLDEN_ROOT / "baselines" / "before-ru1.json"
CURRENT_BASELINE = GOLDEN_ROOT / "baselines" / "current.json"


class ReviewUnitGoldenTest(unittest.TestCase):
    def test_manifest_is_self_contained_and_freezes_the_contract(self) -> None:
        suite = load_golden_suite(MANIFEST)

        self.assertEqual(suite.suite_id, "review-unit-golden-v1")
        self.assertEqual(len(suite.cases), 16)
        self.assertEqual(
            [case.case_id for case in suite.cases],
            sorted(case.case_id for case in suite.cases),
        )
        self.assertEqual(
            {case.target_phase for case in suite.cases},
            {"RU-1", "RU-2", "RU-4", "RU-5"},
        )
        for case in suite.cases:
            with self.subTest(case_id=case.case_id):
                self.assertTrue(case.source_path.is_file())
                self.assertEqual(case.source_metadata["source_id"], "review-unit-golden")
                self.assertEqual(case.source_metadata["revision"], "review-unit-golden-v1")

        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["frozen_contract"],
            {
                "unit_kinds": list(REVIEW_UNIT_KINDS),
                "selection_reasons": list(SELECTION_REASONS),
                "diagnostic_codes": list(REVIEW_UNIT_DIAGNOSTIC_CODES),
            },
        )

    def test_evaluator_is_repeatable(self) -> None:
        suite = load_golden_suite(MANIFEST)

        first = evaluate_golden_suite(suite)
        second = evaluate_golden_suite(suite)

        self.assertEqual(first, second)

    def test_ru1_gate_checks_outside_line_diagnostics_on_future_cases(self) -> None:
        class MissingOutsideDiagnosticBuilder(ReviewUnitBuilder):
            def build_diff_units(
                self,
                path: str,
                source: str,
                facts: CodeFacts,
                hunks: list[FileHunk],
            ) -> list[ReviewUnit]:
                units = super().build_diff_units(path, source, facts, hunks)
                for unit in units:
                    unit.diagnostics = [
                        item
                        for item in unit.diagnostics
                        if item.code != "changed_lines_outside_context"
                    ]
                return units

        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite, MissingOutsideDiagnosticBuilder())

        self.assertTrue(
            all(
                case["matched"]
                for case in report["cases"]
                if case["target_phase"] == "RU-1"
            )
        )
        self.assertFalse(is_perfect(report, "RU-1"))

    def test_ru1_gate_recomputes_identity_on_future_cases(self) -> None:
        class DriftedFutureIdentityBuilder(ReviewUnitBuilder):
            def build_diff_units(
                self,
                path: str,
                source: str,
                facts: CodeFacts,
                hunks: list[FileHunk],
            ) -> list[ReviewUnit]:
                units = super().build_diff_units(path, source, facts, hunks)
                if len(hunks) == 1 and hunks[0] == FileHunk(new_start=9, new_lines=5):
                    units[0].unit_id += ":drift"
                return units

        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite, DriftedFutureIdentityBuilder())

        self.assertTrue(
            all(
                case["matched"]
                for case in report["cases"]
                if case["target_phase"] == "RU-1"
            )
        )
        self.assertFalse(is_perfect(report, "RU-1"))

    def test_ru1_gate_does_not_ignore_future_case_crashes(self) -> None:
        class CrashingCrossOwnerBuilder(ReviewUnitBuilder):
            def build_diff_units(
                self,
                path: str,
                source: str,
                facts: CodeFacts,
                hunks: list[FileHunk],
            ) -> list[ReviewUnit]:
                if len(hunks) == 1 and hunks[0] == FileHunk(new_start=9, new_lines=5):
                    raise RuntimeError("simulated RU-1 regression")
                return super().build_diff_units(path, source, facts, hunks)

        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite, CrashingCrossOwnerBuilder())

        self.assertTrue(
            all(
                case["matched"]
                for case in report["cases"]
                if case["target_phase"] == "RU-1"
            )
        )
        self.assertFalse(is_perfect(report, "RU-1"))

    def test_ru1_gate_rejects_unrelated_value_error_in_out_of_range_case(self) -> None:
        class WrongFailClosedErrorBuilder(ReviewUnitBuilder):
            def build_diff_units(
                self,
                path: str,
                source: str,
                facts: CodeFacts,
                hunks: list[FileHunk],
            ) -> list[ReviewUnit]:
                if any(hunk.new_start == 99 for hunk in hunks):
                    raise ValueError("unrelated identity regression")
                return super().build_diff_units(path, source, facts, hunks)

        report = evaluate_golden_suite(
            load_golden_suite(MANIFEST),
            WrongFailClosedErrorBuilder(),
        )

        self.assertFalse(is_perfect(report, "RU-1"))

    def test_gate_rejects_corrupted_compatibility_fields_and_bool_coercion(self) -> None:
        class CorruptCompatibilityBuilder(ReviewUnitBuilder):
            def build_units(  # type: ignore[override]
                self,
                path: str,
                source: str,
                facts: CodeFacts,
                mode: str,
                hunks: list[FileHunk],
            ) -> list[ReviewUnit]:
                units = super().build_units(path, source, facts, mode, hunks)  # type: ignore[arg-type]
                for unit in units:
                    unit.unit_ref = "drifted@legacy-ref"
                    unit.context_degraded = int(unit.context_degraded)  # type: ignore[assignment]
                    unit.host_summary = None  # type: ignore[assignment]
                return units

        report = evaluate_golden_suite(
            load_golden_suite(MANIFEST),
            CorruptCompatibilityBuilder(),
        )

        self.assertFalse(is_perfect(report, "RU-1"))
        self.assertTrue(
            any(case["error"] is not None for case in report["cases"][:5])
        )

    def test_before_ru1_baseline_exposes_collision_and_order_dependence(self) -> None:
        suite = load_golden_suite(MANIFEST)
        baseline = load_golden_baseline(BEFORE_RU1_BASELINE, suite=suite)
        collision = next(
            case
            for case in baseline["report"]["cases"]
            if case["case_id"] == "RU004-collision-same-name-ui"
        )

        self.assertEqual(len(collision["expected"]["units"]), 2)
        self.assertEqual(len(collision["actual"]["units"]), 1)
        self.assertEqual(collision["legacy_units"][0]["changed_lines"], [5, 163])
        self.assertFalse(collision["reversed_hunks_equal"])
        self.assertTrue(
            any("expected 2, actual 1" in item for item in collision["differences"])
        )

    def test_current_builder_matches_strict_baseline_and_ru1_target(self) -> None:
        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite)
        baseline = load_golden_baseline(CURRENT_BASELINE, suite=suite)

        self.maxDiff = None
        self.assertEqual(report, baseline["report"])
        self.assertTrue(is_perfect(report, "RU-1"))
        self.assertFalse(is_perfect(report))

        collision = next(
            case
            for case in report["cases"]
            if case["case_id"] == "RU004-collision-same-name-ui"
        )
        self.assertEqual(len(collision["actual"]["units"]), 2)
        self.assertEqual(
            len({unit["unit_id"] for unit in collision["actual"]["units"]}),
            2,
        )
        self.assertEqual(
            {unit["unit_ref"] for unit in collision["legacy_units"]},
            {"CollisionPage.build.Column@golden/review_unit/CollisionPage.ets"},
        )
        self.assertTrue(collision["reversed_hunks_equal"])

        cross_owner = next(
            case
            for case in report["cases"]
            if case["case_id"] == "RU006-cross-two-methods"
        )
        self.assertEqual(
            cross_owner["actual"]["units"][0]["diagnostics"],
            [{"code": "changed_lines_outside_context", "lines": [11, 12, 13]}],
        )

        with_global_invariant = copy.deepcopy(report)
        cross_owner_copy = next(
            case
            for case in with_global_invariant["cases"]
            if case["case_id"] == "RU006-cross-two-methods"
        )
        cross_owner_copy["invariant_violations"] = ["simulated RU-1 invariant drift"]
        self.assertFalse(is_perfect(with_global_invariant, "RU-1"))

    def test_builder_slices_full_text_from_context_span(self) -> None:
        source = "function work() {\n  return\n}\n"
        facts = CodeFacts(
            path="src/work.ets",
            declarations=[
                Declaration(
                    kind="function",
                    name="work",
                    qualified_name="work",
                    span=SourceSpan(start_line=1, end_line=3),
                    text="stale declaration text",
                )
            ],
        )

        unit = ReviewUnitBuilder().build_diff_units(
            "src/work.ets",
            source,
            facts,
            [FileHunk(new_start=2, new_lines=1)],
        )[0]

        self.assertEqual(unit.full_text, "function work() {\n  return\n}")
        self.assertEqual((unit.context_span.start_line, unit.context_span.end_line), (1, 3))

    def test_review_unit_span_rejects_zero_based_and_reversed_lines(self) -> None:
        with self.assertRaisesRegex(ValueError, "start_line"):
            ReviewUnitSpan(start_line=0, end_line=1)
        with self.assertRaisesRegex(ValueError, "end_line"):
            ReviewUnitSpan(start_line=2, end_line=1)

        for lines in ((0,), (2, 1), (1, 1), (True,)):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                ReviewUnitDiagnostic(code="hunk_out_of_range", lines=lines)
        with self.assertRaisesRegex(ValueError, "unsupported ReviewUnit diagnostic"):
            ReviewUnitDiagnostic(code="free_text")  # type: ignore[arg-type]

    def test_identity_serialization_is_unambiguous_and_paths_fail_closed(self) -> None:
        injected_path = declaration_unit_id(
            "src@method:Injected",
            "method",
            "Target",
            1,
            2,
        )
        injected_symbol = declaration_unit_id(
            "src",
            "method",
            "Injected@method:Target",
            1,
            2,
        )

        self.assertNotEqual(injected_path, injected_symbol)
        self.assertIn("src%40method%3AInjected", injected_path)
        self.assertIn("Injected%40method%3ATarget", injected_symbol)
        self.assertEqual(
            normalize_review_path(r"./src\pages//Main.ets"),
            "src/pages/Main.ets",
        )
        for invalid in (
            "",
            ".",
            "./",
            "../Main.ets",
            "src/../../Main.ets",
            "/tmp/Main.ets",
            r"C:\Main.ets",
            "C:Main.ets",
            "src/\x00Main.ets",
        ):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                normalize_review_path(invalid)
        self.assertEqual(normalize_review_path("src/sub/../Main.ets"), "src/Main.ets")

    def test_review_unit_cannot_exist_with_missing_or_drifted_identity(self) -> None:
        with self.assertRaises(TypeError):
            ReviewUnit(
                file="src/work.ets",
                unit_symbol="work",
                unit_ref="work@src/work.ets",
                full_text="",
            )

        source = "function work() {\n  return\n}\n"
        facts = CodeFacts(
            path="src/work.ets",
            declarations=[
                Declaration(
                    kind="function",
                    name="work",
                    qualified_name="work",
                    span=SourceSpan(start_line=1, end_line=3),
                    text="stale",
                )
            ],
        )
        unit = ReviewUnitBuilder().build_full_units("src/work.ets", source, facts)[0]
        unit.unit_ref = "wrong@src/work.ets"
        with self.assertRaisesRegex(ValueError, "unit_ref"):
            unit.validate()

        fallback = ReviewUnitBuilder().build_full_units(
            "src/plain.ets",
            "const value = 1\n",
            CodeFacts(path="src/plain.ets"),
        )[0]
        fallback.unit_symbol = "arbitrary-fallback"
        fallback.unit_ref = "arbitrary-fallback@src/plain.ets"
        with self.assertRaisesRegex(ValueError, "fallback ReviewUnit symbol"):
            fallback.validate()

    def test_hunks_and_builder_inputs_reject_invalid_coordinates(self) -> None:
        for start, lines in ((0, 1), (1, 0), (-1, 1), (1, -1)):
            with self.subTest(start=start, lines=lines), self.assertRaises(ValueError):
                FileHunk(new_start=start, new_lines=lines)

        source = "function work() {\n}\n"
        declaration = Declaration(
            kind="function",
            name="work",
            qualified_name="work",
            span=SourceSpan(start_line=1, end_line=2),
            text=source.rstrip(),
        )
        builder = ReviewUnitBuilder()
        with self.assertRaisesRegex(ValueError, "path must match"):
            builder.build_full_units(
                "src/work.ets",
                source,
                CodeFacts(path="src/other.ets", declarations=[declaration]),
            )
        with self.assertRaisesRegex(ValueError, "invalid source span"):
            builder.build_full_units(
                "src/work.ets",
                source,
                CodeFacts(
                    path="src/work.ets",
                    declarations=[
                        Declaration(
                            kind="function",
                            name="work",
                            qualified_name="work",
                            span=SourceSpan(start_line=1, end_line=3),
                            text=source.rstrip(),
                        )
                    ],
                ),
            )
        with self.assertRaisesRegex(ValueError, "exceeds source line count"):
            builder.build_diff_units(
                "src/work.ets",
                source,
                CodeFacts(path="src/work.ets", declarations=[declaration]),
                [FileHunk(new_start=3, new_lines=1)],
            )
        with self.assertRaisesRegex(ValueError, "empty source"):
            builder.build_full_units(
                "src/empty.ets",
                "",
                CodeFacts(path="src/empty.ets"),
            )

        for kwargs in ({"max_build_lines": 0}, {"fallback_context_lines": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ReviewUnitBuilder(**kwargs)

    def test_builder_uses_context_first_order_for_mixed_units(self) -> None:
        lines = [f"const padding{line}: number = {line}" for line in range(1, 31)]
        lines[7:10] = ["function work() {", "  return", "}"]
        source = "\n".join(lines) + "\n"
        facts = CodeFacts(
            path="src/mixed.ets",
            declarations=[
                Declaration(
                    kind="function",
                    name="work",
                    qualified_name="work",
                    span=SourceSpan(start_line=8, end_line=10),
                    text="stale",
                )
            ],
        )

        units = ReviewUnitBuilder().build_diff_units(
            "src/mixed.ets",
            source,
            facts,
            [FileHunk(new_start=9, new_lines=1), FileHunk(new_start=20, new_lines=1)],
        )

        self.assertEqual([unit.unit_kind for unit in units], ["fallback", "function"])
        self.assertEqual(
            [(unit.context_span.start_line, unit.context_span.end_line) for unit in units],
            [(1, 30), (8, 10)],
        )

    def test_full_mode_deduplicates_identical_occurrences_by_unit_id(self) -> None:
        source = "struct Host {\n}\n"
        declaration = Declaration(
            kind="struct",
            name="Host",
            qualified_name="Host",
            span=SourceSpan(start_line=1, end_line=2),
            text=source.rstrip(),
        )
        facts = CodeFacts(
            path="src/Host.ets",
            declarations=[declaration, copy.deepcopy(declaration)],
        )

        units = ReviewUnitBuilder().build_full_units("src/Host.ets", source, facts)

        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].unit_id, "src/Host.ets@struct:Host:L1-L2")

    def test_manifest_and_baseline_reject_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            text = MANIFEST.read_text(encoding="utf-8")
            path.write_text(
                text.replace(
                    '"schema_version": "review-unit-golden-v1",',
                    '"schema_version": "review-unit-golden-v1",\n'
                    '  "schema_version": "review-unit-golden-v1",',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_golden_suite(path)

        suite = load_golden_suite(MANIFEST)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            text = CURRENT_BASELINE.read_text(encoding="utf-8")
            path.write_text(
                text.replace(
                    '"schema_version": "review-unit-golden-baseline-v1",',
                    '"schema_version": "review-unit-golden-baseline-v1",\n'
                    '  "schema_version": "review-unit-golden-baseline-v1",',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_golden_baseline(path, suite=suite)

    def test_manifest_rejects_duplicate_case_and_unknown_or_missing_fields(self) -> None:
        mutations = {
            "duplicate case": lambda data: data["cases"][1].__setitem__(
                "case_id", data["cases"][0]["case_id"]
            ),
            "unknown field": lambda data: data["cases"][0].__setitem__("typo", True),
            "missing field": lambda data: data["cases"][0]["expected"]["units"][0].pop(
                "unit_kind"
            ),
            "unknown diagnostic": lambda data: data["cases"][7]["expected"]["units"][
                0
            ]["diagnostics"][0].__setitem__("code", "free_text"),
            "duplicate unit id": lambda data: data["cases"][3]["expected"]["units"][
                1
            ].__setitem__(
                "unit_id", data["cases"][3]["expected"]["units"][0]["unit_id"]
            ),
            "missing target phase": lambda data: data["cases"][14].__setitem__(
                "target_phase", "RU-5"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self._load_mutated_manifest(mutate)

    def test_manifest_rejects_invalid_spans_and_changed_lines(self) -> None:
        mutations = {
            "zero based": lambda data: data["cases"][0]["expected"]["units"][0][
                "source_span"
            ].__setitem__("start_line", 0),
            "reversed": lambda data: data["cases"][0]["expected"]["units"][0][
                "context_span"
            ].update({"start_line": 10, "end_line": 8}),
            "past eof": lambda data: data["cases"][0]["expected"]["units"][0][
                "context_span"
            ].__setitem__("end_line", 999),
            "unsorted lines": lambda data: data["cases"][4]["expected"]["units"][0].__setitem__(
                "changed_new_lines", [10, 8]
            ),
            "duplicate lines": lambda data: data["cases"][4]["expected"]["units"][0].__setitem__(
                "changed_new_lines", [8, 8]
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self._load_mutated_manifest(mutate)

    def test_manifest_rejects_source_hash_and_provenance_drift(self) -> None:
        mutations = {
            "source hash": lambda data: data["cases"][0]["source"].__setitem__(
                "content_sha256", "0" * 64
            ),
            "source id": lambda data: data["cases"][0]["source"].__setitem__(
                "source_id", "other"
            ),
            "revision": lambda data: data["cases"][0]["source"].__setitem__(
                "revision", "moving-main"
            ),
            "origin lines": lambda data: data["cases"][0]["source"].__setitem__(
                "origin_lines", [1, 20]
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self._load_mutated_manifest(mutate)

    def test_manifest_rejects_semantically_forged_expected_and_duplicate_cases(self) -> None:
        def forge_owner(data: dict[str, object]) -> None:
            cases = data["cases"]  # type: ignore[index]
            unit = cases[0]["expected"]["units"][0]
            unit["unit_symbol"] = "CorePage.notFrozen"
            unit["unit_id"] = (
                "golden/review_unit/CorePage.ets@method:CorePage.notFrozen:L8-L10"
            )

        def duplicate_semantics(data: dict[str, object]) -> None:
            cases = data["cases"]  # type: ignore[index]
            replacement = copy.deepcopy(cases[0])
            replacement["case_id"] = cases[1]["case_id"]
            replacement["description"] = cases[1]["description"]
            cases[1] = replacement

        mutations = {
            "forged owner": forge_owner,
            "changed line outside hunk": lambda data: data["cases"][0]["expected"][
                "units"
            ][0].__setitem__("changed_new_lines", [8]),
            "synthetic origin offset": lambda data: data["cases"][0]["source"].__setitem__(
                "origin_lines", [100, 120]
            ),
            "unrelated relative path": lambda data: data["cases"][0]["source"].__setitem__(
                "relative_path", "synthetic/unrelated.ets"
            ),
            "parser degradation hidden": lambda data: data["cases"][11]["expected"][
                "units"
            ][0].__setitem__("context_degraded", False),
            "duplicate semantics": duplicate_semantics,
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                self._load_mutated_manifest(mutate)

    def test_evaluator_rechecks_manifest_and_source_after_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied_root = Path(directory) / "review_unit"
            shutil.copytree(GOLDEN_ROOT, copied_root)
            manifest_path = copied_root / "manifest.json"
            suite = load_golden_suite(manifest_path)

            source_path = suite.cases[0].source_path
            source_path.write_text(
                source_path.read_text(encoding="utf-8") + "// drift\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "content_sha256 drift"):
                evaluate_golden_suite(suite)

        with tempfile.TemporaryDirectory() as directory:
            copied_root = Path(directory) / "review_unit"
            shutil.copytree(GOLDEN_ROOT, copied_root)
            manifest_path = copied_root / "manifest.json"
            suite = load_golden_suite(manifest_path)
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "manifest changed after it was loaded"):
                evaluate_golden_suite(suite)

    def test_baseline_rejects_manifest_hash_provenance_and_case_order_drift(self) -> None:
        suite = load_golden_suite(MANIFEST)
        baseline = json.loads(CURRENT_BASELINE.read_text(encoding="utf-8"))
        mutations = {
            "manifest hash": lambda data: data["report"].__setitem__(
                "manifest_sha256", "0" * 64
            ),
            "provenance": lambda data: data["report"]["cases"][0][
                "provenance"
            ].__setitem__("revision", "drift"),
            "case order": lambda data: data["report"]["cases"].reverse(),
            "unknown report field": lambda data: data["report"].__setitem__("partial", True),
            "hidden actual drift": lambda data: data["report"]["cases"][0]["actual"][
                "units"
            ][0].__setitem__("unit_symbol", "drifted"),
            "bool coerced to int": lambda data: data["report"]["cases"][0]["actual"][
                "units"
            ][0].__setitem__("context_degraded", 0),
            "compatibility ref drift": lambda data: data["report"]["cases"][0][
                "legacy_units"
            ][0].__setitem__("unit_ref", "drifted@legacy-ref"),
            "missing reversed evidence": lambda data: data["report"]["cases"][3].__setitem__(
                "reversed_hunks_equal", None
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                malformed = copy.deepcopy(baseline)
                mutate(malformed)
                path = Path(directory) / "baseline.json"
                path.write_text(json.dumps(malformed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_golden_baseline(path, suite=suite)

    def test_cli_refuses_to_overwrite_truth_with_report_or_baseline(self) -> None:
        command = REPO_ROOT / "tools" / "evaluate_review_unit_golden.py"
        original = MANIFEST.read_bytes()
        for flag in ("--json-output", "--write-current-baseline"):
            with self.subTest(flag=flag):
                completed = subprocess.run(
                    [sys.executable, str(command), flag, str(MANIFEST)],
                    cwd=REPO_ROOT,
                    env={"PYTHONPATH": str(REPO_ROOT / "src")},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(MANIFEST.read_bytes(), original)

        with tempfile.TemporaryDirectory() as directory:
            copied_root = Path(directory) / "review_unit"
            shutil.copytree(GOLDEN_ROOT, copied_root)
            copied_manifest = copied_root / "manifest.json"
            protected_outputs = (
                ("--json-output", copied_root / "report.json"),
                ("--json-output", copied_root / "README.md"),
                (
                    "--write-current-baseline",
                    copied_root / "baselines" / "before-ru1.json",
                ),
                ("--write-current-baseline", copied_root / "README.md"),
            )
            for flag, output in protected_outputs:
                with self.subTest(flag=flag, output=output):
                    before = output.read_bytes() if output.is_file() else None
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(command),
                            "--manifest",
                            str(copied_manifest),
                            flag,
                            str(output),
                        ],
                        cwd=REPO_ROOT,
                        env={"PYTHONPATH": str(REPO_ROOT / "src")},
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(
                        output.read_bytes() if output.is_file() else None,
                        before,
                    )

            current_output = copied_root / "baselines" / "current.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(command),
                    "--manifest",
                    str(copied_manifest),
                    "--write-current-baseline",
                    str(current_output),
                ],
                cwd=REPO_ROOT,
                env={"PYTHONPATH": str(REPO_ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            load_golden_baseline(
                current_output,
                suite=load_golden_suite(copied_manifest),
            )

    def _load_mutated_manifest(self, mutate: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied_root = Path(directory) / "review_unit"
            shutil.copytree(GOLDEN_ROOT, copied_root)
            manifest_path = copied_root / "manifest.json"
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            mutate(data)  # type: ignore[operator]
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            load_golden_suite(manifest_path)


if __name__ == "__main__":
    unittest.main()
