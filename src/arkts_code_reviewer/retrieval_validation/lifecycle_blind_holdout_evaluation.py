from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from arkts_code_reviewer.code_analysis.file_analysis_parser import FileAnalysisParser
    from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder

from arkts_code_reviewer.retrieval_validation.lifecycle_blind_holdout import (
    LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT,
    LIFECYCLE_POSITIVE_STRATA,
    HoldoutSelectionCase,
    LifecycleHoldoutConsensus,
    LifecycleHoldoutReviewPacket,
    LifecycleHoldoutSelection,
    VerifiedHoldoutCheckout,
    build_lifecycle_holdout_consensus,
    build_lifecycle_holdout_review_packet,
    canonical_json,
    load_canonical_lifecycle_review_material,
    parse_lifecycle_holdout_consensus,
    parse_lifecycle_holdout_review_packet,
    parse_lifecycle_holdout_review_receipt,
    parse_lifecycle_holdout_selection,
    verify_approved_selection_policy,
    verify_candidate_corpus_independence,
    verify_candidate_runtime_bundle,
    verify_evaluation_harness_bundle,
    verify_lifecycle_holdout_checkout,
    verify_selection_development_exclusions,
)

LIFECYCLE_HOLDOUT_EVALUATION_SCHEMA_VERSION = "lifecycle-owner-role-holdout-evaluation-v1"

_EXPECTED_OWNER_ROLE = {
    "aboutToAppear": "arkui_custom_component",
    "aboutToDisappear": "arkui_custom_component",
    "onBackPress": "arkui_router_page",
    "onPageHide": "arkui_router_page",
    "onPageShow": "arkui_router_page",
}
_CUSTOM_COMPONENT_LEAVES = {"aboutToAppear", "aboutToDisappear"}
_ROUTER_PAGE_LEAVES = {"onBackPress", "onPageHide", "onPageShow"}
_EXACT_LIFECYCLE_LEAVES = _CUSTOM_COMPONENT_LEAVES | _ROUTER_PAGE_LEAVES
_ROUTING_HINT_LEAVES = _EXACT_LIFECYCLE_LEAVES | {"onReady"}


def _dependency_site_packages_from_executable() -> Path:
    environment_root = Path(sys.executable).absolute().parent.parent
    configuration = environment_root / "pyvenv.cfg"
    if configuration.is_symlink() or not configuration.is_file():
        raise ValueError("formal holdout interpreter must belong to an isolated virtualenv")
    candidates = (
        environment_root
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages",
        environment_root / "Lib/site-packages",
    )
    available = tuple(path.resolve(strict=True) for path in candidates if path.is_dir())
    if len(available) != 1:
        raise ValueError(
            "formal holdout requires one identifiable isolated dependency site-packages"
        )
    return available[0]


def _run_git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot inspect holdout seal revision: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValueError(f"cannot inspect holdout seal revision: {detail}")
    return completed.stdout.strip()


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot inspect holdout seal ancestry: {exc}") from exc
    return completed.returncode == 0


@dataclass(frozen=True)
class VerifiedHoldoutArtifacts:
    seal_revision: str
    paths: tuple[Path, ...]
    artifact_bytes: tuple[bytes, ...]


def verify_holdout_execution_environment(repository_root: str | Path) -> None:
    root = Path(repository_root).resolve(strict=True)
    expected_source = (root / "src").resolve(strict=True)
    if os.environ.get("PYTHONPATH"):
        raise ValueError("formal holdout requires an empty PYTHONPATH")
    if not sys.flags.safe_path:
        raise ValueError("formal holdout requires Python safe-path mode (-P)")
    if not sys.flags.no_site:
        raise ValueError("formal holdout requires Python no-site mode (-S)")
    if not sys.flags.no_user_site:
        raise ValueError("formal holdout requires PYTHONNOUSERSITE=1 at interpreter startup")
    if not sys.dont_write_bytecode:
        raise ValueError("formal holdout requires bytecode writes disabled (-B)")
    dependency_site_packages = _dependency_site_packages_from_executable()
    if dependency_site_packages.is_relative_to(root):
        raise ValueError(
            "formal holdout requires a dependency environment outside the sealed repository"
        )
    resolved_paths = tuple(
        Path(item).resolve(strict=True) for item in sys.path if item and Path(item).exists()
    )
    if not resolved_paths or resolved_paths[0] != expected_source:
        raise ValueError("formal holdout repository src must be inserted only after preflight")
    if resolved_paths.count(expected_source) != 1:
        raise ValueError("formal holdout repository src must appear exactly once on sys.path")
    if resolved_paths.count(dependency_site_packages) != 1:
        raise ValueError(
            "formal holdout dependency site-packages must be appended exactly once after preflight"
        )
    unexpected_repository_paths = tuple(
        path for path in resolved_paths[1:] if path == root or path.is_relative_to(root)
    )
    if unexpected_repository_paths:
        raise ValueError("formal holdout sys.path contains an unverified repository path")
    core_module = sys.modules.get(LifecycleHoldoutSelection.__module__)
    core_file = getattr(core_module, "__file__", None)
    if not isinstance(core_file, str):
        raise ValueError("holdout contract module has no verifiable source path")
    module_paths = (
        Path(__file__).resolve(strict=True),
        Path(core_file).resolve(strict=True),
    )
    if any(not path.is_relative_to(expected_source) for path in module_paths):
        raise ValueError("holdout evaluator was imported outside the sealed repository source")


