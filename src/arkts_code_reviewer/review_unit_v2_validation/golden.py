from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from arkts_code_reviewer.code_analysis.change_review import (
    CHANGE_REVIEW_BUILD_SCHEMA_VERSION,
    build_change_review_units,
)
from arkts_code_reviewer.code_analysis.change_set import (
    ChangeAtomInput,
    ChangedFileInput,
    ChangeSet,
    CodeSourceSnapshot,
    normalize_change_set,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    DeclarationOccurrence,
    ExactRange,
    FileAnalysis,
    FileParseResult,
    FileParserQuality,
    ReviewRegion,
    ScopedFacts,
)
from arkts_code_reviewer.code_analysis.models import (
    CodeFacts,
    Declaration,
    ReviewUnit,
    ReviewUnitBuildResult,
    ReviewUnitDiagnostic,
    ReviewUnitSpan,
    SourceRole,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    REVIEW_UNIT_V2_DIAGNOSTIC_CODES,
    REVIEW_UNIT_V2_KINDS,
    REVIEW_UNIT_V2_SELECTION_REASONS,
    declaration_unit_id,
    fallback_unit_id,
)
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.text_utils import extract_lines

SCHEMA_VERSION = "review-unit-v2-golden-v1"
BASELINE_SCHEMA_VERSION = "review-unit-v2-golden-baseline-v1"

_ROOT_FIELDS = {
    "schema_version",
    "suite_id",
    "description",
    "coordinate_system",
    "cases",
}
_COORDINATE_FIELDS = {"line_base", "line_end", "utf16_offsets"}
_CASE_FIELDS = {
    "case_id",
    "description",
    "repository",
    "base_revision",
    "head_revision",
    "diff_normalizer_version",
    "sources",
    "files",
    "expected",
}
_SOURCE_FIELDS = {
    "alias",
    "file",
    "logical_path",
    "revision",
    "content_sha256",
    "source_ref_id",
    "analysis",
}
_ANALYSIS_FIELDS = {
    "parser_version",
    "parser_quality",
    "declarations",
    "review_regions",
    "diagnostics",
}
_QUALITY_FIELDS = {"layer", "error_nodes", "missing_nodes", "warnings"}
_DECLARATION_FIELDS = {
    "alias",
    "declaration_id",
    "kind",
    "name",
    "qualified_name",
    "parent_alias",
    "span",
    "start_offset_utf16",
    "end_offset_utf16",
    "quality",
}
_REGION_FIELDS = {
    "alias",
    "region_id",
    "kind",
    "symbol",
    "owner_alias",
    "span",
    "start_offset_utf16",
    "end_offset_utf16",
    "quality",
    "provenance",
}
_FILE_FIELDS = {
    "alias",
    "changed_file_id",
    "status",
    "old_path",
    "new_path",
    "old_source_alias",
    "new_source_alias",
    "is_binary",
    "atoms",
}
_ATOM_FIELDS = {
    "alias",
    "atom_id",
    "kind",
    "old_span",
    "new_span",
    "added_new_lines",
    "deleted_old_lines",
}
_SPAN_FIELDS = {"start_line", "end_line"}
_EXPECTED_FIELDS = {
    "schema_version",
    "change_set_id",
    "file_results",
    "diagnostics",
    "unassigned_change_atom_aliases",
    "unassigned_change_atom_ids",
    "coverage",
}
_EXPECTED_FILE_FIELDS = {
    "changed_file_alias",
    "changed_file_id",
    "source_alias",
    "source_ref_id",
    "source_role",
    "path",
    "parser_quality",
    "diagnostics",
    "unassigned_hunk_lines",
    "unassigned_change_atom_aliases",
    "unassigned_change_atom_ids",
    "units",
}
_EXPECTED_UNIT_FIELDS = {
    "unit_id",
    "unit_kind",
    "unit_symbol",
    "source_span",
    "context_span",
    "changed_new_lines",
    "changed_old_lines",
    "selection_reason",
    "context_degraded",
    "diagnostics",
    "owner",
    "change_atom_aliases",
    "change_atom_ids",
    "full_text_sha256",
}
_OWNER_FIELDS = {"kind", "alias", "ref_id"}
_DIAGNOSTIC_FIELDS = {"code", "lines"}
_COVERAGE_FIELDS = {"atom_alias", "atom_id", "source_role", "lines"}

_DECLARATION_KINDS = {
    "struct",
    "class",
    "function",
    "method",
    "build_method",
    "builder",
    "ui_block",
}
_REGION_KINDS = {"field_region", "import_region"}
_LAYERS = {"L0", "L1", "parse_degraded"}
_STRUCTURAL_QUALITIES = {"exact", "recovered"}
_PROVENANCE = {"L0", "L1", "recovered"}
_FILE_ANALYSIS_DIAGNOSTICS = {
    "file_analysis_sidecar_unavailable",
    "file_analysis_sidecar_failed",
    "file_analysis_snapshot_invalid",
    "occurrence_extraction_unavailable",
    "unresolved_fact_owner",
    "parser_error_nodes",
    "parser_missing_nodes",
    "ambiguous_binding_scope",
}
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_STABLE_ID_RE = re.compile(
    r"^(?:code-source|declaration|region|change-atom|changed-file|change-set):sha256:[0-9a-f]{64}$"
)


@dataclass(frozen=True)
class _SourceFixture:
    alias: str
    source_path: Path
    source: str
    snapshot: CodeSourceSnapshot
    parse_result: FileParseResult
    declaration_ids: dict[str, str]
    region_ids: dict[str, str]


@dataclass(frozen=True)
class _FileFixture:
    alias: str
    input: ChangedFileInput
    atom_aliases: tuple[str, ...]
    expected_changed_file_id: str
    expected_atom_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReviewUnitV2GoldenCase:
    case_id: str
    description: str
    repository: str
    base_revision: str
    head_revision: str
    diff_normalizer_version: str
    sources: tuple[_SourceFixture, ...]
    files: tuple[_FileFixture, ...]
    expected: dict[str, Any]


@dataclass(frozen=True)
class ReviewUnitV2GoldenSuite:
    suite_id: str
    manifest_path: Path
    manifest_sha256: str
    cases: tuple[ReviewUnitV2GoldenCase, ...]


def load_golden_suite(manifest_path: str | Path) -> ReviewUnitV2GoldenSuite:
    unresolved_path = Path(manifest_path)
    if unresolved_path.is_symlink():
        raise ValueError("manifest must not be a symlink")
    path = unresolved_path.resolve()
    raw = _read_regular_file(path, "manifest")
    data = _json_object(raw, str(path))
    _exact_fields(data, _ROOT_FIELDS, "manifest")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"manifest.schema_version must be {SCHEMA_VERSION!r}")
    suite_id = _text(data["suite_id"], "manifest.suite_id")
    _text(data["description"], "manifest.description")
    coordinate = _object(data["coordinate_system"], "manifest.coordinate_system")
    _exact_fields(coordinate, _COORDINATE_FIELDS, "manifest.coordinate_system")
    if coordinate != {
        "line_base": 1,
        "line_end": "inclusive",
        "utf16_offsets": "0-based-end-exclusive",
    }:
        raise ValueError("manifest.coordinate_system does not match the frozen contract")

    raw_cases = _array(data["cases"], "manifest.cases")
    if len(raw_cases) != 16:
        raise ValueError("manifest.cases must contain exactly 16 RU-4 cases")
    root = path.parent
    cases: list[ReviewUnitV2GoldenCase] = []
    seen_ids: set[str] = set()
    seen_semantics: set[str] = set()
    for index, value in enumerate(raw_cases):
        context = f"manifest.cases[{index}]"
        case, semantic = _load_case(_object(value, context), root, context)
        if case.case_id in seen_ids:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        if semantic in seen_semantics:
            raise ValueError(f"duplicate semantic Golden case: {case.case_id}")
        seen_ids.add(case.case_id)
        seen_semantics.add(semantic)
        cases.append(case)
    if [case.case_id for case in cases] != sorted(case.case_id for case in cases):
        raise ValueError("manifest.cases must be sorted by case_id")
    if [case.case_id for case in cases] != [f"RV2{index:02d}" for index in range(1, 17)]:
        raise ValueError("manifest must freeze the RV201-RV216 case matrix")
    return ReviewUnitV2GoldenSuite(
        suite_id=suite_id,
        manifest_path=path,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        cases=tuple(cases),
    )


