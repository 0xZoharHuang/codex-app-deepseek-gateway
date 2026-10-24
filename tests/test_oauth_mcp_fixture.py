"""HTTP self-tests for the isolated synthetic OAuth fixture; never use an OS vault."""
from __future__ import annotations
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import psutil
import pytest

SERVER = Path(__file__).parent / "fixtures/oauth_mcp/server.py"

def check(value, message):
    if not value:
        raise AssertionError(message)

@pytest.fixture(scope="module")
def fixture():
    process = subprocess.Popen([sys.executable, "-I", "-X", "utf8", str(SERVER),
        "--access-ttl", "2", "--lifetime", "90"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf8",
        env={k: os.environ[k] for k in ("SystemRoot", "WINDIR", "PATH") if k in os.environ})
    owned = psutil.Process(process.pid)
    birth = owned.create_time()
    descriptor = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            line = pool.submit(process.stdout.readline).result(timeout=10)
        descriptor = json.loads(line)
        check(descriptor.get("fixture") == "cdag-oauth", "Fixture did not publish its safe descriptor")
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            deadline = time.monotonic() + 5
            while True:
                try:
                    ready = client.get(descriptor["base"] + "/.well-known/oauth-authorization-server")
                    if ready.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                check(time.monotonic() < deadline, "Fixture did not become ready")
                time.sleep(.05)
            yield descriptor, client
    finally:
        if process.poll() is None:
            try:
                process.stdin.write("q\n"); process.stdin.flush()
                process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                # Exact parent/descendant ownership only, all within this synthetic fixture.
                if owned.is_running() and owned.create_time() == birth:
                    children = [(p, p.create_time()) for p in owned.children(recursive=True)]
                    for child, created in reversed(children):
                        try:
                            if child.create_time() == created and str(SERVER) in " ".join(child.cmdline()):
                                child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    process.kill(); process.wait(timeout=5)
        output, errors = process.communicate(timeout=5)
        # The fixture has no access logs or token-bearing exception tracebacks.
        check(errors == "", "Fixture emitted unexpected stderr; no raw diagnostics may be published")
        if descriptor:
            try:
                with httpx.Client(timeout=.3, trust_env=False) as client:
                    client.get(descriptor["base"] + "/.well-known/oauth-authorization-server")
            except httpx.HTTPError:
                pass
            else:
                raise AssertionError("Owned fixture listener survived shutdown")

def register(fixture, redirect="http://127.0.0.1:43219/callback"):
    descriptor, client = fixture
    result = client.post(descriptor["base"] + "/register", json={
        "redirect_uris":[redirect], "token_endpoint_auth_method":"none",
        "grant_types":["authorization_code","refresh_token"], "response_types":["code"],
        "scope":"fixture:read", "client_name":"synthetic local test"})
    check(result.status_code == 201, "Synthetic client registration failed")
    return result.json(), redirect

def authorization(fixture, client_info, redirect, **changes):
    descriptor, client = fixture
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(24)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    params = {"client_id":client_info["client_id"],"redirect_uri":redirect,"response_type":"code",
        "code_challenge":challenge,"code_challenge_method":"S256","state":state,
        "scope":"fixture:read","resource":descriptor["mcp_url"]}
    params.update(changes)
    return client.get(descriptor["base"]+"/authorize", params=params), verifier, state

def consent(fixture, authorization_response, decision="allow"):
    descriptor, client = fixture
    check(authorization_response.status_code == 302, "Authorize did not redirect to local consent")
    location = authorization_response.headers["location"]
    check(location.startswith(descriptor["base"]+"/fixture/"), "Consent left fixture origin")
    page = client.get(location)
    check(page.status_code == 200, "Consent page unavailable")
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    check(match is not None, "Consent page omitted CSRF binding")
    result = client.post(location, data={"csrf":match.group(1),"decision":decision},
                         headers={"Origin":descriptor["base"]})
    check(result.status_code == 302, "Consent did not finish through callback")
    return result, location

def grant(fixture, client_info, redirect):
    response, verifier, state = authorization(fixture,client_info,redirect)
    result, _ = consent(fixture,response)
    parsed=urlsplit(result.headers["location"])
    values=parse_qs(parsed.query)
    check(values.get("state")==[state], "OAuth state changed")
    check(values.get("iss")==[fixture[0]["base"]], "Issuer binding missing")
    return values["code"][0], verifier

def exchange(fixture, client_info, redirect, code, verifier, **changes):
    params={"grant_type":"authorization_code","client_id":client_info["client_id"],
        "code":code,"code_verifier":verifier,"redirect_uri":redirect,"resource":fixture[0]["mcp_url"]}
    params.update(changes)
    return fixture[1].post(fixture[0]["base"]+"/token",data=params)

def refresh(fixture, client_info, refresh_token, **changes):
    params={"grant_type":"refresh_token","client_id":client_info["client_id"],
        "refresh_token":refresh_token,"resource":fixture[0]["mcp_url"]}
    params.update(changes)
    return fixture[1].post(fixture[0]["base"]+"/token",data=params)

async def call_mcp(url, access_token):
    async with httpx.AsyncClient(headers={"Authorization":"Bearer "+access_token},trust_env=False) as http:
        async with streamable_http_client(url,http_client=http) as streams:
            async with ClientSession(streams[0],streams[1],read_timeout_seconds=5) as session:
                initialized=await session.initialize()
                check(initialized.server_info.name=="CDAG synthetic OAuth","Unexpected MCP server")
                tools=await session.list_tools()
                check([x.name for x in tools.tools]==["read_reference"],"Unexpected OAuth tools")
                result=await session.call_tool("read_reference",{})
                check(result.is_error is False and result.structured_content["identity"]=="synthetic-fixture-user",
                      "Authorized read-only MCP call failed")

def test_discovery_and_invalid_registration_and_boundary(fixture):
    descriptor,client=fixture
    missing=client.get(descriptor["mcp_url"])
    check(missing.status_code==401,"Anonymous MCP access was not denied")
    challenge=missing.headers.get("www-authenticate","")
    check("resource_metadata=" in challenge,"Protected resource metadata omitted")
    metadata=client.get(descriptor["base"]+"/.well-known/oauth-protected-resource"+urlsplit(descriptor["mcp_url"]).path)
    check(metadata.status_code==200 and metadata.json()["resource"]==descriptor["mcp_url"],"Resource metadata mismatch")
    server=client.get(descriptor["base"]+"/.well-known/oauth-authorization-server").json()
    check(server["code_challenge_methods_supported"]==["S256"],"S256 not advertised exclusively")
    check("revocation_endpoint" in server,"Revocation metadata absent")
    for redirect in ["https://example.com/callback","http://127.0.0.1:43219/other",
                     "http://127.0.0.1:43219/callback?next=https://example.com",
                     "http://user@127.0.0.1:43219/callback"]:
        result=client.post(descriptor["base"]+"/register",json={"redirect_uris":[redirect],
            "token_endpoint_auth_method":"none","grant_types":["authorization_code"],"response_types":["code"]})
        check(result.status_code==400,"Unsafe registration redirect was accepted")
    result=client.get(descriptor["base"]+"/.well-known/oauth-authorization-server",headers={"Host":"foreign.invalid"})
    check(result.status_code==400,"Foreign Host was accepted")
    result=client.post(descriptor["base"]+"/register",content=b"x"*65537,headers={"Content-Type":"application/json"})
    check(result.status_code==413,"Oversized registration body was accepted")

def test_deny_csrf_plain_pkce_state_and_exact_redirect(fixture):
    info,redirect=register(fixture)
    response,_,state=authorization(fixture,info,redirect)
    denied,location=consent(fixture,response,"deny")
    values=parse_qs(urlsplit(denied.headers["location"]).query)
    check(values.get("error")==["access_denied"] and values.get("state")==[state],"Deny/state contract failed")
    check(fixture[1].get(location).status_code==400,"Consent ticket was reusable")
    response,_,_=authorization(fixture,info,redirect)
    location=response.headers["location"]
    fixture[1].get(location)
    rejected=fixture[1].post(location,data={"csrf":"wrong","decision":"allow"})
    check(rejected.status_code==400,"Invalid consent CSRF was accepted")
    for changes in [{"code_challenge_method":"plain"},{"redirect_uri":"http://127.0.0.1:43220/callback"},
                    {"resource":fixture[0]["mcp_url"]+"/other"},{"state":""}]:
        response,_,_=authorization(fixture,info,redirect,**changes)
        check(response.status_code==400 or (response.status_code==302 and "error=" in response.headers.get("location","")),
              "Invalid authorization binding was accepted")
        check("/fixture/" not in response.headers.get("location",""),"Invalid authorization reached consent")

def test_pkce_client_redirect_resource_single_use_and_real_mcp(fixture):
    info,redirect=register(fixture)
    other,_=register(fixture)
    code,verifier=grant(fixture,info,redirect)
    attempts=[
        exchange(fixture,info,redirect,code,"x"*64),
        exchange(fixture,other,redirect,code,verifier),
        exchange(fixture,info,redirect+"/changed",code,verifier),
        exchange(fixture,info,redirect,code,verifier,resource=fixture[0]["mcp_url"]+"/other"),
    ]
    check(all(r.status_code==400 for r in attempts),"A mismatched grant binding was accepted")
    successful=exchange(fixture,info,redirect,code,verifier)
    check(successful.status_code==200,"Valid PKCE grant failed")
    token=successful.json()
    check(exchange(fixture,info,redirect,code,verifier).status_code==400,"Authorization code was replayed")
    asyncio.run(call_mcp(fixture[0]["mcp_url"],token["access_token"]))
    safe=fixture[1].get(fixture[0]["events_url"]).text
    check(all(value not in safe for value in [code,verifier,token["access_token"],token["refresh_token"]]),
          "Safe diagnostics exposed OAuth material")
    events=json.loads(safe)["events"]
    check(all(set(event)=={"event","at","nonce_sha256"} for event in events),"Unexpected diagnostic fields")

def test_expiry_refresh_rotation_revocation_and_unknown_token(fixture):
    info,redirect=register(fixture)
    code,verifier=grant(fixture,info,redirect)
    result=exchange(fixture,info,redirect,code,verifier)
    check(result.status_code==200,"Initial grant failed")
    first=result.json()
    time.sleep(3.1)
    expired=fixture[1].get(fixture[0]["mcp_url"],headers={"Authorization":"Bearer "+first["access_token"]})
    check(expired.status_code==401,"Expired token was accepted")
    wrong=refresh(fixture,info,first["refresh_token"],resource=fixture[0]["mcp_url"]+"/other")
    check(wrong.status_code==400,"Refresh resource escalation was accepted")
    renewed=refresh(fixture,info,first["refresh_token"])
    check(renewed.status_code==200,"Refresh failed")
    second=renewed.json()
    check(second["access_token"]!=first["access_token"] and second["refresh_token"]!=first["refresh_token"],
          "Refresh did not rotate both tokens")
    check(refresh(fixture,info,first["refresh_token"]).status_code==400,"Rotated refresh token was replayed")
    asyncio.run(call_mcp(fixture[0]["mcp_url"],second["access_token"]))
    revoked=fixture[1].post(fixture[0]["base"]+"/revoke",data={"token":second["refresh_token"],
        "token_type_hint":"refresh_token","client_id":info["client_id"],"client_secret":""})
    check(revoked.status_code==200,"Standard revocation failed")
    check(refresh(fixture,info,second["refresh_token"]).status_code==400,"Revoked refresh still worked")
    denied=fixture[1].get(fixture[0]["mcp_url"],headers={"Authorization":"Bearer "+second["access_token"]})
    check(denied.status_code==401,"Paired access token survived revocation")
    unknown=fixture[1].post(fixture[0]["base"]+"/revoke",data={"token":"unknown-synthetic-value",
        "client_id":info["client_id"],"client_secret":""})
    check(unknown.status_code==200,"Unknown token revocation was not idempotent")

def test_real_loopback_callback_and_no_implicit_redirect(fixture):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    received = []
    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(parse_qs(urlsplit(self.path).query))
            self.send_response(200); self.end_headers(); self.wfile.write(b"synthetic callback received")
        def log_message(self, *args):
            pass
    callback=ThreadingHTTPServer(("127.0.0.1",0),Callback)
    thread=threading.Thread(target=callback.serve_forever,daemon=True);thread.start()
    try:
        redirect=f"http://127.0.0.1:{callback.server_port}/callback"
        info,_=register(fixture,redirect)
        response,verifier,state=authorization(fixture,info,redirect)
        result,_=consent(fixture,response)
        location=result.headers["location"]
        check(location.startswith(redirect+"?"),"Consent selected a different callback")
        response=fixture[1].get(location)
        check(response.status_code==200 and len(received)==1,"Real owned callback was not reached")
        check(received[0].get("state")==[state],"State did not arrive unchanged")
        token=exchange(fixture,info,redirect,received[0]["code"][0],verifier)
        check(token.status_code==200,"Code from real callback failed exchange")
        asyncio.run(call_mcp(fixture[0]["mcp_url"],token.json()["access_token"]))
    finally:
        callback.shutdown();callback.server_close();thread.join(timeout=3)
        check(not thread.is_alive(),"Owned callback server did not stop")

def test_token_scope_duplicate_parameters_and_invalid_utf8(fixture):
    info,redirect=register(fixture)
    code,verifier=grant(fixture,info,redirect)
    token=exchange(fixture,info,redirect,code,verifier)
    check(token.status_code==200,"Initial grant failed")
    old=token.json()
    rejected=refresh(fixture,info,old["refresh_token"],scope="fixture:write")
    check(rejected.status_code==400,"Refresh scope escalation was accepted")
    duplicate=fixture[1].post(fixture[0]["base"]+"/token",
        content="resource="+fixture[0]["mcp_url"]+"&resource=other",
        headers={"Content-Type":"application/x-www-form-urlencoded"})
    check(duplicate.status_code==400,"Duplicate security field was accepted")
    malformed=fixture[1].post(fixture[0]["base"]+"/token",content=b"resource=\xff",
        headers={"Content-Type":"application/x-www-form-urlencoded"})
    check(malformed.status_code==400,"Malformed UTF8 was not rejected as an input error")
    other,_=register(fixture)
    foreign=fixture[1].post(fixture[0]["base"]+"/revoke",data={"token":old["refresh_token"],
        "client_id":other["client_id"],"client_secret":""})
    check(foreign.status_code==200,"Foreign revocation should not reveal token ownership")
    still_valid=refresh(fixture,info,old["refresh_token"])
    check(still_valid.status_code==200,"Other client revoked this client's tokens")

def test_expired_access_revocation_removes_paired_refresh(fixture):
    info,redirect=register(fixture)
    code,verifier=grant(fixture,info,redirect)
    result=exchange(fixture,info,redirect,code,verifier)
    check(result.status_code==200,"Initial grant failed")
    token=result.json()
    time.sleep(3.1)
    revoked=fixture[1].post(fixture[0]["base"]+"/revoke",data={"token":token["access_token"],
        "token_type_hint":"access_token","client_id":info["client_id"],"client_secret":""})
    check(revoked.status_code==200,"Expired access revocation request failed")
    check(refresh(fixture,info,token["refresh_token"]).status_code==400,
          "Revoking known expired access did not revoke its paired refresh")


def test_provider_expired_code_and_refresh_and_nonce_independence():
    spec=importlib.util.spec_from_file_location("oauth_fixture_unit",SERVER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    from mcp.server.auth.provider import AuthorizationCode, TokenError
    from mcp.shared.auth import OAuthClientInformationFull
    async def scenario():
        first=module.FixtureProvider("http://127.0.0.1:43219","a"*32,60)
        second=module.FixtureProvider("http://127.0.0.1:43219","b"*32,60)
        client=OAuthClientInformationFull(client_id="synthetic-client",
            redirect_uris=["http://127.0.0.1:43220/callback"],token_endpoint_auth_method="none",
            grant_types=["authorization_code","refresh_token"],response_types=["code"],scope="fixture:read")
        code=AuthorizationCode(code="synthetic-expired-code",client_id=client.client_id,
            scopes=["fixture:read"],expires_at=time.time()-1,code_challenge="x"*43,
            redirect_uri=client.redirect_uris[0],redirect_uri_provided_explicitly=True,
            resource=first.resource,subject="synthetic-fixture-user")
        first.codes[code.code]=code
        with pytest.raises(TokenError):
            await first.exchange_authorization_code(client,code)
        token=first.issue(client.client_id,["fixture:read"])
        check(await second.load_access_token(token.access_token) is None,"Nonce instances shared tokens")
        record=first.refresh[token.refresh_token]
        record.expires_at=int(time.time())-1
        with pytest.raises(TokenError):
            await first.exchange_refresh_token(client,record,["fixture:read"])
    asyncio.run(scenario())

@pytest.mark.parametrize("method", ["client_secret_post", "client_secret_basic"])
def test_sdk_confidential_client_authentication(fixture, method):
    descriptor, client = fixture
    redirect = "http://127.0.0.1:43219/callback"
    registration=client.post(descriptor["base"]+"/register",json={
        "redirect_uris":[redirect],"token_endpoint_auth_method":method,
        "grant_types":["authorization_code","refresh_token"],"response_types":["code"],"scope":"fixture:read"})
    check(registration.status_code==201,"Confidential fixture registration failed")
    info=registration.json()
    code,verifier=grant(fixture,info,redirect)
    data={"grant_type":"authorization_code","client_id":info["client_id"],
          "redirect_uri":redirect,"code":code,"code_verifier":verifier,"resource":descriptor["mcp_url"]}
    if method=="client_secret_post":
        wrong=client.post(descriptor["base"]+"/token",data={**data,"client_secret":"incorrect"})
        correct=client.post(descriptor["base"]+"/token",data={**data,"client_secret":info["client_secret"]})
    else:
        wrong=client.post(descriptor["base"]+"/token",data=data,auth=(info["client_id"],"incorrect"))
        correct=client.post(descriptor["base"]+"/token",data=data,auth=(info["client_id"],info["client_secret"]))
    check(wrong.status_code==401,"Incorrect client secret was accepted")
    check(correct.status_code==200,"Valid standard client authentication failed")
    token=correct.json()
    asyncio.run(call_mcp(descriptor["mcp_url"],token["access_token"]))
    safe=client.get(descriptor["events_url"]).text
    check(info["client_secret"] not in safe,"Diagnostics exposed client secret")

def test_concurrent_consent_consumes_ticket_once_with_explicit_error():
    spec=importlib.util.spec_from_file_location("oauth_fixture_race",SERVER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    from mcp.shared.auth import OAuthClientInformationFull
    from mcp.server.auth.provider import AuthorizationParams
    async def scenario():
        provider=module.FixtureProvider("http://127.0.0.1:43219","d"*32,60)
        client=OAuthClientInformationFull(client_id="synthetic",
            redirect_uris=["http://127.0.0.1:43220/callback"],token_endpoint_auth_method="none",
            grant_types=["authorization_code"],response_types=["code"],scope="fixture:read")
        params=AuthorizationParams(state="synthetic-state",scopes=["fixture:read"],code_challenge="x"*43,
            redirect_uri=client.redirect_uris[0],redirect_uri_provided_explicitly=True,resource=provider.resource)
        location=await provider.authorize(client,params)
        ticket=location.rsplit("/",1)[1]
        csrf=provider.pending[ticket]["csrf"]
        ready=asyncio.Event()
        count=0
        class Request:
            path_params={"ticket":ticket}
            method="POST"
            headers={"origin":provider.base}
            cookies={"cdag_fixture_consent":csrf}
            async def form(self):
                nonlocal count
                count+=1
                if count==2:
                    ready.set()
                await ready.wait()
                return {"csrf":csrf,"decision":"allow"}
        results=await asyncio.gather(provider.consent(Request()),provider.consent(Request()))
        check(sorted(r.status_code for r in results)==[302,400],"Concurrent consent did not fail explicitly")
        check(len(provider.codes)==1 and not provider.pending,"Consent ticket produced duplicate grants")
        check(any(e["event"]=="consent_replay_rejected" for e in provider.events),"Replay evidence missing")
    asyncio.run(scenario())



def test_owned_receipts_control_only_synthetic_fixture(tmp_path):
    receipt_dir = tmp_path / "owned-oauth"
    receipt_dir.mkdir()
    process = subprocess.Popen([sys.executable, "-I", "-X", "utf8", str(SERVER),
        "--access-ttl", "30", "--lifetime", "30", "--receipt-dir", str(receipt_dir)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={k: os.environ[k] for k in ("SystemRoot", "WINDIR", "PATH") if k in os.environ})
    owned = psutil.Process(process.pid)
    birth = owned.create_time()
    try:
        deadline = time.monotonic() + 10
        while not (receipt_dir / "ready.json").exists():
            check(process.poll() is None and time.monotonic() < deadline, "Owned fixture did not become ready")
            time.sleep(.05)
        descriptor = json.loads((receipt_dir / "ready.json").read_text())
        actual = psutil.Process(descriptor["pid"])
        ancestry = [actual, *actual.parents()]
        check(actual.create_time() == descriptor["created"]
              and any(parent.pid == process.pid and parent.create_time() == birth for parent in ancestry)
              and str(SERVER) in actual.cmdline() and str(receipt_dir) in actual.cmdline(),
              "Fixture receipt does not identify the owned process or Windows venv child")
        check(descriptor["source_sha256"] == hashlib.sha256(SERVER.read_bytes()).hexdigest(),
              "Fixture source provenance differs")
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            fixture = descriptor, client
            info, redirect = register(fixture)
            code, verifier = grant(fixture, info, redirect)
            token = exchange(fixture, info, redirect, code, verifier).json()
            asyncio.run(call_mcp(descriptor["mcp_url"], token["access_token"]))
            check(any(x["event"] == "tool_called" for x in client.get(descriptor["events_url"]).json()["events"]),
                  "Actual MCP call did not reach the fixture tool")
            (receipt_dir / "revoke.request").touch()
            deadline = time.monotonic() + 5
            while not (receipt_dir / "revoked.json").exists():
                check(time.monotonic() < deadline, "Owned revoke request was not processed")
                time.sleep(.05)
            revoked = json.loads((receipt_dir / "revoked.json").read_text())
            check(revoked["access_count"] == revoked["refresh_count"] == 0, "Synthetic grants survived revocation")
            check(refresh(fixture, info, token["refresh_token"]).status_code == 400,
                  "Native-facing refresh remained valid after operator revocation")
            check(client.get(descriptor["mcp_url"], headers={"Authorization":"Bearer " + token["access_token"]}).status_code == 401,
                  "Synthetic access token survived operator revocation")
            (receipt_dir / "stop.request").touch()
            process.wait(timeout=8)
            check(process.returncode == 0 and (receipt_dir / "stopped.json").exists(),
                  "Owned fixture did not stop cleanly")
            evidence = (receipt_dir / "stopped.json").read_text()
            check(all(value not in evidence for value in [token["access_token"], token["refresh_token"], code, verifier]),
                  "Safe fixture receipt exposed OAuth material")
            with pytest.raises(httpx.HTTPError):
                client.get(descriptor["base"] + "/.well-known/oauth-authorization-server")
    finally:
        if process.poll() is None and owned.create_time() == birth:
            (receipt_dir / "stop.request").touch()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                # Windows venv launchers may own a real runtime-python child.
                children = [(child, child.create_time()) for child in owned.children(recursive=True)]
                for child, created in reversed(children):
                    try:
                        if (child.create_time() == created and str(SERVER) in child.cmdline()
                                and str(receipt_dir) in child.cmdline()):
                            child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
                process.wait(timeout=5)
        stdout, stderr = process.communicate(timeout=5)
        check(stderr == b"", "Fixture emitted unsanitized diagnostics")



def test_real_browser_form_origin_policy_and_fixed_reject_diagnostics(fixture):
    descriptor, client = fixture
    info, redirect = register(fixture)
    response, _, state = authorization(fixture, info, redirect)
    location = response.headers['location']
    page = client.get(location)
    check(page.headers['referrer-policy'] == 'same-origin', 'Consent page would make its form origin opaque')
    check('form-action \'self\'' in page.headers['content-security-policy'], 'Consent form origin restriction changed')
    check(re.search(r'Max-Age=300(?:;|$)', page.headers['set-cookie'], re.I) is not None,
          'Consent cookie is not aligned with the native 300-second callback window')
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    for origin in ['null', 'http://UNTRUSTED_ORIGIN_VALUE.invalid']:
        denied = client.post(location, data={'csrf':csrf, 'decision':'deny'}, headers={'Origin':origin})
        check(denied.status_code == 403 and denied.json()['error'] == 'invalid_origin',
              'Opaque/foreign origin was accepted')
    wrong_csrf = client.post(location, data={'csrf':'incorrect', 'decision':'deny'},
                            headers={'Origin':descriptor['base']})
    check(wrong_csrf.status_code == 400, 'Same origin bypassed CSRF validation')
    denied = client.post(location, data={'csrf':csrf, 'decision':'deny'}, headers={'Origin':descriptor['base']})
    check(denied.status_code == 302, 'Valid same-origin synthetic denial failed')
    query = parse_qs(urlsplit(denied.headers['location']).query)
    check(query.get('state') == [state] and query.get('error') == ['access_denied'], 'Denial callback binding changed')
    check(denied.headers['referrer-policy'] == 'no-referrer', 'Cross-origin callback redirect could leak its source referrer')
    diagnostics = client.get(descriptor['events_url']).text
    check('UNTRUSTED_ORIGIN_VALUE' not in diagnostics and csrf not in diagnostics,
          'Reject diagnostics exposed raw headers or CSRF')
    names = [event['event'] for event in json.loads(diagnostics)['events']]
    check('origin_rejected_null' in names and 'origin_rejected_other' in names
          and 'consent_origin_same' in names and 'consent_csrf_rejected' in names,
          'Fixed diagnostic classification missing')


def test_consent_window_does_not_extend_code_or_token_boundaries():
    spec = importlib.util.spec_from_file_location('oauth_fixture_consent_window', SERVER)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    from mcp.shared.auth import OAuthClientInformationFull
    from mcp.server.auth.provider import AuthorizationParams
    async def scenario():
        provider = module.FixtureProvider('http://127.0.0.1:43219', 'e'*32, 2)
        client = OAuthClientInformationFull(client_id='synthetic', redirect_uris=['http://127.0.0.1:43220/callback'],
            token_endpoint_auth_method='none', grant_types=['authorization_code','refresh_token'],
            response_types=['code'], scope='fixture:read')
        params = AuthorizationParams(state='synthetic-state', scopes=['fixture:read'], code_challenge='x'*43,
            redirect_uri=client.redirect_uris[0], redirect_uri_provided_explicitly=True, resource=provider.resource)
        location = await provider.authorize(client, params)
        ticket = location.rsplit('/',1)[1]
        pending = provider.pending[ticket]
        check(299 <= pending['expires']-time.time() <= 300, 'Consent ticket is not bounded to native callback time')
        class Request:
            path_params={'ticket':ticket}
            method='POST'
            headers={'origin':provider.base}
            cookies={'cdag_fixture_consent':pending['csrf']}
            async def form(self): return {'csrf':pending['csrf'],'decision':'allow'}
        allowed = await provider.consent(Request())
        check(allowed.status_code == 302 and len(provider.codes)==1, 'Valid bounded consent failed')
        code = next(iter(provider.codes.values()))
        check(59 <= code.expires_at-time.time() <= 60, 'Authorization code lifetime was widened')
        token = await provider.exchange_authorization_code(client, code)
        check(token.expires_in==2 and provider.refresh[token.refresh_token].expires_at-int(time.time()) <= 3600,
              'Access or refresh token boundary was widened')
        location = await provider.authorize(client, params)
        expired = location.rsplit('/',1)[1]
        provider.pending[expired]['expires'] = time.time()-1
        class ExpiredRequest:
            path_params={'ticket':expired}
            method='GET'
        result = await provider.consent(ExpiredRequest())
        check(result.status_code==400 and expired not in provider.pending, 'Expired consent survived')
        check(provider.events[-1]['event']=='consent_unknown_or_expired', 'Expired consent was not diagnosed safely')
    asyncio.run(scenario())


def test_consent_csp_allows_only_its_registered_exact_callback_origin(fixture):
    descriptor, client = fixture
    redirect = "http://127.0.0.1:43227/callback/selected"
    info, _ = register(fixture, redirect)
    response, _, _ = authorization(fixture, info, redirect)
    check(response.status_code == 302, "Selected registered callback did not authorize")
    page = client.get(response.headers["location"])
    policy = page.headers["content-security-policy"]
    directives = {part.strip().split()[0]: part.strip().split()[1:] for part in policy.split(";") if part.strip()}
    check(directives == {"default-src": ["'none'"], "form-action": ["'self'", "http://127.0.0.1:43227"],
                         "frame-ancestors": ["'none'"]},
          "CSP must allow only self and this authorization's exact loopback callback origin")
    check("*" not in policy and "43219" not in policy and "selected" not in policy,
          "CSP broadened to wildcard/other registered callback or exposed URL data")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    forged = client.post(response.headers["location"], data={"csrf":csrf,"decision":"allow"},
                         headers={"Origin":"http://127.0.0.1:43227"})
    check(forged.status_code == 403, "Callback CSP allowance bypassed consent Origin check")
    valid = client.post(response.headers["location"], data={"csrf":csrf,"decision":"deny"},
                        headers={"Origin":descriptor["base"]})
    check(valid.status_code == 302 and valid.headers["location"].startswith(redirect+"?"),
          "Legitimate denial did not retain its exact registered callback")

@pytest.mark.parametrize("redirect,expected", [
    ("http://127.0.0.1:43227/callback", "http://127.0.0.1:43227"),
    ("http://[::1]:43228/callback/one", "http://[::1]:43228"),
])
def test_callback_csp_origin_uses_only_verified_loopback_parts(redirect, expected):
    spec=importlib.util.spec_from_file_location("oauth_fixture_csp",SERVER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.callback_origin(redirect) == expected

@pytest.mark.parametrize("redirect", [
    "https://example.invalid/callback", "http://127.0.0.1:43227/other",
    "http://user@127.0.0.1:43227/callback", "http://127.0.0.1:43227/callback?to=elsewhere",
    "http://127.0.0.1:43227/callback#fragment",
])
def test_callback_csp_cannot_accept_arbitrary_destination(redirect):
    spec=importlib.util.spec_from_file_location("oauth_fixture_csp_reject",SERVER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(ValueError):
        module.callback_origin(redirect)
