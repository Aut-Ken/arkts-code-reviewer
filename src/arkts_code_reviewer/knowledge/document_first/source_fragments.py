from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
    load_json_model,
    sha256_text,
)
from arkts_code_reviewer.knowledge.document_first.models import MarkdownDocumentMap
from arkts_code_reviewer.knowledge.document_first.source_atoms import (
    SourceAtom,
    SourceAtomSet,
    slice_source_atom_text,
    verify_source_atom_set,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    verify_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef

SOURCE_FRAGMENT_SET_SCHEMA_VERSION: Literal["source-fragment-set-v1"] = (
    "source-fragment-set-v1"
)
SOURCE_FRAGMENT_BUILDER_VERSION: Literal["markdown-source-fragment-v1"] = (
    "markdown-source-fragment-v1"
)
SOURCE_FRAGMENT_OFFSET_UNIT: Literal["utf8_byte_within_atom_half_open"] = (
    "utf8_byte_within_atom_half_open"
)
SOURCE_FRAGMENT_MAX_CODEPOINTS = 800

_HASH = r"[0-9a-f]{64}"
_SHA256_RE = re.compile(rf"^sha256:{_HASH}$")
_MAP_ID_RE = re.compile(rf"^markdown-document-map:sha256:{_HASH}$")
_ATOM_ID_RE = re.compile(rf"^source-atom:sha256:{_HASH}$")
_ATOM_SET_ID_RE = re.compile(rf"^source-atom-set:sha256:{_HASH}$")
_FRAGMENT_ID_RE = re.compile(rf"^source-fragment:sha256:{_HASH}$")
_FRAGMENT_SET_ID_RE = re.compile(rf"^source-fragment-set:sha256:{_HASH}$")

_PROSE_ATOM_KINDS = frozenset({"paragraph", "list_item", "blockquote", "note"})
_FULLWIDTH_TERMINATORS = frozenset("。！？；")
_ASCII_TERMINATORS = frozenset("!?;")
_CLOSING_PUNCTUATION = frozenset("\"'”’」』）)]}》〉")
_SOFT_SPLIT_CHARACTERS = frozenset("，、,:：。！？；!?; \t\n")

SourceFragmentKind = Literal[
    "sentence",
    "line",
    "bounded_segment",
    "whole_atom",
]


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _strict_source_ref(value: object, context: str) -> object:
    if isinstance(value, SourceRef):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a SourceRef object")
    for field in (
        "source_id",
        "revision",
        "relative_path",
        "anchor",
        "authority",
        "content_hash",
    ):
        if field in value and not isinstance(value[field], str):
            raise ValueError(f"{context}.{field} must be a string")
    content_hash = value.get("content_hash")
    if isinstance(content_hash, str) and not _SHA256_RE.fullmatch(content_hash):
        raise ValueError(f"{context}.content_hash must use canonical sha256:<hex> form")
    return value


class Utf8ByteSpan(FrozenModel):
    start_byte: Annotated[int, Field(ge=0)]
    end_byte: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end_byte <= self.start_byte:
            raise ValueError("Utf8ByteSpan.end_byte must be greater than start_byte")
        return self


def _strict_utf8_span(value: object, context: str) -> object:
    if isinstance(value, Utf8ByteSpan):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a Utf8ByteSpan object")
    for field in ("start_byte", "end_byte"):
        if field in value and type(value[field]) is not int:
            raise ValueError(f"{context}.{field} must be an integer")
    return value


class _SourceFragmentFields(FrozenModel):
    ordinal: Annotated[int, Field(ge=0)]
    atom_id: Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)]
    atom_ordinal: Annotated[int, Field(ge=0)]
    atom_fragment_ordinal: Annotated[int, Field(ge=0)]
    kind: SourceFragmentKind
    relative_utf8_span: Utf8ByteSpan
    atom_utf8_length: Annotated[int, Field(ge=1)]
    text_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]

    @field_validator("relative_utf8_span", mode="before")
    @classmethod
    def parse_utf8_span(cls, value: object) -> object:
        return _strict_utf8_span(value, "SourceFragment.relative_utf8_span")

    @model_validator(mode="after")
    def validate_within_atom(self) -> Self:
        if self.relative_utf8_span.end_byte > self.atom_utf8_length:
            raise ValueError("SourceFragment span exceeds atom_utf8_length")
        return self


class _SourceFragmentPayload(_SourceFragmentFields):
    pass


