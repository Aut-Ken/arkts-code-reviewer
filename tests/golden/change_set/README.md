# ChangeSet v1 Golden Set

This suite is the independent, human-reviewed truth for structured ChangeSet
normalization. It does not parse raw Git output and it does not exercise or modify
Parser v1/v2. Each case supplies already-structured file and atom inputs plus local,
self-contained base/head source snapshots.

`manifest.json` owns the expected truth. `baselines/current.json` only records the
current implementation report. The baseline writer cannot target the manifest or
any history file, and a baseline must never be copied back into `expected`.

## Frozen output

Every case compares the complete public `ChangeSet.to_dict()` value, including:

- `change_set_id`, every `source_ref_id`, `changed_file_id`, `atom_id`, and
  `diagnostic_id`;
- repository, base/head revision, path, role, and content-hash provenance;
- file status and stable file/atom/source/diagnostic output order;
- addition, deletion, and replacement semantics;
- 1-based inclusive line spans and 0-based end-exclusive UTF-16 offsets;
- changed-line sets and optional side-aware diff positions;
- binary-source diagnostics without fabricated source or atoms.

The evaluator also repeats every case after reversing file and per-file atom input
order. A case fails if that changes any ID or serialized field.

## Case matrix

| Case | Contract frozen |
| --- | --- |
| CS001 | added text file and full head coverage |
| CS002 | deletion-only file and base provenance |
| CS003 | replacement span versus changed line |
| CS004 | pure rename with no fabricated atom |
| CS005 | rename with edited base/head content |
| CS006 | multiple independent hunks and atom order |
| CS007 | multi-file input-order determinism |
| CS008 | empty ChangeSet |
| CS009 | empty added text file |
| CS010 | binary structured diagnostic |
| CS011 | astral Unicode and UTF-16 offsets |
| CS012 | side-aware optional diff positions |
| CS013 | multiline replacement and changed lines |
| CS014 | diff normalizer version in stable identity |

The source text and declared spans were reviewed first. Candidate normalizer output
was used only to calculate the long public hashes after the source/role/line
semantics were fixed; it was then checked case by case before being frozen. The
loader independently revalidates hashes, provenance, exact ranges, graph links,
stable order, and every public ID. Candidate output is never automatically accepted
as truth during evaluation.

## Fail-closed loader

The loader rejects duplicate JSON keys, duplicate cases or source aliases, unknown
or missing fields, unnormalized paths, source hash/provenance drift, invalid or
out-of-range spans, 0-based lines, duplicate/unsorted changed lines, invalid diff
positions, unstable expected ordering, invalid/dangling IDs, and manifest/source/
baseline symlinks.

## Commands

```bash
PYTHONPATH=src .venv/bin/python tools/evaluate_change_set_golden.py \
  --require-perfect

PYTHONPATH=src .venv/bin/python tools/evaluate_change_set_golden.py \
  --baseline tests/golden/change_set/baselines/current.json \
  --require-perfect
```
