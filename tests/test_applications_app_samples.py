from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from arkts_code_reviewer.retrieval_validation import app_samples
from arkts_code_reviewer.retrieval_validation.app_samples import (
    APP_SAMPLES_REVISION,
    APP_SAMPLES_SOURCE_ID,
    AppSamplesManifest,
    load_app_samples_manifest,
    verify_checkout,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/fixtures/applications_app_samples_v1.json"
LOCAL_CHECKOUT = Path("/home/autken/Code/applications_app_samples")

EXPECTED_PATHS = (
    "code/BasicFeature/Connectivity/NetworkObserver/entry/src/main/ets/utils/NetUtils.ets",
    "code/DocsSample/Ability/UIAbilityLifecycle/README_zh.md",
    "code/DocsSample/Ability/UIAbilityLifecycle/entry/src/main/ets/entryability/EntryAbility.ets",
    "code/DocsSample/ArkTS/ArkTsConcurrent/ApplicationMultithreadingDevelopment/PracticalCases/README.md",
    "code/DocsSample/ArkTS/ArkTsConcurrent/ApplicationMultithreadingDevelopment/PracticalCases/entry/src/main/ets/managers/UsingTaskPool.ets",
    "code/DocsSample/ArkTS/ArkTsConcurrent/ApplicationMultithreadingDevelopment/PracticalCases/entry/src/main/ets/sdk/TimerSdk.ets",
    "code/DocsSample/ArkTS/ArkTsConcurrent/ApplicationMultithreadingDevelopment/PracticalCasesSecond/entry/src/main/ets/pages/workerAndTaskpool.ets",
    "code/DocsSample/ArkTS/ArkTsConcurrent/AsyncConcurrencyOverview/README.md",
    "code/DocsSample/ArkTS/ArkTsConcurrent/AsyncConcurrencyOverview/entry/src/main/ets/pages/Index.ets",
    "code/DocsSample/ArkUISample/ComponentStateManagement/README_zh.md",
    "code/DocsSample/ArkUISample/ComponentStateManagement/entry/src/main/ets/pages/LinkDecorator/LinkUsage.ets",
    "code/DocsSample/ArkUISample/DialogProject/entry/src/main/ets/pages/customdialog/pageleveldialogbox/PageLevelDialogInNavigation.ets",
    "code/DocsSample/ArkUISample/ImageComponent/README_zh.md",
    "code/DocsSample/ArkUISample/ImageComponent/entry/src/main/ets/pages/LoadingResources.ets",
    "code/DocsSample/ArkUISample/Navigation/README_zh.md",
    "code/DocsSample/ArkUISample/Navigation/entry/src/main/ets/pages/navigation/template2/Index.ets",
    "code/DocsSample/ArkUISample/Navigation/entry/src/main/ets/pages/pageRouter/lifeCycle/Index.ets",
    "code/DocsSample/ArkUISample/ParadigmStateManagement/README_zh.md",
    "code/DocsSample/ArkUISample/ParadigmStateManagement/entry/src/main/ets/pages/local/LocalObserveChangesDeepObject.ets",
    "code/DocsSample/ArkUISample/ParadigmStateManagement/entry/src/main/ets/pages/localBuilder/ProblemUINotRefreshOpposite.ets",
    "code/DocsSample/ArkUISample/ParadigmStateManagement/entry/src/main/ets/pages/localBuilder/ProblemUINotRefreshPositive.ets",
    "code/DocsSample/ArkUISample/RenderingControl/entry/src/main/ets/pages/Index.ets",
    "code/DocsSample/NetWork_Kit/NetWorkKit_Datatransmission/HTTP_case/README_zh.md",
    "code/DocsSample/NetWork_Kit/NetWorkKit_Datatransmission/HTTP_case/entry/src/main/ets/pages/Index.ets",
    "code/DocsSample/ResourceManagement/ResourceCategoriesAndAccess/README_zh.md",
    "code/DocsSample/ResourceManagement/ResourceCategoriesAndAccess/entry/src/main/ets/pages/Index.ets",
)


def _load_payload() -> dict[str, Any]:
    payload: object = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _write_payload(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "applications_app_samples_v1.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _mutated_manifest(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> Path:
    payload = _load_payload()
    mutate(payload)
    return _write_payload(tmp_path, payload)


def _make_synthetic_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    head: str = APP_SAMPLES_REVISION,
    status: str = "",
) -> tuple[AppSamplesManifest, Path]:
    root = tmp_path / "checkout"
    root.mkdir(parents=True)
    payload = _load_payload()
    for entry in payload["entries"]:
        raw = f"fixture for {entry['path']}\n".encode()
        target = root / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        entry["sha256"] = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        entry["line_count"] = 1
    manifest = load_app_samples_manifest(_write_payload(tmp_path, payload))
    resolved_root = root.resolve()

    def fake_run_git(checkout_root: Path, *arguments: str) -> str:
        assert checkout_root == resolved_root
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(resolved_root)
        if arguments == ("rev-parse", "HEAD"):
            return head
        if arguments == ("status", "--porcelain", "--untracked-files=all"):
            return status
        raise AssertionError(f"unexpected git arguments: {arguments}")

    monkeypatch.setattr(app_samples, "_run_git", fake_run_git)
    return manifest, root


def test_manifest_freezes_exact_non_normative_allowlist() -> None:
    manifest = load_app_samples_manifest(MANIFEST)

    assert manifest.source_id == APP_SAMPLES_SOURCE_ID
    assert manifest.revision == APP_SAMPLES_REVISION
    assert tuple(entry.path for entry in manifest.entries) == EXPECTED_PATHS
    assert sum(entry.kind == "code" for entry in manifest.entries) == 17
    assert sum(entry.kind == "sample_guidance" for entry in manifest.entries) == 9
    assert all(entry.normative is False for entry in manifest.entries)
    assert all(
        entry.case_role == "neutral"
        for entry in manifest.entries
        if entry.kind == "sample_guidance"
    )
    assert [entry.path for entry in manifest.entries if entry.case_role == "negative"] == [
        "code/DocsSample/ArkUISample/ParadigmStateManagement/entry/src/main/ets/"
        "pages/localBuilder/ProblemUINotRefreshOpposite.ets"
    ]


def test_loader_rejects_duplicate_json_key(tmp_path: Path) -> None:
    raw = MANIFEST.read_text(encoding="utf-8")
    raw = raw.replace(
        '  "source_id": "applications-app-samples",',
        '  "source_id": "applications-app-samples",\n  "source_id": "applications-app-samples",',
        1,
    )
    path = tmp_path / "duplicate.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON key: source_id"):
        load_app_samples_manifest(path)


