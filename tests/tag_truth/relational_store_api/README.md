# TAG-RDB-01b provisional shadow truth

This package evaluates the proposed `has_relational_store_api` Tag without changing
the default Feature Routing configuration. The Tag means that the current
ReviewUnit directly uses a RelationalStore API, type, or configuration binding. It
does not mean that an indirect wrapper participates in a database business flow.
The package is deliberately independent from the synthetic Feature Routing Golden.

## What is frozen

- Source checkout: `applications_app_samples` at
  `8255a2987f70317cc3a2a4d46044c6b55f092bb3`.
- Unit of truth: one immutable source file plus one 1-based changed line resolving
  to exactly one ReviewUnit owner.
- Candidate: frozen `tag-config-v2`, Draft-only `any_import_use` matching the three
  canonical identities listed in `manifest.json`. A v3 config is rejected even if it
  declares no v3-only operator. VectorStore is intentionally inside this API-family
  semantic.
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

All 114 cases currently have `review_status: proposed`, and the suite has
`truth_status: provisional`. Consequently its Precision/Recall are exploratory;
they are not human-adjudicated product quality proof and cannot qualify the Tag for
Active status.

The provisional review cohort contains exactly 40 positive and 65 hard-negative
owner cases:

- 40 direct API/type/config positives from `src/main` owners;
- 60 related-domain hard negatives;
- 5 indirect-wrapper hard negatives;
- 4 unresolved-owner controls, 4 isolated `ohosTest` controls, and one VectorStore
  API-family positive outside the product metrics.

VectorStore directly imports and calls `@kit.ArkData#relationalStore`, so it is a
positive boundary case for this API-family Tag. It remains diagnostic-only because
`DocsSample` sources cannot enter product metrics. A future traditional relational
database-mode Tag would still need `StoreConfig.vector` dataflow and separate truth.

The five wrapper cases call local DAO, manager, or helper abstractions but contain no
direct Unit-owned RelationalStore use. They are hard negatives for this Tag and are
retained as seeds for a future relation-aware business-flow truth package.

The five new direct positives were added only to calibration. The existing
acceptance holdout was not expanded after observing current behavior, so this
semantic migration does not present post-hoc selected cases as independent holdout
evidence.

## Run

```bash
PYTHONPATH=src .venv/bin/python tools/evaluate_tag_truth.py \
  --source-root /home/autken/Code/applications_app_samples \
  --strict-baseline tests/tag_truth/relational_store_api/baselines/current.json \
  --require-contract-perfect \
  --require-review-package-ready
```

`--require-review-package-ready` means only that this provisional package has a
mechanically complete 40/65 review cohort and executable contract stability. It
does not mean the candidate is safe to activate. `tag-truth-v1` is intentionally
provisional-only, so `--require-activation-ready` must fail. Activation requires a
receipt-bearing successor schema, a larger independent holdout with confidence
bounds, at least 300 hard negatives, and the Precision/Recall gates.