def verify_holdout_artifacts_sealed(
    repository_root: str | Path,
    seal_revision: str,
    artifact_paths: Sequence[str | Path],
) -> VerifiedHoldoutArtifacts:
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"holdout repository root does not exist: {repository_root}") from exc
    top = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != root:
        raise ValueError("holdout repository root must be the Git top level")
    sealed = _run_git(root, "rev-parse", seal_revision)
    if len(sealed) != 40 or any(character not in "0123456789abcdef" for character in sealed):
        raise ValueError("holdout seal revision must resolve to a full commit identity")
    if not _is_ancestor(root, LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT, sealed):
        raise ValueError("holdout was sealed before the candidate freeze commit")
    if not _is_ancestor(root, sealed, "HEAD"):
        raise ValueError("holdout seal revision is not an ancestor of HEAD")
    if _run_git(root, "rev-parse", "HEAD") != sealed:
        raise ValueError("formal holdout must run with HEAD exactly at the seal revision")
    if _run_git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("formal holdout repository must be completely clean")

    relative_paths: list[str] = []
    resolved_paths: list[Path] = []
    verified_bytes: list[bytes] = []
    for artifact_path in artifact_paths:
        candidate = Path(artifact_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"sealed artifact must be a regular non-symlink file: {candidate}")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"sealed artifact does not exist: {candidate}") from exc
        if not resolved.is_relative_to(root):
            raise ValueError("sealed artifact must stay inside the repository")
        relative = resolved.relative_to(root).as_posix()
        if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise ValueError("sealed artifact path is unsafe")
        relative_paths.append(relative)
        try:
            sealed_bytes = subprocess.run(
                ["git", "-C", str(root), "show", f"{sealed}:{relative}"],
                check=True,
                capture_output=True,
                timeout=30,
            ).stdout
            current_bytes = resolved.read_bytes()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"artifact is unavailable at holdout seal: {relative}") from exc
        if sealed_bytes != current_bytes:
            raise ValueError(f"sealed holdout artifact drift: {relative}")
        resolved_paths.append(resolved)
        verified_bytes.append(current_bytes)
    if len(relative_paths) != len(set(relative_paths)):
        raise ValueError("sealed artifact paths must be unique")
    return VerifiedHoldoutArtifacts(
        seal_revision=sealed,
        paths=tuple(resolved_paths),
        artifact_bytes=tuple(verified_bytes),
    )


def _wilson_lower(successes: int, total: int, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return 0.0
    proportion = successes / total
    denominator = 1.0 + z * z / total
    centre = proportion + z * z / (2.0 * total)
    margin = z * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total))
    return max(0.0, (centre - margin) / denominator)


def _confusion_metrics(rows: Sequence[Mapping[str, object]]) -> dict[str, int | float]:
    true_positive = sum(
        row["expected_exact_tag"] is True and row["actual_exact_tag"] is True for row in rows
    )
    false_positive = sum(
        row["expected_exact_tag"] is False and row["actual_exact_tag"] is True for row in rows
    )
    false_negative = sum(
        row["expected_exact_tag"] is True and row["actual_exact_tag"] is False for row in rows
    )
    true_negative = sum(
        row["expected_exact_tag"] is False and row["actual_exact_tag"] is False for row in rows
    )
    predicted_positive = true_positive + false_positive
    actual_positive = true_positive + false_negative
    precision = true_positive / predicted_positive if predicted_positive else 0.0
    recall = true_positive / actual_positive if actual_positive else 0.0
    return {
        "case_count": len(rows),
        "positive_case_count": actual_positive,
        "negative_case_count": false_positive + true_negative,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": precision,
        "recall": recall,
        "precision_wilson_lower_95": _wilson_lower(true_positive, predicted_positive),
        "recall_wilson_lower_95": _wilson_lower(true_positive, actual_positive),
    }


