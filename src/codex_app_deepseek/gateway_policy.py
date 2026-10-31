"""Versioned gateway policy and explicit fresh-App initialization; no runtime ownership."""
from __future__ import annotations
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from .catalog import NATIVE_VERSION, PINNED_MODEL, REQUEST_MODEL_IDS
MODEL = PINNED_MODEL
PROVIDER, STRUCTURED_PROVIDER, SERIAL_PROVIDER = "deepseek", "morph/fp8", "primeintellect"

class ServiceConfigError(ValueError):
    """The gateway configuration is invalid; no service has been constructed."""


@dataclasses.dataclass(frozen=True)
class ServiceConfig:
    accepted_models: tuple[str, ...]
    allowed_providers: tuple[str, ...]
    upstream_proxy: str | None = dataclasses.field(default=None, repr=False)
    app_version: str | None = None
    native_version: str | None = None
    native_sha256: str | None = None
    image_search_backend: str | None = None
    web_search_backend: str = "openrouter_exa"
    image_transport: dict | None = None
    policy_origin: dict | None = None
    config_sha256: str | None = None

    @property
    def configured_baseline(self) -> dict[str, str]:
        # These are declared metadata, not an assertion that this process verified
        # an installed App/runtime executable. Keep that distinction in receipts.
        return {name: value for name in ("app_version", "native_version", "native_sha256")
                if (value := getattr(self, name)) is not None}


def _unique_config_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ServiceConfigError("gateway.json contains duplicate configuration fields")
        result[key] = value
    return result


def _config_names(config: dict, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = config.get(key, list(default))
    if (not isinstance(value, list) or not value
            or any(not isinstance(name, str) or not name or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in name)
                   for name in value)):
        raise ServiceConfigError(f"{key} must be a nonempty JSON array of explicit non-whitespace names")
    if len(set(value)) != len(value):
        raise ServiceConfigError(f"{key} must not contain duplicate names")
    return tuple(value)



def resolve_service_home(home: Path) -> Path:
    """Keep gateway files outside App/CDS homes, including resolved junctions."""
    try:
        resolved = Path(home).expanduser().resolve()
        user = Path.home()
        local = Path(os.environ.get("LOCALAPPDATA", str(user / "AppData" / "Local")))
        protected = [(user / ".codex").resolve(), (local / "codex-deepseek").resolve(),
                     (local / "codex-app-deepseek-app").resolve()]
        if resolved.exists() and not resolved.is_dir():
            raise ServiceConfigError("Gateway home must be a directory")
        if any(resolved == path or resolved.is_relative_to(path) or path.is_relative_to(resolved)
               for path in protected):
            raise ServiceConfigError("Gateway home must not overlap the current user's App or CDS home")
        return resolved
    except ServiceConfigError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ServiceConfigError("Gateway home cannot be resolved safely") from None


