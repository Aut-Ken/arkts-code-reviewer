from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from arkts_code_reviewer.code_analysis.change_set import (
    CHANGE_SET_SCHEMA_VERSION,
    DIFF_NORMALIZER_VERSION,
    ChangeAtom,
    ChangeAtomInput,
    ChangedFile,
    ChangedFileInput,
    ChangeSet,
    ChangeSetDiagnostic,
    CodeSourceSnapshot,
    DiffPosition,
    normalize_change_set,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    ExactRange,
)
from arkts_code_reviewer.code_analysis.models import ReviewUnitSpan
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    normalize_review_path,
)

SCHEMA_VERSION = "change-set-golden-v1"
BASELINE_SCHEMA_VERSION = "change-set-golden-baseline-v1"

MANIFEST_FIELDS = (
    "schema_version",
    "suite_id",
    "description",
    "coordinate_system",
    "frozen_contract",
    "cases",
)
COORDINATE_FIELDS = (
    "line_base",
    "line_end",
    "offset_encoding",
    "offset_base",
    "offset_end",
)
CONTRACT_FIELDS = (
    "change_set_schema_version",
    "diff_normalizer_version",
    "atom_kinds",
    "file_statuses",
    "diagnostic_codes",
)
CASE_FIELDS = (
    "case_id",
    "description",
    "repository",
    "base_revision",
    "head_revision",
    "diff_normalizer_version",
    "sources",
    "files",
    "expected",
)
SOURCE_FIELDS = (
    "alias",
    "file",
    "role",
    "relative_path",
    "origin_lines",
    "content_sha256",
)
FILE_INPUT_FIELDS = (
    "status",
    "old_path",
    "new_path",
    "old_source_alias",
    "new_source_alias",
    "is_binary",
    "atoms",
)
ATOM_INPUT_FIELDS = (
    "kind",
    "old_span",
    "new_span",
    "added_new_lines",
    "deleted_old_lines",
    "diff_positions",
)
LINE_SPAN_FIELDS = ("start_line", "end_line")
DIFF_POSITION_FIELDS = ("side", "source_line", "diff_position")

CHANGE_SET_FIELDS = (
    "schema_version",
    "change_set_id",
    "repository",
    "base_revision",
    "head_revision",
    "diff_normalizer_version",
    "source_refs",
    "files",
    "atoms",
    "diagnostics",
)
SOURCE_REF_FIELDS = (
    "source_ref_id",
    "repository",
    "revision",
    "path",
    "content_hash",
)
CHANGED_FILE_FIELDS = (
    "changed_file_id",
    "status",
    "old_path",
    "new_path",
    "old_source_ref_id",
    "new_source_ref_id",
    "atom_ids",
    "is_binary",
)
CHANGE_ATOM_FIELDS = (
    "atom_id",
    "kind",
    "old_source_ref_id",
    "new_source_ref_id",
    "old_span",
    "new_span",
    "added_new_lines",
    "deleted_old_lines",
    "diff_positions",
    "diff_normalizer_version",
)
EXACT_RANGE_FIELDS = (
    "start_line",
    "end_line",
    "start_offset_utf16",
    "end_offset_utf16",
)
DIAGNOSTIC_FIELDS = ("diagnostic_id", "code", "changed_file_id")
BASELINE_FIELDS = ("schema_version", "suite_id", "manifest_sha256", "report")


@dataclass(frozen=True)
class _SourceSpec:
    alias: str
    role: str
    file_path: Path
    relative_path: str
    origin_lines: tuple[int, int] | None
    content_sha256: str
    snapshot: CodeSourceSnapshot


@dataclass(frozen=True)
class ChangeSetGoldenCase:
    case_id: str
    description: str
    repository: str
    base_revision: str
    head_revision: str
    diff_normalizer_version: str
    source_specs: tuple[_SourceSpec, ...]
    files: tuple[ChangedFileInput, ...]
    expected: dict[str, Any]

    def normalize(self, files: tuple[ChangedFileInput, ...] | None = None) -> ChangeSet:
        return normalize_change_set(
            repository=self.repository,
            base_revision=self.base_revision,
            head_revision=self.head_revision,
            diff_normalizer_version=self.diff_normalizer_version,
            files=self.files if files is None else files,
        )


