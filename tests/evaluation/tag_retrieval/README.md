# Active Tag → Retrieval pilot, stage 1

This package freezes the reviewed inputs for `EVAL-TR-01`. It does **not** claim that
Retrieval quality is qualified, and it is not a production Knowledge publication.

## Pinned sources

- Code: `applications_app_samples` at
  `8255a2987f70317cc3a2a4d46044c6b55f092bb3`.
- Official documentation: registered source `openharmony-docs` at
  `c8f5fb6c2fe03cf66b8a41c196ad7fc5e7891c47`.
- Allowed documentation roots are limited to `network/**`, `task-management/**`,
  and `ui/**` below `zh-cn/application-dev/`.

For both checkouts, the verifier requires the configured `origin` remote to match the
repository identity in the fixture, `HEAD` to equal the exact revision, and the worktree
to be clean. Every selected code file and document is also bound to its byte hash and
line count. Every Clause freezes the hash of its exact source span. Timer examples that
support a normative paragraph use a separate supporting-span hash instead of silently
expanding the Clause boundary.

## Code Truth

`manifest.json` contains 48 owner-level development-regression cases for four current
Active Tags:

| Tag | Direct positive | Hint-only hard negative | Ownership/lookalike negative | Multi-Tag positive | Total |
|---|---:|---:|---:|---:|---:|
| `has_timer` | 6 | 3 | 2 | 1 | 12 |
| `has_network` | 6 | 3 | 2 | 1 | 12 |
| `has_state_management` | 6 | 3 | 2 | 1 | 12 |
| `has_lifecycle` | 6 | 3 | 2 | 1 | 12 |

Each Tag has eight cases under the historical `calibration` split name and four under
the historical `acceptance_holdout` split name. A source family cannot cross that split,
and all metric cases are non-DocsSample `src/main` code. The complete 48-case suite has
now participated in matcher/routing design and iteration, so neither split is an
independent blind holdout.

Truth labels describe the desired ReviewUnit semantics; they are not generated from
current Matcher output. In particular, seven lifecycle-target cases remain expected exact
positives even though the current owner projection exposes qualified symbols such as
`Index.aboutToAppear` and the current Tag configuration contains bare symbols.
Each case freezes the expected ReviewUnit kind, symbol, and 1-based inclusive source
span. Verification requires that complete owner identity to match; merely finding an
evidence line somewhere inside the ReviewUnit produced by the current implementation is
not sufficient.

## Official-docs fixture

`knowledge_fixture.json` contains 24 proposed, fixture-only Clause summaries:

- 6 network Clauses;
- 8 state-management Clauses;
- 7 lifecycle Clauses;
- 3 conditional Timer Clauses.

The fixture is deliberately marked `golden_fixture`, `provisional`, and `proposed`.
It has not passed the extraction → dual review → consensus → human curation →
`PublishedKnowledgeBuild` governance chain. It must never be activated as a
production `KnowledgeIndex`.

The approved document slice has no general rule saying that every ordinary foreground
Timer is defective. The three Timer Clauses apply only to background suspension,
Timer-driven invisible Canvas work, or Timer-driven immersive-material churn. Other
Timer cases must be allowed to return no applicable Clause.

## Verification

Run the strict read-only source and owner check with:

```bash
PYTHONPATH=src .venv/bin/python tools/check_tag_retrieval_fixture.py \
  --source-root /home/autken/Code/applications_app_samples \
  --docs-root /home/autken/Code/arkts-knowledge/sources/official-docs/openharmony-docs
```

The checker does not write either checkout. It verifies the Git `origin`, revision, and
cleanliness, all hashes and spans, ReviewUnit owner identity, Parser quality, and current
exact vs. routing observations. Its default summary reports every Tag separately for the
historical calibration and acceptance-holdout split names, so legacy split behavior remains
visible instead of being absorbed into an aggregate; this reporting does not restore blind
holdout status. For a multi-Tag case, every `required_co_tags` entry is
part of the case contract: a missing required exact co-Tag is counted as a case-contract
mismatch even when the target Tag itself matches. FileAnalysis and UnitFactScope
diagnostics remain visible as separate risk counts and case IDs. A zero checker exit code
means fixture provenance and execution were valid; it does not mean current behavior
matched every proposed Truth label.

The external-checkout integration test is skipped when either pinned checkout is absent.
That skip means the external source, owner, Parser, and current-behavior checks did not
run; it is not a passing quality result. Unit tests built from temporary Git repositories
exercise only the repository verifier's basic `origin`, revision, and cleanliness
branches and do not replace the pinned-corpus integration check.

Stage 1 proves that the proposed inputs are reproducible and that current Tag behavior
can be measured against human-authored development-regression labels. It does not yet
run the planned A/B/C Retrieval ablation, build an EvidencePack baseline, test vector
retrieval, or establish Finding quality. Those belong to the next separately reviewed
stage.

## FR-02/FR-02B lifecycle candidates

The explicit `tag-config-v3` fixture at
`tests/fixtures/feature_routing/tag_config_lifecycle_symbol_leaf_shadow_v1.yaml`
replaces only `has_lifecycle.any_symbol` with `any_symbol_leaf`. Its
`feature-routing-v2` trace preserves the raw symbol, `operator=any_symbol_leaf`, and the
case-sensitive final dot-delimited leaf. This pure-leaf route is retained as a
development regression: it cannot distinguish an ArkUI lifecycle owner from an ordinary
class that happens to declare the same method name.

