# Explicit Firecrawl search backend

The gateway can use the official Firecrawl data API for native alpha/search.
This optional external data service does not change the DeepSeek model, native
tool execution, approvals, threads, or App UI.

Choose it explicitly in the private gateway policy:

~~~json
{"web_search_backend": "firecrawl"}
~~~

An absent field retains openrouter_exa. There is no automatic selection, failed
request fallback, account creation, or switch of model/provider. Restart the
gateway through its normal lifecycle after deliberately changing its policy.

## Data and service limits

A search sends the original query plus explicit domain/date/location constraints
to https://api.firecrawl.dev/v2/search. A cached document read sends its URL to
https://api.firecrawl.dev/v2/scrape with lockdown=true. These are external
requests even though the gateway listens on localhost. Send queries or URLs only
when your data policy permits this service.

This implementation uses the documented keyless route. No Firecrawl account or
credential is read or created. Keyless use has service-managed rate and usage
limits; HTTP 429 is a visible failure, without a hidden switch to another service.
It is not a promise of unlimited production capacity or a service SLA. Optional
authenticated team billing is not implemented or qualified by this adapter.

## Native settings and retained evidence

- q remains the API query; constraints are separate API fields.
- Native allowed domains and per-query domains are intersected before a request.
  Every returned, requested, and reported final URL is checked against the policy.
- country is sent as its own ISO code, as well as the supplied city/region
  location bias. This is approximate geographic ranking, not a geographic fence.
  No city or country is inferred from a timezone.
- search_context_size selects bounded result counts: low 3, medium 5, high 10.
  It controls retrieval count, not a guarantee of a particular token count.
- Native recency day counts currently map to the documented UTC calendar-date
  range in tbs. Receipts identify this as a calendar range, **not an exact rolling
  timestamp window**. Full recency equivalence remains unqualified where that
  precision matters; the native source specifies days without further precision.
- Cached mode requests HTML only from the service cache. The official lockdown
  contract forbids target-URL requests on a miss. A service-reported cache miss is
  returned explicitly, without a live fallback. Original PDF bytes and pixels
  are not reconstructed from cached HTML.
- Indexed mode uses the existing public document fetcher, checking exact URL
  membership in search results before each target request and redirect. A missing
  exact match means **membership unverified**, not proof of absence from the
  index. No unverified target is fetched.
- Live mode uses the same guarded public fetcher as before. Source references,
  retained HTML, page links, find, navigation, and native session ownership
  continue through the existing source store.

The successful HTTP receipt survives downstream source/metadata errors. Receipts
record response hashes, bounded byte counts, complete versus incomplete streams,
cache metadata and service identity. They do not log authentication, model
reasoning, or raw documents in public diagnostics. The reported final URL is
checked; a complete redirect chain is not invented when the cache API omits it.

## Explicit limitations and acceptance

Timezone search fields have no verified mapping in this backend and fail before
the whole batch starts. Combined allow/block domain policies are reduced only
when the resulting hostname set is exactly equivalent: blocks covering an
allowed subtree remove that subtree; disjoint blocks are already excluded by
the allowlist. If no allowed host remains, no upstream request is sent. A blocked
child inside a remaining allowed subtree cannot be expressed by the upstream
API and fails before the batch; results are never post-filtered to hide that gap.
Existing Wikimedia image search does not gain date/location or cached/indexed
controls by choosing Firecrawl. Unsupported image-policy combinations fail
explicitly. Finance and sports still require separately verified typed data feeds; ordinary
web snippets are not returned as those data feeds. The explicitly configured
weather feed is documented in [weather-service.md](weather-service.md); its
App acceptance is recorded separately from search.

Windows checks verified keyless API access, an actual cached HTML hit and a
unique cache miss, and API acceptance of separate location/date fields. These
are lower-level service checks. App/runtime qualification requires an explicitly
selected installed gateway and native GUI search/open/find/click/cold recovery;
backend unit tests do not establish that result.

Official contracts:
[search](https://docs.firecrawl.dev/api-reference/endpoint/search),
[scrape and lockdown](https://docs.firecrawl.dev/api-reference/endpoint/scrape),
[keyless access](https://docs.firecrawl.dev/mcp-server/keyless).
