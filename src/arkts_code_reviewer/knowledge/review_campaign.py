from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from arkts_code_reviewer.knowledge.models import (
    KnowledgeModelReview,
    ModelReviewSummary,
)
from arkts_code_reviewer.knowledge.review_packets import (
    KnowledgeReviewPacket,
    KnowledgeReviewPacketBuild,
)
from arkts_code_reviewer.knowledge.review_validation import (
    load_and_validate_knowledge_model_review,
)

CAMPAIGN_SUMMARY_SCHEMA_VERSION = "knowledge-grok-campaign-summary-v1"

_ROUND_PREFIX_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PACKET_ID_RE = re.compile(r"^knowledge-review-packet:sha256:([0-9a-f]{64})$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


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
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid {context} JSON: {exc}") from exc


def _load_json_file(path: Path, context: str) -> tuple[object, str]:
    raw = _read_regular_text(path, context)
    return _load_json(raw, context), raw


def _safe_relative_path(value: str, context: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or "." in path.parts
        or ".." in path.parts
        or "\\" in value
    ):
        raise ValueError(f"{context} must stay below its root")
    return str(path)


class CampaignUsage(_FrozenModel):
    input_tokens: Annotated[int, Field(ge=0)]
    cache_read_input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    reasoning_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_totals(self) -> CampaignUsage:
        expected = (
            self.input_tokens
            + self.cache_read_input_tokens
            + self.output_tokens
        )
        if self.total_tokens != expected:
            raise ValueError("Campaign usage total_tokens does not match token fields")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("Campaign reasoning_tokens must be included in output_tokens")
        return self


class GrokReviewReceipt(_FrozenModel):
    schema_version: Literal["knowledge-grok-review-receipt-v1"]
    packet_id: Annotated[
        str,
        Field(pattern=r"^knowledge-review-packet:sha256:[0-9a-f]{64}$"),
    ]
    packet_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    review_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    raw_response_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    provider: Annotated[str, Field(min_length=1)]
    model: Annotated[str, Field(min_length=1)]
    prompt_version: Annotated[str, Field(min_length=1)]
    prompt_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    request_id: Annotated[str, Field(min_length=1)]
    session_id: Annotated[str, Field(min_length=1)]
    stop_reason: Literal["EndTurn"]
    usage: CampaignUsage
    packet_decision: Literal["accept", "reject", "uncertain"]
    summary: ModelReviewSummary
    validated: Literal[True]


class PacketManifestEntry(_FrozenModel):
    relative_path: Annotated[str, Field(min_length=1)]
    content_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_relative_path(value, "Packet manifest path")


class PacketManifest(_FrozenModel):
    schema_version: Literal["knowledge-review-packet-manifest-v1"]
    build_id: Annotated[
        str,
        Field(pattern=r"^knowledge-review-packets:sha256:[0-9a-f]{64}$"),
    ]
    distribution: Literal["local_only", "external_model"]
    files: tuple[PacketManifestEntry, ...]

    @field_validator("files", mode="before")
    @classmethod
    def parse_files(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("PacketManifest.files must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_files(self) -> PacketManifest:
        paths = [item.relative_path for item in self.files]
        if not paths or paths != sorted(set(paths)):
            raise ValueError("Packet manifest files must be non-empty, sorted, and unique")
        return self


class CampaignPacketDecisionTotals(_FrozenModel):
    accept: Annotated[int, Field(ge=0)]
    reject: Annotated[int, Field(ge=0)]
    uncertain: Annotated[int, Field(ge=0)]


class CampaignGlobalFindings(_FrozenModel):
    missing_clauses: Annotated[int, Field(ge=0)]
    duplicate_groups: Annotated[int, Field(ge=0)]
    conflicts: Annotated[int, Field(ge=0)]


class SelectedCampaignReview(_FrozenModel):
    packet_id: Annotated[
        str,
        Field(pattern=r"^knowledge-review-packet:sha256:[0-9a-f]{64}$"),
    ]
    round_name: Annotated[str, Field(min_length=1)]
    receipt_file: Annotated[str, Field(min_length=1)]
    packet_file_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    review_file_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    raw_file_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    receipt_file_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    request_id: Annotated[str, Field(min_length=1)]
    session_id: Annotated[str, Field(min_length=1)]
    stop_reason: Literal["EndTurn"]
    packet_decision: Literal["accept", "reject", "uncertain"]
    summary: ModelReviewSummary
    usage: CampaignUsage
    global_findings: CampaignGlobalFindings

    @field_validator("receipt_file")
    @classmethod
    def validate_receipt_file(cls, value: str) -> str:
        return _safe_relative_path(value, "Campaign receipt path")


class CampaignArtifactRef(_FrozenModel):
    relative_path: Annotated[str, Field(min_length=1)]
    content_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _safe_relative_path(value, "Campaign artifact path")


class CampaignFailedAttempt(_FrozenModel):
    round_name: Annotated[str, Field(min_length=1)]
    packet_id: Annotated[
        str,
        Field(pattern=r"^knowledge-review-packet:sha256:[0-9a-f]{64}$"),
    ]
    failure_kind: Literal[
        "transport_error",
        "validation_error",
        "wrapper_error",
        "incomplete_artifact",
    ]
    message: Annotated[str, Field(min_length=1)]
    request_id: str | None = None
    session_id: str | None = None
    usage: CampaignUsage | None = None
    structured_response: bool
    artifacts: tuple[CampaignArtifactRef, ...]

    @field_validator("artifacts", mode="before")
    @classmethod
    def parse_artifacts(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("CampaignFailedAttempt.artifacts must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_artifacts(self) -> CampaignFailedAttempt:
        paths = [item.relative_path for item in self.artifacts]
        if not paths or paths != sorted(set(paths)):
            raise ValueError("Campaign failed attempt artifacts must be sorted and unique")
        if not self.structured_response and self.usage is not None:
            raise ValueError("non-structured failure must not carry model usage")
        return self


class KnowledgeGrokCampaignSummary(_FrozenModel):
    schema_version: Literal["knowledge-grok-campaign-summary-v1"] = (
        "knowledge-grok-campaign-summary-v1"
    )
    summary_id: Annotated[
        str,
        Field(pattern=r"^knowledge-grok-campaign:sha256:[0-9a-f]{64}$"),
    ]
    packet_build_id: Annotated[
        str,
        Field(pattern=r"^knowledge-review-packets:sha256:[0-9a-f]{64}$"),
    ]
    packet_manifest_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    round_prefix: Annotated[str, Field(min_length=1)]
    round_names: tuple[str, ...]
    packet_count: Annotated[int, Field(ge=1)]
    selected_receipts: tuple[SelectedCampaignReview, ...]
    failed_attempts: tuple[CampaignFailedAttempt, ...]
    packet_decisions: CampaignPacketDecisionTotals
    clause_decisions: ModelReviewSummary
    global_findings: CampaignGlobalFindings
    valid_usage: CampaignUsage
    failed_structured_usage: CampaignUsage
    all_structured_usage: CampaignUsage
    structured_attempt_count: Annotated[int, Field(ge=1)]
    transport_failure_count: Annotated[int, Field(ge=0)]

    @field_validator("round_names", "selected_receipts", "failed_attempts", mode="before")
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Campaign summary collections must be sequences")
        return tuple(value)

    def identity_payload(self) -> dict[str, object]:
        return {
            "packet_build_id": self.packet_build_id,
            "packet_manifest_hash": self.packet_manifest_hash,
            "round_prefix": self.round_prefix,
            "round_names": self.round_names,
            "packet_count": self.packet_count,
            "selected_receipts": [
                item.model_dump(mode="json") for item in self.selected_receipts
            ],
            "failed_attempts": [
                item.model_dump(mode="json") for item in self.failed_attempts
            ],
            "packet_decisions": self.packet_decisions.model_dump(mode="json"),
            "clause_decisions": self.clause_decisions.model_dump(mode="json"),
            "global_findings": self.global_findings.model_dump(mode="json"),
            "valid_usage": self.valid_usage.model_dump(mode="json"),
            "failed_structured_usage": self.failed_structured_usage.model_dump(
                mode="json"
            ),
            "all_structured_usage": self.all_structured_usage.model_dump(mode="json"),
            "structured_attempt_count": self.structured_attempt_count,
            "transport_failure_count": self.transport_failure_count,
        }

    def expected_summary_id(self) -> str:
        return _canonical_hash("knowledge-grok-campaign", self.identity_payload())

    @model_validator(mode="after")
    def validate_summary(self) -> KnowledgeGrokCampaignSummary:
        if not _ROUND_PREFIX_RE.fullmatch(self.round_prefix):
            raise ValueError("Campaign round_prefix must use lowercase kebab-case")
        if not self.round_names or list(self.round_names) != sorted(set(self.round_names)):
            raise ValueError("Campaign round_names must be sorted and unique")
        if any(
            name != self.round_prefix and not name.startswith(f"{self.round_prefix}-")
            for name in self.round_names
        ):
            raise ValueError("Campaign round_names do not match round_prefix")
        selected_ids = [item.packet_id for item in self.selected_receipts]
        if len(selected_ids) != self.packet_count:
            raise ValueError("Campaign selected receipt count does not match packet_count")
        if selected_ids != sorted(set(selected_ids)):
            raise ValueError("Campaign selected receipts must be packet-sorted and unique")
        failure_keys = [
            (item.round_name, item.packet_id, item.failure_kind, item.message)
            for item in self.failed_attempts
        ]
        if failure_keys != sorted(set(failure_keys)):
            raise ValueError("Campaign failed attempts must be sorted and unique")
        known_rounds = set(self.round_names)
        if any(item.round_name not in known_rounds for item in self.selected_receipts):
            raise ValueError("Campaign selected receipt references an unknown round")
        if any(item.round_name not in known_rounds for item in self.failed_attempts):
            raise ValueError("Campaign failed attempt references an unknown round")
        all_request_ids = [
            *(item.request_id for item in self.selected_receipts),
            *(
                item.request_id
                for item in self.failed_attempts
                if item.request_id is not None
            ),
        ]
        all_session_ids = [
            *(item.session_id for item in self.selected_receipts),
            *(
                item.session_id
                for item in self.failed_attempts
                if item.session_id is not None
            ),
        ]
        if len(all_request_ids) != len(set(all_request_ids)):
            raise ValueError("Campaign summary request_id values must be globally unique")
        if len(all_session_ids) != len(set(all_session_ids)):
            raise ValueError("Campaign summary session_id values must be globally unique")
        expected_packet_decisions = CampaignPacketDecisionTotals(
            accept=sum(
                item.packet_decision == "accept" for item in self.selected_receipts
            ),
            reject=sum(
                item.packet_decision == "reject" for item in self.selected_receipts
            ),
            uncertain=sum(
                item.packet_decision == "uncertain" for item in self.selected_receipts
            ),
        )
        if self.packet_decisions != expected_packet_decisions:
            raise ValueError("Campaign packet decision totals do not match selected reviews")
        expected_clause_decisions = ModelReviewSummary(
            accepted=sum(item.summary.accepted for item in self.selected_receipts),
            rejected=sum(item.summary.rejected for item in self.selected_receipts),
            uncertain=sum(item.summary.uncertain for item in self.selected_receipts),
            with_corrections=sum(
                item.summary.with_corrections for item in self.selected_receipts
            ),
        )
        if self.clause_decisions != expected_clause_decisions:
            raise ValueError("Campaign Clause decision totals do not match selected reviews")
        expected_findings = CampaignGlobalFindings(
            missing_clauses=sum(
                item.global_findings.missing_clauses
                for item in self.selected_receipts
            ),
            duplicate_groups=sum(
                item.global_findings.duplicate_groups
                for item in self.selected_receipts
            ),
            conflicts=sum(
                item.global_findings.conflicts for item in self.selected_receipts
            ),
        )
        if self.global_findings != expected_findings:
            raise ValueError("Campaign global finding totals do not match selected reviews")
        expected_valid_usage = _sum_usage(
            [item.usage for item in self.selected_receipts]
        )
        expected_failed_usage = _sum_usage(
            [
                item.usage
                for item in self.failed_attempts
                if item.usage is not None
            ]
        )
        if self.valid_usage != expected_valid_usage:
            raise ValueError("Campaign valid usage does not match selected reviews")
        if self.failed_structured_usage != expected_failed_usage:
            raise ValueError("Campaign failed usage does not match failed attempts")
        if self.all_structured_usage != _sum_usage(
            [expected_valid_usage, expected_failed_usage]
        ):
            raise ValueError("Campaign all structured usage total is inconsistent")
        expected_structured_count = len(self.selected_receipts) + sum(
            item.structured_response for item in self.failed_attempts
        )
        if self.structured_attempt_count != expected_structured_count:
            raise ValueError("Campaign structured attempt count is inconsistent")
        expected_transport_count = sum(
            item.failure_kind == "transport_error" for item in self.failed_attempts
        )
        if self.transport_failure_count != expected_transport_count:
            raise ValueError("Campaign transport failure count is inconsistent")
        if self.summary_id != self.expected_summary_id():
            raise ValueError("KnowledgeGrokCampaignSummary.summary_id does not match content")
        return self


def _zero_usage() -> CampaignUsage:
    return CampaignUsage(
        input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        total_tokens=0,
    )


def _sum_usage(values: list[CampaignUsage]) -> CampaignUsage:
    if not values:
        return _zero_usage()
    return CampaignUsage(
        input_tokens=sum(item.input_tokens for item in values),
        cache_read_input_tokens=sum(item.cache_read_input_tokens for item in values),
        output_tokens=sum(item.output_tokens for item in values),
        reasoning_tokens=sum(item.reasoning_tokens for item in values),
        total_tokens=sum(item.total_tokens for item in values),
    )


def _load_packet_artifacts(
    packet_root: Path,
) -> tuple[
    KnowledgeReviewPacketBuild,
    PacketManifest,
    str,
    dict[str, KnowledgeReviewPacket],
    dict[str, str],
]:
    if packet_root.is_symlink() or not packet_root.is_dir():
        raise ValueError("packet root must be a regular non-symlink directory")
    manifest_payload, manifest_raw = _load_json_file(
        packet_root / "manifest.json",
        "packet manifest",
    )
    try:
        manifest = PacketManifest.model_validate(manifest_payload)
    except ValidationError as exc:
        raise ValueError(f"invalid packet manifest: {exc}") from exc
    unexpected_nodes = sorted(
        item.name
        for item in packet_root.iterdir()
        if not item.is_file() or item.is_symlink()
    )
    if unexpected_nodes:
        raise ValueError(f"packet root contains unsupported nodes: {unexpected_nodes}")
    declared = {item.relative_path: item.content_hash for item in manifest.files}
    actual = {
        item.name
        for item in packet_root.iterdir()
        if item.is_file() and item.name != "manifest.json"
    }
    if actual != set(declared):
        raise ValueError(
            "packet manifest file set mismatch: "
            f"missing={sorted(set(declared) - actual)}, "
            f"extra={sorted(actual - set(declared))}"
        )
    for relative_path, expected_hash in declared.items():
        path = packet_root / relative_path
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"packet manifest entry is not a regular file: {relative_path}")
        if _sha256_bytes(path.read_bytes()) != expected_hash:
            raise ValueError(f"packet manifest hash mismatch: {relative_path}")
    _, build_raw = _load_json_file(packet_root / "build.json", "packet build")
    try:
        build = KnowledgeReviewPacketBuild.model_validate_json(build_raw)
    except ValidationError as exc:
        raise ValueError(f"invalid packet build: {exc}") from exc
    if manifest.build_id != build.build_id or manifest.distribution != build.distribution:
        raise ValueError("packet manifest provenance does not match build")
    if build.distribution != "external_model":
        raise ValueError("Grok campaign requires an external_model packet build")
    prompt = _read_regular_text(packet_root / "prompt.md", "campaign prompt").strip()
    if not prompt or _sha256_text(prompt) != build.prompt_hash:
        raise ValueError("campaign prompt hash does not match packet build")
    schema_payload, _ = _load_json_file(
        packet_root / "grok-review-output.schema.json",
        "campaign output schema",
    )
    if not isinstance(schema_payload, dict):
        raise ValueError("campaign output schema must be a JSON object")
    packets: dict[str, KnowledgeReviewPacket] = {}
    packet_raws: dict[str, str] = {}
    for path in sorted(packet_root.glob("packet-*.json")):
        _, raw = _load_json_file(path, "campaign packet")
        try:
            packet = KnowledgeReviewPacket.model_validate_json(raw)
        except ValidationError as exc:
            raise ValueError(f"invalid campaign packet {path.name}: {exc}") from exc
        digest_match = _PACKET_ID_RE.fullmatch(packet.packet_id)
        if digest_match is None or not path.name.endswith(f"-{digest_match.group(1)[:12]}.json"):
            raise ValueError(f"campaign packet filename does not match packet_id: {path.name}")
        if packet.packet_id in packets:
            raise ValueError("campaign packet IDs must be unique")
        packets[packet.packet_id] = packet
        packet_raws[packet.packet_id] = raw
    embedded = {item.packet_id: item for item in build.packets}
    if set(packets) != set(embedded):
        raise ValueError("campaign packet files do not match packet build")
    if any(packets[packet_id] != embedded[packet_id] for packet_id in packets):
        raise ValueError("campaign packet content does not match packet build")
    return build, manifest, manifest_raw, packets, packet_raws


def _round_directories(campaign_base: Path, round_prefix: str) -> tuple[Path, ...]:
    if not _ROUND_PREFIX_RE.fullmatch(round_prefix):
        raise ValueError("round_prefix must use lowercase kebab-case")
    if campaign_base.is_symlink() or not campaign_base.is_dir():
        raise ValueError("campaign base must be a regular non-symlink directory")
    rounds = tuple(
        sorted(
            (
                item
                for item in campaign_base.iterdir()
                if item.is_dir()
                and (item.name == round_prefix or item.name.startswith(f"{round_prefix}-"))
            ),
            key=lambda item: item.name,
        )
    )
    if not rounds:
        raise ValueError(f"campaign contains no rounds matching prefix: {round_prefix}")
    if any(item.is_symlink() for item in rounds):
        raise ValueError("campaign rounds must not use symlinks")
    return rounds


def _packet_id_from_stem(stem: str) -> str:
    if not re.fullmatch(r"review-[0-9a-f]{64}", stem):
        raise ValueError(f"invalid campaign review artifact name: {stem}")
    return f"knowledge-review-packet:sha256:{stem.removeprefix('review-')}"


def _wrapper_fields(
    raw_response: str,
    *,
    context: str,
) -> tuple[dict[str, object], dict[str, object], CampaignUsage, str, str, str]:
    payload = _load_json(raw_response, context)
    if not isinstance(payload, dict):
        raise ValueError(f"{context} must be a JSON object")
    structured = payload.get("structuredOutput")
    text = payload.get("text")
    request_id = payload.get("requestId")
    session_id = payload.get("sessionId")
    stop_reason = payload.get("stopReason")
    if not isinstance(structured, dict) or not isinstance(text, str):
        raise ValueError(f"{context} is missing structured/text output")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError(f"{context} is missing requestId")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError(f"{context} is missing sessionId")
    if not isinstance(stop_reason, str) or not stop_reason:
        raise ValueError(f"{context} is missing stopReason")
    text_payload = _load_json(text, f"{context} text")
    if text_payload != structured:
        raise ValueError(f"{context} structuredOutput and text do not match")
    try:
        usage = CampaignUsage.model_validate(payload.get("usage"))
    except ValidationError as exc:
        raise ValueError(f"invalid {context} usage: {exc}") from exc
    return payload, structured, usage, request_id, session_id, stop_reason


def _outer_wrapper_metadata(
    raw_response: str,
    *,
    context: str,
) -> tuple[dict[str, object], str | None, str | None, CampaignUsage | None]:
    payload = _load_json(raw_response, context)
    if not isinstance(payload, dict):
        raise ValueError(f"{context} must be a JSON object")
    request_value = payload.get("requestId")
    session_value = payload.get("sessionId")
    request_id = (
        request_value if isinstance(request_value, str) and request_value else None
    )
    session_id = (
        session_value if isinstance(session_value, str) and session_value else None
    )
    usage: CampaignUsage | None = None
    if payload.get("usage") is not None:
        try:
            usage = CampaignUsage.model_validate(payload.get("usage"))
        except ValidationError:
            usage = None
    return payload, request_id, session_id, usage


def _artifact_ref(path: Path, campaign_base: Path) -> CampaignArtifactRef:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"campaign artifact must be a regular file: {path}")
    return CampaignArtifactRef(
        relative_path=path.relative_to(campaign_base).as_posix(),
        content_hash=_sha256_bytes(path.read_bytes()),
    )


def _validate_selected_receipt(
    *,
    receipt_path: Path,
    round_name: str,
    packet: KnowledgeReviewPacket,
    packet_raw: str,
    campaign_base: Path,
) -> tuple[
    SelectedCampaignReview,
    KnowledgeModelReview,
    str,
    str,
]:
    receipt_payload, _ = _load_json_file(receipt_path, "campaign receipt")
    try:
        receipt = GrokReviewReceipt.model_validate(receipt_payload)
    except ValidationError as exc:
        raise ValueError(f"invalid campaign receipt {receipt_path.name}: {exc}") from exc
    stem = receipt_path.name.removesuffix(".receipt.json")
    if _packet_id_from_stem(stem) != receipt.packet_id:
        raise ValueError("campaign receipt filename does not match packet_id")
    if receipt.packet_id != packet.packet_id:
        raise ValueError("campaign receipt packet_id does not match selected packet")
    review_path = receipt_path.with_name(f"{stem}.review.json")
    raw_path = receipt_path.with_name(f"{stem}.raw.json")
    review_payload, review_raw = _load_json_file(review_path, "campaign review")
    _, raw_file = _load_json_file(raw_path, "campaign raw response")
    raw_response = raw_file.strip()
    _, structured, usage, request_id, session_id, stop_reason = _wrapper_fields(
        raw_response,
        context="campaign raw response",
    )
    if stop_reason != "EndTurn":
        raise ValueError("selected campaign raw response stopReason must be EndTurn")
    validated_stop_reason: Literal["EndTurn"] = "EndTurn"
    if review_payload != structured:
        raise ValueError("campaign raw structuredOutput does not match review file")
    if receipt.packet_hash != _sha256_text(packet_raw):
        raise ValueError("campaign receipt packet_hash mismatch")
    if receipt.review_hash != _sha256_text(review_raw):
        raise ValueError("campaign receipt review_hash mismatch")
    if receipt.raw_response_hash != _sha256_text(raw_response):
        raise ValueError("campaign receipt raw_response_hash mismatch")
    try:
        review = load_and_validate_knowledge_model_review(review_raw, packet=packet)
    except ValueError as exc:
        raise ValueError(f"campaign review validator rejected {receipt_path.name}: {exc}") from exc
    expected = {
        "provider": packet.model_provider,
        "model": packet.model_name,
        "prompt_version": packet.prompt_version,
        "prompt_hash": packet.prompt_hash,
        "request_id": request_id,
        "session_id": session_id,
        "stop_reason": stop_reason,
        "usage": usage,
        "packet_decision": review.packet_decision,
        "summary": review.summary,
    }
    actual = {
        "provider": receipt.provider,
        "model": receipt.model,
        "prompt_version": receipt.prompt_version,
        "prompt_hash": receipt.prompt_hash,
        "request_id": receipt.request_id,
        "session_id": receipt.session_id,
        "stop_reason": receipt.stop_reason,
        "usage": receipt.usage,
        "packet_decision": receipt.packet_decision,
        "summary": receipt.summary,
    }
    if actual != expected:
        mismatches = sorted(key for key in expected if actual[key] != expected[key])
        raise ValueError(f"campaign receipt metadata mismatch: {mismatches}")
    selected = SelectedCampaignReview(
        packet_id=packet.packet_id,
        round_name=round_name,
        receipt_file=receipt_path.relative_to(campaign_base).as_posix(),
        packet_file_hash=_sha256_bytes(packet_raw.encode("utf-8")),
        review_file_hash=_sha256_bytes(review_path.read_bytes()),
        raw_file_hash=_sha256_bytes(raw_path.read_bytes()),
        receipt_file_hash=_sha256_bytes(receipt_path.read_bytes()),
        request_id=request_id,
        session_id=session_id,
        stop_reason=validated_stop_reason,
        packet_decision=review.packet_decision,
        summary=review.summary,
        usage=usage,
        global_findings=CampaignGlobalFindings(
            missing_clauses=len(review.missing_clauses),
            duplicate_groups=len(review.duplicate_groups),
            conflicts=len(review.conflicts),
        ),
    )
    return selected, review, request_id, session_id


def _failed_attempts(
    *,
    rounds: tuple[Path, ...],
    selected_stems: set[tuple[str, str]],
    packets: dict[str, KnowledgeReviewPacket],
    campaign_base: Path,
) -> tuple[list[CampaignFailedAttempt], list[str], list[str]]:
    failures: list[CampaignFailedAttempt] = []
    request_ids: list[str] = []
    session_ids: list[str] = []
    for round_path in rounds:
        raw_paths = {
            path.name.removesuffix(".raw.json"): path
            for path in round_path.glob("*.raw.json")
        }
        review_paths = {
            path.name.removesuffix(".review.json"): path
            for path in round_path.glob("*.review.json")
        }
        orphan_stems = sorted((set(raw_paths) | set(review_paths)) - {
            stem for round_name, stem in selected_stems if round_name == round_path.name
        })
        for stem in orphan_stems:
            packet_id = _packet_id_from_stem(stem)
            if packet_id not in packets:
                raise ValueError("failed campaign attempt references an unknown packet")
            raw_path = raw_paths.get(stem)
            review_path = review_paths.get(stem)
            artifact_paths = sorted(
                (path for path in (raw_path, review_path) if path is not None),
                key=lambda path: path.relative_to(campaign_base).as_posix(),
            )
            artifacts = tuple(
                _artifact_ref(path, campaign_base) for path in artifact_paths
            )
            structured_response = False
            usage: CampaignUsage | None = None
            request_id: str | None = None
            session_id: str | None = None
            raw_response: str | None = None
            outer_error: ValueError | None = None
            if raw_path is not None:
                try:
                    _, raw_file = _load_json_file(
                        raw_path,
                        "failed campaign raw response",
                    )
                    raw_response = raw_file.strip()
                    _, request_id, session_id, usage = _outer_wrapper_metadata(
                        raw_response,
                        context="failed campaign raw response",
                    )
                    structured_response = True
                    if request_id is not None:
                        request_ids.append(request_id)
                    if session_id is not None:
                        session_ids.append(session_id)
                except ValueError as exc:
                    outer_error = exc
            if raw_path is None or review_path is None:
                failures.append(
                    CampaignFailedAttempt(
                        round_name=round_path.name,
                        packet_id=packet_id,
                        failure_kind="incomplete_artifact",
                        message=(
                            str(outer_error)
                            if outer_error is not None
                            else "failed attempt is missing raw or review artifact"
                        ),
                        request_id=request_id,
                        session_id=session_id,
                        usage=usage,
                        structured_response=structured_response,
                        artifacts=artifacts,
                    )
                )
                continue
            if outer_error is not None or raw_response is None:
                failures.append(
                    CampaignFailedAttempt(
                        round_name=round_path.name,
                        packet_id=packet_id,
                        failure_kind="wrapper_error",
                        message=str(outer_error),
                        structured_response=False,
                        artifacts=artifacts,
                    )
                )
                continue
            try:
                review_payload, review_raw = _load_json_file(
                    review_path,
                    "failed campaign review",
                )
                (
                    _,
                    structured,
                    strict_usage,
                    strict_request_id,
                    strict_session_id,
                    _,
                ) = _wrapper_fields(
                    raw_response,
                    context="failed campaign raw response",
                )
                if (
                    strict_usage != usage
                    or strict_request_id != request_id
                    or strict_session_id != session_id
                ):
                    raise ValueError("failed wrapper metadata changed during validation")
                if structured != review_payload:
                    raise ValueError("raw structuredOutput does not match review file")
            except ValueError as exc:
                failures.append(
                    CampaignFailedAttempt(
                        round_name=round_path.name,
                        packet_id=packet_id,
                        failure_kind="wrapper_error",
                        message=str(exc),
                        request_id=request_id,
                        session_id=session_id,
                        usage=usage,
                        structured_response=True,
                        artifacts=artifacts,
                    )
                )
                continue
            try:
                load_and_validate_knowledge_model_review(
                    review_raw,
                    packet=packets[packet_id],
                )
            except ValueError as exc:
                failures.append(
                    CampaignFailedAttempt(
                        round_name=round_path.name,
                        packet_id=packet_id,
                        failure_kind="validation_error",
                        message=str(exc),
                        request_id=request_id,
                        session_id=session_id,
                        usage=usage,
                        structured_response=True,
                        artifacts=artifacts,
                    )
                )
            else:
                raise ValueError(
                    "campaign contains a validator-accepted response without a receipt"
                )
        failure_stems = sorted(
            {
                path.name.split(".failed.", 1)[0]
                for path in round_path.glob("*.failed.*.txt")
            }
        )
        for stem in failure_stems:
            packet_id = _packet_id_from_stem(stem)
            if packet_id not in packets:
                raise ValueError("transport failure references an unknown packet")
            stderr_path = round_path / f"{stem}.failed.stderr.txt"
            stdout_path = round_path / f"{stem}.failed.stdout.txt"
            message_path = stderr_path if stderr_path.is_file() else stdout_path
            message = _read_regular_text(message_path, "campaign failure log").strip()
            if not message:
                message = "Grok invocation failed without a diagnostic"
            failures.append(
                CampaignFailedAttempt(
                    round_name=round_path.name,
                    packet_id=packet_id,
                    failure_kind="transport_error",
                    message=message,
                    structured_response=False,
                    artifacts=tuple(
                        _artifact_ref(path, campaign_base)
                        for path in sorted(
                            (
                                path
                                for path in (stderr_path, stdout_path)
                                if path.is_file()
                            ),
                            key=lambda path: path.relative_to(
                                campaign_base
                            ).as_posix(),
                        )
                    ),
                )
            )
    return failures, request_ids, session_ids


def summarize_knowledge_grok_campaign(
    *,
    packet_root: str | Path,
    campaign_base: str | Path,
    round_prefix: str,
) -> KnowledgeGrokCampaignSummary:
    packet_root_path = Path(packet_root)
    campaign_base_path = Path(campaign_base)
    build, _, _, packets, packet_raws = _load_packet_artifacts(
        packet_root_path
    )
    manifest_hash = _sha256_bytes((packet_root_path / "manifest.json").read_bytes())
    rounds = _round_directories(campaign_base_path, round_prefix)
    receipt_paths: dict[str, list[tuple[Path, Path]]] = {}
    for round_path in rounds:
        for receipt_path in sorted(round_path.glob("*.receipt.json")):
            if receipt_path.is_symlink() or not receipt_path.is_file():
                raise ValueError("campaign receipts must be regular non-symlink files")
            stem = receipt_path.name.removesuffix(".receipt.json")
            packet_id = _packet_id_from_stem(stem)
            receipt_paths.setdefault(packet_id, []).append((round_path, receipt_path))
    unknown = sorted(set(receipt_paths) - set(packets))
    if unknown:
        raise ValueError(f"campaign receipts reference unknown packets: {unknown}")
    missing = sorted(set(packets) - set(receipt_paths))
    duplicate = sorted(
        packet_id for packet_id, paths in receipt_paths.items() if len(paths) != 1
    )
    if missing or duplicate:
        raise ValueError(
            "campaign must select exactly one validated receipt per packet: "
            f"missing={missing}, duplicate={duplicate}"
        )
    selected: list[SelectedCampaignReview] = []
    reviews: list[KnowledgeModelReview] = []
    selected_request_ids: list[str] = []
    selected_session_ids: list[str] = []
    selected_stems: set[tuple[str, str]] = set()
    for packet_id in sorted(packets):
        round_path, receipt_path = receipt_paths[packet_id][0]
        item, review, request_id, session_id = _validate_selected_receipt(
            receipt_path=receipt_path,
            round_name=round_path.name,
            packet=packets[packet_id],
            packet_raw=packet_raws[packet_id],
            campaign_base=campaign_base_path,
        )
        selected.append(item)
        reviews.append(review)
        selected_request_ids.append(request_id)
        selected_session_ids.append(session_id)
        selected_stems.add(
            (round_path.name, receipt_path.name.removesuffix(".receipt.json"))
        )
    failed, failed_request_ids, failed_session_ids = _failed_attempts(
        rounds=rounds,
        selected_stems=selected_stems,
        packets=packets,
        campaign_base=campaign_base_path,
    )
    all_request_ids = [*selected_request_ids, *failed_request_ids]
    all_session_ids = [*selected_session_ids, *failed_session_ids]
    if len(all_request_ids) != len(set(all_request_ids)):
        raise ValueError("campaign request_id values must be globally unique")
    if len(all_session_ids) != len(set(all_session_ids)):
        raise ValueError("campaign session_id values must be globally unique")
    packet_counts = Counter(item.packet_decision for item in selected)
    clause_decisions = ModelReviewSummary(
        accepted=sum(item.summary.accepted for item in selected),
        rejected=sum(item.summary.rejected for item in selected),
        uncertain=sum(item.summary.uncertain for item in selected),
        with_corrections=sum(item.summary.with_corrections for item in selected),
    )
    global_findings = CampaignGlobalFindings(
        missing_clauses=sum(len(item.missing_clauses) for item in reviews),
        duplicate_groups=sum(len(item.duplicate_groups) for item in reviews),
        conflicts=sum(len(item.conflicts) for item in reviews),
    )
    valid_usage = _sum_usage([item.usage for item in selected])
    failed_usage = _sum_usage(
        [item.usage for item in failed if item.usage is not None]
    )
    all_usage = _sum_usage([valid_usage, failed_usage])
    ordered_failures = tuple(
        sorted(
            failed,
            key=lambda item: (
                item.round_name,
                item.packet_id,
                item.failure_kind,
                item.message,
            ),
        )
    )
    structured_attempt_count = len(selected) + sum(
        item.structured_response for item in ordered_failures
    )
    transport_failure_count = sum(
        item.failure_kind == "transport_error" for item in ordered_failures
    )
    payload = {
        "packet_build_id": build.build_id,
        "packet_manifest_hash": manifest_hash,
        "round_prefix": round_prefix,
        "round_names": tuple(item.name for item in rounds),
        "packet_count": len(packets),
        "selected_receipts": [item.model_dump(mode="json") for item in selected],
        "failed_attempts": [item.model_dump(mode="json") for item in ordered_failures],
        "packet_decisions": {
            "accept": packet_counts["accept"],
            "reject": packet_counts["reject"],
            "uncertain": packet_counts["uncertain"],
        },
        "clause_decisions": clause_decisions.model_dump(mode="json"),
        "global_findings": global_findings.model_dump(mode="json"),
        "valid_usage": valid_usage.model_dump(mode="json"),
        "failed_structured_usage": failed_usage.model_dump(mode="json"),
        "all_structured_usage": all_usage.model_dump(mode="json"),
        "structured_attempt_count": structured_attempt_count,
        "transport_failure_count": transport_failure_count,
    }
    return KnowledgeGrokCampaignSummary(
        summary_id=_canonical_hash("knowledge-grok-campaign", payload),
        packet_build_id=build.build_id,
        packet_manifest_hash=manifest_hash,
        round_prefix=round_prefix,
        round_names=tuple(item.name for item in rounds),
        packet_count=len(packets),
        selected_receipts=tuple(selected),
        failed_attempts=ordered_failures,
        packet_decisions=CampaignPacketDecisionTotals(
            accept=packet_counts["accept"],
            reject=packet_counts["reject"],
            uncertain=packet_counts["uncertain"],
        ),
        clause_decisions=clause_decisions,
        global_findings=global_findings,
        valid_usage=valid_usage,
        failed_structured_usage=failed_usage,
        all_structured_usage=all_usage,
        structured_attempt_count=structured_attempt_count,
        transport_failure_count=transport_failure_count,
    )


type LoadedSelectedCampaignReview = tuple[
    SelectedCampaignReview,
    KnowledgeReviewPacket,
    KnowledgeModelReview,
]


def load_selected_knowledge_grok_campaign_reviews(
    *,
    packet_root: str | Path,
    campaign_base: str | Path,
    summary: KnowledgeGrokCampaignSummary,
) -> tuple[LoadedSelectedCampaignReview, ...]:
    """Replay every selected receipt and return immutable validated review triples."""

    def read_captured_bytes(path: Path, context: str) -> bytes:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ValueError(f"cannot open {context}: {path}") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError(f"{context} must be a regular file: {path}")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        except OSError as exc:
            raise ValueError(f"cannot read {context}: {path}") from exc
        finally:
            os.close(descriptor)

    def decode_captured(value: bytes, context: str) -> str:
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{context} must use UTF-8") from exc

    packet_root_path = Path(packet_root)
    campaign_base_path = Path(campaign_base)
    replayed_summary = summarize_knowledge_grok_campaign(
        packet_root=packet_root_path,
        campaign_base=campaign_base_path,
        round_prefix=summary.round_prefix,
    )
    if replayed_summary != summary:
        raise ValueError("campaign summary changed before selected review replay")

    build, _, _, packets, packet_raws = _load_packet_artifacts(packet_root_path)
    manifest_hash = _sha256_bytes((packet_root_path / "manifest.json").read_bytes())
    if (
        build.build_id != summary.packet_build_id
        or manifest_hash != summary.packet_manifest_hash
    ):
        raise ValueError("campaign packet provenance changed before review replay")
    selected_ids = tuple(item.packet_id for item in summary.selected_receipts)
    if selected_ids != tuple(sorted(packets)):
        raise ValueError("campaign selected reviews do not cover the packet build")

    loaded: list[LoadedSelectedCampaignReview] = []
    for selected in summary.selected_receipts:
        relative_receipt = PurePosixPath(selected.receipt_file)
        expected_name = (
            "review-"
            f"{selected.packet_id.removeprefix('knowledge-review-packet:sha256:')}"
            ".receipt.json"
        )
        if (
            relative_receipt.parent != PurePosixPath(selected.round_name)
            or relative_receipt.name != expected_name
        ):
            raise ValueError("campaign selected receipt path does not match its identity")
        round_path = campaign_base_path / selected.round_name
        if round_path.is_symlink() or not round_path.is_dir():
            raise ValueError("campaign selected round must be a non-symlink directory")
        receipt_path = campaign_base_path / selected.receipt_file
        replayed, review, _, _ = _validate_selected_receipt(
            receipt_path=receipt_path,
            round_name=selected.round_name,
            packet=packets[selected.packet_id],
            packet_raw=packet_raws[selected.packet_id],
            campaign_base=campaign_base_path,
        )
        if replayed != selected:
            raise ValueError("campaign selected receipt changed during review replay")

        stem = receipt_path.name.removesuffix(".receipt.json")
        review_path = receipt_path.with_name(f"{stem}.review.json")
        raw_path = receipt_path.with_name(f"{stem}.raw.json")
        receipt_bytes = read_captured_bytes(receipt_path, "campaign receipt")
        review_bytes = read_captured_bytes(review_path, "campaign review")
        raw_bytes = read_captured_bytes(raw_path, "campaign raw response")
        captured_hashes = (
            _sha256_bytes(receipt_bytes),
            _sha256_bytes(review_bytes),
            _sha256_bytes(raw_bytes),
        )
        expected_hashes = (
            selected.receipt_file_hash,
            selected.review_file_hash,
            selected.raw_file_hash,
        )
        if captured_hashes != expected_hashes:
            raise ValueError("campaign selected artifacts changed during final capture")

        receipt_text = decode_captured(receipt_bytes, "campaign receipt")
        review_text = decode_captured(review_bytes, "campaign review")
        raw_response = decode_captured(raw_bytes, "campaign raw response").strip()
        receipt_payload = _load_json(receipt_text, "campaign receipt")
        review_payload = _load_json(review_text, "campaign review")
        try:
            receipt = GrokReviewReceipt.model_validate(receipt_payload)
        except ValidationError as exc:
            raise ValueError(
                f"invalid campaign receipt {receipt_path.name}: {exc}"
            ) from exc
        _, structured, usage, request_id, session_id, stop_reason = _wrapper_fields(
            raw_response,
            context="campaign raw response",
        )
        if review_payload != structured:
            raise ValueError(
                "campaign raw structuredOutput does not match captured review file"
            )
        if (
            receipt.packet_hash != selected.packet_file_hash
            or receipt.review_hash != _sha256_bytes(review_bytes)
            or receipt.raw_response_hash != _sha256_text(raw_response)
        ):
            raise ValueError("captured campaign receipt hashes do not match artifacts")
        try:
            captured_review = load_and_validate_knowledge_model_review(
                review_bytes,
                packet=packets[selected.packet_id],
            )
        except ValueError as exc:
            raise ValueError(
                f"captured campaign review validator rejected {receipt_path.name}: {exc}"
            ) from exc
        captured_metadata = (
            receipt.packet_id,
            receipt.provider,
            receipt.model,
            receipt.prompt_version,
            receipt.prompt_hash,
            receipt.request_id,
            receipt.session_id,
            receipt.stop_reason,
            receipt.usage,
            receipt.packet_decision,
            receipt.summary,
        )
        expected_metadata = (
            selected.packet_id,
            packets[selected.packet_id].model_provider,
            packets[selected.packet_id].model_name,
            packets[selected.packet_id].prompt_version,
            packets[selected.packet_id].prompt_hash,
            request_id,
            session_id,
            stop_reason,
            usage,
            captured_review.packet_decision,
            captured_review.summary,
        )
        if captured_metadata != expected_metadata:
            raise ValueError("captured campaign receipt metadata does not match review")
        selected_metadata = (
            selected.request_id,
            selected.session_id,
            selected.stop_reason,
            selected.packet_decision,
            selected.summary,
            selected.usage,
            selected.global_findings,
        )
        expected_selected_metadata = (
            request_id,
            session_id,
            stop_reason,
            captured_review.packet_decision,
            captured_review.summary,
            usage,
            CampaignGlobalFindings(
                missing_clauses=len(captured_review.missing_clauses),
                duplicate_groups=len(captured_review.duplicate_groups),
                conflicts=len(captured_review.conflicts),
            ),
        )
        if selected_metadata != expected_selected_metadata or captured_review != review:
            raise ValueError("campaign selected review metadata changed during replay")
        loaded.append((selected, packets[selected.packet_id], captured_review))
    return tuple(loaded)


__all__ = [
    "CAMPAIGN_SUMMARY_SCHEMA_VERSION",
    "CampaignArtifactRef",
    "CampaignFailedAttempt",
    "CampaignGlobalFindings",
    "CampaignPacketDecisionTotals",
    "CampaignUsage",
    "GrokReviewReceipt",
    "KnowledgeGrokCampaignSummary",
    "LoadedSelectedCampaignReview",
    "SelectedCampaignReview",
    "load_selected_knowledge_grok_campaign_reviews",
    "summarize_knowledge_grok_campaign",
]
