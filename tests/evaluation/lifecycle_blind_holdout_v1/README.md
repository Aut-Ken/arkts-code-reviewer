# Lifecycle owner-role blind holdout v1

This package implements a fail-closed process for obtaining independent evidence about the
isolated `tag-config-v4/tags-lifecycle-owner-role-shadow-v1` candidate. It does **not** contain
a real selection, review receipt, consensus artifact, or candidate result today. Do not run the
candidate until those artifacts exist and are sealed in the required order.

The canonical 48-case Tag Retrieval Truth remains development regression. It cannot be
replaced through a CLI option or copied into this process and renamed as blind evidence.

## Hard independence boundary

Candidate development was exposed to the entire tracked `applications_app_samples` tree at
revision `8255a2987f70317cc3a2a4d46044c6b55f092bb3`. Therefore:

- that same revision is never an independent holdout;
- a holdout revision must be a strictly later descendant of it;
- every selected source must have a new path-derived family, a new path, and new Git
  blob/content relative to the complete exposure tree;
- evaluated source bytes must exactly equal `selection.repository.revision:path`; a clean-status
  assertion or a selection hash cannot substitute for the pinned Git blob;
- the complete family/path/content set from
  `tests/evaluation/tag_retrieval/manifest.json` is an additional canonical exclusion set.

The leakage contract is development family/path/content.

## Fixed 32-case challenge design

The only approved v1 design is exactly 32 cases from exactly 32 path-derived families, one case
per family:

| Stratum | Count |
|---|---:|
| `component_v1_positive` | 4 |
| `component_v2_positive` | 4 |
| `router_page_positive` | 8 |
| `nested_owner_negative` | 4 |
| `non_entry_page_negative` | 4 |
| `ordinary_owner_negative` | 4 |
| `routing_only_negative` | 4 |

This is a `purposive_stratified_challenge_holdout`. It does not claim random selection, known
inclusion probabilities, or natural production prevalence; the artifact fixes
`natural_prevalence_claimed=false`.

The currently eligible corpus contains no independent non-`DocsSample` `@ComponentV2` family.
The real selection is therefore not constructible under the approved design today. Do not
shrink the V2 stratum, reuse the exposed revision, or substitute development cases to make the
pipeline runnable.

See `selection_policy.md` for the full selection and zero-risk gates.

## Roles and sealed artifacts

| Role | Must not have done | Produces |
|---|---|---|
| candidate team | — | frozen candidate/runtime and evaluation-harness identities |
| independent dataset custodian | candidate design; candidate/config/output inspection | unlabeled `selection.json` |
| two independent ArkTS reviewers | candidate design; selection; candidate/config/output inspection | two complete review receipts |

The required order is:

```text
unlabeled selection
-> canonical candidate-blind review packet
-> reviewer A receipt + reviewer B receipt
-> consensus
-> Git seal commit
-> intended first candidate execution
-> lifecycle-owner-role-holdout-evaluation-v1
```

Every artifact uses a closed schema and canonical self-hash. The selection freezes both sides
of executable behavior:

- candidate commit `9b7a828449cbe760ce9374d222f75c48b6f5c852`;
- the complete candidate-commit Python tree under `src/arkts_code_reviewer`, default
  `tags.yaml`/`dimensions.yaml`, Parser sidecar files, and owner-role candidate config;
- Python version/packages/platform, Node version/executable hash, and the complete
  `sidecars/arkts-parser/node_modules` tree fingerprint;
- the evaluation-harness commit, its fixed contract/evaluator/tool file set, and harness
  fingerprint.

The frozen Tag config fingerprint remains
`feature-config:sha256:844418e3d7938c816fd3b64b62cdae3d1753d286d50a6a103406838ed6db01e7`.

## 1. Prepare and seal a selection

Printing the freeze token hashes runtime state but does not run FeatureRouter:

```bash
PYTHONPATH=src .venv/bin/python -B \
  tools/seal_lifecycle_blind_selection.py \
  --print-candidate-freeze
```

Only after a qualifying descendant corpus exists may an independent custodian prepare an
unlabeled draft outside the candidate team's workspace and seal it:

```bash
PYTHONPATH=src .venv/bin/python -B \
  tools/seal_lifecycle_blind_selection.py \
  --draft /private/holdout/selection.draft.json \
  --source-root /path/to/clean-descendant-applications_app_samples \
  > /private/holdout/selection.json
```

The sealer verifies the canonical development exclusions, fixed policy, clean pinned checkout,
strict descendant ancestry, new family/path/content boundary, candidate runtime, environment,
and evaluation harness. Selection cases contain no label, rationale, expected/actual field,
Tag trace, diagnostic, or candidate output.

## 2. Build the canonical blinded packet

```bash
PYTHONPATH=src .venv/bin/python -B \
  tools/build_lifecycle_blind_review_packet.py \
  --selection /private/holdout/selection.json \
  --source-root /path/to/clean-descendant-applications_app_samples \
  > /private/holdout/review_packet.json
```

There are deliberately no `--tag-contract` or `--review-policy` arguments. The tool loads
`tag_contract.md` and `review_policy.md` from their fixed repository paths, hashes their exact
text, and builds the packet from the sealed selection plus verified checkout. Formal evaluation
rebuilds the same canonical packet and requires exact artifact equality.

The packet omits candidate identity/output, selection rank, challenge stratum, and normalized
body hash. Each reviewer sees only the packet plus its embedded canonical contract/policy and
records, for every case:

