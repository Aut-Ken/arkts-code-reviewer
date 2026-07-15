# Lifecycle independent holdout selection policy v1

This policy defines the only approved v1 selection for independent evidence about the
owner-aware `has_lifecycle` candidate. It is a fixed, purposive challenge design—not a
random sample and not an estimate of production prevalence.

## Candidate-exposure boundary

Candidate design had access to the entire tracked `applications_app_samples` repository at
revision `8255a2987f70317cc3a2a4d46044c6b55f092bb3`. That revision is therefore an exposure
boundary, not a holdout source. A selection at the same revision can never be independent.

The selected repository revision must be a strictly later Git descendant of the exposure
revision. Every selected source must be new relative to the complete exposure tree in all
three ways:

- its path-derived source family must not equal, contain, or be contained by an exposed
  source family;
- its source path must not exist in the exposure tree;
- its Git blob/content must not have appeared anywhere in the exposure tree.

The canonical development Truth at `tests/evaluation/tag_retrieval/manifest.json` adds a
second, non-replaceable exclusion set. Its complete source-family, source-path, and content-hash
sets must match the selection artifact exactly. There is no command-line override for this
Truth. Independence is checked only at family/path/content boundaries.

At sealing and evaluation time, every selected source's current bytes must equal the Git blob at
the pinned selection revision and path. `git status` cleanliness, even combined with artifact
hashes, is not accepted as proof of that binding.

## Fixed challenge composition

V1 contains exactly 32 cases from exactly 32 path-derived source families, with at most one
case per family:

| Stratum | Cases | Expected contract slice |
|---|---:|---|
| `component_v1_positive` | 4 | `@Component` custom-component lifecycle |
| `component_v2_positive` | 4 | `@ComponentV2` custom-component lifecycle |
| `router_page_positive` | 8 | `@Entry` router-page lifecycle |
| `nested_owner_negative` | 4 | same-name lifecycle leaf under a nested non-owning declaration |
| `non_entry_page_negative` | 4 | router-page leaf without the required `@Entry` role |
| `ordinary_owner_negative` | 4 | ordinary owner with a lifecycle lookalike leaf |
| `routing_only_negative` | 4 | file-level lifecycle hint that must not become an exact Tag |

This yields exactly 16 positive and 16 negative cases. All four negative strata are critical
negative strata. Cases must be production `src/main/ets` `.ets`/`.ts` sources; `DocsSample` and
`ohosTest` content are excluded. Normalized review bodies, source paths, source hashes, and
families must be unique.

The dataset kind is exactly `purposive_stratified_challenge_holdout`, with
`natural_prevalence_claimed=false`. The design does not claim random selection, a known
inclusion probability, or representation of natural traffic prevalence. No sampling-frame
count, seed, or pseudo-random rank can turn a purposive challenge set into those claims.

The currently eligible corpus contains no independent non-`DocsSample` `@ComponentV2` family.
Consequently the fixed 32-case selection cannot currently be constructed as designed. The V2
stratum may not be dropped, relabeled, borrowed from the exposed revision, or replaced with
additional V1 cases merely to obtain a runnable artifact.

## Frozen quality and safety gates

Before review or candidate execution, the selection freezes these minimum metric gates:

- precision and recall at least `0.95`;
- 95% Wilson lower bounds for precision and recall at least `0.80`;
- zero false positives, false negatives, and critical-negative false positives.

The following safety counts must each be zero:

- Parser risk;
- ReviewUnit/build risk;
- UnitFactScope risk;
- owner-role provenance failure;
- challenge-owner evidence failure;
- file-hint promotion to exact;
- routing-only contract failure.

The stratum category is a pre-review challenge-design assertion, not an independent semantic
label. The two blinded human receipts and consensus remain the canonical Truth source for
evaluation. Selector/reviewer attestations and the intended “first candidate run” ordering are
auditable process claims, not cryptographic proof that no person or machine saw the candidate
elsewhere.
