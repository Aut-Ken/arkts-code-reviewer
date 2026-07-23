from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from arkts_code_reviewer.knowledge.adapters import (
    ArkuiSpecAdapter,
    GitObjectReader,
    OpenHarmonyDocsAdapter,
    SourceObject,
)
from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
    load_json_model,
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
from arkts_code_reviewer.knowledge.document_first.models import MarkdownDocumentMap
from arkts_code_reviewer.knowledge.document_first.structure import (
    build_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument
from arkts_code_reviewer.knowledge.registry import (
    DEFAULT_SOURCE_REGISTRY,
    SourceBundle,
    SourceRegistry,
    build_source_bundle,
    load_source_registry,
)
from arkts_code_reviewer.knowledge.seed import (
    DEFAULT_KNOWLEDGE_SEED,
    KnowledgeSeed,
    load_knowledge_seed,
)

DOCUMENT_CARD_CAMPAIGN_SELECTION_SCHEMA_VERSION: Literal[
    "document-card-campaign-selection-v1"
] = "document-card-campaign-selection-v1"
DOCUMENT_CARD_CAMPAIGN_INSPECTION_SCHEMA_VERSION: Literal[
    "document-card-campaign-inspection-v1"
] = "document-card-campaign-inspection-v1"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PACKAGED_DEFAULTS = Path(__file__).resolve().parent / "defaults"


def _default_asset_path(filename: str, source_relative_path: str) -> Path:
    packaged = _PACKAGED_DEFAULTS / filename
    if packaged.is_file():
        return packaged
    return _REPO_ROOT / source_relative_path


DEFAULT_DOCUMENT_CARD_CAMPAIGN_SELECTION_PATH = _default_asset_path(
    "knowledge_document_card_pilot_selection_v1.yaml",
    "config/knowledge_document_card_pilot_selection_v1.yaml",
)
DEFAULT_DOCUMENT_CARD_CAMPAIGN_EXPORT_POLICY_PATH = _default_asset_path(
    "knowledge_document_card_pilot_export_v1.yaml",
    "config/knowledge_document_card_pilot_export_v1.yaml",
)
DEFAULT_DOCUMENT_CARD_CAMPAIGN_OUTPUT_ROOT = (
    _REPO_ROOT / "E2E_test_example_4_document_card_campaign" / "artifacts"
)

_HASH = r"[0-9a-f]{64}"
_SHA256 = rf"^sha256:{_HASH}$"
_MAP_ID = rf"^markdown-document-map:sha256:{_HASH}$"
_REQUEST_ID = rf"^document-card-request:sha256:{_HASH}$"
_PLAN_ID = rf"^document-card-plan:sha256:{_HASH}$"
_SELECTION_FINGERPRINT = rf"^document-card-campaign-selection:sha256:{_HASH}$"
_PLAN_SET_DIGEST = rf"^document-card-campaign-plan-set:sha256:{_HASH}$"
_CAMPAIGN_ID = rf"^document-card-campaign:sha256:{_HASH}$"


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _relative_path(value: str, context: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or value != value.strip()
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or "\\" in value
    ):
        raise ValueError(f"{context} must stay below its source root")
    return str(path)


class DocumentCardCampaignSelectionItem(FrozenModel):
    source_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    relative_path: Annotated[str, Field(min_length=1)]

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _relative_path(value, "DocumentCardCampaignSelectionItem.relative_path")


class DocumentCardCampaignSelection(FrozenModel):
    schema_version: Literal["document-card-campaign-selection-v1"]
    version: Annotated[str, Field(min_length=1)]
    documents: tuple[DocumentCardCampaignSelectionItem, ...]
    qualification: Literal["pilot_selection_not_export_or_execution_authorization"]

    @field_validator("documents", mode="before")
    @classmethod
    def parse_documents(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "DocumentCardCampaignSelection.documents")

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("DocumentCardCampaignSelection.version must be trimmed")
        return value

    @model_validator(mode="after")
    def validate_documents(self) -> Self:
        keys = tuple(
            (item.source_id, item.revision, item.relative_path) for item in self.documents
        )
        if not keys or keys != tuple(sorted(set(keys))):
            raise ValueError("campaign selection documents must be non-empty, sorted, and unique")
        return self

    @property
    def fingerprint(self) -> str:
        return canonical_hash(
            "document-card-campaign-selection",
            self.model_dump(mode="json"),
        )


class DocumentCardCampaignInspectionItem(FrozenModel):
    ordinal: Annotated[int, Field(ge=0)]
    source_id: Annotated[str, Field(min_length=1)]
    source_revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    source_relative_path: Annotated[str, Field(min_length=1)]
    source_content_hash: Annotated[str, Field(pattern=_SHA256)]
    document_id: Annotated[str, Field(min_length=1)]
    document_map_id: Annotated[str, Field(pattern=_MAP_ID)]
    section_count: Annotated[int, Field(ge=1, le=200)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    wire_body_size_bytes: Annotated[int, Field(ge=1, le=2_000_000)]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    model: Literal["deepseek-v4-pro"]
    max_output_tokens: Annotated[int, Field(ge=256, le=16_384)]
    wall_clock_timeout_ms: Annotated[int, Field(ge=1_000, le=300_000)]
    max_response_bytes: Annotated[int, Field(ge=1_024, le=8_000_000)]
    max_attempts: Literal[1]

    @field_validator("source_relative_path")
    @classmethod
    def validate_source_relative_path(cls, value: str) -> str:
        return _relative_path(
            value,
            "DocumentCardCampaignInspectionItem.source_relative_path",
        )


def _plan_set_rows(
    items: tuple[DocumentCardCampaignInspectionItem, ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "ordinal": item.ordinal,
            "source_id": item.source_id,
            "source_revision": item.source_revision,
            "source_relative_path": item.source_relative_path,
            "plan_id": item.plan_id,
            "wire_body_sha256": item.wire_body_sha256,
            "wire_body_size_bytes": item.wire_body_size_bytes,
            "endpoint_url": item.endpoint_url,
            "model": item.model,
            "max_output_tokens": item.max_output_tokens,
            "wall_clock_timeout_ms": item.wall_clock_timeout_ms,
            "max_response_bytes": item.max_response_bytes,
            "max_attempts": item.max_attempts,
        }
        for item in items
    )


def _plan_set_digest(
    items: tuple[DocumentCardCampaignInspectionItem, ...],
) -> str:
    return canonical_hash(
        "document-card-campaign-plan-set",
        {"plans": _plan_set_rows(items)},
    )


class _DocumentCardCampaignInspectionFields(FrozenModel):
    schema_version: Literal["document-card-campaign-inspection-v1"]
    mode: Literal["inspect_only"]
    selection_fingerprint: Annotated[str, Field(pattern=_SELECTION_FINGERPRINT)]
    knowledge_seed_fingerprint: Annotated[
        str,
        Field(pattern=rf"^knowledge-seed:sha256:{_HASH}$"),
    ]
    source_bundle_id: Annotated[str, Field(pattern=rf"^source-bundle:sha256:{_HASH}$")]
    export_policy_fingerprint: Annotated[
        str,
        Field(pattern=rf"^document-card-export-policy:sha256:{_HASH}$"),
    ]
    prompt_version: Literal["deepseek-document-card-v1"]
    prompt_hash: Annotated[str, Field(pattern=_SHA256)]
    plans: tuple[DocumentCardCampaignInspectionItem, ...]
    document_count: Annotated[int, Field(ge=1)]
    plan_set_digest: Annotated[str, Field(pattern=_PLAN_SET_DIGEST)]
    total_attempt_cap: Annotated[int, Field(ge=1)]
    total_request_body_bytes: Annotated[int, Field(ge=1)]
    total_output_token_cap: Annotated[int, Field(ge=256)]
    total_response_body_bytes: Annotated[int, Field(ge=1_024)]
    total_wall_clock_timeout_ms: Annotated[int, Field(ge=1_000)]
    network_attempted: Literal[False]
    credential_accessed: Literal[False]
    execution_authorized: Literal[False]
    required_runtime_authorization: Literal[
        "exact_campaign_id_plan_set_digest_aggregate_caps_and_explicit_export_ack"
    ]
    use_scope: Literal["navigation_generation_plans_not_knowledge_evidence"]
    evidence_eligible: Literal[False]
    production_qualified: Literal[False]
    qualification: Literal[
        "offline_campaign_inspection_not_execution_or_document_quality_evidence"
    ]

    @field_validator("plans", mode="before")
    @classmethod
    def parse_plans(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "DocumentCardCampaignInspection.plans")

    @model_validator(mode="after")
    def validate_plan_set_and_budgets(self) -> Self:
        ordinals = tuple(item.ordinal for item in self.plans)
        keys = tuple(
            (item.source_id, item.source_revision, item.source_relative_path)
            for item in self.plans
        )
        if (
            ordinals != tuple(range(len(self.plans)))
            or len(self.plans) != self.document_count
            or keys != tuple(sorted(set(keys)))
        ):
            raise ValueError("campaign inspection plans must be canonical, unique, and counted")
        for attribute in (
            "document_id",
            "document_map_id",
            "request_id",
            "plan_id",
            "wire_body_sha256",
        ):
            values = tuple(getattr(item, attribute) for item in self.plans)
            if len(values) != len(set(values)):
                raise ValueError(f"campaign inspection contains duplicate {attribute}")
        if self.plan_set_digest != _plan_set_digest(self.plans):
            raise ValueError("campaign plan-set digest does not match exact plans")
        expected_totals = (
            sum(item.max_attempts for item in self.plans),
            sum(item.wire_body_size_bytes for item in self.plans),
            sum(item.max_output_tokens for item in self.plans),
            sum(item.max_response_bytes for item in self.plans),
            sum(item.wall_clock_timeout_ms for item in self.plans),
        )
        actual_totals = (
            self.total_attempt_cap,
            self.total_request_body_bytes,
            self.total_output_token_cap,
            self.total_response_body_bytes,
            self.total_wall_clock_timeout_ms,
        )
        if actual_totals != expected_totals:
            raise ValueError("campaign aggregate budgets do not rebuild from exact plans")
        return self


class _DocumentCardCampaignInspectionPayload(_DocumentCardCampaignInspectionFields):
    pass


class DocumentCardCampaignInspection(_DocumentCardCampaignInspectionFields):
    campaign_id: Annotated[str, Field(pattern=_CAMPAIGN_ID)]

    @model_validator(mode="after")
    def validate_campaign_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"campaign_id"})
        if self.campaign_id != canonical_hash("document-card-campaign", payload):
            raise ValueError("Document Card campaign ID does not match its complete contents")
        return self


