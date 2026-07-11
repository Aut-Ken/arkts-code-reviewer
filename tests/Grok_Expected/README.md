# Grok Parser candidates

This directory stores Grok-produced `candidate_unreviewed` shards. These files are
annotation inputs, not approved Parser Golden truth, and the formal Parser Golden
evaluator and strict baselines must not consume them directly.

- `Bxxx.candidate.json`: current candidate for a task group.
- `raw/`: immutable first-pass outputs kept for annotation-quality auditing.
- A candidate may enter `tests/golden/parser/` only after schema/provenance checks
  and source-level adjudication.
- Parser output, baselines, TP/FP/FN reports, and prior Golden expected values do
  not belong here.

The separate `tools/evaluate_parser_candidates.py` command may consume a strictly
validated subset for provisional development metrics. Its output remains
`candidate_unreviewed`, cannot be used as a strict Golden baseline, and defaults
to the 23 cases whose annotations have completed the current review pass. B007
and B009 remain available only through explicit `--groups` selection until their
known `ForEach`/`LazyForEach` policy conflicts are adjudicated.

From `/home/autken/Code/grok_text`, validate one group with:

```bash
python scripts/validate_outputs.py \
  --group Bxxx \
  --output-dir ../arkts-code-reviewer/tests/Grok_Expected
```