@dataclass(frozen=True)
class ChangeSetGoldenSuite:
    suite_id: str
    manifest_path: Path
    manifest_sha256: str
    cases: tuple[ChangeSetGoldenCase, ...]


def load_golden_suite(manifest_path: str | Path) -> ChangeSetGoldenSuite:
    path = Path(manifest_path)
    raw_bytes = _read_regular_file(path, "manifest")
    data = _mapping(_load_json_bytes(raw_bytes, str(path)), "manifest")
    _require_fields(data, MANIFEST_FIELDS, "manifest")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"manifest.schema_version must be {SCHEMA_VERSION!r}")
    suite_id = _text(data["suite_id"], "manifest.suite_id")
    _text(data["description"], "manifest.description")
    _validate_coordinate_system(data["coordinate_system"])
    _validate_frozen_contract(data["frozen_contract"])

    raw_cases = _list(data["cases"], "manifest.cases")
    if not 12 <= len(raw_cases) <= 16:
        raise ValueError("manifest.cases must contain between 12 and 16 cases")
    root = path.resolve().parent
    cases: list[ChangeSetGoldenCase] = []
    seen_case_ids: set[str] = set()
    seen_semantics: set[str] = set()
    for index, value in enumerate(raw_cases):
        case = _load_case(_mapping(value, f"manifest.cases[{index}]"), root, index)
        if case.case_id in seen_case_ids:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        semantic = _canonical(
            {
                "repository": case.repository,
                "base_revision": case.base_revision,
                "head_revision": case.head_revision,
                "diff_normalizer_version": case.diff_normalizer_version,
                "sources": [
                    {
                        "role": item.role,
                        "path": item.relative_path,
                        "hash": item.content_sha256,
                    }
                    for item in case.source_specs
                ],
                "files": [_file_input_projection(item) for item in case.files],
            }
        )
        if semantic in seen_semantics:
            raise ValueError(f"duplicate semantic case: {case.case_id}")
        seen_case_ids.add(case.case_id)
        seen_semantics.add(semantic)
        cases.append(case)
    if [case.case_id for case in cases] != sorted(case.case_id for case in cases):
        raise ValueError("manifest.cases must be sorted by case_id")
    return ChangeSetGoldenSuite(
        suite_id=suite_id,
        manifest_path=path.resolve(),
        manifest_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        cases=tuple(cases),
    )


def evaluate_golden_suite(suite: ChangeSetGoldenSuite) -> dict[str, Any]:
    case_reports = [_evaluate_case(case) for case in suite.cases]
    matched = sum(bool(item["matched"]) for item in case_reports)
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "implementation": "normalize_change_set",
        "manifest_sha256": suite.manifest_sha256,
        "case_count": len(case_reports),
        "matched_case_count": matched,
        "mismatched_case_count": len(case_reports) - matched,
        "cases": case_reports,
    }


def is_perfect(report: dict[str, Any]) -> bool:
    try:
        cases = report["cases"]
        return (
            report["schema_version"] == SCHEMA_VERSION
            and isinstance(cases, list)
            and report["case_count"] == len(cases)
            and report["matched_case_count"] == len(cases)
            and report["mismatched_case_count"] == 0
            and 12 <= len(cases) <= 16
            and all(case.get("matched") is True for case in cases)
        )
    except (KeyError, TypeError):
        return False


def write_current_baseline(
    report: dict[str, Any],
    suite: ChangeSetGoldenSuite,
    baseline_path: str | Path,
) -> None:
    path = Path(baseline_path)
    allowed = suite.manifest_path.parent / "baselines" / "current.json"
    if path.resolve() != allowed.resolve():
        raise ValueError("ChangeSet baseline writer may only update baselines/current.json")
    if path.exists() and path.is_symlink():
        raise ValueError("ChangeSet baseline must not be a symlink")
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "manifest_sha256": suite.manifest_sha256,
        "report": report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def assert_strict_baseline(
    report: dict[str, Any],
    suite: ChangeSetGoldenSuite,
    baseline_path: str | Path,
) -> None:
    path = Path(baseline_path)
    data = _mapping(
        _load_json_bytes(_read_regular_file(path, "baseline"), str(path)),
        "baseline",
    )
    _require_fields(data, BASELINE_FIELDS, "baseline")
    if data["schema_version"] != BASELINE_SCHEMA_VERSION:
        raise ValueError("unsupported ChangeSet baseline schema")
    if data["suite_id"] != suite.suite_id:
        raise ValueError("baseline suite_id drift")
    if data["manifest_sha256"] != suite.manifest_sha256:
        raise ValueError("baseline manifest hash drift")
    if data["report"] != report:
        preview = "; ".join(_differences(data["report"], report)[:5])
        raise ValueError(f"ChangeSet strict baseline mismatch: {preview}")


