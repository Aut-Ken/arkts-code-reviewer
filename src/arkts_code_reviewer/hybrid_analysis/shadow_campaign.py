from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationError, ValidationInfo, field_validator, model_validator

from arkts_code_reviewer.code_analysis.change_set import CodeSourceSnapshot
from arkts_code_reviewer.code_analysis.context_planning import ContextPlanResult
from arkts_code_reviewer.code_analysis.models import AnalysisResult
from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    canonical_json,
    identity_payload,
    load_json_model,
    seal_payload,
)
from arkts_code_reviewer.hybrid_analysis.builders import (
    AIModelViewProjectionPolicy,
    AnalysisCardBuilder,
    AnalysisContextPolicy,
    AnalysisParserProfile,
    ModelViewBuilder,
    verify_model_view_against_card_and_policy,
)
from arkts_code_reviewer.hybrid_analysis.dispatch import (
    AITagDispatchEnvelopeBuilder,
    VerifiedAITagDispatchEnvelope,
)
from arkts_code_reviewer.hybrid_analysis.execution import (
    AITagResponseValidation,
)
from arkts_code_reviewer.hybrid_analysis.models import (
    AITagAnalysisRequest,
    AITagModelView,
    ReviewUnitAnalysisCard,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchPlan,
    build_ai_tag_shadow_dispatch_plan,
    verify_ai_tag_shadow_dispatch_plan,
)
from arkts_code_reviewer.hybrid_analysis.request_builder import (
    FullTaxonomyRequestBuilder,
)
from arkts_code_reviewer.hybrid_analysis.shadow_evaluation import (
    AITagShadowEvaluationBuilder,
    AITagShadowEvaluationInput,
    AITagShadowEvaluationReport,
)

AI_TAG_SHADOW_CAMPAIGN_MANIFEST_SCHEMA_VERSION: Literal["ai-tag-shadow-campaign-manifest-v1"] = (
    "ai-tag-shadow-campaign-manifest-v1"
)
AI_TAG_SHADOW_CAMPAIGN_INSPECTION_SCHEMA_VERSION: Literal[
    "ai-tag-shadow-campaign-inspection-v1"
] = "ai-tag-shadow-campaign-inspection-v1"
AI_TAG_SHADOW_CAMPAIGN_BUILDER_VERSION: Literal["ai-tag-shadow-campaign-builder-v1"] = (
    "ai-tag-shadow-campaign-builder-v1"
)

DEFAULT_PROVIDER_EGRESS_ANALYSIS_CONTEXT_POLICY = AnalysisContextPolicy(
    builder_version="analysis-card-builder-v2-provider-egress",
    redaction_policy="none_requires_exact_body_runtime_approval",
)
DEFAULT_CAMPAIGN_PROJECTION_POLICY = AIModelViewProjectionPolicy()

CampaignQualificationBlocker = Literal[
    "document_retrieval_truth_not_evaluated",
    "independent_tag_truth_missing",
    "production_prevalence_not_measured",
    "provider_execution_not_observed",
    "provider_runtime_authorization_not_granted",
    "source_git_provenance_not_attested",
]

_QUALIFICATION_BLOCKERS: tuple[CampaignQualificationBlocker, ...] = (
    "document_retrieval_truth_not_evaluated",
    "independent_tag_truth_missing",
    "production_prevalence_not_measured",
    "provider_execution_not_observed",
    "provider_runtime_authorization_not_granted",
    "source_git_provenance_not_attested",
)

