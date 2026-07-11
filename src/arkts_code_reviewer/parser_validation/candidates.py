from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from arkts_code_reviewer.code_analysis.models import CodeParser
from arkts_code_reviewer.parser_validation.golden import (
    EXPECTED_FIELDS,
    SET_FACT_FIELDS,
    SYNTAX_KINDS,
    ParserGoldenCase,
    ParserGoldenSuite,
    _flatten_imports,
    _validate_declaration,
    _validate_declaration_relationships,
    _validate_fact_declaration_projection,
    evaluate_golden_suite,
)
from arkts_code_reviewer.parser_validation.manifest import (
    CorpusManifest,
    SampleEntry,
    load_corpus_manifest,
    verify_corpus_checkout,
)

CANDIDATE_SCHEMA_VERSION = "parser-golden-candidate-v1"
CANDIDATE_REPORT_SCHEMA_VERSION = "parser-candidate-evaluation-v1"
TRUTH_STATUS = "candidate_unreviewed"
EVALUATION_STATUS = "provisional"
SOURCE_ID = "arkui-ace-engine"
CONTRACT_UNSUPPORTED_FIELDS = (
    "fact_occurrences",
    "fact_spans",
    "fact_owners",
    "parser_diagnostics",
    "raw_l1_snapshot",
)
DEFAULT_REVIEWED_GROUPS = (
    "B001",
    "B002",
    "B003",
    "B004",
    "B005",
    "B006",
    "B008",
    "B010",
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CORPUS_MANIFEST = _REPO_ROOT / "tests" / "fixtures" / "arkui_ace_engine_samples.json"
DEFAULT_REGISTRY = _REPO_ROOT.parent / "arkts-knowledge" / "registry" / "sources.yaml"


@dataclass(frozen=True)
class CandidateGroupContract:
    manifest_sha256: str
    case_ids: tuple[str, ...]


DEFAULT_GROUP_CONTRACTS: Mapping[str, CandidateGroupContract] = {
    "B001": CandidateGroupContract(
        "d4f12682cff537d653f1b02fb231a9cfbcc455db2ce43f0348118b129fba901f",
        ("R63-008", "R63-009", "R63-044", "R63-045", "R63-046"),
    ),
    "B002": CandidateGroupContract(
        "dc9f0c514e206623ce44ff9b4e30f9d9b0e607056ed9b7f0c29cf7a9bc11cfbc",
        ("R63-047", "R63-048", "R63-053", "R63-054", "R63-055"),
    ),
    "B003": CandidateGroupContract(
        "79b16ff8b357955a4df1f0a0a5f6b4aa6ca3357a2e7d0aa30ba87d51cf0bf464",
        ("R63-056", "R63-057", "R63-062", "R63-063"),
    ),
    "B004": CandidateGroupContract(
        "519bc87d58829df4268d0436b79588471947a56173905d0107eae17e6f9e8aa8",
        ("R63-050", "R63-051", "R63-052"),
    ),
    "B005": CandidateGroupContract(
        "167d1f3578f4d2f65ff47744502b53c92970e6f4177080acf83cab5553fa5097",
        ("R63-040", "R63-049"),
    ),
    "B006": CandidateGroupContract(
        "fbe0d57c658e02dfa508743af433b9a72a05aab154518a77f0c4566f5491e843",
        ("R63-010",),
    ),
    "B007": CandidateGroupContract(
        "bb1a34151cb9caefecebf6e0724b2621e1d493d6656a6d9896d4b84370524f16",
        ("R63-039", "R63-003"),
    ),
    "B008": CandidateGroupContract(
        "c001b21fc0b7bf54358ab36cd3865f23fcf0c16faba1772961112e3b812d411d",
        ("R63-005", "R63-038"),
    ),
    "B009": CandidateGroupContract(
        "a5260625660815f8fb8f5c84ee30e99dbbe3fff58b59225473f91e0ed10d7290",
        ("R63-001",),
    ),
    "B010": CandidateGroupContract(
        "579cc21288c634d624cf4da964d33f01895a55bdb1105de1877c4a7ea58e4f2c",
        ("R63-002",),
    ),
}

_TOP_FIELDS = (
    "schema_version",
    "target_schema_version",
    "truth_status",
    "prompt_version",
    "corpus",
    "group",
    "annotator",
    "independence_attestation",
    "cases",
)
_CASE_FIELDS = (
    "case_id",
    "category",
    "logical_path",
    "source",
    "annotation_status",
    "adjudication_status",
    "proposed_scored_fields",
    "candidate_expected",
    "candidate_must_not_emit",
    "field_reviews",
    "self_checks",
)
_SOURCE_FIELDS = (
    "source_id",
    "revision",
    "relative_path",
    "copied_path",
    "content_sha256",
    "line_count",
)
_MUST_NOT_FIELDS = tuple(SET_FACT_FIELDS)
_REVIEW_FIELDS = ("coverage", "confidence", "evidence", "excluded", "uncertainties")
_EVIDENCE_FIELDS = ("value", "line_ranges", "reason")
_LINE_RANGE_FIELDS = ("start_line", "end_line")
_SELF_CHECK_FIELDS = (
    "source_fully_read",
    "comments_and_strings_excluded",
    "components_equal_ui_block_names",
    "symbols_equal_declaration_names",
    "declaration_parents_and_spans_checked",
    "lists_sorted_unique",
)
_GROUP_RE = re.compile(r"B[0-9]{3}\Z")


@dataclass(frozen=True)
class ParserCandidateSuite:
    golden_suite: ParserGoldenSuite
    groups: tuple[str, ...]
    source_id: str
    revision: str
    source_root: Path
    suite_fingerprint: str
    annotation_fingerprint: str
    truth_status: str = TRUTH_STATUS
    evaluation_status: str = EVALUATION_STATUS

    @property
    def suite_id(self) -> str:
        return self.golden_suite.suite_id

    @property
    def cases(self) -> tuple[ParserGoldenCase, ...]:
        return self.golden_suite.cases


def load_candidate_suite(
    candidate_dir: Path,
    *,
    groups: Sequence[str] = DEFAULT_REVIEWED_GROUPS,
    source_root: Path | None = None,
    registry_path: Path | None = DEFAULT_REGISTRY,
    corpus_manifest_path: Path = DEFAULT_CORPUS_MANIFEST,
    group_contracts: Mapping[str, CandidateGroupContract] = DEFAULT_GROUP_CONTRACTS,
) -> ParserCandidateSuite:
    """Load unreviewed candidate shards as an explicitly provisional score suite."""

    candidate_dir = candidate_dir.resolve()
    selected_groups = _normalize_groups(groups, group_contracts)
    corpus = load_corpus_manifest(corpus_manifest_path)
    if corpus.source_id != SOURCE_ID:
        raise ValueError(f"corpus source_id must be {SOURCE_ID!r}")
    case_catalog = _case_catalog(corpus)
    selected_samples: list[SampleEntry] = []
    for group_id in selected_groups:
        for case_id in group_contracts[group_id].case_ids:
            try:
                selected_samples.append(case_catalog[case_id])
            except KeyError as exc:
                raise ValueError(f"{group_id} refers to unknown corpus case {case_id}") from exc

    resolved_source_root = (
        source_root.resolve()
        if source_root is not None
        else resolve_source_root(
            registry_path,
            source_id=corpus.source_id,
            revision=corpus.revision,
        )
    )
    selected_manifest = CorpusManifest(
        schema_version=corpus.schema_version,
        suite_id=corpus.suite_id,
        suite_role=corpus.suite_role,
        source_id=corpus.source_id,
        revision=corpus.revision,
        samples=tuple(selected_samples),
    )
    verify_corpus_checkout(resolved_source_root, selected_manifest)

    cases: list[ParserGoldenCase] = []
    fingerprint_groups: list[dict[str, Any]] = []
    annotation_groups: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for group_id in selected_groups:
        contract = group_contracts[group_id]
        shard_path = candidate_dir / f"{group_id}.candidate.json"
        data = _load_json(shard_path)
        raw_cases = _validate_shard_header(data, group_id, contract, corpus)
        loaded_cases: list[dict[str, Any]] = []
        for index, (raw_case, case_id) in enumerate(
            zip(raw_cases, contract.case_ids, strict=True)
        ):
            if case_id in seen_case_ids:
                raise ValueError(f"duplicate candidate case_id: {case_id}")
            seen_case_ids.add(case_id)
            sample = case_catalog[case_id]
            case, fingerprint_case = _load_case(
                raw_case,
                context=f"{group_id}.cases[{index}]",
                expected_case_id=case_id,
                expected_sample=sample,
                revision=corpus.revision,
                source_root=resolved_source_root,
            )
            cases.append(case)
            loaded_cases.append(fingerprint_case)
        fingerprint_groups.append(
            {
                "group_id": group_id,
                "group_manifest_sha256": contract.manifest_sha256,
                "cases": loaded_cases,
            }
        )
        annotation_groups.append(
            {
                "group_id": group_id,
                "annotator": data["annotator"],
                "cases": [
                    {
                        "case_id": raw_case["case_id"],
                        "field_reviews": raw_case["field_reviews"],
                        "self_checks": raw_case["self_checks"],
                    }
                    for raw_case in raw_cases
                ],
            }
        )

    fingerprint_payload = {
        "schema_version": CANDIDATE_REPORT_SCHEMA_VERSION,
        "truth_status": TRUTH_STATUS,
        "source_id": corpus.source_id,
        "revision": corpus.revision,
        "groups": fingerprint_groups,
    }
    fingerprint = hashlib.sha256(_canonical_bytes(fingerprint_payload)).hexdigest()
    annotation_fingerprint = hashlib.sha256(
        _canonical_bytes(
            {
                "schema_version": CANDIDATE_REPORT_SCHEMA_VERSION,
                "truth_status": TRUTH_STATUS,
                "groups": annotation_groups,
            }
        )
    ).hexdigest()
    suite_id = f"{corpus.suite_id}-candidate-{'-'.join(selected_groups)}"
    golden_suite = ParserGoldenSuite(
        suite_id=suite_id,
        manifest_path=candidate_dir / f"{selected_groups[0]}.candidate.json",
        cases=tuple(cases),
        unsupported_fields=CONTRACT_UNSUPPORTED_FIELDS,
        suite_fingerprint=fingerprint,
    )
    return ParserCandidateSuite(
        golden_suite=golden_suite,
        groups=selected_groups,
        source_id=corpus.source_id,
        revision=corpus.revision,
        source_root=resolved_source_root,
        suite_fingerprint=fingerprint,
        annotation_fingerprint=annotation_fingerprint,
    )


def evaluate_candidate_suite(
    suite: ParserCandidateSuite,
    parser: CodeParser,
) -> dict[str, Any]:
    report = evaluate_golden_suite(suite.golden_suite, parser)
    report.update(
        {
            "schema_version": CANDIDATE_REPORT_SCHEMA_VERSION,
            "truth_status": suite.truth_status,
            "evaluation_status": suite.evaluation_status,
            "candidate_groups": list(suite.groups),
            "source_id": suite.source_id,
            "revision": suite.revision,
            "suite_fingerprint": suite.suite_fingerprint,
            "annotation_fingerprint": suite.annotation_fingerprint,
        }
    )
    return report


def audit_candidate_evidence(
    suite: ParserCandidateSuite,
    candidate_dir: Path,
) -> dict[str, Any]:
    """Audit annotation evidence without promoting provisional candidates to truth."""

    candidate_dir = candidate_dir.resolve()
    cases_by_id = {case.case_id: case for case in suite.cases}
    issues: list[dict[str, Any]] = []

    def add_issue(
        group: str,
        case_id: str,
        field: str,
        code: str,
        value: object,
        detail: str,
    ) -> None:
        issues.append(
            {
                "group": group,
                "case_id": case_id,
                "field": field,
                "code": code,
                "value": value,
                "detail": detail,
            }
        )

    for group in suite.groups:
        data = _load_json(candidate_dir / f"{group}.candidate.json")
        for raw_case in data["cases"]:
            case_id = raw_case["case_id"]
            case = cases_by_id[case_id]
            line_count = int(case.source_metadata["line_count"])
            raw_expected = raw_case["candidate_expected"]
            raw_must_not = raw_case["candidate_must_not_emit"]
            declarations = raw_expected.get("declarations") or []

            for field in EXPECTED_FIELDS:
                review = raw_case["field_reviews"][field]
                valid_evidence = _audit_evidence_entries(
                    review["evidence"],
                    group=group,
                    case_id=case_id,
                    field=field,
                    kind="evidence",
                    line_count=line_count,
                    add_issue=add_issue,
                )
                valid_excluded = _audit_evidence_entries(
                    review["excluded"],
                    group=group,
                    case_id=case_id,
                    field=field,
                    kind="excluded",
                    line_count=line_count,
                    add_issue=add_issue,
                )

                expected_values = raw_expected[field]
                if expected_values is not None:
                    evidence_values = {
                        _canonical_bytes(entry["value"]) for entry in valid_evidence
                    }
                    for value in expected_values:
                        if _canonical_bytes(value) not in evidence_values:
                            add_issue(
                                group,
                                case_id,
                                field,
                                "missing_expected_evidence",
                                value,
                                "scored expected value has no matching evidence entry",
                            )

                if field == "declarations":
                    for entry in valid_evidence:
                        value = entry["value"]
                        if not isinstance(value, dict) or not isinstance(value.get("span"), dict):
                            continue
                        if entry["line_ranges"] != [value["span"]]:
                            add_issue(
                                group,
                                case_id,
                                field,
                                "declaration_evidence_not_exact_span",
                                value,
                                "declaration evidence must equal its complete occurrence span",
                            )
                elif field == "symbols":
                    for entry in valid_evidence:
                        value = entry["value"]
                        if not isinstance(value, str):
                            continue
                        possible = [
                            declaration["span"]
                            for declaration in declarations
                            if declaration["qualified_name"] == value
                        ]
                        if not possible:
                            possible = [
                                declaration["span"]
                                for declaration in declarations
                                if declaration["name"] == value
                            ]
                        for line_range in entry["line_ranges"]:
                            if line_range not in possible:
                                add_issue(
                                    group,
                                    case_id,
                                    field,
                                    "symbol_evidence_not_declaration_span",
                                    value,
                                    "symbol evidence must equal a matching declaration span",
                                )
                                break

                if field in raw_must_not:
                    excluded_values = {
                        _canonical_bytes(entry["value"]) for entry in valid_excluded
                    }
                    for value in raw_must_not[field]:
                        if _canonical_bytes(value) not in excluded_values:
                            add_issue(
                                group,
                                case_id,
                                field,
                                "missing_must_not_excluded_evidence",
                                value,
                                "must-not value has no matching excluded evidence",
                            )

    issues.sort(
        key=lambda item: (
            item["group"],
            item["case_id"],
            item["field"],
            item["code"],
            json.dumps(item["value"], ensure_ascii=False, sort_keys=True),
        )
    )
    return {
        "schema_version": "parser-candidate-evidence-audit-v1",
        "truth_status": suite.truth_status,
        "groups": list(suite.groups),
        "suite_fingerprint": suite.suite_fingerprint,
        "annotation_fingerprint": suite.annotation_fingerprint,
        "issue_count": len(issues),
        "issues": issues,
    }


def _audit_evidence_entries(
    raw_entries: object,
    *,
    group: str,
    case_id: str,
    field: str,
    kind: str,
    line_count: int,
    add_issue: Any,
) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(raw_entries if isinstance(raw_entries, list) else []):
        if not isinstance(raw_entry, dict) or set(raw_entry) != set(_EVIDENCE_FIELDS):
            add_issue(
                group,
                case_id,
                field,
                f"invalid_{kind}_schema",
                index,
                f"{kind} entry fields must be {list(_EVIDENCE_FIELDS)}",
            )
            continue
        reason = raw_entry.get("reason")
        ranges = raw_entry.get("line_ranges")
        if not isinstance(reason, str) or not reason or not isinstance(ranges, list) or not ranges:
            add_issue(
                group,
                case_id,
                field,
                f"invalid_{kind}_schema",
                raw_entry.get("value"),
                f"{kind} requires a non-empty reason and line_ranges",
            )
            continue
        normalized_ranges: list[dict[str, int]] = []
        range_error = False
        for line_range in ranges:
            if not isinstance(line_range, dict) or set(line_range) != set(_LINE_RANGE_FIELDS):
                range_error = True
                break
            start_line = line_range.get("start_line")
            end_line = line_range.get("end_line")
            if (
                not isinstance(start_line, int)
                or isinstance(start_line, bool)
                or not isinstance(end_line, int)
                or isinstance(end_line, bool)
                or start_line < 1
                or end_line < start_line
                or end_line > line_count
            ):
                range_error = True
                break
            normalized_ranges.append(
                {"start_line": start_line, "end_line": end_line}
            )
        if range_error:
            add_issue(
                group,
                case_id,
                field,
                f"invalid_{kind}_line_range",
                raw_entry.get("value"),
                f"{kind} line range must be inside the selected source",
            )
            continue
        valid.append({**raw_entry, "line_ranges": normalized_ranges})
    return valid


def resolve_source_root(
    registry_path: Path | None,
    *,
    source_id: str,
    revision: str,
) -> Path:
    if registry_path is None:
        raise ValueError("source_root or registry_path is required")
    registry_path = registry_path.resolve()
    yaml = YAML(typ="safe")
    raw = yaml.load(registry_path.read_text(encoding="utf-8"))
    registry = _mapping(raw, "registry")
    sources = _list(registry.get("sources"), "registry.sources")
    matches = [item for item in sources if isinstance(item, dict) and item.get("id") == source_id]
    if len(matches) != 1:
        raise ValueError(f"registry must contain exactly one source {source_id!r}")
    source = _mapping(matches[0], f"registry.sources[{source_id}]")
    if source.get("revision") != revision:
        raise ValueError(
            f"registry revision mismatch for {source_id}: expected {revision}, "
            f"got {source.get('revision')!r}"
        )
    env_override = source.get("env_override")
    configured = os.getenv(env_override) if isinstance(env_override, str) else None
    root_value = configured or source.get("local_path")
    if not isinstance(root_value, str) or not root_value:
        raise ValueError(f"registry source {source_id!r} has no usable local_path")
    return Path(root_value).expanduser().resolve()


def _load_case(
    value: object,
    *,
    context: str,
    expected_case_id: str,
    expected_sample: SampleEntry,
    revision: str,
    source_root: Path,
) -> tuple[ParserGoldenCase, dict[str, Any]]:
    raw = _mapping(value, context)
    _exact_fields(raw, _CASE_FIELDS, context)
    if raw.get("case_id") != expected_case_id:
        raise ValueError(f"{context}.case_id must be {expected_case_id!r}")
    if raw.get("category") != expected_sample.category:
        raise ValueError(f"{context}.category does not match the corpus manifest")
    logical_path = _portable_path(raw.get("logical_path"), f"{context}.logical_path")
    if logical_path != expected_sample.path:
        raise ValueError(f"{context}.logical_path must be {expected_sample.path!r}")
    if raw.get("annotation_status") != "complete":
        raise ValueError(f"{context}.annotation_status must be 'complete' for evaluation")
    if raw.get("adjudication_status") != "unreviewed":
        raise ValueError(f"{context}.adjudication_status must be 'unreviewed'")

    source = _mapping(raw.get("source"), f"{context}.source")
    _exact_fields(source, _SOURCE_FIELDS, f"{context}.source")
    expected_copied_path = f"sources/{logical_path}"
    expected_identity = {
        "source_id": SOURCE_ID,
        "revision": revision,
        "relative_path": logical_path,
        "copied_path": expected_copied_path,
    }
    for key, expected_value in expected_identity.items():
        if source.get(key) != expected_value:
            raise ValueError(f"{context}.source.{key} must be {expected_value!r}")
    expected_hash = _lower_hex(source.get("content_sha256"), f"{context}.source.content_sha256", 64)
    line_count = _positive_int(source.get("line_count"), f"{context}.source.line_count")
    source_path = (source_root / logical_path).resolve()
    if not source_path.is_relative_to(source_root) or not source_path.is_file():
        raise ValueError(f"{context}.logical_path is not a selected source file")
    source_bytes = source_path.read_bytes()
    actual_hash = hashlib.sha256(source_bytes).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(
            f"{context}.source hash mismatch: expected {expected_hash}, got {actual_hash}"
        )
    try:
        actual_line_count = len(source_bytes.decode("utf-8").splitlines())
    except UnicodeDecodeError as exc:
        raise ValueError(f"{context}.source is not UTF-8") from exc
    if actual_line_count != line_count:
        raise ValueError(
            f"{context}.source line_count mismatch: expected {line_count}, "
            f"got {actual_line_count}"
        )

    raw_expected = _mapping(raw.get("candidate_expected"), f"{context}.candidate_expected")
    _exact_fields(raw_expected, EXPECTED_FIELDS, f"{context}.candidate_expected")
    proposed = tuple(
        _string(item, f"{context}.proposed_scored_fields[]")
        for item in _list(raw.get("proposed_scored_fields"), f"{context}.proposed_scored_fields")
    )
    if len(proposed) != len(set(proposed)) or set(proposed) - set(EXPECTED_FIELDS):
        raise ValueError(f"{context}.proposed_scored_fields is invalid")
    non_null_fields = tuple(
        field for field in EXPECTED_FIELDS if raw_expected[field] is not None
    )
    if proposed != non_null_fields:
        raise ValueError(
            f"{context}.proposed_scored_fields must exactly match non-null "
            "candidate_expected in canonical field order"
        )
    expected = _normalize_expected(raw_expected, line_count=line_count, context=context)
    _validate_fact_declaration_projection(expected, context)

    raw_must_not = _mapping(
        raw.get("candidate_must_not_emit"), f"{context}.candidate_must_not_emit"
    )
    _exact_fields(raw_must_not, _MUST_NOT_FIELDS, f"{context}.candidate_must_not_emit")
    must_not: dict[str, list[str]] = {}
    for field in _MUST_NOT_FIELDS:
        values = _sorted_unique_strings(
            raw_must_not.get(field), f"{context}.candidate_must_not_emit.{field}"
        )
        expected_values = expected[field]
        if expected_values is not None and set(values) & set(expected_values):
            raise ValueError(f"{context}.candidate_must_not_emit.{field} overlaps expected")
        must_not[field] = values

    reviews = _mapping(raw.get("field_reviews"), f"{context}.field_reviews")
    _exact_fields(reviews, EXPECTED_FIELDS, f"{context}.field_reviews")
    for field in EXPECTED_FIELDS:
        review = _mapping(reviews.get(field), f"{context}.field_reviews.{field}")
        _exact_fields(review, _REVIEW_FIELDS, f"{context}.field_reviews.{field}")
        coverage = _string(review.get("coverage"), f"{context}.field_reviews.{field}.coverage")
        confidence = _string(
            review.get("confidence"), f"{context}.field_reviews.{field}.confidence"
        )
        _list(review.get("evidence"), f"{context}.field_reviews.{field}.evidence")
        _list(review.get("excluded"), f"{context}.field_reviews.{field}.excluded")
        uncertainties = _list(
            review.get("uncertainties"), f"{context}.field_reviews.{field}.uncertainties"
        )
        if field in proposed and (
            coverage != "complete" or confidence != "high" or uncertainties
        ):
            raise ValueError(
                f"{context}.field_reviews.{field} must be complete, high-confidence, "
                "and uncertainty-free before scoring"
            )
    self_checks = _mapping(raw.get("self_checks"), f"{context}.self_checks")
    _exact_fields(self_checks, _SELF_CHECK_FIELDS, f"{context}.self_checks")
    if any(value is not True for value in self_checks.values()):
        raise ValueError(f"{context}.self_checks must all be true")

    metadata = {
        "kind": "external_checkout",
        "source_id": SOURCE_ID,
        "revision": revision,
        "relative_path": logical_path,
        "content_sha256": expected_hash,
        "line_count": line_count,
    }
    case = ParserGoldenCase(
        case_id=expected_case_id,
        description=f"Provisional Grok candidate for {logical_path}",
        source_path=source_path,
        logical_path=logical_path,
        source_metadata=metadata,
        scored_fields=proposed,
        expected=expected,
        must_not_emit=must_not,
    )
    fingerprint_case = {
        "case_id": expected_case_id,
        "logical_path": logical_path,
        "source": metadata,
        "scored_fields": list(proposed),
        "expected": expected,
        "must_not_emit": must_not,
    }
    return case, fingerprint_case


def _validate_shard_header(
    data: dict[str, Any],
    group_id: str,
    contract: CandidateGroupContract,
    corpus: CorpusManifest,
) -> list[Any]:
    _exact_fields(data, _TOP_FIELDS, group_id)
    constants = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "target_schema_version": "parser-golden-v1",
        "truth_status": TRUTH_STATUS,
        "prompt_version": "r63-grok-annotation-v1",
    }
    for key, expected in constants.items():
        if data.get(key) != expected:
            raise ValueError(f"{group_id}.{key} must be {expected!r}")
    annotator = _mapping(data.get("annotator"), f"{group_id}.annotator")
    _exact_fields(annotator, ("provider", "model", "run_id"), f"{group_id}.annotator")
    if annotator.get("provider") != "xai":
        raise ValueError(f"{group_id}.annotator.provider must be 'xai'")
    _string(annotator.get("model"), f"{group_id}.annotator.model")
    _string(annotator.get("run_id"), f"{group_id}.annotator.run_id")
    expected_attestation = {
        "only_allowlisted_inputs_read": True,
        "parser_source_read": False,
        "parser_output_read": False,
        "baseline_read": False,
        "prior_expected_read": False,
        "parser_executed": False,
    }
    if data.get("independence_attestation") != expected_attestation:
        raise ValueError(f"{group_id}.independence_attestation is invalid")
    expected_corpus = {
        "suite_id": corpus.suite_id,
        "source_id": corpus.source_id,
        "revision": corpus.revision,
    }
    if data.get("corpus") != expected_corpus:
        raise ValueError(f"{group_id}.corpus does not match the pinned corpus")
    expected_group = {
        "group_id": group_id,
        "group_manifest_sha256": contract.manifest_sha256,
        "expected_case_ids": list(contract.case_ids),
    }
    if data.get("group") != expected_group:
        raise ValueError(f"{group_id}.group does not match its trusted group contract")
    raw_cases = _list(data.get("cases"), f"{group_id}.cases")
    if len(raw_cases) != len(contract.case_ids):
        raise ValueError(f"{group_id}.cases count does not match its trusted group contract")
    return raw_cases


