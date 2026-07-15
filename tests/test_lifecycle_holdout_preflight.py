from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from arkts_code_reviewer.retrieval_validation import lifecycle_blind_holdout as holdout
from arkts_code_reviewer.retrieval_validation.lifecycle_blind_holdout import (
    LIFECYCLE_EVALUATION_HARNESS_PATHS,
)

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "tools/lifecycle_holdout_preflight.py"


def _load_preflight_module() -> object:
    import importlib.util

    spec = importlib.util.spec_from_file_location("lifecycle_holdout_preflight", PREFLIGHT)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load preflight module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_preflight_source_has_no_project_package_import() -> None:
    tree = ast.parse(PREFLIGHT.read_text(encoding="utf-8"))
    imported = {
        name
        for node in ast.walk(tree)
        for name in ([node.module] if isinstance(node, ast.ImportFrom) else [])
        if isinstance(name, str)
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any(name.startswith("arkts_code_reviewer") for name in imported)


def test_preflight_and_typed_harness_freeze_the_same_paths() -> None:
    module = _load_preflight_module()

    assert vars(module)["HARNESS_PATHS"] == LIFECYCLE_EVALUATION_HARNESS_PATHS


def test_preflight_and_typed_runtime_checks_produce_the_same_freeze() -> None:
    module = _load_preflight_module()
    dependency_site_packages = vars(module)["_dependency_site_packages_from_executable"]()

    assert vars(module)["_candidate_runtime_paths"](ROOT) == holdout._candidate_runtime_paths(
        ROOT,
        holdout.LIFECYCLE_OWNER_ROLE_CANDIDATE_COMMIT,
    )
    assert vars(module)["_runtime_environment"](
        ROOT,
        dependency_site_packages,
    ) == holdout._current_runtime_environment(ROOT).model_dump(mode="json")


def test_formal_cli_import_does_not_load_candidate_modules() -> None:
    script = f"""
import importlib.util
import json
import pathlib
import sys
tools = pathlib.Path({str(ROOT / "tools")!r})
path = tools / 'evaluate_lifecycle_owner_role_holdout.py'
spec = importlib.util.spec_from_file_location('formal_holdout_cli', path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
loaded = sorted(name for name in sys.modules if name.startswith((
    'arkts_code_reviewer.code_analysis',
    'arkts_code_reviewer.feature_routing',
    'arkts_code_reviewer.retrieval_validation',
)))
print(json.dumps(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-P", "-B", "-S", "-c", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"},
    )

    assert json.loads(completed.stdout) == []


def test_formal_cli_evaluation_id_binds_emitted_report_shape() -> None:
    import importlib.util

    path = ROOT / "tools/evaluate_lifecycle_owner_role_holdout.py"
    spec = importlib.util.spec_from_file_location("formal_holdout_cli_identity", path)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load formal holdout CLI")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = {"schema_version": "example", "case_details_omitted": False}

    full_id = vars(module)["_evaluation_id"](report)
    report["case_details_omitted"] = True

    assert full_id.startswith("lifecycle-owner-role-holdout-evaluation:sha256:")
    assert vars(module)["_evaluation_id"](report) != full_id


def test_formal_cli_converts_candidate_runtime_exception_to_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import importlib.util

    from arkts_code_reviewer.retrieval_validation import (
        lifecycle_blind_holdout_evaluation as evaluation,
    )

    path = ROOT / "tools/evaluate_lifecycle_owner_role_holdout.py"
    spec = importlib.util.spec_from_file_location("formal_holdout_cli_runtime_error", path)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load formal holdout CLI")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dependency_site_packages = vars(_load_preflight_module())[
        "_dependency_site_packages_from_executable"
    ]()
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr(
        module,
        "_load_standard_library_preflight",
        lambda: lambda **_kwargs: ("a" * 40, str(dependency_site_packages)),
    )
    monkeypatch.setattr(
        evaluation,
        "evaluate_lifecycle_owner_role_holdout",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("sidecar failed")),
    )

    exit_code = vars(module)["main"](
        [
            "--selection",
            "selection.json",
            "--packet",
            "packet.json",
            "--receipt",
            "reviewer-a.json",
            "--receipt",
            "reviewer-b.json",
            "--consensus",
            "consensus.json",
            "--source-root",
            "/source",
            "--seal-revision",
            "a" * 40,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "sidecar failed" in captured.err


def test_failed_cli_preflight_does_not_import_candidate_modules() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout.strip()
    script = f"""
import importlib.util
import json
import pathlib
import sys
tools = pathlib.Path({str(ROOT / "tools")!r})
path = tools / 'evaluate_lifecycle_owner_role_holdout.py'
spec = importlib.util.spec_from_file_location('formal_holdout_cli_failed', path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
exit_code = module.main([
    '--selection', 'missing-selection.json',
    '--packet', 'missing-packet.json',
    '--receipt', 'missing-a.json',
    '--receipt', 'missing-b.json',
    '--consensus', 'missing-consensus.json',
    '--source-root', '/missing-source',
    '--seal-revision', {head!r},
])
loaded = sorted(name for name in sys.modules if name.startswith((
    'arkts_code_reviewer.code_analysis',
    'arkts_code_reviewer.feature_routing',
    'arkts_code_reviewer.retrieval_validation',
)))
print(json.dumps({{'exit_code': exit_code, 'loaded': loaded}}))
"""
    completed = subprocess.run(
        [sys.executable, "-P", "-B", "-S", "-c", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"},
    )
    result = json.loads(completed.stdout)

    assert result == {"exit_code": 2, "loaded": []}
    assert "holdout evaluation failed" in completed.stderr


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _build_preflight_repository(
    tmp_path: Path,
    module: object,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, dict[str, object], tuple[str, ...]]:
    root = tmp_path / "sealed-repo"
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Preflight Test")
    _git(root, "config", "user.email", "preflight@example.invalid")
    (root / ".gitignore").write_text(
        "__pycache__/\nsidecars/arkts-parser/node_modules/\n",
        encoding="utf-8",
    )
    (root / "runtime.py").write_text("RUNTIME = 1\n", encoding="utf-8")
    source = root / "src/arkts_code_reviewer/core.py"
    source.parent.mkdir(parents=True)
    source.write_text("CORE = 1\n", encoding="utf-8")
    dependencies = root / "sidecars/arkts-parser/node_modules/package"
    dependencies.mkdir(parents=True)
    (dependencies / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "runtime.py", "src/arkts_code_reviewer/core.py")
    _git(root, "commit", "--quiet", "-m", "candidate")
    candidate = _git(root, "rev-parse", "HEAD")
    (root / "harness.py").write_text("HARNESS = 1\n", encoding="utf-8")
    _git(root, "add", "harness.py")
    _git(root, "commit", "--quiet", "-m", "harness")
    harness = _git(root, "rev-parse", "HEAD")

    monkeypatch.setattr(module, "CANDIDATE_COMMIT", candidate)
    monkeypatch.setattr(module, "CANDIDATE_FINGERPRINT", "feature-config:sha256:" + "f" * 64)
    monkeypatch.setattr(module, "CORPUS_EXPOSURE_REVISION", "e" * 40)
    monkeypatch.setattr(module, "HARNESS_PATHS", ("harness.py",))
    monkeypatch.setattr(module, "_STATIC_RUNTIME_PATHS", {"runtime.py"})
    monkeypatch.setattr(module, "_assert_candidate_modules_not_loaded", lambda: None)
    dependency_site_packages = vars(module)["_dependency_site_packages_from_executable"]()
    monkeypatch.setattr(
        module,
        "_verify_python_environment",
        lambda _root: dependency_site_packages,
    )

    runtime_paths = vars(module)["_candidate_runtime_paths"](root)
    runtime_files = [
        {
            "path": path,
            "content_sha256": vars(module)["_bytes_hash"](
                subprocess.run(
                    ["git", "-C", str(root), "show", f"{candidate}:{path}"],
                    check=True,
                    capture_output=True,
                ).stdout
            ),
        }
        for path in runtime_paths
    ]
    harness_files = [
        {
            "path": "harness.py",
            "content_sha256": vars(module)["_bytes_hash"]((root / "harness.py").read_bytes()),
        }
    ]
    freeze = {
        "candidate_commit": candidate,
        "feature_config_fingerprint": "feature-config:sha256:" + "f" * 64,
        "candidate_corpus_exposure_revision": "e" * 40,
        "candidate_corpus_exposure_scope": "entire_tracked_repository",
        "runtime_files": runtime_files,
        "runtime_bundle_fingerprint": vars(module)["_canonical_hash"](
            "lifecycle-candidate-runtime", runtime_files
        ),
        "runtime_environment": vars(module)["_runtime_environment"](
            root,
            dependency_site_packages,
        ),
        "evaluation_harness_commit": harness,
        "evaluation_harness_files": harness_files,
        "evaluation_harness_fingerprint": vars(module)["_canonical_hash"](
            "lifecycle-evaluation-harness", harness_files
        ),
    }
    selection: dict[str, object] = {
        "schema_version": "lifecycle-holdout-selection-v1",
        "candidate_freeze": freeze,
    }
    selection["selection_id"] = vars(module)["_canonical_hash"](
        "lifecycle-holdout-selection", selection
    )
    artifacts = (
        "artifacts/selection.json",
        "artifacts/packet.json",
        "artifacts/reviewer-a.json",
        "artifacts/reviewer-b.json",
        "artifacts/consensus.json",
    )
    for index, relative in enumerate(artifacts):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: object = selection if index == 0 else {"artifact": index}
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    _git(root, "add", "artifacts")
    _git(root, "commit", "--quiet", "-m", "seal")
    seal = _git(root, "rev-parse", "HEAD")
    return root, seal, selection, artifacts


def test_standard_library_preflight_accepts_a_complete_clean_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_preflight_module()
    root, seal, _selection, artifacts = _build_preflight_repository(
        tmp_path,
        module,
        monkeypatch,
    )
    dependency_site_packages = vars(module)["_dependency_site_packages_from_executable"]()

    assert vars(module)["preflight_formal_holdout"](
        repository_root=root,
        seal_revision=seal,
        artifact_paths=artifacts,
    ) == (seal, str(dependency_site_packages))


def test_standard_library_preflight_rejects_symbolic_seal_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_preflight_module()
    root, _seal, _selection, artifacts = _build_preflight_repository(
        tmp_path,
        module,
        monkeypatch,
    )

    with pytest.raises(ValueError, match="full lowercase commit identity"):
        vars(module)["preflight_formal_holdout"](
            repository_root=root,
            seal_revision="HEAD",
            artifact_paths=artifacts,
        )


def test_standard_library_preflight_rejects_runtime_and_harness_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_preflight_module()
    root, seal, selection, _artifacts = _build_preflight_repository(
        tmp_path,
        module,
        monkeypatch,
    )
    for field, expected in (
        ("runtime_files", "candidate runtime bundle fingerprint mismatch"),
        ("evaluation_harness_files", "evaluation harness fingerprint mismatch"),
        ("runtime_environment", "candidate runtime environment drift"),
    ):
        tampered = copy.deepcopy(selection)
        freeze = tampered["candidate_freeze"]
        assert isinstance(freeze, dict)
        if field == "runtime_environment":
            environment = freeze[field]
            assert isinstance(environment, dict)
            environment["node_version"] = "v0.0.0"
        else:
            snapshots = freeze[field]
            assert isinstance(snapshots, list)
            snapshots[0]["content_sha256"] = "sha256:" + "0" * 64
        try:
            dependency_site_packages = vars(module)["_dependency_site_packages_from_executable"]()
            vars(module)["_verify_freeze"](
                root,
                seal,
                tampered,
                dependency_site_packages,
            )
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"tampered {field} unexpectedly passed")


def test_standard_library_preflight_rejects_dirty_or_post_seal_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_preflight_module()
    root, seal, _selection, artifacts = _build_preflight_repository(
        tmp_path,
        module,
        monkeypatch,
    )
    dirty = root / "dirty.txt"
    dirty.write_text("dirty\n", encoding="utf-8")
    try:
        vars(module)["preflight_formal_holdout"](
            repository_root=root,
            seal_revision=seal,
            artifact_paths=artifacts,
        )
    except ValueError as exc:
        assert "completely clean" in str(exc)
    else:
        raise AssertionError("dirty repository unexpectedly passed")
    dirty.unlink()
    (root / "later.txt").write_text("later\n", encoding="utf-8")
    _git(root, "add", "later.txt")
    _git(root, "commit", "--quiet", "-m", "later")

    try:
        vars(module)["preflight_formal_holdout"](
            repository_root=root,
            seal_revision=seal,
            artifact_paths=artifacts,
        )
    except ValueError as exc:
        assert "HEAD exactly" in str(exc)
    else:
        raise AssertionError("post-seal HEAD unexpectedly passed")


def test_standard_library_preflight_rejects_ignored_project_bytecode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_preflight_module()
    root, seal, _selection, artifacts = _build_preflight_repository(
        tmp_path,
        module,
        monkeypatch,
    )
    cache = root / "src/arkts_code_reviewer/__pycache__"
    cache.mkdir()
    (cache / "core.cpython-312.pyc").write_bytes(b"not-real-bytecode")
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""

    with pytest.raises(ValueError, match="forbids project bytecode cache"):
        vars(module)["preflight_formal_holdout"](
            repository_root=root,
            seal_revision=seal,
            artifact_paths=artifacts,
        )


def test_standard_library_preflight_rejects_extra_sealed_project_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_preflight_module()
    root, _seal, _selection, artifacts = _build_preflight_repository(
        tmp_path,
        module,
        monkeypatch,
    )
    injected = root / "src/injected.py"
    injected.write_text("INJECTED = True\n", encoding="utf-8")
    _git(root, "add", injected.relative_to(root).as_posix())
    _git(root, "commit", "--quiet", "-m", "unexpected executable")
    seal = _git(root, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="sealed project import closure differs"):
        vars(module)["preflight_formal_holdout"](
            repository_root=root,
            seal_revision=seal,
            artifact_paths=artifacts,
        )
