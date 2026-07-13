from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Annotated

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from arkts_code_reviewer.knowledge.models import (
    Applicability,
    ClauseCandidate,
    ClauseExample,
    ClauseStatus,
    ExampleKind,
    NormalizedDocument,
    SourceRef,
    SourceSpan,
    validate_stable_rule_id,
)

CLAUSE_PARSER_VERSION = "knowledge-clause-parser-v1"

_NATIVE_RULE_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:AC|ADR|BR|ER|FR|RC|US|VM|R)[-_][A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)",
    re.IGNORECASE,
)
_API_LEVEL_RES = (
    re.compile(
        r"API\s*(?:version\s*)?(\d+)\s*(?:起|开始|及以上|以上)",
        re.IGNORECASE,
    ),
    re.compile(r"API\s*(?:version\s*)?(?:>=|≥)\s*(\d+)", re.IGNORECASE),
)
_CONSTRAINT_CUES = (
    "不得",
    "不能",
    "不应",
    "不允许",
    "不可",
    "必须",
    "务必",
    "需要",
    "应当",
    "应在",
    "应申请",
    " 应",
    "，应",
    "。应",
    "只允许",
    "只能",
    "禁止",
    "严禁",
    "must ",
    "must not",
    "should ",
    "should not",
    "required",
)
_GUIDANCE_CUES = (
    "建议",
    "推荐",
    "优先",
    "适合",
    "可以使用",
    "recommend",
    "prefer",
)
_BEHAVIOR_CUES = (
    "默认",
    "返回",
    "触发",
    "生效",
    "支持",
    "调用",
    "发生",
    "进行校验",
    "产生告警",
    "will ",
    "returns ",
    "default",
)
_DEPRECATION_CUES = ("deprecated", "废弃", "弃用", "不再维护")
_NORMATIVE_HEADING_CUES = (
    "不得",
    "不应",
    "不允许",
    "不可",
    "必须",
    "只能",
    "只允许",
    "禁止",
    "严禁",
    "must ",
    "must not",
    "should not",
)
_NOTE_PREFIX_RE = re.compile(r"^(?:注意|说明|备注|Note)\s*[：:]\s*", re.IGNORECASE)
_EXAMPLE_LABEL_RE = re.compile(
    r"^(?:\*{0,2}|【)(正例|正确示例|反例|错误示例|示例\d*)"
    r"(?:\s*[：:]?\*{0,2}|】)",
    re.IGNORECASE,
)
_METADATA_LABEL_RE = re.compile(
    r"^(?:参数|返回值|系统能力|起始版本|版本说明|返回类型|属性|方法)\s*[：:]$",
    re.IGNORECASE,
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExtractedClause(_FrozenModel):
    rule_id: Annotated[str, Field(min_length=1)]
    proposed_status: ClauseStatus
    candidate: ClauseCandidate

    @field_validator("rule_id")
    @classmethod
    def validate_rule_id(cls, value: str) -> str:
        return validate_stable_rule_id(value, "ExtractedClause.rule_id")


class ClauseParseResult(_FrozenModel):
    parser_version: str = CLAUSE_PARSER_VERSION
    clauses: tuple[ExtractedClause, ...]
    diagnostics: tuple[str, ...] = ()

    @field_validator("diagnostics")
    @classmethod
    def validate_diagnostics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if list(value) != sorted(set(value)):
            raise ValueError("ClauseParseResult.diagnostics must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_clause_order(self) -> ClauseParseResult:
        rule_ids = [item.rule_id for item in self.clauses]
        if rule_ids != sorted(set(rule_ids)):
            raise ValueError("ClauseParseResult.clauses must be sorted and unique")
        return self


@dataclass
class _Draft:
    native_rule_id: str | None
    rule_type: str
    text: str
    heading_path: tuple[str, ...]
    parent_context: str | None
    start_line: int
    end_line: int
    applicability: Applicability
    status: ClauseStatus
    examples: list[ClauseExample] = field(default_factory=list)


def _normalize_inline(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())


def _is_metadata_label(text: str) -> bool:
    plain = text.strip().strip("*_`").strip()
    return _METADATA_LABEL_RE.fullmatch(plain) is not None


def _classify(text: str, heading_path: tuple[str, ...]) -> tuple[str, ClauseStatus] | None:
    if _is_metadata_label(text):
        return None
    lowered_text = text.lower()
    deprecation_context = f"{' '.join(heading_path)} {text}".lower()
    if any(cue.lower() in deprecation_context for cue in _DEPRECATION_CUES):
        return "deprecation", "Deprecated"
    if any(cue.lower() in lowered_text for cue in _CONSTRAINT_CUES):
        return "constraint", "Draft"
    if any(cue.lower() in lowered_text for cue in _GUIDANCE_CUES):
        return "guidance", "Draft"
    if any(cue.lower() in lowered_text for cue in _BEHAVIOR_CUES):
        return "behavior", "Draft"
    return None


def _is_normative_heading(title: str) -> bool:
    lowered = title.lower()
    return len(title) >= 8 and any(
        cue.lower() in lowered for cue in _NORMATIVE_HEADING_CUES
    )


def _api_levels(text: str) -> set[int]:
    return {
        int(match.group(1))
        for pattern in _API_LEVEL_RES
        for match in pattern.finditer(text)
    }


def _applicability(
    document: NormalizedDocument,
    headings: tuple[str, ...],
    text: str,
) -> Applicability:
    api_level = document.api_level
    for heading in reversed(headings):
        levels = _api_levels(heading)
        if len(levels) == 1:
            api_level = levels.pop()
            break
    else:
        levels = _api_levels(text)
        if len(levels) == 1:
            api_level = levels.pop()
    return Applicability(
        min_api_level=api_level,
        releases=() if document.release is None else (document.release,),
        language_modes=() if document.language_mode is None else (document.language_mode,),
    )


def _native_rule_id(text: str) -> str | None:
    match = _NATIVE_RULE_RE.search(text)
    return None if match is None else match.group(1).upper().replace("_", "-")


def _tokens_between(tokens: list[Token], start: int, closing_type: str) -> tuple[list[Token], int]:
    nested = 0
    collected: list[Token] = []
    for index in range(start, len(tokens)):
        token = tokens[index]
        if token.type == tokens[start].type:
            nested += 1
        if token.type == closing_type:
            nested -= 1
            if nested == 0:
                return collected, index
        collected.append(token)
    raise ValueError(f"unterminated Markdown token: {tokens[start].type}")


def _inline_contents(tokens: list[Token]) -> list[str]:
    return [_normalize_inline(token.content) for token in tokens if token.type == "inline"]


def _parse_table(
    tokens: list[Token],
    start: int,
    headings: tuple[str, ...],
    document: NormalizedDocument,
) -> tuple[list[_Draft], int, set[str]]:
    table_tokens, end = _tokens_between(tokens, start, "table_close")
    table_map = tokens[start].map
    if table_map is None:
        raise ValueError("Markdown table token is missing its source span")
    header: list[str] = []
    drafts: list[_Draft] = []
    diagnostics: set[str] = set()
    section = ""
    index = 0
    while index < len(table_tokens):
        token = table_tokens[index]
        if token.type == "thead_open":
            section = "head"
        elif token.type == "tbody_open":
            section = "body"
        elif token.type == "tr_open" and token.map is not None:
            row_tokens, row_end = _tokens_between(table_tokens, index, "tr_close")
            cells = _inline_contents(row_tokens)
            if section == "head":
                header = cells
            elif section == "body" and cells:
                is_acceptance_table = (
                    len(header) >= 2
                    and header[0].replace(" ", "").upper() in {"ACID", "AC编号"}
                    and header[1].endswith("标准")
                )
                is_rule_table = (
                    len(header) >= 5
                    and header[0].replace(" ", "").upper() in {"RULEID", "规则ID"}
                    and "预期行为" in header
                    and "边界/约束" in header
                )
                if not is_acceptance_table and not is_rule_table:
                    if any(
                        cue in cell
                        for cell in header
                        for cue in ("标准", "要求", "约束", "规则", "预期行为")
                    ):
                        diagnostics.add(
                            f"unsupported_normative_table:L{table_map[0] + 1}"
                        )
                    index = row_end
                    index += 1
                    continue
                native_id = _native_rule_id(cells[0])
                if is_acceptance_table:
                    text = cells[1] if len(cells) >= 2 else ""
                    parent_context = header[1]
                    classified = _classify(text, headings)
                    if native_id is not None and classified is None:
                        classified = ("behavior", "Draft")
                else:
                    values = dict(zip(header, cells, strict=False))
                    parts = [
                        f"{name}：{values[name]}"
                        for name in ("触发条件", "预期行为", "边界/约束")
                        if values.get(name) not in {None, "", "—", "-"}
                    ]
                    text = "；".join(parts)
                    parent_context = "规则定义"
                    declared_type = values.get("类型", "")
                    if "约束" in declared_type:
                        classified = ("constraint", "Draft")
                    elif "指导" in declared_type or "建议" in declared_type:
                        classified = ("guidance", "Draft")
                    else:
                        classified = _classify(text, headings) or (
                            "behavior",
                            "Draft",
                        )
                if native_id is None or not text:
                    diagnostics.add(f"invalid_acceptance_row:L{token.map[0] + 1}")
                    index = row_end
                    index += 1
                    continue
                if classified is not None:
                    rule_type, status = classified
                    drafts.append(
                        _Draft(
                            native_rule_id=native_id,
                            rule_type=rule_type,
                            text=text,
                            heading_path=headings,
                            parent_context=parent_context,
                            start_line=token.map[0] + 1,
                            end_line=token.map[1],
                            applicability=_applicability(document, headings, text),
                            status=status,
                        )
                    )
            index = row_end
        index += 1
    return drafts, end, diagnostics


def _parse_list(
    tokens: list[Token],
    start: int,
    headings: tuple[str, ...],
    document: NormalizedDocument,
    parent_context: str | None,
) -> tuple[list[_Draft], int, set[str]]:
    closing = (
        "bullet_list_close"
        if tokens[start].type == "bullet_list_open"
        else "ordered_list_close"
    )
    list_tokens, end = _tokens_between(tokens, start, closing)
    drafts: list[_Draft] = []
    diagnostics: set[str] = set()
    direct_level = tokens[start].level + 1
    index = 0
    while index < len(list_tokens):
        token = list_tokens[index]
        if (
            token.type == "list_item_open"
            and token.level == direct_level
            and token.map is not None
        ):
            item_tokens, item_end = _tokens_between(list_tokens, index, "list_item_close")
            item_drafts: list[_Draft] = []
            pending_kind: ExampleKind | None = None
            item_context: str | None = None
            item_index = 0
            while item_index < len(item_tokens):
                item = item_tokens[item_index]
                if item.type in {"bullet_list_open", "ordered_list_open"}:
                    nested_drafts, nested_end, nested_diagnostics = _parse_list(
                        item_tokens,
                        item_index,
                        headings,
                        document,
                        item_context or parent_context,
                    )
                    item_drafts.extend(nested_drafts)
                    diagnostics.update(nested_diagnostics)
                    item_index = nested_end + 1
                    continue
                if item.type == "paragraph_open" and item.map is not None:
                    inline = item_tokens[item_index + 1]
                    text = _normalize_inline(inline.content)
                    label_kind = _example_kind(text)
                    if label_kind is not None:
                        if item_drafts:
                            pending_kind = label_kind
                        else:
                            diagnostics.add(
                                f"orphan_example_label:L{item.map[0] + 1}"
                            )
                    elif item.level == direct_level + 1:
                        if item_context is None:
                            item_context = text
                        classified = (
                            None
                            if text.endswith(("：", ":"))
                            else _classify(text, headings)
                        )
                        if classified is not None:
                            rule_type, status = classified
                            item_drafts.append(
                                _Draft(
                                    native_rule_id=_native_rule_id(text),
                                    rule_type=rule_type,
                                    text=text,
                                    heading_path=headings,
                                    parent_context=parent_context,
                                    start_line=item.map[0] + 1,
                                    end_line=item.map[1],
                                    applicability=_applicability(
                                        document,
                                        headings,
                                        text,
                                    ),
                                    status=status,
                                )
                            )
                            pending_kind = None
                    item_index += 3
                    continue
                if item.type == "fence" and item.map is not None:
                    can_attach = bool(item_drafts) and (
                        pending_kind is not None
                        or item.map[0] + 1 <= item_drafts[-1].end_line + 2
                    )
                    if can_attach:
                        item_drafts[-1].examples.append(
                            ClauseExample(
                                kind=pending_kind or "neutral",
                                text=item.content.rstrip("\n"),
                                source_span=SourceSpan(
                                    start_line=item.map[0] + 1,
                                    end_line=item.map[1],
                                ),
                            )
                        )
                    else:
                        diagnostics.add(f"orphan_code_example:L{item.map[0] + 1}")
                item_index += 1
            drafts.extend(item_drafts)
            index = item_end
        index += 1
    return drafts, end, diagnostics


def _source_ref_for_span(document: NormalizedDocument, span: SourceSpan) -> SourceRef:
    source = document.source_ref
    return SourceRef(
        source_id=source.source_id,
        revision=source.revision,
        relative_path=source.relative_path,
        anchor=f"L{span.start_line}-L{span.end_line}",
        authority=source.authority,
        content_hash=source.content_hash,
    )


def _example_kind(text: str) -> ExampleKind | None:
    match = _EXAMPLE_LABEL_RE.match(text)
    if match is None:
        return None
    label = match.group(1).lower()
    if label in {"正例", "正确示例"}:
        return "positive"
    if label in {"反例", "错误示例"}:
        return "negative"
    return "neutral"


def _rule_id(
    document: NormalizedDocument,
    draft: _Draft,
    ordinal: int,
    rule_namespace: str | None,
) -> str:
    if draft.native_rule_id is not None:
        namespace = rule_namespace or (
            f"{document.source_ref.source_id}:{document.source_ref.relative_path}"
        )
        return f"{namespace}/{draft.native_rule_id}"
    anchor = draft.heading_path[-1] if draft.heading_path else document.title
    return (
        f"{document.source_ref.source_id}:{document.source_ref.relative_path}:"
        f"{anchor}:{ordinal:02d}"
    )


def _finalize(
    document: NormalizedDocument,
    drafts: list[_Draft],
    rule_namespace: str | None,
) -> tuple[tuple[ExtractedClause, ...], tuple[str, ...]]:
    ordinals: dict[tuple[str, ...], int] = {}
    preliminary: list[tuple[str, _Draft, ClauseCandidate]] = []
    diagnostics: set[str] = set()
    for draft in drafts:
        ordinals[draft.heading_path] = ordinals.get(draft.heading_path, 0) + 1
        span = SourceSpan(start_line=draft.start_line, end_line=draft.end_line)
        candidate = ClauseCandidate.create(
            native_rule_id=draft.native_rule_id,
            rule_type=draft.rule_type,
            text=draft.text,
            heading_path=draft.heading_path,
            parent_context=draft.parent_context,
            neighbor_candidate_ids=(),
            applicability=draft.applicability,
            source_ref=_source_ref_for_span(document, span),
            source_span=span,
            examples=tuple(draft.examples),
        )
        base_rule_id = _rule_id(
            document,
            draft,
            ordinals[draft.heading_path],
            rule_namespace,
        )
        preliminary.append((base_rule_id, draft, candidate))
    base_counts: dict[str, int] = {}
    for base_rule_id, _, _ in preliminary:
        base_counts[base_rule_id] = base_counts.get(base_rule_id, 0) + 1
    resolved: list[tuple[str, _Draft, ClauseCandidate]] = []
    for base_rule_id, draft, candidate in preliminary:
        rule_id = base_rule_id
        if base_counts[base_rule_id] > 1 and draft.native_rule_id is not None:
            diagnostics.add(f"duplicate_native_rule_id:{base_rule_id}")
            span = candidate.source_span
            rule_id = f"{base_rule_id}@L{span.start_line}-L{span.end_line}"
        elif base_counts[base_rule_id] > 1:
            diagnostics.add(f"ambiguous_heading_anchor:{base_rule_id}")
            raw_path = json.dumps(
                draft.heading_path,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            heading_hash = hashlib.sha256(raw_path).hexdigest()[:12]
            rule_id = f"{base_rule_id}@heading-{heading_hash}"
        resolved.append((rule_id, draft, candidate))
    extracted: list[ExtractedClause] = []
    for index, (rule_id, draft, candidate) in enumerate(resolved):
        neighbors = []
        if index > 0 and resolved[index - 1][1].heading_path == draft.heading_path:
            neighbors.append(resolved[index - 1][2].candidate_id)
        if (
            index + 1 < len(resolved)
            and resolved[index + 1][1].heading_path == draft.heading_path
        ):
            neighbors.append(resolved[index + 1][2].candidate_id)
        candidate = ClauseCandidate.model_validate(
            {
                **candidate.model_dump(),
                "neighbor_candidate_ids": tuple(sorted(neighbors)),
            }
        )
        extracted.append(
            ExtractedClause(
                rule_id=rule_id,
                proposed_status=draft.status,
                candidate=candidate,
            )
        )
    return (
        tuple(sorted(extracted, key=lambda item: item.rule_id)),
        tuple(sorted(diagnostics)),
    )


def parse_markdown_clauses(
    document: NormalizedDocument,
    *,
    rule_namespace: str | None = None,
) -> ClauseParseResult:
    if document.media_type != "text/markdown":
        raise ValueError("Clause parser requires a Markdown NormalizedDocument")
    if rule_namespace is not None:
        validate_stable_rule_id(rule_namespace, "rule_namespace")
    tokens = MarkdownIt("commonmark").enable("table").parse(document.body)
    headings: list[str] = []
    drafts: list[_Draft] = []
    diagnostics: set[str] = set()
    pending_parent_context: str | None = None
    pending_example: tuple[int, ExampleKind] | None = None
    example_owner: int | None = None
    metadata_context = False
    blockquote_depth = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "blockquote_open":
            blockquote_depth += 1
            index += 1
            continue
        if token.type == "blockquote_close":
            blockquote_depth -= 1
            if blockquote_depth < 0:
                raise ValueError("Markdown blockquote token depth became negative")
            index += 1
            continue
        if token.type == "heading_open":
            inline = tokens[index + 1]
            level = int(token.tag[1:])
            if token.map is None:
                raise ValueError("Markdown heading token is missing its source span")
            if level > len(headings) + 1:
                diagnostics.add(f"skipped_heading_level:L{token.map[0] + 1}")
            heading_title = _normalize_inline(inline.content)
            headings[level - 1 :] = [heading_title]
            pending_parent_context = None
            pending_example = None
            example_owner = None
            metadata_context = False
            if _is_normative_heading(heading_title):
                drafts.append(
                    _Draft(
                        native_rule_id=_native_rule_id(heading_title),
                        rule_type="constraint",
                        text=heading_title,
                        heading_path=tuple(headings),
                        parent_context=None,
                        start_line=token.map[0] + 1,
                        end_line=token.map[1],
                        applicability=_applicability(
                            document,
                            tuple(headings),
                            heading_title,
                        ),
                        status="Draft",
                    )
                )
                example_owner = len(drafts) - 1
            index += 3
            continue
        if token.type == "table_open":
            if token.map is None:
                raise ValueError("Markdown table token is missing its source span")
            if metadata_context:
                _, index = _tokens_between(tokens, index, "table_close")
                diagnostics.add(f"skipped_metadata_table:L{token.map[0] + 1}")
                index += 1
                continue
            table_drafts, index, table_diagnostics = _parse_table(
                tokens,
                index,
                tuple(headings),
                document,
            )
            drafts.extend(table_drafts)
            diagnostics.update(table_diagnostics)
            pending_parent_context = None
            pending_example = None
            example_owner = None
            metadata_context = False
            index += 1
            continue
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            if token.map is None:
                raise ValueError("Markdown list token is missing its source span")
            if metadata_context:
                closing = (
                    "bullet_list_close"
                    if token.type == "bullet_list_open"
                    else "ordered_list_close"
                )
                _, index = _tokens_between(tokens, index, closing)
                diagnostics.add(f"skipped_metadata_list:L{token.map[0] + 1}")
                index += 1
                continue
            list_drafts, index, list_diagnostics = _parse_list(
                tokens,
                index,
                tuple(headings),
                document,
                pending_parent_context,
            )
            drafts.extend(list_drafts)
            diagnostics.update(list_diagnostics)
            pending_parent_context = None
            pending_example = None
            example_owner = None
            metadata_context = False
            index += 1
            continue
        if token.type == "paragraph_open" and token.map is not None:
            inline = tokens[index + 1]
            text = _normalize_inline(inline.content)
            example_kind = _example_kind(text)
            if example_kind is not None:
                metadata_context = False
                previous_target = (
                    example_owner
                )
                pending_example = None
                target_index = (
                    len(drafts) - 1 if previous_target is None else previous_target
                )
                can_target = (
                    bool(drafts)
                    and drafts[target_index].heading_path == tuple(headings)
                    and (
                        previous_target is not None
                        or token.map[0] + 1 <= drafts[target_index].end_line + 3
                    )
                )
                if can_target:
                    pending_example = (target_index, example_kind)
                    example_owner = target_index
                else:
                    example_owner = None
                    diagnostics.add(f"orphan_example_label:L{token.map[0] + 1}")
                index += 3
                continue
            if _is_metadata_label(text):
                metadata_context = True
                pending_parent_context = None
                pending_example = None
                example_owner = None
                index += 3
                continue
            if metadata_context:
                index += 3
                continue
            in_blockquote = blockquote_depth > 0
            if in_blockquote:
                can_attach = (
                    bool(drafts)
                    and drafts[-1].heading_path == tuple(headings)
                    and token.map[0] + 1 <= drafts[-1].end_line + 2
                )
                if can_attach:
                    note = _NOTE_PREFIX_RE.sub("", text)
                    drafts[-1].text = f"{drafts[-1].text}{note}"
                    drafts[-1].end_line = token.map[1]
                else:
                    diagnostics.add(f"orphan_normative_note:L{token.map[0] + 1}")
            else:
                native_id = _native_rule_id(headings[-1]) if headings else None
                classified = (
                    None if text.endswith(("：", ":")) else _classify(text, tuple(headings))
                )
                if native_id is not None and classified is None:
                    classified = ("behavior", "Draft")
                if classified is not None:
                    rule_type, status = classified
                    drafts.append(
                        _Draft(
                            native_rule_id=native_id,
                            rule_type=rule_type,
                            text=text,
                            heading_path=tuple(headings),
                            parent_context=None,
                            start_line=token.map[0] + 1,
                            end_line=token.map[1],
                            applicability=_applicability(
                                document,
                                tuple(headings),
                                text,
                            ),
                            status=status,
                        )
                    )
                    pending_parent_context = None
                    pending_example = None
                    example_owner = None
                elif text.endswith(("：", ":")):
                    pending_parent_context = text
                else:
                    pending_parent_context = None
            index += 3
            continue
        if token.type == "fence" and token.map is not None:
            target_index = len(drafts) - 1
            kind: ExampleKind = "neutral"
            if pending_example is not None:
                target_index, kind = pending_example
            can_attach = bool(drafts) and drafts[target_index].heading_path == tuple(headings)
            if pending_example is None:
                can_attach = (
                    can_attach
                    and token.map[0] + 1 <= drafts[target_index].end_line + 2
                )
            if can_attach:
                drafts[target_index].examples.append(
                    ClauseExample(
                        kind=kind,
                        text=token.content.rstrip("\n"),
                        source_span=SourceSpan(
                            start_line=token.map[0] + 1,
                            end_line=token.map[1],
                        ),
                    )
                )
            else:
                diagnostics.add(f"orphan_code_example:L{token.map[0] + 1}")
            pending_example = None
        index += 1
    if blockquote_depth != 0:
        raise ValueError("Markdown blockquote tokens are unbalanced")
    clauses, identity_diagnostics = _finalize(document, drafts, rule_namespace)
    diagnostics.update(identity_diagnostics)
    return ClauseParseResult(clauses=clauses, diagnostics=tuple(sorted(diagnostics)))


__all__ = [
    "CLAUSE_PARSER_VERSION",
    "ClauseParseResult",
    "ExtractedClause",
    "parse_markdown_clauses",
]
