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
from .app_config import apply, prepare, _atomic_write, _private, _regular_path
from .atomic_io import target_state
from . import gateway_policy as gp
from . import native_app_tools as tools

LAUNCHER_SHA256 = "586b96f7f2df599f6b1bca7befbbb7f329900a4ab068fb95db1eb3b5dceeb31a"
AUTH_ARGS = ("-I", "-m", "codex_app_deepseek.cli", "auth-token")
# Only this previously shipped, retained cohort is eligible for an explicit
# upgrade. Normal reads still require the current baseline exactly.
PRIOR_APP_BASELINES = {
    "a67ef1480c1a526f56b4cd84e8a2e87c8fa26b3868aa4baf5a3fbf8586c53325":
        ("26.1016.8120.0", "0.163.0-alpha.11.1"),
}
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


def _binding_snapshot(installation):
    path = binding_path(installation)
    if not path.is_file():
        raise DeliveryError("No private App runtime binding; use app-bind first")
    try:
        _regular_path(path, "App binding")
    except ValueError:
        raise DeliveryError("App binding must be a regular owned file") from None
    if path.stat().st_nlink != 1:
        raise DeliveryError("App binding must have exactly one filesystem link")
    state = target_state(path)
    raw = path.read_bytes()
    if state is None or hashlib.sha256(raw).hexdigest() != state[1] or target_state(path) != state:
        raise DeliveryError("App binding changed during inspection")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=gp._unique_config_object)
    except (ValueError, UnicodeError):
        raise DeliveryError("App binding must contain readable JSON") from None
    return value, raw, state


def _validate_binding(value, installation, backend, *, previous_release, verify_catalog, baselines):
    if (not isinstance(value, dict) or set(value) != BINDING_KEYS
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("project") != lc.PROJECT
            or not isinstance(value.get("gateway_release_id"), str)
            or not re.fullmatch("[0-9a-f]{20}", value["gateway_release_id"])
            or (not previous_release and value["gateway_release_id"] != installation.receipt["release_id"])
            or not isinstance(value.get("app_baseline_sha256"), str)
            or value["app_baseline_sha256"] not in baselines):
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



def read_binding(installation, backend, *, previous_release=False, verify_catalog=True):
    value, _, _ = _binding_snapshot(installation)
    return _validate_binding(value, installation, backend, previous_release=previous_release,
                             verify_catalog=verify_catalog, baselines={app.BASELINE_SHA256})


def _prior_binding_for_explicit_rebind(installation, backend):
    value, raw, state = _binding_snapshot(installation)
    _validate_binding(value, installation, backend, previous_release=True, verify_catalog=False,
                      baselines={app.BASELINE_SHA256, *PRIOR_APP_BASELINES})
    if value["app_baseline_sha256"] == app.BASELINE_SHA256:
        return value, raw, state, None
    # Only explicit bind can cross a known App cohort, after the new package and
    # bundle passed validation. Inspection, start and configure remain strict.
    prior = installation.root / "releases" / value["gateway_release_id"]
    # Inspect the caller-visible path before resolve can hide an ancestor link.
    receipt_path = _regular_path(prior / "installation.json", "Prior installation receipt")
    prior = lc._within(prior, installation.root / "releases")
    receipt_state = target_state(receipt_path)
    receipt_raw = receipt_path.read_bytes()
    if receipt_state is None or hashlib.sha256(receipt_raw).hexdigest() != receipt_state[1]:
        raise DeliveryError("Prior installation receipt changed during inspection")
    receipt = json.loads(receipt_raw.decode("utf-8-sig"), object_pairs_hook=gp._unique_config_object)
    if (not isinstance(receipt, dict) or receipt.get("project") != lc.PROJECT
            or receipt.get("release_id") != value["gateway_release_id"]
            or not isinstance(receipt.get("data_home"), str)
            or not Path(receipt["data_home"]).is_absolute()
            or canonical_path(receipt["data_home"]) != canonical_path(installation.data)):
        raise DeliveryError("Prior App binding does not identify a retained owned installation")
    lc._verify_installation(prior, receipt)
    asset = _regular_path(prior / "venv/Lib/site-packages/codex_app_deepseek/assets/app-launch-baseline.json",
                          "Prior App baseline")
    if app.sha(asset) != value["app_baseline_sha256"]:
        raise DeliveryError("Prior App baseline bytes differ from the known binding")
    baseline = app.load_json(asset)
    if not isinstance(baseline, dict):
        raise DeliveryError("Prior App baseline identity differs")
    expected = PRIOR_APP_BASELINES[value["app_baseline_sha256"]]
    if (baseline.get("version"), baseline.get("native_version")) != expected:
        raise DeliveryError("Prior App baseline identity differs")
    baseline_raw = asset.read_bytes()
    if (target_state(receipt_path) != receipt_state
            or hashlib.sha256(baseline_raw).hexdigest() != value["app_baseline_sha256"]):
        raise DeliveryError("Prior installation provenance changed during inspection")
    retained = {"installation": receipt_raw, "baseline": baseline_raw}
    return value, raw, state, retained