class SourceFragment(_SourceFragmentFields):
    fragment_id: Annotated[str, Field(pattern=_FRAGMENT_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_fragment_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"fragment_id"})
        if self.fragment_id != canonical_hash("source-fragment", payload):
            raise ValueError("SourceFragment.fragment_id does not match its complete contents")
        return self


class _SourceFragmentSetFields(FrozenModel):
    schema_version: Literal["source-fragment-set-v1"] = SOURCE_FRAGMENT_SET_SCHEMA_VERSION
    document_id: Annotated[str, Field(min_length=1)]
    source_ref: SourceRef
    normalized_body_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]
    document_map_id: Annotated[str, Field(pattern=_MAP_ID_RE.pattern)]
    atom_set_id: Annotated[str, Field(pattern=_ATOM_SET_ID_RE.pattern)]
    atom_count: Annotated[int, Field(ge=1)]
    fragment_count: Annotated[int, Field(ge=1)]
    builder_version: Literal["markdown-source-fragment-v1"] = (
        SOURCE_FRAGMENT_BUILDER_VERSION
    )
    offset_unit: Literal["utf8_byte_within_atom_half_open"] = (
        SOURCE_FRAGMENT_OFFSET_UNIT
    )
    fragments: tuple[SourceFragment, ...]
    use_scope: Literal["semantic_projection_input_only_not_evidence"] = (
        "semantic_projection_input_only_not_evidence"
    )
    evidence_eligible: Literal[False] = False
    production_qualified: Literal[False] = False
    qualification: Literal["exact_source_partition_not_semantically_qualified"] = (
        "exact_source_partition_not_semantically_qualified"
    )

    @field_validator("source_ref", mode="before")
    @classmethod
    def parse_source_ref(cls, value: object) -> object:
        return _strict_source_ref(value, "SourceFragmentSet.source_ref")

    @field_validator("fragments", mode="before")
    @classmethod
    def parse_fragments(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"SourceFragmentSet.{info.field_name}")

    @model_validator(mode="after")
    def validate_inventory(self) -> Self:
        if self.fragment_count != len(self.fragments):
            raise ValueError("SourceFragmentSet.fragment_count does not match fragments")
        if tuple(fragment.ordinal for fragment in self.fragments) != tuple(
            range(len(self.fragments))
        ):
            raise ValueError("SourceFragmentSet fragment ordinals must be contiguous and ordered")
        fragment_ids = tuple(fragment.fragment_id for fragment in self.fragments)
        if len(fragment_ids) != len(set(fragment_ids)):
            raise ValueError("SourceFragmentSet fragment IDs must be unique")

        groups: dict[int, list[SourceFragment]] = {}
        for fragment in self.fragments:
            groups.setdefault(fragment.atom_ordinal, []).append(fragment)
        if tuple(groups) != tuple(range(self.atom_count)):
            raise ValueError(
                "SourceFragmentSet must contain at least one ordered Fragment for every Atom"
            )

        seen_atom_ids: set[str] = set()
        for atom_ordinal, group in groups.items():
            atom_ids = {fragment.atom_id for fragment in group}
            if len(atom_ids) != 1:
                raise ValueError("SourceFragmentSet Atom group mixes different atom IDs")
            atom_id = next(iter(atom_ids))
            if atom_id in seen_atom_ids:
                raise ValueError("SourceFragmentSet atom IDs must be unique across Atom groups")
            seen_atom_ids.add(atom_id)
            if any(fragment.atom_ordinal != atom_ordinal for fragment in group):
                raise ValueError("SourceFragmentSet Atom group ordinal mismatch")
            if tuple(fragment.atom_fragment_ordinal for fragment in group) != tuple(
                range(len(group))
            ):
                raise ValueError(
                    "SourceFragmentSet atom_fragment_ordinals must be contiguous and ordered"
                )
            lengths = {fragment.atom_utf8_length for fragment in group}
            if len(lengths) != 1:
                raise ValueError("SourceFragmentSet Atom group has inconsistent byte lengths")
            atom_length = next(iter(lengths))
            cursor = 0
            for fragment in group:
                span = fragment.relative_utf8_span
                if span.start_byte != cursor:
                    raise ValueError(
                        "SourceFragmentSet Fragments must partition each Atom without gaps "
                        "or overlaps"
                    )
                cursor = span.end_byte
            if cursor != atom_length:
                raise ValueError(
                    "SourceFragmentSet Fragments must cover the complete Atom UTF-8 text"
                )
        return self


