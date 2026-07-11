# Parser Golden Set

This directory contains the deterministic accuracy oracle for Parser facts. It is deliberately
separate from `tests/fixtures/arkui_ace_engine_samples.json`, whose 63 complete external files are
the robustness and performance corpus.

## Roles

```text
Parser Golden Set
  exact human-reviewed facts, declaration boundaries, and explicit negative facts

arkui-ace-engine-r63
  missing/crash/degraded/determinism/performance and broad real-source distribution

third_party/tree-sitter-arkts/test/corpus
  grammar source -> AST tests; useful inputs, but not a CodeFacts oracle
```

The `expected` block is source truth. Files under `baselines/` record current Parser behavior and
must never be copied into `expected` automatically. A baseline change therefore shows whether a
Parser edit added or removed false positives and false negatives.

## Scored contract

`parser-golden-v1` scores the current `CodeFacts` contract subset:

- import bindings;
- file-level components, APIs, decorators, attributes, symbols, and syntax sets;
- the seven currently supported declaration kinds (`struct`, `class`, `function`, `method`,
  `build_method`, `builder`, and `ui_block`), including qualified name, parent, and inclusive
  1-based line span;
- `must_not_emit` facts that represent known precision traps.

Each case declares `scored_fields`. An unscored field must be explicit JSON `null`; an empty array
always means the reviewed truth is empty. This prevents incomplete annotations from looking like
exact source truth when a canonical policy has not been frozen.

Fact occurrence spans, owners, structured Parser diagnostics, and a raw-L1 result are explicitly
listed as unsupported. They will become scored fields after the `FileAnalysis/FactOccurrence`
contract exists.

Grammar-derived cases verify only the facts represented by this contract. For example, the lazy
import case currently verifies its binding and declarations, not a separate `lazy` flag. The suite
does not yet contain a genuine syntax-recovery case.

## Run

```bash
PYTHONPATH=src python tools/evaluate_parser_golden.py --parser lexical --require-layer L0
```

Use `--require-perfect` only when checking whether all reviewed mismatches have been fixed. The
committed L0 baseline intentionally contains current false positives and false negatives.

Run the checked-in L0 behavior baseline strictly with:

```bash
PYTHONPATH=src python tools/evaluate_parser_golden.py \
  --parser lexical \
  --baseline tests/golden/parser/baselines/lexical.json \
  --require-layer L0
```

Install the sidecar from its lockfile, then run the merged-L1 baseline strictly:

```bash
(cd sidecars/arkts-parser && npm ci)
PYTHONPATH=src python tools/evaluate_parser_golden.py \
  --parser arkts-tree-sitter \
  --baseline tests/golden/parser/baselines/arkts-tree-sitter-merged.json \
  --require-layer L1
```

The strict command fails if dependencies or the pinned Node/npm environment are missing, if any
case degrades, or if any per-case false-positive/false-negative identity changes. Ordinary pytest
keeps the L1 test optional so a Python-only checkout can still run L0 tests. Raw L1 must not be
claimed until the sidecar snapshot has a dedicated public evaluation path.
