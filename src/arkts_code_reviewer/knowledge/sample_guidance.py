from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.knowledge.models import SourceRef, SourceSpan
from arkts_code_reviewer.retrieval_validation.app_samples import (
    APP_SAMPLES_REVISION,
    APP_SAMPLES_SOURCE_ID,
    AppSampleEntry,
    AppSamplesManifest,
    load_app_samples_manifest,
    verify_checkout,
)

SAMPLE_GUIDANCE_SCHEMA_VERSION: Literal["sample-guidance-build-v1"] = "sample-guidance-build-v1"
SAMPLE_GUIDANCE_PARSER_VERSION: Literal["sample-guidance-parser-v1"] = "sample-guidance-parser-v1"
SAMPLE_GUIDANCE_AUTHORITY = "official-sample-guidance"
SAMPLE_GUIDANCE_MANIFEST_HASH: Literal[
    "sha256:a03bc1276f9c3e798d399168cfbc56bc56247f5a26f8509cdcbb70b8e3ba54e9"
] = "sha256:a03bc1276f9c3e798d399168cfbc56bc56247f5a26f8509cdcbb70b8e3ba54e9"
_SOURCE_ID: Literal["applications-app-samples"] = "applications-app-samples"
_REVISION: Literal["8255a2987f70317cc3a2a4d46044c6b55f092bb3"] = (
    "8255a2987f70317cc3a2a4d46044c6b55f092bb3"
)

_ALLOWED_SECTIONS = {"介绍", "使用说明", "具体实现", "约束与限制"}
_EXCLUDED_SECTIONS = {
    "效果预览",
    "工程目录",
    "工程目录结构",
    "目录结构",
    "下载",
    "相关权限",
    "依赖",
}
_SPACE_RE = re.compile(r"\s+")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _file_hash(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _normalize_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip()


def _validate_topics(value: tuple[str, ...], context: str) -> tuple[str, ...]:
    if (
        not value
        or any(
            not topic
            or topic != topic.strip()
            or topic.lower() != topic
            or not all(character.isalnum() or character == "-" for character in topic)
            for topic in value
        )
        or list(value) != sorted(set(value))
    ):
        raise ValueError(f"{context} must be non-empty, sorted, and unique")
    return value


class SampleGuidanceSource(_FrozenModel):
    relative_path: Annotated[str, Field(min_length=1)]
    content_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    line_count: Annotated[int, Field(ge=1)]
    topics: tuple[str, ...]

    @field_validator("topics", mode="before")
    @classmethod
    def parse_topics(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "Sample guidance source topics")

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.as_posix() != value
            or ".." in path.parts
            or "\\" in value
            or not path.name.startswith("README")
            or path.suffix != ".md"
        ):
            raise ValueError("Sample guidance source path is invalid")
        return value

    @field_validator("topics")
    @classmethod
    def validate_topics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_topics(value, "Sample guidance source topics")


class SampleGuidancePassage(_FrozenModel):
    passage_id: Annotated[
        str,
        Field(pattern=r"^sample-guidance-passage:sha256:[0-9a-f]{64}$"),
    ]
    source_ref: SourceRef
    source_span: SourceSpan
    heading_path: tuple[str, ...]
    text: Annotated[str, Field(min_length=1)]
    topics: tuple[str, ...]
    normative: Literal[False]
    evidence_role: Literal["context_only"]

    @field_validator("heading_path", "topics", mode="before")
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "Sample guidance passage collections")

    @field_validator("heading_path")
    @classmethod
    def validate_heading_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item or item != item.strip() for item in value):
            raise ValueError("Sample guidance heading path must contain trimmed text")
        return value

    @field_validator("topics")
    @classmethod
    def validate_topics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_topics(value, "Sample guidance passage topics")

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError("Sample guidance passage text must be normalized to one line")
        return value

    def identity_payload(self) -> dict[str, object]:
        return {
            "source_ref": self.source_ref.model_dump(mode="json"),
            "source_span": self.source_span.model_dump(mode="json"),
            "heading_path": list(self.heading_path),
            "text": self.text,
            "topics": list(self.topics),
            "normative": self.normative,
            "evidence_role": self.evidence_role,
        }

    def expected_passage_id(self) -> str:
        return _canonical_hash("sample-guidance-passage", self.identity_payload())

    @model_validator(mode="after")
    def validate_identity(self) -> SampleGuidancePassage:
        expected_anchor = f"L{self.source_span.start_line}-L{self.source_span.end_line}"
        if self.source_ref.anchor != expected_anchor:
            raise ValueError("Sample guidance SourceRef anchor must match source span")
        if self.passage_id != self.expected_passage_id():
            raise ValueError("Sample guidance passage_id does not match content")
        return self

    @classmethod
    def create(
        cls,
        *,
        source_ref: SourceRef,
        source_span: SourceSpan,
        heading_path: tuple[str, ...],
        text: str,
        topics: tuple[str, ...],
    ) -> SampleGuidancePassage:
        payload = {
            "source_ref": source_ref.model_dump(mode="json"),
            "source_span": source_span.model_dump(mode="json"),
            "heading_path": list(heading_path),
            "text": text,
            "topics": list(topics),
            "normative": False,
            "evidence_role": "context_only",
        }
        return cls(
            passage_id=_canonical_hash("sample-guidance-passage", payload),
            source_ref=source_ref,
            source_span=source_span,
            heading_path=heading_path,
            text=text,
            topics=topics,
            normative=False,
            evidence_role="context_only",
        )


