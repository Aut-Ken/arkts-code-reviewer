from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
    canonical_json,
    load_json_model,
    sha256_text,
)
from arkts_code_reviewer.knowledge.document_first.export_policy import (
    DocumentCardExportPolicy,
)
from arkts_code_reviewer.knowledge.document_first.models import MarkdownDocumentMap
from arkts_code_reviewer.knowledge.document_first.structure import (
    verify_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument
from arkts_code_reviewer.knowledge.registry import (
    SourceRegistry,
    ingestion_path_allowed,
)

DOCUMENT_CARD_PROMPT_ASSET_SCHEMA_VERSION: Literal["document-card-prompt-asset-v1"] = (
    "document-card-prompt-asset-v1"
)
DOCUMENT_CARD_REQUEST_SCHEMA_VERSION: Literal["document-card-request-v1"] = (
    "document-card-request-v1"
)
DOCUMENT_CARD_WIRE_INPUT_SCHEMA_VERSION: Literal["document-card-wire-input-v1"] = (
    "document-card-wire-input-v1"
)
DOCUMENT_CARD_DISPATCH_PLAN_SCHEMA_VERSION: Literal["document-card-dispatch-plan-v1"] = (
    "document-card-dispatch-plan-v1"
)
DOCUMENT_CARD_REQUEST_BUILDER_VERSION: Literal["document-card-request-builder-v1"] = (
    "document-card-request-builder-v1"
)
DOCUMENT_CARD_WIRE_RENDERER_VERSION: Literal["document-card-wire-renderer-v1"] = (
    "document-card-wire-renderer-v1"
)
DOCUMENT_CARD_OUTPUT_CONTRACT_VERSION: Literal["document-card-draft-v1"] = "document-card-draft-v1"
DOCUMENT_CARD_PROMPT_VERSION: Literal["deepseek-document-card-v1"] = "deepseek-document-card-v1"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PACKAGED_DEFAULTS = Path(__file__).resolve().parent / "defaults"


def _default_asset_path(filename: str, source_relative_path: str) -> Path:
    packaged = _PACKAGED_DEFAULTS / filename
    if packaged.is_file():
        return packaged
    return _REPO_ROOT / source_relative_path


DEFAULT_DOCUMENT_CARD_PROMPT_PATH = _default_asset_path(
    "deepseek-document-card-v1.md",
    "prompts/knowledge/deepseek-document-card-v1.md",
)

_HASH = r"[0-9a-f]{64}"
_SHA256 = rf"^sha256:{_HASH}$"
_MAP_ID = rf"^markdown-document-map:sha256:{_HASH}$"
_SECTION_ID = rf"^document-section:sha256:{_HASH}$"
_POLICY_FINGERPRINT = rf"^document-card-export-policy:sha256:{_HASH}$"
_REQUEST_ID = rf"^document-card-request:sha256:{_HASH}$"
_PLAN_ID = rf"^document-card-plan:sha256:{_HASH}$"


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _identity_payload(model: FrozenModel, identity_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={identity_field})


def _seal[
    PayloadT: FrozenModel,
    SealedT: FrozenModel,
](
    payload: Mapping[str, object],
    *,
    payload_type: type[PayloadT],
    sealed_type: type[SealedT],
    identity_field: str,
    identity_prefix: str,
) -> SealedT:
    if identity_field in payload:
        raise ValueError(f"unsealed payload cannot contain {identity_field}")
    validated = payload_type.model_validate(payload)
    sealed = validated.model_dump(mode="json")
    sealed[identity_field] = canonical_hash(identity_prefix, sealed)
    return sealed_type.model_validate(sealed)


class DocumentCardPromptAsset(FrozenModel):
    schema_version: Literal["document-card-prompt-asset-v1"]
    prompt_version: Literal["deepseek-document-card-v1"]
    text: Annotated[str, Field(min_length=1, max_length=100_000)]
    prompt_hash: Annotated[str, Field(pattern=_SHA256)]

    @model_validator(mode="after")
    def validate_prompt(self) -> Self:
        if self.text != self.text.strip() or "\x00" in self.text:
            raise ValueError("Document Card prompt must be trimmed text without NUL")
        if "JSON" not in self.text:
            raise ValueError("Document Card prompt must explicitly request JSON")
        if self.prompt_hash != sha256_text(self.text):
            raise ValueError("Document Card prompt hash does not match text")
        return self


def load_document_card_prompt(path: str | Path | None = None) -> DocumentCardPromptAsset:
    prompt_path = DEFAULT_DOCUMENT_CARD_PROMPT_PATH if path is None else Path(path)
    if prompt_path.is_symlink() or not prompt_path.is_file():
        raise ValueError("Document Card prompt must be a regular non-symlink file")
    try:
        text = prompt_path.read_text(encoding="utf-8").rstrip("\n")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"invalid Document Card prompt: {exc}") from exc
    return DocumentCardPromptAsset(
        schema_version=DOCUMENT_CARD_PROMPT_ASSET_SCHEMA_VERSION,
        prompt_version=DOCUMENT_CARD_PROMPT_VERSION,
        text=text,
        prompt_hash=sha256_text(text),
    )


