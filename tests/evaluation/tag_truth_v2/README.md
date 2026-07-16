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

## EVAL-01B Stage-2D1 shadow-screening boundary

Stage 2D1 adds a deterministic post-seal near-duplicate screen without changing any Stage-2A,
Stage-2B or Stage-2C artifact. Run it only after saving the Stage-2C report, from the same fresh
checkout at the exact seal commit:

```bash
.venv/bin/python -I -B tools/screen_tag_truth_v2_near_duplicates.py \
  --selection path/in/repo/selection.json \
  --packet path/in/repo/review_packet.json \
  --receipt path/in/repo/reviewer-a.json \
  --receipt path/in/repo/reviewer-b.json \
  --consensus path/in/repo/consensus.json \
  --provenance-verification /path/to/stage-2c-report.json \
  --policy tests/evaluation/tag_truth_v2/near_duplicate_shadow_policy_v1.json \
  --source-root /path/to/clean-selection-revision-applications_app_samples \
  --seal-revision <full-seal-commit>
```

Before typed imports, the standard-library preflight repeats the Stage-2C seal checks, captures the
five committed artifacts and the external self-hashed Stage-2C report, and verifies the screening
policy already existed at `candidate_commit` with the same Git blob later present at the seal. It
also requires the screening core and both screening tools to have identical Git blobs at candidate
freeze and seal, then byte-checks the current checkout against the seal. The typed layer rebuilds
the Stage-2C report instead of trusting the supplied JSON. This closes post-freeze tuning through
either policy fields or implementation semantics.

The screen has two axes: the agreed ReviewUnit protects exact-Tag Truth, while the complete selected
file protects routing-hint Truth. Both are compared against all UTF-8 tracked text at the candidate
project commit and exposure revision, every registered development-Truth source at its pinned
revision, and the other files/Units in the blind campaign. NUL-bearing binary blobs are outside the
`all_tracked_utf8_text` scope but remain explicitly counted and fingerprinted. Non-regular,
over-budget, non-UTF-8 and otherwise unevaluable entries become blockers; none is silently treated
as independent code.

The committed policy uses deterministic `lexical-content-v1` and `lexical-shape-v1` token channels,
7/11-token shingles, bidirectional containment, Jaccard and bidirectional
longest-contiguous-run coverage. Long normalized shape-stream equality and shape containment can
only request review; neither can reject a sample on its own. Gray content similarity also requires
at least 16 shared shingles, so a single seven-token fragment cannot gray a large file. The
threshold values are calibration seeds under `snapshot_only_not_approved`, not measured quality
gates.

Before tokenization/comparison, `nfc-character-work-v1` records probe count, selected and unique
reference NFC characters, eligible pair count, and the sum of NFC characters on both sides of every
pair. The shadow limits are 16,000,000 selected characters, 64,000,000 unique reference characters,
2,000,000 pairs, 250,000,000 pair-side characters and 10,000 recorded matches. A preflight limit
abstains before comparing; a runtime match overflow discards every partial match. Both paths report
their planned/attempted work, set every axis to abstain and exit `1`. A case with
`probe_evaluation_status=not_run_resource_limit` uses zero token/shingle counts as an explicit
not-run sentinel, not as a measurement. Incomplete inventories and reference-tokenizer failures
likewise prevent an otherwise unmatched axis from becoming `clear`.

The `screening_id` self-hash binds report identity only. A consumer must call the complete verifier
and rebuild the report from the sealed policy, five artifacts and three pinned inventories before
trusting its semantics; parsing or recomputing the self-hash is insufficient. Consequently every valid
Stage-2D1 report exits `1`, whether its shadow outcome is clear, duplicate or review-required.
Exit `0` is reserved for a separately reviewed future approved policy, and invalid schema, Git,
path, freeze or binding exits `2`.

The shadow policy deliberately caps one blob at 2 MiB. The current project commit contains the
24,144,840-byte generated `third_party/tree-sitter-arkts/src/parser.c`, so a real screen whose
candidate-project reference is the current tree necessarily reports
`candidate_project:oversize_entries` and cannot have a clean shadow outcome. This is a visible
resource abstention, not a qualified result. A later calibrated policy must solve it through a
reviewed streaming/resource design or a new limit; the blind campaign cannot tune it away.

At pre-Stage2D commit `fdac0fcc2a003f4aa1e4e00aac88b871f7ba602a`, a read-only scan found 554
tracked entries, 552 loaded UTF-8 documents, 549 unique texts and 15,161,916 candidate-project path
NFC characters. Applying the ArkTS-like tokenizer to all tracked text flags 54 Markdown, Python,
JavaScript and other documents. A 24-case file+Unit campaign would also exceed 727 million pair-side
NFC characters from the candidate-project reference alone. Thus the current tree has oversize,
tokenizer-media and work-budget abstentions independently; none may be hidden by excluding non-ETS
files after seeing the blind sample.

