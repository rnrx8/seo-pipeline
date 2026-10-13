# Writing model comparison — isolated evaluation

Status: approved up to 200 JPY. Sonnet generation completed; restricted Opus edit completed. Standalone Opus generation is excluded.
Branch: codex/writing-model-comparison. Production routing is unchanged.

## Sequence

1. Freeze current research, outline, settings, shared writing policy and acceptance criteria. Compare models only.
2. After choosing a candidate model arrangement, compare the current outline against the proposed reader-state instructions. Do not change both variables at once.
3. Verify the combined arrangement on another article, including end-to-end cost and time.

## First experiment

Use the saved `マッチングアプリ 40代 既婚者` / meetingtechnology inputs from job a401a011-bfd9-44c9-86e7-4bb9f61b2988. Scope: H1/lead, comparison H2 introduction and five-company table, first two product H3s. This is an excerpt, not a claim that the complete article has only two products.

- A: OMITTED by user instruction; no standalone Opus generation.
- B: Sonnet 5.5 once, exactly the same system/user inputs, effort medium, output cap including thinking 10,000 tokens.
- C: the SAME B draft, optionally edited once by Opus. Do not regenerate B. If B already meets the agreed quality without a meaningful weakness, skip the paid edit and record why.

The existing published article contains subsequent repairs and cannot be treated as an untreated Opus baseline. Keep it as the user-accepted reference only. No model substitution on failure. No automatic retries, whole-text repair loops, additional research, deployment, or production job mutation.

For C, target at most three prose blocks: lead, featured-product opening, and a featured-product explanation after its table. Select only blocks already present that have a concrete quality weakness. Freeze IDs before sending. Preserve all other blocks byte-for-byte, especially headings, tables, figures, plan conditions and comparison conclusion. Supply the excerpt for context and the same intent/evidence needed by those blocks. Do not add an invented customer-state analysis during this experiment. Reject edits outside the IDs or unsupported factual changes. Cap C at 50 JPY; count its actual request before sending. No charged judge workflow in this initial experiment; inspect outputs directly against the frozen rubric and use existing deterministic checks where applicable.

## Assessment

Show B/C texts side by side using neutral labels for the user's comparison; retain a reproducible model mapping. Record concrete examples rather than a single synthetic quality score.

- Main query words in the comparative H2 and its first answer sentence.
- H2 concise comparative answer versus distinct H3 product overview.
- Clear recommendation tied to confirmed strengths and deep search intent.
- Empathy retained without padding, guarantees, or repeated openings.
- Fact/condition preservation; each product has a table; no internal research notes.
- Natural subject/predicate, no inversion, short conditional scope permitted in the first answer sentence.
- Readability and length; measure rather than rewarding longer output.
- Actual tokens, cost (input/thinking/output included), latency, and number of needed edits.

A single-topic excerpt cannot establish general superiority or production-wide cost. Any full-article test or new-topic research needs its own scope/estimate before paid execution.

## Reproduce preparation (no network)

Set ARTICLE_REVIEW_PROVIDER=tiered and QUALITY_RESEARCH_ROUTING=focused to match the saved research policy. Run prepare.py with --artifacts, --settings and --output absolute paths. Do not commit tenant settings, article fixtures or secrets. Generated requests and input hashes live in seo-saas/tmp/writing-model-comparison-20261007.

Token-count calls are free per https://platform.claude.com/docs/en/build-with-claude/token-counting. Counts are estimates, so reserve margin. Prices verified at https://platform.claude.com/docs/en/about-claude/pricing on 2026-10-07: Opus 5.5 $4/$20; Sonnet 5.5 $2/$10 per million input/output tokens, uncached standard API. All test estimates use 200 JPY/USD. No cache discount assumed.

## Preflight result / proposed authorization

Both frozen requests count as 63,040 input tokens via provider count_tokens. At the configured 10,000 output-token limit (including thinking), uncached Opus ceiling is 90.432 JPY and Sonnet 45.216 JPY. Token counts are estimates. Proposed experiment cap: 200 JPY total, up to 3 model requests (A, B, optional C), no retries; C hard allocation 50 JPY. Sum of counted A/B ceilings plus C is 185.648 JPY, leaving 14.352 JPY margin. No paid generation has run. This is a new experiment; it does not silently extend the prior article's budget. Before executing, implement/verify durable one-call-per-arm reservations and reject a request that cannot fit its approved remaining allowance.

## Latest user authorization

User approved the 200 JPY ceiling, then chose to keep the existing 40s married-app inputs and omit the standalone Opus arm. Execute Sonnet once and only if useful, one Opus block edit of that same draft. run_once.py rejects the standalone Opus arm, reserves at most 50 JPY per allowed arm within the 200 JPY authorization, preserves unknown charges and prevents resending an attempted arm. No production job/state writes. The former A/B/C proposal above is historical; the full Opus arm must not run.

## Completed experiment (2026-10-07)

Sonnet: 63,040 input / 3,001 output tokens, 19.07 seconds, 31.218 JPY. Restricted Opus edit: 12,014 input / 1,250 output (including 770 thinking), 14.34 seconds, 14.6112 JPY. Total 45.8292 JPY at the budgeting rate of 200 JPY/USD. Two calls, no retries, no standalone Opus.

Edited three frozen blocks only; all other blocks, headings, tables and prices match byte-for-byte. Both excerpts pass the existing continuous-prose check. Sonnet includes the query terms in the first H2 answer and product tables. Direct inspection still finds verbose H2 recommendation detail and unnecessary caveats. The saved outline explicitly requests three need/strength pairs in H2 in addition to brief reasons; this contributes to duplication and must not be misattributed solely to the model. Opus improves the lead and product overview but retains a guarantee disclaimer and adds caution in the third block. This is a completed experiment, not approval of the draft or production routing. Targeted human-specified feedback is an intervention, so the result evaluates this editing workflow, not pure model superiority. No new source verification was performed.

## Same-instruction editing comparison (user-authorized follow-up)

Added one sonnet_edit call with a 15 JPY allocation under the original 200 JPY total. The request equals opus_edit in every field except model, including original Sonnet draft, exact three target blocks, feedback, system prompt, JSON schema, medium effort and 3,000-token cap. No outline changes, retries, additional research, production writes or model-routing changes.

Sonnet edit: 12,014 input / 385 output tokens, 4.73 seconds, 5.5756 JPY at 200 JPY/USD. Cumulative experiment spend: 51.4048 JPY. All non-target blocks remain byte-identical; both outputs pass continuous-prose checks and the 200-character per-edit limit. Direct reading: both remove the three-minute lifestyle assertion and connect product features to needs. Opus has a richer product overview, Sonnet is shorter; neither removes the unnecessary guarantee caveat. This supports an editing-stage benefit even with Sonnet, not a demonstrated universal Opus advantage. One sample each; the models use different amounts of thinking under identical settings. No acceptance or production switch follows automatically. Local artifacts: editing-comparison.html, sonnet-plus-sonnet.md, sonnet-plus-opus.md, editing-checks.json in the existing evaluation directory.
