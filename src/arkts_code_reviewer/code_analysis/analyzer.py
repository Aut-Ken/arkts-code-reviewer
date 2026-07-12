from __future__ import annotations

from collections.abc import Mapping, Sequence

from arkts_code_reviewer.code_analysis.change_review import (
    build_change_review_units,
)
from arkts_code_reviewer.code_analysis.change_set import (
    ChangeSet,
    CodeSourceSnapshot,
)
from arkts_code_reviewer.code_analysis.context_planning import (
    ContextCandidate,
    ContextPlanner,
    ContextPlanResult,
    QuestionBinding,
    RelationEdge,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    ExactRange,
    FileAnalysis,
    FileParseResult,
    FileParserQuality,
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    ArktsFileAnalysisParser,
    FileAnalysisParser,
    LegacyFileAnalysisAdapter,
)
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
    ParserQuality,
    RetrievalQuery,
    RetrievalUnit,
    ReviewUnit,
    ReviewUnitBuildResult,
    ReviewUnitFileResult,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import normalize_review_path
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.tagger import derive_tags, trigger_dimensions
from arkts_code_reviewer.code_analysis.text_utils import extract_lines
from arkts_code_reviewer.code_analysis.unit_facts import project
from arkts_code_reviewer.feature_routing.engine import FeatureRouter