def _load_case(data: dict[str, Any], root: Path, index: int) -> ChangeSetGoldenCase:
    context = f"manifest.cases[{index}]"
    _require_fields(data, CASE_FIELDS, context)
    case_id = _text(data["case_id"], f"{context}.case_id")
    description = _text(data["description"], f"{context}.description")
    repository = _text(data["repository"], f"{context}.repository")
    base_revision = _text(data["base_revision"], f"{context}.base_revision")
    head_revision = _text(data["head_revision"], f"{context}.head_revision")
    normalizer = _text(
        data["diff_normalizer_version"],
        f"{context}.diff_normalizer_version",
    )

    source_specs: list[_SourceSpec] = []
    aliases: set[str] = set()
    source_semantics: set[tuple[str, str]] = set()
    for source_index, raw_source in enumerate(_list(data["sources"], f"{context}.sources")):
        source_context = f"{context}.sources[{source_index}]"
        source = _mapping(raw_source, source_context)
        _require_fields(source, SOURCE_FIELDS, source_context)
        alias = _text(source["alias"], f"{source_context}.alias")
        if alias in aliases:
            raise ValueError(f"duplicate source alias: {alias}")
        aliases.add(alias)
        role = _text(source["role"], f"{source_context}.role")
        if role not in {"base", "head"}:
            raise ValueError(f"{source_context}.role must be base or head")
        relative_path = _normalized_path(source["relative_path"], f"{source_context}.relative_path")
        role_path = (role, relative_path)
        if role_path in source_semantics:
            raise ValueError(f"duplicate source provenance: {role_path!r}")
        source_semantics.add(role_path)
        file_path = _resolve_snapshot(root, source["file"], source_context)
        content = _read_regular_file(file_path, source_context).decode("utf-8")
        content_sha256 = _sha256(source["content_sha256"], source_context)
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != content_sha256:
            raise ValueError(f"{source_context} source hash drift")
        line_count = len(content.splitlines(keepends=True))
        origin_lines = _origin_lines(
            source["origin_lines"], line_count, f"{source_context}.origin_lines"
        )
        revision = base_revision if role == "base" else head_revision
        source_ref = CodeSourceRef.create(
            repository=repository,
            revision=revision,
            path=relative_path,
            content_hash=f"sha256:{content_sha256}",
        )
        source_specs.append(
            _SourceSpec(
                alias=alias,
                role=role,
                file_path=file_path,
                relative_path=relative_path,
                origin_lines=origin_lines,
                content_sha256=content_sha256,
                snapshot=CodeSourceSnapshot(source_ref, content),
            )
        )
    source_order = [(item.role, item.relative_path, item.alias) for item in source_specs]
    if source_order != sorted(source_order):
        raise ValueError(f"{context}.sources must use stable base/head path order")
    source_by_alias = {item.alias: item for item in source_specs}

    files = tuple(
        _load_file_input(
            _mapping(value, f"{context}.files[{file_index}]"),
            source_by_alias,
            f"{context}.files[{file_index}]",
        )
        for file_index, value in enumerate(_list(data["files"], f"{context}.files"))
    )
    _validate_input_source_coverage(files, source_by_alias, context)
    expected = _validate_expected(
        data["expected"],
        repository=repository,
        base_revision=base_revision,
        head_revision=head_revision,
        normalizer=normalizer,
        source_specs=source_specs,
        files=files,
        context=f"{context}.expected",
    )
    return ChangeSetGoldenCase(
        case_id=case_id,
        description=description,
        repository=repository,
        base_revision=base_revision,
        head_revision=head_revision,
        diff_normalizer_version=normalizer,
        source_specs=tuple(source_specs),
        files=files,
        expected=expected,
    )


