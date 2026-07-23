from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Literal, Self, cast

from markdown_it import MarkdownIt
from pydantic import Field, ValidationInfo, field_validator, model_validator

from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
    load_json_model,
    sha256_text,
)
from arkts_code_reviewer.knowledge.document_first.models import (
    MarkdownDocumentMap,
    MarkdownSection,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    detect_markdown_front_matter_span,
    verify_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef, SourceSpan

SOURCE_ATOM_SET_SCHEMA_VERSION: Literal["source-atom-set-v1"] = "source-atom-set-v1"
SOURCE_ATOM_BUILDER_VERSION: Literal["markdown-source-atom-v1"] = "markdown-source-atom-v1"

_HASH = r"[0-9a-f]{64}"
_SHA256_RE = re.compile(rf"^sha256:{_HASH}$")
_SECTION_ID_RE = re.compile(rf"^document-section:sha256:{_HASH}$")
_DOCUMENT_MAP_ID_RE = re.compile(rf"^markdown-document-map:sha256:{_HASH}$")
_ATOM_ID_RE = re.compile(rf"^source-atom:sha256:{_HASH}$")
_REGION_ID_RE = re.compile(rf"^source-region:sha256:{_HASH}$")
_ATOM_SET_ID_RE = re.compile(rf"^source-atom-set:sha256:{_HASH}$")
_NOTE_MARKER_RE = re.compile(
    r"^\s*>\s*\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]",
    re.IGNORECASE,
)
_HTML_COMMENT_RE = re.compile(r"\s*(?:<!--[\s\S]*?-->\s*)+")

SourceAtomKind = Literal[
    "paragraph",
    "list_item",
    "table",
    "code_block",
    "blockquote",
    "note",
    "raw_block",
]
SourceRegionKind = Literal[
    "heading",
    "blank",
    "html_comment",
    "thematic_break",
    "front_matter",
]


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _trimmed_single_line(value: str, context: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{context} must be non-empty and trimmed")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{context} must be a single line without control characters")
    return value


def _strict_source_span(value: object, context: str) -> object:
    if isinstance(value, SourceSpan):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a SourceSpan object")
    for field in ("start_line", "end_line"):
        if field in value and type(value[field]) is not int:
            raise ValueError(f"{context}.{field} must be an integer")
    return value


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


def _validate_unique_strings(
    values: tuple[str, ...],
    context: str,
    *,
    sorted_values: bool,
) -> tuple[str, ...]:
    for value in values:
        _trimmed_single_line(value, context)
    if len(values) != len(set(values)):
        raise ValueError(f"{context} must not contain duplicates")
    if sorted_values and values != tuple(sorted(values)):
        raise ValueError(f"{context} must be sorted")
    return values


class SourceAtom(FrozenModel):
    atom_id: Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)]
    ordinal: Annotated[int, Field(ge=0)]
    kind: SourceAtomKind
    section_id: Annotated[str, Field(pattern=_SECTION_ID_RE.pattern)]
    heading_path: tuple[str, ...]
    source_span: SourceSpan
    text_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]
    required_context_atom_ids: tuple[Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)], ...] = ()

    @field_validator("heading_path", "required_context_atom_ids", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"SourceAtom.{info.field_name}")

    @field_validator("source_span", mode="before")
    @classmethod
    def parse_source_span(cls, value: object) -> object:
        return _strict_source_span(value, "SourceAtom.source_span")

    @field_validator("heading_path")
    @classmethod
    def validate_heading_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _trimmed_single_line(item, "SourceAtom.heading_path")
        return value

    @field_validator("required_context_atom_ids")
    @classmethod
    def validate_required_context(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _validate_unique_strings(
            value,
            "SourceAtom.required_context_atom_ids",
            sorted_values=True,
        )


class SourceRegion(FrozenModel):
    region_id: Annotated[str, Field(pattern=_REGION_ID_RE.pattern)]
    ordinal: Annotated[int, Field(ge=0)]
    kind: SourceRegionKind
    source_span: SourceSpan
    text_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]

    @field_validator("source_span", mode="before")
    @classmethod
    def parse_source_span(cls, value: object) -> object:
        return _strict_source_span(value, "SourceRegion.source_span")


