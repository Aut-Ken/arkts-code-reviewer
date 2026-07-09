from __future__ import annotations

import re

from arkts_code_reviewer.code_analysis.arkts_lexicon import LIFECYCLE_SYMBOLS, STATE_DECORATORS
from arkts_code_reviewer.code_analysis.models import CodeFacts, Declaration, FileHunk, HostSummary, ReviewUnit
from arkts_code_reviewer.code_analysis.text_utils import extract_lines


class ReviewUnitBuilder:
    def __init__(self, max_build_lines: int = 160, fallback_context_lines: int = 20) -> None:
        self.max_build_lines = max_build_lines
        self.fallback_context_lines = fallback_context_lines

    def build_full_units(self, path: str, source: str, facts: CodeFacts) -> list[ReviewUnit]:
        structs = [item for item in facts.declarations if item.kind in {"struct", "class"}]
        if not structs:
            end_line = max(1, len(source.splitlines()))
            return [
                self._fallback_unit(path, source, FileHunk(new_start=1, new_lines=end_line), start=1, end=end_line)
            ]
        units = []
        for declaration in structs:
            units.append(
                ReviewUnit(
                    file=path,
                    unit_symbol=declaration.qualified_name,
                    unit_ref=f"{declaration.qualified_name}@{path}",
                    full_text=declaration.text,
                    changed_lines=[],
                    host_summary=self._host_summary(facts, declaration),
                )
            )
        return units

    def build_diff_units(
        self, path: str, source: str, facts: CodeFacts, hunks: list[FileHunk]
    ) -> list[ReviewUnit]:
        units: list[ReviewUnit] = []
        for hunk in hunks:
            units.append(self._unit_for_hunk(path, source, facts, hunk))
        return self._deduplicate_units(units)

    def _unit_for_hunk(self, path: str, source: str, facts: CodeFacts, hunk: FileHunk) -> ReviewUnit:
        declaration = self._choose_declaration(facts.declarations, hunk)
        if declaration is None:
            start = max(1, hunk.new_start - self.fallback_context_lines)
            end = min(len(source.splitlines()), hunk.new_end + self.fallback_context_lines)
            return self._fallback_unit(path, source, hunk, start, end)

        file_changed = list(range(hunk.new_start, hunk.new_end + 1))
        unit_changed = [
            line - declaration.span.start_line + 1
            for line in file_changed
            if declaration.span.contains_line(line)
        ]
        return ReviewUnit(
            file=path,
            unit_symbol=declaration.qualified_name,
            unit_ref=f"{declaration.qualified_name}@{path}",
            full_text=declaration.text,
            changed_lines=file_changed,
            file_changed_lines=file_changed,
            unit_changed_lines=unit_changed,
            host_summary=self._host_summary(facts, declaration),
            context_degraded=False,
        )

    def _choose_declaration(self, declarations: list[Declaration], hunk: FileHunk) -> Declaration | None:
        covering = [
            item
            for item in declarations
            if self._overlaps(item, hunk)
        ]
        if not covering:
            return None

        build_methods = [item for item in covering if item.kind == "build_method"]
        if build_methods:
            build_method = min(build_methods, key=lambda item: item.line_count)
            if build_method.line_count > self.max_build_lines:
                ui_blocks = [item for item in covering if item.kind == "ui_block"]
                if ui_blocks:
                    return min(ui_blocks, key=lambda item: item.line_count)
            return build_method

        named = [
            item
            for item in covering
            if item.kind in {"method", "function", "builder", "struct", "class"}
        ]
        if named:
            return min(named, key=lambda item: item.line_count)
        return min(covering, key=lambda item: item.line_count)

    def _overlaps(self, declaration: Declaration, hunk: FileHunk) -> bool:
        return declaration.span.start_line <= hunk.new_end and hunk.new_start <= declaration.span.end_line

    def _fallback_unit(
        self, path: str, source: str, hunk: FileHunk, start: int, end: int
    ) -> ReviewUnit:
        file_changed = list(range(hunk.new_start, hunk.new_end + 1))
        return ReviewUnit(
            file=path,
            unit_symbol=f"hunk-L{hunk.new_start}-L{hunk.new_end}",
            unit_ref=f"hunk-L{hunk.new_start}-L{hunk.new_end}@{path}",
            full_text=extract_lines(source, start, end),
            changed_lines=file_changed,
            file_changed_lines=file_changed,
            unit_changed_lines=[line - start + 1 for line in file_changed if start <= line <= end],
            context_degraded=True,
        )

    def _host_summary(self, facts: CodeFacts, declaration: Declaration) -> HostSummary:
        host = self._find_host(facts.declarations, declaration)
        imports = sorted({item.module for item in facts.imports})
        if host is None:
            return HostSummary(imports=imports)

        state_lines = self._state_lines(host.text)
        lifecycle = sorted({symbol for symbol in facts.symbols if symbol in LIFECYCLE_SYMBOLS})
        decorators = sorted(item for item in facts.decorators if item in {"@Component", "@ComponentV2", "@Entry"})
        return HostSummary(
            struct=host.name,
            decorators=decorators,
            states=state_lines,
            lifecycle=lifecycle,
            imports=imports,
        )

    def _find_host(self, declarations: list[Declaration], declaration: Declaration) -> Declaration | None:
        hosts = [
            item
            for item in declarations
            if item.kind in {"struct", "class"}
            and item.span.contains_line_range(declaration.span.start_line, declaration.span.end_line)
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
            existing = merged.get(unit.unit_ref)
            if existing is None:
                merged[unit.unit_ref] = unit
                continue
            existing.changed_lines = sorted(set(existing.changed_lines) | set(unit.changed_lines))
            existing.file_changed_lines = sorted(
                set(existing.file_changed_lines) | set(unit.file_changed_lines)
            )
            existing.unit_changed_lines = sorted(
                set(existing.unit_changed_lines) | set(unit.unit_changed_lines)
            )
        return list(merged.values())
