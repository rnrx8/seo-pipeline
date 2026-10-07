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