class _DocumentCardRequestFields(FrozenModel):
    schema_version: Literal["document-card-request-v1"]
    document_map: MarkdownDocumentMap
    markdown_body: Annotated[str, Field(min_length=1, max_length=500_000)]
    prompt_version: Literal["deepseek-document-card-v1"]
    prompt_hash: Annotated[str, Field(pattern=_SHA256)]
    export_policy_fingerprint: Annotated[str, Field(pattern=_POLICY_FINGERPRINT)]
    model: Literal["deepseek-v4-pro"]
    request_builder_version: Literal["document-card-request-builder-v1"]
    output_contract_version: Literal["document-card-draft-v1"]
    qualification: Literal["navigation_generation_request_not_authorization"]

    @model_validator(mode="after")
    def validate_bound_body(self) -> Self:
        if sha256_text(self.markdown_body) != self.document_map.normalized_body_hash:
            raise ValueError("Document Card request body differs from document map")
        return self


class _DocumentCardRequestPayload(_DocumentCardRequestFields):
    pass


class DocumentCardRequest(_DocumentCardRequestFields):
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]

    @model_validator(mode="after")
    def validate_request_id(self) -> Self:
        expected = canonical_hash("document-card-request", _identity_payload(self, "request_id"))
        if self.request_id != expected:
            raise ValueError("DocumentCardRequest.request_id does not match its contents")
        return self


class DocumentCardSectionView(FrozenModel):
    section_id: Annotated[str, Field(pattern=_SECTION_ID)]
    ordinal: Annotated[int, Field(ge=0)]
    kind: Literal["preamble", "heading", "document_body"]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    heading_level: Annotated[int | None, Field(ge=1, le=6)]
    heading_path: tuple[str, ...]
    content_start_line: Annotated[int, Field(ge=1)]
    content_end_line: Annotated[int, Field(ge=1)]
    subtree_end_line: Annotated[int, Field(ge=1)]

    @field_validator("heading_path", mode="before")
    @classmethod
    def parse_heading_path(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "DocumentCardSectionView.heading_path")

    @model_validator(mode="after")
    def validate_lines(self) -> Self:
        if self.content_end_line < self.content_start_line:
            raise ValueError("Document Card section content range is inverted")
        if self.subtree_end_line < self.content_end_line:
            raise ValueError("Document Card section subtree cannot end before content")
        return self


