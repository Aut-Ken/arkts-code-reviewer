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

from arkts_code_reviewer.context_validation.golden import (
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden" / "context_plan"
MANIFEST = GOLDEN_ROOT / "manifest.json"
BASELINE = GOLDEN_ROOT / "baselines" / "current.json"


class ContextPlanGoldenTest(unittest.TestCase):
    def test_loads_the_frozen_cp001_to_cp016_matrix(self) -> None:
        suite = load_golden_suite(MANIFEST)

        self.assertEqual(
            [case.case_id for case in suite.cases],
            [f"CP{index:03d}" for index in range(1, 17)],
        )
        self.assertEqual(len(suite.cases), 16)
        self.assertTrue(all(case.primaries for case in suite.cases))
        self.assertTrue(
            all(
                source.snapshot.source_ref.source_ref_id.startswith("code-source:sha256:")
                for case in suite.cases
                for source in case.sources
            )
        )

    def test_matrix_freezes_groups_budget_roles_questions_and_omissions(self) -> None:
        cases = {case.case_id: case.expected for case in load_golden_suite(MANIFEST).cases}

        self.assertEqual(len(cases["CP001"]["change_groups"]), 1)
        cp001 = next(case for case in load_golden_suite(MANIFEST).cases if case.case_id == "CP001")
        self.assertEqual(cp001.primaries[0].unit.unit_kind, "fallback")
        cp002 = next(case for case in load_golden_suite(MANIFEST).cases if case.case_id == "CP002")
        self.assertIn("field_region", {item.unit.unit_kind for item in cp002.primaries})
        self.assertEqual(len(cases["CP002"]["change_groups"]), 2)
        self.assertEqual(len(cases["CP003"]["change_groups"]), 1)
        self.assertEqual(len(cases["CP003"]["change_groups"][0]["primary_unit_ids"]), 2)
        self.assertEqual(len(cases["CP004"]["change_groups"]), 2)
        self.assertEqual(
            cases["CP006"]["omitted_candidates"][0]["reason"], "budget_exceeded"
        )
        self.assertTrue(all(item["dispatch_allowed"] for item in cases["CP006"]["bundles"]))
        self.assertEqual(
            cases["CP007"]["omitted_candidates"][0]["reason"], "distractor_rejected"
        )
        self.assertEqual(
            cases["CP008"]["bundles"][0]["budget"]["total_tokens"],
            cases["CP008"]["bundles"][0]["budget"]["limit"],
        )
        self.assertFalse(cases["CP009"]["bundles"][0]["dispatch_allowed"])
        self.assertFalse(cases["CP010"]["bundles"][0]["dispatch_allowed"])
        self.assertEqual(len(cases["CP011"]["primary_question_bindings"]), 2)
        cp011 = next(case for case in load_golden_suite(MANIFEST).cases if case.case_id == "CP011")
        self.assertEqual(len(cp011.primaries[0].unit.change_atom_ids), 2)

        cp012_roles = {
            primary.unit.source_role
            for primary in next(
                case for case in load_golden_suite(MANIFEST).cases if case.case_id == "CP012"
            ).primaries
        }
        self.assertEqual(cp012_roles, {"base", "head"})
        cp012 = next(
            case for case in load_golden_suite(MANIFEST).cases if case.case_id == "CP012"
        )
        self.assertEqual(len(cp012.edges), 0)
        self.assertEqual(len(cases["CP012"]["change_groups"]), 1)
        self.assertEqual(len(cases["CP012"]["bundles"]), 1)
        correspondence = cases["CP012"]["relation_edges"][0]
        self.assertEqual(correspondence["relation_type"], "change_correspondence")
        shared_atom_ids = set(cp012.primaries[0].unit.change_atom_ids).intersection(
            cp012.primaries[1].unit.change_atom_ids
        )
        self.assertEqual(correspondence["evidence_refs"], sorted(shared_atom_ids))
        self.assertEqual(
            cases["CP012"]["change_groups"][0]["strong_edge_ids"],
            [correspondence["edge_id"]],
        )
        self.assertEqual(len(cases["CP013"]["change_groups"]), 2)
        self.assertEqual(
            cases["CP015"]["omitted_candidates"][0]["reason"],
            "relation_degraded",
        )
        self.assertGreaterEqual(len(cases["CP016"]["bundles"]), 2)
        cp016_required = next(
            item
            for item in cases["CP016"]["supporting_segments"]
            if item["selection_reason"] == "required_context"
        )
        self.assertEqual(
            sum(
                cp016_required["segment_id"] in item["supporting_segment_ids"]
                for item in cases["CP016"]["bundles"]
            ),
            2,
        )
        for bundle in cases["CP011"]["bundles"]:
            self.assertEqual(
                len(
                    {
                        item["review_question_id"]
                        for item in bundle["primary_question_bindings"]
                    }
                ),
                1,
            )

    def test_every_dispatchable_bundle_respects_its_real_code_budget(self) -> None:
        suite = load_golden_suite(MANIFEST)

        for case in suite.cases:
            for bundle in case.expected["bundles"]:
                with self.subTest(case=case.case_id, bundle=bundle["bundle_id"]):
                    budget = bundle["budget"]
                    self.assertEqual(
                        budget["total_tokens"],
                        budget["primary_tokens"] + budget["supporting_tokens"],
                    )
                    if bundle["dispatch_allowed"]:
                        self.assertLessEqual(budget["total_tokens"], budget["limit"])

    def test_evaluator_is_perfect_repeatable_permutation_safe_and_strict(self) -> None:
        suite = load_golden_suite(MANIFEST)
        first = evaluate_golden_suite(suite)
        second = evaluate_golden_suite(suite)

        self.assertEqual(first, second)
        self.assertTrue(is_perfect(first, suite))
        self.assertEqual(first["matched_case_count"], 16)
        self.assertTrue(all(item["repeat_equal"] for item in first["cases"]))
        self.assertTrue(all(item["permutation_equal"] for item in first["cases"]))
        self.assertEqual(first["metrics"]["primary_coverage"], 1.0)
        self.assertEqual(first["metrics"]["relation_precision"], 1.0)
        self.assertEqual(first["metrics"]["relation_recall"], 1.0)
        self.assertEqual(first["metrics"]["required_context_recall_at_budget"], 1.0)
        self.assertEqual(first["metrics"]["distractor_rejection"], 1.0)
        self.assertGreater(first["metrics"]["required_context_insufficient_count"], 0)
        self.assertLessEqual(first["metrics"]["budget_utilization"], 1.0)
        self.assertEqual(first["metrics"]["input_order_stability"], 1.0)
        assert_strict_baseline(first, suite, BASELINE)

    def test_perfect_gate_rejects_forged_counts_without_case_rows(self) -> None:
        suite = load_golden_suite(MANIFEST)
        self.assertFalse(
            is_perfect(
                {
                    "schema_version": "context-plan-golden-report-v1",
                    "case_count": 16,
                    "matched_case_count": 16,
                    "mismatched_case_count": 0,
                    "cases": [],
                },
                suite,
            )
        )
        forged_payloads = evaluate_golden_suite(suite)
        for row in forged_payloads["cases"]:
            row["expected"] = {"schema_version": "context-plan-v1"}
            row["actual"] = {"schema_version": "context-plan-v1"}
        self.assertFalse(is_perfect(forged_payloads, suite))

        negative_metric = evaluate_golden_suite(suite)
        negative_metric["metrics"]["required_context_insufficient_count"] = -1.0
        self.assertFalse(is_perfect(negative_metric, suite))

        forged_counts = evaluate_golden_suite(suite)
        forged_counts["cases"][0]["metric_counts"]["required_insufficient"] += 1
        forged_counts["metrics"]["required_context_insufficient_count"] += 1.0
        self.assertFalse(is_perfect(forged_counts, suite))

        forged_provenance = evaluate_golden_suite(suite)
        forged_provenance["cases"][0]["source_provenance"][0][
            "content_sha256"
        ] = "0" * 64
        self.assertFalse(is_perfect(forged_provenance, suite))
        forged_rows = [
            {"case_id": f"CP{index:03d}", "matched": True}
            for index in range(1, 17)
        ]
        self.assertFalse(
            is_perfect(
                {
                    "schema_version": "context-plan-golden-report-v1",
                    "suite_id": "context-plan-ru5",
                    "implementation": "ContextPlanner.plan",
                    "manifest_sha256": "0" * 64,
                    "case_count": 16,
                    "matched_case_count": 16,
                    "mismatched_case_count": 0,
                    "metrics": {},
                    "cases": forged_rows,
                },
                suite,
            )
        )

    def test_loader_rejects_duplicate_json_keys(self) -> None:
        with self._copy_golden() as root:
            manifest = root / "manifest.json"
            text = manifest.read_text(encoding="utf-8").replace(
                '  "suite_id": "context-plan-ru5",',
                '  "suite_id": "context-plan-ru5",\n  "suite_id": "duplicate",',
                1,
            )
            manifest.write_text(text, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_golden_suite(manifest)

    def test_loader_rejects_unknown_missing_duplicate_case_and_alias_fields(self) -> None:
        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "unknown field": lambda data: data["cases"][0].__setitem__("unknown", True),
            "missing field": lambda data: data["cases"][0]["expected"].pop(
                "budget_summary"
            ),
            "duplicate case": lambda data: data["cases"].__setitem__(
                1, copy.deepcopy(data["cases"][0])
            ),
            "duplicate source alias": lambda data: data["cases"][1]["sources"][1].__setitem__(
                "alias", data["cases"][1]["sources"][0]["alias"]
            ),
            "duplicate primary alias": lambda data: data["cases"][2]["primaries"][1].__setitem__(
                "alias", data["cases"][2]["primaries"][0]["alias"]
            ),
            "duplicate candidate alias": lambda data: data["cases"][5]["candidates"][
                1
            ].__setitem__("alias", data["cases"][5]["candidates"][0]["alias"]),
            "duplicate edge alias": lambda data: data["cases"][5]["relation_edges"][
                1
            ].__setitem__("alias", data["cases"][5]["relation_edges"][0]["alias"]),
            "unsupported blocking identity": lambda data: data["cases"][0][
                "expected"
            ].__setitem__("blocking_change_ids", ["context-plan:sha256:" + "0" * 64]),
            "unsorted blocking identities": lambda data: data["cases"][0][
                "expected"
            ].__setitem__(
                "blocking_change_ids",
                [
                    "changed-file:sha256:" + "f" * 64,
                    "change-atom:sha256:" + "0" * 64,
                ],
            ),
            "duplicate blocking identity": lambda data: data["cases"][0][
                "expected"
            ].__setitem__(
                "blocking_change_ids",
                ["change-atom:sha256:" + "0" * 64] * 2,
            ),
        }
        self._assert_manifest_mutations_rejected(mutations)

    def test_loader_rejects_source_hash_revision_path_span_token_and_order_drift(self) -> None:
        with self._copy_golden() as root:
            source = root / "sources" / "CP001_head.ets"
            source.write_text(source.read_text(encoding="utf-8") + "// drift\n")
            with self.assertRaisesRegex(ValueError, "source hash/provenance drift"):
                load_golden_suite(root / "manifest.json")

        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "source hash": lambda data: data["cases"][0]["sources"][0].__setitem__(
                "content_sha256", "0" * 64
            ),
            "revision": lambda data: data["cases"][0]["sources"][0].__setitem__(
                "revision", "drift"
            ),
            "origin lines": lambda data: data["cases"][0]["sources"][0].__setitem__(
                "origin_lines", [1, 99]
            ),
            "path traversal": lambda data: data["cases"][0]["sources"][0].__setitem__(
                "logical_path", "../escape.ets"
            ),
            "source outside fixtures": lambda data: data["cases"][0]["sources"][
                0
            ].__setitem__("file", "README.md"),
            "zero based span": lambda data: data["cases"][4]["candidates"][0][
                "target_span"
            ].__setitem__("start_line", 0),
            "out of range span": lambda data: data["cases"][4]["candidates"][0][
                "target_span"
            ].__setitem__("end_line", 99),
            "token drift": lambda data: data["cases"][4]["candidates"][0].__setitem__(
                "estimated_tokens", 1
            ),
            "mid expression": lambda data: data["cases"][4]["candidates"][0][
                "target_span"
            ].__setitem__(
                "start_offset_utf16",
                data["cases"][4]["candidates"][0]["target_span"][
                    "start_offset_utf16"
                ]
                + 1,
            ),
            "unsorted questions": lambda data: data["cases"][10]["primaries"][0].__setitem__(
                "review_question_ids",
                list(
                    reversed(
                        data["cases"][10]["primaries"][0]["review_question_ids"]
                    )
                ),
            ),
        }
        self._assert_manifest_mutations_rejected(mutations)

    def test_loader_rejects_dangling_provenance_graph_and_public_id_drift(self) -> None:
        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "dangling source": lambda data: data["cases"][4]["candidates"][0].__setitem__(
                "target_source_alias", "missing"
            ),
            "dangling primary": lambda data: data["cases"][4]["candidates"][0].__setitem__(
                "primary_alias", "missing"
            ),
            "dangling edge": lambda data: data["cases"][4]["candidates"][0].__setitem__(
                "relation_edge_alias", "missing"
            ),
            "source id": lambda data: data["cases"][0]["sources"][0].__setitem__(
                "source_ref_id", "code-source:sha256:" + "0" * 64
            ),
            "unit id": lambda data: data["cases"][0]["primaries"][0].__setitem__(
                "unit_id", "not-an-id"
            ),
            "plan id": lambda data: data["cases"][0]["expected"].__setitem__(
                "context_plan_id", "context-plan:sha256:" + "0" * 64
            ),
            "shared atom correspondence": lambda data: data["cases"][11][
                "primaries"
            ][1].__setitem__(
                "change_atom_ids", ["change-atom:sha256:" + "0" * 64]
            ),
            "missing derived correspondence": lambda data: data["cases"][11][
                "expected"
            ].__setitem__("relation_edges", []),
            "fake boundary owner": lambda data: data["cases"][4]["candidates"][
                0
            ].__setitem__("target_owner_ref_id", "declaration:sha256:" + "0" * 64),
            "owner missing from evidence": lambda data: data["cases"][4][
                "relation_edges"
            ][0].__setitem__(
                "evidence_refs",
                [
                    value
                    for value in data["cases"][4]["relation_edges"][0][
                        "evidence_refs"
                    ]
                    if value
                    != data["cases"][4]["candidates"][0]["target_owner_ref_id"]
                ],
            ),
        }
        self._assert_manifest_mutations_rejected(mutations)

    def test_loader_rejects_output_order_and_source_symlinks(self) -> None:
        with self._copy_golden() as root:
            manifest = root / "manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["cases"][1]["expected"]["change_groups"].reverse()
            manifest.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "stable"):
                load_golden_suite(manifest)

        with self._copy_golden() as root:
            source = root / "sources" / "CP001_head.ets"
            target = root / "source-target.ets"
            source.rename(target)
            source.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                load_golden_suite(root / "manifest.json")

    def test_manifest_and_baseline_symlinks_are_rejected(self) -> None:
        with self._copy_golden() as root:
            manifest = root / "manifest.json"
            target = root.parent / "outside-manifest.json"
            target.write_bytes(manifest.read_bytes())
            manifest.unlink()
            manifest.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                load_golden_suite(manifest)

        suite = load_golden_suite(MANIFEST)
        report = evaluate_golden_suite(suite)
        with self._copy_golden() as root:
            baseline = root / "baselines" / "current.json"
            target = root.parent / "outside-baseline.json"
            target.write_bytes(baseline.read_bytes())
            baseline.unlink()
            baseline.symlink_to(target)
            copied_suite = load_golden_suite(root / "manifest.json")
            with self.assertRaisesRegex(ValueError, "symlink"):
                assert_strict_baseline(report, copied_suite, baseline)

        with self._copy_golden() as root:
            baselines = root / "baselines"
            target = root.parent / "outside-baselines"
            baselines.rename(target)
            baselines.symlink_to(target, target_is_directory=True)
            copied_suite = load_golden_suite(root / "manifest.json")
            with self.assertRaisesRegex(ValueError, "symlink"):
                assert_strict_baseline(
                    report,
                    copied_suite,
                    baselines / "current.json",
                )

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
            root = Path(directory) / "context_plan"
            shutil.copytree(GOLDEN_ROOT, root)
            yield root


if __name__ == "__main__":
    unittest.main()