FR-02B evaluates the separate
`tests/fixtures/feature_routing/tag_config_lifecycle_owner_role_shadow_v1.yaml` fixture
(`tag-config-v4/tags-lifecycle-owner-role-shadow-v1`, output `feature-routing-v3`) as an
owner-aware shadow candidate. Its exact operator is
`any_unit_symbol_leaf_with_owner_role`; every configured item binds a leaf to one allowed
owner role:

| Lifecycle leaf | Required owner role |
|---|---|
| `aboutToAppear`, `aboutToDisappear` | `arkui_custom_component` |
| `onBackPress`, `onPageHide`, `onPageShow` | `arkui_router_page` |

`onReady` is intentionally excluded from owner-aware exact matching because
`Canvas.onReady` is a component callback, not a page/custom-component lifecycle method;
it remains available only through `any_file_symbol_leaf` as a conservative routing hint.
Each exact signal binds the raw symbol and normalized leaf to its own
`symbol_occurrence_id`, `direct_owner_declaration_id`,
`enclosing_owner_declaration_id`, `owner_role`, and
`role_evidence_occurrence_ids`. Evidence from another declaration in the same file cannot
be borrowed. The role derivation reuses existing FileAnalysis declarations, decorators,
owners, and occurrence identities; it does not change Parser schema or Parser v1
behavior. A method Unit may bind its own method declaration, while a struct Unit may bind
only direct lifecycle method children of that ArkUI struct. A same-named method inside a
nested ordinary class must abstain instead of inheriting the outer struct's role.
`arkui_custom_component` accepts only `@Component/@ComponentV2`; `@CustomDialog` does not
map to an owner role and must abstain.

V4 uses the separate `any_file_symbol_leaf` operator for routing-only file hints. It is
evaluated only in `file_hint`, while `any_unit_symbol_leaf_with_owner_role` is evaluated
only in `unit_exact`. A file-hint match cannot claim the current Unit's owner role, bind a
specialized Review Question, or become Finding evidence. The default `config/tags.yaml`,
`tags-v1/feature-routing-v1` output, frozen Feature Routing Golden, and CodeAnalyzer path
remain unchanged.

FR-02 v3 uses `tag-retrieval-truth-observation-v2`; FR-02B v4 requires
`tag-retrieval-truth-observation-v3`. Both record the feature-config identity,
`feature_routing_schema_version`, exact/routing Tag sets, symbols from both
`unit_exact/file_hints`, and complete `tag_matches` traces. Observation-v3 additionally
records owner-context diagnostics and the owner-aware evidence above.
`tag-retrieval-truth-observation-v1` remains frozen; candidate-only fields are not
backfilled into the v1 schema.

Run the read-only baseline/candidate E2E comparison with:

```bash
PYTHONPATH=src .venv/bin/python \
  tools/evaluate_lifecycle_symbol_leaf_candidate.py \
  --source-root /home/autken/Code/applications_app_samples \
  --require-declared-contract
```

The current pinned E2E run returns exit code 0 with this decision summary:

```jsonc
{
  "schema_version": "lifecycle-owner-role-evaluation-v1",
  "comparison": {
    "candidate_kind": "owner_aware_shadow",
    "candidate_config": {
      "feature_config_fingerprint": "feature-config:sha256:844418e3d7938c816fd3b64b62cdae3d1753d286d50a6a103406838ed6db01e7"
    },
    "development_regression_lifecycle_exact_metrics": {
      "true_positive": 15,
      "true_negative": 5,
      "false_positive": 0,
      "false_negative": 0
    },
    "declared_contract_gate": {"passed": true, "failures": []},
    "candidate_evidence_gate": {
      "passed": false,
      "failures": [
        "truth_is_provisional",
        "development_regression_only",
        "independent_adjudicated_holdout_missing"
      ]
    },
    "quality_decision": {"activation_ready": false}
  }
}
```

The development-regression contract covers seven lifecycle-target additions, the
declared `TR-TIMER-008` lifecycle co-Tag, and these seven human-adjudicated positive
cross-target additions (recorded product decisions, not blinded independent adjudication):

```text
TR-NET-008
TR-STATE-007
TR-STATE-009
TR-STATE-010
TR-STATE-012
TR-TIMER-004
TR-TIMER-010
```

The declared-contract gate also checks the five lifecycle-target negatives,
routing expectations, required co-Tags, operator/scope separation, per-symbol owner-role
provenance, and the absence of unrelated Tag behavior changes. Passing it proves only
that the known 48-case development-regression contract can be replayed. Metrics over the
seven target positives/five target negatives, or over the 15 declared/adjudicated exact
additions, are selected-label fixture metrics—not the candidate's overall
Precision/Recall.

The historical `calibration` and `acceptance_holdout` split names remain in the manifest
for deterministic reporting, but the whole suite has participated in design and
iteration. An independent blind holdout is still missing, so the report must keep
`activation_ready=false` even when every known contract and provenance check passes. The
owner-aware candidate is not a production-complete route and does not activate the
default v1 configuration.

The report therefore keeps the separate `candidate_evidence_gate` closed. Running the
same command with `--require-candidate-evidence` is expected to return exit code 1 until
the provisional/development-regression and independent-holdout blockers are removed;
that flag is a release-evidence gate, not the known-contract E2E success criterion.
