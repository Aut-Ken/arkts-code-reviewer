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

`manifest.json` contains 48 proposed owner-level cases for four current Active Tags:

| Tag | Direct positive | Hint-only hard negative | Ownership/lookalike negative | Multi-Tag positive | Total |
|---|---:|---:|---:|---:|---:|
| `has_timer` | 6 | 3 | 2 | 1 | 12 |
| `has_network` | 6 | 3 | 2 | 1 | 12 |
| `has_state_management` | 6 | 3 | 2 | 1 | 12 |
| `has_lifecycle` | 6 | 3 | 2 | 1 | 12 |

Each Tag has eight calibration cases and four acceptance-holdout cases. A source
family cannot cross that split. All metric cases are non-DocsSample `src/main` code.

Truth labels describe the desired ReviewUnit semantics; they are not generated from
current Matcher output. In particular, seven lifecycle cases remain expected exact
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
calibration and acceptance-holdout splits, so holdout behavior remains visible instead of
being absorbed into an aggregate. For a multi-Tag case, every `required_co_tags` entry is
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
can be measured against independent labels. It does not yet run the planned A/B/C
Retrieval ablation, build an EvidencePack baseline, test vector retrieval, or establish
Finding quality. Those belong to the next separately reviewed stage.
