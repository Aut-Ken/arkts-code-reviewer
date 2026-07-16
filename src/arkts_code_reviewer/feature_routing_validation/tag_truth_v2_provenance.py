from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    bytes_hash,
    canonical_hash,
    canonical_json,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review import (
    ConsensusBlocker,
    TagTruthV2Consensus,
    TagTruthV2ReviewReceipt,
    parse_tag_truth_v2_consensus,
    parse_tag_truth_v2_review_receipt,
    validate_tag_truth_v2_review_receipt,
    verify_tag_truth_v2_consensus,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    DevelopmentTruthExclusionSnapshot,
    TagTruthV2ReviewPacket,
    TagTruthV2Selection,
    parse_tag_truth_v2_review_packet,
    parse_tag_truth_v2_selection,
    verify_tag_truth_v2_development_exclusions,
    verify_tag_truth_v2_review_packet,
    verify_tag_truth_v2_selection_checkout,
    verify_tag_truth_v2_selection_exposure,
)

TAG_TRUTH_V2_PROVENANCE_VERIFICATION_SCHEMA_VERSION = "tag-truth-v2-provenance-verification-v1"

_GIT_OBJECT_ID = r"^[0-9a-f]{40}$"
_SHA256 = r"^sha256:[0-9a-f]{64}$"
_VERIFICATION_ID = r"^tag-truth-provenance-verification:sha256:[0-9a-f]{64}$"
_CANDIDATE_FREEZE_ID = r"^tag-truth-candidate-freeze:sha256:[0-9a-f]{64}$"
_SELECTION_ID = r"^tag-truth-selection:sha256:[0-9a-f]{64}$"
_PACKET_ID = r"^tag-truth-review-packet:sha256:[0-9a-f]{64}$"
_RECEIPT_ID = r"^tag-truth-review-receipt:sha256:[0-9a-f]{64}$"
_CONSENSUS_ID = r"^tag-truth-consensus:sha256:[0-9a-f]{64}$"

