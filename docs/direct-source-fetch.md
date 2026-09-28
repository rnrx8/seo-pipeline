# Direct source freshness

The fact-sheet step deterministically fetches public URLs from the selected service,
CTA, active category company settings, applied primary sources, required references,
and custom instructions. Sources are selected by preset first, then category.
Selected service/CTA ownership is checked. Settings read errors stop the step rather
than silently omitting required URLs.

Each run starts without a content cache. Requests send cache revalidation headers;
HTTP redirects are checked for public destinations and forbidden reference URLs.
HTML/text and text-based PDF bodies are supported. Script-only pages, access blocks,
unsupported files and short/empty bodies are recorded as failures, never evidence.
HTTP bodies are bounded to 3 MB; PDF extraction covers up to 40 pages; stored text is
bounded to 24,000 characters. Excerpts are explicitly marked when truncated. At most
40 unique URLs are allowed per pass; an oversized applied URL set stops explicitly.

Claude can call `fetch_current_page` for discovered sources. Claimed confirmed
citations are also fetched by the server even if Claude did not call the tool.
A bounded repair pass reconciles the draft with directly fetched content. A confirmed
fact needs its current check date and short verbatim evidence matching the cited
page; missing evidence downgrades it. This is a provenance check, not a semantic
proof that the quote supports every part of a claim. Dates and applicability still
require model judgment. A publisher/CDN may itself serve stale content despite
revalidation; retrieval time is not publication or update time.

The final high-accuracy review performs a new fetch of applied URLs and article
citations. Both review passes share those fresh bodies and can retrieve more pages.
Incomplete output blocks receive one repair attempt. Failed direct-evidence checks
preserve the previous article. Outline, writing, service placement and normal review
are instructed not to reintroduce stale registered values, including CTA wording.

Evidence is stored per job in `fresh_sources` / `fresh_sources_review`: requested and
final URL, retrieval time, status, failure reason, text hash, excerpt, truncation,
Last-Modified and Age where supplied. Failure details also appear in the fact sheet.
Settings themselves and previously generated articles are not rewritten.

Validation on 2026-09-28:
- 63 Python tests passed, including public redirect/size restrictions, owner isolation,
  per-run fetching, settings URL coverage, client tool/pause continuations, stale
  quote rejection, citation prefetch and malformed review output repair.
- Live selected service/CTA check: one distinct configured URL, successful body fetch.
- Live Claude fact sheet with a deliberately obsolete GitHub Free service setting:
  9 fetched pages; 11 confirmed blocks; 4 unsupported blocks downgraded. The statement
  that private repositories require a paid plan was corrected using official bodies.
- Live high-accuracy review and final audit completed; the obsolete paid-plan claim
  was corrected and an unsupported added Pro price was removed in the final audit.
- Live AI runs used in-memory job/artifact stores; no production article or credit
  balance was changed by validation. Existing provider API usage applies.