_HASH = r"[0-9a-f]{64}"
_CAMPAIGN_ID = rf"^ai-tag-shadow-campaign:sha256:{_HASH}$"
_CHANGE_SET_ID = rf"^change-set:sha256:{_HASH}$"
_CONTEXT_PLAN_ID = rf"^context-plan:sha256:{_HASH}$"
_FEATURE_ROUTING_ID = rf"^feature-routing:sha256:{_HASH}$"
_FEATURE_CONFIG_FINGERPRINT = rf"^feature-config:sha256:{_HASH}$"
_CONTEXT_POLICY_FINGERPRINT = rf"^analysis-context-policy:sha256:{_HASH}$"
_PROJECTION_POLICY_FINGERPRINT = rf"^ai-model-view-policy:sha256:{_HASH}$"
_TAXONOMY_FINGERPRINT = rf"^ai-tag-taxonomy:sha256:{_HASH}$"
_CATALOG_FINGERPRINT = rf"^ai-tag-contract-catalog:sha256:{_HASH}$"
_MODEL_POLICY_FINGERPRINT = rf"^ai-tag-policy:sha256:{_HASH}$"
_SHADOW_POLICY_FINGERPRINT = rf"^ai-tag-shadow-policy:sha256:{_HASH}$"
_SOURCE_REF_ID = rf"^code-source:sha256:{_HASH}$"
_CARD_ID = rf"^analysis-card:sha256:{_HASH}$"
_MODEL_VIEW_ID = rf"^ai-tag-model-view:sha256:{_HASH}$"
_REQUEST_ID = rf"^ai-tag-request:sha256:{_HASH}$"
_ENVELOPE_ID = rf"^ai-tag-dispatch-envelope:sha256:{_HASH}$"
_PLAN_ID = rf"^ai-tag-shadow-plan:sha256:{_HASH}$"
_SHA256 = rf"^sha256:{_HASH}$"


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _trimmed_single_line(value: str, context: str, maximum: int = 2_000) -> str:
    if (
        not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(
            f"{context} must be non-empty, trimmed, single-line, and at most {maximum} characters"
        )
    return value


def _sha256_json(payload: object) -> str:
    encoded = canonical_json(payload).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _revalidate[ModelT: FrozenModel](
    value: ModelT,
    model_type: type[ModelT],
    context: str,
) -> ModelT:
    try:
        return model_type.model_validate(value.model_dump(mode="json"))
    except (AttributeError, ValidationError) as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


class AITagShadowCampaignUnitRef(FrozenModel):
    unit_id: Annotated[str, Field(min_length=1, max_length=2_000)]
    unit_kind: Literal[
        "struct",
        "class",
        "function",
        "method",
        "build_method",
        "builder",
        "ui_block",
        "field_region",
        "import_region",
        "fallback",
    ]
    source_ref_id: Annotated[str, Field(pattern=_SOURCE_REF_ID)]
    source_role: Literal["base", "head"]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]

    @field_validator("unit_id")
    @classmethod
    def validate_unit_id(cls, value: str) -> str:
        return _trimmed_single_line(value, "AITagShadowCampaignUnitRef.unit_id")


class _AITagShadowCampaignManifestPayload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-campaign-manifest-v1"]
    builder_version: Literal["ai-tag-shadow-campaign-builder-v1"]
    analysis_schema_version: Literal["analysis-result-v1"]
    analysis_graph_sha256: Annotated[str, Field(pattern=_SHA256)]
    source_snapshot_bundle_sha256: Annotated[str, Field(pattern=_SHA256)]
    change_set_id: Annotated[str, Field(pattern=_CHANGE_SET_ID)]
    context_plan_id: Annotated[str, Field(pattern=_CONTEXT_PLAN_ID)]
    context_plan_graph_sha256: Annotated[str, Field(pattern=_SHA256)]
    feature_routing_id: Annotated[str, Field(pattern=_FEATURE_ROUTING_ID)]
    feature_config_fingerprint: Annotated[
        str,
        Field(pattern=_FEATURE_CONFIG_FINGERPRINT),
    ]
    context_policy_fingerprint: Annotated[
        str,
        Field(pattern=_CONTEXT_POLICY_FINGERPRINT),
    ]
    projection_policy_fingerprint: Annotated[
        str,
        Field(pattern=_PROJECTION_POLICY_FINGERPRINT),
    ]
    active_taxonomy_fingerprint: Annotated[
        str,
        Field(pattern=_TAXONOMY_FINGERPRINT),
    ]
    catalog_fingerprint: Annotated[str, Field(pattern=_CATALOG_FINGERPRINT)]
    prompt_hash: Annotated[str, Field(pattern=_SHA256)]
    model_policy_fingerprint: Annotated[str, Field(pattern=_MODEL_POLICY_FINGERPRINT)]
    shadow_provider_policy_fingerprint: Annotated[
        str,
        Field(pattern=_SHADOW_POLICY_FINGERPRINT),
    ]
    selection_scope: Literal["caller_selected_review_unit_set"]
    selected_source_ref_ids: tuple[Annotated[str, Field(pattern=_SOURCE_REF_ID)], ...]
    units: tuple[AITagShadowCampaignUnitRef, ...]
    unit_count: Annotated[int, Field(ge=1)]
    execution_scope: Literal["inspect_only_no_dispatch_no_hybrid_no_retrieval"]
    authorization_state: Literal["not_authorized"]
    verification_root_scope: Literal[
        "caller_supplied_analysis_context_snapshots_and_builder_assets"
    ]
    source_provenance_scope: Literal["content_hash_replayed_git_attestation_not_verified"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]
    qualification_blockers: tuple[CampaignQualificationBlocker, ...]

    @field_validator(
        "selected_source_ref_ids",
        "units",
        "qualification_blockers",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"AITagShadowCampaignManifest.{info.field_name}")

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        order = tuple((item.unit_id, item.card_id, item.plan_id) for item in self.units)
        if len(order) != self.unit_count or order != tuple(sorted(set(order))):
            raise ValueError("campaign Units must be canonical, unique, and match unit_count")
        for attribute in (
            "unit_id",
            "card_id",
            "model_view_id",
            "request_id",
            "envelope_id",
            "plan_id",
        ):
            values = tuple(getattr(item, attribute) for item in self.units)
            if len(values) != len(set(values)):
                raise ValueError(f"campaign contains duplicate {attribute}")
        expected_sources = tuple(sorted({item.source_ref_id for item in self.units}))
        if self.selected_source_ref_ids != expected_sources:
            raise ValueError("campaign selected_source_ref_ids do not rebuild from Units")
        if self.qualification_blockers != _QUALIFICATION_BLOCKERS:
            raise ValueError("campaign must retain all qualification blockers")
        return self


