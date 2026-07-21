#!/usr/bin/env python3
"""Run the reportable VideoPlayer Diff -> Retrieval staging demonstration.

The script deliberately stops at ``EvidencePack``.  Rules, Prompt assembly and an
LLM review are not part of this executable example.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import os
import sys
from dataclasses import asdict
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

from arkts_code_reviewer.code_analysis import (
    ChangeAtomInput,
    ChangedFileInput,
    ChangeSet,
    CodeAnalyzer,
    CodeSourceRef,
    CodeSourceSnapshot,
    ReviewUnitSpan,
    normalize_change_set,
)
from arkts_code_reviewer.code_analysis.text_utils import extract_lines

if TYPE_CHECKING:
    from arkts_code_reviewer.retrieval.embeddings import FastEmbedProvider

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "inputs"
BASE_INPUT = INPUTS / "base.ets"
HEAD_INPUT = INPUTS / "head.ets"
DIFF_INPUT = INPUTS / "diff.patch"

SOURCE_REPOSITORY = "applications_app_samples"
SOURCE_REVISION = "8255a2987f70317cc3a2a4d46044c6b55f092bb3"
HEAD_REVISION = "synthetic-e2e-example-1-v1"
SOURCE_PATH = "code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets"
EXPECTED_BASE_HASH = "sha256:6d9f373ca3ea6cf1b0386f4e92dd9fe785cc421263e3d7c6500d5a35fb808c1a"
EXPECTED_HEAD_HASH = "sha256:8609950ac243718cf843ec7314c1725529bebb452d8d9d7a0e17acce076a5c60"
EXPECTED_BASE_LINES = 984
EXPECTED_HEAD_LINES = 1051
DEFAULT_ALIAS = "staging-knowledge-seed-v1"
EXPECTED_INDEX_VERSION = (
    "knowledge-index:sha256:aa792a335c07f03f740f8f0f790f16782c7dba93bc4c94af6666214b985e291a"
)

OUTPUT_NAMES = (
    "00_run_manifest.json",
    "01_change_set.json",
    "02_parser_base.json",
    "03_parser_head.json",
    "04_review_unit_build.json",
    "05_unit_fact_scopes.json",
    "06_feature_routing.json",
    "07_context_plan.json",
    "08_retrieval_request.json",
    "09_knowledge_index_summary.json",
    "10_evidence_pack.json",
    "11_assertions.json",
    "12_summary.json",
)


class DuplicateJsonKeyError(ValueError):
    """Raised when an optional fixture metadata file is not unambiguous JSON."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sha256(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def _line_count(content: str) -> int:
    return len(content.splitlines(keepends=True))


