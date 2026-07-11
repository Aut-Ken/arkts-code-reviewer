# ReviewUnit Golden v1

This suite is the deterministic oracle for ReviewUnit selection and identity. It is independent
from Parser Golden: parser declarations, layer, and warnings are manually frozen as case inputs;
ReviewUnit expected output is reviewed separately and is never generated from Parser output.

## Contract

- Lines are 1-based, end-inclusive, and file-absolute. Columns are unsupported.
- Expected Units compare `unit_id`, `unit_kind`, `unit_symbol`, source/context spans,
  `changed_new_lines`, `selection_reason`, `context_degraded`, diagnostics, and list order.
- The evaluator also checks `full_text == source[context_span]`, compatibility fields,
  repeated execution, and reversed-hunk determinism.
- Manifest and source hashes are rechecked at evaluation time. Expected owners must match a
  frozen declaration occurrence, expected changed lines must come from input hunks, and JSON
  comparisons are type-sensitive (`false` is not accepted as `0`).
- Diagnostics are structured as `{"code": "...", "lines": [...]}` and use the frozen values in
  `manifest.json`.
- `expected` is human-reviewed target truth. Baselines are complete observations of an
  implementation and must never be copied back into expected.
- The CLI only writes a baseline to this suite's `baselines/current.json`; raw reports must stay
  outside the entire Golden root. It cannot overwrite the historical `before-ru1.json`, source
  truth, manifest, or README.

## Cases

| ID | Phase | Scenario |
|---|---|---|
| RU001 | RU-1 | ordinary method hunk with Parser L0 fixture |
| RU002 | RU-1 | short build |
| RU003 | RU-1 | build longer than 160 lines |
| RU004 | RU-1 | same-qualified-name UI occurrences |
| RU005 | RU-1 | multiple hunks for one owner |
| RU006 | RU-2 | one hunk crossing two methods |
| RU007 | RU-1 | full mode with multiple hosts |
| RU008 | RU-1 | no-declaration fallback |
| RU009 | RU-1 | field region |
| RU010 | RU-1 | import region |
| RU011 | RU-2 | diff mode without hunks |
| RU012 | RU-2 | parse-degraded fixture |
| RU013 | RU-2 | L1 ERROR/missing warnings |
| RU014 | RU-2 | out-of-range hunk |
| RU015 | RU-4 | deletion-only explicitly unsupported |
| RU016 | RU-5 | budget explicitly not enforced |

RU-1 does not claim that future-phase cases are fixed. The full report keeps those target
differences visible, while `--require-target RU-1` is the cumulative phase-equivalent perfect
gate and also checks RU-1 invariants on every successfully executed future case.

## Commands

```bash
PYTHONPATH=src .venv/bin/python tools/evaluate_review_unit_golden.py

PYTHONPATH=src .venv/bin/python tools/evaluate_review_unit_golden.py \
  --baseline tests/golden/review_unit/baselines/current.json

PYTHONPATH=src .venv/bin/python tools/evaluate_review_unit_golden.py \
  --require-target RU-1

# This intentionally remains red until all later target phases are implemented.
PYTHONPATH=src .venv/bin/python tools/evaluate_review_unit_golden.py --require-perfect
```
