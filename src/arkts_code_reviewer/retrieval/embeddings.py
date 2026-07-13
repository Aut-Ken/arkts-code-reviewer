from __future__ import annotations

import hashlib
import importlib
import math
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

DEFAULT_FASTEMBED_MODEL = "jinaai/jina-embeddings-v2-base-code"
DEFAULT_FASTEMBED_DIMENSIONS = 768


def _cache_fingerprint(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("FastEmbed cache must be a regular directory")
    digest = hashlib.sha256()
    entries = sorted(root.rglob("*"))
    files: list[tuple[Path, Path]] = []
    for path in entries:
        physical = _managed_cache_file(root, path) if path.is_symlink() else path
        if physical.is_file():
            files.append((path, physical))
    if not files:
        raise ValueError("FastEmbed cache is empty after model initialization")
    for logical, physical in files:
        relative = logical.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with physical.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _model_cache_fingerprint(cache_root: Path, model_root: Path) -> str:
    """Hash one initialized model without coupling it to unrelated cache entries."""

    if cache_root.is_symlink() or not cache_root.is_dir():
        raise ValueError("FastEmbed cache must be a regular directory")
    if model_root.is_symlink() or not model_root.is_dir():
        raise ValueError("FastEmbed model cache must be a regular directory")
    cache_resolved = cache_root.resolve()
    model_resolved = model_root.resolve()
    try:
        model_resolved.relative_to(cache_resolved)
    except ValueError as exc:
        raise ValueError("FastEmbed model cache must stay inside the configured cache") from exc

    digest = hashlib.sha256()
    model_identity = model_resolved.relative_to(cache_resolved).as_posix().encode("utf-8")
    digest.update(len(model_identity).to_bytes(8, "big"))
    digest.update(model_identity)
    files: list[tuple[Path, Path]] = []
    for path in sorted(model_resolved.rglob("*")):
        if path.is_symlink():
            physical = _managed_cache_file(cache_resolved, path)
        else:
            physical = path
        if physical.is_file():
            files.append((path, physical))
    if not files:
        raise ValueError("FastEmbed model cache is empty after initialization")
    for logical, physical in files:
        relative = logical.relative_to(model_resolved).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with physical.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _managed_cache_file(root: Path, path: Path) -> Path:
    """Resolve only Hugging Face snapshot links into the same model's blob store."""

    relative = path.relative_to(root)
    if len(relative.parts) < 4 or relative.parts[1] != "snapshots":
        raise ValueError("FastEmbed cache must not contain symlinks outside managed snapshots")
    model_root = root / relative.parts[0]
    blob_root = (model_root / "blobs").resolve()
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(blob_root)
    except (OSError, ValueError) as exc:
        raise ValueError("FastEmbed cache symlink must target its local blob store") from exc
    if not resolved.is_file():
        raise ValueError("FastEmbed cache symlink must target a regular blob")
    return resolved


def _vector(value: Any) -> tuple[float, ...]:
    raw = value.tolist() if hasattr(value, "tolist") else value
    return tuple(float(item) for item in raw)


class FastEmbedProvider:
    """Lazy optional adapter for the local FastEmbed CPU runtime."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_FASTEMBED_MODEL,
        dimensions: int = DEFAULT_FASTEMBED_DIMENSIONS,
        cache_dir: str | Path,
        local_files_only: bool = False,
    ) -> None:
        if (
            not isinstance(model_id, str)
            or not model_id
            or model_id != model_id.strip()
            or not isinstance(dimensions, int)
            or isinstance(dimensions, bool)
            or dimensions < 1
        ):
            raise ValueError("FastEmbed model metadata is invalid")
        if not isinstance(local_files_only, bool):
            raise TypeError("local_files_only must be boolean")
        cache_path = Path(cache_dir).expanduser().absolute()
        if cache_path.is_symlink():
            raise ValueError("FastEmbed cache must not be a symlink")
        cache_path.mkdir(parents=True, exist_ok=True)
        try:
            fastembed = importlib.import_module("fastembed")
            text_embedding = fastembed.TextEmbedding
        except (AttributeError, ImportError) as exc:
            raise RuntimeError(
                "FastEmbed is not installed; install the embedding-local extra"
            ) from exc
        self._model_id = model_id
        self._dimensions = dimensions
        self._model = text_embedding(
            model_name=model_id,
            cache_dir=str(cache_path),
            local_files_only=local_files_only,
            providers=["CPUExecutionProvider"],
        )
        runtime_model = getattr(self._model, "model", None)
        model_path = getattr(runtime_model, "_model_dir", None)
        model_description = getattr(runtime_model, "model_description", None)
        model_dimensions = getattr(model_description, "dim", None)
        if not isinstance(model_path, Path):
            raise RuntimeError("FastEmbed runtime did not expose its initialized model path")
        if model_dimensions != dimensions:
            raise ValueError(
                "FastEmbed configured dimensions do not match the selected model"
            )
        cache_hash = _model_cache_fingerprint(cache_path, model_path)
        self._version = f"fastembed:{package_version('fastembed')}:{model_id}:{cache_hash}"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not isinstance(texts, tuple) or any(
            not isinstance(text, str) or not text for text in texts
        ):
            raise ValueError("FastEmbed passages must be a tuple of non-empty strings")
        vectors = tuple(_vector(value) for value in self._model.passage_embed(list(texts)))
        if len(vectors) != len(texts):
            raise RuntimeError("FastEmbed returned an unexpected passage count")
        return self._validate(vectors)

    def embed_query(self, text: str) -> tuple[float, ...]:
        if not isinstance(text, str) or not text:
            raise ValueError("FastEmbed query must be non-empty text")
        vectors = tuple(_vector(value) for value in self._model.query_embed([text]))
        if len(vectors) != 1:
            raise RuntimeError("FastEmbed returned an unexpected query count")
        return self._validate(vectors)[0]

    def _validate(
        self,
        vectors: tuple[tuple[float, ...], ...],
    ) -> tuple[tuple[float, ...], ...]:
        if any(
            len(value) != self._dimensions or any(not math.isfinite(item) for item in value)
            for value in vectors
        ):
            raise RuntimeError("FastEmbed vectors do not match configured finite dimensions")
        return vectors


__all__ = [
    "DEFAULT_FASTEMBED_DIMENSIONS",
    "DEFAULT_FASTEMBED_MODEL",
    "FastEmbedProvider",
]
