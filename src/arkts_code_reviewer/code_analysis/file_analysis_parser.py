from __future__ import annotations

import copy
import re
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from arkts_code_reviewer.code_analysis.api_ownership import (
    canonical_api_bindings,
    canonicalize_api_call,
)
from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import (
    ArktsTreeSitterParser,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    DeclarationOccurrence,
    ExactRange,
    FactKind,
    FactOccurrence,
    FileAnalysis,
    FileAnalysisDiagnostic,
    FileParseResult,
    FileParserQuality,
    OwnerRef,
    ReviewRegion,
    ScopedFacts,
)
from arkts_code_reviewer.code_analysis.models import (
    CodeFacts,
    CodeParser,
    ImportInfo,
    SourceSpan,
)

SIDECAR_OUTPUT_SCHEMA = "file-analysis-v1"
SIDECAR_OFFSET_UNIT = "utf16_code_unit"
SIDECAR_PRODUCER_VERSION = "arkts-parser-sidecar-v2.0.0"
PYTHON_PRODUCER_VERSION = "arkts-file-analysis-python-v1.0.0"
LEGACY_FALLBACK_VERSION = (
    f"{PYTHON_PRODUCER_VERSION}/{SIDECAR_PRODUCER_VERSION}/legacy-fallback"
)

_LEGACY_KEYS = {
    "parser",
    "parser_version",
    "path",
    "root_type",
    "node_count",
    "error_nodes",
    "missing_nodes",
    "components",
    "calls",
    "decorators",
    "attributes",
    "symbols",
    "syntax",
    "declarations",
}
_TOP_LEVEL_KEYS = _LEGACY_KEYS | {
    "output_schema",
    "producer_version",
    "offset_unit",
    "declarations_v2",
    "review_regions",
    "raw_occurrences",
    "error_spans",
    "missing_spans",
}
_DECLARATION_KEYS = {
    "local_id",
    "kind",
    "name",
    "qualified_name",
    "span",
    "start_offset",
    "end_offset",
    "parent",
}
_REGION_KEYS = {
    "local_id",
    "kind",
    "symbol",
    "span",
    "start_offset",
    "end_offset",
    "owner",
}
_OCCURRENCE_KEYS = {
    "local_id",
    "kind",
    "name",
    "canonical_name",
    "span",
    "start_offset",
    "end_offset",
    "owner",
}
_IMPORT_OCCURRENCE_KEYS = _OCCURRENCE_KEYS | {
    "module",
    "imported_name",
    "local_name",
}
_IMPORT_USE_KEYS = _OCCURRENCE_KEYS | {
    "binding_local_id",
    "binding_region_local_id",
    "binding_status",
}
_RAW_CALL_KEYS = _OCCURRENCE_KEYS | {"root_name", "binding_status"}
_RESOURCE_REFERENCE_KEYS = _OCCURRENCE_KEYS | {"binding_status"}
_DIAGNOSTIC_KEYS = {
    "local_id",
    "kind",
    "node_type",
    "span",
    "start_offset",
    "end_offset",
    "owner",
}
_LEGACY_DECLARATION_KEYS = {
    "kind",
    "name",
    "qualified_name",
    "parent_name",
    "span",
}
_LEGACY_SPAN_KEYS = {"start_line", "end_line", "start_col", "end_col"}
_DECLARATION_KINDS = {
    "struct",
    "class",
    "function",
    "method",
    "build_method",
    "builder",
    "ui_block",
}
_RAW_FACT_KINDS: Mapping[str, FactKind] = {
    "component": "component",
    "decorator": "decorator",
    "attribute": "attribute",
    "syntax": "syntax",
    "import_binding": "import_binding",
    "field_read": "field_read",
    "field_write": "field_write",
    "import_use": "import_use",
    "resource_reference": "resource_reference",
    "string_literal": "string_literal",
    "symbol": "symbol",
}


class FileAnalysisSnapshotError(ValueError):
    """Raised when the versioned sidecar snapshot violates its closed schema."""


class FileAnalysisParser(Protocol):
    """Public contract for parsing one immutable source snapshot exactly once."""

    def parse_file(
        self,
        source_ref: CodeSourceRef,
        source: str,
    ) -> FileParseResult: ...


