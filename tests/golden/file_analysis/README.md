# FileAnalysis Golden v1

This directory is the independent Parser v2 accuracy contract for occurrence-level
`FileAnalysis`. It does not replace, import, or rewrite the Parser v1 Golden under
`tests/golden/parser/`.

## Truth and baseline are different

- `manifest.json` contains human-reviewed semantic expected truth.
- Expected declarations and regions use readable aliases such as `host`, `build`, and
  `field`; fact owners point to those aliases.
- Expected entries deliberately do not contain long generated IDs. The evaluator
  recomputes and validates every actual ID, uniqueness constraint, reference, and stable
  output order before applying aliases.
- `baselines/current.json` records current implementation behavior. It may detect drift,
  but it must never generate or overwrite `manifest.json` expected truth.
- `--write-current-baseline` is fail-closed and can write only this directory's
  `baselines/current.json`.

All fixtures are self-contained synthetic ArkTS sources. Source SHA-256, logical path,
repository snapshot ID, 1-based inclusive line span, and 0-based end-exclusive UTF-16
offsets are frozen in the manifest. The loader rejects duplicate JSON keys, duplicate
case IDs, unknown or missing fields, source hash drift, invalid UTF-16 boundaries,
unsorted output, dangling owner/parent aliases, and containment violations.

## Complete semantic projection

Every case lists every declaration, review region, and fact occurrence produced by the
supported FileAnalysis contract. Comparison is exact across the complete lists: a new
kind, an extra occurrence, an owner change, or a missing occurrence is a failure. There is
no kind allowlist that can silently hide new output. Adding a producer capability therefore
requires a deliberate human update to expected truth before the Golden can become green.

The manifest also freezes the complete producer `parser_version`, parser quality,
diagnostics, and every `ScopedFacts` file-hint field consumed by routing. Compact rows keep
the full truth reviewable without generated IDs:

```text
declaration = alias, kind, name, qualified_name, start_line, end_line,
              start_utf16, end_utf16, parent_alias, quality
region      = alias, kind, symbol, start_line, end_line,
              start_utf16, end_utf16, owner_alias, quality, provenance
fact        = kind, name, canonical_name, start_line, end_line,
              start_utf16, end_utf16, [owner_kind, owner_alias], quality, provenance
```

These are serialization columns only. The loader converts them to named semantic fields,
validates exact width and types, and rejects dangling aliases or invalid containment.
Aliases are deliberately semantic (`host`, `build`, `first_column`, `second_column`,
`import`, `count`, `work`, and similar), rather than generated list positions.

For every case, the expected table was reviewed source-first: identify the syntax node,
verify its 1-based line span and UTF-16 boundaries, assign the semantic declaration/region
owner, then adjudicate quality and provenance. Parser output may be used as candidate
evidence during this review, but it is not accepted as truth automatically. The current
baseline is generated only after that expected table is frozen and cannot update it.

`FA005` freezes two `Column` and two `Text` occurrences on the same source line. Their
line spans and qualified names collide, while UTF-16 offsets and owner aliases remain
different. This is the explicit occurrence identity collision case.

Across the suite, expected truth exercises all seven declaration kinds and all thirteen
fact kinds in the frozen v1 contract. `FA015` also freezes the honest failure mode for an
L1-parsed fact with no declaration or region owner: the occurrence remains `unresolved`,
its owner is `null`, and the file reports `unresolved_fact_owner` rather than fabricating
an association.

## Case matrix

| Case | Contract |
|---|---|
| `FA001` | SDK API canonicalization and declaration owner |
| `FA002` | nested component occurrences and owner chain |
| `FA003` | host, field, and builder decorator attachment |
| `FA004` | async/Promise/await/try/arrow syntax occurrences |
| `FA005` | same-line, same-name offset-safe identity |
| `FA006` | multiline import region and bindings |
| `FA007` | local parameter shadow suppresses false SDK API |
| `FA008` | field read/write classification and owners |
| `FA009` | field initializer API owned by field region |
| `FA010` | multiline UI modifier continuation owner |
| `FA011` | sibling method decorator attaches only to next method |
| `FA012` | comment/string exclusion and local ERROR/missing recovery |
| `FA013` | non-BMP UTF-16 offsets, import use, resource/string and local call |
| `FA014` | class, field region, method ownership, and exact field read/write |
| `FA015` | top-level imported call with explicit unresolved owner diagnostic |

## Gates

```bash
PYTHONPATH=src .venv/bin/python tools/evaluate_file_analysis_golden.py \
  --require-perfect

PYTHONPATH=src .venv/bin/python tools/evaluate_file_analysis_golden.py \
  --baseline tests/golden/file_analysis/baselines/current.json \
  --require-perfect
```

After any Parser v2 change, run the Parser v1 release gate separately. A perfect
FileAnalysis Golden result cannot excuse Parser v1 expected or baseline drift.
