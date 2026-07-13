from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.knowledge.registry import (
    CheckoutProfile,
    GovernanceProfile,
    IngestionProfile,
    SourceRecord,
    SourceRegistry,
    build_source_bundle,
    ingestion_path_allowed,
    load_source_registry,
    resolve_source_path,
    verify_source_checkout,
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _temporary_repo(tmp_path: Path) -> tuple[Path, str, str]:
    path = tmp_path / "source"
    path.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(path, "config", "user.email", "knowledge@example.invalid")
    _git(path, "config", "user.name", "Knowledge Test")
    remote = "https://example.invalid/knowledge/source.git"
    _git(path, "remote", "add", "origin", remote)
    (path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    _git(path, "add", "guide.md")
    _git(path, "commit", "-m", "fixture")
    return path, _git(path, "rev-parse", "HEAD"), remote


def _source(path: Path, revision: str, remote: str) -> SourceRecord:
    return SourceRecord(
        id="fixture-source",
        group="knowledge_source",
        kind="official_documentation",
        remote=remote,
        local_path=path,
        env_override="FIXTURE_SOURCE_PATH",
        branch="main",
        revision=revision,
        shallow_clone=True,
        checkout=CheckoutProfile(mode="full"),
        use_for=("retrieval_knowledge",),
        ingestion=IngestionProfile(
            include=("**/*.md",),
            exclude=("generated/**",),
            execute_repository_scripts=False,
            index_as_normative_knowledge=True,
        ),
        governance=GovernanceProfile(
            authority="official_documentation",
            curation_required=True,
            raw_prompt_use_allowed=False,
        ),
    )


def _registry_yaml(path: Path, revision: str, remote: str) -> str:
    return f"""\
schema_version: 1
updated_at: 2026-07-13
sources:
  - id: fixture-source
    group: knowledge_source
    kind: official_documentation
    remote: {remote}
    local_path: {path}
    env_override: FIXTURE_SOURCE_PATH
    branch: main
    revision: {revision}
    shallow_clone: true
    checkout:
      mode: full
    use_for:
      - retrieval_knowledge
    ingestion:
      include:
        - "**/*.md"
      exclude:
        - "generated/**"
      execute_repository_scripts: false
      index_as_normative_knowledge: true
    governance:
      authority: official_documentation
      curation_required: true
      raw_prompt_use_allowed: false
"""


def test_registry_loader_and_checkout_verification_are_separate(tmp_path: Path) -> None:
    repo, revision, remote = _temporary_repo(tmp_path)
    registry_path = tmp_path / "sources.yaml"
    registry_path.write_text(_registry_yaml(repo, revision, remote), encoding="utf-8")

    registry = load_source_registry(registry_path)
    assert len(registry.sources) == 1
    verified = verify_source_checkout(registry.sources[0], environment={})
    assert verified.head_revision == revision
    assert verified.resolved_local_path == repo.resolve()


def test_source_bundle_is_order_independent_and_excludes_local_path(tmp_path: Path) -> None:
    repo, revision, remote = _temporary_repo(tmp_path)
    source_a = _source(repo, revision, remote)
    source_b = source_a.model_copy(
        update={
            "id": "fixture-second",
            "env_override": "FIXTURE_SECOND_PATH",
            "local_path": Path("/different/machine/path"),
        }
    )
    registry = SourceRegistry(
        schema_version=1,
        updated_at=__import__("datetime").date(2026, 7, 13),
        sources=(source_a, source_b),
    )

    first, _ = build_source_bundle(
        registry,
        ["fixture-second", "fixture-source"],
        verify=False,
    )
    second, _ = build_source_bundle(
        registry,
        ["fixture-source", "fixture-second"],
        verify=False,
    )
    assert first == second
    assert [entry.source_id for entry in first.entries] == [
        "fixture-second",
        "fixture-source",
    ]
    assert "/different/machine/path" not in first.model_dump_json()


def test_registry_loader_rejects_duplicate_yaml_key(tmp_path: Path) -> None:
    repo, revision, remote = _temporary_repo(tmp_path)
    registry_path = tmp_path / "sources.yaml"
    raw = _registry_yaml(repo, revision, remote)
    raw = raw.replace("schema_version: 1", "schema_version: 1\nschema_version: 1", 1)
    registry_path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate key"):
        load_source_registry(registry_path)


def test_registry_loader_rejects_unknown_fields(tmp_path: Path) -> None:
    repo, revision, remote = _temporary_repo(tmp_path)
    registry_path = tmp_path / "sources.yaml"
    raw = _registry_yaml(repo, revision, remote).replace(
        "schema_version: 1",
        "schema_version: 1\nunknown: true",
        1,
    )
    registry_path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_source_registry(registry_path)


def test_registry_governance_keeps_scripts_disabled_and_requires_explicit_prompt_policy(
    tmp_path: Path,
) -> None:
    repo, revision, remote = _temporary_repo(tmp_path)
    source = _source(repo, revision, remote)
    with pytest.raises(ValidationError, match="Input should be False"):
        source.ingestion.model_copy(
            update={"execute_repository_scripts": True}
        ).model_validate(
            {
                **source.ingestion.model_dump(),
                "execute_repository_scripts": True,
            }
        )
    governance = GovernanceProfile(
        authority="official_documentation",
        curation_required=True,
        raw_prompt_use_allowed=True,
    )
    assert governance.raw_prompt_use_allowed is True


def test_environment_override_must_be_absolute(tmp_path: Path) -> None:
    repo, revision, remote = _temporary_repo(tmp_path)
    source = _source(repo, revision, remote)
    with pytest.raises(ValueError, match="absolute path"):
        resolve_source_path(source, {source.env_override: "relative/source"})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"branch": "master"}, "branch does not match"),
        ({"revision": "0" * 40}, "HEAD does not match"),
        ({"remote": "https://example.invalid/other.git"}, "remote does not match"),
    ],
)
def test_checkout_verification_rejects_registry_drift(
    tmp_path: Path,
    change: dict[str, object],
    message: str,
) -> None:
    repo, revision, remote = _temporary_repo(tmp_path)
    source = _source(repo, revision, remote).model_copy(update=change)
    with pytest.raises(ValueError, match=message):
        verify_source_checkout(source, environment={})


def test_ingestion_glob_semantics_are_frozen() -> None:
    profile = IngestionProfile(
        include=("**/*.md", "skills/review/**"),
        exclude=("**/generated/**", "skills/review/private/**"),
        execute_repository_scripts=False,
        index_as_normative_knowledge=True,
    )
    assert ingestion_path_allowed("README.md", profile)
    assert ingestion_path_allowed("docs/guide.md", profile)
    assert ingestion_path_allowed("skills/review/SKILL.md", profile)
    assert not ingestion_path_allowed("docs/generated/output.md", profile)
    assert not ingestion_path_allowed("skills/review/private/secret.md", profile)
    assert not ingestion_path_allowed("docs/guide.rst", profile)
