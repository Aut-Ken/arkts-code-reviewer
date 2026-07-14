# TAG-RDB-01a provisional shadow truth

This package evaluates the proposed `has_relational_database` Tag without changing
the default Feature Routing configuration. It is deliberately independent from the
synthetic Feature Routing Golden.

## What is frozen

- Source checkout: `applications_app_samples` at
  `8255a2987f70317cc3a2a4d46044c6b55f092bb3`.
- Unit of truth: one immutable source file plus one 1-based changed line resolving
  to exactly one ReviewUnit owner.
- Candidate: Draft-only `any_import_use` matching the three canonical identities
  listed in `manifest.json`.
- Existing 24 Tags, Dimensions, Review Questions, default fingerprint, Parser v1,
  existing Golden, Knowledge and E2E stay unchanged.

This v1 package exercises declaration-level ReviewUnits produced by the current
builder. It does not claim coverage of future `field_region` or `import_region`
ReviewUnit v2 identities.

The evaluator reads only the source files allowlisted by the manifest. It verifies
the external Git top level, exact revision, clean worktree, source hash and line
count before parsing. It never checks out, cleans or writes the external repository.

## Truth versus behavior baseline

`manifest.json` contains the proposed semantic labels and the expected mechanism
behavior. `baselines/current.json` is only a snapshot of current executable
behavior. A baseline must never generate or overwrite semantic labels.

All 109 cases currently have `review_status: proposed`, and the suite has
`truth_status: provisional`. Consequently its Precision/Recall are exploratory;
they are not human-adjudicated product quality proof and cannot qualify the Tag for
Active status.

The provisional review cohort contains exactly 40 positive and 60 hard-negative
owner cases:

- 35 direct import-use positives;
- 5 semantically positive wrapper cases that v1 intentionally does not match;
- 60 related-domain hard negatives;
- 4 unresolved-owner controls, 4 isolated `ohosTest` controls, and one VectorStore
  taxonomy quarantine outside the product metrics.

VectorStore is quarantined because official VectorStore code also imports
`@kit.ArkData#relationalStore` and distinguishes the mode with
`StoreConfig.vector: true`. Import-use alone cannot decide whether the proposed Tag
means relational mode or the broader RelationalStore API family.

The five wrapper cases expose the opposite boundary: they are semantically in the
database path but do not contain a direct Unit-owned import use. Until the product
chooses a direct API-family Tag or a broader business-path Tag, the reported P/R is
diagnostic evidence for that decision, not evidence that this Tag ID should ship.

## Run

```bash
PYTHONPATH=src .venv/bin/python tools/evaluate_tag_truth.py \
  --source-root /home/autken/Code/applications_app_samples \
  --strict-baseline tests/tag_truth/relational_database/baselines/current.json \
  --require-contract-perfect \
  --require-review-package-ready
```

`--require-review-package-ready` means only that this provisional package has a
mechanically complete 40/60 review cohort and executable contract stability. It
does not mean the candidate is safe to activate. `tag-truth-v1` is intentionally
provisional-only, so `--require-activation-ready` must fail. Activation requires a
receipt-bearing successor schema, a larger independent holdout with confidence
bounds, at least 300 hard negatives, the Precision/Recall gates, and a resolved
VectorStore taxonomy.