class SampleGuidanceBuild(_FrozenModel):
    schema_version: Literal["sample-guidance-build-v1"] = SAMPLE_GUIDANCE_SCHEMA_VERSION
    build_id: Annotated[
        str,
        Field(pattern=r"^sample-guidance-build:sha256:[0-9a-f]{64}$"),
    ]
    source_id: Literal["applications-app-samples"]
    revision: Literal["8255a2987f70317cc3a2a4d46044c6b55f092bb3"]
    manifest_hash: Literal[
        "sha256:a03bc1276f9c3e798d399168cfbc56bc56247f5a26f8509cdcbb70b8e3ba54e9"
    ]
    parser_version: Literal["sample-guidance-parser-v1"]
    sources: tuple[SampleGuidanceSource, ...]
    passages: tuple[SampleGuidancePassage, ...]

    @field_validator("sources", "passages", mode="before")
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "Sample guidance build collections")

    def identity_payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "revision": self.revision,
            "manifest_hash": self.manifest_hash,
            "parser_version": self.parser_version,
            "sources": [item.model_dump(mode="json") for item in self.sources],
            "passages": [item.model_dump(mode="json") for item in self.passages],
        }

    def expected_build_id(self) -> str:
        return _canonical_hash("sample-guidance-build", self.identity_payload())

    @model_validator(mode="after")
    def validate_build(self) -> SampleGuidanceBuild:
        source_paths = [item.relative_path for item in self.sources]
        if len(source_paths) != 9 or source_paths != sorted(set(source_paths)):
            raise ValueError("Sample guidance build must contain 9 sorted unique sources")
        source_by_path = {item.relative_path: item for item in self.sources}

        passage_keys = [
            (
                item.source_ref.relative_path,
                item.source_span.start_line,
                item.source_span.end_line,
                item.passage_id,
            )
            for item in self.passages
        ]
        if not passage_keys or passage_keys != sorted(set(passage_keys)):
            raise ValueError("Sample guidance passages must be non-empty, sorted, and unique")
        if {item.source_ref.relative_path for item in self.passages} != set(source_paths):
            raise ValueError("Sample guidance passages must cover every guidance source")

        for passage in self.passages:
            source = source_by_path.get(passage.source_ref.relative_path)
            if source is None:
                raise ValueError("Sample guidance passage references an unknown source")
            if (
                passage.source_ref.source_id != self.source_id
                or passage.source_ref.revision != self.revision
                or passage.source_ref.authority != SAMPLE_GUIDANCE_AUTHORITY
                or passage.source_ref.content_hash != source.content_hash
            ):
                raise ValueError("Sample guidance passage provenance does not match source")
            if passage.source_span.end_line > source.line_count:
                raise ValueError("Sample guidance passage span exceeds source line count")
            if passage.topics != source.topics:
                raise ValueError("Sample guidance passage topics do not match source")

        if self.build_id != self.expected_build_id():
            raise ValueError("Sample guidance build_id does not match content")
        return self

    @classmethod
    def create(
        cls,
        *,
        sources: tuple[SampleGuidanceSource, ...],
        passages: tuple[SampleGuidancePassage, ...],
    ) -> SampleGuidanceBuild:
        payload = {
            "source_id": _SOURCE_ID,
            "revision": _REVISION,
            "manifest_hash": SAMPLE_GUIDANCE_MANIFEST_HASH,
            "parser_version": SAMPLE_GUIDANCE_PARSER_VERSION,
            "sources": [item.model_dump(mode="json") for item in sources],
            "passages": [item.model_dump(mode="json") for item in passages],
        }
        return cls(
            build_id=_canonical_hash("sample-guidance-build", payload),
            source_id=_SOURCE_ID,
            revision=_REVISION,
            manifest_hash=SAMPLE_GUIDANCE_MANIFEST_HASH,
            parser_version=SAMPLE_GUIDANCE_PARSER_VERSION,
            sources=sources,
            passages=passages,
        )


