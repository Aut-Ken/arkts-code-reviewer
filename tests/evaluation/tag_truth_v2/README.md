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

## EVAL-01B Stage-2A implementation boundary

Stage 2A adds only the reusable infrastructure needed *before* independent human labelling:

- a generic, label-free selection contract and verifier;
- a fail-closed, policy-sized structural-selection-capacity lower bound against a frozen selection
  policy and exposure boundary; proxy-stratum capacity remains `not_measured`;
- a candidate-blind, dual-axis review packet built from a verified source revision;
- a path-redacted full-file review view in which the reviewer, rather than the selector, identifies
  the ReviewUnit to judge.

This stage deliberately does **not** create a real selection or review packet. It also does not
create labels, reviewer receipts, consensus, a post-seal candidate runner or an activation
decision, and it does not change any Tag, Dimension or Review Question configuration. A schema or
verifier being implemented is not evidence that the independent process described by that schema
has occurred.

The current independence boundary is fail-closed. Candidate development has already been exposed
to the complete tracked `applications_app_samples` tree at revision
`8255a2987f70317cc3a2a4d46044c6b55f092bb3`, so that revision cannot be renamed or resampled into
blind evidence. At the time of this stage there is no registered, locally available strict
descendant that can satisfy the boundary. Consequently a real blind selection is currently
`not_constructible`, and the target-Tag evidence status remains `not_qualified`.

Removing the current `not_constructible` result requires both:

1. a newly registered `applications_app_samples` revision that is a strict descendant of the
   exposure revision and satisfies the frozen family/path/content exclusions; and
2. an independent dataset custodian, outside the candidate-development process, to prepare and
   seal the unlabeled selection.

The verifier cannot manufacture either condition. It counts only regular Git files with safe
paths, non-empty UTF-8 content, unique content identities and a conservative pairwise-compatible
family set. The resulting value is a verified lower bound, so it may abstain even when a more
complex selector could find a larger set, but it cannot overstate capacity from duplicate content
or nested families. Even when that lower bound reaches the policy total, its most positive result
is `inventory_capacity_only`: it does not prove that every neutral proxy stratum has enough cases.
It reports that separate status as `not_measured` instead of weakening the selection design or
borrowing exposed development samples.

### Proxy strata are not Truth

Selection-time proxy strata, ranks and constructibility counts are coverage controls only. They
may help an independent custodian obtain a deliberately varied challenge set, but they are not
exact-positive, exact-negative, routing-positive or routing-negative labels. They cannot be used
as metric denominators, imported into a receipt, or treated as an automatic substitute for human
judgement. The same rule applies even when a proxy was derived from an import, call, symbol or
other apparently strong code signal.

### Path-redacted full file and reviewer-owned ReviewUnit

Routing-hint applicability depends on file context, so a generic packet cannot show only a
selector-chosen source span. Each review item instead carries an opaque identity and the complete
source-file text with its repository path redacted. The reviewer view also omits repository
revision and the original source content hash, plus source family, proxy stratum, selection rank
and all candidate identity, configuration, output and diagnostic fields.

The reviewer selects the ReviewUnit from that file and records the exact-applicability and
routing-hint judgements as two independent axes under the embedded Tag contract. This prevents a
selector's proposed span or proxy category from silently becoming Truth. This is candidate-blind
and path-redacted, not anonymous: identifiers inside full source text can still suggest or reveal
their origin.

Every Stage-2A selection remains `not_qualified`: both selection and review policies are explicitly
unapproved drafts, the selector record is an unauthenticated attestation, external selection has
not been verified, and human review, near-duplicate qualification and the first candidate run are
still absent.

Stage 2A stops at this packet boundary. Stage 2B below adds receipt sealing and two-reviewer
consensus; post-seal first-candidate execution and quality-gate calculation remain later reviewed
stages.

## EVAL-01B Stage-2B implementation boundary

Stage 2B adds the generic human-review layer immediately after the Stage-2A packet:

- closed, self-hashed `tag-truth-v2-review-receipt-v1` and
  `tag-truth-v2-consensus-v1` artifacts;
- a receipt sealer that binds the self-hashed packet, its recorded `selection_id`, target Tag
  contract and complete review-policy fingerprint to one human reviewer's full-case decisions;
- exactly two distinct human receipts for one consensus, with reviewer identity, round and blinding
  declarations checked fail-closed;
- reviewer-owned ReviewUnit identity plus exact-applicability and routing-hint decisions preserved
  as two independent axes;
- canonical consensus output that retains both original votes, rationales and evidence rather than
  overwriting disagreement.

If the reviewers select different ReviewUnit identities, neither axis is publishable for that case.
Once the Unit identity agrees, each axis is resolved independently: an exact-axis disagreement or
abstention does not discard an agreed routing judgement, and a routing-axis disagreement or
abstention does not discard an agreed exact judgement. Matching taxonomy abstentions become an
explicit `agreed_abstain` blocker; they are not converted to negative labels or removed from the
campaign.

The receipt CLI is:

```bash
PYTHONPATH=src .venv/bin/python tools/seal_tag_truth_v2_review_receipt.py \
  --packet PACKET.json --draft REVIEW_DRAFT.json > RECEIPT.json
```

