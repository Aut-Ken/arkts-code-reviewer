from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arkts_code_reviewer.feature_routing.config import FeatureConfig
from arkts_code_reviewer.feature_routing.matcher import active_tag_ids
from arkts_code_reviewer.knowledge.annotation_config import KnowledgeAnnotationConfig
from arkts_code_reviewer.knowledge.extraction import KnowledgeExtractionBuild
from arkts_code_reviewer.knowledge.models import (
    AnnotationProvenance,
    ApiSymbol,
    KnowledgeAnnotation,
)
from arkts_code_reviewer.knowledge.parsing import ExtractedClause

ANNOTATION_BUILD_SCHEMA_VERSION = "knowledge-annotation-build-v1"

_BACKTICK_RE = re.compile(r"`([^`\r\n]+)`")
_CALL_RE = re.compile(
    r"(?<![A-Za-z0-9_$])([A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)\s*\("
)
_LEADING_IDENTIFIER_RE = re.compile(
    r"^(@?[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)"
)
_DECORATOR_RE = re.compile(r"(?<![A-Za-z0-9_$])@[A-Za-z_$][A-Za-z0-9_$]*")
_RESERVED_IDENTIFIERS = {
    "false",
    "null",
    "number",
    "string",
    "true",
    "undefined",
    "void",
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class KnowledgeAnnotationBuild(_FrozenModel):
    schema_version: Literal["knowledge-annotation-build-v1"] = (
        "knowledge-annotation-build-v1"
    )
    build_id: Annotated[str, Field(pattern=r"^knowledge-annotation:sha256:[0-9a-f]{64}$")]
    extraction_build_id: Annotated[
        str,
        Field(pattern=r"^knowledge-extraction:sha256:[0-9a-f]{64}$"),
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
    annotations: tuple[KnowledgeAnnotation, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> KnowledgeAnnotationBuild:
        keys = [(item.target_kind, item.target_id) for item in self.annotations]
        if keys != sorted(set(keys)):
            raise ValueError("Knowledge annotations must be sorted and target-unique")
        if self.build_id != _annotation_build_id(self.identity_payload()):
            raise ValueError("KnowledgeAnnotationBuild.build_id does not match content")
        return self

    def identity_payload(self) -> dict[str, object]:
        return {
            "extraction_build_id": self.extraction_build_id,
            "feature_config_fingerprint": self.feature_config_fingerprint,
            "annotation_config_fingerprint": self.annotation_config_fingerprint,
            "annotation_version": self.annotation_version,
            "annotations": [item.model_dump(mode="json") for item in self.annotations],
        }


@dataclass(frozen=True)
class _KnowledgeFacts:
    components: tuple[str, ...]
    apis: tuple[str, ...]
    decorators: tuple[str, ...]
    attributes: tuple[str, ...]
    symbols: tuple[str, ...]
    syntax: tuple[str, ...] = ()
    resource_references: tuple[str, ...] = ()


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _annotation_build_id(payload: object) -> str:
    return _canonical_hash("knowledge-annotation", payload)


def annotation_version(
    features: FeatureConfig,
    config: KnowledgeAnnotationConfig,
) -> str:
    return _canonical_hash(
        "knowledge-annotation-version",
        {
            "feature_config_fingerprint": features.fingerprint,
            "annotation_config_fingerprint": config.fingerprint,
        },
    )


def _clause_content(clause: ExtractedClause) -> str:
    candidate = clause.candidate
    return "\n".join(
        (
            *candidate.heading_path,
            *(value for value in (candidate.parent_context, candidate.text) if value),
            *(example.text for example in candidate.examples),
        )
    )


def _identifier_signals(text: str) -> tuple[set[str], set[str], set[str]]:
    identifiers = set(_CALL_RE.findall(text))
    decorators = set(_DECORATOR_RE.findall(text))
    for content in _BACKTICK_RE.findall(text):
        match = _LEADING_IDENTIFIER_RE.match(content.strip())
        if match is None:
            continue
        identifier = match.group(1)
        if identifier.startswith("@"):
            decorators.add(identifier)
        elif identifier not in _RESERVED_IDENTIFIERS:
            identifiers.add(identifier)
    symbols = {
        part
        for identifier in identifiers
        for part in (identifier, identifier.rsplit(".", 1)[-1])
    }
    return identifiers, decorators, symbols


def _keyword_matches(
    text: str,
    config: KnowledgeAnnotationConfig,
) -> tuple[set[str], set[str]]:
    tags: set[str] = set()
    keywords: set[str] = set()
    for rule in config.keyword_tag_rules:
        matched = {keyword for keyword in rule.keywords if keyword in text}
        if matched:
            tags.add(rule.tag_id)
            keywords.update(matched)
    return tags, keywords


def _alias_apis(text: str, config: KnowledgeAnnotationConfig) -> tuple[set[str], set[str]]:
    apis: set[str] = set()
    keywords: set[str] = set()
    for alias in config.api_aliases:
        if any(phrase in text for phrase in alias.match_phrases):
            apis.add(alias.canonical_name)
            keywords.add(alias.keyword)
    return apis, keywords


def _known_signal_sets(features: FeatureConfig) -> tuple[set[str], set[str]]:
    components = {
        value
        for definition in features.tags_by_id.values()
        for value in definition.triggers.any_component
    }
    attributes = {
        value
        for definition in features.tags_by_id.values()
        for value in definition.triggers.any_attribute
    }
    return components, attributes


def _domain_matches(
    text: str,
    tags: set[str],
    apis: set[str],
    config: KnowledgeAnnotationConfig,
) -> tuple[set[str], set[str]]:
    domains: set[str] = set()
    keywords: set[str] = set()
    for rule in config.domain_rules:
        matched_keywords = {keyword for keyword in rule.any_keywords if keyword in text}
        if (
            set(rule.any_tags).intersection(tags)
            or set(rule.any_apis).intersection(apis)
            or matched_keywords
        ):
            domains.add(rule.domain_id)
            keywords.update(matched_keywords)
    return domains, keywords


def _conditional_dimensions(tags: set[str], features: FeatureConfig) -> set[str]:
    return {
        definition.id
        for definition in features.dimensions_by_id.values()
        if not definition.always_check
        and set(definition.triggers.any_tag).intersection(tags)
    }


def _provenance(
    *,
    tags: set[str],
    dimensions: set[str],
    apis: set[str],
    domains: set[str],
    source_domains: set[str],
    keywords: set[str],
    components: set[str],
    decorators: set[str],
    evidence_ref: str,
    source_domain_evidence_prefix: str | None,
    config: KnowledgeAnnotationConfig,
    catalog: dict[str, ApiSymbol],
) -> tuple[AnnotationProvenance, ...]:
    items = [
        *(
            AnnotationProvenance(
                kind="tag",
                value=value,
                origin="deterministic_parser",
                evidence_ref=f"{config.fingerprint}:tag:{value}",
            )
            for value in tags
        ),
        *(
            AnnotationProvenance(
                kind="dimension",
                value=value,
                origin="deterministic_parser",
                evidence_ref=f"{config.fingerprint}:dimension:{value}",
            )
            for value in dimensions
        ),
        *(
            AnnotationProvenance(
                kind="api",
                value=value,
                origin="api_catalog" if value in catalog else "deterministic_parser",
                evidence_ref=(
                    catalog[value].declaration_id if value in catalog else evidence_ref
                ),
            )
            for value in apis
        ),
        *(
            AnnotationProvenance(
                kind="domain",
                value=value,
                origin=(
                    "source_metadata"
                    if value in source_domains
                    else "deterministic_parser"
                ),
                evidence_ref=(
                    (
                        f"{source_domain_evidence_prefix}:domain:{value}"
                        if source_domain_evidence_prefix is not None
                        else f"source-domain:{value}"
                    )
                    if value in source_domains
                    else f"{config.fingerprint}:domain:{value}"
                ),
            )
            for value in domains
        ),
        *(
            AnnotationProvenance(
                kind="keyword",
                value=value,
                origin="deterministic_parser",
                evidence_ref=evidence_ref,
            )
            for value in keywords
        ),
        *(
            AnnotationProvenance(
                kind="component",
                value=value,
                origin="deterministic_parser",
                evidence_ref=evidence_ref,
            )
            for value in components
        ),
        *(
            AnnotationProvenance(
                kind="decorator",
                value=value,
                origin="deterministic_parser",
                evidence_ref=evidence_ref,
            )
            for value in decorators
        ),
    ]
    return tuple(
        sorted(
            items,
            key=lambda item: (item.kind, item.value, item.origin, item.evidence_ref),
        )
    )


def annotate_clause(
    clause: ExtractedClause,
    *,
    catalog: dict[str, ApiSymbol],
    feature_config: FeatureConfig,
    config: KnowledgeAnnotationConfig,
    index_version: str,
    source_domains: tuple[str, ...] = (),
    source_domain_evidence_prefix: str | None = None,
) -> KnowledgeAnnotation:
    text = _clause_content(clause)
    identifiers, decorators, symbols = _identifier_signals(text)
    alias_apis, alias_keywords = _alias_apis(text, config)
    apis = identifiers | alias_apis
    known_components, known_attributes = _known_signal_sets(feature_config)
    facts = _KnowledgeFacts(
        components=tuple(sorted(identifiers.intersection(known_components))),
        apis=tuple(sorted(apis)),
        decorators=tuple(sorted(decorators)),
        attributes=tuple(sorted(symbols.intersection(known_attributes))),
        symbols=tuple(sorted(symbols)),
    )
    tags = active_tag_ids(facts, feature_config)
    keyword_tags, keywords = _keyword_matches(text, config)
    tags.update(keyword_tags)
    keywords.update(alias_keywords)
    domains, domain_keywords = _domain_matches(text, tags, apis, config)
    unknown_source_domains = sorted(set(source_domains) - set(config.source_domain_ids))
    if unknown_source_domains:
        raise ValueError(f"unregistered source domains: {unknown_source_domains}")
    domains.update(source_domains)
    keywords.update(domain_keywords)
    dimensions = _conditional_dimensions(tags, feature_config)
    candidate = clause.candidate
    evidence_ref = (
        f"{candidate.source_ref.source_id}:{candidate.source_ref.relative_path}:"
        f"L{candidate.source_span.start_line}-L{candidate.source_span.end_line}"
    )
    version = annotation_version(feature_config, config)
    return KnowledgeAnnotation(
        target_kind="clause",
        target_id=clause.rule_id,
        index_version=index_version,
        dimension_ids=tuple(sorted(dimensions)),
        tags=tuple(sorted(tags)),
        apis=tuple(sorted(apis)),
        components=facts.components,
        decorators=facts.decorators,
        domains=tuple(sorted(domains)),
        raw_keywords=tuple(sorted(keywords)),
        provenance=_provenance(
            tags=tags,
            dimensions=dimensions,
            apis=apis,
            domains=domains,
            source_domains=set(source_domains),
            keywords=keywords,
            components=set(facts.components),
            decorators=set(facts.decorators),
            evidence_ref=evidence_ref,
            source_domain_evidence_prefix=source_domain_evidence_prefix,
            config=config,
            catalog=catalog,
        ),
        annotation_version=version,
    )


def annotate_api_symbol(
    symbol: ApiSymbol,
    *,
    catalog: dict[str, ApiSymbol],
    feature_config: FeatureConfig,
    config: KnowledgeAnnotationConfig,
    index_version: str,
    source_domains: tuple[str, ...] = (),
    source_domain_evidence_prefix: str | None = None,
) -> KnowledgeAnnotation:
    text = f"{symbol.canonical_name}\n{symbol.signature}"
    facts = _KnowledgeFacts(
        components=(),
        apis=(symbol.canonical_name,),
        decorators=(),
        attributes=(),
        symbols=(symbol.canonical_name, symbol.canonical_name.rsplit(".", 1)[-1]),
    )
    tags = active_tag_ids(facts, feature_config)
    keyword_tags, keywords = _keyword_matches(text, config)
    tags.update(keyword_tags)
    domains, domain_keywords = _domain_matches(
        text,
        tags,
        {symbol.canonical_name},
        config,
    )
    unknown_source_domains = sorted(set(source_domains) - set(config.source_domain_ids))
    if unknown_source_domains:
        raise ValueError(f"unregistered source domains: {unknown_source_domains}")
    domains.update(source_domains)
    keywords.update(domain_keywords)
    dimensions = _conditional_dimensions(tags, feature_config)
    evidence_ref = symbol.declaration_id
    version = annotation_version(feature_config, config)
    return KnowledgeAnnotation(
        target_kind="api_symbol",
        target_id=symbol.declaration_id,
        index_version=index_version,
        dimension_ids=tuple(sorted(dimensions)),
        tags=tuple(sorted(tags)),
        apis=(symbol.canonical_name,),
        domains=tuple(sorted(domains)),
        raw_keywords=tuple(sorted(keywords)),
        provenance=_provenance(
            tags=tags,
            dimensions=dimensions,
            apis={symbol.canonical_name},
            domains=domains,
            source_domains=set(source_domains),
            keywords=keywords,
            components=set(),
            decorators=set(),
            evidence_ref=evidence_ref,
            source_domain_evidence_prefix=source_domain_evidence_prefix,
            config=config,
            # The declaration itself is always an exact catalog match.  Override
            # a same-name overload here so its provenance cannot accidentally
            # point at another declaration with the same canonical name.
            catalog={**catalog, symbol.canonical_name: symbol},
        ),
        annotation_version=version,
    )


def build_knowledge_annotations(
    extraction: KnowledgeExtractionBuild,
    *,
    feature_config: FeatureConfig,
    config: KnowledgeAnnotationConfig,
) -> KnowledgeAnnotationBuild:
    config.validate_feature_references(feature_config)
    all_symbols = [symbol for document in extraction.documents for symbol in document.api_symbols]
    canonical_counts = Counter(symbol.canonical_name for symbol in all_symbols)
    catalog = {
        symbol.canonical_name: symbol
        for symbol in all_symbols
        if canonical_counts[symbol.canonical_name] == 1
    }
    index_version = f"candidate:{extraction.build_id}"
    annotations = [
        *(
            annotate_clause(
                clause,
                catalog=catalog,
                feature_config=feature_config,
                config=config,
                index_version=index_version,
                source_domains=document.domains,
                source_domain_evidence_prefix=(
                    f"{extraction.seed_fingerprint}:{document.document_id}"
                ),
            )
            for document in extraction.documents
            for clause in document.clauses
        ),
        *(
            annotate_api_symbol(
                symbol,
                catalog=catalog,
                feature_config=feature_config,
                config=config,
                index_version=index_version,
                source_domains=document.domains,
                source_domain_evidence_prefix=(
                    f"{extraction.seed_fingerprint}:{document.document_id}"
                ),
            )
            for document in extraction.documents
            for symbol in document.api_symbols
        ),
    ]
    ordered = tuple(sorted(annotations, key=lambda item: (item.target_kind, item.target_id)))
    expected_targets = {
        *(
            ("clause", clause.rule_id)
            for document in extraction.documents
            for clause in document.clauses
        ),
        *(("api_symbol", symbol.declaration_id) for symbol in all_symbols),
    }
    actual_targets = {(item.target_kind, item.target_id) for item in ordered}
    if actual_targets != expected_targets:
        raise ValueError("Knowledge annotation coverage does not match extraction targets")
    version = annotation_version(feature_config, config)
    payload = {
        "extraction_build_id": extraction.build_id,
        "feature_config_fingerprint": feature_config.fingerprint,
        "annotation_config_fingerprint": config.fingerprint,
        "annotation_version": version,
        "annotations": [item.model_dump(mode="json") for item in ordered],
    }
    return KnowledgeAnnotationBuild(
        build_id=_annotation_build_id(payload),
        extraction_build_id=extraction.build_id,
        feature_config_fingerprint=feature_config.fingerprint,
        annotation_config_fingerprint=config.fingerprint,
        annotation_version=version,
        annotations=ordered,
    )


__all__ = [
    "ANNOTATION_BUILD_SCHEMA_VERSION",
    "KnowledgeAnnotationBuild",
    "annotate_api_symbol",
    "annotate_clause",
    "annotation_version",
    "build_knowledge_annotations",
]
