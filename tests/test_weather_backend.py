"""Official weather HTTP contract and native alpha/search dispatch boundaries."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import json
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest
from codex_app_deepseek import weather_backend as w
from codex_app_deepseek.web_backend import (
    DomainPolicy, MODEL, WebBackend, WebBackendError, WebSourceStore, validate_request,
)
NOW = datetime(2026, 10, 28, 23, 30, tzinfo=timezone.utc)
PLACE = {"id": 1795565, "name": "Shenzhen", "admin1": "Guangdong",
         "country": "China", "country_code": "CN", "latitude": 22.54554,
         "longitude": 114.0683, "timezone": "Asia/Shanghai"}
def source_data(source):
    return {"location": json.loads(source["lines"][2].split(": ", 1)[1]),
            "daily_units": json.loads(source["lines"][3].split(": ", 1)[1]),
            "daily": [json.loads(line) for line in source["lines"][4:]]}
def forecast_data():
    days = [(datetime(2026, 10, 29) + timedelta(days=i)).date().isoformat() for i in range(16)]
    return {"timezone": "Asia/Shanghai", "utc_offset_seconds": 28800,
            "latitude": 22.55, "longitude": 114.07,
            "daily_units": deepcopy(w.UNITS),
            "daily": {"time": days, "weather_code": [3] * 16,
                      "temperature_2m_max": [29.0 + i / 10 for i in range(16)],
                      "temperature_2m_min": [22.1] * 16,
                      "precipitation_probability_max": [25] * 16,
                      "precipitation_sum": [0.3] * 16,
                      "wind_speed_10m_max": [11.5] * 16}}
@pytest.fixture
async def service(monkeypatch):
    state = {"geo": {"results": [deepcopy(PLACE)]}, "forecast": forecast_data(),
             "requests": [], "status": 200, "kind": "application/json", "raw": None, "geo_by_country": None}
    async def reply(request):
        state["requests"].append((request.path, dict(request.query)))
        value = state["geo"] if request.path == "/geo" else state["forecast"]
        if request.path == "/geo" and request.query.get("countryCode") and state["geo_by_country"] is not None:
            value = state["geo_by_country"]
        raw = state["raw"] if state["raw"] is not None else json.dumps(value).encode()
        return web.Response(body=raw, status=state["status"], content_type=state["kind"])
    app = web.Application()
    app.router.add_get("/geo", reply); app.router.add_get("/forecast", reply)
    async with TestServer(app) as server:
        monkeypatch.setattr(w, "GEOCODING", str(server.make_url("/geo")))
        monkeypatch.setattr(w, "FORECAST", str(server.make_url("/forecast")))
        monkeypatch.setattr(w, "PublicResolver", aiohttp.resolver.DefaultResolver)
        # Only the test's two fixed local endpoints bypass public-IP admission.
        original = w.checked_url
        def check(url, policy):
            if url in {w.GEOCODING, w.FORECAST}:
                if not policy.accepts("api.open-meteo.com") or not policy.accepts("geocoding-api.open-meteo.com"):
                    raise WebBackendError("domain_blocked", "Test service denied by domain policy", 403)
                return url
            return original(url, policy)
        monkeypatch.setattr(w, "checked_url", check)
        yield state, w.OpenMeteoWeather(clock=lambda: NOW)
async def test_native_three_part_country_region_and_location_local_default_day(service):
    state, backend = service
    source, raw, proof = await backend.forecast({"location": "China,Guangdong,Shenzhen"})
    assert len(state["requests"]) == 2
    assert state["requests"][0] == ("/geo", {"name": "Shenzhen", "count": "100",
                                          "language": "en", "format": "json"})
    params = state["requests"][1][1]
    assert params["timezone"] == "Asia/Shanghai" and params["forecast_days"] == "16"
    assert not {"past_days", "start_date", "end_date"} & params.keys()
    data = source_data(source)
    assert len(data["daily"]) == 7
    assert data["daily"][0]["time"] == "2026-10-29"  # UTC is still October28.
    assert data["daily"][-1]["time"] == "2026-11-04"
    assert data["daily_units"]["temperature_2m_max"] == "°C"
    assert data["location"]["id"] == PLACE["id"]
    assert proof["data_kind"] == "forecast_not_observation" and proof["automatic_retries"] == 0
    assert proof["requests"][1]["sha256"] == source["source_sha256"]
    assert json.loads(raw) == state["forecast"]
async def test_country_code_is_explicit_and_future_inclusive_slice(service):
    state, backend = service
    source, _, _ = await backend.forecast({"location": "CN,Guangdong,Shenzhen",
                                           "start": "2026-11-11", "duration": 3})
    assert state["requests"][0][1]["countryCode"] == "CN"
    data = source_data(source)
    assert [x["time"] for x in data["daily"]] == ["2026-11-11", "2026-11-12", "2026-11-13"]
@pytest.mark.parametrize("change,code", [
    ({"results": [PLACE, {**PLACE, "id": 2}]}, "weather_location_ambiguous"),
    ({"results": [{**PLACE, "country": "Other"}]}, "weather_location_not_found"),
    ({"results": [{**PLACE, "admin1": "Other"}]}, "weather_location_not_found"),
    ({"results": [{**PLACE, "name": "Shenzhen-other"}]}, "weather_location_not_found"),
    ({"results": []}, "weather_location_not_found"),
    ({"results": [{**PLACE, "latitude": 190}]}, "weather_protocol_error"),
    ({"results": [{**PLACE, "country": None}]}, "weather_protocol_error"),
    ({"results": [PLACE, {**PLACE, "latitude": 20}]}, "weather_protocol_error"),
])
async def test_geocoder_never_selects_first_wrong_homonym_or_prefix(service, change, code):
    state, backend = service; state["geo"] = change
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "China,Guangdong,Shenzhen"})
    assert error.value.code == code and len(state["requests"]) == 1
@pytest.mark.parametrize("location", ["Shenzhen", "Shenzhen,China", "Shenzhen,Guangdong"])
async def test_unambiguous_bare_and_documented_city_qualifier(service, location):
    state, backend = service
    source, _, _ = await backend.forecast({"location": location, "duration": 1})
    assert len(source_data(source)["daily"]) == 1
@pytest.mark.parametrize("value", [
    {"location": ""}, {"location": "A,,B"}, {"location": "A,B,C,D"}, {"location": "San Francisco,CA"},
    {"location": "A\nB"}, {"location": "Shenzhen", "duration": 0},
    {"location": "Shenzhen", "duration": 17}, {"location": "Shenzhen", "duration": True},
    {"location": "Shenzhen", "start": "2026-02-30"},
    {"location": "Shenzhen", "start": "2026-1-2"},
])
async def test_bad_native_input_fails_before_network(service, value):
    state, backend = service
    with pytest.raises(WebBackendError):
        await backend.forecast(value)
    assert not state["requests"]
@pytest.mark.parametrize("settings", [{"external_web_access": False},
                                      {"external_web_access": "cached"},
                                      {"external_web_access": "indexed"}])
async def test_nonlive_modes_never_fetch_live_weather(service, settings):
    state, backend = service
    with pytest.raises(WebBackendError, match="live external"):
        await backend.forecast({"location": "Shenzhen"}, settings=settings)
    assert not state["requests"]
async def test_domain_policy_checked_for_both_services_before_any_request(service):
    state, backend = service
    with pytest.raises(WebBackendError):
        await backend.forecast({"location": "Shenzhen"}, DomainPolicy(blocked=("open-meteo.com",)))
    assert not state["requests"]
@pytest.mark.parametrize("start,duration", [("2026-10-28", 1), ("2026-11-13", 2), ("9999-12-31", 16)])
async def test_no_historical_or_beyond_horizon_forecast_is_presented(service, start, duration):
    state, backend = service
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "Shenzhen", "start": start, "duration": duration})
    assert error.value.code == "unsupported_weather_date_range"
    assert "past_days" not in state["requests"][1][1]
@pytest.mark.parametrize("mutation", ["wrong_unit", "wrong_timezone", "date_shift", "array_length", "nonnumeric",
                                      "wrong_grid", "missing_grid", "bad_probability", "bad_temperature", "negative_wind"])
async def test_provider_200_cannot_hide_malformed_or_shifted_forecast(service, mutation):
    state, backend = service; data = state["forecast"]
    if mutation == "wrong_unit": data["daily_units"]["temperature_2m_max"] = "°F"
    if mutation == "wrong_timezone": data["timezone"] = "UTC"
    if mutation == "date_shift": data["daily"]["time"][0] = "2026-10-28"
    if mutation == "array_length": data["daily"]["weather_code"].pop()
    if mutation == "nonnumeric": data["daily"]["temperature_2m_max"][0] = "hot"
    if mutation == "wrong_grid": data["latitude"] = 50
    if mutation == "missing_grid": data.pop("latitude")
    if mutation == "bad_probability": data["daily"]["precipitation_probability_max"][0] = 200
    if mutation == "bad_temperature": data["daily"]["temperature_2m_min"][0] = 50
    if mutation == "negative_wind": data["daily"]["wind_speed_10m_max"][0] = -1
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "Shenzhen"})
    assert error.value.code == "weather_protocol_error"
    assert error.value.evidence["requests"][-1]["response_status"] == 200
@pytest.mark.parametrize("status,reported_status", [(301, 502), (429, 429), (503, 502)])
async def test_http_failure_is_single_attempt_no_redirect_or_fallback(service, status, reported_status):
    state, backend = service; state["status"] = status
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "Shenzhen"})
    assert error.value.code == "weather_http_error"
    assert error.value.status == reported_status
    assert len(state["requests"]) == 1
    assert error.value.evidence["requests"][0]["response_status"] == status
@pytest.mark.parametrize("raw,kind,code", [
    (b'{"results":[],"results":[]}', "application/json", "invalid_provider_json"),
    (b'{"results":NaN}', "application/json", "invalid_provider_json"),
    (b"<html>private provider error body</html>", "text/html", "weather_protocol_error"),
])
async def test_provider_parser_is_strict_and_errors_do_not_echo_body(service, raw, kind, code):
    state, backend = service; state.update(raw=raw, kind=kind)
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "Shenzhen"})
    assert error.value.code == code and "private provider error" not in error.value.message
async def test_real_http_native_dispatch_uses_existing_receipt_and_reference_store(service, tmp_path):
    state, backend = service
    class NoCalls:
        async def search(self, *args): raise AssertionError("No model/search fallback")
        async def fetch(self, *args): raise AssertionError("No document fallback")
    store = WebSourceStore(tmp_path / "web.sqlite")
    try:
        native = WebBackend(store, NoCalls(), NoCalls(), clock=lambda: NOW, weather_backend=backend)
        response = await native.handle({"id": "weather-test", "model": MODEL,
            "commands": {"weather": [{"location": "China,Guangdong,Shenzhen", "duration": 2}],
                         "response_length": "long"}})
        assert len(response["results"]) == 1
        result = response["results"][0]
        source = store.get("weather-test", result["ref_id"])
        assert source["provenance"]["backend"] == "open-meteo"
        assert source["provenance"]["duration"] == 2
        assert json.loads(store.raw("weather-test", result["ref_id"])) == state["forecast"]
        assert "forecast_not_observation" in response["output"]
        row = store.db.execute("SELECT operation,outcome,evidence FROM web_receipts").fetchone()
        assert row[0:2] == ("weather", "completed")
        assert json.loads(row[2])["source_refs"] == [result["ref_id"]]
    finally:
        store.close()
def test_native_schema_rejects_unknown_weather_fields_and_keeps_other_cloud_gates():
    for value in [{"location": "Shenzhen", "temperature_unit": "fahrenheit"},
                  {"location": "Shenzhen", "duration": False}]:
        with pytest.raises(WebBackendError):
            validate_request({"id": "test", "model": MODEL, "commands": {"weather": [value]}}, (MODEL,))
    for name in ["finance", "sports"]:
        with pytest.raises(WebBackendError) as error:
            validate_request({"id": "test", "model": MODEL, "commands": {name: [{}]}}, (MODEL,))
        assert error.value.status == 501
async def test_two_part_full_qualifier_is_not_ignored(service):
    state, backend = service
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "Shenzhen,Canada"})
    assert error.value.code == "weather_location_not_found"
    assert len(state["requests"]) == 1 and state["requests"][0][1]["name"] == "Shenzhen"
async def test_full_country_cap_requires_one_explicit_narrowing_and_complete_result_page(service):
    state, backend = service
    state["geo"] = {"results": [PLACE, *[{**PLACE, "id": i + 100, "country": "Other", "country_code": "ZZ"} for i in range(99)]]}
    state["geo_by_country"] = {"results": [PLACE]}
    source, _, proof = await backend.forecast({"location": "China,Guangdong,Shenzhen", "duration": 1})
    assert len(state["requests"]) == 3
    assert state["requests"][1][1]["countryCode"] == "CN"
    assert proof["country_refinement"] == {"country_code": "CN", "maximum_refinements": 1}
    assert source_data(source)["location"]["id"] == PLACE["id"]
async def test_full_and_iso_country_caps_never_assume_hidden_matches_are_absent(service):
    state, backend = service
    state["geo"] = {"results": [{**PLACE, "id": i + 100} for i in range(100)]}
    state["geo_by_country"] = state["geo"]
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "China,Guangdong,Shenzhen"})
    assert error.value.code == "weather_location_ambiguous" and len(state["requests"]) == 2
    state["requests"].clear()
    with pytest.raises(WebBackendError):
        await backend.forecast({"location": "CN,Guangdong,Shenzhen"})
    assert len(state["requests"]) == 1
async def test_native_usps_area_abbreviation_is_country_scoped_and_visible(service):
    state, backend = service
    place = {**PLACE, "name": "San Francisco", "country": "United States",
             "country_code": "US", "admin1": "California"}
    state["geo"] = {"results": [place]}
    source, _, _ = await backend.forecast({"location": "US,CA,San Francisco", "duration": 1})
    assert source_data(source)["location"]["admin1"] == "California"
    assert state["requests"][0][1]["countryCode"] == "US"
    state["requests"].clear()
    state["geo"] = {"results": [{**place, "country": "Canada", "country_code": "CA"}]}
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "CA,CA,San Francisco"})
    assert error.value.code == "weather_location_not_found"
async def test_explicit_state_of_label_is_not_a_different_administrative_area(service):
    state, backend = service
    state["geo"] = {"results": [{**PLACE, "admin1": "State of Guangdong"}]}
    source, _, _ = await backend.forecast({"location": "China,Guangdong,Shenzhen"})
    assert source_data(source)["location"]["admin1"] == "State of Guangdong"
async def test_requested_all_null_day_is_unavailable_not_success(service):
    state, backend = service
    for key in w.DAILY:
        state["forecast"]["daily"][key][0] = None
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "Shenzhen", "duration": 1})
    assert error.value.code == "weather_data_unavailable" and error.value.status == 503
async def test_truncated_country_identity_cannot_supply_untyped_country_code(service):
    state, backend = service
    state["geo"] = {"results": [{**PLACE, "id": i + 100, "country_code": []} for i in range(100)]}
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "China,Guangdong,Shenzhen"})
    assert error.value.code == "weather_protocol_error" and len(state["requests"]) == 1

@pytest.mark.parametrize("missing", ["admin1", "country", "country_code"])
async def test_unrelated_geocoder_prefix_with_omitted_optional_fields_does_not_poison_exact_match(service, missing):
    state, backend = service
    other = {**PLACE, "id": 2, "name": "Shenzhen-other"}
    other.pop(missing)
    state["geo"] = {"results": [other, PLACE]}
    source, _, _ = await backend.forecast({"location": "China,Guangdong,Shenzhen", "duration": 1})
    assert source_data(source)["location"]["id"] == PLACE["id"]
    assert len(state["requests"]) == 2

@pytest.mark.parametrize("location", ["Shenzhen", "Shenzhen,China"])
@pytest.mark.parametrize("admin", [None, ""])
async def test_country_or_city_only_location_does_not_require_optional_admin1(service, location, admin):
    state, backend = service
    place = dict(PLACE)
    if admin is None:
        place.pop("admin1")
    else:
        place["admin1"] = admin
    state["geo"] = {"results": [place]}
    source, _, _ = await backend.forecast({"location": location, "duration": 1})
    assert source_data(source)["location"]["id"] == PLACE["id"]
    assert not source_data(source)["location"]["admin1"]
    assert len(state["requests"]) == 2

@pytest.mark.parametrize("location", ["China,Guangdong,Shenzhen", "Shenzhen,Guangdong"])
@pytest.mark.parametrize("also_verified", [False, True])
async def test_explicit_area_missing_from_matching_place_cannot_be_assumed_or_guessed(service, location, also_verified):
    state, backend = service
    unknown_area = {**PLACE, "id": 2}
    unknown_area.pop("admin1")
    state["geo"] = {"results": ([PLACE] if also_verified else []) + [unknown_area]}
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": location, "duration": 1})
    assert error.value.code == "weather_location_area_unavailable"
    assert error.value.status == 422 and len(state["requests"]) == 1

@pytest.mark.parametrize("admin", [False, 123, [], {}])
async def test_present_nonstring_admin_field_is_still_malformed_for_selected_city(service, admin):
    state, backend = service
    state["geo"] = {"results": [{**PLACE, "admin1": admin}]}
    with pytest.raises(WebBackendError) as error:
        await backend.forecast({"location": "Shenzhen", "duration": 1})
    assert error.value.code == "weather_protocol_error"
    assert error.value.status == 502 and len(state["requests"]) == 1
