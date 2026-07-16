from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.feature_routing_validation import (
    tag_truth_v2_near_duplicate as near_duplicate_core,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    bytes_hash,
    canonical_hash,
    canonical_json,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate import (
    RatioThreshold,
    ReferenceDocument,
    ReferenceInventorySummary,
    ReferenceRole,
    ScannedReferenceInventory,
    TagTruthV2NearDuplicatePolicy,
    TagTruthV2NearDuplicateVerification,
    build_tag_truth_v2_near_duplicate_verification,
    load_tag_truth_v2_near_duplicate_policy,
    load_tag_truth_v2_near_duplicate_verification,
    near_duplicate_policy_payload_with_fingerprint,
    parse_tag_truth_v2_near_duplicate_policy,
    parse_tag_truth_v2_near_duplicate_verification,
    scan_pinned_git_reference_inventory,
    tokenize_arkts_like,
    verify_tag_truth_v2_near_duplicate_verification,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_provenance import (
    TagTruthV2ProvenanceVerification,
    build_tag_truth_v2_provenance_verification,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review import (
    TagTruthV2Consensus,
    TagTruthV2ReviewReceipt,
    build_tag_truth_v2_consensus,
    seal_tag_truth_v2_review_receipt_payload,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    DevelopmentTruthExclusionSnapshot,
    TagTruthV2ReviewPacket,
    TagTruthV2Selection,
    build_tag_truth_v2_review_packet,
    verify_tag_truth_v2_development_exclusions,
    verify_tag_truth_v2_selection_checkout,
    verify_tag_truth_v2_selection_exposure,
)
from tests import test_tag_truth_v2_provenance as provenance_test_support

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "tests/evaluation/tag_truth_v2/near_duplicate_shadow_policy_v1.json"
CLI = ROOT / "tools/screen_tag_truth_v2_near_duplicates.py"
POLICY_RELATIVE_PATH = "tests/evaluation/tag_truth_v2/near_duplicate_shadow_policy_v1.json"


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-c", "core.commitGraph=false", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _write(root: Path, relative_path: str, text: str | bytes) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def _init_git_repository(root: Path, *, remote: str = "https://example.invalid/source.git") -> None:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Near Duplicate Test")
    _git(root, "config", "user.email", "near-duplicate@example.invalid")
    _git(root, "remote", "add", "origin", remote)


def _commit_all(root: Path, message: str) -> tuple[str, str]:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^{tree}")


@dataclass(frozen=True)
class _NearDuplicateCliChain:
    project_root: Path
    source_root: Path
    seal_revision: str
    artifact_paths: tuple[Path, Path, Path, Path, Path]
    provenance_path: Path
    report: TagTruthV2ProvenanceVerification
    selection: TagTruthV2Selection
    packet: TagTruthV2ReviewPacket
    consensus: TagTruthV2Consensus
    development_truth: DevelopmentTruthExclusionSnapshot


@dataclass(frozen=True)
class _InventoryFixture:
    root: Path
    revision: str
    tree_id: str
    inventory: ScannedReferenceInventory


def _receipt_with_spans(
    packet: TagTruthV2ReviewPacket,
    *,
    round_id: str,
    reviewer_id: str,
    spans: tuple[tuple[int, int], tuple[int, int]],
    routing_disagreement: bool = False,
) -> TagTruthV2ReviewReceipt:
    receipt = provenance_test_support._receipt(
        packet,
        round_id=round_id,
        reviewer_id=reviewer_id,
        routing_disagreement=routing_disagreement,
    )
    payload = receipt.model_dump(mode="json", exclude={"receipt_id"})
    decisions = payload["decisions"]
    assert isinstance(decisions, list)
    for decision, (start_line, end_line) in zip(decisions, spans, strict=True):
        assert isinstance(decision, dict)
        review_unit = decision["review_unit"]
        exact = decision["exact"]
        assert isinstance(review_unit, dict)
        assert isinstance(exact, dict)
        review_unit["source_span"] = {"start_line": start_line, "end_line": end_line}
        exact["evidence_lines"] = [start_line]
    return seal_tag_truth_v2_review_receipt_payload(payload)


def _build_near_duplicate_cli_chain(
    tmp_path: Path,
    *,
    unresolved: bool = False,
    hostile_import_shadows: bool = False,
    mutate_runtime_after_candidate: str | None = None,
    selected_texts: tuple[str, str] | None = None,
    unit_spans: tuple[tuple[int, int], tuple[int, int]] | None = None,
) -> _NearDuplicateCliChain:
    source = provenance_test_support._build_source_repository(tmp_path / "source")
    if selected_texts is not None:
        for selected_path, text in zip(source.selected_paths, selected_texts, strict=True):
            _write(source.root, selected_path, text)
        selection_revision, selection_tree_id = _commit_all(
            source.root,
            "custom near-duplicate selected sources",
        )
        source = replace(
            source,
            selection_revision=selection_revision,
            selection_tree_id=selection_tree_id,
            selected_texts=selected_texts,
        )
    project_root = tmp_path / "project"
    _init_git_repository(project_root, remote="https://example.invalid/project.git")

    shutil.copytree(
        ROOT / "src",
        project_root / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    for tool_name in (
        "tag_truth_v2_seal_preflight.py",
        "verify_tag_truth_v2_git_seal.py",
        "tag_truth_v2_near_duplicate_preflight.py",
        "screen_tag_truth_v2_near_duplicates.py",
    ):
        source_path = ROOT / "tools" / tool_name
        destination = project_root / "tools" / tool_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
    _write(
        project_root,
        "tests/evaluation/tag_retrieval/manifest.json",
        (
            json.dumps(
                source.development_manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ),
    )
    _write(project_root, POLICY_RELATIVE_PATH, POLICY_PATH.read_bytes())
    _write(project_root, "candidate.txt", "frozen candidate\n")
    _write(project_root, "inside-provenance.json", "{}\n")
    if hostile_import_shadows:
        hostile = "raise RuntimeError('hostile local import shadow loaded')\n"
        for relative_path in ("pydantic.py", "tools/pydantic.py", "tools/json.py"):
            _write(project_root, relative_path, hostile)
    candidate_commit, _ = _commit_all(
        project_root,
        "candidate freeze with duplicate-screen runtime",
    )
    if mutate_runtime_after_candidate is not None:
        runtime_path = project_root / mutate_runtime_after_candidate
        runtime_path.write_text(
            runtime_path.read_text(encoding="utf-8") + "\n# post-candidate semantic drift\n",
            encoding="utf-8",
        )
        _commit_all(project_root, "forged post-candidate screening drift")

    selection = provenance_test_support._selection(source, candidate_commit=candidate_commit)
    verify_tag_truth_v2_development_exclusions(selection, source.development_truth, source.root)
    verify_tag_truth_v2_selection_exposure(selection, source.root)
    checkout = verify_tag_truth_v2_selection_checkout(selection, source.root)
    packet = build_tag_truth_v2_review_packet(selection, checkout)
    if unit_spans is None:
        first = provenance_test_support._receipt(
            packet,
            round_id="round-a",
            reviewer_id="reviewer-a",
        )
        second = provenance_test_support._receipt(
            packet,
            round_id="round-b",
            reviewer_id="reviewer-b",
            routing_disagreement=unresolved,
        )
    else:
        first = _receipt_with_spans(
            packet,
            round_id="round-a",
            reviewer_id="reviewer-a",
            spans=unit_spans,
        )
        second = _receipt_with_spans(
            packet,
            round_id="round-b",
            reviewer_id="reviewer-b",
            spans=unit_spans,
            routing_disagreement=unresolved,
        )
    consensus = build_tag_truth_v2_consensus(packet, (first, second))

    artifact_paths = tuple(
        project_root / path
        for path in (
            "artifacts/selection.json",
            "artifacts/packet.json",
            "artifacts/receipt-a.json",
            "artifacts/receipt-b.json",
            "artifacts/consensus.json",
        )
    )
    for artifact_path, value in zip(
        artifact_paths,
        (selection, packet, first, second, consensus),
        strict=True,
    ):
        provenance_test_support._write_json(artifact_path, value.model_dump(mode="json"))
    _, _ = _commit_all(project_root, "seal reviewed artifacts")
    seal_revision = _git(project_root, "rev-parse", "HEAD")
    seal_tree_id = _git(project_root, "rev-parse", "HEAD^{tree}")
    artifacts = tuple(
        provenance_test_support._capture(project_root, seal_revision, role, path)
        for role, path in zip(
            ("selection", "packet", "receipt", "receipt", "consensus"),
            artifact_paths,
            strict=True,
        )
    )
    report = build_tag_truth_v2_provenance_verification(
        seal_revision=seal_revision,
        seal_tree_id=seal_tree_id,
        artifacts=artifacts,
        source_root=source.root,
        development_truth=source.development_truth,
    )
    provenance_path = tmp_path / "external" / "provenance.json"
    _write(
        tmp_path,
        "external/provenance.json",
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
    )
    return _NearDuplicateCliChain(
        project_root=project_root,
        source_root=source.root,
        seal_revision=seal_revision,
        artifact_paths=artifact_paths,  # type: ignore[arg-type]
        provenance_path=provenance_path,
        report=report,
        selection=selection,
        packet=packet,
        consensus=consensus,
        development_truth=source.development_truth,
    )


def _run_near_duplicate_cli(
    chain: _NearDuplicateCliChain,
    *,
    seal_revision: str | None = None,
    provenance_path: Path | None = None,
    receipts: tuple[Path, ...] | None = None,
    isolated: bool = True,
) -> subprocess.CompletedProcess[str]:
    relative_paths = tuple(
        path.relative_to(chain.project_root).as_posix() for path in chain.artifact_paths
    )
    receipt_paths = receipts or (chain.artifact_paths[2], chain.artifact_paths[3])
    arguments = [sys.executable]
    if isolated:
        arguments.append("-I")
    arguments.extend(
        [
            "-B",
            str(chain.project_root / "tools/screen_tag_truth_v2_near_duplicates.py"),
            "--selection",
            relative_paths[0],
            "--packet",
            relative_paths[1],
        ]
    )
    for receipt in receipt_paths:
        arguments.extend(["--receipt", receipt.relative_to(chain.project_root).as_posix()])
    arguments.extend(
        [
            "--consensus",
            relative_paths[4],
            "--source-root",
            str(chain.source_root),
            "--seal-revision",
            seal_revision or chain.seal_revision,
            "--provenance-verification",
            str(provenance_path or chain.provenance_path),
            "--policy",
            POLICY_RELATIVE_PATH,
        ]
    )
    return subprocess.run(
        arguments,
        cwd=chain.project_root,
        env={
            **os.environ,
            "PYTHONPATH": str(chain.project_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )


def _policy_with_ratios(
    *,
    hard_content_containment: tuple[int, int] = (4, 5),
    hard_content_jaccard: tuple[int, int] = (7, 10),
    hard_contiguous_selected_coverage: tuple[int, int] = (3, 5),
    gray_content_containment: tuple[int, int] = (3, 5),
    gray_content_jaccard: tuple[int, int] = (1, 2),
    gray_shape_containment: tuple[int, int] = (9, 10),
) -> TagTruthV2NearDuplicatePolicy:
    payload = load_tag_truth_v2_near_duplicate_policy(POLICY_PATH).model_dump(
        mode="json",
        exclude={"policy_fingerprint"},
    )
    for field, ratio in {
        "hard_content_containment": hard_content_containment,
        "hard_content_jaccard": hard_content_jaccard,
        "hard_contiguous_selected_coverage": hard_contiguous_selected_coverage,
        "gray_content_containment": gray_content_containment,
        "gray_content_jaccard": gray_content_jaccard,
        "gray_shape_containment": gray_shape_containment,
    }.items():
        payload[field] = {"numerator": ratio[0], "denominator": ratio[1]}
    return TagTruthV2NearDuplicatePolicy.model_validate(
        near_duplicate_policy_payload_with_fingerprint(payload)
    )


def _identifier_stream(prefix: str, count: int) -> str:
    return " ".join(f"{prefix}{index}" for index in range(count))


def _git_blob_id(raw: bytes) -> str:
    return (
        subprocess.run(
            ["git", "hash-object", "--stdin"],
            input=raw,
            check=True,
            capture_output=True,
        )
        .stdout.decode("ascii")
        .strip()
    )


def _single_document_inventory(
    *,
    role: ReferenceRole,
    repository_source_id: str,
    revision: str,
    tree_id: str,
    path: str,
    text: str,
) -> ScannedReferenceInventory:
    raw = text.encode("utf-8")
    document = ReferenceDocument(
        role=role,
        repository_source_id=repository_source_id,
        revision=revision,
        tree_id=tree_id,
        path=path,
        git_blob_id=_git_blob_id(raw),
        content_sha256=bytes_hash(raw),
        text=text,
    )
    payload: dict[str, object] = {
        "role": role,
        "repository_source_id": repository_source_id,
        "revision": revision,
        "tree_id": tree_id,
        "scope": "registered_paths" if role == "development_truth" else "entire_tracked_tree",
        "requested_paths": [path] if role == "development_truth" else [],
        "requested_path_count": 1 if role == "development_truth" else 0,
        "total_entry_count": 1,
        "regular_blob_count": 1,
        "unique_blob_count": 1,
        "utf8_text_count": 1,
        "binary_count": 0,
        "non_utf8_count": 0,
        "empty_text_count": 0,
        "oversize_count": 0,
        "symlink_count": 0,
        "gitlink_count": 0,
        "other_nonregular_count": 0,
        "scanned_document_count": 1,
        "budget_skipped_count": 0,
        "loaded_unique_blob_bytes": len(raw),
        "inventory_issues": [],
        "scope_entry_fingerprint": canonical_hash(
            "tag-truth-reference-scope",
            [
                {
                    "path": path,
                    "mode": "100644",
                    "kind": "blob",
                    "object_id": document.git_blob_id,
                }
            ],
        ),
        "document_set_fingerprint": canonical_hash(
            "tag-truth-reference-documents",
            [
                {
                    "role": document.role,
                    "repository_source_id": document.repository_source_id,
                    "revision": document.revision,
                    "tree_id": document.tree_id,
                    "path": path,
                    "git_blob_id": document.git_blob_id,
                    "content_sha256": document.content_sha256,
                }
            ],
        ),
    }
    payload["inventory_fingerprint"] = canonical_hash(
        "tag-truth-reference-inventory",
        payload,
    )
    summary = ReferenceInventorySummary.model_validate(payload)
    return ScannedReferenceInventory(summary=summary, documents=(document,))


def _reference_inventories_for_texts(
    chain: _NearDuplicateCliChain,
    *,
    candidate_text: str,
    exposure_text: str,
) -> tuple[ScannedReferenceInventory, ScannedReferenceInventory, ScannedReferenceInventory]:
    candidate_tree = _git(
        chain.project_root,
        "rev-parse",
        f"{chain.report.candidate_commit}^{{tree}}",
    )
    development_revision = chain.development_truth.repository_revision
    return (
        _single_document_inventory(
            role="candidate_project",
            repository_source_id="arkts-code-reviewer",
            revision=chain.report.candidate_commit,
            tree_id=candidate_tree,
            path="candidate/reference.ets",
            text=candidate_text,
        ),
        _single_document_inventory(
            role="exposure",
            repository_source_id=chain.report.source_repository_source_id,
            revision=chain.report.exposure_revision,
            tree_id=chain.report.exposure_tree_id,
            path="exposure/reference.ets",
            text=exposure_text,
        ),
        scan_pinned_git_reference_inventory(
            chain.source_root,
            role="development_truth",
            repository_source_id=chain.report.source_repository_source_id,
            revision=development_revision,
            expected_tree_id=None,
            included_paths=tuple(item.path for item in chain.development_truth.sources),
            maximum_blob_bytes=2 * 1024 * 1024,
        ),
    )


def _build_screening(
    chain: _NearDuplicateCliChain,
    inventories: tuple[
        ScannedReferenceInventory,
        ScannedReferenceInventory,
        ScannedReferenceInventory,
    ],
    *,
    policy: TagTruthV2NearDuplicatePolicy | None = None,
) -> tuple[TagTruthV2NearDuplicatePolicy, TagTruthV2NearDuplicateVerification]:
    policy = policy or load_tag_truth_v2_near_duplicate_policy(POLICY_PATH)
    report = build_tag_truth_v2_near_duplicate_verification(
        policy=policy,
        provenance=chain.report,
        selection=chain.selection,
        packet=chain.packet,
        consensus=chain.consensus,
        reference_inventories=inventories,
    )
    return policy, report


def _policy_with_updates(**updates: object) -> TagTruthV2NearDuplicatePolicy:
    payload = load_tag_truth_v2_near_duplicate_policy(POLICY_PATH).model_dump(
        mode="json",
        exclude={"policy_fingerprint"},
    )
    payload.update(updates)
    return TagTruthV2NearDuplicatePolicy.model_validate(
        near_duplicate_policy_payload_with_fingerprint(payload)
    )


def _development_inventory_with_unavailable_document(
    inventory: ScannedReferenceInventory,
    *,
    issue: str,
) -> ScannedReferenceInventory:
    payload = inventory.summary.model_dump(mode="json", exclude={"inventory_fingerprint"})
    nonregular = issue == "symlink_entries"
    payload.update(
        {
            "regular_blob_count": 0 if nonregular else 1,
            "unique_blob_count": 0 if nonregular else 1,
            "utf8_text_count": 0,
            "scanned_document_count": 0,
            "oversize_count": int(issue == "oversize_entries"),
            "non_utf8_count": int(issue == "non_utf8_entries"),
            "budget_skipped_count": int(issue == "reference_byte_budget_exceeded"),
            "symlink_count": int(nonregular),
            "loaded_unique_blob_bytes": (
                inventory.summary.loaded_unique_blob_bytes if issue == "non_utf8_entries" else 0
            ),
            "inventory_issues": [issue],
            "document_set_fingerprint": canonical_hash(
                "tag-truth-reference-documents",
                [],
            ),
        }
    )
    payload["inventory_fingerprint"] = canonical_hash(
        "tag-truth-reference-inventory",
        payload,
    )
    return ScannedReferenceInventory(
        summary=ReferenceInventorySummary.model_validate(payload),
        documents=(),
    )


def _build_hostile_inventory(tmp_path: Path) -> _InventoryFixture:
    gitlink_repository = tmp_path / "gitlink-source"
    _init_git_repository(gitlink_repository)
    _write(gitlink_repository, "README.md", "gitlink fixture\n")
    gitlink_revision, _ = _commit_all(gitlink_repository, "gitlink source")

    root = tmp_path / "inventory"
    _init_git_repository(root)
    _write(root, "text/Alpha.ets", "const alpha: string = 'ok';\n")
    _write(root, "text/AlphaCopy.ets", "const alpha: string = 'ok';\n")
    _write(root, "text/Empty.ets", "")
    _write(root, "text/BadToken.ets", "const broken = 'unterminated")
    _write(root, "binary/Nul.bin", b"before\0after")
    _write(root, "binary/NonUtf8.bin", b"\xff\xfe")
    _write(root, "large/Oversize.ets", b"x" * 1025)
    symlink = root / "links/Alpha.ets"
    symlink.parent.mkdir(parents=True)
    symlink.symlink_to("../text/Alpha.ets")
    _git(root, "add", "-A")
    _git(
        root,
        "update-index",
        "--add",
        "--cacheinfo",
        "160000",
        gitlink_revision,
        "vendor/gitlink",
    )
    _git(root, "commit", "-qm", "mixed tracked inventory")
    revision = _git(root, "rev-parse", "HEAD")
    tree_id = _git(root, "rev-parse", "HEAD^{tree}")
    inventory = scan_pinned_git_reference_inventory(
        root,
        role="exposure",
        repository_source_id="hostile-inventory",
        revision=revision,
        expected_tree_id=tree_id,
        included_paths=None,
        maximum_blob_bytes=1024,
        maximum_inventory_entries=100,
    )
    return _InventoryFixture(
        root=root,
        revision=revision,
        tree_id=tree_id,
        inventory=inventory,
    )


def _set_hostile_git_environment(
    monkeypatch: pytest.MonkeyPatch,
    hostile_repository: Path,
) -> None:
    monkeypatch.setenv("GIT_DIR", str(hostile_repository / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(hostile_repository))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(hostile_repository / ".git/objects"))
    monkeypatch.setenv(
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        str(hostile_repository / ".git/objects"),
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile_repository))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(hostile_repository))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.delenv("GIT_CONFIG_KEY_0", raising=False)
    monkeypatch.delenv("GIT_CONFIG_VALUE_0", raising=False)


@pytest.mark.parametrize(
    "source",
    [
        "const pattern = /a\\/b+/gi; const ok = pattern.test(value);",
        "const broken = 'unterminated",
        "const template = `value=${input}`;",
        "/* unterminated block comment",
    ],
)
def test_tokenizer_is_total_and_byte_deterministic_for_hostile_lexemes(source: str) -> None:
    for mode in ("lexical_content", "lexical_shape"):
        first = tokenize_arkts_like(source, mode=mode)
        second = tokenize_arkts_like(source, mode=mode)
        assert first == second
        assert isinstance(first, tuple)
        assert all(isinstance(token, str) and token for token in first)


def test_tokenizer_freezes_whitespace_comments_identifiers_literals_and_operators() -> None:
    base = "const total = account.value + 42; return total?.toString() ?? 'none';"
    formatting = (
        "// leading comment\r\n const  total=account.value+42;"
        " /* middle */ return total?.toString ( ) ?? 'none' ;"
    )
    renamed = "const result = wallet.amount + 7; return result?.toString() ?? 'missing';"
    different_operator = "const total = account.value - 42; return total?.toString() ?? 'none';"

    content = tokenize_arkts_like(base, mode="lexical_content")
    assert content == tokenize_arkts_like(formatting, mode="lexical_content")
    assert content != tokenize_arkts_like(renamed, mode="lexical_content")
    assert content != tokenize_arkts_like(different_operator, mode="lexical_content")

    shape = tokenize_arkts_like(base, mode="lexical_shape")
    assert shape == tokenize_arkts_like(formatting, mode="lexical_shape")
    assert shape == tokenize_arkts_like(renamed, mode="lexical_shape")
    assert shape != tokenize_arkts_like(different_operator, mode="lexical_shape")


def test_tokenizer_freezes_unicode_and_multi_character_operator_boundaries() -> None:
    unicode_source = "const 状态 = 输入?.值 ?? '默认'; if (状态 === '就绪') { 状态 += '!' }"
    tokens = tokenize_arkts_like(unicode_source, mode="lexical_content")
    assert tokens == tokenize_arkts_like(unicode_source, mode="lexical_content")

    for operator in ("?.", "??", "===", "+="):
        assert operator in tokens
    assert tokenize_arkts_like("a == b", mode="lexical_content") != tokenize_arkts_like(
        "a === b", mode="lexical_content"
    )


def test_template_literal_text_is_normalized_but_simple_expression_tokens_are_preserved() -> None:
    left = "const value = `x=${left}`;"
    right = "const value = `y=${right}`;"

    left_content = tokenize_arkts_like(left, mode="lexical_content")
    right_content = tokenize_arkts_like(right, mode="lexical_content")

    assert left_content != right_content
    assert "left" in left_content
    assert "right" in right_content
    assert tokenize_arkts_like(left, mode="lexical_shape") == tokenize_arkts_like(
        right,
        mode="lexical_shape",
    )


def test_complex_nested_template_interpolation_abstains_even_when_bytes_are_equal() -> None:
    source = "const value = `x=${{nested: left}}`;"
    policy = load_tag_truth_v2_near_duplicate_policy(POLICY_PATH)
    selected = near_duplicate_core._probe(
        "case-0000000000000001",
        "unit",
        source,
        policy,
    )

    decision, signals, _, issues = near_duplicate_core._similarity(selected, source, policy)

    assert decision == "abstain"
    assert signals == ()
    assert issues == ("complex_template_interpolation",)


def test_ratio_threshold_uses_exact_integer_boundary_comparisons() -> None:
    threshold = RatioThreshold(numerator=4, denominator=5)

    assert threshold.reached(80, 100)
    assert threshold.reached(800_000_000_000_000_000, 1_000_000_000_000_000_000)
    assert not threshold.reached(79, 100)
    assert threshold.reached(81, 100)
    assert not threshold.reached(0, 0)


def test_shingles_keep_canonical_token_tuples_instead_of_joined_or_hash_only_keys() -> None:
    joined_collision_left = ("ab", "c")
    joined_collision_right = ("a", "bc")

    left = near_duplicate_core._shingles(joined_collision_left, 2)
    right = near_duplicate_core._shingles(joined_collision_right, 2)

    assert left == frozenset({joined_collision_left})
    assert right == frozenset({joined_collision_right})
    assert left.isdisjoint(right)


def test_similarity_freezes_exact_containment_jaccard_and_contiguous_signals() -> None:
    policy = _policy_with_ratios(
        hard_content_containment=(1, 1),
        hard_content_jaccard=(4, 5),
        hard_contiguous_selected_coverage=(4, 5),
        gray_content_containment=(1, 1),
        gray_content_jaccard=(4, 5),
    )
    selected_text = _identifier_stream("selected", 100)
    selected = near_duplicate_core._probe(
        "case-0000000000000001",
        "unit",
        selected_text,
        policy,
    )

    exact, exact_signals, exact_scores, exact_issues = near_duplicate_core._similarity(
        selected,
        " // formatting only\n" + selected_text,
        policy,
    )
    assert exact == "duplicate"
    assert "normalized_token_stream_equal" in exact_signals
    assert exact_scores.normalized_token_stream_equal is True
    assert exact_issues == ()

    extended = selected_text + " " + _identifier_stream("extra", 120)
    containment, containment_signals, containment_scores, _ = near_duplicate_core._similarity(
        selected, extended, policy
    )
    assert containment == "duplicate"
    assert "content_containment" in containment_signals
    assert (
        containment_scores.shared_content_shingle_count
        == containment_scores.selected_content_shingle_count
    )

    one_change = " ".join("changed" if index == 50 else f"selected{index}" for index in range(100))
    jaccard, jaccard_signals, jaccard_scores, _ = near_duplicate_core._similarity(
        selected,
        one_change,
        policy,
    )
    assert jaccard == "duplicate"
    assert jaccard_signals == ("content_jaccard",)
    assert RatioThreshold(numerator=4, denominator=5).reached(
        jaccard_scores.shared_content_shingle_count,
        jaccard_scores.content_union_shingle_count,
    )

    contiguous_reference = " ".join(
        ["outside-left", *(f"selected{index}" for index in range(10, 90)), "outside-right"]
    )
    contiguous, contiguous_signals, contiguous_scores, _ = near_duplicate_core._similarity(
        selected,
        contiguous_reference,
        policy,
    )
    assert contiguous == "duplicate"
    assert contiguous_signals == (
        "contiguous_reference_coverage",
        "contiguous_token_run",
    )
    assert contiguous_scores.longest_contiguous_token_run == 80


def test_shape_similarity_only_enters_gray_review_channel() -> None:
    policy = _policy_with_ratios(
        hard_content_containment=(1, 1),
        hard_content_jaccard=(1, 1),
        hard_contiguous_selected_coverage=(1, 1),
        gray_content_containment=(1, 1),
        gray_content_jaccard=(1, 1),
        gray_shape_containment=(9, 10),
    )
    operators = ("+", "-", "*", "/", "%", "&&", "||", "??", "==", "===", "<=", ">=")
    state = 1
    fragments: list[str] = []
    for index in range(220):
        state = (1_103_515_245 * state + 12_345) % (2**31)
        fragments.append(f"left{index} {operators[state % len(operators)]}")
    selected_text = " ".join(fragments)
    reference_text = selected_text.replace("left", "renamed")
    selected = near_duplicate_core._probe(
        "case-0000000000000001",
        "unit",
        selected_text,
        policy,
    )

    decision, signals, scores, _ = near_duplicate_core._similarity(
        selected,
        reference_text,
        policy,
    )

    assert scores.shared_content_shingle_count == 0
    assert scores.shared_shape_shingle_count >= policy.gray_minimum_shared_shape_shingles
    assert decision == "gray"
    assert {
        "normalized_shape_token_stream_equal",
        "reference_shape_containment",
        "shape_containment",
    }.issubset(signals)


def test_bidirectional_similarity_catches_legacy_reference_embedded_in_large_selection() -> None:
    policy = load_tag_truth_v2_near_duplicate_policy(POLICY_PATH)
    legacy = _identifier_stream("legacy", 800)
    selected_text = " ".join(
        (
            _identifier_stream("newPrefix", 5_000),
            legacy,
            _identifier_stream("newSuffix", 5_000),
        )
    )
    selected = near_duplicate_core._probe(
        "case-0000000000000001",
        "unit",
        selected_text,
        policy,
    )

    decision, signals, scores, _ = near_duplicate_core._similarity(selected, legacy, policy)

    assert scores.selected_content_token_count == 10_800
    assert scores.reference_content_token_count == 800
    assert decision == "duplicate"
    assert "reference_content_containment" in signals
    assert "contiguous_reference_coverage" in signals
    assert "content_containment" not in signals
    assert "contiguous_token_run" not in signals


def test_repeated_shape_exact_stream_enters_gray_even_with_few_unique_shingles() -> None:
    policy = load_tag_truth_v2_near_duplicate_policy(POLICY_PATH)
    selected_text = _identifier_stream("selected", 100)
    reference_text = _identifier_stream("renamed", 100)
    selected = near_duplicate_core._probe(
        "case-0000000000000001",
        "unit",
        selected_text,
        policy,
    )

    decision, signals, scores, _ = near_duplicate_core._similarity(
        selected,
        reference_text,
        policy,
    )

    assert scores.normalized_shape_token_stream_equal is True
    assert scores.shared_shape_shingle_count == 1
    assert scores.shared_shape_shingle_count < policy.gray_minimum_shared_shape_shingles
    assert decision == "gray"
    assert signals == ("normalized_shape_token_stream_equal",)


def test_one_shared_content_shingle_cannot_trigger_gray_containment() -> None:
    policy = load_tag_truth_v2_near_duplicate_policy(POLICY_PATH)
    selected_text = _identifier_stream("selected", 30)
    reference_text = _identifier_stream("selected", 7)
    selected = near_duplicate_core._probe(
        "case-0000000000000001",
        "unit",
        selected_text,
        policy,
    )

    decision, signals, scores, _ = near_duplicate_core._similarity(
        selected,
        reference_text,
        policy,
    )

    assert scores.shared_content_shingle_count == 1
    assert scores.shared_content_shingle_count < policy.gray_minimum_shared_content_shingles
    assert decision == "clear"
    assert signals == ()


def test_longest_contiguous_score_is_exact_below_one_shingle() -> None:
    policy = load_tag_truth_v2_near_duplicate_policy(POLICY_PATH)
    selected = near_duplicate_core._probe(
        "case-0000000000000001",
        "unit",
        "a b c d e f selectedTail",
        policy,
    )

    _, _, scores, _ = near_duplicate_core._similarity(
        selected,
        "a b c d e f referenceTail",
        policy,
    )

    assert scores.shared_content_shingle_count == 0
    assert scores.longest_contiguous_token_run == 6


@pytest.mark.parametrize(
    "source",
    [
        "const broken = 'unterminated",
        "const broken = `unterminated ${value}",
        "/* unterminated block comment",
    ],
)
def test_tokenization_issues_abstain_before_exact_or_similarity_signals(source: str) -> None:
    policy = load_tag_truth_v2_near_duplicate_policy(POLICY_PATH)
    selected = near_duplicate_core._probe(
        "case-0000000000000001",
        "unit",
        source,
        policy,
    )

    decision, signals, _, issues = near_duplicate_core._similarity(selected, source, policy)

    assert decision == "abstain"
    assert signals == ()
    assert issues


def test_committed_shadow_policy_is_closed_self_hashed_and_duplicate_key_safe(
    tmp_path: Path,
) -> None:
    policy = load_tag_truth_v2_near_duplicate_policy(POLICY_PATH)
    assert policy.approval_status == "snapshot_only_not_approved"
    assert policy.model_config.get("frozen") is True
    assert parse_tag_truth_v2_near_duplicate_policy(POLICY_PATH.read_bytes()) == policy

    with pytest.raises(ValidationError, match="extra"):
        TagTruthV2NearDuplicatePolicy.model_validate(
            {**policy.model_dump(mode="json"), "unknown": True}
        )
    forged_policy = policy.model_dump(mode="json")
    forged_policy["maximum_total_reference_bytes"] += 1
    with pytest.raises(ValidationError, match="fingerprint"):
        TagTruthV2NearDuplicatePolicy.model_validate(forged_policy)

    duplicate = tmp_path / "duplicate-policy.json"
    raw = POLICY_PATH.read_text(encoding="utf-8")
    duplicate.write_text(raw.replace("{", '{"schema_version":"forged",', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_tag_truth_v2_near_duplicate_policy(duplicate)

    symlink = tmp_path / "policy-link.json"
    symlink.symlink_to(POLICY_PATH)
    with pytest.raises(ValueError, match="non-symlink|regular"):
        load_tag_truth_v2_near_duplicate_policy(symlink)


def test_pinned_inventory_counts_every_tracked_entry_and_keeps_failures_visible(
    tmp_path: Path,
) -> None:
    fixture = _build_hostile_inventory(tmp_path)
    summary = fixture.inventory.summary

    assert summary.total_entry_count == 9
    assert summary.regular_blob_count == 7
    assert summary.unique_blob_count == 6
    assert summary.utf8_text_count == 4
    assert summary.scanned_document_count == 4
    assert summary.empty_text_count == 1
    assert summary.binary_count == 1
    assert summary.non_utf8_count == 1
    assert summary.oversize_count == 1
    assert summary.symlink_count == 1
    assert summary.gitlink_count == 1
    assert summary.other_nonregular_count == 0
    assert summary.inventory_issues == (
        "gitlink_entries",
        "non_utf8_entries",
        "oversize_entries",
        "symlink_entries",
    )
    assert tuple(document.path for document in fixture.inventory.documents) == (
        "text/Alpha.ets",
        "text/AlphaCopy.ets",
        "text/BadToken.ets",
        "text/Empty.ets",
    )


def test_pinned_inventory_is_path_order_and_blob_dedup_deterministic(tmp_path: Path) -> None:
    fixture = _build_hostile_inventory(tmp_path)
    paths = ("text/AlphaCopy.ets", "text/Alpha.ets")
    forward = scan_pinned_git_reference_inventory(
        fixture.root,
        role="development_truth",
        repository_source_id="hostile-inventory",
        revision=fixture.revision,
        expected_tree_id=fixture.tree_id,
        included_paths=paths,
        maximum_blob_bytes=1024,
    )
    reverse = scan_pinned_git_reference_inventory(
        fixture.root,
        role="development_truth",
        repository_source_id="hostile-inventory",
        revision=fixture.revision,
        expected_tree_id=fixture.tree_id,
        included_paths=tuple(reversed(paths)),
        maximum_blob_bytes=1024,
    )

    assert forward == reverse
    assert forward.summary.scope == "registered_paths"
    assert forward.summary.regular_blob_count == 2
    assert forward.summary.unique_blob_count == 1
    assert tuple(document.path for document in forward.documents) == tuple(sorted(paths))


def test_pinned_inventory_total_byte_budget_skips_blob_with_explicit_blocker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "budget"
    _init_git_repository(root)
    _write(root, "a.ets", "a" * 700)
    _write(root, "b.ets", "b" * 700)
    revision, tree_id = _commit_all(root, "two individually allowed blobs")

    inventory = scan_pinned_git_reference_inventory(
        root,
        role="exposure",
        repository_source_id="budget-fixture",
        revision=revision,
        expected_tree_id=tree_id,
        included_paths=None,
        maximum_blob_bytes=1024,
        maximum_total_reference_bytes=1024,
    )

    assert inventory.summary.loaded_unique_blob_bytes == 700
    assert inventory.summary.budget_skipped_count == 1
    assert inventory.summary.scanned_document_count == 1
    assert inventory.summary.inventory_issues == ("reference_byte_budget_exceeded",)


def test_registered_symlink_is_reported_as_unevaluable_instead_of_raising(
    tmp_path: Path,
) -> None:
    fixture = _build_hostile_inventory(tmp_path)

    inventory = scan_pinned_git_reference_inventory(
        fixture.root,
        role="development_truth",
        repository_source_id="hostile-inventory",
        revision=fixture.revision,
        expected_tree_id=fixture.tree_id,
        included_paths=("links/Alpha.ets",),
        maximum_blob_bytes=1024,
        maximum_inventory_entries=100,
    )

    assert inventory.summary.requested_paths == ("links/Alpha.ets",)
    assert inventory.summary.symlink_count == 1
    assert inventory.summary.inventory_issues == ("symlink_entries",)
    assert inventory.documents == ()


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("symbolic_revision", "full lowercase"),
        ("wrong_tree", "tree identity drift"),
        ("missing_registered", "absent"),
        ("duplicate_registered", "unique"),
        ("entry_limit", "entry limit"),
        ("subdirectory_root", "top level"),
    ],
)
def test_pinned_inventory_hostile_contract_inputs_fail_closed(
    tmp_path: Path,
    mutation: str,
    match: str,
) -> None:
    fixture = _build_hostile_inventory(tmp_path)
    root = fixture.root
    revision = fixture.revision
    expected_tree_id = fixture.tree_id
    included_paths: tuple[str, ...] | None = None
    maximum_inventory_entries = 100
    if mutation == "symbolic_revision":
        revision = "HEAD"
    elif mutation == "wrong_tree":
        expected_tree_id = "f" * 40
    elif mutation == "missing_registered":
        included_paths = ("missing.ets",)
    elif mutation == "duplicate_registered":
        included_paths = ("text/Alpha.ets", "text/Alpha.ets")
    elif mutation == "entry_limit":
        maximum_inventory_entries = 1
    elif mutation == "subdirectory_root":
        root = fixture.root / "text"
    else:  # pragma: no cover
        raise AssertionError(mutation)

    with pytest.raises(ValueError, match=match):
        scan_pinned_git_reference_inventory(
            root,
            role="exposure",
            repository_source_id="hostile-inventory",
            revision=revision,
            expected_tree_id=expected_tree_id,
            included_paths=included_paths,
            maximum_blob_bytes=1024,
            maximum_inventory_entries=maximum_inventory_entries,
        )


def test_pinned_inventory_ignores_replace_objects_and_rejects_grafts(tmp_path: Path) -> None:
    fixture = _build_hostile_inventory(tmp_path)
    empty_tree = _git(fixture.root, "mktree")
    unrelated = subprocess.run(
        ["git", "-C", str(fixture.root), "commit-tree", empty_tree],
        input="hostile replacement\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(fixture.root, "replace", fixture.revision, unrelated)
    try:
        replaced = scan_pinned_git_reference_inventory(
            fixture.root,
            role="exposure",
            repository_source_id="hostile-inventory",
            revision=fixture.revision,
            expected_tree_id=fixture.tree_id,
            included_paths=None,
            maximum_blob_bytes=1024,
        )
    finally:
        _git(fixture.root, "replace", "-d", fixture.revision)
    assert replaced == fixture.inventory

    grafts = fixture.root / ".git/info/grafts"
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(f"{fixture.revision} {unrelated}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="grafts are forbidden"):
        scan_pinned_git_reference_inventory(
            fixture.root,
            role="exposure",
            repository_source_id="hostile-inventory",
            revision=fixture.revision,
            expected_tree_id=fixture.tree_id,
            included_paths=None,
            maximum_blob_bytes=1024,
        )


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("repository_source_id", "forged-source"),
        ("revision", "e" * 40),
        ("tree_id", "d" * 40),
    ],
)
def test_scanned_inventory_rejects_document_provenance_different_from_summary(
    tmp_path: Path,
    field: str,
    forged_value: str,
) -> None:
    fixture = _build_hostile_inventory(tmp_path)
    documents = list(fixture.inventory.documents)
    forged = documents[0].model_dump(mode="json")
    forged[field] = forged_value
    documents[0] = ReferenceDocument.model_validate(forged)

    with pytest.raises(ValueError, match="provenance|inventory"):
        ScannedReferenceInventory(
            summary=fixture.inventory.summary,
            documents=tuple(documents),
        )


def test_pinned_inventory_ignores_hostile_process_git_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_hostile_inventory(tmp_path)
    hostile = tmp_path / "hostile-git-environment"
    _init_git_repository(hostile)
    _write(hostile, "hostile.txt", "must not redirect inventory\n")
    _commit_all(hostile, "hostile redirect target")
    _set_hostile_git_environment(monkeypatch, hostile)

    rebuilt = scan_pinned_git_reference_inventory(
        fixture.root,
        role="exposure",
        repository_source_id="hostile-inventory",
        revision=fixture.revision,
        expected_tree_id=fixture.tree_id,
        included_paths=None,
        maximum_blob_bytes=1024,
        maximum_inventory_entries=100,
    )

    assert rebuilt == fixture.inventory


def test_builder_keeps_unit_and_file_axes_independent_in_all_four_quadrants(
    tmp_path: Path,
) -> None:
    alpha_unit = _identifier_stream("alphaUnit", 100)
    alpha_file = "\n".join(
        (
            _identifier_stream("alphaOuterLeftA", 200),
            _identifier_stream("alphaOuterLeftB", 200),
            alpha_unit,
            _identifier_stream("alphaOuterRight", 400),
        )
    )
    beta_unit = " ".join(f"betaUnit{index} +" for index in range(100))
    beta_prefix_a = _identifier_stream("betaCommonLeftA", 325)
    beta_prefix_b = _identifier_stream("betaCommonLeftB", 325)
    beta_suffix = _identifier_stream("betaCommonRight", 650)
    beta_file = "\n".join((beta_prefix_a, beta_prefix_b, beta_unit, beta_suffix))
    chain = _build_near_duplicate_cli_chain(
        tmp_path,
        selected_texts=(alpha_file, beta_file),
        unit_spans=((3, 3), (3, 3)),
    )
    alpha_unit_only_reference = "\n".join(
        (
            " ".join(f"candidateOtherLeft{index} +" for index in range(400)),
            alpha_unit,
            " ".join(f"candidateOtherRight{index} -" for index in range(400)),
        )
    )
    beta_file_reference = "\n".join(
        (
            beta_prefix_a,
            beta_prefix_b,
            _identifier_stream("betaDifferentUnit", 100),
            beta_suffix,
        )
    )
    mixed_inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=alpha_unit_only_reference,
        exposure_text=beta_file_reference,
    )

    _, mixed = _build_screening(chain, mixed_inventories)
    alpha, beta = mixed.cases
    assert (alpha.unit_decision, alpha.file_decision) == ("duplicate", "clear")
    assert (beta.unit_decision, beta.file_decision) == ("clear", "duplicate")

    both_and_neither_inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=alpha_file,
        exposure_text=_identifier_stream("exposureUnrelated", 1400),
    )
    _, both_and_neither = _build_screening(chain, both_and_neither_inventories)
    alpha, beta = both_and_neither.cases
    assert (alpha.unit_decision, alpha.file_decision) == ("duplicate", "duplicate")
    assert (beta.unit_decision, beta.file_decision) == ("clear", "clear")


def test_builder_preserves_all_reference_roles_and_deterministic_match_order(
    tmp_path: Path,
) -> None:
    peer_text = provenance_test_support._source_text("Dev", "first blind path")
    second_peer_text = provenance_test_support._source_text("Dev", "second blind path")
    chain = _build_near_duplicate_cli_chain(
        tmp_path,
        selected_texts=(peer_text, second_peer_text),
    )
    inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=peer_text,
        exposure_text=peer_text,
    )

    _, forward = _build_screening(chain, inventories)
    _, reversed_inputs = _build_screening(
        chain,
        (inventories[2], inventories[1], inventories[0]),
    )

    assert forward == reversed_inputs
    assert {match.reference_role for match in forward.matches} == {
        "candidate_project",
        "exposure",
        "development_truth",
        "campaign_peer",
    }
    match_keys = tuple(near_duplicate_core._match_sort_key(match) for match in forward.matches)
    assert match_keys == tuple(sorted(match_keys))
    assert canonical_json(forward.model_dump(mode="json")) == canonical_json(
        reversed_inputs.model_dump(mode="json")
    )


def test_builder_short_units_abstain_without_a_hard_match_but_exact_copy_rejects(
    tmp_path: Path,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    unrelated = _identifier_stream("unrelated", 120)
    independent_inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=unrelated,
        exposure_text=_identifier_stream("exposure", 120),
    )

    _, independent = _build_screening(chain, independent_inventories)
    assert all(case.file_decision == "abstain" for case in independent.cases)
    assert all(case.unit_decision == "abstain" for case in independent.cases)
    assert all("file_too_short_for_policy" in case.blockers for case in independent.cases)
    assert all("unit_too_short_for_policy" in case.blockers for case in independent.cases)

    exact_short = chain.packet.cases[0].source_text
    exact_inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=exact_short,
        exposure_text=_identifier_stream("exposure", 120),
    )
    _, copied = _build_screening(chain, exact_inventories)
    assert copied.cases[0].file_decision == "duplicate"
    assert copied.cases[0].unit_decision == "duplicate"
    assert "file_too_short_for_policy" not in copied.cases[0].blockers
    assert "unit_too_short_for_policy" not in copied.cases[0].blockers


def test_builder_unresolved_consensus_is_visible_and_never_clean(tmp_path: Path) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path, unresolved=True)
    inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=_identifier_stream("candidate", 120),
        exposure_text=_identifier_stream("exposure", 120),
    )

    _, report = _build_screening(chain, inventories)

    assert "consensus_unresolved" in report.screening_blockers
    assert report.screening_outcome == "review_required"
    assert report.near_duplicate_qualification_status == "not_qualified_policy_unapproved"


