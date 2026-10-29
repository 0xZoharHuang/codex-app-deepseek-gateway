from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest

from codex_app_deepseek import lifecycle as lc
from codex_app_deepseek import native_cleanup as nc
from test_lifecycle import bundle, activate


def namespace(home, owner="S-1-5-21-1-1001"):
    return {"schema": 1, "namespace": "a"*64, "owner_sid": owner,
            "home": str(home.resolve()).lower(), "volume": 12, "file_id": list(range(16)),
            "offline": "Cdx" + "a"*16 + "f", "online": "Cdx" + "a"*16 + "n",
            "group": "CodexAppDeepSeek-"+"a"*64}


class Backend:
    def __init__(self, *, elevated=True):
        self.owner = {"sid": "S-1-5-21-1-1001", "elevated": elevated}
    def current(self):
        return dict(self.owner)


def cleanup(tmp_path, monkeypatch):
    home = tmp_path/"app"; home.mkdir()
    native = tmp_path/"codex.exe"; native.write_bytes(b"verified fixture native")
    ns = namespace(home)
    backend = Backend()
    monkeypatch.setattr(nc, "_namespace", lambda *args: ns)
    monkeypatch.setattr(nc, "_drained", lambda *args: None)
    monkeypatch.setattr(nc, "_account_sid", lambda *args: None)
    return nc.Cleanup(tmp_path, home, native, ns, backend,
                      {str(native): lc.digest(native)}, "native_cleanup_required")


def test_native_delegation_pins_explicit_apphome_not_gateway_and_strips_selectors(tmp_path, monkeypatch):
    plan = cleanup(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path/"wrong gateway"))
    monkeypatch.setenv("CODEX_SANDBOX_WINDOWS_DRIVER", "registered_core")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-forwarded")
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"native complete\n", stderr=b"")
    result = plan.execute(run=run)
    argv, arguments = calls[0]
    assert argv == [str(plan.executable), "sandbox", "uninstall"]
    assert arguments["env"]["CODEX_HOME"] == str(plan.home)
    assert "CODEX_SANDBOX_WINDOWS_DRIVER" not in arguments["env"]
    assert "OPENROUTER_API_KEY" not in arguments["env"]
    assert arguments["cwd"] == plan.home and arguments["stdin"] == subprocess.DEVNULL
    assert result["status"] == "native_cleanup_completed"
    record = lc._json(Path(result["receipt"]))
    assert record["principals_after"] == {"offline": None, "online": None, "group": None}
    assert record["stdout_sha256"] == hashlib.sha256(b"native complete\n").hexdigest()
    assert result["ordinary_filesystem_acl_removed"] is False and result["user_files_deleted"] is False


@pytest.mark.parametrize("mode", ["nonzero", "timeout", "oserror", "still-present"])
def test_native_failure_is_durable_and_never_replayed_or_retired(tmp_path, monkeypatch, mode):
    plan = cleanup(tmp_path, monkeypatch)
    calls = []
    if mode == "still-present":
        monkeypatch.setattr(nc, "_account_sid", lambda *args: "S-1-5-21-1-1200")
    def run(argv, **kwargs):
        calls.append(argv)
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, 120)
        if mode == "oserror":
            raise OSError("private path")
        return SimpleNamespace(returncode=7 if mode == "nonzero" else 0, stdout=b"partial", stderr=b"native refused")
    with pytest.raises(lc.LifecycleError):
        plan.execute(run=run)
    assert len(calls) == 1
    record = lc._json(tmp_path/"native-uninstall.json")
    assert record["status"] == {"nonzero": "native_cleanup_failed", "timeout": "native_cleanup_outcome_unconfirmed",
                               "oserror": "native_cleanup_outcome_unconfirmed",
                               "still-present": "native_cleanup_verification_failed"}[mode]
    assert plan.executable.is_file() and plan.home.is_dir()
    assert not (tmp_path/"uninstall-ready.json").exists()


