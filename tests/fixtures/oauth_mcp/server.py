"""Loopback-only synthetic OAuth MCP fixture. No real account or credential store."""
from __future__ import annotations
import argparse
import asyncio
from collections import deque
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import threading
import time
from urllib.parse import parse_qsl, urlsplit

from mcp.server import MCPServer
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams,
    AuthorizeError, OAuthAuthorizationServerProvider, RefreshToken, RegistrationError,
    TokenError, construct_redirect_uri)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.types import ToolAnnotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route
import uvicorn

ROOT = Path(__file__).resolve().parent
SCOPE = "fixture:read"
CONSENT_TTL_SECONDS = 300

def valid_redirect(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
            and parsed.username is None and parsed.password is None
            and parsed.port is not None and 1024 <= parsed.port <= 65535
            and not parsed.query and not parsed.fragment
            and re.fullmatch(r"/callback(?:/[A-Za-z0-9_-]{1,128})?", parsed.path) is not None)
    except ValueError:
        return False

def callback_origin(redirect: str) -> str:
    # Form redirects remain subject to form-action in Chromium. Rebuild the one
    # already registered/authorized loopback origin, never echo arbitrary URL text.
    if not valid_redirect(redirect):
        raise ValueError("Invalid fixture callback")
    parsed = urlsplit(redirect)
    host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
    return f"http://{host}:{parsed.port}"

class FixtureProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, base: str, nonce: str, access_ttl: int):
        self.base, self.nonce, self.access_ttl = base, nonce, access_ttl
        self.resource = base + "/mcp/" + nonce
        self.nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
        self.clients = {}
        self.pending = {}
        self.codes = {}
        self.access = {}
        self.refresh = {}
        self.pairs = {}
        self.events = deque(maxlen=512)

    def event(self, name: str) -> None:
        # Callers supply only fixed event names, never request fields or credentials.
        self.events.append({"event": name, "at": time.time(), "nonce_sha256": self.nonce_hash})

    def prune(self) -> None:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v["expires"] > now}
        self.codes = {k: v for k, v in self.codes.items() if v.expires_at > now}
        for token, record in list(self.refresh.items()):
            if record.expires_at <= now:
                self.drop_pair(token)
        # Keep expired access records until refresh/revoke so a live refresh can be revoked.

    def drop_pair(self, token: str) -> None:
        other = self.pairs.pop(token, None)
        self.access.pop(token, None); self.refresh.pop(token, None)
        if other:
            self.pairs.pop(other, None)
            self.access.pop(other, None); self.refresh.pop(other, None)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if len(self.clients) >= 16:
            raise RegistrationError("invalid_client_metadata", "Fixture client capacity reached")
        redirects = client_info.redirect_uris or []
        if not 1 <= len(redirects) <= 2 or not all(valid_redirect(str(uri)) for uri in redirects):
            self.event("registration_rejected")
            raise RegistrationError("invalid_redirect_uri", "Only exact local fixture callback URIs are permitted")
        if (set(client_info.grant_types) - {"authorization_code", "refresh_token"}
                or set(client_info.response_types) != {"code"}
                or client_info.token_endpoint_auth_method not in {"none", "client_secret_post", "client_secret_basic"}):
            raise RegistrationError("invalid_client_metadata", "Unsupported fixture client metadata")
        self.clients[client_info.client_id] = client_info
        self.event("client_registered")

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self.prune()
        if params.resource != self.resource:
            raise AuthorizeError("invalid_target", "Fixture resource mismatch")
        if (not params.state or len(params.state) > 512 or not params.redirect_uri_provided_explicitly
                or str(params.redirect_uri) not in {str(uri) for uri in client.redirect_uris or []}
                or not valid_redirect(str(params.redirect_uri))
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge)
                or set(params.scopes or []) != {SCOPE}):
            raise AuthorizeError("invalid_request", "Fixture authorization binding is incomplete")
        if len(self.pending) >= 32:
            raise AuthorizeError("temporarily_unavailable", "Fixture consent capacity reached")
        ticket = secrets.token_urlsafe(32)
        self.pending[ticket] = {"client": client, "params": params,
            "csrf": secrets.token_urlsafe(32), "expires": time.time() + CONSENT_TTL_SECONDS}
        self.event("consent_requested")
        return self.base + "/fixture/" + self.nonce + "/consent/" + ticket

    async def consent(self, request: Request):
        ticket = request.path_params["ticket"]
        self.prune()
        pending = self.pending.get(ticket)
        if pending is None:
            self.event("consent_unknown_or_expired")
            return JSONResponse({"error": "unknown_or_expired_consent"}, status_code=400)
        if request.method == "GET":
            csrf = pending["csrf"]
            page = ('<!doctype html><meta charset="utf-8"><title>Local synthetic MCP test</title>'
                '<h1>Authorize a local synthetic test</h1>'
                '<p>This fixture uses no real account. It grants read-only access to fixed test data.</p>'
                '<p>Closing or denying this page grants no access.</p>'
                '<form method="post"><input type="hidden" name="csrf" value="' + html.escape(csrf, quote=True) + '">'
                '<button name="decision" value="allow">Allow fixture</button>'
                '<button name="decision" value="deny">Deny</button></form>')
            response = HTMLResponse(page, headers={"Cache-Control": "no-store",
                # Fetch makes an Origin header opaque under no-referrer for this form POST.
                # same-origin preserves our form origin while withholding cross-origin referrers.
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'none'; form-action 'self' "
                + callback_origin(str(pending["params"].redirect_uri)) + "; frame-ancestors 'none'"})
            response.set_cookie("cdag_fixture_consent", csrf, httponly=True, samesite="strict", max_age=CONSENT_TTL_SECONDS)
            return response
        data = await request.form()
        self.event("consent_origin_" + ("none" if request.headers.get("origin") is None else
                                      "same" if request.headers.get("origin") == self.base else
                                      "null" if request.headers.get("origin") == "null" else "other"))
        if (request.headers.get("origin") not in {None, self.base}
                or not secrets.compare_digest(str(data.get("csrf", "")), pending["csrf"])
                or not secrets.compare_digest(request.cookies.get("cdag_fixture_consent", ""), pending["csrf"])
                or data.get("decision") not in {"allow", "deny"}):
            self.event("consent_csrf_rejected")
            return JSONResponse({"error": "invalid_consent"}, status_code=400)
        # Pop without awaiting: a consent ticket can produce at most one code.
        if self.pending.pop(ticket, None) is not pending:
            self.event("consent_replay_rejected")
            return JSONResponse({"error": "consent_already_used"}, status_code=400)
        params = pending["params"]
        fields = {"state": params.state, "iss": self.base}
        if data["decision"] == "deny":
            fields["error"] = "access_denied"
            self.event("consent_denied")
        else:
            if len(self.codes) >= 32:
                return JSONResponse({"error": "fixture_capacity"}, status_code=503)
            code = secrets.token_urlsafe(32)
            self.codes[code] = AuthorizationCode(code=code, scopes=[SCOPE], expires_at=time.time()+60,
                client_id=pending["client"].client_id, code_challenge=params.code_challenge,
                redirect_uri=params.redirect_uri, redirect_uri_provided_explicitly=True,
                resource=self.resource, subject="synthetic-fixture-user")
            fields["code"] = code
            self.event("code_issued")
        response = RedirectResponse(construct_redirect_uri(str(params.redirect_uri), **fields),
            status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        response.delete_cookie("cdag_fixture_consent")
        return response

    async def load_authorization_code(self, client, authorization_code):
        record = self.codes.get(authorization_code)
        return record if record is not None and record.client_id == client.client_id else None

    def issue(self, client_id: str, scopes: list[str]) -> OAuthToken:
        if len(self.refresh) >= 64:
            raise TokenError("invalid_request", "Fixture token capacity reached")
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        self.access[access] = AccessToken(token=access, client_id=client_id, scopes=scopes,
            expires_at=now+self.access_ttl, resource=self.resource, subject="synthetic-fixture-user",
            claims={"iss": self.base})
        self.refresh[refresh] = RefreshToken(token=refresh, client_id=client_id, scopes=scopes,
            expires_at=now+3600, resource=self.resource, subject="synthetic-fixture-user")
        self.pairs[access] = refresh; self.pairs[refresh] = access
        return OAuthToken(access_token=access, refresh_token=refresh, expires_in=self.access_ttl, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        stored = self.codes.pop(authorization_code.code, None)
        if (stored is None or stored != authorization_code or stored.expires_at <= time.time()
                or stored.client_id != client.client_id or stored.resource != self.resource):
            raise TokenError("invalid_grant", "Code already consumed or binding invalid")
        self.event("code_exchanged")
        return self.issue(client.client_id, stored.scopes)

    async def load_refresh_token(self, client, refresh_token):
        record = self.refresh.get(refresh_token)
        return record if record is not None and record.client_id == client.client_id else None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        stored = self.refresh.get(refresh_token.token)
        if (stored is None or stored != refresh_token or stored.expires_at <= time.time()
                or stored.client_id != client.client_id or stored.resource != self.resource
                or set(scopes) != {SCOPE}):
            raise TokenError("invalid_grant", "Refresh token is invalid")
        self.drop_pair(refresh_token.token)
        self.event("refresh_exchanged")
        return self.issue(client.client_id, scopes)

    async def load_access_token(self, token):
        record = self.access.get(token)
        if record is None or record.resource != self.resource:
            self.event("access_rejected")
            return None
        # SDK BearerAuthBackend checks expiry. Preserve known expired records here
        # so the SDK revoke route can still invalidate their paired refresh token.
        self.event("access_loaded_expired" if record.expires_at <= time.time() else "access_loaded")
        return record

    async def revoke_token(self, token):
        self.drop_pair(token.token)
        self.event("token_revoked")

    async def diagnostics(self, request):
        return JSONResponse({"nonce_sha256": self.nonce_hash, "events": list(self.events),
            "counts": {"clients": len(self.clients), "pending": len(self.pending),
                       "codes": len(self.codes), "access": len(self.access), "refresh": len(self.refresh)}},
            headers={"Cache-Control": "no-store"})


class FixtureBoundary(BaseHTTPMiddleware):
    """A bounded fixture policy around SDK routes, not an OAuth implementation."""
    def __init__(self, app, provider: FixtureProvider):
        super().__init__(app)
        self.provider = provider

    async def dispatch(self, request, call_next):
        if request.headers.get("host") != urlsplit(self.provider.base).netloc:
            return JSONResponse({"error": "invalid_host"}, status_code=400)
        if request.headers.get("origin") not in {None, self.provider.base}:
            self.provider.event("origin_rejected_null" if request.headers.get("origin") == "null"
                                else "origin_rejected_other")
            return JSONResponse({"error": "invalid_origin"}, status_code=403)
        params = None
        if request.url.path in {"/authorize", "/token", "/revoke"}:
            if request.method == "GET":
                source = request.url.query
            elif request.method == "POST":
                if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
                    return JSONResponse({"error": "invalid_request"}, status_code=400)
                # Outer RequestBodyLimitMiddleware bounds bytes before this read.
                try:
                    source = (await request.body()).decode("utf8", errors="strict")
                except UnicodeError:
                    return JSONResponse({"error": "invalid_request"}, status_code=400)
            else:
                source = ""
            try:
                pairs = parse_qsl(source, keep_blank_values=True, max_num_fields=32)
            except (ValueError, UnicodeError):
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            if len({key for key, _ in pairs}) != len(pairs):
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            params = dict(pairs)
        if request.url.path == "/authorize" and params is not None:
            if params.get("code_challenge_method") != "S256":
                self.provider.event("pkce_method_rejected")
                return JSONResponse({"error": "invalid_request"}, status_code=400)
        if request.url.path == "/token" and params is not None and request.method == "POST":
            resource = params.get("resource")
            # The SDK parses resource but does not compare/pass it to exchange hooks.
            # Omission inherits the resource already fixed in the code/refresh grant.
            if resource is not None and resource != self.provider.resource:
                self.provider.event("token_resource_rejected")
                return JSONResponse({"error": "invalid_target"}, status_code=400)
            self.provider.event("token_resource_explicit" if resource is not None else "token_resource_inherited")
        return await call_next(request)


def make_app(base: str, nonce: str, access_ttl: int = 60):
    provider = FixtureProvider(base, nonce, access_ttl)
    server = MCPServer("CDAG synthetic OAuth", version="1.0.0", auth_server_provider=provider,
        auth=AuthSettings(issuer_url=base, resource_server_url=provider.resource,
            validate_token_resource=True, required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(enabled=True,
                valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True)), log_level="CRITICAL")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False),
                 structured_output=True)
    def read_reference() -> dict[str, str]:
        """Return the fixed test identity and baseline; no filesystem or network access."""
        provider.event("tool_called")
        return {"identity": "synthetic-fixture-user", "fixture": "cdag-oauth",
                "native_source": "680c4ba93ec79c292287fb9e0b627dc7a76e3b5e"}

    app = server.streamable_http_app(streamable_http_path="/mcp/"+nonce, json_response=True,
        stateless_http=True, max_request_body_size=65536, max_sessions=8,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[urlsplit(base).netloc], allowed_origins=[base]))
    app.routes.extend([
        Route("/fixture/"+nonce+"/consent/{ticket}", provider.consent, methods=["GET","POST"]),
        Route("/fixture/"+nonce+"/events", provider.diagnostics, methods=["GET"]),
    ])
    app.add_middleware(FixtureBoundary, provider=provider)
    # Applies to OAuth and consent as well as MCP, before any Request.body() above.
    from mcp.server.transport_security import RequestBodyLimitMiddleware
    app.add_middleware(RequestBodyLimitMiddleware, max_body_size=65536)
    return app, provider


