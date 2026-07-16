from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    bytes_hash,
    canonical_hash,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    CandidateFreezeReference,
    DevelopmentExclusions,
    DevelopmentTruthExclusionSnapshot,
    ReviewPolicySnapshot,
    SelectionPolicy,
    TagTruthV2ConstructibilityReport,
    TagTruthV2ReviewPacket,
    TagTruthV2Selection,
    VerifiedTagTruthV2Checkout,
    assess_tag_truth_v2_constructibility,
    build_tag_truth_v2_review_packet,
    candidate_freeze_payload_with_id,
    development_exclusions_payload_with_id,
    load_tag_truth_v2_development_exclusion_snapshot,
    load_tag_truth_v2_selection,
    parse_tag_truth_v2_review_packet,
    parse_tag_truth_v2_selection,
    review_policy_payload_with_id,
    seal_tag_truth_v2_selection_payload,
    selection_policy_payload_with_id,
    verify_tag_truth_v2_development_exclusions,
    verify_tag_truth_v2_review_packet,
    verify_tag_truth_v2_selection_checkout,
    verify_tag_truth_v2_selection_exposure,
)

ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_TRUTH_PATH = ROOT / "tests/evaluation/tag_retrieval/manifest.json"
REMOTE = "https://gitcode.com/openharmony/applications_app_samples.git"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def corpus_repo(tmp_path: Path) -> Iterator[dict[str, Any]]:
    root = tmp_path / "applications_app_samples"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "truth@example.invalid")
    _git(root, "config", "user.name", "Tag Truth Test")
    _git(root, "remote", "add", "origin", REMOTE)

    old_text = "// exposure source\nexport const oldValue = 1;\n"
    _write(root, "samples/old/entry/src/main/ets/Old.ets", old_text)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "exposure")
    exposure = _git(root, "rev-parse", "HEAD")
    exposure_tree = _git(root, "rev-parse", "HEAD^{tree}")

    new1 = "// whole file start\nfunction alpha() {\n  return 1;\n}\n// whole file end\n"
    new2 = "// second source\nfunction beta() {\n  return 2;\n}\n"
    family_overlap = "// same exposed family, new path\nexport const sameFamily = 2;\n"
    duplicate_new = "// duplicated only in new tree\nexport const duplicateValue = 3;\n"
    _write(root, "samples/new1/entry/src/main/ets/New1.ets", new1)
    _write(root, "samples/new2/entry/src/main/ets/New2.ets", new2)
    _write(root, "samples/old/feature/src/main/ets/NewInOldFamily.ets", family_overlap)
    _write(root, "samples/copy/entry/src/main/ets/Copied.ets", old_text)
    symlink = root / "samples/link/entry/src/main/ets/Linked.ets"
    symlink_target = "../../../../new1/entry/src/main/ets/New1.ets"
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(symlink_target)
    _write(root, "code/DocsSample/demo/entry/src/main/ets/Excluded.ets", "docs\n")
    _write(root, "samples/test/entry/src/ohosTest/ets/Test.ets", "test\n")
    _write(root, "samples/dup1/entry/src/main/ets/Duplicate.ets", duplicate_new)
    _write(root, "samples/dup2/entry/src/main/ets/Duplicate.ets", duplicate_new)
    _write(root, "samples/nested/entry/src/main/ets/Parent.ets", "export const parent = 1;\n")
    _write(
        root,
        "samples/nested/child/entry/src/main/ets/Child.ets",
        "export const child = 1;\n",
    )
    empty = root / "samples/empty/entry/src/main/ets/Empty.ets"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_bytes(b"")
    non_utf8 = root / "samples/binary/entry/src/main/ets/Invalid.ets"
    non_utf8.parent.mkdir(parents=True, exist_ok=True)
    non_utf8.write_bytes(b"\xff\xfe")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "selection candidates")
    selection = _git(root, "rev-parse", "HEAD")
    selection_tree = _git(root, "rev-parse", "HEAD^{tree}")

    _git(root, "checkout", "-q", "--orphan", "unrelated")
    for path in sorted(root.iterdir()):
        if path.name == ".git":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    _write(root, "samples/other/entry/src/main/ets/Other.ets", "export const other = 1;\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "unrelated root")
    unrelated = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", selection)

    yield {
        "root": root,
        "exposure": exposure,
        "exposure_tree": exposure_tree,
        "selection": selection,
        "selection_tree": selection_tree,
        "unrelated": unrelated,
        "symlink_target": symlink_target,
        "texts": {
            "samples/new1/entry/src/main/ets/New1.ets": new1,
            "samples/new2/entry/src/main/ets/New2.ets": new2,
            "samples/old/entry/src/main/ets/Old.ets": old_text,
            "samples/old/feature/src/main/ets/NewInOldFamily.ets": family_overlap,
            "samples/copy/entry/src/main/ets/Copied.ets": old_text,
        },
    }


@pytest.fixture
def development_truth(corpus_repo: dict[str, Any]) -> DevelopmentTruthExclusionSnapshot:
    loaded = load_tag_truth_v2_development_exclusion_snapshot(DEVELOPMENT_TRUTH_PATH)
    return replace(loaded, repository_revision=corpus_repo["exposure"])


def _contract() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "tag-contract-snapshot-v1",
        "tag_id": "has_network",
        "version": "network-blind-pilot-v1",
        "axes_relationship": "independent",
        "exact_semantics": {
            "positive": "The ReviewUnit itself performs network behavior.",
            "negative": "The ReviewUnit itself does not perform network behavior.",
            "abstain": "The exact axis cannot be decided from the ReviewUnit.",
        },
        "routing_semantics": {
            "positive": "The full source contains a conservative network routing signal.",
            "negative": "The full source contains no reliable network routing signal.",
            "abstain": "The routing axis cannot be decided from the full source.",
        },
    }
    payload["contract_fingerprint"] = canonical_hash("tag-contract-snapshot", payload)
    return payload