SealedArtifactRole = Literal["selection", "packet", "receipt", "consensus"]
_ROLE_ORDER: Mapping[SealedArtifactRole, int] = {
    "selection": 0,
    "packet": 1,
    "receipt": 2,
    "consensus": 3,
}
_LOGICAL_ID_PREFIX: Mapping[SealedArtifactRole, str] = {
    "selection": "tag-truth-selection:sha256:",
    "packet": "tag-truth-review-packet:sha256:",
    "receipt": "tag-truth-review-receipt:sha256:",
    "consensus": "tag-truth-consensus:sha256:",
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _relative_path(value: str, context: str) -> str:
    if (
        value != value.strip()
        or not value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{context} must be a non-empty trimmed POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{context} must be relative and cannot traverse parents")
    if path.as_posix() != value:
        raise ValueError(f"{context} must be normalized")
    return value


def _single_line(value: str, context: str) -> str:
    if value != value.strip() or not value or any(ord(character) < 32 for character in value):
        raise ValueError(f"{context} must be non-empty, trimmed, and single-line")
    return value


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={identity_field})


def _artifact_sort_key(
    artifact: TagTruthV2SealedArtifact,
) -> tuple[int, str, str]:
    return (_ROLE_ORDER[artifact.role], artifact.logical_id, artifact.path)


def _git_blob_id(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw, usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class CapturedCommittedArtifact:
    """The exact bytes and Git metadata captured from a seal commit."""

    role: SealedArtifactRole
    path: str
    raw_bytes: bytes
    git_blob_id: str


class TagTruthV2SealedArtifact(_FrozenModel):
    role: SealedArtifactRole
    path: str
    content_sha256: Annotated[str, Field(pattern=_SHA256)]
    git_blob_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    logical_id: Annotated[
        str,
        Field(
            pattern=(
                r"^tag-truth-(?:selection|review-packet|review-receipt|consensus):"
                r"sha256:[0-9a-f]{64}$"
            )
        ),
    ]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value, "sealed artifact path")

    @model_validator(mode="after")
    def validate_role_identity(self) -> TagTruthV2SealedArtifact:
        if not self.logical_id.startswith(_LOGICAL_ID_PREFIX[self.role]):
            raise ValueError("sealed artifact logical_id does not match its role")
        return self


class _TagTruthV2ProvenanceVerificationPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-provenance-verification-v1"]
    seal_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    seal_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    sealed_artifacts: tuple[TagTruthV2SealedArtifact, ...]
    candidate_freeze_id: Annotated[str, Field(pattern=_CANDIDATE_FREEZE_ID)]
    candidate_commit: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    source_repository_source_id: Annotated[
        str,
        Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"),
    ]
    source_repository_origin: Annotated[str, Field(min_length=1)]
    source_repository_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    source_repository_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    exposure_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    exposure_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    selection_id: Annotated[str, Field(pattern=_SELECTION_ID)]
    packet_id: Annotated[str, Field(pattern=_PACKET_ID)]
    receipt_ids: tuple[Annotated[str, Field(pattern=_RECEIPT_ID)], ...]
    consensus_id: Annotated[str, Field(pattern=_CONSENSUS_ID)]
    consensus_status: Literal["complete", "unresolved"]
    consensus_blockers: tuple[ConsensusBlocker, ...]
    integrity_status: Literal["verified"]
    evidence_qualification_status: Literal["not_qualified"]
    candidate_execution_status: Literal["not_run"]

    @field_validator("source_repository_origin")
    @classmethod
    def validate_source_repository_origin(cls, value: str) -> str:
        return _single_line(value, "source repository origin")

    @field_validator(
        "sealed_artifacts",
        "receipt_ids",
        "consensus_blockers",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"provenance {getattr(info, 'field_name', 'sequence')}")

    @model_validator(mode="after")
    def validate_payload(self) -> _TagTruthV2ProvenanceVerificationPayload:
        expected_counts: Counter[str] = Counter(
            {"selection": 1, "packet": 1, "receipt": 2, "consensus": 1}
        )
        actual_counts = Counter(item.role for item in self.sealed_artifacts)
        if actual_counts != expected_counts:
            raise ValueError(
                "provenance verification requires one selection, one packet, "
                "two receipts, and one consensus"
            )
        if self.sealed_artifacts != tuple(sorted(self.sealed_artifacts, key=_artifact_sort_key)):
            raise ValueError("sealed artifacts must use deterministic canonical ordering")
        paths = tuple(item.path for item in self.sealed_artifacts)
        logical_ids = tuple(item.logical_id for item in self.sealed_artifacts)
        if len(paths) != len(set(paths)):
            raise ValueError("sealed artifact paths must be unique")
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError("sealed artifact logical IDs must be unique")

        if len(self.receipt_ids) != 2 or self.receipt_ids != tuple(sorted(set(self.receipt_ids))):
            raise ValueError("provenance verification requires two sorted unique receipt IDs")
        if self.consensus_blockers != tuple(sorted(set(self.consensus_blockers))):
            raise ValueError("consensus blockers must be sorted and unique")
        if (self.consensus_status == "complete") != (not self.consensus_blockers):
            raise ValueError("consensus status and blockers are inconsistent")

        by_role: dict[str, tuple[str, ...]] = {
            role: tuple(item.logical_id for item in self.sealed_artifacts if item.role == role)
            for role in _ROLE_ORDER
        }
        if by_role["selection"] != (self.selection_id,):
            raise ValueError("selection artifact does not match selection_id")
        if by_role["packet"] != (self.packet_id,):
            raise ValueError("packet artifact does not match packet_id")
        if tuple(sorted(by_role["receipt"])) != self.receipt_ids:
            raise ValueError("receipt artifacts do not match receipt_ids")
        if by_role["consensus"] != (self.consensus_id,):
            raise ValueError("consensus artifact does not match consensus_id")
        return self


class TagTruthV2ProvenanceVerification(_TagTruthV2ProvenanceVerificationPayload):
    verification_id: Annotated[str, Field(pattern=_VERIFICATION_ID)]

    @model_validator(mode="after")
    def validate_verification_id(self) -> TagTruthV2ProvenanceVerification:
        expected = canonical_hash(
            "tag-truth-provenance-verification",
            _identity_payload(self, "verification_id"),
        )
        if self.verification_id != expected:
            raise ValueError("provenance verification_id does not match its complete report")
        return self


def provenance_verification_payload_with_id(
    payload: Mapping[str, object],
) -> dict[str, object]:
    if "verification_id" in payload:
        raise ValueError("unsealed provenance report payload cannot contain verification_id")
    canonical_payload = _TagTruthV2ProvenanceVerificationPayload.model_validate_json(
        canonical_json(dict(payload))
    )
    result = canonical_payload.model_dump(mode="json")
    result["verification_id"] = canonical_hash(
        "tag-truth-provenance-verification",
        result,
    )
    return result


def _parse_captured_artifacts(
    artifacts: Sequence[CapturedCommittedArtifact],
) -> tuple[
    TagTruthV2Selection,
    TagTruthV2ReviewPacket,
    tuple[TagTruthV2ReviewReceipt, TagTruthV2ReviewReceipt],
    TagTruthV2Consensus,
    tuple[TagTruthV2SealedArtifact, ...],
]:
    captured = tuple(artifacts)
    counts = Counter(item.role for item in captured)
    expected_counts: Counter[str] = Counter(
        {"selection": 1, "packet": 1, "receipt": 2, "consensus": 1}
    )
    if counts != expected_counts:
        raise ValueError(
            "captured seal requires one selection, one packet, two receipts, and one consensus"
        )
    for item in captured:
        _relative_path(item.path, "captured sealed artifact path")
        if not isinstance(item.raw_bytes, bytes):
            raise ValueError("captured sealed artifact raw_bytes must be bytes")
        if _git_blob_id(item.raw_bytes) != item.git_blob_id:
            raise ValueError(f"captured Git blob identity drift: {item.path}")
    paths = tuple(item.path for item in captured)
    if len(paths) != len(set(paths)):
        raise ValueError("captured sealed artifact paths must be unique")

    selection_capture = next(item for item in captured if item.role == "selection")
    packet_capture = next(item for item in captured if item.role == "packet")
    receipt_captures = tuple(item for item in captured if item.role == "receipt")
    consensus_capture = next(item for item in captured if item.role == "consensus")

    selection = parse_tag_truth_v2_selection(selection_capture.raw_bytes)
    packet = parse_tag_truth_v2_review_packet(packet_capture.raw_bytes)
    parsed_receipts = tuple(
        parse_tag_truth_v2_review_receipt(item.raw_bytes) for item in receipt_captures
    )
    if len(parsed_receipts) != 2:
        raise ValueError("captured seal requires exactly two review receipts")
    receipts = tuple(sorted(parsed_receipts, key=lambda item: item.receipt_id))
    consensus = parse_tag_truth_v2_consensus(consensus_capture.raw_bytes)

    logical_ids_by_path = {
        selection_capture.path: selection.selection_id,
        packet_capture.path: packet.packet_id,
        consensus_capture.path: consensus.consensus_id,
        **{
            capture.path: receipt.receipt_id
            for capture, receipt in zip(receipt_captures, parsed_receipts, strict=True)
        },
    }
    records = tuple(
        sorted(
            (
                TagTruthV2SealedArtifact(
                    role=item.role,
                    path=item.path,
                    content_sha256=bytes_hash(item.raw_bytes),
                    git_blob_id=item.git_blob_id,
                    logical_id=logical_ids_by_path[item.path],
                )
                for item in captured
            ),
            key=_artifact_sort_key,
        )
    )
    return selection, packet, (receipts[0], receipts[1]), consensus, records


def _source_tree_id(source_root: Path, revision: str) -> str:
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.commitGraph=false",
                "-C",
                str(source_root),
                "rev-parse",
                f"{revision}^{{tree}}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("cannot inspect selected source tree identity") from exc
    tree_id = completed.stdout.strip()
    if len(tree_id) != 40 or any(character not in "0123456789abcdef" for character in tree_id):
        raise ValueError("selected source tree does not use a full lowercase object identity")
    return tree_id


def _reject_source_git_grafts(source_root: str | Path) -> None:
    root = Path(source_root)
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.commitGraph=false",
                "-C",
                str(root),
                "rev-parse",
                "--git-common-dir",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("cannot inspect selected source Git common directory") from exc
    common_dir = Path(completed.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    try:
        common_dir = common_dir.resolve(strict=True)
    except OSError as exc:
        raise ValueError("selected source Git common directory is unavailable") from exc
    grafts = common_dir / "info" / "grafts"
    try:
        grafts.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError("cannot inspect selected source legacy Git grafts") from exc
    raise ValueError("legacy Git grafts are forbidden for Tag Truth v2 source verification")


def _source_is_ancestor(source_root: str | Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.commitGraph=false",
                "-C",
                str(source_root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("cannot inspect selected source ancestry") from exc
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise ValueError("cannot inspect selected source ancestry")


def build_tag_truth_v2_provenance_verification(
    *,
    seal_revision: str,
    seal_tree_id: str,
    artifacts: Sequence[CapturedCommittedArtifact],
    source_root: str | Path,
    development_truth: DevelopmentTruthExclusionSnapshot,
) -> TagTruthV2ProvenanceVerification:
    (
        selection,
        packet,
        receipts,
        consensus,
        artifact_records,
    ) = _parse_captured_artifacts(artifacts)

    _reject_source_git_grafts(source_root)
    development_revision = development_truth.repository_revision
    exposure_revision = selection.candidate_freeze.exposure_revision
    selection_revision = selection.repository.revision
    if development_revision != exposure_revision and not _source_is_ancestor(
        source_root,
        development_revision,
        exposure_revision,
    ):
        raise ValueError("development Truth revision is outside the candidate exposure history")
    if exposure_revision == selection_revision or not _source_is_ancestor(
        source_root,
        exposure_revision,
        selection_revision,
    ):
        raise ValueError("selection revision is not a strict descendant of candidate exposure")
    checkout = verify_tag_truth_v2_selection_checkout(selection, source_root)
    verify_tag_truth_v2_development_exclusions(selection, development_truth, source_root)
    verify_tag_truth_v2_selection_exposure(selection, source_root)
    verify_tag_truth_v2_review_packet(packet, selection)
    for receipt in receipts:
        validate_tag_truth_v2_review_receipt(receipt, packet)
    verify_tag_truth_v2_consensus(consensus, packet, receipts)

    source_tree_id = _source_tree_id(checkout.root, selection.repository.revision)
    payload: dict[str, object] = {
        "schema_version": TAG_TRUTH_V2_PROVENANCE_VERIFICATION_SCHEMA_VERSION,
        "seal_revision": seal_revision,
        "seal_tree_id": seal_tree_id,
        "sealed_artifacts": [item.model_dump(mode="json") for item in artifact_records],
        "candidate_freeze_id": selection.candidate_freeze.candidate_freeze_id,
        "candidate_commit": selection.candidate_freeze.candidate_commit,
        "source_repository_source_id": selection.repository.source_id,
        "source_repository_origin": selection.repository.origin,
        "source_repository_revision": selection.repository.revision,
        "source_repository_tree_id": source_tree_id,
        "exposure_revision": selection.candidate_freeze.exposure_revision,
        "exposure_tree_id": selection.candidate_freeze.exposure_tree_id,
        "selection_id": selection.selection_id,
        "packet_id": packet.packet_id,
        "receipt_ids": sorted(receipt.receipt_id for receipt in receipts),
        "consensus_id": consensus.consensus_id,
        "consensus_status": consensus.consensus_status,
        "consensus_blockers": list(consensus.consensus_blockers),
        "integrity_status": "verified",
        "evidence_qualification_status": "not_qualified",
        "candidate_execution_status": "not_run",
    }
    sealed = provenance_verification_payload_with_id(payload)
    return TagTruthV2ProvenanceVerification.model_validate_json(canonical_json(sealed))


def verify_tag_truth_v2_provenance_verification(
    report: TagTruthV2ProvenanceVerification,
    *,
    seal_revision: str,
    seal_tree_id: str,
    artifacts: Sequence[CapturedCommittedArtifact],
    source_root: str | Path,
    development_truth: DevelopmentTruthExclusionSnapshot,
) -> None:
    canonical_report = TagTruthV2ProvenanceVerification.model_validate_json(
        canonical_json(report.model_dump(mode="json"))
    )
    rebuilt = build_tag_truth_v2_provenance_verification(
        seal_revision=seal_revision,
        seal_tree_id=seal_tree_id,
        artifacts=artifacts,
        source_root=source_root,
        development_truth=development_truth,
    )
    if canonical_report != rebuilt:
        raise ValueError(
            "Tag Truth v2 provenance verification does not rebuild from sealed artifacts"
        )


def parse_tag_truth_v2_provenance_verification(
    raw: bytes,
) -> TagTruthV2ProvenanceVerification:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        return TagTruthV2ProvenanceVerification.model_validate_json(canonical_json(payload))
    except (UnicodeError, json.JSONDecodeError, ValidationError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid Tag Truth v2 provenance verification: {exc}") from exc


def load_tag_truth_v2_provenance_verification(
    path: str | Path,
) -> TagTruthV2ProvenanceVerification:
    artifact = Path(path)
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError(
            f"Tag Truth v2 provenance verification must be a regular non-symlink file: {artifact}"
        )
    try:
        raw = artifact.read_bytes()
    except OSError as exc:
        raise ValueError(
            f"cannot read Tag Truth v2 provenance verification {artifact}: {exc}"
        ) from exc
    return parse_tag_truth_v2_provenance_verification(raw)


__all__ = [
    "CapturedCommittedArtifact",
    "SealedArtifactRole",
    "TAG_TRUTH_V2_PROVENANCE_VERIFICATION_SCHEMA_VERSION",
    "TagTruthV2ProvenanceVerification",
    "TagTruthV2SealedArtifact",
    "build_tag_truth_v2_provenance_verification",
    "load_tag_truth_v2_provenance_verification",
    "parse_tag_truth_v2_provenance_verification",
    "provenance_verification_payload_with_id",
    "verify_tag_truth_v2_provenance_verification",
]