def test_native_input_change_aborts_before_command_or_started_receipt(tmp_path, monkeypatch):
    plan = cleanup(tmp_path, monkeypatch)
    plan.executable.write_bytes(b"external replacement")
    with pytest.raises(lc.LifecycleError, match="changed"):
        plan.execute(run=lambda *a, **kw: pytest.fail("must not execute"))
    assert not (tmp_path/"native-uninstall.json").exists()


@pytest.mark.parametrize("field,value", [("owner_sid", "S-1-5-21-1-1002"), ("home", "c:\\foreign"),
                                        ("volume", 17), ("file_id", [0]*16), ("namespace", "nothex"),
                                        ("offline", "SomeoneElse"), ("group", "Administrators")])
def test_namespace_preflight_refuses_wrong_identity_without_decoding_credentials(tmp_path, monkeypatch, field, value):
    home = tmp_path/"home"; path = home/".sandbox-secrets/namespace_owner.json"
    path.parent.mkdir(parents=True)
    ns = namespace(home); ns[field] = value; path.write_text(json.dumps(ns))
    (path.parent/"sandbox_users.json").write_bytes(b"never parse encrypted passwords")
    monkeypatch.setattr(nc, "_directory_identity", lambda *args: (12, list(range(16))))
    with pytest.raises(lc.LifecycleError, match="identity"):
        nc._namespace(home, Backend(), Backend().current())


def test_namespace_with_state_and_missing_owner_never_becomes_not_provisioned(tmp_path):
    (tmp_path/".sandbox").mkdir(); (tmp_path/".sandbox/setup_marker.json").write_text("{}")
    with pytest.raises(lc.LifecycleError, match="no namespace"):
        nc._namespace(tmp_path, Backend(), Backend().current())


def test_live_namespace_token_rejects_even_different_user_and_no_frontend(tmp_path, monkeypatch):
    backend = Backend(); ns = namespace(tmp_path)
    monkeypatch.setattr(nc, "_account_sid", lambda name, backend: "S-1-5-21-1-9009")
    monkeypatch.setattr(nc, "_process_owners", lambda backend: [
        {"pid": 111, "name": "powershell.exe", "sid": "S-1-5-21-1-9009"}])
    with pytest.raises(lc.LifecycleError, match="live sandbox workload"):
        nc._drained(tmp_path, tmp_path/"codex.exe", ns, backend)


def test_unrelated_sandbox_and_other_user_app_are_not_terminated(tmp_path, monkeypatch):
    backend = Backend(); ns = namespace(tmp_path)
    monkeypatch.setattr(nc, "_account_sid", lambda name, backend: "S-1-5-21-1-9009")
    monkeypatch.setattr(nc, "_process_owners", lambda backend: [
        {"pid": 111, "name": "powershell.exe", "sid": "S-1-5-21-1-9010"},
        {"pid": 222, "name": "ChatGPT.exe", "sid": "S-1-5-21-1-1002"}])
    nc._drained(tmp_path, tmp_path/"codex.exe", ns, backend)


@pytest.mark.parametrize("kind", ["requires_administrator", "live_workload", "binding_changed"])
def test_uninstall_admission_failure_precedes_rollback_stop_or_receipt(bundle, monkeypatch, kind):
    activate(bundle)
    before = lc.digest(bundle.install/"active.json")
    @contextmanager
    def denied(*args, **kwargs):
        raise lc.LifecycleError(kind)
        yield
    monkeypatch.setattr(nc, "admission", denied)
    monkeypatch.setattr(lc, "rollback", lambda *args: pytest.fail("rollback before native admission"))
    monkeypatch.setattr(lc, "_stop_locked", lambda *args, **kwargs: pytest.fail("stop before native admission"))
    with pytest.raises(lc.LifecycleError, match=kind):
        lc.prepare_uninstall(bundle.install, remove_task=False)
    assert lc.digest(bundle.install/"active.json") == before
    assert not (bundle.install/"uninstall-ready.json").exists()