class _SourceAtomSetFields(FrozenModel):
    schema_version: Literal["source-atom-set-v1"] = SOURCE_ATOM_SET_SCHEMA_VERSION
    document_id: Annotated[str, Field(min_length=1)]
    source_ref: SourceRef
    normalized_body_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]
    document_map_id: Annotated[str, Field(pattern=_DOCUMENT_MAP_ID_RE.pattern)]
    source_line_count: Annotated[int, Field(ge=1)]
    builder_version: Literal["markdown-source-atom-v1"] = SOURCE_ATOM_BUILDER_VERSION
    atoms: tuple[SourceAtom, ...]
    regions: tuple[SourceRegion, ...]
    use_scope: Literal["retrieval_projection_input_only_not_evidence"] = (
        "retrieval_projection_input_only_not_evidence"
    )
    evidence_eligible: Literal[False] = False
    production_qualified: Literal[False] = False
    qualification: Literal["structural_atom_contract_not_quality_qualified"] = (
        "structural_atom_contract_not_quality_qualified"
    )

    @field_validator("source_ref", mode="before")
    @classmethod
    def parse_source_ref(cls, value: object) -> object:
        return _strict_source_ref(value, "SourceAtomSet.source_ref")

    @field_validator("atoms", "regions", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"SourceAtomSet.{info.field_name}")

    @model_validator(mode="after")
    def validate_inventory(self) -> Self:
        if not self.atoms:
            raise ValueError(
                "SourceAtomSet requires at least one eligible Markdown content block"
            )
        if tuple(atom.ordinal for atom in self.atoms) != tuple(range(len(self.atoms))):
            raise ValueError("SourceAtomSet atom ordinals must be contiguous and ordered")
        if tuple(region.ordinal for region in self.regions) != tuple(range(len(self.regions))):
            raise ValueError("SourceAtomSet region ordinals must be contiguous and ordered")

        atom_ids = tuple(atom.atom_id for atom in self.atoms)
        region_ids = tuple(region.region_id for region in self.regions)
        if len(atom_ids) != len(set(atom_ids)):
            raise ValueError("SourceAtomSet atom IDs must be unique")
        if len(region_ids) != len(set(region_ids)):
            raise ValueError("SourceAtomSet region IDs must be unique")

        known_atom_ids = set(atom_ids)
        for atom in self.atoms:
            if atom.atom_id in atom.required_context_atom_ids:
                raise ValueError("SourceAtom cannot require itself as context")
            if not set(atom.required_context_atom_ids).issubset(known_atom_ids):
                raise ValueError("SourceAtom required context references an unknown Atom")

            expected_atom_id = _atom_id(
                document_id=self.document_id,
                source_ref=self.source_ref,
                normalized_body_hash=self.normalized_body_hash,
                document_map_id=self.document_map_id,
                atom=atom,
            )
            if atom.atom_id != expected_atom_id:
                raise ValueError("SourceAtom.atom_id does not match its source identity")

        for region in self.regions:
            expected_region_id = _region_id(
                document_id=self.document_id,
                source_ref=self.source_ref,
                normalized_body_hash=self.normalized_body_hash,
                document_map_id=self.document_map_id,
                region=region,
            )
            if region.region_id != expected_region_id:
                raise ValueError("SourceRegion.region_id does not match its source identity")

        units = sorted(
            (
                *((atom.source_span.start_line, atom.source_span.end_line) for atom in self.atoms),
                *(
                    (region.source_span.start_line, region.source_span.end_line)
                    for region in self.regions
                ),
            )
        )
        if not units or units[0][0] != 1 or units[-1][1] != self.source_line_count:
            raise ValueError("SourceAtomSet units must cover every physical source line")
        previous_end = 0
        for start_line, end_line in units:
            if start_line != previous_end + 1:
                raise ValueError(
                    "SourceAtomSet units must partition physical source lines "
                    "without gaps or overlaps"
                )
            previous_end = end_line
        return self


class _SourceAtomSetPayload(_SourceAtomSetFields):
    pass


class SourceAtomSet(_SourceAtomSetFields):
    atom_set_id: Annotated[str, Field(pattern=_ATOM_SET_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_atom_set_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"atom_set_id"})
        if self.atom_set_id != canonical_hash("source-atom-set", payload):
            raise ValueError("SourceAtomSet.atom_set_id does not match its complete contents")
        return self


@dataclass(frozen=True)
class _UnitDraft:
    kind: SourceAtomKind | SourceRegionKind
    source_span: SourceSpan
    section_id: str | None
    heading_path: tuple[str, ...]
    eligible: bool


