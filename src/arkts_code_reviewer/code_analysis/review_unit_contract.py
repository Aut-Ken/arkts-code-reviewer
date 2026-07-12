from __future__ import annotations

import posixpath
import re
from typing import Literal
from urllib.parse import quote

ReviewUnitKind = Literal[
    "struct",
    "class",
    "function",
    "method",
    "build_method",
    "builder",
    "ui_block",
    "fallback",
]
SelectionReason = Literal[
    "full_top_level_declaration",
    "innermost_changed_declaration",
    "large_build_ui_block",
    "fallback_window",
]
ReviewUnitDiagnosticCode = Literal[
    "budget_not_enforced",
    "changed_lines_outside_context",
    "diff_file_without_hunks",
    "hunk_out_of_range",
    "no_matching_declaration",
    "parser_degraded",
    "parser_error_nodes",
    "parser_missing_nodes",
    "unsupported_deletion_only",
]

REVIEW_UNIT_KINDS: tuple[ReviewUnitKind, ...] = (
    "struct",
    "class",
    "function",
    "method",
    "build_method",
    "builder",
    "ui_block",
    "fallback",
)
SELECTION_REASONS: tuple[SelectionReason, ...] = (
    "full_top_level_declaration",
    "innermost_changed_declaration",
    "large_build_ui_block",
    "fallback_window",
)
REVIEW_UNIT_DIAGNOSTIC_CODES: tuple[ReviewUnitDiagnosticCode, ...] = (
    "budget_not_enforced",
    "changed_lines_outside_context",
    "diff_file_without_hunks",
    "hunk_out_of_range",
    "no_matching_declaration",
    "parser_degraded",
    "parser_error_nodes",
    "parser_missing_nodes",
    "unsupported_deletion_only",
)


def normalize_review_path(path: str) -> str:
    """Normalize a logical source path without touching the filesystem."""

    if not isinstance(path, str) or not path:
        raise ValueError("ReviewUnit path must be a non-empty string")
    if any(ord(character) < 32 for character in path):
        raise ValueError("ReviewUnit path must not contain control characters")
    portable = path.replace("\\", "/")
    if (
        portable.startswith("/")
        or re.match(r"^[A-Za-z]:", portable)
    ):
        raise ValueError("ReviewUnit path must be repository-relative without traversal")
    normalized = posixpath.normpath(portable).removeprefix("./")
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise ValueError("ReviewUnit path must identify a repository-relative file")
    return normalized


def _identity_component(value: str, context: str, *, path: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{context} must not contain control characters")
    safe = "/._-$~" if path else "._-$~"
    return quote(value, safe=safe)


def _validate_identity_span(start_line: int, end_line: int, context: str) -> None:
    if (
        not isinstance(start_line, int)
        or isinstance(start_line, bool)
        or start_line < 1
        or not isinstance(end_line, int)
        or isinstance(end_line, bool)
        or end_line < start_line
    ):
        raise ValueError(f"{context} must use 1-based inclusive lines")


def declaration_unit_id(
    path: str,
    unit_kind: ReviewUnitKind,
    unit_symbol: str,
    start_line: int,
    end_line: int,
    *,
    start_offset_utf16: int | None = None,
    end_offset_utf16: int | None = None,
) -> str:
    if unit_kind not in REVIEW_UNIT_KINDS or unit_kind == "fallback":
        raise ValueError(f"unsupported declaration ReviewUnit kind: {unit_kind}")
    _validate_identity_span(start_line, end_line, "declaration span")
    normalized_path = _identity_component(
        normalize_review_path(path),
        "ReviewUnit path",
        path=True,
    )
    normalized_symbol = _identity_component(unit_symbol, "ReviewUnit symbol")
    unit_id = (
        f"{normalized_path}@{unit_kind}:{normalized_symbol}:"
        f"L{start_line}-L{end_line}"
    )
    if (start_offset_utf16 is None) != (end_offset_utf16 is None):
        raise ValueError("declaration identity offsets must be provided together")
    if start_offset_utf16 is None:
        return unit_id
    if (
        not isinstance(start_offset_utf16, int)
        or isinstance(start_offset_utf16, bool)
        or start_offset_utf16 < 0
        or not isinstance(end_offset_utf16, int)
        or isinstance(end_offset_utf16, bool)
        or end_offset_utf16 <= start_offset_utf16
    ):
        raise ValueError("declaration identity offsets must be a valid UTF-16 range")
    return f"{unit_id}:O{start_offset_utf16}-{end_offset_utf16}"


def fallback_unit_id(
    path: str,
    source_start_line: int,
    source_end_line: int,
    context_start_line: int,
    context_end_line: int,
) -> str:
    _validate_identity_span(source_start_line, source_end_line, "fallback source span")
    _validate_identity_span(context_start_line, context_end_line, "fallback context span")
    if not (
        context_start_line <= source_start_line
        and source_end_line <= context_end_line
    ):
        raise ValueError("fallback source span must be inside context span")
    normalized_path = _identity_component(
        normalize_review_path(path),
        "ReviewUnit path",
        path=True,
    )
    return (
        f"{normalized_path}@fallback:fallback:"
        f"L{source_start_line}-L{source_end_line}:"
        f"C{context_start_line}-L{context_end_line}"
    )
