# Final synthesis and acceptance

The initiating agent completes `final-review.md` before handing it to the user.
Read the original request and this document first. Inspect source or machine
artifacts only to resolve a concrete material doubt; this is a bounded synthesis
of existing review evidence, not another discovery loop.

## One standalone deliverable

The final document must contain:

- the overall recommendation, actual scope and coverage limits;
- the original request, targets, repository snapshot and reviewer identities;
- confirmed, contested, unverified and resolved historical items, with stable IDs;
- both the original claim and cross-check evidence, including source locations;
- a next action for each material item and the remaining follow-up questions.

Write the final synthesis in the user's language. Reconcile stale discovery
questions using cross-check evidence; retain any uncertainty that evidence does
not settle. Separate defects, optional improvements, implementation-time checks
and missing review coverage. A rejected finding is not automatically disproved:
check material counter-evidence before dismissing it. Explain any final disposition
that differs from the ledger; preserve the ledger and raw artifacts unchanged.

Update `report.md` to the same content for compatibility. Return the link to
`final-review.md`, with a concise recommendation. The receiving user or agent
should understand the conclusions without `status.json`, `result.json` or `audit/`.
Those files remain available for diagnostics and recovery.

## Acceptance of the review conclusion

Record one of these dispositions, with the specific reason, in the final document:

- **ACCEPTED**: the conclusion is consistent with the request, project rules and
  evidence. This accepts the review conclusion, not necessarily the reviewed target.
- **REJECTED**: a material conclusion conflicts with the request or evidence.
  Explain the contradiction without silently replacing the model positions.
- **NEEDS_FOLLOWUP**: a specific material question or missing coverage prevents
  acceptance of the requested overall conclusion. State what remains to check.

For `FAILED`, `CANCELED` or `ORPHANED`, record incomplete execution and its failure
code instead of an acceptance disposition. Retained findings are partial evidence.
For `FIXES_VERIFIED`, accept only the specified fixes, not the entire target.

`UNRESOLVED_MAX_CYCLES` is a compatibility name for a completed bounded review
with questions or unverified claims. It does not mean the controller must keep
running. Judge the materiality of each remaining question; incidental uncertainty
does not automatically block the design. Disagreement may remain in a useful final
review document. Reviewer agreement alone does not prove correctness.

Finish the document even when the conclusion needs follow-up. Avoid automatic
reruns, new reviewer calls or requiring all findings to reach consensus before
delivering recommendations.