def _source_lines(body: str) -> tuple[str, ...]:
    parts = body.split("\n")
    lines = tuple(f"{part}\n" for part in parts[:-1])
    if parts[-1]:
        return (*lines, parts[-1])
    return lines


def _slice_lines(lines: tuple[str, ...], span: SourceSpan) -> str:
    return "".join(lines[span.start_line - 1 : span.end_line])


def _span(start_line: int, end_line: int) -> SourceSpan:
    return SourceSpan(start_line=start_line, end_line=end_line)


def _atom_id(
    *,
    document_id: str,
    source_ref: SourceRef,
    normalized_body_hash: str,
    document_map_id: str,
    atom: SourceAtom,
) -> str:
    return canonical_hash(
        "source-atom",
        {
            "document_id": document_id,
            "source_ref": source_ref.model_dump(mode="json"),
            "normalized_body_hash": normalized_body_hash,
            "document_map_id": document_map_id,
            "ordinal": atom.ordinal,
            "kind": atom.kind,
            "section_id": atom.section_id,
            "heading_path": atom.heading_path,
            "source_span": atom.source_span.model_dump(mode="json"),
            "text_hash": atom.text_hash,
        },
    )


def _region_id(
    *,
    document_id: str,
    source_ref: SourceRef,
    normalized_body_hash: str,
    document_map_id: str,
    region: SourceRegion,
) -> str:
    return canonical_hash(
        "source-region",
        {
            "document_id": document_id,
            "source_ref": source_ref.model_dump(mode="json"),
            "normalized_body_hash": normalized_body_hash,
            "document_map_id": document_map_id,
            "ordinal": region.ordinal,
            "kind": region.kind,
            "source_span": region.source_span.model_dump(mode="json"),
            "text_hash": region.text_hash,
        },
    )


def _gap_drafts(
    lines: tuple[str, ...],
    *,
    start_line: int,
    end_line: int,
    section: MarkdownSection | None,
) -> tuple[_UnitDraft, ...]:
    if end_line < start_line:
        return ()
    drafts: list[_UnitDraft] = []
    run_start = start_line
    run_is_blank = not lines[start_line - 1].strip()
    for line_number in range(start_line + 1, end_line + 1):
        is_blank = not lines[line_number - 1].strip()
        if is_blank == run_is_blank:
            continue
        drafts.append(
            _gap_run_draft(
                run_start,
                line_number - 1,
                is_blank=run_is_blank,
                section=section,
            )
        )
        run_start = line_number
        run_is_blank = is_blank
    drafts.append(_gap_run_draft(run_start, end_line, is_blank=run_is_blank, section=section))
    return tuple(drafts)


def _gap_run_draft(
    start_line: int,
    end_line: int,
    *,
    is_blank: bool,
    section: MarkdownSection | None,
) -> _UnitDraft:
    if is_blank:
        return _UnitDraft(
            kind="blank",
            source_span=_span(start_line, end_line),
            section_id=None,
            heading_path=(),
            eligible=False,
        )
    if section is None:
        raise ValueError("non-blank source content is outside the Markdown document map")
    return _UnitDraft(
        kind="raw_block",
        source_span=_span(start_line, end_line),
        section_id=section.section_id,
        heading_path=section.heading_path,
        eligible=True,
    )