def _load_file_input(
    data: dict[str, Any],
    source_by_alias: dict[str, _SourceSpec],
    context: str,
) -> ChangedFileInput:
    _require_fields(data, FILE_INPUT_FIELDS, context)
    status = _text(data["status"], f"{context}.status")
    if status not in {"added", "modified", "deleted", "renamed"}:
        raise ValueError(f"{context}.status is unsupported")
    old_path = _optional_path(data["old_path"], f"{context}.old_path")
    new_path = _optional_path(data["new_path"], f"{context}.new_path")
    old_alias = _optional_text(data["old_source_alias"], f"{context}.old_source_alias")
    new_alias = _optional_text(data["new_source_alias"], f"{context}.new_source_alias")
    is_binary = _boolean(data["is_binary"], f"{context}.is_binary")
    old_spec = _source_alias(source_by_alias, old_alias, "base", old_path, context)
    new_spec = _source_alias(source_by_alias, new_alias, "head", new_path, context)
    atoms = tuple(
        _load_atom_input(
            _mapping(value, f"{context}.atoms[{atom_index}]"),
            old_spec,
            new_spec,
            f"{context}.atoms[{atom_index}]",
        )
        for atom_index, value in enumerate(_list(data["atoms"], f"{context}.atoms"))
    )
    return ChangedFileInput(
        status=status,  # type: ignore[arg-type]
        old_path=old_path,
        new_path=new_path,
        old_snapshot=None if old_spec is None else old_spec.snapshot,
        new_snapshot=None if new_spec is None else new_spec.snapshot,
        atoms=atoms,
        is_binary=is_binary,
    )


def _load_atom_input(
    data: dict[str, Any],
    old_spec: _SourceSpec | None,
    new_spec: _SourceSpec | None,
    context: str,
) -> ChangeAtomInput:
    _require_fields(data, ATOM_INPUT_FIELDS, context)
    kind = _text(data["kind"], f"{context}.kind")
    if kind not in {"addition", "deletion", "replacement"}:
        raise ValueError(f"{context}.kind is unsupported")
    old_span = _line_span(data["old_span"], old_spec, f"{context}.old_span")
    new_span = _line_span(data["new_span"], new_spec, f"{context}.new_span")
    added = _lines(data["added_new_lines"], f"{context}.added_new_lines")
    deleted = _lines(data["deleted_old_lines"], f"{context}.deleted_old_lines")
    positions = tuple(
        _diff_position(
            _mapping(value, f"{context}.diff_positions[{position_index}]"),
            f"{context}.diff_positions[{position_index}]",
        )
        for position_index, value in enumerate(
            _list(data["diff_positions"], f"{context}.diff_positions")
        )
    )
    return ChangeAtomInput(
        kind=kind,  # type: ignore[arg-type]
        old_span=old_span,
        new_span=new_span,
        added_new_lines=added,
        deleted_old_lines=deleted,
        diff_positions=positions,
    )


def _validate_expected(
    value: object,
    *,
    repository: str,
    base_revision: str,
    head_revision: str,
    normalizer: str,
    source_specs: list[_SourceSpec],
    files: tuple[ChangedFileInput, ...],
    context: str,
) -> dict[str, Any]:
    data = _mapping(value, context)
    _require_fields(data, CHANGE_SET_FIELDS, context)
    source_refs = tuple(
        _expected_source_ref(
            _mapping(item, f"{context}.source_refs[{index}]"), f"{context}.source_refs[{index}]"
        )
        for index, item in enumerate(_list(data["source_refs"], f"{context}.source_refs"))
    )
    changed_files = tuple(
        _expected_changed_file(
            _mapping(item, f"{context}.files[{index}]"), f"{context}.files[{index}]"
        )
        for index, item in enumerate(_list(data["files"], f"{context}.files"))
    )
    atoms = tuple(
        _expected_atom(_mapping(item, f"{context}.atoms[{index}]"), f"{context}.atoms[{index}]")
        for index, item in enumerate(_list(data["atoms"], f"{context}.atoms"))
    )
    diagnostics = tuple(
        _expected_diagnostic(
            _mapping(item, f"{context}.diagnostics[{index}]"), f"{context}.diagnostics[{index}]"
        )
        for index, item in enumerate(_list(data["diagnostics"], f"{context}.diagnostics"))
    )
    change_set = ChangeSet(
        schema_version=_text(data["schema_version"], f"{context}.schema_version"),
        change_set_id=_text(data["change_set_id"], f"{context}.change_set_id"),
        repository=_text(data["repository"], f"{context}.repository"),
        base_revision=_text(data["base_revision"], f"{context}.base_revision"),
        head_revision=_text(data["head_revision"], f"{context}.head_revision"),
        diff_normalizer_version=_text(
            data["diff_normalizer_version"],
            f"{context}.diff_normalizer_version",
        ),
        source_refs=source_refs,
        files=changed_files,
        atoms=atoms,
        diagnostics=diagnostics,
    )
    if (
        change_set.repository != repository
        or change_set.base_revision != base_revision
        or change_set.head_revision != head_revision
        or change_set.diff_normalizer_version != normalizer
    ):
        raise ValueError(f"{context} endpoint or normalizer provenance drift")
    expected_refs = tuple(
        sorted(
            (item.snapshot.source_ref for item in source_specs),
            key=lambda item: item.source_ref_id,
        )
    )
    if change_set.source_refs != expected_refs:
        raise ValueError(f"{context}.source_refs source hash/provenance drift")
    _validate_expected_semantics(change_set, files, context)
    return change_set.to_dict()


