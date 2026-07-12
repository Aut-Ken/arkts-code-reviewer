from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR = REPO_ROOT / "sidecars" / "arkts-parser" / "parse_arkts.js"
SIDECAR_NODE_MODULE = (
    REPO_ROOT
    / "sidecars"
    / "arkts-parser"
    / "node_modules"
    / "tree-sitter-arkts"
    / "package.json"
)
LEGACY_FIELDS = (
    "parser",
    "parser_version",
    "path",
    "root_type",
    "node_count",
    "error_nodes",
    "missing_nodes",
    "components",
    "calls",
    "decorators",
    "attributes",
    "symbols",
    "syntax",
    "declarations",
)
V2_ONLY_FIELDS = {
    "output_schema",
    "producer_version",
    "offset_unit",
    "declarations_v2",
    "review_regions",
    "raw_occurrences",
    "error_spans",
    "missing_spans",
}

SIDECAR_SAMPLE = (
    """import router from '@ohos.router'

@Component
struct SameLinePage {
  @State count: number = 0

  async load() {
    const marker = '😀'
    this.count += 1
    await router.back()
  }

"""
    "  build() { Column() { Text(`${this.count}`).onClick(() => router.back()) } "
    "Column() { Text('two') } }\n"
    "}\n"
)


@unittest.skipUnless(
    SIDECAR_NODE_MODULE.is_file(),
    "ArkTS tree-sitter sidecar dependencies are not installed",
)
class FileAnalysisSidecarTest(unittest.TestCase):
    def _run_sidecar(
        self,
        source: str,
        *,
        output_schema: str | None = None,
    ) -> dict[str, object]:
        command = ["node", str(SIDECAR), "--path", "golden/FileAnalysis.ets"]
        if output_schema is not None:
            command.extend(["--output-schema", output_schema])
        completed = subprocess.run(
            command,
            input=source.encode(),
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr.decode(errors="replace"),
        )
        value: object = json.loads(completed.stdout)
        self.assertIsInstance(value, dict)
        return cast(dict[str, object], value)

    def test_v2_is_explicit_and_preserves_the_complete_legacy_projection(self) -> None:
        legacy = self._run_sidecar(SIDECAR_SAMPLE)
        v2 = self._run_sidecar(SIDECAR_SAMPLE, output_schema="file-analysis-v1")

        self.assertEqual(tuple(legacy), LEGACY_FIELDS)
        self.assertEqual(v2["output_schema"], "file-analysis-v1")
        self.assertEqual(v2["producer_version"], "arkts-parser-sidecar-v2.0.0")
        self.assertEqual(v2["offset_unit"], "utf16_code_unit")
        self.assertEqual(
            {key: value for key, value in v2.items() if key not in V2_ONLY_FIELDS},
            legacy,
        )
        self.assertEqual(
            v2,
            self._run_sidecar(SIDECAR_SAMPLE, output_schema="file-analysis-v1"),
        )

    def test_v2_emits_unique_structures_and_owned_fact_occurrences(self) -> None:
        result = self._run_sidecar(SIDECAR_SAMPLE, output_schema="file-analysis-v1")
        declarations = result["declarations_v2"]
        regions = result["review_regions"]
        occurrences = result["raw_occurrences"]
        assert isinstance(declarations, list)
        assert isinstance(regions, list)
        assert isinstance(occurrences, list)

        declaration_ids = {item["local_id"] for item in declarations}
        region_ids = {item["local_id"] for item in regions}
        self.assertEqual(len(declaration_ids), len(declarations))
        self.assertEqual(len(region_ids), len(regions))
        columns = [
            item
            for item in declarations
            if item["kind"] == "ui_block" and item["name"] == "Column"
        ]
        self.assertEqual(len(columns), 2)
        self.assertEqual(len({item["local_id"] for item in columns}), 2)
        self.assertEqual(len({item["start_offset"] for item in columns}), 2)

        self.assertEqual(
            {item["kind"] for item in regions},
            {"field_region", "import_region"},
        )
        expected_kinds = {
            "attribute",
            "component",
            "decorator",
            "field_read",
            "field_write",
            "import_binding",
            "raw_call",
            "syntax",
        }
        self.assertTrue(expected_kinds.issubset({item["kind"] for item in occurrences}))

        owners = {
            ("declaration", local_id) for local_id in declaration_ids
        } | {("region", local_id) for local_id in region_ids}
        for collection in (declarations, regions, occurrences):
            for item in collection:
                with self.subTest(local_id=item["local_id"]):
                    self.assertGreaterEqual(item["span"]["start_line"], 1)
                    self.assertGreaterEqual(
                        item["span"]["end_line"],
                        item["span"]["start_line"],
                    )
                    self.assertGreaterEqual(item["start_offset"], 0)
                    self.assertGreaterEqual(item["end_offset"], item["start_offset"])
                    reference = item.get("parent", item.get("owner"))
                    if reference is not None:
                        self.assertIn(
                            (reference["kind"], reference["local_id"]),
                            owners,
                        )

        state = next(
            item
            for item in occurrences
            if item["kind"] == "decorator" and item["canonical_name"] == "@State"
        )
        self.assertEqual(state["owner"]["kind"], "region")
        on_click = next(
            item
            for item in occurrences
            if item["kind"] == "attribute" and item["name"] == "onClick"
        )
        self.assertEqual(on_click["owner"]["kind"], "declaration")

        field_reads = [
            item
            for item in occurrences
            if item["kind"] == "field_read" and item["name"] == "count"
        ]
        field_writes = [
            item
            for item in occurrences
            if item["kind"] == "field_write" and item["name"] == "count"
        ]
        self.assertGreaterEqual(len(field_reads), 2)
        self.assertEqual(len(field_writes), 1)
        self.assertTrue(
            any(
                read["start_offset"] == field_writes[0]["start_offset"]
                and read["end_offset"] == field_writes[0]["end_offset"]
                for read in field_reads
            )
        )

        expected_router_offset = len(
            SIDECAR_SAMPLE[: SIDECAR_SAMPLE.index("router.back()")].encode("utf-16-le")
        ) // 2
        router_call = next(
            item
            for item in occurrences
            if item["kind"] == "raw_call" and item["name"] == "router.back"
        )
        self.assertEqual(router_call["start_offset"], expected_router_offset)

    def test_v2_localizes_error_and_missing_nodes(self) -> None:
        error_result = self._run_sidecar(
            "function broken() {\n  router.pushUrl(\n}\n",
            output_schema="file-analysis-v1",
        )
        missing_result = self._run_sidecar(
            "@Component\nstruct Broken {\n  build() { Text('x') }\n",
            output_schema="file-analysis-v1",
        )

        error_nodes = cast(int, error_result["error_nodes"])
        error_spans = cast(list[dict[str, object]], error_result["error_spans"])
        missing_nodes = cast(int, missing_result["missing_nodes"])
        missing_spans = cast(list[dict[str, object]], missing_result["missing_spans"])

        self.assertGreater(error_nodes, 0)
        self.assertEqual(len(error_spans), error_nodes)
        self.assertGreater(missing_nodes, 0)
        self.assertEqual(
            len(missing_spans),
            missing_nodes,
        )
        for item in error_spans + missing_spans:
            span = cast(dict[str, int], item["span"])
            self.assertGreaterEqual(span["start_line"], 1)
            self.assertGreaterEqual(
                cast(int, item["end_offset"]),
                cast(int, item["start_offset"]),
            )
            self.assertIn("owner", item)


if __name__ == "__main__":
    unittest.main()