def _validate_chain(
    selection: LifecycleHoldoutSelection,
    packet: LifecycleHoldoutReviewPacket,
    consensus: LifecycleHoldoutConsensus,
) -> None:
    if not isinstance(selection, LifecycleHoldoutSelection):
        raise ValueError("holdout evaluation requires a sealed selection")
    if not isinstance(packet, LifecycleHoldoutReviewPacket):
        raise ValueError("holdout evaluation requires a blinded review packet")
    if not isinstance(consensus, LifecycleHoldoutConsensus):
        raise ValueError("holdout evaluation requires sealed consensus")
    LifecycleHoldoutSelection.model_validate_json(canonical_json(selection.model_dump(mode="json")))
    LifecycleHoldoutReviewPacket.model_validate_json(canonical_json(packet.model_dump(mode="json")))
    LifecycleHoldoutConsensus.model_validate_json(canonical_json(consensus.model_dump(mode="json")))
    if packet.selection_id != selection.selection_id:
        raise ValueError("holdout packet does not reference the sealed selection")
    if consensus.selection_id != selection.selection_id or consensus.packet_id != packet.packet_id:
        raise ValueError("holdout consensus does not reference the sealed selection and packet")
    selection_case_ids = [item.case_id for item in selection.cases]
    packet_case_ids = [item.case_id for item in packet.cases]
    consensus_case_ids = [item.case_id for item in consensus.cases]
    if not (selection_case_ids == packet_case_ids == consensus_case_ids):
        raise ValueError("holdout selection, packet, and consensus case sets differ")
    if consensus.release_ready is not True or consensus.consensus_status != "complete":
        raise ValueError("holdout consensus must be complete before candidate evaluation")
    if any(item.semantic_label not in {"positive", "negative"} for item in consensus.cases):
        raise ValueError("holdout consensus contains no metric-ready semantic label")
    selection_by_case = {item.case_id: item for item in selection.cases}
    mismatches = [
        item.case_id
        for item in consensus.cases
        if (item.semantic_label == "positive")
        != (selection_by_case[item.case_id].stratum_id in LIFECYCLE_POSITIVE_STRATA)
    ]
    if mismatches:
        raise ValueError(
            f"selection challenge stratum disagrees with independent consensus: {mismatches!r}"
        )


def _validate_challenge_eligibility(
    selection: LifecycleHoldoutSelection,
    consensus: LifecycleHoldoutConsensus,
    checkout: VerifiedHoldoutCheckout,
) -> None:
    selection_by_case = {item.case_id: item for item in selection.cases}
    sources = {item.alias: item for item in selection.sources}
    failures: list[str] = []
    for truth in consensus.cases:
        case = selection_by_case[truth.case_id]
        leaf = cast(str, truth.expected_unit_symbol).rsplit(".", 1)[-1]
        if (
            case.stratum_id
            in {
                "component_v1_positive",
                "component_v2_positive",
            }
            and leaf not in _CUSTOM_COMPONENT_LEAVES
        ):
            failures.append(truth.case_id)
        elif case.stratum_id == "router_page_positive" and leaf not in _ROUTER_PAGE_LEAVES:
            failures.append(truth.case_id)
        elif case.stratum_id in {"ordinary_owner_negative", "nested_owner_negative"}:
            if leaf not in _EXACT_LIFECYCLE_LEAVES:
                failures.append(truth.case_id)
        elif case.stratum_id == "non_entry_page_negative" and leaf not in _ROUTER_PAGE_LEAVES:
            failures.append(truth.case_id)
        elif case.stratum_id == "routing_only_negative":
            source_text = checkout.source_text_by_alias[sources[case.source_alias].alias]
            if not any(item in source_text for item in _ROUTING_HINT_LEAVES):
                failures.append(truth.case_id)
    if failures:
        raise ValueError(
            "selection cases do not satisfy candidate-independent challenge eligibility: "
            f"{sorted(failures)!r}"
        )