class ArktsFileAnalysisParser:
    """Parse one complete source into compatibility facts and occurrence facts."""

    def __init__(self, parser: ArktsTreeSitterParser | None = None) -> None:
        self.parser = parser or ArktsTreeSitterParser()

    def parse_file(self, source_ref: CodeSourceRef, source: str) -> FileParseResult:
        source_ref.verify_content(source)
        facts = self.parser.fallback.parse(source, source_ref.path)
        if not self.parser.sidecar_path.exists():
            facts.warnings.append(
                f"arkts_tree_sitter_unavailable: {self.parser.sidecar_path}"
            )
            return self._fallback_result(
                source_ref,
                facts,
                "file_analysis_sidecar_unavailable",
            )

        try:
            snapshot = self.parser._run_sidecar(
                source,
                source_ref.path,
                output_schema=SIDECAR_OUTPUT_SCHEMA,
            )
        except Exception as exc:
            facts.parser_layer = "parse_degraded"
            facts.warnings.append(f"arkts_tree_sitter_failed: {exc}")
            return self._fallback_result(
                source_ref,
                facts,
                "file_analysis_sidecar_failed",
            )

        if error := snapshot.get("error"):
            facts.parser_layer = "parse_degraded"
            facts.warnings.append(f"arkts_tree_sitter_failed: {error}")
            return self._fallback_result(
                source_ref,
                facts,
                "file_analysis_sidecar_failed",
            )

        try:
            parsed = _parse_snapshot(snapshot, source_ref, source)
        except (FileAnalysisSnapshotError, TypeError, ValueError) as exc:
            facts.parser_layer = "parse_degraded"
            facts.warnings.append(f"arkts_file_analysis_invalid: {exc}")
            return self._fallback_result(
                source_ref,
                facts,
                "file_analysis_snapshot_invalid",
            )

        fallback_facts = copy.deepcopy(facts)
        try:
            self.parser._merge_snapshot(facts, source, snapshot)
            _attach_declaration_identity(facts, parsed.declarations)
        except (TypeError, ValueError) as exc:
            fallback_facts.parser_layer = "parse_degraded"
            fallback_facts.warnings.append(f"arkts_file_analysis_invalid: {exc}")
            return self._fallback_result(
                source_ref,
                fallback_facts,
                "file_analysis_snapshot_invalid",
            )
        facts.parser_layer = "L1"
        if parsed.error_nodes:
            facts.warnings.append(
                f"arkts_tree_sitter_error_nodes: {parsed.error_nodes}"
            )
        if parsed.missing_nodes:
            facts.warnings.append(
                f"arkts_tree_sitter_missing_nodes: {parsed.missing_nodes}"
            )
        facts.warnings = sorted(set(facts.warnings))

        diagnostics: set[FileAnalysisDiagnostic] = set(parsed.diagnostics)
        if parsed.error_nodes:
            diagnostics.add("parser_error_nodes")
        if parsed.missing_nodes:
            diagnostics.add("parser_missing_nodes")
        analysis = FileAnalysis.create(
            source_ref=source_ref,
            parser_version=parsed.parser_version,
            parser_quality=FileParserQuality(
                layer="L1",
                error_nodes=parsed.error_nodes,
                missing_nodes=parsed.missing_nodes,
                warnings=tuple(facts.warnings),
            ),
            file_hints=ScopedFacts.from_code_facts(facts),
            declarations=parsed.declarations,
            review_regions=parsed.review_regions,
            fact_occurrences=parsed.fact_occurrences,
            diagnostics=tuple(sorted(diagnostics)),
        )
        return FileParseResult(analysis=analysis, compatibility_facts=facts)

    def _fallback_result(
        self,
        source_ref: CodeSourceRef,
        facts: CodeFacts,
        diagnostic: FileAnalysisDiagnostic,
    ) -> FileParseResult:
        facts.warnings = sorted(set(facts.warnings))
        analysis = FileAnalysis.create(
            source_ref=source_ref,
            parser_version=LEGACY_FALLBACK_VERSION,
            parser_quality=FileParserQuality(
                layer=facts.parser_layer,
                error_nodes=None,
                missing_nodes=None,
                warnings=tuple(facts.warnings),
            ),
            file_hints=ScopedFacts.from_code_facts(facts),
            diagnostics=(diagnostic,),
        )
        return FileParseResult(analysis=analysis, compatibility_facts=facts)


class LegacyFileAnalysisAdapter:
    """Adapt a legacy CodeParser without inventing occurrence-level evidence."""

    def __init__(self, parser: CodeParser) -> None:
        self.parser = parser

    def parse_file(self, source_ref: CodeSourceRef, source: str) -> FileParseResult:
        source_ref.verify_content(source)
        facts = self.parser.parse(source, source_ref.path)
        facts.warnings = sorted(set(facts.warnings))
        parser_version = (
            f"{LEGACY_FALLBACK_VERSION}/{type(self.parser).__module__}."
            f"{type(self.parser).__qualname__}"
        )
        analysis = FileAnalysis.create(
            source_ref=source_ref,
            parser_version=parser_version,
            parser_quality=FileParserQuality(
                layer=facts.parser_layer,
                error_nodes=None,
                missing_nodes=None,
                warnings=tuple(facts.warnings),
            ),
            file_hints=ScopedFacts.from_code_facts(facts),
            diagnostics=("occurrence_extraction_unavailable",),
        )
        return FileParseResult(analysis=analysis, compatibility_facts=facts)


