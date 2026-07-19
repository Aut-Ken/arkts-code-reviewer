from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Literal

from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import (
    ArktsTreeSitterParser,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    FileParseResult,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    SIDECAR_OUTPUT_SCHEMA,
    ArktsFileAnalysisParser,
)
from arkts_code_reviewer.hybrid_analysis._canonical import load_json_object

REPOSITORY_SYNTHETIC_CAMPAIGN_PARSER_PROFILE: Literal[
    "repository-synthetic-campaign-file-analysis-v1"
] = "repository-synthetic-campaign-file-analysis-v1"
REPOSITORY_SYNTHETIC_CAMPAIGN_REPOSITORY = "arkts-code-reviewer-fixed-synthetic"
REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_REVISION = "repository-synthetic-campaign-base-v1"
REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_REVISION = "repository-synthetic-campaign-head-v1"
REPOSITORY_SYNTHETIC_CAMPAIGN_PATH = "fixtures/repository_synthetic_campaign.ets"
REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_CODE_SHA256 = (
    "sha256:a40fa0c5ab4a2ba3486686d8ad2ef65f6f3d6dbf3497dd84d08fc95e14413a1d"
)
REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_CODE_SHA256 = (
    "sha256:8b4f74c2986d3214f5883dbba482292511958422024797cc481b900843751b09"
)

_ASSET_PATH = (
    Path(__file__).with_name("defaults") / "repository_synthetic_campaign_file_analysis.json"
)
_ASSET_SHA256 = "b095bc45f9fae3f6d1b36eeb95092219f7c4b65bc2939e74e7c9b41bd1bf4c24"


class RepositorySyntheticCampaignParserError(ValueError):
    """The closed repository Campaign parser profile cannot be replayed."""


def _load_snapshots() -> dict[str, dict[str, Any]]:
    try:
        raw = _ASSET_PATH.read_bytes()
    except OSError:
        raise RepositorySyntheticCampaignParserError(
            "frozen_file_analysis_asset_unavailable"
        ) from None
    if hashlib.sha256(raw).hexdigest() != _ASSET_SHA256:
        raise RepositorySyntheticCampaignParserError("frozen_file_analysis_asset_identity_mismatch")
    try:
        payload = load_json_object(raw, "Repository Synthetic Campaign FileAnalysis")
    except (TypeError, ValueError):
        raise RepositorySyntheticCampaignParserError("frozen_file_analysis_asset_invalid") from None
    if set(payload) != {"base", "head"}:
        raise RepositorySyntheticCampaignParserError("frozen_file_analysis_asset_invalid")

    snapshots: dict[str, dict[str, Any]] = {}
    for name, source_sha256 in (
        ("base", REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_CODE_SHA256),
        ("head", REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_CODE_SHA256),
    ):
        snapshot = payload.get(name)
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("path") != REPOSITORY_SYNTHETIC_CAMPAIGN_PATH
            or snapshot.get("output_schema") != SIDECAR_OUTPUT_SCHEMA
            or snapshot.get("error_nodes") != 0
            or snapshot.get("missing_nodes") != 0
        ):
            raise RepositorySyntheticCampaignParserError("frozen_file_analysis_asset_invalid")
        snapshots[source_sha256] = snapshot
    return snapshots


class _SnapshotTreeSitterParser(ArktsTreeSitterParser):
    def __init__(self) -> None:
        super().__init__(
            sidecar_path=_ASSET_PATH,
            node_executable="disabled-frozen-campaign-no-subprocess",
            timeout_seconds=1.0,
        )
        self._snapshots = _load_snapshots()

    def _run_sidecar(
        self,
        source: str,
        path: str,
        output_schema: str | None = None,
    ) -> dict[str, Any]:
        if path != REPOSITORY_SYNTHETIC_CAMPAIGN_PATH:
            raise RepositorySyntheticCampaignParserError("frozen_file_analysis_path_mismatch")
        if output_schema != SIDECAR_OUTPUT_SCHEMA:
            raise RepositorySyntheticCampaignParserError("frozen_file_analysis_schema_mismatch")
        source_sha256 = "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()
        snapshot = self._snapshots.get(source_sha256)
        if snapshot is None:
            raise RepositorySyntheticCampaignParserError("frozen_file_analysis_source_mismatch")
        return copy.deepcopy(snapshot)


class RepositorySyntheticCampaignFileParser:
    """Closed two-source FileAnalysis replay used only by the fixed Campaign."""

    def __init__(self) -> None:
        self._delegate = ArktsFileAnalysisParser(parser=_SnapshotTreeSitterParser())
        self._expected_refs = {
            CodeSourceRef.create(
                repository=REPOSITORY_SYNTHETIC_CAMPAIGN_REPOSITORY,
                revision=REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_REVISION,
                path=REPOSITORY_SYNTHETIC_CAMPAIGN_PATH,
                content_hash=REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_CODE_SHA256,
            ),
            CodeSourceRef.create(
                repository=REPOSITORY_SYNTHETIC_CAMPAIGN_REPOSITORY,
                revision=REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_REVISION,
                path=REPOSITORY_SYNTHETIC_CAMPAIGN_PATH,
                content_hash=REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_CODE_SHA256,
            ),
        }

    def parse_file(self, source_ref: CodeSourceRef, source: str) -> FileParseResult:
        if source_ref not in self._expected_refs:
            raise RepositorySyntheticCampaignParserError("frozen_file_analysis_source_ref_mismatch")
        source_ref.verify_content(source)
        result = self._delegate.parse_file(source_ref, source)
        quality = result.analysis.parser_quality
        if (
            quality.layer != "L1"
            or quality.error_nodes != 0
            or quality.missing_nodes != 0
            or quality.warnings
            or result.analysis.diagnostics
        ):
            raise RepositorySyntheticCampaignParserError("frozen_file_analysis_quality_mismatch")
        return result

    def __repr__(self) -> str:
        return "RepositorySyntheticCampaignFileParser(<hash-verified-package-asset>)"


__all__ = [
    "REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_CODE_SHA256",
    "REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_REVISION",
    "REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_CODE_SHA256",
    "REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_REVISION",
    "REPOSITORY_SYNTHETIC_CAMPAIGN_PARSER_PROFILE",
    "REPOSITORY_SYNTHETIC_CAMPAIGN_PATH",
    "REPOSITORY_SYNTHETIC_CAMPAIGN_REPOSITORY",
    "RepositorySyntheticCampaignFileParser",
    "RepositorySyntheticCampaignParserError",
]