def _owner_provenance_valid(profile: object) -> bool:
    from arkts_code_reviewer.feature_routing.models import (
        UnitSymbolLeafOwnerRoleFeatureSignal,
    )
    from arkts_code_reviewer.retrieval_validation.lifecycle_symbol_leaf import (
        LIFECYCLE_OWNER_AWARE_EXACT_SYMBOL_LEAVES,
    )

    exact_tags = getattr(profile, "exact_tags", ())
    if "has_lifecycle" not in exact_tags:
        return True
    matches = [
        item
        for item in getattr(profile, "tag_matches", ())
        if item.tag_id == "has_lifecycle" and item.status == "Active" and item.scope == "unit_exact"
    ]
    if len(matches) != 1:
        return False
    signals = matches[0].signals
    if not signals:
        return False
    for signal in signals:
        if type(signal) is not UnitSymbolLeafOwnerRoleFeatureSignal:
            return False
        owner_signal = signal
        if (
            owner_signal.normalized_value not in LIFECYCLE_OWNER_AWARE_EXACT_SYMBOL_LEAVES
            or owner_signal.owner_role != _EXPECTED_OWNER_ROLE[owner_signal.normalized_value]
        ):
            return False
    return True


def _file_hint_provenance_valid(profile: object) -> bool:
    from arkts_code_reviewer.feature_routing.models import FileSymbolLeafFeatureSignal

    matches = [
        item
        for item in getattr(profile, "tag_matches", ())
        if item.tag_id == "has_lifecycle" and item.status == "Active" and item.scope == "file_hint"
    ]
    return bool(matches) and all(
        match.signals
        and all(type(signal) is FileSymbolLeafFeatureSignal for signal in match.signals)
        for match in matches
    )


def _load_candidate_config(path: str | Path) -> Any:
    from arkts_code_reviewer.retrieval_validation.lifecycle_symbol_leaf import (
        load_lifecycle_owner_role_candidate_config,
    )

    return load_lifecycle_owner_role_candidate_config(path)


