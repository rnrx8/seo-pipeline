# Generation completion and publication readiness

2026-10-26. Requested UX: do not require a user to resume generation because an editorial check or bounded repair did not pass.

## Contract

The shared `quality_warnings.scoped` stage boundary is used by API and CLI. Research completeness, structural inspection, draft readiness's coverage/length checks, service placement, CTA finishing, review, fact review, content QA and final validation are advisory during delivery. Existing bounded repairs and exactly matching saved responses remain available. Their failures or limits do not fail an otherwise deliverable article. This does not increase a job's paid-call or dollar allowance.

A fully assembled body is persisted before optional structure/lead finishing. Only this invocation's checkpoint may replace failed finishing; an older article is never mistaken for a newly generated draft. After a body exists, an unavailable optional QA provider or a QA implementation/response-format error leaves the body readable with warnings. No retry is issued by the warning handler.

Final validation still writes `valid:false` and every violation. The article is labelled `content_audited:false`, `publication_approved:false`, `completion_with_warnings:true`. Its old audit snapshot is cleared. Failed recovery/audit receipts and costs are retained. A failed final validation cannot seal a publication revision or clear warnings. `done` means the requested deliverable is available, not that it has passed publication review.

Unknown or stale research never becomes verified. Writers receive only current accepted answers; malformed publication conditions exclude that answer with a warning. Outline-reader/answer mapping, volume and semantic QA findings do not constitute a permission gate. Physical JSON/blueprint parsing and actual input/metadata integrity are still needed to execute the writer. A model or storage failure before any usable output exists cannot honestly be called completion.

A real concurrent worker keeps ownership: a second controller exits without marking its job failed/done or resending paid work. Explicit user dollar caps, uncertain paid requests, credentials and data ownership remain protected. Save failures remain real errors. No job/data schema, credit grant, purchase, publishing or old adopted reference changes are included.

## Proof scope

No new paid generation or model call was used for this change. Offline tests exercise the real shared API and CLI orchestration with mocked generation/DB providers, failed research checks and failed article QA, and the actual final validation. Both reach `done`, retain the generated article and leave publication validation false. Separate cases cover all post-body stages, provider/format/repair errors, the in-invocation body checkpoint, absence of a body, busy workers, storage failures, and warning-notification failure. The existing strict-success path and all prior source/QA validators remain tested.

A real fresh paid generation and its duration/quality remain for the user's normal-use visual check. This contract cannot guarantee immunity to provider outages, missing credentials, or a failure to create/store a deliverable.
