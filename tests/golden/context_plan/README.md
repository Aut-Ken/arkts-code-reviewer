# ContextPlan RU-5 Golden Set

This suite is the independent, human-reviewed truth for the RU-5 boundary. It
starts from already-built Primary `ReviewUnit` objects plus explicitly injected,
fixed-revision relation candidates. It does not scan a repository, build a call
graph, retrieve knowledge, assemble prompts, invoke an LLM, or change Parser
behavior.

`manifest.json` owns the expected truth. `baselines/current.json` records only
the current planner report. The baseline writer can update only that one current
baseline and can never overwrite the manifest or a history file. Expected
Primary membership, relation strength, question binding, support necessity,
budget outcome, and omission reason were reviewed source-first; planner output
is not accepted as truth.

## Frozen planning rules

- Every directly changed `ReviewUnit` is Primary and survives planning.
- One `ChangeGroup` produces one or more per-question bundles. Every bundle
  retains all group Primaries, while required support is repeated and helpful
  support is deterministically first-fit packed under a per-bundle budget.
- Only strong, exact Primary-to-Primary relations can merge groups. `same_file`
  and `same_host` never merge groups by themselves, even if labelled strong and
  exact. Base/head Primaries sharing a `ChangeAtom` derive one strong, exact
  `change_correspondence` edge from base to head; its evidence is the shared
  atom ID and its provenance is the frozen `change_set_id`.
- Supporting source is selected only from an explicitly supplied candidate. Its
  exact source/span must be proven by a declaration or review-region owner ID,
  and that owner ID must occur in the typed relation evidence.
- Required support is selected before helpful support. Distractors are rejected.
- Primary source is never truncated. A Primary-only overflow blocks dispatch and
  reports `primary_exceeds_budget` plus `context_insufficient`.
- Omitting required support for budget also blocks dispatch and reports
  `context_insufficient`. Omitting helpful support for budget remains
  dispatchable.
- Code budget counts only Primary and selected Supporting source tokens. It uses
  `arkts-code-token-v1`: ArkTS lexical chunks are charged at
  `max(1, ceil(UTF-8 bytes / 4))`, so long comments, long strings, and Unicode
  text cannot be treated as one cheap token.
- All selected and omitted candidates, relation edges, groups, bundles,
  diagnostics, token totals, and output order are deterministic.

## Case matrix

| Case | Contract frozen |
| --- | --- |
| CP001 | fallback Primary, one question, no Supporting |
| CP002 | unrelated field-region and function Primaries |
| CP003 | strong exact Primary relation forms one group |
| CP004 | weak edge does not group; required degraded relation blocks one question |
| CP005 | required Supporting fits and is selected |
| CP006 | required fits while helpful is omitted by budget |
| CP007 | distractor is rejected even with spare budget |
| CP008 | exact token boundary with long string, comment, and Unicode |
| CP009 | Primary alone exceeds budget and blocks dispatch |
| CP010 | oversized required Supporting is omitted and blocks dispatch |
| CP011 | multiple questions split bundles; Primary retains multiple atom IDs |
| CP012 | shared base/head atom derives correspondence, one group, and one bundle |
| CP013 | explicit strong exact `same_file` edge still does not group |
| CP014 | source, Primary, candidate, and edge input permutations are stable |
| CP015 | required degraded relation has exact omission and diagnostics |
| CP016 | required support repeats while helpful support first-fit splits bundles |

Loader tests separately corrupt provenance and graph references. They cover
duplicate JSON keys/cases/aliases, unknown or missing fields, source hash and
revision drift, invalid or 0-based spans, source-text drift, unsorted output,
dangling candidate/edge/owner provenance, invalid public IDs, path traversal,
unsafe mid-expression boundaries, and manifest, source, or baseline symlinks.
Opaque relation evidence is not treated as a registry; only the target boundary
owner membership required by this contract is validated here.

## Commands

```bash
PYTHONPATH=src .venv/bin/python tools/evaluate_context_plan_golden.py \
  --require-perfect

PYTHONPATH=src .venv/bin/python tools/evaluate_context_plan_golden.py \
  --baseline tests/golden/context_plan/baselines/current.json \
  --require-perfect
```