class _SourceFragmentSetPayload(_SourceFragmentSetFields):
    pass


class SourceFragmentSet(_SourceFragmentSetFields):
    fragment_set_id: Annotated[str, Field(pattern=_FRAGMENT_SET_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_fragment_set_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"fragment_set_id"})
        if self.fragment_set_id != canonical_hash("source-fragment-set", payload):
            raise ValueError(
                "SourceFragmentSet.fragment_set_id does not match its complete contents"
            )
        return self


def _is_ascii_terminator(text: str, index: int) -> bool:
    if text[index] not in _ASCII_TERMINATORS:
        return False
    next_index = index + 1
    while next_index < len(text) and text[next_index] in _CLOSING_PUNCTUATION:
        next_index += 1
    return next_index == len(text) or text[next_index].isspace()


def _preferred_char_spans(text: str) -> tuple[tuple[int, int, SourceFragmentKind], ...]:
    spans: list[tuple[int, int, SourceFragmentKind]] = []
    start = 0
    index = 0
    while index < len(text):
        character = text[index]
        kind: SourceFragmentKind | None = None
        if character == "\n":
            kind = "line"
        elif character in _FULLWIDTH_TERMINATORS or _is_ascii_terminator(text, index):
            kind = "sentence"
        if kind is None:
            index += 1
            continue

        end = index + 1
        if character != "\n":
            while end < len(text) and text[end] in _CLOSING_PUNCTUATION:
                end += 1
        while end < len(text) and text[end].isspace():
            end += 1
        if text[start:end]:
            spans.append((start, end, kind))
        start = end
        index = end

    if start < len(text):
        spans.append((start, len(text), "bounded_segment"))
    if not spans:
        return ((0, len(text), "whole_atom"),)
    return tuple(spans)


def _bounded_char_spans(
    text: str,
    start: int,
    end: int,
    kind: SourceFragmentKind,
) -> tuple[tuple[int, int, SourceFragmentKind], ...]:
    if end - start <= SOURCE_FRAGMENT_MAX_CODEPOINTS:
        return ((start, end, kind),)

    spans: list[tuple[int, int, SourceFragmentKind]] = []
    cursor = start
    while end - cursor > SOURCE_FRAGMENT_MAX_CODEPOINTS:
        hard_end = cursor + SOURCE_FRAGMENT_MAX_CODEPOINTS
        search_start = cursor + SOURCE_FRAGMENT_MAX_CODEPOINTS // 2
        split = hard_end
        for candidate in range(hard_end, search_start, -1):
            if text[candidate - 1] in _SOFT_SPLIT_CHARACTERS:
                split = candidate
                break
        while split < hard_end and text[split].isspace():
            split += 1
        spans.append((cursor, split, "bounded_segment"))
        cursor = split
    if cursor < end:
        spans.append((cursor, end, kind))
    return tuple(spans)


def _fragment_char_spans(
    text: str,
    atom: SourceAtom,
) -> tuple[tuple[int, int, SourceFragmentKind], ...]:
    if atom.kind not in _PROSE_ATOM_KINDS:
        return ((0, len(text), "whole_atom"),)
    preferred = _preferred_char_spans(text)
    bounded = tuple(
        segment
        for start, end, kind in preferred
        for segment in _bounded_char_spans(text, start, end, kind)
    )
    if len(bounded) == 1 and bounded[0][:2] == (0, len(text)):
        return ((0, len(text), "whole_atom"),)
    return bounded


def _utf8_offsets(text: str) -> tuple[int, ...]:
    offsets = [0]
    total = 0
    for character in text:
        total += len(character.encode("utf-8"))
        offsets.append(total)
    return tuple(offsets)


