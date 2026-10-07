# Reader-state outline pilot

Existing keyword/preset: マッチングアプリ 40代 既婚者 / meetingtechnology. This isolated evaluation keeps Sonnet 5.5 drafting and Opus 5.5 editing fixed. No production change.

The candidate manually replaces the old prose-planning instructions for lead, comparative H2 and two product H3s with four reader-state fields: understood so far, current decision, remaining uncertainty, confirmed strengths that address it. Existing source URLs, tables, prices, evidence, company settings, system prompt and model parameters are retained. Reader states are hypotheses grounded in the saved intent/chains, not claims about every reader. The old H2 instruction duplication is replaced within the same outline treatment rather than adding another priority rule.

This tests the combined outline-planning revision, not the isolated causal effect of four field labels. It does not prove that an automated outline generator will produce equally useful notes. One existing control draft is reused; a candidate Sonnet draft is generated once. Both drafts receive the same neutral Opus edit policy, context and three roles (lead, featured-product opening, safety-related prose after its information table). Only article text and block addresses differ between editing requests. The earlier human-feedback-specific Opus result is not the control for this experiment.

Scope: H1/lead, first comparison H2, two product H3s. Three authorized requests, caps 50+25+25 JPY under the existing 200 JPY cumulative limit. No retries, new research, deployment or production writes. Every request is counted before execution; unknown charges remain reserved. All nonselected blocks must remain identical within each edit arm. This is not a claim that all facts match between independently generated drafts.

Offline scripts: prepare.py builds the candidate from frozen local inputs; prepare_edits.py freezes the same editorial policy in each arm; check.py applies edits and records scope integrity and readability. Generated tenant fixtures stay outside Git in the existing local evaluation directory.

Assessment includes reader impression, non-assumptive empathy, feature-to-value connection and concrete gentle suggestions. Do not label a caution defective solely because it is longer or retained; distinguish useful guidance from redundant research/guarantee boilerplate. User preferred all three prior Opus edits, including the safety suggestion.

## Result

All three calls completed: candidate Sonnet 31.7428 JPY, control Opus 14.8984 JPY, candidate Opus 15.6544 JPY. Additional total 62.2956 JPY; cumulative experiment total 113.7004 JPY (200 JPY/USD). No retry.

Both edited arms preserve every non-target block and include query words in the H2 first answer sentence. Candidate first H2 answer is 70 characters (control 37); candidate continuous prose grows from 347 to 370 characters after editing, violating the existing 300-character rule (control has no violation). Both edit calls respect the 200-character per-edited-block limit, demonstrating that paragraph-level compliance does not ensure section-level readability. Candidate H3 adds explicit emotional context, but the product overview itself is not clearly stronger than control. The candidate lead selects a beauty-compliment example from the existing intent, which is more specific but does not establish universally better reader fit.

Decision: experiment completed; candidate not accepted for production. This does not reverse the user's preferred Sonnet+Opus model arrangement. The next design adjustment should integrate reader states into existing section roles/format limits, rather than append emotional paragraphs or add repeated bans. Do not rerun paid generation without a new concrete test scope. Manually prepared notes, a single draft per arm, partial editing, and no fresh source verification limit conclusions.

## Follow-up: internal claim limits versus published disclaimers

User approved removing generic guarantee denials while retaining concrete gentle suggestions and meaningful usage conditions. Replaced the existing shared disclaimer rule with a single EFFECT_CLAIM_POLICY used inside the existing reader presentation policy and by the pilot editor. Removed the guarantee-denial sentence from the candidate outline notes. This aligns planning/writing/review/editing without adding an audit pass or a string-removal postprocessor. Contractual guarantees or limitations that answer the query or materially affect use remain explainable.

The frozen experiment requests/results remain unchanged. A separately named local reader-candidate-editorial-preview.md manually demonstrates the correction: two disclaimer-bearing paragraphs become three parallel list items, while free-search explanation and price introduction remain. It passes the existing continuous-prose checker. This preview is not a new model output, and the updated prompt's actual generation compliance is not yet tested. Existing 63 reader presentation/prose/content quality tests pass with PYTHONPATH=.:tests. No paid calls or deployment.

## Withdrawal and readability correction

The user rejected reader-candidate-editorial-preview.md: mixed-purpose paragraphs were merely turned into bullet items. It is NOT an accepted example, regardless of its mechanical count result. The preview is marked withdrawn.

Replaced (not appended to) the existing READABILITY_POLICY: preserve the 300-character contiguous-prose maximum; remove redundancy first, preserve required information and useful empathy, and use a structure appropriate to the information. Numeric threshold checks still run in audit, after repair and at final validation. The existing organization review handles semantic appropriateness; no new model call or checker was added. The limited pilot editor now receives this same policy. Its fixed three-block scope may still prevent fixing a whole-section problem; it must not be treated as a complete article repair flow. No paid generation verification has yet established compliance of this revised prompt.

## Revised-policy real-model verification

Two further authorized requests, allocations 50+25 JPY within the unchanged 200 JPY limit. The Sonnet request reused reader_sonnet input, replaced system with current step_article.SYSTEM_PROMPT and removed the guarantee-denial sentence from the experimental outline. No new research. Sonnet alone still produced a 332-character prose run and a generic guarantee caveat, so prompt-level prevention is not established.

One Opus edit used the updated shared claim/readability policies. The issue-specific packet (prepare_revision_edit.py) allowed eight blocks: lead, table introduction, featured opening, redundant cumulative-count explanation and the full four-block prose run. Seven were edited, one retained. This is directed editor selection, not proof of automatic selection in a production hybrid pipeline. Changed scope is explicitly broader than the previous three-paragraph comparison; this is verification of a correction, not a new model A/B.

The model removed the generic disclaimer and redundant prose without adding lists/tables/headings. The run is now 253 characters; the entire excerpt has zero continuous-prose findings. Existing headings, tables, prices and lists match the Sonnet draft exactly; only allowed blocks changed. The lead no longer treats a three-minute intent example as a reader statistic; the table intro no longer describes all columns as men's prices or exposes retrieval status. Reader desire to preserve family life and pre-payment search remain. No manual rewrite of the final output. No new full factual/whole-article audit was performed.

Sonnet cost 31.5884 JPY, Opus edit 15.8744 JPY; incremental 47.4628, cumulative 161.1632 at 200 JPY/USD. No retry or deployment. Local outputs: reader-v2-final.md and reader-v2-checks.json. Remaining work before production adoption: route detected whole prose runs and semantic findings through existing bounded editing rather than hard-coded three-block selection, and verify that automated integration separately.
