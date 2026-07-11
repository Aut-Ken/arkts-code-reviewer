from __future__ import annotations

from dataclasses import asdict

from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import ArktsTreeSitterParser
from arkts_code_reviewer.code_analysis.lexical import LexicalParser
from arkts_code_reviewer.code_analysis.models import (
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
    RetrievalQuery,
    RetrievalUnit,
    ReviewUnit,
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

        review_units = []
        retrieval_units: list[RetrievalUnit] = []
        all_tags: set[str] = set()
        warnings: list[str] = []
        parser_layers: set[str] = set()
        review_unit_ids: set[str] = set()

        for file_input in files:
            facts = self.parser.parse(file_input.content, file_input.path)
            warnings.extend(
                f"{file_input.path}: {warning}" for warning in facts.warnings
            )
            parser_layers.add(facts.parser_layer)

            units = self.unit_builder.build_units(
                file_input.path,
                file_input.content,
                facts,
                mode,
                file_input.hunks,
            )

            for unit in units:
                self._validate_unit_for_file(unit, file_input, mode)
                if unit.unit_id in review_unit_ids:
                    raise ValueError(
                        f"duplicate ReviewUnit unit_id in AnalysisResult: {unit.unit_id!r}"
                    )
                review_unit_ids.add(unit.unit_id)
                unit_source = self._unit_source_with_imports(
                    file_input.content, unit.full_text
                )
                unit_facts = self.parser.parse(unit_source, file_input.path)
                unit_tags = derive_tags(unit_facts)
                all_tags.update(unit_tags)
                review_units.append(unit)
                retrieval_units.append(
                    RetrievalUnit(
                        unit_ref=unit.unit_ref,
                        code_features=CodeFeatures.from_facts(unit_facts, unit_tags),
                        intent_summary=self._intent_summary(unit_facts, unit_tags),
                    )
                )

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
                warnings=warnings,
            ),
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
        return asdict(result)

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
        if "parse_degraded" in layers:
            return "parse_degraded"
        if "L1" in layers:
            return "L1"
        return "L0"

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

    def _unit_source_with_imports(self, file_source: str, unit_text: str) -> str:
        import_lines = [
            line
            for line in file_source.splitlines()
            if line.lstrip().startswith("import ")
        ]
        if not import_lines:
            return unit_text
        return "\n".join(import_lines) + "\n\n" + unit_text
