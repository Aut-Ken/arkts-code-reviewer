from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from arkts_code_reviewer.knowledge.evaluation import (
    EvaluationKnowledgeBuild,
    load_evaluation_knowledge,
)
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
from arkts_code_reviewer.retrieval.index import (
    EmbeddingProvider,
    build_evaluation_knowledge_index,
    build_knowledge_index,
)
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

    def switch_alias(
        self,
        index_version: str,
        alias_name: str = "current",
        *,
        allow_evaluation_fixture: bool = False,
        allow_golden_fixture: bool = False,
    ) -> bool: ...


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


def load_evaluation_knowledge_file(path: str | Path) -> EvaluationKnowledgeBuild:
    evaluation_path = Path(path).expanduser().absolute()
    if evaluation_path.is_symlink() or not evaluation_path.is_file():
        raise ValueError(
            f"Evaluation Knowledge input must be a regular non-symlink file: {evaluation_path}"
        )
    try:
        raw = evaluation_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read Evaluation Knowledge input: {exc}") from exc
    return load_evaluation_knowledge(raw)


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


def publish_evaluation_knowledge(
    evaluation: EvaluationKnowledgeBuild,
    store: RuntimeIndexStore,
    *,
    embedding_provider: EmbeddingProvider | None = None,
) -> IndexPublicationResult:
    if not isinstance(evaluation, EvaluationKnowledgeBuild):
        raise TypeError("evaluation must use EvaluationKnowledgeBuild")
    validated = load_evaluation_knowledge(evaluation.model_dump_json())
    if validated.production_eligible is not False:
        raise ValueError("Evaluation Knowledge must remain production-ineligible")
    if any(item.clause.status != "Draft" for item in validated.clauses):
        raise ValueError("Retrieval evaluation accepts only Draft Clauses")

    retrieval_config = load_default_retrieval_config()
    index = build_evaluation_knowledge_index(
        validated,
        retrieval_version=retrieval_config.version,
        embedding_provider=embedding_provider,
    )
    published = store.publish(index)
    if store.load(index.index_version) != index:
        raise RuntimeError("evaluation KnowledgeIndex failed PostgreSQL round-trip validation")
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


def _add_embedding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--embedding-cache", type=Path, default=_default_cache_dir())
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument(
        "--embedding-dimensions",
        type=int,
        default=DEFAULT_EMBEDDING_DIMENSIONS,
    )
    parser.add_argument(
        "--embedding-device",
        choices=("cpu", "cuda"),
        default="cpu",
        help="ONNX Runtime execution device; cuda fails closed if CUDA is unavailable",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=8,
        help="bounded FastEmbed batch size (1..64)",
    )
    parser.add_argument(
        "--embedding-threads",
        type=int,
        default=2,
        help="bounded FastEmbed worker thread count (1..64)",
    )
    parser.add_argument("--local-files-only", action="store_true")


def _embedding_provider(args: argparse.Namespace) -> FastEmbedProvider | None:
    if args.exact_only:
        return None
    return FastEmbedProvider(
        model_id=args.embedding_model,
        dimensions=args.embedding_dimensions,
        cache_dir=args.embedding_cache,
        local_files_only=args.local_files_only,
        execution_device=args.embedding_device,
        batch_size=args.embedding_batch_size,
        threads=args.embedding_threads,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, publish, and activate versioned Retrieval indexes"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser(
        "publish",
        help="build and publish an index from a strict PublishedKnowledgeBuild JSON file",
    )
    publish.add_argument("--publication", type=Path, required=True)
    publish.add_argument("--database-url")
    publish.add_argument("--exact-only", action="store_true")
    _add_embedding_arguments(publish)
    publish.add_argument(
        "--switch-alias",
        metavar="ALIAS",
        help="switch this ready index into an alias after successful publication",
    )

    publish_evaluation = subparsers.add_parser(
        "publish-evaluation",
        help="build and publish an isolated staging index from EvaluationKnowledgeBuild",
    )
    publish_evaluation.add_argument("--evaluation", type=Path, required=True)
    publish_evaluation.add_argument("--database-url")
    publish_evaluation.add_argument("--exact-only", action="store_true")
    _add_embedding_arguments(publish_evaluation)
    publish_evaluation.add_argument(
        "--allow-evaluation-fixture",
        action="store_true",
        help="explicitly acknowledge that this index is not production evidence",
    )
    publish_evaluation.add_argument(
        "--switch-alias",
        metavar="STAGING_ALIAS",
        help="switch into a staging-* alias after successful publication",
    )

    switch = subparsers.add_parser(
        "alias-switch",
        help="atomically switch a ready, fully validated index into an alias",
    )
    switch.add_argument("--database-url")
    switch.add_argument("--index-version", required=True)
    switch.add_argument("--alias", default="current")
    switch.add_argument("--allow-evaluation-fixture", action="store_true")
    switch.add_argument("--allow-golden-fixture", action="store_true")

    resolve = subparsers.add_parser(
        "alias-resolve",
        help="resolve an alias after validating its ready index",
    )
    resolve.add_argument("--database-url")
    resolve.add_argument("--alias", default="current")
    return parser


