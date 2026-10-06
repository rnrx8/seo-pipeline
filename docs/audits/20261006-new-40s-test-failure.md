# New 40s article production test

Job: `a401a011-bfd9-44c9-86e7-4bb9f61b2988`
Keyword: マッチングアプリ 40代 既婚者
Preset: `2ae0c003-c11b-4085-b372-21e4943297c9` (既婚者クラブ｜meeting technology掲載用)

User approved a new total ceiling of USD10 at JPY200/USD (JPY2,000), retaining normal USD1.25 quality allowance. A job-specific durable ledger was seeded before creating the prepared job. Because the UI has no per-job budget entry, the prepared row was created in failed/start-pending state, then started via the existing authenticated frontend retry action. Browser SERP was captured normally. This tested the normal production step plan, not the frontend new-row insertion operation.

## Result

Not passed. No article or outline completed. Search intent and five of six subject collections were saved before an unclassified research failure. Existing API orchestration retried the entire pipeline, repeating SERP and paid search-intent generation and invalidating the accepted research plan. The run was stopped by marking this job failed and restarting the service, after confirming no other recent jobs were active. Ancient unrelated running rows were left unchanged. No new generation was started after stopping.

Measured at stop: settled model usage USD2.28059765 (JPY456.11953), plus search estimates USD0.011 (JPY2.2), plus interrupted research-plan reservation USD0.984704 (JPY196.9408). Conservative total USD3.27630165 (JPY655.26033). The interrupted call's reservation remains charged against the ceiling; its actual provider bill is unknown. Quality calls account for USD0.11875 (JPY23.75).

## Confirmed defects

- `api_server._run_pipeline` recursively replayed all stages for transient or unknown exceptions, despite request-level spending limits.
- `classify_error` treated every unknown exception as retryable.
- Query attributes and intent-chain extraction used Haiku 4.5, absent from the budget model tables. Both optional helpers swallowed that configuration error and were skipped.
- Failure details were not persisted before automatic retries. The original exception and stop reason cannot be recovered from the existing log. The final subject's settled output reached exactly 6,000 tokens, making response truncation the leading hypothesis, not a confirmed recorded stop reason.

## Fix scope

Remove whole-pipeline recursive retries; persist failed stage/type/detail before terminal status. Unknown exceptions require diagnosis. Preserve incomplete research responses in separate diagnostic artifacts, never as complete evidence. Keep existing data, total limits, quality limits, and acceptance criteria intact. Add Haiku pricing and centralize both auxiliary model selections; validate configured model accounting before any generation.

Haiku rates verified against https://platform.claude.com/docs/en/about-claude/pricing on 2026-10-06: input1, output5, cache write1.25/2, cache read0.1 USD/MTok. Reserve at the larger cache-write rate. No model upgrade or higher output-token limit was introduced.

Offline regressions cover no replay for timeout/unknown failure, recorded diagnosis, auxiliary billing, unpriced-model preflight rejection, truncated-result retention, and separate incomplete collection persistence. Network disabled for the suite.

## Remaining

The original research truncation hypothesis needs a bounded recovery test after inspecting saved subject scope/output requirements. This patch does not claim that research completes, article quality passes, or JPY300/article is achieved. Do not start a fresh full run, replenish the ledger, increase limits, or replay completed research as a response to this failure.

## Bounded recoveries and coverage routing (2026-10-06)

The original approved plan was recovered only after reconstructing the exact hash
`3c89e20d0d7b72623f7ee76d6f1a782d2a73c0c75641cb01eb3bd87952ee61cc`
from the reviewed candidate and matching all five saved collections. Their work
was preserved. A common-question recovery recorded `max_tokens` at 6,000 output
tokens, confirming internal-memo truncation. Raw partial notes are now retained
for full independent source verification; they are not accepted article facts.
No output limit, acceptance policy or budget was increased.

The first complete evidence/coverage audit flagged q23 (MarriedGo message access
at the compared plan price) and q28 (first-meeting safety). Supplemental q23
research finished and its paid receipt is retained. The next q28 request was
blocked before sending: counted input bound 53,762 plus maximum 6,000 output
reserved JPY82.5144, exceeding remaining quality allowance JPY61.28193.
Client-tool token counting now includes the actual tool definitions and can
refine byte-based estimates. Server-side tools keep conservative bounds.

Ledger after that stop: 76 calls, accounted model usage JPY696.59575,
conservative total JPY896.53655 (including search estimates and the original
unknown interrupted reservation), quality use JPY188.71807. These are ledger
calculations at JPY200/USD, not invoice reconciliation. Total cap JPY2,000 and
normal quality cap JPY250 remain unchanged. No outline or article exists.

### Free correction after the stop

`verify` previously sent every coverage issue into supplemental retrieval even
when the preceding factual reviewer explicitly returned
`additional_sources_needed=false`. q28 contains that decision and an exact
quotation in already-fetched text. Its candidate also has a source-classification
problem (secondary quotations under primary basis), so existing text is not
sufficient grounds for declaring it accepted.

Routing now skips recollection only for coverage issues with both an explicit
no-new-sources decision and an actual matching saved quotation (including saved
omission/unresolved candidates). Missing routing information or evidence retains
the retrieval path. Skipping recollection never changes the verdict: the existing
second audit receives coverage feedback and independently revalidates evidence
and coverage. Its failure remains terminal; there is no third audit or new retry
allowance. Non-tiered subject review now receives the same prior coverage feedback
already used by tiered review.

The original supplemental request identity is preserved while narrowing actual
collection IDs. This matters because the old paid receipt key includes the full
gap list: simply deleting q28 from that list would repeat already-paid q23 work.
The supplementary receipt remains evidence-only and never skips final review.

Offline suite: 482 tests pass with network blocked. New tests exercise actual
retrieval avoidance, missing-evidence fallback, omitted candidate preservation,
feedback delivery, unchanged paid-receipt reuse, second-audit failure and refusal
to restart a third audit. No additional paid model call was made for this patch.

Remaining: real-model verification and article completion are unproven. The
remaining JPY61.28 quality allowance is not a credible end-to-end completion
estimate: a prior article's first five final reviews alone cost about JPY81.27,
excluding this job's pending research audit, outline audit and potential repairs.
That historical result is a planning reference, not a price quote for this article.
Do not increase or reallocate the quality allowance without explicit approval,
and do not spend its remainder merely to stop again mid-flow.
