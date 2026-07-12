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
    "field_region",
    "import_region",
    "fallback",
]
SelectionReason = Literal[
    "full_top_level_declaration",
    "innermost_changed_declaration",
    "large_build_ui_block",
    "changed_review_region",
    "fallback_window",
]
ReviewUnitDiagnosticCode = Literal[
    "binary_change_unsupported",
    "budget_not_enforced",
    "change_atom_unassigned",
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

# ReviewUnit v1 remains a frozen compatibility contract. RU-4 adds an independent
# v2 vocabulary instead of silently changing the meaning of its Golden manifest.
REVIEW_UNIT_V2_KINDS: tuple[ReviewUnitKind, ...] = (
    *REVIEW_UNIT_KINDS[:-1],
    "field_region",
    "import_region",
    "fallback",
)
REVIEW_UNIT_V2_SELECTION_REASONS: tuple[SelectionReason, ...] = (
    *SELECTION_REASONS[:-1],
    "changed_review_region",
    "fallback_window",
)
REVIEW_UNIT_V2_DIAGNOSTIC_CODES: tuple[ReviewUnitDiagnosticCode, ...] = (
    "binary_change_unsupported",
    *REVIEW_UNIT_DIAGNOSTIC_CODES[:1],
    "change_atom_unassigned",
    *REVIEW_UNIT_DIAGNOSTIC_CODES[1:],
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
    source_role: Literal["base", "head"] | None = None,
    source_ref_id: str | None = None,
) -> str:
    if unit_kind not in REVIEW_UNIT_V2_KINDS or unit_kind == "fallback":
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
    if start_offset_utf16 is not None:
        if (
            not isinstance(start_offset_utf16, int)
            or isinstance(start_offset_utf16, bool)
            or start_offset_utf16 < 0
            or not isinstance(end_offset_utf16, int)
            or isinstance(end_offset_utf16, bool)
            or end_offset_utf16 <= start_offset_utf16
        ):
            raise ValueError("declaration identity offsets must be a valid UTF-16 range")
        unit_id = f"{unit_id}:O{start_offset_utf16}-{end_offset_utf16}"
    return _with_source_identity(unit_id, source_role, source_ref_id)


def fallback_unit_id(
    path: str,
    source_start_line: int,
    source_end_line: int,
    context_start_line: int,
    context_end_line: int,
    *,
    source_role: Literal["base", "head"] | None = None,
    source_ref_id: str | None = None,
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
    unit_id = (
        f"{normalized_path}@fallback:fallback:"
        f"L{source_start_line}-L{source_end_line}:"
        f"C{context_start_line}-L{context_end_line}"
    )
    return _with_source_identity(unit_id, source_role, source_ref_id)


def _with_source_identity(
    unit_id: str,
    source_role: Literal["base", "head"] | None,
    source_ref_id: str | None,
) -> str:
    """Scope a ChangeSet Unit to an immutable source without changing legacy IDs."""

    if source_role is None and source_ref_id is None:
        return unit_id
    if source_role not in {"base", "head"} or source_ref_id is None:
        raise ValueError(
            "ReviewUnit source identity requires source_role and source_ref_id together"
        )
    if not isinstance(source_ref_id, str) or not re.fullmatch(
        r"code-source:sha256:[0-9a-f]{64}", source_ref_id
    ):
        raise ValueError(
            "ReviewUnit identity source_ref_id must use code-source:sha256:<64 hex>"
        )
    return f"{unit_id}:R{source_role}:S{source_ref_id.rsplit(':', 1)[-1]}"
