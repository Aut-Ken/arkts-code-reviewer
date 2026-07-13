from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from arkts_code_reviewer.knowledge.publication import (
    PublishedKnowledgeBuild,
    load_published_knowledge,
)
from arkts_code_reviewer.retrieval.config import load_default_retrieval_config
from arkts_code_reviewer.retrieval.embeddings import (
    DEFAULT_FASTEMBED_DIMENSIONS,
    DEFAULT_FASTEMBED_MODEL,
    FastEmbedProvider,
)
from arkts_code_reviewer.retrieval.index import EmbeddingProvider, build_knowledge_index
from arkts_code_reviewer.retrieval.models import KnowledgeIndex
from arkts_code_reviewer.retrieval.postgres import (
    PostgresIndexStore,
    PostgresIndexStoreError,
)

DEFAULT_EMBEDDING_MODEL = DEFAULT_FASTEMBED_MODEL
DEFAULT_EMBEDDING_DIMENSIONS = DEFAULT_FASTEMBED_DIMENSIONS


class RuntimeIndexStore(Protocol):
    def publish(self, index: KnowledgeIndex) -> bool: ...

    def load(self, index_version: str) -> KnowledgeIndex: ...

    def resolve_alias(self, alias_name: str = "current") -> str: ...

    def switch_alias(self, index_version: str, alias_name: str = "current") -> bool: ...


@dataclass(frozen=True, slots=True)
class IndexPublicationResult:
    index: KnowledgeIndex
    published: bool


def load_published_knowledge_file(path: str | Path) -> PublishedKnowledgeBuild:
    publication_path = Path(path).expanduser().absolute()
    if publication_path.is_symlink() or not publication_path.is_file():
        raise ValueError(
            f"Published Knowledge input must be a regular non-symlink file: {publication_path}"
        )
    try:
        raw = publication_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read Published Knowledge input: {exc}") from exc
    return load_published_knowledge(raw)


def publish_published_knowledge(
    publication: PublishedKnowledgeBuild,
    store: RuntimeIndexStore,
    *,
    embedding_provider: EmbeddingProvider | None = None,
) -> IndexPublicationResult:
    if not isinstance(publication, PublishedKnowledgeBuild):
        raise TypeError("publication must use PublishedKnowledgeBuild")
    validated = load_published_knowledge(publication.model_dump_json())
    if any(item.clause.status != "Baselined" for item in validated.clauses):
        raise ValueError("Retrieval publication accepts only Baselined Clauses")

    retrieval_config = load_default_retrieval_config()
    index = build_knowledge_index(
        validated,
        retrieval_version=retrieval_config.version,
        embedding_provider=embedding_provider,
    )
    published = store.publish(index)
    if store.load(index.index_version) != index:
        raise RuntimeError("published KnowledgeIndex failed PostgreSQL round-trip validation")
    return IndexPublicationResult(index=index, published=published)


def _database_url(argument: str | None) -> str:
    value = (
        argument or os.environ.get("ARKTS_RETRIEVAL_DATABASE_URL") or os.environ.get("DATABASE_URL")
    )
    if not value or not value.strip():
        raise ValueError(
            "--database-url, ARKTS_RETRIEVAL_DATABASE_URL, or DATABASE_URL is required"
        )
    return value


def _default_cache_dir() -> Path:
    configured = os.environ.get("ARKTS_FASTEMBED_CACHE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "arkts-code-reviewer" / "fastembed"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, publish, and activate production Retrieval indexes"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser(
        "publish",
        help="build and publish an index from a strict PublishedKnowledgeBuild JSON file",
    )
    publish.add_argument("--publication", type=Path, required=True)
    publish.add_argument("--database-url")
    publish.add_argument("--exact-only", action="store_true")
    publish.add_argument("--embedding-cache", type=Path, default=_default_cache_dir())
    publish.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    publish.add_argument(
        "--embedding-dimensions",
        type=int,
        default=DEFAULT_EMBEDDING_DIMENSIONS,
    )
    publish.add_argument("--local-files-only", action="store_true")
    publish.add_argument(
        "--switch-alias",
        metavar="ALIAS",
        help="switch this ready index into an alias after successful publication",
    )

    switch = subparsers.add_parser(
        "alias-switch",
        help="atomically switch a ready, fully validated index into an alias",
    )
    switch.add_argument("--database-url")
    switch.add_argument("--index-version", required=True)
    switch.add_argument("--alias", default="current")

    resolve = subparsers.add_parser(
        "alias-resolve",
        help="resolve an alias after validating its ready index",
    )
    resolve.add_argument("--database-url")
    resolve.add_argument("--alias", default="current")
    return parser


def _publish(args: argparse.Namespace) -> dict[str, object]:
    publication = load_published_knowledge_file(args.publication)
    provider: EmbeddingProvider | None
    if args.exact_only:
        provider = None
    else:
        provider = FastEmbedProvider(
            model_id=args.embedding_model,
            dimensions=args.embedding_dimensions,
            cache_dir=args.embedding_cache,
            local_files_only=args.local_files_only,
        )
    store = PostgresIndexStore(_database_url(args.database_url))
    result = publish_published_knowledge(
        publication,
        store,
        embedding_provider=provider,
    )
    alias_changed: bool | None = None
    if args.switch_alias is not None:
        alias_changed = store.switch_alias(result.index.index_version, args.switch_alias)
        if store.resolve_alias(args.switch_alias) != result.index.index_version:
            raise RuntimeError("Retrieval alias did not resolve to the published index")
    return {
        "operation": "publish",
        "publication_build_id": publication.build_id,
        "index_version": result.index.index_version,
        "record_count": len(result.index.records),
        "published": result.published,
        "embedding_model": result.index.embedding_model,
        "embedding_version": result.index.embedding_version,
        "embedding_dimensions": result.index.embedding_dimensions,
        "alias": args.switch_alias,
        "alias_changed": alias_changed,
    }


def _alias_switch(args: argparse.Namespace) -> dict[str, object]:
    store = PostgresIndexStore(_database_url(args.database_url))
    changed = store.switch_alias(args.index_version, args.alias)
    if store.resolve_alias(args.alias) != args.index_version:
        raise RuntimeError("Retrieval alias did not resolve to the requested index")
    return {
        "operation": "alias-switch",
        "alias": args.alias,
        "index_version": args.index_version,
        "changed": changed,
    }


def _alias_resolve(args: argparse.Namespace) -> dict[str, object]:
    store = PostgresIndexStore(_database_url(args.database_url))
    return {
        "operation": "alias-resolve",
        "alias": args.alias,
        "index_version": store.resolve_alias(args.alias),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "publish":
            payload = _publish(args)
        elif args.command == "alias-switch":
            payload = _alias_switch(args)
        else:
            payload = _alias_resolve(args)
    except (OSError, ValueError, RuntimeError, PostgresIndexStoreError) as exc:
        print(f"Retrieval runtime failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_EMBEDDING_DIMENSIONS",
    "DEFAULT_EMBEDDING_MODEL",
    "IndexPublicationResult",
    "RuntimeIndexStore",
    "load_published_knowledge_file",
    "main",
    "publish_published_knowledge",
]
