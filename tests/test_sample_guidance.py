from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from arkts_code_reviewer.knowledge.models import SourceRef, SourceSpan
from arkts_code_reviewer.knowledge.sample_guidance import (
    SAMPLE_GUIDANCE_AUTHORITY,
    SAMPLE_GUIDANCE_MANIFEST_HASH,
    SampleGuidanceBuild,
    SampleGuidancePassage,
    SampleGuidanceSource,
    _extract_document_passages,
    build_sample_guidance,
    load_sample_guidance_build,
    render_sample_guidance_build,
)
from arkts_code_reviewer.retrieval_validation.app_samples import (
    APP_SAMPLES_REVISION,
    APP_SAMPLES_SOURCE_ID,
    AppSampleEntry,
    load_app_samples_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/fixtures/applications_app_samples_v1.json"
LOCAL_CHECKOUT = Path("/home/autken/Code/applications_app_samples")

EXPECTED_PASSAGE_COUNT = 182
EXPECTED_BUILD_ID = (
    "sample-guidance-build:sha256:2efc3f6cdcb6f4a3ffad8b8296f83e3b4265304cf03c7c35a48636a62f12bb16"
)
EXPECTED_MANIFEST_HASH = "sha256:a03bc1276f9c3e798d399168cfbc56bc56247f5a26f8509cdcbb70b8e3ba54e9"
EXPECTED_ORDER_HASH = "sha256:2d6fd63a4a593decf97dc0948ed35c837489ad73b105a881a26092c4fac141cb"
EXPECTED_COUNTS = {
    "code/DocsSample/Ability/UIAbilityLifecycle/README_zh.md": 23,
    "code/DocsSample/ArkTS/ArkTsConcurrent/ApplicationMultithreadingDevelopment/"
    "PracticalCases/README.md": 16,
    "code/DocsSample/ArkTS/ArkTsConcurrent/AsyncConcurrencyOverview/README.md": 7,
    "code/DocsSample/ArkUISample/ComponentStateManagement/README_zh.md": 11,
    "code/DocsSample/ArkUISample/ImageComponent/README_zh.md": 13,
    "code/DocsSample/ArkUISample/Navigation/README_zh.md": 8,
    "code/DocsSample/ArkUISample/ParadigmStateManagement/README_zh.md": 47,
    "code/DocsSample/NetWork_Kit/NetWorkKit_Datatransmission/HTTP_case/README_zh.md": 49,
    "code/DocsSample/ResourceManagement/ResourceCategoriesAndAccess/README_zh.md": 8,
}


def _minimal_build() -> SampleGuidanceBuild:
    sources: list[SampleGuidanceSource] = []
    passages: list[SampleGuidancePassage] = []
    manifest = load_app_samples_manifest(MANIFEST)
    entries = [entry for entry in manifest.entries if entry.kind == "sample_guidance"]
    for number, entry in enumerate(entries):
        path = entry.path
        content_hash = entry.sha256
        source = SampleGuidanceSource(
            relative_path=path,
            content_hash=content_hash,
            line_count=entry.line_count,
            topics=entry.topics,
        )
        span = SourceSpan(start_line=2, end_line=2)
        source_ref = SourceRef(
            source_id=APP_SAMPLES_SOURCE_ID,
            revision=APP_SAMPLES_REVISION,
            relative_path=path,
            anchor="L2-L2",
            authority=SAMPLE_GUIDANCE_AUTHORITY,
            content_hash=content_hash,
        )
        sources.append(source)
        passages.append(
            SampleGuidancePassage.create(
                source_ref=source_ref,
                source_span=span,
                heading_path=("Sample", "介绍"),
                text=f"Context passage {number:02d}",
                topics=entry.topics,
            )
        )
    return SampleGuidanceBuild.create(
        sources=tuple(sources),
        passages=tuple(passages),
    )


def _write_build(tmp_path: Path, payload: dict[str, Any] | None = None) -> Path:
    if payload is None:
        return _write_raw(tmp_path, render_sample_guidance_build(_minimal_build()))
    return _write_raw(
        tmp_path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _write_raw(tmp_path: Path, raw: str) -> Path:
    path = tmp_path / "sample-guidance.json"
    path.write_text(raw, encoding="utf-8")
    return path


def _payload() -> dict[str, Any]:
    return _minimal_build().model_dump(mode="json")


def test_markdown_extraction_keeps_context_and_excludes_noise() -> None:
    raw = """# Sample title

### 介绍

保留介绍段落。

[纯链接](https://example.invalid)

![预览图](preview.png)

<img src="preview.png" />

### 效果预览

必须排除的效果说明。

使用说明

1. 保留操作步骤。

### 工程目录

必须排除的目录说明。

### 具体实现

#### 子机制

调用 [API](https://example.invalid/api) 完成操作。

### 相关权限

必须排除的权限段落。

### 约束与限制

- 仅支持测试环境。

### 下载

git clone must-not-appear
""".encode()
    entry = AppSampleEntry(
        path="docs/README.md",
        sha256=f"sha256:{hashlib.sha256(raw).hexdigest()}",
        line_count=len(raw.splitlines()),
        kind="sample_guidance",
        case_role="neutral",
        topics=("sample",),
        normative=False,
    )

    passages = _extract_document_passages(entry=entry, raw=raw)

    assert [item.text for item in passages] == [
        "保留介绍段落。",
        "保留操作步骤。",
        "调用 API 完成操作。",
        "仅支持测试环境。",
    ]
    assert [item.heading_path for item in passages] == [
        ("Sample title", "介绍"),
        ("Sample title", "使用说明"),
        ("Sample title", "具体实现", "子机制"),
        ("Sample title", "约束与限制"),
    ]
    assert all(item.normative is False for item in passages)
    assert all(item.evidence_role == "context_only" for item in passages)
    assert all(item.source_span.start_line >= 1 for item in passages)


def test_build_round_trip_is_canonical_and_non_normative(tmp_path: Path) -> None:
    build = _minimal_build()
    rendered = render_sample_guidance_build(build)
    loaded = load_sample_guidance_build(
        _write_raw(tmp_path, rendered),
        manifest_path=MANIFEST,
    )

    assert loaded == build
    assert render_sample_guidance_build(loaded) == rendered
    assert all(item.normative is False for item in loaded.passages)
    assert all(item.evidence_role == "context_only" for item in loaded.passages)


def test_loader_rejects_duplicate_key_and_unknown_field(tmp_path: Path) -> None:
    rendered = render_sample_guidance_build(_minimal_build())
    duplicate = rendered.replace(
        '  "source_id": "applications-app-samples",',
        '  "source_id": "applications-app-samples",\n  "source_id": "applications-app-samples",',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON key: source_id"):
        load_sample_guidance_build(
            _write_raw(tmp_path, duplicate),
            manifest_path=MANIFEST,
        )

    payload = _payload()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_sample_guidance_build(
            _write_build(tmp_path, payload),
            manifest_path=MANIFEST,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("normative", True), ("evidence_role", "rule_evidence")],
)
def test_loader_rejects_evidence_promotion(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload = _payload()
    payload["passages"][0][field] = value

    with pytest.raises(ValueError):
        load_sample_guidance_build(
            _write_build(tmp_path, payload),
            manifest_path=MANIFEST,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["passages"][0].__setitem__("text", "drift"),
        lambda payload: payload["passages"][0]["source_span"].__setitem__("end_line", 11),
        lambda payload: payload["sources"][0].__setitem__("content_hash", "sha256:" + "0" * 64),
        lambda payload: payload.__setitem__("build_id", "sample-guidance-build:sha256:" + "0" * 64),
    ],
)
def test_loader_rejects_content_hash_and_span_drift(
    tmp_path: Path,
    mutate: Any,
) -> None:
    payload = _payload()
    mutate(payload)

    with pytest.raises(ValueError):
        load_sample_guidance_build(
            _write_build(tmp_path, payload),
            manifest_path=MANIFEST,
        )


def test_loader_checks_manifest_hash_when_manifest_is_supplied(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    build = _minimal_build()
    build_path = _write_raw(tmp_path, render_sample_guidance_build(build))

    with pytest.raises(ValueError, match="manifest hash mismatch"):
        load_sample_guidance_build(build_path, manifest_path=manifest)


def test_real_allowlisted_guidance_build_is_stable_when_checkout_exists(
    tmp_path: Path,
) -> None:
    if not LOCAL_CHECKOUT.exists():
        return

    first = build_sample_guidance(MANIFEST, LOCAL_CHECKOUT)
    second = build_sample_guidance(MANIFEST, LOCAL_CHECKOUT)
    rendered = render_sample_guidance_build(first)
    loaded = load_sample_guidance_build(
        _write_raw(tmp_path, rendered),
        manifest_path=MANIFEST,
    )
    order_hash = (
        "sha256:"
        + hashlib.sha256(
            ("\n".join(item.passage_id for item in first.passages) + "\n").encode()
        ).hexdigest()
    )

    assert first == second == loaded
    assert len(first.sources) == 9
    assert len(first.passages) == EXPECTED_PASSAGE_COUNT
    assert first.build_id == EXPECTED_BUILD_ID
    assert first.manifest_hash == EXPECTED_MANIFEST_HASH
    assert first.manifest_hash == SAMPLE_GUIDANCE_MANIFEST_HASH
    assert order_hash == EXPECTED_ORDER_HASH
    assert Counter(item.source_ref.relative_path for item in first.passages) == EXPECTED_COUNTS
    assert all(item.normative is False for item in first.passages)
    assert all(item.evidence_role == "context_only" for item in first.passages)
    assert all(
        item.source_span.end_line
        <= next(
            source.line_count
            for source in first.sources
            if source.relative_path == item.source_ref.relative_path
        )
        for item in first.passages
    )