def _expected_source_ref(data: dict[str, Any], context: str) -> CodeSourceRef:
    _require_fields(data, SOURCE_REF_FIELDS, context)
    return CodeSourceRef(
        source_ref_id=_text(data["source_ref_id"], f"{context}.source_ref_id"),
        repository=_text(data["repository"], f"{context}.repository"),
        revision=_text(data["revision"], f"{context}.revision"),
        path=_normalized_path(data["path"], f"{context}.path"),
        content_hash=_prefixed_sha256(data["content_hash"], f"{context}.content_hash"),
    )


def _expected_changed_file(data: dict[str, Any], context: str) -> ChangedFile:
    _require_fields(data, CHANGED_FILE_FIELDS, context)
    status = _text(data["status"], f"{context}.status")
    atom_ids = tuple(
        _text(item, f"{context}.atom_ids[{index}]")
        for index, item in enumerate(_list(data["atom_ids"], f"{context}.atom_ids"))
    )
    return ChangedFile(
        changed_file_id=_text(data["changed_file_id"], f"{context}.changed_file_id"),
        status=status,  # type: ignore[arg-type]
        old_path=_optional_path(data["old_path"], f"{context}.old_path"),
        new_path=_optional_path(data["new_path"], f"{context}.new_path"),
        old_source_ref_id=_optional_text(data["old_source_ref_id"], f"{context}.old_source_ref_id"),
        new_source_ref_id=_optional_text(data["new_source_ref_id"], f"{context}.new_source_ref_id"),
        atom_ids=atom_ids,
        is_binary=_boolean(data["is_binary"], f"{context}.is_binary"),
    )


def _expected_atom(data: dict[str, Any], context: str) -> ChangeAtom:
    _require_fields(data, CHANGE_ATOM_FIELDS, context)
    kind = _text(data["kind"], f"{context}.kind")
    return ChangeAtom(
        atom_id=_text(data["atom_id"], f"{context}.atom_id"),
        kind=kind,  # type: ignore[arg-type]
        old_source_ref_id=_optional_text(data["old_source_ref_id"], f"{context}.old_source_ref_id"),
        new_source_ref_id=_optional_text(data["new_source_ref_id"], f"{context}.new_source_ref_id"),
        old_span=_exact_range(data["old_span"], f"{context}.old_span"),
        new_span=_exact_range(data["new_span"], f"{context}.new_span"),
        added_new_lines=_lines(data["added_new_lines"], f"{context}.added_new_lines"),
        deleted_old_lines=_lines(data["deleted_old_lines"], f"{context}.deleted_old_lines"),
        diff_positions=tuple(
            _diff_position(
                _mapping(item, f"{context}.diff_positions[{index}]"),
                f"{context}.diff_positions[{index}]",
            )
            for index, item in enumerate(_list(data["diff_positions"], f"{context}.diff_positions"))
        ),
        diff_normalizer_version=_text(
            data["diff_normalizer_version"],
            f"{context}.diff_normalizer_version",
        ),
    )


def _expected_diagnostic(data: dict[str, Any], context: str) -> ChangeSetDiagnostic:
    _require_fields(data, DIAGNOSTIC_FIELDS, context)
    code = _text(data["code"], f"{context}.code")
    return ChangeSetDiagnostic(
        diagnostic_id=_text(data["diagnostic_id"], f"{context}.diagnostic_id"),
        code=code,  # type: ignore[arg-type]
        changed_file_id=_text(data["changed_file_id"], f"{context}.changed_file_id"),
    )


