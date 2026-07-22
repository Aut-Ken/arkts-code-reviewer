from __future__ import annotations

import argparse
import builtins
import hashlib
import os
import signal
import stat
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Annotated, Literal, Protocol, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekCredentialProvider,
    DeepSeekCredentialUnavailableError,
    DeepSeekHttpResponse,
    EnvironmentDeepSeekCredentialProvider,
)
from arkts_code_reviewer.knowledge.adapters import (
    GitObjectReader,
    OpenHarmonyDocsAdapter,
    SourceObject,
)
from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
    canonical_json,
    load_json_model,
    sha256_text,
)
from arkts_code_reviewer.knowledge.document_first.enrichment import (
    DocumentCardDispatchPlan,
    DocumentCardPromptAsset,
    DocumentCardRequest,
    build_document_card_dispatch_plan,
    build_document_card_request,
    load_document_card_prompt,
    verify_document_card_dispatch_plan,
)
from arkts_code_reviewer.knowledge.document_first.export_policy import (
    DocumentCardExportPolicy,
    load_document_card_export_policy,
)
from arkts_code_reviewer.knowledge.document_first.models import (
    DocumentCard,
    DocumentCardDraft,
    MarkdownDocumentMap,
    load_document_card_draft,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    build_document_card,
    build_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument
from arkts_code_reviewer.knowledge.registry import (
    DEFAULT_SOURCE_REGISTRY,
    SourceRegistry,
    VerifiedSource,
    build_source_bundle,
    load_source_registry,
)
from arkts_code_reviewer.knowledge.seed import (
    DEFAULT_KNOWLEDGE_SEED,
    KnowledgeSeed,
    load_knowledge_seed,
)

DOCUMENT_CARD_SMOKE_SOURCE_ID = "openharmony-docs"
DOCUMENT_CARD_SMOKE_RELATIVE_PATH = "zh-cn/application-dev/arkts-utils/taskpool-vs-worker.md"
DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT = "YES_DEEPSEEK_DOCUMENT_CARD_EXACT_BODY"
DOCUMENT_CARD_LIVE_RECEIPT_SCHEMA_VERSION = "document-card-live-receipt-v1"
DOCUMENT_CARD_INSPECTION_SCHEMA_VERSION = "document-card-inspection-v1"

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DOCUMENT_CARD_OUTPUT_ROOT = _REPO_ROOT / "E2E_test_example_3_document_card" / "artifacts"

_HASH = r"[0-9a-f]{64}"
_SHA256 = rf"^sha256:{_HASH}$"
_PLAN_ID = rf"^document-card-plan:sha256:{_HASH}$"
_REQUEST_ID = rf"^document-card-request:sha256:{_HASH}$"
_MAP_ID = rf"^markdown-document-map:sha256:{_HASH}$"
_CARD_ID = rf"^document-card:sha256:{_HASH}$"
_RECEIPT_ID = rf"^document-card-live-receipt:sha256:{_HASH}$"

DocumentCardRunStatus = Literal[
    "valid_card",
    "transport_error",
    "provider_http_error",
    "provider_response_invalid",
    "document_card_invalid",
]


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


class _ProviderCompletionTokenDetails(FrozenModel):
    reasoning_tokens: Annotated[int | None, Field(ge=0)] = None


class DocumentCardProviderUsage(FrozenModel):
    model_config = ConfigDict(
        extra="allow",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    completion_tokens: Annotated[int, Field(ge=0)]
    prompt_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    prompt_cache_hit_tokens: Annotated[int | None, Field(ge=0)] = None
    prompt_cache_miss_tokens: Annotated[int | None, Field(ge=0)] = None
    completion_tokens_details: _ProviderCompletionTokenDetails | None = None

    @model_validator(mode="after")
    def validate_usage(self) -> Self:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("provider usage total does not match prompt plus completion")
        cache = (self.prompt_cache_hit_tokens, self.prompt_cache_miss_tokens)
        if (cache[0] is None) != (cache[1] is None):
            raise ValueError("provider cache token fields must appear together")
        if (
            cache[0] is not None
            and cache[1] is not None
            and cache[0] + cache[1] != self.prompt_tokens
        ):
            raise ValueError("provider cache tokens do not match prompt tokens")
        extras = self.__pydantic_extra__ or {}
        if len(extras) > 16:
            raise ValueError("provider usage contains too many extension fields")
        return self


class _ProviderAssistantMessage(FrozenModel):
    role: Literal["assistant"]
    content: Annotated[str | None, Field(max_length=2_000_000)]
    reasoning_content: Annotated[str | None, Field(max_length=2_000_000)] = None
    tool_calls: tuple[object, ...] | None = None

    @field_validator("tool_calls", mode="before")
    @classmethod
    def parse_tool_calls(cls, value: object) -> object:
        if value is None:
            return None
        return _sequence(value, "provider tool_calls")

    @model_validator(mode="after")
    def validate_disabled_features(self) -> Self:
        if self.reasoning_content not in (None, ""):
            raise ValueError("thinking-disabled response contains reasoning content")
        if self.tool_calls not in (None, ()):
            raise ValueError("tool-disabled response contains tool calls")
        if self.content is None or not self.content.strip():
            raise ValueError("provider response content is empty")
        return self


class _ProviderChoice(FrozenModel):
    finish_reason: Literal[
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "insufficient_system_resource",
    ]
    index: Literal[0]
    message: _ProviderAssistantMessage
    logprobs: None = None


class _ProviderChatCompletion(FrozenModel):
    id: Annotated[str, Field(min_length=1, max_length=500)]
    choices: Annotated[tuple[_ProviderChoice, ...], Field(min_length=1, max_length=1)]
    created: Annotated[int, Field(ge=0)]
    model: Literal["deepseek-v4-pro"]
    object: Literal["chat.completion"]
    system_fingerprint: Annotated[str | None, Field(max_length=500)] = None
    usage: DocumentCardProviderUsage | None = None

    @field_validator("choices", mode="before")
    @classmethod
    def parse_choices(
        cls,
        value: builtins.object,
    ) -> tuple[builtins.object, ...]:
        return _sequence(value, "provider choices")

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("provider response ID must be a trimmed single line")
        return value


class _DocumentCardLiveReceiptFields(FrozenModel):
    schema_version: Literal["document-card-live-receipt-v1"]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    document_map_id: Annotated[str, Field(pattern=_MAP_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    status: DocumentCardRunStatus
    network_attempted: Literal[True]
    attempt_count: Literal[1]
    retry_count: Literal[0]
    http_status: Annotated[int | None, Field(ge=100, le=599)]
    latency_ms: Annotated[int, Field(ge=0)]
    response_body_sha256: Annotated[str | None, Field(pattern=_SHA256)]
    response_body_size_bytes: Annotated[int | None, Field(ge=0, le=8_000_000)]
    provider_response_id: str | None
    provider_model: Literal["deepseek-v4-pro"] | None
    system_fingerprint: str | None
    finish_reason: str | None
    usage: DocumentCardProviderUsage | None
    draft_sha256: Annotated[str | None, Field(pattern=_SHA256)]
    card_id: Annotated[str | None, Field(pattern=_CARD_ID)]
    failure_code: (
        Literal[
            "transport_failed",
            "http_status_not_success",
            "outer_response_invalid",
            "finish_reason_not_stop",
            "usage_exceeds_reserved_output",
            "document_card_draft_invalid",
        ]
        | None
    )
    qualification: Literal["single_document_navigation_smoke_not_quality_evidence"]

    @model_validator(mode="after")
    def validate_status_shape(self) -> Self:
        if (self.response_body_sha256 is None) != (self.response_body_size_bytes is None):
            raise ValueError("response body hash and size must appear together")
        if self.status == "valid_card":
            if (
                self.http_status != 200
                or self.provider_response_id is None
                or self.provider_model is None
                or self.finish_reason != "stop"
                or self.draft_sha256 is None
                or self.card_id is None
                or self.failure_code is not None
            ):
                raise ValueError("valid Document Card receipt is incomplete")
        elif self.failure_code is None or self.card_id is not None:
            raise ValueError("failed Document Card receipt has an invalid status shape")
        return self


class _DocumentCardLiveReceiptPayload(_DocumentCardLiveReceiptFields):
    pass


class DocumentCardLiveReceipt(_DocumentCardLiveReceiptFields):
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]

    @model_validator(mode="after")
    def validate_receipt_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"receipt_id"})
        if self.receipt_id != canonical_hash("document-card-live-receipt", payload):
            raise ValueError("Document Card live receipt ID does not match")
        return self


class DocumentCardTransport(Protocol):
    def send(
        self,
        plan: DocumentCardDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse: ...


class HttpxDocumentCardTransport:
    def send(
        self,
        plan: DocumentCardDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        plan = DocumentCardDispatchPlan.model_validate(plan.model_dump(mode="json"))
        if not api_key or api_key != api_key.strip():
            raise ValueError("DeepSeek credential is invalid")
        started = time.monotonic_ns()
        try:
            import httpx
        except ImportError:
            raise RuntimeError("Document Card live transport requires httpx") from None
        try:
            timeout_seconds = plan.wall_clock_timeout_ms / 1_000
            with httpx.Client(
                verify=plan.tls_verify,
                follow_redirects=plan.follow_redirects,
                trust_env=plan.trust_env,
                timeout=httpx.Timeout(timeout_seconds),
            ) as client:
                with client.stream(
                    plan.http_method,
                    plan.endpoint_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    content=plan.wire_body_json.encode("utf-8"),
                ) as response:
                    body_parts: list[bytes] = []
                    body_size = 0
                    for part in response.iter_bytes():
                        body_size += len(part)
                        if body_size > plan.max_response_bytes:
                            raise ValueError("provider response exceeds byte budget")
                        body_parts.append(part)
                    return DeepSeekHttpResponse(
                        status_code=response.status_code,
                        body=b"".join(body_parts),
                        retry_after_ms=None,
                        latency_ms=max(0, (time.monotonic_ns() - started) // 1_000_000),
                    )
        except httpx.HTTPError as exc:
            raise RuntimeError("DeepSeek Document Card HTTP transport failed") from exc


@dataclass(frozen=True)
class DocumentCardSmokeBundle:
    registry: SourceRegistry
    policy: DocumentCardExportPolicy
    prompt: DocumentCardPromptAsset
    document: NormalizedDocument
    document_map: MarkdownDocumentMap
    request: DocumentCardRequest
    plan: DocumentCardDispatchPlan


@dataclass(frozen=True)
class DocumentCardRunArtifacts:
    receipt: DocumentCardLiveReceipt
    raw_response_body: bytes | None
    draft: DocumentCardDraft | None
    card: DocumentCard | None


class DocumentCardSmokeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DocumentCardDeadlineExceeded(TimeoutError):
    pass


@contextmanager
def _absolute_wall_clock_deadline(timeout_ms: int) -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread() or not hasattr(
        signal, "setitimer"
    ):
        raise DocumentCardSmokeError("wall_clock_deadline_unavailable")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise DocumentCardSmokeError("wall_clock_deadline_in_use")

    def raise_timeout(_signum: int, _frame: FrameType | None) -> None:
        raise _DocumentCardDeadlineExceeded("Document Card request exceeded its wall-clock budget")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_ms / 1_000)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _seal_receipt(payload: dict[str, object]) -> DocumentCardLiveReceipt:
    validated = _DocumentCardLiveReceiptPayload.model_validate(payload)
    sealed = validated.model_dump(mode="json")
    sealed["receipt_id"] = canonical_hash("document-card-live-receipt", sealed)
    return DocumentCardLiveReceipt.model_validate(sealed)


def _source_object(bundle: tuple[VerifiedSource, ...], seed: KnowledgeSeed) -> SourceObject:
    verified = bundle[0]
    source = verified.source
    matches = tuple(
        item
        for item in seed.documents
        if item.source_id == DOCUMENT_CARD_SMOKE_SOURCE_ID
        and item.relative_path == DOCUMENT_CARD_SMOKE_RELATIVE_PATH
    )
    if len(matches) != 1:
        raise ValueError("Document Card smoke source must occur once in Knowledge Seed")
    seed_document = matches[0]
    return SourceObject(
        source_id=source.id,
        revision=source.revision,
        relative_path=seed_document.relative_path,
        authority=source.governance.authority,
        domains=seed_document.domains,
        media_type="text/markdown",
    )


def build_document_card_smoke_bundle(
    *,
    registry_path: str | Path = DEFAULT_SOURCE_REGISTRY,
    seed_path: str | Path = DEFAULT_KNOWLEDGE_SEED,
    policy_path: str | Path | None = None,
    prompt_path: str | Path | None = None,
) -> DocumentCardSmokeBundle:
    registry = load_source_registry(registry_path)
    seed = load_knowledge_seed(seed_path)
    policy = load_document_card_export_policy(policy_path)
    prompt = load_document_card_prompt(prompt_path)
    _source_bundle, verified_sources = build_source_bundle(
        registry,
        (DOCUMENT_CARD_SMOKE_SOURCE_ID,),
    )
    source_object = _source_object(verified_sources, seed)
    verified_by_id = {item.source.id: item for item in verified_sources}
    document = OpenHarmonyDocsAdapter().load(
        source_object,
        GitObjectReader(verified_by_id),
    )
    document_map = build_markdown_document_map(document)
    request = build_document_card_request(
        document=document,
        document_map=document_map,
        registry=registry,
        policy=policy,
        prompt=prompt,
    )
    plan = build_document_card_dispatch_plan(
        document=document,
        document_map=document_map,
        request=request,
        registry=registry,
        policy=policy,
        prompt=prompt,
    )
    return DocumentCardSmokeBundle(
        registry=registry,
        policy=policy,
        prompt=prompt,
        document=document,
        document_map=document_map,
        request=request,
        plan=plan,
    )


def _verify_bundle(bundle: DocumentCardSmokeBundle) -> None:
    if not isinstance(bundle, DocumentCardSmokeBundle):
        raise DocumentCardSmokeError("bundle_not_trusted")
    verify_document_card_dispatch_plan(
        bundle.plan,
        document=bundle.document,
        document_map=bundle.document_map,
        request=bundle.request,
        registry=bundle.registry,
        policy=bundle.policy,
        prompt=bundle.prompt,
    )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        if written <= 0:
            raise OSError("atomic artifact write made no progress")
        offset += written


def _write_new_atomic(path: Path, content: bytes, *, exists_code: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    descriptor_open = True
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor_open = False
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except FileExistsError:
            raise DocumentCardSmokeError(exists_code) from None
        _fsync_directory(path.parent)
    finally:
        if descriptor_open:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        else:
            _fsync_directory(path.parent)


def _ensure_attempt_marker(state_dir: Path, plan: DocumentCardDispatchPlan) -> Path:
    if state_dir.exists():
        if state_dir.is_symlink() or not state_dir.is_dir():
            raise DocumentCardSmokeError("unsafe_state_directory")
    else:
        parent = state_dir.parent
        if not parent.is_dir() or parent.is_symlink():
            raise DocumentCardSmokeError("unsafe_state_parent")
        os.mkdir(state_dir, mode=0o700)
        _fsync_directory(parent)
    metadata = os.stat(state_dir, follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise DocumentCardSmokeError("unsafe_state_permissions")
    marker = state_dir / f"{plan.plan_id.rsplit(':', 1)[-1]}.consumed.json"
    data = canonical_json(
        {
            "schema_version": "document-card-attempt-consumption-v1",
            "plan_id": plan.plan_id,
            "wire_body_sha256": plan.wire_body_sha256,
            "max_output_tokens": plan.wire_payload.max_tokens,
            "qualification": "local_replay_guard_not_provider_or_cost_evidence",
        }
    ).encode("utf-8")
    _write_new_atomic(marker, data, exists_code="attempt_already_consumed")
    return marker


def _base_receipt_payload(
    plan: DocumentCardDispatchPlan,
    *,
    status: DocumentCardRunStatus,
    latency_ms: int,
) -> dict[str, object]:
    return {
        "schema_version": DOCUMENT_CARD_LIVE_RECEIPT_SCHEMA_VERSION,
        "plan_id": plan.plan_id,
        "request_id": plan.request_id,
        "document_map_id": plan.document_map_id,
        "wire_body_sha256": plan.wire_body_sha256,
        "status": status,
        "network_attempted": True,
        "attempt_count": 1,
        "retry_count": 0,
        "http_status": None,
        "latency_ms": latency_ms,
        "response_body_sha256": None,
        "response_body_size_bytes": None,
        "provider_response_id": None,
        "provider_model": None,
        "system_fingerprint": None,
        "finish_reason": None,
        "usage": None,
        "draft_sha256": None,
        "card_id": None,
        "failure_code": None,
        "qualification": "single_document_navigation_smoke_not_quality_evidence",
    }


def _interpret_provider_response(
    bundle: DocumentCardSmokeBundle,
    response: DeepSeekHttpResponse,
) -> DocumentCardRunArtifacts:
    plan = bundle.plan
    response_hash = "sha256:" + hashlib.sha256(response.body).hexdigest()
    common = {
        "http_status": response.status_code,
        "latency_ms": response.latency_ms,
        "response_body_sha256": response_hash,
        "response_body_size_bytes": len(response.body),
    }
    if response.status_code != 200:
        payload = _base_receipt_payload(
            plan,
            status="provider_http_error",
            latency_ms=response.latency_ms,
        )
        payload.update(common)
        payload["failure_code"] = "http_status_not_success"
        return DocumentCardRunArtifacts(
            receipt=_seal_receipt(payload),
            raw_response_body=response.body,
            draft=None,
            card=None,
        )

    try:
        completion = load_json_model(
            response.body,
            _ProviderChatCompletion,
            "DeepSeek Document Card response",
        )
    except (TypeError, ValueError):
        payload = _base_receipt_payload(
            plan,
            status="provider_response_invalid",
            latency_ms=response.latency_ms,
        )
        payload.update(common)
        payload["failure_code"] = "outer_response_invalid"
        return DocumentCardRunArtifacts(
            receipt=_seal_receipt(payload),
            raw_response_body=response.body,
            draft=None,
            card=None,
        )

    choice = completion.choices[0]
    provider_fields = {
        "provider_response_id": completion.id,
        "provider_model": completion.model,
        "system_fingerprint": completion.system_fingerprint,
        "finish_reason": choice.finish_reason,
        "usage": completion.usage,
    }
    if (
        completion.usage is not None
        and completion.usage.completion_tokens > plan.wire_payload.max_tokens
    ):
        payload = _base_receipt_payload(
            plan,
            status="provider_response_invalid",
            latency_ms=response.latency_ms,
        )
        payload.update(common)
        payload.update(provider_fields)
        payload["failure_code"] = "usage_exceeds_reserved_output"
        return DocumentCardRunArtifacts(
            receipt=_seal_receipt(payload),
            raw_response_body=response.body,
            draft=None,
            card=None,
        )
    if choice.finish_reason != "stop":
        payload = _base_receipt_payload(
            plan,
            status="provider_response_invalid",
            latency_ms=response.latency_ms,
        )
        payload.update(common)
        payload.update(provider_fields)
        payload["failure_code"] = "finish_reason_not_stop"
        return DocumentCardRunArtifacts(
            receipt=_seal_receipt(payload),
            raw_response_body=response.body,
            draft=None,
            card=None,
        )

    try:
        content = choice.message.content
        if content is None:
            raise ValueError("empty provider content")
        draft = load_document_card_draft(content)
        if len(draft.summary) > 500:
            raise ValueError("Document Card live summary exceeds prompt contract")
        card = build_document_card(bundle.document, bundle.document_map, draft)
    except (TypeError, ValueError):
        payload = _base_receipt_payload(
            plan,
            status="document_card_invalid",
            latency_ms=response.latency_ms,
        )
        payload.update(common)
        payload.update(provider_fields)
        payload["failure_code"] = "document_card_draft_invalid"
        return DocumentCardRunArtifacts(
            receipt=_seal_receipt(payload),
            raw_response_body=response.body,
            draft=None,
            card=None,
        )

    payload = _base_receipt_payload(
        plan,
        status="valid_card",
        latency_ms=response.latency_ms,
    )
    payload.update(common)
    payload.update(provider_fields)
    payload["draft_sha256"] = sha256_text(canonical_json(draft.model_dump(mode="json")))
    payload["card_id"] = card.card_id
    return DocumentCardRunArtifacts(
        receipt=_seal_receipt(payload),
        raw_response_body=response.body,
        draft=draft,
        card=card,
    )


def run_document_card_live_once(
    *,
    bundle: DocumentCardSmokeBundle,
    approved_plan_id: str,
    approved_wire_body_sha256: str,
    reserved_max_output_tokens: int,
    acknowledgement: str,
    state_dir: Path,
    credential_provider: DeepSeekCredentialProvider,
    transport: DocumentCardTransport | None = None,
) -> DocumentCardRunArtifacts:
    _verify_bundle(bundle)
    plan = bundle.plan
    if approved_plan_id != plan.plan_id:
        raise DocumentCardSmokeError("approved_plan_id_mismatch")
    if approved_wire_body_sha256 != plan.wire_body_sha256:
        raise DocumentCardSmokeError("approved_body_sha256_mismatch")
    if reserved_max_output_tokens != plan.wire_payload.max_tokens:
        raise DocumentCardSmokeError("reserved_output_tokens_mismatch")
    if acknowledgement != DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT:
        raise DocumentCardSmokeError("document_export_acknowledgement_missing")
    if not credential_provider.is_configured():
        raise DocumentCardSmokeError("credential_not_configured")
    try:
        api_key = credential_provider.get_api_key()
    except DeepSeekCredentialUnavailableError:
        raise DocumentCardSmokeError("credential_unavailable") from None
    _ensure_attempt_marker(state_dir, plan)

    fixed_transport = HttpxDocumentCardTransport() if transport is None else transport
    attempt_started = time.monotonic_ns()
    try:
        with _absolute_wall_clock_deadline(plan.wall_clock_timeout_ms):
            response = fixed_transport.send(plan, api_key=api_key)
    except (OSError, RuntimeError, ValueError):
        latency_ms = max(0, (time.monotonic_ns() - attempt_started) // 1_000_000)
        payload = _base_receipt_payload(
            plan,
            status="transport_error",
            latency_ms=latency_ms,
        )
        payload["failure_code"] = "transport_failed"
        return DocumentCardRunArtifacts(
            receipt=_seal_receipt(payload),
            raw_response_body=None,
            draft=None,
            card=None,
        )

    return _interpret_provider_response(bundle, response)


def _json_bytes(value: object) -> bytes:
    payload: object
    if isinstance(value, FrozenModel):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    import json

    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _write_or_verify(path: Path, content: bytes) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise DocumentCardSmokeError("artifact_collision")
        return
    try:
        _write_new_atomic(path, content, exists_code="artifact_collision")
    except DocumentCardSmokeError:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise


def _artifact_directory(output_root: Path, plan: DocumentCardDispatchPlan) -> Path:
    if output_root.exists():
        if output_root.is_symlink() or not output_root.is_dir():
            raise DocumentCardSmokeError("unsafe_output_root")
    else:
        output_root.mkdir(parents=True, mode=0o700)
    output_dir = output_root / plan.plan_id.rsplit(":", 1)[-1]
    if output_dir.exists():
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise DocumentCardSmokeError("unsafe_output_directory")
    else:
        os.mkdir(output_dir, mode=0o700)
    return output_dir


def materialize_document_card_inspection(
    bundle: DocumentCardSmokeBundle,
    *,
    output_root: Path,
) -> dict[str, object]:
    _verify_bundle(bundle)
    output_dir = _artifact_directory(output_root, bundle.plan)
    source_manifest = {
        "schema_version": "document-card-source-manifest-v1",
        "document_id": bundle.document.document_id,
        "source_ref": bundle.document.source_ref.model_dump(mode="json"),
        "normalized_body_hash": bundle.document_map.normalized_body_hash,
        "document_map_id": bundle.document_map.map_id,
        "request_id": bundle.request.request_id,
        "plan_id": bundle.plan.plan_id,
        "prompt_version": bundle.prompt.prompt_version,
        "prompt_hash": bundle.prompt.prompt_hash,
        "export_policy_fingerprint": bundle.policy.fingerprint,
        "qualification": "pinned_source_identity_not_publication_approval",
    }
    inspection = {
        "schema_version": DOCUMENT_CARD_INSPECTION_SCHEMA_VERSION,
        "mode": "inspect_only",
        "network_attempted": False,
        "document_id": bundle.document.document_id,
        "source_id": bundle.document.source_ref.source_id,
        "source_revision": bundle.document.source_ref.revision,
        "source_relative_path": bundle.document.source_ref.relative_path,
        "source_content_hash": bundle.document.source_ref.content_hash,
        "normalized_body_hash": bundle.document_map.normalized_body_hash,
        "document_map_id": bundle.document_map.map_id,
        "section_count": len(bundle.document_map.sections),
        "prompt_version": bundle.prompt.prompt_version,
        "prompt_hash": bundle.prompt.prompt_hash,
        "export_policy_fingerprint": bundle.policy.fingerprint,
        "request_id": bundle.request.request_id,
        "plan_id": bundle.plan.plan_id,
        "endpoint_url": bundle.plan.endpoint_url,
        "model": bundle.plan.wire_payload.model,
        "wire_body_sha256": bundle.plan.wire_body_sha256,
        "wire_body_size_bytes": bundle.plan.wire_body_size_bytes,
        "max_output_tokens": bundle.plan.wire_payload.max_tokens,
        "wall_clock_timeout_ms": bundle.plan.wall_clock_timeout_ms,
        "max_response_bytes": bundle.plan.max_response_bytes,
        "max_attempts": bundle.plan.max_attempts,
        "required_acknowledgement": DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT,
        "artifact_directory": str(output_dir),
        "qualification": "inspect_only_single_document_navigation_smoke_not_quality_evidence",
    }
    _write_or_verify(output_dir / "00_source-manifest.json", _json_bytes(source_manifest))
    _write_or_verify(output_dir / "01_source.md", bundle.document.body.encode("utf-8"))
    _write_or_verify(output_dir / "02_document-map.json", _json_bytes(bundle.document_map))
    _write_or_verify(output_dir / "03_request.json", _json_bytes(bundle.request))
    _write_or_verify(output_dir / "04_dispatch-plan.json", _json_bytes(bundle.plan))
    _write_or_verify(output_dir / "05_inspection.json", _json_bytes(inspection))
    return inspection


def _verify_run_artifacts(
    bundle: DocumentCardSmokeBundle,
    artifacts: DocumentCardRunArtifacts,
) -> None:
    _verify_bundle(bundle)
    try:
        receipt = DocumentCardLiveReceipt.model_validate(artifacts.receipt.model_dump(mode="json"))
    except (TypeError, ValueError):
        raise DocumentCardSmokeError("run_artifacts_receipt_invalid") from None
    if (
        receipt.plan_id != bundle.plan.plan_id
        or receipt.request_id != bundle.plan.request_id
        or receipt.document_map_id != bundle.plan.document_map_id
        or receipt.wire_body_sha256 != bundle.plan.wire_body_sha256
    ):
        raise DocumentCardSmokeError("run_artifacts_bundle_mismatch")

    raw_body = artifacts.raw_response_body
    if receipt.status == "transport_error":
        payload = _base_receipt_payload(
            bundle.plan,
            status="transport_error",
            latency_ms=receipt.latency_ms,
        )
        payload["failure_code"] = "transport_failed"
        expected = DocumentCardRunArtifacts(
            receipt=_seal_receipt(payload),
            raw_response_body=None,
            draft=None,
            card=None,
        )
    else:
        if raw_body is None or receipt.http_status is None:
            raise DocumentCardSmokeError("run_artifacts_provider_chain_mismatch")
        expected = _interpret_provider_response(
            bundle,
            DeepSeekHttpResponse(
                status_code=receipt.http_status,
                body=raw_body,
                retry_after_ms=None,
                latency_ms=receipt.latency_ms,
            ),
        )
    if artifacts != expected:
        raise DocumentCardSmokeError("run_artifacts_provider_chain_mismatch")


def materialize_document_card_run(
    bundle: DocumentCardSmokeBundle,
    artifacts: DocumentCardRunArtifacts,
    *,
    output_root: Path,
) -> Path:
    _verify_run_artifacts(bundle, artifacts)
    output_dir = _artifact_directory(output_root, bundle.plan)
    if artifacts.raw_response_body is not None:
        _write_or_verify(
            output_dir / "06_provider-response.raw.json",
            artifacts.raw_response_body,
        )
    if artifacts.draft is not None:
        _write_or_verify(output_dir / "07_document-card-draft.json", _json_bytes(artifacts.draft))
    if artifacts.card is not None:
        _write_or_verify(output_dir / "08_document-card.json", _json_bytes(artifacts.card))
    _write_or_verify(output_dir / "09_receipt.json", _json_bytes(artifacts.receipt))
    return output_dir


def _safe_error(code: str, *, attempted: bool | None) -> dict[str, object]:
    return {
        "schema_version": "document-card-smoke-error-v1",
        "network_attempted": attempted,
        "error_code": code,
        "qualification": "local_smoke_error_not_document_quality_evidence",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or run one controlled DeepSeek Document Card smoke"
    )
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--approve-plan-id")
    parser.add_argument("--approve-body-sha256")
    parser.add_argument("--reserve-max-output-tokens", type=int)
    parser.add_argument("--acknowledge-document-export")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_DOCUMENT_CARD_OUTPUT_ROOT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_SOURCE_REGISTRY)
    parser.add_argument("--seed", type=Path, default=DEFAULT_KNOWLEDGE_SEED)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--prompt", type=Path)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    credential_provider_factory: type[DeepSeekCredentialProvider] | None = None,
    test_transport: DocumentCardTransport | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        bundle = build_document_card_smoke_bundle(
            registry_path=args.registry,
            seed_path=args.seed,
            policy_path=args.policy,
            prompt_path=args.prompt,
        )
        inspection = materialize_document_card_inspection(
            bundle,
            output_root=args.output_root,
        )
    except (DocumentCardSmokeError, TypeError, ValueError) as exc:
        code = exc.code if isinstance(exc, DocumentCardSmokeError) else "inspect_build_invalid"
        print(canonical_json(_safe_error(code, attempted=False)))
        return 2
    if not args.execute_live:
        print(canonical_json(inspection))
        return 0
    required = (
        args.approve_plan_id,
        args.approve_body_sha256,
        args.reserve_max_output_tokens,
        args.acknowledge_document_export,
        args.state_dir,
    )
    if any(value is None for value in required):
        print(canonical_json(_safe_error("live_controls_incomplete", attempted=False)))
        return 2
    provider_factory = (
        EnvironmentDeepSeekCredentialProvider
        if credential_provider_factory is None
        else credential_provider_factory
    )
    try:
        credential_provider = provider_factory()
        run = run_document_card_live_once(
            bundle=bundle,
            approved_plan_id=args.approve_plan_id,
            approved_wire_body_sha256=args.approve_body_sha256,
            reserved_max_output_tokens=args.reserve_max_output_tokens,
            acknowledgement=args.acknowledge_document_export,
            state_dir=args.state_dir,
            credential_provider=credential_provider,
            transport=test_transport,
        )
    except DocumentCardSmokeError as exc:
        print(canonical_json(_safe_error(exc.code, attempted=False)))
        return 2
    except Exception:
        print(canonical_json(_safe_error("live_runtime_error", attempted=None)))
        return 3
    try:
        output_dir = materialize_document_card_run(
            bundle,
            run,
            output_root=args.output_root,
        )
    except DocumentCardSmokeError as exc:
        print(canonical_json(_safe_error(exc.code, attempted=True)))
        return 3
    except Exception:
        print(canonical_json(_safe_error("live_materialization_error", attempted=True)))
        return 3
    summary = {
        "schema_version": "document-card-smoke-summary-v1",
        "network_attempted": True,
        "status": run.receipt.status,
        "plan_id": bundle.plan.plan_id,
        "wire_body_sha256": bundle.plan.wire_body_sha256,
        "receipt_id": run.receipt.receipt_id,
        "card_id": run.receipt.card_id,
        "artifact_directory": str(output_dir),
        "qualification": "single_document_navigation_smoke_not_quality_evidence",
    }
    print(canonical_json(summary))
    return 0 if run.receipt.status == "valid_card" else 3


__all__ = [
    "DEFAULT_DOCUMENT_CARD_OUTPUT_ROOT",
    "DOCUMENT_CARD_INSPECTION_SCHEMA_VERSION",
    "DOCUMENT_CARD_LIVE_RECEIPT_SCHEMA_VERSION",
    "DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT",
    "DOCUMENT_CARD_SMOKE_RELATIVE_PATH",
    "DOCUMENT_CARD_SMOKE_SOURCE_ID",
    "DocumentCardLiveReceipt",
    "DocumentCardProviderUsage",
    "DocumentCardRunArtifacts",
    "DocumentCardSmokeBundle",
    "DocumentCardSmokeError",
    "DocumentCardTransport",
    "HttpxDocumentCardTransport",
    "build_document_card_smoke_bundle",
    "main",
    "materialize_document_card_inspection",
    "materialize_document_card_run",
    "run_document_card_live_once",
]