class CodeAnalyzer:
    def __init__(
        self,
        parser: CodeParser | None = None,
        unit_builder: ReviewUnitBuilder | None = None,
        token_budget: int = 8000,
        *,
        file_parser: FileAnalysisParser | None = None,
    ) -> None:
        if parser is not None and file_parser is not None:
            raise ValueError("parser and file_parser are mutually exclusive")
        self.file_parser: FileAnalysisParser = (
            file_parser
            if file_parser is not None
            else (
                LegacyFileAnalysisAdapter(parser)
                if parser is not None
                else ArktsFileAnalysisParser()
            )
        )
        self.unit_builder = unit_builder or ReviewUnitBuilder()
        self.token_budget = token_budget
        self.feature_router = FeatureRouter()

    def analyze_files(
        self,
        files: list[FileInput],
        mode: AnalysisMode = "full",
        token_budget: int | None = None,
    ) -> AnalysisResult:
        self._validate_inputs(files, mode)

        retrieval_units: list[RetrievalUnit] = []
        file_results: list[ReviewUnitFileResult] = []
        file_parse_results: list[FileParseResult] = []
        unit_fact_scopes: list[UnitFactScope] = []
        exact_tags: set[str] = set()
        routing_tags: set[str] = set()
        warnings: list[str] = []
        parser_layers: set[str] = set()
        review_unit_ids: set[str] = set()
        parse_results_by_source_ref: dict[str, FileParseResult] = {}

        for file_input in sorted(
            files,
            key=lambda item: normalize_review_path(item.path),
        ):
            source_ref = file_input.source_ref or CodeSourceRef.inline(
                file_input.path,
                file_input.content,
            )
            parse_result = parse_results_by_source_ref.get(source_ref.source_ref_id)
            if parse_result is None:
                parse_result = self.file_parser.parse_file(
                    source_ref,
                    file_input.content,
                )
                self._validate_parse_result(parse_result, source_ref)
                parse_results_by_source_ref[source_ref.source_ref_id] = parse_result
            facts = parse_result.compatibility_facts
            file_parse_results.append(parse_result)
            warnings.extend(
                f"{file_input.path}: {warning}" for warning in facts.warnings
            )
            parser_layers.add(facts.parser_layer)

            file_routing_tags = self._derive_scoped_tags(
                parse_result.analysis.file_hints,
                parse_result.analysis.file_hints.to_code_facts(
                    file_input.path,
                    parser_layer=facts.parser_layer,
                ),
            )
            routing_tags.update(file_routing_tags)

            file_result = self.unit_builder.build_file_result(
                file_input.path,
                file_input.content,
                facts,
                mode,
                file_input.hunks,
                source_ref_id=source_ref.source_ref_id,
            )
            self._validate_file_result_contract(
                file_result,
                file_input,
                facts,
                mode,
                source_ref.source_ref_id,
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
                unit_scope = project(parse_result.analysis, unit)
                unit_fact_scopes.append(unit_scope)
                unit_facts = unit_scope.unit_exact.to_code_facts(
                    file_input.path,
                    parser_layer=facts.parser_layer,
                )
                unit_tags = self._derive_scoped_tags(
                    unit_scope.unit_exact,
                    unit_facts,
                )
                exact_tags.update(unit_tags)
                retrieval_units.append(
                    RetrievalUnit(
                        unit_ref=unit.unit_ref,
                        code_features=CodeFeatures.from_facts(unit_facts, unit_tags),
                        intent_summary=self._intent_summary(unit_facts, unit_tags),
                        unit_id=unit.unit_id,
                        source_ref_id=source_ref.source_ref_id,
                        unit_fact_scope=unit_scope,
                        dimensions=trigger_dimensions(unit_tags),
                        routing_tags=sorted(file_routing_tags),
                    )
                )

            file_results.append(file_result)

        review_unit_build_result = ReviewUnitBuildResult(
            schema_version="review-unit-build-v2",
            mode=mode,
            file_results=file_results,
        )
        review_units = review_unit_build_result.flatten_units()
        feature_routing_result = self.feature_router.route(unit_fact_scopes)

        mr_context = MrContext(
            triggered_dimensions=list(feature_routing_result.mr_dimensions),
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
            file_parse_results=file_parse_results,
            unit_fact_scopes=unit_fact_scopes,
            feature_routing_result=feature_routing_result,
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

    def analyze_change_set(
        self,
        change_set: ChangeSet,
        source_snapshots: Mapping[str, CodeSourceSnapshot],
        token_budget: int | None = None,
    ) -> AnalysisResult:
        """Analyze an immutable base/head ChangeSet without parsing a Git diff.

        Source acquisition stays outside this module. Every supplied snapshot is
        hash-checked before the first parser call, then each unique CodeSourceRef is
        parsed exactly once regardless of the number of ChangeAtoms or ReviewUnits.
        """

        snapshots = self._validate_change_set_inputs(change_set, source_snapshots)
        parse_results_by_source_ref: dict[str, FileParseResult] = {}
        for source_ref in sorted(
            change_set.source_refs,
            key=lambda item: (item.path, item.revision, item.source_ref_id),
        ):
            snapshot = snapshots[source_ref.source_ref_id]
            parse_result = self.file_parser.parse_file(source_ref, snapshot.content)
            self._validate_parse_result(parse_result, source_ref)
            parse_results_by_source_ref[source_ref.source_ref_id] = parse_result

        build_result = build_change_review_units(
            change_set=change_set,
            source_snapshots=snapshots,
            file_parse_results=parse_results_by_source_ref,
            review_unit_builder=self.unit_builder,
        )
        if build_result.change_set_id != change_set.change_set_id:
            raise ValueError(
                "ChangeSet ReviewUnit build result must retain change_set_id"
            )
        return self._assemble_change_set_result(
            change_set=change_set,
            build_result=build_result,
            parse_results_by_source_ref=parse_results_by_source_ref,
            token_budget=token_budget,
        )

    def plan_context(
        self,
        analysis_result: AnalysisResult,
        *,
        primary_question_bindings: Sequence[QuestionBinding] | None = None,
        source_snapshots: Mapping[str, CodeSourceSnapshot]
        | Sequence[CodeSourceSnapshot],
        supporting_file_analyses: Sequence[FileAnalysis] = (),
        candidates: Sequence[ContextCandidate] = (),
        relation_edges: Sequence[RelationEdge] = (),
        code_context_budget: int,
    ) -> ContextPlanResult:
        """Build the RU-5 context plan from one complete RU-4 result.

        This entry point deliberately forwards every ReviewUnit and every Active
        review question selected by Feature Routing. Callers may rank or omit
        Supporting candidates, but cannot override applicability or silently turn a
        direct change owner into an optional context segment. The explicit binding
        parameter is retained only as a compatibility assertion: when supplied, it
        must equal the Feature Routing result.
        """

        if not isinstance(analysis_result, AnalysisResult):
            raise ValueError("analysis_result must use AnalysisResult")
        analysis_result.validate()
        if (
            analysis_result.change_set is None
            or analysis_result.review_unit_build_result is None
            or analysis_result.review_unit_build_result.schema_version
            != "review-unit-build-v3"
        ):
            raise ValueError(
                "plan_context requires a complete review-unit-build-v3 AnalysisResult"
            )
        change_set_id = analysis_result.change_set.change_set_id
        if analysis_result.review_unit_build_result.change_set_id != change_set_id:
            raise ValueError("AnalysisResult ChangeSet and ReviewUnit build disagree")
        routed_bindings = tuple(
            QuestionBinding(
                binding.primary_unit_id,
                binding.review_question_id,
            )
            for binding in analysis_result.feature_routing_result.question_bindings
        )
        if primary_question_bindings is not None:
            supplied_bindings = tuple(primary_question_bindings)
            if any(
                not isinstance(binding, QuestionBinding)
                for binding in supplied_bindings
            ):
                raise ValueError(
                    "primary_question_bindings must contain QuestionBinding values"
                )
            supplied_bindings = tuple(
                sorted(
                    supplied_bindings,
                    key=lambda item: (
                        item.primary_unit_id,
                        item.review_question_id,
                    ),
                )
            )
            if supplied_bindings != routed_bindings:
                raise ValueError(
                    "primary_question_bindings must match FeatureRoutingResult"
                )
        self._validate_context_candidate_boundaries(
            analysis_result,
            candidates,
            relation_edges,
            source_snapshots,
            supporting_file_analyses,
        )
        blocking_change_ids = tuple(
            sorted(
                set(
                    analysis_result.review_unit_build_result.unassigned_change_atom_ids
                ).union(
                    changed_file.changed_file_id
                    for changed_file in analysis_result.change_set.files
                    if changed_file.is_binary
                )
            )
        )
        return ContextPlanner().plan(
            change_set_id=change_set_id,
            primary_units=analysis_result.review_units,
            primary_question_bindings=routed_bindings,
            source_snapshots=source_snapshots,
            candidates=candidates,
            relation_edges=relation_edges,
            blocking_change_ids=blocking_change_ids,
            code_context_budget=code_context_budget,
        )

    def _validate_context_candidate_boundaries(
        self,
        analysis_result: AnalysisResult,
        candidates: Sequence[ContextCandidate],
        relation_edges: Sequence[RelationEdge],
        source_snapshots: Mapping[str, CodeSourceSnapshot]
        | Sequence[CodeSourceSnapshot],
        supporting_file_analyses: Sequence[FileAnalysis],
    ) -> None:
        """Require every Supporting target to equal one exact Parser v2 boundary."""

        boundaries: dict[
            str,
            tuple[str, ExactRange, str, FileParserQuality],
        ] = {}
        changed_analyses = tuple(
            parse_result.analysis
            for parse_result in analysis_result.file_parse_results
        )
        if any(
            not isinstance(analysis, FileAnalysis)
            for analysis in supporting_file_analyses
        ):
            raise ValueError(
                "supporting_file_analyses must contain FileAnalysis values"
            )
        extra_analyses = tuple(supporting_file_analyses)
        changed_source_ids = {
            analysis.source_ref.source_ref_id for analysis in changed_analyses
        }
        extra_source_ids = [
            analysis.source_ref.source_ref_id for analysis in extra_analyses
        ]
        if len(extra_source_ids) != len(set(extra_source_ids)) or set(
            extra_source_ids
        ).intersection(changed_source_ids):
            raise ValueError(
                "supporting_file_analyses must use unique external source revisions"
            )
        candidate_source_ids = {
            candidate.target_source_ref_id for candidate in candidates
        }
        if set(extra_source_ids) != candidate_source_ids - changed_source_ids:
            raise ValueError(
                "supporting_file_analyses must exactly cover external candidate sources"
            )

        if isinstance(source_snapshots, Mapping):
            snapshots = dict(source_snapshots)
        elif isinstance(source_snapshots, Sequence) and not isinstance(
            source_snapshots,
            str | bytes,
        ):
            snapshot_sequence = tuple(source_snapshots)
            snapshots = {
                snapshot.source_ref.source_ref_id: snapshot
                for snapshot in snapshot_sequence
            }
            if len(snapshots) != len(snapshot_sequence):
                raise ValueError("source_snapshots contains duplicate source identities")
        else:
            raise ValueError("source_snapshots must be a mapping or sequence")
        for analysis in extra_analyses:
            snapshot = snapshots.get(analysis.source_ref.source_ref_id)
            if snapshot is None or snapshot.source_ref != analysis.source_ref:
                raise ValueError(
                    "external FileAnalysis must match its immutable source snapshot"
                )

        for analysis in (*changed_analyses, *extra_analyses):
            analysis.validate()
            source_ref_id = analysis.source_ref.source_ref_id
            for declaration in analysis.declarations:
                if declaration.declaration_id in boundaries:
                    raise ValueError("duplicate declaration boundary in AnalysisResult")
                boundaries[declaration.declaration_id] = (
                    source_ref_id,
                    declaration.exact_range,
                    declaration.quality,
                    analysis.parser_quality,
                )
            for region in analysis.review_regions:
                if region.region_id in boundaries:
                    raise ValueError("duplicate review region boundary in AnalysisResult")
                boundaries[region.region_id] = (
                    source_ref_id,
                    region.exact_range,
                    region.quality,
                    analysis.parser_quality,
                )

        edge_by_id = {
            edge.edge_id: edge
            for edge in relation_edges
            if isinstance(edge, RelationEdge)
        }
        for candidate in candidates:
            if not isinstance(candidate, ContextCandidate):
                raise ValueError("candidates must contain ContextCandidate values")
            boundary = boundaries.get(candidate.provenance_ref)
            if boundary is None:
                raise ValueError(
                    "Supporting candidate provenance is not a parsed declaration/region boundary"
                )
            source_ref_id, exact_range, quality, parser_quality = boundary
            if (
                source_ref_id != candidate.target_source_ref_id
                or exact_range != candidate.target_span
            ):
                raise ValueError(
                    "Supporting candidate target does not equal its occurrence boundary"
                )
            edge = edge_by_id.get(candidate.relation_edge_id)
            if edge is None:
                raise ValueError(
                    "Supporting candidate references an unavailable RelationEdge"
                )
            parser_is_clean = (
                parser_quality.layer == "L1"
                and parser_quality.error_nodes == 0
                and parser_quality.missing_nodes == 0
                and not parser_quality.warnings
            )
            if (
                quality != "exact" or not parser_is_clean
            ) and edge.quality != "degraded":
                raise ValueError(
                    "non-exact Supporting boundary quality requires a degraded relation"
                )

    def to_json_ready(self, result: AnalysisResult) -> dict[str, object]:
        return result.to_dict()

    def _validate_change_set_inputs(
        self,
        change_set: ChangeSet,
        source_snapshots: Mapping[str, CodeSourceSnapshot],
    ) -> dict[str, CodeSourceSnapshot]:
        if not isinstance(change_set, ChangeSet):
            raise ValueError("change_set must use ChangeSet")
        change_set.validate()
        if not isinstance(source_snapshots, Mapping):
            raise ValueError("source_snapshots must be a source_ref_id mapping")
        expected = {
            source_ref.source_ref_id: source_ref
            for source_ref in change_set.source_refs
        }
        if set(source_snapshots) != set(expected):
            raise ValueError(
                "source_snapshots must exactly cover ChangeSet.source_refs"
            )
        validated: dict[str, CodeSourceSnapshot] = {}
        for source_ref_id, source_ref in expected.items():
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
            validated[source_ref_id] = snapshot
        return validated

    def _assemble_change_set_result(
        self,
        *,
        change_set: ChangeSet,
        build_result: ReviewUnitBuildResult,
        parse_results_by_source_ref: Mapping[str, FileParseResult],
        token_budget: int | None,
    ) -> AnalysisResult:
        build_result.validate()
        review_units = build_result.flatten_units()
        parse_results = sorted(
            parse_results_by_source_ref.values(),
            key=lambda item: (
                item.analysis.source_ref.path,
                item.analysis.source_ref.revision,
                item.analysis.source_ref.source_ref_id,
            ),
        )

        routing_tags_by_source: dict[str, set[str]] = {}
        routing_tags: set[str] = set()
        parser_layers: set[str] = set()
        warnings: list[str] = []
        for parse_result in parse_results:
            analysis = parse_result.analysis
            facts = parse_result.compatibility_facts
            source_ref = analysis.source_ref
            source_routing_tags = self._derive_scoped_tags(
                analysis.file_hints,
                analysis.file_hints.to_code_facts(
                    source_ref.path,
                    parser_layer=facts.parser_layer,
                ),
            )
            routing_tags_by_source[source_ref.source_ref_id] = source_routing_tags
            routing_tags.update(source_routing_tags)
            parser_layers.add(facts.parser_layer)
            warnings.extend(
                f"{source_ref.path}@{source_ref.revision}: {warning}"
                for warning in facts.warnings
            )

        exact_tags: set[str] = set()
        unit_fact_scopes: list[UnitFactScope] = []
        retrieval_units: list[RetrievalUnit] = []
        for unit in review_units:
            if unit.source_ref_id is None:
                raise ValueError("ChangeSet ReviewUnit requires source_ref_id")
            unit_parse_result = parse_results_by_source_ref.get(unit.source_ref_id)
            if unit_parse_result is None:
                raise ValueError(
                    "ChangeSet ReviewUnit references an unparsed source revision"
                )
            unit_scope = project(unit_parse_result.analysis, unit)
            unit_fact_scopes.append(unit_scope)
            unit_facts = unit_scope.unit_exact.to_code_facts(
                unit.file,
                parser_layer=unit_parse_result.compatibility_facts.parser_layer,
            )
            unit_tags = self._derive_scoped_tags(
                unit_scope.unit_exact,
                unit_facts,
            )
            exact_tags.update(unit_tags)
            unit_routing_tags = routing_tags_by_source[unit.source_ref_id]
            retrieval_units.append(
                RetrievalUnit(
                    unit_ref=unit.unit_ref,
                    code_features=CodeFeatures.from_facts(unit_facts, unit_tags),
                    intent_summary=self._intent_summary(unit_facts, unit_tags),
                    unit_id=unit.unit_id,
                    source_ref_id=unit.source_ref_id,
                    unit_fact_scope=unit_scope,
                    dimensions=trigger_dimensions(unit_tags),
                    routing_tags=sorted(unit_routing_tags),
                )
            )

        feature_routing_result = self.feature_router.route(unit_fact_scopes)
        result = AnalysisResult(
            retrieval_query=RetrievalQuery(
                mr_context=MrContext(
                    triggered_dimensions=list(feature_routing_result.mr_dimensions),
                    token_budget=token_budget or self.token_budget,
                ),
                units=retrieval_units,
            ),
            review_units=review_units,
            metadata=AnalysisMetadata(
                parser_layer=self._dominant_parser_layer(parser_layers),
                warnings=sorted(set(warnings)),
            ),
            review_unit_build_result=build_result,
            file_parse_results=parse_results,
            unit_fact_scopes=unit_fact_scopes,
            change_set=change_set,
            feature_routing_result=feature_routing_result,
        )
        result.validate()
        return result

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

    def _derive_scoped_tags(
        self,
        scoped_facts: ScopedFacts,
        code_facts: CodeFacts,
    ) -> set[str]:
        tags = derive_tags(code_facts)
        if scoped_facts.resource_references:
            tags.add("has_resource_ref")
        return tags

    def _dominant_parser_layer(self, layers: set[str]) -> ParserLayer:
        unsupported = sorted(layers - {"L0", "L1", "parse_degraded"})
        if unsupported:
            raise ValueError(f"unsupported parser layers: {unsupported!r}")
        if "parse_degraded" in layers:
            return "parse_degraded"
        if "L0" in layers or not layers:
            return "L0"
        return "L1"

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
            if file_input.source_ref is not None:
                if not isinstance(file_input.source_ref, CodeSourceRef):
                    raise ValueError(
                        f"files[{index}].source_ref must use CodeSourceRef or None"
                    )
                if file_input.source_ref.path != normalized_path:
                    raise ValueError(
                        f"files[{index}].source_ref path must match FileInput.path"
                    )
                file_input.source_ref.verify_content(file_input.content)
            previous_path = paths.get(normalized_path)
            if previous_path is not None:
                raise ValueError(
                    "duplicate normalized ReviewUnit path "
                    f"{normalized_path!r}: {previous_path!r} and {file_input.path!r}"
                )
            paths[normalized_path] = file_input.path

    def _validate_parse_result(
        self,
        result: FileParseResult,
        source_ref: CodeSourceRef,
    ) -> None:
        if not isinstance(result, FileParseResult):
            raise ValueError(
                "FileAnalysisParser.parse_file must return FileParseResult"
            )
        if result.analysis.source_ref != source_ref:
            raise ValueError(
                "FileParseResult source_ref must match the requested source revision"
            )
        expected_hints = ScopedFacts.from_code_facts(result.compatibility_facts)
        if result.analysis.file_hints != expected_hints:
            raise ValueError(
                "FileAnalysis.file_hints must match compatibility CodeFacts"
            )
        if result.analysis.parser_quality.warnings != tuple(
            sorted(set(result.compatibility_facts.warnings))
        ):
            raise ValueError(
                "FileAnalysis parser warnings must match compatibility CodeFacts"
            )

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
        source_ref_id: str,
    ) -> None:
        if not isinstance(result, ReviewUnitFileResult):
            raise ValueError(
                "ReviewUnitBuilder.build_file_result must return ReviewUnitFileResult"
            )
        if result.path != file_input.path:
            raise ValueError("ReviewUnitFileResult.path must match its FileInput.path")
        if result.source_ref_id != source_ref_id:
            raise ValueError(
                "ReviewUnitFileResult.source_ref_id must match the parsed source"
            )
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
