#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from arkts_code_reviewer.knowledge.review_packets import KnowledgeReviewPacket
from arkts_code_reviewer.knowledge.review_validation import (
    load_and_validate_knowledge_model_review,
)

DEFAULT_PACKET_ROOT = Path(
    "/home/autken/Code/arkts-review-data/reports/knowledge-review/"
    "knowledge-seed-v1-grok-4.5-auditor-v2"
)
DEFAULT_OUTPUT_ROOT = Path(
    "/home/autken/Code/arkts-review-data/reports/knowledge-review-responses/"
    "knowledge-seed-v1/grok-4.5/round-1"
)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_regular_text(path: Path, context: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{context} must be a regular non-symlink file: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read {context}: {path}") from exc


def _load_json(raw: str, context: str) -> object:
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid {context} JSON: {exc}") from exc


def _load_packet(path: Path) -> tuple[KnowledgeReviewPacket, str]:
    raw = _read_regular_text(path, "Knowledge review packet")
    _load_json(raw, "Knowledge review packet")
    return KnowledgeReviewPacket.model_validate_json(raw), raw


def _load_response_schema(path: Path) -> str:
    raw = _read_regular_text(path, "Grok response schema").strip()
    payload = _load_json(raw, "Grok response schema")
    if not isinstance(payload, dict):
        raise ValueError("Grok response schema must be a JSON object")
    return raw


def _build_request(prompt: str, packet_raw: str) -> str:
    if not prompt or prompt.strip() != prompt or "\x00" in prompt:
        raise ValueError("Knowledge review prompt must be non-empty and trimmed")
    return (
        f"{prompt}\n\n"
        "下面的 knowledge_review_packet 是唯一允许审核的数据。"
        "不要调用工具，不要读取本地文件，不要联网。\n"
        "<knowledge_review_packet>\n"
        f"{packet_raw.rstrip()}\n"
        "</knowledge_review_packet>"
    )


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _review_paths(output_dir: Path, packet: KnowledgeReviewPacket) -> dict[str, Path]:
    digest = packet.packet_id.rsplit(":", 1)[-1]
    stem = f"review-{digest}"
    return {
        "raw": output_dir / f"{stem}.raw.json",
        "review": output_dir / f"{stem}.review.json",
        "receipt": output_dir / f"{stem}.receipt.json",
    }


def _parse_grok_wrapper(raw: str) -> tuple[dict[str, Any], dict[str, object]]:
    payload = _load_json(raw, "Grok CLI response")
    if not isinstance(payload, dict):
        raise ValueError("Grok CLI response must be a JSON object")
    structured = payload.get("structuredOutput")
    if not isinstance(structured, dict):
        raise ValueError("Grok CLI response is missing structuredOutput")
    text_output = payload.get("text")
    if not isinstance(text_output, str):
        raise ValueError("Grok CLI response is missing text")
    text_payload = _load_json(text_output, "Grok text output")
    if text_payload != structured:
        raise ValueError("Grok text and structured outputs do not match")
    return payload, structured


def run_review(
    *,
    packet_path: Path,
    prompt_path: Path,
    schema_path: Path,
    output_dir: Path,
    grok_binary: str,
    reasoning_effort: str,
    timeout_seconds: int,
) -> dict[str, object]:
    packet, packet_raw = _load_packet(packet_path)
    if packet.distribution != "external_model":
        raise ValueError("Grok review requires an external_model packet")
    if packet.model_provider != "xai" or not packet.model_name:
        raise ValueError("Grok review packet must bind an xAI model")
    prompt = _read_regular_text(prompt_path, "Knowledge review prompt").strip()
    schema = _load_response_schema(schema_path)
    request = _build_request(prompt, packet_raw)
    paths = _review_paths(output_dir, packet)
    existing = [path for path in paths.values() if path.exists()]
    if existing:
        raise ValueError(f"Knowledge review output already exists: {existing}")
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        grok_binary,
        "--single",
        request,
        "--model",
        packet.model_name,
        "--reasoning-effort",
        reasoning_effort,
        "--max-turns",
        "1",
        "--no-memory",
        "--no-subagents",
        "--disable-web-search",
        "--no-plan",
        "--verbatim",
        "--permission-mode",
        "plan",
        "--tools",
        "",
        "--json-schema",
        schema,
        "--output-format",
        "json",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Grok CLI invocation failed before producing a response") from exc
    if completed.returncode != 0:
        digest = packet.packet_id.rsplit(":", 1)[-1]
        failure_prefix = output_dir / f"review-{digest}.failed"
        Path(f"{failure_prefix}.stdout.txt").write_text(
            completed.stdout,
            encoding="utf-8",
        )
        Path(f"{failure_prefix}.stderr.txt").write_text(
            completed.stderr,
            encoding="utf-8",
        )
        raise ValueError(
            f"Grok CLI exited with {completed.returncode}: {completed.stderr.strip()}"
        )
    raw_response = completed.stdout.strip()
    paths["raw"].write_text(raw_response + "\n", encoding="utf-8")
    wrapper, structured = _parse_grok_wrapper(raw_response)
    structured_raw = json.dumps(
        structured,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    paths["review"].write_text(structured_raw, encoding="utf-8")
    review = load_and_validate_knowledge_model_review(
        structured_raw,
        packet=packet,
    )
    receipt: dict[str, object] = {
        "schema_version": "knowledge-grok-review-receipt-v1",
        "packet_id": packet.packet_id,
        "packet_hash": _sha256_text(packet_raw),
        "review_hash": _sha256_text(structured_raw),
        "raw_response_hash": _sha256_text(raw_response),
        "provider": packet.model_provider,
        "model": packet.model_name,
        "prompt_version": packet.prompt_version,
        "prompt_hash": packet.prompt_hash,
        "request_id": wrapper.get("requestId"),
        "session_id": wrapper.get("sessionId"),
        "stop_reason": wrapper.get("stopReason"),
        "usage": wrapper.get("usage"),
        "packet_decision": review.packet_decision,
        "summary": review.summary.model_dump(mode="json"),
        "validated": True,
    }
    paths["receipt"].write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one policy-authorized Knowledge packet through Grok Build"
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--prompt", type=Path)
    parser.add_argument("--response-schema", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--grok-binary", default="grok")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()

    prompt = args.prompt or args.packet.parent / "prompt.md"
    schema = args.response_schema or args.packet.parent / "grok-review-output.schema.json"
    receipt = run_review(
        packet_path=args.packet,
        prompt_path=prompt,
        schema_path=schema,
        output_dir=args.output_dir,
        grok_binary=args.grok_binary,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
