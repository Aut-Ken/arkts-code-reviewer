from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from arkts_code_reviewer.knowledge.adapters import (
    ArkuiSpecAdapter,
    GitObjectReader,
    InterfaceSdkAdapter,
    OpenHarmonyDocsAdapter,
    SourceAdapter,
    discover_seed_objects,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument
from arkts_code_reviewer.knowledge.registry import SourceBundle, VerifiedSource
from arkts_code_reviewer.knowledge.seed import KnowledgeSeed

NORMALIZED_BUILD_SCHEMA_VERSION = "normalized-knowledge-build-v1"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _build_id(payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"knowledge-build:sha256:{hashlib.sha256(raw).hexdigest()}"


class NormalizedSeedDocument(_FrozenModel):
    domains: tuple[str, ...]
    document: NormalizedDocument

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or list(value) != sorted(set(value)):
            raise ValueError("NormalizedSeedDocument.domains must be non-empty and sorted")
        return value


class NormalizedKnowledgeBuild(_FrozenModel):
    schema_version: Literal["normalized-knowledge-build-v1"] = NORMALIZED_BUILD_SCHEMA_VERSION
    build_id: Annotated[str, Field(pattern=r"^knowledge-build:sha256:[0-9a-f]{64}$")]
    seed_id: Literal["knowledge-seed-v1"]
    seed_fingerprint: Annotated[str, Field(pattern=r"^knowledge-seed:sha256:[0-9a-f]{64}$")]
    source_bundle_id: Annotated[str, Field(pattern=r"^source-bundle:sha256:[0-9a-f]{64}$")]
    adapter_versions: tuple[str, ...]
    documents: tuple[NormalizedSeedDocument, ...]

    @field_validator("adapter_versions")
    @classmethod
    def validate_adapter_versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or list(value) != sorted(set(value)):
            raise ValueError("adapter_versions must be non-empty, sorted, and unique")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> NormalizedKnowledgeBuild:
        keys = [item.document.document_id for item in self.documents]
        if len(keys) != 24 or keys != sorted(set(keys)):
            raise ValueError("Normalized Knowledge seed must contain 24 sorted documents")
        payload = {
            "seed_id": self.seed_id,
            "seed_fingerprint": self.seed_fingerprint,
            "source_bundle_id": self.source_bundle_id,
            "adapter_versions": self.adapter_versions,
            "documents": [
                {
                    "document_id": item.document.document_id,
                    "content_hash": item.document.source_ref.content_hash,
                    "domains": item.domains,
                    "adapter_version": item.document.adapter_version,
                }
                for item in self.documents
            ],
        }
        if self.build_id != _build_id(payload):
            raise ValueError("NormalizedKnowledgeBuild.build_id does not match content")
        return self


def build_normalized_seed(
    seed: KnowledgeSeed,
    bundle: SourceBundle,
    verified_sources: Mapping[str, VerifiedSource],
) -> NormalizedKnowledgeBuild:
    if set(seed.source_ids) != {entry.source_id for entry in bundle.entries}:
        raise ValueError("Knowledge seed sources must exactly match SourceBundle")
    objects = discover_seed_objects(seed, verified_sources)
    reader = GitObjectReader(verified_sources)
    adapters: dict[str, SourceAdapter] = {
        "arkui-specs": ArkuiSpecAdapter(),
        "interface-sdk-js": InterfaceSdkAdapter(),
        "openharmony-docs": OpenHarmonyDocsAdapter(),
    }
    documents = tuple(
        NormalizedSeedDocument(
            domains=source.domains,
            document=adapters[source.source_id].load(source, reader),
        )
        for source in objects
    )
    adapter_versions = tuple(sorted({item.document.adapter_version for item in documents}))
    payload = {
        "seed_id": seed.seed_id,
        "seed_fingerprint": seed.fingerprint,
        "source_bundle_id": bundle.source_bundle_id,
        "adapter_versions": adapter_versions,
        "documents": [
            {
                "document_id": item.document.document_id,
                "content_hash": item.document.source_ref.content_hash,
                "domains": item.domains,
                "adapter_version": item.document.adapter_version,
            }
            for item in documents
        ],
    }
    return NormalizedKnowledgeBuild(
        build_id=_build_id(payload),
        seed_id=seed.seed_id,
        seed_fingerprint=seed.fingerprint,
        source_bundle_id=bundle.source_bundle_id,
        adapter_versions=adapter_versions,
        documents=documents,
    )


__all__ = [
    "NORMALIZED_BUILD_SCHEMA_VERSION",
    "NormalizedKnowledgeBuild",
    "NormalizedSeedDocument",
    "build_normalized_seed",
]
