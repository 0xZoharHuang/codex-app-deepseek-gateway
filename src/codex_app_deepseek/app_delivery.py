"""Installed, one-shot App entrypoints. No App supervisor or agent runtime."""
from __future__ import annotations
from .paths import _is_link_or_reparse
from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from . import isolated_app as app
from . import lifecycle as lc
from .app_config import apply, prepare, _atomic_write, _private
from . import gateway_policy as gp

LAUNCHER_SHA256 = "9a9269f2439aacca2c5b859a82497a1f8ad41a3b1fd111638e5c8701e8025507"
AUTH_ARGS = ("-I", "-m", "codex_app_deepseek.cli", "auth-token")
BINDING_KEYS = {"schema_version", "project", "gateway_release_id", "owner_sid",
                "native_manifest", "native_manifest_sha256", "app_baseline_sha256", "catalog"}

class DeliveryError(RuntimeError):
    pass

@dataclass(frozen=True)
class Installation:
    root: Path
    directory: Path
    data: Path
    receipt: dict


def canonical_path(path):
    """Normalize Win32 extended spelling after resolving the actual filesystem path."""
    resolved = Path(path).resolve()
    text = str(resolved)
    if os.name == "nt":
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif (text.startswith("\\\\?\\") and len(text) >= 7
              and "A" <= text[4].upper() <= "Z" and text[5:7] == ":\\"):
            text = text[4:]
    return Path(text)


def verify_installed_context(directory, executable, module_path):
    # pip's Windows launcher can legitimately embed an extended-length interpreter
    # path. This spelling must not weaken or accidentally fail ownership checks.
    directory = canonical_path(directory)
    expected = canonical_path(directory / "venv/Scripts/python.exe")
    if (canonical_path(executable) != expected
            or not canonical_path(module_path).is_relative_to(directory / "venv")):
        raise DeliveryError("Use the active installed release's codex-app-deepseek.exe")


def assets():
    directory = canonical_path(__file__).parent / "assets"
    baseline = directory / "app-launch-baseline.json"
    launcher = directory / "launch_isolated_app.ps1"
    if not baseline.is_file() or not launcher.is_file():
        raise DeliveryError("Installed App assets are missing; reinstall the verified release")
    app.baseline(baseline)
    if app.sha(launcher) != LAUNCHER_SHA256:
        raise DeliveryError("Packaged App launcher changed; reinstall the verified release")
    return baseline, launcher


def installed(root):
    root = lc._home(Path(root))
    if not (root / "active.json").is_file():
        raise DeliveryError("No active managed installation; complete Install and Activate first")
    directory, receipt, _ = lc._active(root)
    lc._verify_installation(directory, receipt)
    verify_installed_context(directory, sys.executable, __file__)
    assets()
    return Installation(root, directory, lc._home(Path(receipt["data_home"])), receipt)


def interactive(backend):
    owner = backend.current()
    if owner["session"] <= 0 or owner["elevated"]:
        raise DeliveryError("Use the intended user's non-elevated interactive Windows desktop")
    return owner


def binding_path(installation):
    return installation.data / "app-binding.json"


