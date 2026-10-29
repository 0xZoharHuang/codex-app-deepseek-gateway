# Public weather forecast service

The gateway's alpha/search adapter implements the native weather command
through the official Open-Meteo geocoding and forecast APIs. Codex still owns the
tool call, session, permission and cancellation mechanisms. This adapter performs
data-service HTTP requests; it does not invoke a model or execute a tool loop.

The public endpoints require no API key and are provided for non-commercial use.
Open-Meteo's commercial service is not configured by this implementation. Review
the [service terms](https://open-meteo.com/en/terms) for your use. Forecast output
retains Open-Meteo and GeoNames attribution.

## Requests and results

Native fields are location, optional ISO start, and optional duration.
The default is seven days starting on the resolved location's current date.
The supported forecast horizon is today through fifteen days ahead. Historical
observations are not configured; archived forecasts are never presented as
observations. Temperatures use Celsius, precipitation millimetres and wind km/h.
Each retained daily row has its own line, so ordinary native open/find references
can navigate the complete result after output truncation.

Prefer Country,Area,City, for example:

    {"location":"United States,California,San Francisco","duration":7}
    {"location":"US,CA,San Francisco","duration":2}
    {"location":"Germany,Berlin,Berlin","duration":2}

Country codes must use the two-letter country-code form accepted by the service,
and the returned country is checked independently. US state/DC abbreviations
use the fixed [USPS table](https://pe.usps.com/text/pub28/28apb.htm), scoped to the
United States. Other areas use the returned full administrative name. The
English display prefix "State of " is accepted as the same area name; the
original resolved label remains visible. An unambiguous bare city or a
City,FullCountryOrArea form also works. Two-letter two-part qualifiers are
rejected because a country code and an area abbreviation can collide.

The adapter does not choose the first or largest same-name location. It
requires one exact city/country/area match. A full geocoder result page is
inconclusive; when the supplied full country can be bound to one returned country
code, one explicit country-code refinement is permitted. A still-full or
ambiguous page fails visibly. This is bounded lookup, not a retry of a failed
request. Localized aliases, country-name synonyms and arbitrary administrative
abbreviations are not claimed to be universally resolved.

The result includes resolved place identity, country, area, coordinates,
timezone, units, retrieval time and source hashes. It is explicitly marked
forecast_not_observation. Wrong units, incomplete dates, invalid numeric
values, wrong grid identity and entirely missing requested days fail rather
than becoming successful forecasts.

## Transport and qualification

Only the two fixed official service hosts are contacted. Existing public-DNS,
domain-policy, TLS and single-attempt conventions apply. Redirects, cookies,
inherited proxies and automatic retries are disabled. Each HTTP response has
a byte and time limit. At most two geocoding requests and one forecast request
are made for one weather operation. Cached/indexed modes cannot silently fetch
live weather and are rejected before network work.

Source/HTTP fixture tests and representative direct official-service requests
passed. The first failed query and a later two-part-qualifier regression remain
in private evidence. Those results do not qualify the actual Codex App.
The current installed gateway is r4 with the published CDS 0.6.6 protocol pin;
the changed weather path still requires an explicit installed-version binding
and actual App acceptance. Final r5 qualification is also pending. Private
financial quotes and sports feeds remain separately unconnected cloud services
under the agreed local-runtime scope; they are not required replacement data
adapters and are not counted as functional passes. Other applicable A19 runtime
requirements retain their own gates.

API references: [forecast](https://open-meteo.com/en/docs),
[geocoding](https://open-meteo.com/en/docs/geocoding-api).

The geocoding service can omit administrative fields. City or country-only
queries allow that omission. An explicitly requested area must be verifiable
from returned fields; otherwise the adapter returns 422 instead of guessing.
Upstream HTTP 429 is preserved as 429; source tests do not establish installed
or actual App qualification.
