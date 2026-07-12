from __future__ import annotations

from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import ArktsTreeSitterParser
from arkts_code_reviewer.code_analysis.lexical import LexicalParser
from arkts_code_reviewer.code_analysis.models import (
    REVIEW_UNIT_BUILD_SCHEMA_VERSION,
    AnalysisMetadata,
    AnalysisMode,
    AnalysisResult,
    CodeFacts,
    CodeFeatures,
    CodeParser,
    FileHunk,
    FileInput,
    MrContext,
    ParserLayer,
    ParserQuality,
    RetrievalQuery,
    RetrievalUnit,
    ReviewUnit,
    ReviewUnitBuildResult,
    ReviewUnitDiagnostic,
    ReviewUnitFileResult,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import normalize_review_path
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.tagger import derive_tags, trigger_dimensions
from arkts_code_reviewer.code_analysis.text_utils import extract_lines


class CodeAnalyzer:
    def __init__(
        self,
        parser: CodeParser | None = None,
        unit_builder: ReviewUnitBuilder | None = None,
        token_budget: int = 8000,
    ) -> None:
        self.parser = parser or ArktsTreeSitterParser(fallback=LexicalParser())
        self.unit_builder = unit_builder or ReviewUnitBuilder()
        self.token_budget = token_budget

    def analyze_files(
        self,
        files: list[FileInput],
        mode: AnalysisMode = "full",
        token_budget: int | None = None,
    ) -> AnalysisResult:
        self._validate_inputs(files, mode)

        retrieval_units: list[RetrievalUnit] = []
        file_results: list[ReviewUnitFileResult] = []
        all_tags: set[str] = set()
        warnings: list[str] = []
        parser_layers: set[str] = set()
        review_unit_ids: set[str] = set()

        for file_input in sorted(
            files,
            key=lambda item: normalize_review_path(item.path),
        ):
            facts = self.parser.parse(file_input.content, file_input.path)
            warnings.extend(
                f"{file_input.path}: {warning}" for warning in facts.warnings
            )
            parser_layers.add(facts.parser_layer)

            file_result = self.unit_builder.build_file_result(
                file_input.path,
                file_input.content,
                facts,
                mode,
                file_input.hunks,
            )
            self._validate_file_result_contract(
                file_result,
                file_input,
                facts,
                mode,
            )
            for unit in file_result.units:
                self._validate_unit_for_file(unit, file_input, mode)
            self._validate_file_result_assignment(file_result, file_input, mode)
            for unit in file_result.units:
                if unit.unit_id in review_unit_ids:
                    raise ValueError(
                        f"duplicate ReviewUnit unit_id in AnalysisResult: {unit.unit_id!r}"
                    )
                review_unit_ids.add(unit.unit_id)
                unit_source = self._unit_source_with_imports(
                    file_input.content, unit.full_text
                )
                unit_facts = self.parser.parse(unit_source, file_input.path)
                parser_layers.add(unit_facts.parser_layer)
                for warning in unit_facts.warnings:
                    scoped_warning = f"unit {unit.unit_id}: {warning}"
                    warnings.append(f"{file_input.path}: {scoped_warning}")
                self._propagate_unit_parser_quality(unit, unit_facts)
                unit_tags = derive_tags(unit_facts)
                all_tags.update(unit_tags)
                retrieval_units.append(
                    RetrievalUnit(
                        unit_ref=unit.unit_ref,
                        code_features=CodeFeatures.from_facts(unit_facts, unit_tags),
                        intent_summary=self._intent_summary(unit_facts, unit_tags),
                    )
                )

            file_results.append(
                ReviewUnitFileResult(
                    path=file_result.path,
                    units=file_result.units,
                    parser_quality=file_result.parser_quality,
                    diagnostics=file_result.diagnostics,
                    unassigned_hunk_lines=file_result.unassigned_hunk_lines,
                )
            )

        review_unit_build_result = ReviewUnitBuildResult(
            schema_version=REVIEW_UNIT_BUILD_SCHEMA_VERSION,
            mode=mode,
            file_results=file_results,
        )
        review_units = review_unit_build_result.flatten_units()

        mr_context = MrContext(
            triggered_dimensions=trigger_dimensions(all_tags),
            token_budget=token_budget or self.token_budget,
        )
        return AnalysisResult(
            retrieval_query=RetrievalQuery(
                mr_context=mr_context, units=retrieval_units
            ),
            review_units=review_units,
            metadata=AnalysisMetadata(
                parser_layer=self._dominant_parser_layer(parser_layers),
                warnings=sorted(set(warnings)),
            ),
            review_unit_build_result=review_unit_build_result,
        )

    def analyze_file(
        self,
        path: str,
        content: str,
        mode: AnalysisMode = "full",
        hunks: list[tuple[int, int]] | None = None,
        token_budget: int | None = None,
    ) -> AnalysisResult:
        file_input = FileInput(
            path=path,
            content=content,
            hunks=(
                []
                if hunks is None
                else [self._hunk(start, lines) for start, lines in hunks]
            ),
        )
        return self.analyze_files([file_input], mode=mode, token_budget=token_budget)

    def to_json_ready(self, result: AnalysisResult) -> dict[str, object]:
        return result.to_dict()

    def _intent_summary(self, facts: CodeFacts, tags: set[str]) -> str:
        components = facts.components
        apis = facts.apis
        pieces: list[str] = []
        if components:
            pieces.append("components: " + ", ".join(sorted(components)[:5]))
        if apis:
            pieces.append("apis: " + ", ".join(sorted(apis)[:5]))
        if tags:
            pieces.append("tags: " + ", ".join(sorted(tags)[:5]))
        return "; ".join(pieces) if pieces else "ArkTS review unit"

    def _dominant_parser_layer(self, layers: set[str]) -> ParserLayer:
        unsupported = sorted(layers - {"L0", "L1", "parse_degraded"})
        if unsupported:
            raise ValueError(f"unsupported parser layers: {unsupported!r}")
        if "parse_degraded" in layers:
            return "parse_degraded"
        if "L0" in layers or not layers:
            return "L0"
        return "L1"

    def _propagate_unit_parser_quality(
        self,
        unit: ReviewUnit,
        facts: CodeFacts,
    ) -> None:
        """Retain secondary-parser degradation until RU-3 removes that parse."""

        quality_codes: set[str] = set()
        if facts.parser_layer == "parse_degraded":
            quality_codes.add("parser_degraded")
        for warning in facts.warnings:
            warning_code = warning.partition(":")[0]
            if warning_code in {
                "arkts_tree_sitter_error_nodes",
                "tree_sitter_error_nodes",
            }:
                quality_codes.add("parser_error_nodes")
            elif warning_code in {
                "arkts_tree_sitter_missing_nodes",
                "tree_sitter_missing_nodes",
            }:
                quality_codes.add("parser_missing_nodes")

        if not quality_codes:
            return

        lines_by_code = {
            diagnostic.code: set(diagnostic.lines)
            for diagnostic in unit.diagnostics
        }
        for code in quality_codes:
            lines_by_code.setdefault(code, set())
        unit.diagnostics = [
            ReviewUnitDiagnostic(code=code, lines=tuple(sorted(lines)))  # type: ignore[arg-type]
            for code, lines in sorted(lines_by_code.items())
        ]
        unit.context_degraded = True
        unit.validate()

    def _hunk(self, start: int, lines: int) -> FileHunk:
        return FileHunk(new_start=start, new_lines=lines)

    def _validate_inputs(self, files: list[FileInput], mode: AnalysisMode) -> None:
        """Fail before parsing when a request cannot have stable file identity."""

        if mode not in {"full", "diff"}:
            raise ValueError(f"unsupported analysis mode: {mode}")
        if not isinstance(files, list):
            raise ValueError("files must be a list of FileInput values")

        paths: dict[str, str] = {}
        for index, file_input in enumerate(files):
            if not isinstance(file_input, FileInput):
                raise ValueError(f"files[{index}] must be a FileInput")
            if not isinstance(file_input.content, str):
                raise ValueError(f"files[{index}].content must be a string")
            if not isinstance(file_input.hunks, list) or any(
                not isinstance(hunk, FileHunk) for hunk in file_input.hunks
            ):
                raise ValueError(f"files[{index}].hunks must contain FileHunk values")

            normalized_path = normalize_review_path(file_input.path)
            previous_path = paths.get(normalized_path)
            if previous_path is not None:
                raise ValueError(
                    "duplicate normalized ReviewUnit path "
                    f"{normalized_path!r}: {previous_path!r} and {file_input.path!r}"
                )
            paths[normalized_path] = file_input.path

    def _validate_unit_for_file(
        self,
        unit: ReviewUnit,
        file_input: FileInput,
        mode: AnalysisMode,
    ) -> None:
        if not isinstance(unit, ReviewUnit):
            raise ValueError("ReviewUnitBuilder must return ReviewUnit values")
        unit.validate()
        if unit.file != file_input.path:
            raise ValueError("ReviewUnit.file must match its FileInput.path")
        source_line_count = len(file_input.content.splitlines())
        if unit.context_span.end_line > source_line_count:
            raise ValueError("ReviewUnit.context_span exceeds its FileInput source")
        expected_text = extract_lines(
            file_input.content,
            unit.context_span.start_line,
            unit.context_span.end_line,
        )
        if unit.full_text != expected_text:
            raise ValueError("ReviewUnit.full_text must equal its context_span source slice")
        if any(line > source_line_count for line in unit.file_changed_lines):
            raise ValueError("ReviewUnit.file_changed_lines exceeds its FileInput source")
        if mode == "diff" and file_input.hunks:
            hunk_lines = {
                line
                for hunk in file_input.hunks
                for line in range(hunk.new_start, hunk.new_end + 1)
            }
            if any(line not in hunk_lines for line in unit.file_changed_lines):
                raise ValueError(
                    "ReviewUnit.file_changed_lines must come from its FileInput hunks"
                )

    def _validate_file_result_contract(
        self,
        result: ReviewUnitFileResult,
        file_input: FileInput,
        facts: CodeFacts,
        mode: AnalysisMode,
    ) -> None:
        if not isinstance(result, ReviewUnitFileResult):
            raise ValueError(
                "ReviewUnitBuilder.build_file_result must return ReviewUnitFileResult"
            )
        if result.path != file_input.path:
            raise ValueError("ReviewUnitFileResult.path must match its FileInput.path")
        expected_quality = ParserQuality(
            parser_layer=facts.parser_layer,
            warnings=sorted(set(facts.warnings)),
        )
        if result.parser_quality != expected_quality:
            raise ValueError(
                "ReviewUnitFileResult.parser_quality must match the full-file CodeFacts"
            )
        result.validate()
        if mode == "full":
            if result.unassigned_hunk_lines:
                raise ValueError(
                    "full ReviewUnitFileResult must not report unassigned hunk lines"
                )

    def _validate_file_result_assignment(
        self,
        result: ReviewUnitFileResult,
        file_input: FileInput,
        mode: AnalysisMode,
    ) -> None:
        if mode == "full":
            return

        hunk_lines = {
            line
            for hunk in file_input.hunks
            for line in range(hunk.new_start, hunk.new_end + 1)
        }
        assigned_lines = {
            line
            for unit in result.units
            for line in unit.changed_new_lines
        }
        if assigned_lines | set(result.unassigned_hunk_lines) != hunk_lines:
            raise ValueError(
                "ReviewUnitFileResult must account for every diff hunk line as assigned "
                "or unassigned"
            )

    def _unit_source_with_imports(self, file_source: str, unit_text: str) -> str:
        import_lines = [
            line
            for line in file_source.splitlines()
            if line.lstrip().startswith("import ")
        ]
        if not import_lines:
            return unit_text
        return "\n".join(import_lines) + "\n\n" + unit_text