def read_binding(installation, backend, *, previous_release=False, verify_catalog=True):
    path = binding_path(installation)
    if not path.is_file():
        raise DeliveryError("No private App runtime binding; use app-bind first")
    value = app.load_json(path)
    if (not isinstance(value, dict) or set(value) != BINDING_KEYS
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("project") != lc.PROJECT
            or not isinstance(value.get("gateway_release_id"), str)
            or not re.fullmatch("[0-9a-f]{20}", value["gateway_release_id"])
            or (not previous_release and value["gateway_release_id"] != installation.receipt["release_id"])
            or value.get("app_baseline_sha256") != app.BASELINE_SHA256):
        raise DeliveryError("App binding does not match this installed release; explicitly bind again")
    if value.get("owner_sid") != backend.current()["sid"]:
        raise DeliveryError("App binding belongs to another Windows user")
    manifest = value.get("native_manifest")
    digest = value.get("native_manifest_sha256")
    if (not isinstance(manifest, str) or not Path(manifest).is_absolute()
            or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
        raise DeliveryError("App binding has invalid native provenance")
    if value["catalog"] is not None:
        catalog = value["catalog"]
        if (not isinstance(catalog, dict) or set(catalog) != {"path", "sha256"}
                or not isinstance(catalog["path"], str)
                or not Path(catalog["path"]).is_absolute()
                or not isinstance(catalog["sha256"], str)
                or not re.fullmatch("[0-9a-f]{64}", catalog["sha256"])):
            raise DeliveryError("App binding has invalid model catalog provenance")
        path = Path(catalog["path"])
        if path.resolve() != (installation.data / "app-catalogs" / (catalog["sha256"] + ".json")).resolve():
            raise DeliveryError("Bound model catalog escapes the owned catalog directory")
        if verify_catalog and app.sha(path) != catalog["sha256"]:
            raise DeliveryError("Bound model catalog changed; explicit configuration is required")
    return value


def native_request(binding, *, accessibility=False, observation_seconds=60):
    baseline, _ = assets()
    return {"baseline": str(baseline), "native_manifest": binding["native_manifest"],
            "native_manifest_sha256": binding["native_manifest_sha256"],
            "app_args": ["--force-renderer-accessibility"] if accessibility else [],
            "timeout_seconds": observation_seconds}


def prepare_runtime(root, manifest, digest, *, backend=None):
    installation = installed(root)
    backend = backend or app.WindowsBackend()
    interactive(backend)
    baseline, _ = assets()
    # The native candidate remains an explicit release input. Never select C/D,
    # download a replacement, or redistribute the installed closed App assets.
    result = app.prepare_runtime(Path(manifest), digest, baseline, backend)
    return {"status": "runtime_prepared_not_bound", **result,
            "gateway_release_id": installation.receipt["release_id"]}


def bind(root, manifest, digest, *, backend=None):
    installation = installed(root)
    backend = backend or app.WindowsBackend()
    owner = interactive(backend)
    baseline, _ = assets()
    expected = app.baseline(baseline)
    manifest = Path(manifest).resolve(strict=True)
    lc._home(manifest.parent)  # Refuse protected App/CDS locations.
    with lc._operation_lease(installation.root), backend.launch_lock(owner["sid"]):
        paths = app.defaults()
        app.validate_state_paths(paths)
        try:
            app.require_disjoint_apps(backend, owner, paths)
        except app.LaunchError as error:
            raise DeliveryError(str(error)) from error
        app.verify_package(backend.package(expected), expected)
        app.verify_bundle(manifest, digest, expected)
        value = {"schema_version": 1, "project": lc.PROJECT,
                 "gateway_release_id": installation.receipt["release_id"], "owner_sid": owner["sid"],
                 "native_manifest": str(manifest), "native_manifest_sha256": digest,
                 "app_baseline_sha256": app.BASELINE_SHA256, "catalog": None}
        path = binding_path(installation)
        if path.exists():
            previous = read_binding(installation, backend, previous_release=True, verify_catalog=False)
            if previous["native_manifest"] == str(manifest) and previous["native_manifest_sha256"] == digest:
                old_catalog = previous["catalog"]
                if old_catalog is not None and Path(old_catalog["path"]).is_file() and app.sha(old_catalog["path"]) == old_catalog["sha256"]:
                    value["catalog"] = old_catalog
        app.save(path, value)
    return {"status": "runtime_bound_not_launched", "binding": str(path),
            "native_manifest_sha256": digest, "gateway_release_id": installation.receipt["release_id"]}


def configure(root, catalog, catalog_sha256, *, port=42817, backend=None):
    installation = installed(root)
    backend = backend or app.WindowsBackend()
    owner = interactive(backend)
    if not isinstance(catalog_sha256, str) or not re.fullmatch("[0-9a-f]{64}", catalog_sha256):
        raise DeliveryError("Supply the separately trusted model catalog SHA256")
    source = Path(catalog).resolve(strict=True)
    if app.sha(source) != catalog_sha256:
        raise DeliveryError("Model catalog SHA256 mismatch")
    catalog_models = gp.catalog_model_ids(source, catalog_sha256)
    baseline, _ = assets()
    expected = app.baseline(baseline)
    with lc._operation_lease(installation.root), backend.launch_lock(owner["sid"]):
        paths = app.defaults()
        app.validate_state_paths(paths)
        try:
            app.require_disjoint_apps(backend, owner, paths)
        except app.LaunchError as error:
            raise DeliveryError(str(error)) from error
        value = read_binding(installation, backend, verify_catalog=False)
        app.verify_package(backend.package(expected), expected)
        app.verify_bundle(value["native_manifest"], value["native_manifest_sha256"], expected)
        with backend.home_guard(paths["home"], owner["sid"], create=True) as home_identity:
            executable = installation.directory / "venv/Scripts/python.exe"
            if not executable.is_file():
                raise DeliveryError("Installed authentication helper is missing")
            # Reject invalid source JSON/permissions before creating any catalog file.
            prepare(paths["home"] / "config.toml", catalog_path=source,
                    auth_command=str(executable), auth_args=AUTH_ARGS, port=port)
            policy, policy_result = gp.ensure_app_policy(installation.data)
            gp.require_app_policy(policy, catalog_models)
            target = installation.data / "app-catalogs" / (catalog_sha256 + ".json")
            if target.exists():
                if app.sha(target) != catalog_sha256:
                    raise DeliveryError("Existing immutable catalog bytes differ")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                _private(target.parent, directory=True)
                _atomic_write(target, source.read_bytes())
                if app.sha(target) != catalog_sha256:
                    raise DeliveryError("Catalog changed during copying")
            planned = prepare(paths["home"] / "config.toml", catalog_path=target,
                              auth_command=str(executable), auth_args=AUTH_ARGS, port=port)
            if gp.load_service_config(installation.data).config_sha256 != policy.config_sha256:
                raise DeliveryError("Gateway policy changed during App configuration; no App config was applied")
            checkpoint = apply(planned, backup_dir=installation.data / "config-backups")
            # Preserve the returned checkpoint even if registry update fails. The
            # existing public register-checkpoint command is the explicit recovery.
            try:
                registered = lc.register_checkpoint(installation.root, checkpoint.manifest_path)
            except Exception:
                raise DeliveryError("Configuration applied but checkpoint registration failed; retain and register "
                                    + str(checkpoint.manifest_path) + " before rollback/uninstall") from None
            if gp.load_service_config(installation.data).config_sha256 != policy.config_sha256:
                raise DeliveryError("Gateway policy changed after App configuration; retain registered checkpoint "
                                    + str(checkpoint.manifest_path) + " and explicitly configure again")
            value["catalog"] = {"path": str(target), "sha256": catalog_sha256}
            app.save(binding_path(installation), value)
    return {"status": "private_app_configured_not_launched", "config": str(paths["home"] / "config.toml"),
            "checkpoint": str(checkpoint.manifest_path), "registered_checkpoint": registered,
            "catalog": str(target), "auth_command": str(executable), "auth_args": list(AUTH_ARGS),
            "home_identity": home_identity, "gateway_policy": policy_result, "instruction": "Start or explicitly restart the owned gateway, then run app-check",
            "model_request_performed": False}


def owned_gateway(installation):
    process, _ = lc._owned_process(installation.directory, installation.data)
    if process is None:
        raise DeliveryError("This installed release's gateway is not running; use service-start first")
    record = lc._json(installation.data / "service.json")
    port = record.get("port")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise DeliveryError("Owned gateway listener record has an invalid port")
    health = lc._owned_health(process, installation.data, port)
    return {"status": "owned_healthy", "pid": process.pid, "port": port, "health": health,
            "upstream_model_request_performed": False}


def check(root, *, backend=None, accessibility=False, observation_seconds=60):
    installation = installed(root)
    backend = backend or app.WindowsBackend()
    value = read_binding(installation, backend)
    if value["catalog"] is None:
        raise DeliveryError("Configure the bound private App using app-configure first")
    catalog_models = gp.catalog_model_ids(value["catalog"]["path"], value["catalog"]["sha256"])
    policy = gp.load_service_config(installation.data)
    gp.require_app_policy(policy, catalog_models)
    request = native_request(value, accessibility=accessibility, observation_seconds=observation_seconds)
    checked = app.validate_request(request, backend)
    config_bytes = (app.defaults()["home"] / "config.toml").read_bytes()
    if hashlib.sha256(config_bytes).hexdigest() != checked["config_sha256"]:
        raise DeliveryError("Private App configuration changed during installed preflight")
    config = tomllib.loads(config_bytes.decode("utf-8-sig"))
    provider = config.get("model_providers", {}).get("codex_app_deepseek", {})
    executable = installation.directory / "venv/Scripts/python.exe"
    auth = provider.get("auth", {})
    if (auth.get("command") != str(executable) or auth.get("args") != list(AUTH_ARGS)
            or config.get("model_catalog_json") != value["catalog"]["path"]):
        raise DeliveryError("Private App config points outside the selected installed helper/catalog")
    gateway = owned_gateway(installation)
    if provider.get("base_url") != f"http://127.0.0.1:{gateway['port']}/v1":
        raise DeliveryError("Private App provider does not address this owned gateway listener")
    health = gateway.get("health", {})
    if (health.get("model") != gp.PINNED_MODEL
            or not policy.config_sha256 or health.get("config_sha256") != policy.config_sha256
            or health.get("accepted_models") is None or set(health["accepted_models"]) != set(catalog_models)
            or health.get("allowed_providers") is None or set(health["allowed_providers"]) != set(policy.allowed_providers)):
        raise DeliveryError("Owned gateway loaded a different policy/catalog; explicitly stop/start it, then run app-check")
    if gp.load_service_config(installation.data).config_sha256 != policy.config_sha256:
        raise DeliveryError("Gateway policy changed during installed preflight; run app-check again")
    gp.catalog_model_ids(value["catalog"]["path"], value["catalog"]["sha256"])
    request["expected_config_sha256"] = checked["config_sha256"]
    return {"status": "validated_not_launched", "gateway_release_id": installation.receipt["release_id"],
            "gateway": gateway, "launch": checked, "request": request,
            "gateway_policy": gp.policy_info(policy), "catalog_models": list(catalog_models),
            "App_runtime_acceptance": False}


def launch(root, *, accessibility=False, observation_seconds=60, wait_seconds=60,
           backend=None, run=subprocess.run):
    installation = installed(root)
    if type(wait_seconds) is not int or not 1 <= wait_seconds <= 120:
        raise DeliveryError("App launch wait must be 1 through 120 seconds")
    # No implicit service start, current-App restart, native update, or retry.
    with lc._operation_lease(installation.root):
        ready = check(root, backend=backend, accessibility=accessibility,
                      observation_seconds=observation_seconds)
        baseline, launcher = assets()
        request = ready["request"]
        expected_config = request.get("expected_config_sha256")
        if (not isinstance(expected_config, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_config)
                or expected_config != ready.get("launch", {}).get("config_sha256")):
            raise DeliveryError("Installed App launch requires the verified configuration checksum")
        powershell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        arguments = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                     str(launcher), "-Mode", "Launch", "-PythonPath", str(installation.directory / "venv/Scripts/python.exe"),
                     "-BaselinePath", str(baseline), "-NativeManifest", request["native_manifest"],
                     "-NativeManifestSha256", request["native_manifest_sha256"],
                     "-ExpectedConfigSha256", expected_config,
                     "-ObservationSeconds", str(observation_seconds), "-WaitSeconds", str(wait_seconds)]
        if accessibility:
            arguments.append("-Accessibility")
        try:
            result = run(arguments, capture_output=True, text=True, encoding="utf-8", timeout=wait_seconds + 30)
        except subprocess.TimeoutExpired:
            return {"status": "launch_outcome_unknown", "exit_code": 3, "replay_allowed": False,
                    "instruction": "Use app-status; do not automatically relaunch", "App_runtime_acceptance": False}
        try:
            value = json.loads(result.stdout)
        except (TypeError, ValueError):
            # Never print arbitrary PowerShell stderr or echoed arguments.
            raise DeliveryError("App launch did not return a valid receipt; use app-status before retrying") from None
        if not isinstance(value, dict):
            raise DeliveryError("App launch returned an invalid receipt")
        confirmed = (result.returncode == 0 and value.get("receipt", {}).get("status") == "started_expected_backend")
        if confirmed:
            observed = app.launch_status(value["run_dir"], backend)
            confirmed = (observed["recorded_status"] == "started_expected_backend"
                         and any(x["role"] == "app" and x["live"] is True for x in observed["processes"])
                         and any(x["role"] == "native" and x["live"] is True for x in observed["processes"]))
            value["current_observation"] = observed
        return {"status": "started_expected_backend" if confirmed else "launch_unverified",
                "exit_code": 0 if confirmed else 3, "launch": value, "replay_allowed": False,
                "App_runtime_acceptance": False}


