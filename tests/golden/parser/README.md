# Parser Golden Set

This directory contains the deterministic accuracy oracle for Parser facts. It is deliberately
separate from `tests/fixtures/arkui_ace_engine_samples.json`, whose 63 complete external files are
the robustness and performance corpus.

## Roles

```text
Parser Golden Set
  15 cases with exact human-reviewed facts, declaration boundaries, and explicit negative facts

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

Every case must score imports, components, decorators, attributes, symbols, syntax, and
declarations; APIs remain explicitly optional per case. An unscored field must be JSON `null`; an
empty array always means the reviewed truth is empty. The loader also rejects duplicate JSON keys,
unknown syntax values, coverage shrinkage, and components/symbols that drift from declarations.
The suite as a whole must cover all seven declaration kinds and all five frozen syntax kinds.

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
  --require-layer L1 \
  --require-perfect
```

The strict command fails if dependencies or the pinned Node/npm environment are missing, if any
case degrades, or if any per-case false-positive/false-negative identity changes. Ordinary pytest
keeps the L1 test optional so a Python-only checkout can still run L0 tests. Raw L1 must not be
claimed until the sidecar snapshot has a dedicated public evaluation path.

Verify the four external snapshots against the pinned checkout and run the complete deterministic
Parser v1 gate with:

```bash
PYTHONPATH=src python tools/verify_parser_golden_provenance.py \
  --source-root /home/autken/Code/arkui_ace_engine
PYTHONPATH=src python tools/check_parser_v1.py \
  --source-root /home/autken/Code/arkui_ace_engine
```

## Provisional real-source candidates

The separately stored Grok candidates remain `candidate_unreviewed`; they are not formal Golden
truth and cannot be used with `--baseline`. For parser development, the explicitly allowlisted
23-case default allowlist can be scored provisionally with:

```bash
PYTHONPATH=src python tools/evaluate_parser_candidates.py \
  --source-root /home/autken/Code/arkui_ace_engine \
  --parser arkts-tree-sitter \
  --require-layer L1
```

That command verifies the pinned checkout revision and only the selected source paths, then marks
its report `provisional` and `candidate_unreviewed`.

B007 is excluded because its `must_not` treatment of `ForEach` conflicts with the formal Golden
contract, which records `ForEach` as a component/ui_block. B009 remains unadjudicated and has
large-file boundary disagreements. Both groups may be run for investigation, but neither belongs
in the default accuracy diagnostic.

Candidate evidence has a separate fail-closed audit:

```bash
PYTHONPATH=src python tools/audit_parser_candidate_evidence.py \
  --source-root /home/autken/Code/arkui_ace_engine
```

As of 2026-07-11 this audit intentionally fails with 441 legacy symbol-evidence issues. Candidate
values remain useful diagnostics, but neither the values nor evidence become formal Golden truth
until the evidence is rebuilt and the remaining B010 annotation conflicts are adjudicated.