@dataclass(frozen=True)
class DocumentCardCampaignPlanBundle:
    document: NormalizedDocument
    document_map: MarkdownDocumentMap
    request: DocumentCardRequest
    plan: DocumentCardDispatchPlan


@dataclass(frozen=True)
class DocumentCardCampaignBundle:
    registry: SourceRegistry
    seed: KnowledgeSeed
    selection: DocumentCardCampaignSelection
    source_bundle: SourceBundle
    policy: DocumentCardExportPolicy
    prompt: DocumentCardPromptAsset
    plans: tuple[DocumentCardCampaignPlanBundle, ...]
    inspection: DocumentCardCampaignInspection


def load_document_card_campaign_selection(
    path: str | Path = DEFAULT_DOCUMENT_CARD_CAMPAIGN_SELECTION_PATH,
) -> DocumentCardCampaignSelection:
    selection_path = Path(path)
    if selection_path.is_symlink() or not selection_path.is_file():
        raise ValueError("Document Card campaign selection must be a regular non-symlink file")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        payload = yaml.load(selection_path.read_text(encoding="utf-8"))
        return DocumentCardCampaignSelection.model_validate(payload)
    except (OSError, UnicodeError, TypeError, ValueError, YAMLError) as exc:
        raise ValueError(f"invalid Document Card campaign selection: {exc}") from exc


