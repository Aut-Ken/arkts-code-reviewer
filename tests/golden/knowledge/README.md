# Knowledge Golden v1

This suite is the independent, human-authored truth for Knowledge v1 Clause
boundaries, source spans, API metadata, applicability, examples, and retrieval
annotations. It is separate from Parser, ReviewUnit, Feature Routing, and future
Retrieval Golden suites.

The committed `expected` values are never generated from Adapter, Clause Parser,
annotation, model-review, or database output. `baselines/current.json` records
the current implementation only and may not overwrite the manifest.

K-0 intentionally records the pre-implementation baseline. K-3 must make the
formal Clause/API evaluator perfect without rewriting reviewed truth merely to
match implementation behavior.

The loader fails closed on duplicate JSON keys, unknown or missing fields,
duplicate or unsorted IDs, unregistered Tags/Dimensions, invalid or out-of-range
1-based spans, symlinks, path escape, source hash drift, and baseline drift.