def test_builder_preserves_oversize_inventory_as_review_required_blocker(
    tmp_path: Path,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=_identifier_stream("candidate", 120),
        exposure_text=_identifier_stream("exposure", 120),
    )
    candidate, exposure, development = inventories
    payload = candidate.summary.model_dump(mode="json", exclude={"inventory_fingerprint"})
    payload.update(
        {
            "total_entry_count": 2,
            "regular_blob_count": 2,
            "unique_blob_count": 2,
            "oversize_count": 1,
            "inventory_issues": ["oversize_entries"],
            "scope_entry_fingerprint": canonical_hash(
                "tag-truth-reference-scope",
                {"fixture": "one loaded UTF-8 blob and one oversize blob"},
            ),
        }
    )
    payload["inventory_fingerprint"] = canonical_hash(
        "tag-truth-reference-inventory",
        payload,
    )
    oversize_candidate = ScannedReferenceInventory(
        summary=ReferenceInventorySummary.model_validate(payload),
        documents=candidate.documents,
    )

    _, report = _build_screening(
        chain,
        (oversize_candidate, exposure, development),
    )

    assert "candidate_project:oversize_entries" in report.screening_blockers
    assert report.screening_outcome == "review_required"


@pytest.mark.parametrize(
    "issue",
    [
        "oversize_entries",
        "non_utf8_entries",
        "reference_byte_budget_exceeded",
        "symlink_entries",
    ],
)
def test_builder_development_unavailable_input_reports_blocker_instead_of_raising(
    tmp_path: Path,
    issue: str,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    candidate, exposure, development = _reference_inventories_for_texts(
        chain,
        candidate_text=_identifier_stream("candidate", 120),
        exposure_text=_identifier_stream("exposure", 120),
    )
    unavailable_development = _development_inventory_with_unavailable_document(
        development,
        issue=issue,
    )

    _, report = _build_screening(
        chain,
        (candidate, exposure, unavailable_development),
    )

    assert f"development_truth:{issue}" in report.screening_blockers
    assert report.screening_outcome == "review_required"
    assert all(case.overall_decision != "clear" for case in report.cases)
    assert all(
        any("reference_inventory_incomplete" in blocker for blocker in case.blockers)
        for case in report.cases
    )


def test_builder_reference_tokenization_issue_abstains_instead_of_reporting_clear(
    tmp_path: Path,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    inventories = _reference_inventories_for_texts(
        chain,
        candidate_text="const broken = 'unterminated",
        exposure_text=_identifier_stream("exposure", 120),
    )

    _, report = _build_screening(chain, inventories)

    assert report.reference_tokenization_issue_count == 1
    assert "reference_tokenization_issues" in report.screening_blockers
    assert all(case.overall_decision != "clear" for case in report.cases)
    assert all(
        any("reference_tokenization_issue" in blocker for blocker in case.blockers)
        for case in report.cases
    )


def test_builder_precomparison_work_cap_abstains_without_token_measurements(
    tmp_path: Path,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=_identifier_stream("candidate", 120),
        exposure_text=_identifier_stream("exposure", 120),
    )
    policy = _policy_with_updates(
        maximum_similarity_pairs=1,
        maximum_recorded_matches=1,
    )

    _, report = _build_screening(chain, inventories, policy=policy)

    assert report.similarity_work_status == "abstained_resource_limit"
    assert report.similarity_work_blockers == ("similarity_pair_budget_exceeded",)
    assert report.attempted_similarity_pair_count == 0
    assert report.matches == ()
    assert all(case.probe_evaluation_status == "not_run_resource_limit" for case in report.cases)
    assert all(case.file_content_token_count == 0 for case in report.cases)
    assert all(case.overall_decision == "abstain" for case in report.cases)

    forged = report.model_dump(mode="json", exclude={"screening_id"})
    forged["similarity_work_blockers"] = sorted(
        [*forged["similarity_work_blockers"], "recorded_match_budget_exceeded"]
    )
    forged["screening_blockers"] = sorted(
        [*forged["screening_blockers"], "recorded_match_budget_exceeded"]
    )
    forged["attempted_similarity_pair_count"] = 1
    with pytest.raises(ValidationError, match="invalid work-blocker state"):
        near_duplicate_core.near_duplicate_verification_payload_with_id(forged)


def test_builder_recorded_match_cap_discards_partial_results_and_abstains(
    tmp_path: Path,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    selected_text = chain.packet.cases[0].source_text
    inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=selected_text,
        exposure_text=selected_text,
    )
    policy = _policy_with_updates(maximum_recorded_matches=1)

    _, report = _build_screening(chain, inventories, policy=policy)

    assert report.similarity_work_status == "abstained_resource_limit"
    assert report.similarity_work_blockers == ("recorded_match_budget_exceeded",)
    assert 0 < report.attempted_similarity_pair_count <= report.planned_similarity_pair_count
    assert report.matches == ()
    assert all(case.probe_evaluation_status == "evaluated" for case in report.cases)
    assert all(case.overall_decision == "abstain" for case in report.cases)

    forged = report.model_dump(mode="json", exclude={"screening_id"})
    for case in forged["cases"]:
        case["probe_evaluation_status"] = "not_run_resource_limit"
        for field in (
            "file_content_token_count",
            "unit_content_token_count",
            "file_content_shingle_count",
            "unit_content_shingle_count",
            "hard_match_count",
            "gray_match_count",
        ):
            case[field] = 0
        case["file_tokenization_issues"] = []
        case["unit_tokenization_issues"] = []
    with pytest.raises(ValidationError, match="runtime match overflow requires evaluated probes"):
        near_duplicate_core.near_duplicate_verification_payload_with_id(forged)


def test_report_is_strict_self_hashed_rebuildable_and_cross_chain_safe(tmp_path: Path) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path / "first")
    inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=_identifier_stream("candidate", 120),
        exposure_text=_identifier_stream("exposure", 120),
    )
    policy, report = _build_screening(chain, inventories)
    verify_tag_truth_v2_near_duplicate_verification(
        report,
        policy=policy,
        provenance=chain.report,
        selection=chain.selection,
        packet=chain.packet,
        consensus=chain.consensus,
        reference_inventories=inventories,
    )
    raw = canonical_json(report.model_dump(mode="json")).encode("utf-8")
    assert parse_tag_truth_v2_near_duplicate_verification(raw) == report
    report_path = tmp_path / "screening.json"
    report_path.write_bytes(raw)
    assert load_tag_truth_v2_near_duplicate_verification(report_path) == report
    report_symlink = tmp_path / "screening-link.json"
    report_symlink.symlink_to(report_path)
    with pytest.raises(ValueError, match="non-symlink|regular"):
        load_tag_truth_v2_near_duplicate_verification(report_symlink)

    with pytest.raises(ValidationError, match="extra"):
        TagTruthV2NearDuplicateVerification.model_validate(
            {**report.model_dump(mode="json"), "unknown": True}
        )
    duplicate_key = raw.replace(b"{", b'{"schema_version":"forged",', 1)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_tag_truth_v2_near_duplicate_verification(duplicate_key)
    forged = report.model_dump(mode="json")
    forged["seal_tree_id"] = "f" * 40
    with pytest.raises(ValidationError, match="screening ID"):
        TagTruthV2NearDuplicateVerification.model_validate(forged)

    other = _build_near_duplicate_cli_chain(
        tmp_path / "other",
        hostile_import_shadows=True,
    )
    other_inventories = _reference_inventories_for_texts(
        other,
        candidate_text=_identifier_stream("candidate", 120),
        exposure_text=_identifier_stream("exposure", 120),
    )
    with pytest.raises(ValueError):
        verify_tag_truth_v2_near_duplicate_verification(
            report,
            policy=policy,
            provenance=other.report,
            selection=other.selection,
            packet=other.packet,
            consensus=other.consensus,
            reference_inventories=other_inventories,
        )