def build_source_fragment_set(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
) -> SourceFragmentSet:
    trusted_document = NormalizedDocument.model_validate(document.model_dump(mode="json"))
    trusted_map = MarkdownDocumentMap.model_validate(document_map.model_dump(mode="json"))
    trusted_atoms = SourceAtomSet.model_validate(atom_set.model_dump(mode="json"))
    verify_markdown_document_map(trusted_document, trusted_map)
    verify_source_atom_set(trusted_document, trusted_map, trusted_atoms)

    fragments: list[SourceFragment] = []
    for atom in trusted_atoms.atoms:
        atom_text = slice_source_atom_text(trusted_document, atom)
        atom_bytes = atom_text.encode("utf-8")
        if not atom_bytes:
            raise ValueError("Source Fragment builder cannot segment an empty Source Atom")
        offsets = _utf8_offsets(atom_text)
        spans = _fragment_char_spans(atom_text, atom)
        for atom_fragment_ordinal, (start, end, kind) in enumerate(spans):
            start_byte = offsets[start]
            end_byte = offsets[end]
            fragment_bytes = atom_bytes[start_byte:end_byte]
            try:
                fragment_text = fragment_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:  # pragma: no cover - builder invariant
                raise ValueError("Source Fragment boundary is not valid UTF-8") from exc
            provisional = _SourceFragmentPayload(
                ordinal=len(fragments),
                atom_id=atom.atom_id,
                atom_ordinal=atom.ordinal,
                atom_fragment_ordinal=atom_fragment_ordinal,
                kind=kind,
                relative_utf8_span=Utf8ByteSpan(
                    start_byte=start_byte,
                    end_byte=end_byte,
                ),
                atom_utf8_length=len(atom_bytes),
                text_hash=sha256_text(fragment_text),
            ).model_dump(mode="json")
            provisional["fragment_id"] = canonical_hash("source-fragment", provisional)
            fragments.append(SourceFragment.model_validate(provisional))

    payload = _SourceFragmentSetPayload(
        document_id=trusted_document.document_id,
        source_ref=trusted_document.source_ref,
        normalized_body_hash=trusted_map.normalized_body_hash,
        document_map_id=trusted_map.map_id,
        atom_set_id=trusted_atoms.atom_set_id,
        atom_count=len(trusted_atoms.atoms),
        fragment_count=len(fragments),
        fragments=tuple(fragments),
        use_scope="semantic_projection_input_only_not_evidence",
        evidence_eligible=False,
        production_qualified=False,
        qualification="exact_source_partition_not_semantically_qualified",
    ).model_dump(mode="json")
    payload["fragment_set_id"] = canonical_hash("source-fragment-set", payload)
    return SourceFragmentSet.model_validate(payload)


def verify_source_fragment_set(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    fragment_set: SourceFragmentSet,
) -> None:
    rebuilt = build_source_fragment_set(document, document_map, atom_set)
    validated = SourceFragmentSet.model_validate(fragment_set.model_dump(mode="json"))
    if rebuilt != validated:
        raise ValueError("Source Fragment Set does not match the trusted source inputs")


def slice_source_fragment_text(
    document: NormalizedDocument,
    atom: SourceAtom,
    fragment: SourceFragment,
) -> str:
    trusted_document = NormalizedDocument.model_validate(document.model_dump(mode="json"))
    trusted_atom = SourceAtom.model_validate(atom.model_dump(mode="json"))
    trusted_fragment = SourceFragment.model_validate(fragment.model_dump(mode="json"))
    if trusted_fragment.atom_id != trusted_atom.atom_id:
        raise ValueError("Source Fragment does not belong to the supplied Source Atom")
    atom_text = slice_source_atom_text(trusted_document, trusted_atom)
    atom_bytes = atom_text.encode("utf-8")
    if len(atom_bytes) != trusted_fragment.atom_utf8_length:
        raise ValueError("Source Fragment atom_utf8_length does not match the trusted Atom")
    span = trusted_fragment.relative_utf8_span
    try:
        text = atom_bytes[span.start_byte : span.end_byte].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Source Fragment boundary is not valid UTF-8") from exc
    if sha256_text(text) != trusted_fragment.text_hash:
        raise ValueError("Source Fragment text hash does not match the trusted source slice")
    return text


def load_source_fragment_set(
    raw: str | bytes,
    *,
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
) -> SourceFragmentSet:
    fragment_set = load_json_model(raw, SourceFragmentSet, "Source Fragment Set")
    verify_source_fragment_set(document, document_map, atom_set, fragment_set)
    return fragment_set


__all__ = [
    "SOURCE_FRAGMENT_BUILDER_VERSION",
    "SOURCE_FRAGMENT_MAX_CODEPOINTS",
    "SOURCE_FRAGMENT_OFFSET_UNIT",
    "SOURCE_FRAGMENT_SET_SCHEMA_VERSION",
    "SourceFragment",
    "SourceFragmentKind",
    "SourceFragmentSet",
    "Utf8ByteSpan",
    "build_source_fragment_set",
    "load_source_fragment_set",
    "slice_source_fragment_text",
    "verify_source_fragment_set",
]
