"""Explicit HTTP entry guards reuse the reviewed dependency without import-time changes."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest
from aiohttp import web
from codex_deepseek import windows_asyncio as compatibility
from codex_app_deepseek import bridge, cli, firecrawl_backend, upstream_evidence, weather_backend, web_backend
from codex_app_deepseek.protocol_state import ProtocolState

TOKEN = "local-fixture-token-at-least24-characters"


def child(program, tmp_path):
    # Trusted actual package roots work for source qualification and installed qualification.
    roots = [str(Path(bridge.__file__).resolve().parent.parent),
             str(Path(compatibility.__file__).resolve().parent.parent)]
    prelude = "import sys;sys.path[:0]=" + repr(list(dict.fromkeys(roots))) + "\n"
    return subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", "-W", "error", "-c",
                           prelude + program], cwd=tmp_path, capture_output=True,
                          text=True, encoding="utf-8", timeout=30)


def test_importing_http_modules_and_helper_does_not_patch_python(tmp_path):
    program = r"""
import sys
original = None
if sys.platform == "win32":
    from asyncio import proactor_events
    original = proactor_events._ProactorBasePipeTransport._call_connection_lost
from codex_app_deepseek import bridge,cli,firecrawl_backend,upstream_evidence,weather_backend,web_backend
from codex_deepseek import windows_asyncio
if original is not None:
    assert proactor_events._ProactorBasePipeTransport._call_connection_lost is original
assert not any(name in sys.modules for name in (
    "codex_deepseek.native_http", "codex_deepseek.service", "codex_deepseek.supervisor",
    "codex_deepseek.cli", "codex_deepseek.image_provider"))
print("IMPORT_ONLY_UNCHANGED")
"""
    result = child(program, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "IMPORT_ONLY_UNCHANGED"


def test_real_independent_bootstrap_imports_cli_without_CDS_and_rejects_regression(tmp_path):
    import zipfile
    package = Path(cli.__file__).resolve().parent
    paths = [path for path in package.rglob("*") if path.is_file()
             and path.suffix in {".py", ".json", ".ps1"}]
    def make_wheel(name, buggy):
        wheel = tmp_path / name
        with zipfile.ZipFile(wheel, "w") as archive:
            for path in paths:
                raw = path.read_bytes()
                if buggy and path == package / "cli.py":
                    raw = raw.replace(b"from . import __version__\n",
                        b"from . import __version__\nfrom codex_deepseek.windows_asyncio import install_proactor_cleanup_backport\n", 1)
                archive.writestr("codex_app_deepseek/" + path.relative_to(package).as_posix(), raw)
        return wheel
    program = r"""