It exits `0` after writing a valid canonical receipt and `2` for invalid input. It never evaluates
a candidate. Consensus requires exactly two `--receipt` arguments:

```bash
PYTHONPATH=src .venv/bin/python tools/build_tag_truth_v2_consensus.py \
  --packet PACKET.json \
  --receipt REVIEWER_A.json --receipt REVIEWER_B.json > CONSENSUS.json
```

The consensus command exits `0` only for a valid complete consensus with no unresolved or abstained
axis, `1` after writing a valid consensus that contains an unresolved axis or `agreed_abstain`, and
`2` for invalid schema, binding, coverage or reviewer inputs. Exit `1` is an auditable review
outcome, not a malformed artifact.

Stage 2B is still infrastructure, not evidence. No real selection, packet, receipt or consensus has
been created. There is still no eligible strict-descendant source revision; selection and review
policies remain unapproved, external selection and reviewer identity are attestations rather than
authenticated facts, near-duplicate qualification is absent, and no sealed first candidate run has
occurred. Consequently even `consensus_status=complete` would mean only that two valid receipts
resolved every axis. It does **not** mean the dataset, evidence, Tag candidate or activation is
qualified.

The Stage-2B CLIs validate the packet's self-hash and bind receipts to the `selection_id` recorded
inside it. They do not accept the external Stage-2A selection artifact and therefore do not
re-verify selection/checkout provenance; that separate provenance bridge remains a later stage.

This stage does not change the Matcher, Tag/Dimension/Review Question configuration, any combined
configuration fingerprint, Parser, Golden or candidate behavior, and neither CLI imports or runs a
candidate. Stage 2C below supplies only the generic five-artifact provenance/Git-seal verifier.
Later reviewed stages must still provide the consensus-to-`TagTruthV2Suite` publication bridge,
versioned near-duplicate qualification, real externally controlled policy/selection and seal, a
post-seal first-run candidate evaluator, and separate quality-gate and activation decisions.

## EVAL-01B Stage-2C implementation boundary

Stage 2C verifies the immutable relationship among exactly five final review artifacts that are
all present in one exact Git seal tree:

```text
selection + packet + receipt A + receipt B + consensus
-> exact Git seal tree
-> provenance verification report
```

Run the verifier only from a fresh dedicated checkout at the exact seal commit:

```bash
.venv/bin/python -I -B tools/verify_tag_truth_v2_git_seal.py \
  --selection path/in/repo/selection.json \
  --packet path/in/repo/review_packet.json \
  --receipt path/in/repo/reviewer-a.json \
  --receipt path/in/repo/reviewer-b.json \
  --consensus path/in/repo/consensus.json \
  --source-root /path/to/clean-selection-revision-applications_app_samples \
  --seal-revision <full-seal-commit>
```

The CLI requires Python isolated mode (`-I`) before its first standard-library import so the
script directory, current directory and `PYTHONPATH` cannot shadow the bootstrap modules. Before
loading typed project code,
its preflight requires a full lowercase seal revision, `HEAD == seal_revision`, a clean project
worktree, and five unique in-repository regular non-symlink artifacts. Each current artifact byte
stream must equal `git show seal:path`; the candidate commit recorded by Selection must be a strict
ancestor of the seal. Git replacement objects are disabled, and ancestry checks disable the local
commit-graph cache.
Legacy `info/grafts` in either the project or source Git common directory fail closed because they
can rewrite ancestry independently of replacement objects.

The seal proves that all five artifacts coexist at the specified tree; it does not prove that the
seal commit introduced all five paths or that its diff contains no unrelated paths. Preflight also
byte-verifies the frozen typed-verifier closure and rejects bytecode caches, symlinks and import
shadows. Before typed imports, the CLI removes other repository-local Python search paths. The
Python startup, the interpreter, standard library and site-packages remain an explicit host trust
boundary outside this seal.

Typed verification then re-runs the canonical development exclusions, clean pinned source
checkout, exposure path/family/blob boundary, Packet rebuild, both Receipt validations and
Consensus rebuild. It parses only the artifact bytes captured by preflight. The deterministic,
self-hashed `tag-truth-v2-provenance-verification-v1` report records project/source tree identities,
the five Git blob and raw-byte hashes, logical IDs, consensus status and blockers. The report is
created after the seal and is not part of that same commit; committing it requires a later audit
commit.

A complete consensus returns `0`; a valid sealed chain with unresolved/abstained review returns
`1` while preserving the report; malformed schema, unsafe path, Git drift, source drift or binding
failure returns `2`. Neither success code qualifies evidence: the report explicitly remains
`not_qualified` with candidate execution `not_run`.

This verifier proves only the integrity and internal provenance of the bytes and Git relationships
it inspected. It does not authenticate the selector, reviewers, Git host, remote, runtime host or
runner, and it cannot prove candidate first-execution ordering. Formal use needs a fresh,
exclusive, preferably read-only checkout; the later candidate runner must verify again. No real
selection, review chain or seal is created by Stage 2C, and near-duplicate qualification,
`TagTruthV2Suite` publication, policy approval, candidate runtime/environment/harness freeze, P/R,
quality gates and activation remain separate stages.

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