def _block_candidates(
    lines: tuple[str, ...],
    section: MarkdownSection,
    front_matter_span: SourceSpan | None,
) -> tuple[_UnitDraft, ...]:
    section_lines = list(
        lines[section.content_span.start_line - 1 : section.content_span.end_line]
    )
    candidates: list[_UnitDraft] = []
    if front_matter_span is not None:
        start_line = max(front_matter_span.start_line, section.content_span.start_line)
        end_line = min(front_matter_span.end_line, section.content_span.end_line)
        if start_line <= end_line:
            candidates.append(
                _UnitDraft(
                    kind="front_matter",
                    source_span=_span(start_line, end_line),
                    section_id=None,
                    heading_path=(),
                    eligible=False,
                )
            )
            for line_number in range(start_line, end_line + 1):
                local_index = line_number - section.content_span.start_line
                original = section_lines[local_index]
                section_lines[local_index] = "\n" if original.endswith("\n") else ""
    section_text = "".join(section_lines)
    tokens = MarkdownIt("commonmark").enable("table").parse(section_text)

    for token in tokens:
        if token.map is None:
            continue
        start_line = section.content_span.start_line + token.map[0]
        end_line = section.content_span.start_line + token.map[1] - 1
        if (
            start_line < section.content_span.start_line
            or end_line > section.content_span.end_line
            or end_line < start_line
        ):
            raise ValueError("Markdown block span escapes its document-map section")

        atom_kind: SourceAtomKind | None = None
        region_kind: SourceRegionKind | None = None
        if token.type == "heading_open" and token.level == 0:
            region_kind = "heading"
        elif token.type == "paragraph_open" and token.level == 0:
            atom_kind = "paragraph"
        elif token.type == "list_item_open" and token.level == 1:
            atom_kind = "list_item"
        elif token.type == "table_open" and token.level == 0:
            atom_kind = "table"
        elif token.type in {"fence", "code_block"} and token.level == 0:
            atom_kind = "code_block"
        elif token.type == "blockquote_open" and token.level == 0:
            block_text = _slice_lines(lines, _span(start_line, end_line))
            atom_kind = "note" if _NOTE_MARKER_RE.match(block_text) else "blockquote"
        elif token.type == "html_block" and token.level == 0:
            block_text = _slice_lines(lines, _span(start_line, end_line))
            if _HTML_COMMENT_RE.fullmatch(block_text):
                region_kind = "html_comment"
            else:
                atom_kind = "raw_block"
        elif token.type == "hr" and token.level == 0:
            region_kind = "thematic_break"

        if atom_kind is not None:
            candidates.append(
                _UnitDraft(
                    kind=atom_kind,
                    source_span=_span(start_line, end_line),
                    section_id=section.section_id,
                    heading_path=section.heading_path,
                    eligible=True,
                )
            )
        elif region_kind is not None:
            candidates.append(
                _UnitDraft(
                    kind=region_kind,
                    source_span=_span(start_line, end_line),
                    section_id=None,
                    heading_path=(),
                    eligible=False,
                )
            )

    return tuple(
        sorted(
            candidates,
            key=lambda item: (item.source_span.start_line, item.source_span.end_line),
        )
    )


def _section_drafts(
    lines: tuple[str, ...],
    section: MarkdownSection,
    front_matter_span: SourceSpan | None,
) -> tuple[_UnitDraft, ...]:
    candidates = _block_candidates(lines, section, front_matter_span)
    drafts: list[_UnitDraft] = []
    cursor = section.content_span.start_line
    for candidate in candidates:
        if candidate.source_span.start_line < cursor:
            raise ValueError("Markdown block candidates overlap inside a document-map section")
        drafts.extend(
            _gap_drafts(
                lines,
                start_line=cursor,
                end_line=candidate.source_span.start_line - 1,
                section=section,
            )
        )
        drafts.append(candidate)
        cursor = candidate.source_span.end_line + 1
    drafts.extend(
        _gap_drafts(
            lines,
            start_line=cursor,
            end_line=section.content_span.end_line,
            section=section,
        )
    )
    return tuple(drafts)


def _document_drafts(
    lines: tuple[str, ...],
    document_map: MarkdownDocumentMap,
    front_matter_span: SourceSpan | None,
) -> tuple[_UnitDraft, ...]:
    section_units = tuple(
        unit
        for section in document_map.sections
        for unit in _section_drafts(lines, section, front_matter_span)
    )
    ordered = tuple(
        sorted(
            section_units,
            key=lambda item: (item.source_span.start_line, item.source_span.end_line),
        )
    )
    drafts: list[_UnitDraft] = []
    cursor = 1
    for unit in ordered:
        if unit.source_span.start_line < cursor:
            raise ValueError("document-map sections produce overlapping Source Atom units")
        drafts.extend(
            _gap_drafts(
                lines,
                start_line=cursor,
                end_line=unit.source_span.start_line - 1,
                section=None,
            )
        )
        drafts.append(unit)
        cursor = unit.source_span.end_line + 1
    drafts.extend(
        _gap_drafts(
            lines,
            start_line=cursor,
            end_line=document_map.source_line_count,
            section=None,
        )
    )
    return tuple(drafts)