def test_loader_rejects_unknown_field(tmp_path: Path) -> None:
    path = _mutated_manifest(tmp_path, lambda payload: payload.update({"unexpected": True}))

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_app_samples_manifest(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", "other-source"),
        ("revision", "0" * 40),
        ("schema_version", "applications-app-samples-v2"),
    ],
)
def test_loader_rejects_frozen_provenance(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    path = _mutated_manifest(tmp_path, lambda payload: payload.__setitem__(field, value))

    with pytest.raises(ValueError):
        load_app_samples_manifest(path)


@pytest.mark.parametrize("value", ["bad", "sha256:" + "A" * 64, "sha256:" + "0" * 63])
def test_loader_rejects_invalid_hash(tmp_path: Path, value: str) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["entries"][0]["sha256"] = value

    with pytest.raises(ValueError, match="sha256"):
        load_app_samples_manifest(_mutated_manifest(tmp_path, mutate))


@pytest.mark.parametrize("value", ["/absolute.ets", "../escape.ets", "a/../escape.ets", "a\\b.ets"])
def test_loader_rejects_unsafe_path(tmp_path: Path, value: str) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["entries"][0]["path"] = value

    with pytest.raises(ValueError, match="path"):
        load_app_samples_manifest(_mutated_manifest(tmp_path, mutate))


def test_loader_rejects_duplicate_and_unsorted_paths(tmp_path: Path) -> None:
    duplicate = _load_payload()
    duplicate["entries"][4]["path"] = duplicate["entries"][2]["path"]
    with pytest.raises(ValueError, match="sorted and unique"):
        load_app_samples_manifest(_write_payload(tmp_path, duplicate))

    unsorted = _load_payload()
    unsorted["entries"].reverse()
    with pytest.raises(ValueError, match="sorted and unique"):
        load_app_samples_manifest(_write_payload(tmp_path, unsorted))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "documentation"),
        ("case_role", "trusted"),
        ("normative", True),
    ],
)
def test_loader_rejects_unknown_enums_and_normative_claims(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["entries"][0][field] = value

    with pytest.raises(ValueError):
        load_app_samples_manifest(_mutated_manifest(tmp_path, mutate))


@pytest.mark.parametrize("topics", [["observer", "network"], ["network", "network"], []])
def test_loader_rejects_invalid_topics(tmp_path: Path, topics: list[str]) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["entries"][0]["topics"] = topics

    with pytest.raises(ValueError, match="topics"):
        load_app_samples_manifest(_mutated_manifest(tmp_path, mutate))


def test_verify_checkout_reads_only_frozen_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, root = _make_synthetic_checkout(tmp_path, monkeypatch)
    report = verify_checkout(manifest, root)

    assert report.source_id == APP_SAMPLES_SOURCE_ID
    assert report.revision == APP_SAMPLES_REVISION
    assert report.checkout_root == root.resolve()
    assert report.file_count == 26
    assert report.code_count == 17
    assert report.guidance_count == 9


def test_verify_checkout_rejects_hash_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, root = _make_synthetic_checkout(tmp_path, monkeypatch)
    target = root / manifest.entries[0].path
    target.write_text("drift\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        verify_checkout(manifest, root)


def test_verify_checkout_rejects_line_count_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, root = _make_synthetic_checkout(tmp_path, monkeypatch)
    entry = manifest.entries[0]
    altered = manifest.model_copy(
        update={
            "entries": (
                entry.model_copy(update={"line_count": 2}),
                *manifest.entries[1:],
            )
        }
    )

    with pytest.raises(ValueError, match="line count mismatch"):
        verify_checkout(altered, root)


def test_verify_checkout_rejects_revision_and_dirty_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, root = _make_synthetic_checkout(tmp_path, monkeypatch, head="0" * 40)
    with pytest.raises(ValueError, match="revision mismatch"):
        verify_checkout(manifest, root)

    manifest, root = _make_synthetic_checkout(
        tmp_path / "dirty",
        monkeypatch,
        status="?? unexpected.txt",
    )
    with pytest.raises(ValueError, match="must be clean"):
        verify_checkout(manifest, root)


def test_local_checkout_matches_manifest_when_available() -> None:
    manifest = load_app_samples_manifest(MANIFEST)
    if LOCAL_CHECKOUT.exists():
        report = verify_checkout(manifest, LOCAL_CHECKOUT)
        assert report.file_count == len(EXPECTED_PATHS)