def _observe_lifecycle_owner_role_holdout(
    selection: LifecycleHoldoutSelection,
    packet: LifecycleHoldoutReviewPacket,
    consensus: LifecycleHoldoutConsensus,
    checkout: VerifiedHoldoutCheckout,
    *,
    candidate_tags_path: str | Path,
    file_parser: FileAnalysisParser | None = None,
    unit_builder: ReviewUnitBuilder | None = None,
) -> tuple[dict[str, object], ...]:
    _validate_chain(selection, packet, consensus)
    from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import (
        ArktsTreeSitterParser,
    )
    from arkts_code_reviewer.code_analysis.file_analysis_models import CodeSourceRef
    from arkts_code_reviewer.code_analysis.file_analysis_parser import (
        ArktsFileAnalysisParser,
    )
    from arkts_code_reviewer.code_analysis.models import FileHunk
    from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
    from arkts_code_reviewer.code_analysis.unit_facts import project
    from arkts_code_reviewer.feature_routing.engine import FeatureRouter
    from arkts_code_reviewer.feature_routing.models import FEATURE_ROUTING_V3_SCHEMA_VERSION
    from arkts_code_reviewer.feature_routing.owner_context import (
        OwnerAwareRoutingInput,
        derive_unit_owner_context,
    )

    config = _load_candidate_config(candidate_tags_path)
    if config.fingerprint != selection.candidate_freeze.feature_config_fingerprint:
        raise ValueError("holdout candidate config differs from the pre-selection freeze")
    node_executable = shutil.which("node")
    if node_executable is None:
        raise ValueError("frozen holdout Node executable is unavailable")
    parser = file_parser or ArktsFileAnalysisParser(
        ArktsTreeSitterParser(
            node_executable=node_executable,
            timeout_seconds=20.0,
        )
    )
    builder = unit_builder or ReviewUnitBuilder()
    router = FeatureRouter(config)
    sources = {item.alias: item for item in selection.sources}
    cases_by_source: dict[str, list[HoldoutSelectionCase]] = {}
    for item in selection.cases:
        cases_by_source.setdefault(item.source_alias, []).append(item)
    consensus_by_case = {item.case_id: item for item in consensus.cases}
    rows: list[dict[str, object]] = []
    observed_unit_ids: set[str] = set()

    for alias in sorted(cases_by_source):
        source = sources[alias]
        text = checkout.source_text_by_alias[alias]
        source_ref = CodeSourceRef.create(
            repository=selection.repository.repository,
            revision=selection.repository.revision,
            path=source.path,
            content_hash=source.content_sha256,
        )
        parsed = parser.parse_file(source_ref, text)
        for case in sorted(cases_by_source[alias], key=lambda value: value.case_id):
            case_id = case.case_id
            changed_line = case.changed_line
            built = builder.build_file_result(
                source.path,
                text,
                parsed.compatibility_facts,
                "diff",
                [FileHunk(new_start=changed_line, new_lines=1)],
                source_ref_id=source_ref.source_ref_id,
            )
            if len(built.units) != 1:
                raise ValueError(
                    f"holdout case {case_id} must resolve to exactly one ReviewUnit, "
                    f"got {len(built.units)}"
                )
            unit = built.units[0]
            truth = consensus_by_case[case_id]
            actual_span = {
                "start_line": unit.source_span.start_line,
                "end_line": unit.source_span.end_line,
            }
            expected_span = truth.expected_source_span
            expected_identity = (
                truth.expected_unit_kind,
                truth.expected_unit_symbol,
                None
                if expected_span is None
                else {
                    "start_line": expected_span.start_line,
                    "end_line": expected_span.end_line,
                },
            )
            actual_identity = (unit.unit_kind, unit.unit_symbol, actual_span)
            if actual_identity != expected_identity:
                raise ValueError(
                    f"holdout case {case_id} ReviewUnit identity drift: "
                    f"expected={expected_identity!r}, actual={actual_identity!r}"
                )
            scope = project(parsed.analysis, unit)
            owner_context = derive_unit_owner_context(parsed.analysis, unit)
            occurrence_names = {
                item.occurrence_id: item.canonical_name for item in parsed.analysis.fact_occurrences
            }
            matching_owner_evidence = [
                item for item in owner_context.evidence if item.symbol == unit.unit_symbol
            ]
            role_evidence_names = {
                occurrence_names[occurrence_id]
                for item in matching_owner_evidence
                for occurrence_id in item.role_evidence_occurrence_ids
                if occurrence_id in occurrence_names
            }
            challenge_owner_failure = False
            if case.stratum_id == "component_v1_positive":
                challenge_owner_failure = "@Component" not in role_evidence_names
            elif case.stratum_id == "component_v2_positive":
                challenge_owner_failure = "@ComponentV2" not in role_evidence_names
            elif case.stratum_id == "router_page_positive":
                challenge_owner_failure = "@Entry" not in role_evidence_names or not {
                    "@Component",
                    "@ComponentV2",
                }.intersection(role_evidence_names)
            result = router.route_owner_aware_shadow(
                [
                    OwnerAwareRoutingInput(
                        scope=scope,
                        unit=unit,
                        file_analysis=parsed.analysis,
                    )
                ]
            )
            if result.schema_version != FEATURE_ROUTING_V3_SCHEMA_VERSION:
                raise ValueError("holdout candidate did not produce feature-routing-v3")
            profile = result.units[0]
            actual_exact = "has_lifecycle" in profile.exact_tags
            exact_matches = [
                item
                for item in profile.tag_matches
                if item.tag_id == "has_lifecycle" and item.scope == "unit_exact"
            ]
            file_hint_promoted = actual_exact and not exact_matches
            routing_only_failure = case.stratum_id == "routing_only_negative" and (
                actual_exact
                or "has_lifecycle" not in profile.routing_tags
                or not _file_hint_provenance_valid(profile)
            )
            parser_quality = parsed.analysis.parser_quality
            parser_risk = (
                parser_quality.layer != "L1"
                or parser_quality.error_nodes != 0
                or parser_quality.missing_nodes != 0
                or bool(parser_quality.warnings)
                or bool(parsed.analysis.diagnostics)
            )
            review_unit_risk = (
                unit.context_degraded
                or bool(unit.diagnostics)
                or bool(built.diagnostics)
                or bool(built.unassigned_hunk_lines)
            )
            scope_risk = bool(scope.diagnostics)
            if unit.unit_id in observed_unit_ids:
                raise ValueError("holdout cases resolve to duplicate ReviewUnit identities")
            observed_unit_ids.add(unit.unit_id)
            rows.append(
                {
                    "case_id": case_id,
                    "source_alias": alias,
                    "source_family_id": source.source_family_id,
                    "stratum_id": case.stratum_id,
                    "unit_id": unit.unit_id,
                    "unit_kind": unit.unit_kind,
                    "unit_symbol": unit.unit_symbol,
                    "expected_exact_tag": truth.semantic_label == "positive",
                    "actual_exact_tag": actual_exact,
                    "actual_routing_tag": "has_lifecycle" in profile.routing_tags,
                    "parser_layer": parser_quality.layer,
                    "parser_error_nodes": parser_quality.error_nodes,
                    "parser_missing_nodes": parser_quality.missing_nodes,
                    "parser_warnings": list(parser_quality.warnings),
                    "file_diagnostics": list(parsed.analysis.diagnostics),
                    "scope_diagnostics": list(scope.diagnostics),
                    "profile_diagnostics": list(profile.diagnostics),
                    "parser_risk": parser_risk,
                    "review_unit_risk": review_unit_risk,
                    "scope_risk": scope_risk,
                    "routing_only_failure": routing_only_failure,
                    "challenge_owner_failure": challenge_owner_failure,
                    "owner_provenance_failure": not _owner_provenance_valid(profile),
                    "file_hint_promoted_to_exact": file_hint_promoted,
                    "tag_matches": [item.to_dict() for item in profile.tag_matches],
                    "review_unit_diagnostics": [
                        {"code": item.code, "lines": list(item.lines)} for item in unit.diagnostics
                    ],
                    "review_unit_build_diagnostics": [
                        {"code": item.code, "lines": list(item.lines)} for item in built.diagnostics
                    ],
                    "unassigned_hunk_lines": list(built.unassigned_hunk_lines),
                }
            )
    return tuple(sorted(rows, key=lambda item: cast(str, item["case_id"])))


