# Generic Tag Truth v2

This package defines the generic evidence boundary for evaluating one Feature Tag on real
ReviewUnits. It does not change `config/tags.yaml`, activate a candidate, migrate an existing
Truth package, or turn development data into independent evidence.

## Question answered

One campaign answers only:

> For this immutable ReviewUnit, does the versioned target-Tag contract apply exactly, does the
> containing file provide only a conservative routing hint, or is the available context
> insufficient for a decision?

Tag Truth is not a defect label. It does not judge Dimension or Review Question taxonomy,
Knowledge applicability, Retrieval relevance, Finding severity, or final review correctness.

The unit of evidence is:

```text
repository revision
+ source path/blob
+ ReviewUnit kind/qualified symbol/inclusive span
+ target_tag_id
+ exact label
+ routing-hint label
```

An absent Tag judgement is never interpreted as a negative label.

## Stage-1 implementation boundary

Stage 1 provides:

- closed, versioned `tag-contract-snapshot-v1` and `tag-truth-v2` models;
- duplicate-key-safe loading and canonical fingerprints;
- explicit dataset roles, Truth status, review-chain references and frozen quality gates;
- source family, content, normalized ReviewUnit body and template-cluster identities;
- a read-only coverage report over current committed evidence packages.

Stage 1 does **not** provide a generic selector, blinded packet builder, receipt/consensus CLI,
candidate runner, production-prevalence sampler, or activation command. Those require separate
reviewed stages. Lifecycle holdout v1 remains the only implemented post-seal candidate runner and
is not modified by this package.

## Dataset roles

The contract reserves these real-code roles:

| Role | May guide implementation | May qualify activation | Natural-prevalence claim |
|---|---|---|---|
| `development_regression` | yes | no | no |
| `independent_blind_challenge` | no, until the sealed first run | evidence input only | no |
| `production_prevalence` | no, while sealed | evidence input only | only with a frozen probability design |

The Stage-1 loader accepts only `development_regression`. It rejects the two evidence-bearing
roles fail-closed until later stages provide an external selector, sealed review artifacts and a
versioned verifier; declaring a role in the manifest is not evidence that its process occurred.

Synthetic Feature Routing Golden remains a separate contract suite. The coverage report may
reference its counts, but synthetic expected values are never imported into Tag Truth v2.

## Tag contract

Every suite embeds a `TagContractSnapshot`. It freezes:

- the target Tag identity and contract version;
- independent exact-applicability and routing-hint axes;
- a positive, negative and abstain rule for each axis;
- the contract fingerprint used by review artifacts.

These six semantic rules are required structured fields, not one opaque policy paragraph. The
contract describes semantics, not matcher implementation. Trigger operators, candidate
predictions and behavior baselines are forbidden from Truth labels.

Changing the contract changes its fingerprint. Existing packets, receipts and consensus cannot
be reused across that boundary.

## Source and case identity

Every source binds the registered repository identity, origin, full revision, normalized path,
content SHA-256, line count, source kind, app scope and deterministic path-derived family.

Every case binds one source plus:

- an opaque case ID;
- a 1-based changed line;
- expected ReviewUnit kind, qualified symbol and 1-based inclusive span;
- exact and routing-hint labels, each with its own metric eligibility and abstain reason;
- source evidence lines and rationale;
- ReviewUnit body SHA-256, normalized-body SHA-256 and template-cluster ID;
- stratum and exact critical-negative status.

The two axes are measured independently: one axis may abstain without silently removing a resolved
judgement on the other axis. A critical negative is always an exact-axis, metric-eligible negative,
and the frozen critical-negative strata must match the strata actually marked critical.

Family/path/blob checks catch direct reuse. Normalized-body and template-cluster identities make a
future versioned near-duplicate decision auditable, but Stage 1 does not define a similarity
algorithm or threshold. A suite whose near-duplicate check is not explicitly qualified must remain
`not_qualified`; storing these identifiers alone does not prove that semantically rewritten
examples are independent. Human process and externally controlled selection remain necessary.

## Truth and review status

`proposed` development labels are useful only for regression. Receipt-bearing states must bind a
complete review chain. An independent blind suite additionally requires distinct reviewers,
consensus, one case per family and a frozen exposure boundary. Unresolved taxonomy cases remain
non-metric blockers; they are not deleted to improve a score.

The schema validates artifact shape and internal references. It does not authenticate reviewer
identity, prove that blinding was honored, or prove that a host was trustworthy.

## Quality gates

The suite can freeze point-estimate and uncertainty gates before candidate execution:

- minimum positive, negative and independent-family counts;
- separate minimum exact and routing-hint precision and recall;
- separate minimum exact and routing-hint 95% Wilson lower bounds;
- separate maximum exact and routing-hint false positives and false negatives, plus critical exact
  false positives;
- maximum file-hint-to-exact promotions;
- maximum Parser, ReviewUnit and UnitFactScope risks;
- maximum unresolved taxonomy decisions.

Stage 1 validates the gate snapshot but does not run a candidate or calculate a release decision.
Development readiness and activation evidence are separate decisions. A development suite with
perfect behavior remains `development_only`. Missing evidence is reported as `not_qualified`, not
removed from aggregate denominators.

The minimum blind pilot discussed for a single Tag is 16 consensus positives and 16 consensus
negatives from distinct families. Zero error yields a Wilson lower bound of about `0.806`. A
stronger production target can require 40/40 independent cases (zero-error lower bound about
`0.912`) without rewriting an older frozen campaign.

## Current evidence adapters

The coverage report reads current packages without changing their meaning:

- `tests/golden/feature_routing/manifest.json`: synthetic contract coverage;
- `tests/evaluation/tag_retrieval/manifest.json`: exposed four-Tag development regression;
- `tests/tag_truth/relational_store_api/manifest.json`: provisional Draft-Tag development Truth;
- its strict baseline: diagnostic availability only, never a label source.

It must preserve these facts:

- all 48 Tag Retrieval cases are exposed development data;
- historical `acceptance_holdout` names do not restore independence;
- RelationalStore labels remain proposed/provisional;
- no current Tag has a generic v2 blind or production-prevalence result;
- missing Parser-risk evidence is `not_measured`, not zero.

Run the read-only report from the repository root:

```bash
PYTHONPATH=src .venv/bin/python tools/report_tag_truth_coverage.py
```

The command writes canonical JSON to standard output and does not evaluate a candidate or mutate
any Truth, baseline, configuration or external repository.
