from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from arkts_code_reviewer.feature_routing.config import (
    DimensionDefinition,
    FeatureConfig,
    TagDefinition,
)
from arkts_code_reviewer.knowledge.annotation import KnowledgeAnnotationBuild
from arkts_code_reviewer.knowledge.annotation_config import (
    KnowledgeAnnotationConfig,
    KnowledgeDomainRule,
)
from arkts_code_reviewer.knowledge.build import NormalizedKnowledgeBuild
from arkts_code_reviewer.knowledge.extraction import KnowledgeExtractionBuild
from arkts_code_reviewer.knowledge.models import (
    ApiSymbol,
    ClauseCandidate,
    ClauseStatus,
    KnowledgeAnnotation,
)
from arkts_code_reviewer.knowledge.registry import SourceRegistry

REVIEW_PACKET_SCHEMA_VERSION = "knowledge-review-packet-v1"
REVIEW_PACKET_BUILD_SCHEMA_VERSION = "knowledge-review-packet-build-v1"
EXPORT_POLICY_SCHEMA_VERSION = "knowledge-model-export-policy-v1"
REPO_EXPORT_POLICY = (
    Path(__file__).resolve().parents[3] / "config" / "knowledge_model_export.yaml"
)
PACKAGED_EXPORT_POLICY = (
    Path(__file__).resolve().parent / "defaults" / "knowledge_model_export.yaml"
)
REPO_REVIEW_PROMPT = (
    Path(__file__).resolve().parents[3]
    / "prompts"
    / "knowledge"
    / "grok-knowledge-auditor-v2.md"
)
PACKAGED_REVIEW_PROMPT = (
    Path(__file__).resolve().parent / "defaults" / "grok-knowledge-auditor-v2.md"
)
DEFAULT_EXPORT_POLICY = (
    REPO_EXPORT_POLICY if REPO_EXPORT_POLICY.is_file() else PACKAGED_EXPORT_POLICY
)
DEFAULT_REVIEW_PROMPT = (
    REPO_REVIEW_PROMPT if REPO_REVIEW_PROMPT.is_file() else PACKAGED_REVIEW_PROMPT
)