def load_document_card_campaign_inspection(
    raw: str | bytes,
) -> DocumentCardCampaignInspection:
    return load_json_model(
        raw,
        DocumentCardCampaignInspection,
        "Document Card campaign inspection",
    )


def _adapter_for(source_id: str) -> ArkuiSpecAdapter | OpenHarmonyDocsAdapter:
    if source_id == "arkui-specs":
        return ArkuiSpecAdapter()
    if source_id == "openharmony-docs":
        return OpenHarmonyDocsAdapter()
    raise ValueError(f"Document Card campaign does not support source adapter: {source_id}")


def _inspection_item(
    ordinal: int,
    bundle: DocumentCardCampaignPlanBundle,
) -> DocumentCardCampaignInspectionItem:
    source_ref = bundle.document.source_ref
    return DocumentCardCampaignInspectionItem(
        ordinal=ordinal,
        source_id=source_ref.source_id,
        source_revision=source_ref.revision,
        source_relative_path=source_ref.relative_path,
        source_content_hash=source_ref.content_hash,
        document_id=bundle.document.document_id,
        document_map_id=bundle.document_map.map_id,
        section_count=len(bundle.document_map.sections),
        request_id=bundle.request.request_id,
        plan_id=bundle.plan.plan_id,
        wire_body_sha256=bundle.plan.wire_body_sha256,
        wire_body_size_bytes=bundle.plan.wire_body_size_bytes,
        endpoint_url=bundle.plan.endpoint_url,
        model=bundle.plan.wire_payload.model,
        max_output_tokens=bundle.plan.wire_payload.max_tokens,
        wall_clock_timeout_ms=bundle.plan.wall_clock_timeout_ms,
        max_response_bytes=bundle.plan.max_response_bytes,
        max_attempts=bundle.plan.max_attempts,
    )