def _plain_inline(token: Token) -> tuple[str, bool]:
    children = token.children or []
    parts: list[str] = []
    link_depth = 0
    outside_link_parts: list[str] = []
    has_link = False
    has_image = False
    for child in children:
        if child.type == "link_open":
            has_link = True
            link_depth += 1
        elif child.type == "link_close":
            link_depth = max(0, link_depth - 1)
        elif child.type == "image":
            has_image = True
        elif child.type in {"text", "code_inline"}:
            parts.append(child.content)
            if link_depth == 0:
                outside_link_parts.append(child.content)
        elif child.type == "html_inline" and re.search(
            r"<\s*(?:img|picture|source)\b",
            child.content,
            re.IGNORECASE,
        ):
            has_image = True
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
            if link_depth == 0:
                outside_link_parts.append(" ")
    text = _normalize_text("".join(parts))
    meaningful_outside_link = any(character.isalnum() for character in "".join(outside_link_parts))
    is_noise = not text or (has_image and not text) or (has_link and not meaningful_outside_link)
    return text, is_noise


def _heading_title(tokens: list[Token], index: int) -> str:
    if index + 1 >= len(tokens) or tokens[index + 1].type != "inline":
        raise ValueError("Markdown heading is missing inline content")
    text, _ = _plain_inline(tokens[index + 1])
    if not text:
        raise ValueError("Markdown heading cannot be empty")
    return text


def _section_name(text: str) -> str | None:
    normalized = _normalize_text(text).strip("#：: ")
    if normalized in _ALLOWED_SECTIONS or normalized in _EXCLUDED_SECTIONS:
        return normalized
    return None


def _passage_heading_path(
    document_title: str,
    active_section: str,
    current_heading: str | None,
) -> tuple[str, ...]:
    values = [document_title, active_section]
    if current_heading is not None and current_heading not in {
        document_title,
        active_section,
    }:
        values.append(current_heading)
    return tuple(values)


def _extract_document_passages(
    *,
    entry: AppSampleEntry,
    raw: bytes,
) -> tuple[SampleGuidancePassage, ...]:
    text = raw.decode("utf-8")
    tokens = MarkdownIt("commonmark").parse(text)
    document_title = PurePosixPath(entry.path).stem
    current_heading: str | None = None
    active_section: str | None = None
    passages: list[SampleGuidancePassage] = []

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "heading_open":
            title = _heading_title(tokens, index)
            level = int(token.tag.removeprefix("h"))
            if level == 1:
                document_title = title
                active_section = None
            else:
                section = _section_name(title)
                if section in _ALLOWED_SECTIONS:
                    active_section = section
                elif section in _EXCLUDED_SECTIONS:
                    active_section = None
                current_heading = title
            index += 1
        elif token.type == "paragraph_open":
            if index + 1 >= len(tokens) or tokens[index + 1].type != "inline":
                raise ValueError("Markdown paragraph is missing inline content")
            inline = tokens[index + 1]
            paragraph, is_noise = _plain_inline(inline)
            pseudo_section = _section_name(paragraph)
            if pseudo_section is not None and pseudo_section in _ALLOWED_SECTIONS:
                active_section = pseudo_section
                current_heading = pseudo_section
            elif pseudo_section is not None and pseudo_section in _EXCLUDED_SECTIONS:
                active_section = None
                current_heading = pseudo_section
            elif active_section is not None and not is_noise and token.map is not None:
                start_line = token.map[0] + 1
                end_line = token.map[1]
                source_span = SourceSpan(start_line=start_line, end_line=end_line)
                source_ref = SourceRef(
                    source_id=APP_SAMPLES_SOURCE_ID,
                    revision=APP_SAMPLES_REVISION,
                    relative_path=entry.path,
                    anchor=f"L{start_line}-L{end_line}",
                    authority=SAMPLE_GUIDANCE_AUTHORITY,
                    content_hash=entry.sha256,
                )
                passages.append(
                    SampleGuidancePassage.create(
                        source_ref=source_ref,
                        source_span=source_span,
                        heading_path=_passage_heading_path(
                            document_title,
                            active_section,
                            current_heading,
                        ),
                        text=paragraph,
                        topics=entry.topics,
                    )
                )
        index += 1

    return tuple(passages)


