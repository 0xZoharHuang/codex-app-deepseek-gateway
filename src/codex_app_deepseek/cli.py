"""Gateway and explicit one-shot App entrypoints; no agent runtime supervision."""
from __future__ import annotations
from .paths import _is_link_or_reparse
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import ssl
import re
from urllib.parse import urlsplit
import dataclasses
from contextlib import ExitStack, contextmanager
import urllib.request

from . import __version__
from .credentials import CredentialUnavailable,LOCAL_TARGET,UPSTREAM_TARGET,initialize,read,write


def default_home() -> Path:
    return Path(os.environ.get("LOCALAPPDATA",str(Path.home()/"AppData/Local")))/"codex-app-deepseek-gateway"



from .gateway_policy import ServiceConfigError, ServiceConfig, load_service_config, resolve_service_home


@contextmanager
def _service_lease(home: Path, *, lock_name: str = "service.lock"):
    from .app_config import _private
    if lock_name not in {"service.lock", "lifecycle.lock"}:
        raise ServiceConfigError("Unrecognized gateway lock name")
    path = home / lock_name
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise ServiceConfigError("Gateway service lock must be a regular owned file")
    with path.open("a+b") as handle:
        _private(path)
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ServiceConfigError("Another gateway process already owns this data home") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_service_record(home: Path, record: dict):
    from .app_config import _atomic_write
    # Preserve the existing service-record wire bytes (no trailing newline).
    _atomic_write(home / "service.json", json.dumps(record, indent=2).encode("utf-8"))


def _image_cache_home(home: Path) -> Path:
    path = home / "image-transport"
    if _is_link_or_reparse(path):
        raise ServiceConfigError("Image transport directory cannot be a link or junction")
    if path.exists() and not path.is_dir():
        raise ServiceConfigError("Image transport directory must be a directory")
    return path


def image_transport_factory(home: Path, config: ServiceConfig):
    if config.image_transport is None:
        return None
    from .image_transport import ImageTransport, OpenRouterFilesBackend, TransportPolicy
    cache = _image_cache_home(home)
    policy = TransportPolicy(**config.image_transport)
    def factory(client, credential_provider):
        # The bridge supplies a callback bound to this request's credential.
        # File upload and inference must never observe different rotated keys.
        return ImageTransport(cache, credential_provider,
                              OpenRouterFilesBackend(client, proxy_url=config.upstream_proxy), policy)
    return factory


async def image_cache_operation(home: Path, operation: str, *, all_unpinned: bool = False):
    from codex_deepseek.windows_asyncio import install_proactor_cleanup_backport
    import aiohttp
    import truststore
    home = resolve_service_home(home)
    config = load_service_config(home)
    factory = image_transport_factory(home, config)
    if factory is None:
        raise ServiceConfigError("Configure image_transport explicitly before using its maintenance commands")
    install_proactor_cleanup_backport()
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context), timeout=timeout,
                                     trust_env=False, cookie_jar=aiohttp.DummyCookieJar()) as client:
        key = read(UPSTREAM_TARGET)
        transport = factory(client, lambda: key)
        if operation == "image-cache-status":
            return await transport.diagnostics()
        if operation == "image-cache-cleanup":
            return await transport.cleanup(all_unpinned=all_unpinned)
        if operation == "image-cache-reconcile-uploads":
            return await transport.reconcile_unknown_uploads()
        if operation == "image-cache-reconcile-calls":
            from .image_call_reconciliation import reconcile_image_calls
            result = await reconcile_image_calls(
                transport, home / "protocol.sqlite", home / "image-call-reconciliation.json", lambda: key,
                max_calls=2, timeout_seconds=10, proxy_url=config.upstream_proxy, ssl_context=context)
            # This collector reports an enumerated status, unlike upload/cleanup
            # operations' boolean contract. Never let absence mean success.
            return {**result, "complete": result.get("status") == "complete"}
        raise ServiceConfigError("Unknown image transport maintenance operation")