def _exact_range(value: object, context: str) -> ExactRange | None:
    if value is None:
        return None
    data = _mapping(value, context)
    _require_fields(data, EXACT_RANGE_FIELDS, context)
    return ExactRange(
        start_line=_integer(data["start_line"], f"{context}.start_line", 1),
        end_line=_integer(data["end_line"], f"{context}.end_line", 1),
        start_offset_utf16=_integer(data["start_offset_utf16"], f"{context}.start_offset_utf16", 0),
        end_offset_utf16=_integer(data["end_offset_utf16"], f"{context}.end_offset_utf16", 0),
    )


def _validate_expected_semantics(
    expected: ChangeSet,
    inputs: tuple[ChangedFileInput, ...],
    context: str,
) -> None:
    expected_by_paths = {
        (item.status, item.old_path, item.new_path): item for item in expected.files
    }
    atom_by_id = {item.atom_id: item for item in expected.atoms}
    if len(expected_by_paths) != len(inputs):
        raise ValueError(f"{context}.files do not match Golden input")
    for file_input in inputs:
        key = (file_input.status, file_input.old_path, file_input.new_path)
        output_file = expected_by_paths.get(key)
        if output_file is None:
            raise ValueError(f"{context}.files do not match Golden input")
        old_id = (
            None
            if file_input.old_snapshot is None
            else file_input.old_snapshot.source_ref.source_ref_id
        )
        new_id = (
            None
            if file_input.new_snapshot is None
            else file_input.new_snapshot.source_ref.source_ref_id
        )
        if (
            output_file.old_source_ref_id != old_id
            or output_file.new_source_ref_id != new_id
            or output_file.is_binary != file_input.is_binary
            or len(output_file.atom_ids) != len(file_input.atoms)
        ):
            raise ValueError(f"{context} file provenance or atom count drift")
        actual_semantics = sorted(
            (_atom_semantics(atom_by_id[atom_id]) for atom_id in output_file.atom_ids),
            key=_canonical,
        )
        input_semantics = sorted(
            (
                _input_atom_semantics(
                    atom,
                    file_input.old_snapshot,
                    file_input.new_snapshot,
                    expected.diff_normalizer_version,
                )
                for atom in file_input.atoms
            ),
            key=_canonical,
        )
        if actual_semantics != input_semantics:
            raise ValueError(f"{context} atom span/line semantics drift")


def _input_atom_semantics(
    atom: ChangeAtomInput,
    old_snapshot: CodeSourceSnapshot | None,
    new_snapshot: CodeSourceSnapshot | None,
    normalizer: str,
) -> dict[str, object]:
    return {
        "kind": atom.kind,
        "old_source_ref_id": None
        if old_snapshot is None or atom.old_span is None
        else old_snapshot.source_ref.source_ref_id,
        "new_source_ref_id": None
        if new_snapshot is None or atom.new_span is None
        else new_snapshot.source_ref.source_ref_id,
        "old_span": None
        if old_snapshot is None or atom.old_span is None
        else asdict(_exact_from_lines(old_snapshot.content, atom.old_span)),
        "new_span": None
        if new_snapshot is None or atom.new_span is None
        else asdict(_exact_from_lines(new_snapshot.content, atom.new_span)),
        "added_new_lines": list(atom.added_new_lines),
        "deleted_old_lines": list(atom.deleted_old_lines),
        "diff_positions": [asdict(item) for item in atom.diff_positions],
        "diff_normalizer_version": normalizer,
    }


def _atom_semantics(atom: ChangeAtom) -> dict[str, object]:
    data = _mapping(json.loads(json.dumps(asdict(atom))), "ChangeAtom semantics")
    data.pop("atom_id")
    return data


def _exact_from_lines(content: str, span: ReviewUnitSpan) -> ExactRange:
    lines = content.splitlines(keepends=True)
    start = sum(_utf16_length(line) for line in lines[: span.start_line - 1])
    end = start + sum(_utf16_length(line) for line in lines[span.start_line - 1 : span.end_line])
    return ExactRange(span.start_line, span.end_line, start, end)