def test_rehashed_report_cannot_forge_case_match_counts(tmp_path: Path) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=_identifier_stream("candidate", 120),
        exposure_text=_identifier_stream("exposure", 120),
    )
    _, report = _build_screening(chain, inventories)
    payload = report.model_dump(mode="json", exclude={"screening_id"})
    payload["cases"][0]["hard_match_count"] += 1

    with pytest.raises(ValidationError, match="hard-match count"):
        near_duplicate_core.near_duplicate_verification_payload_with_id(payload)


def test_rehashed_report_cannot_forge_planned_pair_count(tmp_path: Path) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=_identifier_stream("candidate", 120),
        exposure_text=_identifier_stream("exposure", 120),
    )
    _, report = _build_screening(chain, inventories)
    payload = report.model_dump(mode="json", exclude={"screening_id"})
    payload["planned_similarity_pair_count"] = 0
    payload["attempted_similarity_pair_count"] = 0

    with pytest.raises(ValidationError, match="planned similarity-pair count"):
        near_duplicate_core.near_duplicate_verification_payload_with_id(payload)


def test_rehashed_report_cannot_claim_shape_signal_as_hard_duplicate(tmp_path: Path) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    selected_text = chain.packet.cases[0].source_text
    inventories = _reference_inventories_for_texts(
        chain,
        candidate_text=selected_text,
        exposure_text=_identifier_stream("exposure", 120),
    )
    _, report = _build_screening(chain, inventories)
    payload = report.model_dump(mode="json", exclude={"screening_id"})
    assert payload["matches"]
    payload["matches"][0]["signals"] = ["normalized_shape_token_stream_equal"]

    with pytest.raises(ValidationError, match="shape-only signals"):
        near_duplicate_core.near_duplicate_verification_payload_with_id(payload)