def _load_case(
    data: dict[str, Any],
    root: Path,
    context: str,
) -> tuple[ReviewUnitV2GoldenCase, str]:
    _exact_fields(data, _CASE_FIELDS, context)
    case_id = _text(data["case_id"], f"{context}.case_id")
    description = _text(data["description"], f"{context}.description")
    repository = _text(data["repository"], f"{context}.repository")
    base_revision = _text(data["base_revision"], f"{context}.base_revision")
    head_revision = _text(data["head_revision"], f"{context}.head_revision")
    normalizer = _text(
        data["diff_normalizer_version"],
        f"{context}.diff_normalizer_version",
    )

    sources: list[_SourceFixture] = []
    source_by_alias: dict[str, _SourceFixture] = {}
    for index, value in enumerate(_array(data["sources"], f"{context}.sources")):
        source = _load_source(
            _object(value, f"{context}.sources[{index}]"),
            root,
            repository,
            f"{context}.sources[{index}]",
        )
        if source.alias in source_by_alias:
            raise ValueError(f"duplicate source alias: {source.alias}")
        source_by_alias[source.alias] = source
        sources.append(source)
    _sorted_aliases([source.alias for source in sources], f"{context}.sources")

    files: list[_FileFixture] = []
    file_aliases: set[str] = set()
    atom_aliases: set[str] = set()
    for index, value in enumerate(_array(data["files"], f"{context}.files")):
        file_fixture = _load_file(
            _object(value, f"{context}.files[{index}]"),
            source_by_alias,
            base_revision,
            head_revision,
            f"{context}.files[{index}]",
        )
        if file_fixture.alias in file_aliases:
            raise ValueError(f"duplicate changed-file alias: {file_fixture.alias}")
        duplicates = atom_aliases.intersection(file_fixture.atom_aliases)
        if duplicates:
            raise ValueError(f"duplicate ChangeAtom aliases: {sorted(duplicates)}")
        file_aliases.add(file_fixture.alias)
        atom_aliases.update(file_fixture.atom_aliases)
        files.append(file_fixture)
    _sorted_aliases([item.alias for item in files], f"{context}.files")

    change_set = normalize_change_set(
        repository=repository,
        base_revision=base_revision,
        head_revision=head_revision,
        diff_normalizer_version=normalizer,
        files=tuple(item.input for item in files),
    )
    _validate_frozen_change_ids(change_set, files, context)
    _validate_source_use(change_set, sources, context)

    expected = _load_expected(
        _object(data["expected"], f"{context}.expected"),
        change_set,
        sources,
        files,
        f"{context}.expected",
    )
    semantic = _canonical(
        {
            "repository": repository,
            "base_revision": base_revision,
            "head_revision": head_revision,
            "normalizer": normalizer,
            "source_refs": [item.snapshot.source_ref.source_ref_id for item in sources],
            "change_set_id": change_set.change_set_id,
            "expected": expected,
        }
    )
    return (
        ReviewUnitV2GoldenCase(
            case_id=case_id,
            description=description,
            repository=repository,
            base_revision=base_revision,
            head_revision=head_revision,
            diff_normalizer_version=normalizer,
            sources=tuple(sources),
            files=tuple(files),
            expected=expected,
        ),
        semantic,
    )


def _load_source(
    data: dict[str, Any],
    root: Path,
    repository: str,
    context: str,
) -> _SourceFixture:
    _exact_fields(data, _SOURCE_FIELDS, context)
    alias = _alias(data["alias"], f"{context}.alias")
    file_value = _text(data["file"], f"{context}.file")
    logical_path = _logical_path(data["logical_path"], f"{context}.logical_path")
    revision = _text(data["revision"], f"{context}.revision")
    digest = _sha(data["content_sha256"], f"{context}.content_sha256")
    source_path = _safe_child(root, file_value, f"{context}.file")
    source_bytes = _read_regular_file(source_path, f"{context}.file")
    actual_digest = hashlib.sha256(source_bytes).hexdigest()
    if digest != actual_digest:
        raise ValueError(f"{context} source hash/provenance drift")
    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{context}.file must be UTF-8") from exc
    source_ref = CodeSourceRef.create(
        repository=repository,
        revision=revision,
        path=logical_path,
        content_hash=f"sha256:{digest}",
    )
    frozen_source_id = _stable_id(data["source_ref_id"], f"{context}.source_ref_id")
    if frozen_source_id != source_ref.source_ref_id:
        raise ValueError(f"{context}.source_ref_id does not match provenance")
    snapshot = CodeSourceSnapshot(source_ref=source_ref, content=source)

    analysis_data = _object(data["analysis"], f"{context}.analysis")
    _exact_fields(analysis_data, _ANALYSIS_FIELDS, f"{context}.analysis")
    parser_version = _text(
        analysis_data["parser_version"],
        f"{context}.analysis.parser_version",
    )
    if not parser_version.startswith("fixture-file-analysis-"):
        raise ValueError(f"{context}.analysis.parser_version must freeze fixture provenance")
    quality = _load_quality(
        _object(analysis_data["parser_quality"], f"{context}.analysis.parser_quality"),
        f"{context}.analysis.parser_quality",
    )
    declarations, declaration_ids = _load_declarations(
        _array(analysis_data["declarations"], f"{context}.analysis.declarations"),
        snapshot,
        f"{context}.analysis.declarations",
    )
    regions, region_ids = _load_regions(
        _array(analysis_data["review_regions"], f"{context}.analysis.review_regions"),
        snapshot,
        declaration_ids,
        f"{context}.analysis.review_regions",
    )
    diagnostics = tuple(
        _text(value, f"{context}.analysis.diagnostics")
        for value in _array(analysis_data["diagnostics"], f"{context}.analysis.diagnostics")
    )
    if list(diagnostics) != sorted(set(diagnostics)) or any(
        value not in _FILE_ANALYSIS_DIAGNOSTICS for value in diagnostics
    ):
        raise ValueError(f"{context}.analysis.diagnostics must be sorted frozen codes")

    compatibility = CodeFacts(
        path=logical_path,
        declarations=[
            Declaration(
                kind=cast(Any, item.kind),
                name=item.name,
                qualified_name=item.qualified_name,
                parent_name=(
                    None
                    if item.parent_id is None
                    else next(
                        parent.name
                        for parent in declarations
                        if parent.declaration_id == item.parent_id
                    )
                ),
                span=item.span,
                text=extract_lines(source, item.span.start_line, item.span.end_line),
                declaration_id=item.declaration_id,
                parent_id=item.parent_id,
                start_offset_utf16=item.exact_range.start_offset_utf16,
                end_offset_utf16=item.exact_range.end_offset_utf16,
            )
            for item in declarations
        ],
        parser_layer=cast(Any, quality.layer),
        warnings=list(quality.warnings),
    )
    analysis = FileAnalysis.create(
        source_ref=source_ref,
        parser_version=parser_version,
        parser_quality=quality,
        file_hints=ScopedFacts(),
        declarations=declarations,
        review_regions=regions,
        diagnostics=cast(Any, diagnostics),
    )
    return _SourceFixture(
        alias=alias,
        source_path=source_path,
        source=source,
        snapshot=snapshot,
        parse_result=FileParseResult(
            analysis=analysis,
            compatibility_facts=compatibility,
        ),
        declaration_ids=declaration_ids,
        region_ids=region_ids,
    )


def _load_quality(data: dict[str, Any], context: str) -> FileParserQuality:
    _exact_fields(data, _QUALITY_FIELDS, context)
    layer = _text(data["layer"], f"{context}.layer")
    if layer not in _LAYERS:
        raise ValueError(f"{context}.layer is unsupported")
    warnings = tuple(
        _text(item, f"{context}.warnings")
        for item in _array(data["warnings"], f"{context}.warnings")
    )
    if list(warnings) != sorted(set(warnings)):
        raise ValueError(f"{context}.warnings must be sorted and unique")
    error_nodes = _nullable_count(data["error_nodes"], f"{context}.error_nodes")
    missing_nodes = _nullable_count(data["missing_nodes"], f"{context}.missing_nodes")
    return FileParserQuality(
        layer=cast(Any, layer),
        error_nodes=error_nodes,
        missing_nodes=missing_nodes,
        warnings=warnings,
    )


