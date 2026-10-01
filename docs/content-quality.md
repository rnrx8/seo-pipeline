# Mandatory content quality gates

The normal pipeline now runs `research_validation` after outline structure repair,
then `content_audit` after editing (and after optional high-accuracy fact review).
Neither gate changes the configured models. Writing keeps the existing concrete
phrase/style policy; factual claims inside those phrases must match evidence.

## Evidence and recovery

Only confirmed summary paragraphs cross into planning and writing; mixed
confirmed/hypothesis paragraphs and untagged summaries are excluded. Outline,
service placement, writing and editing also receive the same fetched source
bodies as readiness/content audit. A missing summary entry does not mean the
source is non-public. Same-scope official source text takes priority over an
incorrect summary. Contract candidate extraction uses summary identities, not
property labels such as membership counts or security measures.
Research artifacts still retain all findings for diagnosis. Source retrieval now
returns bounded same-site navigation links, including actual pricing/FAQ URLs,
and preserves row/column spans in HTML tables before flattening page text.
Research correction retains the original source reliability policy and receives
specific evidence/date mismatch feedback. A current fetch date can stand in for
a check date only when a quote matches a directly fetched body.

Readiness evaluates coverage, evidence, comparison conditions, conclusions,
metric definitions, unfinished content, and unsupported guarantees. A failed
readiness check first tries one outline correction using the existing sources
and re-audits. Persistent failures trigger one new research/contract/outline/
structure pass and a final re-audit. Persistent gaps stop the job before writing. The additional research
is supplied with the concrete failed checks, rather than a generic retry.

Final content audit checks the same seven areas, with at most two corrections.
Each candidate is saved and re-audited; corrections cannot inherit a pass.
Malformed/truncated/skipped audits are failures. Invalid editing retains the old
article but stops the job. Quality failures are operational/non-retryable so the
API does not restart the entire generation or create a code-bug issue.

Passed audit reports are bound to a hash of policy version, text, evidence,
outline, contract, requirements and the exact fetched-source context. Final completion requires a matching audit.
The readiness hash is also checked before writing. Unresolved structural gaps
are recorded as invalid and passed to bounded research recovery; readiness
rechecks the mechanical contract and cannot approve those gaps based only on a
model verdict. High-accuracy fact-review corrections carry source/quote evidence
and lineage hashes into the later audits. Starting an audit invalidates
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


## Integration verification (2026-10-01)

133 unit/regression tests pass. Additional tests cover evidence handoff,
source-bound audit snapshots, row/column spans, erroneous service identities,
and invalid structure recovery. A live audit rejects a female pricing column
mix-up and accepts its correct counterpart with table-preserving source text.
The 2026-09-29 two-article replay stopped at research readiness and Part 1;
neither was published. It exposed a missing evidence handoff to authors and
property labels being counted as extra services. Those paths and conflicting
confirmed-only writing instructions are now corrected. A fresh two-article
replay is underway; unit tests are not evidence of completed article quality.

## 2026-10-01 本文単独の整合性検査

大量の原文を伴う監査が「ノーリスク」「退会すれば記録が残らない」を見逃した実生成例から、本文単独の検査を追加。出典監査とどちらか一方でも不合格なら修正・再検査。対象条件の矛盾、安全保証、編集用のH2/H3案内を検査する。共通執筆方針にも対象・範囲の維持を追加。policy v4で旧合格は無効。回帰テスト138件成功。正常文の実モデル検査2件は合格。見逃した実本文は不合格を確認。内容修正にも共通執筆方針を適用。実生成の修正・再検査は進行中。
