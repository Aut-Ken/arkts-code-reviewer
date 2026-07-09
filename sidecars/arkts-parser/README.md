# ArkTS Parser Sidecar

This sidecar wraps `tree-sitter-arkts` and emits compact JSON facts for the
Python code-analysis layer. The Python package remains the owner of the
`CodeFacts` contract and falls back to `LexicalParser` when this sidecar is not
available.

Install dependencies from this directory:

```powershell
npm install
```

Parse source from stdin:

```powershell
Get-Content sample.ets -Raw | node parse_arkts.js --path sample.ets
```
