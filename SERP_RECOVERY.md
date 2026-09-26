# SERP acquisition and recovery

`SERP_PROVIDER=serper` and `SERPER_API_KEY` select Serper. The key belongs in the
backend environment only. With no explicit provider, a configured Serper key is
preferred; otherwise the existing SerpAPI key is used. There is no silent fallback
between providers. Requests use the original query, Japanese, Japan and normal
Google web search. The saved `serp.source` records provider, query and acquisition
time.

The SerpAPI path also requests verbatim search as a diagnostic. When both responses
have at least three URLs and overlap is below 20% of the smaller set, generation
stops before fetching competitor pages or writing a SERP. This catches the saved
2026-09-26 free/recommended-app failures, but is a heuristic: normal and verbatim
search can legitimately differ, and overlap does not prove the rankings are
correct. Verbatim results are never substituted for normal rankings. Empty,
malformed, duplicate and altered-query responses also stop generation. These
errors are operational, not automatic code-repair or retry candidates.

Missing PAA remains empty; questions from a different query are not mixed in.

## Recover an existing article

Record the actual browser results, excluding ads, AI citations and private Search
Console cards. Preserve their order and visible titles. A JSON snapshot requires:

- `job_id`, exact `query`, timezone-aware `observed_at`, normal Google `search_url`
- `organic_results`: ordered objects with `title`, `link`, optional `snippet`
- Optional `people_also_ask` and `related_searches` from that same search

Snapshots expire after 24 hours and cannot be reused for a different article or
query. An override is deliberately retained as an audit record; a future rerun
requires a fresh observation or explicit removal of the override by an operator.

Run from the backend root:

```sh
python3 -m scripts.repair_serp snapshot.json
python3 -u -m scripts.repair_serp snapshot.json --apply
```

The first command is a dry run. Applying backs up the complete existing job and
artifacts to the ignored `repair-backups/` directory with file mode 0600, then
rebuilds all dependent steps using the normal shared step plan. It does not charge
application credits or send completion emails. A failed rebuild marks the job
failed; it does not pretend that partially rebuilt artifacts are a completed
article. Preserve the backup until the recovered result is verified.

Validation: `python3 -m unittest discover -s tests -q`.

Upstream incident evidence: https://github.com/serpapi/public-roadmap/issues/3781
(similar irrelevant-results reports; the precise upstream cause of our responses
has not been established).
