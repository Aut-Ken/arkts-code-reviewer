# Knowledge Golden v1

This suite is the independent, human-authored truth for Knowledge v1 Clause
boundaries, source spans, API metadata, applicability, examples, and retrieval
annotations. It is separate from Parser, ReviewUnit, Feature Routing, and future
Retrieval Golden suites.

The committed `expected` values are never generated from Adapter, Clause Parser,
annotation, model-review, or database output. `baselines/current.json` records
the current implementation only and may not overwrite the manifest.

K-0 recorded the pre-implementation baseline. K-3 replaces it with the current
formal implementation baseline and must make the `clauses + api_symbols`
structure scope perfect without rewriting reviewed truth merely to match
implementation behavior. The full scope intentionally remains red until K-4
implements curation status, annotations, and their negative assertions.

The K-3 structure scope excludes the governance-only Clause `status`: parsing
may propose `Draft` or detect explicit deprecation, but only curation may
promote a Clause to `Baselined`. The full scope continues to compare status.

The loader fails closed on duplicate JSON keys, unknown or missing fields,
duplicate or unsorted IDs, unregistered Tags/Dimensions, invalid or out-of-range
1-based spans, symlinks, path escape, source hash drift, and baseline drift.
