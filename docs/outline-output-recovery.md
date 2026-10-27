# Recover progressively truncated outline output

The production outline and its first suffix both reached the provider output
limit. The second partial response was saved, but the suffix completion helper
did not consume it. This stopped generation before the body and incorrectly
reported a quality-review failure.

The helper now validates and retains every fully decoded schema value or array
item in each matching partial response. It requests only the remaining suffix
under a new request identity. Completed sections, source bindings and expense
records remain intact. An unchanged, mismatched or non-progressing partial is
never resent. A completed cached suffix is reused. Output truncation has its
own operational error classification rather than an article-quality verdict.

Verification: 1,325 offline tests pass. Replaying the actual production responses
without network access preserves 26 completed sections and reaches only the
second suffix request, without repeating research, the original outline or the
first suffix. The final article still requires live generation and review.