import importlib.abc,sys
class NoCDS(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=="codex_deepseek" or fullname.startswith("codex_deepseek."):
   raise RuntimeError("Bootstrap unexpectedly imported CDS before dependencies were installed")
sys.meta_path.insert(0,NoCDS())
sys.path.insert(0,sys.argv[1])
from codex_app_deepseek.lifecycle import install
from codex_app_deepseek.cli import resolve_service_home
assert callable(install) and callable(resolve_service_home)
assert not any(name=="codex_deepseek" or name.startswith("codex_deepseek.") for name in sys.modules)
print("BOOTSTRAP_ONLY_STDLIB_NO_CDS")
"""
    python = str(Path(sys._base_executable).resolve())
    for buggy in [False, True]:
        wheel = make_wheel("broken.whl" if buggy else "fixed.whl", buggy)
        result = subprocess.run([python, "-I", "-S", "-B", "-X", "utf8", "-W", "error",
                                 "-c", program, str(wheel)], cwd=tmp_path,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        if buggy:
            assert result.returncode != 0
            assert "Bootstrap unexpectedly imported CDS before dependencies were installed" in result.stderr
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            assert result.stdout.strip() == "BOOTSTRAP_ONLY_STDLIB_NO_CDS"


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["bridge", "image-cli", "firecrawl", "metadata",
                                  "weather", "document", "search", "images"])
async def test_each_http_owner_calls_guard_before_transport_creation(owner, tmp_path, monkeypatch):
    module = {"bridge": bridge, "image-cli": cli, "firecrawl": firecrawl_backend,
              "metadata": upstream_evidence, "weather": weather_backend,
              "document": web_backend, "search": web_backend, "images": web_backend}[owner]
    error = RuntimeError("owned-test-guard-before-any-transport")
    calls = []
    def guard():
        calls.append(owner)
        raise error
    helper_owner = compatibility if owner == "image-cli" else module
    monkeypatch.setattr(helper_owner, "install_proactor_cleanup_backport", guard)
    sessions = []
    def forbidden_session(*args, **kwargs):
        sessions.append(owner)
        raise AssertionError("ClientSession constructed before the explicit transport guard")
    # CLI imports aiohttp lazily; it resolves the same already loaded module.
    monkeypatch.setattr(getattr(module, "aiohttp", sys.modules["aiohttp"]), "ClientSession", forbidden_session)
    def forbidden_credential(*args, **kwargs):
        raise AssertionError("Must not access the real credential store")
    with pytest.raises(RuntimeError) as failure:
        if owner == "bridge":
            bridge.create_app(bridge.BridgeSettings(token=TOKEN), forbidden_credential, object())
        elif owner == "image-cli":
            monkeypatch.setattr(cli, "resolve_service_home", lambda home: home)
            monkeypatch.setattr(cli, "load_service_config", lambda home: SimpleNamespace())
            monkeypatch.setattr(cli, "image_transport_factory", lambda home, config: object())
            monkeypatch.setattr(cli, "read", forbidden_credential)
            await cli.image_cache_operation(tmp_path, "image-cache-status")
        elif owner == "firecrawl":
            await object.__new__(firecrawl_backend.FirecrawlBackend)._post("/v2/search", {})
        elif owner == "metadata":
            await upstream_evidence.read_generation_evidence(
                {"id": "fixture", "response_id": "gen-example123",
                 "requested_upstream_model": upstream_evidence.MODEL}, lambda: "SYNTHETIC_ONLY")
        elif owner == "weather":
            await object.__new__(weather_backend.OpenMeteoWeather)._json(
                "https://api.open-meteo.com/v1/forecast", {}, {"requests": []})
        elif owner == "document":
            await web_backend.PublicDocumentFetcher().fetch(
                "https://example.com/doc", web_backend.DomainPolicy())
        elif owner == "search":
            await web_backend.OpenRouterSearch(lambda: "SYNTHETIC_ONLY").search(
                "fixture", web_backend.DomainPolicy(), "medium")
        else:
            await web_backend.WikimediaImageSearch().search("fixture", web_backend.DomainPolicy())
    assert failure.value is error, "Guard errors must not be disguised as completed HTTP operations"
    assert calls == [owner]
    assert sessions == []


@pytest.mark.skipif(sys.platform != "win32", reason="Reviewed actual Windows CPython Proactor method")
def test_actual_windows_method_cleanup_preserves_error_and_unknown_guard(tmp_path):
    program = r"""
import asyncio,ctypes,hashlib,inspect,logging
from pathlib import Path
from types import SimpleNamespace
from asyncio import proactor_events
from codex_deepseek import windows_asyncio as helper
target=proactor_events._ProactorBasePipeTransport
original=target._call_connection_lost
assert hashlib.sha256(inspect.getsource(original).encode()).hexdigest()=="21755f1f4d9172c66ccb8ff6787e522916a0375b1e47222cb5f74916f30fa5ef"
disk=Path(proactor_events.__file__)
before=hashlib.sha256(disk.read_bytes()).hexdigest()
policy=asyncio.get_event_loop_policy()
def unknown(self,exc): pass
target._call_connection_lost=unknown
assert helper.install_proactor_cleanup_backport()=="not_applied"
assert target._call_connection_lost is unknown
target._call_connection_lost=original
class Socket:
 def __init__(self,error):self.error=error;self.closes=0
 def fileno(self):return 17
 def shutdown(self,how):raise self.error
 def close(self):self.closes+=1
def fixture(error):
 calls=[];detached=[];sock=Socket(error)
 value=SimpleNamespace(_called_connection_lost=False,_sock=sock,
     _protocol=SimpleNamespace(connection_lost=lambda exc:calls.append(exc)),
     _server=SimpleNamespace(_detach=lambda value:detached.append(value)))
 return value,sock,calls,detached
error=ctypes.WinError(10054)
assert isinstance(error,ConnectionResetError) and error.winerror==10054
value,sock,calls,detached=fixture(error)
try:original(value,None)
except ConnectionResetError as caught:assert caught is error
else:raise AssertionError("Original reset must remain observable")
assert sock.closes==0 and value._sock is sock and not detached and not value._called_connection_lost
assert helper.install_proactor_cleanup_backport()=="applied"
assert helper.install_proactor_cleanup_backport()=="already_applied"
value,sock,calls,detached=fixture(error)
try:target._call_connection_lost(value,None)
except ConnectionResetError as caught:assert caught is error
else:raise AssertionError("The fix must not swallow the original error")
assert calls==[None] and sock.closes==1 and detached==[value]
assert value._sock is None and value._server is None and value._called_connection_lost
target._call_connection_lost(value,None)
assert sock.closes==1 and calls==[None]
assert asyncio.get_event_loop_policy() is policy
assert hashlib.sha256(disk.read_bytes()).hexdigest()==before
print("KNOWN_RESET_CLEANUP_UNKNOWN_GUARD_AND_POLICY_PASS")
"""
    result = child(program, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "KNOWN_RESET_CLEANUP_UNKNOWN_GUARD_AND_POLICY_PASS"
    assert "NOT_APPLIED" in result.stderr and "unrecognized_function" in result.stderr


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows gateway Proactor FIN/RST")
@pytest.mark.parametrize("reset", [False, True], ids=["FIN", "RST"])
async def test_real_gateway_server_fin_and_rst_release_transport_and_connection(tmp_path, reset):
    from asyncio import proactor_events
    assert isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop)
    state = ProtocolState(tmp_path / "protocol.sqlite")
    def no_credential():
        raise AssertionError("A local transport check must not read upstream credentials")
    app = bridge.create_app(bridge.BridgeSettings(token=TOKEN), no_credential, state)
    assert proactor_events._ProactorBasePipeTransport._call_connection_lost is compatibility._call_connection_lost
    accepted = []
    async def endpoint(request):
        accepted.append(request.transport)
        return web.Response(body=b"OK")
    app.router.add_get("/test-transport", endpoint)
    runner = web.AppRunner(app, handler_cancellation=True)
    try:
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        port = runner.addresses[0][1]
        def client():
            with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                connection.sendall(b"GET /test-transport HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n")
                body = b""
                while b"\r\n\r\n" not in body or not body.split(b"\r\n\r\n", 1)[1].endswith(b"OK"):
                    chunk = connection.recv(4096)
                    assert chunk, "Gateway closed before complete HTTP response"
                    body += chunk
                assert body.startswith(b"HTTP/1.1 200 ")
                assert body.split(b"\r\n\r\n", 1)[1] == b"OK"
                if reset:
                    connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
        await asyncio.wait_for(asyncio.to_thread(client), 3)
        async with asyncio.timeout(2):
            while not accepted or not accepted[0]._called_connection_lost or runner.server.connections:
                await asyncio.sleep(.01)
        assert accepted[0]._sock is None and accepted[0]._server is None
        assert not runner.server.connections
        assert state.list_receipts() == []
    finally:
        await runner.cleanup()
        state.close()


def test_non_windows_path_is_explicit_noop(monkeypatch):
    monkeypatch.setattr(compatibility, "_WINDOWS", False)
    monkeypatch.setattr(compatibility.inspect, "getsource",
                        lambda value: pytest.fail("Non-Windows must not inspect private Proactor source"))
    assert compatibility.install_proactor_cleanup_backport() == "not_applicable"