def _seal_inspection(payload: dict[str, object]) -> DocumentCardCampaignInspection:
    validated = _DocumentCardCampaignInspectionPayload.model_validate(payload)
    sealed = validated.model_dump(mode="json")
    sealed["campaign_id"] = canonical_hash("document-card-campaign", sealed)
    return DocumentCardCampaignInspection.model_validate(sealed)


def assemble_document_card_campaign(
    *,
    registry: SourceRegistry,
    seed: KnowledgeSeed,
    selection: DocumentCardCampaignSelection,
    source_bundle: SourceBundle,
    policy: DocumentCardExportPolicy,
    prompt: DocumentCardPromptAsset,
    plans: tuple[DocumentCardCampaignPlanBundle, ...],
) -> DocumentCardCampaignBundle:
    inspected = tuple(_inspection_item(index, item) for index, item in enumerate(plans))
    inspection = _seal_inspection(
        {
            "schema_version": DOCUMENT_CARD_CAMPAIGN_INSPECTION_SCHEMA_VERSION,
            "mode": "inspect_only",
            "selection_fingerprint": selection.fingerprint,
            "knowledge_seed_fingerprint": seed.fingerprint,
            "source_bundle_id": source_bundle.source_bundle_id,
            "export_policy_fingerprint": policy.fingerprint,
            "prompt_version": prompt.prompt_version,
            "prompt_hash": prompt.prompt_hash,
            "plans": inspected,
            "document_count": len(inspected),
            "plan_set_digest": _plan_set_digest(inspected),
            "total_attempt_cap": sum(item.max_attempts for item in inspected),
            "total_request_body_bytes": sum(item.wire_body_size_bytes for item in inspected),
            "total_output_token_cap": sum(item.max_output_tokens for item in inspected),
            "total_response_body_bytes": sum(item.max_response_bytes for item in inspected),
            "total_wall_clock_timeout_ms": sum(
                item.wall_clock_timeout_ms for item in inspected
            ),
            "network_attempted": False,
            "credential_accessed": False,
            "execution_authorized": False,
            "required_runtime_authorization": (
                "exact_campaign_id_plan_set_digest_aggregate_caps_and_explicit_export_ack"
            ),
            "use_scope": "navigation_generation_plans_not_knowledge_evidence",
            "evidence_eligible": False,
            "production_qualified": False,
            "qualification": (
                "offline_campaign_inspection_not_execution_or_document_quality_evidence"
            ),
        }
    )
    bundle = DocumentCardCampaignBundle(
        registry=registry,
        seed=seed,
        selection=selection,
        source_bundle=source_bundle,
        policy=policy,
        prompt=prompt,
        plans=plans,
        inspection=inspection,
    )
    verify_document_card_campaign(bundle)
    return bundle


