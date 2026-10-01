# Opus 5.5 generation and Astra final review

This change is isolated on `codex/opus55-astra-review`. It does not include the proposed redesign of coverage, research planning, competitive positioning, or emotional tone controls.

## Model roles

- Search intent, outline, and article: `claude-opus-5-5` (medium effort). Existing visible-output budgets receive 8,000 extra tokens for adaptive thinking. Text is selected by block type, not response position. Incomplete/refused text is not accepted as a complete article.
- Fact gathering, research readiness, service placement, and optional high-accuracy fact review: existing Claude models retained.
- Final evidence audit, separate article-only editorial audit, and targeted block repairs: `gpt-6-astra` (high effort, Responses API, strict JSON schema, `store: false`). These are two focused checks within one final review flow, not one API call.
- The standalone Sonnet full-article rewrite is omitted in Astra mode. Its existing editorial checklist and tenant-specific learned style rules are supplied to the final audit and repairs. Successful review artifacts retain compatibility with the earlier flow.

Repairs remain bounded to two rounds and are audited again. Final completion still requires evidence, prose, structure, comparison, and CTA checks. Missing credentials, refusal, incomplete output, malformed output, and failed API requests stop completion; there is no silent downgrade to Sonnet. Final audit fingerprints include the selected audit model and editorial requirements so earlier approvals cannot be reused across these changes.

## Configuration and rollback

The backend requires `OPENAI_API_KEY` in addition to its existing credentials. The local key was securely created and saved to the ignored `.env` file; it is not in Git. Deployment must separately configure the production secret before enabling Astra. Full jobs check for the key before paid generation begins.

Defaults:

- `ARTICLE_GENERATION_MODEL=claude-opus-5-5`
- `ARTICLE_REVIEW_PROVIDER=astra`

To compare against or restore the prior model route, set `ARTICLE_GENERATION_MODEL=claude-opus-4-8` and `ARTICLE_REVIEW_PROVIDER=sonnet`, then restart the process. Sonnet mode restores the standalone review step and Sonnet final audits. Re-audit existing generated text before completing it under another route.

## Verification

- Unit/integration suite: 191 tests passed, including model routing, legacy rollback, thinking blocks, missing credentials, API failures, incomplete/refused output, bounded retries, and preservation of learned rules and review artifacts.
- Real Opus 5.5 text request: successful.
- Real Astra strict JSON request: successful using the newly provisioned key.
- A synthetic incorrect-price article passed real Astra audit, bounded repair, and final validation with in-memory artifact storage. The first audit rejected the 600-yen claim against the 500-yen fixture source; one repair changed it to 500 yen; the second audit and final gate passed. No production database was used for this test.
- These checks establish connectivity and the review mechanism. They do not establish real-article quality parity or success rates. The agreed paired article evaluation is still required before production rollout.

Official API references consulted:
- https://platform.claude.com/docs/en/models/opus-5-5/migration-guide
- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md
- https://developers.openai.com/api/docs/guides/structured-outputs

## Preset test — not passed (2026-10-02 JST)

Keyword: `既婚者 マッチングアプリ おすすめ`; preset: `既婚者クラブ｜meeting technology掲載用` (`2ae0c003-c11b-4085-b372-21e4943297c9`). Fresh SERP and source retrieval, no prompt overrides or manual article edits. Local staging blocks all Supabase mutations. Job ID: `3d7ae67e-db8f-4d59-9570-2208a1c0bfdc`.

The first attempt exposed a deterministic comparison-candidate bug: a generic shared-features heading and a discontinued-service label were treated as products, while an official Japanese product name was omitted. Commit `2f111a3` fixes these labels; 192 tests pass. The test resumed from contract/outline generation using the already retrieved sources. Original artifacts remain under `attempt-1`.

Research readiness passed, the three-part article was generated, and the configured two CTAs were inserted. The first real Astra audit rejected the draft for evidence/coverage gaps, age-composition exclusivity, unsupported demographic/risk claims, survey scope, awkward prose, and other editorial conditions. This is not a successful article test or permission to deploy. The first automatic repair request failed after about 301 seconds with the original five-minute read timeout; no repair was applied and the run stopped. This timing is consistent with a read timeout, but the old sanitized error did not retain the exception subtype. Artifacts and the failed report are preserved under `attempt-2`.

Local evidence: `/Users/ryoka/seo-saas/tmp/opus55-astra-20261002/`. This directory contains source bodies and tenant settings; do not commit it. No test article has been published and no production model switch has occurred.

Transport follow-up (`81b0822`): consume Responses API SSE privately, require the `response.completed` event and completed status before accepting output, close connections, reject truncated/malformed/failed streams, bound wait/response size, and preserve only safe exception type names. No retry on stream interruption. Unit suite: 194 passed. The re-test from the unchanged article received complete audit responses; successful audit responses have taken 139.1 and 310.9 seconds. This verifies receiving a response beyond five minutes, not article quality or a guaranteed latency improvement. First audit findings remained consistent. The repair response was received after 318.9 seconds, but section identity validation rejected an added H4 before saving the candidate.

Streaming reference: https://developers.openai.com/api/docs/guides/streaming-responses

Repair integration follow-up (`bc1c205`): the prior editorial instructions allowed H4 splitting while stable-section preservation forbade added headings. Repairs now use tables/lists inside existing sections, and topology errors are included in the bounded invalid-patch retry. A matching failed audit can resume repair only when its complete input/rule/model fingerprint is unchanged; successful verdicts are not reused by this checkpoint. Every saved repair is audited again. Unit suite: 197 passed.

The next run resumed the matching failed audit, received a repair after 198.7 seconds, and saved an unapproved candidate (13,926 raw Markdown characters). The next evidence-audit response completed after 301.6 seconds, but the article-only audit ended with an incomplete terminal event. No final validation passed. The old error did not preserve the terminal reason, so token exhaustion is not established as its cause.

Inspection also found that the configured 24,000-token Astra budget was not reaching the two audit calls (they effectively used 16,000). Both now use the configured budget, with regression assertions; terminal incomplete reasons are retained using a safe whitelist. These last changes passed the 197-test suite but have not passed another full real-article run.

Independent review of the unapproved candidate still finds missing document-retention research and removal of the unresolved featured-service renewal/refund paragraph. The existing repair stage has no retrieval tools and cannot itself acquire missing evidence. Rewording these gaps is not proof of resolution. Production release and test-article publication remain blocked by the failed acceptance test, not by a lack of deployment access. The proposed new-flow branch has not been started because the user conditioned it on successful production completion; a sequencing clarification is pending.

Frontend branch `codex/astra-review-progress` (`ad61b4d`) has a successful local production build, four browser-SERP tests, and a READY Vercel preview. No production promotion. Production backend still uses `240418a` and has not received the new model route or OpenAI secret. The local OpenAI secret remains securely configured.