class _ParsedSnapshot:
    def __init__(
        self,
        *,
        parser_version: str,
        error_nodes: int,
        missing_nodes: int,
        declarations: tuple[DeclarationOccurrence, ...],
        review_regions: tuple[ReviewRegion, ...],
        fact_occurrences: tuple[FactOccurrence, ...],
        diagnostics: tuple[FileAnalysisDiagnostic, ...],
    ) -> None:
        self.parser_version = parser_version
        self.error_nodes = error_nodes
        self.missing_nodes = missing_nodes
        self.declarations = declarations
        self.review_regions = review_regions
        self.fact_occurrences = fact_occurrences
        self.diagnostics = diagnostics


def _attach_declaration_identity(
    facts: CodeFacts,
    occurrences: tuple[DeclarationOccurrence, ...],
) -> None:
    """Attach v2 identity to v1 declarations without replacing their text/spans."""

    by_identity: dict[
        tuple[str, str, str, int], deque[DeclarationOccurrence]
    ] = defaultdict(deque)
    for occurrence in occurrences:
        by_identity[
            (
                occurrence.kind,
                occurrence.name,
                occurrence.qualified_name,
                occurrence.span.end_line,
            )
        ].append(occurrence)

    for declaration in facts.declarations:
        key = (
            declaration.kind,
            declaration.name,
            declaration.qualified_name,
            declaration.span.end_line,
        )
        candidates = by_identity.get(key)
        if not candidates:
            raise FileAnalysisSnapshotError(
                "legacy declaration cannot be aligned with declarations_v2"
            )
        occurrence = candidates.popleft()
        declaration.declaration_id = occurrence.declaration_id
        declaration.parent_id = occurrence.parent_id
        declaration.start_offset_utf16 = occurrence.exact_range.start_offset_utf16
        declaration.end_offset_utf16 = occurrence.exact_range.end_offset_utf16

    if any(candidates for candidates in by_identity.values()):
        raise FileAnalysisSnapshotError(
            "declarations_v2 contains an unmatched declaration"
        )


