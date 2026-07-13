from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.knowledge.adapters import (
    ArkuiSpecAdapter,
    GitObjectReader,
    InterfaceSdkAdapter,
    SourceObject,
)
from arkts_code_reviewer.knowledge.registry import (
    CheckoutProfile,
    GovernanceProfile,
    IngestionProfile,
    SourceRecord,
    VerifiedSource,
)
from arkts_code_reviewer.knowledge.seed import KnowledgeSeed, load_knowledge_seed

ROOT = Path(__file__).resolve().parents[1]


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _verified_source(
    tmp_path: Path,
    *,
    source_id: str,
    relative_path: str,
    content: str,
) -> tuple[VerifiedSource, str]:
    repo = tmp_path / source_id
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repo, "config", "user.email", "knowledge@example.invalid")
    _git(repo, "config", "user.name", "Knowledge Test")
    remote = f"https://example.invalid/{source_id}.git"
    _git(repo, "remote", "add", "origin", remote)
    file_path = repo / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    _git(repo, "add", relative_path)
    _git(repo, "commit", "-m", "fixture")
    revision = _git(repo, "rev-parse", "HEAD")
    source = SourceRecord(
        id=source_id,
        group="knowledge_source",
        kind="official_documentation",
        remote=remote,
        local_path=repo,
        env_override=f"{source_id.upper().replace('-', '_')}_PATH",
        branch="main",
        revision=revision,
        shallow_clone=True,
        checkout=CheckoutProfile(mode="full"),
        use_for=("retrieval_knowledge",),
        ingestion=IngestionProfile(
            include=("**/*.d.ts", "**/*.md"),
            execute_repository_scripts=False,
            index_as_normative_knowledge=True,
        ),
        governance=GovernanceProfile(
            authority="official_documentation",
            curation_required=True,
            raw_prompt_use_allowed=False,
        ),
    )
    return (
        VerifiedSource(
            source=source,
            resolved_local_path=repo.resolve(),
            git_toplevel=repo.resolve(),
            remote=remote,
            branch="main",
            head_revision=revision,
        ),
        relative_path,
    )


def test_seed_v1_freezes_exact_24_document_batch() -> None:
    seed = load_knowledge_seed(ROOT / "config/knowledge_seed_v1.yaml")
    assert len(seed.documents) == 24
    assert seed.source_ids == ("arkui-specs", "interface-sdk-js", "openharmony-docs")
    assert seed.fingerprint.startswith("knowledge-seed:sha256:")
    assert len(seed.fingerprint) == len("knowledge-seed:sha256:") + 64


def test_seed_contract_rejects_missing_document() -> None:
    seed = load_knowledge_seed(ROOT / "config/knowledge_seed_v1.yaml")
    payload = seed.model_dump()
    payload["documents"] = tuple(payload["documents"][:-1])
    with pytest.raises(ValidationError, match="exactly 24"):
        KnowledgeSeed.model_validate(payload)


def test_markdown_adapter_reads_pinned_git_object_not_dirty_worktree(tmp_path: Path) -> None:
    committed = "# 规格标题\n\n## AC-1.1 约束\n\n必须清理资源。\n"
    verified, relative_path = _verified_source(
        tmp_path,
        source_id="arkui-specs",
        relative_path="specs/example.md",
        content=committed,
    )
    (verified.resolved_local_path / relative_path).write_text(
        "# 被污染的工作树\n",
        encoding="utf-8",
    )
    source = SourceObject(
        source_id="arkui-specs",
        revision=verified.source.revision,
        relative_path=relative_path,
        authority=verified.source.governance.authority,
        domains=("timer-subscription-lifecycle",),
        media_type="text/markdown",
    )
    document = ArkuiSpecAdapter().load(
        source,
        GitObjectReader({"arkui-specs": verified}),
    )

    assert document.body == committed
    assert document.title == "规格标题"
    assert [(item.level, item.title, item.span.start_line) for item in document.heading_tree] == [
        (1, "规格标题", 1),
        (2, "AC-1.1 约束", 3),
    ]
    assert document.diagnostics == ()


def test_interface_adapter_preserves_declaration_text_without_fake_headings(
    tmp_path: Path,
) -> None:
    committed = "declare namespace taskpool {\n  class Task {}\n}\n"
    verified, relative_path = _verified_source(
        tmp_path,
        source_id="interface-sdk-js",
        relative_path="api/@ohos.taskpool.d.ts",
        content=committed,
    )
    source = SourceObject(
        source_id="interface-sdk-js",
        revision=verified.source.revision,
        relative_path=relative_path,
        authority=verified.source.governance.authority,
        domains=("async-taskpool-worker",),
        media_type="text/typescript-declaration",
    )
    document = InterfaceSdkAdapter().load(
        source,
        GitObjectReader({"interface-sdk-js": verified}),
    )

    assert document.body == committed
    assert document.title == "@ohos.taskpool.d.ts"
    assert document.heading_tree == ()
    assert document.media_type == "text/typescript-declaration"
    assert document.diagnostics == ()