def _read_source(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"required E2E input is missing or is not a regular file: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"E2E input must use UTF-8: {path}") from exc


def _read_optional_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"optional E2E metadata is not a regular file: {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid optional E2E metadata {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"optional E2E metadata must be a JSON object: {path}")
    return value


def _json_text(payload: object) -> str:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _write_json(output_dir: Path, name: str, payload: object) -> Path:
    path = output_dir / name
    path.write_text(_json_text(payload), encoding="utf-8")
    return path


def _source_snapshot(content: str, revision: str) -> CodeSourceSnapshot:
    source_ref = CodeSourceRef.create(
        repository=SOURCE_REPOSITORY,
        revision=revision,
        path=SOURCE_PATH,
        content_hash=_sha256(content),
    )
    return CodeSourceSnapshot(source_ref=source_ref, content=content)


def _span(start: int, stop: int) -> ReviewUnitSpan:
    """Convert a zero-based half-open SequenceMatcher range to file lines."""

    if stop <= start:
        raise ValueError("cannot create a line span for an empty opcode side")
    return ReviewUnitSpan(start_line=start + 1, end_line=stop)


def _sequence_matcher_atoms(
    base_source: str,
    head_source: str,
) -> tuple[tuple[ChangeAtomInput, ...], tuple[dict[str, object], ...]]:
    """Translate deterministic line opcodes into the formal ChangeSet contract."""

    base_lines = base_source.splitlines(keepends=True)
    head_lines = head_source.splitlines(keepends=True)
    matcher = SequenceMatcher(
        None,
        base_lines,
        head_lines,
        autojunk=False,
    )
    atoms: list[ChangeAtomInput] = []
    opcode_contracts: list[dict[str, object]] = []
    for opcode, old_start, old_stop, new_start, new_stop in matcher.get_opcodes():
        if opcode == "equal":
            continue
        # Blank lines appear in the human-readable unified Diff, but they do not
        # have a meaningful declaration owner. Keep them in diff.patch and the
        # raw statistics while excluding them from formal changed-line ownership.
        added_new_lines = tuple(
            line for line in range(new_start + 1, new_stop + 1) if head_lines[line - 1].strip()
        )
        deleted_old_lines = tuple(
            line for line in range(old_start + 1, old_stop + 1) if base_lines[line - 1].strip()
        )
        opcode_contracts.append(
            {
                "kind": opcode,
                "old_span": (
                    None
                    if old_stop <= old_start
                    else {"start_line": old_start + 1, "end_line": old_stop}
                ),
                "new_span": (
                    None
                    if new_stop <= new_start
                    else {"start_line": new_start + 1, "end_line": new_stop}
                ),
                "added_new_lines": list(added_new_lines),
                "deleted_old_lines": list(deleted_old_lines),
                "raw_added_new_lines": list(range(new_start + 1, new_stop + 1)),
                "raw_deleted_old_lines": list(range(old_start + 1, old_stop + 1)),
            }
        )
        if opcode == "replace":
            atoms.append(
                ChangeAtomInput(
                    kind="replacement",
                    old_span=_span(old_start, old_stop),
                    new_span=_span(new_start, new_stop),
                    added_new_lines=added_new_lines,
                    deleted_old_lines=deleted_old_lines,
                )
            )
        elif opcode == "insert":
            atoms.append(
                ChangeAtomInput(
                    kind="addition",
                    old_span=None,
                    new_span=_span(new_start, new_stop),
                    added_new_lines=added_new_lines,
                )
            )
        elif opcode == "delete":
            atoms.append(
                ChangeAtomInput(
                    kind="deletion",
                    old_span=_span(old_start, old_stop),
                    new_span=None,
                    deleted_old_lines=deleted_old_lines,
                )
            )
        else:  # pragma: no cover - SequenceMatcher has only four opcode values.
            raise AssertionError(f"unexpected SequenceMatcher opcode: {opcode}")
    if not atoms:
        raise ValueError("base.ets and head.ets do not contain a source change")
    return tuple(atoms), tuple(opcode_contracts)


def _unified_diff(base_source: str, head_source: str) -> str:
    return "".join(
        difflib.unified_diff(
            base_source.splitlines(keepends=True),
            head_source.splitlines(keepends=True),
            fromfile=f"a/{SOURCE_PATH}",
            tofile=f"b/{SOURCE_PATH}",
        )
    )


def _database_url_from_environment() -> tuple[str, str]:
    for name in ("ARKTS_RETRIEVAL_DATABASE_URL", "DATABASE_URL"):
        value = os.environ.get(name)
        if value and value.strip():
            return value, name
    raise ValueError(
        "set ARKTS_RETRIEVAL_DATABASE_URL (or DATABASE_URL) before running; "
        "the database URL is accepted only through the environment and is never "
        "written to an E2E artifact"
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fixed VideoPlayer ChangeSet through Parser, ReviewUnit, "
            "Feature Routing, Context Planning and staging Retrieval"
        )
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts")
    parser.add_argument("--report-path", type=Path, default=ROOT / "REPORT.md")
    parser.add_argument(
        "--staging-alias",
        default=os.environ.get("ARKTS_RETRIEVAL_ALIAS", DEFAULT_ALIAS),
    )
    parser.add_argument(
        "--embedding-cache",
        type=Path,
        default=Path(
            os.environ.get(
                "ARKTS_FASTEMBED_CACHE",
                str(Path.home() / ".cache/arkts-code-reviewer/fastembed-code"),
            )
        ),
    )
    parser.add_argument("--embedding-batch-size", type=int, default=8)
    parser.add_argument("--embedding-threads", type=int, default=2)
    parser.add_argument("--code-context-budget", type=int, default=32768)
    parser.add_argument("--knowledge-token-budget", type=int, default=8192)
    return parser.parse_args(argv)


def _assertion(
    assertions: list[dict[str, object]],
    assertion_id: str,
    description: str,
    passed: bool,
    evidence: object,
) -> None:
    assertions.append(
        {
            "assertion_id": assertion_id,
            "description": description,
            "evidence": evidence,
            "passed": passed,
        }
    )
    if not passed:
        raise AssertionError(f"{assertion_id} failed: {description}; {evidence!r}")


def _index_summary(index: Any, *, alias: str, roundtrip_ok: bool) -> dict[str, object]:
    embedded_records = sum(record.embedding is not None for record in index.records)
    return {
        "schema_version": "e2e-knowledge-index-summary-v1",
        "staging_alias": alias,
        "index_version": index.index_version,
        "origin": index.origin,
        "production_eligible": index.origin == "publication",
        "published_build_id": index.published_build_id,
        "source_bundle_id": index.source_bundle_id,
        "feature_config_version": index.feature_config_version,
        "annotation_version": index.annotation_version,
        "catalog_version": index.catalog_version,
        "retrieval_version": index.retrieval_version,
        "retrieval_config_fingerprint": index.retrieval_config_fingerprint,
        "embedding_model": index.embedding_model,
        "embedding_version": index.embedding_version,
        "embedding_dimensions": index.embedding_dimensions,
        "record_count": len(index.records),
        "embedded_record_count": embedded_records,
        "draft_record_count": sum(record.clause.status == "Draft" for record in index.records),
        "baselined_record_count": sum(
            record.clause.status == "Baselined" for record in index.records
        ),
        "rule_ids": [record.clause.rule_id for record in index.records],
        "formal_loader_roundtrip": roundtrip_ok,
        "note": "109x768 vectors are intentionally not duplicated into this report artifact.",
    }


def _compact_id(value: str, keep: int = 16) -> str:
    prefix, separator, digest = value.rpartition(":")
    if not separator or len(digest) <= keep:
        return value
    return f"{prefix}:…{digest[-keep:]}"


def _cell(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, (list, tuple, set)):
        text = ", ".join(str(item) for item in value) or "—"
    else:
        text = str(value)
    # Knowledge text is external data. Render it as inert table text so links or
    # HTML embedded in a Clause cannot become active report markup.
    return (
        html.escape(text, quote=False)
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("|", "\\|")
        .replace("\n", "<br>")
    )


def _lines(value: list[int] | tuple[int, ...]) -> str:
    if not value:
        return "—"
    ranges: list[str] = []
    start = previous = value[0]
    for line in value[1:]:
        if line == previous + 1:
            previous = line
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = line
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


def _report(
    *,
    change_set: ChangeSet,
    analysis_result: Any,
    context_plan: Any,
    request: Any,
    index: Any,
    provider: FastEmbedProvider,
    evidence_pack: Any,
    assertions: list[dict[str, object]],
    base_source: str,
    head_source: str,
    alias: str,
    mutation_spec: dict[str, object],
    artifact_prefix: str,
) -> str:
    build = analysis_result.review_unit_build_result
    if build is None:  # pragma: no cover - analyze_change_set requires v3.
        raise AssertionError("ChangeSet analysis did not produce ReviewUnitBuildResult")
    parse_by_role: dict[str, Any] = {}
    role_by_source = {result.source_ref_id: result.source_role for result in build.file_results}
    for result in analysis_result.file_parse_results:
        role = role_by_source[result.analysis.source_ref.source_ref_id]
        parse_by_role[role] = result
    profiles = {
        profile.unit_id: profile for profile in analysis_result.feature_routing_result.units
    }
    scopes = {scope.unit_id: scope for scope in analysis_result.unit_fact_scopes}
    review_units = {unit.unit_id: unit for unit in analysis_result.review_units}
    clause_relations = [clause for unit in evidence_pack.units for clause in unit.clauses]
    distinct_rule_ids = {clause.rule_id for clause in clause_relations}
    unit_exact_relations = sum(
        any(match.scope == "unit_exact" for match in clause.matched_by)
        for clause in clause_relations
    )
    file_hint_relations = sum(
        any(match.scope == "file_hint" for match in clause.matched_by)
        for clause in clause_relations
    )
    vector_relations = sum(
        any(match.kind == "vector" for match in clause.matched_by) for clause in clause_relations
    )
    vector_similarities = sorted(
        clause.rank_detail.vector_similarity
        for clause in clause_relations
        if clause.rank_detail.vector_similarity is not None
    )
    vector_similarity_median = (
        vector_similarities[len(vector_similarities) // 2] if vector_similarities else None
    )
    low_similarity_relations = sum(value < 0.35 for value in vector_similarities)
    covered_dimensions = sum(len(unit.covered_dimension_ids) for unit in evidence_pack.units)
    uncovered_dimensions = sum(len(unit.uncovered_dimension_ids) for unit in evidence_pack.units)
    budget_exhausted_units = sum(
        any(item.code == "budget_exhausted" for item in unit.diagnostics)
        for unit in evidence_pack.units
    )

    diff_stats = mutation_spec["diff_stats"]
    formal_stats = mutation_spec["formal_change_stats"]
    if not isinstance(diff_stats, dict) or not isinstance(formal_stats, dict):
        raise ValueError("mutation_spec Diff statistics must be JSON objects")
    raw_added = diff_stats["added_new_lines"]
    raw_deleted = diff_stats["deleted_old_lines"]
    formal_added = formal_stats["assigned_added_new_lines"]
    formal_deleted = formal_stats["assigned_deleted_old_lines"]
    excluded_whitespace = formal_stats["excluded_whitespace_only_diff_lines"]
    report: list[str] = [
        "# ArkTS Code Reviewer 端到端示例 1",
        "",
        "> 固定的 984 行 VideoPlayer 文件，经人工构造约 100 行 Diff 后，真实执行 "
        "ChangeSet → Parser → ReviewUnit → Unit Facts → Feature Routing → Context "
        "Planning → PostgreSQL staging KnowledgeIndex → GPU 向量/精确融合检索 → "
        "EvidencePack。",
        "",
        "## 1. 汇报结论",
        "",
        f"- 链路完整性：**{'PASS' if all(item['passed'] for item in assertions) else 'FAIL'}**",
        "- 语义检索质量：**NOT QUALIFIED（当前不能作为高质量评审知识）**",
        f"- 输入规模：base `{_line_count(base_source)}` 行，head "
        f"`{_line_count(head_source)}` 行，原始 Diff `+{raw_added}/-{raw_deleted}`。",
        f"- 正式变更行：新增 `{formal_added}`、删除 `{formal_deleted}`；另有 "
        f"`{excluded_whitespace}` 行纯空白排版保留在补丁中，但不伪造代码 owner。",
        f"- Parser：base/head 均为 `{parse_by_role['base'].analysis.parser_quality.layer}` / "
        f"`{parse_by_role['head'].analysis.parser_quality.layer}`。",
        f"- ReviewUnit：`{len(analysis_result.review_units)}` 个 source-role Unit；未分配 "
        f"ChangeAtom `{len(build.unassigned_change_atom_ids)}` 个。",
        f"- Feature Routing：`{len(analysis_result.feature_routing_result.question_bindings)}` "
        "个 Unit/评审问题绑定。",
        f"- Context Plan：`{len(context_plan.bundles)}` 个 bundle，其中 "
        f"`{context_plan.budget_summary.dispatchable_bundles}` 个可派发。",
        f"- 知识索引：staging alias `{alias}`，`{len(index.records)}` 条 Draft Clause，"
        f"GPU provider `{provider.execution_provider}`。",
        f"- Retrieval：`{len(request.units)}` 个检索 Unit，返回 "
        f"`{sum(len(unit.clauses) for unit in evidence_pack.units)}` 条 Unit/Clause "
        f"证据关系；`production_eligible={str(evidence_pack.production_eligible).lower()}`。",
        f"- 检索结构信号：`{len(distinct_rule_ids)}` 条不同 Clause；"
        f"`{unit_exact_relations}` 条含 Unit 精确匹配，`{file_hint_relations}` 条含文件级提示，"
        f"`{vector_relations}` 条含向量召回。",
        f"- 向量相似度：中位数 `{_cell(vector_similarity_median)}`；"
        f"`{low_similarity_relations}/{len(vector_similarities)}` 条低于 `0.35`。",
        f"- 知识覆盖：要求的 dimension 实例 covered `{covered_dimensions}`、uncovered "
        f"`{uncovered_dimensions}`；`{budget_exhausted_units}/{len(evidence_pack.units)}` "
        "个 Unit 在知识 token 上限处截断候选。",
        "",
        "本结果证明的是：各阶段正式数据结构可以贯通并找到候选知识。它**不是最终代码评审结论**；"
        "本示例没有执行 Rules、Prompt 组装或 LLM。",
        "本样例也暴露了当前知识质量缺口：召回主要由宽泛的 `file_hints` 驱动，"
        "timer/network/AVSession 专项知识不足，不能把 67 条候选关系当成 67 条有效规范。",
        "",
        "## 2. 输入与可复现性",
        "",
        f"- 原仓库：`{SOURCE_REPOSITORY}`",
        f"- 固定 revision：`{SOURCE_REVISION}`",
        f"- 原始路径：`{SOURCE_PATH}`",
        f"- base SHA-256：`{EXPECTED_BASE_HASH}`",
        f"- synthetic head SHA-256：`{EXPECTED_HEAD_HASH}`",
        "- 文件：[base.ets](inputs/base.ets) · [head.ets](inputs/head.ets) · "
        "[完整 Diff](inputs/diff.patch) · [变更说明](inputs/mutation_spec.json) · "
        "[来源证明](inputs/provenance.json) · "
        "[ReviewUnit 人工 expected](inputs/expected_review_units.json)",
        "",
        "## 3. 链路与阶段产物",
        "",
        "```text",
        "base/head source",
        "  → SequenceMatcher(autojunk=False) / ChangeSet",
        "  → Parser L1 (base 与 head 各解析一次)",
        "  → ReviewUnitBuild v3 (base/head owner 分开保留)",
        "  → UnitFactScope (unit_exact 与 file_hints 分离)",
        "  → Feature Routing (Tags → Dimensions → Review Questions)",
        "  → Context Plan (Primary Unit / bundle / 代码预算)",
        "  → RetrievalRequest (精确信号 + 小型 semantic excerpt)",
        "  → PostgreSQL staging KnowledgeIndex + CUDA embedding",
        "  → Exact + Vector + RRF",
        "  → EvidencePack (候选知识，不是 Finding)",
        "```",
        "",
        "| 阶段 | 完整机器可读结果 |",
        "|---|---|",
        f"| 运行配置 | [{artifact_prefix}/00_run_manifest.json]"
        f"({artifact_prefix}/00_run_manifest.json) |",
        f"| ChangeSet | [{artifact_prefix}/01_change_set.json]"
        f"({artifact_prefix}/01_change_set.json) |",
        f"| Parser base | [{artifact_prefix}/02_parser_base.json]"
        f"({artifact_prefix}/02_parser_base.json) |",
        f"| Parser head | [{artifact_prefix}/03_parser_head.json]"
        f"({artifact_prefix}/03_parser_head.json) |",
        f"| ReviewUnit build | [{artifact_prefix}/04_review_unit_build.json]"
        f"({artifact_prefix}/04_review_unit_build.json) |",
        f"| Unit facts | [{artifact_prefix}/05_unit_fact_scopes.json]"
        f"({artifact_prefix}/05_unit_fact_scopes.json) |",
        f"| Feature Routing | [{artifact_prefix}/06_feature_routing.json]"
        f"({artifact_prefix}/06_feature_routing.json) |",
        f"| Context Plan | [{artifact_prefix}/07_context_plan.json]"
        f"({artifact_prefix}/07_context_plan.json) |",
        f"| RetrievalRequest | [{artifact_prefix}/08_retrieval_request.json]"
        f"({artifact_prefix}/08_retrieval_request.json) |",
        f"| KnowledgeIndex 摘要 | [{artifact_prefix}/09_knowledge_index_summary.json]"
        f"({artifact_prefix}/09_knowledge_index_summary.json) |",
        f"| EvidencePack | [{artifact_prefix}/10_evidence_pack.json]"
        f"({artifact_prefix}/10_evidence_pack.json) |",
        f"| 端到端断言 | [{artifact_prefix}/11_assertions.json]"
        f"({artifact_prefix}/11_assertions.json) |",
        f"| 汇总 | [{artifact_prefix}/12_summary.json]({artifact_prefix}/12_summary.json) |",
        "",
        "## 4. ChangeSet：Diff 被标准化成什么",
        "",
        f"`change_set_id`: `{change_set.change_set_id}`",
        "",
        "| # | kind | base 行 | head 行 | ChangeAtom ID |",
        "|---:|---|---|---|---|",
    ]
    for number, atom in enumerate(change_set.atoms, start=1):
        report.append(
            f"| {number} | {atom.kind} | {_lines(atom.deleted_old_lines)} | "
            f"{_lines(atom.added_new_lines)} | `{_compact_id(atom.atom_id)}` |"
        )

    report.extend(
        [
            "",
            "## 5. Parser：完整文件结构与事实",
            "",
            "Parser 对 base/head 各执行一次，行号全部是源文件 1-based 绝对行号。",
            "",
            "| side | layer | ERROR | missing | declarations | review regions | facts | warnings |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for role in ("base", "head"):
        result = parse_by_role[role]
        analysis = result.analysis
        quality = analysis.parser_quality
        report.append(
            f"| {role} | {quality.layer} | {quality.error_nodes} | "
            f"{quality.missing_nodes} | {len(analysis.declarations)} | "
            f"{len(analysis.review_regions)} | {len(analysis.fact_occurrences)} | "
            f"{_cell(quality.warnings)} |"
        )

    report.extend(
        [
            "",
            "## 6. ReviewUnit：Diff 最终切成哪些代码段",
            "",
            "同一个 replacement 会在 base 与 head 各形成 owner Unit，便于后续比较变更前后。"
            "`full_text` 的完整正文位于 04 JSON；下表展示身份、范围和归属。",
            "",
            "| side | kind / symbol | source span | context span | changed lines | "
            "atoms | reason | diagnostics |",
            "|---|---|---|---|---|---:|---|---|",
        ]
    )
    for unit in analysis_result.review_units:
        changed_lines = (
            unit.changed_old_lines if unit.source_role == "base" else unit.changed_new_lines
        )
        report.append(
            f"| {unit.source_role} | `{_cell(unit.unit_kind)}` / `{_cell(unit.unit_symbol)}` | "
            f"L{unit.source_span.start_line}-L{unit.source_span.end_line} | "
            f"L{unit.context_span.start_line}-L{unit.context_span.end_line} | "
            f"{_lines(changed_lines)} | {len(unit.change_atom_ids)} | "
            f"{unit.selection_reason} | "
            f"{_cell([item.code for item in unit.diagnostics])} |"
        )
        report.append(f"<!-- unit_id: {unit.unit_id} -->")

    report.extend(
        [
            "",
            "## 7. Unit Facts：代码段自身事实与文件提示分开",
            "",
            "`unit_exact` 是能定位到该 Unit span 的事实，可参与精确检索；`file_hints` 只是"
            "整文件提示，只能保守扩大路由，不能直接当 Finding 证据。",
            "",
            "| side / symbol | unit_exact APIs | calls | exact decorators | "
            "file_hints APIs | diagnostics |",
            "|---|---|---|---|---|---|",
        ]
    )
    for unit in analysis_result.review_units:
        scope = scopes[unit.unit_id]
        report.append(
            f"| {unit.source_role} / `{_cell(unit.unit_symbol)}` | "
            f"{_cell(scope.unit_exact.apis)} | {_cell(scope.unit_exact.calls)} | "
            f"{_cell(scope.unit_exact.decorators)} | {_cell(scope.file_hints.apis)} | "
            f"{_cell(scope.diagnostics)} |"
        )

    report.extend(
        [
            "",
            "## 8. Feature Routing：Tags、Dimensions 和评审问题",
            "",
            "| side / symbol | exact tags | routing tags | retrieval dimensions | "
            "routing dimensions | review questions |",
            "|---|---|---|---|---|---|",
        ]
    )
    for unit in analysis_result.review_units:
        profile = profiles[unit.unit_id]
        report.append(
            f"| {unit.source_role} / `{_cell(unit.unit_symbol)}` | "
            f"{_cell(profile.exact_tags)} | {_cell(profile.routing_tags)} | "
            f"{_cell(profile.retrieval_dimensions)} | "
            f"{_cell(profile.routing_dimensions)} | "
            f"{_cell(profile.review_question_ids)} |"
        )

    report.extend(
        [
            "",
            "## 9. Context Plan：哪些代码会进入后续评审上下文",
            "",
            f"代码预算 `{context_plan.budget_summary.limit}` tokens；Primary "
            f"`{context_plan.budget_summary.total_primary_tokens}`；Supporting "
            f"`{context_plan.budget_summary.total_supporting_tokens}`。",
            "",
            "| bundle | primary Units | questions | supporting | tokens | dispatch | diagnostics |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for bundle in context_plan.bundles:
        report.append(
            f"| `{_compact_id(bundle.bundle_id)}` | {len(bundle.primary_unit_ids)} | "
            f"{len(bundle.primary_question_bindings)} | "
            f"{len(bundle.supporting_segment_ids)} | {bundle.budget.total_tokens}/"
            f"{bundle.budget.limit} | {str(bundle.dispatch_allowed).lower()} | "
            f"{_cell([item.code for item in bundle.diagnostics])} |"
        )

    report.extend(
        [
            "",
            "本次 `supporting_segments=0`：它验证了所有直接变更 owner 的组织与预算，"
            "但没有证明相关未修改 helper/调用方的扩展召回能力。",
        ]
    )

    report.extend(
        [
            "",
            "## 10. RetrievalRequest：真正发给检索器的内容",
            "",
            "完整 ReviewUnit 不会被直接拿去做向量查询；检索器使用精确信号、路由结果和"
            "最多 16 行/1600 字符的 `semantic_code_excerpt`。完整字段见 08 JSON。",
            "",
        ]
    )
    for unit_request in request.units:
        unit = review_units[unit_request.unit_id]
        report.extend(
            [
                f"### {unit.source_role} · `{unit.unit_symbol}`",
                "",
                f"- Unit ID：`{unit_request.unit_id}`",
                f"- Review questions：`{_cell(unit_request.review_question_ids)}`",
                f"- Exact tags：`{_cell(unit_request.exact_tags)}`",
                f"- Retrieval dimensions：`{_cell(unit_request.retrieval_dimension_ids)}`",
                f"- Knowledge budget：`{unit_request.knowledge_token_budget}`",
                "- Semantic excerpt：",
                "",
                "```text",
                unit_request.semantic_code_excerpt or "<none>",
                "```",
                "",
            ]
        )

    report.extend(
        [
            "## 11. KnowledgeIndex：检索所用知识",
            "",
            f"- PostgreSQL alias：`{alias}`",
            f"- Index version：`{index.index_version}`",
            f"- Origin：`{index.origin}`",
            f"- Knowledge build：`{index.published_build_id}`",
            f"- Clause：`{len(index.records)}` 条，状态全部为 Draft",
            f"- Embedding：`{index.embedding_model}` / `{index.embedding_dimensions}` 维",
            f"- 实际执行 provider：`{provider.execution_provider}`",
            "- Production eligible：`false`",
            "",
            "为避免在汇报目录重复保存 109×768 个浮点数，09 JSON 只保存索引元数据、"
            "统计和 rule_id；运行时已对数据库加载出的完整 KnowledgeIndex 做正式 loader 往返校验。",
            "",
            "## 12. EvidencePack：每个 Unit 最后搜到的知识",
            "",
            "下列 Clause 是候选知识证据。`vector_rank` 表示向量召回名次；`matched_by` "
            "同时展示 API/Tag/Dimension 等精确匹配。完整正文、来源与分数见 10 JSON。",
            "",
        ]
    )
    for unit_evidence in evidence_pack.units:
        unit = review_units[unit_evidence.unit_id]
        report.extend(
            [
                f"### {unit.source_role} · `{unit.unit_symbol}`",
                "",
                f"- Unit ID：`{unit.unit_id}`",
                f"- Dimension coverage：covered `{_cell(unit_evidence.covered_dimension_ids)}`；"
                f"uncovered `{_cell(unit_evidence.uncovered_dimension_ids)}`",
                f"- Diagnostics：`{_cell([item.code for item in unit_evidence.diagnostics])}`",
                "",
                "| rank | rule_id | 规则正文 | matched_by | exact/vector rank | "
                "similarity | source |",
                "|---:|---|---|---|---|---:|---|",
            ]
        )
        for clause in unit_evidence.clauses:
            matches = [f"{match.kind}:{match.value} ({match.scope})" for match in clause.matched_by]
            report.append(
                f"| {clause.rank} | `{_cell(clause.rule_id)}` | {_cell(clause.text)} | "
                f"{_cell(matches)} | {clause.rank_detail.exact_rank}/"
                f"{clause.rank_detail.vector_rank} | "
                f"{_cell(clause.rank_detail.vector_similarity)} | "
                f"`{_cell(clause.source_ref.relative_path)}` |"
            )
        if not unit_evidence.clauses:
            report.append("| — | — | 本 Unit 没有召回知识 | — | — | — | — |")
        report.append("")

    report.extend(
        [
            "## 13. 自动校验结果",
            "",
            "| ID | 断言 | 结果 |",
            "|---|---|---|",
        ]
    )
    for item in assertions:
        report.append(
            f"| {item['assertion_id']} | {_cell(item['description'])} | "
            f"{'PASS' if item['passed'] else 'FAIL'} |"
        )

    report.extend(
        [
            "",
            "## 14. 如何解读本次准确性",
            "",
            "- **结构准确性已自动验证**：来源哈希、Parser 质量、ChangeAtom 归属、"
            f"ReviewUnit 人工 expected、源码切片、跨阶段 ID、GPU/索引身份和"
            f"正式 loader 共 {len(assertions)} 项断言通过。",
            f"- **本样例的检索覆盖不理想**：dimension coverage 为 "
            f"`{covered_dimensions}/{covered_dimensions + uncovered_dimensions}`；只有 "
            f"`{unit_exact_relations}/{len(clause_relations)}` 条候选关系含 Unit 精确匹配。",
            f"- **宽泛提示占主导**：`{file_hint_relations}/{len(clause_relations)}` 条关系含 "
            "`file_hint`，会把整文件的 state/lifecycle 等提示扩散到无关 Unit。",
            "- **人工抽查发现明显错域候选**：`removePlayerListeners` 召回 Tabs 的 "
            "`animationMode/onUnselected`，Slider timer 召回 Tabs `onGestureSwipe`，network "
            "代码召回 Sendable 约束。这些仅用于揭示问题，不作为正式 Precision 标注。",
            "- **语义准确率不能由本次运行单独给出**：这个样例没有人工标注“每个 Unit "
            "应该命中哪些 Clause”，所以不能伪造 Precision/Recall。需要为该样例增加人工或"
            "独立模型审阅的 relevant / irrelevant 标签后再计算。",
            "- `budget_exhausted` 表示候选知识被每 Unit 的 token 配额截断，不表示程序故障；"
            "本次 EvidencePack 的 `degraded=false`。",
            "",
            "## 15. 本示例没有证明什么",
            "",
            "- 没有执行 Rules，因此没有规则引擎 Finding。",
            "- 没有组装 Prompt，也没有调用 LLM，因此没有优点/问题/影响评审结论。",
            "- 没有运行 ArkTS 编译、类型检查或应用运行测试；Parser L1 干净不等于代码一定可编译。",
            "- 当前索引是 evaluation fixture，知识 Clause 为 Draft，不能当生产规范。",
            "- AVSession 专项知识覆盖仍有限；没有召回不代表代码没有问题。",
            "- EvidencePack 的 Clause 需要由后续评审模块结合具体代码判断是否适用，不能把"
            "“检索到了”直接写成“代码违规”。",
            "",
            "## 16. 交付前独立复核记录",
            "",
            "以下命令是在本目录提交前独立执行的，不属于 `run_e2e.py` 内部步骤。代码或"
            "知识索引变化后，必须重新执行并更新本节。",
            "",
            "| 检查 | 结果 |",
            "|---|---|",
            "| 全量 pytest | 712 passed，3 skipped |",
            "| 本样例静态合同测试 | 5/5 passed |",
            "| FileAnalysis / ChangeSet Golden | 15/15 · 14/14 |",
            "| ReviewUnit v2 / ContextPlan Golden | 16/16 · 16/16 |",
            "| Feature Routing Golden | 16/16，全指标 1.0 |",
            "| Retrieval Golden | 36/36，Recall@5 / Precision@5 / MRR 均 1.0 |",
            "| Parser v1 release gate | L1 15/15 perfect；R63 L0/L1 均 63/63 |",
            "| PostgreSQL live integration | 3/3 passed |",
            "| CUDA real-embedding 12-case gate | Recall@5 0.857143；Precision@5 "
            "0.692308；MRR 0.875；forbidden 0 |",
            "| 本样例确定性重跑 | 13 个 JSON artifact + REPORT.md 字节哈希不变 |",
            "| ruff / git diff --check | passed |",
            "",
            "注意：冻结 Golden 的 perfect 表示测试夹具上的算法合同成立；本次真实 109 条"
            "Draft 知识样例仍然是 NOT QUALIFIED，两者不能混为一谈。",
            "",
        ]
    )
    return "\n".join(report)


def run(argv: list[str] | None = None) -> dict[str, object]:
    args = _parse_args(argv)
    output_dir = args.output_dir.expanduser().absolute()
    report_path = args.report_path.expanduser().absolute()
    if output_dir.is_symlink():
        raise ValueError("output directory must not be a symlink")
    if report_path.is_symlink():
        raise ValueError("report path must not be a symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    incomplete_marker = output_dir / "RUN_INCOMPLETE"
    incomplete_marker.write_text(
        "The latest E2E attempt did not complete; do not treat older PASS artifacts "
        "as the current run.\n",
        encoding="utf-8",
    )

    base_source = _read_source(BASE_INPUT)
    head_source = _read_source(HEAD_INPUT)
    provenance = _read_optional_json(INPUTS / "provenance.json")
    mutation_spec = _read_optional_json(INPUTS / "mutation_spec.json")
    expected_review_units = _read_optional_json(INPUTS / "expected_review_units.json")
    if provenance is None or mutation_spec is None or expected_review_units is None:
        raise ValueError(
            "provenance.json, mutation_spec.json and expected_review_units.json "
            "are required E2E inputs"
        )
    if provenance.get("schema_version") != "e2e-source-provenance-v1":
        raise ValueError("unsupported or missing provenance schema_version")
    if mutation_spec.get("schema_version") != "e2e-mutation-spec-v1":
        raise ValueError("unsupported or missing mutation_spec schema_version")
    if (
        expected_review_units.get("schema_version") != "e2e-review-unit-expected-v1"
        or set(expected_review_units) != {"schema_version", "source_path", "units"}
        or expected_review_units.get("source_path") != SOURCE_PATH
        or not isinstance(expected_review_units.get("units"), list)
    ):
        raise ValueError("invalid expected_review_units.json contract")
    expected_provenance_keys = {
        "schema_version",
        "source_id",
        "repository_root_hint",
        "revision",
        "relative_path",
        "content_sha256",
        "line_count",
        "synthetic_head_revision",
        "synthetic_head_sha256",
        "synthetic_head_line_count",
    }
    expected_mutation_keys = {
        "schema_version",
        "purpose",
        "diff_stats",
        "formal_change_stats",
        "topics",
        "opcodes",
    }
    if set(provenance) != expected_provenance_keys:
        raise ValueError("provenance.json contains missing or unknown fields")
    if set(mutation_spec) != expected_mutation_keys:
        raise ValueError("mutation_spec.json contains missing or unknown fields")
    opcodes = mutation_spec.get("opcodes")
    expected_opcode_keys = {
        "kind",
        "old_span",
        "new_span",
        "added_new_lines",
        "deleted_old_lines",
        "raw_added_new_lines",
        "raw_deleted_old_lines",
    }
    if not isinstance(opcodes, list) or any(
        not isinstance(item, dict) or set(item) != expected_opcode_keys for item in opcodes
    ):
        raise ValueError("mutation_spec opcodes contain missing or unknown fields")
    base_hash = _sha256(base_source)
    head_hash = _sha256(head_source)
    if base_hash != EXPECTED_BASE_HASH or _line_count(base_source) != EXPECTED_BASE_LINES:
        raise ValueError(
            "inputs/base.ets provenance drift: expected fixed VideoPlayer revision "
            f"{SOURCE_REVISION}, hash {EXPECTED_BASE_HASH}, {EXPECTED_BASE_LINES} lines"
        )
    if head_hash != EXPECTED_HEAD_HASH or _line_count(head_source) != EXPECTED_HEAD_LINES:
        raise ValueError(
            "inputs/head.ets drift: rerun prepare_fixture.py and review the synthetic Diff "
            "before accepting a new E2E identity"
        )
    expected_provenance = {
        "source_id": SOURCE_REPOSITORY,
        "revision": SOURCE_REVISION,
        "relative_path": SOURCE_PATH,
        "content_sha256": base_hash,
        "line_count": EXPECTED_BASE_LINES,
        "synthetic_head_revision": HEAD_REVISION,
        "synthetic_head_sha256": head_hash,
        "synthetic_head_line_count": EXPECTED_HEAD_LINES,
    }
    provenance_drift = {
        key: {"expected": expected, "actual": provenance.get(key)}
        for key, expected in expected_provenance.items()
        if provenance.get(key) != expected
    }
    if provenance_drift:
        raise ValueError(f"inputs/provenance.json drift: {provenance_drift}")

    rendered_diff = _unified_diff(base_source, head_source)
    if _read_source(DIFF_INPUT) != rendered_diff:
        raise ValueError("inputs/diff.patch does not match base.ets and head.ets")

    base_snapshot = _source_snapshot(base_source, SOURCE_REVISION)
    head_snapshot = _source_snapshot(head_source, HEAD_REVISION)
    atoms, opcode_contracts = _sequence_matcher_atoms(base_source, head_source)
    formal_added = sum(len(atom.added_new_lines) for atom in atoms)
    formal_deleted = sum(len(atom.deleted_old_lines) for atom in atoms)
    raw_added = sum(
        1
        for line in rendered_diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    raw_deleted = sum(
        1
        for line in rendered_diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    actual_diff_stats = {
        "added_new_lines": raw_added,
        "deleted_old_lines": raw_deleted,
        "total_diff_lines": raw_added + raw_deleted,
    }
    actual_formal_stats = {
        "assigned_added_new_lines": formal_added,
        "assigned_deleted_old_lines": formal_deleted,
        "assigned_total_lines": formal_added + formal_deleted,
        "excluded_whitespace_only_diff_lines": (
            raw_added + raw_deleted - formal_added - formal_deleted
        ),
    }
    if mutation_spec.get("diff_stats") != actual_diff_stats:
        raise ValueError("mutation_spec raw Diff statistics do not match diff.patch")
    if mutation_spec.get("formal_change_stats") != actual_formal_stats:
        raise ValueError("mutation_spec formal changed-line statistics drifted")
    if mutation_spec.get("opcodes") != list(opcode_contracts):
        raise ValueError("mutation_spec opcode contract drifted from base/head source")
    change_set = normalize_change_set(
        repository=SOURCE_REPOSITORY,
        base_revision=SOURCE_REVISION,
        head_revision=HEAD_REVISION,
        files=(
            ChangedFileInput(
                status="modified",
                old_path=SOURCE_PATH,
                new_path=SOURCE_PATH,
                old_snapshot=base_snapshot,
                new_snapshot=head_snapshot,
                atoms=atoms,
            ),
        ),
    )
    change_set.validate()
    snapshots_by_id = {
        base_snapshot.source_ref.source_ref_id: base_snapshot,
        head_snapshot.source_ref.source_ref_id: head_snapshot,
    }

    analyzer = CodeAnalyzer()
    analysis_result = analyzer.analyze_change_set(change_set, snapshots_by_id)
    build_result = analysis_result.review_unit_build_result
    if build_result is None:
        raise AssertionError("analyze_change_set did not produce ReviewUnitBuildResult")
    build_result.validate()
    context_plan = analyzer.plan_context(
        analysis_result,
        source_snapshots=(base_snapshot, head_snapshot),
        code_context_budget=args.code_context_budget,
    )
    context_plan.__post_init__()

    # Keep the ChangeSet -> ContextPlan fixture importable by offline Tag pilots.
    # Retrieval's PostgreSQL/FastEmbed dependencies are needed only after this boundary.
    from arkts_code_reviewer.retrieval import (
        RetrievalService,
        TargetPlatform,
        build_retrieval_request,
        load_evidence_pack,
        load_knowledge_index,
        load_retrieval_request,
    )
    from arkts_code_reviewer.retrieval.embeddings import FastEmbedProvider
    from arkts_code_reviewer.retrieval.postgres import PostgresIndexStore

    database_url, database_url_source = _database_url_from_environment()
    if not args.staging_alias.startswith("staging-"):
        raise ValueError("this evaluation example requires a staging-* alias")
    store = PostgresIndexStore(database_url)
    resolved_index_version = store.resolve_alias(args.staging_alias)
    if resolved_index_version != EXPECTED_INDEX_VERSION:
        raise ValueError(
            "staging alias drifted from the reviewed E2E KnowledgeIndex: "
            f"expected={EXPECTED_INDEX_VERSION!r}, actual={resolved_index_version!r}"
        )
    index = store.load(resolved_index_version)
    index_raw = index.model_dump_json()
    index_roundtrip = load_knowledge_index(index_raw)
    if index_roundtrip != index:
        raise AssertionError("KnowledgeIndex formal loader round-trip changed content")
    if (
        index.embedding_model is None
        or index.embedding_dimensions is None
        or index.embedding_version is None
    ):
        raise ValueError("staging index does not contain a complete vector index")

    provider = FastEmbedProvider(
        model_id=index.embedding_model,
        dimensions=index.embedding_dimensions,
        cache_dir=args.embedding_cache,
        local_files_only=True,
        execution_device="cuda",
        batch_size=args.embedding_batch_size,
        threads=args.embedding_threads,
    )
    if provider.version != index.embedding_version:
        raise ValueError(
            "FastEmbed provider does not match the immutable staging index: "
            f"runtime={provider.version!r}, index={index.embedding_version!r}"
        )

    request = build_retrieval_request(
        analysis_result,
        context_plan,
        target_platform=TargetPlatform(
            release="OpenHarmony-5.0",
            api_level=12,
            language_mode="ArkTS",
        ),
        resolved_index_version=resolved_index_version,
        knowledge_token_budget=args.knowledge_token_budget,
    )
    request_roundtrip = load_retrieval_request(request.model_dump_json())
    if request_roundtrip != request:
        raise AssertionError("RetrievalRequest formal loader round-trip changed content")
    evidence_pack = RetrievalService(
        index,
        embedding_provider=provider,
        allow_evaluation_fixture=True,
    ).retrieve(request)
    evidence_roundtrip = load_evidence_pack(evidence_pack.model_dump_json())
    if evidence_roundtrip != evidence_pack:
        raise AssertionError("EvidencePack formal loader round-trip changed content")

    assertions: list[dict[str, object]] = []
    _assertion(
        assertions,
        "A00",
        "固定来源、base/head 内容哈希和原始/正式 Diff 统计均无漂移",
        not provenance_drift
        and mutation_spec.get("diff_stats") == actual_diff_stats
        and mutation_spec.get("formal_change_stats") == actual_formal_stats,
        {
            "source_revision": SOURCE_REVISION,
            "base_content_hash": base_hash,
            "head_content_hash": head_hash,
            "raw_diff_stats": actual_diff_stats,
            "formal_change_stats": actual_formal_stats,
        },
    )
    parser_layers = [
        result.analysis.parser_quality.layer for result in analysis_result.file_parse_results
    ]
    parser_quality = [
        {
            "layer": result.analysis.parser_quality.layer,
            "error_nodes": result.analysis.parser_quality.error_nodes,
            "missing_nodes": result.analysis.parser_quality.missing_nodes,
            "warnings": list(result.analysis.parser_quality.warnings),
        }
        for result in analysis_result.file_parse_results
    ]
    _assertion(
        assertions,
        "A01",
        "base/head Parser 都实际使用 L1，且无 ERROR、missing 或 warning",
        parser_layers == ["L1", "L1"]
        and all(
            item["error_nodes"] == 0 and item["missing_nodes"] == 0 and not item["warnings"]
            for item in parser_quality
        ),
        {"parser_quality": parser_quality},
    )
    _assertion(
        assertions,
        "A02",
        "ChangeSet 身份贯穿 AnalysisResult、ReviewUnitBuild 和 ContextPlan",
        (
            analysis_result.change_set == change_set
            and build_result.change_set_id == change_set.change_set_id
            and context_plan.change_set_id == change_set.change_set_id
        ),
        {
            "change_set_id": change_set.change_set_id,
            "review_unit_change_set_id": build_result.change_set_id,
            "context_plan_change_set_id": context_plan.change_set_id,
        },
    )
    _assertion(
        assertions,
        "A03",
        "所有 ChangeAtom 在 base/head 适用侧均被分配给 ReviewUnit",
        not build_result.unassigned_change_atom_ids
        and all(not result.unassigned_change_atom_ids for result in build_result.file_results),
        {
            "build_unassigned_change_atom_ids": build_result.unassigned_change_atom_ids,
            "file_unassigned": {
                f"{result.source_role}:{result.path}": result.unassigned_change_atom_ids
                for result in build_result.file_results
            },
        },
    )
    source_by_id = {
        base_snapshot.source_ref.source_ref_id: base_source,
        head_snapshot.source_ref.source_ref_id: head_source,
    }
    slice_failures: list[str] = []
    changed_line_failures: list[str] = []
    for unit in analysis_result.review_units:
        source = source_by_id[unit.source_ref_id]
        expected = extract_lines(
            source,
            unit.context_span.start_line,
            unit.context_span.end_line,
        )
        if unit.full_text != expected:
            slice_failures.append(unit.unit_id)
        changed = unit.changed_old_lines if unit.source_role == "base" else unit.changed_new_lines
        if any(
            line < unit.context_span.start_line or line > unit.context_span.end_line
            for line in changed
        ):
            changed_line_failures.append(unit.unit_id)
    _assertion(
        assertions,
        "A04",
        "每个 ReviewUnit.full_text 严格等于 context_span 对应源码切片",
        not slice_failures,
        {"failed_unit_ids": slice_failures},
    )
    _assertion(
        assertions,
        "A05",
        "每个 assigned changed line 都位于 Unit context_span 内",
        not changed_line_failures,
        {"failed_unit_ids": changed_line_failures},
    )
    unit_ids = [unit.unit_id for unit in analysis_result.review_units]
    _assertion(
        assertions,
        "A06",
        "ReviewUnit 身份唯一且 UnitFactScope/FeatureProfile 按 unit_id 完整覆盖",
        (
            len(unit_ids) == len(set(unit_ids))
            and {scope.unit_id for scope in analysis_result.unit_fact_scopes} == set(unit_ids)
            and {profile.unit_id for profile in analysis_result.feature_routing_result.units}
            == set(unit_ids)
        ),
        {"review_unit_count": len(unit_ids)},
    )
    actual_review_unit_projection = [
        {
            "source_role": unit.source_role,
            "unit_kind": unit.unit_kind,
            "unit_symbol": unit.unit_symbol,
            "source_span": {
                "start_line": unit.source_span.start_line,
                "end_line": unit.source_span.end_line,
            },
            "context_span": {
                "start_line": unit.context_span.start_line,
                "end_line": unit.context_span.end_line,
            },
            "changed_lines": (
                unit.changed_old_lines if unit.source_role == "base" else unit.changed_new_lines
            ),
            "selection_reason": unit.selection_reason,
        }
        for unit in analysis_result.review_units
    ]
    _assertion(
        assertions,
        "A06-GOLDEN",
        "ReviewUnit owner、span、changed lines、reason 和输出顺序匹配人工 expected",
        actual_review_unit_projection == expected_review_units["units"],
        {
            "expected_unit_count": len(expected_review_units["units"]),
            "actual_unit_count": len(actual_review_unit_projection),
            "expected_file": "inputs/expected_review_units.json",
        },
    )
    routed_bindings = {
        (item.primary_unit_id, item.review_question_id)
        for item in analysis_result.feature_routing_result.question_bindings
    }
    planned_bindings = {
        (item.primary_unit_id, item.review_question_id)
        for item in context_plan.primary_question_bindings
    }
    _assertion(
        assertions,
        "A07",
        "ContextPlan 保留全部 Feature Routing 问题绑定且没有阻塞变更",
        routed_bindings == planned_bindings and not context_plan.blocking_change_ids,
        {
            "routed_binding_count": len(routed_bindings),
            "planned_binding_count": len(planned_bindings),
            "blocking_change_ids": list(context_plan.blocking_change_ids),
        },
    )
    _assertion(
        assertions,
        "A08",
        "PostgreSQL alias 解析到人工冻结的不可变 staging index",
        request.index_version
        == index.index_version
        == resolved_index_version
        == EXPECTED_INDEX_VERSION,
        {
            "alias": args.staging_alias,
            "expected_index_version": EXPECTED_INDEX_VERSION,
            "resolved_index_version": index.index_version,
        },
    )
    embedded_records = sum(record.embedding is not None for record in index.records)
    _assertion(
        assertions,
        "A09",
        "KnowledgeIndex 向量完整，且运行时 GPU provider 与索引版本完全匹配",
        (
            provider.execution_provider == "CUDAExecutionProvider"
            and provider.version == index.embedding_version
            and embedded_records == len(index.records)
        ),
        {
            "execution_provider": provider.execution_provider,
            "embedded_record_count": embedded_records,
            "record_count": len(index.records),
        },
    )
    evidence_unit_ids = [unit.unit_id for unit in evidence_pack.units]
    request_unit_ids = [unit.unit_id for unit in request.units]
    _assertion(
        assertions,
        "A10",
        "RetrievalRequest 与 EvidencePack 按 unit_id 和 request_id 保持身份对齐",
        (evidence_pack.request_id == request.request_id and evidence_unit_ids == request_unit_ids),
        {
            "request_id": request.request_id,
            "evidence_request_id": evidence_pack.request_id,
            "request_unit_count": len(request_unit_ids),
            "evidence_unit_count": len(evidence_unit_ids),
        },
    )
    vector_matches = sum(
        match.kind == "vector"
        for unit in evidence_pack.units
        for clause in unit.clauses
        for match in clause.matched_by
    )
    vector_ranked_clauses = sum(
        clause.rank_detail.vector_rank is not None
        for unit in evidence_pack.units
        for clause in unit.clauses
    )
    _assertion(
        assertions,
        "A11",
        "真实 EvidencePack 至少包含一个向量召回结果",
        vector_matches > 0 and vector_ranked_clauses > 0,
        {
            "vector_match_count": vector_matches,
            "vector_ranked_clause_count": vector_ranked_clauses,
        },
    )
    _assertion(
        assertions,
        "A12",
        "evaluation fixture 与 EvidencePack 均明确不可用于生产",
        index.origin == "evaluation_fixture"
        and evidence_pack.index_origin == "evaluation_fixture"
        and evidence_pack.production_eligible is False
        and all(record.clause.status == "Draft" for record in index.records),
        {
            "index_origin": index.origin,
            "evidence_origin": evidence_pack.index_origin,
            "production_eligible": evidence_pack.production_eligible,
        },
    )
    _assertion(
        assertions,
        "A13",
        "KnowledgeIndex、RetrievalRequest、EvidencePack 正式 loader 往返无漂移",
        (
            index_roundtrip == index
            and request_roundtrip == request
            and evidence_roundtrip == evidence_pack
        ),
        {"knowledge_index": True, "retrieval_request": True, "evidence_pack": True},
    )

    parse_by_source = {
        result.analysis.source_ref.source_ref_id: result
        for result in analysis_result.file_parse_results
    }
    base_parse = parse_by_source[base_snapshot.source_ref.source_ref_id]
    head_parse = parse_by_source[head_snapshot.source_ref.source_ref_id]
    manifest = {
        "schema_version": "e2e-run-manifest-v1",
        "status": "pass",
        "semantic_retrieval_quality_status": "not_qualified",
        "scope": "ChangeSet through Retrieval EvidencePack",
        "excluded_stages": ["Rules", "Prompt", "LLM review"],
        "source": {
            "repository": SOURCE_REPOSITORY,
            "revision": SOURCE_REVISION,
            "path": SOURCE_PATH,
            "base_content_hash": base_hash,
            "base_line_count": _line_count(base_source),
            "head_revision": HEAD_REVISION,
            "head_content_hash": head_hash,
            "head_line_count": _line_count(head_source),
        },
        "fixture_provenance": provenance,
        "mutation_spec": mutation_spec,
        "review_unit_expected_file": "inputs/expected_review_units.json",
        "runtime": {
            "python": sys.version.split()[0],
            "database_url_source": database_url_source,
            "database_url_persisted": False,
            "staging_alias": args.staging_alias,
            "embedding_cache_configured": True,
            "embedding_cache_path_persisted": False,
            "embedding_device": "cuda",
            "embedding_execution_provider": provider.execution_provider,
            "embedding_batch_size": provider.batch_size,
            "embedding_threads": provider.threads,
            "code_context_budget": args.code_context_budget,
            "knowledge_token_budget": args.knowledge_token_budget,
        },
        "output_files": list(OUTPUT_NAMES),
    }
    index_summary = _index_summary(
        index,
        alias=args.staging_alias,
        roundtrip_ok=index_roundtrip == index,
    )
    assertions_payload = {
        "schema_version": "e2e-assertions-v1",
        "status": "pass",
        "passed": len(assertions),
        "failed": 0,
        "assertions": assertions,
    }
    summary = {
        "schema_version": "e2e-summary-v1",
        "status": "pass",
        "chain_integrity_status": "pass",
        "semantic_retrieval_quality_status": "not_qualified",
        "change_set_id": change_set.change_set_id,
        "change_atom_count": len(change_set.atoms),
        "raw_diff_stats": actual_diff_stats,
        "formal_change_stats": actual_formal_stats,
        "parser_layers": parser_layers,
        "review_unit_count": len(analysis_result.review_units),
        "unassigned_change_atom_count": len(build_result.unassigned_change_atom_ids),
        "feature_profile_count": len(analysis_result.feature_routing_result.units),
        "review_question_binding_count": len(routed_bindings),
        "context_bundle_count": len(context_plan.bundles),
        "dispatchable_context_bundle_count": (context_plan.budget_summary.dispatchable_bundles),
        "retrieval_request_unit_count": len(request.units),
        "evidence_unit_count": len(evidence_pack.units),
        "evidence_clause_relation_count": sum(len(unit.clauses) for unit in evidence_pack.units),
        "distinct_recalled_rule_ids": sorted(
            {clause.rule_id for unit in evidence_pack.units for clause in unit.clauses}
        ),
        "vector_match_count": vector_matches,
        "unit_exact_clause_relation_count": sum(
            any(match.scope == "unit_exact" for match in clause.matched_by)
            for unit in evidence_pack.units
            for clause in unit.clauses
        ),
        "file_hint_clause_relation_count": sum(
            any(match.scope == "file_hint" for match in clause.matched_by)
            for unit in evidence_pack.units
            for clause in unit.clauses
        ),
        "covered_dimension_instance_count": sum(
            len(unit.covered_dimension_ids) for unit in evidence_pack.units
        ),
        "uncovered_dimension_instance_count": sum(
            len(unit.uncovered_dimension_ids) for unit in evidence_pack.units
        ),
        "budget_exhausted_unit_count": sum(
            any(item.code == "budget_exhausted" for item in unit.diagnostics)
            for unit in evidence_pack.units
        ),
        "index_origin": index.origin,
        "production_eligible": evidence_pack.production_eligible,
        "degraded": evidence_pack.degraded,
        "request_id": request.request_id,
        "evidence_pack_id": evidence_pack.evidence_pack_id,
        "report": "REPORT.md",
    }

    report = _report(
        change_set=change_set,
        analysis_result=analysis_result,
        context_plan=context_plan,
        request=request,
        index=index,
        provider=provider,
        evidence_pack=evidence_pack,
        assertions=assertions,
        base_source=base_source,
        head_source=head_source,
        alias=args.staging_alias,
        mutation_spec=mutation_spec,
        artifact_prefix=Path(os.path.relpath(output_dir, report_path.parent)).as_posix(),
    )
    with TemporaryDirectory(prefix=".e2e-staging-", dir=output_dir.parent) as temp_name:
        staging_dir = Path(temp_name)
        _write_json(staging_dir, "00_run_manifest.json", manifest)
        _write_json(staging_dir, "01_change_set.json", change_set.to_dict())
        _write_json(staging_dir, "02_parser_base.json", base_parse.to_dict())
        _write_json(staging_dir, "03_parser_head.json", head_parse.to_dict())
        _write_json(staging_dir, "04_review_unit_build.json", asdict(build_result))
        _write_json(
            staging_dir,
            "05_unit_fact_scopes.json",
            {
                "schema_version": "unit-fact-scopes-v1",
                "scopes": [scope.to_dict() for scope in analysis_result.unit_fact_scopes],
            },
        )
        _write_json(
            staging_dir,
            "06_feature_routing.json",
            analysis_result.feature_routing_result.to_dict(),
        )
        _write_json(staging_dir, "07_context_plan.json", context_plan.to_dict())
        request_path = _write_json(
            staging_dir,
            "08_retrieval_request.json",
            request.model_dump(mode="json"),
        )
        _write_json(staging_dir, "09_knowledge_index_summary.json", index_summary)
        evidence_path = _write_json(
            staging_dir,
            "10_evidence_pack.json",
            evidence_pack.model_dump(mode="json"),
        )
        _write_json(staging_dir, "11_assertions.json", assertions_payload)
        _write_json(staging_dir, "12_summary.json", summary)
        staged_report = staging_dir / "REPORT.md"
        staged_report.write_text(report, encoding="utf-8")

        # Validate the exact bytes before replacing any previously successful run.
        if load_retrieval_request(request_path.read_bytes()) != request:
            raise AssertionError("written RetrievalRequest failed loader round-trip")
        if load_evidence_pack(evidence_path.read_bytes()) != evidence_pack:
            raise AssertionError("written EvidencePack failed loader round-trip")
        for name in OUTPUT_NAMES:
            os.replace(staging_dir / name, output_dir / name)
        os.replace(staged_report, report_path)
    incomplete_marker.unlink()
    return summary


def main(argv: list[str] | None = None) -> int:
    try:
        summary = run(argv)
    except (AssertionError, OSError, RuntimeError, ValueError) as exc:
        print(f"E2E example failed: {exc}", file=sys.stderr)
        return 1
    print(_json_text(summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
