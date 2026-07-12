from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from arkts_code_reviewer.code_analysis.change_set import (
    CHANGE_SET_SCHEMA_VERSION,
    ChangeAtomInput,
    ChangedFileInput,
    CodeSourceSnapshot,
    DiffPosition,
    normalize_change_set,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import CodeSourceRef
from arkts_code_reviewer.code_analysis.models import ReviewUnitSpan

REPOSITORY = "repo"
BASE = "base-commit"
HEAD = "head-commit"


def _snapshot(path: str, content: str, revision: str) -> CodeSourceSnapshot:
    content_hash = f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
    return CodeSourceSnapshot(
        source_ref=CodeSourceRef.create(
            repository=REPOSITORY,
            revision=revision,
            path=path,
            content_hash=content_hash,
        ),
        content=content,
    )


def _replacement(
    old_start: int,
    old_end: int,
    new_start: int,
    new_end: int,
    *,
    deleted: tuple[int, ...],
    added: tuple[int, ...],
) -> ChangeAtomInput:
    return ChangeAtomInput(
        kind="replacement",
        old_span=ReviewUnitSpan(old_start, old_end),
        new_span=ReviewUnitSpan(new_start, new_end),
        added_new_lines=added,
        deleted_old_lines=deleted,
    )


def test_added_file_has_stable_source_and_utf16_exact_range() -> None:
    content = "const emoji = '😀';\nnext();\n"
    head = _snapshot("src/New.ets", content, HEAD)
    result = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="added",
                old_path=None,
                new_path="src/New.ets",
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(1, 2),
                        added_new_lines=(1, 2),
                        diff_positions=(
                            DiffPosition("head", 1, 1),
                            DiffPosition("head", 2, 2),
                        ),
                    ),
                ),
            ),
        ),
    )

    assert result.schema_version == CHANGE_SET_SCHEMA_VERSION
    assert result.source_refs == (head.source_ref,)
    assert result.files[0].status == "added"
    atom = result.atoms[0]
    assert atom.old_span is None
    assert atom.new_span is not None
    assert atom.new_span.start_line == 1
    assert atom.new_span.end_line == 2
    assert atom.new_span.start_offset_utf16 == 0
    assert atom.new_span.end_offset_utf16 == len(content.encode("utf-16-le")) // 2
    assert atom.added_new_lines == (1, 2)
    assert result.diagnostics == ()


def test_deletion_only_retains_only_base_source_and_lines() -> None:
    base = _snapshot("src/Old.ets", "one\ntwo\n", BASE)
    result = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="deleted",
                old_path="src/Old.ets",
                new_path=None,
                old_snapshot=base,
                atoms=(
                    ChangeAtomInput(
                        kind="deletion",
                        old_span=ReviewUnitSpan(1, 2),
                        new_span=None,
                        deleted_old_lines=(1, 2),
                    ),
                ),
            ),
        ),
    )

    changed_file = result.files[0]
    atom = result.atoms[0]
    assert changed_file.old_source_ref_id == base.source_ref.source_ref_id
    assert changed_file.new_source_ref_id is None
    assert atom.old_source_ref_id == base.source_ref.source_ref_id
    assert atom.new_source_ref_id is None
    assert atom.deleted_old_lines == (1, 2)
    assert atom.added_new_lines == ()


def test_replacement_distinguishes_changed_lines_from_span_context() -> None:
    base = _snapshot("src/A.ets", "keep\nold\nkeep\n", BASE)
    head = _snapshot("src/A.ets", "keep\nnew\nkeep\n", HEAD)
    result = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/A.ets",
                new_path="src/A.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    _replacement(
                        1,
                        3,
                        1,
                        3,
                        deleted=(2,),
                        added=(2,),
                    ),
                ),
            ),
        ),
    )

    assert result.atoms[0].deleted_old_lines == (2,)
    assert result.atoms[0].added_new_lines == (2,)
    assert 1 not in result.atoms[0].added_new_lines
    assert 3 not in result.atoms[0].added_new_lines


def test_pure_rename_has_two_sources_and_no_atoms() -> None:
    content = "struct A {}\n"
    base = _snapshot("src/A.ets", content, BASE)
    head = _snapshot("src/Renamed.ets", content, HEAD)
    result = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="renamed",
                old_path="src/A.ets",
                new_path="src/Renamed.ets",
                old_snapshot=base,
                new_snapshot=head,
            ),
        ),
    )

    assert len(result.source_refs) == 2
    assert result.files[0].status == "renamed"
    assert result.files[0].atom_ids == ()
    assert result.atoms == ()