def _load_declarations(
    values: list[Any],
    snapshot: CodeSourceSnapshot,
    context: str,
) -> tuple[tuple[DeclarationOccurrence, ...], dict[str, str]]:
    declarations: list[DeclarationOccurrence] = []
    ids: dict[str, str] = {}
    aliases_by_id: dict[str, str] = {}
    pending: list[tuple[dict[str, Any], str]] = []
    for index, value in enumerate(values):
        item_context = f"{context}[{index}]"
        data = _object(value, item_context)
        _exact_fields(data, _DECLARATION_FIELDS, item_context)
        alias = _alias(data["alias"], f"{item_context}.alias")
        if alias in ids:
            raise ValueError(f"duplicate declaration alias: {alias}")
        frozen_id = _stable_id(data["declaration_id"], f"{item_context}.declaration_id")
        ids[alias] = frozen_id
        aliases_by_id[frozen_id] = alias
        pending.append((data, item_context))
    for data, item_context in pending:
        alias = cast(str, data["alias"])
        kind = _text(data["kind"], f"{item_context}.kind")
        if kind not in _DECLARATION_KINDS:
            raise ValueError(f"{item_context}.kind is unsupported")
        parent_alias = _nullable_alias(data["parent_alias"], f"{item_context}.parent_alias")
        if parent_alias is not None and parent_alias not in ids:
            raise ValueError(f"{item_context}.parent_alias is dangling")
        span, exact_range = _load_exact_range(data, snapshot.content, item_context)
        quality = _text(data["quality"], f"{item_context}.quality")
        if quality not in _STRUCTURAL_QUALITIES:
            raise ValueError(f"{item_context}.quality is unsupported")
        occurrence = DeclarationOccurrence.create(
            source_ref_id=snapshot.source_ref.source_ref_id,
            kind=kind,
            name=_text(data["name"], f"{item_context}.name"),
            qualified_name=_text(data["qualified_name"], f"{item_context}.qualified_name"),
            span=span,
            exact_range=exact_range,
            parent_id=None if parent_alias is None else ids[parent_alias],
            quality=cast(Any, quality),
        )
        if occurrence.declaration_id != ids[alias]:
            raise ValueError(f"{item_context}.declaration_id does not match identity")
        declarations.append(occurrence)
    declarations.sort(key=_declaration_sort_key)
    if [aliases_by_id[item.declaration_id] for item in declarations] != [
        cast(str, item[0]["alias"]) for item in pending
    ]:
        raise ValueError(f"{context} must use stable source order")
    by_id = {item.declaration_id: item for item in declarations}
    for item in declarations:
        if item.parent_id is not None:
            parent = by_id[item.parent_id]
            if not parent.span.contains_line_range(item.span.start_line, item.span.end_line):
                raise ValueError(f"{context} parent must contain child span")
    return tuple(declarations), ids


def _load_regions(
    values: list[Any],
    snapshot: CodeSourceSnapshot,
    declaration_ids: dict[str, str],
    context: str,
) -> tuple[tuple[ReviewRegion, ...], dict[str, str]]:
    regions: list[ReviewRegion] = []
    ids: dict[str, str] = {}
    aliases_by_id: dict[str, str] = {}
    for index, value in enumerate(values):
        item_context = f"{context}[{index}]"
        data = _object(value, item_context)
        _exact_fields(data, _REGION_FIELDS, item_context)
        alias = _alias(data["alias"], f"{item_context}.alias")
        if alias in ids:
            raise ValueError(f"duplicate region alias: {alias}")
        kind = _text(data["kind"], f"{item_context}.kind")
        if kind not in _REGION_KINDS:
            raise ValueError(f"{item_context}.kind is unsupported")
        owner_alias = _nullable_alias(data["owner_alias"], f"{item_context}.owner_alias")
        if owner_alias is not None and owner_alias not in declaration_ids:
            raise ValueError(f"{item_context}.owner_alias is dangling")
        span, exact_range = _load_exact_range(data, snapshot.content, item_context)
        quality = _text(data["quality"], f"{item_context}.quality")
        provenance = _text(data["provenance"], f"{item_context}.provenance")
        if quality not in _STRUCTURAL_QUALITIES or provenance not in _PROVENANCE:
            raise ValueError(f"{item_context} has unsupported quality/provenance")
        region = ReviewRegion.create(
            source_ref_id=snapshot.source_ref.source_ref_id,
            kind=cast(Any, kind),
            symbol=_text(data["symbol"], f"{item_context}.symbol"),
            span=span,
            exact_range=exact_range,
            owner_declaration_id=(None if owner_alias is None else declaration_ids[owner_alias]),
            quality=cast(Any, quality),
            provenance=cast(Any, provenance),
        )
        frozen_id = _stable_id(data["region_id"], f"{item_context}.region_id")
        if region.region_id != frozen_id:
            raise ValueError(f"{item_context}.region_id does not match identity")
        ids[alias] = frozen_id
        aliases_by_id[frozen_id] = alias
        regions.append(region)
    sorted_regions = sorted(regions, key=_region_sort_key)
    if regions != sorted_regions:
        raise ValueError(f"{context} must use stable source order")
    return tuple(regions), ids


def _load_file(
    data: dict[str, Any],
    sources: dict[str, _SourceFixture],
    base_revision: str,
    head_revision: str,
    context: str,
) -> _FileFixture:
    _exact_fields(data, _FILE_FIELDS, context)
    alias = _alias(data["alias"], f"{context}.alias")
    status = _text(data["status"], f"{context}.status")
    if status not in {"added", "modified", "deleted", "renamed"}:
        raise ValueError(f"{context}.status is unsupported")
    old_path = _nullable_path(data["old_path"], f"{context}.old_path")
    new_path = _nullable_path(data["new_path"], f"{context}.new_path")
    old_alias = _nullable_alias(data["old_source_alias"], f"{context}.old_source_alias")
    new_alias = _nullable_alias(data["new_source_alias"], f"{context}.new_source_alias")
    old_source = _resolve_source(old_alias, sources, f"{context}.old_source_alias")
    new_source = _resolve_source(new_alias, sources, f"{context}.new_source_alias")
    if old_source is not None and (
        old_source.snapshot.source_ref.revision != base_revision
        or old_source.snapshot.source_ref.path != old_path
    ):
        raise ValueError(f"{context} old source provenance drift")
    if new_source is not None and (
        new_source.snapshot.source_ref.revision != head_revision
        or new_source.snapshot.source_ref.path != new_path
    ):
        raise ValueError(f"{context} new source provenance drift")
    is_binary = _boolean(data["is_binary"], f"{context}.is_binary")
    atoms: list[ChangeAtomInput] = []
    atom_aliases: list[str] = []
    frozen_atom_ids: list[str] = []
    for index, value in enumerate(_array(data["atoms"], f"{context}.atoms")):
        atom_context = f"{context}.atoms[{index}]"
        atom_data = _object(value, atom_context)
        _exact_fields(atom_data, _ATOM_FIELDS, atom_context)
        atom_alias = _alias(atom_data["alias"], f"{atom_context}.alias")
        if atom_alias in atom_aliases:
            raise ValueError(f"duplicate ChangeAtom alias: {atom_alias}")
        kind = _text(atom_data["kind"], f"{atom_context}.kind")
        if kind not in {"addition", "deletion", "replacement"}:
            raise ValueError(f"{atom_context}.kind is unsupported")
        atom_aliases.append(atom_alias)
        frozen_atom_ids.append(_stable_id(atom_data["atom_id"], f"{atom_context}.atom_id"))
        atoms.append(
            ChangeAtomInput(
                kind=cast(Any, kind),
                old_span=_load_optional_span(atom_data["old_span"], f"{atom_context}.old_span"),
                new_span=_load_optional_span(atom_data["new_span"], f"{atom_context}.new_span"),
                added_new_lines=tuple(
                    _lines(atom_data["added_new_lines"], f"{atom_context}.added_new_lines")
                ),
                deleted_old_lines=tuple(
                    _lines(atom_data["deleted_old_lines"], f"{atom_context}.deleted_old_lines")
                ),
            )
        )
    _sorted_aliases(atom_aliases, f"{context}.atoms")
    return _FileFixture(
        alias=alias,
        input=ChangedFileInput(
            status=cast(Any, status),
            old_path=old_path,
            new_path=new_path,
            old_snapshot=None if old_source is None else old_source.snapshot,
            new_snapshot=None if new_source is None else new_source.snapshot,
            atoms=tuple(atoms),
            is_binary=is_binary,
        ),
        atom_aliases=tuple(atom_aliases),
        expected_changed_file_id=_stable_id(data["changed_file_id"], f"{context}.changed_file_id"),
        expected_atom_ids=tuple(frozen_atom_ids),
    )


