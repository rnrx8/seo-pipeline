# Production rollout — 2026-10-06

User authorized production deployment and connection/migration verification.

## Release and configuration

- Runtime release: 66139cc on codex/quality-flow-simplification.
- Railway project 2ea2db3f-87e3-4e3e-a04b-244f91397f2c, production web service 49b91f2b-ddb2-47ce-ab21-6df6eb015a94.
- Successful CLI deployment e9baf012-5c95-4a5a-8dbd-9a4c1ee4f892. Previous release: 240418a, deployment 53584f31-ec7c-4bc6-bb9a-2927eb98958a.
- ARTICLE_GENERATION_MODEL=claude-opus-5-5; ARTICLE_REVIEW_PROVIDER=tiered; QUALITY_RESEARCH_ROUTING=focused.
- Existing approved OpenAI credential transferred without printing or creating a new key. Existing Serper and database configuration retained.
- Durable volume /data, ledger directory /data/quality-budget. No local test ledger or experimental completion-budget override copied into production. Standard code review budget/call limits apply.
- Railway volume initializes under UID 0, then pipeline.serve drops to browseruser UID 1000 before loading the API/browser. A dedicated ledger directory is assigned; existing ledgers are not reset.

## Checks completed without paid model calls

- 464 network-blocked tests pass; diff check clean.
- Production deployment SUCCESS and GET /health returns 200, browser_rendering=true.
- Frontend GET /api/generate returns 200. Frontend POST with keyword=null receives backend's Pydantic 422; generation is not started.
- Runtime code hashes for reader_presentation, content_quality, step_research_guard and serve match the release.
- Runtime validates credential presence and correct model routing. This is not a paid model-access test.
- Production database read succeeds. Existing articles 2939798a-44b2-4152-94cd-515a6087339b and 2c9be430-2c51-52ec-96bf-d7e7f6d992fd remain done with article/outline contents retained.
- Old outlines require explicit update and recheck before reuse; no policy restamping or automatic paid regeneration. No schema migration needed for these artifact-based changes.
- Runtime UID is 1000. Ledger write/reopen succeeds as UID 1000 for deployment-smoke-66139cc with an empty calls list.
- Two running-status records from April/May were left untouched. No queued/bug_fixing records were found before rollout.

## Scope and operational notes

This deploys the code and settings, not a freshly generated end-to-end article. The accepted one-sentence-deletion sample remains local and does not replace any complete production article. Legacy research answer reclassification has not been silently approved/migrated. Resume guards preserve existing content and require the appropriate stage to be updated.

The first archive creation failed locally and an empty upload produced a failed build (215b956f-6ecf-4e2c-99cd-23ffcc599671). It was superseded by the verified complete release. During volume/startup transition the frontend probe saw Application not found; subsequent health and frontend-to-backend checks passed.

Roll back by deploying the previous source revision if necessary; preserve the volume and its ledger history. Do not reset budgets or stamp legacy artifacts as current. Release source is aligned to main after verification to avoid a future main push restoring older code.

Local verification evidence: /Users/ryoka/seo-saas/tmp/production-rollout-20261006.