def test_native_failure_does_not_stop_service_or_publish_ready(bundle, monkeypatch):
    activate(bundle)
    class Failed:
        def execute(self, **kwargs):
            raise lc.LifecycleError("native partial failure")
    @contextmanager
    def admitted(*args, **kwargs):
        yield Failed()
    monkeypatch.setattr(nc, "admission", admitted)
    monkeypatch.setattr(lc, "_stop_locked", lambda *a, **k: pytest.fail("stop after failed cleanup"))
    with pytest.raises(lc.LifecycleError, match="partial"):
        lc.prepare_uninstall(bundle.install, remove_task=False)
    assert (bundle.install/"active.json").exists()
    assert not (bundle.install/"uninstall-ready.json").exists()


def test_unbound_custom_gateway_does_not_adopt_another_apps_namespace(bundle, monkeypatch):
    activate(bundle)
    result = lc.prepare_uninstall(bundle.install, remove_task=False)
    assert result["native_cleanup"]["status"] == "not_app_bound"
    assert result["native_cleanup"]["native_command_performed"] is False


def test_bound_old_uninstall_proof_cannot_bypass_native_admission(bundle):
    activate(bundle)
    prepared = lc.prepare_uninstall(bundle.install, remove_task=False)
    path = Path(prepared["receipt"]); record = lc._json(path)
    del record["native_cleanup"]; lc._write(path, record)
    (bundle.data/"app-binding.json").write_text("{}")
    with pytest.raises(lc.LifecycleError, match="native cleanup admission"):
        lc.finalize_uninstall(bundle.install, lc.digest(path))
    assert (bundle.install/"active.json").exists()


def test_changed_native_cleanup_proof_blocks_stdlib_finalizer(bundle):
    activate(bundle)
    prepared = lc.prepare_uninstall(bundle.install, remove_task=False)
    path = Path(prepared["receipt"]); record = lc._json(path)
    record["native_cleanup"] = {"status": "native_cleanup_completed", "native_command_performed": True,
                                "receipt": str(bundle.install/"native-uninstall.json"), "receipt_sha256": "a"*64}
    lc._write(path, record); (bundle.install/"native-uninstall.json").write_text("{}")
    with pytest.raises(lc.LifecycleError, match="receipt changed"):
        lc.finalize_uninstall(bundle.install, lc.digest(path))
    assert (bundle.install/"active.json").exists()