def _validate_frozen_change_ids(change_set: Any, files: list[_FileFixture], context: str) -> None:
    actual_files = {item.changed_file_id: item for item in change_set.files}
    actual_atoms = {item.atom_id: item for item in change_set.atoms}
    for fixture in files:
        if fixture.expected_changed_file_id not in actual_files:
            raise ValueError(f"{context} changed_file_id does not match normalized input")
        changed_file = actual_files[fixture.expected_changed_file_id]
        if tuple(changed_file.atom_ids) != fixture.expected_atom_ids:
            raise ValueError(f"{context} atom_id does not match normalized input/order")
        if any(atom_id not in actual_atoms for atom_id in fixture.expected_atom_ids):
            raise ValueError(f"{context} contains a dangling atom_id")


def _validate_source_use(change_set: Any, sources: list[_SourceFixture], context: str) -> None:
    expected = {item.snapshot.source_ref.source_ref_id for item in sources}
    actual = {item.source_ref_id for item in change_set.source_refs}
    if expected != actual:
        raise ValueError(f"{context}.sources must exactly cover ChangeSet sources")


def _load_expected(
    data: dict[str, Any],
    change_set: Any,
    sources: list[_SourceFixture],
    files: list[_FileFixture],
    context: str,
) -> dict[str, Any]:
    _exact_fields(data, _EXPECTED_FIELDS, context)
    if data["schema_version"] != CHANGE_REVIEW_BUILD_SCHEMA_VERSION:
        raise ValueError(f"{context}.schema_version is unsupported")
    if _stable_id(data["change_set_id"], f"{context}.change_set_id") != change_set.change_set_id:
        raise ValueError(f"{context}.change_set_id does not match input")
    source_by_alias = {item.alias: item for item in sources}
    file_by_alias = {item.alias: item for item in files}
    atom_id_by_alias = {
        alias: atom_id
        for file in files
        for alias, atom_id in zip(file.atom_aliases, file.expected_atom_ids, strict=True)
    }
    expected_file_results: list[dict[str, Any]] = []
    for index, value in enumerate(_array(data["file_results"], f"{context}.file_results")):
        item_context = f"{context}.file_results[{index}]"
        item = _object(value, item_context)
        _exact_fields(item, _EXPECTED_FILE_FIELDS, item_context)
        file_alias = _alias(item["changed_file_alias"], f"{item_context}.changed_file_alias")
        source_alias = _alias(item["source_alias"], f"{item_context}.source_alias")
        if file_alias not in file_by_alias or source_alias not in source_by_alias:
            raise ValueError(f"{item_context} contains a dangling alias")
        fixture_file = file_by_alias[file_alias]
        fixture_source = source_by_alias[source_alias]
        if (
            _stable_id(item["changed_file_id"], f"{item_context}.changed_file_id")
            != fixture_file.expected_changed_file_id
        ):
            raise ValueError(f"{item_context}.changed_file_id alias mismatch")
        if (
            _stable_id(item["source_ref_id"], f"{item_context}.source_ref_id")
            != fixture_source.snapshot.source_ref.source_ref_id
        ):
            raise ValueError(f"{item_context}.source_ref_id alias mismatch")
        role = _role(item["source_role"], f"{item_context}.source_role")
        if fixture_source.snapshot.source_ref.revision != (
            change_set.base_revision if role == "base" else change_set.head_revision
        ):
            raise ValueError(f"{item_context}.source_role revision mismatch")
        if item["path"] != fixture_source.snapshot.source_ref.path:
            raise ValueError(f"{item_context}.path source mismatch")
        parser_quality = _expected_parser_quality(
            _object(item["parser_quality"], f"{item_context}.parser_quality"),
            f"{item_context}.parser_quality",
        )
        diagnostics = _diagnostics(item["diagnostics"], f"{item_context}.diagnostics")
        unassigned_aliases, unassigned_ids = _alias_id_lists(
            item["unassigned_change_atom_aliases"],
            item["unassigned_change_atom_ids"],
            atom_id_by_alias,
            f"{item_context}.unassigned_change_atom",
        )
        units: list[dict[str, Any]] = []
        for unit_index, unit_value in enumerate(_array(item["units"], f"{item_context}.units")):
            units.append(
                _load_expected_unit(
                    _object(unit_value, f"{item_context}.units[{unit_index}]"),
                    fixture_source,
                    role,
                    atom_id_by_alias,
                    f"{item_context}.units[{unit_index}]",
                )
            )
        expected_file_results.append(
            {
                "changed_file_alias": file_alias,
                "changed_file_id": fixture_file.expected_changed_file_id,
                "source_alias": source_alias,
                "source_ref_id": fixture_source.snapshot.source_ref.source_ref_id,
                "source_role": role,
                "path": item["path"],
                "parser_quality": parser_quality,
                "diagnostics": diagnostics,
                "unassigned_hunk_lines": _lines(
                    item["unassigned_hunk_lines"], f"{item_context}.unassigned_hunk_lines"
                ),
                "unassigned_change_atom_aliases": unassigned_aliases,
                "unassigned_change_atom_ids": unassigned_ids,
                "units": units,
            }
        )
    diagnostics = _diagnostics(data["diagnostics"], f"{context}.diagnostics")
    unassigned_aliases, unassigned_ids = _alias_id_lists(
        data["unassigned_change_atom_aliases"],
        data["unassigned_change_atom_ids"],
        atom_id_by_alias,
        f"{context}.unassigned_change_atom",
    )
    coverage: list[dict[str, Any]] = []
    for index, value in enumerate(_array(data["coverage"], f"{context}.coverage")):
        item_context = f"{context}.coverage[{index}]"
        item = _object(value, item_context)
        _exact_fields(item, _COVERAGE_FIELDS, item_context)
        alias = _alias(item["atom_alias"], f"{item_context}.atom_alias")
        if alias not in atom_id_by_alias:
            raise ValueError(f"{item_context}.atom_alias is dangling")
        atom_id = _stable_id(item["atom_id"], f"{item_context}.atom_id")
        if atom_id != atom_id_by_alias[alias]:
            raise ValueError(f"{item_context}.atom_id alias mismatch")
        coverage.append(
            {
                "atom_alias": alias,
                "atom_id": atom_id,
                "source_role": _role(item["source_role"], f"{item_context}.source_role"),
                "lines": _lines(item["lines"], f"{item_context}.lines"),
            }
        )
    projection = {
        "schema_version": data["schema_version"],
        "change_set_id": change_set.change_set_id,
        "file_results": expected_file_results,
        "diagnostics": diagnostics,
        "unassigned_change_atom_aliases": unassigned_aliases,
        "unassigned_change_atom_ids": unassigned_ids,
        "coverage": coverage,
    }
    expected_file_keys = {
        (file.changed_file_id, "base", file.old_source_ref_id)
        for file in change_set.files
        if file.old_source_ref_id is not None
    } | {
        (file.changed_file_id, "head", file.new_source_ref_id)
        for file in change_set.files
        if file.new_source_ref_id is not None
    }
    actual_file_keys = {
        (item["changed_file_id"], item["source_role"], item["source_ref_id"])
        for item in expected_file_results
    }
    if actual_file_keys != expected_file_keys:
        raise ValueError(f"{context}.file_results must exactly cover source-backed roles")
    file_unassigned = sorted(
        {
            atom_id
            for item in expected_file_results
            for atom_id in item["unassigned_change_atom_ids"]
        }
    )
    if file_unassigned != unassigned_ids:
        raise ValueError(f"{context} unassigned ChangeAtom aggregation mismatch")
    unassigned_sides = {
        (atom_id, item["source_role"])
        for item in expected_file_results
        for atom_id in item["unassigned_change_atom_ids"]
    }
    expected_coverage = {
        (atom.atom_id, role): (
            [] if (atom.atom_id, role) in unassigned_sides else list(lines)
        )
        for atom in change_set.atoms
        for role, lines in (
            ("base", atom.deleted_old_lines),
            ("head", atom.added_new_lines),
        )
        if lines
    }
    actual_coverage = {(item["atom_id"], item["source_role"]): item["lines"] for item in coverage}
    if len(actual_coverage) != len(coverage):
        raise ValueError(f"{context}.coverage contains duplicate atom/role rows")
    if actual_coverage != expected_coverage:
        raise ValueError(f"{context}.coverage must freeze every atom/role changed line")
    _validate_expected_assignment_graph(
        expected_file_results,
        change_set,
        context,
    )
    _require_stable_expected_order(projection, context)
    return projection