async def serve(access_ttl: int, lifetime: int, receipt_dir: Path | None = None) -> None:
    nonce = secrets.token_hex(16)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0)); sock.listen(32)
    port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    app, provider = make_app(base, nonce, access_ttl)
    configuration = uvicorn.Config(app, log_config=None, access_log=False, log_level="critical",
                                   server_header=False, timeout_graceful_shutdown=3, limit_concurrency=16)
    service = uvicorn.Server(configuration)
    def operator_stop():
        # The test controller owns stdin; only q requests graceful fixture shutdown.
        if sys.stdin.read(1) == "q":
            service.should_exit = True
    threading.Thread(target=operator_stop, daemon=True).start()
    async def expiry():
        await asyncio.sleep(lifetime)
        service.should_exit = True
    timer = asyncio.create_task(expiry())
    descriptor = {"fixture":"cdag-oauth", "startup_state":"bound_not_ready", "base":base, "mcp_url":provider.resource,
        "server_name":"cdag_oauth_"+nonce, "events_url":base+"/fixture/"+nonce+"/events",
        "nonce_sha256":provider.nonce_hash, "access_ttl_seconds":access_ttl,
        "lifetime_seconds":lifetime}
    def receipt(name: str, value: dict) -> None:
        if receipt_dir is None:
            return
        temporary = receipt_dir / (name + ".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf8")
        temporary.replace(receipt_dir / name)
    identity = {}
    if receipt_dir is not None:
        import psutil
        if not receipt_dir.is_absolute() or receipt_dir.resolve() != receipt_dir:
            raise ValueError("Fixture receipt directory must be an exact absolute path")
        if not receipt_dir.is_dir() or any(receipt_dir.iterdir()):
            raise ValueError("Fixture receipt directory must be empty and precreated")
        process = psutil.Process()
        identity = {"pid":os.getpid(), "created":process.create_time(), "exe":process.exe(),
                    "source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    async def controls():
        while not service.started and not service.should_exit:
            await asyncio.sleep(.05)
        if not service.started:
            return
        receipt("ready.json", {**descriptor, **identity, "startup_state":"ready", "ready_at":time.time()})
        while not service.should_exit:
            if receipt_dir is not None:
                if (receipt_dir / "revoke.request").exists():
                    for token in list(provider.refresh):
                        provider.drop_pair(token)
                    provider.event("operator_tokens_revoked")
                    (receipt_dir / "revoke.request").unlink()
                    receipt("revoked.json", {"at":time.time(), "access_count":len(provider.access),
                                            "refresh_count":len(provider.refresh)})
                if (receipt_dir / "stop.request").exists():
                    provider.event("operator_stop")
                    service.should_exit = True
            await asyncio.sleep(.2)
    controller = asyncio.create_task(controls())
    print(json.dumps(descriptor), flush=True)
    try:
        await service.serve(sockets=[sock])
    finally:
        timer.cancel()
        controller.cancel()
        sock.close()
        receipt("stopped.json", {**identity, "stopped_at":time.time(),
                                 "events":list(provider.events), "nonce_sha256":provider.nonce_hash})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access-ttl", type=int, default=60)
    parser.add_argument("--lifetime", type=int, default=1800)
    parser.add_argument("--receipt-dir", type=Path, help="Empty owned QA directory; safe receipts and stop/revoke request files only")
    args = parser.parse_args()
    if not 2 <= args.access_ttl <= 300 or not 10 <= args.lifetime <= 3600:
        parser.error("Fixture access TTL must be 2..300 seconds and lifetime 10..3600 seconds")
    # No access log or exception body can record authorization parameters.
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(serve(args.access_ttl, args.lifetime, args.receipt_dir))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(json.dumps({"fixture_error":type(error).__name__}), flush=True)
        raise SystemExit(1)
