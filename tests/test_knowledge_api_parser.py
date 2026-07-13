from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef
from arkts_code_reviewer.knowledge.parsing.api import parse_api_symbols

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ROOT / "sidecars/knowledge-api-parser/parse_api.js"
SIDECAR_GRAMMAR = ROOT / "sidecars/knowledge-api-parser/node_modules/tree-sitter-arkts/package.json"
GOLDEN_ROOT = ROOT / "tests/golden/knowledge/sources"
INTERFACE_SDK = Path(
    "/home/autken/Code/arkts-knowledge/sources/api-metadata/interface_sdk-js"
)
INTERFACE_SDK_REVISION = "e3f5c3bb3282c1c4b08cbed209250a563e0b4e7b"
INTERFACE_SEED_PATHS = (
    "api/@ohos.arkui.StateManagement.d.ts",
    "api/@ohos.arkui.observer.d.ts",
    "api/@ohos.events.emitter.d.ts",
    "api/@ohos.systemTimer.d.ts",
    "api/@ohos.taskpool.d.ts",
    "api/@ohos.worker.d.ts",
    "api/arkui/stateManagement/decorator.static.d.ets",
    "arkts/builtin/static/Global.static.d.ets",
)


def _document(
    body: str,
    *,
    relative_path: str = "api/@ohos.taskpool.d.ts",
    revision: str = "0" * 40,
) -> NormalizedDocument:
    return NormalizedDocument(
        document_id=f"interface-sdk-js:{relative_path}",
        source_ref=SourceRef(
            source_id="interface-sdk-js",
            revision=revision,
            relative_path=relative_path,
            anchor="document",
            authority="official_api_definition",
            content_hash=f"sha256:{hashlib.sha256(body.encode()).hexdigest()}",
        ),
        media_type="text/typescript-declaration",
        title=_pure_path_name(relative_path),
        heading_tree=(),
        body=body,
        language="en",
        adapter_version="interface-sdk-adapter-v1",
    )


def _pure_path_name(relative_path: str) -> str:
    return relative_path.rsplit("/", 1)[-1]


def _require_sidecar() -> None:
    if not SIDECAR_GRAMMAR.is_file():
        pytest.skip("Knowledge API sidecar dependencies are not installed")


def _golden_projection(document: NormalizedDocument) -> list[dict[str, object]]:
    result = parse_api_symbols(document)
    return [
        {
            "canonical_name": item.canonical_name,
            "kind": item.kind,
            "signature": item.signature,
            "since": item.since,
            "deprecated_since": item.deprecated_since,
            "source_span": item.source_span.model_dump(mode="json"),
        }
        for item in result.symbols
    ]


def test_api_parser_matches_kg011_namespace_class_truth() -> None:
    _require_sidecar()
    body = (GOLDEN_ROOT / "KG011_api_taskpool.d.ts").read_text(encoding="utf-8")
    document = _document(body, relative_path="KG011_api_taskpool.d.ts")

    assert _golden_projection(document) == [
        {
            "canonical_name": "taskpool.Task",
            "kind": "class",
            "signature": "class Task",
            "since": 9,
            "deprecated_since": None,
            "source_span": {"start_line": 6, "end_line": 9},
        },
        {
            "canonical_name": "taskpool.Task.isCanceled",
            "kind": "method",
            "signature": "static isCanceled(): boolean",
            "since": 9,
            "deprecated_since": None,
            "source_span": {"start_line": 8, "end_line": 8},
        },
    ]
    result = parse_api_symbols(document)
    assert result.diagnostics == ()
    assert result.symbols[0].system_capabilities == ("SystemCapability.Utils.Lang",)
    assert result.symbols[0].source_ref.anchor == "L6-L9"


def test_api_parser_matches_kg012_deprecation_truth() -> None:
    _require_sidecar()
    body = (GOLDEN_ROOT / "KG012_api_deprecated.d.ts").read_text(encoding="utf-8")

    assert _golden_projection(_document(body, relative_path="KG012_api_deprecated.d.ts")) == [
        {
            "canonical_name": "timer.legacyStart",
            "kind": "function",
            "signature": "function legacyStart(): number",
            "since": 9,
            "deprecated_since": 12,
            "source_span": {"start_line": 6, "end_line": 6},
        }
    ]