def test_cli_rejects_non_isolated_python_before_loading_standard_library_inputs() -> None:
    completed = subprocess.run(
        [sys.executable, "-B", str(CLI), "--help"],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires Python isolated mode (-I)" in completed.stderr


def test_cli_valid_shadow_run_is_deterministic_and_does_not_publish(
    tmp_path: Path,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)

    first = _run_near_duplicate_cli(chain)
    second = _run_near_duplicate_cli(chain)

    assert first.returncode == 1, first.stderr
    assert second.returncode == 1, second.stderr
    assert first.stdout == second.stdout
    report = parse_tag_truth_v2_near_duplicate_verification(first.stdout.encode("utf-8"))
    assert report.near_duplicate_qualification_status == "not_qualified_policy_unapproved"
    assert report.candidate_execution_status == "not_run"
    assert report.screening_outcome in {"clean", "potential_duplicate", "review_required"}


def test_cli_disables_repository_configured_fsmonitor(tmp_path: Path) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path / "chain")
    marker = tmp_path / "fsmonitor-called"
    monitor = tmp_path / "fsmonitor.sh"
    monitor.write_text(
        f"#!/bin/sh\nprintf called > '{marker}'\nexit 1\n",
        encoding="utf-8",
    )
    monitor.chmod(0o755)
    _git(chain.project_root, "config", "core.fsmonitor", str(monitor))
    _git(chain.source_root, "config", "core.fsmonitor", str(monitor))

    control = subprocess.run(
        ["git", "-C", str(chain.project_root), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert control.returncode == 0
    assert marker.is_file()
    marker.unlink()

    completed = _run_near_duplicate_cli(chain)

    assert completed.returncode == 1, completed.stderr
    assert not marker.exists()


@pytest.mark.parametrize("receipt_count", [1, 3])
def test_cli_requires_exactly_two_receipts(tmp_path: Path, receipt_count: int) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    receipts = (chain.artifact_paths[2],) * receipt_count

    completed = _run_near_duplicate_cli(chain, receipts=receipts)

    assert completed.returncode == 2
    assert "exactly two --receipt" in completed.stderr


@pytest.mark.parametrize("mutation", ["tracked_dirty", "untracked", "pycache"])
def test_cli_project_worktree_mutations_fail_closed(tmp_path: Path, mutation: str) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    if mutation == "tracked_dirty":
        _write(chain.project_root, "candidate.txt", "dirty candidate\n")
    elif mutation == "untracked":
        _write(chain.project_root, "untracked.tmp", "untracked\n")
    elif mutation == "pycache":
        _write(chain.project_root, "tools/__pycache__/hostile.pyc", b"hostile cache")
    else:  # pragma: no cover
        raise AssertionError(mutation)

    completed = _run_near_duplicate_cli(chain)

    assert completed.returncode == 2


def test_cli_rejects_symbolic_short_and_non_head_seals(tmp_path: Path) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)

    for seal_revision in ("HEAD", chain.seal_revision[:12], chain.report.candidate_commit):
        completed = _run_near_duplicate_cli(chain, seal_revision=seal_revision)
        assert completed.returncode == 2


def test_cli_policy_must_be_identical_at_candidate_seal_and_current_head(
    tmp_path: Path,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    policy_path = chain.project_root / POLICY_RELATIVE_PATH
    policy_path.write_bytes(policy_path.read_bytes() + b"\n")
    new_seal, _ = _commit_all(chain.project_root, "hostile policy changed after candidate freeze")

    changed_after_candidate = _run_near_duplicate_cli(chain, seal_revision=new_seal)
    assert changed_after_candidate.returncode == 2
    assert "policy Git blob changed after candidate freeze" in changed_after_candidate.stderr

    _git(chain.project_root, "update-index", "--assume-unchanged", POLICY_RELATIVE_PATH)
    policy_path.write_bytes(policy_path.read_bytes() + b" ")
    current_drift = _run_near_duplicate_cli(chain, seal_revision=new_seal)
    assert current_drift.returncode == 2


def test_cli_rejects_policy_symlink_and_noncanonical_policy_path(tmp_path: Path) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    policy_path = chain.project_root / POLICY_RELATIVE_PATH
    replacement = chain.project_root / "replacement-policy.json"
    replacement.write_bytes(policy_path.read_bytes())
    _git(chain.project_root, "update-index", "--assume-unchanged", POLICY_RELATIVE_PATH)
    policy_path.unlink()
    policy_path.symlink_to(replacement)

    symlink = _run_near_duplicate_cli(chain)
    assert symlink.returncode == 2

    clean_chain = _build_near_duplicate_cli_chain(tmp_path / "other")
    relative_paths = tuple(
        path.relative_to(clean_chain.project_root).as_posix() for path in clean_chain.artifact_paths
    )
    noncanonical = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(clean_chain.project_root / "tools/screen_tag_truth_v2_near_duplicates.py"),
            "--selection",
            relative_paths[0],
            "--packet",
            relative_paths[1],
            "--receipt",
            relative_paths[2],
            "--receipt",
            relative_paths[3],
            "--consensus",
            relative_paths[4],
            "--source-root",
            str(clean_chain.source_root),
            "--seal-revision",
            clean_chain.seal_revision,
            "--provenance-verification",
            str(clean_chain.provenance_path),
            "--policy",
            f"./{POLICY_RELATIVE_PATH}",
        ],
        cwd=clean_chain.project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert noncanonical.returncode == 2


@pytest.mark.parametrize(
    "mutation",
    ["inside", "symlink", "parent_symlink", "fifo", "oversize"],
)
def test_cli_requires_external_regular_non_symlink_provenance(
    tmp_path: Path,
    mutation: str,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    if mutation == "inside":
        path = chain.project_root / "inside-provenance.json"
    elif mutation == "symlink":
        path = tmp_path / "provenance-link.json"
        path.symlink_to(chain.provenance_path)
    elif mutation == "parent_symlink":
        path = tmp_path / "linked-parent" / "provenance.json"
        (tmp_path / "linked-parent").symlink_to(
            chain.provenance_path.parent,
            target_is_directory=True,
        )
    elif mutation == "fifo":
        path = tmp_path / "provenance.pipe"
        os.mkfifo(path)
    elif mutation == "oversize":
        path = tmp_path / "oversize-provenance.json"
        path.write_bytes(b"x" * (16 * 1024 * 1024 + 1))
    else:  # pragma: no cover
        raise AssertionError(mutation)

    completed = _run_near_duplicate_cli(chain, provenance_path=path)

    assert completed.returncode == 2


def test_cli_isolated_import_path_ignores_repository_and_tools_shadows(
    tmp_path: Path,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path, hostile_import_shadows=True)

    completed = _run_near_duplicate_cli(chain)

    assert completed.returncode == 1, completed.stderr


def test_cli_preflight_and_scanners_ignore_hostile_process_git_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    hostile = tmp_path / "hostile-git-environment"
    _init_git_repository(hostile)
    _write(hostile, "hostile.txt", "must not redirect preflight\n")
    _commit_all(hostile, "hostile redirect target")
    _set_hostile_git_environment(monkeypatch, hostile)

    completed = _run_near_duplicate_cli(chain)

    assert completed.returncode == 1, completed.stderr


def test_cli_ignores_git_replace_objects_but_rejects_legacy_grafts(tmp_path: Path) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    unrelated = provenance_test_support._unrelated_commit(chain.project_root)
    _git(chain.project_root, "replace", chain.report.candidate_commit, unrelated)
    try:
        replacement = _run_near_duplicate_cli(chain)
    finally:
        _git(chain.project_root, "replace", "-d", chain.report.candidate_commit)
    assert replacement.returncode == 1, replacement.stderr

    grafts = chain.project_root / ".git/info/grafts"
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(
        f"{chain.seal_revision} {chain.report.candidate_commit}\n",
        encoding="utf-8",
    )
    grafted = _run_near_duplicate_cli(chain)
    assert grafted.returncode == 2


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_near_duplicate.py",
        "tools/tag_truth_v2_near_duplicate_preflight.py",
        "tools/screen_tag_truth_v2_near_duplicates.py",
    ],
)
def test_cli_rejects_near_duplicate_verifier_closure_drift(
    tmp_path: Path,
    relative_path: str,
) -> None:
    chain = _build_near_duplicate_cli_chain(tmp_path)
    path = chain.project_root / relative_path
    _git(chain.project_root, "update-index", "--assume-unchanged", relative_path)
    path.write_bytes(path.read_bytes() + b"\n# hostile hidden drift\n")

    completed = _run_near_duplicate_cli(chain)

    assert completed.returncode == 2


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_near_duplicate.py",
        "tools/tag_truth_v2_near_duplicate_preflight.py",
        "tools/screen_tag_truth_v2_near_duplicates.py",
    ],
)
def test_cli_rejects_verifier_semantic_drift_between_candidate_and_seal(
    tmp_path: Path,
    relative_path: str,
) -> None:
    chain = _build_near_duplicate_cli_chain(
        tmp_path,
        mutate_runtime_after_candidate=relative_path,
    )

    completed = _run_near_duplicate_cli(chain)

    assert completed.returncode == 2
    assert "changed after candidate freeze" in completed.stderr