class AITagShadowCampaignManifest(_AITagShadowCampaignManifestPayload):
    campaign_id: Annotated[str, Field(pattern=_CAMPAIGN_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-campaign",
            identity_payload(self, "campaign_id"),
        )
        if self.campaign_id != expected:
            raise ValueError("campaign ID does not match its contents")
        return self


class AITagShadowCampaignInspectionUnit(FrozenModel):
    source_ref_id: Annotated[str, Field(pattern=_SOURCE_REF_ID)]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    wire_body_size_bytes: Annotated[int, Field(ge=1, le=8_000_000)]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    model: Literal["deepseek-v4-pro"]
    max_output_tokens: Annotated[int, Field(ge=256, le=16_384)]
    wall_clock_timeout_ms: Annotated[int, Field(ge=1_000, le=120_000)]
    max_response_bytes: Annotated[int, Field(ge=1_024, le=8_000_000)]
    max_attempts: Literal[1]


class AITagShadowCampaignInspection(FrozenModel):
    schema_version: Literal["ai-tag-shadow-campaign-inspection-v1"]
    mode: Literal["inspect_only"]
    campaign_id: Annotated[str, Field(pattern=_CAMPAIGN_ID)]
    change_set_id: Annotated[str, Field(pattern=_CHANGE_SET_ID)]
    context_plan_id: Annotated[str, Field(pattern=_CONTEXT_PLAN_ID)]
    units: tuple[AITagShadowCampaignInspectionUnit, ...]
    unit_count: Annotated[int, Field(ge=1)]
    planned_attempt_cap_sum: Annotated[int, Field(ge=1)]
    planned_output_token_cap_sum: Annotated[int, Field(ge=256)]
    total_wire_body_size_bytes: Annotated[int, Field(ge=1)]
    network_attempted: Literal[False]
    credential_accessed: Literal[False]
    required_runtime_authorization: Literal[
        "per_plan_exact_body_approval_budget_and_single_use_capability"
    ]
    verification_scope: Literal["projection_requires_campaign_bundle_rebuild"]
    output_scope: Literal["metadata_only_no_code_prompt_wire_body_or_response"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]

    @field_validator("units", mode="before")
    @classmethod
    def parse_units(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AITagShadowCampaignInspection.units")

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        order = tuple((item.card_id, item.plan_id) for item in self.units)
        if len(order) != self.unit_count or order != tuple(sorted(set(order))):
            raise ValueError("campaign inspection Units are not canonical or unique")
        for attribute in (
            "card_id",
            "model_view_id",
            "request_id",
            "envelope_id",
            "plan_id",
            "wire_body_sha256",
        ):
            values = tuple(getattr(item, attribute) for item in self.units)
            if len(values) != len(set(values)):
                raise ValueError(f"campaign inspection contains duplicate {attribute}")
        if self.planned_attempt_cap_sum != sum(item.max_attempts for item in self.units):
            raise ValueError("campaign inspection attempt cap does not rebuild")
        if self.planned_output_token_cap_sum != sum(item.max_output_tokens for item in self.units):
            raise ValueError("campaign inspection output token cap does not rebuild")
        if self.total_wire_body_size_bytes != sum(item.wire_body_size_bytes for item in self.units):
            raise ValueError("campaign inspection body byte total does not rebuild")
        return self


@dataclass(frozen=True)
class AITagShadowCampaignUnitArtifacts:
    card: ReviewUnitAnalysisCard
    model_view: AITagModelView
    request: AITagAnalysisRequest
    envelope: VerifiedAITagDispatchEnvelope
    plan: AITagShadowDispatchPlan

    def __post_init__(self) -> None:
        card = _revalidate(self.card, ReviewUnitAnalysisCard, "campaign Analysis Card")
        model_view = _revalidate(self.model_view, AITagModelView, "campaign Model View")
        request = _revalidate(self.request, AITagAnalysisRequest, "campaign Request")
        envelope = _revalidate(
            self.envelope,
            VerifiedAITagDispatchEnvelope,
            "campaign Envelope",
        )
        plan = _revalidate(self.plan, AITagShadowDispatchPlan, "campaign Plan")
        object.__setattr__(self, "card", card)
        object.__setattr__(self, "model_view", model_view)
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "envelope", envelope)
        object.__setattr__(self, "plan", plan)


@dataclass(frozen=True)
class AITagShadowCampaignBundle:
    manifest: AITagShadowCampaignManifest
    inspection: AITagShadowCampaignInspection
    units: tuple[AITagShadowCampaignUnitArtifacts, ...]
    context_policy: AnalysisContextPolicy
    projection_policy: AIModelViewProjectionPolicy

    def __post_init__(self) -> None:
        manifest = _revalidate(
            self.manifest,
            AITagShadowCampaignManifest,
            "campaign Manifest",
        )
        inspection = _revalidate(
            self.inspection,
            AITagShadowCampaignInspection,
            "campaign Inspection",
        )
        if not isinstance(self.units, tuple) or not self.units:
            raise ValueError("campaign Bundle requires a non-empty Unit tuple")
        if any(not isinstance(item, AITagShadowCampaignUnitArtifacts) for item in self.units):
            raise TypeError("campaign Bundle Units use an unsupported type")
        if not isinstance(self.context_policy, AnalysisContextPolicy):
            raise TypeError("campaign Bundle context policy has an unsupported type")
        if not isinstance(self.projection_policy, AIModelViewProjectionPolicy):
            raise TypeError("campaign Bundle projection policy has an unsupported type")
        object.__setattr__(self, "manifest", manifest)
        object.__setattr__(self, "inspection", inspection)


def seal_ai_tag_shadow_campaign_manifest(
    payload: Mapping[str, object],
) -> AITagShadowCampaignManifest:
    return seal_payload(
        payload,
        payload_type=_AITagShadowCampaignManifestPayload,
        sealed_type=AITagShadowCampaignManifest,
        identity_field="campaign_id",
        identity_prefix="ai-tag-shadow-campaign",
        context="AI Tag Shadow Campaign Manifest",
    )


def load_ai_tag_shadow_campaign_manifest(
    raw: str | bytes,
) -> AITagShadowCampaignManifest:
    return load_json_model(
        raw,
        AITagShadowCampaignManifest,
        "AI Tag Shadow Campaign Manifest",
    )


def load_ai_tag_shadow_campaign_inspection(
    raw: str | bytes,
) -> AITagShadowCampaignInspection:
    return load_json_model(
        raw,
        AITagShadowCampaignInspection,
        "AI Tag Shadow Campaign Inspection",
    )


def render_ai_tag_shadow_campaign_inspection(
    inspection: AITagShadowCampaignInspection,
) -> str:
    canonical = _revalidate(
        inspection,
        AITagShadowCampaignInspection,
        "campaign Inspection",
    )
    return canonical_json(canonical.model_dump(mode="json"))


def _unit_ref(item: AITagShadowCampaignUnitArtifacts) -> AITagShadowCampaignUnitRef:
    return AITagShadowCampaignUnitRef(
        unit_id=item.card.unit_id,
        unit_kind=item.card.unit_kind,
        source_ref_id=item.card.source_ref_id,
        source_role=item.card.source_role,
        card_id=item.card.card_id,
        model_view_id=item.model_view.model_view_id,
        request_id=item.request.request_id,
        envelope_id=item.envelope.envelope_id,
        plan_id=item.plan.plan_id,
    )


def _inspection_unit(
    item: AITagShadowCampaignUnitArtifacts,
) -> AITagShadowCampaignInspectionUnit:
    return AITagShadowCampaignInspectionUnit(
        source_ref_id=item.card.source_ref_id,
        card_id=item.card.card_id,
        model_view_id=item.model_view.model_view_id,
        request_id=item.request.request_id,
        envelope_id=item.envelope.envelope_id,
        plan_id=item.plan.plan_id,
        wire_body_sha256=item.plan.wire_body_sha256,
        wire_body_size_bytes=len(item.plan.wire_body_json.encode("utf-8")),
        endpoint_url=item.plan.endpoint_url,
        model=item.plan.wire_payload.model,
        max_output_tokens=item.plan.wire_payload.max_tokens,
        wall_clock_timeout_ms=item.plan.wall_clock_timeout_ms,
        max_response_bytes=item.plan.max_response_bytes,
        max_attempts=item.plan.max_attempts,
    )


def _build_inspection(
    manifest: AITagShadowCampaignManifest,
    units: tuple[AITagShadowCampaignUnitArtifacts, ...],
) -> AITagShadowCampaignInspection:
    inspected = tuple(
        sorted(
            (_inspection_unit(item) for item in units),
            key=lambda item: (item.card_id, item.plan_id),
        )
    )
    return AITagShadowCampaignInspection(
        schema_version=AI_TAG_SHADOW_CAMPAIGN_INSPECTION_SCHEMA_VERSION,
        mode="inspect_only",
        campaign_id=manifest.campaign_id,
        change_set_id=manifest.change_set_id,
        context_plan_id=manifest.context_plan_id,
        units=inspected,
        unit_count=len(inspected),
        planned_attempt_cap_sum=sum(item.max_attempts for item in inspected),
        planned_output_token_cap_sum=sum(item.max_output_tokens for item in inspected),
        total_wire_body_size_bytes=sum(item.wire_body_size_bytes for item in inspected),
        network_attempted=False,
        credential_accessed=False,
        required_runtime_authorization=(
            "per_plan_exact_body_approval_budget_and_single_use_capability"
        ),
        verification_scope="projection_requires_campaign_bundle_rebuild",
        output_scope="metadata_only_no_code_prompt_wire_body_or_response",
        evidence_qualification_status="not_qualified",
        production_qualified=False,
    )


def _source_snapshot_bundle_sha256(
    source_snapshots: Mapping[str, CodeSourceSnapshot],
    source_ref_ids: tuple[str, ...],
) -> str:
    rows: list[dict[str, str]] = []
    for source_ref_id in source_ref_ids:
        snapshot = source_snapshots.get(source_ref_id)
        if not isinstance(snapshot, CodeSourceSnapshot):
            raise ValueError("campaign source snapshot set is incomplete")
        if snapshot.source_ref.source_ref_id != source_ref_id:
            raise ValueError("campaign source snapshot key differs from source identity")
        snapshot.source_ref.verify_content(snapshot.content)
        rows.append(
            {
                "source_ref_id": source_ref_id,
                "content_sha256": (
                    "sha256:" + hashlib.sha256(snapshot.content.encode("utf-8")).hexdigest()
                ),
            }
        )
    return _sha256_json(rows)


@dataclass(frozen=True)
class AITagShadowCampaignBuilder:
    request_builder: FullTaxonomyRequestBuilder

    @classmethod
    def default(cls) -> AITagShadowCampaignBuilder:
        return cls(request_builder=FullTaxonomyRequestBuilder.default())

    def build(
        self,
        *,
        analysis_result: AnalysisResult,
        context_plan: ContextPlanResult,
        source_snapshots: Mapping[str, CodeSourceSnapshot],
        unit_ids: tuple[str, ...],
        context_policy: AnalysisContextPolicy = (DEFAULT_PROVIDER_EGRESS_ANALYSIS_CONTEXT_POLICY),
        projection_policy: AIModelViewProjectionPolicy = (DEFAULT_CAMPAIGN_PROJECTION_POLICY),
        max_output_tokens: int = 4_096,
        timeout_ms: int = 60_000,
        max_response_bytes: int = 2_000_000,
        parser_profile: AnalysisParserProfile = "default",
    ) -> AITagShadowCampaignBundle:
        if not isinstance(analysis_result, AnalysisResult):
            raise TypeError("campaign analysis_result has an unsupported type")
        if not isinstance(context_plan, ContextPlanResult):
            raise TypeError("campaign context_plan has an unsupported type")
        if not isinstance(source_snapshots, Mapping):
            raise TypeError("campaign source_snapshots must use a mapping")
        if not isinstance(context_policy, AnalysisContextPolicy):
            raise TypeError("campaign context policy has an unsupported type")
        if not isinstance(projection_policy, AIModelViewProjectionPolicy):
            raise TypeError("campaign projection policy has an unsupported type")
        if (
            not isinstance(unit_ids, tuple)
            or not unit_ids
            or any(
                not isinstance(item, str) or not item or item != item.strip() for item in unit_ids
            )
            or len(unit_ids) != len(set(unit_ids))
        ):
            raise ValueError("campaign unit_ids must be a non-empty tuple of unique IDs")
        if context_policy != DEFAULT_PROVIDER_EGRESS_ANALYSIS_CONTEXT_POLICY:
            context_policy.__post_init__()
        projection_policy.__post_init__()
        canonical_unit_ids = tuple(sorted(unit_ids))

        analysis_result.validate()
        context_plan.__post_init__()
        if analysis_result.change_set is None:
            raise ValueError("campaign requires an AnalysisResult with a ChangeSet")
        if context_plan.change_set_id != analysis_result.change_set.change_set_id:
            raise ValueError("campaign ContextPlan and AnalysisResult ChangeSet differ")

        cards = AnalysisCardBuilder(parser_profile=parser_profile).build_many(
            analysis_result=analysis_result,
            context_plan=context_plan,
            source_snapshots=source_snapshots,
            unit_ids=canonical_unit_ids,
            policy=context_policy,
        )
        model_view_builder = ModelViewBuilder()
        envelope_builder = AITagDispatchEnvelopeBuilder(request_builder=self.request_builder)
        prepared: list[AITagShadowCampaignUnitArtifacts] = []
        for card in cards:
            model_view = model_view_builder.build(
                card=card,
                policy=projection_policy,
            )
            request = self.request_builder.build(card=card, model_view=model_view)
            envelope = envelope_builder.build(
                card=card,
                model_view=model_view,
                request=request,
            )
            plan = build_ai_tag_shadow_dispatch_plan(
                envelope=envelope,
                card=card,
                context_policy=context_policy,
                max_output_tokens=max_output_tokens,
                timeout_ms=timeout_ms,
                max_response_bytes=max_response_bytes,
            )
            prepared.append(
                AITagShadowCampaignUnitArtifacts(
                    card=card,
                    model_view=model_view,
                    request=request,
                    envelope=envelope,
                    plan=plan,
                )
            )
        units = tuple(
            sorted(
                prepared,
                key=lambda item: (item.card.unit_id, item.card.card_id, item.plan.plan_id),
            )
        )
        refs = tuple(_unit_ref(item) for item in units)
        first = units[0]
        source_ref_ids = tuple(sorted({item.card.source_ref_id for item in units}))
        manifest = seal_ai_tag_shadow_campaign_manifest(
            {
                "schema_version": AI_TAG_SHADOW_CAMPAIGN_MANIFEST_SCHEMA_VERSION,
                "builder_version": AI_TAG_SHADOW_CAMPAIGN_BUILDER_VERSION,
                "analysis_schema_version": analysis_result.schema_version,
                "analysis_graph_sha256": _sha256_json(analysis_result.to_dict()),
                "source_snapshot_bundle_sha256": _source_snapshot_bundle_sha256(
                    source_snapshots,
                    source_ref_ids,
                ),
                "change_set_id": analysis_result.change_set.change_set_id,
                "context_plan_id": context_plan.context_plan_id,
                "context_plan_graph_sha256": _sha256_json(context_plan.to_dict()),
                "feature_routing_id": first.card.feature_routing_id,
                "feature_config_fingerprint": first.card.feature_config_fingerprint,
                "context_policy_fingerprint": context_policy.fingerprint,
                "projection_policy_fingerprint": projection_policy.fingerprint,
                "active_taxonomy_fingerprint": (first.request.active_taxonomy_fingerprint),
                "catalog_fingerprint": self.request_builder.catalog.catalog_fingerprint,
                "prompt_hash": self.request_builder.prompt.prompt_hash,
                "model_policy_fingerprint": (
                    self.request_builder.model_policy.model_policy_fingerprint
                ),
                "shadow_provider_policy_fingerprint": (
                    first.plan.shadow_provider_policy.policy_fingerprint
                ),
                "selection_scope": "caller_selected_review_unit_set",
                "selected_source_ref_ids": source_ref_ids,
                "units": refs,
                "unit_count": len(refs),
                "execution_scope": "inspect_only_no_dispatch_no_hybrid_no_retrieval",
                "authorization_state": "not_authorized",
                "verification_root_scope": (
                    "caller_supplied_analysis_context_snapshots_and_builder_assets"
                ),
                "source_provenance_scope": ("content_hash_replayed_git_attestation_not_verified"),
                "evidence_qualification_status": "not_qualified",
                "production_qualified": False,
                "qualification_blockers": _QUALIFICATION_BLOCKERS,
            }
        )
        inspection = _build_inspection(manifest, units)
        bundle = AITagShadowCampaignBundle(
            manifest=manifest,
            inspection=inspection,
            units=units,
            context_policy=context_policy,
            projection_policy=projection_policy,
        )
        self.verify_bundle_graph(bundle)
        return bundle

    def verify_bundle_graph(self, bundle: AITagShadowCampaignBundle) -> None:
        if not isinstance(bundle, AITagShadowCampaignBundle):
            raise TypeError("campaign bundle has an unsupported type")
        manifest = _revalidate(
            bundle.manifest,
            AITagShadowCampaignManifest,
            "campaign Manifest",
        )
        inspection = _revalidate(
            bundle.inspection,
            AITagShadowCampaignInspection,
            "campaign Inspection",
        )
        units = tuple(
            sorted(
                bundle.units,
                key=lambda item: (item.card.unit_id, item.card.card_id, item.plan.plan_id),
            )
        )
        if units != bundle.units:
            raise ValueError("campaign Bundle Units must use canonical order")
        if bundle.context_policy.fingerprint != manifest.context_policy_fingerprint:
            raise ValueError("campaign context policy differs from Manifest")
        if bundle.projection_policy.fingerprint != manifest.projection_policy_fingerprint:
            raise ValueError("campaign projection policy differs from Manifest")
        if any(item.card.context_plan_id != manifest.context_plan_id for item in units):
            raise ValueError("campaign Cards differ from Manifest ContextPlan")
        envelope_builder = AITagDispatchEnvelopeBuilder(request_builder=self.request_builder)
        for item in units:
            verify_model_view_against_card_and_policy(
                item.model_view,
                item.card,
                policy=bundle.projection_policy,
            )
            expected_request = self.request_builder.build(
                card=item.card,
                model_view=item.model_view,
            )
            if item.request != expected_request:
                raise ValueError("campaign Request differs from Builder-bound rebuild")
            expected_envelope = envelope_builder.build(
                card=item.card,
                model_view=item.model_view,
                request=item.request,
            )
            if item.envelope != expected_envelope:
                raise ValueError("campaign Envelope differs from Builder-bound rebuild")
            verify_ai_tag_shadow_dispatch_plan(
                item.plan,
                envelope=item.envelope,
                card=item.card,
                context_policy=bundle.context_policy,
                trusted_max_output_tokens=item.plan.wire_payload.max_tokens,
                trusted_timeout_ms=item.plan.wall_clock_timeout_ms,
                trusted_max_response_bytes=item.plan.max_response_bytes,
            )
        expected_refs = tuple(_unit_ref(item) for item in units)
        if manifest.units != expected_refs:
            raise ValueError("campaign Manifest Unit refs differ from Bundle artifacts")
        if manifest.unit_count != len(units):
            raise ValueError("campaign Manifest count differs from Bundle")
        expected_inspection = _build_inspection(manifest, units)
        if inspection != expected_inspection:
            raise ValueError("campaign Inspection differs from Bundle projection")
        first = units[0]
        if (
            manifest.feature_routing_id != first.card.feature_routing_id
            or manifest.feature_config_fingerprint != first.card.feature_config_fingerprint
            or manifest.active_taxonomy_fingerprint != first.request.active_taxonomy_fingerprint
            or manifest.catalog_fingerprint != self.request_builder.catalog.catalog_fingerprint
            or manifest.prompt_hash != self.request_builder.prompt.prompt_hash
            or manifest.model_policy_fingerprint
            != self.request_builder.model_policy.model_policy_fingerprint
            or manifest.shadow_provider_policy_fingerprint
            != first.plan.shadow_provider_policy.policy_fingerprint
        ):
            raise ValueError("campaign Manifest shared assets differ from Bundle")
        shared_fields = (
            "feature_routing_id",
            "feature_config_fingerprint",
        )
        for field_name in shared_fields:
            if any(
                getattr(item.card, field_name) != getattr(first.card, field_name) for item in units
            ):
                raise ValueError(f"campaign mixes Units with different {field_name}")
        if any(
            item.request.active_taxonomy_fingerprint != first.request.active_taxonomy_fingerprint
            or item.plan.shadow_provider_policy.policy_fingerprint
            != first.plan.shadow_provider_policy.policy_fingerprint
            for item in units
        ):
            raise ValueError("campaign mixes taxonomy or shadow provider policies")

    def verify_against_upstream(
        self,
        bundle: AITagShadowCampaignBundle,
        *,
        analysis_result: AnalysisResult,
        context_plan: ContextPlanResult,
        source_snapshots: Mapping[str, CodeSourceSnapshot],
        unit_ids: tuple[str, ...],
        context_policy: AnalysisContextPolicy = (DEFAULT_PROVIDER_EGRESS_ANALYSIS_CONTEXT_POLICY),
        projection_policy: AIModelViewProjectionPolicy = (DEFAULT_CAMPAIGN_PROJECTION_POLICY),
        max_output_tokens: int = 4_096,
        timeout_ms: int = 60_000,
        max_response_bytes: int = 2_000_000,
        parser_profile: AnalysisParserProfile = "default",
    ) -> None:
        self.verify_bundle_graph(bundle)
        expected = self.build(
            analysis_result=analysis_result,
            context_plan=context_plan,
            source_snapshots=source_snapshots,
            unit_ids=unit_ids,
            context_policy=context_policy,
            projection_policy=projection_policy,
            max_output_tokens=max_output_tokens,
            timeout_ms=timeout_ms,
            max_response_bytes=max_response_bytes,
            parser_profile=parser_profile,
        )
        if bundle != expected:
            raise ValueError("campaign Bundle differs from full upstream rebuild")

    def build_evaluation_report(
        self,
        bundle: AITagShadowCampaignBundle,
        response_validations_by_plan_id: Mapping[str, AITagResponseValidation],
    ) -> AITagShadowEvaluationReport:
        self.verify_bundle_graph(bundle)
        if not isinstance(response_validations_by_plan_id, Mapping):
            raise TypeError("campaign response validations must use a mapping")
        expected_plan_ids = {item.plan.plan_id for item in bundle.units}
        provided_plan_ids = set(response_validations_by_plan_id)
        if provided_plan_ids != expected_plan_ids:
            missing = len(expected_plan_ids - provided_plan_ids)
            extra = len(provided_plan_ids - expected_plan_ids)
            raise ValueError(
                "campaign evaluation requires exactly one ResponseValidation per Plan "
                f"(missing={missing}, extra={extra})"
            )
        inputs: list[AITagShadowEvaluationInput] = []
        for item in bundle.units:
            validation = response_validations_by_plan_id[item.plan.plan_id]
            if not isinstance(validation, AITagResponseValidation):
                raise TypeError("campaign validation mapping contains an unsupported value")
            inputs.append(
                AITagShadowEvaluationInput(
                    card=item.card,
                    envelope=item.envelope,
                    response_validation=validation,
                )
            )
        return AITagShadowEvaluationBuilder(request_builder=self.request_builder).build_report(
            inputs
        )

    def verify_evaluation_report(
        self,
        report: AITagShadowEvaluationReport,
        bundle: AITagShadowCampaignBundle,
        response_validations_by_plan_id: Mapping[str, AITagResponseValidation],
    ) -> None:
        canonical = _revalidate(
            report,
            AITagShadowEvaluationReport,
            "campaign Evaluation Report",
        )
        expected = self.build_evaluation_report(
            bundle,
            response_validations_by_plan_id,
        )
        if canonical != expected:
            raise ValueError("campaign Evaluation Report differs from Campaign roots")


def build_ai_tag_shadow_campaign(
    *,
    analysis_result: AnalysisResult,
    context_plan: ContextPlanResult,
    source_snapshots: Mapping[str, CodeSourceSnapshot],
    unit_ids: tuple[str, ...],
    context_policy: AnalysisContextPolicy = DEFAULT_PROVIDER_EGRESS_ANALYSIS_CONTEXT_POLICY,
    projection_policy: AIModelViewProjectionPolicy = DEFAULT_CAMPAIGN_PROJECTION_POLICY,
    max_output_tokens: int = 4_096,
    timeout_ms: int = 60_000,
    max_response_bytes: int = 2_000_000,
    parser_profile: AnalysisParserProfile = "default",
) -> AITagShadowCampaignBundle:
    return AITagShadowCampaignBuilder.default().build(
        analysis_result=analysis_result,
        context_plan=context_plan,
        source_snapshots=source_snapshots,
        unit_ids=unit_ids,
        context_policy=context_policy,
        projection_policy=projection_policy,
        max_output_tokens=max_output_tokens,
        timeout_ms=timeout_ms,
        max_response_bytes=max_response_bytes,
        parser_profile=parser_profile,
    )


def verify_ai_tag_shadow_campaign_against_upstream(
    bundle: AITagShadowCampaignBundle,
    *,
    analysis_result: AnalysisResult,
    context_plan: ContextPlanResult,
    source_snapshots: Mapping[str, CodeSourceSnapshot],
    unit_ids: tuple[str, ...],
    context_policy: AnalysisContextPolicy = DEFAULT_PROVIDER_EGRESS_ANALYSIS_CONTEXT_POLICY,
    projection_policy: AIModelViewProjectionPolicy = DEFAULT_CAMPAIGN_PROJECTION_POLICY,
    max_output_tokens: int = 4_096,
    timeout_ms: int = 60_000,
    max_response_bytes: int = 2_000_000,
    parser_profile: AnalysisParserProfile = "default",
) -> None:
    AITagShadowCampaignBuilder.default().verify_against_upstream(
        bundle,
        analysis_result=analysis_result,
        context_plan=context_plan,
        source_snapshots=source_snapshots,
        unit_ids=unit_ids,
        context_policy=context_policy,
        projection_policy=projection_policy,
        max_output_tokens=max_output_tokens,
        timeout_ms=timeout_ms,
        max_response_bytes=max_response_bytes,
        parser_profile=parser_profile,
    )


def build_ai_tag_shadow_campaign_evaluation_report(
    bundle: AITagShadowCampaignBundle,
    response_validations_by_plan_id: Mapping[str, AITagResponseValidation],
) -> AITagShadowEvaluationReport:
    return AITagShadowCampaignBuilder.default().build_evaluation_report(
        bundle,
        response_validations_by_plan_id,
    )


def verify_ai_tag_shadow_campaign_evaluation_report(
    report: AITagShadowEvaluationReport,
    bundle: AITagShadowCampaignBundle,
    response_validations_by_plan_id: Mapping[str, AITagResponseValidation],
) -> None:
    AITagShadowCampaignBuilder.default().verify_evaluation_report(
        report,
        bundle,
        response_validations_by_plan_id,
    )


__all__ = [
    "AI_TAG_SHADOW_CAMPAIGN_BUILDER_VERSION",
    "AI_TAG_SHADOW_CAMPAIGN_INSPECTION_SCHEMA_VERSION",
    "AI_TAG_SHADOW_CAMPAIGN_MANIFEST_SCHEMA_VERSION",
    "AITagShadowCampaignBuilder",
    "AITagShadowCampaignBundle",
    "AITagShadowCampaignInspection",
    "AITagShadowCampaignInspectionUnit",
    "AITagShadowCampaignManifest",
    "AITagShadowCampaignUnitArtifacts",
    "AITagShadowCampaignUnitRef",
    "CampaignQualificationBlocker",
    "DEFAULT_CAMPAIGN_PROJECTION_POLICY",
    "DEFAULT_PROVIDER_EGRESS_ANALYSIS_CONTEXT_POLICY",
    "build_ai_tag_shadow_campaign",
    "build_ai_tag_shadow_campaign_evaluation_report",
    "load_ai_tag_shadow_campaign_inspection",
    "load_ai_tag_shadow_campaign_manifest",
    "render_ai_tag_shadow_campaign_inspection",
    "seal_ai_tag_shadow_campaign_manifest",
    "verify_ai_tag_shadow_campaign_against_upstream",
    "verify_ai_tag_shadow_campaign_evaluation_report",
]
