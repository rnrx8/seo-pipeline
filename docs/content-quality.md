# Mandatory content quality gates

The normal pipeline now runs `research_validation` after outline structure repair,
then `content_audit` after editing (and after optional high-accuracy fact review).
Neither gate changes the configured models. Writing keeps the existing concrete
phrase/style policy; factual claims inside those phrases must match evidence.

## Evidence and recovery

Only confirmed paragraphs are passed to outline, contract, writing, and review.
Mixed confirmed/hypothesis paragraphs and untagged summaries are excluded.
Research artifacts still retain all findings for diagnosis. Source retrieval now
returns bounded same-site navigation links, including actual pricing/FAQ URLs.

Readiness evaluates coverage, evidence, comparison conditions, conclusions,
metric definitions, unfinished content, and unsupported guarantees. A failed
readiness check triggers one new research/contract/outline/structure pass, then
re-audits. Persistent gaps stop the job before writing. The additional research
is supplied with the concrete failed checks, rather than a generic retry.

Final content audit checks the same seven areas, with at most two corrections.
Each candidate is saved and re-audited; corrections cannot inherit a pass.
Malformed/truncated/skipped audits are failures. Invalid editing retains the old
article but stops the job. Quality failures are operational/non-retryable so the
API does not restart the entire generation or create a code-bug issue.

Passed audit reports are bound to a hash of policy version, text, evidence,
outline, contract and requirements. Final completion requires a matching audit.
The readiness hash is also checked before writing. Starting an audit invalidates
any previous pass, including when an API request subsequently fails.

## Deterministic checks and limits

Explicit N-selection headings are checked against recognized service-name tables.
A semantic audit also checks actual coverage and aliases; merely mentioning names
is insufficient. Legitimate non-public fields and price-date caveats are allowed.

For recognized service-labelled period-by-plan pricing tables, code computes the
lowest listed monthly value per period. An unqualified long-term cheapness claim
that contradicts those values fails regardless of the model's verdict. Explicit
plan-qualified comparisons remain subject to semantic checks. Tables with missing
or ambiguous price cells, ranges, different known tax/gender contexts, or total-
only multi-month amounts are not ranked mechanically. The code does not infer
feature equivalence, campaign eligibility, or prices not present in the table.

This does not guarantee perfect factual accuracy. Semantic checks still use the
existing review model, and uncommon table layouts are reviewed by that model.
Compared with the old flow, successful runs add two audit calls; failures may
add one research rebuild and two article corrections. All retries are bounded.

## Verification (2026-09-29)

- 116 unit/regression tests passed, including the actual incomplete eight-item
  comparison and the actual 3,280 vs 3,800 yen contradictory conclusion.
- Live, read-only model audits of both saved failed articles rejected them.
  Both old outlines also failed readiness.
- The initial semantic audit still missed the fee contradiction; the arithmetic
  check was added and the paired article was re-tested successfully as a failure.
- A separate short control comparing explicitly scoped one-month prices passed
  the live audit. This control is not a regenerated production article.
- The two production article bodies were not replaced by this change. A full
  end-to-end regeneration under the new gates has not yet been measured.
