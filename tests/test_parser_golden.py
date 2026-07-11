from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import ArktsTreeSitterParser
from arkts_code_reviewer.code_analysis.lexical import LexicalParser
from arkts_code_reviewer.code_analysis.models import CodeFacts
from arkts_code_reviewer.parser_validation.golden import (
    DECLARATION_KINDS,
    EXPECTED_FIELDS,
    SYNTAX_KINDS,
    UNSUPPORTED_FIELDS,
    evaluate_golden_suite,
    load_golden_baseline,
    load_golden_suite,
    score_items,
    verify_external_snapshot_provenance,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden" / "parser"
MANIFEST = GOLDEN_ROOT / "manifest.json"
LEXICAL_BASELINE = GOLDEN_ROOT / "baselines" / "lexical.json"
MERGED_L1_BASELINE = GOLDEN_ROOT / "baselines" / "arkts-tree-sitter-merged.json"
SIDECAR_NODE_MODULE = (
    REPO_ROOT / "sidecars" / "arkts-parser" / "node_modules" / "tree-sitter-arkts" / "package.json"
)
SIDECAR_ROOT = REPO_ROOT / "sidecars" / "arkts-parser"
ENGINE_ROOT = Path(os.getenv("ARKUI_ENGINE_PATH", REPO_ROOT.parent / "arkui_ace_engine"))


class CrashingParser:
    def parse(self, source: str, path: str) -> CodeFacts:
        raise RuntimeError(f"cannot parse {path}")


class ParserGoldenTest(unittest.TestCase):
    def test_manifest_is_self_contained_and_provenanced(self) -> None:
        suite = load_golden_suite(MANIFEST)

        self.assertEqual(suite.suite_id, "parser-golden-v1")
        self.assertEqual(len(suite.cases), 15)
        self.assertEqual(suite.unsupported_fields, UNSUPPORTED_FIELDS)
        self.assertEqual(len({case.case_id for case in suite.cases}), len(suite.cases))
        self.assertGreaterEqual(
            sum(case.source_metadata["kind"] == "external_snapshot" for case in suite.cases),
            4,
        )
        self.assertGreaterEqual(
            sum(case.source_metadata["kind"] == "grammar_derived" for case in suite.cases),
            2,
        )
        for case in suite.cases:
            with self.subTest(case_id=case.case_id):
                self.assertTrue(case.source_path.is_file())
                self.assertTrue(case.logical_path.endswith(".ets"))
                self.assertEqual(set(case.expected), set(EXPECTED_FIELDS))
                if case.source_metadata["kind"] == "external_snapshot":
                    self.assertEqual(case.source_metadata["source_id"], "arkui-ace-engine")
                    self.assertEqual(
                        case.source_metadata["revision"],
                        "39f2c7cc8e25019ce5d0934980b7721614b7eaa2",
                    )

        self.assertEqual(
            {
                declaration["kind"]
                for case in suite.cases
                for declaration in (case.expected["declarations"] or [])
            },
            DECLARATION_KINDS,
        )
        self.assertEqual(
            {
                syntax
                for case in suite.cases
                for syntax in (case.expected["syntax"] or [])
            },
            SYNTAX_KINDS,
        )

    def test_score_items_preserves_duplicate_occurrences(self) -> None:
        score = score_items(
            ["Column", "Column", "Text"],
            ["Column", "Text", "Image"],
        )

        self.assertEqual(score["tp"], 2)
        self.assertEqual(score["fp"], 1)
        self.assertEqual(score["fn"], 1)
        self.assertEqual(score["false_positives"], ["Image"])
        self.assertEqual(score["false_negatives"], ["Column"])

    def test_parser_crashes_are_counted_as_false_negatives(self) -> None:
        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite, CrashingParser())

        self.assertEqual(report["crashed"], len(suite.cases))
        self.assertEqual(report["parser_layers"], {})
        for field in EXPECTED_FIELDS:
            with self.subTest(field=field):
                scored_cases = [case for case in suite.cases if field in case.scored_fields]
                expected_facts = sum(len(case.expected[field] or []) for case in scored_cases)
                self.assertEqual(report["fields"][field]["case_count"], len(scored_cases))
                self.assertEqual(report["fields"][field]["tp"], 0)
                self.assertEqual(report["fields"][field]["fp"], 0)
                self.assertEqual(report["fields"][field]["fn"], expected_facts)

    def test_manifest_schema_rejects_unknown_import_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied_root = Path(directory) / "parser"
            shutil.copytree(GOLDEN_ROOT, copied_root)
            copied_manifest = copied_root / "manifest.json"
            malformed = json.loads(copied_manifest.read_text(encoding="utf-8"))
            malformed["cases"][0]["expected"]["imports"][0]["module_typo"] = "bad"
            copied_manifest.write_text(json.dumps(malformed), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, r"imports\[0\] fields mismatch"):
                load_golden_suite(copied_manifest)

    def test_manifest_and_baseline_reject_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied_manifest = Path(directory) / "manifest.json"
            text = MANIFEST.read_text(encoding="utf-8")
            copied_manifest.write_text(
                text.replace(
                    '"schema_version": "parser-golden-v1",',
                    '"schema_version": "parser-golden-v1",\n'
                    '  "schema_version": "parser-golden-v1",',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_golden_suite(copied_manifest)

        suite = load_golden_suite(MANIFEST)
        with tempfile.TemporaryDirectory() as directory:
            copied_baseline = Path(directory) / "baseline.json"
            text = LEXICAL_BASELINE.read_text(encoding="utf-8")
            copied_baseline.write_text(
                text.replace(
                    '"schema_version": "parser-golden-baseline-v2",',
                    '"schema_version": "parser-golden-baseline-v2",\n'
                    '  "schema_version": "parser-golden-baseline-v2",',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_golden_baseline(
                    copied_baseline,
                    suite=suite,
                    parser_id="lexical",
                )

    def test_manifest_rejects_v1_contract_coverage_drift(self) -> None:
        mutations = {
            "unsupported fields": lambda data: data.__setitem__(
                "unsupported_fields", ["made_up"]
            ),
            "scored field shrink": lambda data: (
                data["cases"][0]["scored_fields"].remove("symbols"),
                data["cases"][0]["expected"].__setitem__("symbols", None),
            ),
            "unknown syntax": lambda data: data["cases"][0]["expected"].__setitem__(
                "syntax", ["not_a_real_syntax_kind"]
            ),
            "component declaration drift": lambda data: data["cases"][0][
                "expected"
            ].__setitem__("components", ["Column"]),
            "symbol declaration drift": lambda data: data["cases"][0][
                "expected"
            ].__setitem__("symbols", ["PhotoWall"]),
            "missing kind and syntax coverage": lambda data: data.__setitem__(
                "cases",
                [
                    case
                    for case in data["cases"]
                    if case["case_id"] != "synthetic-class-promise"
                ],
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                copied_root = Path(directory) / "parser"
                shutil.copytree(GOLDEN_ROOT, copied_root)
                copied_manifest = copied_root / "manifest.json"
                malformed = json.loads(copied_manifest.read_text(encoding="utf-8"))
                mutate(malformed)
                copied_manifest.write_text(json.dumps(malformed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_golden_suite(copied_manifest)

    def test_lexical_parser_matches_reviewed_baseline(self) -> None:
        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite, LexicalParser())
        baseline = load_golden_baseline(
            LEXICAL_BASELINE,
            suite=suite,
            parser_id="lexical",
        )

        self.maxDiff = None
        self.assertEqual(report, baseline["report"])

    def test_baseline_schema_rejects_partial_aggregate_fields(self) -> None:
        suite = load_golden_suite(MANIFEST)
        baseline = json.loads(LEXICAL_BASELINE.read_text(encoding="utf-8"))
        malformed = copy.deepcopy(baseline)
        del malformed["report"]["fields"]["syntax"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial-baseline.json"
            path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fields mismatch"):
                load_golden_baseline(path, suite=suite, parser_id="lexical")

    def test_baseline_schema_rejects_aggregate_that_hides_case_drift(self) -> None:
        suite = load_golden_suite(MANIFEST)
        baseline = json.loads(LEXICAL_BASELINE.read_text(encoding="utf-8"))
        malformed = copy.deepcopy(baseline)
        first_case = malformed["report"]["cases"][0]
        first_case["field_scores"]["imports"]["false_positives"].append("shifted")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case-drift-baseline.json"
            path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "false_positives length must equal fp"):
                load_golden_baseline(path, suite=suite, parser_id="lexical")

    def test_strict_l1_baseline_rejects_missing_dependencies(self) -> None:
        suite = load_golden_suite(MANIFEST)
        with tempfile.TemporaryDirectory() as directory:
            sidecar_root = Path(directory)
            for file_name in (".node-version", "package.json", "package-lock.json"):
                shutil.copy2(SIDECAR_ROOT / file_name, sidecar_root / file_name)

            with self.assertRaisesRegex(ValueError, "run npm ci"):
                load_golden_baseline(
                    MERGED_L1_BASELINE,
                    suite=suite,
                    parser_id="arkts-tree-sitter-merged",
                    sidecar_root=sidecar_root,
                )

    @unittest.skipUnless(
        ENGINE_ROOT.is_dir(),
        "pinned arkui_ace_engine checkout is unavailable",
    )
    def test_external_snapshots_match_pinned_upstream_sources(self) -> None:
        suite = load_golden_suite(MANIFEST)

        verified = verify_external_snapshot_provenance(suite, ENGINE_ROOT)

        self.assertEqual(len(verified), 4)
        self.assertEqual(
            {item["case_id"] for item in verified},
            {
                "arkui-animate-to",
                "arkui-download-file-button",
                "arkui-image-generator-complex-receivers",
                "arkui-v2-decorated-variables",
            },
        )

    @unittest.skipUnless(
        SIDECAR_NODE_MODULE.exists(),
        "ArkTS tree-sitter sidecar dependencies are not installed",
    )
    def test_merged_l1_parser_matches_reviewed_baseline(self) -> None:
        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite, ArktsTreeSitterParser())
        baseline = load_golden_baseline(
            MERGED_L1_BASELINE,
            suite=suite,
            parser_id="arkts-tree-sitter-merged",
            sidecar_root=SIDECAR_ROOT,
        )

        self.maxDiff = None
        self.assertEqual(report, baseline["report"])


if __name__ == "__main__":
    unittest.main()