def _parse_snapshot(
    snapshot: dict[str, Any],
    source_ref: CodeSourceRef,
    source: str,
) -> _ParsedSnapshot:
    _require_keys(snapshot, _TOP_LEVEL_KEYS, "snapshot")
    if snapshot["output_schema"] != SIDECAR_OUTPUT_SCHEMA:
        raise FileAnalysisSnapshotError("snapshot.output_schema does not match")
    if snapshot["producer_version"] != SIDECAR_PRODUCER_VERSION:
        raise FileAnalysisSnapshotError("snapshot.producer_version does not match")
    if snapshot["offset_unit"] != SIDECAR_OFFSET_UNIT:
        raise FileAnalysisSnapshotError("snapshot.offset_unit does not match")
    if snapshot["path"] != source_ref.path:
        raise FileAnalysisSnapshotError("snapshot.path does not match source_ref.path")
    _validate_legacy_snapshot(snapshot)
    parser_name = _required_string(snapshot["parser"], "snapshot.parser")
    grammar_version = _required_string(
        snapshot["parser_version"], "snapshot.parser_version"
    )
    error_nodes = _non_negative_int(snapshot["error_nodes"], "snapshot.error_nodes")
    missing_nodes = _non_negative_int(
        snapshot["missing_nodes"], "snapshot.missing_nodes"
    )
    boundaries = _utf16_boundaries(source)

    raw_errors = _required_list(snapshot["error_spans"], "snapshot.error_spans")
    raw_missing = _required_list(snapshot["missing_spans"], "snapshot.missing_spans")
    if len(raw_errors) != error_nodes or len(raw_missing) != missing_nodes:
        raise FileAnalysisSnapshotError("diagnostic span counts do not match node counts")
    affected_ranges = tuple(
        _parse_diagnostic_range(
            item,
            f"{context}[{index}]",
            boundaries,
            "error_node" if context == "error_spans" else "missing_node",
        )
        for context, values in (
            ("error_spans", raw_errors),
            ("missing_spans", raw_missing),
        )
        for index, item in enumerate(values)
    )

    raw_declarations = _parse_raw_items(
        snapshot["declarations_v2"],
        _DECLARATION_KEYS,
        "declarations_v2",
        boundaries,
    )
    declaration_ids: dict[str, str] = {}
    for item in raw_declarations:
        local_id = _local_id(item, "declaration")
        span, exact_range = _range_pair(item)
        declaration_ids[local_id] = DeclarationOccurrence.expected_id(
            source_ref.source_ref_id,
            _required_string(item["kind"], f"{local_id}.kind"),
            _required_string(item["qualified_name"], f"{local_id}.qualified_name"),
            span,
            exact_range,
        )
    declarations: list[DeclarationOccurrence] = []
    declaration_quality: dict[str, str] = {}
    for item in raw_declarations:
        local_id = str(item["local_id"])
        span, exact_range = _range_pair(item)
        parent_local_id = _local_ref(item["parent"], "declaration", allow_null=True)
        parent_id = None if parent_local_id is None else declaration_ids.get(parent_local_id)
        if parent_local_id is not None and parent_id is None:
            raise FileAnalysisSnapshotError(f"{local_id}.parent is dangling")
        quality = "recovered" if _touches_any(exact_range, affected_ranges) else "exact"
        declaration_quality[local_id] = quality
        declarations.append(
            DeclarationOccurrence.create(
                source_ref_id=source_ref.source_ref_id,
                kind=_required_string(item["kind"], f"{local_id}.kind"),
                name=_required_string(item["name"], f"{local_id}.name"),
                qualified_name=_required_string(
                    item["qualified_name"], f"{local_id}.qualified_name"
                ),
                span=span,
                exact_range=exact_range,
                parent_id=parent_id,
                quality=quality,  # type: ignore[arg-type]
            )
        )

    raw_regions = _parse_raw_items(
        snapshot["review_regions"],
        _REGION_KEYS,
        "review_regions",
        boundaries,
    )
    region_ids: dict[str, str] = {}
    region_owner_ids: dict[str, str | None] = {}
    for item in raw_regions:
        local_id = _local_id(item, "region")
        span, exact_range = _range_pair(item)
        owner_local_id = _local_ref(item["owner"], "declaration", allow_null=True)
        owner_id = None if owner_local_id is None else declaration_ids.get(owner_local_id)
        if owner_local_id is not None and owner_id is None:
            raise FileAnalysisSnapshotError(f"{local_id}.owner is dangling")
        region_owner_ids[local_id] = owner_id
        region_ids[local_id] = ReviewRegion.expected_id(
            source_ref.source_ref_id,
            _required_string(item["kind"], f"{local_id}.kind"),  # type: ignore[arg-type]
            _required_string(item["symbol"], f"{local_id}.symbol"),
            span,
            exact_range,
            owner_id,
        )
    review_regions: list[ReviewRegion] = []
    region_quality: dict[str, str] = {}
    for item in raw_regions:
        local_id = str(item["local_id"])
        span, exact_range = _range_pair(item)
        quality = "recovered" if _touches_any(exact_range, affected_ranges) else "exact"
        owner_local_id = _local_ref(item["owner"], "declaration", allow_null=True)
        if owner_local_id is not None and declaration_quality[owner_local_id] != "exact":
            quality = "recovered"
        region_quality[local_id] = quality
        review_regions.append(
            ReviewRegion.create(
                source_ref_id=source_ref.source_ref_id,
                kind=_required_string(item["kind"], f"{local_id}.kind"),  # type: ignore[arg-type]
                symbol=_required_string(item["symbol"], f"{local_id}.symbol"),
                span=span,
                exact_range=exact_range,
                owner_declaration_id=region_owner_ids[local_id],
                quality=quality,  # type: ignore[arg-type]
                provenance="recovered" if quality == "recovered" else "L1",
            )
        )

    _validate_diagnostic_owner_refs(
        (*raw_errors, *raw_missing),
        declaration_ids,
        region_ids,
    )

    diagnostics: set[FileAnalysisDiagnostic] = set()
    occurrences: list[FactOccurrence] = []
    raw_occurrences = _required_list(
        snapshot["raw_occurrences"], "snapshot.raw_occurrences"
    )
    raw_import_bindings = _raw_import_binding_index(raw_occurrences)
    seen_occurrence_local_ids: set[str] = set()
    bindings = canonical_api_bindings(
        _imports_from_raw_bindings(raw_import_bindings.values())
    )
    for index, raw in enumerate(raw_occurrences):
        item = _required_dict(raw, f"raw_occurrences[{index}]")
        raw_kind = _required_string(item.get("kind"), f"raw_occurrences[{index}].kind")
        if raw_kind == "import_binding":
            expected_keys = _IMPORT_OCCURRENCE_KEYS
        elif raw_kind == "import_use":
            expected_keys = _IMPORT_USE_KEYS
        elif raw_kind == "raw_call":
            expected_keys = _RAW_CALL_KEYS
        elif raw_kind == "resource_reference":
            expected_keys = _RESOURCE_REFERENCE_KEYS
        else:
            expected_keys = _OCCURRENCE_KEYS
        _require_keys(item, expected_keys, f"raw_occurrences[{index}]")
        _validate_range_item(item, f"raw_occurrences[{index}]", boundaries)
        local_id = _local_id(item, "occurrence")
        if local_id in seen_occurrence_local_ids:
            raise FileAnalysisSnapshotError("duplicate raw occurrence local_id")
        seen_occurrence_local_ids.add(local_id)
        owner_ref, owner_quality = _formal_owner(
            item["owner"], declaration_ids, region_ids, declaration_quality, region_quality
        )
        span, exact_range = _range_pair(item)
        quality = "recovered" if _touches_any(exact_range, affected_ranges) else "exact"
        if owner_quality == "recovered":
            quality = "recovered"
        if owner_ref is None:
            quality = "unresolved"
            diagnostics.add("unresolved_fact_owner")

        name = _required_string(item["name"], f"{local_id}.name")
        canonical_name = item["canonical_name"]
        if canonical_name is not None:
            canonical_name = _required_string(canonical_name, f"{local_id}.canonical_name")
        fact_kind = _RAW_FACT_KINDS.get(raw_kind)
        if raw_kind == "import_binding":
            module = _required_string(item["module"], f"{local_id}.module")
            imported_name = _required_string(
                item["imported_name"], f"{local_id}.imported_name"
            )
            local_name = _required_string(
                item["local_name"], f"{local_id}.local_name"
            )
            if name != local_name or canonical_name != f"{module}#{imported_name}":
                raise FileAnalysisSnapshotError(
                    f"{local_id} import binding fields are inconsistent"
                )
        elif raw_kind == "import_use":
            _validate_import_use(item, raw_import_bindings, region_ids, local_id)
            if _binding_status(item["binding_status"], local_id) != "clear":
                if item["binding_status"] == "ambiguous":
                    diagnostics.add("ambiguous_binding_scope")
                continue
        elif raw_kind == "resource_reference":
            resource_status = _binding_status(item["binding_status"], local_id)
            if resource_status != "clear":
                if resource_status == "ambiguous":
                    diagnostics.add("ambiguous_binding_scope")
                continue
        if raw_kind == "raw_call":
            root_name_value = item["root_name"]
            root_name = (
                None
                if root_name_value is None
                else _required_string(root_name_value, f"{local_id}.root_name")
            )
            binding_status = _binding_status(item["binding_status"], local_id)
            if binding_status == "ambiguous":
                diagnostics.add("ambiguous_binding_scope")
            if root_name != _raw_call_root(name):
                raise FileAnalysisSnapshotError(
                    f"{local_id}.root_name does not match the call"
                )
            api_name = None
            if root_name is not None and binding_status == "clear":
                api_name = canonicalize_api_call(name, bindings)
            if api_name is None:
                canonical_name = name
                fact_kind = "call"
            else:
                canonical_name = api_name
                fact_kind = "api"
        if fact_kind is None:
            raise FileAnalysisSnapshotError(f"unsupported raw occurrence kind: {raw_kind}")
        if raw_kind not in {
            "raw_call",
            "resource_reference",
            "string_literal",
        } and canonical_name is None:
            raise FileAnalysisSnapshotError(
                f"{local_id}.canonical_name is required for {raw_kind}"
            )
        occurrences.append(
            FactOccurrence.create(
                source_ref_id=source_ref.source_ref_id,
                kind=fact_kind,
                name=name,
                canonical_name=canonical_name,
                span=span,
                exact_range=exact_range,
                owner_ref=owner_ref,
                quality=quality,  # type: ignore[arg-type]
                provenance="recovered" if quality == "recovered" else "L1",
            )
        )

    declarations_tuple = tuple(sorted(declarations, key=_declaration_sort_key))
    regions_tuple = tuple(sorted(review_regions, key=_region_sort_key))
    occurrences_tuple = tuple(sorted(occurrences, key=_occurrence_sort_key))
    return _ParsedSnapshot(
        parser_version=(
            f"{PYTHON_PRODUCER_VERSION}/{SIDECAR_PRODUCER_VERSION}/"
            f"{parser_name}@{grammar_version}/{SIDECAR_OUTPUT_SCHEMA}"
        ),
        error_nodes=error_nodes,
        missing_nodes=missing_nodes,
        declarations=declarations_tuple,
        review_regions=regions_tuple,
        fact_occurrences=occurrences_tuple,
        diagnostics=tuple(sorted(diagnostics)),
    )