def _evaluate_case(case: ChangeSetGoldenCase) -> dict[str, Any]:
    invariants: list[str] = []
    error: str | None = None
    try:
        first = case.normalize()
        second = case.normalize()
        actual = first.to_dict()
        if actual != second.to_dict():
            invariants.append("normalization is not repeatable")
        reordered = tuple(
            ChangedFileInput(
                status=item.status,
                old_path=item.old_path,
                new_path=item.new_path,
                old_snapshot=item.old_snapshot,
                new_snapshot=item.new_snapshot,
                atoms=tuple(reversed(item.atoms)),
                is_binary=item.is_binary,
            )
            for item in reversed(case.files)
        )
        if case.normalize(reordered).to_dict() != actual:
            invariants.append("file or atom input order changes normalized output")
    except Exception as exc:  # Golden reports execution failures as mismatches.
        actual = {"error": f"{type(exc).__name__}: {exc}"}
        error = f"{type(exc).__name__}: {exc}"
        invariants.append("normalization failed")
    differences = _differences(case.expected, actual)
    return {
        "case_id": case.case_id,
        "matched": not differences and not invariants,
        "source_provenance": [
            {
                "alias": item.alias,
                "role": item.role,
                "relative_path": item.relative_path,
                "content_sha256": item.content_sha256,
                "origin_lines": None if item.origin_lines is None else list(item.origin_lines),
            }
            for item in case.source_specs
        ],
        "expected": case.expected,
        "actual": actual,
        "differences": differences,
        "invariant_violations": invariants,
        "error": error,
    }


def _validate_coordinate_system(value: object) -> None:
    data = _mapping(value, "manifest.coordinate_system")
    _require_fields(data, COORDINATE_FIELDS, "manifest.coordinate_system")
    expected = {
        "line_base": 1,
        "line_end": "inclusive",
        "offset_encoding": "UTF-16",
        "offset_base": 0,
        "offset_end": "exclusive",
    }
    if data != expected:
        raise ValueError("manifest.coordinate_system drift")


def _validate_frozen_contract(value: object) -> None:
    data = _mapping(value, "manifest.frozen_contract")
    _require_fields(data, CONTRACT_FIELDS, "manifest.frozen_contract")
    expected = {
        "change_set_schema_version": CHANGE_SET_SCHEMA_VERSION,
        "diff_normalizer_version": DIFF_NORMALIZER_VERSION,
        "atom_kinds": ["addition", "deletion", "replacement"],
        "file_statuses": ["added", "modified", "deleted", "renamed"],
        "diagnostic_codes": ["binary_source_unavailable"],
    }
    if data != expected:
        raise ValueError("manifest.frozen_contract drift")


def _resolve_snapshot(root: Path, value: object, context: str) -> Path:
    relative = Path(_text(value, f"{context}.file"))
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{context}.file must be a normalized relative path")
    candidate = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{context}.file must not use symlinks")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"{context}.file escapes the Golden root")
    return candidate


def _read_regular_file(path: Path, context: str) -> bytes:
    if path.is_symlink():
        raise ValueError(f"{context} must not be a symlink")
    if not path.is_file():
        raise ValueError(f"{context} must be a regular file")
    return path.read_bytes()


def _load_json_bytes(raw: bytes, context: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r} in {context}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON in {context}: {exc}") from exc


def _require_fields(data: dict[str, Any], fields: tuple[str, ...], context: str) -> None:
    expected = set(fields)
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{context} fields mismatch: missing={missing}, unknown={unknown}")


