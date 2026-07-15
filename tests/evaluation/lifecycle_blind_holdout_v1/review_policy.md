# Lifecycle blind review policy v1

Review only the supplied candidate-blind packet and this versioned contract.

For every case:

1. Identify the ReviewUnit that owns the changed line.
2. Record its exact kind, qualified symbol and inclusive source span.
3. Apply the `has_lifecycle` exact-Tag contract.
4. Cite the minimum source lines that justify the owner and semantic label.
5. Record a concise rationale without speculating about candidate behavior.

Do not inspect or request candidate configuration, source code, tests, predictions, diagnostics,
development-regression labels, another reviewer's decisions, or aggregate metrics. Do not infer a
label from sampling order or suspected stratum. If the packet lacks enough owner context, choose
`needs_taxonomy_decision`; do not fill gaps from memory.

The reviewer must be a human ArkTS domain reviewer who did not design the candidate or select the
holdout. The receipt must honestly record the reviewer identity, affiliation, round, timestamps,
and blinding attestations. A receipt hash proves artifact integrity only; it does not prove that
the human-process attestations are true.