def _backup_prior_binding(installation, raw, retained, applied):
    applied_sha256 = hashlib.sha256(applied).hexdigest()
    digest = hashlib.sha256(raw).hexdigest()
    transition = hashlib.sha256((digest + ":" + applied_sha256).encode("ascii")).hexdigest()
    directory = installation.data / "app-binding-backups" / transition
    _regular_path(directory / "binding.before.json", "Prior binding backup")
    directory.mkdir(parents=True, exist_ok=True)
    _private(directory, directory=True)
    files = {"binding.before.json": raw, "binding.after.json": applied}
    if retained is not None:
        files.update({"prior-installation.json": retained["installation"],
                      "prior-app-baseline.json": retained["baseline"]})
    for name, content in files.items():
        path = _regular_path(directory / name, "Prior binding backup")
        if path.exists():
            if path.stat().st_nlink != 1 or path.read_bytes() != content:
                raise DeliveryError("Prior binding backup changed")
        else:
            with path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            _private(path)
    record = _regular_path(directory / "migration.json", "Prior binding backup record")
    value = {"schema_version": 1, "before_sha256": digest, "planned_binding_sha256": applied_sha256,
             "before_file": "binding.before.json",
             "rollback_rule": "Restore only if current binding still matches planned_binding_sha256; preserve any later explicit binding selection."}
    if retained is not None:
        value.update(prior_installation_file="prior-installation.json",
                     prior_App_baseline_file="prior-app-baseline.json")
    legacy = value
    value = {**legacy, "schema_version": 2, "after_file": "binding.after.json"}
    if record.exists():
        recorded = app.load_json(record)
        if record.stat().st_nlink != 1 or not (recorded == value or (retained is not None and recorded == legacy)):
            raise DeliveryError("Prior binding migration record changed")
    else:
        app.save(record, value)
    return str(directory)


def _publish_binding(installation, snapshot, value, retained=None):
    path = binding_path(installation)
    before_state = snapshot[2] if snapshot else None
    if target_state(path) != before_state:
        raise DeliveryError("App binding changed before publication; later selection preserved")
    if snapshot and snapshot[0] == value:
        return None
    payload = (json.dumps(value, indent=2) + "\n").encode()
    backup = _backup_prior_binding(installation, snapshot[1], retained, payload) if snapshot else None
    if target_state(path) != before_state:
        raise DeliveryError("App binding changed before publication; later selection preserved")
    _atomic_write(path, payload, expected_sha256=before_state[1] if before_state else None)
    return backup


