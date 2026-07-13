#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.annotation import KnowledgeAnnotationBuild
from arkts_code_reviewer.knowledge.annotation_config import (
    load_knowledge_annotation_config,
)
from arkts_code_reviewer.knowledge.build import NormalizedKnowledgeBuild
from arkts_code_reviewer.knowledge.extraction import KnowledgeExtractionBuild
from arkts_code_reviewer.knowledge.registry import load_source_registry
from arkts_code_reviewer.knowledge.review_packets import (
    DEFAULT_EXPORT_POLICY,
    DEFAULT_REVIEW_PROMPT,
    build_knowledge_review_packets,
    load_knowledge_model_export_policy,
    load_knowledge_review_prompt,
)

DEFAULT_NORMALIZED = Path(
    "/home/autken/Code/arkts-review-data/normalized/knowledge-seed-v1/normalized.json"
)
DEFAULT_EXTRACTION = Path(
    "/home/autken/Code/arkts-review-data/normalized/knowledge-seed-v1/candidates.json"
)
DEFAULT_ANNOTATIONS = Path(
    "/home/autken/Code/arkts-review-data/normalized/knowledge-seed-v1/annotations.json"
)
DEFAULT_OUTPUT = Path(
    "/home/autken/Code/arkts-review-data/reports/knowledge-review/knowledge-seed-v1"
)
DEFAULT_RESPONSE_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "knowledge"
    / "grok-review-output.schema.json"
)

def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"input must be a regular non-symlink file: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
        json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        return model.model_validate_json(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid input {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic, policy-bound Knowledge review packets"
    )
    parser.add_argument("--normalized", type=Path, default=DEFAULT_NORMALIZED)
    parser.add_argument("--extraction", type=Path, default=DEFAULT_EXTRACTION)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--policy", type=Path, default=DEFAULT_EXPORT_POLICY)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_REVIEW_PROMPT)
    parser.add_argument("--annotation-config", type=Path)
    parser.add_argument("--response-schema", type=Path, default=DEFAULT_RESPONSE_SCHEMA)
    parser.add_argument("--registry", type=Path)
    parser.add_argument(
        "--distribution",
        choices=("local_only", "external_model"),
        default="local_only",
    )
    parser.add_argument("--model-provider")
    parser.add_argument("--model-name")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    normalized = _load_model(args.normalized, NormalizedKnowledgeBuild)
    extraction = _load_model(args.extraction, KnowledgeExtractionBuild)
    annotations = _load_model(args.annotations, KnowledgeAnnotationBuild)
    registry = load_source_registry(args.registry)
    policy = load_knowledge_model_export_policy(args.policy)
    prompt = load_knowledge_review_prompt(args.prompt)
    feature_config = load_default_feature_config()
    annotation_config = load_knowledge_annotation_config(
        args.annotation_config,
        feature_config=feature_config,
    )
    build = build_knowledge_review_packets(
        normalized,
        extraction,
        annotations,
        registry=registry,
        feature_config=feature_config,
        annotation_config=annotation_config,
        policy=policy,
        prompt=prompt,
        distribution=args.distribution,
        model_provider=args.model_provider,
        model_name=args.model_name,
    )
    if args.response_schema.is_symlink() or not args.response_schema.is_file():
        raise ValueError("Grok review output schema must be a regular non-symlink file")
    response_schema = args.response_schema.read_text(encoding="utf-8")
    json.loads(response_schema, object_pairs_hook=_reject_duplicate_keys)

    if args.output_dir.exists():
        raise ValueError(f"review packet output already exists: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    files: list[Path] = []
    build_path = args.output_dir / "build.json"
    build_path.write_text(build.model_dump_json(indent=2) + "\n", encoding="utf-8")
    files.append(build_path)
    prompt_path = args.output_dir / "prompt.md"
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    files.append(prompt_path)
    schema_path = args.output_dir / "grok-review-output.schema.json"
    schema_path.write_text(response_schema.rstrip() + "\n", encoding="utf-8")
    files.append(schema_path)
    if build.distribution == "local_only":
        boundary_path = args.output_dir / "LOCAL_ONLY_DO_NOT_EXPORT.txt"
        boundary_path.write_text(
            "This packet contains source-derived text and is not authorized for "
            "external model export.\n",
            encoding="utf-8",
        )
        files.append(boundary_path)
    for ordinal, packet in enumerate(build.packets, start=1):
        digest = packet.packet_id.rsplit(":", 1)[-1][:12]
        packet_path = args.output_dir / f"packet-{ordinal:03d}-{digest}.json"
        packet_path.write_text(packet.model_dump_json(indent=2) + "\n", encoding="utf-8")
        files.append(packet_path)
    manifest = {
        "schema_version": "knowledge-review-packet-manifest-v1",
        "build_id": build.build_id,
        "distribution": build.distribution,
        "files": [
            {
                "relative_path": path.relative_to(args.output_dir).as_posix(),
                "content_hash": _sha256(path),
            }
            for path in sorted(files)
        ],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    clause_count = sum(len(packet.clauses) for packet in build.packets)
    print(
        f"built {len(build.packets)} {build.distribution} review packets for "
        f"{clause_count} Clauses as {build.build_id} -> {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