class DocumentCardWireInput(FrozenModel):
    schema_version: Literal["document-card-wire-input-v1"]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    document_id: Annotated[str, Field(min_length=1)]
    document_map_id: Annotated[str, Field(pattern=_MAP_ID)]
    source_id: Annotated[str, Field(min_length=1)]
    source_revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    source_relative_path: Annotated[str, Field(min_length=1)]
    source_content_hash: Annotated[str, Field(pattern=_SHA256)]
    normalized_body_hash: Annotated[str, Field(pattern=_SHA256)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    language: Annotated[str, Field(min_length=1)]
    release: str | None
    api_level: Annotated[int | None, Field(ge=1)]
    language_mode: str | None
    sections: tuple[DocumentCardSectionView, ...]
    markdown_body: Annotated[str, Field(min_length=1, max_length=500_000)]
    required_output_contract: Literal["document-card-draft-v1"]
    trust_notice: Literal["markdown_is_untrusted_data_navigation_only_not_evidence"]

    @field_validator("sections", mode="before")
    @classmethod
    def parse_sections(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "DocumentCardWireInput.sections")

    @model_validator(mode="after")
    def validate_sections_and_body(self) -> Self:
        if not self.sections:
            raise ValueError("DocumentCardWireInput.sections must not be empty")
        ordinals = tuple(section.ordinal for section in self.sections)
        if ordinals != tuple(range(len(self.sections))):
            raise ValueError("DocumentCardWireInput sections must use map order")
        if sha256_text(self.markdown_body) != self.normalized_body_hash:
            raise ValueError("DocumentCardWireInput body hash does not match")
        return self


class DocumentCardChatMessage(FrozenModel):
    role: Literal["system", "user"]
    content: Annotated[str, Field(min_length=1, max_length=2_000_000)]


class DocumentCardThinking(FrozenModel):
    type: Literal["disabled"]


class DocumentCardResponseFormat(FrozenModel):
    type: Literal["json_object"]


class DocumentCardChatPayload(FrozenModel):
    model: Literal["deepseek-v4-pro"]
    messages: Annotated[tuple[DocumentCardChatMessage, ...], Field(min_length=2, max_length=2)]
    thinking: DocumentCardThinking
    temperature: Literal[0]
    stream: Literal[False]
    tool_choice: Literal["none"]
    response_format: DocumentCardResponseFormat
    max_tokens: Annotated[int, Field(ge=256, le=16_384)]

    @field_validator("messages", mode="before")
    @classmethod
    def parse_messages(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "DocumentCardChatPayload.messages")

    @model_validator(mode="after")
    def validate_roles(self) -> Self:
        if tuple(message.role for message in self.messages) != ("system", "user"):
            raise ValueError("Document Card messages must be system then user")
        return self


class _DocumentCardDispatchPlanFields(FrozenModel):
    schema_version: Literal["document-card-dispatch-plan-v1"]
    plan_version: Literal["deepseek-document-card-single-attempt-v1"]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    document_id: Annotated[str, Field(min_length=1)]
    document_map_id: Annotated[str, Field(pattern=_MAP_ID)]
    source_content_hash: Annotated[str, Field(pattern=_SHA256)]
    normalized_body_hash: Annotated[str, Field(pattern=_SHA256)]
    prompt_version: Literal["deepseek-document-card-v1"]
    prompt_hash: Annotated[str, Field(pattern=_SHA256)]
    export_policy_fingerprint: Annotated[str, Field(pattern=_POLICY_FINGERPRINT)]
    wire_renderer_version: Literal["document-card-wire-renderer-v1"]
    provider: Literal["deepseek"]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    http_method: Literal["POST"]
    wire_input: DocumentCardWireInput
    wire_payload: DocumentCardChatPayload
    wire_body_json: Annotated[str, Field(min_length=1, max_length=2_000_000)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    wire_body_size_bytes: Annotated[int, Field(ge=1, le=2_000_000)]
    wall_clock_timeout_ms: Annotated[int, Field(ge=1_000, le=300_000)]
    max_response_bytes: Annotated[int, Field(ge=1_024, le=8_000_000)]
    max_attempts: Literal[1]
    retry_policy: Literal["none_single_attempt_v1"]
    tls_verify: Literal[True]
    follow_redirects: Literal[False]
    trust_env: Literal[False]
    qualification: Literal["plan_not_authorization"]

    @model_validator(mode="after")
    def validate_wire_graph(self) -> Self:
        if (
            self.request_id != self.wire_input.request_id
            or self.document_id != self.wire_input.document_id
            or self.document_map_id != self.wire_input.document_map_id
            or self.source_content_hash != self.wire_input.source_content_hash
            or self.normalized_body_hash != self.wire_input.normalized_body_hash
        ):
            raise ValueError("Document Card plan identity differs from wire input")
        expected_messages = (
            DocumentCardChatMessage(role="system", content=self.wire_payload.messages[0].content),
            DocumentCardChatMessage(
                role="user",
                content=canonical_json(self.wire_input.model_dump(mode="json")),
            ),
        )
        if self.wire_payload.messages != expected_messages:
            raise ValueError("Document Card wire messages differ from input")
        expected_body = canonical_json(self.wire_payload.model_dump(mode="json"))
        if self.wire_body_json != expected_body:
            raise ValueError("Document Card wire body is not canonical")
        encoded = expected_body.encode("utf-8")
        if self.wire_body_sha256 != "sha256:" + hashlib.sha256(encoded).hexdigest():
            raise ValueError("Document Card wire body hash does not match")
        if self.wire_body_size_bytes != len(encoded):
            raise ValueError("Document Card wire body byte count does not match")
        return self


class _DocumentCardDispatchPlanPayload(_DocumentCardDispatchPlanFields):
    pass


class DocumentCardDispatchPlan(_DocumentCardDispatchPlanFields):
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]

    @model_validator(mode="after")
    def validate_plan_id(self) -> Self:
        expected = canonical_hash("document-card-plan", _identity_payload(self, "plan_id"))
        if self.plan_id != expected:
            raise ValueError("DocumentCardDispatchPlan.plan_id does not match its contents")
        return self


def _validate_static_export(
    *,
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    registry: SourceRegistry,
    policy: DocumentCardExportPolicy,
    prompt: DocumentCardPromptAsset,
    model: str,
) -> None:
    verify_markdown_document_map(document, document_map)
    if not policy.enabled:
        raise ValueError("Document Card external export is disabled")
    if model not in policy.allowed_models:
        raise ValueError("Document Card model is not allowlisted")
    if prompt.prompt_version not in policy.allowed_prompt_versions:
        raise ValueError("Document Card prompt is not allowlisted")
    source_ref = document.source_ref
    try:
        source = registry.sources_by_id[source_ref.source_id]
    except KeyError as exc:
        raise ValueError("Document Card source is not registered") from exc
    if (
        source.revision != source_ref.revision
        or source.governance.authority != source_ref.authority
        or not source.governance.raw_prompt_use_allowed
        or not source.ingestion.index_as_normative_knowledge
        or not ingestion_path_allowed(source_ref.relative_path, source.ingestion)
    ):
        raise ValueError("Document Card source registry policy does not permit export")
    if not policy.permits_source(
        source_id=source_ref.source_id,
        revision=source_ref.revision,
        relative_path=source_ref.relative_path,
    ):
        raise ValueError("Document Card source is not in the exact export allowlist")
    if len(document.body) > policy.max_document_characters:
        raise ValueError("Document Card source exceeds the character budget")
    if len(document_map.sections) > policy.max_sections:
        raise ValueError("Document Card source exceeds the section budget")


def build_document_card_request(
    *,
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    registry: SourceRegistry,
    policy: DocumentCardExportPolicy,
    prompt: DocumentCardPromptAsset,
    model: Literal["deepseek-v4-pro"] = "deepseek-v4-pro",
) -> DocumentCardRequest:
    _validate_static_export(
        document=document,
        document_map=document_map,
        registry=registry,
        policy=policy,
        prompt=prompt,
        model=model,
    )
    return _seal(
        {
            "schema_version": DOCUMENT_CARD_REQUEST_SCHEMA_VERSION,
            "document_map": document_map,
            "markdown_body": document.body,
            "prompt_version": prompt.prompt_version,
            "prompt_hash": prompt.prompt_hash,
            "export_policy_fingerprint": policy.fingerprint,
            "model": model,
            "request_builder_version": DOCUMENT_CARD_REQUEST_BUILDER_VERSION,
            "output_contract_version": DOCUMENT_CARD_OUTPUT_CONTRACT_VERSION,
            "qualification": "navigation_generation_request_not_authorization",
        },
        payload_type=_DocumentCardRequestPayload,
        sealed_type=DocumentCardRequest,
        identity_field="request_id",
        identity_prefix="document-card-request",
    )


def verify_document_card_request(
    request: DocumentCardRequest,
    *,
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    registry: SourceRegistry,
    policy: DocumentCardExportPolicy,
    prompt: DocumentCardPromptAsset,
) -> None:
    request = DocumentCardRequest.model_validate(request.model_dump(mode="json"))
    expected = build_document_card_request(
        document=document,
        document_map=document_map,
        registry=registry,
        policy=policy,
        prompt=prompt,
        model=request.model,
    )
    if request != expected:
        raise ValueError("Document Card request differs from deterministic rebuild")


def _section_views(document_map: MarkdownDocumentMap) -> tuple[DocumentCardSectionView, ...]:
    return tuple(
        DocumentCardSectionView(
            section_id=section.section_id,
            ordinal=section.ordinal,
            kind=section.kind,
            title=section.title,
            heading_level=section.heading_level,
            heading_path=section.heading_path,
            content_start_line=section.content_span.start_line,
            content_end_line=section.content_span.end_line,
            subtree_end_line=section.subtree_span.end_line,
        )
        for section in document_map.sections
    )


def build_document_card_dispatch_plan(
    *,
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    request: DocumentCardRequest,
    registry: SourceRegistry,
    policy: DocumentCardExportPolicy,
    prompt: DocumentCardPromptAsset,
) -> DocumentCardDispatchPlan:
    verify_document_card_request(
        request,
        document=document,
        document_map=document_map,
        registry=registry,
        policy=policy,
        prompt=prompt,
    )
    source_ref = document.source_ref
    wire_input = DocumentCardWireInput(
        schema_version=DOCUMENT_CARD_WIRE_INPUT_SCHEMA_VERSION,
        request_id=request.request_id,
        document_id=document.document_id,
        document_map_id=document_map.map_id,
        source_id=source_ref.source_id,
        source_revision=source_ref.revision,
        source_relative_path=source_ref.relative_path,
        source_content_hash=source_ref.content_hash,
        normalized_body_hash=document_map.normalized_body_hash,
        title=document.title,
        language=document.language,
        release=document.release,
        api_level=document.api_level,
        language_mode=document.language_mode,
        sections=_section_views(document_map),
        markdown_body=document.body,
        required_output_contract=DOCUMENT_CARD_OUTPUT_CONTRACT_VERSION,
        trust_notice="markdown_is_untrusted_data_navigation_only_not_evidence",
    )
    wire_payload = DocumentCardChatPayload(
        model=request.model,
        messages=(
            DocumentCardChatMessage(role="system", content=prompt.text),
            DocumentCardChatMessage(
                role="user",
                content=canonical_json(wire_input.model_dump(mode="json")),
            ),
        ),
        thinking=DocumentCardThinking(type=policy.thinking),
        temperature=policy.temperature,
        stream=False,
        tool_choice="none",
        response_format=DocumentCardResponseFormat(type=policy.response_format),
        max_tokens=policy.max_output_tokens,
    )
    wire_body_json = canonical_json(wire_payload.model_dump(mode="json"))
    wire_bytes = wire_body_json.encode("utf-8")
    if len(wire_bytes) > policy.max_request_body_bytes:
        raise ValueError("Document Card wire body exceeds the request byte budget")
    return _seal(
        {
            "schema_version": DOCUMENT_CARD_DISPATCH_PLAN_SCHEMA_VERSION,
            "plan_version": "deepseek-document-card-single-attempt-v1",
            "request_id": request.request_id,
            "document_id": document.document_id,
            "document_map_id": document_map.map_id,
            "source_content_hash": source_ref.content_hash,
            "normalized_body_hash": document_map.normalized_body_hash,
            "prompt_version": prompt.prompt_version,
            "prompt_hash": prompt.prompt_hash,
            "export_policy_fingerprint": policy.fingerprint,
            "wire_renderer_version": DOCUMENT_CARD_WIRE_RENDERER_VERSION,
            "provider": policy.provider,
            "endpoint_url": policy.endpoint_url,
            "http_method": "POST",
            "wire_input": wire_input,
            "wire_payload": wire_payload,
            "wire_body_json": wire_body_json,
            "wire_body_sha256": "sha256:" + hashlib.sha256(wire_bytes).hexdigest(),
            "wire_body_size_bytes": len(wire_bytes),
            "wall_clock_timeout_ms": policy.wall_clock_timeout_ms,
            "max_response_bytes": policy.max_response_bytes,
            "max_attempts": policy.max_attempts,
            "retry_policy": policy.retry_policy,
            "tls_verify": policy.tls_verify,
            "follow_redirects": policy.follow_redirects,
            "trust_env": policy.trust_env,
            "qualification": "plan_not_authorization",
        },
        payload_type=_DocumentCardDispatchPlanPayload,
        sealed_type=DocumentCardDispatchPlan,
        identity_field="plan_id",
        identity_prefix="document-card-plan",
    )


def verify_document_card_dispatch_plan(
    plan: DocumentCardDispatchPlan,
    *,
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    request: DocumentCardRequest,
    registry: SourceRegistry,
    policy: DocumentCardExportPolicy,
    prompt: DocumentCardPromptAsset,
) -> None:
    plan = DocumentCardDispatchPlan.model_validate(plan.model_dump(mode="json"))
    expected = build_document_card_dispatch_plan(
        document=document,
        document_map=document_map,
        request=request,
        registry=registry,
        policy=policy,
        prompt=prompt,
    )
    if plan != expected:
        raise ValueError("Document Card dispatch plan differs from deterministic rebuild")


def load_document_card_request(raw: str | bytes) -> DocumentCardRequest:
    return load_json_model(raw, DocumentCardRequest, "Document Card request")


def load_document_card_dispatch_plan(raw: str | bytes) -> DocumentCardDispatchPlan:
    return load_json_model(raw, DocumentCardDispatchPlan, "Document Card dispatch plan")


__all__ = [
    "DEFAULT_DOCUMENT_CARD_PROMPT_PATH",
    "DOCUMENT_CARD_DISPATCH_PLAN_SCHEMA_VERSION",
    "DOCUMENT_CARD_OUTPUT_CONTRACT_VERSION",
    "DOCUMENT_CARD_PROMPT_ASSET_SCHEMA_VERSION",
    "DOCUMENT_CARD_PROMPT_VERSION",
    "DOCUMENT_CARD_REQUEST_BUILDER_VERSION",
    "DOCUMENT_CARD_REQUEST_SCHEMA_VERSION",
    "DOCUMENT_CARD_WIRE_INPUT_SCHEMA_VERSION",
    "DOCUMENT_CARD_WIRE_RENDERER_VERSION",
    "DocumentCardChatMessage",
    "DocumentCardChatPayload",
    "DocumentCardDispatchPlan",
    "DocumentCardPromptAsset",
    "DocumentCardRequest",
    "DocumentCardResponseFormat",
    "DocumentCardSectionView",
    "DocumentCardThinking",
    "DocumentCardWireInput",
    "build_document_card_dispatch_plan",
    "build_document_card_request",
    "load_document_card_dispatch_plan",
    "load_document_card_prompt",
    "load_document_card_request",
    "verify_document_card_dispatch_plan",
    "verify_document_card_request",
]
