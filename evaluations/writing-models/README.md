# Writing model comparison — isolated evaluation

Status: requests prepared; paid generation NOT approved or executed yet.
Branch: codex/writing-model-comparison. Production routing is unchanged.

## Sequence

1. Freeze current research, outline, settings, shared writing policy and acceptance criteria. Compare models only.
2. After choosing a candidate model arrangement, compare the current outline against the proposed reader-state instructions. Do not change both variables at once.
3. Verify the combined arrangement on another article, including end-to-end cost and time.

## First experiment

Use the saved `マッチングアプリ 40代 既婚者` / meetingtechnology inputs from job a401a011-bfd9-44c9-86e7-4bb9f61b2988. Scope: H1/lead, comparison H2 introduction and five-company table, first two product H3s. This is an excerpt, not a claim that the complete article has only two products.

- A: Opus 5.5 once.
- B: Sonnet 5.5 once, exactly the same system/user inputs, effort medium, output cap including thinking 10,000 tokens.
- C: the SAME B draft, optionally edited once by Opus. Do not regenerate B. If B already meets the agreed quality without a meaningful weakness, skip the paid edit and record why.

The existing published article contains subsequent repairs and cannot be treated as an untreated Opus baseline. Keep it as the user-accepted reference only. No model substitution on failure. No automatic retries, whole-text repair loops, additional research, deployment, or production job mutation.

For C, target at most three prose blocks: lead, featured-product opening, and a featured-product explanation after its table. Select only blocks already present that have a concrete quality weakness. Freeze IDs before sending. Preserve all other blocks byte-for-byte, especially headings, tables, figures, plan conditions and comparison conclusion. Supply the excerpt for context and the same intent/evidence needed by those blocks. Do not add an invented customer-state analysis during this experiment. Reject edits outside the IDs or unsupported factual changes. Cap C at 50 JPY; count its actual request before sending. No charged judge workflow in this initial experiment; inspect outputs directly against the frozen rubric and use existing deterministic checks where applicable.

## Assessment

Show A/B/C texts side by side using neutral labels for the user's comparison; retain a reproducible model mapping. Record concrete examples rather than a single synthetic quality score.

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
