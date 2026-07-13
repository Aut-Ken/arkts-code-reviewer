from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from arkts_code_reviewer.retrieval import embeddings
from arkts_code_reviewer.retrieval.embeddings import FastEmbedProvider


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    available: tuple[str, ...],
    active: tuple[str, ...],
) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    class FakeTextEmbedding:
        def __init__(self, **kwargs: object) -> None:
            calls["init"] = kwargs
            model_dir = Path(str(kwargs["cache_dir"])) / "models--fixture" / "snapshot"
            model_dir.mkdir(parents=True)
            (model_dir / "model.onnx").write_bytes(b"fixture-model")
            session = SimpleNamespace(get_providers=lambda: list(active))
            self.model = SimpleNamespace(
                _model_dir=model_dir,
                model_description=SimpleNamespace(dim=2),
                model=session,
            )

        def passage_embed(self, texts: list[str], **kwargs: object) -> object:
            calls["passage"] = (texts, kwargs)
            return iter((1.0, 0.0) for _ in texts)

        def query_embed(self, texts: list[str], **kwargs: object) -> object:
            calls["query"] = (texts, kwargs)
            return iter((0.0, 1.0) for _ in texts)

    def fake_import(name: str) -> object:
        if name == "fastembed":
            return SimpleNamespace(TextEmbedding=FakeTextEmbedding)
        if name == "onnxruntime":
            return SimpleNamespace(get_available_providers=lambda: list(available))
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(
        embeddings,
        "_fastembed_distribution",
        lambda: ("fastembed-gpu", "0.8.0"),
    )
    return calls


def test_cuda_provider_is_explicit_bounded_and_part_of_embedding_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_runtime(
        monkeypatch,
        available=("CUDAExecutionProvider", "CPUExecutionProvider"),
        active=("CUDAExecutionProvider", "CPUExecutionProvider"),
    )

    provider = FastEmbedProvider(
        model_id="fixture/code-model",
        dimensions=2,
        cache_dir=tmp_path / "cache",
        execution_device="cuda",
        batch_size=4,
        threads=3,
    )

    assert calls["init"]["providers"] == ["CUDAExecutionProvider"]
    assert calls["init"]["threads"] == 3
    assert provider.execution_device == "cuda"
    assert provider.execution_provider == "CUDAExecutionProvider"
    assert provider.batch_size == 4
    assert provider.threads == 3
    assert "fastembed-gpu:0.8.0" in provider.version
    assert "provider=CUDAExecutionProvider:batch=4:threads=3" in provider.version

    assert provider.embed_passages(("first", "second")) == (
        (1.0, 0.0),
        (1.0, 0.0),
    )
    assert provider.embed_query("query") == (0.0, 1.0)
    assert calls["passage"] == (
        ["first", "second"],
        {"batch_size": 4, "parallel": None},
    )
    assert calls["query"] == (["query"], {"batch_size": 4, "parallel": None})


def test_cuda_request_fails_when_onnx_cuda_provider_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_runtime(
        monkeypatch,
        available=("CPUExecutionProvider",),
        active=("CPUExecutionProvider",),
    )

    with pytest.raises(RuntimeError, match="CUDAExecutionProvider is unavailable"):
        FastEmbedProvider(
            model_id="fixture/code-model",
            dimensions=2,
            cache_dir=tmp_path / "cache",
            execution_device="cuda",
        )

    assert "init" not in calls


def test_cuda_request_rejects_silent_cpu_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_runtime(
        monkeypatch,
        available=("CUDAExecutionProvider", "CPUExecutionProvider"),
        active=("CPUExecutionProvider",),
    )

    with pytest.raises(RuntimeError, match="silently fell back"):
        FastEmbedProvider(
            model_id="fixture/code-model",
            dimensions=2,
            cache_dir=tmp_path / "cache",
            execution_device="cuda",
        )


@pytest.mark.parametrize(
    ("argument", "value", "message"),
    [
        ("execution_device", "gpu", "execution_device"),
        ("batch_size", 0, "batch_size"),
        ("batch_size", 65, "batch_size"),
        ("threads", 0, "threads"),
        ("threads", 65, "threads"),
    ],
)
def test_embedding_runtime_limits_fail_closed(
    tmp_path: Path,
    argument: str,
    value: object,
    message: str,
) -> None:
    kwargs: dict[str, object] = {
        "model_id": "fixture/code-model",
        "dimensions": 2,
        "cache_dir": tmp_path / "cache",
        argument: value,
    }

    with pytest.raises(ValueError, match=message):
        FastEmbedProvider(**kwargs)  # type: ignore[arg-type]