The CLI removes inherited `GIT_*` routing/configuration variables and disables repository-configured
`core.fsmonitor`. It still trusts the local `git` executable, `PATH`, protected Git configuration and
an exclusive checkout. Critical artifacts, policy and verifier-closure bytes are compared directly;
a clean `git status` alone is not treated as byte-for-byte proof. The external Stage-2C report binds
pre-open and opened device/inode, then reads at most 16 MiB + 1 byte from the same nonblocking regular
file descriptor. Inventory entry limits are checked after `git ls-tree` returns,
and NFC work limits apply after sealed inputs are loaded, so these are comparison-stage guards rather
than an OS-level memory sandbox for arbitrarily large trusted repositories.

This stage does not run Parser, Matcher, FeatureRouter or a candidate. It does not publish a
`TagTruthV2Suite`, clear the Selection's historical not-qualified reasons, calculate Tag P/R, or
make an activation decision. No real five-artifact campaign or independently dual-reviewed
duplicate/independent/ambiguous pair Truth currently exists, so current evidence remains
`not_qualified` and candidate execution remains `not_run`.

## EVAL-01B Stage-2D2a consensus-publication boundary

Stage 2D2a adds a deterministic publication bridge after Stage 2D1:

```text
sealed Selection / Packet / two Receipts / Consensus
+ rebuilt Stage-2C provenance
+ rebuilt Stage-2D1 near-duplicate screening
-> tag-truth-v2-publication-v1
```

It creates a new, independent publication schema instead of relaxing the Stage-1
`tag-truth-v2` loader. The old loader still rejects independent-blind and qualified
near-duplicate claims. Publication v1 has exactly two valid outcomes:

| Outcome | Published consensus suite | Meaning |
|---|---|---|
| `published_consensus_not_qualified` | present | Human consensus was projected without loss, but evidence remains unqualified |
| `blocked_no_suite` | absent | Consensus or screening prevents publication |

A published suite preserves the complete Tag contract, registered repository and source
identities, the agreed ReviewUnit, exact/routing labels and merged evidence, and both reviewers'
complete original votes including their separate evidence lines and rationales. Its
`chain_binding_id` self-hashes the five sealed artifacts, candidate freeze, Feature config
fingerprint, Stage-2C verification, Stage-2D1 screening, source/exposure trees and all three
reference-inventory summaries, so the suite fingerprint cannot be detached from that lineage.

Publication deliberately does not invent fields that the sealed inputs did not freeze. It does
not assign critical-negative status, normalize ReviewUnit bodies, create template clusters,
calculate quality gates, run a candidate or decide activation. The selection proxy stratum remains
selection metadata and never becomes a Truth label.

Run the publisher from the same dedicated checkout at the exact seal commit:

```bash
.venv/bin/python -I -B tools/build_tag_truth_v2_publication.py \
  --selection path/in/repo/selection.json \
  --packet path/in/repo/review_packet.json \
  --receipt path/in/repo/reviewer-a.json \
  --receipt path/in/repo/reviewer-b.json \
  --consensus path/in/repo/consensus.json \
  --source-root /path/to/clean-selection-revision-applications_app_samples \
  --seal-revision <full-seal-commit> \
  --provenance-verification /path/to/stage-2c-report.json \
  --near-duplicate-policy tests/evaluation/tag_truth_v2/near_duplicate_shadow_policy_v1.json \
  --near-duplicate-verification /path/to/stage-2d1-report.json
```

Before project imports, the publication preflight repeats the complete Stage-2D1 preflight,
verifies that the publication core/preflight/CLI have identical Git blobs at candidate freeze and
seal, and captures the external screening report once through a bounded nonblocking regular-file
descriptor. The typed layer then rebuilds Stage 2C, rescans all three reference inventories,
rebuilds Stage 2D1 and only then constructs publication output. Report IDs and self-hashes cannot
replace those rebuilds. A parsed `publication_id` or nested suite fingerprint proves JSON identity
only; formal consumption must call the full publication verifier against the sealed Git/source
inputs.

Exit `0` means only that a `published_consensus_not_qualified` Truth projection was created. Exit
`1` writes a valid `blocked_no_suite` report for unresolved/abstained consensus, duplicate, gray or
screening abstention. Invalid schema, Git, path, hash, source, artifact or rebuild input exits `2`
without a partial JSON result.

The current shadow policy remains `snapshot_only_not_approved`, so both publication outcomes carry
the same top-level readiness envelope: policy/calibration and external-identity evidence blockers,
candidate `not_run`, and quality-gate/activation `not_evaluated`.
The known real project-tree oversize, tokenizer and work-budget abstentions also mean the current
real campaign would remain `blocked_no_suite`; Stage 2D2a does not change that fact.

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