def serve(home: Path, port: int):
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ServiceConfigError("Use an unprivileged TCP port from 1024 through 65535")
    home = resolve_service_home(home)
    config = load_service_config(home)
    transport_factory = image_transport_factory(home, config)
    web_home = home / "web-data"
    if _is_link_or_reparse(web_home):
        raise ServiceConfigError("Web data directory cannot be a link or junction")
    from aiohttp import web
    from .bridge import BridgeSettings, create_app
    from .protocol_state import ProtocolState
    from .web_backend import WebBackend, WebSourceStore, OpenRouterSearch, PublicDocumentFetcher, WikimediaImageSearch
    from .app_config import _private
    import truststore
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    settings = BridgeSettings(token=read(LOCAL_TARGET), accepted_models=config.accepted_models,
                              allowed_providers=config.allowed_providers, proxy_url=config.upstream_proxy,
                              ssl_context=context, config_sha256=config.config_sha256,
                              ordinary_default_provider=config.ordinary_default_provider)
    home.mkdir(parents=True, exist_ok=True)
    _private(home, directory=True)
    with ExitStack() as resources:
        resources.enter_context(_service_lease(home))
        state = ProtocolState(home / "protocol.sqlite")
        resources.callback(state.close)
        web_home.mkdir(exist_ok=True)
        _private(web_home, directory=True)
        web_store = WebSourceStore(web_home / "sources.sqlite")
        resources.callback(web_store.close)
        fetcher = PublicDocumentFetcher(ssl_context=context)
        policy_backend = None
        if config.web_search_backend == "firecrawl":
            from .firecrawl_backend import FirecrawlBackend, FirecrawlImageSearch
            policy_backend = FirecrawlBackend(ssl_context=context, fetcher=fetcher)
        image_search = None
        if config.image_search_backend == "firecrawl":
            image_search = FirecrawlImageSearch(policy_backend)
        elif config.image_search_backend == "wikimedia_commons":
            image_search = WikimediaImageSearch(ssl_context=context)
        backend = WebBackend(
            web_store,
            OpenRouterSearch(lambda: read(UPSTREAM_TARGET), ssl_context=context, proxy_url=config.upstream_proxy)
                if policy_backend is None else None,
            fetcher,
            policy_backend=policy_backend,
            accepted_models=config.accepted_models,
            image_search=image_search,
        )
        bridge_options = {"web_backend": backend}
        if transport_factory is not None:
            bridge_options["image_transport_factory"] = transport_factory
        app = create_app(settings, lambda: read(UPSTREAM_TARGET), state, **bridge_options)

        service_record = None

        async def started(_):
            nonlocal service_record
            import psutil
            record = {"pid": os.getpid(), "created": psutil.Process().create_time(),
                      "executable": sys.executable, "port": port, "version": __version__, "host": "127.0.0.1",
                      "config_sha256": config.config_sha256,
                      "ordinary_default_provider": config.ordinary_default_provider}
            if config.configured_baseline:
                record["configured_baseline"] = config.configured_baseline
            _write_service_record(home, record)
            service_record = record

        async def close_record(_):
            path = home / "service.json"
            if service_record is not None and path.exists():
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return  # Keep damaged/replaced evidence for diagnosis.
                if current == service_record:
                    path.unlink()

        app.on_startup.append(started)
        app.on_cleanup.append(close_record)
        web.run_app(app, host="127.0.0.1", port=port, handle_signals=True,
                    handler_cancellation=True, access_log=None, print=None)


def _validate_catalog_agent_version(path: Path, version: str) -> str:
    """Bind an explicit native mode to the same bytes later configured."""
    import hashlib
    from .catalog import PINNED_MODEL
    try:
        raw = path.read_bytes()
        catalog = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ServiceConfigError("Model catalog must contain readable JSON") from error
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list) or any(not isinstance(model, dict) for model in models):
        raise ServiceConfigError("Model catalog must contain a models array")
    main = [model for model in models if model.get("slug") == PINNED_MODEL]
    visible = [model for model in models if model.get("visibility") == "list"]
    if len(main) != 1 or visible != main:
        raise ServiceConfigError("Catalog must contain one unique visible pinned DeepSeek model")
    if main[0].get("multi_agent_version") != version:
        raise ServiceConfigError(
            "Catalog native agent version must match --multi-agent-version; "
            "build the selected catalog explicitly and start a fresh chat")
    return hashlib.sha256(raw).hexdigest()