def load_service_config(home: Path) -> ServiceConfig:
    """Validate every supported gateway.json field before any service side effect.

    Missing config uses the fixed model and official DeepSeek route only. Other
    pinned-model providers require an explicit allowlist; BridgeSettings defaults
    never broaden the CLI's configured selection.
    """
    path = resolve_service_home(home) / "gateway.json"
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise ServiceConfigError("gateway.json must be a regular unlinked file")
    def reject_constant(_):
        raise ServiceConfigError("gateway.json cannot contain non-finite JSON numbers")
    try:
        raw = path.read_bytes() if path.exists() else None
        config = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_config_object,
                            parse_constant=reject_constant) if raw is not None else {}
    except ServiceConfigError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise ServiceConfigError("gateway.json must be a readable, valid UTF-8 JSON document") from None
    if not isinstance(config, dict):
        raise ServiceConfigError("gateway.json must contain a JSON object")
    allowed_fields = {"accepted_models", "allowed_providers", "upstream_proxy",
                      "app_version", "native_version", "native_sha256",
                      "image_search_backend", "image_transport", "policy_origin", "web_search_backend"}
    if set(config) - allowed_fields:
        raise ServiceConfigError("gateway.json contains unsupported configuration fields")
    models = _config_names(config, "accepted_models", (MODEL,))
    providers = _config_names(config, "allowed_providers", (PROVIDER,))
    if not set(providers) <= {PROVIDER, SERIAL_PROVIDER, STRUCTURED_PROVIDER}:
        raise ServiceConfigError("allowed_providers contains unsupported pinned-model routes")
    proxy = config.get("upstream_proxy")
    if proxy is not None:
        if not isinstance(proxy, str) or not proxy or "\\" in proxy or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in proxy):
            raise ServiceConfigError("upstream_proxy must be null or a valid HTTP/HTTPS proxy URL")
        try:
            parsed_proxy = urlsplit(proxy)
            if (parsed_proxy.scheme not in {"http", "https"} or not parsed_proxy.hostname
                    or parsed_proxy.path not in {"", "/"} or parsed_proxy.query or parsed_proxy.fragment
                    or (parsed_proxy.port is not None and not 1 <= parsed_proxy.port <= 65535)):
                raise ValueError
        except ValueError:
            raise ServiceConfigError("upstream_proxy must be null or a valid HTTP/HTTPS proxy URL") from None
    metadata = {}
    for key in ("app_version", "native_version", "native_sha256"):
        if key not in config:
            continue
        value = config[key]
        if (not isinstance(value, str) or not value or value != value.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ServiceConfigError(f"{key} must be a nonempty metadata string without control characters")
        if key == "native_sha256":
            if not re.fullmatch(r"[0-9A-Fa-f]{64}", value):
                raise ServiceConfigError("native_sha256 must be a 64-character hexadecimal digest")
            value = value.lower()
        metadata[key] = value
    web_search = config.get("web_search_backend", "openrouter_exa")
    if web_search not in ("openrouter_exa", "firecrawl"):
        raise ServiceConfigError("web_search_backend must be openrouter_exa or firecrawl")
    image_search = config.get("image_search_backend")
    if image_search not in (None, "wikimedia_commons"):
        raise ServiceConfigError("image_search_backend must be null or wikimedia_commons")
    image_policy = config.get("image_transport")
    if image_policy is not None:
        from .image_transport import TransportPolicy
        fields = {item.name for item in dataclasses.fields(TransportPolicy)}
        if not isinstance(image_policy, dict) or set(image_policy) - fields:
            raise ServiceConfigError("image_transport must be null or an explicit transport policy object")
        try:
            TransportPolicy(**image_policy)
        except (TypeError, ValueError):
            raise ServiceConfigError("image_transport has missing or invalid policy fields") from None
    origin = config.get("policy_origin")
    if origin is not None and origin not in (DEFAULT_POLICY_ORIGIN, LEGACY_POLICY_ORIGIN_067):
        raise ServiceConfigError("gateway.json has an unrecognized policy_origin")
    return ServiceConfig(models, providers, proxy, **metadata,
                         image_search_backend=image_search, web_search_backend=web_search,
                         image_transport=image_policy, policy_origin=origin,
                         config_sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None)



# This is the complete route set reachable in pinned protocol.prepare() at
# fixed CDS 0.6.8 / 4d99981: DeepSeek, strict/peer+hosted Morph, explicit serial Prime.
# It is metadata, not a second routing implementation.
DEFAULT_PROVIDERS = ("deepseek", "morph/fp8", "primeintellect")
DEFAULT_POLICY_ORIGIN = {
    "policy_id": "codex-app-680c4-full-runtime-v1",
    "native_version": NATIVE_VERSION,
    "protocol_commit": "4d99981baaa493b0c4e7b1febbdb167e6c44de91",
    "provider_reasons": {
        "deepseek": "official_default",
        "morph/fp8": "strict_json_schema_or_native_peer_history_with_hosted_tools",
        "primeintellect": "explicit_parallel_tool_calls_false",
    },
}

# A known initialization origin remains valid after a protocol-only upgrade.
# Its original bytes are retained; this does not widen any route or model policy.
LEGACY_POLICY_ORIGIN_067 = {
    "policy_id": "codex-app-680c4-full-runtime-v1",
    "native_version": "0.165.0-alpha.1.2",
    "protocol_commit": "cb788738864e97a16cde82f3c584164162ab6f7c",
    "provider_reasons": {
        "deepseek": "official_default",
        "morph/fp8": "strict_json_schema_or_native_peer_history_with_hosted_tools",
        "primeintellect": "explicit_parallel_tool_calls_false",
    },
}


def default_app_policy():
    return {"accepted_models": list(REQUEST_MODEL_IDS), "allowed_providers": list(DEFAULT_PROVIDERS),
            "policy_origin": json.loads(json.dumps(DEFAULT_POLICY_ORIGIN))}


def require_app_policy(config, catalog_models=None):
    missing = sorted(set(REQUEST_MODEL_IDS) - set(config.accepted_models))
    if missing:
        raise ServiceConfigError(
            "Gateway policy lacks fixed App/runtime aliases: " + ", ".join(missing)
            + "; explicitly update accepted_models and the matching immutable catalog. "
              "Existing policy bytes were not expanded.")
    if not set(DEFAULT_PROVIDERS) <= set(config.allowed_providers):
        raise ServiceConfigError("Gateway policy lacks required native schema/serial/default routes; update the explicit allowlist")
    if catalog_models is not None and set(config.accepted_models) != set(catalog_models):
        raise ServiceConfigError("Gateway accepted_models and immutable App catalog model names differ")


def policy_info(config, *, created=False):
    return {"status": "created_fixed_app_policy" if created else "existing_policy_preserved",
            "config_sha256": config.config_sha256, "accepted_models": list(config.accepted_models),
            "allowed_providers": list(config.allowed_providers), "origin": config.policy_origin}


def ensure_app_policy(home):
    """Initialize only an absent file; never replace an existing custom policy."""
    from .app_config import _private
    home = resolve_service_home(home)
    home.mkdir(parents=True, exist_ok=True)
    _private(home, directory=True)
    path = home / "gateway.json"
    created = False
    if not path.exists() and not path.is_symlink():
        fd, name = tempfile.mkstemp(prefix=".gateway-policy-", dir=home)
        pending = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                _private(pending)
                stream.write((json.dumps(default_app_policy(), ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                if os.name == "nt":
                    os.rename(pending, path)  # Win32 atomic rename without replace.
                else:
                    os.link(pending, path)  # Atomic no-replace publication.
                created = True
            except FileExistsError:
                pass  # The winning explicit file is authoritative, never overwritten.
        finally:
            pending.unlink(missing_ok=True)
    config = load_service_config(home)
    require_app_policy(config)
    return config, policy_info(config, created=created)


def catalog_model_ids(path, expected_sha256):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ServiceConfigError("App catalog must be a regular immutable file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ServiceConfigError("App catalog changed while checking gateway policy")
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_config_object)
    models = value.get("models") if isinstance(value, dict) else None
    if not isinstance(models, list) or any(not isinstance(x, dict) or not isinstance(x.get("slug"), str) or not x["slug"] for x in models):
        raise ServiceConfigError("App catalog must contain explicit model slugs")
    names = tuple(x["slug"] for x in models)
    if len(names) != len(set(names)) or not set(REQUEST_MODEL_IDS) <= set(names):
        raise ServiceConfigError("App catalog must contain unique fixed main/Guardian aliases")
    return names