def _review_policy() -> dict[str, Any]:
    return review_policy_payload_with_id(
        {
            "schema_version": "tag-truth-review-policy-v1",
            "version": "dual-axis-blind-v1",
            "approval_status": "draft_not_approved",
            "owner_instruction": "Locate the ReviewUnit owning the probe line.",
            "exact_instruction": "Judge exact applicability from that ReviewUnit.",
            "routing_instruction": "Judge routing independently using the full source.",
            "abstain_instruction": "Abstain whenever either semantic axis is undecidable.",
        }
    )


def _selection_policy() -> dict[str, Any]:
    return selection_policy_payload_with_id(
        {
            "schema_version": "tag-truth-selection-policy-v1",
            "policy_version": "network-proxy-pilot-v1",
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


def _development_exclusions(truth: DevelopmentTruthExclusionSnapshot) -> dict[str, Any]:
    return development_exclusions_payload_with_id(
        {
            "truth_suite_fingerprint": truth.truth_suite_fingerprint,
            "source_family_ids": sorted({source.source_family_id for source in truth.sources}),
            "source_paths": sorted(source.path for source in truth.sources),
            "content_sha256": sorted({source.content_sha256 for source in truth.sources}),
        }
    )


def _candidate_freeze(repo: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    return candidate_freeze_payload_with_id(
        {
            "candidate_commit": "a" * 40,
            "target_tag_id": "has_network",
            "tag_contract_fingerprint": contract["contract_fingerprint"],
            "feature_config_fingerprint": f"feature-config:sha256:{'b' * 64}",
            "exposure_repository_source_id": "applications-app-samples",
            "exposure_revision": repo["exposure"],
            "exposure_tree_id": repo["exposure_tree"],
            "exposure_scope": "entire_tracked_repository",
            "runtime_verification_status": "deferred_to_candidate_runner",
        }
    )


def _source(repo: dict[str, Any], index: int, path: str) -> dict[str, Any]:
    raw = repo["texts"][path].encode()
    family = "/".join(path.split("/")[:2])
    return {
        "alias": f"src{index:03d}",
        "repository_source_id": "applications-app-samples",
        "origin": REMOTE,
        "revision": repo["selection"],
        "path": path,
        "content_sha256": bytes_hash(raw),
        "line_count": len(raw.splitlines()),
        "source_kind": "main",
        "app_scope": family,
        "source_family_id": family,
    }


def _selection_draft(
    repo: dict[str, Any],
    truth: DevelopmentTruthExclusionSnapshot,
    *,
    paths: tuple[str, str] = (
        "samples/new1/entry/src/main/ets/New1.ets",
        "samples/new2/entry/src/main/ets/New2.ets",
    ),
) -> dict[str, Any]:
    contract = _contract()
    return {
        "schema_version": "tag-truth-v2-selection-v1",
        "suite_id": "network-blind-pilot-v1",
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
        "repository": {
            "source_id": "applications-app-samples",
            "repository": "applications_app_samples",
            "origin": REMOTE,
            "revision": repo["selection"],
        },
        "candidate_freeze": _candidate_freeze(repo, contract),
        "development_exclusions": _development_exclusions(truth),
        "selection_policy": _selection_policy(),
        "selector_attestation": {
            "selector_id": "independent-custodian",
            "selector_role": "independent_dataset_custodian",
            "candidate_design_participant": False,
            "candidate_configuration_seen": False,
            "candidate_output_seen": False,
            "selected_after_candidate_freeze": True,
            "attested_on": "2026-07-15",
            "process_note": "Selection used neutral proxy strata without candidate signals.",
        },
        "tag_contract": contract,
        "review_policy": _review_policy(),
        "sources": [_source(repo, index, path) for index, path in enumerate(paths, start=1)],
        "cases": [
            {
                "case_id": "case-0000000000000001",
                "source_alias": "src001",
                "probe_line": 2,
                "proxy_stratum_id": "api_surface_dense",
                "selection_rank": 1,
            },
            {
                "case_id": "case-0000000000000002",
                "source_alias": "src002",
                "probe_line": 2,
                "proxy_stratum_id": "api_surface_sparse",
                "selection_rank": 2,
            },
        ],
    }


def _selection(
    repo: dict[str, Any],
    truth: DevelopmentTruthExclusionSnapshot,
) -> TagTruthV2Selection:
    return seal_tag_truth_v2_selection_payload(_selection_draft(repo, truth))


def test_artifacts_are_closed_frozen_self_hashed_and_duplicate_safe(
    corpus_repo: dict[str, Any],
    development_truth: DevelopmentTruthExclusionSnapshot,
    tmp_path: Path,
) -> None:
    selection = _selection(corpus_repo, development_truth)
    encoded = json.dumps(selection.model_dump(mode="json"), sort_keys=False).encode()
    assert parse_tag_truth_v2_selection(encoded) == selection
    with pytest.raises(ValidationError, match="frozen"):
        selection.__setattr__("suite_id", "changed")

    extra = _selection_draft(corpus_repo, development_truth)
    extra["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        seal_tag_truth_v2_selection_payload(extra)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_tag_truth_v2_selection(b'{"schema_version":"x","schema_version":"y"}')

    manifest = tmp_path / "selection.json"
    manifest.write_bytes(encoded)
    assert load_tag_truth_v2_selection(manifest) == selection
    symlink = tmp_path / "selection-link.json"
    symlink.symlink_to(manifest)
    with pytest.raises(ValueError, match="regular non-symlink"):
        load_tag_truth_v2_selection(symlink)

    development_link = tmp_path / "development-link.json"
    development_link.symlink_to(DEVELOPMENT_TRUTH_PATH)
    with pytest.raises(ValueError, match="regular non-symlink"):
        load_tag_truth_v2_development_exclusion_snapshot(development_link)
    duplicate_development = tmp_path / "duplicate-development.json"
    canonical_development = DEVELOPMENT_TRUTH_PATH.read_text(encoding="utf-8")
    duplicate_development.write_text(
        '{"schema_version":"tag-retrieval-truth-v2",' + canonical_development.lstrip()[1:],
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_tag_truth_v2_development_exclusion_snapshot(duplicate_development)

    forged = copy.deepcopy(selection.model_dump(mode="json"))
    forged["suite_id"] = "forged"
    with pytest.raises(ValueError, match="selection_id"):
        parse_tag_truth_v2_selection(json.dumps(forged).encode())


@pytest.mark.parametrize("forbidden_field", ["exact_label", "candidate_output"])
def test_unlabeled_selection_rejects_truth_and_prediction_fields(
    corpus_repo: dict[str, Any],
    development_truth: DevelopmentTruthExclusionSnapshot,
    forbidden_field: str,
) -> None:
    draft = _selection_draft(corpus_repo, development_truth)
    draft["cases"][0][forbidden_field] = "positive"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        seal_tag_truth_v2_selection_payload(draft)


def test_selection_enforces_bindings_sorting_strata_family_and_attestation(
    corpus_repo: dict[str, Any],
    development_truth: DevelopmentTruthExclusionSnapshot,
) -> None:
    wrong_tag = _selection_draft(corpus_repo, development_truth)
    freeze = dict(wrong_tag["candidate_freeze"])
    freeze.pop("candidate_freeze_id")
    freeze["target_tag_id"] = "has_timer"
    wrong_tag["candidate_freeze"] = candidate_freeze_payload_with_id(freeze)
    with pytest.raises(ValidationError, match="target Tag differs"):
        seal_tag_truth_v2_selection_payload(wrong_tag)

    unsorted = _selection_draft(corpus_repo, development_truth)
    unsorted["sources"] = list(reversed(unsorted["sources"]))
    with pytest.raises(ValidationError, match="sorted by unique alias"):
        seal_tag_truth_v2_selection_payload(unsorted)

    wrong_stratum = _selection_draft(corpus_repo, development_truth)
    wrong_stratum["cases"][0]["proxy_stratum_id"] = "api_surface_sparse"
    with pytest.raises(ValidationError, match="proxy strata"):
        seal_tag_truth_v2_selection_payload(wrong_stratum)

    duplicate_family = _selection_draft(
        corpus_repo,
        development_truth,
        paths=(
            "samples/old/entry/src/main/ets/Old.ets",
            "samples/old/feature/src/main/ets/NewInOldFamily.ets",
        ),
    )
    with pytest.raises(ValidationError, match="one source family"):
        seal_tag_truth_v2_selection_payload(duplicate_family)

    attestation = _selection_draft(corpus_repo, development_truth)
    attestation["selector_attestation"]["candidate_output_seen"] = True
    with pytest.raises(ValidationError, match="False"):
        seal_tag_truth_v2_selection_payload(attestation)

    impossible_policy = dict(_selection_policy())
    impossible_policy.pop("policy_fingerprint")
    impossible_policy["post_review_minimums"] = {
        "minimum_exact_positive_cases": 2,
        "minimum_exact_negative_cases": 1,
        "minimum_routing_positive_cases": 1,
        "minimum_routing_negative_cases": 1,
    }
    with pytest.raises(ValidationError, match="minimums exceed"):
        SelectionPolicy.model_validate(selection_policy_payload_with_id(impossible_policy))


def test_development_exclusions_must_be_complete_and_selection_cannot_overlap(
    corpus_repo: dict[str, Any],
    development_truth: DevelopmentTruthExclusionSnapshot,
) -> None:
    selection = _selection(corpus_repo, development_truth)
    verify_tag_truth_v2_development_exclusions(
        selection,
        development_truth,
        corpus_repo["root"],
    )
    with pytest.raises(ValueError, match="frozen registered code source"):
        verify_tag_truth_v2_development_exclusions(
            selection,
            replace(development_truth, repository_origin="https://example.invalid/wrong.git"),
            corpus_repo["root"],
        )

    incomplete_payload = selection.development_exclusions.model_dump(
        mode="json",
        exclude={"exclusions_id"},
    )
    incomplete_payload["source_paths"] = incomplete_payload["source_paths"][1:]
    incomplete_selection_payload = selection.model_dump(
        mode="json",
        exclude={"selection_id"},
    )
    incomplete_selection_payload["development_exclusions"] = development_exclusions_payload_with_id(
        incomplete_payload
    )
    incomplete_selection = seal_tag_truth_v2_selection_payload(incomplete_selection_payload)
    with pytest.raises(ValueError, match="source-path set is incomplete"):
        verify_tag_truth_v2_development_exclusions(
            incomplete_selection,
            development_truth,
            corpus_repo["root"],
        )

    overlap = _selection_draft(corpus_repo, development_truth)
    exclusion = dict(overlap["development_exclusions"])
    exclusion.pop("exclusions_id")
    exclusion["source_paths"] = sorted([*exclusion["source_paths"], overlap["sources"][0]["path"]])
    overlap["development_exclusions"] = development_exclusions_payload_with_id(exclusion)
    with pytest.raises(ValidationError, match="path overlaps development Truth"):
        seal_tag_truth_v2_selection_payload(overlap)


def test_identity_helpers_reject_prepopulated_ids(
    corpus_repo: dict[str, Any],
    development_truth: DevelopmentTruthExclusionSnapshot,
) -> None:
    draft = _selection_draft(corpus_repo, development_truth)
    with pytest.raises(ValueError, match="cannot provide candidate_freeze_id"):
        candidate_freeze_payload_with_id(draft["candidate_freeze"])
    with pytest.raises(ValueError, match="cannot provide exclusions_id"):
        development_exclusions_payload_with_id(draft["development_exclusions"])
    with pytest.raises(ValueError, match="cannot provide policy_fingerprint"):
        selection_policy_payload_with_id(draft["selection_policy"])
    with pytest.raises(ValueError, match="cannot provide policy_fingerprint"):
        review_policy_payload_with_id(draft["review_policy"])

    assert CandidateFreezeReference.model_validate_json(json.dumps(draft["candidate_freeze"]))
    assert DevelopmentExclusions.model_validate_json(json.dumps(draft["development_exclusions"]))
    assert SelectionPolicy.model_validate_json(json.dumps(draft["selection_policy"]))
    assert ReviewPolicySnapshot.model_validate_json(json.dumps(draft["review_policy"]))


def test_constructibility_requires_strict_descendant_and_counts_only_new_axes(
    corpus_repo: dict[str, Any],
    development_truth: DevelopmentTruthExclusionSnapshot,
) -> None:
    candidate_freeze = CandidateFreezeReference.model_validate(
        _candidate_freeze(corpus_repo, _contract())
    )
    selection_policy = SelectionPolicy.model_validate(_selection_policy())
    common = {
        "source_root": corpus_repo["root"],
        "candidate_freeze": candidate_freeze,
        "development_truth": development_truth,
        "selection_policy": selection_policy,
    }
    valid = assess_tag_truth_v2_constructibility(
        **common,
        selection_revision=corpus_repo["selection"],
    )
    assert valid.verified_selectable_capacity_satisfied is True
    assert valid.selection_constructibility_status == "inventory_capacity_only"
    assert valid.proxy_strata_capacity_status == "not_measured"
    assert valid.strict_descendant is True
    assert valid.new_eligible_source_count == 6
    assert valid.new_eligible_family_count == 6
    assert valid.verified_selectable_case_lower_bound == 4
    assert valid.reasons == ()

    same = assess_tag_truth_v2_constructibility(
        **common,
        selection_revision=corpus_repo["exposure"],
    )
    assert same.verified_selectable_capacity_satisfied is False
    assert same.selection_constructibility_status == "not_constructible"
    assert same.reasons == ("selection_revision_equals_exposure_revision",)
    forged_report_payload = same.model_dump(mode="json", exclude={"report_id"})
    forged_report_payload["reasons"] = []
    forged_report_payload["report_id"] = canonical_hash(
        "tag-truth-constructibility",
        forged_report_payload,
    )
    with pytest.raises(ValidationError, match="reasons do not match"):
        TagTruthV2ConstructibilityReport.model_validate(forged_report_payload)

    unrelated = assess_tag_truth_v2_constructibility(
        **common,
        selection_revision=corpus_repo["unrelated"],
    )
    assert unrelated.verified_selectable_capacity_satisfied is False
    assert unrelated.reasons == ("selection_revision_not_strict_descendant",)

    _git(
        corpus_repo["root"], "replace", "--graft", corpus_repo["unrelated"], corpus_repo["exposure"]
    )
    try:
        replace_resistant = assess_tag_truth_v2_constructibility(
            **common,
            selection_revision=corpus_repo["unrelated"],
        )
        assert replace_resistant.strict_descendant is False
        assert replace_resistant.reasons == ("selection_revision_not_strict_descendant",)
    finally:
        _git(corpus_repo["root"], "replace", "-d", corpus_repo["unrelated"])

    reverse_freeze_payload = candidate_freeze.model_dump(
        mode="json",
        exclude={"candidate_freeze_id"},
    )
    reverse_freeze_payload["exposure_revision"] = corpus_repo["selection"]
    reverse_freeze_payload["exposure_tree_id"] = corpus_repo["selection_tree"]
    reverse_freeze = CandidateFreezeReference.model_validate(
        candidate_freeze_payload_with_id(reverse_freeze_payload)
    )
    reverse = assess_tag_truth_v2_constructibility(
        corpus_repo["root"],
        candidate_freeze=reverse_freeze,
        development_truth=development_truth,
        selection_policy=selection_policy,
        selection_revision=corpus_repo["exposure"],
    )
    assert reverse.verified_selectable_capacity_satisfied is False
    assert reverse.reasons == ("selection_revision_not_strict_descendant",)

    larger_policy_payload = selection_policy.model_dump(
        mode="json",
        exclude={"policy_fingerprint"},
    )
    larger_policy_payload["selected_case_count"] = 5
    larger_policy_payload["strata"][0]["selected_case_count"] = 4
    larger_policy = SelectionPolicy.model_validate(
        selection_policy_payload_with_id(larger_policy_payload)
    )
    insufficient = assess_tag_truth_v2_constructibility(
        corpus_repo["root"],
        candidate_freeze=candidate_freeze,
        development_truth=development_truth,
        selection_policy=larger_policy,
        selection_revision=corpus_repo["selection"],
    )
    assert insufficient.verified_selectable_capacity_satisfied is False
    assert insufficient.new_eligible_source_count == 6
    assert insufficient.new_eligible_family_count == 6
    assert insufficient.verified_selectable_case_lower_bound == 4
    assert insufficient.reasons == ("insufficient_verified_selectable_capacity",)


def test_checkout_verifier_checks_head_remote_clean_bytes_hash_and_line_count(
    corpus_repo: dict[str, Any],
    development_truth: DevelopmentTruthExclusionSnapshot,
) -> None:
    root = corpus_repo["root"]
    selection = _selection(corpus_repo, development_truth)
    checkout = verify_tag_truth_v2_selection_checkout(selection, root)
    assert (
        checkout.source_text_by_alias["src001"]
        == corpus_repo["texts"]["samples/new1/entry/src/main/ets/New1.ets"]
    )

    (root / "untracked.tmp").write_text("dirty", encoding="utf-8")
    with pytest.raises(ValueError, match="must be clean"):
        verify_tag_truth_v2_selection_checkout(selection, root)
    (root / "untracked.tmp").unlink()

    _git(root, "remote", "set-url", "origin", "https://example.invalid/wrong.git")
    with pytest.raises(ValueError, match="remote mismatch"):
        verify_tag_truth_v2_selection_checkout(selection, root)
    _git(root, "remote", "set-url", "origin", REMOTE)

    _git(root, "checkout", "-q", corpus_repo["exposure"])
    with pytest.raises(ValueError, match="revision mismatch"):
        verify_tag_truth_v2_selection_checkout(selection, root)
    _git(root, "checkout", "-q", corpus_repo["selection"])

    selected_path = root / selection.sources[0].path
    original_text = selected_path.read_text(encoding="utf-8")
    _git(root, "update-index", "--assume-unchanged", selection.sources[0].path)
    try:
        selected_path.write_text(f"{original_text}// hidden worktree drift\n", encoding="utf-8")
        with pytest.raises(ValueError, match="differs from pinned Git bytes"):
            verify_tag_truth_v2_selection_checkout(selection, root)
    finally:
        selected_path.write_text(original_text, encoding="utf-8")
        _git(root, "update-index", "--no-assume-unchanged", selection.sources[0].path)

    forged_payload = selection.model_dump(mode="json", exclude={"selection_id"})
    forged_payload["sources"][0]["content_sha256"] = f"sha256:{'0' * 64}"
    forged = seal_tag_truth_v2_selection_payload(forged_payload)
    with pytest.raises(ValueError, match="hash drift"):
        verify_tag_truth_v2_selection_checkout(forged, root)

    line_count_payload = selection.model_dump(mode="json", exclude={"selection_id"})
    line_count_payload["sources"][0]["line_count"] += 1
    forged_line_count = seal_tag_truth_v2_selection_payload(line_count_payload)
    with pytest.raises(ValueError, match="line-count drift"):
        verify_tag_truth_v2_selection_checkout(forged_line_count, root)

    symlink_path = "samples/link/entry/src/main/ets/Linked.ets"
    symlink_raw = corpus_repo["symlink_target"].encode("utf-8")
    symlink_draft = _selection_draft(corpus_repo, development_truth)
    symlink_draft["sources"][0] = {
        "alias": "src001",
        "repository_source_id": "applications-app-samples",
        "origin": REMOTE,
        "revision": corpus_repo["selection"],
        "path": symlink_path,
        "content_sha256": bytes_hash(symlink_raw),
        "line_count": 1,
        "source_kind": "main",
        "app_scope": "samples/link",
        "source_family_id": "samples/link",
    }
    symlink_draft["cases"][0]["probe_line"] = 1
    symlink_selection = seal_tag_truth_v2_selection_payload(symlink_draft)
    worktree_symlink = root / symlink_path
    _git(root, "update-index", "--assume-unchanged", symlink_path)
    try:
        worktree_symlink.unlink()
        worktree_symlink.write_bytes(symlink_raw)
        with pytest.raises(ValueError, match="regular Git file"):
            verify_tag_truth_v2_selection_checkout(symlink_selection, root)
    finally:
        worktree_symlink.unlink()
        worktree_symlink.symlink_to(corpus_repo["symlink_target"])
        _git(root, "update-index", "--no-assume-unchanged", symlink_path)


@pytest.mark.parametrize(
    ("paths", "axis"),
    [
        (
            (
                "samples/old/entry/src/main/ets/Old.ets",
                "samples/new2/entry/src/main/ets/New2.ets",
            ),
            "path",
        ),
        (
            (
                "samples/old/feature/src/main/ets/NewInOldFamily.ets",
                "samples/new2/entry/src/main/ets/New2.ets",
            ),
            "family",
        ),
        (
            (
                "samples/copy/entry/src/main/ets/Copied.ets",
                "samples/new2/entry/src/main/ets/New2.ets",
            ),
            "content",
        ),
    ],
)
def test_exposure_verifier_rejects_path_family_and_blob_overlap(
    corpus_repo: dict[str, Any],
    development_truth: DevelopmentTruthExclusionSnapshot,
    paths: tuple[str, str],
    axis: str,
) -> None:
    overlapping = seal_tag_truth_v2_selection_payload(
        _selection_draft(corpus_repo, development_truth, paths=paths)
    )
    with pytest.raises(ValueError, match=axis):
        verify_tag_truth_v2_selection_exposure(overlapping, corpus_repo["root"])


def _walk_keys(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_packet_is_path_redacted_and_has_no_selection_or_candidate_leakage(
    corpus_repo: dict[str, Any],
    development_truth: DevelopmentTruthExclusionSnapshot,
) -> None:
    selection = _selection(corpus_repo, development_truth)
    verify_tag_truth_v2_selection_exposure(selection, corpus_repo["root"])
    checkout = verify_tag_truth_v2_selection_checkout(selection, corpus_repo["root"])
    packet = build_tag_truth_v2_review_packet(selection, checkout)
    verify_tag_truth_v2_review_packet(packet, selection)
    assert (
        parse_tag_truth_v2_review_packet(json.dumps(packet.model_dump(mode="json")).encode())
        == packet
    )
    assert packet.tag_contract == selection.tag_contract
    assert packet.review_policy == selection.review_policy
    assert (
        packet.cases[0].source_text
        == corpus_repo["texts"]["samples/new1/entry/src/main/ets/New1.ets"]
    )
    assert packet.cases[0].source_text.startswith("// whole file start")
    assert packet.cases[0].source_text.endswith("// whole file end\n")
    assert packet.cases[0].line_count == len(packet.cases[0].source_text.encode().splitlines())
    packet_payload = packet.model_dump(mode="json")
    assert "repository_revision" not in packet_payload
    assert "source_sha256" not in packet_payload["cases"][0]

    forged_texts = dict(checkout.source_text_by_alias)
    forged_texts["src001"] = forged_texts["src001"].replace("return 1", "return 9")
    forged_checkout = VerifiedTagTruthV2Checkout(
        selection_id=checkout.selection_id,
        repository_revision=checkout.repository_revision,
        root=checkout.root,
        source_text_by_alias=forged_texts,
    )
    with pytest.raises(ValueError, match="source hash differs"):
        build_tag_truth_v2_review_packet(selection, forged_checkout)

    forged_selection = selection.model_copy(update={"suite_id": "forged"})
    with pytest.raises(ValidationError, match="selection_id"):
        build_tag_truth_v2_review_packet(forged_selection, checkout)

    forged_packet_payload = packet.model_dump(mode="json", exclude={"packet_id"})
    forged_packet_payload["cases"][0]["source_text"] = forged_packet_payload["cases"][0][
        "source_text"
    ].replace("return 1", "return 9")
    forged_packet_payload["packet_id"] = canonical_hash(
        "tag-truth-review-packet",
        forged_packet_payload,
    )
    forged_packet = TagTruthV2ReviewPacket.model_validate(forged_packet_payload)
    with pytest.raises(ValueError, match="source hash differs"):
        verify_tag_truth_v2_review_packet(forged_packet, selection)

    forbidden = (
        "path",
        "family",
        "stratum",
        "rank",
        "near_duplicate",
        "candidate",
        "config",
        "output",
        "diagnostic",
        "revision",
        "sha256",
    )
    leaked = sorted(
        key
        for key in _walk_keys(packet.model_dump(mode="json"))
        if any(token in key.lower() for token in forbidden)
    )
    assert leaked == []


def test_stage2a_modules_do_not_load_feature_router_or_candidate_config() -> None:
    module = "arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection"
    tools = tuple(
        str(path)
        for path in (
            ROOT / "tools/check_tag_truth_v2_constructibility.py",
            ROOT / "tools/seal_tag_truth_v2_selection.py",
            ROOT / "tools/build_tag_truth_v2_review_packet.py",
        )
    )
    script = (
        "import runpy,sys; "
        f"import {module}; "
        f"[runpy.run_path(path, run_name='stage2a_import_test') for path in {tools!r}]; "
        "assert 'arkts_code_reviewer.feature_routing.engine' not in sys.modules; "
        "assert 'arkts_code_reviewer.feature_routing.config' not in sys.modules; "
        "assert 'arkts_code_reviewer.feature_routing.matcher' not in sys.modules; "
        "assert 'arkts_code_reviewer.code_analysis' not in sys.modules; "
        "assert 'arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture' "
        "not in sys.modules"
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
