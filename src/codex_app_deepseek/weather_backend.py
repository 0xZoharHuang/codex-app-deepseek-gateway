"""Fixed Open-Meteo public forecast service; no model or runtime orchestration.
Non-commercial, keyless API. Historical observations are deliberately not inferred
from archived forecasts. Ambiguous place names need a more precise native location.
"""
from __future__ import annotations
import asyncio
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import re
import ssl
import unicodedata
from urllib.parse import urlencode
import aiohttp
from codex_deepseek.windows_asyncio import install_proactor_cleanup_backport
from .web_backend import (
    DomainPolicy, PublicResolver, WebBackendError, _single_attempt,
    checked_url, fail, integer, strict_json, string,
)
GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
DAILY = ("weather_code", "temperature_2m_max", "temperature_2m_min",
         "precipitation_probability_max", "precipitation_sum", "wind_speed_10m_max")
UNITS = {"time": "iso8601", "weather_code": "wmo code",
         "temperature_2m_max": "°C", "temperature_2m_min": "°C",
         "precipitation_probability_max": "%", "precipitation_sum": "mm",
         "wind_speed_10m_max": "km/h"}
# USPS Publication28 AppendixB; USstates/DC only, never applied to othercountries.
# https://pe.usps.com/text/pub28/28apb.htm
US_STATES = dict(part.split(":", 1) for part in (
    "AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|"
    "CT:Connecticut|DE:Delaware|DC:District of Columbia|FL:Florida|GA:Georgia|"
    "HI:Hawaii|ID:Idaho|IL:Illinois|IN:Indiana|IA:Iowa|KS:Kansas|KY:Kentucky|"
    "LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|MI:Michigan|MN:Minnesota|"
    "MS:Mississippi|MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|NH:New Hampshire|"
    "NJ:New Jersey|NM:New Mexico|NY:New York|NC:North Carolina|ND:North Dakota|"
    "OH:Ohio|OK:Oklahoma|OR:Oregon|PA:Pennsylvania|RI:Rhode Island|"
    "SC:South Carolina|SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|VT:Vermont|"
    "VA:Virginia|WA:Washington|WV:West Virginia|WI:Wisconsin|WY:Wyoming"
).split("|"))


def _normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c)).strip()


