from __future__ import annotations

from pathlib import PurePosixPath

from markdown_it import MarkdownIt

from arkts_code_reviewer.knowledge.adapters.base import (
    GitObjectReader,
    SourceObject,
    content_sha256,
)
from arkts_code_reviewer.knowledge.models import (
    HeadingNode,
    NormalizedDocument,
    SourceRef,
    SourceSpan,
)


def _decode_text(raw: bytes, relative_path: str) -> str:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Knowledge source must be UTF-8: {relative_path}") from exc
    if "\0" in text:
        raise ValueError(f"Knowledge source contains NUL: {relative_path}")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _markdown_structure(body: str) -> tuple[tuple[HeadingNode, ...], str | None]:
    parser = MarkdownIt("commonmark").enable("table")
    tokens = parser.parse(body)
    headings: list[HeadingNode] = []
    title: str | None = None
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        if inline is None or inline.type != "inline" or not inline.content.strip():
            continue
        level = int(token.tag[1:])
        heading = HeadingNode(
            level=level,
            title=inline.content.strip(),
            span=SourceSpan(start_line=token.map[0] + 1, end_line=token.map[1]),
        )
        headings.append(heading)
        if title is None and level == 1:
            title = heading.title
    return tuple(headings), title


class _BaseTextAdapter:
    source_id: str
    adapter_version: str
    default_language: str

    def load(self, source: SourceObject, reader: GitObjectReader) -> NormalizedDocument:
        if source.source_id != self.source_id:
            raise ValueError(f"{type(self).__name__} cannot load {source.source_id}")
        raw = reader.read_bytes(source)
        body = _decode_text(raw, source.relative_path)
        diagnostics: list[str] = []
        if source.media_type == "text/markdown":
            heading_tree, title = _markdown_structure(body)
            if title is None:
                diagnostics.append("missing_h1_title")
        else:
            heading_tree = ()
            title = None
        if title is None:
            title = PurePosixPath(source.relative_path).name
        language = self.default_language
        if source.relative_path.startswith("zh-cn/"):
            language = "zh-CN"
        return NormalizedDocument(
            document_id=f"{source.source_id}:{source.relative_path}",
            source_ref=SourceRef(
                source_id=source.source_id,
                revision=source.revision,
                relative_path=source.relative_path,
                anchor="document",
                authority=source.authority,
                content_hash=content_sha256(raw),
            ),
            media_type=source.media_type,
            title=title,
            heading_tree=heading_tree,
            body=body,
            language=language,
            adapter_version=self.adapter_version,
            diagnostics=tuple(sorted(diagnostics)),
        )


class ArkuiSpecAdapter(_BaseTextAdapter):
    source_id = "arkui-specs"
    adapter_version = "arkui-spec-adapter-v1"
    default_language = "zh-CN"


class OpenHarmonyDocsAdapter(_BaseTextAdapter):
    source_id = "openharmony-docs"
    adapter_version = "openharmony-docs-adapter-v1"
    default_language = "zh-CN"


class InterfaceSdkAdapter(_BaseTextAdapter):
    source_id = "interface-sdk-js"
    adapter_version = "interface-sdk-adapter-v1"
    default_language = "en"


__all__ = ["ArkuiSpecAdapter", "InterfaceSdkAdapter", "OpenHarmonyDocsAdapter"]