def _normalize_expected(
    raw: dict[str, Any], *, line_count: int, context: str
) -> dict[str, list[Any] | None]:
    expected: dict[str, list[Any] | None] = {}
    for field in EXPECTED_FIELDS:
        value = raw[field]
        if value is None:
            expected[field] = None
            continue
        values = _list(value, f"{context}.candidate_expected.{field}")
        if field == "imports":
            expected[field] = _flatten_imports(values)
        elif field == "declarations":
            declarations = [
                _validate_declaration(
                    item,
                    f"{context}.candidate_expected.declarations[{index}]",
                    source_line_count=line_count,
                )
                for index, item in enumerate(values)
            ]
            _validate_declaration_relationships(declarations, context)
            declaration_order = [
                (
                    declaration["span"]["start_line"],
                    declaration["span"]["end_line"],
                    declaration["qualified_name"],
                )
                for declaration in declarations
            ]
            if declaration_order != sorted(declaration_order):
                raise ValueError(
                    f"{context}.candidate_expected.declarations must be source-position sorted"
                )
            expected[field] = declarations
        else:
            normalized = _sorted_unique_strings(
                values, f"{context}.candidate_expected.{field}"
            )
            if field == "syntax" and not set(normalized).issubset(SYNTAX_KINDS):
                raise ValueError(
                    f"{context}.candidate_expected.syntax contains unsupported kinds"
                )
            expected[field] = normalized
    return expected