def status(root, *, run_id=None, backend=None):
    installation = installed(root)
    backend = backend or app.WindowsBackend()
    app.validate_state_paths(app.defaults())
    launches = app.defaults()["launches"]
    if run_id is not None:
        if not re.fullmatch("[0-9a-f]{32}", run_id):
            raise DeliveryError("App run ID must be the exact 32-character launch ID")
        selected = launches / run_id
    else:
        # Select only the latest owned plan, never processes by executable name.
        candidates = []
        if launches.exists():
            entries = list(launches.iterdir())
            if len(entries) > 4096:
                raise DeliveryError("Many launch receipts exist; select an explicit --run-id")
            for candidate in entries:
                if candidate.is_dir() and re.fullmatch("[0-9a-f]{32}", candidate.name):
                    if candidate.resolve().parent != launches.resolve() or _is_link_or_reparse(candidate):
                        raise DeliveryError("Owned App receipt directory is redirected")
                    plan = app.load_json(candidate / "plan.json")
                    if plan["validated"]["owner"]["sid"] == backend.current()["sid"]:
                        candidates.append((plan["created_at"], candidate))
        if not candidates:
            return {"status": "not_launched", "App_runtime_acceptance": False}
        selected = max(candidates, key=lambda item: item[0])[1]
    result = app.launch_status(selected, backend)
    return {"status": "observation", "run_id": selected.name, "run_dir": str(selected),
            "App": result, "gateway_release_id": installation.receipt["release_id"],
            "App_runtime_acceptance": False}
