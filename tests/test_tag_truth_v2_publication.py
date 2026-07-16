from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.feature_routing_validation import (
    tag_truth_v2_publication as publication_core,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    TagTruthV2Suite,
    canonical_hash,
    load_tag_truth_v2,
    parse_tag_truth_v2,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_near_duplicate import (
    ScannedReferenceInventory,
    TagTruthV2NearDuplicatePolicy,
    TagTruthV2NearDuplicateVerification,
    load_tag_truth_v2_near_duplicate_policy,
    parse_tag_truth_v2_near_duplicate_verification,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_provenance import (
    CapturedCommittedArtifact,
    build_tag_truth_v2_provenance_verification,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_publication import (
    TagTruthV2PublicationV1,
    build_verified_tag_truth_v2_publication,
    load_tag_truth_v2_publication,
    parse_tag_truth_v2_publication,
    publication_payload_with_id,
    published_suite_fingerprint,
    verify_tag_truth_v2_publication,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review import (
    TagTruthV2ReviewReceipt,
    build_tag_truth_v2_consensus,
    load_tag_truth_v2_review_receipt,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    build_tag_truth_v2_review_packet,
    candidate_freeze_payload_with_id,
    seal_tag_truth_v2_selection_payload,
    verify_tag_truth_v2_development_exclusions,
    verify_tag_truth_v2_selection_checkout,
    verify_tag_truth_v2_selection_exposure,
)
from tests import test_tag_truth_v2_contract as contract_test_support
from tests import test_tag_truth_v2_near_duplicate as near_duplicate_test_support
from tests import test_tag_truth_v2_provenance as provenance_test_support

ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_CLI = ROOT / "tools/build_tag_truth_v2_publication.py"


def _long_source(symbol: str, *, variant: str) -> str:
    if variant == "assignments":
        body = "\n".join(
            f"    let alpha{index}: number = {index}; alpha{index} = alpha{index} + {index + 1};"
            for index in range(48)
        )
    elif variant == "branches":
        body = "\n".join(
            (
                f"    if (betaFlag{index}) {{ betaValue{index} = "
                f"computeBeta{index}(betaInput{index}); }} else {{ "
                f"resetBeta{index}(betaValue{index}); }}"
            )
            for index in range(48)
        )
    else:
        raise AssertionError(f"unsupported source variant: {variant}")
    return (
        f"// independent synthetic {variant}\n"
        "@Component\n"
        f"struct {symbol} {{\n"
        "  build() {\n"
        f"{body}\n"
        "  }\n"
        "}\n"
    )


@dataclass(frozen=True)
class _PublicationFixture:
    chain: near_duplicate_test_support._NearDuplicateCliChain
    receipts: tuple[TagTruthV2ReviewReceipt, TagTruthV2ReviewReceipt]
    artifacts: tuple[CapturedCommittedArtifact, ...]
    inventories: tuple[
        ScannedReferenceInventory,
        ScannedReferenceInventory,
        ScannedReferenceInventory,
    ]
    policy: TagTruthV2NearDuplicatePolicy
    screening: TagTruthV2NearDuplicateVerification
    publication: TagTruthV2PublicationV1


@dataclass(frozen=True)
class _PublicationCliFixture:
    chain: near_duplicate_test_support._NearDuplicateCliChain
    screening_path: Path
    screening: TagTruthV2NearDuplicateVerification


def _captured_artifacts(
    chain: near_duplicate_test_support._NearDuplicateCliChain,
) -> tuple[CapturedCommittedArtifact, ...]:
    return tuple(
        provenance_test_support._capture(chain.project_root, chain.seal_revision, role, path)
        for role, path in zip(
            ("selection", "packet", "receipt", "receipt", "consensus"),
            chain.artifact_paths,
            strict=True,
        )
    )


def _receipts(
    chain: near_duplicate_test_support._NearDuplicateCliChain,
) -> tuple[TagTruthV2ReviewReceipt, TagTruthV2ReviewReceipt]:
    return (
        load_tag_truth_v2_review_receipt(chain.artifact_paths[2]),
        load_tag_truth_v2_review_receipt(chain.artifact_paths[3]),
    )


def _build_fixture(
    root: Path,
    *,
    unresolved: bool = False,
) -> _PublicationFixture:
    chain = near_duplicate_test_support._build_near_duplicate_cli_chain(
        root,
        unresolved=unresolved,
        selected_texts=(
            _long_source("Alpha", variant="assignments"),
            _long_source("Beta", variant="branches"),
        ),
    )
    inventories = near_duplicate_test_support._reference_inventories_for_texts(
        chain,
        candidate_text=(
            "type CandidateReference = { ready: boolean; count: number };\n"
            + "\n".join(
                f"const candidate{index}: CandidateReference = {{ ready: true, count: {index} }};"
                for index in range(40)
            )
        ),
        exposure_text=(
            "async function exposureReference(): Promise<void> {\n"
            + "\n".join(f"  await Promise.resolve('exposure-{index}');" for index in range(40))
            + "\n}\n"
        ),
    )
    policy, screening = near_duplicate_test_support._build_screening(chain, inventories)
    receipts = _receipts(chain)
    artifacts = _captured_artifacts(chain)
    publication = publication_core._build_tag_truth_v2_publication(
        selection=chain.selection,
        packet=chain.packet,
        receipts=receipts,
        consensus=chain.consensus,
        provenance=chain.report,
        near_duplicate_policy=policy,
        screening=screening,
    )
    return _PublicationFixture(
        chain=chain,
        receipts=receipts,
        artifacts=artifacts,
        inventories=inventories,
        policy=policy,
        screening=screening,
        publication=publication,
    )


def _build_publication_cli_fixture(root: Path) -> _PublicationCliFixture:
    base = near_duplicate_test_support._build_near_duplicate_cli_chain(
        root,
        selected_texts=(
            _long_source("Alpha", variant="assignments"),
            _long_source("Beta", variant="branches"),
        ),
    )
    if (base.project_root / "artifacts").exists():
        shutil.rmtree(base.project_root / "artifacts")
    for tool_name in (
        "tag_truth_v2_publication_preflight.py",
        "build_tag_truth_v2_publication.py",
    ):
        source = ROOT / "tools" / tool_name
        destination = base.project_root / "tools" / tool_name
        shutil.copy2(source, destination)
    candidate_commit, _ = near_duplicate_test_support._commit_all(
        base.project_root,
        "candidate freeze with publication runtime",
    )

    selection_payload = base.selection.model_dump(mode="json", exclude={"selection_id"})
    candidate_freeze = selection_payload["candidate_freeze"]
    assert isinstance(candidate_freeze, dict)
    candidate_freeze.pop("candidate_freeze_id")
    candidate_freeze["candidate_commit"] = candidate_commit
    selection_payload["candidate_freeze"] = candidate_freeze_payload_with_id(candidate_freeze)
    selection = seal_tag_truth_v2_selection_payload(selection_payload)
    verify_tag_truth_v2_development_exclusions(
        selection,
        base.development_truth,
        base.source_root,
    )
    verify_tag_truth_v2_selection_exposure(selection, base.source_root)
    checkout = verify_tag_truth_v2_selection_checkout(selection, base.source_root)
    packet = build_tag_truth_v2_review_packet(selection, checkout)
    receipts = (
        provenance_test_support._receipt(
            packet,
            round_id="round-a",
            reviewer_id="reviewer-a",
        ),
        provenance_test_support._receipt(
            packet,
            round_id="round-b",
            reviewer_id="reviewer-b",
        ),
    )
    consensus = build_tag_truth_v2_consensus(packet, receipts)
    artifact_paths = tuple(
        base.project_root / path
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
        (selection, packet, receipts[0], receipts[1], consensus),
        strict=True,
    ):
        provenance_test_support._write_json(artifact_path, value.model_dump(mode="json"))
    _, _ = near_duplicate_test_support._commit_all(
        base.project_root,
        "seal publication campaign",
    )
    seal_revision = near_duplicate_test_support._git(
        base.project_root,
        "rev-parse",
        "HEAD",
    )
    seal_tree_id = near_duplicate_test_support._git(
        base.project_root,
        "rev-parse",
        "HEAD^{tree}",
    )
    artifacts = tuple(
        provenance_test_support._capture(base.project_root, seal_revision, role, path)
        for role, path in zip(
            ("selection", "packet", "receipt", "receipt", "consensus"),
            artifact_paths,
            strict=True,
        )
    )
    provenance = build_tag_truth_v2_provenance_verification(
        seal_revision=seal_revision,
        seal_tree_id=seal_tree_id,
        artifacts=artifacts,
        source_root=base.source_root,
        development_truth=base.development_truth,
    )
    provenance_path = root / "external" / "provenance-publication.json"
    provenance_test_support._write_json(
        provenance_path,
        provenance.model_dump(mode="json"),
    )
    chain = near_duplicate_test_support._NearDuplicateCliChain(
        project_root=base.project_root,
        source_root=base.source_root,
        seal_revision=seal_revision,
        artifact_paths=artifact_paths,  # type: ignore[arg-type]
        provenance_path=provenance_path,
        report=provenance,
        selection=selection,
        packet=packet,
        consensus=consensus,
        development_truth=base.development_truth,
    )
    screening_run = near_duplicate_test_support._run_near_duplicate_cli(chain)
    assert screening_run.returncode == 1, screening_run.stderr
    screening = parse_tag_truth_v2_near_duplicate_verification(screening_run.stdout.encode("utf-8"))
    screening_path = root / "external" / "near-duplicate-publication.json"
    screening_path.write_text(screening_run.stdout, encoding="utf-8")
    return _PublicationCliFixture(
        chain=chain,
        screening_path=screening_path,
        screening=screening,
    )


def _run_publication_cli(
    fixture: _PublicationCliFixture,
    *,
    screening_path: Path | None = None,
    environment_overrides: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    chain = fixture.chain
    relative_paths = tuple(
        path.relative_to(chain.project_root).as_posix() for path in chain.artifact_paths
    )
    arguments = [
        sys.executable,
        "-I",
        "-B",
        str(chain.project_root / "tools/build_tag_truth_v2_publication.py"),
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
        str(chain.source_root),
        "--seal-revision",
        chain.seal_revision,
        "--provenance-verification",
        str(chain.provenance_path),
        "--near-duplicate-policy",
        near_duplicate_test_support.POLICY_RELATIVE_PATH,
        "--near-duplicate-verification",
        str(screening_path or fixture.screening_path),
    ]
    return subprocess.run(
        arguments,
        cwd=chain.project_root,
        env={
            **os.environ,
            "PYTHONPATH": str(chain.project_root),
            "PYTHONDONTWRITEBYTECODE": "1",
            **dict(environment_overrides or {}),
        },
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def clean_publication(
    tmp_path_factory: pytest.TempPathFactory,
) -> _PublicationFixture:
    fixture = _build_fixture(tmp_path_factory.mktemp("tag-truth-publication-clean"))
    assert fixture.screening.screening_outcome == "clean"
    assert fixture.publication.publication_status == "published_consensus_not_qualified"
    return fixture


@pytest.fixture(scope="module")
def publication_cli_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> _PublicationCliFixture:
    return _build_publication_cli_fixture(tmp_path_factory.mktemp("tag-truth-publication-cli"))


def _rehashed_publication(payload: dict[str, Any]) -> TagTruthV2PublicationV1:
    payload.pop("publication_id", None)
    return TagTruthV2PublicationV1.model_validate(publication_payload_with_id(payload))


def _rehash_nested_publication(payload: dict[str, Any]) -> dict[str, Any]:
    chain = payload["chain_binding"]
    assert isinstance(chain, dict)
    chain.pop("chain_binding_id", None)
    chain["chain_binding_id"] = canonical_hash(
        "tag-truth-publication-chain",
        chain,
    )
    suite = payload["published_suite"]
    if suite is not None:
        assert isinstance(suite, dict)
        suite["chain_binding_id"] = chain["chain_binding_id"]
        suite.pop("published_suite_id", None)
        suite["published_suite_id"] = canonical_hash(
            "tag-truth-published-consensus",
            suite,
        )
        payload["published_suite_fingerprint"] = suite["published_suite_id"]
    payload.pop("publication_id", None)
    return payload


def test_clean_consensus_publishes_not_qualified_suite_and_preserves_both_votes(
    clean_publication: _PublicationFixture,
) -> None:
    publication = clean_publication.publication
    suite = publication.published_suite
    assert suite is not None
    assert publication.publication_blockers == ()
    assert publication.published_suite_fingerprint == suite.published_suite_id
    assert published_suite_fingerprint(suite) == suite.published_suite_id
    assert suite.readiness.evidence_qualification_status == "not_qualified"
    assert suite.readiness.candidate_execution_status == "not_run"
    assert suite.readiness.quality_gate_status == "not_evaluated"
    assert suite.readiness.activation_status == "not_evaluated"
    assert suite.readiness.near_duplicate_qualification_status == "not_qualified_policy_unapproved"
    assert tuple(case.case_id for case in suite.cases) == tuple(
        case.case_id for case in clean_publication.chain.selection.cases
    )

    consensus_cases = {case.case_id: case for case in clean_publication.chain.consensus.cases}
    for published_case in suite.cases:
        consensus_case = consensus_cases[published_case.case_id]
        assert published_case.review_unit == consensus_case.review_unit
        assert published_case.votes == consensus_case.votes
        assert published_case.exact.label == consensus_case.exact.label
        assert published_case.routing.label == consensus_case.routing.label
        assert published_case.exact.rationale_votes == consensus_case.exact.rationale_votes
        assert published_case.routing.rationale_votes == consensus_case.routing.rationale_votes
        assert len(published_case.exact.rationale_votes) == 2
        assert len(published_case.routing.rationale_votes) == 2
        assert published_case.near_duplicate.overall_decision == "clear"


def test_publication_is_deterministic_under_receipt_order_and_round_trips(
    clean_publication: _PublicationFixture,
    tmp_path: Path,
) -> None:
    fixture = clean_publication
    reversed_publication = publication_core._build_tag_truth_v2_publication(
        selection=fixture.chain.selection,
        packet=fixture.chain.packet,
        receipts=tuple(reversed(fixture.receipts)),
        consensus=fixture.chain.consensus,
        provenance=fixture.chain.report,
        near_duplicate_policy=fixture.policy,
        screening=fixture.screening,
    )
    assert reversed_publication == fixture.publication

    raw = json.dumps(
        fixture.publication.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ).encode()
    assert parse_tag_truth_v2_publication(raw) == fixture.publication
    path = tmp_path / "publication.json"
    path.write_bytes(raw)
    assert load_tag_truth_v2_publication(path) == fixture.publication
    symlink = tmp_path / "publication-link.json"
    symlink.symlink_to(path)
    with pytest.raises(ValueError, match="regular non-symlink"):
        load_tag_truth_v2_publication(symlink)


def test_publication_parser_rejects_duplicate_keys_unknown_fields_and_hash_drift(
    clean_publication: _PublicationFixture,
) -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_tag_truth_v2_publication(
            b'{"schema_version":"tag-truth-v2-publication-v1",'
            b'"schema_version":"tag-truth-v2-publication-v1"}'
        )

    extra = clean_publication.publication.model_dump(mode="json")
    extra["unexpected"] = True
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        parse_tag_truth_v2_publication(json.dumps(extra).encode())

    forged = clean_publication.publication.model_dump(mode="json")
    forged["publication_status"] = "blocked_no_suite"
    with pytest.raises(ValueError, match="blocked publication"):
        parse_tag_truth_v2_publication(json.dumps(forged).encode())

    identity_drift = clean_publication.publication.model_dump(mode="json")
    identity_drift["chain_binding"]["feature_config_fingerprint"] = (
        f"feature-config:sha256:{'a' * 64}"
    )
    with pytest.raises(ValueError, match="chain binding ID"):
        parse_tag_truth_v2_publication(json.dumps(identity_drift).encode())

    publication_id_drift = clean_publication.publication.model_dump(mode="json")
    publication_id_drift["publication_id"] = f"tag-truth-publication:sha256:{'0' * 64}"
    with pytest.raises(ValueError, match="publication ID"):
        parse_tag_truth_v2_publication(json.dumps(publication_id_drift).encode())


def test_rehashed_nested_lineage_cannot_detach_the_published_suite(
    clean_publication: _PublicationFixture,
) -> None:
    payload = clean_publication.publication.model_dump(mode="json")
    chain = payload["chain_binding"]
    suite = payload["published_suite"]
    assert isinstance(chain, dict)
    assert isinstance(suite, dict)
    chain.pop("chain_binding_id")
    chain["source_repository_origin"] = "https://tampered.example.invalid/source.git"
    chain["chain_binding_id"] = canonical_hash(
        "tag-truth-publication-chain",
        chain,
    )
    suite["chain_binding_id"] = chain["chain_binding_id"]
    suite.pop("published_suite_id")
    suite["published_suite_id"] = canonical_hash(
        "tag-truth-published-consensus",
        suite,
    )
    payload["published_suite_fingerprint"] = suite["published_suite_id"]
    payload.pop("publication_id")
    with pytest.raises(ValidationError, match="repository differs"):
        TagTruthV2PublicationV1.model_validate(publication_payload_with_id(payload))


def test_rehashed_publication_rejects_noncanonical_near_duplicate_blockers(
    clean_publication: _PublicationFixture,
) -> None:
    payload = clean_publication.publication.model_dump(mode="json")
    payload["readiness"]["near_duplicate_qualification_blockers"] = ["policy_not_approved"]
    suite = payload["published_suite"]
    assert isinstance(suite, dict)
    suite["readiness"]["near_duplicate_qualification_blockers"] = ["policy_not_approved"]
    with pytest.raises(
        ValidationError,
        match="frozen near-duplicate qualification blockers",
    ):
        TagTruthV2PublicationV1.model_validate(
            publication_payload_with_id(_rehash_nested_publication(payload))
        )


def test_rehashed_publication_rejects_inventory_scope_drift(
    clean_publication: _PublicationFixture,
) -> None:
    payload = clean_publication.publication.model_dump(mode="json")
    inventories = payload["chain_binding"]["reference_inventories"]
    development = inventories[2]
    development["scope"] = "entire_tracked_tree"
    development["requested_paths"] = []
    development["requested_path_count"] = 0
    development.pop("inventory_fingerprint")
    development["inventory_fingerprint"] = canonical_hash(
        "tag-truth-reference-inventory",
        development,
    )
    with pytest.raises(ValidationError, match="invalid scope"):
        TagTruthV2PublicationV1.model_validate(
            publication_payload_with_id(_rehash_nested_publication(payload))
        )


def test_rehashed_suite_rejects_case_vote_and_review_chain_drift(
    clean_publication: _PublicationFixture,
) -> None:
    payload = clean_publication.publication.model_dump(mode="json")
    suite = payload["published_suite"]
    assert isinstance(suite, dict)
    receipt = suite["review_chain"]["receipt_references"][0]
    receipt["reviewer"]["reviewer_id"] = "different-reviewer"
    with pytest.raises(ValidationError, match="case votes differ"):
        TagTruthV2PublicationV1.model_validate(
            publication_payload_with_id(_rehash_nested_publication(payload))
        )


def test_duplicate_screening_blocks_without_publishing_a_suite(
    clean_publication: _PublicationFixture,
) -> None:
    duplicate_inventories = near_duplicate_test_support._reference_inventories_for_texts(
        clean_publication.chain,
        candidate_text=clean_publication.chain.source_root.joinpath(
            clean_publication.chain.selection.sources[0].path
        ).read_text(encoding="utf-8"),
        exposure_text=(
            "async function distinctExposure(): Promise<void> {\n"
            + "\n".join(f"  await Promise.resolve('distinct-{index}');" for index in range(40))
            + "\n}\n"
        ),
    )
    policy, screening = near_duplicate_test_support._build_screening(
        clean_publication.chain,
        duplicate_inventories,
    )
    assert screening.screening_outcome == "potential_duplicate"

    publication = publication_core._build_tag_truth_v2_publication(
        selection=clean_publication.chain.selection,
        packet=clean_publication.chain.packet,
        receipts=clean_publication.receipts,
        consensus=clean_publication.chain.consensus,
        provenance=clean_publication.chain.report,
        near_duplicate_policy=policy,
        screening=screening,
    )
    assert publication.publication_status == "blocked_no_suite"
    assert publication.publication_blockers == ("near_duplicate_potential_duplicate",)
    assert publication.published_suite is None
    assert publication.published_suite_fingerprint is None


def test_abstained_screening_blocks_without_publishing_a_suite(
    clean_publication: _PublicationFixture,
) -> None:
    policy = near_duplicate_test_support._policy_with_updates(maximum_selected_nfc_characters=1)
    _, screening = near_duplicate_test_support._build_screening(
        clean_publication.chain,
        clean_publication.inventories,
        policy=policy,
    )
    assert screening.screening_outcome == "review_required"
    assert all(case.overall_decision == "abstain" for case in screening.cases)

    publication = publication_core._build_tag_truth_v2_publication(
        selection=clean_publication.chain.selection,
        packet=clean_publication.chain.packet,
        receipts=clean_publication.receipts,
        consensus=clean_publication.chain.consensus,
        provenance=clean_publication.chain.report,
        near_duplicate_policy=policy,
        screening=screening,
    )
    assert publication.publication_status == "blocked_no_suite"
    assert publication.publication_blockers == ("near_duplicate_review_required",)
    assert publication.published_suite is None
    assert publication.published_suite_fingerprint is None


def test_unresolved_consensus_blocks_without_publishing_a_suite(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path, unresolved=True)
    assert fixture.chain.consensus.consensus_status == "unresolved"
    assert fixture.publication.publication_status == "blocked_no_suite"
    assert fixture.publication.publication_blockers == (
        "near_duplicate_review_required",
        "unresolved_review_disagreement",
    )
    assert fixture.publication.published_suite is None
    assert fixture.publication.published_suite_fingerprint is None


def test_verified_builder_and_verifier_rebuild_the_exact_publication(
    publication_cli_fixture: _PublicationCliFixture,
) -> None:
    fixture = publication_cli_fixture
    chain = fixture.chain
    seal_tree_id = near_duplicate_test_support._git(
        chain.project_root,
        "rev-parse",
        "HEAD^{tree}",
    )
    artifacts = _captured_artifacts(chain)
    policy = load_tag_truth_v2_near_duplicate_policy(
        chain.project_root / near_duplicate_test_support.POLICY_RELATIVE_PATH
    )
    verified = build_verified_tag_truth_v2_publication(
        repository_root=chain.project_root,
        seal_revision=chain.seal_revision,
        seal_tree_id=seal_tree_id,
        artifacts=artifacts,
        source_root=chain.source_root,
        development_truth=chain.development_truth,
        provenance=chain.report,
        near_duplicate_policy=policy,
        screening=fixture.screening,
    )
    verify_tag_truth_v2_publication(
        verified,
        repository_root=chain.project_root,
        seal_revision=chain.seal_revision,
        seal_tree_id=seal_tree_id,
        artifacts=artifacts,
        source_root=chain.source_root,
        development_truth=chain.development_truth,
        provenance=chain.report,
        near_duplicate_policy=policy,
        screening=fixture.screening,
    )

    tampered_payload = verified.model_dump(mode="json")
    chain_binding = tampered_payload["chain_binding"]
    chain_binding.pop("chain_binding_id")
    chain_binding["source_repository_origin"] = "https://tampered.example.invalid/source.git"
    chain_binding["chain_binding_id"] = canonical_hash(
        "tag-truth-publication-chain",
        chain_binding,
    )
    tampered_suite = tampered_payload["published_suite"]
    if tampered_suite is not None:
        tampered_suite["chain_binding_id"] = chain_binding["chain_binding_id"]
        tampered_suite.pop("published_suite_id")
        tampered_suite["published_suite_id"] = canonical_hash(
            "tag-truth-published-consensus",
            tampered_suite,
        )
        tampered_payload["published_suite_fingerprint"] = tampered_suite["published_suite_id"]
    tampered = _rehashed_publication(tampered_payload)
    with pytest.raises(ValueError, match="does not rebuild"):
        verify_tag_truth_v2_publication(
            tampered,
            repository_root=chain.project_root,
            seal_revision=chain.seal_revision,
            seal_tree_id=seal_tree_id,
            artifacts=artifacts,
            source_root=chain.source_root,
            development_truth=chain.development_truth,
            provenance=chain.report,
            near_duplicate_policy=policy,
            screening=fixture.screening,
        )


def test_publication_does_not_relax_the_stage1_suite_loader(tmp_path: Path) -> None:
    blind_payload = contract_test_support._suite_payload("independent_blind_challenge")
    with pytest.raises(ValidationError, match="Stage 1 only loads development_regression"):
        TagTruthV2Suite.model_validate(blind_payload)
    with pytest.raises(ValueError, match="Stage 1 only loads development_regression"):
        parse_tag_truth_v2(json.dumps(blind_payload).encode())

    blind_path = tmp_path / "blind-suite.json"
    blind_path.write_text(json.dumps(blind_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Stage 1 only loads development_regression"):
        load_tag_truth_v2(blind_path)

    qualified_payload = contract_test_support._suite_payload()
    qualified_payload["data_qualification_status"] = "qualified"
    with pytest.raises(ValidationError, match="not_qualified"):
        TagTruthV2Suite.model_validate(qualified_payload)

    near_duplicate_qualified = contract_test_support._suite_payload(
        near_duplicate_status="qualified"
    )
    with pytest.raises(ValidationError, match="has no near-duplicate verifier"):
        TagTruthV2Suite.model_validate(near_duplicate_qualified)


def test_publication_cli_rejects_non_isolated_python_before_preflight() -> None:
    completed = subprocess.run(
        [sys.executable, "-B", str(PUBLICATION_CLI), "--help"],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "requires Python isolated no-bytecode mode (-I -B)" in completed.stderr


def test_publication_cli_rejects_isolated_python_without_no_bytecode_mode() -> None:
    environment = {
        key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"
    }
    completed = subprocess.run(
        [sys.executable, "-I", str(PUBLICATION_CLI), "--help"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "requires Python isolated no-bytecode mode (-I -B)" in completed.stderr


@pytest.mark.parametrize("receipt_count", [1, 3])
def test_publication_cli_requires_exactly_two_receipts_before_preflight(
    receipt_count: int,
) -> None:
    arguments = [
        sys.executable,
        "-I",
        "-B",
        str(PUBLICATION_CLI),
        "--selection",
        "selection.json",
        "--packet",
        "packet.json",
    ]
    for index in range(receipt_count):
        arguments.extend(["--receipt", f"receipt-{index}.json"])
    arguments.extend(
        [
            "--consensus",
            "consensus.json",
            "--source-root",
            ".",
            "--seal-revision",
            "a" * 40,
            "--provenance-verification",
            "provenance.json",
            "--near-duplicate-policy",
            "policy.json",
            "--near-duplicate-verification",
            "screening.json",
        ]
    )
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "requires exactly two --receipt files" in completed.stderr


def test_publication_cli_rebuilds_the_screening_and_returns_the_matching_status(
    publication_cli_fixture: _PublicationCliFixture,
) -> None:
    completed = _run_publication_cli(publication_cli_fixture)
    expected_returncode = 0 if publication_cli_fixture.screening.screening_outcome == "clean" else 1
    assert completed.returncode == expected_returncode, completed.stderr
    publication = parse_tag_truth_v2_publication(completed.stdout.encode("utf-8"))
    if expected_returncode == 0:
        assert publication.publication_status == "published_consensus_not_qualified"
        assert publication.published_suite is not None
    else:
        assert publication.publication_status == "blocked_no_suite"
        assert publication.published_suite is None
    assert publication.readiness.evidence_qualification_status == "not_qualified"
    assert publication.readiness.candidate_execution_status == "not_run"


def test_publication_cli_ignores_inherited_git_routing_overrides(
    publication_cli_fixture: _PublicationCliFixture,
    tmp_path: Path,
) -> None:
    completed = _run_publication_cli(
        publication_cli_fixture,
        environment_overrides={
            "GIT_DIR": str(tmp_path / "hostile-git-dir"),
            "GIT_WORK_TREE": str(tmp_path / "hostile-work-tree"),
            "GIT_CONFIG_GLOBAL": str(tmp_path / "hostile-git-config"),
        },
    )
    expected_returncode = 0 if publication_cli_fixture.screening.screening_outcome == "clean" else 1
    assert completed.returncode == expected_returncode, completed.stderr
    parse_tag_truth_v2_publication(completed.stdout.encode("utf-8"))


def test_publication_cli_rejects_a_tampered_external_screening_report(
    publication_cli_fixture: _PublicationCliFixture,
    tmp_path: Path,
) -> None:
    payload = publication_cli_fixture.screening.model_dump(mode="json")
    payload["candidate_commit"] = "f" * 40
    tampered = tmp_path / "tampered-screening.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    completed = _run_publication_cli(
        publication_cli_fixture,
        screening_path=tampered,
    )
    assert completed.returncode == 2
    assert not completed.stdout
    assert "Tag Truth v2 publication failed" in completed.stderr


def test_publication_screening_capture_rejects_unsafe_external_inputs(
    tmp_path: Path,
) -> None:
    namespace = runpy.run_path(str(ROOT / "tools/tag_truth_v2_publication_preflight.py"))
    capture = namespace["_capture_external_screening"]
    provenance = tmp_path / "provenance.json"
    provenance.write_text("{}\n", encoding="utf-8")
    valid = tmp_path / "screening.json"
    valid.write_text("{}\n", encoding="utf-8")

    symlink = tmp_path / "screening-link.json"
    symlink.symlink_to(valid)
    with pytest.raises(ValueError, match="cannot use symlinks"):
        capture(
            symlink,
            repository_root=ROOT,
            provenance_path=provenance,
        )

    with pytest.raises(ValueError, match="external to the sealed project checkout"):
        capture(
            ROOT / "pyproject.toml",
            repository_root=ROOT,
            provenance_path=provenance,
        )

    fifo = tmp_path / "screening.fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular file"):
        capture(
            fifo,
            repository_root=ROOT,
            provenance_path=provenance,
        )

    oversize = tmp_path / "oversize-screening.json"
    with oversize.open("wb") as stream:
        stream.seek(64 * 1024 * 1024)
        stream.write(b"x")
    with pytest.raises(ValueError, match="exceeds 64 MiB"):
        capture(
            oversize,
            repository_root=ROOT,
            provenance_path=provenance,
        )