ReviewDistribution = Literal["local_only", "external_model"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _content_hash(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _sorted_unique_strings(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    result = tuple(value)
    if any(
        not isinstance(item, str) or not item or item.strip() != item
        for item in result
    ):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if list(result) != sorted(set(result)):
        raise ValueError(f"{context} must be sorted and unique")
    return result


def _relative_path(value: str, context: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or "." in path.parts
        or ".." in path.parts
        or "\\" in value
    ):
        raise ValueError(f"{context} must stay below the source root")
    return str(path)


class ModelExportSourceRule(_FrozenModel):
    source_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    relative_paths: tuple[str, ...]

    @field_validator("relative_paths", mode="before")
    @classmethod
    def validate_paths(cls, value: object) -> tuple[str, ...]:
        result = _sorted_unique_strings(value, "ModelExportSourceRule.relative_paths")
        if not result:
            raise ValueError("ModelExportSourceRule.relative_paths must not be empty")
        return tuple(
            _relative_path(item, "ModelExportSourceRule.relative_paths") for item in result
        )


class ExternalModelPolicy(_FrozenModel):
    enabled: bool
    provider: Literal["xai"]
    allowed_models: tuple[str, ...]
    allowed_prompt_versions: tuple[str, ...]
    source_allowlist: tuple[ModelExportSourceRule, ...]

    @field_validator("allowed_models", "allowed_prompt_versions", mode="before")
    @classmethod
    def validate_string_sets(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _sorted_unique_strings(value, f"ExternalModelPolicy.{info.field_name}")

    @field_validator("source_allowlist", mode="before")
    @classmethod
    def parse_source_rules(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("ExternalModelPolicy.source_allowlist must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def validate_enabled_policy(self) -> ExternalModelPolicy:
        keys = [(item.source_id, item.revision) for item in self.source_allowlist]
        if keys != sorted(set(keys)):
            raise ValueError("ExternalModelPolicy.source_allowlist must be sorted and unique")
        configured = bool(
            self.allowed_models
            or self.allowed_prompt_versions
            or self.source_allowlist
        )
        if self.enabled and not (
            self.allowed_models
            and self.allowed_prompt_versions
            and self.source_allowlist
        ):
            raise ValueError("enabled external model policy requires complete allowlists")
        if not self.enabled and configured:
            raise ValueError("disabled external model policy must not carry allowlists")
        return self


class KnowledgeModelExportPolicy(_FrozenModel):
    schema_version: Literal["knowledge-model-export-policy-v1"]
    version: Annotated[str, Field(min_length=1)]
    max_clauses_per_packet: Annotated[int, Field(ge=1, le=25)]
    max_source_ids_per_packet: Annotated[int, Field(ge=1, le=3)]
    context_lines_before: Annotated[int, Field(ge=0, le=50)]
    context_lines_after: Annotated[int, Field(ge=0, le=50)]
    max_excerpt_lines: Annotated[int, Field(ge=1, le=500)]
    max_packet_excerpt_characters: Annotated[int, Field(ge=1, le=500_000)]
    external_model: ExternalModelPolicy

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("KnowledgeModelExportPolicy.version must be trimmed")
        return value

    @property
    def fingerprint(self) -> str:
        return _canonical_hash(
            "knowledge-model-export-policy",
            self.model_dump(mode="json"),
        )


class KnowledgeReviewClause(_FrozenModel):
    rule_id: Annotated[str, Field(min_length=1)]
    proposed_status: ClauseStatus
    domains: tuple[str, ...]
    candidate: ClauseCandidate
    annotation: KnowledgeAnnotation

    @field_validator("domains", mode="before")
    @classmethod
    def validate_domains(cls, value: object) -> tuple[str, ...]:
        result = _sorted_unique_strings(value, "KnowledgeReviewClause.domains")
        if not result:
            raise ValueError("KnowledgeReviewClause.domains must not be empty")
        return result

    @model_validator(mode="after")
    def validate_annotation_target(self) -> KnowledgeReviewClause:
        if self.annotation.target_kind != "clause":
            raise ValueError("Knowledge review Clause annotation must target a Clause")
        if self.annotation.target_id != self.rule_id:
            raise ValueError("Knowledge review Clause annotation target does not match rule_id")
        return self


class KnowledgeSourceExcerpt(_FrozenModel):
    excerpt_id: Annotated[
        str,
        Field(pattern=r"^knowledge-source-excerpt:sha256:[0-9a-f]{64}$"),
    ]
    source_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    relative_path: Annotated[str, Field(min_length=1)]
    authority: Annotated[str, Field(min_length=1)]
    content_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    exact_text: Annotated[str, Field(min_length=1)]
    exact_text_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    rule_ids: tuple[str, ...]

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _relative_path(value, "KnowledgeSourceExcerpt.relative_path")

    @field_validator("rule_ids", mode="before")
    @classmethod
    def validate_rule_ids(cls, value: object) -> tuple[str, ...]:
        result = _sorted_unique_strings(value, "KnowledgeSourceExcerpt.rule_ids")
        if not result:
            raise ValueError("KnowledgeSourceExcerpt.rule_ids must not be empty")
        return result

    def identity_payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "revision": self.revision,
            "relative_path": self.relative_path,
            "authority": self.authority,
            "content_hash": self.content_hash,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "exact_text": self.exact_text,
            "exact_text_hash": self.exact_text_hash,
            "rule_ids": self.rule_ids,
        }

    def expected_excerpt_id(self) -> str:
        return _canonical_hash("knowledge-source-excerpt", self.identity_payload())

    @model_validator(mode="after")
    def validate_identity(self) -> KnowledgeSourceExcerpt:
        if self.end_line < self.start_line:
            raise ValueError("KnowledgeSourceExcerpt.end_line must be >= start_line")
        if self.exact_text_hash != _content_hash(self.exact_text):
            raise ValueError("KnowledgeSourceExcerpt.exact_text_hash does not match text")
        if self.excerpt_id != self.expected_excerpt_id():
            raise ValueError("KnowledgeSourceExcerpt.excerpt_id does not match content")
        return self


class KnowledgeReviewPacket(_FrozenModel):
    schema_version: Literal["knowledge-review-packet-v1"] = "knowledge-review-packet-v1"
    packet_id: Annotated[
        str,
        Field(pattern=r"^knowledge-review-packet:sha256:[0-9a-f]{64}$"),
    ]
    distribution: ReviewDistribution
    model_provider: str | None = None
    model_name: str | None = None
    extraction_build_id: Annotated[
        str,
        Field(pattern=r"^knowledge-extraction:sha256:[0-9a-f]{64}$"),
    ]
    annotation_build_id: Annotated[
        str,
        Field(pattern=r"^knowledge-annotation:sha256:[0-9a-f]{64}$"),
    ]
    source_bundle_id: Annotated[
        str,
        Field(pattern=r"^source-bundle:sha256:[0-9a-f]{64}$"),
    ]
    feature_config_fingerprint: Annotated[
        str,
        Field(pattern=r"^feature-config:sha256:[0-9a-f]{64}$"),
    ]
    annotation_config_fingerprint: Annotated[
        str,
        Field(pattern=r"^knowledge-annotation-config:sha256:[0-9a-f]{64}$"),
    ]
    annotation_version: Annotated[
        str,
        Field(pattern=r"^knowledge-annotation-version:sha256:[0-9a-f]{64}$"),
    ]
    export_policy_fingerprint: Annotated[
        str,
        Field(pattern=r"^knowledge-model-export-policy:sha256:[0-9a-f]{64}$"),
    ]
    prompt_version: Literal["grok-knowledge-auditor-v2"]
    prompt_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    tag_registry: tuple[TagDefinition, ...]
    dimension_registry: tuple[DimensionDefinition, ...]
    source_domain_ids: tuple[str, ...]
    domain_registry: tuple[KnowledgeDomainRule, ...]
    api_catalog_slice: tuple[ApiSymbol, ...]
    unresolved_api_names: tuple[str, ...]
    clauses: tuple[KnowledgeReviewClause, ...]
    source_excerpts: tuple[KnowledgeSourceExcerpt, ...]

    @field_validator(
        "source_domain_ids",
        "unresolved_api_names",
        mode="before",
    )
    @classmethod
    def validate_registered_ids(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        result = _sorted_unique_strings(value, f"KnowledgeReviewPacket.{info.field_name}")
        if info.field_name == "source_domain_ids" and not result:
            raise ValueError(f"KnowledgeReviewPacket.{info.field_name} must not be empty")
        return result

    @field_validator(
        "clauses",
        "source_excerpts",
        "tag_registry",
        "dimension_registry",
        "domain_registry",
        "api_catalog_slice",
        mode="before",
    )
    @classmethod
    def parse_records(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("Knowledge review packet records must be sequences")
        return tuple(value)

    def identity_payload(self) -> dict[str, object]:
        return {
            "distribution": self.distribution,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "extraction_build_id": self.extraction_build_id,
            "annotation_build_id": self.annotation_build_id,
            "source_bundle_id": self.source_bundle_id,
            "feature_config_fingerprint": self.feature_config_fingerprint,
            "annotation_config_fingerprint": self.annotation_config_fingerprint,
            "annotation_version": self.annotation_version,
            "export_policy_fingerprint": self.export_policy_fingerprint,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "tag_registry": [item.model_dump(mode="json") for item in self.tag_registry],
            "dimension_registry": [
                item.model_dump(mode="json") for item in self.dimension_registry
            ],
            "source_domain_ids": self.source_domain_ids,
            "domain_registry": [
                item.model_dump(mode="json") for item in self.domain_registry
            ],
            "api_catalog_slice": [
                item.model_dump(mode="json") for item in self.api_catalog_slice
            ],
            "unresolved_api_names": self.unresolved_api_names,
            "clauses": [item.model_dump(mode="json") for item in self.clauses],
            "source_excerpts": [
                item.model_dump(mode="json") for item in self.source_excerpts
            ],
        }

    def expected_packet_id(self) -> str:
        return _canonical_hash("knowledge-review-packet", self.identity_payload())

    @model_validator(mode="after")
    def validate_packet(self) -> KnowledgeReviewPacket:
        if not self.clauses or len(self.clauses) > 25:
            raise ValueError("Knowledge review packet must contain 1 to 25 Clauses")
        rule_ids = [item.rule_id for item in self.clauses]
        if rule_ids != sorted(set(rule_ids)):
            raise ValueError("Knowledge review packet Clauses must be sorted and unique")
        excerpt_keys = [
            (
                item.source_id,
                item.relative_path,
                item.start_line,
                item.end_line,
                item.excerpt_id,
            )
            for item in self.source_excerpts
        ]
        if excerpt_keys != sorted(set(excerpt_keys)):
            raise ValueError("Knowledge review packet excerpts must be sorted and unique")
        catalog_keys = [
            (item.canonical_name, item.signature, item.declaration_id)
            for item in self.api_catalog_slice
        ]
        if catalog_keys != sorted(set(catalog_keys)):
            raise ValueError("Knowledge review API catalog slice must be sorted and unique")
        tag_ids = [item.id for item in self.tag_registry]
        dimension_ids = [item.id for item in self.dimension_registry]
        domain_ids = [item.domain_id for item in self.domain_registry]
        if not tag_ids or tag_ids != sorted(set(tag_ids)):
            raise ValueError("Knowledge review Tag registry must be non-empty and sorted")
        if not dimension_ids or dimension_ids != sorted(set(dimension_ids)):
            raise ValueError("Knowledge review Dimension registry must be non-empty and sorted")
        if domain_ids != sorted(set(domain_ids)):
            raise ValueError("Knowledge review Domain registry must be sorted and unique")
        if self.distribution == "local_only":
            if self.model_provider is not None or self.model_name is not None:
                raise ValueError("local-only packet must not declare an external model")
        elif not self.model_provider or not self.model_name:
            raise ValueError("external packet requires provider and model")
        source_ids = {item.candidate.source_ref.source_id for item in self.clauses}
        if len(source_ids) > 3:
            raise ValueError("Knowledge review packet may reference at most three sources")
        packet_rule_ids = set(rule_ids)
        known_tags = set(tag_ids)
        known_dimensions = set(dimension_ids)
        known_domains = set(self.source_domain_ids).union(domain_ids)
        annotated_apis: set[str] = set()
        for clause in self.clauses:
            if not set(clause.annotation.tags).issubset(known_tags):
                raise ValueError("Knowledge review annotation contains an unregistered Tag")
            if not set(clause.annotation.dimension_ids).issubset(known_dimensions):
                raise ValueError("Knowledge review annotation contains an unregistered Dimension")
            if not set(clause.annotation.domains).issubset(known_domains):
                raise ValueError("Knowledge review annotation contains an unregistered Domain")
            annotated_apis.update(clause.annotation.apis)
        catalog_names = {item.canonical_name for item in self.api_catalog_slice}
        unresolved_names = set(self.unresolved_api_names)
        if catalog_names.intersection(unresolved_names):
            raise ValueError("Knowledge review APIs cannot be both resolved and unresolved")
        if annotated_apis != catalog_names.union(unresolved_names):
            raise ValueError("Knowledge review API snapshot does not cover annotation APIs")
        excerpt_rule_ids = {
            rule_id for excerpt in self.source_excerpts for rule_id in excerpt.rule_ids
        }
        if excerpt_rule_ids != packet_rule_ids:
            raise ValueError("Knowledge review excerpts must cover exactly the packet Clauses")
        for clause in self.clauses:
            source = clause.candidate.source_ref
            span = clause.candidate.source_span
            if not any(
                clause.rule_id in excerpt.rule_ids
                and excerpt.source_id == source.source_id
                and excerpt.revision == source.revision
                and excerpt.relative_path == source.relative_path
                and excerpt.authority == source.authority
                and excerpt.content_hash == source.content_hash
                and excerpt.start_line <= span.start_line
                and excerpt.end_line >= span.end_line
                for excerpt in self.source_excerpts
            ):
                raise ValueError("Knowledge review Clause is not covered by a source excerpt")
        if self.packet_id != self.expected_packet_id():
            raise ValueError("KnowledgeReviewPacket.packet_id does not match content")
        return self


class KnowledgeReviewPacketBuild(_FrozenModel):
    schema_version: Literal["knowledge-review-packet-build-v1"] = (
        "knowledge-review-packet-build-v1"
    )
    build_id: Annotated[
        str,
        Field(pattern=r"^knowledge-review-packets:sha256:[0-9a-f]{64}$"),
    ]
    distribution: ReviewDistribution
    extraction_build_id: Annotated[str, Field(min_length=1)]
    annotation_build_id: Annotated[str, Field(min_length=1)]
    source_bundle_id: Annotated[str, Field(min_length=1)]
    export_policy_fingerprint: Annotated[str, Field(min_length=1)]
    prompt_version: Literal["grok-knowledge-auditor-v2"]
    prompt_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    packets: tuple[KnowledgeReviewPacket, ...]

    @field_validator("packets", mode="before")
    @classmethod
    def parse_packets(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("KnowledgeReviewPacketBuild.packets must be a sequence")
        return tuple(value)

    def identity_payload(self) -> dict[str, object]:
        return {
            "distribution": self.distribution,
            "extraction_build_id": self.extraction_build_id,
            "annotation_build_id": self.annotation_build_id,
            "source_bundle_id": self.source_bundle_id,
            "export_policy_fingerprint": self.export_policy_fingerprint,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "packet_ids": [item.packet_id for item in self.packets],
        }

    @model_validator(mode="after")
    def validate_build(self) -> KnowledgeReviewPacketBuild:
        packet_ids = [item.packet_id for item in self.packets]
        if not packet_ids or packet_ids != sorted(set(packet_ids)):
            raise ValueError("Knowledge review packets must be non-empty, sorted, and unique")
        rule_ids = [
            clause.rule_id for packet in self.packets for clause in packet.clauses
        ]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Knowledge review Clauses must not repeat across packets")
        if any(item.distribution != self.distribution for item in self.packets):
            raise ValueError("Knowledge review packet distribution does not match build")
        if any(
            item.extraction_build_id != self.extraction_build_id
            or item.annotation_build_id != self.annotation_build_id
            or item.source_bundle_id != self.source_bundle_id
            or item.export_policy_fingerprint != self.export_policy_fingerprint
            or item.prompt_version != self.prompt_version
            or item.prompt_hash != self.prompt_hash
            for item in self.packets
        ):
            raise ValueError("Knowledge review packet provenance does not match build")
        expected = _canonical_hash("knowledge-review-packets", self.identity_payload())
        if self.build_id != expected:
            raise ValueError("KnowledgeReviewPacketBuild.build_id does not match content")
        return self


def load_knowledge_model_export_policy(
    path: str | Path | None = None,
) -> KnowledgeModelExportPolicy:
    config_path = DEFAULT_EXPORT_POLICY if path is None else Path(path)
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("Knowledge model export policy must be a regular non-symlink file")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        payload = yaml.load(config_path.read_text(encoding="utf-8"))
        return KnowledgeModelExportPolicy.model_validate(payload)
    except (OSError, UnicodeError, TypeError, ValueError, YAMLError) as exc:
        raise ValueError(f"invalid Knowledge model export policy: {exc}") from exc


def load_knowledge_review_prompt(path: str | Path | None = None) -> str:
    prompt_path = DEFAULT_REVIEW_PROMPT if path is None else Path(path)
    if prompt_path.is_symlink() or not prompt_path.is_file():
        raise ValueError("Knowledge review prompt must be a regular non-symlink file")
    try:
        raw_prompt = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"invalid Knowledge review prompt: {exc}") from exc
    prompt = raw_prompt.rstrip("\n")
    if not prompt or prompt.strip() != prompt or "\x00" in prompt:
        raise ValueError("Knowledge review prompt must be non-empty, trimmed UTF-8 text")
    return prompt


def _validate_build_graph(
    normalized: NormalizedKnowledgeBuild,
    extraction: KnowledgeExtractionBuild,
    annotations: KnowledgeAnnotationBuild,
) -> dict[str, KnowledgeAnnotation]:
    if extraction.normalized_build_id != normalized.build_id:
        raise ValueError("Knowledge extraction does not match normalized build")
    if extraction.source_bundle_id != normalized.source_bundle_id:
        raise ValueError("Knowledge source bundle drift between normalized and extraction")
    if annotations.extraction_build_id != extraction.build_id:
        raise ValueError("Knowledge annotations do not match extraction build")
    clause_ids = {
        clause.rule_id for document in extraction.documents for clause in document.clauses
    }
    annotation_map = {
        item.target_id: item
        for item in annotations.annotations
        if item.target_kind == "clause"
    }
    if set(annotation_map) != clause_ids:
        raise ValueError("Knowledge Clause annotation coverage is incomplete")
    return annotation_map


def _validate_external_export(
    *,
    clauses: tuple[KnowledgeReviewClause, ...],
    registry: SourceRegistry,
    policy: KnowledgeModelExportPolicy,
    prompt_version: str,
    provider: str | None,
    model: str | None,
) -> None:
    external = policy.external_model
    if not external.enabled:
        raise ValueError("external Knowledge model export is disabled by policy")
    if provider != external.provider or model not in external.allowed_models:
        raise ValueError("external Knowledge model target is not allowlisted")
    if prompt_version not in external.allowed_prompt_versions:
        raise ValueError("Knowledge review prompt version is not allowlisted")
    rule_map = {
        (item.source_id, item.revision): set(item.relative_paths)
        for item in external.source_allowlist
    }
    for clause in clauses:
        source_ref = clause.candidate.source_ref
        try:
            source = registry.sources_by_id[source_ref.source_id]
        except KeyError as exc:
            raise ValueError("Knowledge review Clause references an unregistered source") from exc
        if source.revision != source_ref.revision:
            raise ValueError("Knowledge review source revision drift")
        if source.governance.raw_prompt_use_allowed is not True:
            raise ValueError(
                f"raw prompt use is not allowed for source: {source_ref.source_id}"
            )
        allowed_paths = rule_map.get((source_ref.source_id, source_ref.revision), set())
        if source_ref.relative_path not in allowed_paths:
            raise ValueError("Knowledge review source path is not independently allowlisted")


def _make_excerpt(
    *,
    document_body: str,
    clause: KnowledgeReviewClause,
    policy: KnowledgeModelExportPolicy,
) -> KnowledgeSourceExcerpt:
    source = clause.candidate.source_ref
    source_span = clause.candidate.source_span
    lines = document_body.splitlines()
    if source_span.end_line > len(lines):
        raise ValueError("Knowledge review Clause source span is out of range")
    start_line = max(1, source_span.start_line - policy.context_lines_before)
    end_line = min(len(lines), source_span.end_line + policy.context_lines_after)
    if end_line - start_line + 1 > policy.max_excerpt_lines:
        raise ValueError("Knowledge review source excerpt exceeds the line limit")
    exact_text = "\n".join(lines[start_line - 1 : end_line])
    exact_text_hash = _content_hash(exact_text)
    payload = {
        "source_id": source.source_id,
        "revision": source.revision,
        "relative_path": source.relative_path,
        "authority": source.authority,
        "content_hash": source.content_hash,
        "start_line": start_line,
        "end_line": end_line,
        "exact_text": exact_text,
        "exact_text_hash": exact_text_hash,
        "rule_ids": (clause.rule_id,),
    }
    return KnowledgeSourceExcerpt(
        excerpt_id=_canonical_hash("knowledge-source-excerpt", payload),
        source_id=source.source_id,
        revision=source.revision,
        relative_path=source.relative_path,
        authority=source.authority,
        content_hash=source.content_hash,
        start_line=start_line,
        end_line=end_line,
        exact_text=exact_text,
        exact_text_hash=exact_text_hash,
        rule_ids=(clause.rule_id,),
    )


def build_knowledge_review_packets(
    normalized: NormalizedKnowledgeBuild,
    extraction: KnowledgeExtractionBuild,
    annotations: KnowledgeAnnotationBuild,
    *,
    registry: SourceRegistry,
    feature_config: FeatureConfig,
    annotation_config: KnowledgeAnnotationConfig,
    policy: KnowledgeModelExportPolicy,
    prompt: str,
    distribution: ReviewDistribution = "local_only",
    model_provider: str | None = None,
    model_name: str | None = None,
) -> KnowledgeReviewPacketBuild:
    annotation_map = _validate_build_graph(normalized, extraction, annotations)
    if annotations.feature_config_fingerprint != feature_config.fingerprint:
        raise ValueError("Knowledge review Feature config fingerprint drift")
    if annotations.annotation_config_fingerprint != annotation_config.fingerprint:
        raise ValueError("Knowledge review annotation config fingerprint drift")
    annotation_config.validate_feature_references(feature_config)
    if not prompt or prompt.strip() != prompt or "\x00" in prompt:
        raise ValueError("Knowledge review prompt must be non-empty and trimmed")
    prompt_version: Literal["grok-knowledge-auditor-v2"] = (
        "grok-knowledge-auditor-v2"
    )
    prompt_hash = _content_hash(prompt)
    normalized_documents = {
        item.document.document_id: item.document for item in normalized.documents
    }
    tag_registry = tuple(
        sorted(feature_config.tags_by_id.values(), key=lambda item: item.id)
    )
    dimension_registry = tuple(
        sorted(feature_config.dimensions_by_id.values(), key=lambda item: item.id)
    )
    source_domain_ids = annotation_config.source_domain_ids
    domain_registry = tuple(
        sorted(annotation_config.domain_rules, key=lambda item: item.domain_id)
    )
    api_catalog = tuple(
        sorted(
            (
                symbol
                for document in extraction.documents
                for symbol in document.api_symbols
            ),
            key=lambda item: (item.canonical_name, item.signature, item.declaration_id),
        )
    )
    chunks: list[tuple[KnowledgeReviewClause, ...]] = []
    for document in extraction.documents:
        clauses = tuple(
            KnowledgeReviewClause(
                rule_id=item.rule_id,
                proposed_status=item.proposed_status,
                domains=document.domains,
                candidate=item.candidate,
                annotation=annotation_map[item.rule_id],
            )
            for item in document.clauses
        )
        for index in range(0, len(clauses), policy.max_clauses_per_packet):
            chunks.append(clauses[index : index + policy.max_clauses_per_packet])
    if not chunks:
        raise ValueError("Knowledge extraction contains no Clause candidates to review")
    if distribution == "local_only":
        if model_provider is not None or model_name is not None:
            raise ValueError("local-only Knowledge review must not select a model")
    else:
        for chunk in chunks:
            _validate_external_export(
                clauses=chunk,
                registry=registry,
                policy=policy,
                prompt_version=prompt_version,
                provider=model_provider,
                model=model_name,
            )
    packets: list[KnowledgeReviewPacket] = []
    for chunk in chunks:
        if len({item.candidate.source_ref.source_id for item in chunk}) > (
            policy.max_source_ids_per_packet
        ):
            raise ValueError("Knowledge review packet exceeds the configured source limit")
        excerpts: list[KnowledgeSourceExcerpt] = []
        for clause in chunk:
            document_id = (
                f"{clause.candidate.source_ref.source_id}:"
                f"{clause.candidate.source_ref.relative_path}"
            )
            try:
                source_document = normalized_documents[document_id]
            except KeyError as exc:
                raise ValueError("Knowledge review source document is missing") from exc
            source = clause.candidate.source_ref
            normalized_source = source_document.source_ref
            if (
                source.source_id != normalized_source.source_id
                or source.revision != normalized_source.revision
                or source.relative_path != normalized_source.relative_path
                or source.authority != normalized_source.authority
                or source.content_hash != normalized_source.content_hash
            ):
                raise ValueError("Knowledge review source provenance drift")
            excerpts.append(
                _make_excerpt(
                    document_body=source_document.body,
                    clause=clause,
                    policy=policy,
                )
            )
        ordered_excerpts = tuple(
            sorted(
                excerpts,
                key=lambda item: (
                    item.source_id,
                    item.relative_path,
                    item.start_line,
                    item.end_line,
                    item.excerpt_id,
                ),
            )
        )
        if sum(len(item.exact_text) for item in ordered_excerpts) > (
            policy.max_packet_excerpt_characters
        ):
            raise ValueError("Knowledge review packet exceeds the excerpt character limit")
        annotated_apis = {
            api for clause in chunk for api in clause.annotation.apis
        }
        catalog_slice = tuple(
            item for item in api_catalog if item.canonical_name in annotated_apis
        )
        resolved_api_names = {item.canonical_name for item in catalog_slice}
        unresolved_api_names = tuple(sorted(annotated_apis - resolved_api_names))
        payload = {
            "distribution": distribution,
            "model_provider": model_provider,
            "model_name": model_name,
            "extraction_build_id": extraction.build_id,
            "annotation_build_id": annotations.build_id,
            "source_bundle_id": extraction.source_bundle_id,
            "feature_config_fingerprint": annotations.feature_config_fingerprint,
            "annotation_config_fingerprint": annotations.annotation_config_fingerprint,
            "annotation_version": annotations.annotation_version,
            "export_policy_fingerprint": policy.fingerprint,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "tag_registry": [item.model_dump(mode="json") for item in tag_registry],
            "dimension_registry": [
                item.model_dump(mode="json") for item in dimension_registry
            ],
            "source_domain_ids": source_domain_ids,
            "domain_registry": [
                item.model_dump(mode="json") for item in domain_registry
            ],
            "api_catalog_slice": [
                item.model_dump(mode="json") for item in catalog_slice
            ],
            "unresolved_api_names": unresolved_api_names,
            "clauses": [item.model_dump(mode="json") for item in chunk],
            "source_excerpts": [
                item.model_dump(mode="json") for item in ordered_excerpts
            ],
        }
        packets.append(
            KnowledgeReviewPacket(
                packet_id=_canonical_hash("knowledge-review-packet", payload),
                distribution=distribution,
                model_provider=model_provider,
                model_name=model_name,
                extraction_build_id=extraction.build_id,
                annotation_build_id=annotations.build_id,
                source_bundle_id=extraction.source_bundle_id,
                feature_config_fingerprint=annotations.feature_config_fingerprint,
                annotation_config_fingerprint=(
                    annotations.annotation_config_fingerprint
                ),
                annotation_version=annotations.annotation_version,
                export_policy_fingerprint=policy.fingerprint,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                tag_registry=tag_registry,
                dimension_registry=dimension_registry,
                source_domain_ids=source_domain_ids,
                domain_registry=domain_registry,
                api_catalog_slice=catalog_slice,
                unresolved_api_names=unresolved_api_names,
                clauses=chunk,
                source_excerpts=ordered_excerpts,
            )
        )
    ordered_packets = tuple(sorted(packets, key=lambda item: item.packet_id))
    expected_rule_ids = {
        clause.rule_id for document in extraction.documents for clause in document.clauses
    }
    actual_rule_ids = {
        clause.rule_id for packet in ordered_packets for clause in packet.clauses
    }
    if actual_rule_ids != expected_rule_ids:
        raise ValueError("Knowledge review packet coverage does not match extraction Clauses")
    build_payload = {
        "distribution": distribution,
        "extraction_build_id": extraction.build_id,
        "annotation_build_id": annotations.build_id,
        "source_bundle_id": extraction.source_bundle_id,
        "export_policy_fingerprint": policy.fingerprint,
        "prompt_version": prompt_version,
        "prompt_hash": prompt_hash,
        "packet_ids": [item.packet_id for item in ordered_packets],
    }
    return KnowledgeReviewPacketBuild(
        build_id=_canonical_hash("knowledge-review-packets", build_payload),
        packets=ordered_packets,
        distribution=distribution,
        extraction_build_id=extraction.build_id,
        annotation_build_id=annotations.build_id,
        source_bundle_id=extraction.source_bundle_id,
        export_policy_fingerprint=policy.fingerprint,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
    )


__all__ = [
    "DEFAULT_EXPORT_POLICY",
    "DEFAULT_REVIEW_PROMPT",
    "EXPORT_POLICY_SCHEMA_VERSION",
    "KnowledgeModelExportPolicy",
    "KnowledgeReviewClause",
    "KnowledgeReviewPacket",
    "KnowledgeReviewPacketBuild",
    "KnowledgeSourceExcerpt",
    "ModelExportSourceRule",
    "REVIEW_PACKET_BUILD_SCHEMA_VERSION",
    "REVIEW_PACKET_SCHEMA_VERSION",
    "build_knowledge_review_packets",
    "load_knowledge_model_export_policy",
    "load_knowledge_review_prompt",
]
