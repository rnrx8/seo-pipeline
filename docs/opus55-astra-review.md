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