def _parse_raw_items(
    value: object,
    keys: set[str],
    context: str,
    boundaries: Mapping[int, tuple[int, int]],
) -> list[dict[str, Any]]:
    values = _required_list(value, f"snapshot.{context}")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(values):
        item_context = f"{context}[{index}]"
        item = _required_dict(raw, item_context)
        _require_keys(item, keys, item_context)
        _validate_range_item(item, item_context, boundaries)
        local_id = _required_string(item["local_id"], f"{item_context}.local_id")
        if local_id in seen:
            raise FileAnalysisSnapshotError(f"duplicate {context} local_id")
        seen.add(local_id)
        result.append(item)
    return result


def _validate_legacy_snapshot(snapshot: Mapping[str, Any]) -> None:
    _required_string(snapshot["root_type"], "snapshot.root_type")
    _non_negative_int(snapshot["node_count"], "snapshot.node_count")
    for field in (
        "components",
        "calls",
        "decorators",
        "attributes",
        "symbols",
        "syntax",
    ):
        values = _required_list(snapshot[field], f"snapshot.{field}")
        if any(not isinstance(value, str) or not value for value in values):
            raise FileAnalysisSnapshotError(
                f"snapshot.{field} must contain non-empty strings"
            )
        if values != sorted(set(values)):
            raise FileAnalysisSnapshotError(
                f"snapshot.{field} must be sorted and unique"
            )
    declarations = _required_list(
        snapshot["declarations"], "snapshot.declarations"
    )
    for index, raw in enumerate(declarations):
        context = f"snapshot.declarations[{index}]"
        item = _required_dict(raw, context)
        _require_keys(item, _LEGACY_DECLARATION_KEYS, context)
        kind = _required_string(item["kind"], f"{context}.kind")
        if kind not in _DECLARATION_KINDS:
            raise FileAnalysisSnapshotError(f"{context}.kind is unsupported")
        _required_string(item["name"], f"{context}.name")
        _required_string(item["qualified_name"], f"{context}.qualified_name")
        if item["parent_name"] is not None:
            _required_string(item["parent_name"], f"{context}.parent_name")
        span = _required_dict(item["span"], f"{context}.span")
        _require_keys(span, _LEGACY_SPAN_KEYS, f"{context}.span")
        start_line = _positive_int(span["start_line"], f"{context}.span.start_line")
        end_line = _positive_int(span["end_line"], f"{context}.span.end_line")
        if end_line < start_line:
            raise FileAnalysisSnapshotError(f"{context}.span is reversed")
        _positive_int(span["start_col"], f"{context}.span.start_col")
        _positive_int(span["end_col"], f"{context}.span.end_col")