def _case_catalog(corpus: CorpusManifest) -> dict[str, SampleEntry]:
    return {f"R63-{index:03}": sample for index, sample in enumerate(corpus.samples, start=1)}


def _normalize_groups(
    groups: Sequence[str], contracts: Mapping[str, CandidateGroupContract]
) -> tuple[str, ...]:
    normalized = tuple(sorted((group.upper() for group in groups), key=lambda item: int(item[1:])))
    if not normalized:
        raise ValueError("at least one candidate group is required")
    if len(normalized) != len(set(normalized)):
        raise ValueError("candidate groups must be unique")
    invalid = [
        group
        for group in normalized
        if not _GROUP_RE.fullmatch(group) or group not in contracts
    ]
    if invalid:
        raise ValueError(f"unsupported candidate groups: {invalid}")
    return normalized


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return _mapping(
            json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicates),
            str(path),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load candidate shard {path}: {exc}") from exc


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _portable_path(value: object, context: str) -> str:
    path = _string(value, context)
    candidate = Path(path)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or "\\" in path
        or candidate.suffix != ".ets"
    ):
        raise ValueError(f"{context} must be a portable relative .ets path")
    return path


def _sorted_unique_strings(value: object, context: str) -> list[str]:
    values = [_string(item, f"{context}[]") for item in _list(value, context)]
    if values != sorted(set(values)):
        raise ValueError(f"{context} must be sorted unique strings")
    return values


def _exact_fields(value: dict[str, Any], fields: Sequence[str], context: str) -> None:
    expected = set(fields)
    if set(value) != expected:
        raise ValueError(
            f"{context} fields mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _mapping(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _positive_int(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{context} must be an integer >= 1")
    return value


def _lower_hex(value: object, context: str, length: int) -> str:
    text = _string(value, context)
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{context} must be {length} lowercase hexadecimal characters")
    return text


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