def prepare_document_card_campaign(
    *,
    registry_path: str | Path = DEFAULT_SOURCE_REGISTRY,
    seed_path: str | Path = DEFAULT_KNOWLEDGE_SEED,
    selection_path: str | Path = DEFAULT_DOCUMENT_CARD_CAMPAIGN_SELECTION_PATH,
    policy_path: str | Path = DEFAULT_DOCUMENT_CARD_CAMPAIGN_EXPORT_POLICY_PATH,
    prompt_path: str | Path | None = None,
) -> DocumentCardCampaignBundle:
    registry = load_source_registry(registry_path)
    seed = load_knowledge_seed(seed_path)
    selection = load_document_card_campaign_selection(selection_path)
    policy = load_document_card_export_policy(policy_path)
    prompt = load_document_card_prompt(prompt_path)

    source_ids = tuple(sorted({item.source_id for item in selection.documents}))
    source_bundle, verified_sources = build_source_bundle(registry, source_ids)
    verified_by_id = {item.source.id: item for item in verified_sources}
    reader = GitObjectReader(verified_by_id)
    seed_by_key = {
        (item.source_id, item.relative_path): item for item in seed.documents
    }

    plan_bundles: list[DocumentCardCampaignPlanBundle] = []
    for selected in selection.documents:
        source = registry.sources_by_id.get(selected.source_id)
        if source is None or source.revision != selected.revision:
            raise ValueError("campaign selection source revision differs from Registry")
        seed_document = seed_by_key.get((selected.source_id, selected.relative_path))
        if seed_document is None:
            raise ValueError("campaign selection document is not in the pinned Knowledge Seed")
        source_object = SourceObject(
            source_id=selected.source_id,
            revision=selected.revision,
            relative_path=selected.relative_path,
            authority=source.governance.authority,
            domains=seed_document.domains,
            media_type="text/markdown",
        )
        document = _adapter_for(selected.source_id).load(source_object, reader)
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
        plan_bundles.append(
            DocumentCardCampaignPlanBundle(
                document=document,
                document_map=document_map,
                request=request,
                plan=plan,
            )
        )

    return assemble_document_card_campaign(
        registry=registry,
        seed=seed,
        selection=selection,
        source_bundle=source_bundle,
        policy=policy,
        prompt=prompt,
        plans=tuple(plan_bundles),
    )


def verify_document_card_campaign(bundle: DocumentCardCampaignBundle) -> None:
    if not isinstance(bundle, DocumentCardCampaignBundle):
        raise TypeError("Document Card campaign bundle uses an unsupported type")
    if len(bundle.plans) != len(bundle.selection.documents):
        raise ValueError("Document Card campaign plan count differs from selection")
    selected_source_ids = tuple(
        sorted({item.source_id for item in bundle.selection.documents})
    )
    expected_source_bundle, _verified = build_source_bundle(
        bundle.registry,
        selected_source_ids,
        verify=False,
    )
    if bundle.source_bundle != expected_source_bundle:
        raise ValueError("Document Card campaign SourceBundle differs from selection")
    seed_keys = {(item.source_id, item.relative_path) for item in bundle.seed.documents}
    for selected in bundle.selection.documents:
        source = bundle.registry.sources_by_id.get(selected.source_id)
        if source is None or source.revision != selected.revision:
            raise ValueError("Document Card campaign selection differs from Registry")
        if (selected.source_id, selected.relative_path) not in seed_keys:
            raise ValueError("Document Card campaign selection differs from Knowledge Seed")
        if not bundle.policy.permits_source(
            source_id=selected.source_id,
            revision=selected.revision,
            relative_path=selected.relative_path,
        ):
            raise ValueError("Document Card campaign document is outside export policy")
    for selected, planned in zip(bundle.selection.documents, bundle.plans, strict=True):
        source_ref = planned.document.source_ref
        if (
            selected.source_id != source_ref.source_id
            or selected.revision != source_ref.revision
            or selected.relative_path != source_ref.relative_path
        ):
            raise ValueError("Document Card campaign plan differs from selection")
        verify_document_card_dispatch_plan(
            planned.plan,
            document=planned.document,
            document_map=planned.document_map,
            request=planned.request,
            registry=bundle.registry,
            policy=bundle.policy,
            prompt=bundle.prompt,
        )
    expected_items = tuple(
        _inspection_item(index, item) for index, item in enumerate(bundle.plans)
    )
    expected = _seal_inspection(
        {
            **bundle.inspection.model_dump(mode="json", exclude={"campaign_id", "plans"}),
            "plans": expected_items,
        }
    )
    if bundle.inspection != expected:
        raise ValueError("Document Card campaign inspection differs from deterministic rebuild")


def _json_bytes(value: FrozenModel | Mapping[str, object]) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, FrozenModel) else value
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _ensure_directory(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"unsafe campaign artifact directory: {path}")
        return
    path.mkdir(parents=True, mode=0o700)