def _score_lifecycle_holdout_observations(
    selection: LifecycleHoldoutSelection,
    observations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    expected_case_ids = [item.case_id for item in selection.cases]
    actual_case_ids = [cast(str, item.get("case_id")) for item in observations]
    if actual_case_ids != expected_case_ids:
        raise ValueError("holdout observations do not cover the sealed case set in order")
    overall = _confusion_metrics(observations)
    stratum_ids = [item.stratum_id for item in selection.selection_policy.strata]
    by_stratum = {
        stratum: _confusion_metrics(
            [item for item in observations if item.get("stratum_id") == stratum]
        )
        for stratum in stratum_ids
    }
    gates = selection.selection_policy.quality_gates
    blockers: list[str] = []
    if cast(int, overall["case_count"]) < gates.minimum_case_count:
        blockers.append("insufficient_case_count")
    if cast(int, overall["positive_case_count"]) < gates.minimum_positive_cases:
        blockers.append("insufficient_positive_cases")
    if cast(int, overall["negative_case_count"]) < gates.minimum_negative_cases:
        blockers.append("insufficient_negative_cases")
    source_family_count = len({cast(str, item.get("source_family_id")) for item in observations})
    if source_family_count < gates.minimum_source_families:
        blockers.append("insufficient_source_families")
    if cast(float, overall["precision"]) < gates.minimum_precision:
        blockers.append("precision_below_frozen_gate")
    if cast(float, overall["recall"]) < gates.minimum_recall:
        blockers.append("recall_below_frozen_gate")
    if cast(float, overall["precision_wilson_lower_95"]) < gates.minimum_precision_wilson_95:
        blockers.append("precision_wilson_lower_bound_below_frozen_gate")
    if cast(float, overall["recall_wilson_lower_95"]) < gates.minimum_recall_wilson_95:
        blockers.append("recall_wilson_lower_bound_below_frozen_gate")
    if cast(int, overall["false_positive"]) > gates.maximum_false_positives:
        blockers.append("false_positives_above_frozen_gate")
    if cast(int, overall["false_negative"]) > gates.maximum_false_negatives:
        blockers.append("false_negatives_above_frozen_gate")
    critical_false_positives = sum(
        item.get("stratum_id") in gates.critical_negative_strata
        and item.get("expected_exact_tag") is False
        and item.get("actual_exact_tag") is True
        for item in observations
    )
    if critical_false_positives > gates.maximum_critical_false_positives:
        blockers.append("critical_false_positives_above_frozen_gate")
    parser_risk_count = sum(item.get("parser_risk") is True for item in observations)
    provenance_failure_count = sum(
        item.get("owner_provenance_failure") is True for item in observations
    )
    file_hint_promotion_count = sum(
        item.get("file_hint_promoted_to_exact") is True for item in observations
    )
    review_unit_risk_count = sum(item.get("review_unit_risk") is True for item in observations)
    scope_risk_count = sum(item.get("scope_risk") is True for item in observations)
    routing_only_failure_count = sum(
        item.get("routing_only_failure") is True for item in observations
    )
    challenge_owner_failure_count = sum(
        item.get("challenge_owner_failure") is True for item in observations
    )
    if parser_risk_count > gates.maximum_parser_risk_cases:
        blockers.append("parser_risk_above_frozen_gate")
    if provenance_failure_count > gates.maximum_provenance_failure_cases:
        blockers.append("owner_provenance_failure_above_frozen_gate")
    if file_hint_promotion_count > gates.maximum_file_hint_promotions:
        blockers.append("file_hint_promotion_above_frozen_gate")
    if review_unit_risk_count > gates.maximum_review_unit_risk_cases:
        blockers.append("review_unit_risk_above_frozen_gate")
    if scope_risk_count > gates.maximum_scope_risk_cases:
        blockers.append("unit_fact_scope_risk_above_frozen_gate")
    if routing_only_failure_count > gates.maximum_routing_only_failures:
        blockers.append("routing_only_contract_failure_above_frozen_gate")
    if challenge_owner_failure_count > gates.maximum_challenge_owner_failures:
        blockers.append("challenge_owner_evidence_failure_above_frozen_gate")
    blockers = sorted(set(blockers))
    return {
        "source_family_count": source_family_count,
        "overall": overall,
        "by_stratum": by_stratum,
        "safety_counts": {
            "critical_false_positive": critical_false_positives,
            "parser_risk": parser_risk_count,
            "owner_provenance_failure": provenance_failure_count,
            "file_hint_promotion": file_hint_promotion_count,
            "review_unit_risk": review_unit_risk_count,
            "unit_fact_scope_risk": scope_risk_count,
            "routing_only_failure": routing_only_failure_count,
            "challenge_owner_failure": challenge_owner_failure_count,
        },
        "computed_quality_gate": {
            "passed": not blockers,
            "failures": blockers,
        },
        "cases": [dict(item) for item in observations],
    }


def _component_v2_contract_coverage(
    observations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    component_v2_rows = [
        item for item in observations if item.get("stratum_id") == "component_v2_positive"
    ]
    verified_count = sum(item.get("challenge_owner_failure") is False for item in component_v2_rows)
    qualified = len(component_v2_rows) == 4 and verified_count == 4
    if qualified:
        reason = "all four selected ComponentV2 owner-role cases passed evidence checks"
    elif len(component_v2_rows) != 4:
        reason = (
            "formal observations do not preserve the fixed four-case ComponentV2 contract slice"
        )
    else:
        reason = (
            f"{4 - verified_count} of 4 selected ComponentV2 cases failed owner-role "
            "evidence checks"
        )
    return {
        "full_candidate_contract_qualified": qualified,
        "component_v2_selected_case_count": len(component_v2_rows),
        "component_v2_owner_verified_case_count": verified_count,
        "known_unqualified_slices": ([] if qualified else ["component_v2_owner_role"]),
        "reason": reason,
    }


def evaluate_lifecycle_owner_role_holdout(
    *,
    selection_path: str | Path,
    packet_path: str | Path,
    receipt_paths: Sequence[str | Path],
    consensus_path: str | Path,
    source_root: str | Path,
    repository_root: str | Path,
    candidate_tags_path: str | Path,
    seal_revision: str,
) -> dict[str, object]:
    if len(receipt_paths) != 2:
        raise ValueError("sealed holdout evaluation requires exactly two receipt paths")
    verify_holdout_execution_environment(repository_root)
    artifact_paths = (selection_path, packet_path, *receipt_paths, consensus_path)
    verified = verify_holdout_artifacts_sealed(
        repository_root,
        seal_revision,
        artifact_paths,
    )
    selection = parse_lifecycle_holdout_selection(verified.artifact_bytes[0])
    packet = parse_lifecycle_holdout_review_packet(verified.artifact_bytes[1])
    receipts = tuple(
        parse_lifecycle_holdout_review_receipt(raw) for raw in verified.artifact_bytes[2:4]
    )
    consensus = parse_lifecycle_holdout_consensus(verified.artifact_bytes[4])
    sealed = verified.seal_revision
    root = Path(repository_root).resolve(strict=True)
    if not _is_ancestor(
        root,
        selection.candidate_freeze.evaluation_harness_commit,
        sealed,
    ):
        raise ValueError("holdout was sealed before the frozen evaluation harness")
    verify_evaluation_harness_bundle(selection.candidate_freeze, root)
    verify_candidate_runtime_bundle(selection.candidate_freeze, root)
    verify_approved_selection_policy(selection, root)
    from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
        load_tag_retrieval_truth,
    )

    development_truth = load_tag_retrieval_truth(
        root / "tests/evaluation/tag_retrieval/manifest.json"
    )
    verify_selection_development_exclusions(selection, development_truth)
    checkout = verify_lifecycle_holdout_checkout(selection, source_root)
    verify_candidate_corpus_independence(selection, source_root)
    contract, review_policy = load_canonical_lifecycle_review_material(root)
    rebuilt_packet = build_lifecycle_holdout_review_packet(
        selection,
        checkout,
        target_tag_contract=contract,
        review_policy=review_policy,
    )
    if rebuilt_packet != packet:
        raise ValueError("sealed review packet does not match canonical selection source material")
    if build_lifecycle_holdout_consensus(packet, receipts) != consensus:
        raise ValueError("sealed consensus does not match the two sealed review receipts")
    _validate_chain(selection, packet, consensus)
    _validate_challenge_eligibility(selection, consensus, checkout)
    expected_candidate_path = (
        root / "tests/fixtures/feature_routing/tag_config_lifecycle_owner_role_shadow_v1.yaml"
    )
    candidate_path = Path(candidate_tags_path)
    if (
        candidate_path.is_symlink()
        or candidate_path.resolve(strict=True) != expected_candidate_path
    ):
        raise ValueError("holdout evaluation must use the frozen candidate Tag config path")
    rows = _observe_lifecycle_owner_role_holdout(
        selection,
        packet,
        consensus,
        checkout,
        candidate_tags_path=candidate_path,
    )
    computed = _score_lifecycle_holdout_observations(selection, rows)
    computed_gate = cast(dict[str, object], computed.pop("computed_quality_gate"))
    contract_coverage = _component_v2_contract_coverage(rows)
    component_v2_qualified = contract_coverage["full_candidate_contract_qualified"] is True
    gate_failures = list(cast(list[str], computed_gate["failures"]))
    if not component_v2_qualified:
        gate_failures.append("component_v2_contract_slice_not_represented")
    gate_failures = sorted(set(gate_failures))
    report: dict[str, object] = {
        "schema_version": LIFECYCLE_HOLDOUT_EVALUATION_SCHEMA_VERSION,
        "evaluation_role": "post_seal_independent_blind_holdout",
        "selection_id": selection.selection_id,
        "seal_revision": sealed,
        "candidate_commit": selection.candidate_freeze.candidate_commit,
        "candidate_feature_config_fingerprint": (
            selection.candidate_freeze.feature_config_fingerprint
        ),
        "candidate_runtime_bundle_fingerprint": (
            selection.candidate_freeze.runtime_bundle_fingerprint
        ),
        "evaluation_harness_commit": (selection.candidate_freeze.evaluation_harness_commit),
        "evaluation_harness_fingerprint": (
            selection.candidate_freeze.evaluation_harness_fingerprint
        ),
        "tags_config_version": selection.candidate_freeze.tags_config_version,
        "feature_routing_schema_version": (
            selection.candidate_freeze.feature_routing_schema_version
        ),
        **computed,
        "evidence_gate": {
            "evidence_ready": not gate_failures,
            "failures": gate_failures,
            "scope": "full_frozen_candidate_contract",
        },
        "contract_coverage": contract_coverage,
        "production_activation": {
            "activation_ready": False,
            "reason": "independent holdout evidence never mutates or activates default tags-v1",
        },
    }
    report["packet_id"] = packet.packet_id
    report["consensus_id"] = consensus.consensus_id
    report["receipt_ids"] = [item.receipt_id for item in consensus.receipt_references]
    return report


__all__ = [
    "LIFECYCLE_HOLDOUT_EVALUATION_SCHEMA_VERSION",
    "VerifiedHoldoutArtifacts",
    "evaluate_lifecycle_owner_role_holdout",
    "verify_holdout_execution_environment",
    "verify_holdout_artifacts_sealed",
]