def _validate_expected_assignment_graph(
    file_results: list[dict[str, Any]],
    change_set: ChangeSet,
    context: str,
) -> None:
    changed_files = {item.changed_file_id: item for item in change_set.files}
    atoms = {item.atom_id: item for item in change_set.atoms}
    for result in file_results:
        changed_file = changed_files[result["changed_file_id"]]
        role = result["source_role"]
        source_ref_id = result["source_ref_id"]
        allowed_atom_ids = {
            atom_id
            for atom_id in changed_file.atom_ids
            if (
                atoms[atom_id].old_source_ref_id
                if role == "base"
                else atoms[atom_id].new_source_ref_id
            )
            == source_ref_id
        }
        unassigned = set(result["unassigned_change_atom_ids"])
        if not unassigned.issubset(allowed_atom_ids):
            raise ValueError(
                f"{context} file unassigned atom does not belong to its source role"
            )
        line_to_atom: dict[int, str] = {}
        expected_lines: dict[str, set[int]] = {}
        for atom_id in allowed_atom_ids:
            atom = atoms[atom_id]
            lines = set(
                atom.deleted_old_lines if role == "base" else atom.added_new_lines
            )
            expected_lines[atom_id] = lines
            for line in lines:
                if line in line_to_atom:
                    raise ValueError(
                        f"{context} changed line belongs to multiple atoms"
                    )
                line_to_atom[line] = atom_id
        assigned: dict[str, set[int]] = {
            atom_id: set() for atom_id in allowed_atom_ids
        }
        for unit in result["units"]:
            lines = (
                unit["changed_old_lines"]
                if role == "base"
                else unit["changed_new_lines"]
            )
            try:
                mapped_ids = sorted({line_to_atom[line] for line in lines})
            except KeyError as exc:
                raise ValueError(
                    f"{context} Unit changed line is outside its ChangeSet side"
                ) from exc
            if unit["change_atom_ids"] != mapped_ids:
                raise ValueError(
                    f"{context} Unit changed lines and ChangeAtom IDs disagree"
                )
            for line in lines:
                assigned[line_to_atom[line]].add(line)
        for atom_id, lines in expected_lines.items():
            if atom_id in unassigned:
                if assigned[atom_id]:
                    raise ValueError(
                        f"{context} unassigned atom retains partial Unit coverage"
                    )
            elif assigned[atom_id] != lines:
                raise ValueError(
                    f"{context} Unit coverage does not match its ChangeAtom side"
                )


def _load_expected_unit(
    data: dict[str, Any],
    source: _SourceFixture,
    source_role: SourceRole,
    atom_ids: dict[str, str],
    context: str,
) -> dict[str, Any]:
    _exact_fields(data, _EXPECTED_UNIT_FIELDS, context)
    source_span = _span_projection(data["source_span"], f"{context}.source_span")
    context_span = _span_projection(data["context_span"], f"{context}.context_span")
    line_count = len(source.source.splitlines())
    if source_span["end_line"] > line_count or context_span["end_line"] > line_count:
        raise ValueError(f"{context} span exceeds source")
    if not (
        context_span["start_line"] <= source_span["start_line"]
        and source_span["end_line"] <= context_span["end_line"]
    ):
        raise ValueError(f"{context}.source_span must be inside context_span")
    unit_kind = _text(data["unit_kind"], f"{context}.unit_kind")
    selection_reason = _text(data["selection_reason"], f"{context}.selection_reason")
    if unit_kind not in REVIEW_UNIT_V2_KINDS:
        raise ValueError(f"{context}.unit_kind is unsupported")
    if selection_reason not in REVIEW_UNIT_V2_SELECTION_REASONS:
        raise ValueError(f"{context}.selection_reason is unsupported")
    owner_data = data["owner"]
    owner: dict[str, str] | None
    owner_range: ExactRange | None = None
    if owner_data is None:
        owner = None
    else:
        owner_mapping = _object(owner_data, f"{context}.owner")
        _exact_fields(owner_mapping, _OWNER_FIELDS, f"{context}.owner")
        owner_alias = _alias(owner_mapping["alias"], f"{context}.owner.alias")
        owner_kind = _text(owner_mapping["kind"], f"{context}.owner.kind")
        owner_ids = (
            source.declaration_ids
            if owner_kind == "declaration"
            else source.region_ids
            if owner_kind == "region"
            else {}
        )
        if owner_alias not in owner_ids:
            raise ValueError(f"{context}.owner is dangling")
        owner_id = _stable_id(owner_mapping["ref_id"], f"{context}.owner.ref_id")
        if owner_id != owner_ids[owner_alias]:
            raise ValueError(f"{context}.owner.ref_id alias mismatch")
        owner = {"kind": owner_kind, "alias": owner_alias, "ref_id": owner_id}
        if owner_kind == "declaration":
            occurrence = next(
                item
                for item in source.parse_result.analysis.declarations
                if item.declaration_id == owner_id
            )
            owner_range = occurrence.exact_range
            if (
                unit_kind != occurrence.kind
                or data["unit_symbol"] != occurrence.qualified_name
                or source_span != _span(occurrence.span)
            ):
                raise ValueError(f"{context} declaration owner/identity mismatch")
        else:
            region = next(
                item
                for item in source.parse_result.analysis.review_regions
                if item.region_id == owner_id
            )
            owner_range = region.exact_range
            if (
                unit_kind != region.kind
                or data["unit_symbol"] != region.symbol
                or source_span != _span(region.span)
            ):
                raise ValueError(f"{context} region owner/identity mismatch")
    aliases, ids = _alias_id_lists(
        data["change_atom_aliases"],
        data["change_atom_ids"],
        atom_ids,
        f"{context}.change_atom",
    )
    full_text_hash = _sha(data["full_text_sha256"], f"{context}.full_text_sha256")
    expected_text_hash = hashlib.sha256(
        extract_lines(
            source.source,
            context_span["start_line"],
            context_span["end_line"],
        ).encode("utf-8")
    ).hexdigest()
    if full_text_hash != expected_text_hash:
        raise ValueError(f"{context}.full_text_sha256 does not match context_span")
    unit_id = _text(data["unit_id"], f"{context}.unit_id")
    if unit_kind == "fallback":
        if owner is not None or selection_reason != "fallback_window":
            raise ValueError(f"{context} fallback owner/reason mismatch")
        expected_unit_id = fallback_unit_id(
            source.snapshot.source_ref.path,
            source_span["start_line"],
            source_span["end_line"],
            context_span["start_line"],
            context_span["end_line"],
            source_role=source_role,
            source_ref_id=source.snapshot.source_ref.source_ref_id,
        )
    else:
        if owner is None or owner_range is None:
            raise ValueError(f"{context} formal Unit requires an owner")
        identity_start: int | None = None
        identity_end: int | None = None
        if owner["kind"] == "region":
            identity_start = owner_range.start_offset_utf16
            identity_end = owner_range.end_offset_utf16
        else:
            collisions = [
                item
                for item in source.parse_result.analysis.declarations
                if item.kind == unit_kind
                and item.qualified_name == data["unit_symbol"]
                and _span(item.span) == source_span
            ]
            if len(collisions) > 1:
                identity_start = owner_range.start_offset_utf16
                identity_end = owner_range.end_offset_utf16
        expected_unit_id = declaration_unit_id(
            source.snapshot.source_ref.path,
            cast(Any, unit_kind),
            cast(str, data["unit_symbol"]),
            source_span["start_line"],
            source_span["end_line"],
            start_offset_utf16=identity_start,
            end_offset_utf16=identity_end,
            source_role=source_role,
            source_ref_id=source.snapshot.source_ref.source_ref_id,
        )
    if unit_id != expected_unit_id:
        raise ValueError(f"{context}.unit_id does not match frozen identity fields")
    changed_new_lines = _lines(data["changed_new_lines"], f"{context}.changed_new_lines")
    changed_old_lines = _lines(data["changed_old_lines"], f"{context}.changed_old_lines")
    if (source_role == "base" and changed_new_lines) or (
        source_role == "head" and changed_old_lines
    ):
        raise ValueError(f"{context} changed line coordinates use the wrong source role")
    effective_lines = changed_old_lines if source_role == "base" else changed_new_lines
    if any(
        not context_span["start_line"] <= line <= context_span["end_line"]
        for line in effective_lines
    ):
        raise ValueError(f"{context} changed line lies outside context_span")
    return {
        "unit_id": unit_id,
        "unit_kind": unit_kind,
        "unit_symbol": _text(data["unit_symbol"], f"{context}.unit_symbol"),
        "source_span": source_span,
        "context_span": context_span,
        "changed_new_lines": changed_new_lines,
        "changed_old_lines": changed_old_lines,
        "selection_reason": selection_reason,
        "context_degraded": _boolean(data["context_degraded"], f"{context}.context_degraded"),
        "diagnostics": _diagnostics(data["diagnostics"], f"{context}.diagnostics"),
        "owner": owner,
        "change_atom_aliases": aliases,
        "change_atom_ids": ids,
        "full_text_sha256": full_text_hash,
    }


