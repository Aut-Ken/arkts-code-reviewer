# Knowledge API parser sidecar

This sidecar extracts declaration-backed API catalog candidates from pinned
`.d.ts` and `.d.ets` source objects. It is independent from Parser v1 and does
not change application-code parsing behavior.

Install the pinned dependencies:

```bash
cd sidecars/knowledge-api-parser
npm ci
```

The Python entry point is
`arkts_code_reviewer.knowledge.parsing.parse_api_symbols`. A source containing
any tree-sitter `ERROR` or missing node is rejected instead of producing a
partial catalog. Overloads remain separate declarations. Mode-specific
dynamic/static `@since` values remain structured and are never collapsed to a
misleading minimum version.
