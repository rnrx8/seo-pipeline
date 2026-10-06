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
