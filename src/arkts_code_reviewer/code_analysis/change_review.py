from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from arkts_code_reviewer.code_analysis.change_set import (
    ChangeAtom,
    ChangedFile,
    ChangeSet,
    CodeSourceSnapshot,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    DeclarationOccurrence,
    ExactRange,
    FactOccurrence,
    FileParseResult,
    OwnerRef,
    ReviewRegion,
)
from arkts_code_reviewer.code_analysis.models import (
    CodeFacts,
    Declaration,
    DeclarationKind,
    FileHunk,
    HostSummary,
    ParserQuality,
    ReviewUnit,
    ReviewUnitBuildResult,
    ReviewUnitDiagnostic,
    ReviewUnitFileResult,
    ReviewUnitSpan,
    SourceRole,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    ReviewUnitDiagnosticCode,
    declaration_unit_id,
    fallback_unit_id,
)
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.text_utils import extract_lines

CHANGE_REVIEW_BUILD_SCHEMA_VERSION = "review-unit-build-v3"


class ChangeSetReviewUnitBuilder:
    """Build source-role ReviewUnits from an already normalized ChangeSet.

    The caller owns source acquisition and parsing.  This builder verifies those
    immutable inputs and never invokes a parser or reads a repository itself.
    """

    def __init__(self, review_unit_builder: ReviewUnitBuilder) -> None:
        if not isinstance(review_unit_builder, ReviewUnitBuilder):
            raise ValueError(
                "ChangeSetReviewUnitBuilder requires a ReviewUnitBuilder"
            )
        self.review_unit_builder = review_unit_builder

    def build(
        self,
        change_set: ChangeSet,
        source_snapshots: Mapping[str, CodeSourceSnapshot],
        file_parse_results: Mapping[str, FileParseResult],
    ) -> ReviewUnitBuildResult:
        if not isinstance(change_set, ChangeSet):
            raise ValueError("change_set must use ChangeSet")
        change_set.validate()
        snapshots, parse_results = self._validate_inputs(
            change_set,
            source_snapshots,
            file_parse_results,
        )
        atoms_by_id = {atom.atom_id: atom for atom in change_set.atoms}
        self._validate_snapshot_file_semantics(
            change_set,
            snapshots,
            atoms_by_id,
        )

        file_results: list[ReviewUnitFileResult] = []
        build_diagnostics: list[ReviewUnitDiagnostic] = []
        for changed_file in change_set.files:
            if changed_file.is_binary:
                build_diagnostics.append(
                    ReviewUnitDiagnostic(code="binary_change_unsupported")
                )
                continue
            for role, source_ref_id in self._file_sources(changed_file):
                snapshot = snapshots[source_ref_id]
                parse_result = parse_results[source_ref_id]
                file_results.append(
                    self._build_source_result(
                        changed_file=changed_file,
                        role=role,
                        snapshot=snapshot,
                        parse_result=parse_result,
                        atoms=tuple(
                            atoms_by_id[atom_id]
                            for atom_id in changed_file.atom_ids
                        ),
                    )
                )

        file_results.sort(key=self._file_result_sort_key)
        unassigned_atom_ids = sorted(
            {
                atom_id
                for result in file_results
                for atom_id in result.unassigned_change_atom_ids
            }
        )
        if unassigned_atom_ids:
            build_diagnostics.append(
                ReviewUnitDiagnostic(code="change_atom_unassigned")
            )
        build_diagnostics = self._merge_diagnostics([], build_diagnostics)
        return ReviewUnitBuildResult(
            schema_version=CHANGE_REVIEW_BUILD_SCHEMA_VERSION,
            mode="diff",
            file_results=file_results,
            diagnostics=build_diagnostics,
            change_set_id=change_set.change_set_id,
            unassigned_change_atom_ids=unassigned_atom_ids,
        )

    def _validate_inputs(
        self,
        change_set: ChangeSet,
        source_snapshots: Mapping[str, CodeSourceSnapshot],
        file_parse_results: Mapping[str, FileParseResult],
    ) -> tuple[dict[str, CodeSourceSnapshot], dict[str, FileParseResult]]:
        if not isinstance(source_snapshots, Mapping):
            raise ValueError("source_snapshots must be a source_ref_id mapping")
        if not isinstance(file_parse_results, Mapping):
            raise ValueError("file_parse_results must be a source_ref_id mapping")

        expected_sources = {
            source.source_ref_id: source for source in change_set.source_refs
        }
        if set(source_snapshots) != set(expected_sources):
            raise ValueError(
                "source_snapshots must exactly cover ChangeSet.source_refs"
            )
        if set(file_parse_results) != set(expected_sources):
            raise ValueError(
                "file_parse_results must exactly cover ChangeSet.source_refs"
            )

        snapshots: dict[str, CodeSourceSnapshot] = {}
        parse_results: dict[str, FileParseResult] = {}
        for source_ref_id, source_ref in expected_sources.items():
            snapshot = source_snapshots[source_ref_id]
            if not isinstance(snapshot, CodeSourceSnapshot):
                raise ValueError(
                    "source_snapshots must contain CodeSourceSnapshot values"
                )
            if snapshot.source_ref != source_ref:
                raise ValueError(
                    "CodeSourceSnapshot source_ref must match ChangeSet source"
                )
            source_ref.verify_content(snapshot.content)

            parse_result = file_parse_results[source_ref_id]
            if not isinstance(parse_result, FileParseResult):
                raise ValueError(
                    "file_parse_results must contain FileParseResult values"
                )
            if parse_result.analysis.source_ref != source_ref:
                raise ValueError(
                    "FileParseResult source_ref must match ChangeSet source"
                )
            parse_result.analysis.validate()
            FileParseResult(
                analysis=parse_result.analysis,
                compatibility_facts=parse_result.compatibility_facts,
            )
            self._validate_parse_ranges(snapshot, parse_result)
            snapshots[source_ref_id] = snapshot
            parse_results[source_ref_id] = parse_result
        return snapshots, parse_results

    def _validate_snapshot_file_semantics(
        self,
        change_set: ChangeSet,
        snapshots: Mapping[str, CodeSourceSnapshot],
        atoms_by_id: Mapping[str, ChangeAtom],
    ) -> None:
        for changed_file in change_set.files:
            if changed_file.is_binary:
                continue
            file_atoms = [atoms_by_id[atom_id] for atom_id in changed_file.atom_ids]
            old_snapshot = (
                None
                if changed_file.old_source_ref_id is None
                else snapshots[changed_file.old_source_ref_id]
            )
            new_snapshot = (
                None
                if changed_file.new_source_ref_id is None
                else snapshots[changed_file.new_source_ref_id]
            )
            deleted_lines = sorted(
                line for atom in file_atoms for line in atom.deleted_old_lines
            )
            added_lines = sorted(
                line for atom in file_atoms for line in atom.added_new_lines
            )
            if changed_file.status == "added":
                if new_snapshot is None:
                    raise ValueError("added ChangedFile requires a head snapshot")
                expected = list(
                    range(1, len(new_snapshot.content.splitlines(keepends=True)) + 1)
                )
                if any(atom.kind != "addition" for atom in file_atoms) or (
                    added_lines != expected
                ):
                    raise ValueError(
                        "added ChangedFile atoms must cover the complete head source"
                    )
            elif changed_file.status == "deleted":
                if old_snapshot is None:
                    raise ValueError("deleted ChangedFile requires a base snapshot")
                expected = list(
                    range(1, len(old_snapshot.content.splitlines(keepends=True)) + 1)
                )
                if any(atom.kind != "deletion" for atom in file_atoms) or (
                    deleted_lines != expected
                ):
                    raise ValueError(
                        "deleted ChangedFile atoms must cover the complete base source"
                    )
            else:
                if old_snapshot is None or new_snapshot is None:
                    raise ValueError(
                        "modified/renamed ChangedFile requires base/head snapshots"
                    )
                unchanged = (
                    old_snapshot.source_ref.content_hash
                    == new_snapshot.source_ref.content_hash
                )
                if unchanged and (
                    changed_file.status != "renamed" or file_atoms
                ):
                    raise ValueError(
                        "unchanged source is valid only for a pure rename"
                    )
                if not unchanged and not file_atoms:
                    raise ValueError("edited source requires at least one ChangeAtom")

    def _validate_parse_ranges(
        self,
        snapshot: CodeSourceSnapshot,
        parse_result: FileParseResult,
    ) -> None:
        line_count = len(snapshot.content.splitlines())
        boundaries = self._utf16_boundaries(snapshot.content)
        structures: tuple[
            DeclarationOccurrence | ReviewRegion | FactOccurrence, ...
        ] = (
            *parse_result.analysis.declarations,
            *parse_result.analysis.review_regions,
            *parse_result.analysis.fact_occurrences,
        )
        for structure in structures:
            if structure.span.end_line > line_count:
                raise ValueError(
                    "FileParseResult structural span exceeds source content"
                )
            if (
                structure.exact_range.start_offset_utf16 not in boundaries
                or structure.exact_range.end_offset_utf16 not in boundaries
            ):
                raise ValueError(
                    "FileParseResult structural UTF-16 range is not a source boundary"
                )
            start_index = boundaries[structure.exact_range.start_offset_utf16]
            end_index = boundaries[structure.exact_range.end_offset_utf16]
            if snapshot.content[:start_index].count("\n") + 1 != (
                structure.span.start_line
            ):
                raise ValueError(
                    "FileParseResult structural start line/offset mismatch"
                )
            mapped_end_line = snapshot.content[:end_index].count("\n") + 1
            ends_after_declared_newline = (
                end_index > 0
                and snapshot.content[end_index - 1] == "\n"
                and mapped_end_line == structure.span.end_line + 1
            )
            if (
                mapped_end_line != structure.span.end_line
                and not ends_after_declared_newline
            ):
                raise ValueError(
                    "FileParseResult structural end line/offset mismatch"
                )

    def _build_source_result(
        self,
        *,
        changed_file: ChangedFile,
        role: SourceRole,
        snapshot: CodeSourceSnapshot,
        parse_result: FileParseResult,
        atoms: tuple[ChangeAtom, ...],
    ) -> ReviewUnitFileResult:
        source_ref = snapshot.source_ref
        side_atoms = tuple(
            atom
            for atom in atoms
            if self._atom_source_ref_id(atom, role) == source_ref.source_ref_id
        )
        self._validate_atom_ranges(snapshot, side_atoms, role)
        line_to_atom = self._line_to_atom(side_atoms, role)
        parser_quality = ParserQuality(
            parser_layer=parse_result.analysis.parser_quality.layer,
            warnings=list(parse_result.analysis.parser_quality.warnings),
        )
        if not side_atoms:
            return ReviewUnitFileResult(
                path=source_ref.path,
                units=[],
                parser_quality=parser_quality,
                source_ref_id=source_ref.source_ref_id,
                source_role=role,
                changed_file_id=changed_file.changed_file_id,
            )

        region_assignments, remaining_lines = self._assign_regions(
            parse_result,
            set(line_to_atom),
        )
        units = [
            self._build_region_unit(
                region=region,
                lines=lines,
                role=role,
                snapshot=snapshot,
                parse_result=parse_result,
                line_to_atom=line_to_atom,
            )
            for region, lines in region_assignments
        ]

        file_diagnostics: list[ReviewUnitDiagnostic] = []
        unassigned_lines: set[int] = set()
        if remaining_lines:
            declaration_facts = self._formal_declaration_facts(
                parse_result,
                snapshot.content,
            )
            declaration_result = self.review_unit_builder.build_file_result(
                source_ref.path,
                snapshot.content,
                declaration_facts,
                "diff",
                self._line_hunks(remaining_lines),
                source_ref_id=source_ref.source_ref_id,
            )
            file_diagnostics.extend(declaration_result.diagnostics)
            if role == "head":
                unassigned_lines.update(declaration_result.unassigned_hunk_lines)
            for unit in declaration_result.units:
                self._scope_unit_to_change(
                    unit,
                    role=role,
                    source_ref_id=source_ref.source_ref_id,
                    line_to_atom=line_to_atom,
                    quality_diagnostics=self._quality_diagnostics(parse_result),
                )
                units.append(unit)

        units.sort(key=self._unit_sort_key)
        self._verify_unit_text(units, snapshot.content)
        target_lines_by_atom = {
            atom.atom_id: set(self._atom_lines(atom, role)) for atom in side_atoms
        }
        assigned_lines_by_atom: dict[str, set[int]] = {
            atom.atom_id: set() for atom in side_atoms
        }
        for unit in units:
            effective_lines = (
                unit.changed_old_lines if role == "base" else unit.changed_new_lines
            )
            for line in effective_lines:
                atom_id = line_to_atom.get(line)
                if atom_id is not None:
                    assigned_lines_by_atom[atom_id].add(line)
        unassigned_atom_ids = sorted(
            atom_id
            for atom_id, target_lines in target_lines_by_atom.items()
            if assigned_lines_by_atom[atom_id] != target_lines
        )
        if unassigned_atom_ids:
            unassigned_set = set(unassigned_atom_ids)
            units = self._drop_unassigned_atoms(
                units,
                role=role,
                line_to_atom=line_to_atom,
                unassigned_atom_ids=unassigned_set,
            )
            if role == "head":
                unassigned_lines.update(
                    line
                    for atom_id in unassigned_set
                    for line in target_lines_by_atom[atom_id]
                )
            file_diagnostics.append(
                ReviewUnitDiagnostic(code="change_atom_unassigned")
            )

        return ReviewUnitFileResult(
            path=source_ref.path,
            units=units,
            parser_quality=parser_quality,
            diagnostics=self._merge_diagnostics([], file_diagnostics),
            unassigned_hunk_lines=sorted(unassigned_lines),
            source_ref_id=source_ref.source_ref_id,
            source_role=role,
            changed_file_id=changed_file.changed_file_id,
            unassigned_change_atom_ids=unassigned_atom_ids,
        )

    def _formal_declaration_facts(
        self,
        parse_result: FileParseResult,
        source: str,
    ) -> CodeFacts:
        compatibility = parse_result.compatibility_facts
        occurrences = parse_result.analysis.declarations
        occurrence_by_id = {
            occurrence.declaration_id: occurrence for occurrence in occurrences
        }
        declarations = [
            self._compatibility_declaration(
                occurrence,
                occurrence_by_id,
                source,
            )
            for occurrence in occurrences
        ]
        return CodeFacts(
            path=compatibility.path,
            imports=list(compatibility.imports),
            components=set(compatibility.components),
            apis=set(compatibility.apis),
            decorators=set(compatibility.decorators),
            attributes=set(compatibility.attributes),
            symbols=set(compatibility.symbols),
            syntax=set(compatibility.syntax),
            declarations=declarations,
            parser_layer=compatibility.parser_layer,
            warnings=list(compatibility.warnings),
        )

    def _compatibility_declaration(
        self,
        occurrence: DeclarationOccurrence,
        occurrence_by_id: Mapping[str, DeclarationOccurrence],
        source: str,
    ) -> Declaration:
        parent = (
            None
            if occurrence.parent_id is None
            else occurrence_by_id[occurrence.parent_id]
        )
        return Declaration(
            kind=cast(DeclarationKind, occurrence.kind),
            name=occurrence.name,
            qualified_name=occurrence.qualified_name,
            parent_name=None if parent is None else parent.name,
            span=SourceSpan(
                start_line=occurrence.span.start_line,
                end_line=occurrence.span.end_line,
            ),
            text=extract_lines(
                source,
                occurrence.span.start_line,
                occurrence.span.end_line,
            ),
            declaration_id=occurrence.declaration_id,
            parent_id=occurrence.parent_id,
            start_offset_utf16=occurrence.exact_range.start_offset_utf16,
            end_offset_utf16=occurrence.exact_range.end_offset_utf16,
        )

    def _assign_regions(
        self,
        parse_result: FileParseResult,
        changed_lines: set[int],
    ) -> tuple[list[tuple[ReviewRegion, tuple[int, ...]]], set[int]]:
        lines_by_region: dict[str, set[int]] = {}
        region_by_id = {
            region.region_id: region
            for region in parse_result.analysis.review_regions
        }
        remaining = set(changed_lines)
        for line in sorted(changed_lines):
            candidates = [
                region
                for region in parse_result.analysis.review_regions
                if region.span.contains_line(line)
            ]
            if not candidates:
                continue
            innermost = [
                candidate
                for candidate in candidates
                if not any(
                    candidate is not other
                    and self._strictly_contains_region(candidate, other)
                    for other in candidates
                )
            ]
            for selected in sorted(innermost, key=self._region_priority_key):
                lines_by_region.setdefault(selected.region_id, set()).add(line)
            remaining.remove(line)
        assignments = [
            (region_by_id[region_id], tuple(sorted(lines)))
            for region_id, lines in lines_by_region.items()
        ]
        assignments.sort(key=lambda item: self._region_sort_key(item[0]))
        return assignments, remaining

    def _build_region_unit(
        self,
        *,
        region: ReviewRegion,
        lines: tuple[int, ...],
        role: SourceRole,
        snapshot: CodeSourceSnapshot,
        parse_result: FileParseResult,
        line_to_atom: Mapping[int, str],
    ) -> ReviewUnit:
        span = ReviewUnitSpan(region.span.start_line, region.span.end_line)
        changed_lines = list(lines)
        quality_diagnostics = self._quality_diagnostics(parse_result)
        context_degraded = region.quality == "recovered" or bool(
            quality_diagnostics
        )
        return ReviewUnit(
            file=snapshot.source_ref.path,
            unit_symbol=region.symbol,
            unit_ref=f"{region.symbol}@{snapshot.source_ref.path}",
            full_text=extract_lines(
                snapshot.content,
                span.start_line,
                span.end_line,
            ),
            changed_lines=changed_lines,
            file_changed_lines=changed_lines,
            unit_changed_lines=[line - span.start_line + 1 for line in lines],
            host_summary=HostSummary(),
            context_degraded=context_degraded,
            unit_id=declaration_unit_id(
                snapshot.source_ref.path,
                region.kind,
                region.symbol,
                span.start_line,
                span.end_line,
                start_offset_utf16=region.exact_range.start_offset_utf16,
                end_offset_utf16=region.exact_range.end_offset_utf16,
                source_role=role,
                source_ref_id=snapshot.source_ref.source_ref_id,
            ),
            unit_kind=region.kind,
            source_span=span,
            context_span=span,
            changed_new_lines=changed_lines if role == "head" else [],
            selection_reason="changed_review_region",
            diagnostics=quality_diagnostics,
            source_ref_id=snapshot.source_ref.source_ref_id,
            source_role=role,
            change_atom_ids=sorted({line_to_atom[line] for line in lines}),
            changed_old_lines=changed_lines if role == "base" else [],
            owner_ref=OwnerRef(kind="region", ref_id=region.region_id),
            identity_source_ref_id=snapshot.source_ref.source_ref_id,
            identity_start_offset_utf16=region.exact_range.start_offset_utf16,
            identity_end_offset_utf16=region.exact_range.end_offset_utf16,
        )

    def _scope_unit_to_change(
        self,
        unit: ReviewUnit,
        *,
        role: SourceRole,
        source_ref_id: str,
        line_to_atom: Mapping[int, str],
        quality_diagnostics: list[ReviewUnitDiagnostic],
    ) -> None:
        unit.source_role = role
        unit.identity_source_ref_id = source_ref_id
        unit.change_atom_ids = sorted(
            {line_to_atom[line] for line in unit.file_changed_lines}
        )
        if role == "base":
            unit.changed_old_lines = list(unit.changed_new_lines)
            unit.changed_new_lines = []
        unit.diagnostics = self._merge_diagnostics(
            unit.diagnostics,
            quality_diagnostics,
        )
        if quality_diagnostics:
            unit.context_degraded = True
        unit.unit_id = self._source_scoped_unit_id(unit)
        unit.validate()

    def _source_scoped_unit_id(self, unit: ReviewUnit) -> str:
        if unit.unit_kind == "fallback":
            return fallback_unit_id(
                unit.file,
                unit.source_span.start_line,
                unit.source_span.end_line,
                unit.context_span.start_line,
                unit.context_span.end_line,
                source_role=unit.source_role,
                source_ref_id=unit.identity_source_ref_id,
            )
        return declaration_unit_id(
            unit.file,
            unit.unit_kind,
            unit.unit_symbol,
            unit.source_span.start_line,
            unit.source_span.end_line,
            start_offset_utf16=unit.identity_start_offset_utf16,
            end_offset_utf16=unit.identity_end_offset_utf16,
            source_role=unit.source_role,
            source_ref_id=unit.identity_source_ref_id,
        )

    def _drop_unassigned_atoms(
        self,
        units: list[ReviewUnit],
        *,
        role: SourceRole,
        line_to_atom: Mapping[int, str],
        unassigned_atom_ids: set[str],
    ) -> list[ReviewUnit]:
        retained_units: list[ReviewUnit] = []
        for unit in units:
            retained_lines = [
                line
                for line in unit.file_changed_lines
                if line_to_atom[line] not in unassigned_atom_ids
            ]
            if not retained_lines:
                continue
            unit.changed_lines = retained_lines
            unit.file_changed_lines = retained_lines
            if role == "base":
                unit.changed_old_lines = retained_lines
                unit.changed_new_lines = []
            else:
                unit.changed_old_lines = []
                unit.changed_new_lines = retained_lines
            unit.unit_changed_lines = [
                line - unit.context_span.start_line + 1
                for line in retained_lines
            ]
            unit.change_atom_ids = sorted(
                {line_to_atom[line] for line in retained_lines}
            )
            unit.validate()
            retained_units.append(unit)
        retained_units.sort(key=self._unit_sort_key)
        return retained_units

    def _quality_diagnostics(
        self,
        parse_result: FileParseResult,
    ) -> list[ReviewUnitDiagnostic]:
        quality = parse_result.analysis.parser_quality
        codes: set[str] = set()
        if quality.layer == "parse_degraded":
            codes.add("parser_degraded")
        if (quality.error_nodes or 0) > 0 or "parser_error_nodes" in (
            parse_result.analysis.diagnostics
        ):
            codes.add("parser_error_nodes")
        if (quality.missing_nodes or 0) > 0 or "parser_missing_nodes" in (
            parse_result.analysis.diagnostics
        ):
            codes.add("parser_missing_nodes")
        return [
            ReviewUnitDiagnostic(code=cast(ReviewUnitDiagnosticCode, code))
            for code in sorted(codes)
        ]

    def _verify_unit_text(self, units: list[ReviewUnit], source: str) -> None:
        for unit in units:
            expected = extract_lines(
                source,
                unit.context_span.start_line,
                unit.context_span.end_line,
            )
            if unit.full_text != expected:
                raise ValueError(
                    "ReviewUnit.full_text must equal its source context slice"
                )

    def _line_to_atom(
        self,
        atoms: tuple[ChangeAtom, ...],
        role: SourceRole,
    ) -> dict[int, str]:
        result: dict[int, str] = {}
        for atom in atoms:
            for line in self._atom_lines(atom, role):
                if line in result:
                    raise ValueError(
                        "changed lines must map to one ChangeAtom per source role"
                    )
                result[line] = atom.atom_id
        return result

    def _line_hunks(self, lines: set[int]) -> list[FileHunk]:
        return [
            FileHunk(new_start=start, new_lines=end - start + 1)
            for start, end in self._line_runs(lines)
        ]

    def _line_runs(self, lines: set[int]) -> list[tuple[int, int]]:
        if not lines:
            return []
        ordered = sorted(lines)
        runs: list[tuple[int, int]] = []
        start = previous = ordered[0]
        for line in ordered[1:]:
            if line == previous + 1:
                previous = line
                continue
            runs.append((start, previous))
            start = previous = line
        runs.append((start, previous))
        return runs

    def _atom_source_ref_id(
        self,
        atom: ChangeAtom,
        role: SourceRole,
    ) -> str | None:
        return atom.old_source_ref_id if role == "base" else atom.new_source_ref_id

    def _atom_lines(
        self,
        atom: ChangeAtom,
        role: SourceRole,
    ) -> tuple[int, ...]:
        return atom.deleted_old_lines if role == "base" else atom.added_new_lines

    def _validate_atom_ranges(
        self,
        snapshot: CodeSourceSnapshot,
        atoms: tuple[ChangeAtom, ...],
        role: SourceRole,
    ) -> None:
        for atom in atoms:
            exact_range = atom.old_span if role == "base" else atom.new_span
            if exact_range is None:
                raise ValueError("ChangeAtom source side requires an exact range")
            expected = self._full_line_range(
                snapshot.content,
                exact_range.start_line,
                exact_range.end_line,
            )
            if exact_range != expected:
                raise ValueError(
                    "ChangeAtom exact range does not match its source line slice"
                )

    def _full_line_range(
        self,
        source: str,
        start_line: int,
        end_line: int,
    ) -> ExactRange:
        lines = source.splitlines(keepends=True)
        if start_line < 1 or end_line < start_line or end_line > len(lines):
            raise ValueError("ChangeAtom line span exceeds source content")
        start_offset = sum(
            len(line.encode("utf-16-le")) // 2
            for line in lines[: start_line - 1]
        )
        end_offset = start_offset + sum(
            len(line.encode("utf-16-le")) // 2
            for line in lines[start_line - 1 : end_line]
        )
        return ExactRange(
            start_line=start_line,
            end_line=end_line,
            start_offset_utf16=start_offset,
            end_offset_utf16=end_offset,
        )

    def _utf16_boundaries(self, source: str) -> dict[int, int]:
        boundaries = {0: 0}
        offset = 0
        for index, character in enumerate(source, start=1):
            offset += 2 if ord(character) > 0xFFFF else 1
            boundaries[offset] = index
        return boundaries

    def _file_sources(
        self,
        changed_file: ChangedFile,
    ) -> tuple[tuple[SourceRole, str], ...]:
        sources: list[tuple[SourceRole, str]] = []
        if changed_file.old_source_ref_id is not None:
            sources.append(("base", changed_file.old_source_ref_id))
        if changed_file.new_source_ref_id is not None:
            sources.append(("head", changed_file.new_source_ref_id))
        return tuple(sources)

    def _region_priority_key(self, region: ReviewRegion) -> tuple[object, ...]:
        return (
            region.span.line_count,
            region.exact_range.end_offset_utf16
            - region.exact_range.start_offset_utf16,
            region.span.start_line,
            region.exact_range.start_offset_utf16,
            region.kind,
            region.symbol,
            region.region_id,
        )

    def _strictly_contains_region(
        self,
        outer: ReviewRegion,
        inner: ReviewRegion,
    ) -> bool:
        return outer.exact_range.contains(inner.exact_range) and (
            outer.exact_range.start_offset_utf16
            != inner.exact_range.start_offset_utf16
            or outer.exact_range.end_offset_utf16
            != inner.exact_range.end_offset_utf16
        )

    def _region_sort_key(self, region: ReviewRegion) -> tuple[object, ...]:
        return (
            region.span.start_line,
            region.exact_range.start_offset_utf16,
            region.span.end_line,
            region.exact_range.end_offset_utf16,
            region.kind,
            region.symbol,
            region.region_id,
        )

    def _unit_sort_key(self, unit: ReviewUnit) -> tuple[object, ...]:
        return (
            unit.context_span.start_line,
            unit.context_span.end_line,
            unit.source_span.start_line,
            unit.source_span.end_line,
            unit.unit_id,
        )

    def _file_result_sort_key(
        self,
        result: ReviewUnitFileResult,
    ) -> tuple[object, ...]:
        return (
            result.changed_file_id,
            0 if result.source_role == "base" else 1,
            result.path,
            result.source_ref_id,
        )

    def _merge_diagnostics(
        self,
        first: list[ReviewUnitDiagnostic],
        second: list[ReviewUnitDiagnostic],
    ) -> list[ReviewUnitDiagnostic]:
        lines_by_code: dict[str, set[int]] = {}
        for diagnostic in [*first, *second]:
            lines_by_code.setdefault(diagnostic.code, set()).update(
                diagnostic.lines
            )
        return [
            ReviewUnitDiagnostic(
                code=cast(ReviewUnitDiagnosticCode, code),
                lines=tuple(sorted(lines)),
            )
            for code, lines in sorted(lines_by_code.items())
        ]


def build_change_review_units(
    *,
    change_set: ChangeSet,
    source_snapshots: Mapping[str, CodeSourceSnapshot],
    file_parse_results: Mapping[str, FileParseResult],
    review_unit_builder: ReviewUnitBuilder,
) -> ReviewUnitBuildResult:
    """Functional entry point for the independently injected RU-4 pipeline."""

    return ChangeSetReviewUnitBuilder(review_unit_builder).build(
        change_set,
        source_snapshots,
        file_parse_results,
    )
