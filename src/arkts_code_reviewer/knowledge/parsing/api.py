from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
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

from arkts_code_reviewer.knowledge.models import (
    ApiAvailability,
    ApiSymbol,
    NormalizedDocument,
    SourceRef,
    SourceSpan,
)

API_PARSER_VERSION = "knowledge-api-parser-v1"
_PRODUCER_VERSION = "knowledge-api-parser-sidecar-v1.0.0"
_REPOSITORY_SIDECAR = (
    Path(__file__).resolve().parents[4] / "sidecars" / "knowledge-api-parser" / "parse_api.js"
)
_PACKAGED_SIDECAR = (
    Path(__file__).resolve().parents[1] / "sidecars" / "api_parser" / "parse_api.js"
)
_DEFAULT_SIDECAR = (
    _REPOSITORY_SIDECAR if _REPOSITORY_SIDECAR.is_file() else _PACKAGED_SIDECAR
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _RawSpan(_FrozenModel):
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_order(self) -> _RawSpan:
        if self.end_line < self.start_line:
            raise ValueError("API parser span must be ordered")
        return self


class _RawSymbol(_FrozenModel):
    availability: list[ApiAvailability]
    canonical_name: Annotated[str, Field(min_length=1)]
    deprecated_since: Annotated[int | None, Field(ge=1)]
    diagnostics: list[str]
    kind: Annotated[str, Field(min_length=1)]
    module: Annotated[str, Field(min_length=1)]
    permissions: list[str]
    signature: Annotated[str, Field(min_length=1)]
    since: Annotated[int | None, Field(ge=1)]
    source_span: _RawSpan
    system_capabilities: list[str]

    @field_validator("diagnostics", "permissions", "system_capabilities")
    @classmethod
    def validate_sorted_unique(cls, value: list[str], info: ValidationInfo) -> list[str]:
        if any(not item for item in value) or list(value) != sorted(set(value)):
            raise ValueError(f"API parser {info.field_name} must be sorted and unique")
        return value


class _RawOutput(_FrozenModel):
    output_schema: Literal["knowledge-api-parser-v1"]
    producer_version: Literal["knowledge-api-parser-sidecar-v1.0.0"]
    parser: Literal["tree-sitter-arkts"]
    parser_version: Literal["0.2.0"]
    path: Annotated[str, Field(min_length=1)]
    root_type: Literal["program"]
    node_count: Annotated[int, Field(ge=1)]
    error_nodes: Annotated[int, Field(ge=0)]
    missing_nodes: Annotated[int, Field(ge=0)]
    symbols: list[_RawSymbol]
    diagnostics: list[str]

    @field_validator("diagnostics")
    @classmethod
    def validate_diagnostics(cls, value: list[str]) -> list[str]:
        if any(not item for item in value) or list(value) != sorted(set(value)):
            raise ValueError("API parser diagnostics must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_symbol_order(self) -> _RawOutput:
        keys = [
            (
                item.canonical_name,
                item.signature,
                item.source_span.start_line,
                item.source_span.end_line,
            )
            for item in self.symbols
        ]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("API parser symbols must be sorted and exact-duplicate free")
        return self


class ApiCatalogParseResult(_FrozenModel):
    parser_version: Annotated[str, Field(min_length=1)] = API_PARSER_VERSION
    symbols: tuple[ApiSymbol, ...]
    diagnostics: tuple[str, ...] = ()

    @field_validator("diagnostics")
    @classmethod
    def validate_diagnostics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or list(value) != sorted(set(value)):
            raise ValueError("ApiCatalogParseResult.diagnostics must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_symbol_order(self) -> ApiCatalogParseResult:
        keys = [
            (
                item.canonical_name,
                item.signature,
                item.source_span.start_line,
                item.source_span.end_line,
            )
            for item in self.symbols
        ]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError(
                "ApiCatalogParseResult.symbols must preserve deterministic overload order"
            )
        return self


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_output(raw: str) -> object:
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid Knowledge API parser output: {exc}") from exc


def _module_name(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    name = path.name
    for suffix in (".d.ets", ".d.ts"):
        if name.endswith(suffix):
            name = name.removesuffix(suffix)
            break
    if path.parts and path.parts[0] == "api" and name.startswith("@"):
        return name
    if name == "Global.static":
        return "global"
    without_suffix = PurePosixPath(*path.parts[:-1], name)
    return str(without_suffix)


def _catalog_fragment_version(document: NormalizedDocument, parser_version: str) -> str:
    payload = {
        "source_id": document.source_ref.source_id,
        "revision": document.source_ref.revision,
        "relative_path": document.source_ref.relative_path,
        "content_hash": document.source_ref.content_hash,
        "parser_version": parser_version,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"api-catalog-fragment:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _source_ref(document: NormalizedDocument, span: SourceSpan) -> SourceRef:
    source = document.source_ref
    return SourceRef(
        source_id=source.source_id,
        revision=source.revision,
        relative_path=source.relative_path,
        anchor=f"L{span.start_line}-L{span.end_line}",
        authority=source.authority,
        content_hash=source.content_hash,
    )


def parse_api_symbols(
    document: NormalizedDocument,
    *,
    sidecar_path: str | Path = _DEFAULT_SIDECAR,
    timeout: float = 30.0,
) -> ApiCatalogParseResult:
    """Extract declaration-backed API overloads from one normalized SDK document.

    The parser is intentionally independent from Parser v1. Any tree-sitter ERROR or
    missing node fails the document instead of publishing a partial catalog.
    """

    if document.media_type != "text/typescript-declaration":
        raise ValueError("Knowledge API parser requires text/typescript-declaration")
    executable = shutil.which("node")
    if executable is None:
        raise ValueError("Knowledge API parser requires Node.js")
    script = Path(sidecar_path)
    if not script.is_file():
        raise ValueError(f"Knowledge API parser sidecar is unavailable: {script}")
    module_name = _module_name(document.source_ref.relative_path)
    try:
        completed = subprocess.run(
            [
                executable,
                str(script),
                "--path",
                document.source_ref.relative_path,
                "--module",
                module_name,
            ],
            input=document.body,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Knowledge API parser sidecar failed to run") from exc
    payload = _load_output(completed.stdout)
    if completed.returncode != 0:
        detail = payload.get("error") if isinstance(payload, dict) else None
        raise ValueError(f"Knowledge API parser sidecar failed: {detail or 'unknown error'}")
    output = _RawOutput.model_validate(payload)
    if output.path != document.source_ref.relative_path:
        raise ValueError("Knowledge API parser path provenance drift")
    if output.error_nodes or output.missing_nodes:
        raise ValueError(
            "Knowledge API parser rejected syntax-degraded source: "
            f"error_nodes={output.error_nodes}, missing_nodes={output.missing_nodes}"
        )
    line_count = len(document.body.splitlines())
    parser_version = f"{_PRODUCER_VERSION}/tree-sitter-arkts@{output.parser_version}"
    catalog_version = _catalog_fragment_version(document, parser_version)
    symbols: list[ApiSymbol] = []
    for item in output.symbols:
        if item.source_span.end_line > line_count:
            raise ValueError("Knowledge API parser emitted an out-of-range source span")
        span = SourceSpan(
            start_line=item.source_span.start_line,
            end_line=item.source_span.end_line,
        )
        symbols.append(
            ApiSymbol.create(
                canonical_name=item.canonical_name,
                aliases=(),
                module=item.module,
                kind=item.kind,
                signature=item.signature,
                since=item.since,
                deprecated_since=item.deprecated_since,
                permissions=tuple(item.permissions),
                system_capabilities=tuple(item.system_capabilities),
                availability=tuple(item.availability),
                source_ref=_source_ref(document, span),
                source_span=span,
                catalog_version=catalog_version,
                diagnostics=tuple(item.diagnostics),
            )
        )
    return ApiCatalogParseResult(
        parser_version=parser_version,
        symbols=tuple(symbols),
        diagnostics=tuple(output.diagnostics),
    )


__all__ = ["API_PARSER_VERSION", "ApiCatalogParseResult", "parse_api_symbols"]