def test_api_parser_preserves_overloads_and_refuses_lossy_mode_since() -> None:
    _require_sidecar()
    body = """/**
 * @syscap SystemCapability.Notification.Emitter
 */
declare namespace emitter {
  /** @since 7 dynamic\n   * @since 23 static */
  function on(eventId: string): void;
  /** @since 7 dynamiconly\n   * @since 23 staticonly */
  function on(eventId: number): void;
}
"""
    result = parse_api_symbols(_document(body, relative_path="api/@ohos.events.emitter.d.ts"))

    assert [item.canonical_name for item in result.symbols] == ["emitter.on", "emitter.on"]
    assert [item.signature for item in result.symbols] == [
        "function on(eventId: number): void",
        "function on(eventId: string): void",
    ]
    assert [item.since for item in result.symbols] == [None, None]
    assert [
        [(availability.language_mode, availability.since) for availability in item.availability]
        for item in result.symbols
    ] == [
        [("dynamic", 7), ("static", 23)],
        [("dynamic", 7), ("static", 23)],
    ]
    assert [item.diagnostics for item in result.symbols] == [(), ()]
    assert result.diagnostics == ()


def test_api_parser_ignores_constructor_and_inline_object_properties() -> None:
    _require_sidecar()
    body = """/** @since 9 */
declare namespace sample {
  class Task {
    constructor();
    run(options: { navigationId: string }): void;
  }
  function observe(options: { navigationId: string }): void;
}
"""
    result = parse_api_symbols(_document(body))

    assert [(item.canonical_name, item.kind) for item in result.symbols] == [
        ("sample.Task", "class"),
        ("sample.Task.run", "method"),
        ("sample.observe", "function"),
    ]
    assert all("navigationId" not in item.canonical_name for item in result.symbols)


def test_api_parser_only_publishes_explicit_permission_tags() -> None:
    _require_sidecar()
    body = """/**
 * A prose mention of ohos.permission.NOT_EVIDENCE is not a permission tag.
 * @syscap SystemCapability.Utils.Lang
 * @since 9
 */
declare namespace sample {
  /**
   * @permission ohos.permission.ALPHA or ohos.permission.BETA
   * @since 9
   */
  function secured(): void;
}
"""
    symbol = parse_api_symbols(_document(body)).symbols[0]

    assert symbol.permissions == ("ohos.permission.ALPHA", "ohos.permission.BETA")
    assert symbol.system_capabilities == ("SystemCapability.Utils.Lang",)


def test_api_parser_rejects_tree_sitter_error_or_missing_node() -> None:
    _require_sidecar()
    with pytest.raises(ValueError, match="rejected syntax-degraded source"):
        parse_api_symbols(_document("declare namespace broken {\n  class Missing {\n"))


@pytest.mark.skipif(
    not INTERFACE_SDK.is_dir(),
    reason="registered interface-sdk-js checkout is unavailable",
)
def test_api_parser_real_eight_file_seed_smoke() -> None:
    _require_sidecar()
    head = subprocess.run(
        ["git", "-C", str(INTERFACE_SDK), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != INTERFACE_SDK_REVISION:
        pytest.skip("registered interface-sdk-js checkout revision drift")

    symbol_count = 0
    availability_count = 0
    for relative_path in INTERFACE_SEED_PATHS:
        body = subprocess.run(
            [
                "git",
                "-C",
                str(INTERFACE_SDK),
                "show",
                f"{INTERFACE_SDK_REVISION}:{relative_path}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        document = _document(
            body,
            relative_path=relative_path,
            revision=INTERFACE_SDK_REVISION,
        )
        result = parse_api_symbols(document)
        assert result.symbols
        assert all(
            item.source_ref.content_hash == document.source_ref.content_hash
            for item in result.symbols
        )
        symbol_count += len(result.symbols)
        availability_count += sum(len(item.availability) for item in result.symbols)

    assert symbol_count >= 500
    assert availability_count >= 40