```text
semantic_label = positive | negative | needs_taxonomy_decision
expected ReviewUnit kind, qualified symbol, and inclusive source span
evidence lines
single-line rationale
```

## 3. Seal two human receipts and consensus

```bash
PYTHONPATH=src .venv/bin/python -B \
  tools/seal_lifecycle_blind_review_receipt.py \
  --packet /private/holdout/review_packet.json \
  --draft /private/holdout/reviewer-a.draft.json \
  > /private/holdout/reviewer-a.json
```

Repeat with a different human reviewer, reviewer ID, and round ID. Each receipt must cover all
32 cases exactly once and bind the same packet, Tag contract, and review policy. Then build
consensus:

```bash
PYTHONPATH=src .venv/bin/python -B \
  tools/build_lifecycle_blind_consensus.py \
  --packet /private/holdout/review_packet.json \
  --receipt /private/holdout/reviewer-a.json \
  --receipt /private/holdout/reviewer-b.json \
  > /private/holdout/consensus.json
```

The label and complete ReviewUnit identity must agree. Disagreement remains unresolved;
`needs_taxonomy_decision` remains a blocker. V1 does not permit deleting such cases or adding a
post-unblinding third vote. The consensus builder still emits the unresolved artifact for audit,
but returns exit `1`; complete consensus returns `0`, and malformed input returns `2`.

## 4. Commit the seal, then evaluate

Copy the selection, packet, both receipts, and consensus into this repository and commit them as
a dedicated seal. Perform the formal run from a fresh dedicated checkout at exactly that full
seal commit—not the development checkout used by steps 1–3. The checkout must have no ignored
`__pycache__`/bytecode, native extension, symlink, extra file, or extra tracked module anywhere
under `src/`; preflight compares the entire importable tree with the frozen closure. It never
deletes such files. All artifact paths supplied to the evaluator must be regular committed files
inside the repository.

Formal evaluation requires an isolated, non-editable virtualenv outside the sealed checkout.
The local repository `.venv` is not an admissible formal runner because editable-install `.pth`
files and repository-local site packages are processed before application code. Start Python
with no `PYTHONPATH`, safe-path, no-bytecode, and no-site modes:

```bash
PYTHONPATH= PYTHONNOUSERSITE=1 \
  /absolute/path/to/external-holdout-venv/bin/python -P -B -S \
  tools/evaluate_lifecycle_owner_role_holdout.py \
  --selection path/in/repo/selection.json \
  --packet path/in/repo/review_packet.json \
  --receipt path/in/repo/reviewer-a.json \
  --receipt path/in/repo/reviewer-b.json \
  --consensus path/in/repo/consensus.json \
  --source-root /path/to/clean-descendant-applications_app_samples \
  --seal-revision <full-seal-commit>
```

The formal CLI executes the standard-library-only preflight source directly, without importing a
cached project module. Before any repository path is added to `sys.path`, preflight verifies the
Git seal, exact committed artifact bytes, full frozen source/harness closure, declared runtime
versions, and the absence of ignored import artifacts. It then inserts only the verified `src`
and the external dependency directory without processing `.pth` files, and imports the typed
evaluator. Because the package has eager imports, verified project source is executed at this
boundary, but the candidate config is still not loaded and `FeatureRouter` is not instantiated or
run. Typed validation next rebuilds the packet and consensus, verifies source bytes against their
pinned Git blobs, policy, development exclusions, and exposure boundary, and only then loads/runs
the candidate. It enforces zero Parser, ReviewUnit,
UnitFactScope, owner-provenance, challenge-owner, file-hint-promotion, and routing-only risks.

Exit behavior is fail closed:

- validation/runtime error: exit `2`;
- valid report with `evidence_ready=false`: exit `1` by default;
- valid report with `evidence_ready=true`: exit `0`;
- `--report-only` allows exit `0` for a valid non-ready report but does not change the gate;
- `--omit-cases` removes case rows and adds `"case_details_omitted": true`; it does not change
  metrics or readiness.

The emitted shape receives an `evaluation_id` after optional case omission. It is the canonical
JSON hash of every report field except `evaluation_id`, using the
`lifecycle-owner-role-holdout-evaluation` prefix. It detects report reconstruction drift but does
not authenticate the runner; retain the independent CI logs and exact command alongside the
report.

## What the process cannot prove

Hashes and Git ancestry protect artifact/runtime integrity, not human or host reality. Reviewer
and selector blinding fields are attestations. A negative stratum name is a challenge-design
category, not Truth. “First candidate execution” is an intended process boundary, not proof that
the candidate never ran elsewhere. Hostile-runtime and fail-closed tests do not cryptographically
prove that the host, interpreter, Git binary, Node runtime, external virtualenv package bytes, or
reviewers are trustworthy. Python direct and transitive package versions are frozen, but their
bytes remain part of the externally controlled environment boundary.

A production-quality run still requires externally controlled identity/permissions and an
independent CI or container boundary with retained logs and artifacts. Even a valid
`evidence_ready=true` report never activates `tags-v1`: activation requires a separate product
decision, configuration migration, Golden/E2E update, rollout, and rollback plan.

## Current status

- Contract, sealing, hardening, and evaluator infrastructure: implemented.
- Qualifying descendant corpus with the required V2 slice: not available.
- Independent 32-case selection: not available.
- Two independent human receipts: not available.
- Sealed consensus and real candidate result: not available.
- Candidate execution for this holdout: not run.
- Production activation evidence: not qualified.