def test_unknown_attempt_requires_explicit_retry_and_preserves_original(tmp_path, monkeypatch):
    plan = cleanup(tmp_path, monkeypatch)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 120)
    with pytest.raises(lc.LifecycleError, match="unconfirmed"):
        plan.execute(run=timeout)
    old = lc._json(tmp_path/"native-uninstall.json")
    with pytest.raises(lc.LifecycleError, match="--retry-native-cleanup"):
        plan.execute(run=lambda *a, **k: pytest.fail("implicit replay"))
    calls = []
    result = plan.execute(retry_unconfirmed=True, run=lambda *a, **k: (
        calls.append(a[0]) or SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")))
    assert len(calls) == 1 and result["status"] == "native_cleanup_completed"
    assert lc._json(Path(old["attempt_receipt"])) == old
    assert lc._json(tmp_path/"native-uninstall.json")["attempt_id"] != old["attempt_id"]


def test_completed_attempt_is_reused_only_while_current_principals_absent(tmp_path, monkeypatch):
    plan = cleanup(tmp_path, monkeypatch)
    first = plan.execute(run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"ok", stderr=b""))
    previous = lc._json(Path(first["receipt"]))
    result = plan.execute(run=lambda *a, **k: pytest.fail("completed cleanup replay"))
    assert result["reused_completion"] is True and result["receipt_sha256"] == first["receipt_sha256"]
    present = {"value": True}
    monkeypatch.setattr(nc, "_account_sid", lambda *args: "S-1-5-21-1-9999" if present["value"] else None)
    calls = []
    def fresh_lifecycle(*a, **k):
        calls.append(a[0]); present["value"] = False
        return SimpleNamespace(returncode=0, stdout=b"new principals removed", stderr=b"")
    result = plan.execute(run=fresh_lifecycle)
    assert len(calls) == 1 and result.get("reused_completion") is not True
    assert lc._json(Path(previous["attempt_receipt"])) == previous
    assert lc._json(Path(result["receipt"]))["attempt_id"] != previous["attempt_id"]


def test_unbound_gateway_cannot_hide_native_recovery_history(bundle):
    activate(bundle)
    lc._write(bundle.install/"native-uninstall.json", {"schema_version": 1, "project": lc.PROJECT,
                                                      "status": "native_cleanup_outcome_unconfirmed"})
    with pytest.raises(lc.LifecycleError, match="binding is missing"):
        lc.prepare_uninstall(bundle.install, remove_task=False)
    assert (bundle.install/"active.json").exists()


def test_retry_flag_parser_forwards_only_to_explicit_prepare(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(lc, "prepare_uninstall", lambda root, **kw: calls.append((root, kw)) or {"status": "ok"})
    assert lc.main(["prepare-uninstall", "--install-root", str(tmp_path), "--retry-native-cleanup"]) == 0
    assert calls == [(tmp_path, {"retry_native_cleanup": True})]


def test_custom_install_with_verified_private_app_checkpoint_cannot_hide_missing_binding(bundle, monkeypatch):
    from codex_app_deepseek.app_config import prepare, apply, PINNED_MODEL
    activate(bundle)
    local = bundle.root/"local"; home = local/"codex-app-deepseek-app"; home.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    config = home/"config.toml"; config.write_bytes(b'# preserve user bytes\n')
    catalog=bundle.root/"catalog.json"; catalog.write_text(json.dumps({"models":[{"slug":PINNED_MODEL}]}))
    helper=bundle.root/"auth.exe"; helper.write_bytes(b"never execute")
    saved=apply(prepare(config,catalog_path=catalog,auth_command=helper,port=42817),
                backup_dir=bundle.data/"config-backups")
    lc.register_checkpoint(bundle.install,saved.manifest_path)
    before=config.read_bytes()
    with pytest.raises(lc.LifecycleError, match="checkpoint"):
        lc.prepare_uninstall(bundle.install,remove_task=False)
    assert config.read_bytes()==before and (bundle.install/"active.json").exists()


def test_custom_gateway_with_unrelated_checkpoint_is_still_gateway_only(bundle, monkeypatch):
    from test_lifecycle import checkpoint
    config, original, record = checkpoint(bundle)
    local=bundle.root/"unrelated local"; private=local/"codex-app-deepseek-app/.sandbox-secrets"
    private.mkdir(parents=True);(private/"namespace_owner.json").write_text('{"unrelated":"never adopt"}')
    monkeypatch.setenv("LOCALAPPDATA",str(local))
    result=lc.prepare_uninstall(bundle.install,remove_task=False)
    assert result["native_cleanup"]["status"]=="not_app_bound"
    assert config.read_bytes()==original
    assert (private/"namespace_owner.json").read_text()=='{"unrelated":"never adopt"}'


@pytest.mark.parametrize("name", ["codex-windows-sandbox-setup.exe",
                                  "codex-windows-sandbox-setup-0.163.0-alpha.11.1.exe",
                                  "codex-code-mode-host.exe"])
def test_native_setup_and_support_helpers_are_not_mistaken_for_drained(tmp_path, monkeypatch, name):
    import psutil
    backend=Backend();backend.identity=lambda pid: {"exe":str(tmp_path/name),"sid":backend.current()["sid"]}
    monkeypatch.setattr(nc,"_account_sid",lambda *args:None)
    monkeypatch.setattr(nc,"_process_owners",lambda b:[{"pid":123,"name":name,"sid":b.current()["sid"]}])
    monkeypatch.setattr(psutil,"Process",lambda pid:SimpleNamespace(pid=pid,environ=lambda:{"CODEX_HOME":str(tmp_path)}))
    with pytest.raises(lc.LifecycleError,match="backend/helper"):
        nc._drained(tmp_path,tmp_path/"codex.exe",namespace(tmp_path),backend)


def test_module_entry_preserves_only_known_native_cleanup_error_diagnostic(tmp_path, monkeypatch, capsys):
    import contextlib, runpy
    namespace=runpy.run_path(str(Path(lc.__file__)),run_name="codex_app_deepseek.__native_uninstall_entry_test__")
    entry=namespace["main"];current=entry.__globals__
    assert current["LifecycleError"] is not nc.NativeCleanupError
    current["_home"]=lambda path:tmp_path
    current["_operation_lease"]=lambda path:contextlib.nullcontext()
    current["_active"]=lambda path:(tmp_path,{"data_home":str(tmp_path)},None)
    current["_verify_installation"]=lambda *args:None
    @contextmanager
    def rejected(*args,**kwargs):
        raise nc.NativeCleanupError("Native sandbox uninstall requires administrator privileges")
        yield
    monkeypatch.setattr(nc,"admission",rejected)
    assert entry(["prepare-uninstall","--install-root",str(tmp_path)])==2
    result=json.loads(capsys.readouterr().err)
    assert result=={"error":"LifecycleError","detail":"Native sandbox uninstall requires administrator privileges"}
    @contextmanager
    def unknown(*args,**kwargs):
        raise RuntimeError("raw private subprocess stderr must never leak")
        yield
    monkeypatch.setattr(nc,"admission",unknown)
    assert entry(["prepare-uninstall","--install-root",str(tmp_path)])==2
    result=json.loads(capsys.readouterr().err)
    assert "private"not in result["detail"]


from test_isolated_app import fixture as app_fixture


def partial_admission_fixture(bundle, app_fixture, monkeypatch):
    from codex_app_deepseek import app_delivery as delivery, isolated_app as app
    activate(bundle)
    paths, expected, manifest_path, manifest, request, backend = app_fixture
    backend.current_value["elevated"] = True
    binding = {"native_manifest": str(manifest_path),
               "native_manifest_sha256": request["native_manifest_sha256"]}
    binding_path = bundle.data / "app-binding.json"
    binding_path.write_text(json.dumps(binding))
    monkeypatch.setattr(delivery, "read_binding", lambda *a, **k: binding)
    monkeypatch.setattr(delivery, "assets", lambda: (Path(request["baseline"]), None))
    pins = []
    @contextmanager
    def pin(path, backend):
        pins.append(Path(path))
        if any(nc._is_link_or_reparse(part) for part in (Path(path), *Path(path).parents)):
            raise lc.LifecycleError("Native uninstall input is redirected")
        yield
    monkeypatch.setattr(nc, "_pinned_file", pin)
    home = paths["home"]
    owner_path = home / ".sandbox-secrets/namespace_owner.json"
    owner_path.parent.mkdir()
    ns = namespace(home, backend.current()["sid"])
    owner_path.write_text(json.dumps(ns))
    monkeypatch.setattr(nc, "_directory_identity", lambda *a: (12, list(range(16))))
    monkeypatch.setattr(nc, "_drained", lambda *a: None)
    monkeypatch.setattr(nc, "_account_sid", lambda *a: None)
    return paths, expected, manifest_path, manifest, request, backend, binding_path, owner_path, pins


@pytest.mark.parametrize("damage", ["missing", "unreadable", "checksum"])
def test_partial_bundle_cleanup_pins_and_digests_only_executed_cli(bundle, app_fixture, monkeypatch, damage):
    from codex_app_deepseek import isolated_app as app
    paths, expected, path, manifest, request, backend, binding, owner, pins = partial_admission_fixture(bundle, app_fixture, monkeypatch)
    helper = Path(manifest["outputs"][2]["path"])
    if damage == "missing": helper.unlink()
    elif damage == "checksum": helper.write_bytes(b"changed unused helper")
    else:
        real = app.sha
        def unreadable(file):
            if Path(file) == helper: raise PermissionError("unreadable unused helper")
            return real(file)
        monkeypatch.setattr(app, "sha", unreadable)
    real_digest = lc.digest
    def no_helper_digest(file):
        if Path(file) in [Path(row["path"]) for row in manifest["outputs"][1:]]:
            pytest.fail("unused helper made mandatory cleanup input")
        return real_digest(file)
    monkeypatch.setattr(lc, "digest", no_helper_digest)
    directory, receipt, _ = lc._active(bundle.install)
    with nc.admission(bundle.install, directory, receipt, backend=backend) as plan:
        cli = Path(manifest["outputs"][0]["path"])
        assert set(plan.inputs) == {str(binding), str(path), str(cli), str(owner)}
        assert pins == [binding, path, cli]
        calls = []
        result = plan.execute(run=lambda *a, **k: calls.append(a[0]) or
                              SimpleNamespace(returncode=0, stdout=b"native done", stderr=b""))
    assert len(calls) == 1 and result["status"] == "native_cleanup_completed"
    assert result["bundle_verification"]["full_bundle_verified"] is False
    assert result["bundle_verification"]["unexecuted_components"][helper.name]["status"] == {
        "missing": "missing", "unreadable": "unreadable", "checksum": "checksum_mismatch"}[damage]
    assert lc._json(Path(result["receipt"]))["bundle_verification"] == result["bundle_verification"]


@pytest.mark.parametrize("damage", ["missing", "checksum", "outside", "reparse", "wrong-sid", "live-actor"])
def test_partial_cleanup_denial_precedes_real_prepare_mutations(bundle, app_fixture, monkeypatch, damage):
    from codex_app_deepseek import isolated_app as app
    paths, expected, path, manifest, request, backend, binding, owner, pins = partial_admission_fixture(bundle, app_fixture, monkeypatch)
    executable = Path(manifest["outputs"][0]["path"])
    if damage == "missing": executable.unlink()
    elif damage == "checksum": executable.write_bytes(b"changed CLI")
    elif damage == "outside":
        manifest["outputs"][0]["path"] = str(path.parent.parent / "codex.exe")
        path.write_text(json.dumps(manifest))
        request["native_manifest_sha256"] = app.sha(path)
        original = json.loads(binding.read_text()); original["native_manifest_sha256"] = request["native_manifest_sha256"]
        binding.write_text(json.dumps(original))
        from codex_app_deepseek import app_delivery as delivery
        monkeypatch.setattr(delivery, "read_binding", lambda *a, **k: original)
    elif damage == "reparse":
        real = nc._is_link_or_reparse
        monkeypatch.setattr(nc, "_is_link_or_reparse", lambda file: Path(file) == executable or real(file))
    elif damage == "wrong-sid":
        record = json.loads(owner.read_text()); record["owner_sid"] = "S-1-5-21-1-9999"; owner.write_text(json.dumps(record))
    else:
        def active(*a): raise lc.LifecycleError("live sandbox workload")
        monkeypatch.setattr(nc, "_drained", active)
    monkeypatch.setattr(app, "WindowsBackend", lambda: backend)
    monkeypatch.setattr(lc, "rollback", lambda *a, **k: pytest.fail("rollback before rejection"))
    monkeypatch.setattr(lc, "_stop_locked", lambda *a, **k: pytest.fail("stop before rejection"))
    before = lc.digest(bundle.install / "active.json")
    with pytest.raises((lc.LifecycleError, app.LaunchError, OSError)):
        lc.prepare_uninstall(bundle.install, remove_task=False)
    assert lc.digest(bundle.install / "active.json") == before
    assert not (bundle.install / "native-uninstall.json").exists()
    assert not (bundle.install / "uninstall-ready.json").exists()