def test_rename_with_edit_has_replacement_provenance() -> None:
    base = _snapshot("src/A.ets", "old\n", BASE)
    head = _snapshot("src/B.ets", "new\n", HEAD)
    result = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="renamed",
                old_path="src/A.ets",
                new_path="src/B.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    _replacement(1, 1, 1, 1, deleted=(1,), added=(1,)),
                ),
            ),
        ),
    )

    assert result.files[0].atom_ids == (result.atoms[0].atom_id,)
    assert result.atoms[0].old_source_ref_id == base.source_ref.source_ref_id
    assert result.atoms[0].new_source_ref_id == head.source_ref.source_ref_id


def test_modified_file_supports_addition_deletion_and_multiple_hunks() -> None:
    base = _snapshot("src/A.ets", "one\nremove\nthree\n", BASE)
    head = _snapshot("src/A.ets", "one\nthree\nadd\n", HEAD)
    deletion = ChangeAtomInput(
        kind="deletion",
        old_span=ReviewUnitSpan(2, 2),
        new_span=None,
        deleted_old_lines=(2,),
    )
    addition = ChangeAtomInput(
        kind="addition",
        old_span=None,
        new_span=ReviewUnitSpan(3, 3),
        added_new_lines=(3,),
    )

    first = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/A.ets",
                new_path="src/A.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(addition, deletion),
            ),
        ),
    )
    second = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="src/A.ets",
                new_path="src/A.ets",
                old_snapshot=base,
                new_snapshot=head,
                atoms=(deletion, addition),
            ),
        ),
    )

    assert first == second
    assert [atom.kind for atom in first.atoms] == ["addition", "deletion"]
    assert first.files[0].atom_ids == tuple(atom.atom_id for atom in first.atoms)


def test_file_input_order_does_not_change_ids_or_serialization() -> None:
    added_head = _snapshot("src/New.ets", "new\n", HEAD)
    deleted_base = _snapshot("src/Old.ets", "old\n", BASE)
    added = ChangedFileInput(
        status="added",
        old_path=None,
        new_path="src/New.ets",
        new_snapshot=added_head,
        atoms=(
            ChangeAtomInput(
                kind="addition",
                old_span=None,
                new_span=ReviewUnitSpan(1, 1),
                added_new_lines=(1,),
            ),
        ),
    )
    deleted = ChangedFileInput(
        status="deleted",
        old_path="src/Old.ets",
        new_path=None,
        old_snapshot=deleted_base,
        atoms=(
            ChangeAtomInput(
                kind="deletion",
                old_span=ReviewUnitSpan(1, 1),
                new_span=None,
                deleted_old_lines=(1,),
            ),
        ),
    )

    first = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(added, deleted),
    )
    second = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(deleted, added),
    )

    assert first.change_set_id == second.change_set_id
    assert first.to_dict() == second.to_dict()


def test_empty_change_set_and_empty_added_file_are_legal() -> None:
    empty = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(),
    )
    empty_head = _snapshot("src/Empty.ets", "", HEAD)
    empty_added = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="added",
                old_path=None,
                new_path="src/Empty.ets",
                new_snapshot=empty_head,
            ),
        ),
    )

    assert empty.files == ()
    assert empty.atoms == ()
    assert empty.source_refs == ()
    assert empty_added.files[0].atom_ids == ()
    assert empty_added.source_refs == (empty_head.source_ref,)


def test_binary_file_is_structured_diagnostic_without_fake_source_or_atom() -> None:
    result = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path="assets/a.bin",
                new_path="assets/a.bin",
                is_binary=True,
            ),
        ),
    )

    assert result.source_refs == ()
    assert result.atoms == ()
    assert result.files[0].is_binary is True
    assert result.diagnostics[0].code == "binary_source_unavailable"
    assert result.diagnostics[0].changed_file_id == result.files[0].changed_file_id