def _publish(args: argparse.Namespace) -> dict[str, object]:
    publication = load_published_knowledge_file(args.publication)
    provider = _embedding_provider(args)
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
        "embedding_execution_provider": (
            provider.execution_provider if provider is not None else None
        ),
        "embedding_batch_size": provider.batch_size if provider is not None else None,
        "embedding_threads": provider.threads if provider is not None else None,
        "alias": args.switch_alias,
        "alias_changed": alias_changed,
    }


def _publish_evaluation(args: argparse.Namespace) -> dict[str, object]:
    if not args.allow_evaluation_fixture:
        raise ValueError("publish-evaluation requires --allow-evaluation-fixture")
    if args.switch_alias is not None and not _is_staging_alias(args.switch_alias):
        raise ValueError("Evaluation indexes may switch only staging-* aliases")
    evaluation = load_evaluation_knowledge_file(args.evaluation)
    provider = _embedding_provider(args)
    store = PostgresIndexStore(_database_url(args.database_url))
    result = publish_evaluation_knowledge(
        evaluation,
        store,
        embedding_provider=provider,
    )
    alias_changed: bool | None = None
    if args.switch_alias is not None:
        alias_changed = store.switch_alias(
            result.index.index_version,
            args.switch_alias,
            allow_evaluation_fixture=True,
        )
        if store.resolve_alias(args.switch_alias) != result.index.index_version:
            raise RuntimeError("Retrieval alias did not resolve to the evaluation index")
    return {
        "operation": "publish-evaluation",
        "evaluation_build_id": evaluation.build_id,
        "index_origin": result.index.origin,
        "production_eligible": False,
        "index_version": result.index.index_version,
        "record_count": len(result.index.records),
        "published": result.published,
        "embedding_model": result.index.embedding_model,
        "embedding_version": result.index.embedding_version,
        "embedding_dimensions": result.index.embedding_dimensions,
        "embedding_execution_provider": (
            provider.execution_provider if provider is not None else None
        ),
        "embedding_batch_size": provider.batch_size if provider is not None else None,
        "embedding_threads": provider.threads if provider is not None else None,
        "alias": args.switch_alias,
        "alias_changed": alias_changed,
    }


def _alias_switch(args: argparse.Namespace) -> dict[str, object]:
    store = PostgresIndexStore(_database_url(args.database_url))
    index = store.load(args.index_version)
    if index.origin == "evaluation_fixture" and not _is_staging_alias(args.alias):
        raise ValueError("Evaluation indexes may switch only staging-* aliases")
    changed = store.switch_alias(
        args.index_version,
        args.alias,
        allow_evaluation_fixture=args.allow_evaluation_fixture,
        allow_golden_fixture=args.allow_golden_fixture,
    )
    if store.resolve_alias(args.alias) != args.index_version:
        raise RuntimeError("Retrieval alias did not resolve to the requested index")
    return {
        "operation": "alias-switch",
        "alias": args.alias,
        "index_version": args.index_version,
        "changed": changed,
    }


def _is_staging_alias(value: str) -> bool:
    return value.startswith("staging-") and len(value) > len("staging-")


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
        elif args.command == "publish-evaluation":
            payload = _publish_evaluation(args)
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
    "load_evaluation_knowledge_file",
    "load_published_knowledge_file",
    "main",
    "publish_evaluation_knowledge",
    "publish_published_knowledge",
]