def _mapping(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be an object")
    return value


def _list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _optional_text(value: object, context: str) -> str | None:
    return None if value is None else _text(value, context)


def _normalized_path(value: object, context: str) -> str:
    path = _text(value, context)
    if path != normalize_review_path(path):
        raise ValueError(f"{context} must be normalized")
    return path


def _optional_path(value: object, context: str) -> str | None:
    return None if value is None else _normalized_path(value, context)


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")
    return value


def _integer(value: object, context: str, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{context} must be an integer >= {minimum}")
    return value


def _sha256(value: object, context: str) -> str:
    digest = _text(value, f"{context}.content_sha256")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"{context}.content_sha256 must be 64 lowercase hex")
    return digest


def _prefixed_sha256(value: object, context: str) -> str:
    text = _text(value, context)
    if not text.startswith("sha256:") or _sha256(text[7:], context) != text[7:]:
        raise ValueError(f"{context} must use sha256:<64 lowercase hex>")
    return text


def _origin_lines(value: object, line_count: int, context: str) -> tuple[int, int] | None:
    if value is None:
        if line_count != 0:
            raise ValueError(f"{context} may be null only for empty source")
        return None
    values = _list(value, context)
    if len(values) != 2:
        raise ValueError(f"{context} must contain [start, end]")
    start = _integer(values[0], f"{context}[0]", 1)
    end = _integer(values[1], f"{context}[1]", 1)
    if end < start or end - start + 1 != line_count:
        raise ValueError(f"{context} does not match source line count")
    return (start, end)


def _source_alias(
    sources: dict[str, _SourceSpec],
    alias: str | None,
    role: str,
    path: str | None,
    context: str,
) -> _SourceSpec | None:
    if alias is None:
        return None
    source = sources.get(alias)
    if source is None:
        raise ValueError(f"{context} references unknown source alias {alias!r}")
    if source.role != role or source.relative_path != path:
        raise ValueError(f"{context} source alias provenance drift")
    return source


def _line_span(value: object, source: _SourceSpec | None, context: str) -> ReviewUnitSpan | None:
    if value is None:
        return None
    if source is None:
        raise ValueError(f"{context} requires a source snapshot")
    data = _mapping(value, context)
    _require_fields(data, LINE_SPAN_FIELDS, context)
    span = ReviewUnitSpan(
        _integer(data["start_line"], f"{context}.start_line", 1),
        _integer(data["end_line"], f"{context}.end_line", 1),
    )
    line_count = len(source.snapshot.content.splitlines(keepends=True))
    if span.end_line > line_count:
        raise ValueError(f"{context} exceeds source line count")
    return span


def _lines(value: object, context: str) -> tuple[int, ...]:
    values = tuple(
        _integer(item, f"{context}[{index}]", 1) for index, item in enumerate(_list(value, context))
    )
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _diff_position(data: dict[str, Any], context: str) -> DiffPosition:
    _require_fields(data, DIFF_POSITION_FIELDS, context)
    side = _text(data["side"], f"{context}.side")
    return DiffPosition(
        side=side,  # type: ignore[arg-type]
        source_line=_integer(data["source_line"], f"{context}.source_line", 1),
        diff_position=_integer(data["diff_position"], f"{context}.diff_position", 1),
    )


def _validate_input_source_coverage(
    files: tuple[ChangedFileInput, ...],
    source_by_alias: dict[str, _SourceSpec],
    context: str,
) -> None:
    used_ids = {
        snapshot.source_ref.source_ref_id
        for item in files
        for snapshot in (item.old_snapshot, item.new_snapshot)
        if snapshot is not None
    }
    declared_ids = {item.snapshot.source_ref.source_ref_id for item in source_by_alias.values()}
    if used_ids != declared_ids:
        raise ValueError(f"{context}.sources must each belong to exactly one file endpoint")


def _file_input_projection(value: ChangedFileInput) -> dict[str, object]:
    return {
        "status": value.status,
        "old_path": value.old_path,
        "new_path": value.new_path,
        "old_source_ref_id": None
        if value.old_snapshot is None
        else value.old_snapshot.source_ref.source_ref_id,
        "new_source_ref_id": None
        if value.new_snapshot is None
        else value.new_snapshot.source_ref.source_ref_id,
        "is_binary": value.is_binary,
        "atoms": [
            {
                "kind": item.kind,
                "old_span": None if item.old_span is None else asdict(item.old_span),
                "new_span": None if item.new_span is None else asdict(item.new_span),
                "added_new_lines": list(item.added_new_lines),
                "deleted_old_lines": list(item.deleted_old_lines),
                "diff_positions": [asdict(position) for position in item.diff_positions],
            }
            for item in value.atoms
        ],
    }


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _differences(expected: object, actual: object, path: str = "$") -> list[str]:
    if type(expected) is not type(actual):
        return [f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"]
    if isinstance(expected, dict) and isinstance(actual, dict):
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                differences.append(f"{path}.{key}: unexpected")
            elif key not in actual:
                differences.append(f"{path}.{key}: missing")
            else:
                differences.extend(_differences(expected[key], actual[key], f"{path}.{key}"))
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        differences = []
        if len(expected) != len(actual):
            differences.append(f"{path}: expected length {len(expected)}, got {len(actual)}")
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            differences.extend(_differences(left, right, f"{path}[{index}]"))
        return differences
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]
