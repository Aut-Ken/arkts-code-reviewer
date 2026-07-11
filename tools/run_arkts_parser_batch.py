from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from arkts_code_reviewer.code_analysis.parser_factory import (  # noqa: E402
    PARSER_CHOICES,
    ParserChoice,
    create_code_parser,
)
from arkts_code_reviewer.code_analysis.tagger import derive_tags  # noqa: E402
from arkts_code_reviewer.parser_validation.manifest import (  # noqa: E402
    load_corpus_manifest,
    verify_corpus_checkout,
)

DEFAULT_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "arkui_ace_engine_samples.json"
DEFAULT_ENGINE_ROOT = Path(os.getenv("ARKUI_ENGINE_PATH", REPO_ROOT.parent / "arkui_ace_engine"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ArkTS parser against engine samples.")
    parser.add_argument("--engine-root", type=Path, default=DEFAULT_ENGINE_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--parser",
        choices=PARSER_CHOICES,
        default="arkts-tree-sitter",
    )
    parser.add_argument(
        "--require-layer",
        choices=("L0", "L1", "parse_degraded"),
        help="Exit non-zero unless every parsed sample used this parser layer.",
    )
    args = parser.parse_args()

    try:
        report = run_batch(args.engine_root, args.manifest, parser_name=args.parser)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(_format_report(report))
    all_empty = report["parsed"] > 0 and len(report["empty_features"]) == report["parsed"]
    wrong_layer = args.require_layer and report["parser_layers"] != {
        args.require_layer: report["parsed"]
    }
    if report["missing"] or report["crashed"] or all_empty or wrong_layer:
        raise SystemExit(1)


def run_batch(
    engine_root: Path,
    manifest_path: Path,
    parser_name: ParserChoice = "arkts-tree-sitter",
) -> dict[str, Any]:
    manifest = load_corpus_manifest(manifest_path)
    revision = verify_corpus_checkout(engine_root, manifest)
    samples = manifest.samples
    parser = create_code_parser(parser_name)
    started = time.perf_counter()

    categories: Counter[str] = Counter()
    components: Counter[str] = Counter()
    apis: Counter[str] = Counter()
    decorators: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    parser_layers: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    missing: list[str] = []
    crashed: list[dict[str, str]] = []
    empty_features: list[str] = []
    declaration_counts: list[int] = []

    for sample in samples:
        rel_path = sample.path
        categories[sample.category] += 1
        source_path = engine_root / Path(rel_path)
        if not source_path.exists():
            missing.append(rel_path)
            continue

        try:
            source = source_path.read_text(encoding="utf-8")
            facts = parser.parse(source, rel_path)
            sample_tags = derive_tags(facts)
        except Exception as exc:  # pragma: no cover - diagnostic script path.
            crashed.append({"path": rel_path, "error": repr(exc)})
            continue

        components.update(facts.components)
        apis.update(facts.apis)
        decorators.update(facts.decorators)
        tags.update(sample_tags)
        parser_layers[facts.parser_layer] += 1
        warning_counts.update(warning.split(":", 1)[0] for warning in facts.warnings)
        declaration_counts.append(len(facts.declarations))
        has_features = any(
            (facts.components, facts.apis, facts.decorators, facts.declarations, facts.syntax)
        )
        if not has_features:
            empty_features.append(rel_path)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    parsed = len(samples) - len(missing) - len(crashed)
    return {
        "engine_root": str(engine_root),
        "manifest": str(manifest_path),
        "schema_version": manifest.schema_version,
        "suite_id": manifest.suite_id,
        "suite_role": manifest.suite_role,
        "source_id": manifest.source_id,
        "revision": revision,
        "parser": parser_name,
        "total_samples": len(samples),
        "parsed": parsed,
        "missing": missing,
        "crashed": crashed,
        "empty_features": empty_features,
        "elapsed_ms": elapsed_ms,
        "categories": dict(sorted(categories.items())),
        "files_with_declarations": sum(1 for count in declaration_counts if count > 0),
        "declarations_total": sum(declaration_counts),
        "top_components": components.most_common(20),
        "top_apis": apis.most_common(20),
        "top_decorators": decorators.most_common(20),
        "top_tags": tags.most_common(20),
        "parser_layers": dict(sorted(parser_layers.items())),
        "warning_counts": dict(sorted(warning_counts.items())),
    }


def _format_report(report: dict[str, Any]) -> str:
    lines = [
        "ArkTS parser batch report",
        f"  suite_id: {report['suite_id']}",
        f"  suite_role: {report['suite_role']}",
        f"  source_revision: {report['source_id']}@{report['revision']}",
        f"  engine_root: {report['engine_root']}",
        f"  parser: {report['parser']}",
        f"  samples: {report['total_samples']}",
        f"  parsed: {report['parsed']}",
        f"  missing: {len(report['missing'])}",
        f"  crashed: {len(report['crashed'])}",
        f"  empty_features: {len(report['empty_features'])}",
        f"  files_with_declarations: {report['files_with_declarations']}",
        f"  declarations_total: {report['declarations_total']}",
        f"  elapsed_ms: {report['elapsed_ms']}",
        f"  top_components: {_compact(report['top_components'])}",
        f"  top_apis: {_compact(report['top_apis'])}",
        f"  top_decorators: {_compact(report['top_decorators'])}",
        f"  top_tags: {_compact(report['top_tags'])}",
        f"  parser_layers: {report['parser_layers']}",
        f"  warning_counts: {report['warning_counts']}",
    ]
    if report["missing"]:
        lines.append(f"  missing_paths: {report['missing'][:5]}")
    if report["crashed"]:
        lines.append(f"  first_crash: {report['crashed'][0]}")
    if report["empty_features"]:
        lines.append(f"  first_empty_features: {report['empty_features'][:5]}")
    return "\n".join(lines)


def _compact(items: list[list[Any]] | list[tuple[Any, ...]]) -> str:
    return ", ".join(f"{name}={count}" for name, count in items[:8])


if __name__ == "__main__":
    main()