def _backup_bytes(path):
    _regular_path(path, "Binding backup")
    state = target_state(path)
    if state is None or path.stat().st_nlink != 1:
        raise DeliveryError("Missing or shared App binding backup file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != state[1] or target_state(path) != state:
        raise DeliveryError("Binding backup changed during inspection")
    return raw


def _binding_undo_step(installation, directory, backend):
    directory = Path(directory).absolute()
    base = installation.data / "app-binding-backups"
    if directory.parent != base or not re.fullmatch("[0-9a-f]{64}", directory.name):
        raise DeliveryError("Use an owned binding_backup directory from this installation")
    metadata = json.loads(_backup_bytes(directory / "migration.json"),
                          object_pairs_hook=gp._unique_config_object)
    if not isinstance(metadata, dict) or type(metadata.get("schema_version")) is not int:
        raise DeliveryError("Unrecognized binding backup format")
    before = _backup_bytes(directory / "binding.before.json")
    prior_sha = hashlib.sha256(before).hexdigest()
    after_sha = metadata.get("planned_binding_sha256")
    if (not isinstance(after_sha, str) or not re.fullmatch("[0-9a-f]{64}", after_sha)
            or metadata.get("before_file") != "binding.before.json" or metadata.get("before_sha256") != prior_sha
            or hashlib.sha256((prior_sha + ":" + after_sha).encode("ascii")).hexdigest() != directory.name):
        raise DeliveryError("Binding backup does not match its recorded transition")
    if metadata.get("schema_version") == 2 and metadata.get("after_file") == "binding.after.json":
        if hashlib.sha256(_backup_bytes(directory / "binding.after.json")).hexdigest() != after_sha:
            raise DeliveryError("Saved replacement binding differs from its recorded digest")
    elif not (metadata.get("schema_version") == 1
              and metadata.get("prior_installation_file") == "prior-installation.json"
              and metadata.get("prior_App_baseline_file") == "prior-app-baseline.json"):
        raise DeliveryError("Unrecognized binding backup format")
    previous = json.loads(before, object_pairs_hook=gp._unique_config_object)
    _validate_binding(previous, installation, backend, previous_release=True, verify_catalog=True,
                      baselines={app.BASELINE_SHA256, *PRIOR_APP_BASELINES})
    return before, prior_sha, after_sha


def restore_binding(root, backups, *, backend=None):
    installation = installed(root)
    backend = backend or app.WindowsBackend()
    owner = interactive(backend)
    with lc._operation_lease(installation.root), backend.launch_lock(owner["sid"]):
        app.require_disjoint_apps(backend, owner, app.defaults())
        active, _, current = _binding_snapshot(installation)
        _validate_binding(active, installation, backend, previous_release=True, verify_catalog=False,
                          baselines={app.BASELINE_SHA256, *PRIOR_APP_BASELINES})
        steps = [_binding_undo_step(installation, path, backend) for path in backups]
        if not steps or any(earlier[2] != later[1] for later, earlier in zip(steps, steps[1:])):
            raise DeliveryError("Binding backups must form a continuous newest-to-oldest chain")
        before, prior_sha, _ = steps[-1]
        if current[1] == prior_sha:
            return {"status": "binding_already_restored", "binding_sha256": prior_sha}
        if current[1] != steps[0][2]:
            raise DeliveryError("Current binding differs from the backup; later selection preserved")
        _atomic_write(binding_path(installation), before, expected_sha256=current[1])
    return {"status": "binding_restored", "binding_sha256": prior_sha}


def native_request(binding, *, accessibility=False, observation_seconds=60, trace_root=None):
    baseline, _ = assets()
    return {"baseline": str(baseline), "native_manifest": binding["native_manifest"],
            "native_manifest_sha256": binding["native_manifest_sha256"],
            "app_args": ["--force-renderer-accessibility"] if accessibility else [],
            "timeout_seconds": observation_seconds,
            **({"trace_root": str(trace_root)} if trace_root is not None else {})}


def prepare_runtime(root, manifest, digest, *, backend=None):
    installation = installed(root)
    backend = backend or app.WindowsBackend()
    interactive(backend)
    baseline, _ = assets()
    # The native candidate remains an explicit release input. Never select C/D,
    # download a replacement, or redistribute the installed closed App assets.
    result = app.prepare_runtime(Path(manifest), digest, baseline, backend,
                                 resources_receipt=tools.receipt_paths(installation.data)[0])
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
        bundle = app.verify_launch_bundle(manifest, digest, expected, backend, owner, paths["home"])
        snapshot = getattr(backend, "native_runtime_owner_snapshot", None)
        if snapshot is not None:
            from . import runtime_package
            runtime_package.require_mode_available(bundle.get("runtime_package"), snapshot(), owner, paths["home"])
        value = {"schema_version": 1, "project": lc.PROJECT,
                 "gateway_release_id": installation.receipt["release_id"], "owner_sid": owner["sid"],
                 "native_manifest": str(manifest), "native_manifest_sha256": digest,
                 "app_baseline_sha256": app.BASELINE_SHA256, "catalog": None}
        path = binding_path(installation)
        snapshot = None
        retained = None
        if path.exists():
            previous, raw, before_state, retained = _prior_binding_for_explicit_rebind(installation, backend)
            snapshot = (previous, raw, before_state)
            if (previous["app_baseline_sha256"] == app.BASELINE_SHA256
                    and previous["native_manifest"] == str(manifest) and previous["native_manifest_sha256"] == digest):
                old_catalog = previous["catalog"]
                if old_catalog is not None and Path(old_catalog["path"]).is_file() and app.sha(old_catalog["path"]) == old_catalog["sha256"]:
                    value["catalog"] = old_catalog
        backup = _publish_binding(installation, snapshot, value, retained)
    result = {"status": "runtime_bound_not_launched", "binding": str(path),
              "native_manifest_sha256": digest, "gateway_release_id": installation.receipt["release_id"]}
    if backup is not None:
        result["binding_backup"] = backup
    if retained is not None and backup is not None:
        result["prior_cohort_backup"] = backup
        result["catalog_reconfiguration_required"] = True
    return result


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
        snapshot = _binding_snapshot(installation)
        value = _validate_binding(snapshot[0], installation, backend, previous_release=False,
                                  verify_catalog=False, baselines={app.BASELINE_SHA256})
        package = app.verify_package(backend.package(expected), expected)
        bundle = app.verify_launch_bundle(value["native_manifest"], value["native_manifest_sha256"],
                                         expected, backend, owner, paths["home"])
        inventory = tools.load_inventory()
        tools.preflight(paths["home"])
        resources_source = Path(package["package"]["location"]) / inventory["source_relative"]
        tools.verify_resources(resources_source, inventory)
        tools.verify_resources(paths["home"] / ".tmp/bundled-marketplaces" / tools.MARKETPLACE,
                               inventory, source=resources_source, allow_filtered=True)
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
            checkpoints = []
            registered = None
            checkpoint = None
            def publish_provider(planned):
                if not planned.changed:
                    return None
                if gp.load_service_config(installation.data).config_sha256 != policy.config_sha256:
                    raise DeliveryError("Gateway policy changed during App configuration; no App config was applied")
                checkpoint = apply(planned, backup_dir=installation.data / "config-backups")
                try:
                    registration = lc.register_checkpoint(installation.root, checkpoint.manifest_path)
                except Exception:
                    raise DeliveryError("Configuration applied but checkpoint registration failed; retain and register "
                                        + str(checkpoint.manifest_path) + " before rollback/uninstall") from None
                checkpoints.append(str(checkpoint.manifest_path))
                return checkpoint, registration
            # Preserve the original fresh defaults and ensure a registered provider
            # rollback point exists before the native manager edits configuration.
            initial = prepare(paths["home"] / "config.toml", catalog_path=target,
                              auth_command=str(executable), auth_args=AUTH_ARGS, port=port)
            published = publish_provider(initial)
            if published is not None:
                checkpoint, registered = published
            _, native_receipt = tools.receipt_paths(installation.data)
            native_options = {}
            if "runtime_package" in bundle:
                native_options["runtime_package"] = bundle["runtime_package"]
            native_tools = tools.bootstrap(paths["home"], bundle["files"]["codex.exe"]["path"],
                bundle["files"]["codex.exe"]["sha256"], inventory, native_receipt,
                owner_sid=owner["sid"], catalog_path=target, catalog_sha256=catalog_sha256,
                backup_dir=installation.data / "native-app-tools/policy-backups", source_root=resources_source,
                native_manifest=value["native_manifest"], native_manifest_sha256=value["native_manifest_sha256"],
                **native_options)
            # Never apply a stale PreparedConfig captured before native CLI edits.
            final = prepare(paths["home"] / "config.toml", catalog_path=target,
                            auth_command=str(executable), auth_args=AUTH_ARGS, port=port)
            published = publish_provider(final)
            if published is not None:
                checkpoint, registered = published
            if gp.load_service_config(installation.data).config_sha256 != policy.config_sha256:
                raise DeliveryError("Gateway policy changed after App configuration; retain registered checkpoints "
                                    + ", ".join(checkpoints) + " and explicitly configure again")
            selected = {**value, "catalog": {"path": str(target), "sha256": catalog_sha256}}
            binding_backup = _publish_binding(installation, snapshot, selected)
    return {"status": "private_app_configured_not_launched", "config": str(paths["home"] / "config.toml"),
            "checkpoint": str(checkpoint.manifest_path) if checkpoint is not None else None,
            "checkpoints": checkpoints, "registered_checkpoint": registered, "binding_backup": binding_backup,
            "native_app_tools": {"state": native_tools["state"], "plugin_id": tools.PLUGIN_ID},
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


def check(root, *, backend=None, accessibility=False, observation_seconds=60, trace_root=None):
    installation = installed(root)
    backend = backend or app.WindowsBackend()
    value = read_binding(installation, backend)
    if value["catalog"] is None:
        raise DeliveryError("Configure the bound private App using app-configure first")
    catalog_models = gp.catalog_model_ids(value["catalog"]["path"], value["catalog"]["sha256"])
    policy = gp.load_service_config(installation.data)
    gp.require_app_policy(policy, catalog_models)
    request = native_request(value, accessibility=accessibility, observation_seconds=observation_seconds, trace_root=trace_root)
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
    inventory = tools.load_inventory()
    _, tools_receipt = tools.receipt_paths(installation.data)
    native_options = {}
    if "runtime_package" in checked["bundle"]:
        native_options["runtime_package"] = checked["bundle"]["runtime_package"]
    native_tools = tools.configured(app.defaults()["home"], checked["bundle"]["files"]["codex.exe"]["path"],
        checked["bundle"]["files"]["codex.exe"]["sha256"], inventory, tools_receipt,
        owner_sid=checked["owner"]["sid"], **native_options)
    gateway = owned_gateway(installation)
    if provider.get("base_url") != f"http://127.0.0.1:{gateway['port']}/v1":
        raise DeliveryError("Private App provider does not address this owned gateway listener")
    health = gateway.get("health", {})
    if (health.get("model") != gp.PINNED_MODEL
            or not policy.config_sha256 or health.get("config_sha256") != policy.config_sha256
            or health.get("accepted_models") is None or set(health["accepted_models"]) != set(catalog_models)
            or health.get("allowed_providers") is None or set(health["allowed_providers"]) != set(policy.allowed_providers)
            or health.get("ordinary_default_provider") != policy.ordinary_default_provider):
        raise DeliveryError("Owned gateway loaded a different policy/catalog; explicitly stop/start it, then run app-check")
    if gp.load_service_config(installation.data).config_sha256 != policy.config_sha256:
        raise DeliveryError("Gateway policy changed during installed preflight; run app-check again")
    gp.catalog_model_ids(value["catalog"]["path"], value["catalog"]["sha256"])
    request["expected_config_sha256"] = checked["config_sha256"]
    return {"status": "validated_not_launched", "gateway_release_id": installation.receipt["release_id"],
            "gateway": gateway, "launch": checked, "request": request,
            "gateway_policy": gp.policy_info(policy), "catalog_models": list(catalog_models),
            "native_app_tools": native_tools, "App_runtime_acceptance": False}


def launch(root, *, accessibility=False, observation_seconds=60, wait_seconds=60,
           trace_root=None, backend=None, run=subprocess.run):
    installation = installed(root)
    if type(wait_seconds) is not int or not 1 <= wait_seconds <= 120:
        raise DeliveryError("App launch wait must be 1 through 120 seconds")
    # No implicit service start, current-App restart, native update, or retry.
    with lc._operation_lease(installation.root):
        ready = check(root, backend=backend, accessibility=accessibility,
                      observation_seconds=observation_seconds, trace_root=trace_root)
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
        if trace_root is not None:
            arguments.extend(["-TraceRoot", ready["launch"]["trace_root"]])
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
                "native_app_tools": ready.get("native_app_tools"), "App_runtime_acceptance": False}


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
