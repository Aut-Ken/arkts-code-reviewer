from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    TagContractSnapshot,
    TagTruthV2Repository,
    bytes_hash,
    canonical_hash,
    canonical_json,
    derive_source_family_id,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_provenance import (
    CapturedCommittedArtifact,
    TagTruthV2ProvenanceVerification,
    build_tag_truth_v2_provenance_verification,
    load_tag_truth_v2_provenance_verification,
    parse_tag_truth_v2_provenance_verification,
    provenance_verification_payload_with_id,
    verify_tag_truth_v2_provenance_verification,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review import (
    TagTruthV2Consensus,
    TagTruthV2ReviewReceipt,
    build_tag_truth_v2_consensus,
    seal_tag_truth_v2_review_receipt_payload,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    DevelopmentTruthExclusionSnapshot,
    DevelopmentTruthExclusionSource,
    TagTruthV2ReviewPacket,
    TagTruthV2Selection,
    build_tag_truth_v2_review_packet,
    candidate_freeze_payload_with_id,
    development_exclusions_payload_with_id,
    review_policy_payload_with_id,
    seal_tag_truth_v2_selection_payload,
    selection_policy_payload_with_id,
    verify_tag_truth_v2_development_exclusions,
    verify_tag_truth_v2_selection_checkout,
    verify_tag_truth_v2_selection_exposure,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REMOTE = "https://gitcode.com/openharmony/applications_app_samples.git"
TYPED_VERIFIER_CLOSURE = (
    "src/arkts_code_reviewer/__init__.py",
    "src/arkts_code_reviewer/feature_routing_validation/__init__.py",
    "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2.py",
    "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_selection.py",
    "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_review.py",
    "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_provenance.py",
    "tests/evaluation/tag_retrieval/manifest.json",
    "tools/tag_truth_v2_seal_preflight.py",
    "tools/verify_tag_truth_v2_git_seal.py",
)


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _init_repository(root: Path, remote: str | None = None) -> None:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tag-truth@example.invalid")
    _git(root, "config", "user.name", "Tag Truth Fixture")
    if remote is not None:
        _git(root, "remote", "add", "origin", remote)


def _write(root: Path, relative_path: str, text: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_json(path: Path, value: object) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _contract() -> TagContractSnapshot:
    payload: dict[str, object] = {
        "schema_version": "tag-contract-snapshot-v1",
        "tag_id": "has_network",
        "version": "network-v1",
        "axes_relationship": "independent",
        "exact_semantics": {
            "positive": "The selected ReviewUnit has exact network semantics.",
            "negative": "The selected ReviewUnit does not have exact network semantics.",
            "abstain": "The exact semantics require a taxonomy decision.",
        },
        "routing_semantics": {
            "positive": "The file contains a conservative network routing hint.",
            "negative": "The file does not contain a network routing hint.",
            "abstain": "The routing semantics require a taxonomy decision.",
        },
    }
    payload["contract_fingerprint"] = canonical_hash("tag-contract-snapshot", payload)
    return TagContractSnapshot.model_validate(payload)


def _source_text(symbol: str, marker: str) -> str:
    return f"// {marker}\n@Component\nstruct {symbol} {{\n  build() {{}}\n}}\n"


@dataclass(frozen=True)
class _SourceFixture:
    root: Path
    exposure_revision: str
    exposure_tree_id: str
    selection_revision: str
    selection_tree_id: str
    development_truth: DevelopmentTruthExclusionSnapshot
    development_manifest: dict[str, object]
    selected_paths: tuple[str, str]
    selected_texts: tuple[str, str]


def _build_source_repository(root: Path) -> _SourceFixture:
    _init_repository(root, SOURCE_REMOTE)
    development_path = "samples/dev/entry/src/main/ets/Dev.ets"
    development_text = _source_text("Dev", "development exposure")
    _write(root, development_path, development_text)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "candidate exposure")
    exposure_revision = _git(root, "rev-parse", "HEAD")
    exposure_tree_id = _git(root, "rev-parse", "HEAD^{tree}")

    selected_paths = (
        "samples/alpha/entry/src/main/ets/Alpha.ets",
        "samples/beta/entry/src/main/ets/Beta.ets",
    )
    selected_texts = (
        _source_text("Alpha", "whole file alpha"),
        _source_text("Beta", "whole file beta"),
    )
    for path, text in zip(selected_paths, selected_texts, strict=True):
        _write(root, path, text)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "independent selection sources")
    selection_revision = _git(root, "rev-parse", "HEAD")
    selection_tree_id = _git(root, "rev-parse", "HEAD^{tree}")

    development_manifest: dict[str, object] = {
        "schema_version": "tag-retrieval-truth-v2",
        "evaluation_boundary": {"dataset_role": "development_regression"},
        "repository": {
            "source_id": "applications-app-samples",
            "repository": "applications_app_samples",
            "remote": SOURCE_REMOTE,
            "revision": exposure_revision,
        },
        "sources": [
            {
                "source_family_id": derive_source_family_id(development_path),
                "path": development_path,
                "content_sha256": bytes_hash(development_text.encode("utf-8")),
            }
        ],
    }
    truth_fingerprint = canonical_hash("tag-retrieval-truth", development_manifest)
    development_truth = DevelopmentTruthExclusionSnapshot(
        truth_suite_fingerprint=truth_fingerprint,
        repository_source_id="applications-app-samples",
        repository_name="applications_app_samples",
        repository_origin=SOURCE_REMOTE,
        repository_revision=exposure_revision,
        sources=(
            DevelopmentTruthExclusionSource(
                source_family_id=derive_source_family_id(development_path),
                path=development_path,
                content_sha256=bytes_hash(development_text.encode("utf-8")),
            ),
        ),
    )
    return _SourceFixture(
        root=root,
        exposure_revision=exposure_revision,
        exposure_tree_id=exposure_tree_id,
        selection_revision=selection_revision,
        selection_tree_id=selection_tree_id,
        development_truth=development_truth,
        development_manifest=development_manifest,
        selected_paths=selected_paths,
        selected_texts=selected_texts,
    )


def _candidate_commit(root: Path) -> str:
    _init_repository(root)
    _write(root, "candidate.txt", "frozen candidate\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "candidate freeze")
    return _git(root, "rev-parse", "HEAD")


def _selection(
    source: _SourceFixture,
    *,
    candidate_commit: str,
) -> TagTruthV2Selection:
    contract = _contract()
    candidate_freeze = candidate_freeze_payload_with_id(
        {
            "candidate_commit": candidate_commit,
            "target_tag_id": contract.tag_id,
            "tag_contract_fingerprint": contract.contract_fingerprint,
            "feature_config_fingerprint": f"feature-config:sha256:{'7' * 64}",
            "exposure_repository_source_id": "applications-app-samples",
            "exposure_revision": source.exposure_revision,
            "exposure_tree_id": source.exposure_tree_id,
            "exposure_scope": "entire_tracked_repository",
            "runtime_verification_status": "deferred_to_candidate_runner",
        }
    )
    truth = source.development_truth
    exclusions = development_exclusions_payload_with_id(
        {
            "truth_suite_fingerprint": truth.truth_suite_fingerprint,
            "source_family_ids": sorted({item.source_family_id for item in truth.sources}),
            "source_paths": sorted(item.path for item in truth.sources),
            "content_sha256": sorted({item.content_sha256 for item in truth.sources}),
        }
    )
    selection_policy = selection_policy_payload_with_id(
        {
            "schema_version": "tag-truth-selection-policy-v1",
            "policy_version": "generic-blind-v1",
            "approval_status": "draft_not_approved",
            "dataset_kind": "purposive_proxy_stratified_challenge",
            "natural_prevalence_claimed": False,
            "near_duplicate_check_status": "not_qualified",
            "max_cases_per_source_family": 1,
            "selected_case_count": 2,
            "strata": [
                {"stratum_id": "api_surface_dense", "selected_case_count": 1},
                {"stratum_id": "api_surface_sparse", "selected_case_count": 1},
            ],
            "post_review_minimums": {
                "minimum_exact_positive_cases": 1,
                "minimum_exact_negative_cases": 1,
                "minimum_routing_positive_cases": 1,
                "minimum_routing_negative_cases": 1,
            },
        }
    )
    review_policy = review_policy_payload_with_id(
        {
            "schema_version": "tag-truth-review-policy-v1",
            "version": "generic-dual-axis-v1",
            "approval_status": "draft_not_approved",
            "owner_instruction": "Select the ReviewUnit containing the probe line.",
            "exact_instruction": "Judge exact Tag applicability inside that ReviewUnit.",
            "routing_instruction": "Judge conservative routing applicability for the file.",
            "abstain_instruction": "Abstain when taxonomy or ownership is ambiguous.",
        }
    )
    sources: list[dict[str, object]] = []
    cases: list[dict[str, object]] = []
    for index, (path, text) in enumerate(
        zip(source.selected_paths, source.selected_texts, strict=True),
        start=1,
    ):
        alias = f"src{index:03d}"
        family = derive_source_family_id(path)
        sources.append(
            {
                "alias": alias,
                "repository_source_id": "applications-app-samples",
                "origin": SOURCE_REMOTE,
                "revision": source.selection_revision,
                "path": path,
                "content_sha256": bytes_hash(text.encode("utf-8")),
                "line_count": len(text.encode("utf-8").splitlines()),
                "source_kind": "main",
                "app_scope": family,
                "source_family_id": family,
            }
        )
        cases.append(
            {
                "case_id": f"case-{index:016x}",
                "source_alias": alias,
                "probe_line": 3,
                "proxy_stratum_id": ("api_surface_dense" if index == 1 else "api_surface_sparse"),
                "selection_rank": index,
            }
        )
    draft: dict[str, object] = {
        "schema_version": "tag-truth-v2-selection-v1",
        "suite_id": "network-blind-v1",
        "dataset_role": "independent_blind_challenge",
        "data_qualification_status": "not_qualified",
        "data_qualification_reasons": sorted(
            [
                "candidate_runtime_verification_deferred",
                "external_selection_not_verified",
                "human_review_not_completed",
                "near_duplicate_verifier_unavailable",
                "review_policy_not_approved",
                "selection_policy_not_approved",
                "selector_identity_not_authenticated",
                "stage2a_selection_only",
            ]
        ),
        "repository": TagTruthV2Repository(
            source_id="applications-app-samples",
            repository="applications_app_samples",
            origin=SOURCE_REMOTE,
            revision=source.selection_revision,
        ).model_dump(mode="json"),
        "candidate_freeze": candidate_freeze,
        "development_exclusions": exclusions,
        "selection_policy": selection_policy,
        "selector_attestation": {
            "selector_id": "custodian-a",
            "selector_role": "independent_dataset_custodian",
            "candidate_design_participant": False,
            "candidate_configuration_seen": False,
            "candidate_output_seen": False,
            "selected_after_candidate_freeze": True,
            "attested_on": "2026-07-16",
            "process_note": "Fixture selection was prepared after candidate freeze.",
        },
        "tag_contract": contract.model_dump(mode="json"),
        "review_policy": review_policy,
        "sources": sources,
        "cases": cases,
    }
    return seal_tag_truth_v2_selection_payload(draft)


def _receipt(
    packet: TagTruthV2ReviewPacket,
    *,
    round_id: str,
    reviewer_id: str,
    routing_disagreement: bool = False,
) -> TagTruthV2ReviewReceipt:
    decisions: list[dict[str, object]] = []
    symbols = ("Alpha", "Beta")
    for index, case in enumerate(packet.cases):
        routing_label = "negative" if routing_disagreement and index == 0 else "positive"
        decisions.append(
            {
                "case_id": case.case_id,
                "review_unit": {
                    "unit_kind": "struct",
                    "qualified_symbol": symbols[index],
                    "source_span": {"start_line": 1, "end_line": case.line_count},
                },
                "exact": {
                    "label": "positive",
                    "evidence_lines": [2, 3],
                    "rationale": f"{reviewer_id} exact rationale for {case.case_id}.",
                },
                "routing": {
                    "label": routing_label,
                    "evidence_lines": [1, 3],
                    "rationale": f"{reviewer_id} routing rationale for {case.case_id}.",
                },
            }
        )
    return seal_tag_truth_v2_review_receipt_payload(
        {
            "schema_version": "tag-truth-v2-review-receipt-v1",
            "round_id": round_id,
            "selection_id": packet.selection_id,
            "packet_id": packet.packet_id,
            "suite_id": packet.suite_id,
            "target_tag_id": packet.target_tag_id,
            "tag_contract_fingerprint": packet.tag_contract.contract_fingerprint,
            "review_policy_fingerprint": packet.review_policy.policy_fingerprint,
            "reviewer": {
                "reviewer_id": reviewer_id,
                "reviewer_kind": "human",
                "reviewer_role": "arkts_domain_reviewer",
                "affiliation": "Independent fixture review",
                "candidate_design_participant": False,
                "selection_participant": False,
            },
            "blinding": {
                "candidate_output_seen": False,
                "candidate_configuration_seen": False,
                "selection_manifest_seen": False,
                "review_completed_before_unblinding": True,
                "attested_at": "2026-07-16T09:00:00Z",
            },
            "recorded_at": "2026-07-16T09:05:00Z",
            "decisions": decisions,
        }
    )


@dataclass(frozen=True)
class _SealedChain:
    project_root: Path
    source: _SourceFixture
    selection: TagTruthV2Selection
    packet: TagTruthV2ReviewPacket
    receipts: tuple[TagTruthV2ReviewReceipt, TagTruthV2ReviewReceipt]
    consensus: TagTruthV2Consensus
    candidate_commit: str
    seal_revision: str
    seal_tree_id: str
    artifact_paths: tuple[Path, Path, Path, Path, Path]
    artifacts: tuple[CapturedCommittedArtifact, ...]


def _capture(
    project_root: Path,
    seal_revision: str,
    role: str,
    path: Path,
) -> CapturedCommittedArtifact:
    relative = path.resolve().relative_to(project_root.resolve()).as_posix()
    raw = subprocess.run(
        ["git", "-C", str(project_root), "show", f"{seal_revision}:{relative}"],
        check=True,
        capture_output=True,
    ).stdout
    blob_id = _git(project_root, "rev-parse", f"{seal_revision}:{relative}")
    return CapturedCommittedArtifact(
        role=role,  # type: ignore[arg-type]
        path=relative,
        raw_bytes=raw,
        git_blob_id=blob_id,
    )


def _build_chain(
    tmp_path: Path,
    *,
    unresolved: bool = False,
    include_cli_runtime: bool = False,
) -> _SealedChain:
    project_root = tmp_path / "project"
    candidate_commit = _candidate_commit(project_root)
    source = _build_source_repository(tmp_path / "source")
    selection = _selection(source, candidate_commit=candidate_commit)
    if include_cli_runtime:
        shutil.copytree(
            ROOT / "src",
            project_root / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        _write_json(
            project_root / "tests/evaluation/tag_retrieval/manifest.json",
            source.development_manifest,
        )
        tools_root = project_root / "tools"
        tools_root.mkdir(parents=True)
        for tool_name in (
            "tag_truth_v2_seal_preflight.py",
            "verify_tag_truth_v2_git_seal.py",
        ):
            shutil.copy2(ROOT / "tools" / tool_name, tools_root / tool_name)
        _git(project_root, "add", "src", "tests", "tools")
        _git(project_root, "commit", "-qm", "freeze provenance verifier runtime")
    verify_tag_truth_v2_development_exclusions(
        selection,
        source.development_truth,
        source.root,
    )
    verify_tag_truth_v2_selection_exposure(selection, source.root)
    checkout = verify_tag_truth_v2_selection_checkout(selection, source.root)
    packet = build_tag_truth_v2_review_packet(selection, checkout)
    first = _receipt(packet, round_id="round-a", reviewer_id="reviewer-a")
    second = _receipt(
        packet,
        round_id="round-b",
        reviewer_id="reviewer-b",
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
    values = (selection, packet, first, second, consensus)
    for path, value in zip(artifact_paths, values, strict=True):
        _write_json(path, value.model_dump(mode="json"))
    _git(project_root, "add", "artifacts")
    _git(project_root, "commit", "-qm", "seal reviewed artifacts")
    seal_revision = _git(project_root, "rev-parse", "HEAD")
    seal_tree_id = _git(project_root, "rev-parse", "HEAD^{tree}")
    roles = ("selection", "packet", "receipt", "receipt", "consensus")
    artifacts = tuple(
        _capture(project_root, seal_revision, role, path)
        for role, path in zip(roles, artifact_paths, strict=True)
    )
    return _SealedChain(
        project_root=project_root,
        source=source,
        selection=selection,
        packet=packet,
        receipts=(first, second),
        consensus=consensus,
        candidate_commit=candidate_commit,
        seal_revision=seal_revision,
        seal_tree_id=seal_tree_id,
        artifact_paths=artifact_paths,  # type: ignore[arg-type]
        artifacts=artifacts,
    )


@pytest.fixture
def sealed_chain(tmp_path: Path) -> _SealedChain:
    return _build_chain(tmp_path)


@pytest.fixture
def unresolved_chain(tmp_path: Path) -> _SealedChain:
    return _build_chain(tmp_path, unresolved=True)


@pytest.fixture
def preflight_chain(tmp_path: Path) -> _SealedChain:
    return _build_chain(tmp_path, include_cli_runtime=True)


def _report(chain: _SealedChain) -> TagTruthV2ProvenanceVerification:
    return build_tag_truth_v2_provenance_verification(
        seal_revision=chain.seal_revision,
        seal_tree_id=chain.seal_tree_id,
        artifacts=chain.artifacts,
        source_root=chain.source.root,
        development_truth=chain.source.development_truth,
    )


def _blob_id(raw: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(raw)}\0".encode("ascii") + raw,
        usedforsecurity=False,
    ).hexdigest()


def _preflight_artifact_paths(
    chain: _SealedChain,
) -> list[tuple[str, str]]:
    roles = ("selection", "review_packet", "review_receipt", "review_receipt", "consensus")
    return [
        (role, path.relative_to(chain.project_root).as_posix())
        for role, path in zip(roles, chain.artifact_paths, strict=True)
    ]


def _run_preflight(
    chain: _SealedChain,
    *,
    repository_root: Path | None = None,
    seal_revision: str | None = None,
    artifact_paths: list[tuple[str, str]] | None = None,
    import_project_first: bool = False,
) -> subprocess.CompletedProcess[str]:
    script = (
        "import json,runpy,sys; "
        + (
            f"sys.path.insert(0,{str(ROOT / 'src')!r}); "
            "import arkts_code_reviewer.feature_routing_validation.tag_truth_v2; "
            if import_project_first
            else ""
        )
        + "namespace=runpy.run_path(sys.argv[1]); "
        "preflight=namespace['preflight_tag_truth_v2_git_seal']; "
        "\ntry:\n"
        " result=preflight(repository_root=sys.argv[2],seal_revision=sys.argv[3],"
        "artifact_paths=json.loads(sys.argv[4])); "
        "print(json.dumps({'seal_revision':result.seal_revision,'seal_tree_id':result.seal_tree_id}))\n"
        "except Exception as exc:\n print(str(exc),file=sys.stderr); raise SystemExit(2)"
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(ROOT / "tools/tag_truth_v2_seal_preflight.py"),
            str(repository_root or chain.project_root),
            seal_revision or chain.seal_revision,
            json.dumps(artifact_paths or _preflight_artifact_paths(chain)),
        ],
        cwd=chain.project_root,
        env={
            **os.environ,
            "PYTHONPATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )


def _run_git_seal_cli(
    chain: _SealedChain,
    *,
    receipts: tuple[Path, ...] | None = None,
    seal_revision: str | None = None,
) -> subprocess.CompletedProcess[str]:
    relative_paths = tuple(
        path.relative_to(chain.project_root).as_posix() for path in chain.artifact_paths
    )
    arguments = [
        "--selection",
        relative_paths[0],
        "--packet",
        relative_paths[1],
    ]
    for receipt in receipts or chain.artifact_paths[2:4]:
        arguments.extend(("--receipt", receipt.relative_to(chain.project_root).as_posix()))
    arguments.extend(
        (
            "--consensus",
            relative_paths[4],
            "--source-root",
            str(chain.source.root),
            "--seal-revision",
            seal_revision or chain.seal_revision,
        )
    )
    script = (
        "import runpy,sys; "
        "namespace=runpy.run_path(sys.argv[1]); "
        "code=namespace['main'](sys.argv[3:],repository_root=sys.argv[2]); "
        "forbidden=('arkts_code_reviewer.feature_routing.engine',"
        "'arkts_code_reviewer.feature_routing.config',"
        "'arkts_code_reviewer.feature_routing.matcher',"
        "'arkts_code_reviewer.code_analysis'); "
        "assert not any(name in sys.modules for name in forbidden); "
        "raise SystemExit(code)"
    )
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            script,
            str(ROOT / "tools/verify_tag_truth_v2_git_seal.py"),
            str(chain.project_root),
            *arguments,
        ],
        cwd=chain.project_root,
        env={
            **os.environ,
            "PYTHONPATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )


def _unrelated_commit(root: Path) -> str:
    tree_id = _git(root, "rev-parse", "HEAD^{tree}")
    completed = subprocess.run(
        ["git", "-C", str(root), "commit-tree", tree_id],
        input="unrelated root\n",
        check=True,
        capture_output=True,
        text=True,
        env=os.environ,
    )
    return completed.stdout.strip()


def _forge_commit_graph_parent(root: Path, child: str, forged_parent: str) -> None:
    _git(root, "commit-graph", "write", "--reachable")
    graph = root / ".git/objects/info/commit-graph"
    graph.chmod(graph.stat().st_mode | stat.S_IWUSR)
    raw = bytearray(graph.read_bytes())
    if raw[:4] != b"CGPH" or raw[5] != 1:
        raise AssertionError("hostile fixture requires a SHA-1 commit graph")
    offsets: dict[bytes, int] = {}
    for index in range(raw[6] + 1):
        position = 8 + index * 12
        offsets[bytes(raw[position : position + 4])] = struct.unpack(
            ">Q", raw[position + 4 : position + 12]
        )[0]
    oid_lookup = offsets[b"OIDL"]
    commit_data = offsets[b"CDAT"]
    object_ids = [bytes(raw[offset : offset + 20]) for offset in range(oid_lookup, commit_data, 20)]
    child_position = object_ids.index(bytes.fromhex(child))
    parent_position = object_ids.index(bytes.fromhex(forged_parent))
    parent_field = commit_data + child_position * 36 + 20
    raw[parent_field : parent_field + 4] = struct.pack(">I", parent_position)
    raw[-20:] = hashlib.sha1(raw[:-20], usedforsecurity=False).digest()
    graph.write_bytes(raw)


def _git_ancestry_returncode(
    root: Path,
    ancestor: str,
    descendant: str,
    *,
    disable_commit_graph: bool,
) -> int:
    arguments = ["git"]
    if disable_commit_graph:
        arguments.extend(("-c", "core.commitGraph=false"))
    arguments.extend(("-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant))
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    ).returncode


def _replace_capture(
    capture: CapturedCommittedArtifact,
    *,
    role: str | None = None,
    path: str | None = None,
    raw: bytes | object | None = None,
    git_blob_id: str | None = None,
) -> CapturedCommittedArtifact:
    replacement_raw = capture.raw_bytes if raw is None else raw
    return CapturedCommittedArtifact(
        role=capture.role if role is None else role,  # type: ignore[arg-type]
        path=capture.path if path is None else path,
        raw_bytes=replacement_raw,  # type: ignore[arg-type]
        git_blob_id=capture.git_blob_id if git_blob_id is None else git_blob_id,
    )


def _rehash_artifact(
    capture: CapturedCommittedArtifact,
    *,
    identity_field: str,
    prefix: str,
    mutation: Any,
) -> CapturedCommittedArtifact:
    payload = json.loads(capture.raw_bytes)
    payload.pop(identity_field)
    mutation(payload)
    payload[identity_field] = canonical_hash(prefix, payload)
    raw = (json.dumps(payload, sort_keys=True) + "\n").encode()
    return _replace_capture(capture, raw=raw, git_blob_id=_blob_id(raw))


def test_complete_provenance_report_is_deterministic_and_rebuildable(
    sealed_chain: _SealedChain,
) -> None:
    forward = _report(sealed_chain)
    reverse = build_tag_truth_v2_provenance_verification(
        seal_revision=sealed_chain.seal_revision,
        seal_tree_id=sealed_chain.seal_tree_id,
        artifacts=tuple(reversed(sealed_chain.artifacts)),
        source_root=sealed_chain.source.root,
        development_truth=sealed_chain.source.development_truth,
    )

    assert forward == reverse
    assert canonical_json(forward.model_dump(mode="json")) == canonical_json(
        reverse.model_dump(mode="json")
    )
    assert forward.integrity_status == "verified"
    assert forward.evidence_qualification_status == "not_qualified"
    assert forward.candidate_execution_status == "not_run"
    assert forward.consensus_status == "complete"
    assert forward.consensus_blockers == ()
    assert forward.candidate_commit == sealed_chain.candidate_commit
    assert forward.source_repository_tree_id == sealed_chain.source.selection_tree_id
    assert len(forward.sealed_artifacts) == 5
    verify_tag_truth_v2_provenance_verification(
        forward,
        seal_revision=sealed_chain.seal_revision,
        seal_tree_id=sealed_chain.seal_tree_id,
        artifacts=tuple(reversed(sealed_chain.artifacts)),
        source_root=sealed_chain.source.root,
        development_truth=sealed_chain.source.development_truth,
    )


def test_legal_unresolved_consensus_remains_a_not_qualified_integrity_report(
    unresolved_chain: _SealedChain,
) -> None:
    report = _report(unresolved_chain)

    assert report.integrity_status == "verified"
    assert report.consensus_status == "unresolved"
    assert report.consensus_blockers == ("unresolved_review_disagreement",)
    assert report.evidence_qualification_status == "not_qualified"
    assert report.candidate_execution_status == "not_run"


def test_report_is_closed_frozen_self_hashed_duplicate_safe_and_symlink_safe(
    sealed_chain: _SealedChain,
    tmp_path: Path,
) -> None:
    report = _report(sealed_chain)
    raw = json.dumps(report.model_dump(mode="json"), sort_keys=True).encode()
    assert parse_tag_truth_v2_provenance_verification(raw) == report

    with pytest.raises(ValidationError, match="frozen"):
        report.__setattr__("integrity_status", "changed")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_tag_truth_v2_provenance_verification(
            b'{"schema_version":"tag-truth-v2-provenance-verification-v1",'
            b'"schema_version":"tag-truth-v2-provenance-verification-v1"}'
        )
    forged = report.model_dump(mode="json")
    forged["candidate_execution_status"] = "run"
    with pytest.raises(ValueError):
        parse_tag_truth_v2_provenance_verification(json.dumps(forged).encode())
    extra = report.model_dump(mode="json")
    extra["release_ready"] = True
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        parse_tag_truth_v2_provenance_verification(json.dumps(extra).encode())
    with pytest.raises(ValueError, match="cannot contain verification_id"):
        provenance_verification_payload_with_id(report.model_dump(mode="json"))

    path = tmp_path / "verification.json"
    _write_json(path, report.model_dump(mode="json"))
    assert load_tag_truth_v2_provenance_verification(path) == report
    link = tmp_path / "verification-link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="regular non-symlink"):
        load_tag_truth_v2_provenance_verification(link)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_selection",
        "missing_packet",
        "missing_receipt",
        "missing_consensus",
        "extra_receipt",
        "role_collision",
        "duplicate_path",
        "absolute_path",
        "parent_traversal",
        "backslash_path",
        "non_bytes",
        "wrong_blob_id",
        "raw_byte_drift",
        "duplicate_receipt_identity",
        "duplicate_json",
        "extra_json_field",
    ],
)
def test_captured_artifact_hostile_mutations_fail_closed(
    sealed_chain: _SealedChain,
    mutation: str,
) -> None:
    artifacts = list(sealed_chain.artifacts)
    if mutation.startswith("missing_"):
        role = mutation.removeprefix("missing_")
        index = next(index for index, item in enumerate(artifacts) if item.role == role)
        artifacts.pop(index)
    elif mutation == "extra_receipt":
        artifacts.append(
            _replace_capture(
                artifacts[2],
                path="artifacts/receipt-extra.json",
            )
        )
    elif mutation == "role_collision":
        artifacts[0] = _replace_capture(artifacts[0], role="packet")
    elif mutation == "duplicate_path":
        artifacts[3] = _replace_capture(artifacts[3], path=artifacts[2].path)
    elif mutation == "absolute_path":
        artifacts[0] = _replace_capture(artifacts[0], path="/tmp/selection.json")
    elif mutation == "parent_traversal":
        artifacts[0] = _replace_capture(artifacts[0], path="../selection.json")
    elif mutation == "backslash_path":
        artifacts[0] = _replace_capture(artifacts[0], path=r"artifacts\selection.json")
    elif mutation == "non_bytes":
        artifacts[0] = _replace_capture(artifacts[0], raw="not-bytes")
    elif mutation == "wrong_blob_id":
        artifacts[0] = _replace_capture(artifacts[0], git_blob_id="0" * 40)
    elif mutation == "raw_byte_drift":
        artifacts[0] = _replace_capture(
            artifacts[0],
            raw=artifacts[0].raw_bytes + b" ",
        )
    elif mutation == "duplicate_receipt_identity":
        artifacts[3] = _replace_capture(
            artifacts[3],
            raw=artifacts[2].raw_bytes,
            git_blob_id=artifacts[2].git_blob_id,
        )
    elif mutation == "duplicate_json":
        raw = b'{"schema_version":"tag-truth-v2-selection-v1","schema_version":"x"}'
        artifacts[0] = _replace_capture(artifacts[0], raw=raw, git_blob_id=_blob_id(raw))
    elif mutation == "extra_json_field":
        payload = json.loads(artifacts[0].raw_bytes)
        payload["candidate_output"] = ["has_network"]
        raw = json.dumps(payload).encode()
        artifacts[0] = _replace_capture(artifacts[0], raw=raw, git_blob_id=_blob_id(raw))
    else:  # pragma: no cover - protects the mutation table itself
        raise AssertionError(mutation)

    with pytest.raises((ValueError, ValidationError)):
        build_tag_truth_v2_provenance_verification(
            seal_revision=sealed_chain.seal_revision,
            seal_tree_id=sealed_chain.seal_tree_id,
            artifacts=artifacts,
            source_root=sealed_chain.source.root,
            development_truth=sealed_chain.source.development_truth,
        )


@pytest.mark.parametrize(
    ("artifact_index", "identity_field", "prefix", "mutation"),
    [
        (0, "selection_id", "tag-truth-selection", "selection"),
        (1, "packet_id", "tag-truth-review-packet", "packet"),
        (2, "receipt_id", "tag-truth-review-receipt", "receipt"),
        (4, "consensus_id", "tag-truth-consensus", "consensus"),
    ],
)
def test_mixed_or_rehashed_chain_artifacts_fail_reconstruction(
    sealed_chain: _SealedChain,
    artifact_index: int,
    identity_field: str,
    prefix: str,
    mutation: str,
) -> None:
    artifacts = list(sealed_chain.artifacts)

    def mutate(payload: dict[str, Any]) -> None:
        if mutation == "selection":
            payload["suite_id"] = "different-suite"
        elif mutation == "packet":
            payload["cases"][0]["source_text"] = payload["cases"][0]["source_text"].replace(
                "Alpha", "Omega"
            )
        elif mutation == "receipt":
            payload["decisions"][0]["exact"]["rationale"] = "A substituted review vote."
        elif mutation == "consensus":
            payload["suite_id"] = "different-suite"
        else:  # pragma: no cover
            raise AssertionError(mutation)

    artifacts[artifact_index] = _rehash_artifact(
        artifacts[artifact_index],
        identity_field=identity_field,
        prefix=prefix,
        mutation=mutate,
    )
    with pytest.raises((ValueError, ValidationError)):
        build_tag_truth_v2_provenance_verification(
            seal_revision=sealed_chain.seal_revision,
            seal_tree_id=sealed_chain.seal_tree_id,
            artifacts=artifacts,
            source_root=sealed_chain.source.root,
            development_truth=sealed_chain.source.development_truth,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_head",
        "wrong_remote",
        "untracked",
        "tracked_dirty",
        "assume_unchanged",
        "subdirectory_root",
        "symlink_source",
        "development_truth_drift",
    ],
)
def test_source_checkout_and_provenance_mutations_fail_closed(
    sealed_chain: _SealedChain,
    mutation: str,
) -> None:
    source_root = sealed_chain.source.root
    development_truth = sealed_chain.source.development_truth
    if mutation == "wrong_head":
        _git(source_root, "checkout", "-q", sealed_chain.source.exposure_revision)
    elif mutation == "wrong_remote":
        _git(source_root, "remote", "set-url", "origin", "https://example.invalid/fork.git")
    elif mutation == "untracked":
        _write(source_root, "untracked.tmp", "dirty\n")
    elif mutation == "tracked_dirty":
        path = source_root / sealed_chain.selection.sources[0].path
        path.write_text(path.read_text(encoding="utf-8") + "// drift\n", encoding="utf-8")
    elif mutation == "assume_unchanged":
        relative = sealed_chain.selection.sources[0].path
        _git(source_root, "update-index", "--assume-unchanged", relative)
        path = source_root / relative
        path.write_text(path.read_text(encoding="utf-8") + "// hidden drift\n", encoding="utf-8")
    elif mutation == "subdirectory_root":
        source_root = (source_root / sealed_chain.selection.sources[0].path).parent
    elif mutation == "symlink_source":
        relative = sealed_chain.selection.sources[0].path
        _git(source_root, "update-index", "--assume-unchanged", relative)
        path = source_root / relative
        replacement = source_root / "replacement.ets"
        replacement.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(replacement)
    elif mutation == "development_truth_drift":
        development_truth = replace(
            development_truth,
            truth_suite_fingerprint=canonical_hash("tag-retrieval-truth", {"drift": True}),
        )
    else:  # pragma: no cover
        raise AssertionError(mutation)

    with pytest.raises((ValueError, ValidationError)):
        build_tag_truth_v2_provenance_verification(
            seal_revision=sealed_chain.seal_revision,
            seal_tree_id=sealed_chain.seal_tree_id,
            artifacts=sealed_chain.artifacts,
            source_root=source_root,
            development_truth=development_truth,
        )


def test_git_replace_cannot_change_selected_tree_identity(sealed_chain: _SealedChain) -> None:
    report = _report(sealed_chain)
    source_root = sealed_chain.source.root
    _git(
        source_root,
        "replace",
        "--graft",
        sealed_chain.source.selection_revision,
        check=True,
    )
    try:
        rebuilt = _report(sealed_chain)
    finally:
        _git(source_root, "replace", "-d", sealed_chain.source.selection_revision)

    assert rebuilt.source_repository_tree_id == report.source_repository_tree_id
    assert rebuilt.verification_id == report.verification_id


def test_stdlib_preflight_captures_exact_clean_seal_before_project_imports(
    preflight_chain: _SealedChain,
) -> None:
    completed = _run_preflight(preflight_chain)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["seal_revision"] == preflight_chain.seal_revision
    assert payload["seal_tree_id"] == preflight_chain.seal_tree_id


@pytest.mark.parametrize(
    "mutation",
    [
        "short_revision",
        "symbolic_revision",
        "uppercase_revision",
        "head_after_seal",
        "tracked_dirty",
        "untracked",
        "repository_subdirectory",
        "artifact_outside",
        "artifact_missing",
        "artifact_directory",
        "artifact_symlink",
        "duplicate_artifact_path",
        "wrong_role_order",
        "assume_unchanged_byte_drift",
        "candidate_not_ancestor",
        "legacy_git_grafts",
    ],
)
def test_git_seal_preflight_hostile_mutations_fail_closed(
    preflight_chain: _SealedChain,
    mutation: str,
) -> None:
    root = preflight_chain.project_root
    repository_root = root
    seal_revision = preflight_chain.seal_revision
    artifact_paths = _preflight_artifact_paths(preflight_chain)
    if mutation == "short_revision":
        seal_revision = seal_revision[:12]
    elif mutation == "symbolic_revision":
        seal_revision = "HEAD"
    elif mutation == "uppercase_revision":
        seal_revision = seal_revision.upper()
    elif mutation == "head_after_seal":
        _write(root, "after-seal.txt", "post seal\n")
        _git(root, "add", "after-seal.txt")
        _git(root, "commit", "-qm", "post seal descendant")
    elif mutation == "tracked_dirty":
        _write(root, "candidate.txt", "dirty tracked candidate\n")
    elif mutation == "untracked":
        _write(root, "untracked.tmp", "dirty untracked file\n")
    elif mutation == "repository_subdirectory":
        repository_root = root / "artifacts"
    elif mutation == "artifact_outside":
        outside = root.parent / "outside.json"
        outside.write_text("{}\n", encoding="utf-8")
        artifact_paths[1] = (artifact_paths[1][0], str(outside))
    elif mutation == "artifact_missing":
        artifact_paths[1] = (artifact_paths[1][0], "artifacts/missing.json")
    elif mutation == "artifact_directory":
        artifact_paths[1] = (artifact_paths[1][0], "artifacts")
    elif mutation == "artifact_symlink":
        relative = preflight_chain.artifact_paths[2].relative_to(root).as_posix()
        _git(root, "update-index", "--assume-unchanged", relative)
        preflight_chain.artifact_paths[2].unlink()
        preflight_chain.artifact_paths[2].symlink_to(preflight_chain.artifact_paths[3])
    elif mutation == "duplicate_artifact_path":
        artifact_paths[3] = (artifact_paths[3][0], artifact_paths[2][1])
    elif mutation == "wrong_role_order":
        artifact_paths[0] = ("review_packet", artifact_paths[0][1])
        artifact_paths[1] = ("selection", artifact_paths[1][1])
    elif mutation == "assume_unchanged_byte_drift":
        relative = preflight_chain.artifact_paths[0].relative_to(root).as_posix()
        _git(root, "update-index", "--assume-unchanged", relative)
        preflight_chain.artifact_paths[0].write_bytes(
            preflight_chain.artifact_paths[0].read_bytes() + b" "
        )
    elif mutation == "candidate_not_ancestor":
        unrelated = _unrelated_commit(root)
        selection_path = preflight_chain.artifact_paths[0]
        payload = json.loads(selection_path.read_bytes())
        freeze = payload["candidate_freeze"]
        freeze.pop("candidate_freeze_id")
        freeze["candidate_commit"] = unrelated
        freeze["candidate_freeze_id"] = canonical_hash("tag-truth-candidate-freeze", freeze)
        payload.pop("selection_id")
        payload["selection_id"] = canonical_hash("tag-truth-selection", payload)
        _write_json(selection_path, payload)
        _git(root, "add", selection_path.relative_to(root).as_posix())
        _git(root, "commit", "-qm", "hostile unrelated candidate reference")
        seal_revision = _git(root, "rev-parse", "HEAD")
    elif mutation == "legacy_git_grafts":
        grafts = root / ".git/info/grafts"
        grafts.parent.mkdir(parents=True, exist_ok=True)
        grafts.write_text(
            f"{seal_revision} {preflight_chain.candidate_commit}\n",
            encoding="utf-8",
        )
    else:  # pragma: no cover
        raise AssertionError(mutation)

    completed = _run_preflight(
        preflight_chain,
        repository_root=repository_root,
        seal_revision=seal_revision,
        artifact_paths=artifact_paths,
    )
    assert completed.returncode == 2, completed.stdout


def test_source_legacy_git_grafts_fail_closed(sealed_chain: _SealedChain) -> None:
    grafts = sealed_chain.source.root / ".git/info/grafts"
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(
        (f"{sealed_chain.source.selection_revision} {sealed_chain.source.exposure_revision}\n"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="legacy Git grafts are forbidden"):
        _report(sealed_chain)


def test_git_replace_cannot_forge_project_candidate_ancestry(
    preflight_chain: _SealedChain,
) -> None:
    unrelated = _unrelated_commit(preflight_chain.project_root)
    _git(preflight_chain.project_root, "replace", preflight_chain.candidate_commit, unrelated)
    try:
        completed = _run_preflight(preflight_chain)
    finally:
        _git(preflight_chain.project_root, "replace", "-d", preflight_chain.candidate_commit)

    assert completed.returncode == 0, completed.stderr


def test_forged_project_commit_graph_cannot_forge_candidate_ancestry(
    preflight_chain: _SealedChain,
) -> None:
    root = preflight_chain.project_root
    unrelated = _unrelated_commit(root)
    _git(root, "update-ref", "refs/heads/hostile-unrelated", unrelated)
    selection_path = preflight_chain.artifact_paths[0]
    payload = json.loads(selection_path.read_bytes())
    freeze = payload["candidate_freeze"]
    freeze.pop("candidate_freeze_id")
    freeze["candidate_commit"] = unrelated
    freeze["candidate_freeze_id"] = canonical_hash("tag-truth-candidate-freeze", freeze)
    payload.pop("selection_id")
    payload["selection_id"] = canonical_hash("tag-truth-selection", payload)
    _write_json(selection_path, payload)
    _git(root, "add", selection_path.relative_to(root).as_posix())
    _git(root, "commit", "-qm", "hostile seal with unrelated candidate")
    hostile_seal = _git(root, "rev-parse", "HEAD")
    _forge_commit_graph_parent(root, hostile_seal, unrelated)

    assert _git_ancestry_returncode(root, unrelated, hostile_seal, disable_commit_graph=False) == 0
    assert _git_ancestry_returncode(root, unrelated, hostile_seal, disable_commit_graph=True) == 1

    completed = _run_preflight(preflight_chain, seal_revision=hostile_seal)
    assert completed.returncode == 2
    assert "strict ancestor" in completed.stderr


def test_forged_source_commit_graph_cannot_forge_exposure_ancestry(
    sealed_chain: _SealedChain,
) -> None:
    source = sealed_chain.source
    unrelated = _unrelated_commit(source.root)
    _git(source.root, "update-ref", "refs/heads/hostile-unrelated", unrelated)
    _forge_commit_graph_parent(source.root, source.selection_revision, unrelated)
    assert (
        _git_ancestry_returncode(
            source.root,
            unrelated,
            source.selection_revision,
            disable_commit_graph=False,
        )
        == 0
    )
    assert (
        _git_ancestry_returncode(
            source.root,
            unrelated,
            source.selection_revision,
            disable_commit_graph=True,
        )
        == 1
    )

    payload = sealed_chain.selection.model_dump(mode="json")
    payload.pop("selection_id")
    freeze = payload["candidate_freeze"]
    freeze.pop("candidate_freeze_id")
    freeze["exposure_revision"] = unrelated
    freeze["exposure_tree_id"] = _git(source.root, "rev-parse", f"{unrelated}^{{tree}}")
    freeze["candidate_freeze_id"] = canonical_hash("tag-truth-candidate-freeze", freeze)
    forged_selection = seal_tag_truth_v2_selection_payload(payload)

    with pytest.raises(ValueError, match="strict descendant"):
        verify_tag_truth_v2_selection_exposure(forged_selection, source.root)


def test_preflight_rejects_any_project_import_before_capture(
    preflight_chain: _SealedChain,
) -> None:
    completed = _run_preflight(preflight_chain, import_project_first=True)

    assert completed.returncode == 2
    assert "project modules loaded" in completed.stderr


@pytest.mark.parametrize(
    ("relative_path", "expected_error"),
    [
        (
            "src/arkts_code_reviewer.py",
            "unsealed top-level import candidate",
        ),
        (
            "src/pydantic.py",
            "unsealed top-level import candidate",
        ),
        (
            "src/datetime.py",
            "unsealed top-level import candidate",
        ),
        (
            "src/hostile-import-hook.pth",
            "unsealed top-level import candidate",
        ),
        (
            "src/__pycache__/datetime.cpython-312.pyc",
            "unsealed top-level import candidate",
        ),
        (
            "src/arkts_code_reviewer/feature_routing_validation.py",
            "unsealed typed verifier import candidate",
        ),
        (
            "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_provenance.pyc",
            "unsealed typed verifier import candidate",
        ),
        (
            "src/arkts_code_reviewer/feature_routing_validation/__pycache__/"
            "tag_truth_v2_provenance.cpython-312.pyc",
            "forbids bytecode cache",
        ),
    ],
)
def test_preflight_rejects_committed_parent_shadows_and_bytecode_cache(
    preflight_chain: _SealedChain,
    relative_path: str,
    expected_error: str,
) -> None:
    _write(preflight_chain.project_root, relative_path, "hostile import candidate\n")
    _git(preflight_chain.project_root, "add", relative_path)
    _git(preflight_chain.project_root, "commit", "-qm", "add hostile import candidate")
    hostile_seal = _git(preflight_chain.project_root, "rev-parse", "HEAD")

    completed = _run_preflight(preflight_chain, seal_revision=hostile_seal)

    assert completed.returncode == 2
    assert expected_error in completed.stderr


def test_preflight_rejects_git_ignored_untracked_top_level_import_shadow(
    preflight_chain: _SealedChain,
) -> None:
    exclude = preflight_chain.project_root / ".git/info/exclude"
    exclude.write_text("src/pydantic.py\n", encoding="utf-8")
    _write(preflight_chain.project_root, "src/pydantic.py", "hostile shadow\n")
    assert (
        _git(
            preflight_chain.project_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        == ""
    )

    completed = _run_preflight(preflight_chain)

    assert completed.returncode == 2
    assert "unsealed top-level import candidate" in completed.stderr


@pytest.mark.parametrize("relative_path", TYPED_VERIFIER_CLOSURE)
def test_preflight_rejects_hidden_worktree_drift_in_every_frozen_closure_file(
    preflight_chain: _SealedChain,
    relative_path: str,
) -> None:
    path = preflight_chain.project_root / relative_path
    _git(preflight_chain.project_root, "update-index", "--assume-unchanged", relative_path)
    path.write_bytes(path.read_bytes() + b"\n")

    completed = _run_preflight(preflight_chain)

    assert completed.returncode == 2
    assert "typed verifier closure drifted" in completed.stderr


def test_preflight_rejects_symlink_substitution_inside_frozen_closure(
    preflight_chain: _SealedChain,
) -> None:
    relative_path = "src/arkts_code_reviewer/feature_routing_validation/tag_truth_v2_provenance.py"
    path = preflight_chain.project_root / relative_path
    _git(preflight_chain.project_root, "update-index", "--assume-unchanged", relative_path)
    path.unlink()
    path.symlink_to("tag_truth_v2_review.py")

    completed = _run_preflight(preflight_chain)

    assert completed.returncode == 2
    assert "typed verifier import candidate is a symlink" in completed.stderr


@pytest.mark.parametrize("field", ["seal_revision", "seal_tree_id"])
def test_report_rebuild_uses_independent_seal_metadata(
    sealed_chain: _SealedChain,
    field: str,
) -> None:
    report = _report(sealed_chain)
    payload = report.model_dump(mode="json", exclude={"verification_id"})
    payload[field] = "f" * 40
    forged = TagTruthV2ProvenanceVerification.model_validate(
        provenance_verification_payload_with_id(payload)
    )

    with pytest.raises(ValueError, match="does not rebuild"):
        verify_tag_truth_v2_provenance_verification(
            forged,
            seal_revision=sealed_chain.seal_revision,
            seal_tree_id=sealed_chain.seal_tree_id,
            artifacts=sealed_chain.artifacts,
            source_root=sealed_chain.source.root,
            development_truth=sealed_chain.source.development_truth,
        )


def test_git_seal_cli_exit_zero_two_and_import_isolation(tmp_path: Path) -> None:
    chain = _build_chain(tmp_path, include_cli_runtime=True)

    complete = _run_git_seal_cli(chain)
    assert complete.returncode == 0, complete.stderr
    report = parse_tag_truth_v2_provenance_verification(complete.stdout.encode())
    assert report.consensus_status == "complete"
    assert report.integrity_status == "verified"
    assert report.evidence_qualification_status == "not_qualified"
    assert report.candidate_execution_status == "not_run"

    one_receipt = _run_git_seal_cli(chain, receipts=(chain.artifact_paths[2],))
    assert one_receipt.returncode == 2
    bad_seal = _run_git_seal_cli(chain, seal_revision="HEAD")
    assert bad_seal.returncode == 2


def test_git_seal_cli_exit_one_for_legal_unresolved_consensus(tmp_path: Path) -> None:
    chain = _build_chain(tmp_path, unresolved=True, include_cli_runtime=True)

    completed = _run_git_seal_cli(chain)
    assert completed.returncode == 1, completed.stderr
    report = parse_tag_truth_v2_provenance_verification(completed.stdout.encode())
    assert report.consensus_status == "unresolved"
    assert report.consensus_blockers == ("unresolved_review_disagreement",)
    assert report.integrity_status == "verified"


def test_cli_removes_script_and_repository_paths_before_typed_import(
    tmp_path: Path,
) -> None:
    chain = _build_chain(tmp_path, include_cli_runtime=True)
    hostile_source = "raise RuntimeError('hostile local pydantic shadow imported')\n"
    _write(chain.project_root, "tools/pydantic.py", hostile_source)
    _write(chain.project_root, "pydantic.py", hostile_source)
    _git(chain.project_root, "add", "tools/pydantic.py", "pydantic.py")
    _git(chain.project_root, "commit", "-qm", "add hostile local dependency shadows")
    hostile_seal = _git(chain.project_root, "rev-parse", "HEAD")
    relative_paths = tuple(
        path.relative_to(chain.project_root).as_posix() for path in chain.artifact_paths
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(chain.project_root / "tools/verify_tag_truth_v2_git_seal.py"),
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
            str(chain.source.root),
            "--seal-revision",
            hostile_seal,
        ],
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

    assert completed.returncode == 0, completed.stderr
    report = parse_tag_truth_v2_provenance_verification(completed.stdout.encode())
    assert report.consensus_status == "complete"


def test_git_seal_cli_rejects_non_isolated_python_before_standard_library_imports() -> None:
    completed = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools/verify_tag_truth_v2_git_seal.py"), "--help"],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires Python isolated mode (-I)" in completed.stderr


def test_report_rebuild_rejects_other_valid_artifact_chain(
    sealed_chain: _SealedChain,
    tmp_path: Path,
) -> None:
    report = _report(sealed_chain)
    other = _build_chain(tmp_path / "other", unresolved=True)

    with pytest.raises(ValueError, match="does not rebuild"):
        verify_tag_truth_v2_provenance_verification(
            report,
            seal_revision=other.seal_revision,
            seal_tree_id=other.seal_tree_id,
            artifacts=other.artifacts,
            source_root=other.source.root,
            development_truth=other.source.development_truth,
        )


def test_stage2c_core_imports_do_not_load_candidate_or_runtime_routing_modules() -> None:
    module = "arkts_code_reviewer.feature_routing_validation.tag_truth_v2_provenance"
    script = (
        "import sys; "
        f"import {module}; "
        "assert 'arkts_code_reviewer.feature_routing.engine' not in sys.modules; "
        "assert 'arkts_code_reviewer.feature_routing.config' not in sys.modules; "
        "assert 'arkts_code_reviewer.feature_routing.matcher' not in sys.modules; "
        "assert 'arkts_code_reviewer.code_analysis' not in sys.modules; "
        "assert 'arkts_code_reviewer.retrieval_validation.lifecycle_symbol_leaf' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