def evaluate_golden_suite(
    suite: ReviewUnitV2GoldenSuite,
    builder: ReviewUnitBuilder | None = None,
) -> dict[str, Any]:
    current_hash = hashlib.sha256(_read_regular_file(suite.manifest_path, "manifest")).hexdigest()
    if current_hash != suite.manifest_sha256:
        raise ValueError("ReviewUnit v2 Golden manifest changed after loading")
    builder = builder or ReviewUnitBuilder()
    cases = [_evaluate_case(case, builder) for case in suite.cases]
    matched = sum(bool(item["matched"]) for item in cases)
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "implementation": type(builder).__name__,
        "manifest_sha256": suite.manifest_sha256,
        "case_count": len(cases),
        "matched_case_count": matched,
        "mismatched_case_count": len(cases) - matched,
        "cases": cases,
    }


def is_perfect(report: dict[str, Any]) -> bool:
    cases = report.get("cases")
    expected_case_ids = [f"RV2{index:02d}" for index in range(1, 17)]
    return (
        report.get("schema_version") == SCHEMA_VERSION
        and isinstance(cases, list)
        and len(cases) == 16
        and report.get("case_count") == len(cases)
        and report.get("matched_case_count") == len(cases)
        and report.get("mismatched_case_count") == 0
        and all(isinstance(item, dict) for item in cases)
        and [item.get("case_id") for item in cases] == expected_case_ids
        and all(item.get("matched") is True for item in cases)
    )


