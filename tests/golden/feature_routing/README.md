# Feature Routing FR-0 Golden

This directory freezes the human-reviewed Feature Routing truth independently of
Parser, FileAnalysis, ReviewUnit, Retrieval, Rules, and Prompt evaluation.

The suite answers only these questions:

1. Which frozen Tags follow from one Unit's occurrence-scoped `unit_exact` facts?
2. Which conservative routing Tags follow from that Unit's source-scoped
   `file_hints`?
3. Which Dimensions follow from the Unit's exact Tags?
4. Which Dimensions form the MR union after exact and routing-only signals are
   combined?

Tags and Dimensions are routing metadata. They are neither Finding evidence nor
proof that a defect exists.

## Files

```text
tests/golden/feature_routing/
├── README.md
├── manifest.json
├── baselines/current.json
└── sources/
```

`manifest.json` contains manually reviewed `expected` values.
`baselines/current.json` records the current implementation behavior. A baseline
must never overwrite or generate the expected truth. Parser output must not be
used to synthesize expected routing results.

Every source is self-contained and hash-pinned. `source_ref_id` is the real value
returned by `CodeSourceRef.create(repository, revision, logical_path,
sha256:<content_sha256>)`. The manifest uses short opaque Golden `unit_id` values
so FR-0 remains isolated from ReviewUnit identity formatting. Production passes
the real ReviewUnit `unit_id` unchanged.

## Frozen schema

The root object has exactly:

```text
schema_version = feature-routing-golden-v1
suite_id       = feature-routing-fr0
description
tag_ids        = the 24 frozen Tag IDs
dimension_ids  = DIM-01 through DIM-12
cases
```

Each case has `case_id`, `description`, `sources`, `units`, and `expected`.
Sources have an alias plus immutable provenance:

```text
alias
file
repository
revision
logical_path
content_sha256
origin_lines       # 1-based, inclusive
source_ref_id
```

Each input Unit has:

```text
unit_id
source_alias
source_ref_id
unit_exact
file_hints
scope_diagnostics
```

Both fact scopes contain all 13 `ScopedFacts` fields. Every fact array is sorted
and unique:

```text
components, apis, decorators, attributes, symbols, syntax,
import_bindings, import_uses, field_reads, field_writes, calls,
string_literals, resource_references
```

Each expected Unit has `unit_id`, `source_ref_id`, `exact_tags`, `routing_tags`,
and `dimensions`. `expected.mr_dimensions` is the conservative MR union. All
output lists are sorted and unique, and expected Units are ordered by stable
identity even when the input Units are deliberately permuted.

## Scope rules

- `exact_tags` derive only from that Unit's `unit_exact` facts.
- `routing_tags` derive only from `file_hints` belonging to the Unit's exact
  `source_ref_id`.
- File hints may widen retrieval, but may not become Unit-exact facts or Finding
  evidence.
- Unit `dimensions` derive only from `exact_tags`.
- `mr_dimensions` derives from the union of exact and routing Tags across the
  case.
- DIM-01 through DIM-05 and DIM-12 are always active.
- One file's hints must never reach a Unit from another file.

## Case matrix

| Case | Human-reviewed purpose |
|---|---|
| FR001 | Exact timer versus image/resource file hints |
| FR002 | Canonical emitter subscription |
| FR003 | Media and file-I/O convergence on DIM-06 |
| FR004 | Async, taskpool, and worker coverage |
| FR005 | Interactive, layout, and responsive UI coverage |
| FR006 | Text display plus `resource_references` |
| FR007 | Permission, input, network, and storage coverage |
| FR008 | State-management and lifecycle taxonomy |
| FR009 | Cross-file exact/routing isolation |
| FR010 | List-render and animation taxonomy |
| FR011 | Builder, navigation, and logging taxonomy |
| FR012 | Fallback: empty exact facts and conservative file hints |
| FR013 | Timer cleanup positive and unrelated `SDK.on` negative |
| FR014 | Lifecycle/error callback negatives and `onClick` positive |
| FR015 | Input permutation and deterministic output order |
| FR016 | Neutral facts do not fabricate Tags |

Together these cases exercise all 24 frozen Tag IDs and all 12 Dimensions.

## FR-0 red lights and FR-1 resolution

The first evaluator run against the pre-FR implementation exposed three semantic
defects. The FR-0 baseline preserved them at `14/16`; expected truth was not
weakened:

- `clearInterval` is timer lifecycle behavior and must trigger `has_timer`.
- `onAppear` and `onError` do not by themselves trigger
  `has_interactive_component`.
- An unrelated API such as `SDK.on` must not trigger `has_subscription`; only
  canonical subscription namespaces/events may do so.

FR014 retains `onClick` as a positive control so excluding lifecycle/error
callbacks cannot be implemented by disabling attribute-based interaction
routing entirely. FR-1 moved these triggers into validated `tags-v1` config and
the current strict baseline is now `16/16`.

## Loader requirements

The FR-0 loader is expected to fail closed on duplicate JSON keys, duplicate case
or identity values, unknown/missing fields, invalid aliases, malformed or
unsorted arrays, unknown Tag/Dimension IDs, provenance/hash/source-ref drift,
invalid origin lines, symlink/path escape, expected/input identity mismatch, and
nondeterministic output order.