def _write_or_verify(path: Path, content: bytes) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise ValueError(f"campaign artifact collision: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
                raise ValueError(f"campaign artifact collision: {path}") from None
    finally:
        temporary_path.unlink(missing_ok=True)


def materialize_document_card_campaign(
    bundle: DocumentCardCampaignBundle,
    *,
    output_root: Path = DEFAULT_DOCUMENT_CARD_CAMPAIGN_OUTPUT_ROOT,
) -> Path:
    verify_document_card_campaign(bundle)
    _ensure_directory(output_root)
    campaign_digest = bundle.inspection.campaign_id.rsplit(":", 1)[-1]
    output_directory = output_root / campaign_digest
    _ensure_directory(output_directory)
    _write_or_verify(
        output_directory / "00_campaign-selection.json",
        _json_bytes(bundle.selection),
    )
    _write_or_verify(
        output_directory / "01_campaign-inspection.json",
        _json_bytes(bundle.inspection),
    )
    plans_directory = output_directory / "plans"
    _ensure_directory(plans_directory)
    for item, planned in zip(bundle.inspection.plans, bundle.plans, strict=True):
        plan_digest = planned.plan.plan_id.rsplit(":", 1)[-1]
        plan_directory = plans_directory / f"{item.ordinal:02d}_{plan_digest}"
        _ensure_directory(plan_directory)
        source_manifest = {
            "schema_version": "document-card-source-manifest-v1",
            "document_id": planned.document.document_id,
            "source_ref": planned.document.source_ref.model_dump(mode="json"),
            "normalized_body_hash": planned.document_map.normalized_body_hash,
            "document_map_id": planned.document_map.map_id,
            "request_id": planned.request.request_id,
            "plan_id": planned.plan.plan_id,
            "prompt_version": bundle.prompt.prompt_version,
            "prompt_hash": bundle.prompt.prompt_hash,
            "export_policy_fingerprint": bundle.policy.fingerprint,
            "campaign_id": bundle.inspection.campaign_id,
            "plan_set_digest": bundle.inspection.plan_set_digest,
            "qualification": "pinned_source_identity_not_execution_or_publication_approval",
        }
        plan_inspection = {
            "schema_version": "document-card-campaign-plan-inspection-v1",
            "mode": "inspect_only",
            "network_attempted": False,
            "credential_accessed": False,
            "execution_authorized": False,
            "campaign_id": bundle.inspection.campaign_id,
            "plan_set_digest": bundle.inspection.plan_set_digest,
            **item.model_dump(mode="json"),
            "qualification": "offline_plan_not_execution_or_document_quality_evidence",
        }
        _write_or_verify(plan_directory / "00_source-manifest.json", _json_bytes(source_manifest))
        _write_or_verify(plan_directory / "01_source.md", planned.document.body.encode("utf-8"))
        _write_or_verify(plan_directory / "02_document-map.json", _json_bytes(planned.document_map))
        _write_or_verify(plan_directory / "03_request.json", _json_bytes(planned.request))
        _write_or_verify(plan_directory / "04_dispatch-plan.json", _json_bytes(planned.plan))
        _write_or_verify(plan_directory / "05_inspection.json", _json_bytes(plan_inspection))
    return output_directory


__all__ = [
    "DEFAULT_DOCUMENT_CARD_CAMPAIGN_EXPORT_POLICY_PATH",
    "DEFAULT_DOCUMENT_CARD_CAMPAIGN_OUTPUT_ROOT",
    "DEFAULT_DOCUMENT_CARD_CAMPAIGN_SELECTION_PATH",
    "DOCUMENT_CARD_CAMPAIGN_INSPECTION_SCHEMA_VERSION",
    "DOCUMENT_CARD_CAMPAIGN_SELECTION_SCHEMA_VERSION",
    "DocumentCardCampaignBundle",
    "DocumentCardCampaignInspection",
    "DocumentCardCampaignInspectionItem",
    "DocumentCardCampaignPlanBundle",
    "DocumentCardCampaignSelection",
    "DocumentCardCampaignSelectionItem",
    "assemble_document_card_campaign",
    "load_document_card_campaign_inspection",
    "load_document_card_campaign_selection",
    "materialize_document_card_campaign",
    "prepare_document_card_campaign",
    "verify_document_card_campaign",
]