def validate_weather(value):
    string(value.get("location"), "weather location", 512)
    if any(ord(c) < 32 for c in value["location"]):
        fail("invalid_request", "Weather location contains control characters")
    parts = [p.strip() for p in value["location"].split(",")]
    if len(parts) not in {1, 2, 3} or any(not p for p in parts):
        fail("invalid_weather_location", "Use a place, City,Qualifier, or Country,Area,City", 422)
    if len(parts) == 2 and re.fullmatch(r"[A-Za-z]{2}", parts[1]):
        fail("weather_location_ambiguous", "A two-letter qualifier can mean a country or an area; use Country,Area,City", 422)
    duration = value.get("duration")
    integer(7 if duration is None else duration, "weather duration", 1, 16)
    start = value.get("start")
    if start is not None:
        if not isinstance(start, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
            fail("invalid_weather_date", "Weather start must be YYYY-MM-DD")
        try:
            date.fromisoformat(start)
        except ValueError:
            fail("invalid_weather_date", "Weather start is not a calendar date")
    return parts


def _coordinate(value, maximum):
    return type(value) in {int, float} and math.isfinite(value) and -maximum <= value <= maximum


class OpenMeteoWeather:
    """At most two geocoder GETs and one forecast GET; no retries or fallback."""
    def __init__(self, *, ssl_context=None, clock=None, timeout=25, max_bytes=256 * 1024):
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.timeout, self.max_bytes = timeout, max_bytes
    def validate(self, value, settings, policy):
        validate_weather(value)
        if settings.get("external_web_access", True) is not True and settings.get("external_web_access") != "live":
            fail("unsupported_weather_mode", "Weather forecasts require explicit live external access", 422)
        # Check both fixed services before starting either request.
        checked_url(GEOCODING, policy)
        checked_url(FORECAST, policy)
    async def _json(self, endpoint, parameters, proof):
        url = endpoint + "?" + urlencode(parameters)
        record = {"url": url, "attempts": 1, "response_status": None}
        proof["requests"].append(record)
        install_proactor_cleanup_backport()
        connector = aiohttp.TCPConnector(ssl=self.ssl_context, resolver=PublicResolver(),
                                        use_dns_cache=False)
        try:
            async with aiohttp.ClientSession(connector=connector, trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar(), middlewares=(_single_attempt,),
                    timeout=aiohttp.ClientTimeout(total=self.timeout)) as client:
                async with client.get(url, allow_redirects=False,
                        headers={"Accept": "application/json",
                                 "User-Agent": "CodexAppDeepSeekGateway/0.1 (+Open-Meteo-forecast)"}) as reply:
                    record["response_status"] = reply.status
                    if reply.status != 200:
                        fail("weather_http_error", f"Weather service returned HTTP {reply.status}",
                             429 if reply.status == 429 else 502)
                    if reply.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                        fail("weather_protocol_error", "Weather service did not return JSON", 502)
                    parts, size = [], 0
                    async for part in reply.content.iter_chunked(65536):
                        size += len(part)
                        if size > self.max_bytes:
                            fail("weather_response_too_large", "Weather service response exceeds its byte limit", 502)
                        parts.append(part)
                    raw = b"".join(parts)
                    record["sha256"] = sha256(raw).hexdigest()
                    data = strict_json(raw)
                    if not isinstance(data, dict) or data.get("error"):
                        fail("weather_protocol_error", "Weather service returned an invalid envelope", 502)
                    return data, raw, url
        except asyncio.CancelledError:
            raise
        except WebBackendError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
            fail("weather_network_or_protocol_error", "Weather retrieval failed; no retry was attempted", 502)
    async def forecast(self, value, policy=DomainPolicy(), settings=None):
        settings = settings or {}
        self.validate(value, settings, policy)
        proof = {"backend": "open-meteo", "data_kind": "forecast_not_observation",
                 "automatic_retries": 0, "requests": []}
        try:
            parts = validate_weather(value)
            parameters = {"name": value["location"], "count": 100, "language": "en", "format": "json"}
            country, region, qualifier = None, None, None
            if len(parts) == 3:
                country, region, city = parts
                if _normalized(country) in {"us", "united states"}:
                    region = US_STATES.get(region.upper(), region)
                parameters["name"] = city
                if re.fullmatch(r"[A-Za-z]{2}", country):
                    parameters["name"] = city
                    parameters["countryCode"] = country.upper()
            else:
                city = parts[0]
                qualifier = parts[1] if len(parts) == 2 else None
                parameters["name"] = city
            geo, geo_raw, _ = await self._json(GEOCODING, parameters, proof)
            places = geo.get("results", [])
            if not isinstance(places, list) or len(places) > 100:
                fail("weather_protocol_error", "Geocoder returned an invalid result list", 502)
            if len(places) == 100 and country and "countryCode" not in parameters:
                # A full result page is not complete. Resolve the full country
                # from matching records, then one explicit code refinement.
                matching = [p for p in places if isinstance(p, dict)
                            and isinstance(p.get("name"), str) and isinstance(p.get("country"), str)
                            and _normalized(p["name"]) == _normalized(city)
                            and _normalized(p["country"]) == _normalized(country)]
                if any(not isinstance(p.get("country_code"), str) or not re.fullmatch(r"[A-Z]{2}", p["country_code"]) for p in matching):
                    fail("weather_protocol_error", "Geocoder returned invalid country identities", 502)
                codes = {p["country_code"] for p in matching}
                if len(codes) == 1 and isinstance(next(iter(codes)), str) and re.fullmatch(r"[A-Z]{2}", next(iter(codes))):
                    code = next(iter(codes))
                    geo, geo_raw, _ = await self._json(GEOCODING, {**parameters, "countryCode": code}, proof)
                    proof["country_refinement"] = {"country_code": code, "maximum_refinements": 1}
                    places = geo.get("results", [])
                    if not isinstance(places, list) or len(places) > 100:
                        fail("weather_protocol_error", "Geocoder returned an invalid refined result list", 502)
            if len(places) == 100:
                fail("weather_location_ambiguous", "Weather result page is incomplete; use an ISO country code and full area name", 422)
            candidates, unverifiable_area = {}, False
            for place in places:
                if not isinstance(place, dict) or not isinstance(place.get("name"), str):
                    fail("weather_protocol_error", "Geocoder returned an invalid location", 502)
                if _normalized(place["name"]) != _normalized(city):
                    continue  # Prefix results are not candidates, even when optional metadata is absent.
                if (not all(isinstance(place.get(k), str) and place[k] for k in ("country", "country_code"))
                        or not re.fullmatch(r"[A-Z]{2}", place["country_code"])):
                    fail("weather_protocol_error", "Geocoder returned invalid country identities", 502)
                if country:
                    if re.fullmatch(r"[A-Za-z]{2}", country):
                        if place["country_code"] != country.upper():
                            continue
                    elif _normalized(place["country"]) != _normalized(country):
                        continue
                admin = place.get("admin1")
                if admin is not None and not isinstance(admin, str):
                    fail("weather_protocol_error", "Geocoder returned an invalid administrative area", 502)
                if qualifier and _normalized(qualifier) != _normalized(place["country"]):
                    if not admin:
                        unverifiable_area = True
                        continue
                    if _normalized(qualifier) != _normalized(admin):
                        continue
                if country:
                    if not admin:
                        unverifiable_area = True
                        continue
                    area = _normalized(admin)
                    if _normalized(region) not in {area, area.removeprefix("state of ")}:
                        continue
                if type(place.get("id")) is not int:
                    fail("weather_protocol_error", "Geocoder returned no stable place identity", 502)
                if place["id"] in candidates and candidates[place["id"]] != place:
                    fail("weather_protocol_error", "Geocoder returned conflicting place identities", 502)
                candidates[place["id"]] = place
            if unverifiable_area:
                fail("weather_location_area_unavailable", "A matching place has no administrative area to verify the requested qualifier", 422)
            if not candidates:
                fail("weather_location_not_found", "No exact weather location matched; use Country,Area,City", 404)
            if len(candidates) != 1:
                fail("weather_location_ambiguous", "Multiple weather locations matched; provide Country,Area,City", 422)
            place = next(iter(candidates.values()))
            if (not _coordinate(place.get("latitude"), 90) or not _coordinate(place.get("longitude"), 180)
                    or not isinstance(place.get("timezone"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_+/-]{1,128}", place["timezone"])):
                fail("weather_protocol_error", "Geocoder returned invalid coordinates or timezone", 502)
            # Fetch only current forecasts. Never request past_days or reinterpret an archive as observations.
            params = {"latitude": place["latitude"], "longitude": place["longitude"],
                      "daily": ",".join(DAILY), "timezone": place["timezone"],
                      "forecast_days": 16, "temperature_unit": "celsius",
                      "wind_speed_unit": "kmh", "precipitation_unit": "mm"}
            data, raw, url = await self._json(FORECAST, params, proof)
            if (not _coordinate(data.get("latitude"), 90) or not _coordinate(data.get("longitude"), 180)
                    or abs(data["latitude"] - place["latitude"]) > 1
                    or min(abs(data["longitude"] - place["longitude"]), 360 - abs(data["longitude"] - place["longitude"])) > 1):
                fail("weather_protocol_error", "Forecast grid does not match the requested location", 502)
            offset, now = data.get("utc_offset_seconds"), self.clock()
            if (not isinstance(now, datetime) or type(offset) is not int or not -50400 <= offset <= 50400
                    or data.get("timezone") != place["timezone"] or now.tzinfo is None):
                fail("weather_protocol_error", "Forecast returned an invalid timezone", 502)
            today = now.astimezone(timezone(timedelta(seconds=offset))).date()
            start = date.fromisoformat(value["start"]) if value.get("start") else today
            duration = value.get("duration") if value.get("duration") is not None else 7
            if start < today or start > today + timedelta(days=15) or duration > 16 - (start - today).days:
                fail("unsupported_weather_date_range", "This forecast service covers today through 15 days ahead; historical observations are not configured", 422)
            daily, units = data.get("daily"), data.get("daily_units")
            if not isinstance(daily, dict) or units != UNITS:
                fail("weather_protocol_error", "Forecast fields or units differ from the requested contract", 502)
            expected = [(today + timedelta(days=i)).isoformat() for i in range(16)]
            if daily.get("time") != expected:
                fail("weather_protocol_error", "Forecast dates are incomplete or shifted", 502)
            for key in DAILY:
                values = daily.get(key)
                if not isinstance(values, list) or len(values) != 16:
                    fail("weather_protocol_error", "Forecast arrays are incomplete", 502)
                if any(x is not None and (type(x) not in {int, float} or not math.isfinite(x)) for x in values):
                    fail("weather_protocol_error", "Forecast values are not finite numbers", 502)
            for j in range(16):
                row = {k: daily[k][j] for k in DAILY}
                if (row["weather_code"] is not None and (type(row["weather_code"]) is not int or not 0 <= row["weather_code"] <= 99)
                        or row["precipitation_probability_max"] is not None and not 0 <= row["precipitation_probability_max"] <= 100
                        or any(row[k] is not None and row[k] < 0 for k in ("precipitation_sum", "wind_speed_10m_max"))
                        or row["temperature_2m_min"] is not None and row["temperature_2m_max"] is not None and row["temperature_2m_min"] > row["temperature_2m_max"]):
                    fail("weather_protocol_error", "Forecast values violate their physical units", 502)
            i = (start - today).days
            if any(all(daily[key][j] is None for key in DAILY) for j in range(i, i + duration)):
                fail("weather_data_unavailable", "Weather service has no forecast values for a requested day", 503)
            forecast = {"location": {k: place.get(k) for k in
                        ("id", "name", "admin1", "country", "country_code", "latitude", "longitude", "timezone")},
                        "data_kind": "forecast_not_observation", "daily_units": units,
                        "daily": [{key: daily[key][j] for key in ("time", *DAILY)}
                                  for j in range(i, i + duration)]}
            fetched = now.astimezone(timezone.utc).isoformat()
            label = ", ".join(str(place[k]) for k in ("name", "admin1", "country") if place.get(k))
            proof.update(geocoder_sha256=sha256(geo_raw).hexdigest(), forecast_sha256=sha256(raw).hexdigest(),
                         resolved_location=forecast["location"], retrieved_at=fetched,
                         start=start.isoformat(), duration=duration)
            source = {"url": url, "requested_url": url, "title": "Open-Meteo forecast — " + label,
                      "content_type": "application/json", "retrieved_at": fetched,
                      "source_sha256": sha256(raw).hexdigest(), "lines": [
                          "Weather model data_kind=forecast_not_observation; not observed conditions. Temperatures are Celsius.",
                          "Attribution: Open-Meteo (CC BY 4.0); geocoding data: GeoNames.",
                          "Resolved location: " + json.dumps(forecast["location"], ensure_ascii=False, allow_nan=False),
                          "Daily units: " + json.dumps(units, ensure_ascii=False, allow_nan=False),
                          *[json.dumps(day, ensure_ascii=False, allow_nan=False) for day in forecast["daily"]]],
                      "links": [], "excerpt": False, "page_count": None, "provenance": proof}
            return source, raw, proof
        except WebBackendError as error:
            error.evidence = proof
            raise