def write_current_baseline(
    report: dict[str, Any],
    suite: ReviewUnitV2GoldenSuite,
    baseline_path: str | Path,
) -> None:
    unresolved_path = Path(baseline_path)
    if unresolved_path.is_symlink():
        raise ValueError("ReviewUnit v2 baseline must not be a symlink")
    path = unresolved_path.resolve()
    allowed = (suite.manifest_path.parent / "baselines" / "current.json").resolve()
    if path != allowed:
        raise ValueError("ReviewUnit v2 baseline writer may only update baselines/current.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "implementation": report.get("implementation"),
        "manifest_sha256": suite.manifest_sha256,
        "report": report,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def assert_strict_baseline(
    report: dict[str, Any],
    suite: ReviewUnitV2GoldenSuite,
    baseline_path: str | Path,
) -> None:
    unresolved_path = Path(baseline_path)
    if unresolved_path.is_symlink():
        raise ValueError("ReviewUnit v2 baseline must not be a symlink")
    path = unresolved_path.resolve()
    raw = _read_regular_file(path, "baseline")
    baseline = _json_object(raw, str(path))
    fields = {
        "schema_version",
        "suite_id",
        "implementation",
        "manifest_sha256",
        "report",
    }
    _exact_fields(baseline, fields, "baseline")
    if baseline["schema_version"] != BASELINE_SCHEMA_VERSION:
        raise ValueError("baseline.schema_version is unsupported")
    if baseline["suite_id"] != suite.suite_id:
        raise ValueError("baseline.suite_id drift")
    if baseline["manifest_sha256"] != suite.manifest_sha256:
        raise ValueError("baseline manifest hash drift")
    if baseline["implementation"] != report.get("implementation"):
        raise ValueError("baseline implementation drift")
    if not reports_equal(baseline["report"], report):
        differences = _differences(baseline["report"], report)
        preview = "; ".join(differences[:3])
        raise ValueError(f"strict ReviewUnit v2 baseline mismatch: {preview}")


def _evaluate_case(case: ReviewUnitV2GoldenCase, builder: ReviewUnitBuilder) -> dict[str, Any]:
    actual: dict[str, Any] = {}
    differences: list[str] = []
    invariants: list[str] = []
    error: str | None = None
    repeat_equal = False
    permutation_equal = False
    try:
        change_set, result = _run_case(case, builder, permuted=False)
        actual = _project_result(case, change_set, result)
        change_set_repeat, result_repeat = _run_case(case, builder, permuted=False)
        repeat_equal = reports_equal(
            actual,
            _project_result(case, change_set_repeat, result_repeat),
        )
        change_set_permuted, result_permuted = _run_case(case, builder, permuted=True)
        permutation_equal = reports_equal(
            actual,
            _project_result(case, change_set_permuted, result_permuted),
        )
        if not repeat_equal:
            invariants.append("repeat execution changed output")
        if not permutation_equal:
            invariants.append("input permutation changed output")
        invariants.extend(_result_invariants(case, change_set, result, actual))
        differences = _differences(case.expected, actual)
    except Exception as exc:  # pragma: no cover - preserved as evaluator evidence.
        error = repr(exc)
        invariants.append(f"builder execution failed: {error}")
    invariants = sorted(set(invariants))
    return {
        "case_id": case.case_id,
        "description": case.description,
        "expected": case.expected,
        "actual": actual,
        "differences": differences,
        "invariant_violations": invariants,
        "repeat_equal": repeat_equal,
        "permutation_equal": permutation_equal,
        "error": error,
        "matched": not differences and not invariants and error is None,
    }


def _run_case(
    case: ReviewUnitV2GoldenCase,
    builder: ReviewUnitBuilder,
    *,
    permuted: bool,
) -> tuple[Any, ReviewUnitBuildResult]:
    fixtures = list(reversed(case.files)) if permuted else list(case.files)
    inputs = tuple(
        ChangedFileInput(
            status=item.input.status,
            old_path=item.input.old_path,
            new_path=item.input.new_path,
            old_snapshot=item.input.old_snapshot,
            new_snapshot=item.input.new_snapshot,
            atoms=tuple(reversed(item.input.atoms)) if permuted else item.input.atoms,
            is_binary=item.input.is_binary,
        )
        for item in fixtures
    )
    change_set = normalize_change_set(
        repository=case.repository,
        base_revision=case.base_revision,
        head_revision=case.head_revision,
        diff_normalizer_version=case.diff_normalizer_version,
        files=inputs,
    )
    source_order = list(reversed(case.sources)) if permuted else list(case.sources)
    snapshots = {
        source.snapshot.source_ref.source_ref_id: source.snapshot for source in source_order
    }
    parse_results = {
        source.snapshot.source_ref.source_ref_id: source.parse_result for source in source_order
    }
    result = build_change_review_units(
        change_set=change_set,
        source_snapshots=snapshots,
        file_parse_results=parse_results,
        review_unit_builder=builder,
    )
    return change_set, result


def _project_result(case: ReviewUnitV2GoldenCase, change_set: Any, result: Any) -> dict[str, Any]:
    if not isinstance(result, ReviewUnitBuildResult):
        raise ValueError("builder must return ReviewUnitBuildResult")
    result.validate()
    source_alias_by_id = {
        source.snapshot.source_ref.source_ref_id: source.alias for source in case.sources
    }
    file_alias_by_id = {fixture.expected_changed_file_id: fixture.alias for fixture in case.files}
    atom_alias_by_id = {
        atom_id: alias
        for fixture in case.files
        for alias, atom_id in zip(fixture.atom_aliases, fixture.expected_atom_ids, strict=True)
    }
    owner_alias_by_id = {
        owner_id: alias
        for source in case.sources
        for alias, owner_id in {**source.declaration_ids, **source.region_ids}.items()
    }
    file_results: list[dict[str, Any]] = []
    for file_result in result.file_results:
        if (
            file_result.source_ref_id is None
            or file_result.changed_file_id is None
            or file_result.source_role is None
        ):
            raise ValueError("review-unit-build-v3 file result lost source identity")
        file_results.append(
            {
                "changed_file_alias": file_alias_by_id[file_result.changed_file_id],
                "changed_file_id": file_result.changed_file_id,
                "source_alias": source_alias_by_id[file_result.source_ref_id],
                "source_ref_id": file_result.source_ref_id,
                "source_role": file_result.source_role,
                "path": file_result.path,
                "parser_quality": {
                    "layer": file_result.parser_quality.parser_layer,
                    "warnings": list(file_result.parser_quality.warnings),
                },
                "diagnostics": _project_diagnostics(file_result.diagnostics),
                "unassigned_hunk_lines": list(file_result.unassigned_hunk_lines),
                "unassigned_change_atom_aliases": [
                    atom_alias_by_id[item] for item in file_result.unassigned_change_atom_ids
                ],
                "unassigned_change_atom_ids": list(file_result.unassigned_change_atom_ids),
                "units": [
                    _project_unit(unit, atom_alias_by_id, owner_alias_by_id)
                    for unit in file_result.units
                ],
            }
        )
    coverage: list[dict[str, Any]] = []
    for atom in change_set.atoms:
        for role, target_lines in (
            ("base", atom.deleted_old_lines),
            ("head", atom.added_new_lines),
        ):
            if not target_lines:
                continue
            covered = sorted(
                {
                    line
                    for file_result in result.file_results
                    if file_result.source_role == role
                    for unit in file_result.units
                    if atom.atom_id in unit.change_atom_ids
                    for line in (
                        unit.changed_old_lines if role == "base" else unit.changed_new_lines
                    )
                    if line in target_lines
                }
            )
            coverage.append(
                {
                    "atom_alias": atom_alias_by_id[atom.atom_id],
                    "atom_id": atom.atom_id,
                    "source_role": role,
                    "lines": covered,
                }
            )
    coverage.sort(key=lambda item: (item["atom_id"], 0 if item["source_role"] == "base" else 1))
    return {
        "schema_version": result.schema_version,
        "change_set_id": result.change_set_id,
        "file_results": file_results,
        "diagnostics": _project_diagnostics(result.diagnostics),
        "unassigned_change_atom_aliases": [
            atom_alias_by_id[item] for item in result.unassigned_change_atom_ids
        ],
        "unassigned_change_atom_ids": list(result.unassigned_change_atom_ids),
        "coverage": coverage,
    }


def _project_unit(
    unit: ReviewUnit,
    atom_alias_by_id: dict[str, str],
    owner_alias_by_id: dict[str, str],
) -> dict[str, Any]:
    unit.validate()
    owner = None
    if unit.owner_ref is not None:
        owner = {
            "kind": unit.owner_ref.kind,
            "alias": owner_alias_by_id[unit.owner_ref.ref_id],
            "ref_id": unit.owner_ref.ref_id,
        }
    return {
        "unit_id": unit.unit_id,
        "unit_kind": unit.unit_kind,
        "unit_symbol": unit.unit_symbol,
        "source_span": _span(unit.source_span),
        "context_span": _span(unit.context_span),
        "changed_new_lines": list(unit.changed_new_lines),
        "changed_old_lines": list(unit.changed_old_lines),
        "selection_reason": unit.selection_reason,
        "context_degraded": unit.context_degraded,
        "diagnostics": _project_diagnostics(unit.diagnostics),
        "owner": owner,
        "change_atom_aliases": [atom_alias_by_id[item] for item in unit.change_atom_ids],
        "change_atom_ids": list(unit.change_atom_ids),
        "full_text_sha256": hashlib.sha256(unit.full_text.encode("utf-8")).hexdigest(),
    }


def _result_invariants(
    case: ReviewUnitV2GoldenCase,
    change_set: Any,
    result: ReviewUnitBuildResult,
    actual: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    if result.schema_version != CHANGE_REVIEW_BUILD_SCHEMA_VERSION:
        violations.append("build schema is not review-unit-build-v3")
    if result.change_set_id != change_set.change_set_id:
        violations.append("build lost change_set_id")
    sources = {item.snapshot.source_ref.source_ref_id: item for item in case.sources}
    for file_result in result.file_results:
        if file_result.source_ref_id not in sources:
            violations.append("file result has unknown source_ref_id")
            continue
        source = sources[file_result.source_ref_id].source
        for unit in file_result.units:
            expected_text = extract_lines(
                source,
                unit.context_span.start_line,
                unit.context_span.end_line,
            )
            if unit.full_text != expected_text:
                violations.append(f"{unit.unit_id} full_text is not the exact context slice")
            effective = (
                unit.changed_old_lines if unit.source_role == "base" else unit.changed_new_lines
            )
            if any(not unit.context_span.contains_line(line) for line in effective):
                violations.append(f"{unit.unit_id} carries a changed line outside context")
    target = sorted(
        (
            atom.atom_id,
            role,
            list(lines),
        )
        for atom in change_set.atoms
        for role, lines in (("base", atom.deleted_old_lines), ("head", atom.added_new_lines))
        if lines
    )
    covered = sorted(
        (item["atom_id"], item["source_role"], item["lines"]) for item in actual["coverage"]
    )
    unassigned_sides = {
        (atom_id, file_result.source_role)
        for file_result in result.file_results
        for atom_id in file_result.unassigned_change_atom_ids
    }
    expected_covered = [
        (
            atom_id,
            role,
            [] if (atom_id, role) in unassigned_sides else lines,
        )
        for atom_id, role, lines in target
    ]
    if covered != expected_covered:
        violations.append("(atom_id, source_role) changed-line coverage is incomplete")
    return violations


def _require_stable_expected_order(projection: dict[str, Any], context: str) -> None:
    files = projection["file_results"]
    file_keys = [
        (
            item["changed_file_id"],
            0 if item["source_role"] == "base" else 1,
            item["path"],
            item["source_ref_id"],
        )
        for item in files
    ]
    if file_keys != sorted(file_keys) or len(file_keys) != len(set(file_keys)):
        raise ValueError(f"{context}.file_results must use unique stable output order")
    for index, item in enumerate(files):
        units = item["units"]
        unit_keys = [
            (
                unit["context_span"]["start_line"],
                unit["context_span"]["end_line"],
                unit["source_span"]["start_line"],
                unit["source_span"]["end_line"],
                unit["unit_id"],
            )
            for unit in units
        ]
        if unit_keys != sorted(unit_keys) or len(unit_keys) != len(set(unit_keys)):
            raise ValueError(f"{context}.file_results[{index}].units order is unstable")
    coverage_keys = [
        (item["atom_id"], 0 if item["source_role"] == "base" else 1)
        for item in projection["coverage"]
    ]
    if coverage_keys != sorted(coverage_keys) or len(coverage_keys) != len(set(coverage_keys)):
        raise ValueError(f"{context}.coverage must use unique atom/role order")


def _expected_parser_quality(data: dict[str, Any], context: str) -> dict[str, Any]:
    if set(data) != {"layer", "warnings"}:
        raise ValueError(f"{context} has unknown or missing fields")
    layer = _text(data["layer"], f"{context}.layer")
    if layer not in _LAYERS:
        raise ValueError(f"{context}.layer is unsupported")
    warnings = [
        _text(item, f"{context}.warnings")
        for item in _array(data["warnings"], f"{context}.warnings")
    ]
    if warnings != sorted(set(warnings)):
        raise ValueError(f"{context}.warnings must be sorted and unique")
    return {"layer": layer, "warnings": warnings}


def _alias_id_lists(
    alias_value: Any,
    id_value: Any,
    known_ids: dict[str, str],
    context: str,
) -> tuple[list[str], list[str]]:
    aliases = [
        _alias(item, f"{context}_aliases") for item in _array(alias_value, f"{context}_aliases")
    ]
    ids = [_stable_id(item, f"{context}_ids") for item in _array(id_value, f"{context}_ids")]
    if aliases != sorted(set(aliases)) or ids != sorted(set(ids)):
        raise ValueError(f"{context} aliases/IDs must be sorted and unique")
    if len(aliases) != len(ids) or [known_ids.get(alias) for alias in aliases] != ids:
        raise ValueError(f"{context} alias/ID mapping mismatch")
    return aliases, ids


def _diagnostics(value: Any, context: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        mapping = _object(item, item_context)
        _exact_fields(mapping, _DIAGNOSTIC_FIELDS, item_context)
        result.append(
            {
                "code": _text(mapping["code"], f"{item_context}.code"),
                "lines": _lines(mapping["lines"], f"{item_context}.lines"),
            }
        )
        if result[-1]["code"] not in REVIEW_UNIT_V2_DIAGNOSTIC_CODES:
            raise ValueError(f"{item_context}.code is not in the frozen RU-4 contract")
    keys = [(item["code"], item["lines"]) for item in result]
    if keys != sorted(keys) or len(keys) != len(set((code, tuple(lines)) for code, lines in keys)):
        raise ValueError(f"{context} must use unique stable diagnostic order")
    return result


def _project_diagnostics(values: list[ReviewUnitDiagnostic]) -> list[dict[str, Any]]:
    return [{"code": item.code, "lines": list(item.lines)} for item in values]


def _load_exact_range(
    data: dict[str, Any],
    source: str,
    context: str,
) -> tuple[SourceSpan, ExactRange]:
    span = _load_span(data["span"], f"{context}.span")
    if span.end_line > len(source.splitlines()):
        raise ValueError(f"{context}.span exceeds source")
    start = _count(data["start_offset_utf16"], f"{context}.start_offset_utf16")
    end = _count(data["end_offset_utf16"], f"{context}.end_offset_utf16")
    if end <= start:
        raise ValueError(f"{context} UTF-16 range must be non-empty")
    boundaries = _utf16_boundaries(source)
    if start not in boundaries or end not in boundaries:
        raise ValueError(f"{context} UTF-16 offset is not a source boundary")
    start_index = boundaries[start]
    end_index = boundaries[end]
    if source[:start_index].count("\n") + 1 != span.start_line:
        raise ValueError(f"{context} start line/UTF-16 coordinates disagree")
    mapped_end_line = source[:end_index].count("\n") + 1
    ends_after_declared_newline = (
        end_index > 0
        and source[end_index - 1] == "\n"
        and mapped_end_line == span.end_line + 1
    )
    if mapped_end_line != span.end_line and not ends_after_declared_newline:
        raise ValueError(f"{context} end line/UTF-16 coordinates disagree")
    return SourceSpan(span.start_line, span.end_line), ExactRange(
        span.start_line,
        span.end_line,
        start,
        end,
    )


def _utf16_boundaries(source: str) -> dict[int, int]:
    boundaries = {0: 0}
    offset = 0
    for index, character in enumerate(source, start=1):
        offset += 2 if ord(character) > 0xFFFF else 1
        boundaries[offset] = index
    return boundaries


def _declaration_sort_key(item: DeclarationOccurrence) -> tuple[Any, ...]:
    return (
        item.span.start_line,
        item.exact_range.start_offset_utf16,
        item.span.end_line,
        item.exact_range.end_offset_utf16,
        item.kind,
        item.qualified_name,
        item.declaration_id,
    )


def _region_sort_key(item: ReviewRegion) -> tuple[Any, ...]:
    return (
        item.span.start_line,
        item.exact_range.start_offset_utf16,
        item.span.end_line,
        item.exact_range.end_offset_utf16,
        item.kind,
        item.symbol,
        item.region_id,
    )


def _span(value: Any) -> dict[str, int]:
    return {"start_line": value.start_line, "end_line": value.end_line}


def _span_projection(value: Any, context: str) -> dict[str, int]:
    span = _load_span(value, context)
    return _span(span)


def _load_optional_span(value: Any, context: str) -> ReviewUnitSpan | None:
    return None if value is None else _load_span(value, context)


def _load_span(value: Any, context: str) -> ReviewUnitSpan:
    data = _object(value, context)
    _exact_fields(data, _SPAN_FIELDS, context)
    return ReviewUnitSpan(
        _positive(data["start_line"], f"{context}.start_line"),
        _positive(data["end_line"], f"{context}.end_line"),
    )


def _lines(value: Any, context: str) -> list[int]:
    result = [_positive(item, context) for item in _array(value, context)]
    if result != sorted(set(result)):
        raise ValueError(f"{context} must be sorted and unique")
    return result


def _resolve_source(
    alias: str | None,
    sources: dict[str, _SourceFixture],
    context: str,
) -> _SourceFixture | None:
    if alias is None:
        return None
    if alias not in sources:
        raise ValueError(f"{context} is dangling")
    return sources[alias]


def _safe_child(root: Path, value: str, context: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"{context} must be a safe relative path")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{context} must not traverse a symlink")
    resolved_root = root.resolve()
    resolved = current.resolve(strict=True)
    if resolved_root not in resolved.parents:
        raise ValueError(f"{context} escapes the Golden directory")
    return resolved


def _read_regular_file(path: Path, context: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{context} must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _json_object(raw: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{context} is not valid UTF-8 JSON") from exc
    return _object(value, context)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _exact_fields(data: dict[str, Any], fields: set[str], context: str) -> None:
    missing = sorted(fields - set(data))
    unknown = sorted(set(data) - fields)
    if missing or unknown:
        raise ValueError(f"{context} fields invalid: missing={missing}, unknown={unknown}")


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return cast(dict[str, Any], value)


def _array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ValueError(f"{context} must be non-empty text")
    return value


def _alias(value: Any, context: str) -> str:
    result = _text(value, context)
    if not _ALIAS_RE.fullmatch(result):
        raise ValueError(f"{context} must be a lowercase semantic alias")
    return result


def _nullable_alias(value: Any, context: str) -> str | None:
    return None if value is None else _alias(value, context)


def _logical_path(value: Any, context: str) -> str:
    result = _text(value, context)
    if result.startswith("/") or ".." in Path(result).parts or "\\" in result:
        raise ValueError(f"{context} must be a normalized repository path")
    return result


def _nullable_path(value: Any, context: str) -> str | None:
    return None if value is None else _logical_path(value, context)


def _stable_id(value: Any, context: str) -> str:
    result = _text(value, context)
    if not _STABLE_ID_RE.fullmatch(result):
        raise ValueError(f"{context} must be a frozen stable ID")
    return result


def _sha(value: Any, context: str) -> str:
    result = _text(value, context)
    if not _SHA_RE.fullmatch(result):
        raise ValueError(f"{context} must be 64 lowercase hex characters")
    return result


def _role(value: Any, context: str) -> SourceRole:
    result = _text(value, context)
    if result not in {"base", "head"}:
        raise ValueError(f"{context} must be base or head")
    return cast(SourceRole, result)


def _positive(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{context} must be an integer >= 1")
    return value


def _count(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{context} must be an integer >= 0")
    return value


def _nullable_count(value: Any, context: str) -> int | None:
    return None if value is None else _count(value, context)


def _boolean(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")
    return value


def _sorted_aliases(values: list[str], context: str) -> None:
    if values != sorted(set(values)):
        raise ValueError(f"{context} aliases must be sorted and unique")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def reports_equal(first: Any, second: Any) -> bool:
    return _canonical(first) == _canonical(second)


def _differences(expected: Any, actual: Any, path: str = "$") -> list[str]:
    if type(expected) is not type(actual):
        return [f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"]
    if isinstance(expected, dict):
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}"
            if key not in expected:
                differences.append(f"{child}: unexpected")
            elif key not in actual:
                differences.append(f"{child}: missing")
            else:
                differences.extend(_differences(expected[key], actual[key], child))
        return differences
    if isinstance(expected, list):
        differences = []
        if len(expected) != len(actual):
            differences.append(f"{path}: expected {len(expected)} items, got {len(actual)}")
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            differences.extend(_differences(left, right, f"{path}[{index}]"))
        return differences
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]
