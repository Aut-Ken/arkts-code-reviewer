from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from arkts_code_reviewer.code_analysis.models import CodeFacts
from arkts_code_reviewer.parser_validation.candidates import (
    DEFAULT_GROUP_CONTRACTS,
    DEFAULT_REVIEWED_GROUPS,
    CandidateGroupContract,
    audit_candidate_evidence,
    evaluate_candidate_suite,
    load_candidate_suite,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_CANDIDATE_DIR = REPO_ROOT / "tests" / "Grok_Expected"
REAL_ENGINE_ROOT = Path(
    os.getenv("ARKUI_ENGINE_PATH", str(REPO_ROOT.parent / "arkui_ace_engine"))
)


class FixedParser:
    def parse(self, source: str, path: str) -> CodeFacts:
        return CodeFacts(
            path=path,
            components={"Text"},
            apis={"forbidden.call"},
            parser_layer="L0",
        )


class CandidateFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.source_root = root / "source"
        self.candidate_dir = root / "candidates"
        self.source_path = self.source_root / "sample.ets"
        self.source_root.mkdir()
        self.candidate_dir.mkdir()
        self.source_path.write_text("Text('hello')\n", encoding="utf-8")
        self._git("init", "-q")
        self._git("config", "user.email", "tests@example.invalid")
        self._git("config", "user.name", "Parser Candidate Tests")
        self._git("add", "sample.ets")
        self._git("commit", "-qm", "fixture")
        self.revision = self._git("rev-parse", "HEAD")
        self.source_hash = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        self.manifest_path = root / "manifest.json"
        self.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "parser-corpus-v1",
                    "suite_id": "arkui-ace-engine-r63",
                    "suite_role": "robustness_performance",
                    "engine": "arkui_ace_engine",
                    "source_id": "arkui-ace-engine",
                    "revision": self.revision,
                    "description": "temporary candidate evaluator fixture",
                    "samples": [{"category": "fixture", "path": "sample.ets"}],
                }
            ),
            encoding="utf-8",
        )
        self.contracts = {
            "B001": CandidateGroupContract("a" * 64, ("R63-001",)),
            "B002": CandidateGroupContract("b" * 64, ("R63-001",)),
        }
        self.data = self._candidate_data()
        self.write()

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.source_root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def _candidate_data(self) -> dict[str, object]:
        review = {
            "coverage": "complete",
            "confidence": "high",
            "evidence": [],
            "excluded": [],
            "uncertainties": [],
        }
        return {
            "schema_version": "parser-golden-candidate-v1",
            "target_schema_version": "parser-golden-v1",
            "truth_status": "candidate_unreviewed",
            "prompt_version": "r63-grok-annotation-v1",
            "corpus": {
                "suite_id": "arkui-ace-engine-r63",
                "source_id": "arkui-ace-engine",
                "revision": self.revision,
            },
            "group": {
                "group_id": "B001",
                "group_manifest_sha256": "a" * 64,
                "expected_case_ids": ["R63-001"],
            },
            "annotator": {"provider": "xai", "model": "fixture", "run_id": "run-1"},
            "independence_attestation": {
                "only_allowlisted_inputs_read": True,
                "parser_source_read": False,
                "parser_output_read": False,
                "baseline_read": False,
                "prior_expected_read": False,
                "parser_executed": False,
            },
            "cases": [
                {
                    "case_id": "R63-001",
                    "category": "fixture",
                    "logical_path": "sample.ets",
                    "source": {
                        "source_id": "arkui-ace-engine",
                        "revision": self.revision,
                        "relative_path": "sample.ets",
                        "copied_path": "sources/sample.ets",
                        "content_sha256": self.source_hash,
                        "line_count": 1,
                    },
                    "annotation_status": "complete",
                    "adjudication_status": "unreviewed",
                    "proposed_scored_fields": ["components"],
                    "candidate_expected": {
                        "imports": None,
                        "components": ["Text"],
                        "apis": None,
                        "decorators": None,
                        "attributes": None,
                        "symbols": None,
                        "syntax": None,
                        "declarations": None,
                    },
                    "candidate_must_not_emit": {
                        "components": [],
                        "apis": ["forbidden.call"],
                        "decorators": [],
                        "attributes": [],
                        "symbols": [],
                        "syntax": [],
                    },
                    "field_reviews": {
                        field: copy.deepcopy(review)
                        for field in (
                            "imports",
                            "components",
                            "apis",
                            "decorators",
                            "attributes",
                            "symbols",
                            "syntax",
                            "declarations",
                        )
                    },
                    "self_checks": {
                        "source_fully_read": True,
                        "comments_and_strings_excluded": True,
                        "components_equal_ui_block_names": True,
                        "symbols_equal_declaration_names": True,
                        "declaration_parents_and_spans_checked": True,
                        "lists_sorted_unique": True,
                    },
                }
            ],
        }

    def write(self, data: dict[str, object] | None = None) -> None:
        self.data = data or self.data
        (self.candidate_dir / "B001.candidate.json").write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load(self, **kwargs: object):
        return load_candidate_suite(
            self.candidate_dir,
            groups=("B001",),
            source_root=self.source_root,
            corpus_manifest_path=self.manifest_path,
            group_contracts=self.contracts,
            **kwargs,
        )


class ParserCandidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = CandidateFixture(Path(self.temporary.name))

    def test_default_reviewed_groups_cover_23_cases(self) -> None:
        self.assertEqual(len(DEFAULT_REVIEWED_GROUPS), 8)
        self.assertEqual(
            sum(len(DEFAULT_GROUP_CONTRACTS[group].case_ids) for group in DEFAULT_REVIEWED_GROUPS),
            23,
        )
        self.assertTrue({"B007", "B009"}.isdisjoint(DEFAULT_REVIEWED_GROUPS))
        self.assertTrue({"B007", "B009"}.issubset(DEFAULT_GROUP_CONTRACTS))

    def test_null_expected_field_is_unscored_but_must_not_is_enforced(self) -> None:
        suite = self.fixture.load()
        report = evaluate_candidate_suite(suite, FixedParser())

        self.assertEqual(report["truth_status"], "candidate_unreviewed")
        self.assertEqual(report["evaluation_status"], "provisional")
        self.assertEqual(report["fields"]["components"]["f1"], 1.0)
        self.assertEqual(report["fields"]["apis"]["case_count"], 0)
        self.assertEqual(report["must_not_violation_count"], 1)
        self.assertEqual(
            report["cases"][0]["must_not_violations"], {"apis": ["forbidden.call"]}
        )

    def test_candidate_evidence_audit_fails_closed_and_can_pass(self) -> None:
        suite = self.fixture.load()
        report = audit_candidate_evidence(suite, self.fixture.candidate_dir)

        self.assertEqual(report["truth_status"], "candidate_unreviewed")
        self.assertEqual(report["issue_count"], 2)
        self.assertEqual(
            {issue["code"] for issue in report["issues"]},
            {"missing_expected_evidence", "missing_must_not_excluded_evidence"},
        )

        case = self.fixture.data["cases"][0]  # type: ignore[index]
        case["field_reviews"]["components"]["evidence"] = [  # type: ignore[index]
            {
                "value": "Text",
                "line_ranges": [{"start_line": 1, "end_line": 1}],
                "reason": "declarative UI component call",
            }
        ]
        case["field_reviews"]["apis"]["excluded"] = [  # type: ignore[index]
            {
                "value": "forbidden.call",
                "line_ranges": [{"start_line": 1, "end_line": 1}],
                "reason": "frozen API contract exclusion",
            }
        ]
        self.fixture.write()

        passing_suite = self.fixture.load()
        passing = audit_candidate_evidence(passing_suite, self.fixture.candidate_dir)
        self.assertEqual(passing["issue_count"], 0)

    def test_suite_fingerprint_ignores_json_format_and_annotator_run(self) -> None:
        first_suite = self.fixture.load()
        self.fixture.data["annotator"]["run_id"] = "run-2"  # type: ignore[index]
        self.fixture.write()
        second_suite = self.fixture.load()

        self.assertEqual(first_suite.suite_fingerprint, second_suite.suite_fingerprint)
        self.assertNotEqual(
            first_suite.annotation_fingerprint,
            second_suite.annotation_fingerprint,
        )

    def test_suite_fingerprint_changes_with_scored_truth(self) -> None:
        first = self.fixture.load().suite_fingerprint
        case = self.fixture.data["cases"][0]  # type: ignore[index]
        case["candidate_expected"]["components"] = ["Image"]  # type: ignore[index]
        self.fixture.write()

        self.assertNotEqual(first, self.fixture.load().suite_fingerprint)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        path = self.fixture.candidate_dir / "B001.candidate.json"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                '"schema_version": "parser-golden-candidate-v1",',
                '"schema_version": "parser-golden-candidate-v1",\n'
                '  "schema_version": "parser-golden-candidate-v1",',
                1,
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            self.fixture.load()

    def test_wrong_schema_status_group_case_and_path_are_rejected(self) -> None:
        mutations = {
            "schema_version": lambda data: data.__setitem__("schema_version", "wrong"),
            "truth_status": lambda data: data.__setitem__("truth_status", "approved"),
            "group": lambda data: data["group"].__setitem__("group_id", "B999"),
            "case_id": lambda data: data["cases"][0].__setitem__("case_id", "R63-999"),
            "logical_path": lambda data: data["cases"][0].__setitem__(
                "logical_path", "../sample.ets"
            ),
            "incomplete_annotation": lambda data: data["cases"][0].__setitem__(
                "annotation_status", "needs_review"
            ),
            "failed_attestation": lambda data: data["independence_attestation"].__setitem__(
                "parser_executed", True
            ),
            "incomplete_scored_review": lambda data: data["cases"][0][
                "field_reviews"
            ]["components"].__setitem__("coverage", "partial"),
            "unknown_syntax": lambda data: (
                data["cases"][0].__setitem__(
                    "proposed_scored_fields", ["components", "syntax"]
                ),
                data["cases"][0]["candidate_expected"].__setitem__(
                    "syntax", ["not_a_real_syntax_kind"]
                ),
            ),
            "declaration_projection_drift": lambda data: (
                data["cases"][0].__setitem__(
                    "proposed_scored_fields",
                    ["components", "symbols", "declarations"],
                ),
                data["cases"][0]["candidate_expected"].__setitem__(
                    "symbols", ["Wrong"]
                ),
                data["cases"][0]["candidate_expected"].__setitem__(
                    "declarations",
                    [
                        {
                            "kind": "ui_block",
                            "name": "Text",
                            "qualified_name": "Text",
                            "parent_name": None,
                            "span": {"start_line": 1, "end_line": 1},
                        }
                    ],
                ),
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                data = self.fixture._candidate_data()
                mutate(data)
                self.fixture.write(data)
                with self.assertRaises(ValueError):
                    self.fixture.load()

    def test_source_hash_and_line_count_are_verified(self) -> None:
        for field, value, message in (
            ("content_sha256", "0" * 64, "hash mismatch"),
            ("line_count", 2, "line_count mismatch"),
        ):
            with self.subTest(field=field):
                data = self.fixture._candidate_data()
                data["cases"][0]["source"][field] = value  # type: ignore[index]
                self.fixture.write(data)
                with self.assertRaisesRegex(ValueError, message):
                    self.fixture.load()

    def test_only_selected_candidate_shards_are_opened(self) -> None:
        (self.fixture.candidate_dir / "B002.candidate.json").write_text(
            "not JSON", encoding="utf-8"
        )

        suite = self.fixture.load()

        self.assertEqual(suite.groups, ("B001",))
        self.assertEqual(len(suite.cases), 1)

    def test_registry_resolves_source_root_and_checks_revision(self) -> None:
        registry = Path(self.temporary.name) / "sources.yaml"
        registry.write_text(
            "sources:\n"
            "  - id: arkui-ace-engine\n"
            f"    local_path: {self.fixture.source_root}\n"
            f"    revision: {self.fixture.revision}\n",
            encoding="utf-8",
        )

        suite = load_candidate_suite(
            self.fixture.candidate_dir,
            groups=("B001",),
            registry_path=registry,
            corpus_manifest_path=self.fixture.manifest_path,
            group_contracts=self.fixture.contracts,
        )

        self.assertEqual(suite.source_root, self.fixture.source_root.resolve())
        self.assertEqual(
            suite.golden_suite.unsupported_fields,
            (
                "fact_occurrences",
                "fact_spans",
                "fact_owners",
                "parser_diagnostics",
                "raw_l1_snapshot",
            ),
        )

    def test_explicit_source_root_takes_priority_over_registry(self) -> None:
        suite = load_candidate_suite(
            self.fixture.candidate_dir,
            groups=("B001",),
            source_root=self.fixture.source_root,
            registry_path=Path(self.temporary.name) / "missing.yaml",
            corpus_manifest_path=self.fixture.manifest_path,
            group_contracts=self.fixture.contracts,
        )

        self.assertEqual(len(suite.cases), 1)

    def test_checkout_revision_is_verified(self) -> None:
        unrelated = self.fixture.source_root / "unrelated.txt"
        unrelated.write_text("new commit\n", encoding="utf-8")
        self.fixture._git("add", "unrelated.txt")
        self.fixture._git("commit", "-qm", "move head")

        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            self.fixture.load()

    def test_cli_exposes_provisional_inputs_without_baseline_option(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "evaluate_parser_candidates.py"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("--candidate-dir", completed.stdout)
        self.assertIn("--source-root", completed.stdout)
        self.assertIn("--registry", completed.stdout)
        self.assertIn("--require-layer", completed.stdout)
        self.assertNotIn("--baseline", completed.stdout)

    @unittest.skipUnless(
        (REAL_CANDIDATE_DIR / "B010.candidate.json").is_file()
        and REAL_ENGINE_ROOT.is_dir(),
        "reviewed candidate shards or pinned arkui_ace_engine checkout are unavailable",
    )
    def test_reviewed_real_candidate_subset_is_loadable(self) -> None:
        suite = load_candidate_suite(
            REAL_CANDIDATE_DIR,
            source_root=REAL_ENGINE_ROOT,
        )

        self.assertEqual(suite.groups, DEFAULT_REVIEWED_GROUPS)
        self.assertEqual(len(suite.cases), 23)
        self.assertEqual(
            sum(int(case.source_metadata["line_count"]) for case in suite.cases),
            7_622,
        )


if __name__ == "__main__":
    unittest.main()