def _raw_import_binding_index(
    values: list[Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(values):
        item = _required_dict(raw, f"raw_occurrences[{index}]")
        if item.get("kind") != "import_binding":
            continue
        _require_keys(item, _IMPORT_OCCURRENCE_KEYS, f"raw_occurrences[{index}]")
        local_id = _required_string(
            item["local_id"], f"raw_occurrences[{index}].local_id"
        )
        if local_id in result:
            raise FileAnalysisSnapshotError("duplicate import binding local_id")
        result[local_id] = item
    return result


def _imports_from_raw_bindings(
    values: Iterable[Mapping[str, Any]],
) -> list[ImportInfo]:
    modules: set[str] = set()
    default_names: dict[str, str] = {}
    namespace_names: dict[str, str] = {}
    named_bindings: dict[str, dict[str, str]] = {}
    for item in values:
        module = _required_string(item["module"], "raw import binding.module")
        imported = _required_string(
            item["imported_name"], "raw import binding.imported_name"
        )
        local = _required_string(item["local_name"], "raw import binding.local_name")
        modules.add(module)
        if imported == "default":
            default_names[module] = local
        elif imported == "*":
            namespace_names[module] = local
        else:
            named_bindings.setdefault(module, {})[local] = imported
    return [
        ImportInfo(
            module=module,
            default_name=default_names.get(module),
            namespace_name=namespace_names.get(module),
            named=named_bindings.get(module, {}),
        )
        for module in sorted(modules)
    ]


def _validate_import_use(
    item: Mapping[str, Any],
    bindings: Mapping[str, Mapping[str, Any]],
    region_ids: Mapping[str, str],
    local_id: str,
) -> None:
    binding_local_id = _required_string(
        item["binding_local_id"], f"{local_id}.binding_local_id"
    )
    region_local_id = _required_string(
        item["binding_region_local_id"],
        f"{local_id}.binding_region_local_id",
    )
    binding = bindings.get(binding_local_id)
    if binding is None:
        raise FileAnalysisSnapshotError(f"{local_id} references a missing binding")
    binding_owner = _required_dict(binding["owner"], f"{binding_local_id}.owner")
    if (
        binding_owner.get("kind") != "region"
        or binding_owner.get("local_id") != region_local_id
        or region_local_id not in region_ids
    ):
        raise FileAnalysisSnapshotError(f"{local_id} binding region is inconsistent")
    if (
        item["name"] != binding["name"]
        or item["canonical_name"] != binding["canonical_name"]
    ):
        raise FileAnalysisSnapshotError(f"{local_id} does not match its import binding")


def _parse_diagnostic_range(
    value: object,
    context: str,
    boundaries: Mapping[int, tuple[int, int]],
    expected_kind: str,
) -> ExactRange:
    item = _required_dict(value, context)
    _require_keys(item, _DIAGNOSTIC_KEYS, context)
    _validate_range_item(item, context, boundaries)
    local_id = _required_string(item["local_id"], f"{context}.local_id")
    prefix = "error:" if expected_kind == "error_node" else "missing:"
    if not local_id.startswith(prefix):
        raise FileAnalysisSnapshotError(f"{context}.local_id has the wrong prefix")
    if item["kind"] != expected_kind:
        raise FileAnalysisSnapshotError(f"{context}.kind does not match its array")
    _required_string(item["node_type"], f"{context}.node_type")
    if item["owner"] is not None:
        reference = _required_dict(item["owner"], f"{context}.owner")
        _require_keys(reference, {"kind", "local_id"}, f"{context}.owner")
        if reference["kind"] not in {"declaration", "region"}:
            raise FileAnalysisSnapshotError(f"{context}.owner.kind is unsupported")
        _required_string(reference["local_id"], f"{context}.owner.local_id")
    return _range_pair(item)[1]


def _validate_diagnostic_owner_refs(
    values: tuple[object, ...],
    declaration_ids: Mapping[str, str],
    region_ids: Mapping[str, str],
) -> None:
    seen: set[str] = set()
    for index, raw in enumerate(values):
        item = _required_dict(raw, f"diagnostic[{index}]")
        local_id = str(item["local_id"])
        if local_id in seen:
            raise FileAnalysisSnapshotError("duplicate diagnostic local_id")
        seen.add(local_id)
        owner = item["owner"]
        if owner is None:
            continue
        ids = declaration_ids if owner["kind"] == "declaration" else region_ids
        if owner["local_id"] not in ids:
            raise FileAnalysisSnapshotError("diagnostic owner is dangling")


def _validate_range_item(
    item: Mapping[str, Any],
    context: str,
    boundaries: Mapping[int, tuple[int, int]],
) -> None:
    span = _required_dict(item.get("span"), f"{context}.span")
    _require_keys(span, {"start_line", "end_line"}, f"{context}.span")
    start_line = _positive_int(span["start_line"], f"{context}.span.start_line")
    end_line = _positive_int(span["end_line"], f"{context}.span.end_line")
    if end_line < start_line:
        raise FileAnalysisSnapshotError(f"{context}.span is reversed")
    start = _non_negative_int(item.get("start_offset"), f"{context}.start_offset")
    end = _non_negative_int(item.get("end_offset"), f"{context}.end_offset")
    if end < start:
        raise FileAnalysisSnapshotError(f"{context} offsets are reversed")
    if start not in boundaries or end not in boundaries:
        raise FileAnalysisSnapshotError(f"{context} offset is not a UTF-16 boundary")
    if boundaries[start][1] != start_line or boundaries[end][1] != end_line:
        raise FileAnalysisSnapshotError(f"{context} line span does not match its offsets")


def _range_pair(item: Mapping[str, Any]) -> tuple[SourceSpan, ExactRange]:
    span_data = item["span"]
    start_line = int(span_data["start_line"])
    end_line = int(span_data["end_line"])
    return (
        SourceSpan(start_line=start_line, end_line=end_line),
        ExactRange(
            start_line=start_line,
            end_line=end_line,
            start_offset_utf16=int(item["start_offset"]),
            end_offset_utf16=int(item["end_offset"]),
        ),
    )


def _formal_owner(
    value: object,
    declaration_ids: Mapping[str, str],
    region_ids: Mapping[str, str],
    declaration_quality: Mapping[str, str],
    region_quality: Mapping[str, str],
) -> tuple[OwnerRef | None, str | None]:
    if value is None:
        return None, None
    reference = _required_dict(value, "occurrence.owner")
    _require_keys(reference, {"kind", "local_id"}, "occurrence.owner")
    kind = _required_string(reference["kind"], "occurrence.owner.kind")
    local_id = _required_string(reference["local_id"], "occurrence.owner.local_id")
    if kind == "declaration":
        ref_id = declaration_ids.get(local_id)
        quality = declaration_quality.get(local_id)
    elif kind == "region":
        ref_id = region_ids.get(local_id)
        quality = region_quality.get(local_id)
    else:
        raise FileAnalysisSnapshotError("occurrence.owner.kind is unsupported")
    if ref_id is None or quality is None:
        raise FileAnalysisSnapshotError("occurrence.owner is dangling")
    return OwnerRef(kind, ref_id), quality  # type: ignore[arg-type]


def _local_ref(value: object, kind: str, *, allow_null: bool) -> str | None:
    if value is None and allow_null:
        return None
    reference = _required_dict(value, "local reference")
    _require_keys(reference, {"kind", "local_id"}, "local reference")
    if reference["kind"] != kind:
        raise FileAnalysisSnapshotError(f"local reference must use kind {kind!r}")
    return _required_string(reference["local_id"], "local reference.local_id")


def _local_id(item: Mapping[str, Any], prefix: str) -> str:
    local_id = _required_string(item["local_id"], "local_id")
    if not local_id.startswith(f"{prefix}:"):
        raise FileAnalysisSnapshotError(f"local_id must start with {prefix!r}")
    return local_id


def _touches_any(value: ExactRange, affected: tuple[ExactRange, ...]) -> bool:
    for other in affected:
        if other.start_offset_utf16 == other.end_offset_utf16:
            if value.start_offset_utf16 <= other.start_offset_utf16 <= value.end_offset_utf16:
                return True
        elif (
            value.start_offset_utf16 < other.end_offset_utf16
            and other.start_offset_utf16 < value.end_offset_utf16
        ):
            return True
    return False


def _utf16_boundaries(source: str) -> dict[int, tuple[int, int]]:
    result = {0: (0, 1)}
    offset = 0
    line = 1
    for index, character in enumerate(source, start=1):
        offset += 2 if ord(character) > 0xFFFF else 1
        if character == "\n":
            line += 1
        result[offset] = (index, line)
    return result


def _declaration_sort_key(item: DeclarationOccurrence) -> tuple[object, ...]:
    return (
        item.span.start_line,
        item.exact_range.start_offset_utf16,
        item.span.end_line,
        item.exact_range.end_offset_utf16,
        item.kind,
        item.qualified_name,
        item.declaration_id,
    )


def _region_sort_key(item: ReviewRegion) -> tuple[object, ...]:
    return (
        item.span.start_line,
        item.exact_range.start_offset_utf16,
        item.span.end_line,
        item.exact_range.end_offset_utf16,
        item.kind,
        item.symbol,
        item.region_id,
    )


def _occurrence_sort_key(item: FactOccurrence) -> tuple[object, ...]:
    return (
        item.span.start_line,
        item.exact_range.start_offset_utf16,
        item.span.end_line,
        item.exact_range.end_offset_utf16,
        item.kind,
        item.canonical_name or item.name,
        item.occurrence_id,
    )


def _require_keys(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise FileAnalysisSnapshotError(
            f"{context} keys mismatch; missing={missing!r}, unknown={unknown!r}"
        )


def _required_dict(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FileAnalysisSnapshotError(f"{context} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise FileAnalysisSnapshotError(f"{context} keys must be strings")
    return value


def _required_list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise FileAnalysisSnapshotError(f"{context} must be an array")
    return value


def _required_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise FileAnalysisSnapshotError(f"{context} must be a non-empty string")
    return value


def _binding_status(value: object, context: str) -> str:
    if value not in {"clear", "shadowed", "ambiguous"}:
        raise FileAnalysisSnapshotError(
            f"{context}.binding_status is unsupported"
        )
    return str(value)


def _non_negative_int(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FileAnalysisSnapshotError(f"{context} must be a non-negative integer")
    return value


def _raw_call_root(value: str) -> str | None:
    """Match the sidecar's lexical root for arbitrary call-expression spines."""

    match = re.match(r"^([A-Za-z_$][A-Za-z0-9_$]*)", value)
    return None if match is None else match.group(1)


def _positive_int(value: object, context: str) -> int:
    value = _non_negative_int(value, context)
    if value < 1:
        raise FileAnalysisSnapshotError(f"{context} must be >= 1")
    return value
