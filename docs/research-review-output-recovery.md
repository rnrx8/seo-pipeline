# Research review output capacity and recovery

Incident: job `bb527ffc-987d-4205-8c5b-72e08d1699e0`, 副業 おすすめ.

The 22 individual research decisions were saved, but the aggregate review used
4,000 output tokens. Its 3 decision reviews covered 21 answer requirements.
The common estimator returned before counting standalone decision reviews.
The terminal Responses event reported `max_output_tokens` (2,264 reasoning
tokens out of 4,000 output tokens), with known usage and no credit/auth error.
The adapter discarded partial output by raising an untyped quality error.

## Changes

- Size standalone decision and plan reviews as well as article blueprints.
  Aggregate coverage capacity includes actual question and answer requirements;
  the incident request now reserves 14,750 tokens. No fee allowance is raised.
- Distinguish a known terminal output limit from transport, quota, filters and
  provider errors. Retain its visible partial output, response ID and usage.
- Reuse only fully decoded, schema-valid fields. Arrays and strings are atomic;
  nested object maps can retain complete verdict IDs. Preserve failures as well
  as passes. No parser invents a closing quote or a missing verdict.
- Complete only missing fields against the exact original inputs and policy.
  Store immutable original/partial hashes and separate completion receipts.
  Allow two narrowed completion requests, using the existing cost ledger.
  Durable checkpoints make a restart reuse completed work rather than repeat it.
- Keep existing result history, source quotes, source checks, adopted article and
  final QA semantics intact. This patch does not certify the article's quality.

## Verification

Nine focused tests cover sizing, terminal SSE/usage accounting, nested partial
fields, failure retention, schema rejection, exact-material binding, restart
reuse, bounded nonprogress, and a changed assignment retaining its earlier partial. All 1,336 offline tests pass.

A native replay of the actual saved 54 artifacts reuses all 6 completed source
review responses. Only `tiered_coverage` reaches the provider boundary. Its input,
model, schema, policy and 22 decisions are exactly unchanged; only output capacity
differs. Network access is forbidden in the replay. Original artifacts and the
$2.282576325 ledger are unchanged. No paid verification was sent in this replay.

## Release boundary

This hotfix starts from production `f47400a`. It deliberately preserves source
review input identity for the existing failed job. The broader pending normal
flow candidate `41bf4f1` changes source instructions and requires its own release
and semantic verification. This patch is not evidence that candidate is deployed.

Known limits: no automatic repeat of unknown/in-flight requests, exhausted fee
allowances, missing credentials, content filters or refusal. Output that still
makes no progress after the bounded completion attempts is retained for diagnosis;
it is never accepted as a passing review.
