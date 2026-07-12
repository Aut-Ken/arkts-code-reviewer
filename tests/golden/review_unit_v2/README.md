# ReviewUnit v2 Golden Set

This suite freezes the RU-4 boundary between a normalized `ChangeSet`, manually
authored `FileAnalysis` ownership fixtures, and ChangeSet-aware ReviewUnits. It
is independent from both the Parser Golden and the legacy ReviewUnit v1 Golden.

The 16 cases are self-contained. Every source records repository, revision,
logical path, SHA-256, deterministic `source_ref_id`, 1-based inclusive spans,
0-based end-exclusive UTF-16 offsets, and a minimal hand-reviewed declaration
or review-region graph. These occurrence fixtures are written directly in the
manifest; they are not generated from Parser output.

The expected section freezes the complete RU-4 result that matters downstream:

- build-v3 `change_set_id`, changed-file and source identities, and source role;
- collision-safe Unit identity, kind, symbol, owner, source/context spans;
- old/new changed lines, exact context text hash, selection reason, quality and
  structured diagnostics;
- assigned ChangeAtom IDs and exact `(atom_id, source_role)` line coverage;
- file, Unit, diagnostic, and coverage output order.

`manifest.json` is the human-reviewed target truth. `baselines/current.json`
records the current implementation report and must never be copied back over
expected truth. Candidate output may be used to calculate opaque hashes and
stable IDs, but every source slice, owner, line assignment, role, diagnostic,
and ordering entry in expected must be reviewed source-first before acceptance.

The source-first review for RV212 and RV214 freezes one fallback per contiguous
changed-line run. Their L1-L3 changes therefore produce one L1-L3 fallback,
instead of three overlapping single-line windows. Sparse changed runs remain
separate. This is an explicit context-quality decision, not a baseline-derived
rewrite of expected truth.

Case matrix:

| Case | Contract |
| --- | --- |
| RV201 | added file and function owner |
| RV202 | replacement with same-path base/head identity |
| RV203 | deletion-only base-source Unit |
| RV204 | pure rename with no fabricated changes |
| RV205 | rename plus replacement |
| RV206 | empty ChangeSet |
| RV207 | binary unsupported diagnostic |
| RV208 | multiple atoms merged under one owner |
| RV209 | one atom covering two methods |
| RV210 | field review region |
| RV211 | import review region |
| RV212 | no-declaration fallback |
| RV213 | unchanged context excluded from changed lines |
| RV214 | degraded parser-quality propagation |
| RV215 | deterministic multi-file order and permutation |
| RV216 | revision-scoped base/head identity at equal coordinates |

Run the gates from the repository root:

```bash
PYTHONPATH=src .venv/bin/python tools/evaluate_review_unit_v2_golden.py \
  --require-perfect
PYTHONPATH=src .venv/bin/python tools/evaluate_review_unit_v2_golden.py \
  --baseline tests/golden/review_unit_v2/baselines/current.json \
  --require-perfect
```