def build_source_atom_set(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
) -> SourceAtomSet:
    trusted_document = NormalizedDocument.model_validate(document.model_dump(mode="json"))
    trusted_map = MarkdownDocumentMap.model_validate(document_map.model_dump(mode="json"))
    verify_markdown_document_map(trusted_document, trusted_map)
    if trusted_document.media_type != "text/markdown":
        raise ValueError("Source Atom builder currently requires text/markdown")

    lines = _source_lines(trusted_document.body)
    drafts = _document_drafts(
        lines,
        trusted_map,
        detect_markdown_front_matter_span(trusted_document.body),
    )
    atoms: list[SourceAtom] = []
    regions: list[SourceRegion] = []

    for draft in drafts:
        text_hash = sha256_text(_slice_lines(lines, draft.source_span))
        if draft.eligible:
            if draft.section_id is None:
                raise ValueError("eligible Source Atom must belong to a document-map section")
            provisional = SourceAtom(
                atom_id="source-atom:sha256:" + "0" * 64,
                ordinal=len(atoms),
                kind=cast(SourceAtomKind, draft.kind),
                section_id=draft.section_id,
                heading_path=draft.heading_path,
                source_span=draft.source_span,
                text_hash=text_hash,
                required_context_atom_ids=(),
            )
            atoms.append(
                provisional.model_copy(
                    update={
                        "atom_id": _atom_id(
                            document_id=trusted_map.document_id,
                            source_ref=trusted_map.source_ref,
                            normalized_body_hash=trusted_map.normalized_body_hash,
                            document_map_id=trusted_map.map_id,
                            atom=provisional,
                        )
                    }
                )
            )
        else:
            provisional_region = SourceRegion(
                region_id="source-region:sha256:" + "0" * 64,
                ordinal=len(regions),
                kind=cast(SourceRegionKind, draft.kind),
                source_span=draft.source_span,
                text_hash=text_hash,
            )
            regions.append(
                provisional_region.model_copy(
                    update={
                        "region_id": _region_id(
                            document_id=trusted_map.document_id,
                            source_ref=trusted_map.source_ref,
                            normalized_body_hash=trusted_map.normalized_body_hash,
                            document_map_id=trusted_map.map_id,
                            region=provisional_region,
                        )
                    }
                )
            )

    payload = _SourceAtomSetPayload(
        document_id=trusted_map.document_id,
        source_ref=trusted_map.source_ref,
        normalized_body_hash=trusted_map.normalized_body_hash,
        document_map_id=trusted_map.map_id,
        source_line_count=trusted_map.source_line_count,
        builder_version=SOURCE_ATOM_BUILDER_VERSION,
        atoms=tuple(atoms),
        regions=tuple(regions),
        use_scope="retrieval_projection_input_only_not_evidence",
        evidence_eligible=False,
        production_qualified=False,
        qualification="structural_atom_contract_not_quality_qualified",
    ).model_dump(mode="json")
    payload["atom_set_id"] = canonical_hash("source-atom-set", payload)
    return SourceAtomSet.model_validate(payload)


def verify_source_atom_set(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
) -> None:
    rebuilt = build_source_atom_set(document, document_map)
    validated = SourceAtomSet.model_validate(atom_set.model_dump(mode="json"))
    if rebuilt != validated:
        raise ValueError("Source Atom Set does not match the trusted Markdown document")


def slice_source_atom_text(document: NormalizedDocument, atom: SourceAtom) -> str:
    trusted_document = NormalizedDocument.model_validate(document.model_dump(mode="json"))
    trusted_atom = SourceAtom.model_validate(atom.model_dump(mode="json"))
    lines = _source_lines(trusted_document.body)
    if trusted_atom.source_span.end_line > len(lines):
        raise ValueError("Source Atom span exceeds the trusted document")
    text = _slice_lines(lines, trusted_atom.source_span)
    if sha256_text(text) != trusted_atom.text_hash:
        raise ValueError("Source Atom text hash does not match the trusted document slice")
    return text


def load_source_atom_set(raw: str | bytes) -> SourceAtomSet:
    return load_json_model(raw, SourceAtomSet, "Source Atom Set")


__all__ = [
    "SOURCE_ATOM_BUILDER_VERSION",
    "SOURCE_ATOM_SET_SCHEMA_VERSION",
    "SourceAtom",
    "SourceAtomSet",
    "SourceRegion",
    "build_source_atom_set",
    "load_source_atom_set",
    "slice_source_atom_text",
    "verify_source_atom_set",
]