def build_sample_guidance(
    manifest_path: Path,
    checkout_root: Path,
) -> SampleGuidanceBuild:
    manifest: AppSamplesManifest = load_app_samples_manifest(manifest_path)
    actual_manifest_hash = _file_hash(manifest_path)
    if actual_manifest_hash != SAMPLE_GUIDANCE_MANIFEST_HASH:
        raise ValueError(
            "applications_app_samples v1 manifest hash mismatch: "
            f"expected {SAMPLE_GUIDANCE_MANIFEST_HASH}, got {actual_manifest_hash}"
        )
    verify_checkout(manifest, checkout_root)
    root = checkout_root.resolve(strict=True)
    guidance_entries = tuple(entry for entry in manifest.entries if entry.kind == "sample_guidance")
    sources = tuple(
        SampleGuidanceSource(
            relative_path=entry.path,
            content_hash=entry.sha256,
            line_count=entry.line_count,
            topics=entry.topics,
        )
        for entry in guidance_entries
    )
    passages = tuple(
        sorted(
            (
                passage
                for entry in guidance_entries
                for passage in _extract_document_passages(
                    entry=entry,
                    raw=root.joinpath(*PurePosixPath(entry.path).parts).read_bytes(),
                )
            ),
            key=lambda item: (
                item.source_ref.relative_path,
                item.source_span.start_line,
                item.source_span.end_line,
                item.passage_id,
            ),
        )
    )
    return SampleGuidanceBuild.create(
        sources=sources,
        passages=passages,
    )


def render_sample_guidance_build(build: SampleGuidanceBuild) -> str:
    return (
        json.dumps(
            build.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def load_sample_guidance_build(
    path: Path,
    *,
    manifest_path: Path,
) -> SampleGuidanceBuild:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        build = SampleGuidanceBuild.model_validate(payload)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        _DuplicateKeyError,
    ) as exc:
        raise ValueError(f"invalid sample guidance build {path}: {exc}") from exc
    actual_manifest_hash = _file_hash(manifest_path)
    if build.manifest_hash != actual_manifest_hash:
        raise ValueError(
            "sample guidance manifest hash mismatch: "
            f"expected {build.manifest_hash}, got {actual_manifest_hash}"
        )
    manifest = load_app_samples_manifest(manifest_path)
    expected_sources = tuple(
        SampleGuidanceSource(
            relative_path=entry.path,
            content_hash=entry.sha256,
            line_count=entry.line_count,
            topics=entry.topics,
        )
        for entry in manifest.entries
        if entry.kind == "sample_guidance"
    )
    if build.sources != expected_sources:
        raise ValueError("sample guidance sources drift from the frozen manifest")
    return build


__all__ = [
    "SAMPLE_GUIDANCE_AUTHORITY",
    "SAMPLE_GUIDANCE_MANIFEST_HASH",
    "SAMPLE_GUIDANCE_PARSER_VERSION",
    "SAMPLE_GUIDANCE_SCHEMA_VERSION",
    "SampleGuidanceBuild",
    "SampleGuidancePassage",
    "SampleGuidanceSource",
    "build_sample_guidance",
    "load_sample_guidance_build",
    "render_sample_guidance_build",
]
