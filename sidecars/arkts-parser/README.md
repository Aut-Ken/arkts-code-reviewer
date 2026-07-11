# ArkTS Parser Sidecar

This sidecar wraps `tree-sitter-arkts` and emits compact JSON facts for the
Python code-analysis layer. The Python package remains the owner of the
`CodeFacts` contract and falls back to `LexicalParser` when this sidecar is not
available.

Install the exact lockfile dependencies from this directory. The committed
`.node-version` records the runtime used for the checked-in L1 baseline:

```powershell
npm ci
```

Parse source from stdin:

```powershell
Get-Content sample.ets -Raw | node parse_arkts.js --path sample.ets
```

Run the strict merged-L1 Golden baseline from the repository root:

```bash
PYTHONPATH=src python tools/evaluate_parser_golden.py \
  --parser arkts-tree-sitter \
  --baseline tests/golden/parser/baselines/arkts-tree-sitter-merged.json \
  --require-layer L1
```

This is the strict L1 entry point: `--baseline` compares every case and false
positive/negative identity, while `--require-layer L1` fails when dependencies
are missing or any case falls back to `parse_degraded`. The current
`arkts-tree-sitter` parser is an L0+L1 merged result; it must not be reported as
a raw-L1 score.