def main(argv=None):
    agent_version_help = ("Native agent version matching the supplied catalog (default: v2). "
                          "V1 is explicit opt-in for a fresh chat; existing chats retain their recorded version.")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version",action="version",version=__version__)
    sub=parser.add_subparsers(dest="command",required=True)
    command=sub.add_parser("serve");command.add_argument("--home",type=Path,default=default_home());command.add_argument("--port",type=int,default=42817)
    command=sub.add_parser("setup-credentials");command.add_argument("--import-existing-cds",action="store_true")
    command=sub.add_parser("health");command.add_argument("--port",type=int,default=42817)
    command=sub.add_parser("configure-app");command.add_argument("--config",type=Path,default=default_home().parent/"codex-app-deepseek-app/config.toml");command.add_argument("--catalog",type=Path,required=True);command.add_argument("--backup-dir",type=Path,default=default_home()/"config-backups");command.add_argument("--port",type=int,default=42817)
    command.add_argument("--multi-agent-version", choices=("v1", "v2"), default="v2", help=agent_version_help)
    command=sub.add_parser("restore-app");command.add_argument("manifest",type=Path)
    sub.add_parser("auth-token",help="Official command-backed App authentication; stdout contains only the local gateway token")
    for name in ("image-cache-status", "image-cache-cleanup", "image-cache-reconcile-uploads", "image-cache-reconcile-calls"):
        command = sub.add_parser(name)
        command.add_argument("--home", type=Path, default=default_home())
        if name == "image-cache-cleanup":
            command.add_argument("--all-unpinned", action="store_true")
    for name in ("service-start", "service-stop", "diagnose", "app-prepare-runtime", "app-bind",
                 "app-configure", "app-check", "app-launch", "app-status"):
        command = sub.add_parser(name)
        command.add_argument("--install-root", type=Path, default=default_home()/"managed-install")
        if name in {"app-prepare-runtime", "app-bind"}:
            command.add_argument("--native-manifest", type=Path, required=True)
            command.add_argument("--native-manifest-sha256", required=True)
        if name == "app-configure":
            command.add_argument("--catalog", type=Path, required=True)
            command.add_argument("--catalog-sha256", required=True)
            command.add_argument("--multi-agent-version", choices=("v1", "v2"), default="v2", help=agent_version_help)
            command.add_argument("--port", type=int, default=42817)
        if name == "service-start":
            command.add_argument("--port", type=int, default=42817)
            command.add_argument("--at-logon", action="store_true")
        if name in {"app-check", "app-launch"}:
            command.add_argument("--accessibility", action="store_true")
            command.add_argument("--observation-seconds", type=int, default=60)
            command.add_argument("--trace-root", type=Path, help="Private container for this launch's optional official Codex rollout trace")
        if name == "app-launch":
            command.add_argument("--wait-seconds", type=int, default=60)
        if name == "app-status":
            command.add_argument("--run-id")
    args=parser.parse_args(argv)
    from .image_transport import ImageTransportError
    from .image_call_reconciliation import CallReconciliationError
    try:
        if args.command in {"configure-app", "app-configure"}:
            selected_catalog_sha = _validate_catalog_agent_version(args.catalog, args.multi_agent_version)
            if args.command == "app-configure" and selected_catalog_sha != args.catalog_sha256:
                raise ServiceConfigError("Model catalog SHA256 mismatch")
        if args.command in {"service-start", "service-stop", "diagnose"} or args.command.startswith("app-"):
            from . import app_delivery as delivery
            from . import lifecycle
            try:
                if args.command in {"service-start", "service-stop", "diagnose"}:
                    delivery.installed(args.install_root)
                    if args.command == "service-start":
                        result = lifecycle.start(args.install_root, args.port, at_logon=args.at_logon)
                    elif args.command == "service-stop":
                        result = lifecycle.stop(args.install_root)
                    else:
                        result = lifecycle.diagnose(args.install_root)
                elif args.command == "app-prepare-runtime":
                    result = delivery.prepare_runtime(args.install_root, args.native_manifest, args.native_manifest_sha256)
                elif args.command == "app-bind":
                    result = delivery.bind(args.install_root, args.native_manifest, args.native_manifest_sha256)
                elif args.command == "app-configure":
                    result = delivery.configure(args.install_root, args.catalog, args.catalog_sha256, port=args.port)
                elif args.command == "app-status":
                    result = delivery.status(args.install_root, run_id=args.run_id)
                else:
                    options = dict(accessibility=args.accessibility, observation_seconds=args.observation_seconds)
                    if args.trace_root is not None: options["trace_root"] = args.trace_root
                    if args.command == "app-launch": options["wait_seconds"] = args.wait_seconds
                    result = getattr(delivery, "launch" if args.command == "app-launch" else "check")(args.install_root, **options)
                print(json.dumps(result))
                if args.command == "diagnose" and result["status"] != "compatible": return 3
                return result.get("exit_code", 0)
            except Exception as error:
                from .isolated_app import LaunchError
                from .native_app_tools import NativeToolsError
                known = isinstance(error, (delivery.DeliveryError, lifecycle.LifecycleError, LaunchError, ServiceConfigError, NativeToolsError))
                print(json.dumps({"error": type(error).__name__, "detail": str(error) if known else
                                  "Installed App operation failed; inspect its owned local receipts"}), file=sys.stderr)
                return 2
        if args.command.startswith("image-cache-"):
            import asyncio
            result = asyncio.run(image_cache_operation(args.home, args.command,
                                                       all_unpinned=getattr(args, "all_unpinned", False)))
            if args.command != "image-cache-status" and type(result.get("complete")) is not bool:
                raise ServiceConfigError("Maintenance returned no verified completion status")
            print(json.dumps(result))
            return 0 if args.command == "image-cache-status" or result["complete"] else 3
        if args.command=="auth-token":
            sys.stdout.write(read(LOCAL_TARGET));return 0
        if args.command=="health":
            request=urllib.request.Request(f"http://127.0.0.1:{args.port}/healthz",headers={"Authorization":"Bearer "+read(LOCAL_TARGET)})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=10) as response: result=json.load(response)
            print(json.dumps({"local_gateway":result,"upstream_model_request_performed":False}));return 0
        if args.command=="configure-app":
            from .app_config import prepare,apply
            command=Path(sys.prefix)/"Scripts/codex-app-deepseek.exe"
            prepared=prepare(args.config,catalog_path=args.catalog,auth_command=str(command),port=args.port)
            if prepared.catalog_sha256 != selected_catalog_sha:
                raise ServiceConfigError("Model catalog changed after native agent version validation; nothing applied")
            result=apply(prepared,backup_dir=args.backup_dir)
            print(json.dumps(dataclasses.asdict(result),default=str));return 0
        if args.command=="restore-app":
            from .app_config import restore
            print(json.dumps(dataclasses.asdict(restore(args.manifest)),default=str));return 0
        if args.command=="setup-credentials":
            if not args.import_existing_cds:
                value=getpass.getpass("OpenRouter credential (Windows store): ").strip();write(UPSTREAM_TARGET,value);del value
            print(json.dumps(initialize(import_existing_cds=args.import_existing_cds)));return 0
        if not 1024<=args.port<=65535:parser.error("Use an unprivileged TCP port")
        serve(args.home,args.port)
        return 0
    except (CredentialUnavailable, ServiceConfigError, ImageTransportError, CallReconciliationError) as error:
        print(str(error),file=sys.stderr);return 2


if __name__=="__main__":raise SystemExit(main())
