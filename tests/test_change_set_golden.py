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

from arkts_code_reviewer.change_set_validation.golden import (
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden" / "change_set"
MANIFEST = GOLDEN_ROOT / "manifest.json"
BASELINE = GOLDEN_ROOT / "baselines" / "current.json"


class ChangeSetGoldenTest(unittest.TestCase):
    def test_loads_fourteen_human_reviewed_cases(self) -> None:
        suite = load_golden_suite(MANIFEST)

        self.assertEqual(len(suite.cases), 14)
        self.assertEqual(
            [case.case_id for case in suite.cases],
            sorted(case.case_id for case in suite.cases),
        )
        self.assertEqual(
            {item.status for case in suite.cases for item in case.files},
            {"added", "modified", "deleted", "renamed"},
        )
        self.assertEqual(
            {
                atom.kind
                for case in suite.cases
                for changed_file in case.files
                for atom in changed_file.atoms
            },
            {"addition", "deletion", "replacement"},
        )

    def test_expected_freezes_every_public_id_and_output_sequence(self) -> None:
        suite = load_golden_suite(MANIFEST)

        for case in suite.cases:
            with self.subTest(case=case.case_id):
                expected = case.expected
                self.assertTrue(expected["change_set_id"].startswith("change-set:sha256:"))
                for source in expected["source_refs"]:
                    self.assertTrue(source["source_ref_id"].startswith("code-source:sha256:"))
                for changed_file in expected["files"]:
                    self.assertTrue(
                        changed_file["changed_file_id"].startswith("changed-file:sha256:")
                    )
                for atom in expected["atoms"]:
                    self.assertTrue(atom["atom_id"].startswith("change-atom:sha256:"))
                for diagnostic in expected["diagnostics"]:
                    self.assertTrue(
                        diagnostic["diagnostic_id"].startswith("change-diagnostic:sha256:")
                    )

    def test_unicode_diff_positions_and_binary_semantics_are_explicit(self) -> None:
        suite = load_golden_suite(MANIFEST)
        unicode_case = next(case for case in suite.cases if case.case_id.startswith("CS011"))
        positioned = next(case for case in suite.cases if case.case_id.startswith("CS012"))
        binary = next(case for case in suite.cases if case.case_id.startswith("CS010"))

        unicode_atom = unicode_case.expected["atoms"][0]
        self.assertEqual(unicode_atom["old_span"]["end_offset_utf16"], 29)
        self.assertEqual(unicode_atom["new_span"]["end_offset_utf16"], 29)
        self.assertEqual(
            positioned.expected["atoms"][0]["diff_positions"],
            [
                {"side": "base", "source_line": 2, "diff_position": 5},
                {"side": "head", "source_line": 2, "diff_position": 6},
            ],
        )
        self.assertEqual(binary.expected["source_refs"], [])
        self.assertEqual(binary.expected["atoms"], [])
        self.assertEqual(
            binary.expected["diagnostics"][0]["code"],
            "binary_source_unavailable",
        )

    def test_evaluator_is_perfect_deterministic_and_strict(self) -> None:
        suite = load_golden_suite(MANIFEST)
        first = evaluate_golden_suite(suite)
        second = evaluate_golden_suite(suite)

        self.assertEqual(first, second)
        self.assertTrue(is_perfect(first))
        self.assertEqual(first["matched_case_count"], 14)
        for case in first["cases"]:
            self.assertEqual(case["invariant_violations"], [])
        assert_strict_baseline(first, suite, BASELINE)

    def test_loader_rejects_duplicate_json_keys(self) -> None:
        with self._copy_golden() as root:
            manifest = root / "manifest.json"
            text = manifest.read_text(encoding="utf-8")
            text = text.replace(
                '  "suite_id": "change-set-golden-v1",',
                '  "suite_id": "change-set-golden-v1",\n  "suite_id": "duplicate",',
                1,
            )
            manifest.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_golden_suite(manifest)

    def test_loader_rejects_case_alias_and_schema_drift(self) -> None:
        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "duplicate case": lambda data: data["cases"].append(copy.deepcopy(data["cases"][0])),
            "duplicate source alias": lambda data: data["cases"][2]["sources"][1].__setitem__(
                "alias", "base"
            ),
            "unknown field": lambda data: data["cases"][0].__setitem__("unknown", True),
            "missing field": lambda data: data["cases"][0]["files"][0].pop("is_binary"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self._copy_golden() as root:
                self._mutate_manifest(root, mutate)
                with self.assertRaises(ValueError):
                    load_golden_suite(root / "manifest.json")

    def test_loader_rejects_source_hash_and_provenance_drift(self) -> None:
        with self._copy_golden() as root:
            source = root / "sources" / "cs001_added.ets"
            source.write_text(source.read_text(encoding="utf-8") + "// drift\n")
            with self.assertRaisesRegex(ValueError, "source hash drift"):
                load_golden_suite(root / "manifest.json")

        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "source role": lambda data: data["cases"][0]["sources"][0].__setitem__("role", "base"),
            "source path": lambda data: data["cases"][0]["sources"][0].__setitem__(
                "relative_path", "src/Other.ets"
            ),
            "origin lines": lambda data: data["cases"][0]["sources"][0].__setitem__(
                "origin_lines", [1, 4]
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self._copy_golden() as root:
                self._mutate_manifest(root, mutate)
                with self.assertRaises(ValueError):
                    load_golden_suite(root / "manifest.json")

    def test_loader_rejects_invalid_spans_lines_and_positions(self) -> None:
        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "zero based span": lambda data: data["cases"][0]["files"][0]["atoms"][0][
                "new_span"
            ].__setitem__("start_line", 0),
            "out of range span": lambda data: data["cases"][0]["files"][0]["atoms"][0][
                "new_span"
            ].__setitem__("end_line", 6),
            "unsorted lines": lambda data: data["cases"][0]["files"][0]["atoms"][0].__setitem__(
                "added_new_lines", [2, 1, 3, 4, 5]
            ),
            "duplicate lines": lambda data: data["cases"][0]["files"][0]["atoms"][0].__setitem__(
                "added_new_lines", [1, 1, 2, 3, 4, 5]
            ),
            "unbound diff position": lambda data: data["cases"][11]["files"][0]["atoms"][0][
                "diff_positions"
            ][0].__setitem__("source_line", 1),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self._copy_golden() as root:
                self._mutate_manifest(root, mutate)
                with self.assertRaises(ValueError):
                    load_golden_suite(root / "manifest.json")

    def test_loader_rejects_expected_id_order_and_graph_drift(self) -> None:
        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "change set id": lambda data: data["cases"][0]["expected"].__setitem__(
                "change_set_id", "change-set:sha256:" + "0" * 64
            ),
            "atom id": lambda data: data["cases"][0]["expected"]["atoms"][0].__setitem__(
                "atom_id", "change-atom:sha256:" + "0" * 64
            ),
            "dangling atom": lambda data: data["cases"][0]["expected"]["files"][0][
                "atom_ids"
            ].__setitem__(0, "change-atom:sha256:" + "0" * 64),
            "source order": lambda data: data["cases"][2]["expected"]["source_refs"].reverse(),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self._copy_golden() as root:
                self._mutate_manifest(root, mutate)
                with self.assertRaises(ValueError):
                    load_golden_suite(root / "manifest.json")

    def test_loader_rejects_manifest_and_source_symlinks(self) -> None:
        with self._copy_golden() as root:
            source = root / "sources" / "cs001_added.ets"
            target = root.parent / "outside-source.ets"
            target.write_bytes(source.read_bytes())
            source.unlink()
            source.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                load_golden_suite(root / "manifest.json")

        with self._copy_golden() as root:
            manifest = root / "manifest.json"
            target = root.parent / "outside-manifest.json"
            target.write_bytes(manifest.read_bytes())
            manifest.unlink()
            manifest.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                load_golden_suite(manifest)

    def test_baseline_is_separate_and_fail_closed(self) -> None:
        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite)
        for path in (MANIFEST, GOLDEN_ROOT / "baselines" / "history.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "only update"):
                write_current_baseline(report, suite, path)

        with self._copy_golden() as root:
            baseline = root / "baselines" / "current.json"
            target = root.parent / "outside-baseline.json"
            target.write_bytes(baseline.read_bytes())
            baseline.unlink()
            baseline.symlink_to(target)
            copied_suite = load_golden_suite(root / "manifest.json")
            with self.assertRaisesRegex(ValueError, "symlink"):
                assert_strict_baseline(report, copied_suite, baseline)

    @contextmanager
    def _copy_golden(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "change_set"
            shutil.copytree(GOLDEN_ROOT, root)
            yield root

    @staticmethod
    def _mutate_manifest(root: Path, mutate: Callable[[dict[str, Any]], object]) -> None:
        manifest = root / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        mutate(data)
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