@pytest.mark.parametrize(
    ("file_input", "message"),
    [
        (
            lambda: ChangedFileInput(
                status="added",
                old_path=None,
                new_path="src/A.ets",
                new_snapshot=_snapshot("src/A.ets", "a\nb\n", HEAD),
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(1, 2),
                        added_new_lines=(1,),
                    ),
                ),
            ),
            "cover every head source line",
        ),
        (
            lambda: ChangedFileInput(
                status="modified",
                old_path="src/A.ets",
                new_path="src/A.ets",
                old_snapshot=_snapshot("src/A.ets", "old\n", BASE),
                new_snapshot=_snapshot("src/A.ets", "new\n", HEAD),
                atoms=(
                    _replacement(1, 1, 1, 1, deleted=(1,), added=(2,)),
                ),
            ),
            "added lines must be inside new_span",
        ),
        (
            lambda: ChangedFileInput(
                status="modified",
                old_path="src/A.ets",
                new_path="src/A.ets",
                old_snapshot=_snapshot("src/A.ets", "same\n", BASE),
                new_snapshot=_snapshot("src/A.ets", "same\n", HEAD),
            ),
            "unchanged source is valid only for a pure rename",
        ),
    ],
)
def test_invalid_change_inputs_fail_closed(
    file_input: object,
    message: str,
) -> None:
    assert callable(file_input)
    with pytest.raises(ValueError, match=message):
        normalize_change_set(
            repository=REPOSITORY,
            base_revision=BASE,
            head_revision=HEAD,
            files=(file_input(),),
        )


def test_duplicate_changed_line_across_atoms_fails_closed() -> None:
    base = _snapshot("src/A.ets", "old\n", BASE)
    head = _snapshot("src/A.ets", "new\nplus\n", HEAD)
    with pytest.raises(ValueError, match="exactly one ChangeAtom"):
        normalize_change_set(
            repository=REPOSITORY,
            base_revision=BASE,
            head_revision=HEAD,
            files=(
                ChangedFileInput(
                    status="modified",
                    old_path="src/A.ets",
                    new_path="src/A.ets",
                    old_snapshot=base,
                    new_snapshot=head,
                    atoms=(
                        _replacement(1, 1, 1, 1, deleted=(1,), added=(1,)),
                        ChangeAtomInput(
                            kind="addition",
                            old_span=None,
                            new_span=ReviewUnitSpan(1, 2),
                            added_new_lines=(1, 2),
                        ),
                    ),
                ),
            ),
        )


def test_source_hash_revision_and_path_provenance_fail_closed() -> None:
    good = _snapshot("src/A.ets", "value\n", HEAD)
    with pytest.raises(ValueError, match="content hash mismatch"):
        CodeSourceSnapshot(good.source_ref, "tampered\n")

    wrong_revision = _snapshot("src/A.ets", "value\n", BASE)
    with pytest.raises(ValueError, match="head snapshot revision"):
        normalize_change_set(
            repository=REPOSITORY,
            base_revision=BASE,
            head_revision=HEAD,
            files=(
                ChangedFileInput(
                    status="added",
                    old_path=None,
                    new_path="src/A.ets",
                    new_snapshot=wrong_revision,
                    atoms=(
                        ChangeAtomInput(
                            kind="addition",
                            old_span=None,
                            new_span=ReviewUnitSpan(1, 1),
                            added_new_lines=(1,),
                        ),
                    ),
                ),
            ),
        )


def test_reproducible_ids_reject_manual_tampering() -> None:
    head = _snapshot("src/A.ets", "value\n", HEAD)
    result = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="added",
                old_path=None,
                new_path="src/A.ets",
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(1, 1),
                        added_new_lines=(1,),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="atom_id"):
        replace(result.atoms[0], atom_id="change-atom:sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="change_set_id"):
        replace(result, change_set_id="change-set:sha256:" + "0" * 64)


def test_unreferenced_extra_source_ref_fails_closed() -> None:
    head = _snapshot("src/A.ets", "value\n", HEAD)
    extra = _snapshot("src/Unused.ets", "unused\n", HEAD)
    result = normalize_change_set(
        repository=REPOSITORY,
        base_revision=BASE,
        head_revision=HEAD,
        files=(
            ChangedFileInput(
                status="added",
                old_path=None,
                new_path="src/A.ets",
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="addition",
                        old_span=None,
                        new_span=ReviewUnitSpan(1, 1),
                        added_new_lines=(1,),
                    ),
                ),
            ),
        ),
    )
    source_refs = tuple(
        sorted(
            (*result.source_refs, extra.source_ref),
            key=lambda source: source.source_ref_id,
        )
    )

    with pytest.raises(ValueError, match="every CodeSourceRef"):
        replace(result, source_refs=source_refs)


def test_diff_position_cannot_bind_both_sides_of_one_atom() -> None:
    with pytest.raises(ValueError, match="sorted and unique"):
        ChangeAtomInput(
            kind="replacement",
            old_span=ReviewUnitSpan(1, 1),
            new_span=ReviewUnitSpan(1, 1),
            added_new_lines=(1,),
            deleted_old_lines=(1,),
            diff_positions=(
                DiffPosition("base", 1, 1),
                DiffPosition("head", 1, 1),
            ),
        )
