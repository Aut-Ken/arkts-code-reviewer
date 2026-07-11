from __future__ import annotations

import re

from arkts_code_reviewer.code_analysis.arkts_lexicon import LIFECYCLE_SYMBOLS, STATE_DECORATORS
from arkts_code_reviewer.code_analysis.models import (
    AnalysisMode,
    CodeFacts,
    Declaration,
    FileHunk,
    HostSummary,
    ReviewUnit,
    ReviewUnitDiagnostic,
    ReviewUnitSpan,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    REVIEW_UNIT_KINDS,
    SelectionReason,
    declaration_unit_id,
    fallback_unit_id,
    normalize_review_path,
)
from arkts_code_reviewer.code_analysis.text_utils import extract_lines


class ReviewUnitBuilder:
    def __init__(self, max_build_lines: int = 160, fallback_context_lines: int = 20) -> None:
        if (
            not isinstance(max_build_lines, int)
            or isinstance(max_build_lines, bool)
            or max_build_lines < 1
        ):
            raise ValueError("max_build_lines must be an integer >= 1")
        if (
            not isinstance(fallback_context_lines, int)
            or isinstance(fallback_context_lines, bool)
            or fallback_context_lines < 0
        ):
            raise ValueError("fallback_context_lines must be an integer >= 0")
        self.max_build_lines = max_build_lines
        self.fallback_context_lines = fallback_context_lines

    def build_units(
        self,
        path: str,
        source: str,
        facts: CodeFacts,
        mode: AnalysisMode,
        hunks: list[FileHunk],
    ) -> list[ReviewUnit]:
        """Use the same full/diff dispatch as the production Analyzer path."""

        if mode not in {"full", "diff"}:
            raise ValueError(f"unsupported ReviewUnit analysis mode: {mode}")
        if mode == "diff" and hunks:
            return self.build_diff_units(path, source, facts, hunks)
        return self.build_full_units(path, source, facts)

    def build_full_units(self, path: str, source: str, facts: CodeFacts) -> list[ReviewUnit]:
        source_line_count = self._validate_source_contract(path, source, facts)
        if source_line_count == 0:
            raise ValueError("empty source cannot produce a 1-based ReviewUnit")
        structs = [item for item in facts.declarations if item.kind in {"struct", "class"}]
        if not structs:
            end_line = source_line_count
            return [
                self._fallback_unit(
                    path,
                    source,
                    FileHunk(new_start=1, new_lines=end_line),
                    start=1,
                    end=end_line,
                )
            ]
        units = []
        for declaration in structs:
            source_span = self._review_span(declaration)
            units.append(
                ReviewUnit(
                    file=path,
                    unit_symbol=declaration.qualified_name,
                    unit_ref=f"{declaration.qualified_name}@{path}",
                    full_text=extract_lines(
                        source,
                        source_span.start_line,
                        source_span.end_line,
                    ),
                    changed_lines=[],
                    host_summary=self._host_summary(facts, declaration),
                    unit_id=declaration_unit_id(
                        path,
                        declaration.kind,
                        declaration.qualified_name,
                        source_span.start_line,
                        source_span.end_line,
                    ),
                    unit_kind=declaration.kind,
                    source_span=source_span,
                    context_span=source_span,
                    changed_new_lines=[],
                    selection_reason="full_top_level_declaration",
                    diagnostics=[],
                )
            )
        return self._deduplicate_units(units)

    def build_diff_units(
        self, path: str, source: str, facts: CodeFacts, hunks: list[FileHunk]
    ) -> list[ReviewUnit]:
        source_line_count = self._validate_source_contract(path, source, facts)
        for hunk in hunks:
            if not isinstance(hunk, FileHunk):
                raise ValueError("ReviewUnit hunks must use FileHunk")
            if hunk.new_end > source_line_count:
                raise ValueError(
                    f"hunk L{hunk.new_start}-L{hunk.new_end} exceeds "
                    f"source line count {source_line_count}"
                )
        units: list[ReviewUnit] = []
        for hunk in hunks:
            units.append(self._unit_for_hunk(path, source, facts, hunk))
        return self._deduplicate_units(units)

    def _unit_for_hunk(
        self,
        path: str,
        source: str,
        facts: CodeFacts,
        hunk: FileHunk,
    ) -> ReviewUnit:
        selection = self._choose_declaration(facts.declarations, hunk)
        if selection is None:
            start = max(1, hunk.new_start - self.fallback_context_lines)
            end = min(len(source.splitlines()), hunk.new_end + self.fallback_context_lines)
            return self._fallback_unit(path, source, hunk, start, end)
        declaration, selection_reason = selection

        file_changed = list(range(hunk.new_start, hunk.new_end + 1))
        source_span = self._review_span(declaration)
        changed_new_lines = [
            line for line in file_changed if source_span.contains_line(line)
        ]
        unit_changed = [
            line - source_span.start_line + 1 for line in changed_new_lines
        ]
        outside_context = sorted(set(file_changed) - set(changed_new_lines))
        diagnostics = (
            [
                ReviewUnitDiagnostic(
                    code="changed_lines_outside_context",
                    lines=tuple(outside_context),
                )
            ]
            if outside_context
            else []
        )
        return ReviewUnit(
            file=path,
            unit_symbol=declaration.qualified_name,
            unit_ref=f"{declaration.qualified_name}@{path}",
            full_text=extract_lines(
                source,
                source_span.start_line,
                source_span.end_line,
            ),
            changed_lines=file_changed,
            file_changed_lines=file_changed,
            unit_changed_lines=unit_changed,
            host_summary=self._host_summary(facts, declaration),
            context_degraded=False,
            unit_id=declaration_unit_id(
                path,
                declaration.kind,
                declaration.qualified_name,
                source_span.start_line,
                source_span.end_line,
            ),
            unit_kind=declaration.kind,
            source_span=source_span,
            context_span=source_span,
            changed_new_lines=changed_new_lines,
            selection_reason=selection_reason,
            diagnostics=diagnostics,
        )

    def _choose_declaration(
        self,
        declarations: list[Declaration],
        hunk: FileHunk,
    ) -> tuple[Declaration, SelectionReason] | None:
        covering = [item for item in declarations if self._overlaps(item, hunk)]
        if not covering:
            return None

        build_methods = [item for item in covering if item.kind == "build_method"]
        if build_methods:
            build_method = min(build_methods, key=lambda item: item.line_count)
            if build_method.line_count > self.max_build_lines:
                ui_blocks = [item for item in covering if item.kind == "ui_block"]
                if ui_blocks:
                    return (
                        min(ui_blocks, key=lambda item: item.line_count),
                        "large_build_ui_block",
                    )
            return build_method, "innermost_changed_declaration"

        named = [
            item
            for item in covering
            if item.kind in {"method", "function", "builder", "struct", "class"}
        ]
        if named:
            return min(named, key=lambda item: item.line_count), "innermost_changed_declaration"
        return min(covering, key=lambda item: item.line_count), "innermost_changed_declaration"

    def _overlaps(self, declaration: Declaration, hunk: FileHunk) -> bool:
        return (
            declaration.span.start_line <= hunk.new_end
            and hunk.new_start <= declaration.span.end_line
        )

    def _fallback_unit(
        self, path: str, source: str, hunk: FileHunk, start: int, end: int
    ) -> ReviewUnit:
        file_changed = list(range(hunk.new_start, hunk.new_end + 1))
        source_span = ReviewUnitSpan(start_line=hunk.new_start, end_line=hunk.new_end)
        context_span = ReviewUnitSpan(start_line=start, end_line=end)
        changed_new_lines = [
            line for line in file_changed if context_span.contains_line(line)
        ]
        outside_context = sorted(set(file_changed) - set(changed_new_lines))
        diagnostics = [ReviewUnitDiagnostic(code="no_matching_declaration")]
        if outside_context:
            diagnostics.append(
                ReviewUnitDiagnostic(
                    code="changed_lines_outside_context",
                    lines=tuple(outside_context),
                )
            )
        diagnostics.sort(key=lambda item: (item.code, item.lines))
        return ReviewUnit(
            file=path,
            unit_symbol=f"hunk-L{hunk.new_start}-L{hunk.new_end}",
            unit_ref=f"hunk-L{hunk.new_start}-L{hunk.new_end}@{path}",
            full_text=extract_lines(source, context_span.start_line, context_span.end_line),
            changed_lines=file_changed,
            file_changed_lines=file_changed,
            unit_changed_lines=[
                line - context_span.start_line + 1 for line in changed_new_lines
            ],
            context_degraded=True,
            unit_id=fallback_unit_id(
                path,
                source_span.start_line,
                source_span.end_line,
                context_span.start_line,
                context_span.end_line,
            ),
            unit_kind="fallback",
            source_span=source_span,
            context_span=context_span,
            changed_new_lines=changed_new_lines,
            selection_reason="fallback_window",
            diagnostics=diagnostics,
        )

    def _review_span(self, declaration: Declaration) -> ReviewUnitSpan:
        return ReviewUnitSpan(
            start_line=declaration.span.start_line,
            end_line=declaration.span.end_line,
        )

    def _validate_source_contract(
        self,
        path: str,
        source: str,
        facts: CodeFacts,
    ) -> int:
        if not isinstance(source, str):
            raise ValueError("ReviewUnit source must be a string")
        if not isinstance(facts, CodeFacts):
            raise ValueError("ReviewUnit facts must use CodeFacts")
        if normalize_review_path(path) != normalize_review_path(facts.path):
            raise ValueError("ReviewUnit path must match CodeFacts.path")
        if not isinstance(facts.declarations, list):
            raise ValueError("CodeFacts.declarations must be a list")
        source_line_count = len(source.splitlines())
        for declaration in facts.declarations:
            if not isinstance(declaration, Declaration):
                raise ValueError("CodeFacts.declarations must contain Declaration values")
            if declaration.kind not in REVIEW_UNIT_KINDS[:-1]:
                raise ValueError(f"unsupported declaration kind: {declaration.kind}")
            if not isinstance(declaration.name, str) or not declaration.name:
                raise ValueError("Declaration.name must be a non-empty string")
            if (
                not isinstance(declaration.qualified_name, str)
                or not declaration.qualified_name
            ):
                raise ValueError("Declaration.qualified_name must be a non-empty string")
            if declaration.parent_name is not None and not isinstance(
                declaration.parent_name,
                str,
            ):
                raise ValueError("Declaration.parent_name must be a string or None")
            if not isinstance(declaration.text, str):
                raise ValueError("Declaration.text must be a string")
            span = declaration.span
            if (
                not isinstance(span, SourceSpan)
                or not isinstance(span.start_line, int)
                or isinstance(span.start_line, bool)
                or span.start_line < 1
                or not isinstance(span.end_line, int)
                or isinstance(span.end_line, bool)
                or span.end_line < span.start_line
                or span.end_line > source_line_count
            ):
                raise ValueError(
                    f"declaration {declaration.qualified_name!r} has an invalid source span"
                )
        return source_line_count

    def _host_summary(self, facts: CodeFacts, declaration: Declaration) -> HostSummary:
        host = self._find_host(facts.declarations, declaration)
        imports = sorted({item.module for item in facts.imports})
        if host is None:
            return HostSummary(imports=imports)

        state_lines = self._state_lines(host.text)
        lifecycle = sorted({symbol for symbol in facts.symbols if symbol in LIFECYCLE_SYMBOLS})
        decorators = sorted(
            item
            for item in facts.decorators
            if item in {"@Component", "@ComponentV2", "@Entry"}
        )
        return HostSummary(
            struct=host.name,
            decorators=decorators,
            states=state_lines,
            lifecycle=lifecycle,
            imports=imports,
        )

    def _find_host(
        self,
        declarations: list[Declaration],
        declaration: Declaration,
    ) -> Declaration | None:
        hosts = [
            item
            for item in declarations
            if item.kind in {"struct", "class"}
            and item.span.contains_line_range(
                declaration.span.start_line,
                declaration.span.end_line,
            )
        ]
        if not hosts:
            return None
        return min(hosts, key=lambda item: item.line_count)

    def _state_lines(self, text: str) -> list[str]:
        lines: list[str] = []
        decorator_names = "|".join(re.escape(item.removeprefix("@")) for item in STATE_DECORATORS)
        pattern = re.compile(rf"@(?:{decorator_names})\s+[^\n;]+")
        for match in pattern.finditer(text):
            lines.append(" ".join(match.group(0).split()))
        return lines

    def _deduplicate_units(self, units: list[ReviewUnit]) -> list[ReviewUnit]:
        merged: dict[str, ReviewUnit] = {}
        for unit in units:
            existing = merged.get(unit.unit_id)
            if existing is None:
                merged[unit.unit_id] = unit
                continue
            self._validate_merge(existing, unit)
            existing.changed_lines = sorted(set(existing.changed_lines) | set(unit.changed_lines))
            existing.file_changed_lines = sorted(
                set(existing.file_changed_lines) | set(unit.file_changed_lines)
            )
            existing.changed_new_lines = sorted(
                set(existing.changed_new_lines) | set(unit.changed_new_lines)
            )
            existing.unit_changed_lines = [
                line - existing.context_span.start_line + 1
                for line in existing.changed_new_lines
            ]
            existing.diagnostics = self._merge_diagnostics(
                existing.diagnostics,
                unit.diagnostics,
            )
            existing.validate()
        return self._sort_units(list(merged.values()))

    def _validate_merge(self, existing: ReviewUnit, incoming: ReviewUnit) -> None:
        identity_fields = (
            "file",
            "unit_symbol",
            "unit_ref",
            "unit_kind",
            "source_span",
            "context_span",
            "selection_reason",
            "full_text",
            "host_summary",
            "context_degraded",
        )
        if any(
            getattr(existing, field) != getattr(incoming, field)
            for field in identity_fields
        ):
            raise ValueError(f"conflicting ReviewUnit payloads for unit_id {existing.unit_id!r}")

    def _merge_diagnostics(
        self,
        first: list[ReviewUnitDiagnostic],
        second: list[ReviewUnitDiagnostic],
    ) -> list[ReviewUnitDiagnostic]:
        lines_by_code: dict[str, set[int]] = {}
        for diagnostic in [*first, *second]:
            lines_by_code.setdefault(diagnostic.code, set()).update(diagnostic.lines)
        return [
            ReviewUnitDiagnostic(code=code, lines=tuple(sorted(lines)))  # type: ignore[arg-type]
            for code, lines in sorted(lines_by_code.items())
        ]

    def _sort_units(self, units: list[ReviewUnit]) -> list[ReviewUnit]:
        for unit in units:
            unit.validate()
        return sorted(
            units,
            key=lambda item: (
                item.context_span.start_line,
                item.context_span.end_line,
                item.source_span.start_line,
                item.source_span.end_line,
                item.unit_id,
            ),
        )
